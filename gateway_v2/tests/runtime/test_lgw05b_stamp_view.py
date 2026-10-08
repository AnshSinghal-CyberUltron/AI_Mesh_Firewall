"""GW05b phase 3: a process starts from a verified floor, never from zero. This closes SP1.

Two tests carry the phase.

`test_a_stamp_from_before_this_process_started_is_not_fresh` is the start gate, and it is the
part that is easy to get wrong by reasoning only about floors. After a store failover the stamp
sitting on the lagging replica is ITSELF old, so a process that merely adopted its floor would
serve old state believing it was verified. The floor must rise AND the stamp must post-date the
process.

`test_a_fresh_process_refuses_a_lagging_replica_without_reading_a_record` is SP1 itself, with the
command-counting guard attached: a refused round must not pay for the records it refused.

Phase 3 wires floors and freshness evaluation. It does NOT yet turn `fresh()` into a 503 -- that
is phase 4 -- so nothing here asserts a request outcome.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import fakeredis
import pytest

from gateway_v2.domain.plan import StreamingMode
from gateway_v2.domain.state import (
    START,
    ZERO,
    Cursor,
    SignedRecord,
    StateKind,
    Version,
)
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.runtime.state_feed import FeedReader, FeedRound
from gateway_v2.runtime.state_sig import encode_stamp, make_stamp
from gateway_v2.runtime.state_stamp import (
    FRESHNESS_NOT_ENFORCED,
    NO_STAMP_SEEN,
    StampView,
    state_ready,
)
from gateway_v2.runtime.state_task import DeltaBudget, StateSynchroniser
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.store_valkey import ValkeyStateStore
from state_control.db import MemoryControlDB
from state_control.rehydrate import Rehydrator
from state_control.valkey import ValkeyPublisher
from state_control.writer import StateWriter
from tests.plan.test_lgw05c_delta import _draft
from tests.runtime.test_lgw05c_feed import SECRET, FakeStore, _record, _seed

OTHER_SECRET = b"gw05b-rotated-secret"
BY = "rehydrator-a:4711"


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _cursors(**per_kind: Cursor) -> dict[StateKind, Cursor]:
    base = {kind: Cursor(Version(1, 1), 1) for kind in StateKind}
    for name, cursor in per_kind.items():
        base[StateKind(name)] = cursor
    return base


def _raw(
    moment: float,
    cursors: dict[StateKind, Cursor] | None = None,
    *,
    secret: bytes = SECRET,
    by: str = BY,
    deep: bool = False,
    degraded: bool = False,
) -> bytes:
    return encode_stamp(
        make_stamp(
            secret, moment, cursors or _cursors(), by, deep=deep, degraded=degraded,
        ),
    )


def _view(*, started_at: float = 100.0, now: float = 100.0, fresh_ms: int = 5_000) -> StampView:
    """A view whose clock is explicit, so freshness never depends on real time passing."""
    ticks = [now]
    return StampView(
        SECRET, fresh_ms=fresh_ms, started_at=started_at, clock=lambda: ticks[0],
    )


# --- nothing seen yet ----------------------------------------------------------------------------


def test_a_view_with_no_stamp_is_not_fresh_and_says_why() -> None:
    view = _view()

    assert view.fresh() is False
    assert view.age_seconds() is None
    assert NO_STAMP_SEEN in view.unverified()
    assert view.verified_at is None


def test_a_view_with_no_stamp_floors_every_kind_at_start() -> None:
    view = _view()

    for kind in StateKind:
        assert view.floor(kind) == START


def test_an_absent_stamp_is_counted_as_missing_not_invalid() -> None:
    """The two mean different things: absent usually means no re-hydrator is running."""
    view = _view()

    assert view.observe(None) is False

    assert (view.missing, view.invalid) == (1, 0)


def test_a_forged_stamp_is_counted_as_invalid_and_changes_nothing() -> None:
    view = _view()

    assert view.observe(_raw(101.0, secret=OTHER_SECRET)) is False

    assert (view.missing, view.invalid) == (0, 1)
    assert view.fresh() is False
    assert view.floor(StateKind.PLAN) == START


def test_a_partial_stamp_changes_nothing() -> None:
    """Enforced in `decode_stamp`; asserted here because the VIEW must not half-adopt it."""
    view = _view()
    partial = b'{"verified_at_ms":101000,"cursors":{"plan":[1,1,1]},"by":"a","deep":false,' \
              b'"degraded":false,"sig":"x"}'

    assert view.observe(partial) is False

    assert view.floor(StateKind.PLAN) == START


# --- the start gate (I3): the part that actually closes SP1 --------------------------------------


def test_a_stamp_from_before_this_process_started_is_not_fresh() -> None:
    """A floor alone does not close SP1, and this is the test that proves it.

    After a failover the stamp on the lagging replica is old too. The floor it offers is
    adopted -- floors only rise, and an old floor is still better than none -- but the process
    must NOT consider itself verified until a re-hydrator has looked at the store it is now
    reading.
    """
    view = _view(started_at=100.0, now=100.5)

    assert view.observe(_raw(99.0)) is True, "the stamp is genuine, so it is adopted"

    assert view.floor(StateKind.PLAN) == Cursor(Version(1, 1), 1), "the floor still rose"
    assert view.fresh() is False, "but a pre-start round says nothing about THIS store"
    assert "started after this process" in view.unverified()


def test_a_stamp_from_after_this_process_started_is_fresh() -> None:
    view = _view(started_at=100.0, now=100.5)

    assert view.observe(_raw(100.2)) is True

    assert view.fresh() is True
    assert view.by == BY


def test_a_round_that_started_within_the_start_millisecond_counts_as_after() -> None:
    """Stamps carry whole milliseconds, so without the snap this boundary is a coin toss."""
    view = _view(started_at=100.000_4, now=100.5)

    assert view.observe(_raw(100.0)) is True

    assert view.fresh() is True


# --- the freshness bound -------------------------------------------------------------------------


def test_a_stamp_older_than_the_bound_is_not_fresh() -> None:
    view = StampView(SECRET, fresh_ms=5_000, started_at=100.0, clock=lambda: 110.0)

    view.observe(_raw(104.0))

    assert view.age_seconds() == pytest.approx(6.0)
    assert view.fresh() is False
    assert "over the 5 s bound" in view.unverified()


def test_a_stamp_exactly_at_the_bound_is_still_fresh() -> None:
    view = StampView(SECRET, fresh_ms=5_000, started_at=100.0, clock=lambda: 110.0)

    view.observe(_raw(105.0))

    assert view.fresh() is True


def test_freshness_recovers_on_the_next_good_stamp() -> None:
    moment = [110.0]
    view = StampView(SECRET, fresh_ms=5_000, started_at=100.0, clock=lambda: moment[0])
    view.observe(_raw(104.0))
    assert view.fresh() is False

    view.observe(_raw(109.5))

    assert view.fresh() is True


def test_a_stamp_from_the_future_beyond_the_bound_is_not_fresh() -> None:
    """Otherwise one re-hydrator with a skewed clock pins the whole fleet "fresh" for ever."""
    view = StampView(SECRET, fresh_ms=5_000, started_at=100.0, clock=lambda: 110.0)

    view.observe(_raw(130.0))

    assert view.fresh() is False


def test_freshness_can_be_disabled_but_floors_still_apply() -> None:
    """The break-glass turns off the 503, not the anti-regress rule.

    A floor costs nothing and only ever refuses data that is genuinely behind, so there is no
    reason for the escape hatch to give that up as well.
    """
    view = StampView(SECRET, fresh_ms=0, started_at=100.0, clock=lambda: 10_000.0)

    assert view.fresh() is True
    assert view.unverified() == FRESHNESS_NOT_ENFORCED
    assert view.enforced is False

    view.observe(_raw(99.0, _cursors(plan=Cursor(Version(2, 7), 40))))

    assert view.floor(StateKind.PLAN) == Cursor(Version(2, 7), 40)


# --- monotonicity (I5) ---------------------------------------------------------------------------


def test_a_floor_never_falls() -> None:
    view = _view()
    view.observe(_raw(101.0, _cursors(plan=Cursor(Version(2, 50), 50))))

    view.observe(_raw(102.0, _cursors(plan=Cursor(Version(1, 10), 10))))

    assert view.floor(StateKind.PLAN) == Cursor(Version(2, 50), 50)


def test_verified_at_never_moves_backwards() -> None:
    """A slower second re-hydrator's stamp must not rewind the freshness clock."""
    view = _view(started_at=100.0, now=103.0)
    view.observe(_raw(102.0, by="zone-b"))

    view.observe(_raw(101.0, by="zone-a"))

    assert view.verified_at == 102.0
    assert view.by == "zone-b"


