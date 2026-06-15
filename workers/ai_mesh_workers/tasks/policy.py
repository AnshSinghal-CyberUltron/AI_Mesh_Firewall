"""Worker registration of the policy-compile Celery tasks.

SINGLE SOURCE OF TRUTH: the canonical implementations live in the control
plane's ``policy.tasks`` module. Importing the ``@shared_task`` objects here
registers them with the worker's Celery app under their task names
(``policy.compile_policies`` / ``policy.compile_vector_policies``).

Why a re-export instead of a copy:
    This file previously held a DIVERGENT COPY of those tasks. Because the
    worker process (not the control web process) is what actually executes
    them, the stale copy's logic ran in production: it called
    ``compile_and_push()`` with no organization, so it only recompiled the
    global ``policies:compiled:default`` bundle and never the changed org's
    ``policies:compiled:{slug}`` bundle. The gateway reads strictly per-org
    bundles, so per-org policy edits never reached enforcement. The canonical
    control-side task already recompiles each affected org; re-exporting it
    keeps the two in lockstep and prevents this drift from recurring.
"""

from policy.tasks import (  # noqa: F401  (re-exported so Celery registers them)
    compile_policies_task,
    compile_vector_policies_task,
)

__all__ = ["compile_policies_task", "compile_vector_policies_task"]
