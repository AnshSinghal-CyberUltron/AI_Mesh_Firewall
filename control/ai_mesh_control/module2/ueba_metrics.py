"""Shared enforcement event aggregation for UEBA scoring."""

from __future__ import annotations

from collections import defaultdict

from module2.analytics import key_prefix_from_meta
from policy.constants import ACTION_BLOCK, ACTION_REDACT

POLICY_ESCALATION_THREATS = frozenset({
    "kill_switch",
    "model_not_allowed",
    "high_risk_actor",
    "model_isolation",
    "tier2_degraded",
})


def empty_key_metric() -> dict:
    return {
        "total": 0,
        "blocked": 0,
        "redacted": 0,
        "policy_escalations": 0,
        "endpoint_ids": set(),
        "models": set(),
        "model_counts": defaultdict(int),
        "threat_types": defaultdict(int),
        "hourly": defaultdict(int),
    }


def collect_key_metrics(keys_qs, events_qs):
    keys = list(keys_qs)
    key_by_prefix = {k.prefix: k for k in keys}
    prefix_lookup = {k.prefix.lower(): k.prefix for k in keys}
    metrics: dict[str, dict] = defaultdict(empty_key_metric)

    for ev in events_qs.values("created_at", "action", "endpoint_id", "metadata"):
        meta = ev.get("metadata") or {}
        prefix = key_prefix_from_meta(meta)
        if not prefix:
            continue
        canonical = prefix_lookup.get(prefix.lower())
        if not canonical:
            continue
        hour_bucket = ev["created_at"].replace(minute=0, second=0, microsecond=0).isoformat()
        m = metrics[canonical]
        m["total"] += 1
        if ev["action"] == ACTION_BLOCK:
            m["blocked"] += 1
        if ev["action"] == ACTION_REDACT:
            m["redacted"] += 1
        threat = str(meta.get("threat_type") or "unknown")
        if threat in POLICY_ESCALATION_THREATS:
            m["policy_escalations"] += 1
        if ev.get("endpoint_id"):
            m["endpoint_ids"].add(ev["endpoint_id"])
        if meta.get("model"):
            model_name = str(meta["model"])
            m["models"].add(model_name)
            m["model_counts"][model_name] += 1
        m["threat_types"][threat] += 1
        m["hourly"][hour_bucket] += 1

    return key_by_prefix, metrics


def hourly_counts_chronological(hourly: dict) -> list[int]:
    """Return hourly bucket counts sorted by ISO timestamp keys (not insertion order)."""
    if not hourly:
        return [0]
    return [hourly[bucket] for bucket in sorted(hourly.keys())]


def latest_hourly_count(hourly: dict) -> int:
    counts = hourly_counts_chronological(hourly)
    return counts[-1] if counts else 0


def count_lifetime_events_for_prefix(prefix: str, events_qs) -> int:
    total = 0
    for ev in events_qs.values("metadata"):
        meta = ev.get("metadata") or {}
        if key_prefix_from_meta(meta) == prefix:
            total += 1
    return total


def count_lifetime_events_by_prefix(keys, events_qs) -> dict[str, int]:
    """Count all attributed events per key prefix (full history in events_qs)."""
    prefix_lookup = {k.prefix.lower(): k.prefix for k in keys}
    counts: dict[str, int] = {k.prefix: 0 for k in keys}
    for ev in events_qs.values("metadata"):
        meta = ev.get("metadata") or {}
        prefix = key_prefix_from_meta(meta)
        if not prefix:
            continue
        canonical = prefix_lookup.get(prefix.lower())
        if canonical:
            counts[canonical] += 1
    return counts


def increment_lifetime_request_counts(events) -> int:
    """Bump ueba_lifetime_request_count when telemetry events are persisted."""
    from django.db.models import F

    from core.models import GatewayAPIKey

    bumps: dict[tuple[int, str], int] = defaultdict(int)
    for ev in events:
        meta = getattr(ev, "metadata", None) or {}
        prefix = key_prefix_from_meta(meta)
        org_id = getattr(ev, "organization_id", None)
        if prefix and org_id:
            bumps[(org_id, prefix.lower())] += 1

    if not bumps:
        return 0

    updated = 0
    org_ids = {oid for oid, _ in bumps}
    for key in GatewayAPIKey.objects.filter(organization_id__in=org_ids).only(
        "id", "organization_id", "prefix"
    ):
        delta = bumps.get((key.organization_id, key.prefix.lower()), 0)
        if delta:
            GatewayAPIKey.objects.filter(pk=key.pk).update(
                ueba_lifetime_request_count=F("ueba_lifetime_request_count") + delta
            )
            updated += 1
    return updated


def reconcile_lifetime_request_counts(keys, events_qs) -> int:
    """Full lifetime recount for org keys (hourly reconciliation)."""
    from core.models import GatewayAPIKey

    counts = count_lifetime_events_by_prefix(keys, events_qs)
    fixed = 0
    for key in keys:
        lifetime = counts.get(key.prefix, 0)
        if key.ueba_lifetime_request_count != lifetime:
            GatewayAPIKey.objects.filter(pk=key.pk).update(ueba_lifetime_request_count=lifetime)
            key.ueba_lifetime_request_count = lifetime
            fixed += 1
    return fixed
