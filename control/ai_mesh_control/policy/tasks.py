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
PENDING_ORGS_KEY = "policies:pending_orgs"


@shared_task(name="policy.compile_policies")
def compile_policies_task(trigger: str = "signal") -> bool:
    """
    Compile enabled policies per affected organization and push org-scoped bundles to Redis.

    Clears the debounce lock before compiling so that new changes
    arriving while compilation is in progress can schedule a fresh task.
    """
    from auth.models import Organization
    from policy.compiler import PolicyCompiler, _get_redis_client

    changed_policy_ids: list[int] = []
    org_ids: set[int] = set()
    try:
        client = _get_redis_client()
        client.delete("policies:recompile_pending")

        raw_ids = client.lrange(PENDING_CHANGES_KEY, 0, -1)
        client.delete(PENDING_CHANGES_KEY)
        changed_policy_ids = list({int(pid) for pid in raw_ids if pid})

        raw_org_ids = client.smembers(PENDING_ORGS_KEY)
        client.delete(PENDING_ORGS_KEY)
        for raw in raw_org_ids or []:
            try:
                org_ids.add(int(raw))
            except (TypeError, ValueError):
                continue
    except Exception:
        logger.warning("Could not clear recompile_pending key or read pending changes from Redis")

    if not org_ids and changed_policy_ids:
        from policy.models import Policy

        org_ids = set(
            Policy.objects.filter(pk__in=changed_policy_ids)
            .exclude(organization_id__isnull=True)
            .values_list("organization_id", flat=True)
        )

    compiler = PolicyCompiler()
    if not org_ids:
        success = compiler.compile_and_push(
            trigger=trigger,
            changed_policy_ids=changed_policy_ids,
        )
    else:
        success = True
        for org_id in sorted(org_ids):
            org = Organization.objects.filter(pk=org_id).first()
            if not org:
                continue
            ok = compiler.compile_and_push(
                trigger=trigger,
                changed_policy_ids=changed_policy_ids,
                organization=org,
            )
            success = success and ok

    if success:
        logger.info(
            "Policy compilation task completed successfully (orgs=%s, changed_ids=%s)",
            sorted(org_ids),
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


@shared_task(name="policy.refresh_analytics_rollups")
def refresh_analytics_rollups_task(hours: int | None = None) -> dict:
    """Rebuild hourly analytics facts. Redis-locked; no-op if another runner holds the lock."""
    from policy.analytics_rollup_refresh import try_refresh_all_locked

    return try_refresh_all_locked(hours=hours)
