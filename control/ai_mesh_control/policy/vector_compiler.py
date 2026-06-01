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

        Atomicity (mirrors Bundle C1 fix in policy/compiler.py):
        Wrap version-bump + bundle-set + publish in a single WATCH/MULTI/EXEC
        transaction. Previously ``incr`` ran standalone BEFORE the pipeline
        that wrote the bundle, so two concurrent compiles could interleave
        and pair a high version (e.g. v=2) with a stale bundle's bytes.
        Gateways then cached the wrong policies under a "newer" version
        forever. The WATCH on ``version_key`` causes redis-py to auto-retry
        this callable if anyone else bumps the version between WATCH and
        EXEC, guaranteeing every (version, bundle) pair stored is
        consistent and every published notification carries the version
        that matches the bytes at ``REDIS_KEY_COMPILED``.

        Returns True on success, False on Redis failure.
        """
        if bundle is None:
            bundle = self.compile_all()

        try:
            client = _get_redis_client()

            tx_state: dict[str, Any] = {"version": None, "policy_count": 0}

            def _atomic_publish(pipe: "redis.client.Pipeline") -> None:
                current_raw = pipe.get(REDIS_KEY_VERSION)
                try:
                    current = int(current_raw) if current_raw is not None else 0
                except (TypeError, ValueError):
                    current = 0
                new_version = current + 1

                # Mutate the bundle on every retry so the serialized payload
                # always carries the version we are about to commit.
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

                pipe.multi()
                pipe.set(REDIS_KEY_VERSION, new_version)
                pipe.set(REDIS_KEY_COMPILED, serialized_bundle)
                pipe.publish(PUBSUB_CHANNEL, notification)

                tx_state["version"] = new_version
                tx_state["policy_count"] = bundle.get("policy_count", 0)

            client.transaction(_atomic_publish, REDIS_KEY_VERSION)

            logger.info(
                "Pushed compiled vector policies to Redis (version=%d, policies=%d, trigger=%s)",
                tx_state["version"],
                tx_state["policy_count"],
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
