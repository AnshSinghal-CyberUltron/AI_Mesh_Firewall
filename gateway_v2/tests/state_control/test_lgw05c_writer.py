"""GW05c phase 4a — a write publishes one record, and the whole loop is exercised end to end.

`test_one_write_touches_one_record_with_ten_thousand_published` is the guard for R2-02's third
defect: RC2 republished a kind's complete record set on every write (3.5 MB / 23.8 ms at 10,000
keys, 17.4 MB / 124 ms at 50,000, live publish p50 1.72 s at 25,000 tenants).

The `end to end` section is the one that proves the card rather than a component of it: the real
writer, the real FeedReader and the real appliers, wired together over an in-memory twin that is
both a publisher and a reader store. No Postgres, no Valkey, no assumptions traded between the
two planes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

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
from gateway_v2.domain.state import StateKind, StateOp, Version
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader
from gateway_v2.runtime.state_task import (
    DEFAULT_BUDGET,
    DeltaBudget,
    RoundReport,
    StateSynchroniser,
)
from state_control.db import ZERO_COUNTERS, CommitUnknown, MemoryControlDB, advance
from state_control.publisher import BrokenPublisher, MemoryStore
from state_control.writer import OK, OK_PUBLISH_PENDING, StateWriter
from tests.plan.test_lgw05c_delta import _draft

SECRET = b"gw05c-writer-test-secret"


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _writer(
    db: MemoryControlDB | None = None,
    store: MemoryStore | None = None,
) -> tuple[StateWriter, MemoryControlDB, MemoryStore]:
    database = MemoryControlDB() if db is None else db
    published = MemoryStore() if store is None else store
    return StateWriter(database, published, SECRET), database, published


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


class Gateway:
    """The data-plane side, wired exactly as a worker would be."""

    def __init__(self, store: MemoryStore, *, budget: DeltaBudget | None = None) -> None:
        self.plans = PlanStore()
        self.snapshot = ReplicaSnapshot(self.plans, clock=lambda: 1.0)
        self.switches = KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0)
        self.keys = IdentityCache(clock=lambda: 0.0)
        self.sync = StateSynchroniser(
            FeedReader(store, SECRET),
            {
                StateKind.PLAN: plan_applier(self.plans, self.snapshot, clock=lambda: 1.0),
                StateKind.KS: killswitch_applier(self.switches),
                StateKind.KEY: identity_applier(self.keys),
            },
            budget=DEFAULT_BUDGET if budget is None else budget,
        )

    def poll(self, kind: StateKind) -> RoundReport:
        report = _run(self.sync.drain(kind))
        assert report.ok, report.error
        return report

    def round_once(self, kind: StateKind) -> RoundReport:
        """Unchecked, for the tests that expect a refusal."""
        return _run(self.sync.round_once(kind))

    def cold_start_switches(self) -> RoundReport:
        return _run(self.sync.bootstrap_engaged(StateKind.KS, killswitch_adopter(self.switches)))


# --- the cost shape ----------------------------------------------------------------------------


def test_one_write_touches_one_record_with_ten_thousand_published() -> None:
    """R2-02: a write must cost the same at 3 tenants and at 25,000."""
    writer, _db, store = _writer()
    writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 10_001)],
    )
    store.reset_counters()

    outcome = writer.plan_set("org-7", _plan_body("org-7"))

    assert outcome.status == OK
    assert outcome.records == 1
    assert store.record_writes == 1, "one record written, not 10,000"
    assert store.whole_kind_publishes == 0
    assert store.nudges == [(StateKind.PLAN, 10_001)]


def test_the_write_path_never_republishes_a_kind() -> None:
    """publish_kind exists for the re-hydrator restoring a flushed store, and only for it."""
    writer, _db, store = _writer()

    writer.plan_set("org-a", _plan_body("org-a"))
    writer.key_add("hash-a", "org-a", key_id="k1", rate_per_s=1.0, burst=2.0)
    writer.killswitch("org:org-a", on=True)
    writer.killswitch("org:org-a", on=False)
    writer.key_revoke("hash-a")
    writer.plan_offboard("org-a")

    assert store.whole_kind_publishes == 0
    assert store.record_writes == 6


def test_a_bulk_onboard_is_one_manifest_and_one_nudge() -> None:
    """25,000 per-record publishes would make a worker see 25,000 generations."""
    writer, _db, store = _writer()

    outcome = writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 1_001)],
    )

    assert outcome.status == OK
    assert outcome.records == 1_000
    assert outcome.feed_seq == 1_000, "one score per record, so each is selectable"
    assert len(store.nudges) == 1, "one nudge for the whole batch"
    assert store.whole_kind_publishes == 0


# --- counters ----------------------------------------------------------------------------------


def test_count_rises_on_a_new_record_and_not_on_an_update() -> None:
    writer, db, _store = _writer()

    writer.plan_set("org-a", _plan_body("org-a"))
    assert db.counters(StateKind.PLAN).count == 1

    writer.plan_set("org-a", _plan_body("org-a"))
    assert db.counters(StateKind.PLAN).count == 1, "an update is not a new record"

    writer.plan_set("org-b", _plan_body("org-b"))
    assert db.counters(StateKind.PLAN).count == 2


def test_count_does_not_fall_when_a_record_is_offboarded() -> None:
    """C36 keeps an explicit OFF record, and a reader relies on count never falling."""
    writer, db, _store = _writer()
    writer.plan_set("org-a", _plan_body("org-a"))

    writer.plan_offboard("org-a")

    assert db.counters(StateKind.PLAN).count == 1
    assert db.counters(StateKind.PLAN).feed_seq == 2


def test_on_count_tracks_engagement_both_ways() -> None:
    writer, db, _store = _writer()

    writer.killswitch("org:a", on=True)
    assert db.counters(StateKind.KS).on_count == 1

    writer.killswitch("org:b", on=True)
    assert db.counters(StateKind.KS).on_count == 2

    writer.killswitch("org:a", on=False)
    assert db.counters(StateKind.KS).on_count == 1

    writer.killswitch("org:a", on=False)
    assert db.counters(StateKind.KS).on_count == 1, "a repeated disengage must not go negative"


def test_a_key_write_does_not_move_on_count() -> None:
    writer, db, _store = _writer()

    writer.key_add("hash-a", "org-a", key_id="k", rate_per_s=1.0, burst=1.0)

    assert db.counters(StateKind.KEY).on_count == 0


def test_advance_is_pure_arithmetic() -> None:
    moved = advance(ZERO_COUNTERS, epoch_bump=False, is_new=True, engaged_delta=1)

    assert moved.version == Version(1, 1)
    assert (moved.feed_seq, moved.count, moved.on_count) == (1, 1, 1)

    bumped = advance(moved, epoch_bump=True, is_new=False, engaged_delta=0)
    assert bumped.version == Version(2, 0), "a rollback raises epoch and resets seq"
    assert bumped.feed_seq == 2, "but the cursor never resets"


# --- honest outcomes ---------------------------------------------------------------------------


def test_a_failed_publish_is_durable_and_reported_as_pending() -> None:
    """After a commit NOTHING may turn the write into a reported failure."""
    db = MemoryControlDB()
    broken = BrokenPublisher()
    writer = StateWriter(db, broken, SECRET)

    outcome = writer.plan_set("org-a", _plan_body("org-a"))

    assert outcome.status == OK_PUBLISH_PENDING
    assert outcome.durable is True
    assert outcome.published is False
    assert broken.attempts == 1
    assert db.counters(StateKind.PLAN).feed_seq == 1, "the commit stands"


def test_a_failed_batch_publish_is_durable_and_reported_as_pending() -> None:
    db = MemoryControlDB()
    writer = StateWriter(db, BrokenPublisher(), SECRET)

    outcome = writer.put_many(StateKind.PLAN, [("org-a", _plan_body("org-a"))])

    assert outcome.status == OK_PUBLISH_PENDING
    assert db.counters(StateKind.PLAN).count == 1


def test_a_failed_transaction_leaves_no_trace() -> None:
    writer, db, store = _writer()
    db.fail_next = True

    with pytest.raises(ConnectionError):
        writer.plan_set("org-a", _plan_body("org-a"))

    assert db.counters(StateKind.PLAN) == ZERO_COUNTERS
    assert store.record_writes == 0


def test_a_lost_commit_acknowledgement_is_unknown_not_failure() -> None:
    writer, db, _store = _writer()
    db.commit_unknown_next = True

    with pytest.raises(CommitUnknown):
        writer.plan_set("org-a", _plan_body("org-a"))

    assert db.counters(StateKind.PLAN).count == 1, "the write WAS durable"


def test_a_store_already_ahead_is_not_overwritten() -> None:
    """A slow publisher of an older generation loses to the newer one.

    Two writers share one store; the second has its own (empty) source of truth, so its
    manifest is behind what the store already holds. The publish is skipped and reported
    pending rather than moving the store backwards.
    """
    store = MemoryStore()
    ahead, _db_a, _ = _writer(store=store)
    ahead.plan_set("org-a", _plan_body("org-a"))
    ahead.plan_set("org-b", _plan_body("org-b"))
    ahead.plan_set("org-c", _plan_body("org-c"))
    assert store.stored_feed_seq(StateKind.PLAN) == 3

    behind, _db_b, _ = _writer(store=store)
    outcome = behind.plan_set("org-d", _plan_body("org-d"))

    assert outcome.status == OK_PUBLISH_PENDING, "the older generation did not land"
    assert outcome.durable is True
    assert store.stored_feed_seq(StateKind.PLAN) == 3, "the store never moved backwards"


def test_an_empty_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="signing secret is required"):
        StateWriter(MemoryControlDB(), MemoryStore(), b"")


def test_an_empty_batch_is_a_no_op() -> None:
    writer, db, store = _writer()

    outcome = writer.put_many(StateKind.PLAN, [])

    assert outcome.records == 0
    assert store.record_writes == 0
    assert db.counters(StateKind.PLAN) == ZERO_COUNTERS


# --- rollback ----------------------------------------------------------------------------------


def test_a_rollback_is_a_new_version_at_a_new_epoch() -> None:
    writer, db, _store = _writer()
    writer.plan_set("org-a", _plan_body("org-a"))
    first = db.log()[0]
    writer.plan_set("org-a", _plan_body("org-a"))

    outcome = writer.rollback(first.log_id)

    assert outcome.version == Version(2, 0), "older CONTENT, never an older version"
    assert outcome.feed_seq == 3, "and the cursor still moves forward"
    assert outcome.status == OK


def test_rolling_back_an_unknown_write_is_an_error() -> None:
    writer, _db, _store = _writer()

    with pytest.raises(KeyError, match="no logged write"):
        writer.rollback(99)


def test_a_revocation_of_an_unknown_key_still_writes_an_off_record() -> None:
    writer, db, _store = _writer()

    outcome = writer.key_revoke("never-issued")

    assert outcome.status == OK
    with db.tx() as tx:
        record = tx.record(StateKind.KEY, "never-issued")
    assert record is not None
    assert record.deleted is True
    assert record.op is StateOp.REVOKE


# --- end to end: the real writer, the real reader ------------------------------------------------


def test_a_plan_write_reaches_the_gateway_and_is_served() -> None:
    writer, _db, store = _writer()
    gateway = Gateway(store)

    writer.plan_set("org-a", _plan_body("org-a"))
    gateway.poll(StateKind.PLAN)

    served = gateway.plans.read("org-a")
    assert isinstance(served, ExecutionPlan)
    assert served.org_id == "org-a"
    assert served.rules[0].rule_id == "r1"


def test_one_plan_change_among_ten_thousand_costs_one_record_end_to_end() -> None:
    writer, _db, store = _writer()
    writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 10_001)],
    )
    gateway = Gateway(store, budget=DeltaBudget(records=10_000))
    gateway.poll(StateKind.PLAN)
    store.reset_counters()

    writer.plan_set("org-4242", _plan_body("org-4242"))
    gateway.poll(StateKind.PLAN)

    assert store.record_writes == 1, "the control plane published one record"
    served = gateway.plans.read("org-4242")
    assert isinstance(served, ExecutionPlan)
    assert served.feed_seq == 10_001


def test_an_offboard_reaches_the_gateway_as_an_unknown_tenant() -> None:
    writer, _db, store = _writer()
    gateway = Gateway(store)
    writer.plan_set("org-a", _plan_body("org-a"))
    gateway.poll(StateKind.PLAN)

    writer.plan_offboard("org-a")
    gateway.poll(StateKind.PLAN)

    assert isinstance(gateway.plans.read("org-a"), PlanUnknownTenant)


def test_a_kill_switch_engage_reaches_the_gateway() -> None:
    writer, _db, store = _writer()
    gateway = Gateway(store)
    writer.killswitch("global", on=False)
    gateway.cold_start_switches()
    assert gateway.switches.state(100.0) is KillSwitchState.OK

    writer.killswitch("global", on=True)
    gateway.poll(StateKind.KS)

    assert gateway.switches.state(100.0) is KillSwitchState.ENGAGED


def test_an_org_kill_switch_survives_a_cold_start_at_scale() -> None:
    """Gate G-04 end to end: 5,000 scopes published, one engaged, cold start reads one."""
    writer, _db, store = _writer()
    writer.put_many(
        StateKind.KS,
        [(f"org:t{n}", {"on": False}) for n in range(1, 5_001)],
        engaged={},
    )
    writer.killswitch("org:acme", on=True)
    gateway = Gateway(store)
    store.reset_counters()

    report = gateway.cold_start_switches()

    assert report.ok, report.error
    assert gateway.switches.org_killed("acme") is True
    assert gateway.switches.view().count == 1


def test_a_key_revocation_evicts_exactly_that_key_end_to_end() -> None:
    writer, _db, store = _writer()
    gateway = Gateway(store)
    writer.key_add("hash-a", "org-a", key_id="k-a", rate_per_s=1.0, burst=2.0)
    writer.key_add("hash-b", "org-b", key_id="k-b", rate_per_s=1.0, burst=2.0)
    gateway.poll(StateKind.KEY)
    gateway.keys._admit("hash-a", Principal("k-a", "org-a", 1.0, 2.0, 1))
    gateway.keys._admit("hash-b", Principal("k-b", "org-b", 1.0, 2.0, 2))

    writer.key_revoke("hash-a")
    gateway.poll(StateKind.KEY)

    assert gateway.keys.cached("hash-a") is None
    assert gateway.keys.cached("hash-b") is not None, "an unrelated key survives"


def test_a_flushed_store_fails_closed_and_a_whole_kind_restore_recovers_it() -> None:
    """The re-hydrator's repair path, which is the ONLY legitimate whole-kind publish."""
    writer, db, store = _writer()
    gateway = Gateway(store)
    writer.plan_set("org-a", _plan_body("org-a"))
    gateway.poll(StateKind.PLAN)

    store.flush()
    report = gateway.round_once(StateKind.PLAN)
    assert report.ok is False
    assert "manifest missing" in (report.error or "")

    counters, records, engaged = db.snapshot(StateKind.PLAN)
    store.publish_kind(
        StateKind.PLAN,
        records,
        writer._manifest(StateKind.PLAN, counters),
        engaged,
    )
    recovered = gateway.round_once(StateKind.PLAN)

    assert recovered.ok, recovered.error
    assert store.whole_kind_publishes == 1
    assert isinstance(gateway.plans.read("org-a"), ExecutionPlan)


