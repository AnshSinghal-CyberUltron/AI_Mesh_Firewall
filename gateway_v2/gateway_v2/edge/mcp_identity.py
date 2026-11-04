"""Who is calling an MCP route: an API key or an `MCP_Access_Token` (GW32, Requirement 1 and 7).

Identity is resolved from headers only, before any JSON-RPC method is looked at. Both credential
shapes end in the same place: `IdentityCache` through `resolve_hash`, so a revoked key, a
deactivated org or a plan edit binds a token exactly as it binds the key it was minted from. A
token never carries or caches a `Principal` (R7.2).

`McpCaller` holds no raw key and no raw token. This module imports no framework object; the HTTP
`WWW-Authenticate` header value is built here as plain text and rendered by `edge/mcp.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from gateway_v2.domain import posture
from gateway_v2.domain.state import StoreDataUnavailable
from gateway_v2.edge.asgi_sse import ASGIScope
from gateway_v2.edge.identity_resolver import (
    IdentityResolver,
    TenantIdentity,
    bearer_key,
    hashed_bearer_key,
    identity_of,
)

__all__ = (
    "TOKEN_PREFIX",
    "CallerRefused",
    "McpCaller",
    "TokenResolver",
    "challenge",
    "resolve_caller",
)

TOKEN_PREFIX = "zsmcp_"
"""MCP access tokens carry this prefix. It can never match the 48-char API key shape."""
_PROTECTED_RESOURCE = "/.well-known/oauth-protected-resource"


class TokenResolver(Protocol):
    """Maps a presented token to the sha256 hash of the API key it was minted from."""

    async def resolve(self, token: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class McpCaller:
    """The authenticated caller of one MCP request. No raw credential is held."""

    identity: TenantIdentity
    key_hash: str
    via_token: bool


@dataclass(frozen=True, slots=True)
class CallerRefused:
    """Identity failed. `code` is `invalid_api_key` (401) or `shared_state_unavailable` (503)."""

    code: str


async def resolve_caller(
    resolver: IdentityResolver, token_store: TokenResolver | None, scope: ASGIScope
) -> McpCaller | CallerRefused:
    """Resolve the caller from the request headers. Never reads the query string (R5.2)."""
    token = bearer_key(scope)
    if token is not None and token.startswith(TOKEN_PREFIX):
        return await _from_token(resolver, token_store, token)
    outcome = await identity_of(resolver, scope)
    if outcome.identity is None:
        return CallerRefused(outcome.refusal)
    return McpCaller(outcome.identity, hashed_bearer_key(scope) or "", via_token=False)


async def _from_token(
    resolver: IdentityResolver, token_store: TokenResolver | None, token: str
) -> McpCaller | CallerRefused:
    resolve_hash = getattr(resolver, "resolve_hash", None)
    if token_store is None or resolve_hash is None:
        return CallerRefused(posture.INVALID_API_KEY)  # no OAuth server: no valid tokens
    try:
        key_hash = await token_store.resolve(token)
    except StoreDataUnavailable:
        return CallerRefused(posture.SHARED_STATE_UNAVAILABLE)
    if key_hash is None:
        return CallerRefused(posture.INVALID_API_KEY)
    outcome = await resolve_hash(key_hash)
    if outcome.identity is None:
        return CallerRefused(outcome.refusal)
    return McpCaller(outcome.identity, key_hash, via_token=True)


def challenge(public_base: str) -> str:
    """The MCP 401 `WWW-Authenticate` value (R7.3). `resource_metadata` appears only when a
    validated public base URL is configured: a guessed URL is never advertised (R6.2)."""
    if not public_base:
        return "Bearer"
    return f'Bearer resource_metadata="{public_base.rstrip("/")}{_PROTECTED_RESOURCE}"'
