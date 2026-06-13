"""Module 2 API views — UEBA + Threat Intelligence focus."""

from collections import Counter, defaultdict
from datetime import timedelta
from uuid import UUID

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from auth.utils import get_request_organization
from core.admin_views import IsAdminOrSuperuser
from core.models import GatewayAPIKey, LLMModelConfig
from module2.analytics import (
    build_lane_summary,
    build_mcp_activity_payload,
    build_model_exposure_payload,
    build_rag_pipeline_kpis,
    build_stage_hit_distribution,
    build_threat_telemetry_payload,
    build_vector_exposure_payload,
    event_source,
    hours_from_period,
    key_prefix_from_meta,
    paginate_queryset,
    serialize_incident_row,
)
from module2.models import ThreatIntelEntry
from module2.serializers import ThreatIntelEntrySerializer
from module2.tasks import sync_threat_intel_to_redis
from policy.constants import ACTION_BLOCK, ACTION_REDACT
from policy.models import EnforcementEvent, SecurityIncident
from policy.review_views import SecurityIncidentSerializer
from policy.security_views import _enforcement_events_for_request


def _org_or_403(request):
    org = get_request_organization(request)
    if org is None and not getattr(request.user, "is_superuser", False):
        return None
    return org


_hours_from_period = hours_from_period
_key_prefix_from_meta = key_prefix_from_meta
_event_source = event_source


def _collect_key_metrics(keys_qs, events_qs):
    key_by_prefix = {k.prefix: k for k in keys_qs}
    metrics = defaultdict(
        lambda: {
            "total": 0,
            "blocked": 0,
            "redacted": 0,
            "endpoint_ids": set(),
            "models": set(),
            "threat_types": defaultdict(int),
            "hourly": defaultdict(int),
        }
    )

    for ev in events_qs.values("created_at", "action", "endpoint_id", "metadata"):
        meta = ev.get("metadata") or {}
        prefix = _key_prefix_from_meta(meta)
        if not prefix:
            continue
        if prefix not in key_by_prefix:
            continue
        hour_bucket = ev["created_at"].replace(minute=0, second=0, microsecond=0).isoformat()
        m = metrics[prefix]
        m["total"] += 1
        if ev["action"] == ACTION_BLOCK:
            m["blocked"] += 1
        if ev["action"] == ACTION_REDACT:
            m["redacted"] += 1
        if ev.get("endpoint_id"):
            m["endpoint_ids"].add(ev["endpoint_id"])
        if meta.get("model"):
            m["models"].add(str(meta["model"]))
        threat = str(meta.get("threat_type") or "unknown")
        m["threat_types"][threat] += 1
        m["hourly"][hour_bucket] += 1

    return key_by_prefix, metrics


def _risk_payload(prefix: str, key_obj, metric: dict):
    total = metric["total"]
    block_rate = (metric["blocked"] / total) if total else 0.0
    redact_rate = (metric["redacted"] / total) if total else 0.0
    hourly_values = list(metric["hourly"].values()) or [0]
    baseline = sum(hourly_values) / len(hourly_values)
    current = hourly_values[-1] if hourly_values else 0
    velocity_spike = (current / baseline) if baseline else 0.0
    velocity_factor = min(max((velocity_spike - 1.0) / 3.0, 0.0), 1.0)
    risk_score = min((0.55 * block_rate) + (0.2 * redact_rate) + (0.25 * velocity_factor), 1.0)
    if risk_score >= 0.7:
        band = "high"
    elif risk_score >= 0.35:
        band = "medium"
    else:
        band = "low"

    anomalies = []
    if block_rate >= 0.35:
        anomalies.append("high_block_rate")
    if velocity_spike >= 2.5:
        anomalies.append("velocity_spike")
    if len(metric["models"]) >= 4:
        anomalies.append("model_spread")

    top_threat = sorted(metric["threat_types"].items(), key=lambda x: -x[1])[0][0] if metric["threat_types"] else "none"
    return {
        "key_id": str(key_obj.id),
        "prefix": prefix,
        "name": key_obj.name,
        "project_id": key_obj.project_id,
        "is_active": key_obj.is_active,
        "risk_band": band,
        "risk_score": round(risk_score, 3),
        "velocity_spike": round(velocity_spike, 2),
        "anomaly_flags": anomalies,
        "request_count": total,
        "blocked_count": metric["blocked"],
        "redacted_count": metric["redacted"],
        "block_rate_pct": round(block_rate * 100, 1),
        "redact_rate_pct": round(redact_rate * 100, 1),
        "unique_endpoints": len(metric["endpoint_ids"]),
        "unique_models": len(metric["models"]),
        "top_threat_type": top_threat,
    }


class UebaApiKeySummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "24h")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        org = _org_or_403(request)

        keys_qs = GatewayAPIKey.objects.all()
        if org:
            keys_qs = keys_qs.filter(organization=org)
        elif not request.user.is_superuser:
            keys_qs = keys_qs.none()

        events = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        key_by_prefix, metrics = _collect_key_metrics(keys_qs, events)
        rows = [_risk_payload(p, key_by_prefix[p], m) for p, m in metrics.items()]
        rows.sort(key=lambda r: (-r["risk_score"], -r["request_count"]))

        return Response(
            {
                "period": period,
                "summary": {
                    "total_keys": keys_qs.count(),
                    "active_keys": keys_qs.filter(is_active=True).count(),
                    "keys_with_activity": len(rows),
                    "high_risk_keys": sum(1 for r in rows if r["risk_band"] == "high"),
                },
                "top_risky_keys": rows[:10],
            }
        )


class UebaApiKeyRegistryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        qs = GatewayAPIKey.objects.select_related("owner").order_by("-created_at")
        if org:
            qs = qs.filter(organization=org)
        elif not request.user.is_superuser:
            qs = qs.none()

        results = []
        for k in qs[:200]:
            results.append(
                {
                    "key_id": str(k.id),
                    "prefix": k.prefix,
                    "name": k.name,
                    "project_id": k.project_id,
                    "owner_email": getattr(k.owner, "email", ""),
                    "is_active": k.is_active,
                    "rate_limit_tpm": k.rate_limit_tokens_per_minute,
                    "risk_score_baseline": round(float(k.risk_score or 0), 3),
                    "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                    "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                }
            )
        return Response({"count": qs.count(), "results": results})


