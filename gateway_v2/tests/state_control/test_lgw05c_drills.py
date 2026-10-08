"""GW05c phase 6 — fault drills against real Postgres and real Valkey.

These are the C36 / rc3-state-p0 scenarios that can be run on one machine: a store flush, a store
partition and heal, a Postgres outage, re-hydration under a held row lock, concurrent writers,
the gate G-04 cold start, and a 25,000-record bulk onboard. Each drives the REAL writer,
re-hydrator, feed reader and appliers.

Three scenarios from the round-2 list are NOT here and are not faked:

* **sp1 / sp1-live** (a fresh process on a lagging REPLICA) needs a primary plus a replica and a
  promotion. The version-floor behaviour it checks is covered by `raise_floor` tests and by the
  cursor-regress drill below, but a real failover is a lane scenario.
* **sp2** (a committed-but-unpublished write staying unenforced) fails closed on GW05b's signed
  freshness stamp, which is not implemented. Without it a worker legitimately serves its RAM
  snapshot, so there is nothing yet to assert. The unpublished-write drill below pins the
  behaviour that exists today rather than the behaviour GW05b will add.
* **Valkey forced failover** cannot be triggered on Memorystore at all (R2-13); its data-loss
  shape is the flush drill.

Run via `scripts/gw05c_local_drills.sh`, which provisions the containers and tears them down.
Skipped unless `AMF_PG_DSN` and `AMF_VALKEY_URL` are set; the pause drills additionally need
`AMF_DRILL_VALKEY_CONTAINER` / `AMF_DRILL_PG_CONTAINER`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator

import pytest
import redis
import redis.asyncio

from gateway_v2.admit.killswitch import KillSwitchSnapshot, killswitch_adopter
from gateway_v2.domain.locks import PG_GRACE_MS
from gateway_v2.domain.plan import (
    ExecutionPlan,
    PlanUnavailable,
    PlanUnknownTenant,
    StreamingMode,
)
from gateway_v2.domain.state import START, Cursor, StateKind, Version
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader
from gateway_v2.runtime.state_stamp import StampView
from gateway_v2.runtime.state_task import DEFAULT_BUDGET, DeltaBudget, StateSynchroniser
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.store_valkey import (
    UnboundedStoreClient,
    ValkeyStateStore,
    require_bounded_client,
)
from state_control.db import ControlDB
from state_control.pg import PostgresControlDB
from state_control.rehydrate import MISSING, Rehydrator
from state_control.valkey import ValkeyPublisher
from state_control.writer import OK, StateWriter
from tests.plan.test_lgw05c_delta import _draft

DSN = os.environ.get("AMF_PG_DSN", "")
VALKEY_URL = os.environ.get("AMF_VALKEY_URL", "")
VALKEY_CONTAINER = os.environ.get("AMF_DRILL_VALKEY_CONTAINER", "")
PG_CONTAINER = os.environ.get("AMF_DRILL_PG_CONTAINER", "")

pytestmark = pytest.mark.skipif(
    not (DSN and VALKEY_URL), reason="AMF_PG_DSN and AMF_VALKEY_URL are not set",
)

SECRET = b"gw05c-drill-secret"
OP_TIMEOUT_S = 0.25
"""Per-operation timeout. Must be below the refresh period, or a hung round outlives it."""


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


def _docker(*args: str) -> None:
    subprocess.run(["docker", *args], check=True, capture_output=True, timeout=60)


class Drill:
    """A control plane and a worker on real infrastructure, in their own namespace."""

    def __init__(self, dsn: str, namespace: str, schema: str = "") -> None:
        self._dsn = dsn
        self.schema = schema
        self.keys = StoreKeys(namespace=namespace)
        self.sync_client = redis.Redis.from_url(VALKEY_URL, socket_timeout=OP_TIMEOUT_S)
        self.db = PostgresControlDB(dsn)
        self.db.init_schema()
        self.publisher = ValkeyPublisher(self.sync_client, SECRET, self.keys)
        self.writer = StateWriter(self.db, self.publisher, SECRET)
        self.rehydrator = Rehydrator(
            self.db, self.publisher, self.writer, SECRET,
            stale_grace_s=0.0, kinds=(StateKind.PLAN, StateKind.KS),
        )
        self.plans = PlanStore()
        self.snapshot = ReplicaSnapshot(self.plans, clock=lambda: 1.0)
        self.switches = KillSwitchSnapshot(stale_ms=5_000, clock=time.monotonic)
        self._clients: list[redis.asyncio.Redis] = []

    def async_client(self) -> redis.asyncio.Redis:
        client = redis.asyncio.Redis.from_url(VALKEY_URL, socket_timeout=OP_TIMEOUT_S)
        self._clients.append(client)
        return client

    def worker(
        self,
        *,
        budget: DeltaBudget | None = None,
        stamp: StampView | None = None,
    ) -> StateSynchroniser:
        reader = ValkeyStateStore(self.async_client(), self.keys)
        return StateSynchroniser(
            FeedReader(reader, SECRET),
            {StateKind.PLAN: plan_applier(self.plans, self.snapshot, clock=lambda: 1.0)},
            stamp=stamp,
            budget=DEFAULT_BUDGET if budget is None else budget,
        )

    def tightly_bounded_db(self) -> PostgresControlDB:
        """The same database behind shorter session bounds, for outage drills.

        A PAUSED container accepts the connection and never answers, so each operation costs its
        full timeout: at the shipped 5 s that is 20 s per four-kind round, and a drill that
        needs several rounds does not fit in any sensible budget. Measured here: 5.0 s per call
        at the defaults, 2.0 s at these. The bounds are what a 1 s re-hydrator period would be
        tuned to anyway -- the shipped defaults suit the writer, not this loop.
        """
        return PostgresControlDB(
            self._dsn,
            connect_timeout_s=1,
            statement_timeout_ms=1_000,
            tcp_user_timeout_ms=1_000,
        )

    def stamping_rehydrator(
        self,
        clock: Callable[[], float],
        *,
        db: ControlDB | None = None,
    ) -> Rehydrator:
        """A re-hydrator over ALL FOUR kinds, so it is allowed to write a freshness stamp.

        `self.rehydrator` deliberately watches two kinds, which is enough for the GW05c drills
        and -- by GW05b's own rule -- not enough to make the whole-store claim a stamp makes. An
        untouched kind diagnoses as MISSING and is repaired to an empty published kind, so a
        four-kind round verifies even on an estate that has only ever had plans written.

        `clock` is required rather than defaulted, and the reason is the freshness design's one
        real dependency: `verified_at` is the re-hydrator's wall clock and the age is the
        gateway's, so a test that stamps with `time.time()` and reads with a fake clock measures
        an age of about -1.8e9 seconds and fails closed. That is the NTP coupling the module
        docstring warns about, reproduced in miniature.
        """
        return Rehydrator(
            db or self.db, self.publisher, self.writer, SECRET,
            stale_grace_s=0.0, clock=clock, name="drill-rehydrator:1",
        )

    async def aclose(self) -> None:
        for client in self._clients:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - closing after a fault is allowed to fail
                pass
        self._clients.clear()

    def drop_store(self) -> None:
        found = list(self.sync_client.scan_iter(match=f"{self.keys.namespace}*"))
        if found:
            self.sync_client.delete(*found)


@pytest.fixture
def drill() -> Iterator[Drill]:
    import psycopg

    schema = f"amf_drill_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as admin, admin.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA "{schema}"')
    built = Drill(f"{DSN}?options=-csearch_path%3D{schema}", f"{{d{schema[-8:]}}}", schema)
    try:
        yield built
    finally:
        try:
            built.drop_store()
        finally:
            with psycopg.connect(DSN, autocommit=True) as admin, admin.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA "{schema}" CASCADE')


# --- the client bound every drill depends on ------------------------------------------------------


def test_a_client_without_a_timeout_is_refused() -> None:
    """A partitioned store accepts the connection and never answers. Without a timeout the
    refresh round never returns, the snapshot never ages, and the fail-closed window does not
    exist. Nothing in the tree wires this yet -- that is GW06's start-up path -- so the check is
    provided and proven here.
    """
    unbounded = redis.Redis.from_url(VALKEY_URL)

    with pytest.raises(UnboundedStoreClient, match="no socket_timeout"):
        require_bounded_client(unbounded, below_s=0.5)


def test_a_timeout_at_or_above_the_refresh_period_is_refused() -> None:
    """RC2 measured the consequence: a timeout equal to the staleness ceiling produced
    fleet-wide fail-closed on every half-open failover.
    """
    slow = redis.Redis.from_url(VALKEY_URL, socket_timeout=0.5)

    with pytest.raises(UnboundedStoreClient, match="must be below the refresh period"):
        require_bounded_client(slow, below_s=0.5)


def test_a_bounded_client_is_accepted() -> None:
    client = redis.Redis.from_url(VALKEY_URL, socket_timeout=OP_TIMEOUT_S)

    assert require_bounded_client(client, below_s=0.5) == OP_TIMEOUT_S


# --- store data loss ------------------------------------------------------------------------------


def test_drill_flush_fails_closed_then_rehydrates(drill: Drill) -> None:
    """C36's flush scenario: the store loses everything, Postgres does not."""
    for position in range(1, 51):
        drill.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))

    async def body() -> dict[str, object]:
        worker = drill.worker(budget=DeltaBudget(records=1_000))
        first = await worker.drain(StateKind.PLAN)
        assert first.ok, first.error

        drill.drop_store()
        broken = await worker.round_once(StateKind.PLAN)
        assert broken.ok is False
        assert "manifest missing" in (broken.error or "")

        detected = time.monotonic()
        assert drill.rehydrator.diagnose(StateKind.PLAN) == MISSING
        summary = drill.rehydrator.round_once()
        restored_s = time.monotonic() - detected

        recovered = await drill.worker(budget=DeltaBudget(records=1_000)).drain(StateKind.PLAN)
        assert recovered.ok, recovered.error
        return {
            "records": [e.records for e in summary.repairs if e.kind is StateKind.PLAN],
            "restore_s": round(restored_s, 3),
        }

    result = _drive(drill, body)

    assert result["records"] == [50]
    assert isinstance(drill.plans.read("org-7"), ExecutionPlan)
    assert drill.rehydrator.diagnose(StateKind.PLAN) is None


