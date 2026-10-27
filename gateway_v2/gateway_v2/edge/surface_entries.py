"""Route entries for the surfaces built over the chat route: responses and MCP.

``edge/routes.py`` is at its size limit, so ``build_routes`` takes these entries from here.
Without MCP dependencies the MCP route stays the declared 501 placeholder, so a gateway
booted without MCP configuration answers honestly.
"""

from __future__ import annotations

from gateway_v2.edge.mcp import MCP_PATH, MCP_PREFIX, McpRoute
from gateway_v2.edge.mcp_deps import McpDeps
from gateway_v2.edge.openai_responses import responses_entries
from gateway_v2.edge.responses_store import ResponseStore
from gateway_v2.edge.routes import ChatRoute, RouteHandler, _placeholder

__all__ = ("surface_entries",)

_Entries = dict[tuple[str, str], RouteHandler]


def surface_entries(
    chat: ChatRoute, response_store: ResponseStore | None, mcp: McpDeps | None
) -> tuple[_Entries, _Entries]:
    """``(exact, prefix)`` entries for ``/v1/responses`` and ``/v1/mcp``."""
    exact, prefix = responses_entries(chat, response_store)
    if mcp is None:
        exact[("POST", MCP_PATH)] = _placeholder("mcp")
        return exact, prefix
    route = McpRoute(
        chat=chat,
        registry=mcp.registry,
        broker=mcp.broker,
        org_map=mcp.org_map,
        dns=mcp.dns,
        allow_hosts=mcp.allow_hosts,
        stdio_allowed=mcp.stdio_allowed,
    )
    exact[("POST", MCP_PATH)] = route.handle_bare
    prefix[("POST", MCP_PREFIX)] = route.handle
    return exact, prefix
