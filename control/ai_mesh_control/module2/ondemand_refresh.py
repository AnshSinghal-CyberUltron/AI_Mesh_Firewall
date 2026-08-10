"""Rate-limited on-demand refresh helpers for Module 2 hot pages.

Keeps MCP projection and telemetry metadata heals off the critical path of
every request while still catching up within ~10–15s of a page poll.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_MCP_ONDEMAND_TTL_SEC = 10
_MCP_ONDEMAND_BATCH = 100
_RAG_ONDEMAND_TTL_SEC = 15
_RAG_ONDEMAND_BATCH = 100


def maybe_ondemand_mcp_projection(org_id: int | None) -> dict[str, Any]:
    """Sync-project a small MCPEvent batch for one org (rate-limited 10s)."""
    if not org_id:
        return {"skipped": True, "reason": "no_org"}

    cache_key = f"module2:mcp_proj:ondemand:{org_id}"
    if cache.get(cache_key):
        return {"skipped": True, "reason": "rate_limited"}

    cache.set(cache_key, 1, timeout=_MCP_ONDEMAND_TTL_SEC)
    from module2.mcp_enforcement_projection import repair_mcp_enforcement_projection

    try:
        stats = repair_mcp_enforcement_projection(
            lookback_hours=int(getattr(settings, "MODULE2_MCP_PROJECTION_LOOKBACK_HOURS", 24)),
            batch_size=_MCP_ONDEMAND_BATCH,
            org_id=int(org_id),
        )
        stats["skipped"] = False
        return stats
    except Exception as exc:  # pragma: no cover - never break page GETs
        logger.warning("module2 ondemand mcp projection failed org=%s err=%s", org_id, exc)
        return {"skipped": True, "reason": "error", "error": str(exc)}


def maybe_ondemand_telemetry_heal(org_id: int | None) -> dict[str, Any]:
    """Normalize a small batch of org EnforcementEvent metadata (rate-limited 15s)."""
    if not org_id:
        return {"skipped": True, "reason": "no_org"}

    cache_key = f"module2:telemetry_heal:ondemand:{org_id}"
    if cache.get(cache_key):
        return {"skipped": True, "reason": "rate_limited"}

    cache.set(cache_key, 1, timeout=_RAG_ONDEMAND_TTL_SEC)
    from module2.telemetry_health import repair_stale_enforcement_events

    try:
        stats = repair_stale_enforcement_events(
            lookback_hours=int(getattr(settings, "MODULE2_TELEMETRY_REPAIR_LOOKBACK_HOURS", 720)),
            batch_size=_RAG_ONDEMAND_BATCH,
            org_id=int(org_id),
        )
        stats["skipped"] = False
        return stats
    except Exception as exc:  # pragma: no cover
        logger.warning("module2 ondemand telemetry heal failed org=%s err=%s", org_id, exc)
        return {"skipped": True, "reason": "error", "error": str(exc)}
