"""
VectorPolicySync: subscribes to Redis Pub/Sub ``vector_policy_updates`` channel
and maintains an in-memory cache of compiled vector collection policies for
zero-latency enforcement in the Gateway.

The cache is a dict keyed by ``{project_id}::{collection_name}`` for O(1) lookups.

Follows the same lifecycle as gateway/policy_sync.py (PolicySync).
"""

import asyncio
import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.vector_policy_sync")

REDIS_KEY_COMPILED = "vector:policies:compiled"
REDIS_KEY_VERSION = "vector:policies:version"
PUBSUB_CHANNEL = "vector_policy_updates"

RECONNECT_DELAY_SECONDS = 5


class VectorPolicySync:
    """
    Async Redis Pub/Sub subscriber that keeps an in-memory copy
    of the compiled vector policy bundle for zero-latency enforcement.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url: str = redis_url
        self._cache: Optional[dict[str, Any]] = None
        self._version: int = 0
        self._subscriber_task: Optional[asyncio.Task] = None
        self._running: bool = False

    @property
    def policies(self) -> dict[str, dict[str, Any]]:
        """Return the dict of compiled vector policies keyed by project_id::collection_name."""
        if self._cache is None:
            return {}
        return self._cache.get("policies", {})

    @property
    def version(self) -> int:
        """Return the current cached bundle version."""
        return self._version

    @property
    def is_loaded(self) -> bool:
        """Return True if the cache has been populated at least once."""
        return self._cache is not None

    @property
    def policy_count(self) -> int:
        """Return the number of vector policies in the cache."""
        if self._cache is None:
            return 0
        return self._cache.get("policy_count", 0)

    def get_policy(self, project_id: str, collection_name: str) -> dict[str, Any] | None:
        """O(1) lookup of a vector collection policy by project and collection name."""
        key = f"{project_id}::{collection_name}"
        return self.policies.get(key)

    def get_project_collections(self, project_id: str) -> list[str]:
        """Return all collection names accessible to a project."""
        prefix = f"{project_id}::"
        return [k.split("::", 1)[1] for k in self.policies if k.startswith(prefix)]

    async def start(self) -> None:
        """
        Initialize the cache and start the background subscriber.

        1. Load the current bundle from Redis (cold start)
        2. Spawn the subscriber loop as an asyncio background task
        """
        self._running = True
        await self._load_initial_bundle()
        self._subscriber_task = asyncio.create_task(self._subscriber_loop())
        LOG.info(
            "VectorPolicySync started (version=%d, policies=%d)",
            self._version,
            self.policy_count,
        )

    async def stop(self) -> None:
        """Stop the background subscriber gracefully."""
        self._running = False
        if self._subscriber_task is not None:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
        LOG.info("VectorPolicySync stopped")

    async def _load_initial_bundle(self) -> None:
        """Load the compiled vector policy bundle from Redis on startup."""
        try:
            client = aioredis.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=3.0,
                socket_connect_timeout=2.0,
            )
            raw_bundle = await client.get(REDIS_KEY_COMPILED)
            await client.aclose()

            if raw_bundle is not None:
                bundle = json.loads(raw_bundle)
                self._cache = bundle
                self._version = bundle.get("version", 0)
                LOG.info(
                    "Loaded initial vector policy bundle from Redis (version=%d, policies=%d)",
                    self._version,
                    self.policy_count,
                )
            else:
                LOG.warning(
                    "No compiled vector policy bundle found in Redis. "
                    "Gateway will deny all RAG queries until backend compiles vector policies."
                )
        except Exception:
            LOG.warning(
                "Failed to load initial vector policy bundle from Redis. "
                "Will retry when Pub/Sub connects.",
                exc_info=True,
            )

    async def _subscriber_loop(self) -> None:
        """
        Continuously subscribe to the vector_policy_updates Pub/Sub channel.
        Reconnects automatically on connection loss.
        """
        while self._running:
            client = None
            pubsub = None
            try:
                client = aioredis.Redis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_timeout=None,
                    socket_connect_timeout=3.0,
                )
                pubsub = client.pubsub()
                await pubsub.subscribe(PUBSUB_CHANNEL)
                LOG.info("VectorPolicySync subscribed to Pub/Sub channel '%s'", PUBSUB_CHANNEL)

                async for message in pubsub.listen():
                    if not self._running:
                        break

                    if message["type"] != "message":
                        continue

                    try:
                        notification = json.loads(message["data"])
                    except (json.JSONDecodeError, TypeError):
                        LOG.warning("Received invalid JSON on vector_policy_updates channel")
                        continue

                    incoming_version = notification.get("version", 0)
                    if incoming_version <= self._version:
                        LOG.debug(
                            "Skipping stale vector policy notification (incoming=%d, current=%d)",
                            incoming_version,
                            self._version,
                        )
                        continue

                    LOG.info(
                        "Vector policy update notification (version=%d, trigger=%s, changed=%s)",
                        incoming_version,
                        notification.get("trigger", "unknown"),
                        notification.get("changed_policy_ids", []),
                    )

                    await self._refresh_cache(client)

            except asyncio.CancelledError:
                break
            except Exception:
                LOG.warning(
                    "VectorPolicySync subscriber disconnected, reconnecting in %ds",
                    RECONNECT_DELAY_SECONDS,
                    exc_info=True,
                )
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(PUBSUB_CHANNEL)
                        await pubsub.aclose()
                    except Exception:
                        pass
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        pass

    async def _refresh_cache(self, client: aioredis.Redis) -> None:
        """Fetch the latest compiled vector policy bundle from Redis and swap the cache."""
        try:
            raw_bundle = await client.get(REDIS_KEY_COMPILED)
            if raw_bundle is None:
                LOG.warning("vector:policies:compiled key missing during refresh")
                return

            bundle = json.loads(raw_bundle)
            new_version = bundle.get("version", 0)

            self._cache = bundle
            self._version = new_version

            LOG.info(
                "Vector policy cache refreshed (version=%d, policies=%d)",
                new_version,
                self.policy_count,
            )
        except Exception:
            LOG.exception("Failed to refresh vector policy cache from Redis")
