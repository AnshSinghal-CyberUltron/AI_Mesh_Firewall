"""
Gateway stats/bootstrap APIs for gateway analytics and simulator integration.
"""

import os

import redis
from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Agent, GatewayAPIKey
from policy.models import EnforcementEvent

# Minutes after which gateway is considered not healthy
GATEWAY_STALE_MINUTES = 5
SIMULATOR_DEFAULT_KEY_REDIS = "simulator:default_gateway_key"


def _meta(agent, key, default=None):
    return (agent.metadata or {}).get(key, default)


def _top_blocked_rules_for_agent(agent_id, limit=10):
    """Return formatted string of top blocked rules for this gateway agent."""
    from collections import defaultdict

    events = list(EnforcementEvent.objects.filter(agent_id=agent_id, action="block").values("policy__code", "rule_id"))
    counts = defaultdict(int)
    for e in events:
        code = e.get("policy__code") or "N/A"
        rid = e.get("rule_id") or 0
        counts[(code, rid)] += 1
    items = sorted(counts.items(), key=lambda x: -x[1])[:limit]
    return ", ".join(f"{code}-R{rid}: {c}" for (code, rid), c in items)


class GatewayStatsListView(APIView):
    """GET /api/gateways/stats/ — list gateway agents with stats for SOC table (JWT required)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Gateways"],
        summary="Gateway statistics",
        description=(
            "Returns statistics for all registered gateway agents.\n\n"
            "**Authentication:** JWT required.\n\n"
            "Includes total requests, blocked/allowed counts, block rate, average latency, "
            "active connections, top blocked rules, and health status.\n\n"
            "A gateway is marked **WARNING** if it hasn't sent telemetry in the last 5 minutes."
        ),
        responses={
            200: inline_serializer(
                name="GatewayStatsItem",
                fields={
                    "serverId": drf_serializers.UUIDField(help_text="Gateway agent ID"),
                    "serverName": drf_serializers.CharField(help_text="Gateway name"),
                    "location": drf_serializers.CharField(help_text="Deployment location"),
                    "rulesApplied": drf_serializers.IntegerField(help_text="Number of rules applied by this gateway"),
                    "rulesTriggered": drf_serializers.IntegerField(help_text="Total enforcement events"),
                    "totalRequests": drf_serializers.IntegerField(help_text="Total proxied requests"),
                    "blocked": drf_serializers.IntegerField(help_text="Total blocked requests"),
                    "allowed": drf_serializers.IntegerField(help_text="Total allowed requests"),
                    "blockRate": drf_serializers.FloatField(help_text="Block rate percentage (0-100)"),
                    "avgLatency": drf_serializers.CharField(help_text="Average latency (e.g. '145ms')"),
                    "activeConn": drf_serializers.IntegerField(help_text="Current active connections"),
                    "topBlockedRules": drf_serializers.CharField(help_text="Top blocked rules summary"),
                    "status": drf_serializers.CharField(help_text="HEALTHY or WARNING"),
                },
                many=True,
            ),
        },
        examples=[
            OpenApiExample(
                "Gateway stats",
                value=[
                    {
                        "serverId": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                        "serverName": "Gateway Proxy",
                        "location": "us-east-1",
                        "rulesApplied": 0,
                        "rulesTriggered": 12,
                        "totalRequests": 1542,
                        "blocked": 23,
                        "allowed": 1519,
                        "blockRate": 1.5,
                        "avgLatency": "145ms",
                        "activeConn": 3,
                        "topBlockedRules": "POL001-R1: 15, POL003-R2: 8",
                        "status": "HEALTHY",
                    }
                ],
                response_only=True,
            ),
        ],
    )
    def get(self, request):
        from auth.utils import get_request_organization
        org = get_request_organization(request)
        gateways = Agent.objects.filter(agent_type="gateway").order_by("-updated_at")
        if org is not None:
            gateways = gateways.filter(endpoint__organization=org)
        elif not getattr(request.user, "is_superuser", False):
            gateways = gateways.none()
        agent_ids = list(gateways.values_list("id", flat=True))

        rules_triggered_by_agent = {}
        if agent_ids:
            agg = EnforcementEvent.objects.filter(agent_id__in=agent_ids).values("agent_id").annotate(cnt=Count("id"))
            for row in agg:
                rules_triggered_by_agent[str(row["agent_id"])] = row["cnt"]

        results = []
        now = timezone.now()
        for agent in gateways:
            total = _meta(agent, "total_requests") or 0
            blocked = _meta(agent, "blocked") or 0
            allowed = _meta(agent, "allowed") or 0
            total = int(total)
            blocked = int(blocked)
            allowed = int(allowed)
            block_rate = round(blocked / total * 100, 1) if total > 0 else 0.0
            avg_ms = _meta(agent, "avg_latency_ms")
            avg_latency = f"{int(avg_ms)}ms" if avg_ms is not None else "\u2014"
            delta = now - agent.updated_at if agent.updated_at else None
            status = "HEALTHY" if delta and delta.total_seconds() < GATEWAY_STALE_MINUTES * 60 else "WARNING"
            results.append(
                {
                    "serverId": str(agent.id),
                    "serverName": agent.name,
                    "location": _meta(agent, "location") or "\u2014",
                    "rulesApplied": _meta(agent, "rules_applied") or 0,
                    "rulesTriggered": rules_triggered_by_agent.get(str(agent.id), 0),
                    "totalRequests": total,
                    "blocked": blocked,
                    "allowed": allowed,
                    "blockRate": block_rate,
                    "avgLatency": avg_latency,
                    "activeConn": _meta(agent, "active_connections") or 0,
                    "topBlockedRules": _top_blocked_rules_for_agent(agent.id),
                    "status": status,
                }
            )
        return Response(results)


class SimulatorDefaultGatewayKeyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        enabled = os.getenv(
            "SIMULATOR_DEFAULTS_ENABLED",
            "true" if getattr(settings, "DEBUG", False) else "false",
        ).lower() in ("1", "true", "yes", "on")
        if not enabled:
            return Response({"detail": "Simulator defaults are disabled."}, status=404)

        redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        try:
            client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=3)
            raw_key = client.get(SIMULATOR_DEFAULT_KEY_REDIS)
        except redis.RedisError as exc:
            return Response({"detail": f"Redis unavailable: {exc}"}, status=503)

        if not raw_key:
            return Response({"detail": "Simulator default gateway key is not seeded."}, status=404)

        key_meta = {}
        try:
            key_hash = GatewayAPIKey.hash_raw_key(raw_key)
            db_key = GatewayAPIKey.objects.filter(key_hash=key_hash).first()
            if db_key:
                key_meta = {
                    "prefix": db_key.prefix,
                    "key_id": str(db_key.id),
                    "name": db_key.name,
                    "is_simulator_default": db_key.name == "simulator-default",
                }
        except Exception:
            pass

        return Response({"key": raw_key, "storage_key": "zeroshield_gateway_key", **key_meta})


class GatewayKeyContextView(APIView):
    """Resolve a gateway API key to its UEBA prefix (org-scoped, authenticated)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from auth.utils import get_request_organization

        org = get_request_organization(request)
        if org is None and not getattr(request.user, "is_superuser", False):
            return Response({"detail": "Organization scope is required."}, status=403)

        raw_key = str((request.data or {}).get("api_key") or "").strip()
        if raw_key.lower().startswith("bearer "):
            raw_key = raw_key[7:].strip()
        if not raw_key:
            return Response({"detail": "api_key is required."}, status=400)

        key_hash = GatewayAPIKey.hash_raw_key(raw_key)
        qs = GatewayAPIKey.objects.filter(key_hash=key_hash)
        if org is not None:
            qs = qs.filter(organization=org)
        db_key = qs.first()
        if not db_key:
            return Response({"detail": "API key not found for your organization."}, status=404)

        return Response(
            {
                "prefix": db_key.prefix,
                "key_id": str(db_key.id),
                "name": db_key.name,
                "is_simulator_default": db_key.name == "simulator-default",
                "is_active": db_key.is_active,
            }
        )
