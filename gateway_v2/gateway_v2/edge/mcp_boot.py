"""Boot wiring for the GW32 / GW17b / GW17c services (the one place they are constructed).

`build_dev_mcp` builds the GW17 registry, broker and org map; this module adds, from the same
inputs, the shared session store, the call-cap table, the upstream credential service, the OAuth
2.1 server and the policy metrics, and returns the result as one frozen `McpDeps`. Each service
needs the shared store; without a store the service is left unset and the route fails closed:

* no store: no session id is issued, a capped key is refused, OAuth routes answer a declared 503
  and the refresh lock is per-worker (the boot note says so).
* OAuth needs `AMF_GATEWAY_PUBLIC_URL` (https, or loopback http) and `AMF_OAUTH_HASH_KEY` (>= 32
  bytes). One without the other refuses to boot, so a typo cannot leave OAuth half configured.

No secret is logged: the notes name settings, never values.
"""
from __future__ import annotations

import dataclasses
import random
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from gateway_v2.admit.mcp_cap import CallCapTable
from gateway_v2.admit.mcp_metrics import McpPolicyMetrics
from gateway_v2.admit.mcp_sessions import ValkeySessionStore
from gateway_v2.admit.oauth_server import OAuthConfig, OAuthServer, OAuthTokenResolver
from gateway_v2.admit.oauth_store import ValkeyKV
from gateway_v2.dispatch.mcp_credentials import (
    ControlCredentialClient,
    CredentialCache,
    CredentialService,
)
from gateway_v2.dispatch.mcp_refresh_lock import LocalRefreshLock, ValkeyRefreshLock
from gateway_v2.dispatch.mcp_upstream_oauth import ControlOAuthFlowClient
from gateway_v2.dispatch.pinned_transport import pinned_http, pinned_token_endpoint
from gateway_v2.dispatch.url_guard import parse_allow_hosts
from gateway_v2.edge.dev_components import DevProfileError, env_text
from gateway_v2.edge.mcp_deps import McpDeps
from gateway_v2.edge.mcp_upstream_routes import UpstreamOAuthRoutes
from gateway_v2.edge.oauth_routes import OAuthRoutes
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.resources_mcp import (
    derive_cap_bounds,
    derive_credential_bounds,
    derive_transport_bounds,
)
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys

__all__ = ("extend_mcp_deps",)

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def _control_call() -> Any:
    async def call(
        method: str,
        url: str,
        headers: Mapping[str, str],
        query: Mapping[str, str],
        body: Mapping[str, object] | None,
        timeout_s: float,
        max_bytes: int,
    ) -> tuple[int, bytes]:
        import httpx

        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
            async with client.stream(
                method,
                url,
                headers=dict(headers),
                params=dict(query),
                json=None if body is None else dict(body),
            ) as response:
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        return response.status_code, b"\x00" * (max_bytes + 1)
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks)

    return call


