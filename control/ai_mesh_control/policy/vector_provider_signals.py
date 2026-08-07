"""
Django signals for VectorProviderConfig -> Redis synchronization.

On every save/delete of a VectorProviderConfig, the config is pushed to Redis
for zero-latency gateway credential resolution.

Redis key structure:
    - vector:provider:{org_id}:{provider_type}  -- Individual provider config
    - vector:providers:compiled                  -- Full bundle of all active configs
    - Pub/Sub: vector_provider_updates           -- Change notification channel
"""

import json
import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from policy.vector_provider_models import VectorProviderConfig

logger = logging.getLogger(__name__)

REDIS_KEY_PROVIDERS_COMPILED = "vector:providers:compiled"
# RAG-16 (2026-08-06): provider payloads contain the org's vector-DB AND embedding
# API keys in PLAINTEXT (EncryptedCharField decrypts on attribute access, so
# build_redis_payload materialises the secret). Writing every active org into ONE
# global key meant a single GET — from any Redis client, a replica, an RDB/AOF
# snapshot, a backup, or a MONITOR capture — yielded EVERY tenant's provider
# credentials at once, and those keys grant provider-side read/write/delete
# entirely OUTSIDE the firewall. Splitting per org means one read is no longer a
# full-tenant compromise. (This reduces blast radius; it does NOT encrypt — that
# needs the Fernet key distributed to the gateway, tracked separately.)
REDIS_KEY_PROVIDERS_ORG_PREFIX = "vector:providers:compiled:"
PUBSUB_CHANNEL = "vector_provider_updates"


def _sync_provider_to_redis(instance: VectorProviderConfig, deleted: bool = False) -> None:
    """Push provider config to Redis after transaction commits."""

    def _do_sync() -> None:
        try:
            from policy.vector_compiler import _get_redis_client

            client = _get_redis_client()

            # Compile all active provider configs into a bundle
            configs = VectorProviderConfig.objects.filter(is_active=True)
            bundle = {}
            for cfg in configs:
                key = f"{cfg.organization_id}::{cfg.provider_type}"
                bundle[key] = cfg.build_redis_payload()

            # Per-ORG keys (RAG-16). Group the compiled entries by organisation
            # and write one key each, so a single read exposes at most one tenant.
            by_org: dict = {}
            for _k, _payload in bundle.items():
                _org = str(_k).split("::", 1)[0]
                by_org.setdefault(_org, {})[_k] = _payload
            for _org, _sub in by_org.items():
                client.set(f"{REDIS_KEY_PROVIDERS_ORG_PREFIX}{_org}", json.dumps(_sub))
            # Drop any org key that no longer has an active provider, so a removed
            # org's credentials do not linger in Redis indefinitely.
            try:
                for _stale in client.scan_iter(match=f"{REDIS_KEY_PROVIDERS_ORG_PREFIX}*", count=200):
                    _sk = _stale.decode() if isinstance(_stale, bytes) else str(_stale)
                    if _sk.rsplit(":", 1)[-1] not in by_org:
                        client.delete(_sk)
            except Exception:  # noqa: BLE001 — cleanup must not block the sync
                logger.warning("vector provider per-org key cleanup failed", exc_info=True)
            # RAG-16: remove the legacy ALL-TENANT key. Leaving it in place would
            # keep the blast radius exactly as it was.
            client.delete(REDIS_KEY_PROVIDERS_COMPILED)

            # Publish change notification
            notification = {
                "provider_type": instance.provider_type,
                "organization_id": instance.organization_id,
                "action": "deleted" if deleted else "updated",
            }
            client.publish(PUBSUB_CHANNEL, json.dumps(notification))

            logger.info(
                "Synced %d vector provider configs to Redis (trigger: %s %s)",
                len(bundle),
                "delete" if deleted else "save",
                instance,
            )
        except Exception:
            logger.exception("Failed to sync vector provider config to Redis")

    transaction.on_commit(_do_sync)


@receiver(post_save, sender=VectorProviderConfig)
def vector_provider_post_save(sender, instance, **kwargs) -> None:
    _sync_provider_to_redis(instance, deleted=False)


@receiver(post_delete, sender=VectorProviderConfig)
def vector_provider_post_delete(sender, instance, **kwargs) -> None:
    _sync_provider_to_redis(instance, deleted=True)
