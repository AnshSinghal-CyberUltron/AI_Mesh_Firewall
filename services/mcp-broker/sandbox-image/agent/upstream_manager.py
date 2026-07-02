"""In-sandbox HTTP/SSE upstream MCP proxy (SANDBOX_TRANSPORT_CONTRACT §5.2–5.3)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

LOG = logging.getLogger("sandbox_agent.upstream")

_CONNECT_TIMEOUT = float(os.environ.get("MCP_AGENT_CONNECT_TIMEOUT", "30"))
_INIT_TIMEOUT = float(os.environ.get("MCP_STDIO_INIT_TIMEOUT", "120"))
_METHOD_TIMEOUT = float(os.environ.get("MCP_STDIO_METHOD_TIMEOUT", "60"))
_MAX_RESPONSE_BYTES = int(os.environ.get("MCP_AGENT_MAX_RESPONSE_BYTES", str(8 * 1024 * 1024)))

_sessions: dict[str, UpstreamSession] = {}
_registry_lock = asyncio.Lock()


class UpstreamError(Exception):
    def __init__(self, code: int, message: str, *, needs_reauth: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.needs_reauth = needs_reauth


def _normalize_host(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return ""
    try:
        import idna

        return idna.encode(host).decode("ascii")
    except Exception:
        return host


def _validate_upstream(upstream: dict[str, Any]) -> None:
    if upstream.get("oauth_client_role") == "client":
        raise UpstreamError(-32602, "oauth_client_role=client is forbidden in sandbox")
    url = (upstream.get("url") or "").strip()
    if not url:
        raise UpstreamError(-32602, "upstream.url is required")
    allowed = upstream.get("allowed_hosts") or []
    if not allowed:
        raise UpstreamError(-32602, "upstream.allowed_hosts is required")
    host = urlparse(url).hostname
    if not host:
        raise UpstreamError(-32602, "upstream.url has no hostname")
    norm_host = _normalize_host(host)
    allowed_norm = {_normalize_host(h) for h in allowed if h}
    try:
        ipaddress.ip_address(host)
        if norm_host not in allowed_norm and host not in allowed_norm:
            raise UpstreamError(-32002, f"egress denied: IP host {host!r} not in allowlist")
    except ValueError:
        if norm_host not in allowed_norm:
            raise UpstreamError(-32002, f"egress denied: host {host!r} not in allowlist")


@dataclass
class UpstreamSession:
    server_slug: str
    transport: str
    url: str
    allowed_hosts: list[str]
    headers: dict[str, str]
    session_id: str | None = None
    sse_messages_url: str | None = None
    initialized: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client: httpx.AsyncClient | None = None
    ws: Any | None = None

    def config_key(self) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
        return (
            self.transport,
            self.url,
            json.dumps(self.allowed_hosts, sort_keys=True),
            tuple(sorted(self.headers.items())),
        )


def _timeouts(timeouts: dict[str, float] | None) -> tuple[float, float, float]:
    t = timeouts or {}
    return (
        float(t.get("connect_seconds", _CONNECT_TIMEOUT)),
        float(t.get("init_seconds", _INIT_TIMEOUT)),
        float(t.get("method_seconds", _METHOD_TIMEOUT)),
    )


async def _get_session(
    server_slug: str,
    transport: str,
    upstream: dict[str, Any],
    connect_timeout: float,
) -> UpstreamSession:
    _validate_upstream(upstream)
    url = upstream["url"].strip()
    allowed_hosts = list(upstream.get("allowed_hosts") or [])
    headers = dict(upstream.get("headers") or {})
    if upstream.get("session_id"):
        headers.setdefault("Mcp-Session-Id", str(upstream["session_id"]))

    async with _registry_lock:
        sess = _sessions.get(server_slug)
        new_cfg = (transport, url, json.dumps(allowed_hosts, sort_keys=True), tuple(sorted(headers.items())))
        if sess is not None:
            old_cfg = sess.config_key()
            if old_cfg == new_cfg:
                if transport == "websocket" and sess.ws is not None:
                    return sess
                if transport != "websocket" and sess.client is not None:
                    return sess
            await _close_session_unlocked(server_slug)

        client = None
        if transport != "websocket":
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect_timeout, read=connect_timeout + _METHOD_TIMEOUT),
                follow_redirects=False,
            )
        sess = UpstreamSession(
            server_slug=server_slug,
            transport=transport,
            url=url,
            allowed_hosts=allowed_hosts,
            headers=headers,
            client=client,
        )
        if "Mcp-Session-Id" in headers:
            sess.session_id = headers["Mcp-Session-Id"]
        _sessions[server_slug] = sess
        return sess


async def _close_session_unlocked(server_slug: str) -> None:
    sess = _sessions.pop(server_slug, None)
    if sess is None:
        return
    if sess.client:
        await sess.client.aclose()
    if sess.ws:
        from agent.ws_manager import close_ws

        await close_ws(sess)


async def shutdown_all() -> None:
    async with _registry_lock:
        keys = list(_sessions.keys())
    for key in keys:
        async with _registry_lock:
            await _close_session_unlocked(key)
    LOG.info("All upstream MCP sessions closed")


async def list_connections() -> list[dict[str, Any]]:
    result = []
    for slug, sess in _sessions.items():
        result.append({
            "server_slug": slug,
            "transport": sess.transport,
            "url": sess.url,
            "initialized": sess.initialized,
            "session_id": sess.session_id,
        })
    return result


def connection_counts() -> dict[str, int]:
    counts: dict[str, int] = {
        "stdio": 0,
        "streamable-http": 0,
        "sse": 0,
        "websocket": 0,
    }
    for sess in _sessions.values():
        counts[sess.transport] = counts.get(sess.transport, 0) + 1
    return counts


def _wrap_response(
    payload: dict[str, Any],
    *,
    transport: str,
    msg_id: int | str | None,
    needs_reauth: bool,
    upstream_status: int,
    session_id: str | None,
    duration_ms: int,
) -> dict[str, Any]:
    out = dict(payload)
    if msg_id is not None and "id" not in out:
        out["id"] = msg_id
    out["_meta"] = {
        "transport": transport,
        "needs_reauth": needs_reauth,
        "upstream_status": upstream_status,
        "session_id": session_id,
        "duration_ms": duration_ms,
    }
    return out


def _error_response(
    msg_id: int | str | None,
    exc: UpstreamError,
    *,
    transport: str,
    session_id: str | None = None,
    upstream_status: int = 0,
    duration_ms: int = 0,
) -> dict[str, Any]:
    return _wrap_response(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": exc.code, "message": exc.message},
        },
        transport=transport,
        msg_id=msg_id,
        needs_reauth=exc.needs_reauth,
        upstream_status=upstream_status,
        session_id=session_id,
        duration_ms=duration_ms,
    )


async def _read_json_response(
    response: httpx.Response,
    *,
    transport: str,
    msg_id: int | str | None,
    session: UpstreamSession,
    started: float,
) -> dict[str, Any]:
    if response.status_code == 401:
        raise UpstreamError(
            -32001,
            "upstream returned 401; re-authenticate",
            needs_reauth=True,
        )
    if response.status_code >= 400:
        raise UpstreamError(
            -32000,
            f"upstream HTTP {response.status_code}: {response.text[:500]}",
        )
    raw = await response.aread()
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise UpstreamError(-32000, "upstream response too large")
    if not raw:
        return _wrap_response(
            {"jsonrpc": "2.0", "id": msg_id, "result": {}},
            transport=transport,
            msg_id=msg_id,
            needs_reauth=False,
            upstream_status=response.status_code,
            session_id=session.session_id,
            duration_ms=int((time.time() - started) * 1000),
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpstreamError(-32000, f"upstream returned non-JSON: {exc}") from exc
    if isinstance(data, dict) and "Mcp-Session-Id" in response.headers:
        session.session_id = response.headers["Mcp-Session-Id"]
    elif isinstance(data, dict):
        sid = response.headers.get("mcp-session-id")
        if sid:
            session.session_id = sid
    return _wrap_response(
        data if isinstance(data, dict) else {"jsonrpc": "2.0", "id": msg_id, "result": data},
        transport=transport,
        msg_id=msg_id,
        needs_reauth=False,
        upstream_status=response.status_code,
        session_id=session.session_id,
        duration_ms=int((time.time() - started) * 1000),
    )


async def _ensure_sse_endpoint(session: UpstreamSession, connect_timeout: float) -> str:
    if session.sse_messages_url:
        return session.sse_messages_url
    assert session.client is not None
    headers = {**session.headers, "Accept": "text/event-stream"}
    try:
        async with session.client.stream(
            "GET",
            session.url,
            headers=headers,
            timeout=connect_timeout,
        ) as response:
            if response.status_code == 401:
                raise UpstreamError(-32001, "upstream SSE 401; re-authenticate", needs_reauth=True)
            if response.status_code >= 400:
                raise UpstreamError(-32000, f"upstream SSE HTTP {response.status_code}")
            event_type = ""
            data_lines: list[str] = []
            base = f"{urlparse(session.url).scheme}://{urlparse(session.url).netloc}"
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line == "" and data_lines:
                    data = "\n".join(data_lines)
                    data_lines = []
                    if event_type == "endpoint" or "sessionId" in data or data.startswith("/"):
                        path = data.split("sessionId=")[-1] if "sessionId=" in data else data
                        if data.startswith("/") or data.startswith("http"):
                            session.sse_messages_url = (
                                data if data.startswith("http") else urljoin(base, data.split("\n")[0])
                            )
                        else:
                            session.sse_messages_url = urljoin(
                                base, f"/messages?sessionId={path}"
                            )
                        session.session_id = path if "sessionId=" in data else session.session_id
                        return session.sse_messages_url
                    event_type = ""
            raise UpstreamError(-32000, "upstream SSE: no endpoint event received")
    except httpx.TimeoutException as exc:
        raise UpstreamError(-32003, "upstream SSE connect timeout") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(-32000, f"upstream SSE error: {exc}") from exc


async def _post_streamable_http(
    session: UpstreamSession,
    message: dict[str, Any],
    method_timeout: float,
    msg_id: int | str | None,
) -> dict[str, Any]:
    """POST one JSON-RPC message to a Streamable-HTTP upstream and return the reply.

    Streamable-HTTP servers answer a POST with either a single ``application/json``
    body OR a ``text/event-stream`` that STAYS OPEN for server→client messages. A
    non-streaming ``client.post()`` (or ``response.aread()``) would block waiting for
    a body/EOF that never arrives — the classic hang. So we STREAM the response and
    return on the FIRST SSE ``data:`` frame that is a JSON-RPC *response* (has
    ``result``/``error``) matching our request id, then exit the context (closing the
    stream). Plain-JSON responses are read directly.
    """
    assert session.client is not None
    headers = {
        **session.headers,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session.session_id:
        headers["Mcp-Session-Id"] = session.session_id
    started = time.time()
    want_id = message.get("id")
    try:
        async with session.client.stream(
            "POST", session.url, json=message, headers=headers, timeout=method_timeout,
        ) as response:
            if response.status_code == 401:
                raise UpstreamError(-32001, "upstream returned 401; re-authenticate", needs_reauth=True)
            if response.status_code >= 400:
                body = (await response.aread())[:500].decode("utf-8", "replace")
                raise UpstreamError(-32000, f"upstream HTTP {response.status_code}: {body}")
            sid = response.headers.get("mcp-session-id")
            if sid:
                session.session_id = sid

            def _wrap(payload: dict[str, Any]) -> dict[str, Any]:
                return _wrap_response(
                    payload if isinstance(payload, dict)
                    else {"jsonrpc": "2.0", "id": msg_id, "result": payload},
                    transport=session.transport, msg_id=msg_id, needs_reauth=False,
                    upstream_status=response.status_code, session_id=session.session_id,
                    duration_ms=int((time.time() - started) * 1000),
                )

            if "text/event-stream" not in response.headers.get("content-type", ""):
                raw = await response.aread()
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise UpstreamError(-32000, "upstream response too large")
                if not raw:
                    return _wrap({"jsonrpc": "2.0", "id": msg_id, "result": {}})
                try:
                    return _wrap(json.loads(raw))
                except json.JSONDecodeError as exc:
                    raise UpstreamError(-32000, f"upstream returned non-JSON: {exc}") from exc

            data_lines: list[str] = []
            total = 0
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    total += len(line)
                    if total > _MAX_RESPONSE_BYTES:
                        raise UpstreamError(-32000, "upstream response too large")
                elif line == "" and data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    try:
                        parsed = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    # Ignore server-initiated requests/notifications; return the RESPONSE
                    # to our request (matching id, or any response if we didn't set one).
                    if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
                        if want_id is None or parsed.get("id") == want_id:
                            return _wrap(parsed)
            raise UpstreamError(-32000, "upstream SSE closed without a matching response")
    except httpx.TimeoutException as exc:
        raise UpstreamError(-32003, "upstream request timeout") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(-32000, f"upstream HTTP error: {exc}") from exc


async def _post_sse(
    session: UpstreamSession,
    message: dict[str, Any],
    connect_timeout: float,
    method_timeout: float,
    msg_id: int | str | None,
) -> dict[str, Any]:
    messages_url = await _ensure_sse_endpoint(session, connect_timeout)
    assert session.client is not None
    headers = {**session.headers, "Content-Type": "application/json"}
    started = time.time()
    try:
        response = await session.client.post(
            messages_url,
            json=message,
            headers=headers,
            timeout=method_timeout,
        )
    except httpx.TimeoutException as exc:
        raise UpstreamError(-32003, "upstream SSE POST timeout") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(-32000, f"upstream SSE POST error: {exc}") from exc
    return await _read_json_response(
        response,
        transport=session.transport,
        msg_id=msg_id,
        session=session,
        started=started,
    )


async def _initialize_session(
    session: UpstreamSession, connect_timeout: float, init_timeout: float
) -> None:
    """Run the MCP initialize handshake against the upstream, once per session.

    The gateway routes SINGLE methods through the sandbox (it no longer performs the
    handshake itself for sandbox-routed transports), so the agent must ``initialize``
    the upstream before the first real method, then send ``notifications/initialized``.
    """
    init_msg = {
        "jsonrpc": "2.0", "id": "_agent_init", "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-sandbox-agent", "version": "0.2.0"},
        },
    }
    if session.transport == "streamable-http":
        await _post_streamable_http(session, init_msg, init_timeout, "_agent_init")
    elif session.transport == "sse":
        await _post_sse(session, init_msg, connect_timeout, init_timeout, "_agent_init")
    # Best-effort notifications/initialized (a notification — no response expected).
    try:
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        headers = {**session.headers, "Content-Type": "application/json"}
        if session.session_id:
            headers["Mcp-Session-Id"] = session.session_id
        assert session.client is not None
        target = session.sse_messages_url or session.url
        await session.client.post(target, json=notif, headers=headers, timeout=connect_timeout)
    except Exception:  # noqa: BLE001 — notification delivery is best-effort
        pass
    session.initialized = True


async def send_upstream_jsonrpc(
    server_slug: str,
    transport: str,
    upstream: dict[str, Any],
    method: str,
    params: dict | list | None,
    msg_id: int | str | None,
    *,
    timeouts: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Forward one JSON-RPC message to an HTTP, SSE, or WebSocket upstream MCP server."""
    if transport not in ("streamable-http", "sse", "websocket"):
        raise UpstreamError(-32004, f"unsupported upstream transport: {transport}")

    connect_timeout, init_timeout, method_timeout = _timeouts(timeouts)
    session = await _get_session(server_slug, transport, upstream, connect_timeout)

    if method == "initialize" and session.initialized:
        return _wrap_response(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": f"MCP Sandbox Agent — {server_slug} ({transport})",
                        "version": "0.1.0",
                    },
                },
            },
            transport=transport,
            msg_id=msg_id,
            needs_reauth=False,
            upstream_status=200,
            session_id=session.session_id,
            duration_ms=0,
        )

    if method == "notifications/initialized":
        session.initialized = True
        return _wrap_response(
            {"jsonrpc": "2.0", "result": {}},
            transport=transport,
            msg_id=msg_id,
            needs_reauth=False,
            upstream_status=200,
            session_id=session.session_id,
            duration_ms=0,
        )

    use_id = msg_id if msg_id is not None else 1
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": use_id, "method": method}
    if params is not None:
        message["params"] = params

    async with session.lock:
        try:
            # Auto-handshake: initialize the upstream session on first real method
            # (streamable-http/sse only; websocket manages its own handshake).
            if (
                not session.initialized
                and transport in ("streamable-http", "sse")
                and method not in ("initialize", "notifications/initialized")
            ):
                await _initialize_session(session, connect_timeout, init_timeout)
            if transport == "streamable-http":
                result = await _post_streamable_http(session, message, method_timeout, msg_id)
            elif transport == "sse":
                result = await _post_sse(
                    session, message, connect_timeout, method_timeout, msg_id
                )
            else:
                from agent.ws_manager import send_ws_jsonrpc

                result = await send_ws_jsonrpc(
                    session, message, connect_timeout, method_timeout, msg_id
                )
            if method == "initialize" and "error" not in result:
                session.initialized = True
            return result
        except UpstreamError as exc:
            return _error_response(
                msg_id,
                exc,
                transport=transport,
                session_id=session.session_id,
                duration_ms=0,
            )
