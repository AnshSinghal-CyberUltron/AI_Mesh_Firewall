"""
Phase 1 Fx-2b — Server-side admin proxy for gateway /v1/admin/ endpoints.

Why this exists
---------------
Gateway admin endpoints (e.g. ``/v1/admin/circuit-breaker-state``) require
``permissions.admin = True`` in the API-key payload OR a user_id listed in
``GATEWAY_ADMIN_USER_IDS``. The frontend's gateway API key (issued per org)
does not currently carry admin perms, so the demo simulator UI was getting
403s. Rather than mint admin keys per user (privilege expansion in the
data path), we proxy admin reads/mutations through Django where:

  * Django enforces ``IsAdminOrSuperuser`` against the user's session.
  * Django forwards to the gateway using the server-side shared secret
    ``GATEWAY_INTERNAL_API_KEY`` (header ``X-Gateway-Internal-Key``).
  * The gateway's ``_require_admin_role`` short-circuits on the matching
    internal-key header (see ``gateway/ai_mesh_gateway/main.py``).

Response shape
--------------
All endpoints return ``{"status": "ok" | "error", "data": <gw_json>}``
matching the envelope convention used elsewhere under ``/api/admin/``.
"""
from __future__ import annotations

import json
import logging
import os

import requests
from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.admin_views import IsAdminOrSuperuser

logger = logging.getLogger(__name__)


def _gateway_base_url() -> str:
    return (
        (getattr(settings, "GATEWAY_URL", "") or "").strip().rstrip("/")
        or os.environ.get("GATEWAY_URL", "").strip().rstrip("/")
        or "http://gateway:8300"
    )


def _gateway_internal_secret() -> str:
    return (
        (getattr(settings, "GATEWAY_INTERNAL_API_KEY", "") or "").strip()
        or os.environ.get("GATEWAY_INTERNAL_API_KEY", "").strip()
    )


def _envelope_error(message: str, code: str, status: int) -> Response:
    return Response(
        {"status": "error", "data": {"error": code, "message": message}},
        status=status,
    )


def _proxy(method: str, gw_path: str, payload: dict | None = None) -> Response:
    secret = _gateway_internal_secret()
    if not secret:
        logger.error("Gateway admin proxy: GATEWAY_INTERNAL_API_KEY not configured")
        return _envelope_error(
            "Gateway admin proxy not configured (missing GATEWAY_INTERNAL_API_KEY).",
            "proxy_unconfigured",
            503,
        )

    url = f"{_gateway_base_url()}{gw_path}"
    headers = {
        "X-Gateway-Internal-Key": secret,
        "Content-Type": "application/json",
    }
    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=10)
        else:
            resp = requests.request(
                method,
                url,
                headers=headers,
                data=json.dumps(payload or {}),
                timeout=10,
            )
    except requests.RequestException as exc:
        logger.warning("Gateway admin proxy %s %s failed: %s", method, url, exc)
        return _envelope_error(
            f"Gateway unreachable: {exc}", "gateway_unreachable", 502
        )

    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:2000]}

    status_label = "ok" if 200 <= resp.status_code < 300 else "error"
    return Response(
        {"status": status_label, "data": body},
        status=resp.status_code,
    )


class CircuitBreakerStateProxyView(APIView):
    """GET /api/admin/gateway/circuit-breaker/state/"""

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def get(self, request: Request) -> Response:
        return _proxy("GET", "/v1/admin/circuit-breaker-state")


class CircuitBreakerTriggerProxyView(APIView):
    """POST /api/admin/gateway/circuit-breaker/trigger/

    Body: ``{"model": str, "error_count": int, "error_type": str}``.
    """

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def post(self, request: Request) -> Response:
        return _proxy("POST", "/v1/admin/circuit-breaker-trigger", request.data)


class CircuitBreakerResetProxyView(APIView):
    """POST /api/admin/gateway/circuit-breaker/reset/

    Body: ``{"model": str}``.
    """

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def post(self, request: Request) -> Response:
        return _proxy("POST", "/v1/admin/circuit-breaker-reset", request.data)


# --- Phase 1 F-3.1: RAG collection management proxy --------------------
# The tenant ``/v1/rag/collections`` endpoint requires a per-org Bearer
# key whose plaintext Django does not store. We expose three admin
# variants on the gateway (see ``admin_rag_*`` in
# ``gateway/ai_mesh_gateway/main.py``) that accept ``project_id``
# explicitly and are gated by the internal-key bypass. Django resolves
# the caller's ``project_id`` from their organization (slug fallback to
# pk) so the simulator UI never needs an operator-pasted Bearer key.

from auth.utils import get_request_organization  # noqa: E402


def _resolve_project_id(request: Request) -> str | None:
    """Pick the project_id for the calling user's organization.

    Precedence: explicit ``project_id`` in body/query (superusers only,
    for cross-tenant inspection) > ``org.slug`` > ``str(org.pk)``.
    """
    explicit = (
        (request.data.get("project_id") if hasattr(request, "data") else None)
        or request.query_params.get("project_id")
        if hasattr(request, "query_params")
        else None
    )
    if explicit and getattr(request.user, "is_superuser", False):
        return str(explicit).strip() or None
    org = get_request_organization(request)
    if org is None:
        return None
    slug = (getattr(org, "slug", "") or "").strip()
    if slug:
        return slug
    return str(org.pk)


class GatewayRagCollectionsProxyView(APIView):
    """GET/POST/DELETE /api/admin/gateway/rag/collections/

    Lists, creates, or deletes vector-DB collections for the caller's
    organization. The body for POST/DELETE is forwarded as-is plus a
    server-stamped ``project_id`` so the browser cannot impersonate
    another tenant.
    """

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def get(self, request: Request) -> Response:
        project_id = _resolve_project_id(request)
        if not project_id:
            return _envelope_error(
                "Caller has no organization to scope RAG collections.",
                "no_organization",
                400,
            )
        return _proxy("GET", f"/v1/admin/rag/collections?project_id={project_id}")

    def post(self, request: Request) -> Response:
        project_id = _resolve_project_id(request)
        if not project_id:
            return _envelope_error(
                "Caller has no organization to scope RAG collections.",
                "no_organization",
                400,
            )
        payload = dict(request.data or {})
        payload["project_id"] = project_id  # server-stamped, browser cannot override for non-superusers
        return _proxy("POST", "/v1/admin/rag/collections", payload)

    def delete(self, request: Request) -> Response:
        project_id = _resolve_project_id(request)
        if not project_id:
            return _envelope_error(
                "Caller has no organization to scope RAG collections.",
                "no_organization",
                400,
            )
        payload = dict(request.data or {})
        payload["project_id"] = project_id
        return _proxy("DELETE", "/v1/admin/rag/collections", payload)
