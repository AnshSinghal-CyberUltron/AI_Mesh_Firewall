"""In-sandbox WebSocket upstream MCP proxy (SANDBOX_TRANSPORT_CONTRACT §5.4)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from agent.upstream_manager import (
    UpstreamError,
    UpstreamSession,
    _MAX_RESPONSE_BYTES,
    _error_response,
    _wrap_response,
)

LOG = logging.getLogger("sandbox_agent.ws")


def _validate_ws_url(url: str) -> None:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("ws", "wss"):
        raise UpstreamError(-32602, f"websocket upstream url must use ws:// or wss://, got {scheme!r}")


async def ensure_ws_connected(session: UpstreamSession, connect_timeout: float) -> Any:
    """Open or reuse the WebSocket for *session*."""
    _validate_ws_url(session.url)
    ws = session.ws
    if ws is not None:
        try:
            if ws.state.name == "OPEN":
                return ws
        except AttributeError:
            pass
        try:
            await ws.close()
        except Exception:
            pass
        session.ws = None

    extra_headers = list(session.headers.items())
    try:
        ws = await asyncio.wait_for(
            websockets.connect(
                session.url,
                additional_headers=extra_headers,
                open_timeout=connect_timeout,
                # CHG-0131: enforce the agent's configured response cap at the LIBRARY
                # level. Without max_size, websockets defaults to 1 MiB — silently
                # OVERRIDING _MAX_RESPONSE_BYTES (8 MiB default) on the ws transport only:
                # a raised MCP_AGENT_MAX_RESPONSE_BYTES was ignored (legit 1-8 MiB ws
                # responses failed as a connection error), and a LOWERED cap was
                # under-enforced (ws still allowed up to 1 MiB). The library also rejects
                # an over-cap frame BEFORE buffering it whole (real pre-buffer OOM guard),
                # unlike the post-recv len() check below. Parity with the http/sse/stdio
                # _MAX_RESPONSE_BYTES caps.
                max_size=_MAX_RESPONSE_BYTES,
            ),
            timeout=connect_timeout,
        )
    except InvalidStatus as exc:
        status = exc.response.status_code
        if status == 401:
            raise UpstreamError(
                -32001,
                "upstream WS handshake 401; re-authenticate",
                needs_reauth=True,
            ) from exc
        raise UpstreamError(-32000, f"upstream WS handshake HTTP {status}") from exc
    except TimeoutError as exc:
        raise UpstreamError(-32003, "upstream WS connect timeout") from exc
    except Exception as exc:
        raise UpstreamError(-32000, f"upstream WS connect error: {exc}") from exc

    session.ws = ws
    return ws


async def close_ws(session: UpstreamSession) -> None:
    if session.ws is None:
        return
    try:
        await session.ws.close()
    except Exception:
        pass
    session.ws = None


async def send_ws_jsonrpc(
    session: UpstreamSession,
    message: dict[str, Any],
    connect_timeout: float,
    method_timeout: float,
    msg_id: int | str | None,
) -> dict[str, Any]:
    """Send one JSON-RPC request over WebSocket and wait for the matching response."""
    started = time.time()
    try:
        ws = await ensure_ws_connected(session, connect_timeout)
        await ws.send(json.dumps(message))
        target_id = message.get("id")
        deadline = time.time() + method_timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError:
                break
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise UpstreamError(-32000, "upstream WS response too large")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise UpstreamError(-32000, f"upstream WS non-JSON: {exc}") from exc
            if not isinstance(data, dict):
                continue
            if "error" in data:
                err = data.get("error") or {}
                code = err.get("code", 0)
                msg = str(err.get("message", ""))
                if code == 401 or "unauthorized" in msg.lower():
                    raise UpstreamError(
                        -32001,
                        "upstream WS unauthorized; re-authenticate",
                        needs_reauth=True,
                    )
            if target_id is not None and data.get("id") == target_id:
                return _wrap_response(
                    data,
                    transport=session.transport,
                    msg_id=msg_id,
                    needs_reauth=False,
                    upstream_status=200,
                    session_id=session.session_id,
                    duration_ms=int((time.time() - started) * 1000),
                )
        raise UpstreamError(-32003, "upstream WS response timeout")
    except ConnectionClosed as exc:
        session.ws = None
        raise UpstreamError(-32000, f"upstream WS closed: {exc}") from exc
    except UpstreamError as exc:
        return _error_response(
            msg_id,
            exc,
            transport=session.transport,
            session_id=session.session_id,
            duration_ms=int((time.time() - started) * 1000),
        )
