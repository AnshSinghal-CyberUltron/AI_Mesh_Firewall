"""
Django signals for GatewayAPIKey, KillSwitch, and FirewallConfig -> Redis synchronization.

On every save/delete of a GatewayAPIKey, the identity payload is
pushed to (or removed from) Redis at key ``auth:apikey:{key_hash}``.

On every save/delete of a KillSwitch, the kill-switch payload is
pushed to (or removed from) Redis at:
- ``kill_switch:global`` for the global kill-switch
- ``kill_switch:model:{model_name}`` for per-model switches

On every save of FirewallConfig, the full gateway configuration payload
is pushed to Redis at ``firewall:config`` and a notification is published
to the ``config_updates`` Pub/Sub channel for gateway hot-reload.

This enables zero-latency lookups in the Gateway Data Plane.

Error handling: Redis failures are logged but never block the Django
save. The Control Plane must remain operational even if Redis is
temporarily unreachable.
"""

import json
import logging
from typing import Any

import redis
from ai_mesh_shared.redis_pool import connection_pool_kwargs
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from core.models import FirewallConfig, GatewayAPIKey, KillSwitch, LLMModelConfig, ModelState

logger = logging.getLogger(__name__)

REDIS_KEY_PREFIX = "auth:apikey:"

_redis_pool: redis.ConnectionPool | None = None


def _get_redis_pool() -> redis.ConnectionPool:
    """Return (or create) a module level connection pool for Redis."""
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
    """Return a Redis client instance."""
    return redis.Redis(connection_pool=_get_redis_pool())


def _build_redis_key(key_hash: str) -> str:
    """Construct the Redis key for a given API key hash."""
    return f"{REDIS_KEY_PREFIX}{key_hash}"


@receiver(pre_save, sender=GatewayAPIKey)
def ensure_gateway_apikey_organization(
    sender: type,
    instance: GatewayAPIKey,
    **kwargs: Any,
) -> None:
    """Assign tenant org from key owner when missing (prevents Module 2 org mismatch)."""
    if instance.organization_id or not instance.owner_id:
        return
    try:
        org_id = instance.owner.profile.organization_id
    except Exception:
        return
    if org_id:
        instance.organization_id = org_id


@receiver(post_save, sender=GatewayAPIKey)
def sync_gateway_apikey_to_redis(
    sender: type,
    instance: GatewayAPIKey,
    created: bool,
    **kwargs: Any,
) -> None:
    """
    Push the GatewayAPIKey identity payload to Redis on create/update.
    If the key has an 'expires_at', a Redis TTL is set so the key
    auto-expires without requiring a separate cleanup job.
    """
    # Capture values now -- the instance may be stale by the time on_commit fires
    key_hash = instance.key_hash
    prefix = instance.prefix
    payload = json.dumps(instance.build_redis_payload())
    expires_at = instance.expires_at

    def _do_sync():
        redis_key = _build_redis_key(key_hash)
        try:
            client = _get_redis_client()

            if expires_at:
                from django.utils import timezone

                ttl_seconds = int((expires_at - timezone.now()).total_seconds())
                if ttl_seconds > 0:
                    client.setex(redis_key, ttl_seconds, payload)
                    logger.info("Synced GatewayAPIKey %s to Redis with TTL %ds", prefix, ttl_seconds)
                else:
                    client.delete(redis_key)
                    logger.warning("GatewayAPIKey %s has expired, removed from Redis", prefix)
            else:
                client.set(redis_key, payload)
                logger.info("Synced GatewayAPIKey %s to Redis (no expiry)", prefix)
        except redis.RedisError:
            logger.exception(
                "Failed to sync GatewayAPIKey %s to Redis. Gateway may serve stale data until next sync.",
                prefix,
            )

    transaction.on_commit(
        _do_sync
    )  # Ensure Redis sync happens after DB transaction commits, so we don't cache data that might roll back


