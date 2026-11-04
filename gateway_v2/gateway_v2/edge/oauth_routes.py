"""GW32: HTTP rendering of the OAuth 2.1 server (Requirement 6). All HTTP objects live here.

Error bodies are RFC 6749 ``{"error", "error_description"}`` from a fixed table: no input echo,
no exception text. The consent page is a fixed template with escaped values. A store outage is a
503 ``temporarily_unavailable``; an unconfigured server answers 503 ``oauth_unavailable`` and
advertises nothing.

Residual risk (recorded): ``POST /oauth/authorize`` takes a long-lived API key typed into a
browser form (v1 parity). A phishing page can ask for it. Mitigations here: a strict CSP, no
framing, exact redirect matching and redirect-URI allow-listing. It is not stronger user
authentication than v1 had.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping
from types import MappingProxyType
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from gateway_v2.admit.oauth_server import (
    OAuthError,
    OAuthServer,
    TokenGrant,
)
from gateway_v2.domain.state import StoreDataUnavailable
from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend, read_body_bounded
from gateway_v2.edge.identity_resolver import IdentityResolver
from gateway_v2.edge.mcp_paths import parse_mcp_path
from gateway_v2.edge.routes import emit_response

__all__ = ("OAuthRoutes", "OAuthUnavailable")

_KEY_SHAPE = re.compile(r"[A-Za-z0-9_-]{48}")
_ERRORS: Mapping[str, str] = MappingProxyType(
    {
        "invalid_request": "The request is malformed.",
        "invalid_client": "Client authentication failed.",
        "invalid_grant": "The grant is invalid, expired or already used.",
        "unauthorized_client": "The client is not allowed to use this grant.",
        "unsupported_grant_type": "The grant type is not supported.",
        "unsupported_response_type": "The response type is not supported.",
        "invalid_scope": "The scope is invalid.",
        "access_denied": "Access was denied.",
        "server_error": "The server could not complete the request.",
        "temporarily_unavailable": "The service is temporarily unavailable.",
        "oauth_unavailable": "OAuth is not configured on this gateway.",
    }
)
_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Authorize</title>
</head><body><main><h1>Authorize MCP access</h1>
<p>Enter the gateway API key to let this application use it.</p>
<form method="post" action="/oauth/authorize">
<input type="hidden" name="form_token" value="{token}">
<label>API key <input type="password" name="api_key" autocomplete="off" required></label>
<button type="submit">Authorize</button></form></main></body></html>"""
_ERROR_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Error</title></head>'
    "<body><main><h1>Authorization failed</h1><p>{message}</p></main></body></html>"
)
_PAGE_HEADERS = (
    ("content-type", "text/html; charset=utf-8"),
    (
        "content-security-policy",
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
    ),
    ("cache-control", "no-store"),
    ("x-frame-options", "DENY"),
)


def _json(
    status: int, body: Mapping[str, object], extra: tuple[tuple[str, str], ...] = ()
) -> errors.HTTPResponse:
    raw = json.dumps(dict(body), separators=(",", ":")).encode()
    headers = (("content-type", "application/json"), ("cache-control", "no-store"), *extra)
    return errors.HTTPResponse(status=status, body=raw, headers=headers)


def _error(err: OAuthError | str, status: int | None = None) -> errors.HTTPResponse:
    code = err if isinstance(err, str) else err.code
    code = code if code in _ERRORS else "server_error"
    http = status or (err.status if isinstance(err, OAuthError) else 400)
    extra: tuple[tuple[str, str], ...] = ()
    if isinstance(err, OAuthError) and err.retry_after_s > 0:
        extra = (("retry-after", str(max(1, round(err.retry_after_s)))),)
    if http == 401:
        extra = (*extra, ("www-authenticate", 'Basic realm="oauth"'))
    return _json(http, {"error": code, "error_description": _ERRORS[code]}, extra)


def _form(body: bytes) -> dict[str, str]:
    """Strict form decoding: first value wins; undecodable bytes give an empty form."""
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=32)
    except (UnicodeDecodeError, ValueError):
        return {}
    return {key: values[0] for key, values in parsed.items() if values}


def _query(scope: ASGIScope) -> dict[str, str]:
    raw = scope.get("query_string")
    return _form(bytes(raw)) if isinstance(raw, (bytes, bytearray)) else {}


def _redirect(uri: str, **params: str | None) -> errors.HTTPResponse:
    parts = urlsplit(uri)
    extra = urlencode({k: v for k, v in params.items() if v is not None})
    query = f"{parts.query}&{extra}" if parts.query else extra
    location = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    return errors.HTTPResponse(
        status=302, body=b"", headers=(("location", location), ("cache-control", "no-store"))
    )


class OAuthUnavailable:
    """Answers every OAuth route with a declared 503 when the server is not configured."""

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        del scope, receive
        await emit_response(send, _error("oauth_unavailable", 503))


