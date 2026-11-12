"""Durable plan records for known tenants. Missing data is not an unknown tenant."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from gateway_v2.domain.plan import (
    ExecutionPlan,
    PlanUnavailable,
    PlanUnknownTenant,
    is_newer,
)

PLAN_QUARANTINED = "plan_quarantined"
"""`PlanUnavailable.reason` for a tenant whose newest published record failed verification."""

REJECTED = "rejected"
QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class HeldBack:
    """Why this replica is not serving a tenant's newest published record. No record content.

    `state` is `REJECTED` (the record verified but did not compile: the previous valid plan keeps
    serving, GW05's "never fail to an empty plan") or `QUARANTINED` (the record failed
    verification: the tenant is refused until a verified record replaces it). `error` is the
    exception TYPE, never its text.
    """

    state: str
    feed_seq: int
    error: str


class PlanStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tenants: set[str] = set()
        self._plans: dict[str, ExecutionPlan] = {}
        self._down: set[str] = set()
        self._held: dict[str, HeldBack] = {}
        """One entry per tenant at most, removed by the next record that applies: O(held)."""

    def register_tenant(self, org_id: str) -> None:
        with self._lock:
            self._tenants.add(org_id)

    def put(self, plan: ExecutionPlan) -> None:
        with self._lock:
            self._tenants.add(plan.org_id)
            current = self._plans.get(plan.org_id)
            if current is not None and not is_newer(plan, current):
                raise ValueError("plan version regressed")
            self._plans[plan.org_id] = plan
            self._down.discard(plan.org_id)
            self._held.pop(plan.org_id, None)

    def reject(self, org_id: str, *, feed_seq: int, error: str) -> None:
        """A verified record that did not compile. The tenant stays known and keeps its plan.

        It also lifts a quarantine: the newest record is now a VERIFIED one, so what the tenant
        is served is decided by GW05's rejection rule (previous valid plan, or PLAN_UNAVAILABLE
        when there is none), not by an integrity fault an older record had.
        """
        with self._lock:
            self._tenants.add(org_id)
            self._held[org_id] = HeldBack(REJECTED, feed_seq, error)

    def quarantine(self, org_id: str, *, feed_seq: int, error: str) -> None:
        """The tenant's newest record failed verification: refuse it, keep it known.

        Fail CLOSED, not last-good. A record that does not verify is not what the control plane
        wrote, and serving the plan it was meant to replace would let anyone who can write the
        store pin a tenant to a superseded policy -- C36's regress, one tenant at a time. The
        compiled plan is kept (not served) so that a later verified record can still be ordered
        against it.
        """
        with self._lock:
            self._tenants.add(org_id)
            self._held[org_id] = HeldBack(QUARANTINED, feed_seq, error)

    def clear_held(self, org_id: str) -> None:
        """A verified record that changes nothing (already served): nothing is held back now."""
        with self._lock:
            self._held.pop(org_id, None)

    def held_back(self, org_id: str) -> HeldBack | None:
        with self._lock:
            return self._held.get(org_id)

    def current(self, org_id: str) -> ExecutionPlan | None:
        """The compiled plan held for ordering, whether or not it may be served right now.

        The applier orders a new record against THIS, not against `read`: a quarantined or
        unreachable tenant still holds the version a newer record must beat, and `put` refuses
        a regress against it.
        """
        with self._lock:
            return self._plans.get(org_id)

    def mark_unreachable(self, org_id: str) -> None:
        with self._lock:
            self._down.add(org_id)

    def restore(self, org_id: str) -> None:
        with self._lock:
            self._down.discard(org_id)

    def forget_plan(self, org_id: str) -> None:
        """Drop the compiled plan. The tenant stays known, so it reads PLAN_UNAVAILABLE.

        This is what a store flush looks like. It must never make a tenant UNKNOWN: in v2.1 a
        flush plus re-seed wedged tenants at 403 "complete onboarding" forever.
        """
        with self._lock:
            self._plans.pop(org_id, None)

    def offboard(self, org_id: str) -> None:
        """Forget the tenant entirely, so it reads PLAN_UNKNOWN_TENANT.

        Only an explicit signed OFF record may do this. Deliberately a different method from
        `forget_plan`, because the two look identical in a diff and mean opposite things.
        """
        with self._lock:
            self._tenants.discard(org_id)
            self._plans.pop(org_id, None)
            self._down.discard(org_id)
            self._held.pop(org_id, None)

    def known(self) -> tuple[str, ...]:
        """Every known tenant. O(tenants log tenants) — diagnostics and tests only.

        Never call this from a serving path or a periodic refresh: doing so is exactly the
        R2-02 defect (§2.1 rule 1, no work proportional to the tenant count on a serving loop).
        """
        with self._lock:
            return tuple(sorted(self._tenants))

    def read(self, org_id: str) -> ExecutionPlan | PlanUnavailable | PlanUnknownTenant:
        with self._lock:
            if org_id not in self._tenants:
                return PlanUnknownTenant(org_id)
            held = self._held.get(org_id)
            if held is not None and held.state == QUARANTINED:
                return PlanUnavailable(org_id, PLAN_QUARANTINED)
            if org_id in self._down:
                return PlanUnavailable(org_id, "store_unreachable")
            plan = self._plans.get(org_id)
            if plan is None:
                return PlanUnavailable(org_id, "plan_missing")
            return plan
