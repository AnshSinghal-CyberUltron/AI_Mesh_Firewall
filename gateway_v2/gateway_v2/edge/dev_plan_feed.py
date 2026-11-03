"""Reload a published FirewallConfig plan without a request-path store read.

The boot file stays the plan until a document appears at ``PUBLISHED_PLAN_KEY``.
A body the compiler rejects leaves the previous plan in place. The poll runs
on the dev lifespan, off the chat path.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import Any

from gateway_v2.detect.conditions import ConditionProgram, compile_conditions
from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import decode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore

PUBLISHED_PLAN_KEY = "{rv2}:dev:firewall-plan"
_POLL_S = 1.0


def plan_key(org_id: str) -> str:
    """Per-org dev plan. The dev key is used only when this key is absent.

    Not ``{rv2}:plan:{org}``: that is the signed PLAN record the production feed reads (GW36 S2),
    and one key must never hold two formats.
    """
    return f"{{rv2}}:devplan:{org_id}"


def choose_published(org_blob: bytes | None, dev_blob: bytes | None) -> bytes | None:
    """Prefer the org key. An empty org value falls through to the dev key."""
    org_raw = _as_bytes(org_blob)
    if org_raw:
        return org_raw
    return _as_bytes(dev_blob)


def poll_bodies(
    primary: str,
    peers: Sequence[str],
    fetched: Mapping[str, bytes | None],
    dev: bytes | None,
) -> tuple[tuple[str, bytes | None], ...]:
    """One body per org. Peers never receive the dev-key fallback."""
    chosen = [(primary, choose_published(fetched.get(plan_key(primary)), dev))]
    for org in peers:
        chosen.append((org, choose_published(fetched.get(plan_key(org)), None)))
    return tuple(chosen)


#: The org whose customer program the detector scans. Unset means the primary org.
request_org: ContextVar[str | None] = ContextVar("gw26_request_org", default=None)


def _as_bytes(blob: bytes | str | None) -> bytes | None:
    if isinstance(blob, str):
        blob = blob.encode("utf-8")
    if isinstance(blob, bytes) and blob:
        return blob
    return None


class DevPlanFeed:
    """The snapshot the chat path reads, plus one apply for a published body."""

    def __init__(
        self,
        plan: ExecutionPlan,
        *,
        now: float,
        peer_orgs: tuple[str, ...] = (),
    ) -> None:
        self._org_id = plan.org_id
        self._peers = tuple(org for org in peer_orgs if org != plan.org_id)
        self._store = PlanStore()
        self._store.put(plan)
        self._snapshot = ReplicaSnapshot(self._store)
        self._snapshot.absorb(plan.org_id, now=now)
        self._raw: dict[str, bytes] = {}
        self._programs: dict[str, ConditionProgram] = {plan.org_id: compile_conditions(plan.rules)}
        for org in self._peers:
            self._programs[org] = ConditionProgram(())

    def conditions(self) -> ConditionProgram:
        """The customer matcher for the request org. Rebuilt only on apply.

        An unset request org is the primary plan. A peer with no applied plan
        returns an empty program, never the primary one.
        """
        org = request_org.get()
        if org is None:
            org = self._org_id
        return self._programs.get(org, ConditionProgram(()))

    def source(self) -> ReplicaSnapshot:
        return self._snapshot

    @property
    def org_id(self) -> str:
        return self._org_id

    @property
    def peer_orgs(self) -> tuple[str, ...]:
        return self._peers

    def apply(
        self,
        raw: bytes,
        *,
        compiled_at: float,
        now: float,
        org_id: str | None = None,
    ) -> str:
        """``unchanged``, ``rejected``, or the version now served for ``org_id``."""
        target = self._org_id if org_id is None else org_id
        if raw == self._raw.get(target):
            return "unchanged"
        try:
            plan = self._compile(raw, compiled_at, target)
        except CompileError:
            return "rejected"
        self._store.put(plan)
        self._snapshot.absorb(plan.org_id, now=now)
        self._programs[target] = compile_conditions(plan.rules)
        self._raw[target] = raw
        return plan.version

    def _compile(self, raw: bytes, compiled_at: float, org_id: str) -> ExecutionPlan:
        document = decode_plan_body(raw)
        if document.org_id != org_id:
            raise CompileError("published plan org does not match the dev org")
        current = self._store.read(org_id)
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
        primary = self._feed.org_id
        peers = self._feed.peer_orgs
        try:
            fetched: dict[str, bytes | None] = {
                plan_key(primary): await client.get(plan_key(primary))
            }
            dev_raw = await client.get(PUBLISHED_PLAN_KEY)
            for org in peers:
                fetched[plan_key(org)] = await client.get(plan_key(org))
        except Exception as exc:  # noqa: BLE001 — a poll failure keeps the served plan.
            self._note(f"dev: firewall plan poll failed ({type(exc).__name__})\n", b"")
            return
        dev_blob = dev_raw if isinstance(dev_raw, bytes) else None
        for org, blob in poll_bodies(primary, peers, fetched, dev_blob):
            self._apply_blob(org, blob)

    def _apply_blob(self, org_id: str, blob: bytes | None) -> None:
        if blob is None:
            return
        outcome = self._feed.apply(
            blob, compiled_at=time.time(), now=time.monotonic(), org_id=org_id
        )
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