class OAuthRoutes:
    """Handlers for the OAuth 2.1 routes. One instance per worker."""

    def __init__(
        self, *, server: OAuthServer, public_url: str, identity: IdentityResolver, body_bytes: int
    ) -> None:
        self._server, self._public, self._identity = server, public_url.rstrip("/"), identity
        self._body_bytes = body_bytes

    # -- discovery --------------------------------------------------------------------------------
    async def protected_resource(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
    ) -> None:
        del receive
        path = str(scope.get("path", ""))
        suffix = path.removeprefix("/.well-known/oauth-protected-resource")
        if suffix not in ("", "/") and parse_mcp_path(suffix) is None:
            await emit_response(send, errors.render_http("endpoint_not_found"))
            return
        await emit_response(
            send,
            _json(
                200,
                {
                    "resource": self._public + suffix.rstrip("/"),
                    "authorization_servers": [self._public],
                    "bearer_methods_supported": ["header"],
                },
            ),
        )

    async def authorization_server(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
    ) -> None:
        del scope, receive
        await emit_response(
            send,
            _json(
                200,
                {
                    "issuer": self._public,
                    "authorization_endpoint": f"{self._public}/oauth/authorize",
                    "token_endpoint": f"{self._public}/oauth/token",
                    "registration_endpoint": f"{self._public}/oauth/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
                },
            ),
        )

    # -- registration -----------------------------------------------------------------------------
    async def register(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        body = await read_body_bounded(receive, self._body_bytes)
        try:
            doc = json.loads(body) if body else None
        except (UnicodeDecodeError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            await emit_response(send, _error("invalid_request"))
            return
        try:
            out = await self._server.register(doc, self._server.source_of(scope))
        except StoreDataUnavailable:
            await emit_response(send, _error("temporarily_unavailable", 503))
            return
        await emit_response(send, _error(out) if isinstance(out, OAuthError) else _json(201, out))

    # -- authorize --------------------------------------------------------------------------------
    async def authorize(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        method = str(scope.get("method", "GET")).upper()
        try:
            if method == "GET":
                await self._authorize_get(scope, send)
            elif method == "POST":
                await self._authorize_post(scope, receive, send)
            else:
                response = _error("invalid_request", 405)
                await emit_response(
                    send,
                    errors.HTTPResponse(
                        405, response.body, (*response.headers, ("allow", "GET, POST"))
                    ),
                )
        except StoreDataUnavailable:
            await emit_response(send, _error("temporarily_unavailable", 503))

    async def _authorize_get(self, scope: ASGIScope, send: ASGISend) -> None:
        params = _query(scope)
        checked = await self._server.validate_authorize(params)
        if isinstance(checked, OAuthError):
            if checked.code == "invalid_client":  # untrusted redirect: never redirect
                await emit_response(
                    send,
                    self._page(
                        _ERROR_PAGE.format(message="The application is not registered correctly."),
                        400,
                    ),
                )
                return
            await emit_response(
                send,
                _redirect(params["redirect_uri"], error=checked.code, state=params.get("state")),
            )
            return
        token = await self._server.issue_form_token(checked)
        await emit_response(
            send, self._page(_PAGE.format(token=html.escape(token, quote=True)), 200)
        )

    async def _authorize_post(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        body = await read_body_bounded(receive, self._body_bytes)
        form = _form(body or b"")
        source = self._server.source_of(scope)
        result = await self._server.authorize(
            form.get("form_token", ""), self._resolve_key, form.get("api_key", ""), source
        )
        if isinstance(result, OAuthError):
            message = (
                "Too many attempts. Try again later."
                if result.status == 429
                else ("That key could not be used.")
            )
            page = self._page(_ERROR_PAGE.format(message=html.escape(message)), result.status)
            if result.retry_after_s > 0:
                page = errors.HTTPResponse(
                    page.status,
                    page.body,
                    (*page.headers, ("retry-after", str(max(1, round(result.retry_after_s))))),
                )
            await emit_response(send, page)
            return
        request, code = result
        await emit_response(send, _redirect(request.redirect_uri, code=code, state=request.state))

    async def _resolve_key(self, raw_key: str) -> tuple[str, str] | None:
        """The submitted API key through the same identity path (`resolve_hash`) as chat."""
        resolve_hash = getattr(self._identity, "resolve_hash", None)
        if resolve_hash is None or _KEY_SHAPE.fullmatch(raw_key) is None:
            return None
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        outcome = await resolve_hash(key_hash)
        if outcome.identity is None:
            if outcome.refusal == "shared_state_unavailable":
                raise StoreDataUnavailable("identity unavailable")
            return None
        return key_hash, outcome.identity.principal.key_id

    @staticmethod
    def _page(text: str, status: int) -> errors.HTTPResponse:
        return errors.HTTPResponse(status=status, body=text.encode(), headers=_PAGE_HEADERS)

    # -- token ------------------------------------------------------------------------------------
    async def token(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        del scope
        body = await read_body_bounded(receive, self._body_bytes)
        try:
            out = await self._server.token(_form(body or b""))
        except StoreDataUnavailable:
            await emit_response(send, _error("temporarily_unavailable", 503))
            return
        if isinstance(out, OAuthError):
            await emit_response(send, _error(out))
            return
        await emit_response(send, self._grant(out))

    @staticmethod
    def _grant(grant: TokenGrant) -> errors.HTTPResponse:
        return _json(
            200,
            {
                "access_token": grant.access_token,
                "token_type": "Bearer",
                "expires_in": grant.expires_in,
                "refresh_token": grant.refresh_token,
                "scope": "mcp",
            },
            (("pragma", "no-cache"),),
        )
