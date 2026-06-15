"""OAuth 2.1 client for MCP server authorization (Phase C).

Implements the MCP authorization spec (2025-06-18) for HTTP transports:

  401 + ``WWW-Authenticate``  →  RFC 9728 protected-resource metadata
  →  RFC 8414 authorization-server metadata
  →  RFC 7591 dynamic client registration
  →  PKCE (RFC 7636) authorization-code flow
  →  token request/refresh with the RFC 8707 ``resource`` parameter.

The control plane acts as the OAuth *client*; the upstream MCP server is the
resource server. The obtained access token is stored (encrypted) on the
``MCPServerRegistration`` and forwarded to the gateway as a normal bearer, so
the gateway data plane stays OAuth-agnostic.

Security notes:
  * PKCE S256 is always used (public-client safe).
  * ``state`` is a high-entropy CSRF token validated on callback.
  * redirect_uri is restricted to localhost/HTTPS per the spec.
  * Secrets are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
from urllib.parse import urlencode, urlparse

import httpx

from ._url_guard import is_safe_outbound_url

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 20.0
_USER_AGENT = "AIMeshFirewall-MCP-OAuth/1.0"


class OAuthDiscoveryError(Exception):
    """Raised when OAuth metadata/registration discovery fails."""


# ── PKCE ─────────────────────────────────────────────────────────────

def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using S256.

    The verifier is 43-128 chars of unreserved URL-safe characters per
    RFC 7636; the challenge is BASE64URL(SHA256(verifier)) without padding.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def generate_state() -> str:
    """High-entropy CSRF state token."""
    return secrets.token_urlsafe(32)


# ── Canonical resource (RFC 8707) ────────────────────────────────────

def canonical_resource(server_url: str) -> str:
    """Canonical MCP server URI: scheme+host(+port)+path, no fragment, no trailing slash."""
    p = urlparse(server_url.strip())
    netloc = p.netloc
    path = (p.path or "").rstrip("/")
    canon = f"{p.scheme.lower()}://{netloc}{path}"
    return canon


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# ── WWW-Authenticate parsing (RFC 9728 §5.1) ─────────────────────────

def parse_www_authenticate(header: str) -> dict:
    """Extract auth-param key/values from a ``WWW-Authenticate`` header."""
    params: dict[str, str] = {}
    if not header:
        return params
    # Drop the leading scheme token (e.g. "Bearer ").
    rest = re.sub(r"^\s*[A-Za-z][A-Za-z0-9_-]*\s+", "", header, count=1)
    for kv in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*"([^"]*)"', rest):
        params[kv.group(1)] = kv.group(2)
    return params


# ── Discovery ────────────────────────────────────────────────────────

def _get_json(client: httpx.Client, url: str) -> dict | None:
    # SSRF guard (finding mcp#1): metadata URLs can derive from a
    # server-controlled WWW-Authenticate hint (resource_metadata). Reject
    # internal / loopback / link-local / cloud-metadata targets before GET.
    ok, reason = is_safe_outbound_url(url)
    if not ok:
        logger.warning("Blocked OAuth metadata GET to unsafe URL %s: %s", url, reason)
        return None
    try:
        r = client.get(url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT})
        if r.status_code == 200:
            return r.json()
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        logger.debug("OAuth metadata GET failed %s: %s", url, exc)
    return None


def _probe_resource_metadata_url(client: httpx.Client, server_url: str) -> str | None:
    """Send an unauthenticated MCP request to read the WWW-Authenticate hint."""
    # SSRF guard (finding mcp#1): server_url is operator-supplied.
    ok, reason = is_safe_outbound_url(server_url)
    if not ok:
        logger.warning("Blocked OAuth 401 probe to unsafe URL %s: %s", server_url, reason)
        return None
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "ai-mesh-firewall", "version": "1.0"},
        },
    }
    try:
        r = client.post(
            server_url,
            json=init_body,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        if r.status_code in (401, 403):
            params = parse_www_authenticate(r.headers.get("WWW-Authenticate", ""))
            return params.get("resource_metadata")
    except Exception as exc:  # noqa: BLE001
        logger.debug("OAuth 401 probe failed for %s: %s", server_url, exc)
    return None


def _fetch_as_metadata(client: httpx.Client, as_issuer: str) -> dict | None:
    """Fetch RFC 8414 / OIDC authorization-server metadata for an issuer."""
    issuer = as_issuer.rstrip("/")
    p = urlparse(issuer)
    base = f"{p.scheme}://{p.netloc}"
    issuer_path = p.path.rstrip("/")
    # RFC 8414 places well-known BEFORE the issuer path component; OIDC appends.
    candidates = [
        f"{base}/.well-known/oauth-authorization-server{issuer_path}",
        f"{issuer}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration{issuer_path}",
        f"{issuer}/.well-known/openid-configuration",
    ]
    for url in candidates:
        meta = _get_json(client, url)
        if meta and meta.get("authorization_endpoint") and meta.get("token_endpoint"):
            return meta
    return None


def discover(server_url: str) -> dict:
    """Run the full discovery chain for an MCP server URL.

    Returns a dict with: ``authorization_endpoint``, ``token_endpoint``,
    ``registration_endpoint`` (may be ""), ``scopes_supported`` (list),
    ``resource`` (canonical URI). Raises :class:`OAuthDiscoveryError`.
    """
    # SSRF guard (finding mcp#1): server_url is operator-supplied at
    # registration. Reject internal / loopback / link-local / cloud-metadata
    # targets before running the discovery chain.
    ok, reason = is_safe_outbound_url(server_url)
    if not ok:
        raise OAuthDiscoveryError(f"Server URL rejected by SSRF guard: {reason}")

    resource = canonical_resource(server_url)
    origin = _origin(server_url)
    server_path = urlparse(server_url).path.rstrip("/")

    # follow_redirects=False — a redirect to an internal address would bypass
    # the per-URL SSRF checks (redirect-based SSRF). Each candidate URL is
    # validated individually instead (see _get_json / _probe_resource_metadata_url).
    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False) as client:
        # 1. Locate the protected-resource-metadata document.
        prm_hint = _probe_resource_metadata_url(client, server_url)
        prm_candidates = []
        if prm_hint:
            prm_candidates.append(prm_hint)
        if server_path:
            prm_candidates.append(f"{origin}/.well-known/oauth-protected-resource{server_path}")
        prm_candidates.append(f"{origin}/.well-known/oauth-protected-resource")

        prm = None
        for url in prm_candidates:
            prm = _get_json(client, url)
            if prm:
                break

        auth_servers = list((prm or {}).get("authorization_servers") or [])
        prm_resource = (prm or {}).get("resource")
        if prm_resource:
            resource = prm_resource

        # 2. Resolve authorization-server metadata.
        as_meta = None
        for issuer in auth_servers:
            as_meta = _fetch_as_metadata(client, issuer)
            if as_meta:
                break
        if not as_meta:
            # Fallback: treat the resource origin itself as the issuer.
            as_meta = _fetch_as_metadata(client, origin)
        if not as_meta:
            raise OAuthDiscoveryError(
                "Could not discover OAuth authorization-server metadata for this server. "
                "It may not support OAuth, or requires a hardcoded client."
            )

        return {
            "authorization_endpoint": as_meta["authorization_endpoint"],
            "token_endpoint": as_meta["token_endpoint"],
            "registration_endpoint": as_meta.get("registration_endpoint", ""),
            "scopes_supported": as_meta.get("scopes_supported") or [],
            "resource": resource,
        }


# ── Dynamic client registration (RFC 7591) ───────────────────────────

def register_client(registration_endpoint: str, redirect_uri: str, client_name: str) -> dict:
    """Register a public OAuth client via DCR. Returns the registration response."""
    # B10 (second-order SSRF): registration_endpoint is derived VERBATIM from the
    # attacker-controlled RFC-8414 metadata document — validating the metadata URL
    # does NOT validate the endpoints inside it. Re-validate here and disable
    # redirect-following (a redirect to an internal address was the exact bypass
    # the discovery client was hardened against).
    _ok, _reason = is_safe_outbound_url(registration_endpoint)
    if not _ok:
        raise OAuthDiscoveryError(f"Unsafe registration endpoint rejected: {_reason}")
    body = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",  # public client; PKCE provides protection
        "application_type": "native",
    }
    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False) as client:
        r = client.post(
            registration_endpoint,
            json=body,
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": _USER_AGENT},
        )
        if r.status_code not in (200, 201):
            raise OAuthDiscoveryError(
                f"Dynamic client registration failed (HTTP {r.status_code}): {r.text[:200]}"
            )
        return r.json()


# ── Authorization URL ─────────────────────────────────────────────────

def build_authorize_url(
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scope: str,
    resource: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": resource,
    }
    if scope:
        params["scope"] = scope
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{urlencode(params)}"


# ── Token endpoint ─────────────────────────────────────────────────────

def _token_request(token_endpoint: str, data: dict, client_secret: str | None) -> dict:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    # Confidential clients authenticate via HTTP Basic; public clients send
    # client_id in the body only.
    auth = None
    if client_secret:
        auth = (data.get("client_id", ""), client_secret)
    # B10: token_endpoint comes from attacker-controlled metadata and is persisted
    # + reused on refresh — re-validate every call and disable redirect-following.
    _ok, _reason = is_safe_outbound_url(token_endpoint)
    if not _ok:
        raise OAuthDiscoveryError(f"Unsafe token endpoint rejected: {_reason}")
    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False) as client:
        r = client.post(token_endpoint, data=data, headers=headers, auth=auth)
        if r.status_code != 200:
            raise OAuthDiscoveryError(
                f"Token request failed (HTTP {r.status_code}): {r.text[:200]}"
            )
        return r.json()


def exchange_code(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    code_verifier: str,
    resource: str,
) -> dict:
    """Exchange an authorization code for tokens."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "resource": resource,
    }
    return _token_request(token_endpoint, data, client_secret or None)


def refresh_access_token(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    resource: str,
    scope: str = "",
) -> dict:
    """Refresh an access token using a refresh token."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "resource": resource,
    }
    if scope:
        data["scope"] = scope
    return _token_request(token_endpoint, data, client_secret or None)
