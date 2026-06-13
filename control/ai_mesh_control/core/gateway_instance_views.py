"""
Firewall-only gateway instance APIs (not desktop/endpoint agent distribution).

Replaces monorepo /api/agents/register/ and /api/agents/gateway-url/ for this SKU.
"""

import os
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.agent_auth import AgentAPIKeyPermission, AgentKeyAuthentication
from core.models import Agent, Endpoint
from core.serializers import AgentRegisterSerializer, AgentTelemetrySerializer

_INTERNAL_HOSTS = frozenset(
    {
        "control",
        "gateway",
        "frontend",
        "postgres",
        "redis",
        "rabbitmq",
        "mongo",
        "guardrails",
        "mcp-broker",
        "vector-retrieval",
        "workers",
    }
)


def _is_browser_reachable(url: str) -> bool:
    raw = (url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return False
    try:
        return urlparse(raw).hostname not in _INTERNAL_HOSTS
    except Exception:
        return False


def _local_frontend_origin(request: Request) -> str:
    origin = (request.headers.get("Origin") or "").strip()
    if origin and _is_browser_reachable(origin):
        return origin.rstrip("/")
    referer = (request.headers.get("Referer") or "").strip()
    if referer:
        try:
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                candidate = f"{parsed.scheme}://{parsed.netloc}"
                if _is_browser_reachable(candidate):
                    return candidate.rstrip("/")
        except Exception:
            pass
    return ""


def _resolve_public_gateway_url(request: Request) -> str:
    # Local dev: simulators must hit the Vite same-origin proxy so browser API keys
    # match the control plane Redis — not a remote GATEWAY_PUBLIC_URL from .env.
    if getattr(settings, "DEBUG", False):
        for candidate in (
            _local_frontend_origin(request),
            "http://127.0.0.1:8180",
            "http://localhost:8180",
            "http://127.0.0.1:8300",
            os.environ.get("FRONTEND_ORIGIN", "").strip(),
        ):
            if candidate and _is_browser_reachable(candidate):
                return candidate.rstrip("/")

    for candidate in (
        getattr(settings, "GATEWAY_PUBLIC_URL", None) or "",
        os.environ.get("FRONTEND_ORIGIN", "").strip(),
        "http://127.0.0.1:8180",
        "http://127.0.0.1:8300",
    ):
        if _is_browser_reachable(candidate):
            return candidate.rstrip("/")
    return "http://127.0.0.1:8180"


def _resolve_public_backend_url(request: Request) -> str:
    for candidate in (
        getattr(settings, "BACKEND_PUBLIC_URL", None) or "",
        os.environ.get("FRONTEND_ORIGIN", "").strip(),
        "http://127.0.0.1:8180",
        "http://127.0.0.1:8100",
    ):
        if _is_browser_reachable(candidate):
            return candidate.rstrip("/")
    return "http://127.0.0.1:8180"


class GatewayPublicUrlView(APIView):
    """GET /api/gateways/public-url/ — gateway + backend base URLs for UI simulators."""

    permission_classes = [AllowAny]

    def get(self, request: Request):
        gateway_base = _resolve_public_gateway_url(request)
        backend_base = _resolve_public_backend_url(request)
        return Response(
            {
                "gateway_url": gateway_base,
                "api_endpoint": f"{gateway_base}/v1",
                "backend_url": backend_base,
            }
        )


class GatewayInstanceRegisterView(APIView):
    """POST /api/gateways/instances/register/ — idempotent gateway agent row for SOC stats."""

    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def post(self, request: Request):
        ser = AgentRegisterSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        data = ser.validated_data
        if data.get("agent_type") != "gateway":
            return Response(
                {"detail": "Only agent_type=gateway is supported in AI Mesh Firewall."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org_id = getattr(request, "agent_organization_id", None)
        endpoint = None
        identifier = (data.get("endpoint_identifier") or "").strip()
        if identifier:
            endpoint, created = Endpoint.objects.get_or_create(
                identifier=identifier,
                defaults={
                    "name": identifier,
                    "status": "online",
                    "last_seen_at": timezone.now(),
                    "organization_id": org_id,
                },
            )
            if not created:
                endpoint.status = "online"
                endpoint.last_seen_at = timezone.now()
                if org_id and endpoint.organization_id is None:
                    endpoint.organization_id = org_id
                endpoint.save(update_fields=["status", "last_seen_at", "organization_id", "updated_at"])

        agent_id = data.get("agent_id")
        if agent_id:
            agent = Agent.objects.filter(pk=agent_id, agent_type="gateway").first()
            if agent:
                agent.name = data["name"]
                agent.endpoint = endpoint
                agent.metadata = data.get("metadata") or {}
                agent.save(update_fields=["name", "endpoint", "metadata", "updated_at"])
                return Response(
                    {"agent_id": str(agent.id), "endpoint_id": agent.endpoint_id, "status": "updated"},
                    status=status.HTTP_200_OK,
                )

        if endpoint is not None:
            existing = (
                Agent.objects.filter(agent_type="gateway", endpoint=endpoint)
                .order_by("-updated_at")
                .first()
            )
            if existing:
                existing.name = data["name"]
                existing.metadata = data.get("metadata") or {}
                existing.save(update_fields=["name", "metadata", "updated_at"])
                return Response(
                    {"agent_id": str(existing.id), "endpoint_id": existing.endpoint_id, "status": "updated"},
                    status=status.HTTP_200_OK,
                )

        agent = Agent.objects.create(
            agent_type="gateway",
            name=data["name"],
            endpoint=endpoint,
            metadata=data.get("metadata") or {},
        )
        return Response(
            {"agent_id": str(agent.id), "endpoint_id": agent.endpoint_id, "status": "registered"},
            status=status.HTTP_201_CREATED,
        )


class GatewayInstanceTelemetryView(APIView):
    """POST /api/gateways/instances/telemetry/ — gateway metrics heartbeat."""

    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def post(self, request: Request):
        ser = AgentTelemetrySerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        data = ser.validated_data
        org_id = getattr(request, "agent_organization_id", None)

        try:
            agent = Agent.objects.select_related("endpoint").get(pk=data["agent_id"], agent_type="gateway")
        except Agent.DoesNotExist:
            return Response({"detail": "Gateway agent not found."}, status=status.HTTP_404_NOT_FOUND)

        if org_id is not None and agent.endpoint_id and agent.endpoint.organization_id != org_id:
            return Response(
                {"detail": "Agent belongs to another organization."},
                status=status.HTTP_403_FORBIDDEN,
            )

        now = timezone.now()
        endpoint = agent.endpoint
        if endpoint:
            endpoint.last_seen_at = now
            endpoint.status = "online"
            endpoint.save(update_fields=["last_seen_at", "status", "updated_at"])

        gate_meta = dict(agent.metadata or {})
        for key in (
            "total_requests",
            "blocked",
            "allowed",
            "avg_latency_ms",
            "active_connections",
            "rules_applied",
        ):
            if data.get(key) is not None:
                gate_meta[key] = data[key]
        if data.get("location") is not None:
            gate_meta["location"] = data["location"]
        agent.metadata = gate_meta
        agent.save(update_fields=["metadata", "updated_at"])
        return Response({"status": "ok"}, status=status.HTTP_200_OK)
