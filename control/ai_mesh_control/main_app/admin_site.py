"""Django admin site for ZeroShield staff only (#122, Reviewer A B1).

Owner decision (12 Nov 2026): "superuser = ZeroShield staff". Django admin is a staff
surface by design, and it keeps every superuser cross-org. It is also reachable from the
internet through the control vhost (deploy/nginx.conf, ``aimeshbackend`` server,
``location /``); an IP allow-list or SSO in front of /admin/ is a hardening item for the
owner, not done here.

What this site adds to Django's default (``is_active and is_staff``):
- a staff account with an active (uncleared) TerminatedSession is refused, as the REST API
  refuses it (auth/authentication.py); Django's session login does not check it;
- every admin view runs inside ``privilege_actor(request.user, "django_admin")`` so that a
  change to is_superuser / is_staff / is_platform_operator made here is attributed to the
  staff member in AuditLog (auth/privilege_audit.py);
- every admin view request by staff writes one ``admin_access`` AuditLog row (actor, method,
  path, status). Django's own LogEntry keeps recording admin writes.

Only staff can hold is_staff or is_superuser: no API path sets them (auth/privilege_audit.py).
"""

from __future__ import annotations

import functools
import json
import logging

from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig

logger = logging.getLogger("main_app.admin_site")


class StaffAdminSite(AdminSite):
    site_header = "ZeroShield staff administration"

    def has_permission(self, request):
        if not super().has_permission(request):
            return False
        from auth.models import TerminatedSession

        return not TerminatedSession.objects.filter(user_id=request.user.pk, cleared_at__isnull=True).exists()

    def admin_view(self, view, cacheable=False):
        inner = super().admin_view(view, cacheable)

        @functools.wraps(inner)
        def wrapper(request, *args, **kwargs):
            from auth.privilege_audit import privilege_actor

            user = getattr(request, "user", None)
            actor = user if getattr(user, "is_authenticated", False) else None
            with privilege_actor(actor, "django_admin"):
                response = inner(request, *args, **kwargs)
            if actor is not None and self.has_permission(request):
                _audit_admin_access(request, response)
            return response

        return wrapper


def _audit_admin_access(request, response):
    from auth.audit import remote_addr
    from core.models import AuditLog

    try:
        AuditLog.objects.create(
            user=request.user,
            organization=None,
            action="admin_access",
            resource=request.path[:255],
            details=json.dumps(
                {
                    "method": request.method,
                    "path": request.path,
                    "query": (request.META.get("QUERY_STRING") or "")[:512],
                    "status": getattr(response, "status_code", None),
                },
                sort_keys=True,
            ),
            ip_address=remote_addr(request.META),
        )
    except Exception:
        logger.warning("admin_access audit write failed for user_id=%s", request.user.pk, exc_info=True)


class StaffAdminConfig(AdminConfig):
    default_site = "main_app.admin_site.StaffAdminSite"
