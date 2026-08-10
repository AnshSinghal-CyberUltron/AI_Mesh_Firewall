"""Request-scoped enforcement metrics — one gateway request == one counted unit.

A single gateway call emits multiple EnforcementEvent rows (request, model_routed,
output_guard, …) that share metadata.request_id. Module 1.1 SOC KPIs collapse these
to one partition (block > redact > allow). Module 2 must use the same semantics so
dashboard totals match Module 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from policy.constants import ACTION_BLOCK, ACTION_MONITOR, ACTION_REDACT

REQUEST_ID_MIN_LEN = 8


def request_key(meta: dict | None, row_index: int = 0) -> str:
    """Stable dedupe key: shared request_id when present, else one row = one request."""
    meta = meta if isinstance(meta, dict) else {}
    for candidate in (meta.get("request_id"), meta.get("pipeline_request_id")):
        if isinstance(candidate, str):
            rid = candidate.strip()
            if len(rid) >= REQUEST_ID_MIN_LEN:
                return rid
    return f"__row_{row_index}"


def merge_request_action(previous: str | None, action: str | None) -> str:
    """Classify a gateway request: block beats redact beats allow."""
    action = (action or "allow").lower()
    if action == ACTION_BLOCK:
        return "block"
    if action == ACTION_REDACT:
        if previous == "block":
            return "block"
        return "redact"
    if previous is None:
        return "allow"
    return previous


@dataclass
class CollapsedRequest:
    key: str
    action: str = "allow"
    metadata: dict = field(default_factory=dict)
    created_at: Any = None
    had_monitor: bool = False
    had_reroute: bool = False


def _merge_metadata(existing: dict, incoming: dict, incoming_action: str) -> dict:
    if incoming_action == ACTION_BLOCK:
        return {**existing, **incoming}
    if incoming_action == ACTION_REDACT:
        return {**existing, **incoming}
    return {**incoming, **existing}


def collapse_events_by_request(rows: Iterable[dict]) -> list[CollapsedRequest]:
    """Collapse enforcement rows to one record per gateway request."""
    collapsed: dict[str, CollapsedRequest] = {}
    for idx, row in enumerate(rows):
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        key = request_key(meta, idx)
        action = row.get("action") or "allow"
        entry = collapsed.get(key)
        if entry is None:
            entry = CollapsedRequest(
                key=key,
                action=merge_request_action(None, action),
                metadata=dict(meta),
            )
            if row.get("created_at") is not None:
                entry.created_at = row["created_at"]
            collapsed[key] = entry
        else:
            entry.action = merge_request_action(entry.action, action)
            entry.metadata = _merge_metadata(entry.metadata, meta, action)
            ts = row.get("created_at")
            if ts is not None and (entry.created_at is None or ts < entry.created_at):
                entry.created_at = ts
        if action == ACTION_MONITOR:
            entry.had_monitor = True
        if meta.get("rerouted") is True:
            entry.had_reroute = True
        else:
            extra = meta.get("extra")
            if isinstance(extra, dict) and extra.get("rerouted") is True:
                entry.had_reroute = True
    return list(collapsed.values())


def summarize_request_scoped_events(rows: Iterable[dict]) -> dict[str, int]:
    """Return request-scoped KPI counts aligned with /api/security/soc-kpis/."""
    collapsed = collapse_events_by_request(rows)
    blocked = sum(1 for item in collapsed if item.action == "block")
    redacted = sum(1 for item in collapsed if item.action == "redact")
    total = len(collapsed)
    return {
        "requests_inspected": total,
        "requests_blocked": blocked,
        "requests_redacted": redacted,
        "requests_allowed": total - blocked - redacted,
        "total_events": total,
    }


def iter_rows_from_queryset(qs, *, fields: tuple[str, ...] = ("action", "metadata", "created_at")):
    """Normalize queryset .values() for collapse helpers."""
    return list(qs.values(*fields))