def test_drill_a_regressed_store_is_refused(drill: Drill) -> None:
    """The sp1 CLASS without a replica: a store generation older than the applied floor."""
    for position in range(1, 11):
        drill.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))

    async def body() -> None:
        worker = drill.worker(budget=DeltaBudget(records=100))
        assert (await worker.drain(StateKind.PLAN)).ok
        # A worker that has applied position 10 must refuse a store that claims 4.
        worker.raise_floor(StateKind.PLAN, Cursor(Version(1, 10), 10))
        counters, records, engaged = drill.db.snapshot(StateKind.PLAN)
        older = type(counters)(
            version=Version(1, 4), feed_seq=4, count=counters.count,
            on_count=counters.on_count,
        )
        drill.publisher.publish_kind(
            StateKind.PLAN, records[:4], drill.writer.manifest_for(StateKind.PLAN, older), engaged,
        )

        refused = await worker.round_once(StateKind.PLAN)
        assert refused.ok is False
        assert "older than" in (refused.error or "") or "below the applied cursor" in (
            refused.error or ""
        )

    _drive(drill, body)


def test_an_unpublished_write_is_invisible_until_a_round(drill: Drill) -> None:
    """SP2's MECHANISM, pinned, and the negative control for the drill below.

    Without a freshness stamp this is R2-03 exactly: the write is durable, the store never hears
    about it, every drain reports `ok`, and nothing ages. The gateway is not wrong about the
    store -- it is wrong about the store being worth trusting.
    """
    from state_control.publisher import BrokenPublisher

    drill.writer.plan_set("org-a", _plan_body("org-a"))
    pending = StateWriter(drill.db, BrokenPublisher(), SECRET).plan_set(
        "org-b", _plan_body("org-b"),
    )

    async def body() -> None:
        worker = drill.worker()
        assert (await worker.drain(StateKind.PLAN)).ok
        assert drill.plans.read("org-b").__class__.__name__ != "ExecutionPlan"
        drill.rehydrator.round_once()
        assert (await worker.drain(StateKind.PLAN)).ok

    _drive(drill, body)

    assert pending.durable is True and pending.published is False
    assert isinstance(drill.plans.read("org-b"), ExecutionPlan)


