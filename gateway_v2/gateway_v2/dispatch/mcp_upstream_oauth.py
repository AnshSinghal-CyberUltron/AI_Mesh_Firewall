"""GW32 Requirement 9: the gateway-run upstream OAuth setup flow (DD-4), dispatch half.

Discovery (RFC 9728 protected-resource metadata, then RFC 8414 authorization-server metadata),
dynamic client registration (RFC 7591), PKCE S256, and the code exchange. Every outbound call goes
through the injected `UpstreamHttp`; in production that is `pinned_http`, which resolves and
checks the destination once and dials only an approved address (DNS-rebinding control, design
iteration 3 finding 1). Every URL learned from remote metadata must be https. A 3xx is a failure:
no redirect is followed, which is stricter than the design's re-pinned hop budget.

The flow state lives in control (`ControlOAuthFlowClient`), never in the gateway store. No
function here logs, and every failure is a value carrying a fixed reason, never remote text.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NoReturn
from urllib.parse import urlencode, urlsplit, urlunsplit

from gateway_v2.dispatch.mcp_credentials import ControlCall
from gateway_v2.dispatch.pinned_transport import UpstreamHttp
from gateway_v2.runtime.resources_mcp import McpCredentialBounds

__all__ = (
    "ControlOAuthFlowClient",
    "UpstreamMetadata",
    "UpstreamOAuthFailure",
    "UpstreamTokens",
    "authorization_url",
    "discover",
    "exchange_code",
    "new_pkce",
    "new_state",
    "register_client",
)

_OK = 200
_CREATED = 201
_NOT_FOUND = 404
_REDIRECT_LO, _REDIRECT_HI = 300, 400
_JSON: Mapping[str, str] = MappingProxyType({"Accept": "application/json"})
_PRM = "/.well-known/oauth-protected-resource"
_ASM = "/.well-known/oauth-authorization-server"
_OIDC = "/.well-known/openid-configuration"
_STATE_BYTES = 32  # token_urlsafe(32) -> 43 characters
_VERIFIER_BYTES = 48  # token_urlsafe(48) -> 64 characters (RFC 7636: 43..128)
_REDACTED = "<redacted>"


@dataclass(frozen=True, slots=True)
class UpstreamOAuthFailure:
    """A failed step. ``reason`` is fixed text for logs and tests, never remote content."""

    reason: str


@dataclass(frozen=True, slots=True)
class UpstreamMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    resource: str
    scope: str


@dataclass(frozen=True, slots=True)
class UpstreamTokens:
    """The exchanged tokens. Repr-redacted and not serialisable."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_in: int | None

    def __repr__(self) -> str:
        return _REDACTED

    def __str__(self) -> str:
        return _REDACTED

    def __reduce__(self) -> NoReturn:
        raise TypeError("tokens are not serialisable")


