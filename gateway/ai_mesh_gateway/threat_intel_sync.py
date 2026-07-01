"""
ThreatIntelSync: loads org IOC libraries from Redis and matches prompts tier-0.

Control plane writes ``firewall:threat_intel:{org_slug}`` JSON arrays via
``module2.tasks.sync_threat_intel_to_redis`` and publishes ``threat_intel_updates``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.threat_intel_sync")

REDIS_KEY_PREFIX = "firewall:threat_intel:"
PUBSUB_CHANNEL = "threat_intel_updates"
RECONNECT_DELAY_SECONDS = 5
_MAX_INDICATOR_LEN = 2000


def _indicator_matches(indicator: str, text: str) -> bool:
    if not indicator or not text:
        return False
    ind = indicator[:_MAX_INDICATOR_LEN]
    hay = text if isinstance(text, str) else str(text)
    try:
        if re.search(ind, hay, re.IGNORECASE):
            return True
    except re.error:
        pass
    return ind.lower() in hay.lower()


class ThreatIntelSync:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._subscriber_task: Optional[asyncio.Task] = None
        self._running = False
        self._sync_completed = False

    @property
    def is_loaded(self) -> bool:
        return self._sync_completed

    async def start(self) -> None:
        self._running = True
        await self._load_initial()
        self._subscriber_task = asyncio.create_task(self._subscriber_loop())
        LOG.info("ThreatIntelSync started (orgs=%d)", len(self._cache))

    async def stop(self) -> None:
        self._running = False
        if self._subscriber_task is not None:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass

    def get_entries(self, org_slug: str) -> list[dict[str, Any]]:
        return list(self._cache.get(org_slug or "", []) or [])

    def match(self, org_slug: str, text: str) -> Optional[dict[str, Any]]:
        """Return the first matching IOC entry for *text*, or None."""
        if not org_slug or not text:
            return None
        for entry in self.get_entries(org_slug):
            indicator = str(entry.get("indicator") or "")
            if _indicator_matches(indicator, text):
                return entry
        return None

    async def _load_initial(self) -> None:
        client = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            async for key in client.scan_iter(match=f"{REDIS_KEY_PREFIX}*"):
                slug = key[len(REDIS_KEY_PREFIX) :]
                if slug:
                    await self._refresh_org(client, slug)
        finally:
            await client.aclose()
        self._sync_completed = True

    async def _refresh_org(self, client: aioredis.Redis, org_slug: str) -> None:
        key = f"{REDIS_KEY_PREFIX}{org_slug}"
        raw = await client.get(key)
        if not raw:
            self._cache.pop(org_slug, None)
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            LOG.warning("Invalid threat intel JSON for org=%s", org_slug)
            self._cache.pop(org_slug, None)
            return
        if not isinstance(payload, list):
            LOG.warning("Threat intel payload for org=%s is not a list", org_slug)
            self._cache.pop(org_slug, None)
            return
        cleaned: list[dict[str, Any]] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            indicator = str(row.get("indicator") or "").strip()
            if not indicator:
                continue
            cleaned.append(
                {
                    "threat_type": str(row.get("threat_type") or "threat_intel_match"),
                    "indicator": indicator,
                    "owasp_code": str(row.get("owasp_code") or ""),
                    "confidence": float(row.get("confidence") or 0.8),
                    "auto_block": bool(row.get("auto_block")),
                }
            )
        self._cache[org_slug] = cleaned
        LOG.debug("Threat intel cache refreshed org=%s entries=%d", org_slug, len(cleaned))

    async def _subscriber_loop(self) -> None:
        while self._running:
            client = aioredis.from_url(self._redis_url, decode_responses=True)
            pubsub = client.pubsub()
            try:
                await pubsub.subscribe(PUBSUB_CHANNEL)
                async for message in pubsub.listen():
                    if not self._running:
                        break
                    if message.get("type") != "message":
                        continue
                    try:
                        data = json.loads(message.get("data") or "{}")
                    except json.JSONDecodeError:
                        continue
                    slug = str(data.get("org_slug") or "").strip()
                    if slug:
                        await self._refresh_org(client, slug)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.warning("ThreatIntelSync subscriber error; reconnecting", exc_info=True)
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
            finally:
                try:
                    await pubsub.unsubscribe(PUBSUB_CHANNEL)
                except Exception:
                    pass
                await client.aclose()
