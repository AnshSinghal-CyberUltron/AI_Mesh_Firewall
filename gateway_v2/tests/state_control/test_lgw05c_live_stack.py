"""GW05c phase 4 — both real adapters at once: Postgres as the truth, Valkey as the copy.

The other suites each substitute a twin for one plane: the Valkey suite runs on
`MemoryControlDB`, the Postgres suite on `MemoryStore`. Neither of them exercises the seam
BETWEEN the two real adapters, which is where a schema, encoding or ordering mismatch would
actually live. This suite closes that: `PostgresControlDB` + `ValkeyPublisher` + `ValkeyStateStore`
+ the real gateway appliers, driven end to end.

Skipped unless `AMF_PG_DSN` is set. Run it with a throwaway server:

    docker run -d --rm --name amf-gw05c-pg -e POSTGRES_PASSWORD=pg \\
      -p 55432:5432 postgres:16-alpine
    AMF_PG_DSN='postgresql://postgres:pg@127.0.0.1:55432/postgres' \\
      .venv/bin/python -m pytest tests/state_control/test_lgw05c_live_stack.py -q
    docker rm -f amf-gw05c-pg
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import fakeredis
import pytest

from gateway_v2.admit.identity import IdentityCache, identity_applier
from gateway_v2.admit.killswitch import (
    KillSwitchSnapshot,
    KillSwitchState,
    killswitch_adopter,
    killswitch_applier,
)
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, PlanUnknownTenant, StreamingMode
from gateway_v2.domain.state import StateKind
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader
from gateway_v2.runtime.state_task import DeltaBudget, RoundReport, StateSynchroniser
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.store_valkey import ValkeyStateStore
from state_control.pg import PostgresControlDB
from state_control.rehydrate import MISSING, Rehydrator
from state_control.valkey import ValkeyPublisher
from state_control.writer import OK, StateWriter
from tests.plan.test_lgw05c_delta import _draft

DSN = os.environ.get("AMF_PG_DSN", "")
VALKEY_URL = os.environ.get("AMF_VALKEY_URL", "")
"""When set, the store is a REAL Valkey/Redis rather than fakeredis."""

pytestmark = pytest.mark.skipif(not DSN, reason="AMF_PG_DSN is not set")

SECRET = b"gw05c-live-stack-secret"


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


class Stack:
    """One control plane and one gateway worker, both on real clients."""

    def __init__(self, dsn: str, namespace: str) -> None:
        self.keys = StoreKeys(namespace=namespace)
        self.live = bool(VALKEY_URL)
        if self.live:
            import redis

            self.server = None
            self.sync_client = redis.Redis.from_url(VALKEY_URL)
        else:
            self.server = fakeredis.FakeServer()
            self.sync_client = fakeredis.FakeStrictRedis(server=self.server)
        self.db = PostgresControlDB(dsn)
        self.db.init_schema()
        self.publisher = ValkeyPublisher(self.sync_client, SECRET, self.keys)
        self.writer = StateWriter(self.db, self.publisher, SECRET)
        self.rehydrator = Rehydrator(
            self.db,
            self.publisher,
            self.writer,
            SECRET,
            stale_grace_s=0.0,
            kinds=(StateKind.PLAN, StateKind.KEY, StateKind.KS),
        )
        self.plans = PlanStore()
        self.snapshot = ReplicaSnapshot(self.plans, clock=lambda: 1.0)
        self.switches = KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0)
        self.identities = IdentityCache(clock=lambda: 0.0)
        self._clients: list[Any] = []

    def async_client(self) -> object:
        if self.live:
            import redis.asyncio

            client = redis.asyncio.Redis.from_url(VALKEY_URL)
        else:
            client = fakeredis.FakeAsyncRedis(server=self.server)
        self._clients.append(client)
        return client

    async def aclose(self) -> None:
        """Close every async client this stack handed out.

        A real redis.asyncio client binds its transport to the loop it was created on, so a
        worker owns ONE client on ONE loop -- which is exactly how a worker process runs. The
        tests below therefore do all their async work inside a single `asyncio.run`.
        """
        for client in self._clients:
            with contextlib.suppress(Exception):
                await client.aclose()
        self._clients.clear()

    def drop_store(self) -> None:
        """Lose everything this stack published, without touching another test's keys."""
        found = list(self.sync_client.scan_iter(match=f"{self.keys.namespace}*"))
        if found:
            self.sync_client.delete(*found)

    def worker(self, *, budget: DeltaBudget | None = None) -> StateSynchroniser:
        """A fresh worker process, as a restart or a scale-out would create."""
        reader = ValkeyStateStore(self.async_client(), self.keys)
        return StateSynchroniser(
            FeedReader(reader, SECRET),
            {
                StateKind.PLAN: plan_applier(self.plans, self.snapshot, clock=lambda: 1.0),
                StateKind.KS: killswitch_applier(self.switches),
                StateKind.KEY: identity_applier(self.identities),
            },
            **({} if budget is None else {"budget": budget}),
        )