def test_a_higher_version_with_a_lower_position_is_not_adopted_piecemeal() -> None:
    """Adopting the half that rose would build a floor no single generation ever had."""
    view = _view()
    view.observe(_raw(101.0, _cursors(plan=Cursor(Version(1, 30), 30))))

    view.observe(_raw(102.0, _cursors(plan=Cursor(Version(5, 0), 9))))

    assert view.floor(StateKind.PLAN) == Cursor(Version(1, 30), 30)


def test_each_kind_carries_its_own_floor() -> None:
    view = _view()

    view.observe(
        _raw(101.0, _cursors(plan=Cursor(Version(3, 9), 90), ks=Cursor(Version(1, 2), 4))),
    )

    assert view.floor(StateKind.PLAN) == Cursor(Version(3, 9), 90)
    assert view.floor(StateKind.KS) == Cursor(Version(1, 2), 4)


def test_the_deep_and_degraded_flags_are_surfaced() -> None:
    view = StampView(SECRET, fresh_ms=5_000, started_at=100.0, clock=lambda: 102.0)

    view.observe(_raw(101.0, deep=True, degraded=True))

    assert view.deep_age_seconds() == pytest.approx(1.0)
    assert view.degraded is True


def test_a_shallow_round_does_not_refresh_the_deep_age() -> None:
    """The gap between DEEP rounds has to stay alarmable on its own."""
    view = StampView(SECRET, fresh_ms=5_000, started_at=100.0, clock=lambda: 110.0)
    view.observe(_raw(101.0, deep=True))

    view.observe(_raw(109.0, deep=False))

    assert view.deep_age_seconds() == pytest.approx(9.0)
    assert view.age_seconds() == pytest.approx(1.0)


