"""Module 2 API views — UEBA + Threat Intelligence focus."""

from collections import Counter, defaultdict
from datetime import timedelta
import logging
from uuid import UUID

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from auth.utils import get_request_organization
from core.admin_views import IsAdminOrSuperuser
from core.models import FirewallConfig, GatewayAPIKey, KillSwitch, LLMModelConfig
from module2.analytics import (
    build_incident_queue_summary,
    build_lane_summary,
    build_mcp_activity_payload,
    build_model_exposure_payload,
    build_rag_pipeline_kpis,
    build_recent_request_json,
    build_stage_hit_distribution,
    build_threat_telemetry_payload,
    build_vector_exposure_payload,
    count_monitored_events,
    count_rerouted_events,
    event_source,
    hours_from_period,
    key_prefix_from_meta,
    paginate_queryset,
    prefixes_match,
    prompt_snippet_from_meta,
    serialize_incident_row,
)
from module2.models import ThreatIntelEntry
from module2.serializers import ThreatIntelEntrySerializer
from module2.tasks import sync_threat_intel_to_redis
from policy.constants import ACTION_BLOCK, ACTION_REDACT
from policy.models import EnforcementEvent, SecurityIncident
from policy.request_scoped_metrics import collapse_events_by_request, iter_rows_from_queryset, summarize_request_scoped_events
from policy.review_views import SecurityIncidentSerializer
from policy.security_views import _enforcement_events_for_request

logger = logging.getLogger(__name__)


def _org_or_403(request):
    org = get_request_organization(request)
    if org is None and not getattr(request.user, "is_superuser", False):
        return None
    return org