@pytest.fixture
def stack() -> Iterator[Stack]:
    import psycopg

    schema = f"amf_live_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as admin, admin.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA "{schema}"')
    scoped = f"{DSN}?options=-csearch_path%3D{schema}"
    built = Stack(scoped, f"{{live-{schema[-8:]}}}")
    try:
        yield built
    finally:
        built.drop_store()
        with psycopg.connect(DSN, autocommit=True) as admin, admin.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _poll(sync: StateSynchroniser, kind: StateKind) -> RoundReport:
    report = _run(sync.drain(kind))
    assert report.ok, report.error
    return report


async def _apoll(sync: StateSynchroniser, kind: StateKind) -> RoundReport:
    report = await sync.drain(kind)
    assert report.ok, report.error
    return report


def _drive(stack: Stack, body: Callable[[], Awaitable[None]]) -> None:
    """Run a whole scenario in ONE event loop, as a worker process does."""

    async def wrapped() -> None:
        try:
            await body()
        finally:
            await stack.aclose()

    asyncio.run(wrapped())


# --- the seam between the two real adapters ----------------------------------------------------


def test_a_record_signed_into_postgres_verifies_out_of_valkey(stack: Stack) -> None:
    """The encoding seam: text in Postgres, bytes in Valkey, one signature across both."""
    stack.writer.put(
        StateKind.BUDGET,
        "org-a",
        {"limit": 10, "note": "caf\u00e9 \u2014 unicode", "nested": {"b": 2, "a": 1}},
    )

    reader = FeedReader(ValkeyStateStore(stack.async_client(), stack.keys), SECRET)
    from gateway_v2.runtime.state_feed import START

    round_ = _run(reader.poll(StateKind.BUDGET, START, limit=10))

    assert len(round_.records) == 1
    with stack.db.tx() as tx:
        stored = tx.record(StateKind.BUDGET, "org-a")
    assert stored is not None
    assert round_.records[0].body == stored.body, "byte-identical across both stores"
    assert round_.records[0] == stored


def test_a_plan_write_is_served_by_a_worker(stack: Stack) -> None:
    worker = stack.worker()

    assert stack.writer.plan_set("org-a", _plan_body("org-a")).status == OK
    _poll(worker, StateKind.PLAN)

    served = stack.plans.read("org-a")
    assert isinstance(served, ExecutionPlan)
    assert served.org_id == "org-a"


def test_one_change_among_a_thousand_reads_one_record(stack: Stack) -> None:
    stack.writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 1_001)],
    )

    async def body() -> None:
        worker = stack.worker(budget=DeltaBudget(records=2_000))
        await _apoll(worker, StateKind.PLAN)
        stack.writer.plan_set("org-500", _plan_body("org-500"))
        report = await _apoll(worker, StateKind.PLAN)
        assert report.applied == 1

    _drive(stack, body)

    served = stack.plans.read("org-500")
    assert isinstance(served, ExecutionPlan)
    assert served.feed_seq == 1_001


