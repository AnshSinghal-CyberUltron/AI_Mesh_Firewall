"""AuditLog rows for cross-org access by ZeroShield staff and for denied attempts (#120, #122).

Owner decision (12 Nov 2026): "superuser = ZeroShield staff". Staff keep their cross-org
powers, and every cross-org use is recorded here:

- ``cross_org_access``: a staff member (``auth.models.is_zeroshield_staff``) or a platform
  operator acted on, or subscribed to, an org other than their own; or an org-less staff
  member read a fleet-wide view (``organization`` NULL, ``scope: "all"``). Written BEFORE
  the action and never swallowed: if the row cannot be written the request fails and
  nothing is changed.
- ``cross_org_denied``: a caller who is not staff asked for an object outside their org.
  The row holds only what the caller supplied (the requested reference), never the object's
  org or whether it exists, so it reads the same for "another org's object" and "no such
  object". Best effort: an audit failure never turns the 404 into something else.

``details`` is JSON. ``ip_address`` is REMOTE_ADDR (the hop that connected to us);
X-Real-IP and X-Forwarded-For are kept in ``details`` labelled as proxy- and client-supplied
(a trusted-proxy resolver is #133). The older operator row written by
``auth.utils.get_request_organization`` keeps its text format.
"""

from __future__ import annotations

import ipaddress
import json
import logging

from rest_framework.permissions import SAFE_METHODS

logger = logging.getLogger("auth.audit")

ACTION_CROSS_ORG_ACCESS = "cross_org_access"
ACTION_CROSS_ORG_DENIED = "cross_org_denied"


def actor_org_id(user):
    """The user's own profile org id, or None (no profile, no org, or any error)."""
    try:
        return user.profile.organization_id
    except Exception:
        return None


def remote_addr(meta) -> str | None:
    raw = (meta or {}).get("REMOTE_ADDR") or ""
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def _request_context(request) -> tuple[dict, str | None]:
    """(details fragment, ip) from a DRF/Django request; ({}, None) without one."""
    if request is None:
        return {}, None
    meta = getattr(request, "META", None) or {}
    ctx = {
        "path": getattr(request, "path", ""),
        "method": getattr(request, "method", ""),
    }
    real_ip = meta.get("HTTP_X_REAL_IP")
    if real_ip:
        ctx["x_real_ip_proxy_supplied"] = real_ip[:64]
    xff = meta.get("HTTP_X_FORWARDED_FOR")
    if xff:
        ctx["x_forwarded_for_client_supplied"] = xff[:256]
    return ctx, remote_addr(meta)


def audit_cross_org_access(
    *, user, target_org_id, action: str, obj: str, request=None, scope: str | None = None, via: str = "api",
    extra: dict | None = None,
):
    """Write one ``cross_org_access`` row. Raises on failure (callers must not act unaudited)."""
    from core.models import AuditLog

    ctx, ip = _request_context(request)
    details = {
        "actor_id": getattr(user, "pk", None),
        "actor_org_id": actor_org_id(user),
        "actor_is_superuser": bool(getattr(user, "is_superuser", False)),
        "target_org_id": target_org_id,
        "action": action,
        "object": obj,
        "via": via,
        **ctx,
    }
    if scope:
        details["scope"] = scope
    if extra:
        details.update(extra)
    AuditLog.objects.create(
        user=user if getattr(user, "pk", None) else None,
        organization_id=target_org_id,
        action=ACTION_CROSS_ORG_ACCESS,
        resource=obj[:255],
        details=json.dumps(details, sort_keys=True, default=str),
        ip_address=ip,
    )


def audit_cross_org_denied(*, user, action: str, requested: str, request=None, via: str = "api"):
    """Write one ``cross_org_denied`` row (best effort). See the module docstring."""
    from core.models import AuditLog

    try:
        ctx, ip = _request_context(request)
        own_org = actor_org_id(user)
        details = {
            "actor_id": getattr(user, "pk", None),
            "actor_org_id": own_org,
            "action": action,
            "requested": requested,
            "outcome": "not_found_in_caller_scope",
            "via": via,
            **ctx,
        }
        AuditLog.objects.create(
            user=user if getattr(user, "pk", None) else None,
            organization_id=own_org,
            action=ACTION_CROSS_ORG_DENIED,
            resource=str(requested)[:255],
            details=json.dumps(details, sort_keys=True, default=str),
            ip_address=ip,
        )
    except Exception:
        logger.warning(
            "cross_org_denied audit write failed: action=%s user_id=%s", action, getattr(user, "pk", None),
            exc_info=True,
        )


def orgless_staff_fleet_read(request, view: str) -> bool:
    """True when this request is a fleet-wide READ by an org-less staff member (audited).

    Restores develop's "a superuser without an org sees every tenant" for SAFE methods only
    (owner decision 12 Nov: Reviewer A's capability 3, the Admin Portal fleet reads). Writes
    never fall through to "all orgs". One ``cross_org_access`` row (scope "all") per request,
    whichever view helpers ask first. Raises if the row cannot be written.
    """
    from auth.models import is_zeroshield_staff

    if getattr(request, "method", "GET") not in SAFE_METHODS:
        return False
    user = getattr(request, "user", None)
    if not is_zeroshield_staff(user) or actor_org_id(user) is not None:
        return False
    if not getattr(request, "_zs_fleet_read_audited", False):
        audit_cross_org_access(
            user=user, target_org_id=None, action=f"fleet_read.{view}", obj=f"fleet:{view}",
            request=request, scope="all",
        )
        request._zs_fleet_read_audited = True
    return True