# --- readiness -----------------------------------------------------------------------------------


def test_readiness_is_false_while_state_is_unverified() -> None:
    view = _view()

    ready, why = state_ready(view)

    assert ready is False
    assert why is not None and NO_STAMP_SEEN in why


def test_readiness_is_true_once_a_stamp_lands() -> None:
    view = _view(started_at=100.0, now=100.5)
    view.observe(_raw(100.2))

    assert state_ready(view) == (True, None)


def test_readiness_without_a_configured_view_is_true() -> None:
    """A deployment that has not wired freshness must still be able to start."""
    assert state_ready(None) == (True, None)


# --- the floor reaching the synchroniser, which is the SP1 fix --------------------------------


def _sync(
    store: FakeStore,
    view: StampView | None,
    applied: list[SignedRecord],
) -> StateSynchroniser:
    def applier(round_: FeedRound) -> None:
        applied.extend(round_.records)

    return StateSynchroniser(
        FeedReader(store, SECRET),
        {StateKind.PLAN: applier},
        budget=DeltaBudget(records=100),
        stamp=view,
    )


def test_a_fresh_process_refuses_a_lagging_replica_without_reading_a_record() -> None:
    """SP1. A process with nothing applied must still refuse a store that went backwards.

    The command count is part of the assertion: a refused round reads the head and stops. If it
    paid for the records it then refused, the fix would have reintroduced the cost R2-02 was
    about.
    """
    store = FakeStore()
    _seed(store, 10)
    # The store fails over to a replica that only has the first four writes.
    store.forget_manifest(StateKind.PLAN)
    store.publish(_record("org-4", seq=4, feed_seq=4), count=4)
    view = _view(started_at=100.0, now=100.5)
    view.observe(_raw(100.2, _cursors(plan=Cursor(Version(1, 10), 10))))
    applied: list[SignedRecord] = []
    sync = _sync(store, view, applied)
    store.reset_counters()

    reports = _run(sync.drain_all())

    assert applied == [], "no record from a regressed generation reaches an applier"
    assert reports[0].ok is False
    assert "older than 1.10 already applied" in (reports[0].error or "")
    assert store.records_read == 0
    # The FLOOR rose; the cursor did not. Nothing was applied, so nothing was applied.
    assert sync.floor(StateKind.PLAN) == Cursor(Version(1, 10), 10)
    assert sync.cursor(StateKind.PLAN) == START


