"""
Additive Mongo telemetry sink for enforcement events (Phase 1 pilot).

Hot-path policy decisions stay on Redis. This module exists to capture the
append-only event stream for analytics / long-term retention without taxing
the canonical Postgres control plane.

Disabled by default. Enable via GATEWAY_MONGO_TELEMETRY_ENABLED=true. All
write paths are best-effort; failures are logged and swallowed so they
cannot impact request latency.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

log = logging.getLogger(__name__)

_COLLECTION = "enforcement_events"
_DB_NAME = "aiguardx_telemetry"

_client = None  # type: ignore[var-annotated]
_collection = None  # type: ignore[var-annotated]
_init_attempted = False


def _enabled() -> bool:
    return os.getenv("GATEWAY_MONGO_TELEMETRY_ENABLED", "false").lower() == "true"


def is_mongo_telemetry_enabled() -> bool:
    return _enabled()


async def _ensure_collection():
    global _client, _collection, _init_attempted
    if _collection is not None:
        return _collection
    if _init_attempted:
        return None
    _init_attempted = True

    if not _enabled():
        return None

    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
    except ImportError:
        log.warning(
            "GATEWAY_MONGO_TELEMETRY_ENABLED=true but motor not installed; "
            "telemetry sink disabled"
        )
        return None

    url = os.getenv("MONGO_URL", "mongodb://mongo:27017")
    try:
        _client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=2000)
        _collection = _client[_DB_NAME][_COLLECTION]
    except Exception as exc:  # noqa: BLE001
        log.error("mongo telemetry init failed: %s", exc)
        _client = None
        _collection = None
        return _collection

    # Phase-0 CC-1: composite index covering the realistic ops triage query
    # `event_class=X AND org_slug=Y ORDER BY ts DESC`. Org-first per triage
    # Agent-B (cardinality). Background build so writes are not blocked.
    # Idempotent in Mongo — second call is a metadata no-op. Wrapped in
    # try/except so a perms failure cannot kill the sink.
    try:
        await _collection.create_index(
            [("org_slug", 1), ("event_class", 1), ("ts", -1)],
            background=True,
            name="ev_org_evclass_ts",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("mongo telemetry index ensure failed (non-fatal): %s", exc)
    return _collection


async def record_enforcement_event(event: Mapping[str, Any]) -> None:
    """Best-effort write. Never raises."""
    if not _enabled():
        return
    coll = await _ensure_collection()
    if coll is None:
        return
    try:
        await coll.insert_one(dict(event))
    except Exception as exc:  # noqa: BLE001
        log.warning("mongo telemetry insert failed: %s", exc)
