"""What plan version each replica has loaded.

The report is the local half of the applied-version API. A replica whose
snapshot is past the freshness bound does not report a version.
"""

from __future__ import annotations

from typing import Any

from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.plan.snapshot import ReplicaSnapshot


def report_applied(
    *,
    replica_id: str,
    snapshot: ReplicaSnapshot,
    org_id: str,
    now: float,
    fresh_s: float,
) -> dict[str, Any]:
    state = snapshot.lookup(org_id, now)
    if not isinstance(state, ExecutionPlan):
        return {"replica_id": replica_id, "version": None, "fresh": False}
    age = snapshot.refresh_age_seconds(org_id, now)
    fresh = age is not None and age <= fresh_s
    return {"replica_id": replica_id, "version": state.version, "fresh": fresh}
