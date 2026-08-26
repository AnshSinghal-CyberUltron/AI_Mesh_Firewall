"""Phase 0c C-2: refresh hourly facts and serve 24h/7d/30d dashboard reads from them."""

from __future__ import annotations

import os
from datetime import timedelta

from django.db import transaction
from django.db.models import Avg, Case, Count, DurationField, ExpressionWrapper, F, IntegerField, Max, Q, Sum, Value, When
from django.db.models.fields.json import KeyTransform
from django.db.models.functions import TruncHour
from django.utils import timezone

from main_app.analytics_db import ANALYTICS_DB_ALIAS
from policy.analytics_sql import (
    CRITICAL_THRESHOLD,
    annotate_numeric_float,
    annotate_request_key,
    soc_kpis_from_events,
)
from policy.constants import ACTION_BLOCK, ACTION_FLAG, ACTION_REDACT
from policy.firewall_module_classifier import (
    MODULE_IDS,
    increment_bucket,
    specialty_modules_for_event,
)
from policy.models import EnforcementEvent

ROLLUP_PERIODS = frozenset({"24h", "7d", "30d"})
GROUP_META_FIELDS = (
    "source",
    "security_risk_score",
    "event_type",
    "module",
    "module_id",
    "owasp_code",
    "owasp_codes",
    "threat_category",
    "threat_type",
    "is_audit_log",
    "is_isolation_event",
    "trigger_source",
    "pii_detected",
)
MODULE_META_FIELDS = (
    "source",
    "security_risk_score",
    "event_type",
    "module",
    "module_id",
    "owasp_code",
    "threat_type",
    "is_audit_log",
    "is_isolation_event",
    "trigger_source",
)


def serve_rollups() -> bool:
    return os.environ.get("ANALYTICS_SERVE_ROLLUPS", "").lower() in {"1", "true", "yes"}


