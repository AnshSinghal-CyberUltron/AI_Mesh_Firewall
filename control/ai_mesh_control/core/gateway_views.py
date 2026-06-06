"""
Gateway stats/bootstrap APIs for gateway analytics and simulator integration.
"""

import logging
import os

from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Agent, GatewayAPIKey
from policy.models import EnforcementEvent

logger = logging.getLogger(__name__)

# Minutes after which gateway is considered not healthy
GATEWAY_STALE_MINUTES = 5


def _simulator_defaults_enabled() -> bool:
    return os.getenv("SIMULATOR_DEFAULTS_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _resolve_request_org(user):
    profile = getattr(user, "profile", None)
    return getattr(profile, "organization", None)


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
            status_label = "HEALTHY" if delta and delta.total_seconds() < GATEWAY_STALE_MINUTES * 60 else "WARNING"
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
                    "status": status_label,
                }
            )
        return Response(results)


class SimulatorDefaultGatewayKeyView(APIView):
    """
    Per-organization simulator gateway key provisioning.

    GET  — metadata only (prefix, has_gateway_key); never returns plaintext.
    POST — lazy-create ``name=simulator`` key; plaintext returned once on creation.
    """

    permission_classes = [IsAuthenticated]

    def _resolve(self, request):
        if not _simulator_defaults_enabled():
            return None, None, Response(
                {"detail": "Simulator defaults are disabled."},
                status=status.HTTP_404_NOT_FOUND,
            )
        org = _resolve_request_org(request.user)
        if org is None:
            return None, None, Response(
                {"detail": "Organization membership required for simulator keys."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return org, request.user, None

    def get(self, request):
        org, _user, err = self._resolve(request)
        if err:
            return err

        project_id = f"simulator-{org.slug}"
        key = GatewayAPIKey.objects.filter(
            organization=org,
            project_id=project_id,
            is_active=True,
        ).first()
        if key is None:
            key = GatewayAPIKey.objects.filter(
                organization=org,
                name="simulator",
                is_active=True,
            ).first()

        if key is None:
            return Response({"has_gateway_key": False})

        storage_key = f"zeroshield_gateway_key:{org.id}"
        return Response(
            {
                "has_gateway_key": True,
                "prefix": key.prefix,
                "name": key.name,
                "project_id": key.project_id,
                "org_id": org.id,
                "org_slug": org.slug,
                "storage_key": storage_key,
            }
        )

    def post(self, request):
        org, actor, err = self._resolve(request)
        if err:
            return err

        from core.simulator_seed import ensure_simulator_dev_bootstrap

        ensure_simulator_dev_bootstrap(org)

        ensure = str(request.query_params.get("ensure", "")).lower() in ("1", "true", "yes", "on")

        key_instance, raw_key = GatewayAPIKey.ensure_simulator_for_org(org, actor)
        # ?ensure=1: the caller (a simulator panel) needs a USABLE key. If one
        # already exists but its plaintext is unrecoverable (raw_key is None),
        # rotate so the browser always receives a working credential.
        if raw_key is None and ensure:
            key_instance, raw_key = GatewayAPIKey.rotate_simulator_for_org(org, actor)
            logger.info("Simulator gateway key rotated (ensure=1) org_id=%s", org.id)
        storage_key = f"zeroshield_gateway_key:{org.id}"
        data = {
            "has_gateway_key": True,
            "prefix": key_instance.prefix,
            "name": key_instance.name,
            "project_id": key_instance.project_id,
            "org_id": org.id,
            "org_slug": org.slug,
            "storage_key": storage_key,
            "created": raw_key is not None,
        }
        if raw_key:
            data["key"] = raw_key
            data["warning"] = "Store this key securely. It will not be shown again."
            logger.info(
                "Simulator gateway key provisioned org_id=%s prefix=%s",
                org.id,
                key_instance.prefix,
            )
        return Response(
            data,
            status=status.HTTP_201_CREATED if raw_key else status.HTTP_200_OK,
        )
