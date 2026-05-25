"""
VectorPolicyCompiler: compiles enabled VectorCollectionPolicy records into a
JSON bundle, stores it in Redis, and publishes change notifications on Pub/Sub.

Redis key structure:
    - vector:policies:compiled       -- Full JSON bundle keyed by project_id::collection_name
    - vector:policies:version        -- Monotonic counter for cache invalidation
    - Pub/Sub: vector_policy_updates -- Change notification channel

Follows the same pattern as policy.compiler.PolicyCompiler.
"""

import json
import logging
import time
from typing import Any

import redis
from django.conf import settings

from policy.vector_models import VectorCollectionPolicy

logger = logging.getLogger(__name__)

REDIS_KEY_COMPILED = "vector:policies:compiled"
REDIS_KEY_VERSION = "vector:policies:version"
PUBSUB_CHANNEL = "vector_policy_updates"

_redis_pool: redis.ConnectionPool | None = None


def _get_redis_pool() -> redis.ConnectionPool:
    """Return (or create) a module-level connection pool for Redis."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            max_connections=50,
            socket_timeout=3,
            socket_connect_timeout=2,
            retry_on_timeout=True,
        )
    return _redis_pool


def _get_redis_client() -> redis.Redis:
    """Return a Redis client using the module-level connection pool."""
    return redis.Redis(connection_pool=_get_redis_pool())


class VectorPolicyCompiler:
    """
    Compiles enabled VectorCollectionPolicy records into a versioned JSON
    bundle suitable for zero-latency enforcement in the Gateway Data Plane.

    The bundle is structured as a dict keyed by ``{project_id}::{collection_name}``
    for O(1) gateway lookups.
    """

    def compile_all(self) -> dict[str, Any]:
        """
        Compile all enabled vector collection policies into a bundle.

        Returns a dict with metadata and a ``policies`` dict keyed by
        ``{project_id}::{collection_name}`` containing each policy payload.
        """
        policies_qs = VectorCollectionPolicy.objects.filter(
            enabled=True,
        ).order_by("-created_at")

        compiled_policies: dict[str, dict[str, Any]] = {}
        for policy in policies_qs:
            lookup_key = f"{policy.project_id}::{policy.collection_name}"
            compiled_policies[lookup_key] = policy.build_redis_payload()

        bundle: dict[str, Any] = {
            "compiled_at": time.time(),
            "policy_count": len(compiled_policies),
            "policies": compiled_policies,
        }

        logger.info(
            "Compiled %d enabled vector collection policies into bundle",
            len(compiled_policies),
        )
        return bundle

    def push_to_redis(
        self,
        bundle: dict[str, Any] | None = None,
        *,
        trigger: str = "signal",
        changed_policy_ids: list[str] | None = None,
    ) -> bool:
        """
        Store the compiled bundle in Redis and publish a change notification.

        If no bundle is provided, compile_all() is called first.

        Steps:
        1. INCR vector:policies:version
        2. SET vector:policies:compiled (inside MULTI/EXEC pipeline)
        3. PUBLISH vector_policy_updates (inside MULTI/EXEC pipeline)

        Returns True on success, False on Redis failure.
        """
        if bundle is None:
            bundle = self.compile_all()

        try:
            client = _get_redis_client()

            new_version: int = client.incr(REDIS_KEY_VERSION)
            bundle["version"] = new_version

            serialized_bundle = json.dumps(bundle, default=str)

            notification = json.dumps(
                {
                    "event": "vector_policy_compiled",
                    "version": new_version,
                    "policy_count": bundle.get("policy_count", 0),
                    "compiled_at": bundle.get("compiled_at"),
                    "trigger": trigger,
                    "changed_policy_ids": changed_policy_ids or [],
                }
            )

            pipe = client.pipeline(transaction=True)
            pipe.set(REDIS_KEY_COMPILED, serialized_bundle)
            pipe.publish(PUBSUB_CHANNEL, notification)
            pipe.execute()

            logger.info(
                "Pushed compiled vector policies to Redis (version=%d, policies=%d, trigger=%s)",
                new_version,
                bundle.get("policy_count", 0),
                trigger,
            )
            return True

        except redis.RedisError:
            logger.exception(
                "Failed to push compiled vector policies to Redis. "
                "Gateway may serve stale vector policy data until next successful compilation."
            )
            return False

    def compile_and_push(
        self,
        *,
        trigger: str = "signal",
        changed_policy_ids: list[str] | None = None,
    ) -> bool:
        """
        Convenience method: compile all enabled vector policies and push to Redis.
        Returns True on success, False on failure.
        """
        bundle = self.compile_all()
        return self.push_to_redis(
            bundle,
            trigger=trigger,
            changed_policy_ids=changed_policy_ids,
        )
