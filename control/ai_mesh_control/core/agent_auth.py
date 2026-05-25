"""
Agent API key authentication for /api/agents/ (Phase 2.5).
Accepts Authorization: Bearer <key> or X-Agent-Key: <key>.
When AGENT_API_KEY is non-empty, request must supply a matching key; otherwise 401.
When AGENT_API_KEY is empty, all requests are allowed (dev).
Also supports per-organization OrganizationAgentKey; when used, sets request.agent_organization_id.
"""

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
    if configured and key == configured:
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
        if configured and key == configured:
            return (AnonymousUser(), key)
        from core.models import OrganizationAgentKey

        key_hash = OrganizationAgentKey.hash_raw_key(key)
        org_key = OrganizationAgentKey.objects.filter(
            key_hash=key_hash, is_active=True
        ).select_related("organization").first()
        if org_key:
            request.agent_organization_id = org_key.organization_id
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