def test_an_unpublished_write_fails_the_gateway_closed_within_the_bound(drill: Drill) -> None:
    """SP2, inverted. With a stamp, an unpublished commit becomes a 503 instead of silence.

    The sequence is the one the reviewer measured: a write commits, its publish fails, no
    re-hydrator is running. RC2 served happily for 60 s. Here the stamp simply stops being
    refreshed, so within FRESH_MS every kind refuses -- including the plan the worker is
    holding, because the write that superseded it is exactly what nobody can see.

    Recovery is the second half: once a re-hydrator returns it republishes the pending write AND
    stamps, so one gateway cycle restores service.
    """
    from state_control.publisher import BrokenPublisher

    wall = [1_000.0]
    view = StampView(SECRET, fresh_ms=5_000, started_at=999.0, clock=lambda: wall[0])
    snapshot = ReplicaSnapshot(drill.plans, clock=lambda: 1.0, stamp=view)
    rehydrator = drill.stamping_rehydrator(lambda: wall[0])

    drill.writer.plan_set("org-a", _plan_body("org-a"))
    assert rehydrator.round_once().stamped is not None, "a re-hydrator is alive"

    async def body() -> None:
        worker = drill.worker(stamp=view)
        assert (await worker.drain_all())[0].ok
        assert isinstance(snapshot.lookup("org-a"), ExecutionPlan)

        # A write commits and its publish fails. The re-hydrator is gone, so nothing
        # republishes it and nothing stamps.
        pending = StateWriter(drill.db, BrokenPublisher(), SECRET).plan_set(
            "org-b", _plan_body("org-b"),
        )
        assert pending.durable is True and pending.published is False
        assert (await worker.drain_all())[0].ok, "the store itself looks perfectly healthy"
        assert isinstance(snapshot.lookup("org-a"), ExecutionPlan), "and still serves"

        wall[0] += 6.0
        await worker.drain_all()

        assert view.fresh() is False
        held = snapshot.lookup("org-a")
        assert isinstance(held, PlanUnavailable), "a held plan is refused, not served"
        assert not isinstance(held, PlanUnknownTenant), "and never as 403 complete-onboarding"

        # The re-hydrator returns: it republishes the pending write and stamps again.
        assert rehydrator.round_once().stamped is not None
        await worker.drain_all()

        assert view.fresh() is True
        assert isinstance(snapshot.lookup("org-a"), ExecutionPlan)
        assert isinstance(snapshot.lookup("org-b"), ExecutionPlan), "the pending write landed"

    _drive(drill, body)


