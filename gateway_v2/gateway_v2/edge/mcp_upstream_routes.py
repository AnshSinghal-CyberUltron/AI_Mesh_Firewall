"""GW32 Requirement 9: the upstream OAuth setup routes (v1-compatible paths, DD-4).

* ``POST /gateway/{org}/mcp/{srv}/oauth/start``  -> ``{"authorization_url": ...}``
* ``GET  /gateway/oauth/callback``                -> a fixed HTML page
* ``GET  /gateway/{org}/mcp/{srv}/oauth/status``  -> the five status fields, nothing else

`start` and `status` are authenticated like every MCP route (Origin, identity from headers, path
org == key org). `start` also refuses (review M7, R9) a caller that authenticated with an OAuth
`zsmcp_` token, or whose key is restricted (a non-empty allow-list or a call cap), because the
stored upstream credential is org-wide. When the server is already authorised, `start` needs
``{"force": true}``. The server URL is always the REGISTERED one, never a client value. The
callback carries no key: the single-use `state` (bound at control to key, org and server) is its
only authentication. Pages are fixed text; no remote value, token or verifier is ever rendered.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from gateway_v2.dispatch.mcp_upstream_oauth import (
    ControlOAuthFlowClient,
    UpstreamOAuthFailure,
    authorization_url,
    discover,
    exchange_code,
    new_pkce,
    new_state,
    register_client,
)
from gateway_v2.dispatch.pinned_transport import UpstreamHttp
from gateway_v2.domain import posture
from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend, read_body_bounded
from gateway_v2.edge.mcp_identity import CallerRefused, McpCaller, resolve_caller
from gateway_v2.edge.mcp_paths import GatewayPath
from gateway_v2.edge.mcp_registry import ServerEntry
from gateway_v2.edge.mcp_session_gate import check_origin
from gateway_v2.edge.routes import emit_response
from gateway_v2.runtime.resources_mcp import McpCredentialBounds

if TYPE_CHECKING:
    from gateway_v2.edge.mcp import McpRoute

__all__ = ("UpstreamOAuthRoutes", "log_audit", "serve_upstream")

CALLBACK_PATH = "/gateway/oauth/callback"
_STATUS_FIELDS = ("authorized", "expired", "refreshable", "needs_reauth", "obtained_at")
_LOG = logging.getLogger("gateway_v2.mcp.upstream_oauth")
_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>'
    "<body><main><h1>{title}</h1><p>{message}</p></main></body></html>"
)
_PAGE_HEADERS = (
    ("content-type", "text/html; charset=utf-8"),
    ("content-security-policy", "default-src 'none'"),
    ("cache-control", "no-store"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
)
_DONE = "Authorization complete"
_DENIED = "Authorization not completed"
_FAILED = "Authorization failed"
_PAGES: Mapping[str, tuple[int, str, str]] = MappingProxyType({
    "done": (200, _DONE, "The server is connected. You can close this window."),
    "denied": (400, _DENIED, "The authorization server did not grant access."),
    "invalid": (400, _FAILED, "This authorization link is invalid or has expired."),
    "failed": (502, _FAILED, "The token exchange with the server failed."),
})
Audit = Callable[[str, Mapping[str, str]], None]


def log_audit(event: str, detail: Mapping[str, str]) -> None:
    """The default audit sink: one structured line. ``detail`` never carries a token or code."""
    _LOG.info("%s %s", event, json.dumps(dict(detail), sort_keys=True))


@dataclass(frozen=True, slots=True)
class UpstreamOAuthRoutes:
    """Boot-built. ``public_base`` empty = start answers the declared 503."""

    flow: ControlOAuthFlowClient
    http: UpstreamHttp
    bounds: McpCredentialBounds
    body_bytes: int
    public_base: str = ""
    audit: Audit = log_audit

    def redirect_uri(self) -> str:
        return f"{self.public_base.rstrip('/')}{CALLBACK_PATH}"


def _json(status: int, body: Mapping[str, object]) -> errors.HTTPResponse:
    raw = json.dumps(dict(body), separators=(",", ":")).encode()
    headers = (("content-type", "application/json"), ("cache-control", "no-store"))
    return errors.HTTPResponse(status=status, body=raw, headers=headers)


def _page(kind: str) -> errors.HTTPResponse:
    status, title, message = _PAGES[kind]
    body = _PAGE.format(title=title, message=message).encode()  # fixed text only, nothing echoed
    return errors.HTTPResponse(status=status, body=body, headers=_PAGE_HEADERS)


def _query(scope: ASGIScope) -> dict[str, str]:
    raw = scope.get("query_string")
    if not isinstance(raw, (bytes, bytearray)):
        return {}
    try:
        parsed = parse_qs(bytes(raw).decode("utf-8"), max_num_fields=16)
    except (UnicodeDecodeError, ValueError):
        return {}
    return {key: values[0] for key, values in parsed.items() if values}


def oauth_url_of(entry: ServerEntry) -> str:
    """The registered URL to authorise: the server URL, or for a stdio ``mcp-remote`` wrapper the
    first https argument after ``mcp-remote``. Never a client-supplied value."""
    if entry.url:
        return entry.url
    args = list(entry.args)
    for index, arg in enumerate(args):
        if arg.endswith("mcp-remote") or arg.startswith("mcp-remote@"):
            for candidate in args[index + 1:]:
                if urlsplit(candidate).scheme == "https":
                    return candidate
    return ""


async def serve_upstream(
    route: McpRoute, path: GatewayPath, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
) -> None:
    """Dispatch one of the three setup shapes. ``route.upstream_oauth`` is not ``None``."""
    routes = route.upstream_oauth
    assert routes is not None
    method = str(scope.get("method", "GET")).upper()
    expected = "POST" if path.kind == "oauth_start" else "GET"
    if method != expected:
        await emit_response(send, errors.render_http("method_not_allowed"))
        return
    if path.kind == "oauth_callback":
        await emit_response(send, await _callback(routes, scope))
        return
    caller = await _caller(route, path, scope)
    if isinstance(caller, str):
        await emit_response(send, errors.render_http(caller))
        return
    org = route.org_map.control_slug(caller.identity.org_id) or ""
    if path.kind == "oauth_status":
        await emit_response(send, await _status(routes, org, path.server_slug))
        return
    await emit_response(send, await _start(route, routes, caller, org, path, receive))


async def _caller(route: McpRoute, path: GatewayPath, scope: ASGIScope) -> McpCaller | str:
    if not check_origin(scope, route.allowed_origins):
        return posture.MCP_ORIGIN_REFUSED
    caller = await resolve_caller(route.chat.identity, route.token_store, scope)
    if isinstance(caller, CallerRefused):
        return caller.code
    if route.org_map.control_slug(caller.identity.org_id) != path.org:
        return posture.MCP_ORG_MISMATCH  # same answer for "wrong org" and "no such org"
    return caller


async def _status(routes: UpstreamOAuthRoutes, org: str, slug: str) -> errors.HTTPResponse:
    reply = await routes.flow.status(org, slug)
    if reply is None:
        return errors.render_http("mcp_server_not_found")
    return _json(200, {name: reply.get(name) for name in _STATUS_FIELDS})


def _restricted(caller: McpCaller) -> bool:
    principal = caller.identity.principal
    return caller.via_token or bool(principal.mcp_allowed_tools) or principal.mcp_max_tool_calls > 0


async def _start(
    route: McpRoute,
    routes: UpstreamOAuthRoutes,
    caller: McpCaller,
    org: str,
    path: GatewayPath,
    receive: ASGIReceive,
) -> errors.HTTPResponse:
    key_id = caller.identity.principal.key_id
    if _restricted(caller):
        detail = {"key_id": key_id, "server": path.server_slug}
        routes.audit("oauth_start_refused_restricted", detail)
        return errors.render_http(posture.MCP_TOOL_NOT_PERMITTED)
    force = await _force(receive, routes.body_bytes)
    if force is None:
        return errors.render_http("mcp_invalid_request")
    if not routes.public_base:
        return errors.render_http(posture.MCP_UPSTREAM_OAUTH_UNAVAILABLE)
    entry = await route.registry.server(org, path.server_slug)
    if isinstance(entry, str):
        return errors.render_http(entry)
    server_url = oauth_url_of(entry)
    if not entry.usable or not server_url:
        return errors.render_http("mcp_server_not_found")
    status = await routes.flow.status(org, path.server_slug)
    if status is None:
        return errors.render_http(posture.MCP_UPSTREAM_OAUTH_FAILED)
    if status.get("authorized") is True and not force:
        return errors.render_http("mcp_invalid_request")  # already authorised: needs force=true
    url = await _begin(routes, status, server_url, org, path.server_slug, key_id)
    if isinstance(url, UpstreamOAuthFailure):
        return errors.render_http(posture.MCP_UPSTREAM_OAUTH_FAILED)
    routes.audit(
        "oauth_start", {"key_id": key_id, "server": path.server_slug, "force": str(force).lower()}
    )
    return _json(200, {"authorization_url": url})


async def _force(receive: ASGIReceive, limit: int) -> bool | None:
    """``force`` from an optional JSON object body; ``None`` = a malformed or oversized body."""
    raw = await read_body_bounded(receive, limit)
    if raw is None:
        return None
    if not raw.strip():
        return False
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    force = body.get("force", False)
    return force if isinstance(force, bool) else None


async def _begin(
    routes: UpstreamOAuthRoutes,
    status: Mapping[str, object],
    server_url: str,
    org: str,
    slug: str,
    key_id: str,
) -> str | UpstreamOAuthFailure:
    meta = await discover(server_url, routes.http, routes.bounds)
    if isinstance(meta, UpstreamOAuthFailure):
        return meta
    redirect = routes.redirect_uri()
    stored_id = status.get("client_id")
    reuse = isinstance(stored_id, str) and bool(stored_id) and (
        status.get("client_token_endpoint") == meta.token_endpoint
    )
    flow: dict[str, object] = {}
    if reuse:
        client_id = str(stored_id)
    else:
        registered = await register_client(
            meta, redirect, routes.http, routes.bounds, client_name="ZeroShield MCP gateway"
        )
        if isinstance(registered, UpstreamOAuthFailure):
            return registered
        client_id, flow["client_secret"] = registered
    verifier, challenge = new_pkce()
    state = new_state()
    flow.update(
        server_slug=slug, state=state, code_verifier=verifier, key_id=key_id,
        ttl_s=routes.bounds.upstream_flow_ttl_s,
        authorization_endpoint=meta.authorization_endpoint, token_endpoint=meta.token_endpoint,
        registration_endpoint=meta.registration_endpoint, client_id=client_id,
        resource=meta.resource, scope=meta.scope,
    )
    if not await routes.flow.save_flow(org, flow):
        return UpstreamOAuthFailure("flow state could not be stored")
    return authorization_url(meta, client_id, redirect, challenge, state)


async def _callback(routes: UpstreamOAuthRoutes, scope: ASGIScope) -> errors.HTTPResponse:
    query = _query(scope)
    state, code = query.get("state", ""), query.get("code", "")
    if not state:
        return _page("invalid")
    flow = await routes.flow.consume(state)  # single use: consumed even when the user declined
    if flow is None:
        return _page("invalid")
    if query.get("error") or not code:
        return _page("denied")
    tokens = await exchange_code(flow, code, routes.redirect_uri(), routes.http, routes.bounds)
    if isinstance(tokens, UpstreamOAuthFailure):
        return _page("failed")
    if not await routes.flow.store_tokens(flow["org_slug"], flow["server_slug"], tokens):
        return _page("failed")
    routes.audit("oauth_callback", {"key_id": flow["key_id"], "server": flow["server_slug"]})
    return _page("done")
