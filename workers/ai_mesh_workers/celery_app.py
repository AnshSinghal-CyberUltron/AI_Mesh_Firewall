"""Celery application — full Django wiring after task modules are migrated."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")

# Explicitly include every worker task module so its @shared_task handlers are
# registered at startup. The previous call — autodiscover_tasks(["ai_mesh_workers.tasks"])
# — asked Celery to import the non-existent submodule ai_mesh_workers.tasks.tasks
# (autodiscover's default related_name is "tasks"), so it registered NOTHING and
# published tasks (policy.compile_policies, policy.compile_vector_policies,
# tier2.*, mcp.*, isolation.*) were discarded as "unregistered".
WORKER_TASK_MODULES = [
    "ai_mesh_workers.tasks.policy",
    "ai_mesh_workers.tasks.telemetry",
    "ai_mesh_workers.tasks.mcp",
    "ai_mesh_workers.tasks.tier2",
    "ai_mesh_workers.tasks.isolation",
]

app = Celery("ai_mesh_workers", include=WORKER_TASK_MODULES)
app.config_from_object("django.conf:settings", namespace="CELERY")

import django

django.setup()
# Import the package (whose __init__ imports every submodule) as a belt-and-suspenders
# guarantee that handlers register even if `include` import timing changes.
app.autodiscover_tasks(["ai_mesh_workers"])

app.conf.beat_schedule = {
    "scan-model-risk-scores": {
        "task": "isolation.scan_model_risk_scores",
        "schedule": 60.0,
    },
    "expire-model-states": {
        "task": "isolation.expire_model_states",
        "schedule": 60.0,
    },
    # Drain the gateway:jobs queue (async envelopes incl. vector_ingest) and
    # dispatch the routed handler tasks. This beat is the ONLY scheduler in the
    # stack (workers-beat); assigning app.conf.beat_schedule REPLACES the
    # settings CELERY_BEAT_SCHEDULE, so the gateway-jobs drainer must be listed
    # here explicitly or async RAG ingest never persists.
    "process-gateway-jobs": {
        "task": "core.tasks.process_gateway_jobs_batch",
        "schedule": 2.0,
    },
    # Periodically reconcile active gateway API keys into Redis so a Redis flush
    # / container recycle cannot leave the data plane returning 401 for every
    # /v1/* request until each key is re-saved.
    "resync-gateway-keys": {
        "task": "core.tasks.resync_gateway_keys",
        "schedule": float(os.environ.get("GATEWAY_KEY_RESYNC_INTERVAL_SEC", "300")),
    },
    # B2 DEFENSE: re-push routing state (models/allowlist/isolation) so a bulk
    # QuerySet.update() (no post_save signal) cannot leave the gateway routing on
    # stale config. Same pattern as resync-gateway-keys, for the routing plane.
    "reconcile-routing-state": {
        "task": "core.tasks.reconcile_routing_state",
        "schedule": float(os.environ.get("ROUTING_RECONCILE_INTERVAL_SEC", "120")),
    },
    "refresh-analytics-rollups": {
        "task": "policy.refresh_analytics_rollups",
        "schedule": float(os.environ.get("ANALYTICS_ROLLUP_REFRESH_SEC", "900")),
    },
}


from celery.signals import worker_ready as _worker_ready


@_worker_ready.connect
def _resync_gateway_keys_on_boot(**kwargs):
    """On worker startup, immediately reconcile gateway keys into Redis (recovers
    the data plane after a Redis/worker recycle without waiting for the first
    periodic beat run)."""
    try:
        from core.signals import resync_all_gateway_keys

        resync_all_gateway_keys()
    except Exception:  # noqa: BLE001 - never block worker boot on the reconcile
        import logging

        logging.getLogger(__name__).exception("gateway-key boot resync failed")
