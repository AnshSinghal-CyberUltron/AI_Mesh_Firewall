"""Audit and automatic normalization for Module 2 enforcement telemetry."""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from module2.analytics import (
    _looks_like_mcp_event,
    event_source,
    hours_from_period,
    key_prefix_from_meta,
)

logger = logging.getLogger(__name__)

RAG_STAGES = frozenset({"query", "retriever", "ranker", "generator"})

ISSUE_LEGACY_MCP = "legacy_mcp_missing_event_type"
ISSUE_UEBA_BLIND_SPOT = "ueba_missing_key_prefix"
ISSUE_NULL_ORG = "null_organization"
ISSUE_ORG_KEY_MISMATCH = "org_key_mismatch"
ISSUE_RAG_STAGE = "rag_stage_without_event_type"
ISSUE_LEGACY_VECTOR_POLLUTION = "legacy_vector_unknown_pollution"

_HOIST_FIELDS = (
    "tools_invoked",
    "mcp_server",
    "server_slug",
    "mcp_direction",
    "scan_direction",
    "collection",
    "vector_collection",
    "vector_namespace",
    "key_prefix",
    "api_key_prefix",
)

_last_background_repair_at = 0.0


@dataclass
class TelemetryIssue:
    code: str
    event_id: int
    organization_id: int | None
    lane: str
    detail: str


@dataclass
class TelemetryHealthReport:
    period: str
    since_iso: str
    organization_slug: str | None
    total_events: int = 0
    lane_counts: dict[str, int] = field(default_factory=dict)
    issue_counts: Counter = field(default_factory=Counter)
    issues: list[TelemetryIssue] = field(default_factory=list)
    sample_event_ids: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    remediable_event_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "since": self.since_iso,
            "organization": self.organization_slug,
            "total_events": self.total_events,
            "lane_counts": self.lane_counts,
            "issue_counts": dict(self.issue_counts),
            "sample_event_ids": dict(self.sample_event_ids),
            "remediable_event_ids": self.remediable_event_ids,
            "healthy": self.total_events > 0 and not self.issue_counts,
        }


def normalize_enforcement_metadata(metadata: dict | None) -> tuple[dict, bool]:
    """
    Normalize enforcement metadata for Module 2 lane detection.

    Runs automatically on every telemetry drain so operators never need to
    run manual repair commands for new events.
    """
    meta = dict(metadata or {})
    changed = False

    extra = meta.get("extra")
    if isinstance(extra, dict):
        for field in _HOIST_FIELDS:
            if extra.get(field) is not None and meta.get(field) in (None, "", []):
                meta[field] = extra[field]
                changed = True

    prefix = key_prefix_from_meta(meta)
    if prefix and not meta.get("key_prefix"):
        meta["key_prefix"] = prefix
        changed = True

    if _looks_like_mcp_event(meta) and str(meta.get("event_type") or "").lower() != "mcp_tool_call":
        meta["event_type"] = "mcp_tool_call"
        changed = True

    stage = str(meta.get("pipeline_stage") or "").lower()
    if (
        stage in RAG_STAGES
        and str(meta.get("event_type") or "").lower() != "rag_pipeline"
        and not _looks_like_mcp_event(meta)
    ):
        meta["event_type"] = "rag_pipeline"
        changed = True

    if not str(meta.get("prompt_snippet") or "").strip():
        from module2.analytics import prompt_snippet_from_meta

        snippet = prompt_snippet_from_meta(meta)
        if snippet:
            meta["prompt_snippet"] = snippet[:500]
            changed = True

    return meta, changed


def resolve_organization_id(
    event: dict,
    metadata: dict,
    key_org_by_prefix: dict[str, int] | None = None,
) -> int | None:
    """Resolve tenant org from telemetry payload or API key prefix."""
    for candidate in (event.get("organization_id"), metadata.get("organization_id")):
        try:
            org_id = int(candidate) if candidate is not None else None
        except (TypeError, ValueError):
            org_id = None
        if org_id and org_id > 0:
            return org_id

    prefix = key_prefix_from_meta(metadata) or str(event.get("key_prefix") or "").strip()
    if not prefix:
        return None

    if key_org_by_prefix is None:
        key_org_by_prefix = build_key_org_map()
    return key_org_by_prefix.get(prefix)


