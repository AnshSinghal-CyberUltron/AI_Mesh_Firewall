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
import threading
import time
from typing import TYPE_CHECKING, Any

import redis
from ai_mesh_shared.redis_pool import connection_pool_kwargs
from django.conf import settings

if TYPE_CHECKING:
    from django.db.models import QuerySet

from policy.models import Policy
from policy.signing import sign_bundle

logger = logging.getLogger(__name__)

REDIS_KEY_COMPILED = "policies:compiled"
REDIS_KEY_VERSION = "policies:version"
PUBSUB_CHANNEL = "policy_updates"

# HIGH (compile-to-Redis stale-content-wins race): the bundle's ``compiled_at``
# is the monotonic freshness stamp the gateway already orders on
# (policy_sync._is_newer_notification). ``time.time()`` is wall-clock and can
# regress under NTP/leap adjustments, which would let an *older* snapshot
# masquerade as newer. A process-local monotonic floor guarantees that two
# compiles produced by the same process always carry strictly increasing
# stamps even if the wall clock steps backward between them. The Redis-side
# guard in push_to_redis() enforces the same invariant *across* processes /
# celery workers by refusing to overwrite a stored bundle whose ``compiled_at``
# is newer than the one being pushed.
_COMPILED_AT_LOCK = threading.Lock()
_LAST_COMPILED_AT = 0.0

# Per-org compile serialization lock (see compile_and_push). Held just long
# enough to make compile_all()+push_to_redis() atomic w.r.t. other compilers
# of the same org, so a slow compile of a *stale* snapshot can no longer
# interleave its push between a fresh compile and the fresh push.
_COMPILE_LOCK_KEY = "policies:compile_lock:{org}"
_COMPILE_LOCK_TTL_S = 30


class _StaleBundleSkip(Exception):
    """Internal sentinel: abort the WATCH/MULTI/EXEC without writing because a
    strictly-newer bundle is already stored. Never propagates outside
    push_to_redis()."""


