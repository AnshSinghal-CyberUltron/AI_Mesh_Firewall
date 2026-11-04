"""GW32 boot wiring: the WebSocket scope wrapper.

`edge/app.py` declines every non-`http`/`lifespan` scope and is at its size gate, so it is not
edited. `wrap_mcp_app` returns an ASGI callable that sends a `websocket` scope whose path is an MCP
path to `mcp_ws.serve`, and delegates every other scope, and every other websocket path, to the
wrapped app unchanged (so today's decline behaviour for other paths is preserved).

The `McpRoute` is the SAME instance the HTTP route table holds (taken from the table's bound
handler), so the WebSocket path shares the session store, cap table, credential cache and token
store with HTTP. If no MCP route is registered the wrapper declines WebSockets (fail closed).

`ws_limiter` is created by the caller and shared: `None` closes every socket 1013 before accept.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend
from gateway_v2.edge.mcp import MCP_PREFIX, McpRoute
from gateway_v2.edge.mcp_paths import raw_path
from gateway_v2.edge.mcp_ws import WsLimiter, serve, ws_path

__all__ = ("route_of", "wrap_mcp_app")


_Asgi = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]


def route_of(app: object) -> McpRoute | None:
    """The booted `McpRoute`, read from the app's prefix table; ``None`` if MCP is not wired."""
    skin = getattr(app, "__self__", app)  # `build_app` returns the skin's bound `asgi`
    table = getattr(skin, "prefix_routes", None)
    handler = table.get(("POST", MCP_PREFIX)) if isinstance(table, dict) else None
    owner = getattr(handler, "__self__", None)
    return owner if isinstance(owner, McpRoute) else None


def wrap_mcp_app(app: _Asgi, ws_limiter: WsLimiter | None) -> _Asgi:
    """Return ``app`` with MCP WebSocket support."""
    route = route_of(app)

    async def wrapped(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        if scope.get("type") == "websocket" and route is not None:
            path = raw_path(scope)
            if ws_path(path) is not None:
                await serve(route, ws_limiter, scope, receive, send)
                return
        await app(scope, receive, send)

    return wrapped
