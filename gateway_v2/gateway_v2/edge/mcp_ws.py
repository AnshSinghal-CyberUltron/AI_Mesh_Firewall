"""GW32: the MCP WebSocket transport (Requirement 5). One JSON-RPC message per text frame.

Every message runs the SAME pipeline an HTTP POST runs (`McpRoute._serve`), so the allow-list, the
cap, the credential gate and the output scan cannot be bypassed by choosing this transport. The
frame body is handed to that pipeline as if it were a POST body, and the pipeline's reply bytes
are sent back as one text frame. Identity is re-resolved for every message, so a revoked key or a
policy edit takes effect on the next frame.

Refusals before ``accept`` are a ``websocket.close`` with no accept, which ASGI servers surface as
an HTTP 403 handshake. Over a connection bound the close code is 1013. A binary frame closes 1003,
an oversize frame 1009, a revoked or unauthenticated caller 1008.

Known gap, recorded in the handoff: a revocation that happens WHILE a message is in flight is seen
at the next frame (and on the idle re-check), not by cancelling the in-flight broker call.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend
from gateway_v2.edge.mcp import McpRoute
from gateway_v2.edge.mcp_identity import CallerRefused, resolve_caller
from gateway_v2.edge.mcp_paths import McpPath, classify_gateway_path, parse_mcp_path, raw_path
from gateway_v2.edge.mcp_session_gate import check_origin
from gateway_v2.edge.wire import jsonrpc
from gateway_v2.runtime.resources_mcp import (
    WS_CLOSE_POLICY,
    WS_CLOSE_RETRY,
    WS_CLOSE_TOO_BIG,
    WS_CLOSE_UNSUPPORTED,
    McpTransportBounds,
)

__all__ = ("WsLimiter", "serve", "ws_path")

_CLOSE_NORMAL = 1000


def ws_path(raw: str) -> McpPath | None:
    """The MCP path a WebSocket upgrade addresses, or ``None`` (not an MCP socket)."""
    parsed = parse_mcp_path(raw)
    if parsed is not None:
        return parsed
    gateway = classify_gateway_path(raw)
    return McpPath(gateway.server_slug, gateway.org) if gateway.kind == "mcp" else None


@dataclass(slots=True)
class WsLimiter:
    """Per-worker connection counters (instance-owned; one asyncio thread, so no lock)."""

    bounds: McpTransportBounds
    _key: dict[str, int] = field(default_factory=dict)
    _org: dict[str, int] = field(default_factory=dict)
    _total: int = 0

    def acquire(self, key_id: str, org: str) -> bool:
        if (
            self._total >= self.bounds.ws_global
            or self._key.get(key_id, 0) >= self.bounds.ws_per_key
            or self._org.get(org, 0) >= self.bounds.ws_per_org
        ):
            return False
        self._total += 1
        self._key[key_id] = self._key.get(key_id, 0) + 1
        self._org[org] = self._org.get(org, 0) + 1
        return True

    def release(self, key_id: str, org: str) -> None:
        self._total = max(0, self._total - 1)
        for table, name in ((self._key, key_id), (self._org, org)):
            left = table.get(name, 0) - 1
            if left > 0:
                table[name] = left
            else:
                table.pop(name, None)

    def open_count(self) -> int:
        return self._total


async def _close(send: ASGISend, code: int) -> None:
    await send({"type": "websocket.close", "code": code})


async def serve(
    route: McpRoute,
    limiter: WsLimiter | None,
    scope: ASGIScope,
    receive: ASGIReceive,
    send: ASGISend,
) -> None:
    """Handle one WebSocket connection end to end."""
    event = await receive()
    if event.get("type") != "websocket.connect":
        return
    path = ws_path(raw_path(scope))
    if path is None:
        await _close(send, _CLOSE_NORMAL)
        return
    caller = await _admit(route, path, scope)
    if caller is None or limiter is None:
        await _close(send, WS_CLOSE_POLICY if caller is None else WS_CLOSE_RETRY)
        return
    key_id, org = caller.identity.principal.key_id, caller.identity.org_id
    if not limiter.acquire(key_id, org):
        await _close(send, WS_CLOSE_RETRY)
        return
    try:
        await send({"type": "websocket.accept"})
        await _loop(route, path, scope, receive, send)
    finally:
        limiter.release(key_id, org)


async def _admit(route: McpRoute, path: McpPath, scope: ASGIScope):  # type: ignore[no-untyped-def]
    """Origin, identity and org match for the upgrade; ``None`` = refuse (close 1008)."""
    if not check_origin(scope, route.allowed_origins):
        return None
    caller = await resolve_caller(route.chat.identity, route.token_store, scope)
    if isinstance(caller, CallerRefused):
        return None
    if (
        path.path_org is not None
        and route.org_map.control_slug(caller.identity.org_id) != path.path_org
    ):
        return None
    return caller


async def _loop(
    route: McpRoute, path: McpPath, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
) -> None:
    bounds = route._transport()  # noqa: SLF001
    frame_limit = bounds.ws_frame_bytes
    wait_s = min(bounds.ws_idle_s, bounds.stream_recheck_s)
    idle = 0.0
    pending: Mapping[str, object] | None = None
    while True:
        try:
            if pending is not None:
                event, pending = pending, None
            else:
                event = await asyncio.wait_for(receive(), wait_s)
        except TimeoutError:
            idle += wait_s
            again = await resolve_caller(route.chat.identity, route.token_store, scope)
            if isinstance(again, CallerRefused):
                await _close(send, WS_CLOSE_POLICY)
                return
            if idle >= bounds.ws_idle_s:
                await _close(send, _CLOSE_NORMAL)
                return
            continue
        idle = 0.0
        if event.get("type") == "websocket.disconnect":
            return
        text, raw = event.get("text"), event.get("bytes")
        if raw is not None and text is None:
            await _close(send, WS_CLOSE_UNSUPPORTED)
            return
        if not isinstance(text, str) or len(text.encode()) > frame_limit:
            await _close(send, WS_CLOSE_TOO_BIG)
            return
        if text.lstrip().startswith("["):  # a batch is answered, never forwarded
            status, body = jsonrpc.error(None, "mcp_invalid_request")
            del status
            await send({"type": "websocket.send", "text": body.decode()})
            continue
        pending = await _run_message(route, path, scope, receive, send, text)


async def _run_message(
    route: McpRoute,
    path: McpPath,
    scope: ASGIScope,
    receive: ASGIReceive,
    send: ASGISend,
    text: str,
) -> Mapping[str, object] | None:
    """Run one frame through the HTTP pipeline. Returns an event that arrived while it ran
    (a pipelined frame or a disconnect), else ``None``. A disconnect cancels the in-flight call."""
    reply: list[bytes] = []

    async def collect(event: Mapping[str, object]) -> None:
        if event.get("type") == "http.response.body":
            body = event.get("body")
            if isinstance(body, (bytes, bytearray)):
                reply.append(bytes(body))

    sent = False

    async def one_body() -> Mapping[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": text.encode(), "more_body": False}

    post_scope = {**scope, "type": "http", "method": "POST"}
    task = asyncio.ensure_future(route.run_message(post_scope, path, one_body, collect))
    watcher = asyncio.ensure_future(receive())
    try:
        await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done():
            task.cancel()  # the client went away (or pipelined): do not leave the call running
            await asyncio.gather(task, return_exceptions=True)
            return watcher.result() if watcher.done() else None
        task.result()
    finally:
        if not watcher.done():
            watcher.cancel()
    payload = b"".join(reply)
    if payload:
        if _is_identity_refusal(json.loads(payload)):  # a frame is always one JSON document
            await _close(send, WS_CLOSE_POLICY)  # revoked / unauthenticated: end the socket
            return {"type": "websocket.disconnect"}
        await send({"type": "websocket.send", "text": payload.decode()})
    return watcher.result() if watcher.done() else None


def _is_identity_refusal(document: object) -> bool:
    if not isinstance(document, dict):
        return False
    data = (
        (document.get("error") or {}).get("data")
        if isinstance(document.get("error"), dict)
        else None
    )
    return isinstance(data, dict) and data.get("code") in (
        "invalid_api_key",
        "shared_state_unavailable",
    )
