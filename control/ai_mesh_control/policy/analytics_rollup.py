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
    annotate_is_critical,
    bucket_index_expr,
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
# security_risk_score is DELIBERATELY absent from both key sets. The gateway
# emits it as a continuous float, so including it in a GROUP BY produced one
# group per event and made these "facts" a verbatim copy of the event stream
# (measured: 199,882 group rows for 200,000 events). The derived `is_critical`
# boolean carries everything any consumer reads from it.
GROUP_META_FIELDS = (
    "source",
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


def _covered_to(org_id: int):
    """Instant this org's facts were last rebuilt to, or None if never."""
    _, _, _, wm = _facts_alias()
    return wm.filter(organization_id=org_id).values_list("covered_to", flat=True).first()


def org_rollup_covers(org_id: int, since) -> bool:
    """Serve facts when they reach back far enough. Staleness is handled by trimming.

    This used to also require ``covered_to >= hour_floor(now)``. Because
    ``covered_to`` is the refresh *timestamp* and the refresh runs on an interval
    (900s by default), that test went false the moment the clock crossed an hour
    boundary and stayed false until the next refresh -- so for up to a quarter of
    every hour all four Overview endpoints abandoned 30 days of facts and
    re-scanned raw events, which is how 30d requests reached the 5s deadline.

    Freshness is still honoured, but by ``_rollup_hour_bounds`` clamping the fact
    window to the hours actually materialised and reading the remainder from raw
    events. Nothing that landed after the last refresh is lost, and a stale
    watermark now costs one extra hour of raw scan instead of the whole window.
    """
    _, _, _, wm = _facts_alias()
    row = wm.filter(organization_id=org_id).first()
    if row is None:
        return False
    return bool(row.covered_from <= since)


def _rollup_hour_bounds(org_id: int, since, now):
    """(first_full, last_hour) -- the hour range facts may answer for this org.

    ``last_hour`` is the earlier of "the current hour" and "the hour the last
    refresh completed in": events after ``covered_to`` are not in the facts yet,
    so those hours must come from raw events.
    """
    first_full, last_hour = _split_rollup_window(since, now)
    covered_to = _covered_to(org_id)
    if covered_to is not None:
        last_hour = min(last_hour, _hour_floor(covered_to))
    return first_full, last_hour


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
    # Build from the HOUR FLOOR, not from the unaligned lookback. The watermark
    # advertises `covered_from = _hour_floor(since)` and every reader treats each
    # hour from there as complete -- but this scan used to start at `since`, so
    # the first hour was materialised with only its tail. Events in
    # [covered_from, since) were then in neither the facts nor the raw edge and
    # simply vanished from the oldest bucket of every 30d chart.
    covered_from = _hour_floor(since)
    events = EnforcementEvent.objects.filter(
        organization_id=org_id, created_at__gte=covered_from
    )
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
        grouped = annotate_is_critical(grouped)
        grp_facts = []
        for row in grouped.values(
            "hour", "action", "_is_critical", *[f"_{k}" for k in GROUP_META_FIELDS]
        ).annotate(n=Count("id")):
            if row["hour"] is None:
                continue
            meta = {k: row[f"_{k}"] for k in GROUP_META_FIELDS if row[f"_{k}"] is not None}
            meta["is_critical"] = bool(row["_is_critical"])
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
    first_full, last_hour = _rollup_hour_bounds(org_id, since, now)
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


def _is_critical_meta(meta) -> bool:
    """Prefer the stored boolean; fall back to the score for pre-upgrade rows.

    Facts written before `is_critical` existed carry `security_risk_score`
    instead, and they stay readable until the next refresh rewrites them.
    """
    if "is_critical" in meta:
        return bool(meta["is_critical"])
    try:
        return float(meta.get("security_risk_score", 0) or 0) >= CRITICAL_THRESHOLD
    except (TypeError, ValueError):
        return False


def _accumulate_module(modules, action, meta, n):
    source = meta.get("source", "")
    kwargs = dict(
        is_blocked=action == ACTION_BLOCK,
        is_redacted=action == ACTION_REDACT,
        is_flagged=action == ACTION_FLAG,
        is_critical=_is_critical_meta(meta),
        n=n,
    )
    increment_bucket(modules["1.1"], **kwargs)
    for mid in specialty_modules_for_event(meta, source=source):
        increment_bucket(modules[mid], **kwargs)


def _module_groups_from_events(events, extra_values=()):
    """Group live events exactly as the group fact does, for edge/raw merges."""
    grouped = (
        annotate_is_critical(
            events.annotate(**{f"_{f}": KeyTransform(f, "metadata") for f in MODULE_META_FIELDS})
        )
        .values("action", "_is_critical", *extra_values, *[f"_{f}" for f in MODULE_META_FIELDS])
        .annotate(n=Count("id"))
    )
    for ev in grouped:
        meta = {f: ev[f"_{f}"] for f in MODULE_META_FIELDS if ev[f"_{f}"] is not None}
        meta["is_critical"] = bool(ev["_is_critical"])
        row = {"action": ev["action"], "meta": meta, "n": int(ev.get("n") or 0)}
        for key in extra_values:
            row[key] = ev.get(key)
        yield row


def module_kpis_from_rollup(org_id: int, since) -> dict:
    now = timezone.now()
    first_full, last_hour = _rollup_hour_bounds(org_id, since, now)
    modules = {
        mid: {"total": 0, "blocked": 0, "redacted": 0, "flagged": 0, "critical": 0}
        for mid in MODULE_IDS
    }
    # No complete materialised hour inside the window: everything is raw. Reading
    # facts with no upper bound here would double-count against the edge below.
    if first_full >= last_hour:
        for ev in _module_groups_from_events(
            EnforcementEvent.objects.using(ANALYTICS_DB_ALIAS).filter(
                organization_id=org_id, created_at__gte=since
            )
        ):
            _accumulate_module(modules, ev["action"], ev["meta"], ev["n"])
        return modules
    for ev in _iter_groups(org_id, since, hour_gte=first_full, hour_lt=last_hour):
        _accumulate_module(modules, ev["action"], ev.get("meta") or {}, int(ev.get("n") or 0))
    for ev in _module_groups_from_events(
        _edge_events(org_id, since, first_full, last_hour)
    ):
        _accumulate_module(modules, ev["action"], ev["meta"], ev["n"])
    return modules


def _bucket_key_for(ts, since, now, bucket_minutes, keys):
    """Bucket an instant onto the chart grid, or None if it falls outside it."""
    if ts is None:
        return None
    bidx = int((ts - since).total_seconds() // (max(bucket_minutes, 1) * 60))
    if bidx < 0:
        return None
    bucket_start = since + timedelta(minutes=bidx * bucket_minutes)
    if bucket_start > now:
        bucket_start = now
    key = bucket_start.isoformat()
    return key if key in keys else None


def _trend_rows(org_id, since, now, bucket_minutes):
    """Yield (bucket_instant, action, meta, n) from facts plus the raw edge.

    Facts answer the materialised hours; the hours after the last refresh (and
    the partial hour at the start of the window) come from live events. Before
    this merge the fillers read facts with no upper bound and no edge at all, so
    everything recorded since the last refresh was simply missing from the
    trend lines.
    """
    first_full, last_hour = _rollup_hour_bounds(org_id, since, now)
    if first_full < last_hour:
        for ev in _iter_groups(org_id, since, hour_gte=first_full, hour_lt=last_hour):
            yield ev.get("hour"), ev["action"], ev.get("meta") or {}, int(ev.get("n") or 0)
        edge = _edge_events(org_id, since, first_full, last_hour)
    else:
        edge = EnforcementEvent.objects.using(ANALYTICS_DB_ALIAS).filter(
            organization_id=org_id, created_at__gte=since
        )
    edge = edge.annotate(_bidx=bucket_index_expr(since, bucket_minutes))
    for ev in _module_groups_from_events(edge, extra_values=("_bidx",)):
        bidx = ev.get("_bidx")
        if bidx is None or bidx < 0:
            continue
        yield (
            since + timedelta(minutes=int(bidx) * bucket_minutes),
            ev["action"],
            ev["meta"],
            ev["n"],
        )


def fill_vector_buckets_from_rollup(org_id, since, now, bucket_minutes, buckets, event_to_vectors):
    for ts, _action, meta, n in _trend_rows(org_id, since, now, bucket_minutes):
        bucket_key = _bucket_key_for(ts, since, now, bucket_minutes, buckets)
        if bucket_key is None:
            continue
        for v in event_to_vectors({"metadata": meta}):
            if v in buckets[bucket_key]:
                buckets[bucket_key][v] += n


def fill_module_trends_from_rollup(org_id, since, now, bucket_minutes, module_buckets):
    for ts, action, meta, n in _trend_rows(org_id, since, now, bucket_minutes):
        bucket_key = _bucket_key_for(ts, since, now, bucket_minutes, module_buckets["1.1"])
        if bucket_key is None:
            continue
        _accumulate_module(
            {mid: module_buckets[mid][bucket_key] for mid in MODULE_IDS},
            action,
            meta,
            n,
        )