def test_without_a_stamp_the_same_process_accepts_the_lagging_replica() -> None:
    """The negative control. Without this, the test above proves only that floors work.

    This is R2-03 as it stands in GW05c: START is (ZERO, 0), so `not_before` and `feed_floor`
    are both vacuous and a fresh worker adopts whatever the replica holds.
    """
    store = FakeStore()
    _seed(store, 10)
    store.forget_manifest(StateKind.PLAN)
    store.publish(_record("org-4", seq=4, feed_seq=4), count=4)
    applied: list[SignedRecord] = []
    sync = _sync(store, None, applied)

    reports = _run(sync.drain_all())

    assert reports[0].ok is True
    assert [record.key for record in applied] == ["org-1", "org-2", "org-3", "org-4"]
    assert sync.cursor(StateKind.PLAN) == Cursor(Version(1, 4), 4), "the stale generation"


def test_a_current_store_is_applied_normally_under_a_stamp() -> None:
    """The floor must not become a blanket refusal: the matching generation still applies."""
    store = FakeStore()
    _seed(store, 10)
    view = _view(started_at=100.0, now=100.5)
    view.observe(_raw(100.2, _cursors(plan=Cursor(Version(1, 10), 10))))
    applied: list[SignedRecord] = []
    sync = _sync(store, view, applied)

    reports = _run(sync.drain_all())

    assert reports[0].ok is True
    assert len(applied) == 10
    assert sync.cursor(StateKind.PLAN) == Cursor(Version(1, 10), 10)


def test_the_floor_is_raised_before_the_first_poll_not_after() -> None:
    """Ordering is the fix. A floor applied after the round arrives one round too late.

    Asserted on the command stream rather than on an outcome, so a refactor that moves the
    stamp read below the poll fails here even if the end state happens to look right.
    """
    store = FakeStore()
    _seed(store, 3)
    view = _view(started_at=100.0, now=100.5)
    applied: list[SignedRecord] = []
    sync = _sync(store, view, applied)
    store.stamp_raw = _raw(100.2, _cursors(plan=Cursor(Version(1, 3), 3)))
    store.reset_counters()

    _run(sync.drain_all())

    assert store.commands[0] == "get", "the stamp is read first"
    assert store.commands[1:4] == ["get", "zcard", "zrevrange"], "then the kind's head"


def test_a_cycle_reads_the_stamp_once_however_many_kinds_there_are() -> None:
    """One key for the namespace, so freshness costs O(1) per cycle rather than O(kinds)."""
    store = FakeStore()
    _seed(store, 1)
    view = _view(started_at=100.0, now=100.5)

    def nothing(round_: FeedRound) -> None:
        del round_

    sync = StateSynchroniser(
        FeedReader(store, SECRET),
        {kind: nothing for kind in StateKind},
        budget=DeltaBudget(records=100),
        stamp=view,
    )
    store.stamp_raw = _raw(100.2)
    store.reset_counters()

    _run(sync.drain_all())

    assert store.commands.count("get") == 1 + len(StateKind), "one stamp, one head per kind"


def test_a_synchroniser_without_a_stamp_reads_no_stamp_at_all() -> None:
    store = FakeStore()
    _seed(store, 1)
    applied: list[SignedRecord] = []
    sync = _sync(store, None, applied)
    store.reset_counters()

    assert _run(sync.observe_stamp()) is False

    assert store.commands == []


