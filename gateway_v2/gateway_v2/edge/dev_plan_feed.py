"""Reload a published FirewallConfig plan without a request-path store read.

The boot file stays the plan until a document appears at ``PUBLISHED_PLAN_KEY``.
A body the compiler rejects leaves the previous plan in place. The poll runs
on the dev lifespan, off the chat path.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import decode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore

PUBLISHED_PLAN_KEY = "{rv2}:dev:firewall-plan"
_POLL_S = 1.0


class DevPlanFeed:
    """The snapshot the chat path reads, plus one apply for a published body."""

    def __init__(self, plan: ExecutionPlan, *, now: float) -> None:
        self._org_id = plan.org_id
        self._store = PlanStore()
        self._store.put(plan)
        self._snapshot = ReplicaSnapshot(self._store)
        self._snapshot.absorb(plan.org_id, now=now)
        self._raw: bytes | None = None

    def source(self) -> ReplicaSnapshot:
        return self._snapshot

    def apply(self, raw: bytes, *, compiled_at: float, now: float) -> str:
        """``unchanged``, ``rejected``, or the version now served."""
        if raw == self._raw:
            return "unchanged"
        try:
            plan = self._compile(raw, compiled_at)
        except CompileError:
            return "rejected"
        self._store.put(plan)
        self._snapshot.absorb(plan.org_id, now=now)
        self._raw = raw
        return plan.version

    def _compile(self, raw: bytes, compiled_at: float) -> ExecutionPlan:
        document = decode_plan_body(raw)
        if document.org_id != self._org_id:
            raise CompileError("published plan org does not match the dev org")
        current = self._store.read(self._org_id)
        previous = current if isinstance(current, ExecutionPlan) else None
        return compile_plan(
            document.org_id,
            document.drafts,
            streaming_mode=document.streaming_mode,
            previous=previous,
            compiled_at=compiled_at,
        )


class DevPlanWatch:
    """Poll the published key and apply a changed body. One task per worker."""

    def __init__(self, *, redis_url: str, feed: DevPlanFeed, timeout_s: float) -> None:
        self._redis_url = redis_url
        self._feed = feed
        self._timeout_s = timeout_s
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._noted: bytes | None = None

    async def startup(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def shutdown(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    async def _run(self) -> None:
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.Redis.from_url(
            self._redis_url,
            socket_timeout=self._timeout_s,
            socket_connect_timeout=self._timeout_s,
        )
        try:
            while not self._stop.is_set():
                await self._once(client)
                try:
                    await asyncio.wait_for(self._stop.wait(), _POLL_S)
                except TimeoutError:
                    continue
        finally:
            await client.aclose()

    async def _once(self, client: Any) -> None:
        try:
            raw = await client.get(PUBLISHED_PLAN_KEY)
        except Exception as exc:  # noqa: BLE001 — a poll failure keeps the served plan.
            self._note(f"dev: firewall plan poll failed ({type(exc).__name__})\n", b"")
            return
        blob = raw.encode("utf-8") if isinstance(raw, str) else raw
        if not isinstance(blob, bytes) or not blob:
            return
        outcome = self._feed.apply(blob, compiled_at=time.time(), now=time.monotonic())
        if outcome == "unchanged":
            return
        if outcome == "rejected":
            self._note("dev: firewall plan rejected; previous plan still served\n", blob)
            return
        self._note(f"dev: firewall plan applied version={outcome}\n", blob)

    def _note(self, line: str, blob: bytes) -> None:
        if blob == self._noted:
            return
        self._noted = blob
        sys.stdout.write(line)
        sys.stdout.flush()