def test_a_bulk_onboard_converges_on_the_gateway_over_bounded_rounds() -> None:
    writer, _db, store = _writer()
    writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 501)],
    )
    gateway = Gateway(store, budget=DeltaBudget(records=100))

    rounds = 0
    while True:
        rounds += 1
        report = gateway.round_once(StateKind.PLAN)
        assert report.ok, report.error
        if not report.truncated:
            break

    assert rounds == 5
    assert len(gateway.plans.known()) == 500


def test_a_record_signed_with_another_secret_is_unavailable_to_the_gateway() -> None:
    """One signing implementation across both planes, so a mismatched signer is detectable."""
    writer, _db, store = _writer()
    gateway = Gateway(store)
    writer.plan_set("org-a", _plan_body("org-a"))
    gateway.poll(StateKind.PLAN)

    forged = StateWriter(MemoryControlDB(), store, b"a-different-secret")
    forged.plan_set("org-b", _plan_body("org-b"))
    report = gateway.round_once(StateKind.PLAN)

    assert report.ok is False
    assert "signature" in (report.error or "")
    assert isinstance(gateway.plans.read("org-a"), ExecutionPlan), "org-a keeps serving"


def test_the_writer_cannot_reach_the_whole_kind_publish() -> None:
    """A structural guard, because the behavioural one only covers calls I thought to make.

    `publish_kind` is O(records) by definition. It is correct for the re-hydrator restoring a
    flushed store, where the whole kind IS the change, and wrong everywhere else. This asserts
    on the AST which publisher methods the write path can call at all, so a future write path
    that reaches for it fails here rather than in a load test at 25,000 tenants.
    """
    import ast
    import inspect

    import state_control.writer as writer_module

    tree = ast.parse(inspect.getsource(writer_module))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_publisher"
    }

    assert called == {"publish_record", "publish_batch"}, (
        f"the write path may only publish per record or per batch, found {sorted(called)}"
    )
