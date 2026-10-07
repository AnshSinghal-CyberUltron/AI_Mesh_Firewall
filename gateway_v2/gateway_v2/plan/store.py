"""Durable plan records for known tenants. Missing data is not an unknown tenant."""

from __future__ import annotations

import threading

from gateway_v2.domain.plan import (
    ExecutionPlan,
    PlanUnavailable,
    PlanUnknownTenant,
    is_newer,
)


class PlanStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tenants: set[str] = set()
        self._plans: dict[str, ExecutionPlan] = {}
        self._down: set[str] = set()

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
            if org_id in self._down:
                return PlanUnavailable(org_id, "store_unreachable")
            plan = self._plans.get(org_id)
            if plan is None:
                return PlanUnavailable(org_id, "plan_missing")
            return plan