def _coerce_float(val: Any) -> float | None:
    """Best-effort float coercion; None on anything non-numeric. Mirrors the
    gateway's defensive normalization so a malformed stored ``compiled_at``
    can never raise inside the transaction (which would drop the write path)."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _extract_compiled_at(stored_raw: Any) -> Any:
    """Pull ``compiled_at`` out of a serialized bundle read back from Redis.
    Returns None if the value is missing or the blob is unparseable — in which
    case the caller treats freshness as unknown and allows the write (the new
    bundle is at least as fresh as an unreadable one)."""
    if not isinstance(stored_raw, (str, bytes, bytearray)):
        return None
    try:
        parsed = json.loads(stored_raw)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict):
        return parsed.get("compiled_at")
    return None


def _next_compiled_at() -> float:
    """Return a wall-clock-based ``compiled_at`` that never regresses within
    this process. Strictly increasing across calls so co-resident compiles get
    a deterministic ordering even under clock skew."""
    global _LAST_COMPILED_AT
    with _COMPILED_AT_LOCK:
        now = time.time()
        if now <= _LAST_COMPILED_AT:
            # Smallest representable advance keeps the float a wall-clock-ish
            # value the gateway can still compare with ``>``.
            now = _LAST_COMPILED_AT + 1e-6
        _LAST_COMPILED_AT = now
        return now

# R2: keys inside a rule's condition/redaction_config that name a *target model*
# the gateway will route inference to (e.g. a model_downgrade rule sets
# ``redaction_config["downgrade_to"]`` which gateway main.py reads as the new
# ``body["model"]``). The reserved platform/guard model ("zeroshield-model" +
# legacy 120b aliases) must NEVER be emitted as such a target — it is internal
# ML only, never an org inference destination. A policy/rule that names it is
# scrubbed at compile time so the gateway never receives the reserved model as
# a routing/downgrade candidate via the policy bundle.
_MODEL_TARGET_KEYS = ("downgrade_to", "target_model", "model", "route_to", "reroute_to")


def _scrub_reserved_model_targets(mapping: Any) -> Any:
    """Drop reserved platform-model names from any model-target key in a rule's
    condition / redaction_config dict. Returns the mapping unchanged when no
    reserved name is present. Defensive + cheap: only inspects the known
    model-target keys, never touches regex/keyword/redaction content."""
    if not isinstance(mapping, dict):
        return mapping
    try:
        from core.models import is_platform_managed_llm_model_name
    except Exception:  # pragma: no cover - import safety; never block a compile
        return mapping

    cleaned = None
    for key in _MODEL_TARGET_KEYS:
        val = mapping.get(key)
        if isinstance(val, str) and is_platform_managed_llm_model_name(val):
            if cleaned is None:
                cleaned = dict(mapping)
            # Remove the reserved target entirely; the gateway falls back to the
            # org default model when ``downgrade_to`` is absent.
            cleaned.pop(key, None)
            logger.warning(
                "Scrubbed reserved platform model from compiled rule field %r", key
            )
    return cleaned if cleaned is not None else mapping

# M-04: bundle FORMAT/schema version (distinct from the monotonic Redis
# "version" cache-invalidation counter). Bumped to 2 when actor-scoping
# fields (allowed_user_ids/allowed_agent_ids/allowed_roles/redaction_fields)
# were added to the compiled policy snapshot. Consumers may read this to
# detect capability; absence (old bundle) implies schema 1 = no actor scoping.
BUNDLE_SCHEMA_VERSION = 2

_redis_pool: redis.ConnectionPool | None = None


def _get_redis_pool() -> redis.ConnectionPool:
    """Return (or create) a module-level connection pool for Redis."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            **connection_pool_kwargs(
                max_connections=50,
                socket_timeout=3,
                socket_connect_timeout=2,
                retry_on_timeout=True,
            ),
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
            "compiled_at": _next_compiled_at(),
            # M-04: advertise the bundle format so gateways can detect that
            # actor-scoping fields are present. Additive — old gateways ignore it.
            "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
            "policy_count": len(compiled_policies),
            "rule_count": sum(len(p.get("rules", [])) for p in compiled_policies),
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

            # C1 race fix: previously `incr` ran as a standalone command
            # BEFORE the pipeline that wrote the bundle. Two concurrent
            # compiles could interleave so the higher version (e.g. v=2)
            # ended up paired with the older bundle content. Gateways then
            # cached stale rules under a "newer" version forever.
            #
            # Wrap version-bump + bundle-set + publish in a WATCH/MULTI/EXEC
            # transaction. If anyone else writes to version_key between WATCH
            # and EXEC, redis-py auto-retries this callable until success.
            # Result: every (version, bundle) pair stored is consistent and
            # the publish notification carries the version that actually
            # matches the bytes at redis_key.
            tx_state: dict[str, Any] = {
                "version": None,
                "policy_count": 0,
                "skipped_stale": False,
            }

            # HIGH stale-content-wins fix: the bundle being pushed carries a
            # monotonic ``compiled_at`` stamped at compile time. The freshness
            # authority is that stamp, NOT the blind version counter. Resolve
            # it once outside the (possibly-retried) transaction body.
            incoming_compiled_at = _coerce_float(bundle.get("compiled_at"))

            def _atomic_publish(pipe: "redis.client.Pipeline") -> None:
                # WATCH covers both keys: a concurrent push that advances the
                # version OR replaces the bundle content forces a retry, so the
                # staleness comparison below is always made against the bundle
                # that is actually committed.
                current_raw = pipe.get(version_key)
                try:
                    current = int(current_raw) if current_raw is not None else 0
                except (TypeError, ValueError):
                    current = 0

                # Refuse to let an OLDER snapshot win. If a bundle is already
                # stored and its ``compiled_at`` is strictly NEWER than the one
                # we are about to push, abandon the write entirely (no version
                # bump, no set, no publish) so a just-disabled/loosened policy
                # cannot be resurrected by a slow, stale compile that lands
                # last. Only advance to a strictly-newer snapshot.
                stored_raw = pipe.get(redis_key)
                if stored_raw is not None and incoming_compiled_at is not None:
                    stored_compiled_at = _coerce_float(
                        _extract_compiled_at(stored_raw)
                    )
                    if (
                        stored_compiled_at is not None
                        and stored_compiled_at >= incoming_compiled_at
                    ):
                        # Abandon the write entirely: leave the fresher stored
                        # bundle in place (no version bump, no SET, no publish).
                        # Raise a sentinel so the pipeline context manager's
                        # reset() issues UNWATCH and releases the connection
                        # cleanly — robust across redis-py versions, unlike
                        # poking at empty-MULTI/watching internals.
                        tx_state["skipped_stale"] = True
                        tx_state["version"] = current
                        raise _StaleBundleSkip

                new_version = current + 1

                # Bundle is mutated each retry so the signed payload always
                # reflects the version we are about to commit.
                bundle["version"] = new_version
                try:
                    sign_bundle(bundle)
                except RuntimeError:
                    # Surface via outer exception path; raising here aborts
                    # the WATCH (no EXEC issued) so no partial write occurs.
                    raise

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

                pipe.multi()
                pipe.set(version_key, new_version)
                pipe.set(redis_key, serialized_bundle)
                pipe.publish(PUBSUB_CHANNEL, notification)

                tx_state["version"] = new_version
                tx_state["policy_count"] = bundle.get("policy_count", 0)

            try:
                client.transaction(_atomic_publish, version_key)
            except _StaleBundleSkip:
                # A strictly-newer bundle is already stored; we deliberately
                # did NOT overwrite it. This is a successful no-op (the fresher
                # snapshot wins), not a failure.
                logger.info(
                    "Skipped pushing stale policy bundle to Redis (%s, "
                    "incoming compiled_at=%s is not newer than stored, trigger=%s)",
                    redis_key,
                    incoming_compiled_at,
                    trigger,
                )
                return True
            except RuntimeError:
                logger.exception(
                    "Refusing to push unsigned policy bundle "
                    "(POLICY_SIGNING_KEY / DJANGO_SECRET_KEY missing)"
                )
                return False

            new_version = tx_state["version"]

            logger.info(
                "Pushed compiled policies to Redis (%s, version=%s, policies=%d, trigger=%s)",
                redis_key,
                new_version,
                tx_state["policy_count"],
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

        HIGH stale-content-wins fix: compile_all() + push_to_redis() are wrapped
        in a per-org Redis lock so concurrent compiles of the SAME org (celery
        concurrency 2, PolicyCompileView, apps startup all share this entry
        point) serialize instead of interleaving a fresh compile with a slow,
        stale push. If the lock can't be acquired (another compile is already
        running), we still compile+push: the push-side ``compiled_at`` guard is
        the hard correctness backstop, so the lock is a contention optimizer,
        not the sole safety mechanism — we never silently skip a legitimate
        recompile just because the lock is busy or Redis is briefly unavailable.
        """
        org_slug = organization.slug if organization else "default"
        lock_key = _COMPILE_LOCK_KEY.format(org=org_slug)
        lock_token = f"{id(self)}:{time.time()}"
        have_lock = False
        client = None
        try:
            client = _get_redis_client()
            # SET key token NX EX <ttl>: atomic acquire with auto-expiry so a
            # crashed holder can't wedge compiles for this org forever.
            have_lock = bool(
                client.set(lock_key, lock_token, nx=True, ex=_COMPILE_LOCK_TTL_S)
            )
        except redis.RedisError:
            # Lock acquisition is best-effort; fall through and rely on the
            # push-side compiled_at ordering guarantee.
            logger.warning(
                "Could not acquire per-org policy compile lock for %s; "
                "proceeding (push-side freshness guard still applies)",
                org_slug,
            )

        try:
            bundle = self.compile_all(organization=organization)
            return self.push_to_redis(
                bundle,
                trigger=trigger,
                changed_policy_ids=changed_policy_ids,
                organization=organization,
            )
        finally:
            if have_lock and client is not None:
                # Release only if we still own the token (defensive against a
                # TTL-expired lock having been re-acquired by another compile).
                try:
                    self._release_compile_lock(client, lock_key, lock_token)
                except redis.RedisError:
                    logger.warning(
                        "Failed to release per-org policy compile lock for %s "
                        "(it will expire via TTL)",
                        org_slug,
                    )

    @staticmethod
    def _release_compile_lock(client: "redis.Redis", lock_key: str, token: str) -> None:
        """Compare-and-delete the compile lock so we never delete a lock now
        owned by a different compiler (token mismatch after TTL expiry)."""
        lua = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        client.eval(lua, 1, lock_key, token)

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
        # R2: never let a rule emit the reserved platform/guard model as a
        # routing/downgrade target into the gateway-consumed bundle.
        for rule in enabled_rules:
            rule["condition"] = _scrub_reserved_model_targets(rule.get("condition"))
            rule["redaction_config"] = _scrub_reserved_model_targets(
                rule.get("redaction_config")
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
                # M-04: actor-scoping allowlists + response field redaction.
                # These were defined on the model + serializers but never
                # serialized into the compiled bundle, so enforcement was a
                # silent no-op. Emit them additively (default [] = applies to
                # everyone). `or []` guards against legacy NULL column values.
                "allowed_user_ids": list(policy.allowed_user_ids or []),
                "allowed_agent_ids": list(policy.allowed_agent_ids or []),
                "allowed_roles": list(policy.allowed_roles or []),
                "redaction_fields": list(policy.redaction_fields or []),
            },
            "rules": enabled_rules,
        }
