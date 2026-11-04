"""Route entries for the surfaces built over the chat route: responses and MCP.

``edge/routes.py`` is at its size limit, so ``build_routes`` takes these entries from here.
Without MCP dependencies the MCP route stays the declared 501 placeholder, so a gateway
booted without MCP configuration answers honestly.
"""

from __future__ import annotations

from gateway_v2.edge.mcp import GATEWAY_PREFIX, MCP_PATH, MCP_PREFIX, McpRoute
from gateway_v2.edge.mcp_deps import McpDeps
from gateway_v2.edge.oauth_routes import OAuthRoutes, OAuthUnavailable
from gateway_v2.edge.openai_responses import responses_entries
from gateway_v2.edge.responses_store import ResponseStore
from gateway_v2.edge.route_support import placeholder as _placeholder
from gateway_v2.edge.routes import ChatRoute, RouteHandler

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
        token_store=mcp.token_store,
        cap_table=mcp.cap_table,
        sessions=mcp.sessions,
        allowed_origins=mcp.allowed_origins,
        transport_bounds=mcp.transport_bounds,
        policy_metrics=mcp.policy_metrics,
        credentials=mcp.credentials,
        credential_bounds=mcp.credential_bounds,
        plaintext_credential_hosts=mcp.plaintext_credential_hosts,
        public_base=mcp.public_base,
        upstream_oauth=mcp.upstream_oauth,
    )
    exact[("POST", MCP_PATH)] = route.handle_bare
    _oauth_entries(exact, prefix, mcp.oauth if mcp.oauth is not None else OAuthUnavailable())
    for method in ("POST", "GET", "DELETE"):
        prefix[(method, MCP_PREFIX)] = route.handle
        prefix[(method, GATEWAY_PREFIX)] = route.handle_gateway
    return exact, prefix


def _oauth_entries(
    exact: _Entries, prefix: _Entries, oauth: OAuthRoutes | OAuthUnavailable
) -> None:
    """The OAuth 2.1 discovery, registration, authorize and token routes (GW32)."""
    if isinstance(oauth, OAuthUnavailable):
        handlers: dict[str, RouteHandler] = dict.fromkeys(_OAUTH_NAMES, oauth)
    else:
        handlers = {
            "protected": oauth.protected_resource, "as": oauth.authorization_server,
            "register": oauth.register, "authorize": oauth.authorize, "token": oauth.token,
        }
    resource = "/.well-known/oauth-protected-resource"
    exact[("GET", resource)] = handlers["protected"]
    prefix[("GET", resource + "/")] = handlers["protected"]
    exact[("GET", "/.well-known/oauth-authorization-server")] = handlers["as"]
    exact[("POST", "/oauth/register")] = handlers["register"]
    exact[("GET", "/oauth/authorize")] = handlers["authorize"]
    exact[("POST", "/oauth/authorize")] = handlers["authorize"]
    exact[("POST", "/oauth/token")] = handlers["token"]


_OAUTH_NAMES = ("protected", "as", "register", "authorize", "token")
