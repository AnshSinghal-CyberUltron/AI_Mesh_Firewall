"""
Celery tasks for asynchronous policy compilation.

The compile_policies_task is scheduled by compiler_signals.py with a
debounce delay (default 2 seconds) to batch rapid-fire Policy/Rule
changes into a single compilation.

Auto-discovered by celery_app.autodiscover_tasks().
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

PENDING_CHANGES_KEY = "policies:pending_changes"


@shared_task(name="policy.compile_policies")
def compile_policies_task(trigger: str = "signal") -> bool:
    """
    Compile all enabled policies and push the bundle to Redis.

    Clears the debounce lock before compiling so that new changes
    arriving while compilation is in progress can schedule a fresh task.

    Reads and clears the pending changes list to include affected
    policy IDs in the Pub/Sub notification.
    """
    from policy.compiler import PolicyCompiler, _get_redis_client

    changed_policy_ids: list[int] = []
    try:
        client = _get_redis_client()
        client.delete("policies:recompile_pending")

        raw_ids = client.lrange(PENDING_CHANGES_KEY, 0, -1)
        client.delete(PENDING_CHANGES_KEY)
        changed_policy_ids = list({int(pid) for pid in raw_ids if pid})
    except Exception:
        logger.warning("Could not clear recompile_pending key or read pending changes from Redis")

    compiler = PolicyCompiler()
    success = compiler.compile_and_push(
        trigger=trigger,
        changed_policy_ids=changed_policy_ids,
    )

    if success:
        logger.info(
            "Policy compilation task completed successfully (changed_ids=%s)",
            changed_policy_ids,
        )
    else:
        logger.error("Policy compilation task failed (Redis push unsuccessful)")

    return success


VECTOR_PENDING_CHANGES_KEY = "vector:pending_changes"


@shared_task(name="policy.compile_vector_policies")
def compile_vector_policies_task(trigger: str = "signal") -> bool:
    """
    Compile all enabled vector collection policies and push the bundle to Redis.

    Clears the debounce lock before compiling so that new changes arriving
    while compilation is in progress can schedule a fresh task.

    Reads and clears the pending changes list to include affected policy IDs
    in the Pub/Sub notification.
    """
    from policy.vector_compiler import VectorPolicyCompiler, _get_redis_client

    changed_policy_ids: list[str] = []
    try:
        client = _get_redis_client()
        client.delete("vector:recompile_pending")

        raw_ids = client.lrange(VECTOR_PENDING_CHANGES_KEY, 0, -1)
        client.delete(VECTOR_PENDING_CHANGES_KEY)
        changed_policy_ids = list({pid for pid in raw_ids if pid})
    except Exception:
        logger.warning("Could not clear vector recompile_pending key or read pending changes from Redis")

    compiler = VectorPolicyCompiler()
    success = compiler.compile_and_push(
        trigger=trigger,
        changed_policy_ids=changed_policy_ids,
    )

    if success:
        logger.info(
            "Vector policy compilation task completed successfully (changed_ids=%s)",
            changed_policy_ids,
        )
    else:
        logger.error("Vector policy compilation task failed (Redis push unsuccessful)")

    return success
