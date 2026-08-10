"""Request-scoped enforcement metrics — one gateway request == one counted unit.

Module-2 self-contained copy (kept under module2/ so Module 1 policy/ stays untouched).

A single gateway call emits multiple EnforcementEvent rows that share
metadata.request_id. Collapse these to one partition (block > redact > allow)
so Module 2 dashboard totals match one-request semantics.
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
    request_id: str = ""
    enforcement_event_id: int | None = None
    display_event_id: str = ""
    had_monitor: bool = False
    had_reroute: bool = False


def _merge_metadata(existing: dict, incoming: dict, incoming_action: str) -> dict:
    if incoming_action == ACTION_BLOCK:
        merged = {**existing, **incoming}
    elif incoming_action == ACTION_REDACT:
        merged = {**existing, **incoming}
    else:
        merged = {**incoming, **existing}
    # Preserve prompt preview fields when a later event (e.g. input_blocked,
    # rag_query block, stream_complete) merges over an earlier row that carried
    # the user text but does not repeat prompt_snippet / prompt_lineage.
    _prompt_keys = (
        "prompt_snippet",
        "prompt_submitted",
        "original_prompt",
        "user_prompt",
        "input_preview",
        "input_text",
        "forwarded_prompt",
        "prompt_lineage",
        "context_source",
    )
    for key in _prompt_keys:
        if str(merged.get(key) or "").strip():
            continue
        prev = existing.get(key)
        if prev:
            merged[key] = prev
    extra_in = incoming.get("extra")
    extra_ex = existing.get("extra")
    if isinstance(extra_in, dict) and isinstance(extra_ex, dict):
        extra_merged = {**extra_ex, **extra_in}
        for key in ("prompt_snippet", "prompt_submitted", "prompt", "user_message", "query"):
            if str(extra_merged.get(key) or "").strip():
                continue
            prev = extra_ex.get(key)
            if prev:
                extra_merged[key] = prev
        merged["extra"] = extra_merged
    return merged


def collapse_events_by_request(rows: Iterable[dict]) -> list[CollapsedRequest]:
    """Collapse enforcement rows to one record per gateway request."""
    collapsed: dict[str, CollapsedRequest] = {}
    for idx, row in enumerate(rows):
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        key = request_key(meta, idx)
        action = row.get("action") or "allow"
        row_event_id = row.get("id")
        row_request_id = str(meta.get("request_id") or meta.get("pipeline_request_id") or "").strip()
        row_display_id = str(meta.get("event_id") or row_request_id or "").strip()
        entry = collapsed.get(key)
        if entry is None:
            entry = CollapsedRequest(
                key=key,
                action=merge_request_action(None, action),
                metadata=dict(meta),
                request_id=row_request_id,
                enforcement_event_id=row_event_id if isinstance(row_event_id, int) else None,
                display_event_id=row_display_id,
            )
            if row.get("created_at") is not None:
                entry.created_at = row["created_at"]
            collapsed[key] = entry
        else:
            previous_action = entry.action
            entry.action = merge_request_action(entry.action, action)
            entry.metadata = _merge_metadata(entry.metadata, meta, action)
            if not entry.request_id and row_request_id:
                entry.request_id = row_request_id
            if not entry.display_event_id and row_display_id:
                entry.display_event_id = row_display_id
            # Keep the DB id aligned with the strongest observed action for stable
            # "representative event" mapping in request-collapsed activity timelines.
            if (
                isinstance(row_event_id, int)
                and (
                    entry.enforcement_event_id is None
                    or (previous_action != entry.action and action in {ACTION_BLOCK, ACTION_REDACT})
                )
            ):
                entry.enforcement_event_id = row_event_id
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
