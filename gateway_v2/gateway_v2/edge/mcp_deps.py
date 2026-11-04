"""The MCP-only boot dependencies (GW17). ``edge/routes.py`` imports this without a cycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gateway_v2.admit.mcp_cap import CallCapTable
from gateway_v2.admit.mcp_metrics import McpPolicyMetrics
from gateway_v2.admit.mcp_sessions import SessionStore
from gateway_v2.dispatch.mcp_broker import BrokerClient
from gateway_v2.dispatch.mcp_credentials import CredentialService
from gateway_v2.dispatch.url_guard import Resolver
from gateway_v2.edge.mcp_identity import TokenResolver
from gateway_v2.edge.mcp_registry import McpRegistry, OrgMap
from gateway_v2.runtime.resources_mcp import McpCredentialBounds, McpTransportBounds

if TYPE_CHECKING:  # routes.py imports this module; the OAuth routes import routes.py
    from gateway_v2.edge.oauth_routes import OAuthRoutes, OAuthUnavailable

__all__ = ("McpDeps",)


@dataclass(frozen=True, slots=True)
class McpDeps:
    """Built once at boot. ``McpRoute`` adds the booted chat route."""

    registry: McpRegistry
    broker: BrokerClient
    org_map: OrgMap
    dns: Resolver
    allow_hosts: frozenset[str]
    stdio_allowed: frozenset[str]
    token_store: TokenResolver | None = None
    """GW32: resolves a `zsmcp_` token to its key hash. `None` = no OAuth server (fail closed)."""
    credentials: CredentialService | None = None
    """GW17b: upstream credential service. `None` + an authenticated server = refused."""
    credential_bounds: McpCredentialBounds | None = None
    plaintext_credential_hosts: frozenset[str] = frozenset()
    """GW17b: hosts allowed a credential over plaintext (the plaintext-hosts env allow-list)."""
    cap_table: CallCapTable | None = None
    """GW17c: per-key call-cap leases. `None` + a capped key = refused (fail closed)."""
    policy_metrics: McpPolicyMetrics | None = None
    sessions: SessionStore | None = None
    """GW32: shared session store. `None` = no session id is issued; a presented one is invalid."""
    oauth: OAuthRoutes | OAuthUnavailable | None = None
    """GW32: the OAuth 2.1 routes. `None` = not configured: every OAuth route is a declared 503."""
    allowed_origins: frozenset[str] = frozenset()
    """GW32: exact-match `Origin` allow-list (`AMF_MCP_ALLOWED_ORIGINS`), beyond same-authority."""
    transport_bounds: McpTransportBounds | None = None
    public_base: str = ""
    """GW32: validated `AMF_GATEWAY_PUBLIC_URL`; empty = no `resource_metadata` is advertised."""
