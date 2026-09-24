"""Per-worker immutable plan snapshot: load at startup, pub/sub push, periodic reconcile.

The snapshot is a dict swapped atomically (copy-on-write); requests pin one
ExecutionPlan value. Three states stay distinct: a plan, PLAN_UNAVAILABLE (store
stale past the ceiling or plan failed validation), PLAN_UNKNOWN_TENANT.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

import orjson
import redis.asyncio as aioredis

from rvproto.domain.plan import ExecutionPlan, PlanLookup, PlanUnavailable, PlanUnknownTenant
from rvproto.plan.compiler import PlanInvalid, compile_plan
from rvproto.runtime.metrics import Registry
from rvproto.runtime.store import K_PLAN_CHANNEL, K_PLAN_PREFIX, K_PLAN_VERSIONS, StoreError


def _log_error(event: str, exc: BaseException) -> None:
    sys.stderr.write(json.dumps({"event": event, "t": time.time(), "error": repr(exc)}) + "\n")


class PlanSnapshot:
    def __init__(self, r: aioredis.Redis, metrics: Registry, *, reconcile_ms: float,
                 stale_ms: float) -> None:
        self.r = r
        self.metrics = metrics
        self.reconcile_s = reconcile_ms / 1000.0
        self.stale_ns = int(stale_ms * 1e6)
        self._plans: dict[str, ExecutionPlan] = {}
        self._invalid: dict[str, str] = {}
        self._versions: dict[str, str] = {}
        self._last_ok_ns = 0
        self.loaded = False

    def versions(self) -> dict[str, str]:
        return {org: p.version for org, p in self._plans.items()}

    def age_s(self) -> float:
        return (time.perf_counter_ns() - self._last_ok_ns) / 1e9

    def fresh(self) -> bool:
        return self.loaded and time.perf_counter_ns() - self._last_ok_ns <= self.stale_ns

    def get(self, org_id: str) -> PlanLookup:
        if time.perf_counter_ns() - self._last_ok_ns > self.stale_ns:
            return PlanUnavailable(org_id, "plan snapshot older than the staleness ceiling")
        plan = self._plans.get(org_id)
        if plan is not None:
            return plan
        if org_id in self._invalid:
            return PlanUnavailable(org_id, self._invalid[org_id])
        return PlanUnknownTenant(org_id)

    async def load_all(self) -> None:
        versions = await self.r.hgetall(K_PLAN_VERSIONS)
        await self._apply({k.decode(): v.decode() for k, v in versions.items()})
        self.loaded = True

    async def _apply(self, versions: dict[str, str]) -> None:
        changed = [o for o, v in versions.items() if self._versions.get(o) != v]
        plans = dict(self._plans)
        invalid = dict(self._invalid)
        for org in changed:
            raw = await self.r.get(K_PLAN_PREFIX + org)
            try:
                if raw is None:
                    raise PlanInvalid("plan document missing")
                doc: dict[str, Any] = orjson.loads(raw)
                plan = compile_plan(doc)
            except (PlanInvalid, ValueError, KeyError) as exc:
                # the previous valid version keeps serving; never fail to an empty plan
                if org not in plans:
                    invalid[org] = f"plan failed validation: {exc}"
                self.metrics.inc("plan_invalid")
                continue
            plans[org] = plan
            invalid.pop(org, None)
            self._versions[org] = plan.version
            self.metrics.inc("plan_reloads")
        for org in [o for o in plans if o not in versions]:
            del plans[org]
        self._plans = plans
        self._invalid = invalid
        self._last_ok_ns = time.perf_counter_ns()

    async def reconcile_once(self) -> None:
        versions = await self.r.hgetall(K_PLAN_VERSIONS)
        await self._apply({k.decode(): v.decode() for k, v in versions.items()})

    async def run(self) -> None:
        """Push (pub/sub) + periodic reconcile; failures only age the snapshot."""
        listener = asyncio.create_task(self._listen())
        try:
            while True:
                try:
                    await self.reconcile_once()
                except StoreError as exc:
                    self.metrics.inc("plan_reconcile_errors")
                    _log_error("plan_reconcile_error", exc)
                await asyncio.sleep(self.reconcile_s)
        finally:
            listener.cancel()

    async def _listen(self) -> None:
        while True:
            try:
                ps = self.r.pubsub()
                await ps.subscribe(K_PLAN_CHANNEL)
                async for msg in ps.listen():
                    if msg.get("type") == "message":
                        await self.reconcile_once()
                        self.metrics.inc("plan_push_applied")
            except StoreError as exc:
                self.metrics.inc("plan_listen_errors")
                _log_error("plan_listen_error", exc)
                await asyncio.sleep(self.reconcile_s)