def test_a_missing_stamp_leaves_the_cursor_where_it_was() -> None:
    store = FakeStore()
    _seed(store, 3)
    view = _view(started_at=100.0, now=100.5)
    applied: list[SignedRecord] = []
    sync = _sync(store, view, applied)

    assert _run(sync.observe_stamp()) is False

    assert sync.cursor(StateKind.PLAN) == START
    assert view.missing == 1


def test_one_kinds_floor_does_not_stall_another_kinds_round() -> None:
    """Per-kind isolation, now that one shared stamp feeds every kind's floor."""
    store = FakeStore()
    _seed(store, 5)
    store.publish(_record("global", kind=StateKind.KS, seq=2, feed_seq=2), count=1)
    plans: list[SignedRecord] = []
    switches: list[SignedRecord] = []

    def plan_applier(round_: FeedRound) -> None:
        plans.extend(round_.records)

    def ks_applier(round_: FeedRound) -> None:
        switches.extend(round_.records)

    view = _view(started_at=100.0, now=100.5)
    # The plan floor is above what the store holds; the kill switch's matches.
    view.observe(
        _raw(
            100.2,
            _cursors(plan=Cursor(Version(9, 9), 99), ks=Cursor(Version(1, 2), 2)),
        ),
    )
    sync = StateSynchroniser(
        FeedReader(store, SECRET),
        {StateKind.PLAN: plan_applier, StateKind.KS: ks_applier},
        budget=DeltaBudget(records=100),
        stamp=view,
    )

    reports = {report.kind: report for report in _run(sync.drain_all())}

    assert reports[StateKind.PLAN].ok is False
    assert reports[StateKind.KS].ok is True
    assert plans == []
    assert [record.key for record in switches] == ["global"]


def test_raise_floor_never_lowers_and_never_moves_the_cursor() -> None:
    """The guard `observe_stamp` leans on, plus the separation it must preserve."""
    store = FakeStore()
    applied: list[SignedRecord] = []
    sync = _sync(store, None, applied)
    sync.raise_floor(StateKind.PLAN, Cursor(Version(1, 9), 9))

    sync.raise_floor(StateKind.PLAN, Cursor(Version(1, 4), 4))

    assert sync.floor(StateKind.PLAN) == Cursor(Version(1, 9), 9)
    assert sync.cursor(StateKind.PLAN) == START, "a floor is not an application"


def test_an_empty_secret_is_refused_by_the_view() -> None:
    with pytest.raises(ValueError, match="signing secret"):
        StampView(b"")


def test_the_feed_reader_passes_the_stamp_through_untouched() -> None:
    """Verification is the view's job, so a forged stamp must reach it rather than be eaten."""
    store = FakeStore()
    store.stamp_raw = _raw(100.0, secret=OTHER_SECRET)

    assert _run(FeedReader(store, SECRET).read_stamp()) == store.stamp_raw


def test_the_floor_bounds_both_dimensions() -> None:
    """A partial restore can leave an older POSITION under an equal version.

    Flooring on the version alone would accept it, which is why `Cursor` carries both numbers
    and `decode_manifest` checks both.
    """
    store = FakeStore()
    _seed(store, 10)
    store.forget_manifest(StateKind.PLAN)
    # Same version as the floor, but four writes behind in cursor space.
    store.publish(_record("org-10", seq=10, feed_seq=4), count=4)
    view = _view(started_at=100.0, now=100.5)
    view.observe(_raw(100.2, _cursors(plan=Cursor(Version(1, 10), 10))))
    applied: list[SignedRecord] = []
    sync = _sync(store, view, applied)

    reports = _run(sync.drain_all())

    assert reports[0].ok is False
    assert "below the applied cursor" in (reports[0].error or "")
    assert applied == []


def test_a_zero_floor_is_still_the_floorless_start() -> None:
    """Sanity: an all-zero stamp must not pretend to be a floor."""
    view = _view(started_at=100.0, now=100.5)

    view.observe(_raw(100.2, {kind: Cursor(ZERO, 0) for kind in StateKind}))

    assert view.floor(StateKind.PLAN) == START
    assert view.fresh() is True


