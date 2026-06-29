"""Worker task package.

Every task module is imported here so that importing ``ai_mesh_workers.tasks``
registers all ``@shared_task`` handlers with the Celery app. Without these
imports the handlers are never loaded and tasks published by the control plane
(e.g. ``policy.compile_policies``) are dropped with
"Received unregistered task ... has been ignored and discarded".
"""

from . import isolation, mcp, policy, telemetry, tier2  # noqa: F401

__all__ = ["isolation", "mcp", "policy", "telemetry", "tier2"]
