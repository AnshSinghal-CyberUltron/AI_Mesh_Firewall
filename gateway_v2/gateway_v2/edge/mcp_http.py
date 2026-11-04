"""GW32: the long-lived ``GET`` SSE stream (Requirement 3).

A stream sends ``: keepalive`` comments, re-resolves the caller on an interval (a revoked key ends
the stream), closes at its maximum lifetime, and stops within the cancellation bound of a client
disconnect. Exactly two tasks exist while waiting (a ``receive`` and a sleep) and both are
cancelled in ``finally``, so no task outlives the handler.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from gateway_v2.edge.asgi_sse import ASGIReceive, ASGISend
from gateway_v2.edge.mcp_identity import CallerRefused, resolve_caller
from gateway_v2.edge.mcp_session_gate import header
from gateway_v2.edge.wire import mcp_sse

if TYPE_CHECKING:
    from gateway_v2.edge.mcp import McpRoute, _Ctx

__all__ = ("serve_stream",)

_DISCONNECT = "http.disconnect"


async def serve_stream(
    route: McpRoute, receive: ASGIReceive, send: ASGISend, ctx: _Ctx, session_id: str | None
) -> None:
    """Serve ``GET`` for one proven session. Refusals raise before any byte is sent."""
    from gateway_v2.edge.mcp import _Refuse

    accept = header(ctx.scope, "accept") or ""
    if "text/event-stream" not in accept.lower():
        raise _Refuse("method_not_allowed")
    if session_id is None:
        raise _Refuse("mcp_session_invalid")  # a stream with no session is not a stream
    bounds = route._transport()  # noqa: SLF001
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(k.encode(), v.encode()) for k, v in mcp_sse.SSE_HEADERS],
        }
    )
    await send({"type": "http.response.body", "body": mcp_sse.KEEPALIVE, "more_body": True})
    started = time.monotonic()
    next_recheck = started + bounds.stream_recheck_s
    disconnect: asyncio.Task[object] | None = None
    try:
        while time.monotonic() - started < bounds.stream_max_lifetime_s:
            disconnect = disconnect or asyncio.ensure_future(receive())
            sleeper = asyncio.ensure_future(asyncio.sleep(bounds.stream_keepalive_s))
            try:
                done, _ = await asyncio.wait(
                    {disconnect, sleeper}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                sleeper.cancel()
            if disconnect in done:
                event = disconnect.result()
                if isinstance(event, dict) and event.get("type") == _DISCONNECT:
                    return
                disconnect = None
                continue
            if time.monotonic() >= next_recheck:
                next_recheck = time.monotonic() + bounds.stream_recheck_s
                again = await resolve_caller(route.chat.identity, route.token_store, ctx.scope)
                if isinstance(again, CallerRefused):
                    break  # revoked or unverifiable: end the stream
            await send(
                {"type": "http.response.body", "body": mcp_sse.KEEPALIVE, "more_body": True}
            )
    finally:
        if disconnect is not None:
            disconnect.cancel()
        await send({"type": "http.response.body", "body": b"", "more_body": False})
