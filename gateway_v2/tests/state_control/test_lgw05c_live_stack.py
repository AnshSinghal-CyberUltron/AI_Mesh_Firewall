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
import os
import uuid
from collections.abc import Awaitable, Iterator

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

    def worker(self, *, budget: DeltaBudget | None = None) -> StateSynchroniser:
        """A fresh worker process, as a restart or a scale-out would create."""
        reader = ValkeyStateStore(
            fakeredis.FakeAsyncRedis(server=self.server), self.keys,
        )
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
    try:
        yield Stack(scoped, "{live}")
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin, admin.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _poll(sync: StateSynchroniser, kind: StateKind) -> RoundReport:
    report = _run(sync.drain(kind))
    assert report.ok, report.error
    return report


# --- the seam between the two real adapters ----------------------------------------------------


def test_a_record_signed_into_postgres_verifies_out_of_valkey(stack: Stack) -> None:
    """The encoding seam: text in Postgres, bytes in Valkey, one signature across both."""
    stack.writer.put(
        StateKind.BUDGET,
        "org-a",
        {"limit": 10, "note": "caf\u00e9 \u2014 unicode", "nested": {"b": 2, "a": 1}},
    )

    reader = FeedReader(
        ValkeyStateStore(fakeredis.FakeAsyncRedis(server=stack.server), stack.keys), SECRET,
    )
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
    worker = stack.worker(budget=DeltaBudget(records=2_000))
    _poll(worker, StateKind.PLAN)

    stack.writer.plan_set("org-500", _plan_body("org-500"))
    report = _poll(worker, StateKind.PLAN)

    assert report.applied == 1
    served = stack.plans.read("org-500")
    assert isinstance(served, ExecutionPlan)
    assert served.feed_seq == 1_001


def test_the_whole_write_vocabulary_propagates(stack: Stack) -> None:
    worker = stack.worker()
    stack.writer.plan_set("org-a", _plan_body("org-a"))
    stack.writer.key_add("hash-a", "org-a", key_id="k-a", rate_per_s=1.0, burst=2.0)
    stack.writer.killswitch("org:org-a", on=True)
    _poll(worker, StateKind.PLAN)
    _poll(worker, StateKind.KEY)
    _poll(worker, StateKind.KS)
    stack.identities._admit("hash-a", Principal("k-a", "org-a", 1.0, 2.0, 1))

    assert isinstance(stack.plans.read("org-a"), ExecutionPlan)
    assert stack.switches.org_killed("org-a") is True

    stack.writer.killswitch("org:org-a", on=False)
    stack.writer.key_revoke("hash-a")
    stack.writer.plan_offboard("org-a")
    _poll(worker, StateKind.KS)
    _poll(worker, StateKind.KEY)
    _poll(worker, StateKind.PLAN)

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
    worker = stack.worker()
    for position in range(1, 26):
        stack.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    _poll(worker, StateKind.PLAN)
    assert stack.rehydrator.diagnose(StateKind.PLAN) is None

    stack.sync_client.flushall()

    assert stack.rehydrator.diagnose(StateKind.PLAN) == MISSING
    broken = _run(worker.round_once(StateKind.PLAN))
    assert broken.ok is False
    assert "manifest missing" in (broken.error or "")

    summary = stack.rehydrator.round_once()
    assert [event.records for event in summary.repairs if event.kind is StateKind.PLAN] == [25]

    recovered = _poll(stack.worker(), StateKind.PLAN)
    assert recovered.ok
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
    worker = stack.worker(budget=DeltaBudget(records=50))

    rounds = 0
    while True:
        rounds += 1
        report = _run(worker.round_once(StateKind.PLAN))
        assert report.ok, report.error
        if not report.truncated:
            break

    assert rounds == 5
    assert len(stack.plans.known()) == 250
    assert stack.rehydrator.diagnose(StateKind.PLAN) is None


def test_a_rollback_propagates_as_a_new_epoch(stack: Stack) -> None:
    worker = stack.worker()
    stack.writer.plan_set("org-a", _plan_body("org-a"))
    _poll(worker, StateKind.PLAN)
    stack.writer.plan_set("org-a", _plan_body("org-a"))
    _poll(worker, StateKind.PLAN)

    outcome = stack.writer.rollback(1)
    _poll(worker, StateKind.PLAN)

    assert outcome.version.epoch == 2
    served = stack.plans.read("org-a")
    assert isinstance(served, ExecutionPlan)
    assert served.epoch == 2
    assert stack.rehydrator.diagnose(StateKind.PLAN) is None