# --- outages -------------------------------------------------------------------------------------


@pytest.mark.skipif(not VALKEY_CONTAINER, reason="AMF_DRILL_VALKEY_CONTAINER is not set")
def test_drill_store_partition_and_heal(drill: Drill) -> None:
    """A PAUSED store accepts the connection and never answers -- the D2 shape.

    The round must fail inside the operation timeout rather than hang, so the snapshot ages and
    the declared fail-closed window applies. Then it must recover without intervention.
    """
    for position in range(1, 6):
        drill.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))

    async def body() -> float:
        worker = drill.worker()
        assert (await worker.drain(StateKind.PLAN)).ok

        _docker("pause", VALKEY_CONTAINER)
        try:
            started = time.monotonic()
            with pytest.raises((redis.RedisError, OSError, TimeoutError, asyncio.TimeoutError)):
                await worker.round_once(StateKind.PLAN)
            blocked_s = time.monotonic() - started
        finally:
            _docker("unpause", VALKEY_CONTAINER)

        healed = drill.worker()
        for _ in range(40):
            report = await healed.round_once(StateKind.PLAN)
            if report.ok:
                break
            await asyncio.sleep(0.1)
        assert report.ok, report.error
        return blocked_s

    blocked_s = _drive(drill, body)

    assert blocked_s < 2.0, f"a partitioned store blocked a round for {blocked_s:.2f}s"
    assert isinstance(drill.plans.read("org-3"), ExecutionPlan)


