"""
Agent API key authentication for /api/agents/ (Phase 2.5).
Accepts Authorization: Bearer <key> or X-Agent-Key: <key>.
When AGENT_API_KEY is non-empty, request must supply a matching key; otherwise 401.
When AGENT_API_KEY is empty, all requests are allowed (dev).
Also supports per-organization OrganizationAgentKey; when used, sets request.agent_organization_id.
"""

import secrets

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission


def _get_request_key(request):
    """Extract agent API key from Authorization: Bearer or X-Agent-Key header."""
    auth = request.META.get("HTTP_AUTHORIZATION") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.META.get("HTTP_X_AGENT_KEY") or ""


def _resolve_agent_auth(request):
    """
    Validate agent API key and set request.agent_organization_id when using an org key.
    Returns True if allowed, False if no key given (and global key not required), or raises AuthenticationFailed.
    """
    configured = (getattr(settings, "AGENT_API_KEY", None) or "").strip()
    key = _get_request_key(request)
    request.agent_organization_id = None
    if not key:
        if not configured:
            return True
        raise AuthenticationFailed(
            "Invalid or missing agent API key. Use Authorization: Bearer <key> or X-Agent-Key: <key>."
        )
    # M-07 FIX: use constant-time comparison to avoid leaking the global key via
    # timing side-channels. Both operands are guaranteed non-empty here (key checked
    # above, configured guarded by the `configured and` short-circuit).
    # Compare as bytes: compare_digest raises TypeError on str operands containing
    # non-ASCII, and `key` is attacker-controlled (Django decodes headers latin-1,
    # so bytes >0x7F reach us as U+0080-U+00FF). surrogateescape also covers lone
    # surrogates in `configured` (os.environ decodes with surrogateescape), so this
    # encoding cannot raise on any reachable input.
    if configured and secrets.compare_digest(
        key.encode("utf-8", "surrogateescape"),
        configured.encode("utf-8", "surrogateescape"),
    ):
        return True
    from core.models import OrganizationAgentKey
    key_hash = OrganizationAgentKey.hash_raw_key(key)
    org_key = OrganizationAgentKey.objects.filter(key_hash=key_hash, is_active=True).select_related("organization").first()
    if org_key:
        request.agent_organization_id = org_key.organization_id
        return True
    raise AuthenticationFailed(
        "Invalid or missing agent API key. Use Authorization: Bearer <key> or X-Agent-Key: <key>."
    )


class AgentKeyAuthentication(authentication.BaseAuthentication):
    """
    DRF authentication class for agent API key. Use this as the sole authentication
    class on register/telemetry views so that Bearer <api_key> is validated as an
    agent key instead of being passed to JWT (which would reject it).
    """

    def authenticate(self, request):
        key = _get_request_key(request)
        request.agent_organization_id = None
        if not key:
            return None  # No key; permission layer will allow or deny (e.g. dev mode)
        configured = (getattr(settings, "AGENT_API_KEY", None) or "").strip()
        # M-07 FIX: constant-time comparison to prevent timing-attack key recovery.
        # `key` is non-empty (early return above) and `configured` is guarded by the
        # `configured and` short-circuit, so compare_digest never sees an empty arg.
        # Compare as bytes: compare_digest raises TypeError on str operands containing
        # non-ASCII, and `key` is attacker-controlled (Django decodes headers latin-1,
        # so bytes >0x7F reach us as U+0080-U+00FF). surrogateescape also covers lone
        # surrogates in `configured` (os.environ decodes with surrogateescape), so this
        # encoding cannot raise on any reachable input.
        if configured and secrets.compare_digest(
            key.encode("utf-8", "surrogateescape"),
            configured.encode("utf-8", "surrogateescape"),
        ):
            return (AnonymousUser(), key)
        from core.models import OrganizationAgentKey

        key_hash = OrganizationAgentKey.hash_raw_key(key)
        org_key = OrganizationAgentKey.objects.filter(
            key_hash=key_hash, is_active=True
        ).select_related("organization").first()
        if org_key:
            request.agent_organization_id = org_key.organization_id
            # P9b: stamp org into the log context at auth time for agent-key requests.
            try:
                from main_app.log_extras import set_org_id
                set_org_id(org_key.organization_id)
            except Exception:  # noqa: BLE001
                pass
            return (AnonymousUser(), key)
        raise AuthenticationFailed(
            "Invalid or missing agent API key. Use Authorization: Bearer <key> or X-Agent-Key: <key>."
        )

    def authenticate_header(self, request):
        return "Bearer"


class AgentAPIKeyPermission(BasePermission):
    """
    Allow when AGENT_API_KEY is empty (dev), or when request provides a matching key
    (global AGENT_API_KEY or per-organization OrganizationAgentKey).
    When an OrganizationAgentKey is used, sets request.agent_organization_id.
    """

    def has_permission(self, request, view):
        if not (getattr(settings, "AGENT_API_KEY", None) or "").strip():
            key = _get_request_key(request)
            if key:
                from core.models import OrganizationAgentKey
                key_hash = OrganizationAgentKey.hash_raw_key(key)
                org_key = OrganizationAgentKey.objects.filter(
                    key_hash=key_hash, is_active=True
                ).select_related("organization").first()
                if org_key:
                    request.agent_organization_id = org_key.organization_id
                else:
                    request.agent_organization_id = None
            else:
                request.agent_organization_id = None
            return True
        return _resolve_agent_auth(request)