class _ReadOnlyOrAdminPermission(BasePermission):
    """Allow authenticated reads; restrict writes to admin/superuser."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return IsAdminOrSuperuser().has_permission(request, view)


_hours_from_period = hours_from_period
_key_prefix_from_meta = key_prefix_from_meta
_event_source = event_source


_TIMELINE_META_ALLOWLIST = {
    "event_type",
    "pipeline_stage",
    "source",
    "detail",
    "threat_type",
    "request_id",
    "pipeline_request_id",
    "model",
    "project_id",
    "key_prefix",
    "api_key_prefix",
    "collection",
    "vector_collection",
    "vector_namespace",
    "tools_invoked",
    "mcp_server",
    "server_slug",
    "mcp_direction",
    "scan_direction",
    "rerouted",
    "original_model",
    "selected_model",
    "prompt_snippet",
    "response_snippet",
    "prompt_lineage",
    "intent",
    "extra",
}

_TIMELINE_EXTRA_ALLOWLIST = {
    "detail",
    "source",
    "prompt",
    "prompt_snippet",
    "user_message",
    "query",
    "rerouted",
    "original_model",
    "selected_model",
}

_VALID_INCIDENT_STATUSES = {"open", "investigating", "escalated", "resolved"}
_VALID_INCIDENT_SEVERITIES = {"low", "medium", "high", "critical", "critical_high"}
_VALID_INCIDENT_SOURCES = {"threat_intel", "rag", "mcp", "vector", "chat", "generic"}
_VALID_INCIDENT_QUEUES = {"active"}


def _sanitize_incident_metadata(meta):
    src = meta if isinstance(meta, dict) else {}
    out = {}
    for key in _TIMELINE_META_ALLOWLIST:
        if key not in src:
            continue
        value = src.get(key)
        if key == "extra":
            extra = value if isinstance(value, dict) else {}
            out["extra"] = {k: extra[k] for k in _TIMELINE_EXTRA_ALLOWLIST if k in extra}
            continue
        out[key] = value
    return out


def _build_event_trend(events_qs, since, hours, bucket_hours):
    bucket_count = max(hours // bucket_hours, 1)
    timeline = []
    for i in range(bucket_count):
        start = since + timedelta(hours=i * bucket_hours)
        timeline.append(
            {
                "timestamp": start.isoformat(),
                "total": 0,
                "blocked": 0,
                "redacted": 0,
            }
        )

    window_end = since + timedelta(hours=bucket_count * bucket_hours)
    rows = list(
        events_qs.filter(created_at__gte=since, created_at__lt=window_end).values(
            "created_at", "action", "metadata"
        )
    )
    bucket_seconds = bucket_hours * 3600
    for item in collapse_events_by_request(rows):
        ts = item.created_at
        if not ts:
            continue
        idx = int((ts - since).total_seconds() // bucket_seconds)
        if idx < 0 or idx >= bucket_count:
            continue
        target = timeline[idx]
        target["total"] += 1
        if item.action == "block":
            target["blocked"] += 1
        if item.action == "redact":
            target["redacted"] += 1
    return timeline


def _collect_key_metrics(keys_qs, events_qs):
    keys = list(keys_qs)
    key_by_prefix = {k.prefix: k for k in keys}
    prefix_lookup = {k.prefix.lower(): k.prefix for k in keys}
    metrics = defaultdict(
        lambda: {
            "total": 0,
            "blocked": 0,
            "redacted": 0,
            "endpoint_ids": set(),
            "models": set(),
            "model_counts": defaultdict(int),
            "threat_types": defaultdict(int),
            "hourly": defaultdict(int),
        }
    )

    raw_rows = list(
        events_qs.values("created_at", "action", "endpoint_id", "metadata")
    )
    prepared = []
    for ev in raw_rows:
        meta = dict(ev.get("metadata") or {})
        if ev.get("endpoint_id"):
            meta.setdefault("endpoint_id", ev["endpoint_id"])
        prepared.append(
            {
                "created_at": ev.get("created_at"),
                "action": ev.get("action"),
                "metadata": meta,
            }
        )
    for item in collapse_events_by_request(prepared):
        meta = item.metadata or {}
        prefix = _key_prefix_from_meta(meta)
        if not prefix:
            continue
        canonical = prefix_lookup.get(prefix.lower())
        if not canonical:
            continue
        created_at = item.created_at
        hour_bucket = (
            created_at.replace(minute=0, second=0, microsecond=0).isoformat()
            if created_at is not None
            else ""
        )
        m = metrics[canonical]
        m["total"] += 1
        if item.action == "block":
            m["blocked"] += 1
        if item.action == "redact":
            m["redacted"] += 1
        if meta.get("endpoint_id"):
            m["endpoint_ids"].add(meta["endpoint_id"])
        if meta.get("model"):
            model_name = str(meta["model"])
            m["models"].add(model_name)
            m["model_counts"][model_name] += 1
        threat = str(meta.get("threat_type") or "unknown")
        m["threat_types"][threat] += 1
        if hour_bucket:
            m["hourly"][hour_bucket] += 1

    return key_by_prefix, metrics


def _build_key_containment_payload(org, keys_qs=None):
    """Counts and detail rows for disabled API keys and active kill switches."""
    if keys_qs is None:
        keys_qs = GatewayAPIKey.objects.select_related("owner").all()
        if org:
            keys_qs = keys_qs.filter(organization=org)

    disabled_qs = keys_qs.filter(is_active=False).order_by("-updated_at")
    ks_qs = KillSwitch.objects.filter(is_active=True).order_by("-activated_at", "-updated_at")
    if org:
        ks_qs = ks_qs.filter(organization=org)

    disabled_keys_detail = [
        {
            "key_id": str(k.id),
            "prefix": k.prefix,
            "name": k.name,
            "project_id": k.project_id,
            "owner_email": getattr(k.owner, "email", ""),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "updated_at": k.updated_at.isoformat() if k.updated_at else None,
        }
        for k in disabled_qs[:50]
    ]
    active_kill_switches_detail = [
        {
            "id": ks.id,
            "model_name": ks.model_name,
            "api_key_prefix": ks.api_key_prefix or "",
            "action": ks.action,
            "reason": ks.reason,
            "activated_at": ks.activated_at.isoformat() if ks.activated_at else None,
        }
        for ks in ks_qs[:50]
    ]

    return {
        "disabled_keys": disabled_qs.count(),
        "active_kill_switches": ks_qs.count(),
        "disabled_keys_detail": disabled_keys_detail,
        "active_kill_switches_detail": active_kill_switches_detail,
    }


def _empty_key_metric():
    return {
        "total": 0,
        "blocked": 0,
        "redacted": 0,
        "endpoint_ids": set(),
        "models": set(),
        "model_counts": defaultdict(int),
        "threat_types": defaultdict(int),
        "hourly": defaultdict(int),
    }


def _kill_switches_by_prefix(org):
    ks_qs = KillSwitch.objects.filter(is_active=True).order_by("-activated_at")
    if org:
        ks_qs = ks_qs.filter(organization=org)
    grouped = defaultdict(list)
    for ks in ks_qs:
        prefix = str(ks.api_key_prefix or "").strip()
        if prefix:
            grouped[prefix].append(
                {
                    "id": ks.id,
                    "model_name": ks.model_name,
                    "action": ks.action,
                    "is_active": ks.is_active,
                    "reason": ks.reason,
                    "activated_at": ks.activated_at.isoformat() if ks.activated_at else None,
                }
            )
    return grouped


def _build_fleet_registry_payload(keys_qs, key_by_prefix, metrics, kill_by_prefix):
    """Merge gateway key registry rows with UEBA behavior metrics and kill-switch scope."""
    results = []
    for k in keys_qs[:200]:
        prefix = k.prefix
        metric = metrics.get(prefix) or _empty_key_metric()
        risk = _risk_payload(prefix, k, metric)
        active_ks = kill_by_prefix.get(prefix, [])
        top_threats = sorted(metric["threat_types"].items(), key=lambda x: -x[1])[:3]
        top_models = sorted(metric["model_counts"].items(), key=lambda x: -x[1])[:3]

        results.append(
            {
                "key_id": str(k.id),
                "prefix": prefix,
                "name": k.name,
                "project_id": k.project_id,
                "owner_email": getattr(k.owner, "email", ""),
                "is_active": k.is_active,
                "rate_limit_tpm": k.rate_limit_tokens_per_minute,
                "risk_score_baseline": round(float(k.risk_score or 0), 3),
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "risk_band": risk["risk_band"],
                "risk_score": risk["risk_score"],
                "velocity_spike": risk["velocity_spike"],
                "anomaly_flags": risk["anomaly_flags"],
                "request_count": risk["request_count"],
                "blocked_count": risk["blocked_count"],
                "redacted_count": risk["redacted_count"],
                "block_rate_pct": risk["block_rate_pct"],
                "redact_rate_pct": risk["redact_rate_pct"],
                "unique_models": risk["unique_models"],
                "top_threat_type": risk["top_threat_type"],
                "top_threat_types": top_threats,
                "top_models": top_models,
                "active_kill_switches": active_ks,
                "active_kill_switch_count": len(active_ks),
            }
        )

    results.sort(
        key=lambda r: (
            -int(r["is_active"]),
            -r["risk_score"],
            -r["request_count"],
            r["prefix"],
        )
    )
    return results


def _risk_payload(prefix: str, key_obj, metric: dict):
    total = metric["total"]
    block_rate = (metric["blocked"] / total) if total else 0.0
    redact_rate = (metric["redacted"] / total) if total else 0.0
    hourly_values = list(metric["hourly"].values()) or [0]
    baseline = sum(hourly_values) / len(hourly_values)
    current = hourly_values[-1] if hourly_values else 0
    if baseline:
        velocity_spike = current / baseline
    elif current:
        velocity_spike = float(current)
    else:
        velocity_spike = 1.0
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
        total_events = sum(m["total"] for m in metrics.values())
        blocked_events = sum(m["blocked"] for m in metrics.values())

        containment = _build_key_containment_payload(org, keys_qs)

        return Response(
            {
                "period": period,
                "summary": {
                    "total_keys": keys_qs.count(),
                    "active_keys": keys_qs.filter(is_active=True).count(),
                    "keys_with_activity": len(rows),
                    "high_risk_keys": sum(1 for r in rows if r["risk_band"] == "high"),
                    "total_events": total_events,
                    "blocked_events": blocked_events,
                    "disabled_keys": containment["disabled_keys"],
                    "active_kill_switches": containment["active_kill_switches"],
                },
                "containment": containment,
                "top_risky_keys": rows[:10],
            }
        )


class UebaApiKeyRegistryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "24h")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        org = _org_or_403(request)
        qs = GatewayAPIKey.objects.select_related("owner").order_by("-created_at")
        if org:
            qs = qs.filter(organization=org)
        elif not request.user.is_superuser:
            qs = qs.none()

        events = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        key_by_prefix, metrics = _collect_key_metrics(qs, events)
        kill_by_prefix = _kill_switches_by_prefix(org)
        results = _build_fleet_registry_payload(qs, key_by_prefix, metrics, kill_by_prefix)

        return Response(
            {
                "period": period,
                "count": qs.count(),
                "results": results,
            }
        )


class UebaApiKeyTimelineView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        started_at = timezone.now()
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
        base_timeline = _build_event_trend(events, since, hours, bucket_size)
        timeline = []
        for row in base_timeline:
            timeline.append(
                {
                    "timestamp": row["timestamp"],
                    "total_events": row["total"],
                    "blocked": row["blocked"],
                    "redacted": row["redacted"],
                    "keys": {prefix: 0 for prefix in tracked_prefixes},
                }
            )
        if tracked_prefixes:
            bucket_seconds = bucket_size * 3600
            bucket_count = len(timeline)
            window_end = since + timedelta(hours=bucket_count * bucket_size)
            timeline_rows = list(
                events.filter(created_at__gte=since, created_at__lt=window_end).values(
                    "created_at", "action", "metadata"
                )
            )
            for item in collapse_events_by_request(timeline_rows):
                ts = item.created_at
                if not ts:
                    continue
                idx = int((ts - since).total_seconds() // bucket_seconds)
                if idx < 0 or idx >= bucket_count:
                    continue
                prefix = _key_prefix_from_meta(item.metadata or {})
                canonical = prefix.lower()
                matched = next((p for p in tracked_prefixes if p.lower() == canonical), None)
                if matched:
                    timeline[idx]["keys"][matched] += 1

        elapsed_ms = int((timezone.now() - started_at).total_seconds() * 1000)
        logger.info(
            "module2_ueba_timeline_ready org=%s period=%s buckets=%s tracked=%s elapsed_ms=%s",
            getattr(org, "id", None),
            period,
            len(timeline),
            len(tracked_prefixes),
            elapsed_ms,
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

        period = request.query_params.get("period", "24h")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        events_qs = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        from module2.telemetry_health import normalize_enforcement_metadata

        prepared = []
        for raw in events_qs.values("id", "created_at", "action", "endpoint_id", "metadata"):
            meta, _ = normalize_enforcement_metadata(raw.get("metadata") or {})
            if raw.get("endpoint_id"):
                meta.setdefault("endpoint_id", raw["endpoint_id"])
            prepared.append({**raw, "metadata": meta})

        events = []
        for item in collapse_events_by_request(prepared):
            meta = item.metadata or {}
            if not prefixes_match(key.prefix, key_prefix_from_meta(meta)):
                continue
            events.append(
                {
                    "id": meta.get("event_id") or meta.get("request_id"),
                    "created_at": item.created_at,
                    "action": item.action,
                    "endpoint_id": meta.get("endpoint_id"),
                    "metadata": meta,
                }
            )

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
            "blocked": sum(1 for e in events if e["action"] in (ACTION_BLOCK, "block")),
            "redacted": sum(1 for e in events if e["action"] in (ACTION_REDACT, "redact")),
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
        recent = sorted(
            events,
            key=lambda e: (e["created_at"], e.get("id") or 0),
            reverse=True,
        )[:5]
        payload["recent_requests"] = [build_recent_request_json(ev) for ev in recent]
        payload["recent_requests_json"] = payload["recent_requests"]
        return Response(payload)


class ModelExposureView(APIView):
    """GET /api/module2/models/exposure/ — model health, exposure scores, and vulnerability chart data."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "24h")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        org = _org_or_403(request)
        events = list(
            _enforcement_events_for_request(
                request, EnforcementEvent.objects.filter(created_at__gte=since)
            ).values("metadata", "action")
        )

        llm_map = {}
        model_aliases = {}
        active_model_count = 0
        if org:
            # M2.3 should mirror Module 1 "connected user models", not internal
            # guard/runtime entries. Keep only active non-internal models.
            active_cfgs = LLMModelConfig.objects.filter(
                organization=org,
                is_active=True,
            ).exclude(provider="internal")
            active_model_count = active_cfgs.count()
            for cfg in active_cfgs:
                llm_map[cfg.model_name] = cfg.provider
                model_aliases[str(cfg.model_name).strip().lower()] = cfg.model_name
                if cfg.model_id:
                    model_aliases[str(cfg.model_id).strip().lower()] = cfg.model_name

        normalized_events = []
        if model_aliases:
            for ev in events:
                meta = dict(ev.get("metadata") or {})
                raw_model = str(meta.get("model") or "").strip().lower()
                canonical_model = model_aliases.get(raw_model)
                if not canonical_model:
                    continue
                meta["model"] = canonical_model
                normalized_events.append({"metadata": meta, "action": ev.get("action")})
        else:
            normalized_events = events

        payload = build_model_exposure_payload(normalized_events, llm_map, period)
        if org:
            payload.setdefault("summary", {})
            payload["summary"]["active_models"] = active_model_count
        return Response(payload)


