"""Phase 0b C-1′: SQL aggregations for SOC / module / vector analytics (no row haul)."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import (
    Avg,
    Case,
    CharField,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    Max,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Cast, Coalesce, Concat, Extract, Floor
from django.utils import timezone

from policy.analytics_period import REQUEST_ID_SQL
from policy.constants import ACTION_BLOCK, ACTION_REDACT

CRITICAL_THRESHOLD = 80
_NUMERIC_RE = r"^-?[0-9]+(\.[0-9]+)?$"


def metadata_text(key: str):
    return KeyTextTransform(key, "metadata")


def metadata_json(key: str):
    return KeyTransform(key, "metadata")


def annotate_numeric_float(qs, key: str, alias: str):
    """Cast JSON text to float; non-numeric / missing → 0.0 (matches prior Python)."""
    raw = f"{alias}_raw" if alias.startswith("_") else f"_{alias}_raw"
    return qs.annotate(**{raw: KeyTextTransform(key, "metadata")}).annotate(
        **{
            alias: Case(
                When(**{f"{raw}__regex": _NUMERIC_RE}, then=Cast(F(raw), FloatField())),
                default=Value(0.0),
                output_field=FloatField(),
            )
        }
    )


def bucket_index_expr(since, bucket_minutes: int):
    """since-aligned bucket index: int((created_at - since) / bucket)."""
    secs = float(max(int(bucket_minutes), 1) * 60)
    return Cast(
        Floor(
            ExpressionWrapper(
                (Extract("created_at", "epoch") - Value(since.timestamp())) / Value(secs),
                output_field=FloatField(),
            )
        ),
        IntegerField(),
    )


def trend_grid(period: str):
    """Return (now, since, bucket_minutes, ordered iso keys) matching prior Python grid."""
    from policy.analytics_period import period_bucket_minutes, period_hours

    total_hours = period_hours(period)
    bucket_minutes = period_bucket_minutes(period)
    now = timezone.now().replace(second=0, microsecond=0)
    aligned_minute = (now.minute // bucket_minutes) * bucket_minutes
    now = now.replace(minute=aligned_minute)
    since = now - timedelta(hours=total_hours)
    keys = []
    cursor = since
    while cursor <= now:
        keys.append(cursor.isoformat())
        cursor += timedelta(minutes=bucket_minutes)
    return now, since, bucket_minutes, keys


def annotate_escalation_level(qs, alias="_esc"):
    """Prefer metadata.extra.escalation_level, else top-level; default '0'."""
    qs = qs.annotate(
        _esc_extra=KeyTextTransform(
            "escalation_level", KeyTransform("extra", "metadata")
        ),
        _esc_top=KeyTextTransform("escalation_level", "metadata"),
    )
    return qs.annotate(
        **{
            alias: Coalesce(
                F("_esc_extra"),
                F("_esc_top"),
                Value("0"),
                output_field=CharField(),
            )
        }
    )


def annotate_request_key(qs):
    """C-9: letter-starting request_id (len≥8); else per-pk standalone key."""
    qs = qs.annotate(_rid=KeyTextTransform("request_id", "metadata"))
    return qs.annotate(
        _req=Case(
            When(_rid__regex=REQUEST_ID_SQL, then=F("_rid")),
            default=Concat(Value("__row_"), Cast(F("id"), CharField())),
            output_field=CharField(),
        )
    )


def soc_kpis_from_events(events):
    """SQL Count/CASE/GROUP BY for SocKpisView. Peak memory = aggregates, not rows."""
    qs = annotate_numeric_float(events, "security_risk_score", "_risk")
    qs = annotate_numeric_float(qs, "latency_ms", "_lat")
    qs = annotate_request_key(qs)
    qs = qs.annotate(
        _rank=Case(
            When(action=ACTION_BLOCK, then=Value(3)),
            When(action=ACTION_REDACT, then=Value(2)),
            default=Value(1),
            output_field=IntegerField(),
        )
    )

    row_agg = qs.aggregate(
        total=Count("id"),
        blocked=Count("id", filter=Q(action=ACTION_BLOCK)),
        redacted=Count("id", filter=Q(action=ACTION_REDACT)),
        critical_count=Count("id", filter=Q(_risk__gte=CRITICAL_THRESHOLD)),
        latency_sum=Coalesce(Sum("_lat"), Value(0.0)),
        lat_0_50=Count("id", filter=Q(_lat__lte=50)),
        lat_50_100=Count("id", filter=Q(_lat__gt=50, _lat__lte=100)),
        lat_100_250=Count("id", filter=Q(_lat__gt=100, _lat__lte=250)),
        lat_250_500=Count("id", filter=Q(_lat__gt=250, _lat__lte=500)),
        lat_500_1000=Count("id", filter=Q(_lat__gt=500, _lat__lte=1000)),
        lat_1s=Count("id", filter=Q(_lat__gt=1000)),
    )

    action_breakdown = {
        row["action"]: row["c"]
        for row in qs.values("action").annotate(c=Count("id"))
        if row["action"] is not None
    }

    grouped = qs.values("_req").annotate(
        max_rank=Max("_rank"),
        max_risk=Max("_risk"),
    )
    part = grouped.aggregate(
        requests_inspected=Count("_req"),
        requests_blocked=Count("_req", filter=Q(max_rank=3)),
        requests_redacted=Count("_req", filter=Q(max_rank=2)),
        requests_critical=Count("_req", filter=Q(max_risk__gte=CRITICAL_THRESHOLD)),
    )

    total = int(row_agg["total"] or 0)
    blocked = int(row_agg["blocked"] or 0)
    redacted = int(row_agg["redacted"] or 0)
    requests_inspected = int(part["requests_inspected"] or 0)
    requests_blocked = int(part["requests_blocked"] or 0)
    requests_redacted = int(part["requests_redacted"] or 0)
    requests_allowed = requests_inspected - requests_blocked - requests_redacted
    latency_sum = float(row_agg["latency_sum"] or 0.0)

    resolved = events.filter(incident_status="resolved", resolved_at__isnull=False)
    avg_duration = resolved.annotate(
        duration=ExpressionWrapper(
            F("resolved_at") - F("created_at"),
            output_field=DurationField(),
        )
    ).aggregate(avg=Avg("duration"))["avg"]
    mttr_minutes = round(avg_duration.total_seconds() / 60, 1) if avg_duration else None

    return {
        "total": total,
        "blocked": blocked,
        "redacted": redacted,
        "critical_count": int(row_agg["critical_count"] or 0),
        "action_breakdown": action_breakdown,
        "latency_sum": latency_sum,
        "latency_buckets": {
            "0-50ms": int(row_agg["lat_0_50"] or 0),
            "50-100ms": int(row_agg["lat_50_100"] or 0),
            "100-250ms": int(row_agg["lat_100_250"] or 0),
            "250-500ms": int(row_agg["lat_250_500"] or 0),
            "500ms-1s": int(row_agg["lat_500_1000"] or 0),
            "1s+": int(row_agg["lat_1s"] or 0),
        },
        "requests_inspected": requests_inspected,
        "requests_allowed": requests_allowed,
        "requests_blocked": requests_blocked,
        "requests_redacted": requests_redacted,
        "requests_critical": int(part["requests_critical"] or 0),
        "mttr_minutes": mttr_minutes,
    }
