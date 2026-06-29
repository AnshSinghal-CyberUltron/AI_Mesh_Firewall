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
    - Platform operator (is_staff AND is_platform_operator) with ?organization_id=<id>
      or body organization_id: returns that Organization (the ONLY cross-org identity).
    - Platform operator without explicit org: falls back to own profile organization.
    - Any other authenticated user (incl. plain superusers / org admins): ORG-scoped
      to request.user.profile.organization; an explicit ?organization_id is ignored.
    - Unauthenticated: returns None.
    """
    agent_org_id = getattr(request, "agent_organization_id", None)
    if agent_org_id is not None:
        from auth.models import Organization
        return Organization.objects.filter(pk=agent_org_id, is_active=True).first()

    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return None

    # Cross-org targeting via ?organization_id is reserved for PLATFORM OPERATORS
    # only. A plain superuser or per-tenant org admin is ORG-scoped: they cannot
    # read/compile-push another org by passing organization_id (product rule:
    # "org admins and superusers are ORG-scoped only, NO global cross-org").
    from auth.models import is_platform_operator

    if is_platform_operator(request.user):
        org_id = _request_query_get(request, "organization_id") or _request_data_get(request, "organization_id")
        if org_id is not None and str(org_id).strip() != "":
            # An explicit organization_id was requested. It MUST resolve to a
            # real org — otherwise FAIL CLOSED with 400. Returning None here
            # previously let operator views fall through to an unscoped
            # all-tenants queryset (cross-org read/write/delete on a typo'd or
            # deleted org id).
            from rest_framework.exceptions import ValidationError

            try:
                org_id = int(org_id)
            except (ValueError, TypeError):
                raise ValidationError({"organization_id": "Invalid organization_id."})
            from auth.models import Organization

            org = Organization.objects.filter(pk=org_id).first()
            if org is None:
                raise ValidationError({"organization_id": "Organization not found."})
            # L3: audit a GENUINE cross-org access — a platform operator reading
            # another org's data via ?organization_id. Best-effort (never break the
            # request); only logged when the target differs from the operator's own
            # org, so same-org operator traffic carries no write overhead.
            try:
                _own_org_id = getattr(getattr(request.user, "profile", None), "organization_id", None)
                if org.id != _own_org_id:
                    from core.models import AuditLog

                    _fwd = (request.META.get("HTTP_X_FORWARDED_FOR", "") or "").split(",")[0].strip()
                    _ip = _fwd or request.META.get("REMOTE_ADDR") or None
                    AuditLog.objects.create(
                        user=request.user,
                        organization=org,
                        action="cross_org_access",
                        resource=f"organization:{org.id}",
                        details=(
                            f"Platform operator '{getattr(request.user, 'username', '?')}' accessed org "
                            f"{org.id} ({getattr(org, 'slug', '')}) via ?organization_id "
                            f"[own_org={_own_org_id}, path={getattr(request, 'path', '')}, "
                            f"method={getattr(request, 'method', '')}]"
                        ),
                        ip_address=_ip,
                    )
            except Exception:
                pass
            return org
        # No explicit org requested → fall back to the operator's own org.
        try:
            return request.user.profile.organization
        except Exception:
            return None
    # Everyone else (incl. is_superuser without the operator flag) is scoped to
    # their own organization; ?organization_id is intentionally NOT honored.
    try:
        return request.user.profile.organization
    except Exception:
        return None
