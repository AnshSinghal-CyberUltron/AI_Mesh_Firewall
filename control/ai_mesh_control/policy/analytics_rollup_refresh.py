"""Periodic C-2 fact refresh: Redis-locked so gunicorn workers and Celery beat share one runner."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

LOCK_KEY = "analytics:rollup_refresh"


def refresh_enabled() -> bool:
    return os.environ.get("ANALYTICS_ROLLUP_REFRESH", "1").lower() in {"1", "true", "yes"}


def refresh_interval_sec() -> int:
    try:
        return max(int(os.environ.get("ANALYTICS_ROLLUP_REFRESH_SEC", "900")), 60)
    except (TypeError, ValueError):
        return 900


def refresh_hours() -> int:
    try:
        return max(int(os.environ.get("ANALYTICS_ROLLUP_REFRESH_HOURS", str(24 * 30))), 24)
    except (TypeError, ValueError):
        return 24 * 30


def _redis():
    from core.signals import _get_redis_client

    return _get_redis_client()


def refresh_all_orgs(*, hours: int | None = None) -> dict:
    from auth.models import Organization
    from policy.analytics_rollup import refresh_org_rollups

    lookback = hours if hours is not None else refresh_hours()
    orgs = list(Organization.objects.all().order_by("id").values_list("id", "slug"))
    for org_id, _slug in orgs:
        refresh_org_rollups(org_id, hours=lookback)
    return {"orgs": len(orgs), "hours": lookback, "skipped": False}


def try_refresh_all_locked(*, hours: int | None = None) -> dict:
    """Refresh every org if enabled and this process holds the Redis lock."""
    if not refresh_enabled():
        return {"skipped": True, "reason": "disabled"}
    client = _redis()
    acquired = client.set(LOCK_KEY, "1", nx=True, ex=refresh_interval_sec())
    if not acquired:
        return {"skipped": True, "reason": "lock"}
    try:
        result = refresh_all_orgs(hours=hours)
        result["lock"] = LOCK_KEY
        return result
    except Exception:
        logger.exception("Analytics rollup refresh failed")
        raise