# --- the floor is not an application -------------------------------------------------------------
#
# These two pin the bug this phase nearly shipped. The first draft raised the READ CURSOR from
# the stamp, which made `poll` short-circuit on a fresh process's first round: the floor matched
# the store's head, so the delta looked empty and no applier ever ran. For plans that is not a
# stale read, it is every tenant resolving as PlanUnknownTenant -- a 403 telling healthy tenants
# to complete onboarding, which C36 names as the wrong answer. Both tests fail if the cursor and
# the floor are ever collapsed back into one value.


def test_a_fresh_process_with_a_head_high_floor_still_reads_the_whole_kind() -> None:
    store = FakeStore()
    _seed(store, 10)
    view = _view(started_at=100.0, now=100.5)
    view.observe(_raw(100.2, _cursors(plan=Cursor(Version(1, 10), 10))))
    applied: list[SignedRecord] = []
    sync = _sync(store, view, applied)

    reports = _run(sync.drain_all())

    assert reports[0].ok is True
    assert len(applied) == 10, "a raised floor must not be mistaken for applied state"
    assert sync.cursor(StateKind.PLAN) == Cursor(Version(1, 10), 10)


def test_a_raised_floor_alone_leaves_the_applier_un_run() -> None:
    """The mechanism, isolated: a floor at the head and no poll means no records."""
    store = FakeStore()
    _seed(store, 10)
    applied: list[SignedRecord] = []
    sync = _sync(store, None, applied)
    sync.raise_floor(StateKind.PLAN, Cursor(Version(1, 10), 10))

    assert sync.cursor(StateKind.PLAN) == START, "the read position is untouched"

    _run(sync.drain_all())

    assert len(applied) == 10, "so the round still has 10 records to fetch"


# --- the whole loop over a real command set ------------------------------------------------------
#
# Everything above drives `FakeStore`, which proves the LOGIC. These drive the real adapters over
# a real Redis command implementation: the re-hydrator writes the stamp with a synchronous client,
# a gateway reads it with an async one over the same server, and the floor it yields is the thing
# that refuses a rolled-back store. Without this, `ValkeyStateStore.stamp()` -- the production
# read path -- would have no test at all.


class _ValkeyLab:
    """Control plane on a sync client, gateway on an async client, one fake server."""

    def __init__(self, *, started_at: float, now: float) -> None:
        self.server = fakeredis.FakeServer()
        self.keys = StoreKeys(namespace="{t}")
        self.sync = fakeredis.FakeStrictRedis(server=self.server)
        self.publisher = ValkeyPublisher(self.sync, SECRET, self.keys)
        self.db = MemoryControlDB()
        self.writer = StateWriter(self.db, self.publisher, SECRET)
        self.rehydrator = Rehydrator(
            self.db, self.publisher, self.writer, SECRET,
            stale_grace_s=0.0, clock=lambda: now - 0.3, name=BY,
        )
        self.view = StampView(
            SECRET, fresh_ms=5_000, started_at=started_at, clock=lambda: now,
        )
        self.applied: list[SignedRecord] = []

    def sync_task(self) -> StateSynchroniser:
        store = ValkeyStateStore(
            fakeredis.FakeAsyncRedis(server=self.server), self.keys,
        )

        def applier(round_: FeedRound) -> None:
            self.applied.extend(round_.records)

        return StateSynchroniser(
            FeedReader(store, SECRET),
            {StateKind.PLAN: applier},
            budget=DeltaBudget(records=100),
            stamp=self.view,
        )

    def seed(self, tenants: int) -> None:
        for position in range(1, tenants + 1):
            self.writer.plan_set(
                f"org-{position}",
                encode_plan_body(
                    PlanDocument(f"org-{position}", StreamingMode.INCREMENTAL, (_draft(),)),
                ),
            )
        self.writer.key_add("hash-a", "org-a", key_id="k", rate_per_s=1.0, burst=1.0)
        self.writer.killswitch("global", on=False)
        self.writer.put(StateKind.BUDGET, "org-a", {"limit": 1})