@pytest.mark.skipif(not PG_CONTAINER, reason="AMF_DRILL_PG_CONTAINER is not set")
def test_drill_postgres_outage_does_not_stop_serving(drill: Drill) -> None:
    """Workers read the STORE, so a database outage must not touch the serving path."""
    for position in range(1, 6):
        drill.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))

    async def body() -> None:
        worker = drill.worker()
        assert (await worker.drain(StateKind.PLAN)).ok

        _docker("pause", PG_CONTAINER)
        try:
            served = await worker.round_once(StateKind.PLAN)
            assert served.ok, "a Postgres outage must not fail a store round"
            assert isinstance(drill.plans.read("org-3"), ExecutionPlan)
            summary = drill.rehydrator.round_once()
            assert summary.ok is False, "the re-hydrator reports, per kind, and does not raise"
        finally:
            _docker("unpause", PG_CONTAINER)

        for _ in range(60):
            if drill.rehydrator.round_once().ok:
                break
            await asyncio.sleep(0.25)
        assert drill.rehydrator.round_once().ok, "recovered after the outage"

    _drive(drill, body)


def test_drill_rehydration_under_a_held_row_lock(drill: Drill) -> None:
    """R2-04: one idle writer transaction holding FOR UPDATE stalled re-hydration for 38 s.

    The publish snapshot is REPEATABLE READ READ ONLY and takes no lock, so a restore must
    complete while the lock is still held.
    """
    for position in range(1, 31):
        drill.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    drill.drop_store()

    with drill.db.tx() as holding:
        holding.counters(StateKind.PLAN, lock=True)  # held open for the whole repair
        started = time.monotonic()
        summary = drill.rehydrator.round_once()
        took_s = time.monotonic() - started

    assert [e.records for e in summary.repairs if e.kind is StateKind.PLAN] == [30]
    assert took_s < 5.0, f"repair waited {took_s:.2f}s behind a held lock"
    assert drill.rehydrator.diagnose(StateKind.PLAN) is None


# --- correctness under concurrency and scale --------------------------------------------------


def test_drill_concurrent_writers_leave_no_gap(drill: Drill) -> None:
    """Versions are issued under FOR UPDATE, so positions must be dense and unique."""
    from concurrent.futures import ThreadPoolExecutor

    def write(position: int) -> str:
        outcome = drill.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
        return outcome.status

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(write, range(1, 61)))

    assert set(statuses) == {OK}
    counters, records, _engaged = drill.db.snapshot(StateKind.PLAN)
    positions = sorted(record.feed_seq for record in records)
    assert positions == list(range(1, 61)), "dense, no gaps, no duplicates"
    assert counters.feed_seq == 60
    assert counters.count == 60
    assert drill.rehydrator.diagnose(StateKind.PLAN) is None


def test_drill_g04_cold_start_with_two_thousand_keys_over_two_hundred_orgs(
    drill: Drill,
) -> None:
    """Gate G-04's shape: a new gateway must not pay O(estate) to become ready."""
    drill.writer.put_many(
        StateKind.KEY,
        [
            (
                f"hash-{n}",
                {
                    "key_id": f"k{n}",
                    "org_id": f"org-{n % 200}",
                    "rate_per_s": 1.0,
                    "burst": 2.0,
                },
            )
            for n in range(2_000)
        ],
    )
    drill.writer.put_many(
        StateKind.KS, [(f"org:org-{n}", {"on": False}) for n in range(200)], engaged={},
    )
    drill.writer.killswitch("org:org-7", on=True)

    async def body() -> int:
        switches = KillSwitchSnapshot(stale_ms=5_000, clock=time.monotonic)
        sync = StateSynchroniser(
            FeedReader(ValkeyStateStore(drill.async_client(), drill.keys), SECRET),
            {StateKind.KS: lambda round_: None},
        )
        report = await sync.bootstrap_engaged(StateKind.KS, killswitch_adopter(switches))
        assert report.ok, report.error
        assert switches.org_killed("org-7") is True
        return report.applied

    applied = _drive(drill, body)

    assert applied == 1, "one engaged scope read out of 201 kill-switch records"


