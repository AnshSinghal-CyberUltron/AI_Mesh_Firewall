"""What plan version each replica has loaded.

The report is the local half of the applied-version API. A replica whose
snapshot is past the freshness bound does not report a version.

`held_back` is how the console is told that a replica is NOT serving the tenant's newest
published record, and why (GW05: "the previous valid version keeps serving and the tenant is
told"): `rejected` -- the record verified but did not compile, so the reported version is the
previous valid one -- or `quarantined` -- the record failed verification, so no version is
served. It carries the record's feed position and the exception type, never record content.
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
    held = snapshot.held_back(org_id)
    held_back = (
        None if held is None
        else {"state": held.state, "feed_seq": held.feed_seq, "error": held.error}
    )
    if not isinstance(state, ExecutionPlan):
        return {"replica_id": replica_id, "version": None, "fresh": False, "held_back": held_back}
    age = snapshot.refresh_age_seconds(org_id, now)
    fresh = age is not None and age <= fresh_s
    return {
        "replica_id": replica_id, "version": state.version, "fresh": fresh, "held_back": held_back,
    }