def test_a_rehydrators_stamp_reaches_a_gateway_over_valkey() -> None:
    lab = _ValkeyLab(started_at=100.0, now=100.5)
    lab.seed(5)
    assert lab.rehydrator.round_once().stamped is not None
    task = lab.sync_task()

    _run(task.drain_all())

    assert lab.view.fresh() is True
    assert lab.view.by == BY
    assert task.floor(StateKind.PLAN).feed_seq == lab.db.counters(StateKind.PLAN).feed_seq
    assert len(lab.applied) == 5, "a raised floor still reads the kind"


def test_a_FRESH_gateway_refuses_a_rolled_back_store_over_valkey() -> None:
    """SP1 end to end on the real adapters, with a process that has applied NOTHING.

    The worker must be fresh for this to prove anything. A worker that had already drained to
    position 5 would refuse the rollback from its own applied cursor, stamp or no stamp -- which
    is the trap GW05c's own regression drill fell into, and the reason R2-03 survived it.
    """
    lab = _ValkeyLab(started_at=100.0, now=100.5)
    lab.seed(5)
    assert lab.rehydrator.round_once().stamped is not None
    # A partial restore: the manifest and index go back to the first two writes. The stamp the
    # re-hydrator wrote survives, and it still attests position 5.
    counters, records, engaged = lab.db.snapshot(StateKind.PLAN)
    behind = type(counters)(
        version=records[1].version, feed_seq=2, count=2, on_count=counters.on_count,
    )
    lab.publisher.publish_kind(
        StateKind.PLAN,
        records[:2],
        lab.writer.manifest_for(StateKind.PLAN, behind),
        engaged,
        # A deliberate ROLLBACK of the store. `publish_kind` refuses a regress unless asked
        # (R2-04), so a test that forces one has to declare it.
        allow_regress=True,
    )
    fresh_worker = lab.sync_task()

    reports = {report.kind: report for report in _run(fresh_worker.drain_all())}

    assert fresh_worker.cursor(StateKind.PLAN) == START, "it had applied nothing"
    assert reports[StateKind.PLAN].ok is False
    assert "older than" in (reports[StateKind.PLAN].error or "")
    assert lab.applied == []


def test_without_the_stamp_the_same_fresh_gateway_accepts_the_rollback() -> None:
    """The control for the test above: this is R2-03 exactly as GW05c leaves it."""
    lab = _ValkeyLab(started_at=100.0, now=100.5)
    lab.seed(5)
    counters, records, engaged = lab.db.snapshot(StateKind.PLAN)
    behind = type(counters)(
        version=records[1].version, feed_seq=2, count=2, on_count=counters.on_count,
    )
    lab.publisher.publish_kind(
        StateKind.PLAN,
        records[:2],
        lab.writer.manifest_for(StateKind.PLAN, behind),
        engaged,
        # A deliberate ROLLBACK of the store. `publish_kind` refuses a regress unless asked
        # (R2-04), so a test that forces one has to declare it.
        allow_regress=True,
    )
    store = ValkeyStateStore(fakeredis.FakeAsyncRedis(server=lab.server), lab.keys)

    def applier(round_: FeedRound) -> None:
        lab.applied.extend(round_.records)

    unstamped = StateSynchroniser(
        FeedReader(store, SECRET),
        {StateKind.PLAN: applier},
        budget=DeltaBudget(records=100),
    )

    reports = _run(unstamped.drain_all())

    assert reports[0].ok is True
    assert len(lab.applied) == 2, "it serves the rolled-back generation"


def test_the_valkey_reader_returns_none_for_an_empty_stamp_slot() -> None:
    lab = _ValkeyLab(started_at=100.0, now=100.5)
    store = ValkeyStateStore(fakeredis.FakeAsyncRedis(server=lab.server), lab.keys)

    assert _run(store.stamp()) is None


def test_the_valkey_reader_passes_a_forged_stamp_through_to_the_view() -> None:
    """The adapter must not silently swallow what the view needs to count as invalid."""
    lab = _ValkeyLab(started_at=100.0, now=100.5)
    lab.sync.set(lab.keys.stamp, _raw(100.2, secret=OTHER_SECRET))
    task = lab.sync_task()

    assert _run(task.observe_stamp()) is False

    assert lab.view.invalid == 1
    assert lab.view.fresh() is False