@receiver(post_delete, sender=GatewayAPIKey)
def delete_gateway_api_key_from_redis(
    sender: type,
    instance: GatewayAPIKey,
    **kwargs: Any,
) -> None:
    """
    Remove the GatewayAPIKey from Redis on delete.
    """
    redis_key = _build_redis_key(instance.key_hash)

    try:
        client = _get_redis_client()
        result = client.delete(redis_key)

        if result:
            logger.info("Deleted GatewayAPIKey %s from Redis", instance.prefix)
        else:
            logger.warning("GatewayAPIKey %s not found in Redis (already absent)", instance.prefix)
    except redis.RedisError:
        logger.exception("Failed to delete GatewayAPIKey %s from Redis", instance.prefix)


# ── KillSwitch Redis sync ──


@receiver(post_save, sender=KillSwitch)
def sync_kill_switch_to_redis(
    sender: type,
    instance: KillSwitch,
    created: bool,
    **kwargs: Any,
) -> None:
    """
    Push or remove the KillSwitch payload in Redis on create/update.

    Active switches are SET; inactive switches are DELETEd so the
    gateway treats absence as 'no kill-switch'.
    """
    redis_key = instance.build_redis_key()
    is_active = instance.is_active
    payload = json.dumps(instance.build_redis_payload())
    model_name = instance.model_name

    def _do_sync():
        try:
            client = _get_redis_client()
            if is_active:
                client.set(redis_key, payload)
                logger.info(
                    "KillSwitch activated for '%s' -> Redis key %s",
                    model_name,
                    redis_key,
                )
            else:
                client.delete(redis_key)
                logger.info(
                    "KillSwitch deactivated for '%s' -> removed Redis key %s",
                    model_name,
                    redis_key,
                )
        except redis.RedisError:
            logger.exception(
                "Failed to sync KillSwitch '%s' to Redis",
                model_name,
            )

    transaction.on_commit(_do_sync)


@receiver(post_delete, sender=KillSwitch)
def delete_kill_switch_from_redis(
    sender: type,
    instance: KillSwitch,
    **kwargs: Any,
) -> None:
    """Remove the KillSwitch from Redis on delete."""
    redis_key = instance.build_redis_key()
    try:
        client = _get_redis_client()
        result = client.delete(redis_key)
        if result:
            logger.info("Deleted KillSwitch '%s' from Redis", instance.model_name)
        else:
            logger.warning(
                "KillSwitch '%s' not found in Redis (already absent)",
                instance.model_name,
            )
    except redis.RedisError:
        logger.exception(
            "Failed to delete KillSwitch '%s' from Redis",
            instance.model_name,
        )


# ── ModelState Redis sync ──


@receiver(post_save, sender=ModelState)
def sync_model_state_to_redis(
    sender: type,
    instance: ModelState,
    created: bool,
    **kwargs: Any,
) -> None:
    """Push ModelState payload to Redis on create/update."""
    redis_key = instance.build_redis_key()
    payload = json.dumps(instance.build_redis_payload())
    model_name = instance.model_name

    def _do_sync():
        try:
            client = _get_redis_client()
            client.set(redis_key, payload)
            logger.info(
                "ModelState synced '%s' (status=%s, risk=%.1f) -> Redis key %s",
                model_name,
                instance.status,
                instance.risk_score,
                redis_key,
            )
        except redis.RedisError:
            logger.exception(
                "Failed to sync ModelState '%s' to Redis",
                model_name,
            )

    transaction.on_commit(_do_sync)


@receiver(post_delete, sender=ModelState)
def delete_model_state_from_redis(
    sender: type,
    instance: ModelState,
    **kwargs: Any,
) -> None:
    """Remove ModelState from Redis on delete."""
    redis_key = instance.build_redis_key()
    try:
        client = _get_redis_client()
        client.delete(redis_key)
        logger.info("Deleted ModelState '%s' from Redis", instance.model_name)
    except redis.RedisError:
        logger.exception(
            "Failed to delete ModelState '%s' from Redis",
            instance.model_name,
        )