def test_drill_bulk_onboard_of_twenty_five_thousand_records(drill: Drill) -> None:
    """The bulk path at the scale the card names, with bounded worker rounds."""
    outcome = drill.writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 25_001)],
    )

    assert outcome.status == OK
    assert outcome.records == 25_000

    async def body() -> int:
        worker = drill.worker(budget=DeltaBudget(records=2_000))
        rounds = 0
        while True:
            rounds += 1
            report = await worker.round_once(StateKind.PLAN)
            assert report.ok, report.error
            assert report.applied <= 2_000, "a round never exceeds its budget"
            if not report.truncated:
                break
        return rounds

    rounds = _drive(drill, body)

    assert rounds == 13
    assert len(drill.plans.known()) == 25_000
    assert drill.rehydrator.diagnose(StateKind.PLAN) is None


def test_drill_a_steady_round_at_twenty_five_thousand_is_three_commands(drill: Drill) -> None:
    """The headline cost claim, against real infrastructure at the card's top tenant count."""
    drill.writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 25_001)],
    )

    async def body() -> int:
        reader = ValkeyStateStore(drill.async_client(), drill.keys)
        feed = FeedReader(reader, SECRET)
        cursor = START
        while True:
            round_ = await feed.poll(StateKind.PLAN, cursor, limit=5_000)
            cursor = round_.cursor
            if not round_.truncated:
                break
        settled = await feed.poll(StateKind.PLAN, cursor, limit=5_000)
        assert settled.records == ()
        return len(settled.records)

    assert _drive(drill, body) == 0


def _drive[T](drill: Drill, body: object) -> T:
    async def wrapped() -> T:
        try:
            return await body()  # type: ignore[no-any-return, operator]
        finally:
            await drill.aclose()

    return asyncio.run(wrapped())


@pytest.mark.skipif(not PG_CONTAINER, reason="AMF_DRILL_PG_CONTAINER is not set")
def test_drill_a_paused_postgres_does_not_fail_the_fleet_closed(drill: Drill) -> None:
    """GW05b phase 5 against a real paused database: the local analogue of L05b-4.

    Phase 4 made every kind refuse unverified state, which on its own turns a routine 11-16 s
    Cloud SQL failover into a fleet-wide 503 -- the reference patch measured about 7.4 s of
    global 503 on a 10 s freeze. This drill is the one that would catch that regression: a
    PAUSED Postgres container accepts connections and never answers, which is the failover
    shape, and the gateway must stay fresh throughout.

    The clock is injected so the drill does not have to sit through 16 s of real time, but the
    database outage, the store and the re-hydrator are all real.
    """
    wall = [1_000.0]
    view = StampView(SECRET, fresh_ms=5_000, started_at=999.0, clock=lambda: wall[0])
    snapshot = ReplicaSnapshot(drill.plans, clock=lambda: 1.0, stamp=view)
    rehydrator = drill.stamping_rehydrator(
        lambda: wall[0], db=drill.tightly_bounded_db(),
    )

    for position in range(1, 6):
        drill.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    assert rehydrator.round_once().stamped is not None

    async def body() -> None:
        worker = drill.worker(stamp=view)
        assert (await worker.drain_all())[0].ok
        assert isinstance(snapshot.lookup("org-3"), ExecutionPlan)

        _docker("pause", PG_CONTAINER)
        try:
            # Two rounds across 8 s: past the 5 s freshness bound, inside the 16 s grace.
            for _ in range(2):
                wall[0] += 4.0
                summary = rehydrator.round_once()
                assert summary.ok is False, "the round genuinely failed"
                stamp = summary.stamped
                assert stamp is not None and stamp.degraded is True
                await worker.drain_all()
                assert view.fresh() is True
                assert isinstance(snapshot.lookup("org-3"), ExecutionPlan)

            # Past the grace, the fleet is allowed -- required -- to fail closed.
            wall[0] += PG_GRACE_MS / 1000
            rehydrator.round_once()
            await worker.drain_all()
            assert view.fresh() is False
            assert isinstance(snapshot.lookup("org-3"), PlanUnavailable)
        finally:
            _docker("unpause", PG_CONTAINER)

        wall[0] += 1.0
        for _ in range(40):
            if rehydrator.round_once().stamped is not None:
                break
            await asyncio.sleep(0.1)
        await worker.drain_all()

        assert view.fresh() is True, "and recovers once the database answers again"
        assert view.degraded is False
        assert isinstance(snapshot.lookup("org-3"), ExecutionPlan)

    _drive(drill, body)


