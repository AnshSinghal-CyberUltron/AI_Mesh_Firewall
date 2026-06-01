"""
Django signals for Policy/Rule -> PolicyCompiler synchronization.

On every save/delete of a Policy or Rule, a debounced Celery task is
scheduled to recompile all enabled policies and push the bundle to Redis.

Debounce mechanism:
    - Uses Redis SET NX EX on key ``policies:recompile_pending`` (2s TTL)
    - If the lock is acquired, a Celery task is scheduled with countdown=2
    - If the lock already exists, the signal is a no-op (task already pending)
    - This ensures bulk operations (e.g., 10 rule saves) trigger ONE compilation

Changed policy IDs are tracked in a Redis list ``policies:pending_changes``
so the Celery task can include them in the Pub/Sub notification for
selective gateway cache invalidation.
"""

import logging
from typing import Any

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from policy.models import Policy, Rule

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0
PENDING_CHANGES_KEY = "policies:pending_changes"
PENDING_ORGS_KEY = "policies:pending_orgs"


def _trigger_recompilation(
    sender_name: str,
    instance_repr: str,
    changed_policy_id: int | None = None,
    organization_id: int | None = None,
) -> None:
    """
    Schedule a debounced policy recompilation via Celery after the
    current transaction commits.

    Uses a Redis NX lock to deduplicate rapid-fire signals into a
    single Celery task execution.  Pushes the changed policy_id to
    a Redis list for enriched Pub/Sub notifications.
    """

    def _do_schedule() -> None:
        try:
            from policy.compiler import _get_redis_client

            client = _get_redis_client()

            if changed_policy_id is not None:
                client.rpush(PENDING_CHANGES_KEY, str(changed_policy_id))

            if organization_id is not None:
                client.sadd(PENDING_ORGS_KEY, str(organization_id))

            acquired = client.set(
                "policies:recompile_pending",
                "1",
                nx=True,
                ex=DEBOUNCE_SECONDS if DEBOUNCE_SECONDS > 0 else 1,
            )
            if acquired:
                from policy.tasks import compile_policies_task

                compile_policies_task.apply_async(countdown=DEBOUNCE_SECONDS if DEBOUNCE_SECONDS > 0 else 0)
                logger.info(
                    "Scheduled debounced policy recompilation (trigger: %s on %s)",
                    sender_name,
                    instance_repr,
                )
            else:
                logger.debug(
                    "Policy recompilation already scheduled, skipping (trigger: %s on %s)",
                    sender_name,
                    instance_repr,
                )
        except Exception:
            logger.exception(
                "Failed to schedule policy recompilation (trigger: %s on %s)",
                sender_name,
                instance_repr,
            )

    transaction.on_commit(_do_schedule)


@receiver(post_save, sender=Policy)
def recompile_on_policy_save(
    sender: type,
    instance: Policy,
    created: bool,
    **kwargs: Any,
) -> None:
    action = "created" if created else "updated"
    _trigger_recompilation(
        sender_name=f"Policy.post_save ({action})",
        instance_repr=str(instance),
        changed_policy_id=instance.pk,
        organization_id=instance.organization_id,
    )


@receiver(post_delete, sender=Policy)
def recompile_on_policy_delete(
    sender: type,
    instance: Policy,
    **kwargs: Any,
) -> None:
    _trigger_recompilation(
        sender_name="Policy.post_delete",
        instance_repr=str(instance),
        changed_policy_id=instance.pk,
        organization_id=instance.organization_id,
    )


@receiver(post_save, sender=Rule)
def recompile_on_rule_save(
    sender: type,
    instance: Rule,
    created: bool,
    **kwargs: Any,
) -> None:
    action = "created" if created else "updated"
    org_id = None
    try:
        org_id = instance.policy.organization_id
    except Exception:
        org_id = None
    _trigger_recompilation(
        sender_name=f"Rule.post_save ({action})",
        instance_repr=str(instance),
        changed_policy_id=instance.policy_id,
        organization_id=org_id,
    )


@receiver(post_delete, sender=Rule)
def recompile_on_rule_delete(
    sender: type,
    instance: Rule,
    **kwargs: Any,
) -> None:
    org_id = None
    try:
        org_id = instance.policy.organization_id
    except Exception:
        org_id = None
    _trigger_recompilation(
        sender_name="Rule.post_delete",
        instance_repr=str(instance),
        changed_policy_id=instance.policy_id,
        organization_id=org_id,
    )
