"""In-sandbox SSE upstream MCP proxy (SANDBOX_TRANSPORT_CONTRACT §5.3).

Legacy HTTP+SSE servers return 202 Accepted on POST and deliver JSON-RPC responses on
the long-lived GET /sse stream. The agent keeps that reader open per session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from agent.upstream_manager import (
    UpstreamError,
    UpstreamSession,
    _MAX_RESPONSE_BYTES,
    _aiter_sse_lines_bounded,
    _assert_upstream_not_ssrf,
    _error_response,
    _normalize_host,
    _wrap_response,
)

LOG = logging.getLogger("sandbox_agent.sse")


async def _validated_messages_url(
    candidate: str, session: UpstreamSession, base: str
) -> str | None:
    """Validate an UPSTREAM-SUPPLIED SSE ``endpoint`` URL before we POST credentials to it.

    RED-TEAM L5-01 (credential exfiltration). The legacy HTTP+SSE transport learns its POST
    target from the untrusted upstream's ``endpoint`` event. The old code accepted that value
    VERBATIM whenever it started with ``http``, with no allowlist and no SSRF re-check — and
    ``_send_sse_jsonrpc_locked`` then POSTs to it with ``session.headers``, i.e. the operator's
    BYOK ``Authorization: Bearer <token>`` plus any custom auth header, and every tool-call
    argument. A malicious or compromised MCP server therefore harvested the operator's upstream
    credentials simply by naming its own collector host. ``_validate_upstream`` /
    ``_assert_upstream_not_ssrf`` run ONCE on the CONFIGURED ``upstream.url`` at session
    creation and were never re-applied here.

    A second escape lived in the same branch: ``data.startswith("/")`` is also true for a
    PROTOCOL-RELATIVE ``//evil.example/x``, which ``urljoin(base, ...)`` resolves to a
    cross-host absolute URL — so the "relative" path was not safe either. Every candidate,
    whichever branch produced it, is therefore validated HERE.

    Accepted: the configured upstream's own host, or a host the OPERATOR explicitly placed in
    ``allowed_hosts`` (consistent with ``_validate_upstream``, which matches on hostname only),
    and only after the same DNS/SSRF resolution check the configured URL got. Returns the URL
    when it is safe, else ``None`` (caller keeps the previous target and logs the rejection) —
    fail CLOSED: we would rather stall this session than ship a bearer token to a stranger.
    """
    parsed = urlparse(candidate or "")
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.hostname
    if not host:
        return None
    norm = _normalize_host(host)
    base_host = _normalize_host(urlparse(base).hostname or "")
    if norm and norm == base_host:
        # Same host as the CONFIGURED upstream: it already passed _validate_upstream and
        # _assert_upstream_not_ssrf at session creation, so accept without re-resolving
        # (a transient DNS hiccup must not stall an otherwise valid session).
        return candidate
    allowed = {_normalize_host(h) for h in (getattr(session, "allowed_hosts", None) or []) if h}
    if norm not in allowed and host not in allowed:
        return None
    # A DIFFERENT host the operator explicitly allowlisted: honour the selection, but only
    # after the same DNS/SSRF resolution check the configured URL received.
    try:
        await _assert_upstream_not_ssrf(host)
    except UpstreamError:
        return None
    return candidate

# CHG-0129: bound the per-session SSE response queue. The persistent reader keeps
# running between RPCs, so an untrusted upstream flooding UNSOLICITED `message`
# events (with no consumer draining) would grow an unbounded asyncio.Queue until
# the sandbox agent OOMs. Cap it and drop the OLDEST on overflow (keep the freshest,
# most likely to match a pending request) — the reader never blocks.
_SSE_QUEUE_MAXSIZE = max(16, int(os.environ.get("MCP_AGENT_SSE_QUEUE_MAXSIZE", "1024")))

# F-014: GET /sse is long-lived — a single float timeout applies read-timeout to the
# whole stream (idle upstream → ReadTimeout kills the reader). POST /messages uses a
# short read window for 202 ack; the JSON-RPC response arrives on the GET stream.
_SSE_POST_READ_TIMEOUT = float(os.environ.get("MCP_AGENT_SSE_POST_READ_TIMEOUT", "30"))

# Serialize SSE POST+response matching per server — concurrent POSTs while the GET
# reader restarts race the session and cause intermittent POST timeouts (F-014).
_sse_send_locks: dict[str, asyncio.Lock] = {}


def _sse_get_stream_timeout(connect_timeout: float) -> httpx.Timeout:
    """No read timeout on the persistent GET /sse event stream."""
    return httpx.Timeout(
        connect=connect_timeout,
        read=None,
        write=connect_timeout,
        pool=connect_timeout,
    )


def _sse_post_ack_timeout(connect_timeout: float, method_timeout: float) -> httpx.Timeout:
    """Short read window for POST 202/204 ack; full method_timeout waits on SSE queue."""
    read_cap = min(_SSE_POST_READ_TIMEOUT, method_timeout)
    return httpx.Timeout(
        connect=connect_timeout,
        read=read_cap,
        write=method_timeout,
        pool=connect_timeout,
    )


def _new_sse_queue() -> asyncio.Queue:
    return asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)


def _bounded_put(queue: asyncio.Queue, item: Any, server_slug: str) -> None:
    """Non-blocking put that drops the oldest entry when the queue is full, so a
    flooding upstream cannot grow the queue unbounded and cannot stall the reader.
    (A waiting get() receives the item directly and never counts against maxsize.)"""
    try:
        queue.put_nowait(item)
        return
    except asyncio.QueueFull:
        pass
    try:
        queue.get_nowait()  # evict oldest — atomic (no await between get/put)
    except asyncio.QueueEmpty:
        pass
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        pass
    LOG.warning("SSE response queue full for %s; dropped oldest response", server_slug)


async def stop_sse_reader(session: UpstreamSession) -> None:
    """Cancel the background GET /sse reader and drop queued responses."""
    task = session.sse_task
    session.sse_task = None
    session.sse_ready = None
    session.sse_responses = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _sse_reader_loop(session: UpstreamSession, connect_timeout: float) -> None:
    """Maintain GET /sse; parse endpoint + message events until cancelled."""
    assert session.client is not None
    if session.sse_responses is None:
        session.sse_responses = _new_sse_queue()
    ready = session.sse_ready or asyncio.Event()
    session.sse_ready = ready
    headers = {**session.headers, "Accept": "text/event-stream"}
    base = f"{urlparse(session.url).scheme}://{urlparse(session.url).netloc}"
    try:
        async with session.client.stream(
            "GET",
            session.url,
            headers=headers,
            timeout=_sse_get_stream_timeout(connect_timeout),
        ) as response:
            if response.status_code == 401:
                raise UpstreamError(-32001, "upstream SSE 401; re-authenticate", needs_reauth=True)
            if response.status_code >= 400:
                raise UpstreamError(-32000, f"upstream SSE HTTP {response.status_code}")
            event_type = ""
            data_lines: list[str] = []
            data_bytes = 0          # CHG-0128: bound per-event data accumulation
            skipping = False        # CHG-0128: drop the remainder of an oversized event
            # CHG-0130: bounded byte-line reader (aiter_lines buffers one line unbounded).
            async for line in _aiter_sse_lines_bounded(response, _MAX_RESPONSE_BYTES):
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    if skipping:
                        continue
                    _chunk = line[5:].strip()
                    # CHG-0128: an untrusted upstream must not OOM the sandbox agent by
                    # streaming unbounded data: lines before a terminating blank line.
                    # Cap the accumulated event at _MAX_RESPONSE_BYTES and DROP the
                    # overflow (the reader keeps running for the next event) — parity
                    # with the gateway ext-SSE bounds (CHG-0117) and the streamable-http
                    # per-response cap (CHG-0066).
                    if data_bytes + len(_chunk) > _MAX_RESPONSE_BYTES:
                        LOG.warning(
                            "SSE event exceeded %d bytes for %s; dropping event",
                            _MAX_RESPONSE_BYTES, session.server_slug,
                        )
                        skipping = True
                        data_lines = []
                        data_bytes = 0
                        continue
                    data_lines.append(_chunk)
                    data_bytes += len(_chunk)
                elif line == "" and (data_lines or skipping):
                    if skipping:
                        # oversized event fully consumed → reset and wait for the next.
                        skipping = False
                        data_lines = []
                        data_bytes = 0
                        event_type = ""
                        continue
                    data = "\n".join(data_lines)
                    data_lines = []
                    data_bytes = 0
                    if event_type == "endpoint" or "sessionId" in data or data.startswith("/"):
                        # L5-01: build the candidate, then VALIDATE it before it can ever
                        # receive the operator's credentials (see _validated_messages_url).
                        if data.startswith("/") or data.startswith("http"):
                            _candidate = (
                                data if data.startswith("http") else urljoin(base, data.split("\n")[0])
                            )
                        else:
                            path = data.split("sessionId=")[-1] if "sessionId=" in data else data
                            _candidate = urljoin(base, f"/messages?sessionId={path}")
                        _safe = await _validated_messages_url(_candidate, session, base)
                        if _safe is None:
                            LOG.warning(
                                "sse endpoint event REJECTED for server=%s: %r resolves outside the "
                                "configured upstream host / allowed_hosts (credential exfiltration "
                                "attempt or misconfigured upstream) — target unchanged",
                                session.server_slug, _candidate,
                            )
                            event_type = ""
                            continue
                        session.sse_messages_url = _safe
                        if "sessionId=" in data:
                            session.session_id = data.split("sessionId=")[-1].split("&")[0]
                        ready.set()
                    elif event_type == "message" and session.sse_responses is not None:
                        try:
                            parsed = json.loads(data)
                        except json.JSONDecodeError:
                            parsed = None
                        if isinstance(parsed, dict):
                            _bounded_put(session.sse_responses, parsed, session.server_slug)
                    event_type = ""
    except asyncio.CancelledError:
        raise
    except UpstreamError:
        raise
    except httpx.TimeoutException as exc:
        raise UpstreamError(-32003, "upstream SSE connect timeout") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(-32000, f"upstream SSE error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — reader task must not crash the agent
        LOG.warning("SSE reader ended for %s: %s", session.server_slug, exc)
    finally:
        session.sse_task = None


async def ensure_sse_reader(session: UpstreamSession, connect_timeout: float) -> str:
    """Start (or await) the background SSE reader and return the POST messages URL."""
    if session.sse_task is None or session.sse_task.done():
        session.sse_messages_url = None
        session.sse_responses = _new_sse_queue()
        session.sse_ready = asyncio.Event()
        session.sse_task = asyncio.create_task(_sse_reader_loop(session, connect_timeout))
    ready = session.sse_ready
    if ready is None:
        raise UpstreamError(-32000, "upstream SSE reader not started")
    try:
        await asyncio.wait_for(session.sse_ready.wait(), timeout=connect_timeout)
    except asyncio.TimeoutError as exc:
        await stop_sse_reader(session)
        raise UpstreamError(-32003, "upstream SSE endpoint timeout") from exc
    if not session.sse_messages_url:
        await stop_sse_reader(session)
        raise UpstreamError(-32000, "upstream SSE: no endpoint event received")
    return session.sse_messages_url


async def send_sse_jsonrpc(
    session: UpstreamSession,
    message: dict[str, Any],
    connect_timeout: float,
    method_timeout: float,
    msg_id: int | str | None,
) -> dict[str, Any]:
    """POST one JSON-RPC message and read the matching response from the SSE stream."""
    lock = _sse_send_locks.setdefault(session.server_slug, asyncio.Lock())
    async with lock:
        return await _send_sse_jsonrpc_locked(
            session, message, connect_timeout, method_timeout, msg_id
        )


async def _send_sse_jsonrpc_locked(
    session: UpstreamSession,
    message: dict[str, Any],
    connect_timeout: float,
    method_timeout: float,
    msg_id: int | str | None,
) -> dict[str, Any]:
    started = time.time()
    assert session.client is not None
    want_id = message.get("id")
    headers = {**session.headers, "Content-Type": "application/json"}
    post_timeout = _sse_post_ack_timeout(connect_timeout, method_timeout)
    last_exc: UpstreamError | None = None
    for attempt in range(3):
        messages_url = await ensure_sse_reader(session, connect_timeout)
        assert session.sse_responses is not None
        try:
            # Use a dedicated short-lived client for POST so the long-lived GET /sse
            # stream on session.client cannot starve the connection pool (F-014).
            async with httpx.AsyncClient(
                timeout=post_timeout,
                follow_redirects=False,
            ) as post_client:
                async with post_client.stream(
                    "POST",
                    messages_url,
                    json=message,
                    headers=headers,
                ) as response:
                    if response.status_code == 401:
                        raise UpstreamError(
                            -32001, "upstream SSE 401; re-authenticate", needs_reauth=True
                        )
                    if response.status_code >= 400 and response.status_code not in (202, 204):
                        body = (await response.aread())[:500].decode("utf-8", "replace")
                        raise UpstreamError(
                            -32000, f"upstream SSE HTTP {response.status_code}: {body}"
                        )
                    if response.status_code in (202, 204):
                        await response.aclose()
            last_exc = None
            break
        except httpx.TimeoutException as exc:
            last_exc = UpstreamError(-32003, "upstream SSE POST timeout")
            if attempt < 2:
                # Retry once without tearing down a healthy GET reader — restart only
                # if the background reader task has already exited.
                if session.sse_task is not None and session.sse_task.done():
                    await stop_sse_reader(session)
                continue
            raise last_exc from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(-32000, f"upstream SSE POST error: {exc}") from exc
    if last_exc is not None:
        raise last_exc

    deadline = time.time() + method_timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            parsed = await asyncio.wait_for(session.sse_responses.get(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if not isinstance(parsed, dict):
            continue
        if "result" not in parsed and "error" not in parsed:
            continue
        if want_id is not None and parsed.get("id") != want_id:
            continue
        if len(json.dumps(parsed)) > _MAX_RESPONSE_BYTES:
            raise UpstreamError(-32000, "upstream response too large")
        return _wrap_response(
            parsed,
            transport=session.transport,
            msg_id=msg_id,
            needs_reauth=False,
            upstream_status=202,
            session_id=session.session_id,
            duration_ms=int((time.time() - started) * 1000),
        )
    raise UpstreamError(-32003, "upstream SSE response timeout")