# --- GW05b phase 7: the fault scenarios freshness is supposed to survive -------------------------
#
# These measure the two bounds the card is signed on: how long until the fleet fails closed when
# verification stops, and how long until it serves again once verification returns. Both are
# reported as numbers rather than asserted loosely, because L05b-2 and L05b-6 are stated as
# bounds and a drill that only asserts "eventually" cannot contradict them.


def _freshness_lab(
    drill: Drill,
    *,
    fresh_ms: int = 5_000,
) -> tuple[list[float], StampView, ReplicaSnapshot, Rehydrator]:
    wall = [1_000.0]
    view = StampView(SECRET, fresh_ms=fresh_ms, started_at=999.0, clock=lambda: wall[0])
    snapshot = ReplicaSnapshot(drill.plans, clock=lambda: 1.0, stamp=view)
    rehydrator = drill.stamping_rehydrator(lambda: wall[0])
    return wall, view, snapshot, rehydrator


@pytest.mark.skipif(not VALKEY_CONTAINER, reason="AMF_DRILL_VALKEY_CONTAINER is not set")
def test_drill_a_flushed_store_fails_closed_then_restores_freshness(drill: Drill) -> None:
    """R2-13's flush drill, with the freshness dimension GW05c's version could not have.

    A FLUSHALL takes the stamp with it, so the fleet loses verification as well as data. Both
    have to come back, and the stamp must not come back before the data it attests.
    """
    wall, view, snapshot, rehydrator = _freshness_lab(drill)
    for position in range(1, 6):
        drill.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    assert rehydrator.round_once().stamped is not None

    async def body() -> None:
        worker = drill.worker(stamp=view)
        assert (await worker.drain_all())[0].ok
        assert view.fresh() is True

        drill.drop_store()
        wall[0] += 1.0
        await worker.drain_all()

        assert view.observe(drill.publisher.read_stamp()) is False, "the stamp went too"
        wall[0] += 6.0
        assert view.fresh() is False, "and the fleet fails closed"
        assert isinstance(snapshot.lookup("org-3"), PlanUnavailable)

        restored = rehydrator.round_once()
        assert {event.kind for event in restored.repairs} == set(StateKind)
        assert restored.stamped is not None
        await worker.drain_all()

        assert view.fresh() is True
        assert isinstance(snapshot.lookup("org-3"), ExecutionPlan)
        assert snapshot.lookup("org-5").__class__ is ExecutionPlan, "all five, not just some"

    _drive(drill, body)


@pytest.mark.skipif(not VALKEY_CONTAINER, reason="AMF_DRILL_VALKEY_CONTAINER is not set")
def test_drill_a_partitioned_store_ages_freshness_out_and_recovers(drill: Drill) -> None:
    """A PAUSED store accepts the connection and never answers -- R2-13's partition shape.

    The worker cannot read the stamp, so freshness ages out on its own: the fail-closed window
    does not depend on the store being reachable enough to tell us it is broken.
    """
    wall, view, snapshot, rehydrator = _freshness_lab(drill)
    drill.writer.plan_set("org-1", _plan_body("org-1"))
    assert rehydrator.round_once().stamped is not None

    async def body() -> float:
        worker = drill.worker(stamp=view)
        assert (await worker.drain_all())[0].ok
        assert view.fresh() is True

        _docker("pause", VALKEY_CONTAINER)
        try:
            wall[0] += 6.0
            with contextlib.suppress(Exception):
                await worker.drain_all()
            assert view.fresh() is False
            assert isinstance(snapshot.lookup("org-1"), PlanUnavailable)
        finally:
            _docker("unpause", VALKEY_CONTAINER)

        healed = drill.worker(stamp=view)
        started = time.monotonic()
        for _ in range(40):
            wall[0] += 0.1
            rehydrator.round_once()
            if (await healed.drain_all())[0].ok and view.fresh():
                break
            await asyncio.sleep(0.1)
        recovery_s = time.monotonic() - started

        assert view.fresh() is True
        assert isinstance(snapshot.lookup("org-1"), ExecutionPlan)
        return recovery_s

    recovery_s = _drive(drill, body)

    assert recovery_s < 5.0, f"freshness took {recovery_s:.2f}s to return after the partition"


