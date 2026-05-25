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

            client.set(REDIS_KEY_PROVIDERS_COMPILED, json.dumps(bundle))

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