def _has_vector_fields(meta: dict) -> bool:
    return bool(
        meta.get("collection")
        or meta.get("vector_collection")
        or meta.get("vector_namespace")
    )


def audit_event_metadata(
    event_id: int,
    organization_id: int | None,
    metadata: dict | None,
    key_org_by_prefix: dict[str, int],
) -> list[TelemetryIssue]:
    """Return quality issues for a single enforcement event."""
    meta = metadata or {}
    lane = event_source(meta)
    issues: list[TelemetryIssue] = []

    if organization_id is None:
        issues.append(
            TelemetryIssue(
                code=ISSUE_NULL_ORG,
                event_id=event_id,
                organization_id=organization_id,
                lane=lane,
                detail="organization_id is null — event is invisible to org-scoped Module 2 views",
            )
        )

    prefix = key_prefix_from_meta(meta)
    if prefix and organization_id is not None:
        key_org = key_org_by_prefix.get(prefix)
        if key_org is not None and key_org != organization_id:
            issues.append(
                TelemetryIssue(
                    code=ISSUE_ORG_KEY_MISMATCH,
                    event_id=event_id,
                    organization_id=organization_id,
                    lane=lane,
                    detail=f"key_prefix {prefix} belongs to org {key_org}, event org is {organization_id}",
                )
            )

    if _looks_like_mcp_event(meta) and str(meta.get("event_type") or "").lower() != "mcp_tool_call":
        issues.append(
            TelemetryIssue(
                code=ISSUE_LEGACY_MCP,
                event_id=event_id,
                organization_id=organization_id,
                lane=lane,
                detail="MCP-shaped metadata missing event_type=mcp_tool_call",
            )
        )

    stage = str(meta.get("pipeline_stage") or "").lower()
    if stage in RAG_STAGES and str(meta.get("event_type") or "").lower() != "rag_pipeline":
        if not _looks_like_mcp_event(meta):
            issues.append(
                TelemetryIssue(
                    code=ISSUE_RAG_STAGE,
                    event_id=event_id,
                    organization_id=organization_id,
                    lane=lane,
                    detail=f"pipeline_stage={stage} without event_type=rag_pipeline",
                )
            )

    if not prefix and lane in {"chat", "ueba"} and not _looks_like_mcp_event(meta):
        issues.append(
            TelemetryIssue(
                code=ISSUE_UEBA_BLIND_SPOT,
                event_id=event_id,
                organization_id=organization_id,
                lane=lane,
                detail="no key_prefix — excluded from M2.2 UEBA key metrics",
            )
        )

    if not _has_vector_fields(meta) and lane not in {"vector", "mcp", "rag"}:
        issues.append(
            TelemetryIssue(
                code=ISSUE_LEGACY_VECTOR_POLLUTION,
                event_id=event_id,
                organization_id=organization_id,
                lane=lane,
                detail="pre-fix vector exposure would have counted this under collection=unknown",
            )
        )

    return issues


def build_key_org_map() -> dict[str, int]:
    from core.models import GatewayAPIKey

    return {
        k.prefix: k.organization_id
        for k in GatewayAPIKey.objects.exclude(organization_id__isnull=True).only("prefix", "organization_id")
    }