def _https(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    has_user = parts.username is not None or parts.password is not None
    if parts.scheme != "https" or not parts.hostname or has_user or parts.fragment:
        return None
    return url


def _well_known(base: str, suffix: str) -> str:
    """RFC 8414 section 3 / RFC 9728 section 3.1: insert the well-known segment before the path."""
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    return urlunsplit(("https", parts.netloc, suffix + path, "", ""))


async def _get_json(
    http: UpstreamHttp, url: str, bounds: McpCredentialBounds
) -> Mapping[str, object] | int | UpstreamOAuthFailure:
    """A JSON object, or the HTTP status when it is not 200, or a failure."""
    try:
        status, raw = await http(
            "GET", url, _JSON, None, bounds.refresh_timeout_s, bounds.upstream_metadata_bytes
        )
    except Exception:  # noqa: BLE001 - refused destination, timeout, transport error
        return UpstreamOAuthFailure("metadata fetch failed")
    if _REDIRECT_LO <= status < _REDIRECT_HI:
        return UpstreamOAuthFailure("metadata redirect refused")
    if status != _OK:
        return status
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return UpstreamOAuthFailure("metadata is not JSON")
    return decoded if isinstance(decoded, dict) else UpstreamOAuthFailure("metadata not an object")


async def discover(
    server_url: str, http: UpstreamHttp, bounds: McpCredentialBounds
) -> UpstreamMetadata | UpstreamOAuthFailure:
    """Protected-resource metadata, then authorization-server metadata, issuer-checked."""
    if _https(server_url) is None:
        return UpstreamOAuthFailure("server URL is not https")
    parts = urlsplit(server_url)
    resource = urlunsplit(("https", parts.netloc, parts.path, "", ""))
    issuer = urlunsplit(("https", parts.netloc, "", "", ""))
    prm = await _get_json(http, _well_known(resource, _PRM), bounds)
    if isinstance(prm, UpstreamOAuthFailure):
        return prm
    if isinstance(prm, dict):
        servers = prm.get("authorization_servers")
        if isinstance(servers, list) and servers:
            issuer = _https(servers[0]) or ""
            if not issuer:
                return UpstreamOAuthFailure("authorization server is not https")
        if isinstance(prm.get("resource"), str) and _https(prm["resource"]):
            resource = str(prm["resource"])
    meta = await _get_json(http, _well_known(issuer, _ASM), bounds)
    if meta == _NOT_FOUND:
        meta = await _get_json(http, _well_known(issuer, _OIDC), bounds)
    if not isinstance(meta, dict):
        return meta if isinstance(meta, UpstreamOAuthFailure) else UpstreamOAuthFailure(
            "no authorization server metadata"
        )
    return _metadata(meta, issuer, resource)


def _metadata(
    meta: Mapping[str, object], issuer: str, resource: str
) -> UpstreamMetadata | UpstreamOAuthFailure:
    declared = meta.get("issuer")
    if not isinstance(declared, str) or declared.removesuffix("/") != issuer.removesuffix("/"):
        return UpstreamOAuthFailure("issuer mismatch")  # RFC 8414 section 3.3
    authorize = _https(meta.get("authorization_endpoint"))
    token = _https(meta.get("token_endpoint"))
    if authorize is None or token is None:
        return UpstreamOAuthFailure("endpoints missing or not https")
    raw_registration = meta.get("registration_endpoint")
    registration = _https(raw_registration) if raw_registration else ""
    if registration is None:
        return UpstreamOAuthFailure("registration endpoint is not https")
    methods = meta.get("code_challenge_methods_supported")
    if isinstance(methods, list) and "S256" not in methods:
        return UpstreamOAuthFailure("S256 not supported")
    scopes = meta.get("scopes_supported")
    scope = " ".join(s for s in scopes if isinstance(s, str)) if isinstance(scopes, list) else ""
    return UpstreamMetadata(issuer, authorize, token, registration, resource, scope)


async def register_client(
    meta: UpstreamMetadata,
    redirect_uri: str,
    http: UpstreamHttp,
    bounds: McpCredentialBounds,
    client_name: str,
) -> tuple[str, str] | UpstreamOAuthFailure:
    """RFC 7591 registration of a public client. ``(client_id, client_secret or "")``."""
    if not meta.registration_endpoint:
        return UpstreamOAuthFailure("no dynamic registration")
    body = json.dumps({
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }).encode()
    try:
        status, raw = await http(
            "POST", meta.registration_endpoint, {**_JSON, "Content-Type": "application/json"},
            body, bounds.refresh_timeout_s, bounds.upstream_metadata_bytes,
        )
        decoded = json.loads(raw) if status in (_OK, _CREATED) else None
    except Exception:  # noqa: BLE001 - transport error, refused destination or bad JSON
        return UpstreamOAuthFailure("registration failed")
    if not isinstance(decoded, dict) or not isinstance(decoded.get("client_id"), str):
        return UpstreamOAuthFailure("registration returned no client id")
    issued = decoded.get("client_secret")
    return str(decoded["client_id"]), issued if isinstance(issued, str) else ""


def new_pkce() -> tuple[str, str]:
    """``(verifier, S256 challenge)``."""
    verifier = secrets.token_urlsafe(_VERIFIER_BYTES)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_state() -> str:
    return secrets.token_urlsafe(_STATE_BYTES)


def authorization_url(
    meta: UpstreamMetadata, client_id: str, redirect_uri: str, challenge: str, state: str
) -> str:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": meta.resource,
    }
    if meta.scope:
        query["scope"] = meta.scope
    parts = urlsplit(meta.authorization_endpoint)
    joined = f"{parts.query}&{urlencode(query)}" if parts.query else urlencode(query)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, joined, ""))