def test_the_whole_write_vocabulary_propagates(stack: Stack) -> None:
    stack.writer.plan_set("org-a", _plan_body("org-a"))
    stack.writer.key_add("hash-a", "org-a", key_id="k-a", rate_per_s=1.0, burst=2.0)
    stack.writer.killswitch("org:org-a", on=True)

    async def body() -> None:
        worker = stack.worker()
        for kind in (StateKind.PLAN, StateKind.KEY, StateKind.KS):
            await _apoll(worker, kind)
        stack.identities._admit("hash-a", Principal("k-a", "org-a", 1.0, 2.0, 1))
        assert isinstance(stack.plans.read("org-a"), ExecutionPlan)
        assert stack.switches.org_killed("org-a") is True

        stack.writer.killswitch("org:org-a", on=False)
        stack.writer.key_revoke("hash-a")
        stack.writer.plan_offboard("org-a")
        for kind in (StateKind.KS, StateKind.KEY, StateKind.PLAN):
            await _apoll(worker, kind)

    _drive(stack, body)

    assert stack.switches.org_killed("org-a") is False
    assert stack.identities.cached("hash-a") is None
    assert isinstance(stack.plans.read("org-a"), PlanUnknownTenant)


def test_a_fresh_worker_cold_starts_on_the_engaged_set(stack: Stack) -> None:
    """Gate G-04 over both real adapters: 500 scopes published, one engaged."""
    stack.writer.put_many(
        StateKind.KS,
        [(f"org:t{n}", {"on": False}) for n in range(1, 501)],
        engaged={},
    )
    stack.writer.killswitch("org:acme", on=True)

    report = _run(
        stack.worker().bootstrap_engaged(
            StateKind.KS, killswitch_adopter(stack.switches),
        ),
    )

    assert report.ok, report.error
    assert report.applied == 1, "one engaged scope out of 501 records"
    assert stack.switches.org_killed("acme") is True
    assert stack.switches.state(100.0) is KillSwitchState.OK


def test_a_flush_fails_closed_then_the_rehydrator_restores_from_postgres(
    stack: Stack,
) -> None:
    """The full fault loop, both planes real: store loss, fail closed, repair, recovery."""
    for position in range(1, 26):
        stack.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))

    async def body() -> None:
        worker = stack.worker()
        await _apoll(worker, StateKind.PLAN)
        assert stack.rehydrator.diagnose(StateKind.PLAN) is None

        stack.drop_store()
        assert stack.rehydrator.diagnose(StateKind.PLAN) == MISSING
        broken = await worker.round_once(StateKind.PLAN)
        assert broken.ok is False
        assert "manifest missing" in (broken.error or "")

        summary = stack.rehydrator.round_once()
        assert [
            event.records for event in summary.repairs if event.kind is StateKind.PLAN
        ] == [25]
        await _apoll(stack.worker(), StateKind.PLAN)

    _drive(stack, body)

    assert stack.rehydrator.diagnose(StateKind.PLAN) is None
    assert isinstance(stack.plans.read("org-7"), ExecutionPlan)


def test_an_unpublished_write_is_recovered_by_the_rehydrator(stack: Stack) -> None:
    """ok_publish_pending over real Postgres: durable, then published by the next round."""
    from state_control.publisher import BrokenPublisher

    stack.writer.plan_set("org-a", _plan_body("org-a"))
    pending = StateWriter(stack.db, BrokenPublisher(), SECRET).plan_set(
        "org-b", _plan_body("org-b"),
    )
    assert pending.durable is True and pending.published is False

    stack.rehydrator.round_once()
    _poll(stack.worker(), StateKind.PLAN)

    assert isinstance(stack.plans.read("org-b"), ExecutionPlan)