def repair_stale_enforcement_events(
    *,
    lookback_hours: int | None = None,
    batch_size: int | None = None,
) -> dict[str, int]:
    """
    Repair historical enforcement rows in small batches.

    Called automatically from the telemetry drain loop and Celery beat so
    Module 2 dashboards self-heal without manual commands.
    """
    from policy.models import EnforcementEvent

    lookback_hours = lookback_hours or int(getattr(settings, "MODULE2_TELEMETRY_REPAIR_LOOKBACK_HOURS", 720))
    batch_size = batch_size or int(getattr(settings, "MODULE2_TELEMETRY_REPAIR_BATCH_SIZE", 250))
    since = timezone.now() - timedelta(hours=lookback_hours)
    key_org_by_prefix = build_key_org_map()

    stats = {"examined": 0, "metadata_updated": 0, "org_updated": 0, "skipped": 0}
    pending_meta: list[tuple[int, dict]] = []
    pending_org: list[tuple[int, int]] = []

    qs = (
        EnforcementEvent.objects.filter(created_at__gte=since)
        .order_by("-created_at")
        .values("id", "organization_id", "metadata")[:batch_size]
    )

    for row in qs:
        stats["examined"] += 1
        meta, meta_changed = normalize_enforcement_metadata(row.get("metadata") or {})
        resolved_org = resolve_organization_id({}, meta, key_org_by_prefix)
        row_changed = False

        if meta_changed:
            pending_meta.append((row["id"], meta))
            stats["metadata_updated"] += 1
            row_changed = True
        if resolved_org and resolved_org != row.get("organization_id"):
            pending_org.append((row["id"], resolved_org))
            stats["org_updated"] += 1
            row_changed = True
        if not row_changed:
            stats["skipped"] += 1

    now_iso = timezone.now().isoformat()
    if pending_meta:
        by_id = {pk: meta for pk, meta in pending_meta}
        for ev in EnforcementEvent.objects.filter(pk__in=by_id.keys()):
            meta = by_id[ev.pk]
            meta["telemetry_health_repaired_at"] = now_iso
            ev.metadata = meta
            ev.save(update_fields=["metadata"])

    if pending_org:
        by_id = dict(pending_org)
        for ev in EnforcementEvent.objects.filter(pk__in=by_id.keys()):
            ev.organization_id = by_id[ev.pk]
            ev.save(update_fields=["organization_id"])

    if stats["metadata_updated"] or stats["org_updated"]:
        logger.info(
            "module2 telemetry repair: examined=%d metadata_updated=%d org_updated=%d",
            stats["examined"],
            stats["metadata_updated"],
            stats["org_updated"],
        )

    return stats


def maybe_repair_stale_telemetry(*, force: bool = False) -> dict[str, Any]:
    """Rate-limited wrapper used by the telemetry drain thread and Celery."""
    global _last_background_repair_at

    interval = float(getattr(settings, "MODULE2_TELEMETRY_REPAIR_INTERVAL_SEC", 300))
    now = time.monotonic()
    if not force and (now - _last_background_repair_at) < interval:
        return {"skipped": True, "reason": "interval_not_elapsed"}

    _last_background_repair_at = now
    stats = repair_stale_enforcement_events()
    stats["skipped"] = False
    return stats


def run_telemetry_health(
    events_qs,
    *,
    period: str = "7d",
    organization_slug: str | None = None,
    max_samples_per_issue: int = 5,
) -> TelemetryHealthReport:
    """Scan enforcement events and summarize Module 2 telemetry quality issues."""
    hours = hours_from_period(period)
    since = timezone.now() - timedelta(hours=hours)
    key_org_by_prefix = build_key_org_map()

    report = TelemetryHealthReport(
        period=period,
        since_iso=since.isoformat(),
        organization_slug=organization_slug,
    )

    rows = events_qs.filter(created_at__gte=since).values("id", "organization_id", "metadata")
    report.total_events = len(rows)

    for row in rows:
        meta = row.get("metadata") or {}
        lane = event_source(meta)
        report.lane_counts[lane] = report.lane_counts.get(lane, 0) + 1

        for issue in audit_event_metadata(row["id"], row.get("organization_id"), meta, key_org_by_prefix):
            report.issue_counts[issue.code] += 1
            if len(report.sample_event_ids[issue.code]) < max_samples_per_issue:
                report.sample_event_ids[issue.code].append(issue.event_id)
            report.issues.append(issue)
            if issue.code == ISSUE_LEGACY_MCP:
                report.remediable_event_ids.append(issue.event_id)

    return report


def apply_metadata_fixes(event_ids: list[int], *, dry_run: bool = True) -> dict[str, int]:
    """Manual override — normal operation uses automatic repair instead."""
    from policy.models import EnforcementEvent

    stats = {"examined": 0, "updated": 0, "skipped": 0}
    now_iso = timezone.now().isoformat()

    for ev in EnforcementEvent.objects.filter(pk__in=event_ids).iterator():
        stats["examined"] += 1
        meta, changed = normalize_enforcement_metadata(ev.metadata or {})
        if not changed:
            stats["skipped"] += 1
            continue
        meta["telemetry_health_repaired_at"] = now_iso
        if not dry_run:
            ev.metadata = meta
            ev.save(update_fields=["metadata"])
        stats["updated"] += 1

    return stats
