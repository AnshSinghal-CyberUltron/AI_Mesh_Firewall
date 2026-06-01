"""
Admin Portal API: agent version stats, global configuration.
"""

from collections import defaultdict

from django.conf import settings
from django.utils import timezone
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Endpoint, SystemConfig


def _parse_version(v):
    """Parse version string to tuple of ints for comparison (e.g. '0.1.0' -> (0, 1, 0))."""
    if not v:
        return (0, 0, 0)
    parts = []
    for s in str(v).strip().split(".")[:4]:
        try:
            parts.append(int(s))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class IsAdminOrSuperuser(BasePermission):
    """Allow access only for superusers, staff, or users with platform_admin role."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_staff:
            return True
        try:
            return request.user.profile.roles.filter(name="platform_admin").exists()
        except Exception:
            return False


ADMIN_CONFIG_DEFAULTS = {
    "telemetryUploadInterval": 5,
    "enforcementMode": "block",
    "autoUpdates": True,
    "tamperProtection": True,
}

class AdminAgentVersionStatsView(APIView):
    """
    GET /api/admin/agent-version-stats/
    Agent versions with endpoint counts and status (STABLE/DEPRECATED/CRITICAL).
    """

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def get(self, request: Request) -> Response:
        from auth.utils import get_request_organization
        org = get_request_organization(request)
        min_ver_str = getattr(settings, "AGENT_MIN_VERSION", "0.1.0") or "0.1.0"
        rec_ver_str = getattr(settings, "AGENT_RECOMMENDED_VERSION", "") or min_ver_str
        min_ver = _parse_version(min_ver_str)
        rec_ver = _parse_version(rec_ver_str)

        endpoints_qs = Endpoint.objects.values("metadata")
        if org is not None:
            endpoints_qs = endpoints_qs.filter(organization=org)
        elif not (getattr(request, "user", None) and request.user.is_superuser):
            endpoints_qs = endpoints_qs.none()
        by_version = defaultdict(int)
        for ep in endpoints_qs:
            meta = ep.get("metadata") or {}
            ver = (meta.get("agent_version") or "").strip() or "Unknown"
            by_version[ver] += 1

        results = []
        for ver, count in sorted(by_version.items(), key=lambda x: -x[1]):
            if ver == "Unknown":
                status = "CRITICAL"
            else:
                t = _parse_version(ver)
                if t < min_ver:
                    status = "CRITICAL"
                elif t < rec_ver:
                    status = "DEPRECATED"
                else:
                    status = "STABLE"
            results.append({
                "version": ver,
                "endpoints": count,
                "status": status,
                "released": None,
            })
        return Response(results)


class AdminEndpointHealthView(APIView):
    """
    GET /api/admin/endpoint-health/
    Aggregate endpoint status: total, online, offline counts.
    """

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def get(self, request: Request) -> Response:
        from auth.utils import get_request_organization
        org = get_request_organization(request)
        endpoints_qs = Endpoint.objects.all()
        if org is not None:
            endpoints_qs = endpoints_qs.filter(organization=org)
        elif not (getattr(request, "user", None) and request.user.is_superuser):
            endpoints_qs = endpoints_qs.none()
        total = endpoints_qs.count()
        online = endpoints_qs.filter(status="online").count()
        offline = total - online
        return Response({
            "total": total,
            "online": online,
            "offline": offline,
        })


class AdminConfigView(APIView):
    """
    GET /api/admin/config/ — read admin portal config
    PUT /api/admin/config/ — update admin portal config
    """

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def get(self, request: Request) -> Response:
        cfg = SystemConfig.objects.first()
        admin_config = {}
        if cfg and isinstance(cfg.config, dict):
            admin_config = (cfg.config.get("admin_portal") or {}).copy()

        out = ADMIN_CONFIG_DEFAULTS.copy()
        for k, v in admin_config.items():
            if k in out:
                out[k] = v
        return Response(out)

    def put(self, request: Request) -> Response:
        data = request.data or {}
        admin_config = {
            "telemetryUploadInterval": data.get(
                "telemetryUploadInterval", ADMIN_CONFIG_DEFAULTS["telemetryUploadInterval"]
            ),
            "enforcementMode": (
                str(data.get("enforcementMode", ADMIN_CONFIG_DEFAULTS["enforcementMode"])).lower()
                or "block"
            ),
            "autoUpdates": bool(data.get("autoUpdates", ADMIN_CONFIG_DEFAULTS["autoUpdates"])),
            "tamperProtection": bool(
                data.get("tamperProtection", ADMIN_CONFIG_DEFAULTS["tamperProtection"])
            ),
        }
        if admin_config["enforcementMode"] not in ("block", "alert", "monitor"):
            admin_config["enforcementMode"] = "block"
        ti = admin_config["telemetryUploadInterval"]
        if ti not in (1, 5, 10, 30, 60):
            try:
                ti = int(ti)
                if ti not in (1, 5, 10, 30, 60):
                    ti = 5
            except (TypeError, ValueError):
                ti = 5
        admin_config["telemetryUploadInterval"] = ti

        cfg = SystemConfig.objects.first()
        if cfg is None:
            cfg = SystemConfig.objects.create(config={})
        full_config = dict(cfg.config or {})
        full_config["admin_portal"] = admin_config
        cfg.config = full_config
        cfg.updated_at = timezone.now()
        cfg.save(update_fields=["config", "updated_at"])
        return Response(admin_config)


