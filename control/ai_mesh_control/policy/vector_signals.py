"""
Django signals for VectorCollectionPolicy -> Redis synchronization.

On every save/delete of a VectorCollectionPolicy, a debounced Celery task
is scheduled to recompile all enabled vector policies and push the bundle
to Redis.

Debounce mechanism:
    - Uses Redis SET NX EX on key ``vector:recompile_pending`` (1s TTL)
    - If the lock is acquired, a Celery task is scheduled
    - If the lock already exists, the signal is a no-op (task already pending)
    - This ensures bulk operations trigger ONE compilation

Changed policy IDs are tracked in a Redis list ``vector:pending_changes``
so the Celery task can include them in the Pub/Sub notification.
"""

import logging
from typing import Any

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from policy.vector_models import VectorCollectionPolicy

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0
PENDING_CHANGES_KEY = "vector:pending_changes"


def _trigger_vector_recompilation(
    sender_name: str,
    instance_repr: str,
    changed_policy_id: str | None = None,
) -> None:
    """
    Schedule a debounced vector policy recompilation via Celery after the
    current transaction commits.
    """

    def _do_schedule() -> None:
        try:
            from policy.vector_compiler import _get_redis_client

            client = _get_redis_client()

            if changed_policy_id is not None:
                client.rpush(PENDING_CHANGES_KEY, str(changed_policy_id))

            acquired = client.set(
                "vector:recompile_pending",
                "1",
                nx=True,
                ex=DEBOUNCE_SECONDS if DEBOUNCE_SECONDS > 0 else 1,
            )
            if acquired:
                from policy.tasks import compile_vector_policies_task

                compile_vector_policies_task.apply_async(
                    countdown=DEBOUNCE_SECONDS if DEBOUNCE_SECONDS > 0 else 0,
                )
                logger.info(
                    "Scheduled debounced vector policy recompilation (trigger: %s on %s)",
                    sender_name,
                    instance_repr,
                )
            else:
                logger.debug(
                    "Vector policy recompilation already scheduled, skipping (trigger: %s on %s)",
                    sender_name,
                    instance_repr,
                )
        except Exception:
            logger.exception(
                "Failed to schedule vector policy recompilation (trigger: %s on %s)",
                sender_name,
                instance_repr,
            )

    transaction.on_commit(_do_schedule)


@receiver(post_save, sender=VectorCollectionPolicy)
def recompile_on_vector_policy_save(
    sender: type,
    instance: VectorCollectionPolicy,
    created: bool,
    **kwargs: Any,
) -> None:
    action = "created" if created else "updated"
    _trigger_vector_recompilation(
        sender_name=f"VectorCollectionPolicy.post_save ({action})",
        instance_repr=str(instance),
        changed_policy_id=str(instance.pk),
    )


@receiver(post_delete, sender=VectorCollectionPolicy)
def recompile_on_vector_policy_delete(
    sender: type,
    instance: VectorCollectionPolicy,
    **kwargs: Any,
) -> None:
    _trigger_vector_recompilation(
        sender_name="VectorCollectionPolicy.post_delete",
        instance_repr=str(instance),
        changed_policy_id=str(instance.pk),
    )
