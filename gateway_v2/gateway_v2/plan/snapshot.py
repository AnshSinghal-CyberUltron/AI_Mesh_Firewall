"""Per-replica snapshot. One reconcile at a time. Last-known-good inside the freshness bound."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from gateway_v2.domain.locks import FRESH_MS
from gateway_v2.domain.plan import ExecutionPlan, PlanUnavailable, PlanUnknownTenant, is_newer
from gateway_v2.plan.store import PlanStore

PlanState = ExecutionPlan | PlanUnavailable | PlanUnknownTenant


class ReplicaSnapshot:
    def __init__(
        self,
        store: PlanStore,
        *,
        fresh_ms: int = FRESH_MS,
        reconcile_period_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if fresh_ms <= reconcile_period_s * 1000:
            raise ValueError("freshness bound must exceed the reconcile period")
        self._store = store
        self._fresh_s = fresh_ms / 1000
        self._clock = clock
        self._lock = threading.Lock()
        self._served: dict[str, ExecutionPlan] = {}
        self._served_at: dict[str, float] = {}
        self._pins: dict[str, ExecutionPlan] = {}

    def absorb(self, org_id: str, now: float | None = None) -> None:
        """Refresh last-known-good for ONE tenant. O(1).

        GW05c replaced a periodic `reconcile()` that iterated every known tenant — O(tenants) on
        the serving loop, once a second, 72 ms per round at 25,000 tenants (R2-02). The delta
        applier calls this for exactly the tenants whose records changed, so the cost of staying
        current is proportional to changes, not to the size of the estate.
        """
        moment = self._clock() if now is None else now
        with self._lock:
            self._absorb_locked(org_id, moment)

    def _absorb_locked(self, org_id: str, moment: float) -> None:
        state = self._store.read(org_id)
        if not isinstance(state, ExecutionPlan):
            return
        current = self._served.get(org_id)
        if current is not None and not is_newer(state, current):
            return
        self._served[org_id] = state
        self._served_at[org_id] = moment

    def lookup(self, org_id: str, now: float | None = None) -> PlanState:
        moment = self._clock() if now is None else now
        with self._lock:
            return self._lookup_locked(org_id, moment)

    def _lookup_locked(self, org_id: str, moment: float) -> PlanState:
        state = self._store.read(org_id)
        if isinstance(state, ExecutionPlan):
            self._served[org_id] = state
            self._served_at[org_id] = moment
            return state
        if isinstance(state, PlanUnknownTenant):
            return state
        cached = self._served.get(org_id)
        stamped = self._served_at.get(org_id)
        if cached is not None and stamped is not None and moment - stamped <= self._fresh_s:
            return cached
        return state

    def age_seconds(self, org_id: str, now: float | None = None) -> float | None:
        moment = self._clock() if now is None else now
        with self._lock:
            stamped = self._served_at.get(org_id)
            if stamped is None:
                return None
            return moment - stamped

    def pin(self, request_id: str, org_id: str, now: float | None = None) -> PlanState:
        moment = self._clock() if now is None else now
        with self._lock:
            state = self._lookup_locked(org_id, moment)
            if isinstance(state, ExecutionPlan):
                self._pins[request_id] = state
            return state

    def pinned(self, request_id: str) -> ExecutionPlan | None:
        with self._lock:
            return self._pins.get(request_id)