def _hour_floor(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def _split_rollup_window(since, now):
    """Complete hours live in facts; partial first/current hours stay on raw events.

    SocKpis/ModuleKpis use unaligned ``now - period``. Serving TruncHour facts with
    ``hour >= since`` drops the first partial hour and would miss T-C2 identity.
    """
    first_hour = _hour_floor(since)
    first_full = first_hour if since == first_hour else first_hour + timedelta(hours=1)
    last_hour = _hour_floor(now)
    return first_full, last_hour


def _facts_alias():
    from policy.analytics_rollup_models import (
        AnalyticsHourlyGroupFact,
        AnalyticsHourlyRequestFact,
        AnalyticsHourlyRowFact,
        AnalyticsRollupWatermark,
    )

    return (
        AnalyticsHourlyRowFact.objects.using(ANALYTICS_DB_ALIAS),
        AnalyticsHourlyRequestFact.objects.using(ANALYTICS_DB_ALIAS),
        AnalyticsHourlyGroupFact.objects.using(ANALYTICS_DB_ALIAS),
        AnalyticsRollupWatermark.objects.using(ANALYTICS_DB_ALIAS),
    )


def org_rollup_covers(org_id: int, since) -> bool:
    """Serve facts only if the lookback is covered AND the last complete hour was refreshed.

    ``covered_from <= since`` alone stays true forever after one 30d refresh, so
    events that landed after the last rebuild vanish once their hour is no longer
    the live edge. Require ``covered_to >= hour_floor(now)`` so a stale watermark
    falls back to raw until the next refresh.
    """
    _, _, _, wm = _facts_alias()
    row = wm.filter(organization_id=org_id).first()
    if row is None:
        return False
    last_hour = _hour_floor(timezone.now())
    return bool(row.covered_from <= since and row.covered_to >= last_hour)


def should_serve_rollup(period: str, org_id, since) -> bool:
    return bool(
        serve_rollups()
        and period in ROLLUP_PERIODS
        and org_id is not None
        and org_rollup_covers(org_id, since)
    )


def refresh_org_rollups(org_id: int, hours: int = 24 * 30) -> None:
    from policy.analytics_rollup_models import (
        AnalyticsHourlyGroupFact,
        AnalyticsHourlyRequestFact,
        AnalyticsHourlyRowFact,
        AnalyticsRollupWatermark,
    )

    now = timezone.now()
    since = now - timedelta(hours=max(int(hours), 1))
    covered_from = _hour_floor(since)
    events = EnforcementEvent.objects.filter(organization_id=org_id, created_at__gte=since)
    qs = annotate_request_key(
        annotate_numeric_float(
            annotate_numeric_float(events, "security_risk_score", "_risk"),
            "latency_ms",
            "_lat",
        )
    ).annotate(
        hour=TruncHour("created_at"),
        _rank=Case(
            When(action=ACTION_BLOCK, then=Value(3)),
            When(action=ACTION_REDACT, then=Value(2)),
            default=Value(1),
            output_field=IntegerField(),
        ),
    )

    with transaction.atomic():
        AnalyticsHourlyRowFact.objects.filter(organization_id=org_id, hour__gte=covered_from).delete()
        AnalyticsHourlyRequestFact.objects.filter(organization_id=org_id, hour__gte=covered_from).delete()
        AnalyticsHourlyGroupFact.objects.filter(organization_id=org_id, hour__gte=covered_from).delete()

        row_facts = []
        for row in qs.values("hour", "action").annotate(
            n=Count("id"),
            latency_sum=Sum("_lat"),
            critical_n=Count("id", filter=Q(_risk__gte=CRITICAL_THRESHOLD)),
            lat_0_50=Count("id", filter=Q(_lat__lte=50)),
            lat_50_100=Count("id", filter=Q(_lat__gt=50, _lat__lte=100)),
            lat_100_250=Count("id", filter=Q(_lat__gt=100, _lat__lte=250)),
            lat_250_500=Count("id", filter=Q(_lat__gt=250, _lat__lte=500)),
            lat_500_1000=Count("id", filter=Q(_lat__gt=500, _lat__lte=1000)),
            lat_1s=Count("id", filter=Q(_lat__gt=1000)),
        ):
            if row["hour"] is None:
                continue
            row_facts.append(
                AnalyticsHourlyRowFact(
                    organization_id=org_id,
                    hour=row["hour"],
                    action=row["action"] or "",
                    n=int(row["n"] or 0),
                    latency_sum=float(row["latency_sum"] or 0.0),
                    critical_n=int(row["critical_n"] or 0),
                    lat_0_50=int(row["lat_0_50"] or 0),
                    lat_50_100=int(row["lat_50_100"] or 0),
                    lat_100_250=int(row["lat_100_250"] or 0),
                    lat_250_500=int(row["lat_250_500"] or 0),
                    lat_500_1000=int(row["lat_500_1000"] or 0),
                    lat_1s=int(row["lat_1s"] or 0),
                )
            )
        AnalyticsHourlyRowFact.objects.bulk_create(row_facts, batch_size=500)

        req_facts = []
        for row in qs.values("hour", "_req").annotate(max_rank=Max("_rank"), max_risk=Max("_risk")):
            if row["hour"] is None or not row["_req"]:
                continue
            req_facts.append(
                AnalyticsHourlyRequestFact(
                    organization_id=org_id,
                    hour=row["hour"],
                    request_key=str(row["_req"])[:256],
                    max_rank=int(row["max_rank"] or 1),
                    max_risk=float(row["max_risk"] or 0.0),
                )
            )
        AnalyticsHourlyRequestFact.objects.bulk_create(req_facts, batch_size=500)

        grouped = events.annotate(hour=TruncHour("created_at"))
        grouped = grouped.annotate(**{f"_{k}": KeyTransform(k, "metadata") for k in GROUP_META_FIELDS})
        grp_facts = []
        for row in grouped.values("hour", "action", *[f"_{k}" for k in GROUP_META_FIELDS]).annotate(n=Count("id")):
            if row["hour"] is None:
                continue
            meta = {k: row[f"_{k}"] for k in GROUP_META_FIELDS if row[f"_{k}"] is not None}
            grp_facts.append(
                AnalyticsHourlyGroupFact(
                    organization_id=org_id,
                    hour=row["hour"],
                    action=row["action"] or "",
                    meta=meta,
                    n=int(row["n"] or 0),
                )
            )
        AnalyticsHourlyGroupFact.objects.bulk_create(grp_facts, batch_size=500)

        AnalyticsRollupWatermark.objects.update_or_create(
            organization_id=org_id,
            defaults={"covered_from": covered_from, "covered_to": now, "refreshed_at": now},
        )


def _edge_events(org_id, since, first_full, last_hour):
    return EnforcementEvent.objects.using(ANALYTICS_DB_ALIAS).filter(
        organization_id=org_id,
    ).filter(Q(created_at__gte=since, created_at__lt=first_full) | Q(created_at__gte=last_hour))


def _mttr_minutes(org_id, since):
    resolved = EnforcementEvent.objects.using(ANALYTICS_DB_ALIAS).filter(
        organization_id=org_id,
        created_at__gte=since,
        incident_status="resolved",
        resolved_at__isnull=False,
    )
    avg_duration = resolved.annotate(
        duration=ExpressionWrapper(
            F("resolved_at") - F("created_at"),
            output_field=DurationField(),
        )
    ).aggregate(avg=Avg("duration"))["avg"]
    return round(avg_duration.total_seconds() / 60, 1) if avg_duration else None


def _merge_request_partition(org_id, first_full, last_hour, edge_events):
    _, reqs, _, _ = _facts_alias()
    middle = (
        reqs.filter(organization_id=org_id, hour__gte=first_full, hour__lt=last_hour)
        .values("request_key")
        .annotate(max_rank=Max("max_rank"), max_risk=Max("max_risk"))
    )
    part = middle.aggregate(
        requests_inspected=Count("request_key"),
        requests_blocked=Count("request_key", filter=Q(max_rank=3)),
        requests_redacted=Count("request_key", filter=Q(max_rank=2)),
        requests_critical=Count("request_key", filter=Q(max_risk__gte=CRITICAL_THRESHOLD)),
    )
    inspected = int(part["requests_inspected"] or 0)
    blocked = int(part["requests_blocked"] or 0)
    redacted = int(part["requests_redacted"] or 0)
    critical = int(part["requests_critical"] or 0)

    edge_groups = list(
        annotate_request_key(
            annotate_numeric_float(edge_events, "security_risk_score", "_risk")
        )
        .annotate(
            _rank=Case(
                When(action=ACTION_BLOCK, then=Value(3)),
                When(action=ACTION_REDACT, then=Value(2)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .values("_req")
        .annotate(max_rank=Max("_rank"), max_risk=Max("_risk"))
    )
    if not edge_groups:
        return inspected, blocked, redacted, critical

    keys = [str(row["_req"])[:256] for row in edge_groups if row["_req"]]
    overlap = {
        row["request_key"]: row
        for row in reqs.filter(
            organization_id=org_id,
            hour__gte=first_full,
            hour__lt=last_hour,
            request_key__in=keys,
        )
        .values("request_key")
        .annotate(max_rank=Max("max_rank"), max_risk=Max("max_risk"))
    }
    for row in edge_groups:
        key = str(row["_req"] or "")[:256]
        if not key:
            continue
        e_rank = int(row["max_rank"] or 1)
        e_risk = float(row["max_risk"] or 0.0)
        hit = overlap.get(key)
        if hit is None:
            inspected += 1
            if e_rank == 3:
                blocked += 1
            elif e_rank == 2:
                redacted += 1
            if e_risk >= CRITICAL_THRESHOLD:
                critical += 1
            continue
        old_rank = int(hit["max_rank"] or 1)
        old_risk = float(hit["max_risk"] or 0.0)
        new_rank = max(old_rank, e_rank)
        new_risk = max(old_risk, e_risk)
        if old_rank != new_rank:
            if old_rank == 3:
                blocked -= 1
            elif old_rank == 2:
                redacted -= 1
            if new_rank == 3:
                blocked += 1
            elif new_rank == 2:
                redacted += 1
        if old_risk < CRITICAL_THRESHOLD <= new_risk:
            critical += 1
    return inspected, blocked, redacted, critical


def soc_kpis_from_rollup(org_id: int, since):
    now = timezone.now()
    first_full, last_hour = _split_rollup_window(since, now)
    if first_full >= last_hour:
        events = EnforcementEvent.objects.using(ANALYTICS_DB_ALIAS).filter(
            organization_id=org_id, created_at__gte=since
        )
        return soc_kpis_from_events(events)

    rows, _, _, _ = _facts_alias()
    rows = rows.filter(organization_id=org_id, hour__gte=first_full, hour__lt=last_hour)
    agg = rows.aggregate(
        total=Sum("n"),
        blocked=Sum("n", filter=Q(action=ACTION_BLOCK)),
        redacted=Sum("n", filter=Q(action=ACTION_REDACT)),
        critical_count=Sum("critical_n"),
        latency_sum=Sum("latency_sum"),
        lat_0_50=Sum("lat_0_50"),
        lat_50_100=Sum("lat_50_100"),
        lat_100_250=Sum("lat_100_250"),
        lat_250_500=Sum("lat_250_500"),
        lat_500_1000=Sum("lat_500_1000"),
        lat_1s=Sum("lat_1s"),
    )
    action_breakdown = {
        row["action"]: int(row["c"] or 0)
        for row in rows.values("action").annotate(c=Sum("n"))
        if row["action"] is not None
    }
    edge = _edge_events(org_id, since, first_full, last_hour)
    edge_k = soc_kpis_from_events(edge)
    inspected, req_blocked, req_redacted, req_critical = _merge_request_partition(
        org_id, first_full, last_hour, edge
    )

    def _add(a, b):
        return int(a or 0) + int(b or 0)

    latency_buckets = {
        "0-50ms": _add(agg["lat_0_50"], edge_k["latency_buckets"]["0-50ms"]),
        "50-100ms": _add(agg["lat_50_100"], edge_k["latency_buckets"]["50-100ms"]),
        "100-250ms": _add(agg["lat_100_250"], edge_k["latency_buckets"]["100-250ms"]),
        "250-500ms": _add(agg["lat_250_500"], edge_k["latency_buckets"]["250-500ms"]),
        "500ms-1s": _add(agg["lat_500_1000"], edge_k["latency_buckets"]["500ms-1s"]),
        "1s+": _add(agg["lat_1s"], edge_k["latency_buckets"]["1s+"]),
    }
    for action, count in edge_k["action_breakdown"].items():
        action_breakdown[action] = _add(action_breakdown.get(action), count)

    total = _add(agg["total"], edge_k["total"])
    blocked = _add(agg["blocked"], edge_k["blocked"])
    redacted = _add(agg["redacted"], edge_k["redacted"])
    return {
        "total": total,
        "blocked": blocked,
        "redacted": redacted,
        "critical_count": _add(agg["critical_count"], edge_k["critical_count"]),
        "action_breakdown": action_breakdown,
        "latency_sum": float(agg["latency_sum"] or 0.0) + float(edge_k["latency_sum"] or 0.0),
        "latency_buckets": latency_buckets,
        "requests_inspected": inspected,
        "requests_allowed": inspected - req_blocked - req_redacted,
        "requests_blocked": req_blocked,
        "requests_redacted": req_redacted,
        "requests_critical": req_critical,
        "mttr_minutes": _mttr_minutes(org_id, since),
    }


def _iter_groups(org_id: int, since, hour_gte=None, hour_lt=None):
    _, _, groups, _ = _facts_alias()
    qs = groups.filter(organization_id=org_id, hour__gte=since if hour_gte is None else hour_gte)
    if hour_lt is not None:
        qs = qs.filter(hour__lt=hour_lt)
    return qs.values("hour", "action", "meta", "n")


def _accumulate_module(modules, action, meta, n):
    source = meta.get("source", "")
    risk_score = meta.get("security_risk_score", 0) or 0
    try:
        risk_score = float(risk_score)
    except (TypeError, ValueError):
        risk_score = 0.0
    kwargs = dict(
        is_blocked=action == ACTION_BLOCK,
        is_redacted=action == ACTION_REDACT,
        is_flagged=action == ACTION_FLAG,
        is_critical=risk_score >= CRITICAL_THRESHOLD,
        n=n,
    )
    increment_bucket(modules["1.1"], **kwargs)
    for mid in specialty_modules_for_event(meta, source=source):
        increment_bucket(modules[mid], **kwargs)


def module_kpis_from_rollup(org_id: int, since) -> dict:
    now = timezone.now()
    first_full, last_hour = _split_rollup_window(since, now)
    modules = {
        mid: {"total": 0, "blocked": 0, "redacted": 0, "flagged": 0, "critical": 0}
        for mid in MODULE_IDS
    }
    if first_full >= last_hour:
        hour_gte, hour_lt = since, None
    else:
        hour_gte, hour_lt = first_full, last_hour
    for ev in _iter_groups(org_id, since, hour_gte=hour_gte, hour_lt=hour_lt):
        _accumulate_module(modules, ev["action"], ev.get("meta") or {}, int(ev.get("n") or 0))
    if first_full < last_hour:
        grouped = (
            _edge_events(org_id, since, first_full, last_hour)
            .annotate(**{f"_{f}": KeyTransform(f, "metadata") for f in MODULE_META_FIELDS})
            .values("action", *[f"_{f}" for f in MODULE_META_FIELDS])
            .annotate(n=Count("id"))
        )
        for ev in grouped:
            meta = {f: ev[f"_{f}"] for f in MODULE_META_FIELDS if ev[f"_{f}"] is not None}
            _accumulate_module(modules, ev["action"], meta, int(ev.get("n") or 0))
    return modules


def fill_vector_buckets_from_rollup(org_id, since, now, bucket_minutes, buckets, event_to_vectors):
    for ev in _iter_groups(org_id, since):
        hour = ev.get("hour")
        if hour is None:
            continue
        bidx = int((hour - since).total_seconds() // (bucket_minutes * 60))
        if bidx < 0:
            continue
        bucket_start = since + timedelta(minutes=bidx * bucket_minutes)
        if bucket_start > now:
            bucket_start = now
        bucket_key = bucket_start.isoformat()
        if bucket_key not in buckets:
            continue
        meta = ev.get("meta") or {}
        n = int(ev.get("n") or 0)
        for v in event_to_vectors({"metadata": meta}):
            if v in buckets[bucket_key]:
                buckets[bucket_key][v] += n


def fill_module_trends_from_rollup(org_id, since, now, bucket_minutes, module_buckets):
    for ev in _iter_groups(org_id, since):
        hour = ev.get("hour")
        if hour is None:
            continue
        bidx = int((hour - since).total_seconds() // (max(bucket_minutes, 1) * 60))
        if bidx < 0:
            continue
        bucket_start = since + timedelta(minutes=bidx * bucket_minutes)
        if bucket_start > now:
            bucket_start = now
        bucket_key = bucket_start.isoformat()
        if bucket_key not in module_buckets["1.1"]:
            continue
        _accumulate_module(
            {mid: module_buckets[mid][bucket_key] for mid in MODULE_IDS},
            ev["action"],
            ev.get("meta") or {},
            int(ev.get("n") or 0),
        )
