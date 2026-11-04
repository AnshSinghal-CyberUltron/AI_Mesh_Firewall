"""GW32 (DD-4): gateway-internal endpoints for the gateway-run upstream OAuth setup flow.

The gateway does discovery, registration and PKCE; control is the one home for stored flow
state. Every endpoint is internal-key only (``IsGatewayInternalOnly``), throttled, ``no-store``,
and answers one uniform 404 for "no such server" and "not your org".

* ``GET  internal/oauth-status/?server_slug=``  -> status fields + the registered client id.
* ``POST internal/oauth-flow/``                 -> store state, verifier, key binding, endpoints.
* ``POST internal/oauth-flow/consume/``         -> single use: return and clear the flow (any org).
* ``POST internal/credential/oauth-tokens/``    -> store the exchanged tokens, bump the version.

No endpoint returns the access token, the refresh token or the client secret except ``consume``,
which returns the client secret and verifier the gateway needs for the one code exchange.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import MCPServerRegistration
from .views import IsGatewayInternalOnly, _credential_not_found, _gateway_request_org

_STATE_MIN = 32
_STATE_MAX = 128
_FLOW_TTL_MAX_S = 3600
_TEXT_FIELDS = (
    "authorization_endpoint", "token_endpoint", "registration_endpoint", "client_id", "resource",
    "scope",
)


def _no_store(response: Response) -> Response:
    response["Cache-Control"] = "no-store"
    return response


def _server(request, slug: str):
    org = _gateway_request_org(request)
    if org is None or not slug:
        return None
    return MCPServerRegistration.objects.filter(organization=org, server_slug=slug).first()


def _text(data, name: str, limit: int = 1024) -> str | None:
    value = data.get(name, "")
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > limit:
        return None
    return value


class _Internal(APIView):
    permission_classes = [IsGatewayInternalOnly]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "mcp-internal-credential"


class MCPGatewayOAuthStatusView(_Internal):
    def get(self, request):
        slug = (request.query_params.get("server_slug") or "").strip().lower()
        server = _server(request, slug)
        if server is None:
            return _credential_not_found()
        expires = server.oauth_token_expires_at
        authorized = server.auth_type == "oauth" and bool(server.auth_token)
        obtained = server.oauth_token_obtained_at
        return _no_store(Response({
            "authorized": authorized,
            "expired": bool(authorized and expires is not None and expires <= timezone.now()),
            "refreshable": bool(server.oauth_refresh_token and server.oauth_token_endpoint),
            "needs_reauth": bool(server.needs_reauth),
            "obtained_at": obtained.timestamp() if obtained else None,
            "client_id": server.oauth_client_id,
            "client_token_endpoint": server.oauth_token_endpoint,
        }))


class MCPGatewayOAuthFlowView(_Internal):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        slug = str(data.get("server_slug") or "").strip().lower()
        state, verifier = data.get("state"), data.get("code_verifier")
        key_id, ttl = data.get("key_id"), data.get("ttl_s")
        texts = {name: _text(data, name) for name in _TEXT_FIELDS}
        keep_secret = "client_secret" not in data  # a reused client keeps its stored secret
        secret = "" if keep_secret else _text(data, "client_secret", 2048)
        valid = (
            isinstance(state, str) and _STATE_MIN <= len(state) <= _STATE_MAX
            and isinstance(verifier, str) and 43 <= len(verifier) <= 128
            and isinstance(key_id, str) and 0 < len(key_id) <= 128
            and isinstance(ttl, int) and not isinstance(ttl, bool) and 0 < ttl <= _FLOW_TTL_MAX_S
            and None not in texts.values() and secret is not None
            and texts["token_endpoint"] and texts["authorization_endpoint"] and texts["client_id"]
        )
        server = _server(request, slug) if valid else None
        if server is None:
            return _credential_not_found()
        server.oauth_authorization_endpoint = texts["authorization_endpoint"]
        server.oauth_token_endpoint = texts["token_endpoint"]
        server.oauth_registration_endpoint = texts["registration_endpoint"]
        server.oauth_client_id = texts["client_id"]
        if not keep_secret:
            server.oauth_client_secret = secret
        server.oauth_resource = texts["resource"]
        server.oauth_scope = texts["scope"]
        server.oauth_code_verifier = verifier
        server.oauth_state = state
        server.oauth_flow_key_id = key_id
        server.oauth_flow_expires_at = timezone.now() + timedelta(seconds=ttl)
        server.save(update_fields=[
            "oauth_authorization_endpoint", "oauth_token_endpoint", "oauth_registration_endpoint",
            "oauth_client_id", "oauth_client_secret", "oauth_resource", "oauth_scope",
            "oauth_code_verifier", "oauth_state", "oauth_flow_key_id", "oauth_flow_expires_at",
            "updated_at",
        ])
        return _no_store(Response({"stored": True}))


def _clear_flow(server) -> None:
    server.oauth_state = ""
    server.oauth_code_verifier = ""
    server.oauth_flow_key_id = ""
    server.oauth_flow_expires_at = None
    server.save(update_fields=[
        "oauth_state", "oauth_code_verifier", "oauth_flow_key_id", "oauth_flow_expires_at",
        "updated_at",
    ])


class MCPGatewayOAuthFlowConsumeView(_Internal):
    """Single use. The callback carries no key, so the state alone names the org and server."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        state = data.get("state")
        if not isinstance(state, str) or not _STATE_MIN <= len(state) <= _STATE_MAX:
            return _credential_not_found()
        with transaction.atomic():
            server = (
                MCPServerRegistration.objects.select_for_update(of=("self",))
                .select_related("organization")
                .filter(oauth_state=state)
                .first()
            )
            if server is None:
                return _credential_not_found()
            flow = {
                "org_slug": server.organization.slug,
                "server_slug": server.server_slug,
                "key_id": server.oauth_flow_key_id,
                "code_verifier": server.oauth_code_verifier,
                "token_endpoint": server.oauth_token_endpoint,
                "client_id": server.oauth_client_id,
                "client_secret": server.oauth_client_secret,
                "resource": server.oauth_resource,
            }
            expires = server.oauth_flow_expires_at
            _clear_flow(server)  # cleared whether or not it is still valid: never replayable
        if expires is None or expires <= timezone.now() or not flow["key_id"]:
            return _credential_not_found()
        return _no_store(Response(flow))


class MCPGatewayOAuthTokensView(_Internal):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        slug = str(data.get("server_slug") or "").strip().lower()
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires_in = data.get("expires_in")
        valid = (
            isinstance(access, str) and 0 < len(access) <= 2048
            and (refresh is None or (isinstance(refresh, str) and len(refresh) <= 4096))
            and (expires_in is None or (
                isinstance(expires_in, int) and not isinstance(expires_in, bool) and expires_in > 0
            ))
        )
        server = _server(request, slug) if valid else None
        if server is None:
            return _credential_not_found()
        now = timezone.now()
        values = {
            "auth_type": "oauth",
            "auth_token": access,
            "oauth_token_expires_at": now + timedelta(seconds=expires_in) if expires_in else None,
            "oauth_token_obtained_at": now,
            "needs_reauth": False,
            "last_sync_error": "",
            "credential_version": F("credential_version") + 1,
        }
        if refresh:
            values["oauth_refresh_token"] = refresh
        MCPServerRegistration.objects.filter(pk=server.pk).update(**values)
        from .signals import bump_scan_version

        bump_scan_version(server.organization.slug, slug)  # update() fires no post_save
        return _no_store(Response({"stored": True}))