async def exchange_code(
    flow: Mapping[str, str],
    code: str,
    redirect_uri: str,
    http: UpstreamHttp,
    bounds: McpCredentialBounds,
) -> UpstreamTokens | UpstreamOAuthFailure:
    """The authorization-code exchange with the stored verifier (RFC 6749 4.1.3 + RFC 7636)."""
    endpoint = _https(flow.get("token_endpoint"))
    if endpoint is None:
        return UpstreamOAuthFailure("token endpoint is not https")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": flow.get("client_id", ""),
        "code_verifier": flow.get("code_verifier", ""),
    }
    if flow.get("resource"):
        form["resource"] = flow["resource"]
    if flow.get("client_secret"):
        form["client_secret"] = flow["client_secret"]
    try:
        status, raw = await http(
            "POST", endpoint,
            {**_JSON, "Content-Type": "application/x-www-form-urlencoded"},
            urlencode(form).encode(), bounds.refresh_timeout_s, bounds.refresh_response_bytes,
        )
        decoded = json.loads(raw) if status == _OK else None
    except Exception:  # noqa: BLE001 - transport error, refused destination or bad JSON
        return UpstreamOAuthFailure("token exchange failed")
    if not isinstance(decoded, dict):
        return UpstreamOAuthFailure("token exchange refused")
    access, refresh = decoded.get("access_token"), decoded.get("refresh_token")
    expires = decoded.get("expires_in")
    if not isinstance(access, str) or len(access) < bounds.credential_min_len:
        return UpstreamOAuthFailure("token exchange returned no usable token")
    return UpstreamTokens(
        access_token=access,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        expires_in=expires if isinstance(expires, int) and not isinstance(expires, bool)
        and expires > 0 else None,
    )


class ControlOAuthFlowClient:
    """Control's internal flow endpoints. A failure is ``None``/``False``, never error text."""

    __slots__ = ("_base", "_bounds", "_call", "_key")

    def __init__(
        self, *, base_url: str, internal_key: str, call: ControlCall, bounds: McpCredentialBounds
    ) -> None:
        if not base_url or not internal_key:
            raise ValueError("the OAuth flow client needs a control URL and an internal key")
        self._base, self._key, self._call, self._bounds = (
            base_url.rstrip("/"), internal_key, call, bounds,
        )

    async def _request(
        self, method: str, path: str, org: str, query: Mapping[str, str],
        body: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None:
        headers = {"X-Gateway-Auth": "true", "X-Gateway-Internal-Key": self._key, "X-Org-Slug": org}
        try:
            status, raw = await self._call(
                method, self._base + "/api/mcp-connector/internal/" + path, headers, query, body,
                self._bounds.refresh_timeout_s, self._bounds.credential_response_bytes,
            )
            decoded = json.loads(raw) if status == _OK else None
        except Exception:  # noqa: BLE001 - control unreachable or a bad reply
            return None
        return decoded if isinstance(decoded, dict) else None

    async def status(self, org: str, slug: str) -> dict[str, object] | None:
        return await self._request("GET", "oauth-status/", org, {"server_slug": slug})

    async def save_flow(self, org: str, body: Mapping[str, object]) -> bool:
        return await self._request("POST", "oauth-flow/", org, {}, body) is not None

    async def consume(self, state: str) -> dict[str, str] | None:
        reply = await self._request("POST", "oauth-flow/consume/", "", {}, {"state": state})
        if reply is None:
            return None
        flow = {k: v for k, v in reply.items() if isinstance(v, str)}
        needed = ("org_slug", "server_slug", "key_id", "code_verifier", "token_endpoint")
        return flow if all(flow.get(name) for name in needed) else None

    async def store_tokens(self, org: str, slug: str, tokens: UpstreamTokens) -> bool:
        body: dict[str, object] = {"server_slug": slug, "access_token": tokens.access_token}
        if tokens.refresh_token is not None:
            body["refresh_token"] = tokens.refresh_token
        if tokens.expires_in is not None:
            body["expires_in"] = tokens.expires_in
        return await self._request("POST", "credential/oauth-tokens/", org, {}, body) is not None