class UebaApiKeyTimelineView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "24h")
        hours = _hours_from_period(period)
        since = timezone.now() - timedelta(hours=hours)
        org = _org_or_403(request)

        keys_qs = GatewayAPIKey.objects.all()
        if org:
            keys_qs = keys_qs.filter(organization=org)
        elif not request.user.is_superuser:
            keys_qs = keys_qs.none()

        events = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        key_by_prefix, metrics = _collect_key_metrics(keys_qs, events)
        risky_rows = [_risk_payload(p, key_by_prefix[p], m) for p, m in metrics.items()]
        risky_rows.sort(key=lambda r: (-r["risk_score"], -r["request_count"]))
        tracked_prefixes = {r["prefix"] for r in risky_rows[:5]}

        bucket_size = 1 if hours <= 24 else 6
        timeline = []
        for i in range(max(hours // bucket_size, 1)):
            start = since + timedelta(hours=i * bucket_size)
            end = start + timedelta(hours=bucket_size)
            bucket_events = events.filter(created_at__gte=start, created_at__lt=end)
            counts = {prefix: 0 for prefix in tracked_prefixes}
            for ev in bucket_events.values("metadata"):
                prefix = _key_prefix_from_meta(ev.get("metadata") or {})
                if prefix in counts:
                    counts[prefix] += 1
            timeline.append(
                {
                    "timestamp": start.isoformat(),
                    "total_events": bucket_events.count(),
                    "blocked": bucket_events.filter(action=ACTION_BLOCK).count(),
                    "redacted": bucket_events.filter(action=ACTION_REDACT).count(),
                    "keys": counts,
                }
            )

        return Response({"period": period, "tracked_prefixes": sorted(tracked_prefixes), "timeline": timeline})


class UebaApiKeyBehaviorView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, key_id):
        org = _org_or_403(request)
        try:
            UUID(str(key_id))
        except ValueError:
            return Response({"detail": "Invalid key id."}, status=status.HTTP_400_BAD_REQUEST)

        key_qs = GatewayAPIKey.objects.select_related("owner")
        if org:
            key_qs = key_qs.filter(organization=org)
        elif not request.user.is_superuser:
            key_qs = key_qs.none()
        key = key_qs.filter(pk=key_id).first()
        if not key:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        period = request.query_params.get("period", "7d")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        events = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        events = [e for e in events.values("created_at", "action", "endpoint_id", "metadata") if _key_prefix_from_meta(e.get("metadata") or {}) == key.prefix]

        endpoint_counts = defaultdict(int)
        model_counts = defaultdict(int)
        threat_counts = defaultdict(int)
        for ev in events:
            if ev.get("endpoint_id"):
                endpoint_counts[str(ev["endpoint_id"])] += 1
            meta = ev.get("metadata") or {}
            model_counts[str(meta.get("model") or "unknown")] += 1
            threat_counts[str(meta.get("threat_type") or "unknown")] += 1

        metric = {
            "total": len(events),
            "blocked": sum(1 for e in events if e["action"] == ACTION_BLOCK),
            "redacted": sum(1 for e in events if e["action"] == ACTION_REDACT),
            "endpoint_ids": set(endpoint_counts.keys()),
            "models": set(model_counts.keys()),
            "threat_types": threat_counts,
            "hourly": defaultdict(int),
        }
        for ev in events:
            bucket = ev["created_at"].replace(minute=0, second=0, microsecond=0).isoformat()
            metric["hourly"][bucket] += 1

        payload = _risk_payload(key.prefix, key, metric)
        payload["top_endpoints"] = sorted(endpoint_counts.items(), key=lambda x: -x[1])[:10]
        payload["top_models"] = sorted(model_counts.items(), key=lambda x: -x[1])[:10]
        payload["top_threat_types"] = sorted(threat_counts.items(), key=lambda x: -x[1])[:10]
        payload["owner_email"] = getattr(key.owner, "email", "")

        collection_counts: dict = defaultdict(int)
        mcp_tool_counts: dict = defaultdict(int)
        for ev in events:
            meta = ev.get("metadata") or {}
            coll = str(meta.get("collection") or meta.get("vector_collection") or "").strip()
            if coll:
                collection_counts[coll] += 1
            tools = meta.get("tools_invoked") or []
            if isinstance(tools, str):
                tools = [tools]
            for tool in tools:
                if tool:
                    mcp_tool_counts[str(tool)] += 1
        payload["top_collections"] = sorted(collection_counts.items(), key=lambda x: -x[1])[:10]
        payload["top_mcp_tools"] = sorted(mcp_tool_counts.items(), key=lambda x: -x[1])[:10]
        return Response(payload)


class ModelExposureView(APIView):
    """GET /api/module2/models/exposure/ — model health, exposure scores, and vulnerability chart data."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "30d")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        org = _org_or_403(request)
        events = list(
            _enforcement_events_for_request(
                request, EnforcementEvent.objects.filter(created_at__gte=since)
            ).values("metadata", "action")
        )

        llm_map = {}
        if org:
            for cfg in LLMModelConfig.objects.filter(organization=org):
                llm_map[cfg.model_name] = cfg.provider

        return Response(build_model_exposure_payload(events, llm_map, period))


class ThreatIntelTelemetryView(APIView):
    """GET /api/module2/threat-intel/telemetry/ — time-series threat telemetry and attack vectors."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "7d")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        events_qs = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        events = list(events_qs.values("created_at", "action", "metadata"))
        payload = build_threat_telemetry_payload(events, period, since)
        payload["stage_hit_distribution"] = build_stage_hit_distribution(events_qs)
        return Response(payload)


class UnifiedDashboardView(APIView):
    """GET /api/module2/dashboard/ — gateway intelligence command center."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "24h")
        hours = _hours_from_period(period)
        since = timezone.now() - timedelta(hours=hours)
        org = _org_or_403(request)

        events = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        total = events.count()
        blocked = events.filter(action=ACTION_BLOCK).count()
        redacted = events.filter(action=ACTION_REDACT).count()

        keys_qs = GatewayAPIKey.objects.all()
        if org:
            keys_qs = keys_qs.filter(organization=org)
        elif not request.user.is_superuser:
            keys_qs = keys_qs.none()

        key_by_prefix, metrics = _collect_key_metrics(keys_qs, events)
        risky_rows = [_risk_payload(p, key_by_prefix[p], m) for p, m in metrics.items()]
        risky_rows.sort(key=lambda r: (-r["risk_score"], -r["request_count"]))

        bucket_hours = 1 if hours <= 24 else 6
        trend = []
        for i in range(max(hours // bucket_hours, 1)):
            start = since + timedelta(hours=i * bucket_hours)
            end = start + timedelta(hours=bucket_hours)
            bqs = events.filter(created_at__gte=start, created_at__lt=end)
            trend.append(
                {
                    "timestamp": start.isoformat(),
                    "total": bqs.count(),
                    "blocked": bqs.filter(action=ACTION_BLOCK).count(),
                    "redacted": bqs.filter(action=ACTION_REDACT).count(),
                }
            )

        incidents = SecurityIncident.objects.all()
        if org:
            incidents = incidents.filter(organization=org)
        elif not request.user.is_superuser:
            incidents = incidents.none()
        open_incidents = incidents.filter(status__in=["open", "investigating", "escalated"])

        model_data = ModelExposureView().get(request).data.get("models", [])[:8]
        incidents_snapshot = [
            {
                "id": i.id,
                "title": i.title,
                "severity": i.severity,
                "status": i.status,
                "created_at": i.created_at.isoformat(),
                "source": event_source(i.enforcement_event.metadata or {} if i.enforcement_event_id and i.enforcement_event else {}),
            }
            for i in open_incidents.select_related("enforcement_event").order_by("-created_at")[:10]
        ]
        return Response(
            {
                "period": period,
                "kpis": {
                    "total_events": total,
                    "blocked": blocked,
                    "redacted": redacted,
                    "open_incidents": open_incidents.count(),
                    "risky_keys": sum(1 for r in risky_rows if r["risk_band"] == "high"),
                    "block_rate": round((blocked / total) * 100, 1) if total else 0.0,
                },
                "threat_trend": trend,
                "top_risky_keys": risky_rows[:8],
                "key_risk_distribution": dict(Counter([r["risk_band"] for r in risky_rows])),
                "model_exposure": model_data,
                "incidents_snapshot": incidents_snapshot,
                "lane_summary": build_lane_summary(events),
                "rag_funnel": build_rag_pipeline_kpis(events),
                "mcp_summary": build_mcp_activity_payload(events),
            }
        )


class OrgScopedViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated]
    org_scoped_model = None

    def get_queryset(self):
        model = self.org_scoped_model or self.queryset.model
        org = _org_or_403(self.request)
        if org is None:
            if self.request.user.is_superuser:
                return model.objects.all()
            return model.objects.none()
        return model.objects.filter(organization=org)

    def perform_create(self, serializer):
        org = _org_or_403(self.request)
        if org is None:
            from rest_framework.exceptions import ValidationError

            raise ValidationError("Organization required.")
        serializer.save(organization=org)


class ThreatIntelViewSet(OrgScopedViewSet):
    org_scoped_model = ThreatIntelEntry
    queryset = ThreatIntelEntry.objects.all()
    serializer_class = ThreatIntelEntrySerializer


class ThreatIntelSyncView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]

    def post(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_400_BAD_REQUEST)
        now = timezone.now()
        entry_count = ThreatIntelEntry.objects.filter(organization=org).count()
        sync_threat_intel_to_redis.delay(org.id)
        return Response(
            {
                "status": "sync_queued",
                "organization_id": org.id,
                "org_slug": org.slug or str(org.id),
                "entry_count": entry_count,
                "redis_key": f"firewall:threat_intel:{org.slug or org.id}",
                "last_sync_at": now.isoformat(),
            }
        )


class IncidentListView(APIView):
    """GET /api/module2/incidents/ — paginated, filterable security incident queue."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        qs = SecurityIncident.objects.select_related("enforcement_event").order_by("-created_at")
        if org:
            qs = qs.filter(organization=org)
        elif not request.user.is_superuser:
            qs = qs.none()

        status_filter = request.query_params.get("status", "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)

        severity_filter = request.query_params.get("severity", "").strip()
        if severity_filter:
            qs = qs.filter(severity=severity_filter)

        # NULL-safety: ``NOT (metadata->'key' = ...)`` evaluates to NULL (and
        # drops the row) in Postgres when the JSON key is missing, so every
        # exclusion on a JSON value must be guarded with has_key.
        _lane_event_type_q = Q(enforcement_event__metadata__has_key="event_type") & (
            Q(enforcement_event__metadata__event_type="rag_pipeline")
            | Q(enforcement_event__metadata__event_type="mcp_tool_call")
        )
        _mcp_metadata_fallback_q = (
            Q(enforcement_event__metadata__has_key="tools_invoked")
            | Q(enforcement_event__metadata__has_key="mcp_server")
            | Q(enforcement_event__metadata__has_key="server_slug")
            | Q(enforcement_event__metadata__has_key="mcp_direction")
            | Q(enforcement_event__metadata__has_key="scan_direction")
        )
        _threat_intel_q = (
            (
                Q(enforcement_event__metadata__has_key="source")
                & Q(enforcement_event__metadata__source__icontains="threat_intel")
            )
            | (
                Q(enforcement_event__metadata__has_key="detail")
                & Q(enforcement_event__metadata__detail__icontains="threat intel")
            )
        )

        source_filter = request.query_params.get("source", "").strip()
        if source_filter == "threat_intel":
            qs = qs.filter(
                _threat_intel_q
                | Q(enforcement_event__metadata__threat_type__istartswith="threat_intel")
            )
        elif source_filter == "ueba":
            qs = qs.filter(
                Q(enforcement_event__metadata__has_key="key_prefix")
                | Q(enforcement_event__metadata__has_key="api_key_prefix")
            ).exclude(_threat_intel_q)
        elif source_filter == "rag":
            qs = qs.filter(enforcement_event__metadata__event_type="rag_pipeline")
        elif source_filter == "mcp":
            qs = qs.filter(
                Q(enforcement_event__metadata__event_type="mcp_tool_call")
                | _mcp_metadata_fallback_q
            )
        elif source_filter == "vector":
            qs = qs.filter(
                Q(enforcement_event__metadata__has_key="collection")
                | Q(enforcement_event__metadata__has_key="vector_collection")
                | Q(enforcement_event__metadata__has_key="vector_namespace")
            ).exclude(_lane_event_type_q)
        elif source_filter == "chat":
            qs = qs.exclude(
                _lane_event_type_q
                | _mcp_metadata_fallback_q
                | Q(enforcement_event__metadata__has_key="collection")
                | Q(enforcement_event__metadata__has_key="vector_collection")
                | Q(enforcement_event__metadata__has_key="vector_namespace")
            )
        elif source_filter == "generic":
            qs = qs.filter(
                ~Q(enforcement_event__metadata__has_key="key_prefix"),
                ~Q(enforcement_event__metadata__has_key="api_key_prefix"),
            ).exclude(_threat_intel_q)

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(notes__icontains=search))

        try:
            page = int(request.query_params.get("page", 1))
        except ValueError:
            page = 1
        try:
            page_size = int(request.query_params.get("page_size", 25))
        except ValueError:
            page_size = 25

        total, page_qs, page, page_size = paginate_queryset(qs, page, page_size)
        out = [
            serialize_incident_row(incident, SecurityIncidentSerializer(incident).data)
            for incident in page_qs
        ]
        total_pages = (total + page_size - 1) // page_size if page_size else 1

        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "results": out,
            }
        )


class IncidentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        org = _org_or_403(request)
        try:
            incident = SecurityIncident.objects.get(pk=pk, organization=org) if org else SecurityIncident.objects.get(pk=pk)
        except SecurityIncident.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        events = []
        if incident.enforcement_event_id:
            events.append(incident.enforcement_event)
        related = EnforcementEvent.objects.filter(
            organization=incident.organization,
            metadata__incident_id=incident.id,
        ).order_by("-created_at")[:50]
        events.extend(related)

        timeline = []
        source = "generic"
        evidence = {"key_prefix": "", "model": "", "project_id": "", "threat_type": ""}
        for ev in events:
            meta = ev.metadata or {}
            source = source if source != "generic" else _event_source(meta)
            evidence["key_prefix"] = evidence["key_prefix"] or _key_prefix_from_meta(meta)
            evidence["model"] = evidence["model"] or str(meta.get("model") or "")
            evidence["project_id"] = evidence["project_id"] or str(meta.get("project_id") or "")
            evidence["threat_type"] = evidence["threat_type"] or str(meta.get("threat_type") or "")
            timeline.append(
                {
                    "id": ev.id,
                    "action": ev.action,
                    "created_at": ev.created_at.isoformat(),
                    "metadata": meta,
                    "rule_id": ev.rule_id,
                    "policy_id": ev.policy_id,
                    "source": _event_source(meta),
                    "key_prefix": _key_prefix_from_meta(meta),
                    "model": meta.get("model") or "",
                }
            )

        return Response(
            {
                "incident": SecurityIncidentSerializer(incident).data,
                "source": source,
                "evidence": evidence,
                "timeline": timeline,
            }
        )


class RagHealthView(APIView):
    """GET /api/module2/rag/health/ — RAG pipeline KPIs and vector exposure for M2.3 Tab B."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "24h")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        events_qs = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        return Response(
            {
                "period": period,
                "rag_pipeline_kpis": build_rag_pipeline_kpis(events_qs),
                "vector_exposure": build_vector_exposure_payload(events_qs),
            }
        )


class McpRiskView(APIView):
    """GET /api/module2/mcp/risk/ — MCP tool call activity, ledger, direction split, and top servers."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "24h")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        events_qs = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        payload = build_mcp_activity_payload(events_qs)
        payload["period"] = period
        return Response(payload)
