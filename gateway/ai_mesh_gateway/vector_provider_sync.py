"""
VectorProviderSync: subscribes to Redis Pub/Sub ``vector_provider_updates`` channel
and maintains an in-memory cache of organisation vector provider configs for
dynamic credential resolution.

The cache is a dict keyed by ``{org_id}::{provider_type}`` for O(1) lookups.

Credential resolution order (gateway):
    1. Per-request user-supplied credentials
    2. Org-level VectorProviderConfig from this cache
    3. Gateway env-var defaults

DEBOUNCE FIX (March 27, 2026):
- Added 2-second debounce window to batch multiple notifications
- Only reload if actual config content changes (hash comparison)
- Prevents cascading reload loops from repeated sync notifications
"""

import asyncio
import hashlib
import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.vector_provider_sync")

REDIS_KEY_COMPILED = "vector:providers:compiled"
# RAG-16: the control plane now writes ONE KEY PER ORG so a single Redis read
# cannot yield every tenant's provider credentials (the payload carries the
# vector-DB + embedding API keys in plaintext). The legacy all-tenant key is
# still read as a FALLBACK so a new gateway keeps working against an older
# control plane mid-rollout; once both sides are deployed the legacy key is
# deleted by the writer and this fallback simply finds nothing.
REDIS_KEY_ORG_PREFIX = "vector:providers:compiled:"
PUBSUB_CHANNEL = "vector_provider_updates"
RECONNECT_DELAY_SECONDS = 5
DEBOUNCE_DELAY_SECONDS = 2  # Batch updates within 2 seconds


class VectorProviderSync:
    """
    Async Redis subscriber that keeps an in-memory copy
    of organisation vector provider configs for dynamic credential resolution.
    
    DEBOUNCE MECHANISM:
    - Multiple notifications within a 2-second window are batched
    - Content hash is compared to avoid unnecessary reloads
    - Only reloads when actual configuration changes
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url: str = redis_url
        self._cache: dict[str, dict[str, Any]] = {}
        self._subscriber_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._cache_hash: str = ""  # Track hash of current cache
        self._debounce_task: Optional[asyncio.Task] = None  # Debounce task
        self._pending_reload: bool = False  # Flag if reload requested

    def get_provider_config(self, org_id: int | str, provider_type: str) -> dict[str, Any] | None:
        """O(1) lookup of a provider config by org ID and provider type."""
        key = f"{org_id}::{provider_type}"
        cfg = self._cache.get(key)
        if cfg and cfg.get("is_active", False):
            return cfg
        return None

    def get_org_providers(self, org_id: int | str) -> list[dict[str, Any]]:
        """Return all active provider configs for an organisation."""
        prefix = f"{org_id}::"
        return [v for k, v in self._cache.items() if k.startswith(prefix) and v.get("is_active")]

    @property
    def provider_count(self) -> int:
        return len(self._cache)

    async def start(self) -> None:
        self._running = True
        await self._load_initial()
        self._subscriber_task = asyncio.create_task(self._subscriber_loop())
        LOG.info("VectorProviderSync started (providers=%d)", self.provider_count)

    async def stop(self) -> None:
        self._running = False
        if self._debounce_task:
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
        if self._subscriber_task is not None:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
        LOG.info("VectorProviderSync stopped")

    async def _load_initial(self) -> None:
        try:
            client = aioredis.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=3.0,
                socket_connect_timeout=2.0,
            )
            # RAG-16: merge the per-ORG keys into the same {org}::{type} shape the
            # cache has always used, so every consumer is unchanged.
            merged: dict = {}
            found_org_keys = False
            async for _k in client.scan_iter(match=f"{REDIS_KEY_ORG_PREFIX}*", count=200):
                _rawk = await client.get(_k)
                if not _rawk:
                    continue
                try:
                    _sub = json.loads(_rawk)
                except Exception:  # noqa: BLE001 — one bad org must not blank the cache
                    LOG.warning("Skipping unparseable vector provider key %s", _k)
                    continue
                if isinstance(_sub, dict):
                    merged.update(_sub)
                    found_org_keys = True
            raw = None if found_org_keys else await client.get(REDIS_KEY_COMPILED)
            await client.aclose()

            if found_org_keys or raw is not None:
                new_cache = merged if found_org_keys else json.loads(raw)
                # DEBOUNCE FIX: Only update if content actually changed
                new_hash = hashlib.sha256(json.dumps(new_cache, sort_keys=True).encode()).hexdigest()
                if new_hash != self._cache_hash:
                    self._cache = new_cache
                    self._cache_hash = new_hash
                    LOG.info("Loaded %d vector provider configs from Redis (updated)", self.provider_count)
                else:
                    LOG.debug("Vector provider configs unchanged, skipping update")
            else:
                LOG.info("No vector provider configs in Redis yet — using env-var defaults")
        except Exception:
            LOG.warning("Failed to load vector provider configs from Redis — using env-var defaults", exc_info=True)

    async def _subscriber_loop(self) -> None:
        while self._running:
            try:
                client = aioredis.Redis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_timeout=None,
                    socket_connect_timeout=5.0,
                    health_check_interval=30,
                )
                pubsub = client.pubsub()
                await pubsub.subscribe(PUBSUB_CHANNEL)
                LOG.info("VectorProviderSync subscribed to %s", PUBSUB_CHANNEL)

                async for message in pubsub.listen():
                    if not self._running:
                        break
                    if message["type"] != "message":
                        continue
                    
                    # DEBOUNCE FIX: Use debounce instead of immediate reload
                    LOG.debug("VectorProviderSync received update notification")
                    self._pending_reload = True
                    
                    # Cancel previous debounce task if still pending
                    if self._debounce_task and not self._debounce_task.done():
                        self._debounce_task.cancel()
                        try:
                            await self._debounce_task
                        except asyncio.CancelledError:
                            pass
                    
                    # Start new debounce window
                    self._debounce_task = asyncio.create_task(self._debounce_reload())

                await pubsub.unsubscribe(PUBSUB_CHANNEL)
                await client.aclose()
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.warning(
                    "VectorProviderSync subscriber error, reconnecting in %ds",
                    RECONNECT_DELAY_SECONDS,
                    exc_info=True,
                )
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _debounce_reload(self) -> None:
        """
        Wait for DEBOUNCE_DELAY_SECONDS, then reload if pending.
        Batches multiple notifications into a single reload.
        """
        await asyncio.sleep(DEBOUNCE_DELAY_SECONDS)
        if self._pending_reload:
            LOG.info("VectorProviderSync debounce window closed, reloading")
            await self._load_initial()
            self._pending_reload = False
        else:
            LOG.debug("VectorProviderSync debounce: no reload needed (content unchanged)")