def test_the_first_round_bootstraps_kinds_that_have_never_been_written(
    stack: Stack,
) -> None:
    """A kind with no writes has no published manifest, so a reader would fail closed forever.

    The re-hydrator publishes a signed EMPTY manifest for it, which is what lets a worker
    short-circuit that kind instead of refusing every request. It also means a fresh deployment
    needs one re-hydrator round before gateways can serve, which is the readiness behaviour
    GW05b already declares.
    """
    stack.writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 301)],
    )

    first = stack.rehydrator.round_once()

    assert first.ok
    assert StateKind.PLAN in first.healthy, "the written kind needs no repair"
    assert {event.kind for event in first.repairs} == {StateKind.KEY, StateKind.KS}
    assert all(event.records == 0 for event in first.repairs)

    worker = stack.worker()
    assert _run(worker.round_once(StateKind.KEY)).ok, "an empty kind now short-circuits"


def test_a_settled_rehydrator_round_repairs_nothing(stack: Stack) -> None:
    """The round that runs every second, once the store agrees with Postgres."""
    stack.writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 301)],
    )
    stack.rehydrator.round_once()

    settled = stack.rehydrator.round_once()

    assert settled.ok
    assert settled.repairs == (), "a settled round touches nothing"
    assert set(settled.healthy) == {StateKind.PLAN, StateKind.KEY, StateKind.KS}


def test_a_bulk_onboard_converges_over_both_adapters(stack: Stack) -> None:
    stack.writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 251)],
    )
    counted: list[int] = []

    async def body() -> None:
        worker = stack.worker(budget=DeltaBudget(records=50))
        rounds = 0
        while True:
            rounds += 1
            report = await worker.round_once(StateKind.PLAN)
            assert report.ok, report.error
            if not report.truncated:
                break
        counted.append(rounds)

    _drive(stack, body)

    rounds = counted[0]
    assert rounds == 5
    assert len(stack.plans.known()) == 250
    assert stack.rehydrator.diagnose(StateKind.PLAN) is None


def test_a_rollback_propagates_as_a_new_epoch(stack: Stack) -> None:
    stack.writer.plan_set("org-a", _plan_body("org-a"))
    held: list[object] = []

    async def body() -> None:
        worker = stack.worker()
        await _apoll(worker, StateKind.PLAN)
        stack.writer.plan_set("org-a", _plan_body("org-a"))
        await _apoll(worker, StateKind.PLAN)
        held.append(stack.writer.rollback(1))
        await _apoll(worker, StateKind.PLAN)

    _drive(stack, body)

    outcome = held[0]
    assert outcome.version.epoch == 2
    served = stack.plans.read("org-a")
    assert isinstance(served, ExecutionPlan)
    assert served.epoch == 2
    assert stack.rehydrator.diagnose(StateKind.PLAN) is None


def test_a_nudge_reaches_a_listener_over_real_pubsub(stack: Stack) -> None:
    """The nudge path end to end: the writer publishes, the listener coalesces, a round runs.

    Pub/sub is lossy, so this is a LATENCY path and never load-bearing. What it must get right
    is the payload: a worker already at the announced position can skip the round entirely.
    """
    from gateway_v2.runtime.state_nudge import NudgeListener, PushKnobs

    worker = stack.worker()
    seen: list[dict[StateKind, int]] = []

    async def drive() -> None:
        async def nudge(positions: dict[StateKind, int]) -> bool:
            seen.append(dict(positions))
            for kind in positions or (StateKind.PLAN,):
                await worker.round_once(kind)
            return True

        listener = NudgeListener(
            stack.async_client(),
            stack.keys.updates,
            knobs=PushKnobs(ping_s=4.0, first_backoff_s=0.01, cap_backoff_s=0.02, drain_max=8),
        )
        task = asyncio.create_task(listener.run(nudge))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if listener.subscribed:
                break
        assert listener.subscribed, "the listener never subscribed"

        await asyncio.to_thread(stack.writer.plan_set, "org-nudged", _plan_body("org-nudged"))

        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(positions for positions in seen):
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(drive())

    announced = [positions for positions in seen if positions]
    assert announced, f"no nudge carried a position: {seen}"
    assert announced[0].get(StateKind.PLAN) == 1, announced
    assert isinstance(stack.plans.read("org-nudged"), ExecutionPlan)