def test_drill_both_rehydrators_gone_fails_the_fleet_closed(drill: Drill) -> None:
    """L05b-6, locally: global fail-closed at the declared bound, recovery after one returns.

    Two re-hydrators are the deployment rule (R2-04), so the drill kills BOTH -- one surviving
    re-hydrator is the normal case and proves nothing about the bound.
    """
    wall, view, snapshot, _unused = _freshness_lab(drill)
    zone_a = drill.stamping_rehydrator(lambda: wall[0])
    zone_b = drill.stamping_rehydrator(lambda: wall[0])
    drill.writer.plan_set("org-1", _plan_body("org-1"))
    zone_a.round_once()
    zone_b.round_once()

    async def body() -> tuple[float, float]:
        worker = drill.worker(stamp=view)
        assert (await worker.drain_all())[0].ok
        assert view.fresh() is True

        # Both re-hydrators stop. Nothing stamps; the store is otherwise perfectly healthy.
        failed_at = None
        for step in range(1, 101):
            wall[0] += 0.1
            await worker.drain_all()
            if not view.fresh():
                failed_at = step * 0.1
                break
        assert failed_at is not None, "the fleet never failed closed"
        assert isinstance(snapshot.lookup("org-1"), PlanUnavailable)

        # One returns.
        recovered_at = None
        for step in range(1, 101):
            wall[0] += 0.1
            zone_b.round_once()
            await worker.drain_all()
            if view.fresh():
                recovered_at = step * 0.1
                break
        assert recovered_at is not None, "one re-hydrator was not enough"
        assert isinstance(snapshot.lookup("org-1"), ExecutionPlan)
        return failed_at, recovered_at

    failed_at, recovered_at = _drive(drill, body)

    # The stamp was fresh at T0, so it ages out one freshness bound later, not sooner.
    assert 5.0 <= failed_at <= 5.3, f"failed closed after {failed_at:.1f}s"
    assert recovered_at <= 0.2, f"recovered {recovered_at:.1f}s after a re-hydrator returned"


@pytest.mark.skipif(
    not (PG_CONTAINER and VALKEY_CONTAINER), reason="both drill containers must be set",
)
def test_drill_a_flush_under_a_held_row_lock_still_restores_and_stamps(drill: Drill) -> None:
    """L05b-3: an idle `FOR UPDATE` held across a FLUSHALL must not stall re-hydration.

    GW05c proved the lock half. This adds the flush on top, which is the combination the card
    names: RC2 took 38 s because its publish snapshot waited behind the lock, so the restore and
    the stamp both arrive only after the lock is released.
    """
    import psycopg

    wall, view, _snapshot, rehydrator = _freshness_lab(drill)
    for position in range(1, 4):
        drill.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    assert rehydrator.round_once().stamped is not None

    drill.drop_store()
    with psycopg.connect(DSN, autocommit=False) as holder, holder.cursor() as cursor:
        cursor.execute(f'SET search_path TO "{drill.schema}"')
        cursor.execute("SELECT * FROM amf_state_counter WHERE kind = 'plan' FOR UPDATE")
        started = time.monotonic()
        wall[0] += 1.0
        summary = rehydrator.round_once()
        took_s = time.monotonic() - started
        holder.rollback()

    assert summary.stamped is not None, "the stamp arrived while the lock was still held"
    assert {event.kind for event in summary.repairs} == set(StateKind)
    assert took_s < 5.0, f"re-hydration under a held lock took {took_s:.2f}s"
    assert view.observe(drill.publisher.read_stamp()) is True
    assert view.fresh() is True
