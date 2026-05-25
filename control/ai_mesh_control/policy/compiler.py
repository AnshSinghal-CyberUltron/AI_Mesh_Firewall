"""
PolicyCompiler: compiles enabled policies and rules into a JSON bundle,
stores it in Redis, and publishes change notifications on Pub/Sub.

Redis key structure:
    - policies:compiled       -- Full JSON bundle of all enabled policies + rules
    - policies:version        -- Monotonic counter for cache invalidation
    - Pub/Sub: policy_updates -- Change notification channel

This module follows the same Redis connection pattern established in
core.signals (module-level ConnectionPool, error logging without blocking).
"""

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import redis
from django.conf import settings

if TYPE_CHECKING:
    from django.db.models import QuerySet

from policy.models import Policy
from policy.signing import sign_bundle

logger = logging.getLogger(__name__)

REDIS_KEY_COMPILED = "policies:compiled"
REDIS_KEY_VERSION = "policies:version"
PUBSUB_CHANNEL = "policy_updates"

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


class PolicyCompiler:
    """
    Compiles enabled Policy + Rule records into a versioned JSON bundle
    suitable for zero-latency enforcement in the Gateway Data Plane.
    """

    def compile_single(self, policy_id: int) -> dict[str, Any] | None:
        """
        Compile a single policy by primary key.
        Returns the policy snapshot dict, or None if not found / disabled.
        """
        try:
            policy = Policy.objects.filter(pk=policy_id, enabled=True).prefetch_related("rules").first()
        except Exception:
            logger.exception("Failed to query policy id=%s", policy_id)
            return None
        if policy is None:
            return None
        return self._build_snapshot(policy)

    def compile_all(self, organization=None) -> dict[str, Any]:
        """
        Compile all enabled policies with their enabled rules into a
        full bundle suitable for gateway consumption.

        If organization is provided, only include policies bound to that org.
        """
        if organization is not None:
            policies_qs: QuerySet[Policy] = (
                Policy.objects.filter(
                    enabled=True,
                    organization=organization,
                ).select_related("mcp_server").prefetch_related("rules").order_by("-priority", "code")
            )
        else:
            policies_qs: QuerySet[Policy] = (  # type: ignore[no-redef]
                Policy.objects.filter(enabled=True).select_related("mcp_server").prefetch_related("rules").order_by("-priority", "code")
            )

        compiled_policies: list[dict[str, Any]] = []
        for policy in policies_qs:
            snapshot = self._build_snapshot(policy)
            compiled_policies.append(snapshot)

        bundle: dict[str, Any] = {
            "compiled_at": time.time(),
            "policy_count": len(compiled_policies),
            "policies": compiled_policies,
        }

        logger.info(
            "Compiled %d enabled policies into bundle",
            len(compiled_policies),
        )
        return bundle

    def push_to_redis(
        self,
        bundle: dict[str, Any] | None = None,
        *,
        trigger: str = "signal",
        changed_policy_ids: list[int] | None = None,
        organization=None,
    ) -> bool:
        """
        Store the compiled bundle in Redis and publish a change notification.

        If no bundle is provided, compile_all() is called first.
        Uses org-scoped Redis keys when organization is provided.

        Returns True on success, False on Redis failure.
        """
        if bundle is None:
            bundle = self.compile_all(organization=organization)

        org_slug = organization.slug if organization else "default"
        redis_key = f"{REDIS_KEY_COMPILED}:{org_slug}"
        version_key = f"{REDIS_KEY_VERSION}:{org_slug}"

        try:
            client = _get_redis_client()

            new_version: int = client.incr(version_key)
            bundle["version"] = new_version

            # HMAC-sign the bundle BEFORE serialising so the gateway can
            # reject any tampered copy in Redis. Signing covers every field
            # of the bundle except `_sig` itself.
            try:
                sign_bundle(bundle)
            except RuntimeError:
                logger.exception(
                    "Refusing to push unsigned policy bundle "
                    "(POLICY_SIGNING_KEY / DJANGO_SECRET_KEY missing)"
                )
                return False

            serialized_bundle = json.dumps(bundle, default=str)

            notification = json.dumps(
                {
                    "event": "policy_compiled",
                    "version": new_version,
                    "policy_count": bundle.get("policy_count", 0),
                    "compiled_at": bundle.get("compiled_at"),
                    "trigger": trigger,
                    "changed_policy_ids": changed_policy_ids or [],
                    "org_slug": org_slug,
                }
            )

            pipe = client.pipeline(transaction=True)
            pipe.set(redis_key, serialized_bundle)
            pipe.publish(PUBSUB_CHANNEL, notification)
            pipe.execute()

            logger.info(
                "Pushed compiled policies to Redis (%s, version=%d, policies=%d, trigger=%s)",
                redis_key,
                new_version,
                bundle.get("policy_count", 0),
                trigger,
            )
            return True

        except redis.RedisError:
            logger.exception(
                "Failed to push compiled policies to Redis. "
                "Gateway may serve stale policy data until next successful compilation."
            )
            return False

    def compile_and_push(
        self,
        *,
        trigger: str = "signal",
        changed_policy_ids: list[int] | None = None,
        organization=None,
    ) -> bool:
        """
        Convenience method: compile all enabled policies and push to Redis.
        Returns True on success, False on failure.
        """
        bundle = self.compile_all(organization=organization)
        return self.push_to_redis(
            bundle,
            trigger=trigger,
            changed_policy_ids=changed_policy_ids,
            organization=organization,
        )

    @staticmethod
    def _build_snapshot(policy: Policy) -> dict[str, Any]:
        """
        Build a policy snapshot dict for gateway consumption.

        Unlike PolicyViewSet._build_policy_snapshot(), this method
        includes only **enabled** rules, since the compiled bundle is
        consumed by the Gateway for enforcement, not for admin display.
        """
        enabled_rules = list(
            policy.rules.filter(enabled=True)
            .order_by("-priority", "id")
            .values(
                "id",
                "name",
                "rule_type",
                "condition",
                "action",
                "redaction_config",
                "priority",
                "enabled",
                "description",
                "pipeline_stage",
                "target_tool",
            )
        )
        mcp_server_slug = None
        if policy.mcp_server_id:
            mcp_server_slug = policy.mcp_server.server_slug if policy.mcp_server else None
        return {
            "policy": {
                "id": policy.id,
                "name": policy.name,
                "code": policy.code,
                "category": policy.category,
                "severity": policy.severity,
                "description": policy.description,
                "enabled": policy.enabled,
                "priority": policy.priority,
                "metadata": policy.metadata,
                "version": policy.version,
                "policy_domain": policy.policy_domain,
                "mcp_server_slug": mcp_server_slug,
            },
            "rules": enabled_rules,
        }