def test_the_store_holds_only_the_documented_keys(stack: Stack) -> None:
    """Against a real server: a write leaves the record, index, engaged set and manifest."""
    stack.writer.killswitch("org:acme", on=True)

    found = {
        key.decode() if isinstance(key, bytes) else str(key)
        for key in stack.sync_client.scan_iter(match=f"{stack.keys.namespace}*")
    }

    assert found == {
        stack.keys.record_hash(StateKind.KS),
        stack.keys.index(StateKind.KS),
        stack.keys.engaged(StateKind.KS),
        stack.keys.manifest(StateKind.KS),
    }
    assert all(key.startswith(stack.keys.namespace) for key in found), "one hash slot"


def test_an_exclusive_lower_bound_does_not_re_apply_the_cursor_record(stack: Stack) -> None:
    """ZRANGEBYSCORE's `(after` bound, against a real server rather than an emulation."""
    for position in range(1, 6):
        stack.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    reader = FeedReader(ValkeyStateStore(stack.async_client(), stack.keys), SECRET)
    from gateway_v2.domain.state import Version
    from gateway_v2.runtime.state_feed import Cursor

    round_ = _run(
        reader.poll(StateKind.PLAN, Cursor(Version(1, 3), 3), limit=10),
    )

    assert [record.feed_seq for record in round_.records] == [4, 5], "3 is not re-applied"


def test_a_publisher_behind_the_store_loses_under_a_real_watch(stack: Stack) -> None:
    """WATCH/MULTI is the most emulation-sensitive thing here, so prove it on a real server.

    Two writers share one store; the second has its own empty source of truth, so its manifest
    is behind what the store holds. The publish must be skipped and reported pending rather
    than moving the store backwards.
    """
    from state_control.db import MemoryControlDB

    for position in range(1, 4):
        stack.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    assert stack.publisher.stored_feed_seq(StateKind.PLAN) == 3

    behind = StateWriter(MemoryControlDB(), stack.publisher, SECRET)
    outcome = behind.plan_set("org-late", _plan_body("org-late"))

    assert outcome.durable is True
    assert outcome.published is False, "the older generation did not land"
    assert stack.publisher.stored_feed_seq(StateKind.PLAN) == 3, "the store never went back"


def test_a_forged_manifest_never_blocks_a_publish_on_a_real_server(stack: Stack) -> None:
    stack.sync_client.set(stack.keys.manifest(StateKind.PLAN), b"not a manifest")

    outcome = stack.writer.plan_set("org-a", _plan_body("org-a"))

    assert outcome.status == OK
    assert stack.publisher.stored_feed_seq(StateKind.PLAN) == 1


def test_a_hash_kind_and_a_key_kind_coexist_on_a_real_server(stack: Stack) -> None:
    stack.writer.plan_set("org-a", _plan_body("org-a"))
    stack.writer.key_add("hash-a", "org-a", key_id="k", rate_per_s=1.0, burst=1.0)
    stack.writer.key_add("hash-b", "org-a", key_id="k", rate_per_s=1.0, burst=1.0)

    assert stack.sync_client.exists(stack.keys.record_key(StateKind.PLAN, "org-a")) == 1
    assert stack.sync_client.hlen(stack.keys.record_hash(StateKind.KEY)) == 2
    assert stack.sync_client.zcard(stack.keys.index(StateKind.KEY)) == 2