# -- FirewallConfig Redis sync --

CONFIG_UPDATES_CHANNEL = "config_updates"


@receiver(post_save, sender=FirewallConfig)
def sync_firewall_config_to_redis(
    sender: type,
    instance: FirewallConfig,
    created: bool,
    **kwargs: Any,
) -> None:
    """
    Push the full FirewallConfig gateway payload to Redis and publish
    a notification to the ``config_updates`` Pub/Sub channel.

    The gateway ConfigSync subscriber re-fetches the config from Redis
    on each notification, enabling hot-reload without restart.
    """
    redis_key = instance.build_redis_key()
    gw_payload = instance.build_gateway_payload()
    org_slug = instance.organization.slug if instance.organization_id else "default"
    gw_payload["org_slug"] = org_slug
    payload = json.dumps(gw_payload)

    def _do_sync():
        try:
            client = _get_redis_client()
            client.set(redis_key, payload)
            client.publish(CONFIG_UPDATES_CHANNEL, json.dumps({"action": "reload", "org_slug": org_slug}))
            logger.info(
                "FirewallConfig synced to Redis (%s) and published to %s",
                redis_key,
                CONFIG_UPDATES_CHANNEL,
            )
        except redis.RedisError:
            logger.exception(
                "Failed to sync FirewallConfig to Redis. Gateway will use previous config until next successful sync."
            )

    transaction.on_commit(_do_sync)


# -- LLMModelConfig Redis sync --


def _sync_all_llm_models(instance: LLMModelConfig | None = None) -> None:
    """
    Serialize all active LLMModelConfig entries and push to Redis.

    Per-org key pattern: ``llm:model_configs:{org_slug}``
    Also stores routing metadata per model for dynamic routing.
    """
    try:
        from auth.models import Organization

        client = _get_redis_client()

        if instance and instance.organization_id:
            orgs = [instance.organization]
        else:
            org_ids = LLMModelConfig.objects.filter(is_active=True).values_list("organization_id", flat=True).distinct()
            orgs = list(Organization.objects.filter(id__in=[oid for oid in org_ids if oid]))
            # Skip NULL-org models — they are legacy and should not be synced.
            # Previously this mapped None→"default" causing key collision with real default org.

        for org in orgs:
            slug = org.slug
            redis_key = f"llm:model_configs:{slug}"
            qs = LLMModelConfig.objects.filter(is_active=True, organization=org)
            entries = [m.build_litellm_entry() for m in qs]
            routing_entries = [m.build_routing_payload() for m in qs]
            from core.routing_fallback import build_compliant_fallback_chains

            fallback_chains = build_compliant_fallback_chains(routing_entries)
            payload = json.dumps({
                "models": entries,
                "routing": routing_entries,
                "fallback_chains": fallback_chains,
            })
            client.set(redis_key, payload)
            logger.info("LLMModelConfig synced to Redis (%s): %d models", redis_key, len(entries))

        client.publish(CONFIG_UPDATES_CHANNEL, json.dumps({"action": "model_reload"}))
    except redis.RedisError:
        logger.exception("Failed to sync LLMModelConfig to Redis.")


@receiver(post_save, sender=LLMModelConfig)
def sync_llm_model_config_to_redis(
    sender: type,
    instance: LLMModelConfig,
    created: bool,
    **kwargs: Any,
) -> None:
    """Push all active LLM model configs to Redis on create/update."""
    captured = instance
    transaction.on_commit(lambda: _sync_all_llm_models(captured))


@receiver(post_delete, sender=LLMModelConfig)
def delete_llm_model_config_from_redis(
    sender: type,
    instance: LLMModelConfig,
    **kwargs: Any,
) -> None:
    """Re-sync all active LLM model configs to Redis on delete."""
    transaction.on_commit(lambda: _sync_all_llm_models(instance))
