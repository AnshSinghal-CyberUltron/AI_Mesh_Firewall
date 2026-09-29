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
        """Drop the compiled plan. The tenant stays known."""
        with self._lock:
            self._plans.pop(org_id, None)

    def known(self) -> tuple[str, ...]:
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
