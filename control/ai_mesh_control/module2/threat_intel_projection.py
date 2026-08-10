"""Project Module 2 threat-intel entries onto gateway blocked_keywords."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterable

from core.models import FirewallConfig
from module2.models import ThreatIntelEntry, ThreatIntelProjectionState

logger = logging.getLogger(__name__)

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


def live_blocked_keyword_keys_from_csv(value: str | None) -> set[str]:
    return {keyword_key(token) for token in parse_blocked_keywords_csv(value)}


def resolve_projection_eligibility(entry: ThreatIntelEntry) -> ThreatIntelProjectionRow:
    """Eligibility only — whether this IOC *can* project to blocked_keywords."""
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


def resolve_projection_row(
    entry: ThreatIntelEntry,
    live_keyword_keys: set[str] | None = None,
) -> ThreatIntelProjectionRow:
    """Eligibility + optional live FirewallConfig presence check.

    When ``live_keyword_keys`` is provided and a projectable keyword is absent from
    the live blocked list, return ``pending_gateway_sync`` so the UI does not claim
    the gateway is already enforcing the IOC.
    """
    base = resolve_projection_eligibility(entry)
    if base.effective_mode != "synced_for_blocking" or not base.keyword:
        return base
    if live_keyword_keys is not None and keyword_key(base.keyword) not in live_keyword_keys:
        return ThreatIntelProjectionRow(
            entry_id=entry.id,
            effective_mode="pending_gateway_sync",
            effective_reason="not_in_firewall_blocked_keywords",
            keyword=base.keyword,
        )
    return base


def summarize_projection(
    entries: Iterable[ThreatIntelEntry],
    live_keyword_keys: set[str] | None = None,
) -> dict:
    eligibility_rows = [resolve_projection_eligibility(entry) for entry in entries]
    managed_keywords = [
        row.keyword
        for row in eligibility_rows
        if row.effective_mode == "synced_for_blocking" and row.keyword
    ]

    display_rows = [
        resolve_projection_row(entry, live_keyword_keys=live_keyword_keys) for entry in entries
    ]
    summary_rows = [
        {
            "entry_id": row.entry_id,
            "effective_mode": row.effective_mode,
            "effective_reason": row.effective_reason,
            "projected_keyword": row.keyword or "",
        }
        for row in display_rows
    ]
    return {
        "projection_mode": "keyword_literal_blocked_keywords",
        "managed_keywords": [kw for kw in managed_keywords if kw],
        "blocking_entries": sum(1 for row in display_rows if row.effective_mode == "synced_for_blocking"),
        "pending_gateway_sync_entries": sum(
            1 for row in display_rows if row.effective_mode == "pending_gateway_sync"
        ),
        "telemetry_only_entries": sum(
            1 for row in display_rows if row.effective_mode == "telemetry_only"
        ),
        "rows": summary_rows,
    }


def read_redis_blocked_keyword_keys(org) -> set[str] | None:
    """Best-effort read of live gateway blocked_keywords from Redis.

    Returns:
      - set of keyword keys when the Redis config key was read (may be empty)
      - None when Redis is unavailable (skip force-republish)
    """
    try:
        import redis
        from django.conf import settings

        from ai_mesh_shared.redis_pool import connection_pool_kwargs

        slug = getattr(org, "slug", None) or "default"
        redis_key = f"firewall:config:{slug}"
        client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            **connection_pool_kwargs(),
        )
        raw = client.get(redis_key)
        if raw is None:
            return set()
        data = json.loads(raw)
        kws = data.get("blocked_keywords") or []
        if not isinstance(kws, list):
            return set()
        return {keyword_key(k) for k in kws if k}
    except Exception:
        logger.debug("threat_intel projection: Redis blocked_keywords read failed", exc_info=True)
        return None


def _managed_missing_from_keys(managed_list: list[str], present_keys: set[str]) -> bool:
    managed_keys = {keyword_key(k) for k in managed_list}
    return bool(managed_keys) and not managed_keys.issubset(present_keys)


def apply_threat_intel_projection(org) -> dict:
    entries = list(ThreatIntelEntry.objects.filter(organization=org).order_by("id"))
    cfg = FirewallConfig.load(org)
    state, _ = ThreatIntelProjectionState.objects.get_or_create(organization=org)

    # Merge uses eligibility (not live-adjusted mode) so pending IOCs still restore.
    eligibility = summarize_projection(entries, live_keyword_keys=None)
    managed_from_iocs = list(eligibility["managed_keywords"])

    current_all = parse_blocked_keywords_csv(cfg.blocked_keywords)
    previous_managed = parse_blocked_keywords_csv(
        format_blocked_keywords_csv(state.managed_blocked_keywords or [])
    )
    previous_managed_keys = {keyword_key(item) for item in previous_managed}

    manual_keywords = [
        token for token in current_all if keyword_key(token) not in previous_managed_keys
    ]
    merged = manual_keywords + managed_from_iocs
    merged_csv = format_blocked_keywords_csv(merged)
    managed_list = parse_blocked_keywords_csv(format_blocked_keywords_csv(managed_from_iocs))

    db_keys = live_blocked_keyword_keys_from_csv(cfg.blocked_keywords)
    cfg_changed = normalize_keyword(cfg.blocked_keywords or "") != normalize_keyword(merged_csv)
    # Explicit heal: any managed IOC keyword missing from DB CSV must be restored.
    if _managed_missing_from_keys(managed_list, db_keys):
        cfg_changed = True

    state_changed = state.managed_blocked_keywords != managed_list

    redis_keys = read_redis_blocked_keyword_keys(org)
    redis_drift = redis_keys is not None and _managed_missing_from_keys(managed_list, redis_keys)
    force_redis_republish = False

    if cfg_changed:
        cfg.blocked_keywords = merged_csv
        cfg.save(update_fields=["blocked_keywords", "updated_at"])
    elif redis_drift:
        # DB already correct but live Redis missing managed keywords — bump save to
        # trigger core FirewallConfig → Redis post_save without editing Module 1.
        cfg.blocked_keywords = merged_csv
        cfg.save(update_fields=["blocked_keywords", "updated_at"])
        force_redis_republish = True

    if state_changed:
        state.managed_blocked_keywords = managed_list
        state.save(update_fields=["managed_blocked_keywords", "updated_at"])

    # Re-read DB after heal for honest display modes.
    cfg.refresh_from_db()
    live_keys = live_blocked_keyword_keys_from_csv(cfg.blocked_keywords)
    projection = summarize_projection(entries, live_keyword_keys=live_keys)
    projection["manual_keywords_count"] = len(manual_keywords)
    projection["managed_keywords_count"] = len(managed_list)
    projection["blocked_keywords_updated"] = bool(cfg_changed)
    projection["redis_republished"] = bool(force_redis_republish or cfg_changed)
    projection["redis_drift_detected"] = bool(redis_drift)
    return projection
