"""Project Module 2 threat-intel entries onto gateway blocked_keywords."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from core.models import FirewallConfig
from module2.models import ThreatIntelEntry, ThreatIntelProjectionState

_REGEX_META_RE = re.compile(r"[\\^$*+?{}\[\]|()]")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ThreatIntelProjectionRow:
    entry_id: int
    effective_mode: str
    effective_reason: str
    keyword: str | None = None


def normalize_keyword(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "").strip())


def keyword_key(value: str) -> str:
    return normalize_keyword(value).lower()


def parse_blocked_keywords_csv(value: str | None) -> list[str]:
    if not value:
        return []
    rows = []
    seen: set[str] = set()
    for part in str(value).split(","):
        token = normalize_keyword(part)
        if not token:
            continue
        key = keyword_key(token)
        if key in seen:
            continue
        rows.append(token)
        seen.add(key)
    return rows


def format_blocked_keywords_csv(keywords: Iterable[str]) -> str:
    rows = []
    seen: set[str] = set()
    for item in keywords:
        token = normalize_keyword(item)
        if not token:
            continue
        key = keyword_key(token)
        if key in seen:
            continue
        rows.append(token)
        seen.add(key)
    return ", ".join(rows)


def resolve_projection_row(entry: ThreatIntelEntry) -> ThreatIntelProjectionRow:
    if not entry.auto_block:
        return ThreatIntelProjectionRow(
            entry_id=entry.id,
            effective_mode="telemetry_only",
            effective_reason="auto_block_disabled",
        )

    indicator = normalize_keyword(entry.indicator)
    if not indicator:
        return ThreatIntelProjectionRow(
            entry_id=entry.id,
            effective_mode="telemetry_only",
            effective_reason="empty_indicator",
        )

    # blocked_keywords is comma-delimited and literal; regex/comma indicators are not enforceable here.
    if "," in indicator:
        return ThreatIntelProjectionRow(
            entry_id=entry.id,
            effective_mode="telemetry_only",
            effective_reason="contains_comma_not_supported",
        )
    if _REGEX_META_RE.search(indicator):
        return ThreatIntelProjectionRow(
            entry_id=entry.id,
            effective_mode="telemetry_only",
            effective_reason="regex_indicator_not_supported",
        )

    return ThreatIntelProjectionRow(
        entry_id=entry.id,
        effective_mode="synced_for_blocking",
        effective_reason="keyword_literal_match",
        keyword=indicator,
    )


def summarize_projection(entries: Iterable[ThreatIntelEntry]) -> dict:
    rows = [resolve_projection_row(entry) for entry in entries]
    managed_keywords = [
        row.keyword
        for row in rows
        if row.effective_mode == "synced_for_blocking" and row.keyword
    ]
    summary_rows = [
        {
            "entry_id": row.entry_id,
            "effective_mode": row.effective_mode,
            "effective_reason": row.effective_reason,
            "projected_keyword": row.keyword or "",
        }
        for row in rows
    ]
    return {
        "projection_mode": "keyword_literal_blocked_keywords",
        "managed_keywords": [kw for kw in managed_keywords if kw],
        "blocking_entries": sum(1 for row in rows if row.effective_mode == "synced_for_blocking"),
        "telemetry_only_entries": sum(1 for row in rows if row.effective_mode != "synced_for_blocking"),
        "rows": summary_rows,
    }


def apply_threat_intel_projection(org) -> dict:
    entries = list(ThreatIntelEntry.objects.filter(organization=org).order_by("id"))
    projection = summarize_projection(entries)

    cfg = FirewallConfig.load(org)
    state, _ = ThreatIntelProjectionState.objects.get_or_create(organization=org)

    current_all = parse_blocked_keywords_csv(cfg.blocked_keywords)
    previous_managed = parse_blocked_keywords_csv(
        format_blocked_keywords_csv(state.managed_blocked_keywords or [])
    )
    previous_managed_keys = {keyword_key(item) for item in previous_managed}

    manual_keywords = [
        token for token in current_all if keyword_key(token) not in previous_managed_keys
    ]
    merged = manual_keywords + list(projection["managed_keywords"])
    merged_csv = format_blocked_keywords_csv(merged)
    managed_csv = format_blocked_keywords_csv(projection["managed_keywords"])
    managed_list = parse_blocked_keywords_csv(managed_csv)

    cfg_changed = normalize_keyword(cfg.blocked_keywords or "") != normalize_keyword(merged_csv)
    state_changed = state.managed_blocked_keywords != managed_list

    if cfg_changed:
        cfg.blocked_keywords = merged_csv
        cfg.save(update_fields=["blocked_keywords", "updated_at"])
    if state_changed:
        state.managed_blocked_keywords = managed_list
        state.save(update_fields=["managed_blocked_keywords", "updated_at"])

    projection["manual_keywords_count"] = len(manual_keywords)
    projection["managed_keywords_count"] = len(managed_list)
    projection["blocked_keywords_updated"] = bool(cfg_changed)
    return projection