class ThreatIntelTelemetryView(APIView):
    """GET /api/module2/threat-intel/telemetry/ — time-series threat telemetry and attack vectors."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "7d")
        since = timezone.now() - timedelta(hours=_hours_from_period(period))
        org = _org_or_403(request)
        events_qs = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        events = list(events_qs.values("created_at", "action", "metadata"))
        payload = build_threat_telemetry_payload(events, period, since)
        payload["stage_hit_distribution"] = build_stage_hit_distribution(events_qs)
        now = timezone.now()
        if org:
            ioc_qs = ThreatIntelEntry.objects.filter(organization=org)
            payload["ioc_library"] = {
                "total": ioc_qs.count(),
                "auto_block_enabled": ioc_qs.filter(auto_block=True).count(),
                "expired": ioc_qs.filter(expires_at__lt=now).count(),
            }
        else:
            payload["ioc_library"] = {"total": 0, "auto_block_enabled": 0, "expired": 0}
        return Response(payload)


class UnifiedDashboardView(APIView):
    """GET /api/module2/dashboard/ — gateway intelligence command center."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        started_at = timezone.now()
        period = request.query_params.get("period", "24h")
        hours = _hours_from_period(period)
        since = timezone.now() - timedelta(hours=hours)
        org = _org_or_403(request)

        events = _enforcement_events_for_request(
            request, EnforcementEvent.objects.filter(created_at__gte=since)
        )
        req_rows = list(events.values("action", "metadata", "created_at"))
        req_summary = summarize_request_scoped_events(req_rows)
        total = req_summary["requests_inspected"]
        blocked = req_summary["requests_blocked"]
        redacted = req_summary["requests_redacted"]
        monitored = count_monitored_events(events)
        rerouted = count_rerouted_events(events)

        keys_qs = GatewayAPIKey.objects.all()
        if org:
            keys_qs = keys_qs.filter(organization=org)
        elif not request.user.is_superuser:
            keys_qs = keys_qs.none()

        key_by_prefix, metrics = _collect_key_metrics(keys_qs, events)
        risky_rows = [_risk_payload(p, key_by_prefix[p], m) for p, m in metrics.items()]
        risky_rows.sort(key=lambda r: (-r["risk_score"], -r["request_count"]))
        fleet_risk_rows = [
            _risk_payload(k.prefix, k, metrics.get(k.prefix) or _empty_key_metric())
            for k in keys_qs
        ]

        bucket_hours = 1 if hours <= 24 else 6
        trend = _build_event_trend(events, since, hours, bucket_hours)

        incidents = SecurityIncident.objects.all()
        if org:
            incidents = incidents.filter(organization=org)
        elif not request.user.is_superuser:
            incidents = incidents.none()
        open_incidents = incidents.filter(status__in=["open", "investigating", "escalated"])

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
        containment = _build_key_containment_payload(org, keys_qs)
        telemetry_enabled = True
        if org:
            cfg = FirewallConfig.objects.filter(organization=org).order_by("-updated_at").first()
            if cfg is not None:
                telemetry_enabled = bool(cfg.audit_logging_enabled)

        response = Response(
            {
                "period": period,
                "data_health": {
                    "telemetry_enabled": telemetry_enabled,
                },
                "kpis": {
                    "total_events": total,
                    "requests_inspected": total,
                    "requests_blocked": blocked,
                    "requests_redacted": redacted,
                    "requests_allowed": req_summary["requests_allowed"],
                    "blocked": blocked,
                    "redacted": redacted,
                    "monitored": monitored,
                    "rerouted": rerouted,
                    "open_incidents": open_incidents.count(),
                    "risky_keys": sum(1 for r in fleet_risk_rows if r["risk_band"] == "high"),
                    "block_rate": round((blocked / total) * 100, 1) if total else 0.0,
                    "disabled_keys": containment["disabled_keys"],
                    "active_kill_switches": containment["active_kill_switches"],
                },
                "containment": containment,
                "threat_trend": trend,
                "top_risky_keys": risky_rows[:8],
                "key_risk_distribution": dict(Counter([r["risk_band"] for r in fleet_risk_rows])),
                "incidents_snapshot": incidents_snapshot,
                "lane_summary": build_lane_summary(events),
            }
        )
        elapsed_ms = int((timezone.now() - started_at).total_seconds() * 1000)
        logger.info(
            "module2_dashboard_ready org=%s period=%s total=%s incidents_open=%s elapsed_ms=%s",
            getattr(org, "id", None),
            period,
            total,
            open_incidents.count(),
            elapsed_ms,
        )
        return response


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
    permission_classes = [IsAuthenticated, _ReadOnlyOrAdminPermission]


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
        started_at = timezone.now()
        org = _org_or_403(request)
        qs = SecurityIncident.objects.select_related("enforcement_event").order_by("-created_at")
        if org:
            qs = qs.filter(organization=org)
        elif not request.user.is_superuser:
            qs = qs.none()

        summary = build_incident_queue_summary(qs, org_id=org.id if org else None)

        status_filter = request.query_params.get("status", "").strip()
        queue_filter = request.query_params.get("queue", "").strip()
        if status_filter and status_filter not in _VALID_INCIDENT_STATUSES:
            return Response(
                {"detail": "Invalid status filter."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if queue_filter and queue_filter not in _VALID_INCIDENT_QUEUES:
            return Response(
                {"detail": "Invalid queue filter."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if status_filter:
            qs = qs.filter(status=status_filter)
        elif queue_filter == "active":
            qs = qs.filter(status__in=("open", "investigating", "escalated"))

        severity_filter = request.query_params.get("severity", "").strip()
        if severity_filter and severity_filter not in _VALID_INCIDENT_SEVERITIES:
            return Response(
                {"detail": "Invalid severity filter."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if severity_filter:
            if severity_filter == "critical_high":
                qs = qs.filter(severity__in=("critical", "high"))
            else:
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
            | (
                Q(enforcement_event__metadata__has_key="extra")
                & Q(enforcement_event__metadata__extra__has_key="detail")
                & Q(enforcement_event__metadata__extra__detail__icontains="threat intel")
            )
            | Q(enforcement_event__metadata__threat_type__istartswith="threat_intel")
        )

        source_filter = request.query_params.get("source", "").strip()
        if source_filter and source_filter not in _VALID_INCIDENT_SOURCES:
            return Response(
                {"detail": "Invalid source filter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if source_filter == "threat_intel":
            qs = qs.filter(_threat_intel_q)
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
                | _threat_intel_q
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

        response = Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "summary": summary,
                "results": out,
            }
        )
        elapsed_ms = int((timezone.now() - started_at).total_seconds() * 1000)
        logger.info(
            "module2_incident_list_ready org=%s status=%s queue=%s severity=%s source=%s search=%s count=%s elapsed_ms=%s",
            getattr(org, "id", None),
            status_filter or "-",
            queue_filter or "-",
            severity_filter or "-",
            source_filter or "-",
            "yes" if search else "no",
            total,
            elapsed_ms,
        )
        return response


class IncidentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        org = _org_or_403(request)
        if org is None and not request.user.is_superuser:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            incident = (
                SecurityIncident.objects.get(pk=pk, organization=org)
                if org
                else SecurityIncident.objects.get(pk=pk)
            )
        except SecurityIncident.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        events = []
        seen_ids = set()
        candidates = []
        if incident.enforcement_event_id:
            candidates.append(incident.enforcement_event)
        related = EnforcementEvent.objects.filter(
            organization=incident.organization,
            metadata__incident_id=incident.id,
        ).order_by("-created_at")[:50]
        candidates.extend(related)
        for ev in candidates:
            if ev and ev.id not in seen_ids:
                seen_ids.add(ev.id)
                events.append(ev)
        events.sort(key=lambda e: e.created_at, reverse=True)

        timeline = []
        source = "generic"
        evidence = {"key_prefix": "", "model": "", "project_id": "", "threat_type": ""}
        for ev in events:
            meta = _sanitize_incident_metadata(ev.metadata or {})
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