def extend_mcp_deps(
    base: McpDeps,
    env: Mapping[str, str],
    contract: ResourceContract,
    *,
    redis_client: Any | None,
    identity: object,
    clock: Callable[[], float],
    control_url: str,
    internal_key: str,
) -> tuple[McpDeps, tuple[str, ...]]:
    """``(deps with the GW32/GW17b/GW17c services, boot notes)``. Refuses to boot on bad input."""
    keys = McpStoreKeys()
    transport = derive_transport_bounds(contract)
    cred_bounds = derive_credential_bounds(contract)
    cap_bounds = derive_cap_bounds(contract)
    metrics = McpPolicyMetrics()
    notes: list[str] = []

    cache = CredentialCache(
        slots=cred_bounds.credential_cache_slots,
        ttl_s=cred_bounds.credential_cache_ttl_s,
        clock=clock,
    )
    lock = (
        ValkeyRefreshLock(redis_client, keys)
        if redis_client is not None
        else LocalRefreshLock(clock)
    )
    plaintext = parse_allow_hosts(env_text(env, "AMF_MCP_ALLOW_PLAINTEXT_CREDENTIAL_HOSTS") or "")
    credentials: CredentialService | None
    upstream: UpstreamOAuthRoutes | None = None
    try:
        client = ControlCredentialClient(
            base_url=control_url,
            internal_key=internal_key,
            call=_control_call(),
            bounds=cred_bounds,
        )
    except ValueError:
        # An upstream secret must not cross a plaintext link: refuse authenticated servers instead.
        credentials = None
        notes.append(
            "boot: mcp credentials -> UNSET (the control URL is neither https nor loopback); "
            "an authenticated upstream server is refused"
        )
    else:
        credentials = CredentialService(
            client=client,
            cache=cache,
            lock=lock,
            token_endpoint=pinned_token_endpoint(base.dns, base.allow_hosts),
            bounds=cred_bounds,
            clock=clock,
        )
        base.registry.subscribe_drop(_dropper(cache), cache.keys)
        upstream = UpstreamOAuthRoutes(
            flow=ControlOAuthFlowClient(
                base_url=control_url, internal_key=internal_key, call=_control_call(),
                bounds=cred_bounds,
            ),
            http=pinned_http(base.dns, base.allow_hosts),
            bounds=cred_bounds,
            body_bytes=transport.oauth_body_bytes,
            public_base=_public_base(env),
        )
        lock_scope = "shared store" if redis_client is not None else "PER-WORKER, no store"
        notes.append(
            "boot: mcp credentials -> GW17b CredentialService (cache dropped on registry version "
            f"poll; refresh lock {lock_scope}; token endpoint dialled through the pinned transport)"
        )

    sessions = cap_table = None
    if redis_client is not None:
        sessions = ValkeySessionStore(
            redis_client, keys, transport, clock, max_tries=cap_bounds.max_cas_tries
        )
        cap_table = CallCapTable(
            client=redis_client,
            keys=keys,
            bounds=cap_bounds,
            clock=clock,
            rng=random.Random(),
            metrics=metrics,
        )
        notes.append("boot: mcp sessions + per-key call cap -> shared store ({rv2}:mcp:*)")
    else:
        notes.append(
            "boot: mcp sessions + per-key call cap -> UNSET (no store): no session id is issued "
            "and a key with mcp_max_tool_calls > 0 is refused"
        )

    oauth, token_store, public_base, oauth_note = _oauth(
        env, redis_client, identity, keys, transport, clock, cap_bounds.max_cas_tries
    )
    notes.append(oauth_note)
    deps = dataclasses.replace(
        base,
        credentials=credentials,
        credential_bounds=cred_bounds,
        plaintext_credential_hosts=plaintext,
        cap_table=cap_table,
        policy_metrics=metrics,
        sessions=sessions,
        oauth=oauth,
        token_store=token_store,
        allowed_origins=parse_allow_hosts(env_text(env, "AMF_MCP_ALLOWED_ORIGINS") or ""),
        transport_bounds=transport,
        public_base=public_base,
        upstream_oauth=upstream,
    )
    notes.append(
        "boot: mcp upstream oauth setup -> "
        + ("UNSET (no safe control link)" if upstream is None else (
            "oauth/start|callback|status over control flow state, pinned dial"
            + ("" if upstream.public_base else "; start answers 503 (AMF_GATEWAY_PUBLIC_URL unset)")
        ))
    )
    return deps, tuple(notes)


def _public_base(env: Mapping[str, str]) -> str:
    """The validated public URL (https or loopback http), else empty (never a guessed URL)."""
    url = env_text(env, "AMF_GATEWAY_PUBLIC_URL")
    if url is None:
        return ""
    parts = urlsplit(url)
    host = parts.hostname or ""
    ok = parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK)
    if not host or not ok or parts.query or parts.fragment:
        raise DevProfileError("AMF_GATEWAY_PUBLIC_URL must be an https URL (or loopback http)")
    return url.rstrip("/")


def _dropper(cache: CredentialCache) -> Callable[[str, str], None]:
    def drop(org: str, server: str) -> None:
        for cached_org, cached_server in cache.keys():
            if cached_org == org and server in ("", cached_server):
                cache.drop(cached_org, cached_server)

    return drop


def _oauth(
    env: Mapping[str, str],
    redis_client: Any | None,
    identity: object,
    keys: McpStoreKeys,
    transport: Any,
    clock: Callable[[], float],
    max_tries: int,
) -> tuple[OAuthRoutes | None, Any, str, str]:
    url = env_text(env, "AMF_GATEWAY_PUBLIC_URL")
    secret = env_text(env, "AMF_OAUTH_HASH_KEY")
    if url is None and secret is None:
        unset = "boot: mcp oauth -> UNSET (AMF_GATEWAY_PUBLIC_URL, AMF_OAUTH_HASH_KEY)"
        return None, None, "", unset
    if url is None or secret is None:
        raise DevProfileError("MCP OAuth is partly configured; set both AMF_GATEWAY_PUBLIC_URL "
                              "and AMF_OAUTH_HASH_KEY")
    if redis_client is None:
        raise DevProfileError("MCP OAuth needs the shared store (AMF_DEV_REDIS_URL)")
    previous = env_text(env, "AMF_OAUTH_HASH_KEY_PREVIOUS")
    try:
        config = OAuthConfig(
            public_url=url,
            hash_key=secret.encode(),
            previous_key=previous.encode() if previous else None,
            bounds=transport,
        )
    except ValueError as exc:
        raise DevProfileError(f"MCP OAuth settings are invalid: {exc}") from exc
    kv = ValkeyKV(redis_client, max_tries)
    server = OAuthServer(kv=kv, keys=keys, config=config, clock=clock)
    routes = OAuthRoutes(
        server=server,
        public_url=url,
        identity=identity,  # type: ignore[arg-type]
        body_bytes=transport.oauth_body_bytes,
    )
    return (
        routes,
        OAuthTokenResolver(server, transport, clock),
        url.rstrip("/"),
        "boot: mcp oauth -> OAuth 2.1 server over the shared store (keys redacted)",
    )
