"""
Organization-scoping helper for multi-tenant ZeroShield.
"""


def _request_data_get(request, key):
    """Safely read a key from request data for DRF and non-DRF requests."""
    data = getattr(request, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    return None


def _request_query_get(request, key):
    """Safely read a key from query params for DRF and Django requests."""
    query_params = getattr(request, "query_params", None)
    if query_params is not None:
        return query_params.get(key)
    get_params = getattr(request, "GET", None)
    if get_params is not None:
        return get_params.get(key)
    return None


def get_request_organization(request):
    """
    Return the Organization for the current request for data scoping.
    - Agent-key authenticated: returns org from request.agent_organization_id (set by AgentKeyAuthentication).
    - Authenticated non-superuser: returns request.user.profile.organization (may be None).
    - Superuser with ?organization_id=<id> or body organization_id: returns that Organization.
    - Superuser without explicit org: falls back to own profile organization.
    - Unauthenticated: returns None.
    """
    agent_org_id = getattr(request, "agent_organization_id", None)
    if agent_org_id is not None:
        from auth.models import Organization
        return Organization.objects.filter(pk=agent_org_id, is_active=True).first()

    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return None
    if request.user.is_superuser:
        org_id = _request_query_get(request, "organization_id") or _request_data_get(request, "organization_id")
        if org_id is not None:
            try:
                org_id = int(org_id)
            except (ValueError, TypeError):
                return None
            from auth.models import Organization
            return Organization.objects.filter(pk=org_id).first()
        try:
            return request.user.profile.organization
        except Exception:
            return None
    try:
        return request.user.profile.organization
    except Exception:
        return None
