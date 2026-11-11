"""GW05c phase 3d — cursor ownership, bounded rounds, offload, per-kind isolation.

The end-to-end test here is the one that matters: a synchroniser wired to the real plan, kill
switch and identity appliers, driven over a store with 10,000 tenants, must cost nothing per
round when nothing changed and exactly one record when one changed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from gateway_v2.admit.identity import IdentityCache, identity_applier
from gateway_v2.admit.killswitch import (
    KillSwitchSnapshot,
    KillSwitchState,
    killswitch_adopter,
    killswitch_applier,
)
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.domain.state import START, Cursor, StateKind, Version
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader, FeedRound
from gateway_v2.runtime.state_sig import make_record
from gateway_v2.runtime.state_task import (
    DeltaBudget,
    StateSynchroniser,
)
from tests.plan.test_lgw05c_delta import SURFACES, _draft
from tests.runtime.test_lgw05c_feed import SECRET, FakeStore

# FakeStore signs its manifests with the feed suite's secret, so records must match it.
__all__ = ("SECRET",)


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _principal(key_hash: str, *, feed_seq: int) -> Principal:
    return Principal(
        key_id=f"id-{key_hash}",
        org_id="org-a",
        rate_per_s=1.0,
        burst=2.0,
        feed_seq=feed_seq,
    )


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(
        PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)),
    )


def _publish_plan(store: FakeStore, org_id: str, *, seq: int, feed_seq: int, count: int) -> None:
    store.publish(
        make_record(
            SECRET,
            StateKind.PLAN,
            org_id,
            _plan_body(org_id),
            Version(1, seq),
            feed_seq,
        ),
        count=count,
    )


def _publish_ks(
    store: FakeStore,
    scope: str,
    *,
    on: bool,
    seq: int,
    feed_seq: int,
    count: int,
    on_count: int,
) -> None:
    store.publish(
        make_record(SECRET, StateKind.KS, scope, {"on": on}, Version(1, seq), feed_seq),
        count=count,
        on_count=on_count,
    )
    if on:
        store.engage(StateKind.KS, scope)


def _wire(
    store: FakeStore,
    *,
    budget: DeltaBudget | None = None,
    offload: Callable[[Callable[[], None]], Awaitable[None]] | None = None,
) -> tuple[StateSynchroniser, PlanStore, KillSwitchSnapshot, IdentityCache]:
    plans = PlanStore()
    snapshot = ReplicaSnapshot(plans, clock=lambda: 1.0)
    switches = KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0)
    cache = IdentityCache(clock=lambda: 0.0)
    reader = FeedReader(store, SECRET)
    plan_apply = plan_applier(plans, snapshot, clock=lambda: 1.0)
    appliers = {
        StateKind.PLAN: plan_apply,
        StateKind.KS: killswitch_applier(switches),
        StateKind.KEY: identity_applier(cache),
    }
    kwargs: dict[str, object] = {}
    if budget is not None:
        kwargs["budget"] = budget
    if offload is not None:
        kwargs["offload"] = offload
    sync = StateSynchroniser(reader, appliers, **kwargs)  # type: ignore[arg-type]
    return sync, plans, switches, cache


# --- end to end, at scale ----------------------------------------------------------------------


def test_a_steady_round_at_ten_thousand_tenants_costs_three_commands() -> None:
    store = FakeStore()
    for position in range(1, 10_001):
        _publish_plan(store, f"org-{position}", seq=position, feed_seq=position, count=position)
    sync, _plans, _switches, _cache = _wire(store)
    _run(sync.drain(StateKind.PLAN))
    store.reset_counters()

    report = _run(sync.round_once(StateKind.PLAN))

    assert report.ok
    assert report.applied == 0
    assert store.records_read == 0
    assert store.commands == ["get", "zcard", "zrevrange"]


def test_one_plan_change_among_ten_thousand_is_applied_and_served() -> None:
    store = FakeStore()
    for position in range(1, 10_001):
        _publish_plan(store, f"org-{position}", seq=position, feed_seq=position, count=position)
    sync, plans, _switches, _cache = _wire(store)
    _run(sync.drain(StateKind.PLAN))
    _publish_plan(store, "org-7", seq=10_001, feed_seq=10_001, count=10_000)
    store.reset_counters()

    report = _run(sync.round_once(StateKind.PLAN))

    assert (report.applied, report.truncated) == (1, False)
    assert store.records_read == 1
    served = plans.read("org-7")
    assert isinstance(served, ExecutionPlan)
    assert served.sequence == 10_001


def test_the_cold_killswitch_start_reads_the_engaged_set_not_the_kind() -> None:
    """Gate G-04: a new gateway process must not pay O(tenants) to learn the switches."""
    store = FakeStore()
    for position in range(1, 25_000):
        _publish_ks(
            store, f"org:t{position}", on=False, seq=position, feed_seq=position,
            count=position, on_count=0,
        )
    _publish_ks(store, "org:acme", on=True, seq=25_000, feed_seq=25_000, count=25_000, on_count=1)
    sync, _plans, switches, _cache = _wire(store)
    store.reset_counters()

    report = _run(sync.bootstrap_engaged(StateKind.KS, killswitch_adopter(switches)))

    assert report.ok, report.error
    assert switches.org_killed("acme") is True
    assert switches.state(100.0) is KillSwitchState.OK
    assert store.records_read == 1, "one engaged scope read, not 25,000 records"
    assert sync.cursor(StateKind.KS).feed_seq == 25_000, "the cursor jumps to the attested head"


def test_after_a_cold_start_a_switch_flip_arrives_as_a_delta() -> None:
    store = FakeStore()
    _publish_ks(store, "global", on=False, seq=1, feed_seq=1, count=1, on_count=0)
    sync, _plans, switches, _cache = _wire(store)
    _run(sync.bootstrap_engaged(StateKind.KS, killswitch_adopter(switches)))

    _publish_ks(store, "global", on=True, seq=2, feed_seq=2, count=1, on_count=1)
    report = _run(sync.round_once(StateKind.KS))

    assert report.ok, report.error
    assert switches.state(100.0) is KillSwitchState.ENGAGED


def test_a_key_write_evicts_only_that_key_through_the_synchroniser() -> None:
    store = FakeStore()
    body: dict[str, object] = {
        "key_id": "id-a", "org_id": "org-a", "rate_per_s": 1.0, "burst": 2.0,
    }
    store.publish(
        make_record(SECRET, StateKind.KEY, "hash-a", body, Version(1, 1), 1), count=1,
    )
    sync, _plans, _switches, cache = _wire(store)
    _run(sync.drain(StateKind.KEY))
    cache._admit("hash-a", _principal("hash-a", feed_seq=1))
    cache._admit("hash-other", _principal("hash-other", feed_seq=1))

    store.publish(
        make_record(SECRET, StateKind.KEY, "hash-a", body, Version(1, 2), 2), count=1,
    )
    report = _run(sync.round_once(StateKind.KEY))

    assert report.applied == 1
    assert cache.cached("hash-a") is None
    assert cache.cached("hash-other") is not None




# --- bounded rounds and offload -----------------------------------------------------------------


def test_a_bulk_onboard_converges_over_bounded_rounds() -> None:
    store = FakeStore()
    for position in range(1, 1_001):
        _publish_plan(store, f"org-{position}", seq=position, feed_seq=position, count=position)
    sync, plans, _switches, _cache = _wire(store, budget=DeltaBudget(records=100))

    rounds = 0
    while True:
        rounds += 1
        report = _run(sync.round_once(StateKind.PLAN))
        assert report.applied <= 100, "a round must never exceed its budget"
        if not report.truncated:
            break

    assert rounds == 10
    assert len(plans.known()) == 1_000


def test_a_large_delta_is_offloaded_and_a_small_one_is_not() -> None:
    store = FakeStore()
    offloaded: list[int] = []

    async def spy(work: Callable[[], None]) -> None:
        offloaded.append(1)
        work()

    for position in range(1, 41):
        _publish_plan(store, f"org-{position}", seq=position, feed_seq=position, count=position)
    sync, _plans, _switches, _cache = _wire(
        store, budget=DeltaBudget(records=256, offload_above=32), offload=spy,
    )

    big = _run(sync.round_once(StateKind.PLAN))
    assert big.offloaded is True
    assert len(offloaded) == 1

    _publish_plan(store, "org-1", seq=41, feed_seq=41, count=40)
    small = _run(sync.round_once(StateKind.PLAN))
    assert small.offloaded is False
    assert len(offloaded) == 1, "a one-record delta must not pay a thread hop"


def test_a_drain_is_bounded_and_reports_lag() -> None:
    store = FakeStore()
    for position in range(1, 101):
        _publish_plan(store, f"org-{position}", seq=position, feed_seq=position, count=position)
    sync, _plans, _switches, _cache = _wire(store, budget=DeltaBudget(records=1))

    report = _run(sync.drain(StateKind.PLAN, max_rounds=5))

    assert report.truncated is True, "the drain stopped at its bound"
    assert sync.cursor(StateKind.PLAN).feed_seq == 5


def test_a_zero_budget_is_refused() -> None:
    store = FakeStore()
    reader = FeedReader(store, SECRET)

    with pytest.raises(ValueError, match="must be positive"):
        StateSynchroniser(reader, {}, budget=DeltaBudget(records=0))


# --- failure handling ----------------------------------------------------------------------------


def test_a_failed_round_does_not_advance_the_cursor() -> None:
    store = FakeStore()
    for position in range(1, 4):
        _publish_plan(store, f"org-{position}", seq=position, feed_seq=position, count=position)
    sync, _plans, _switches, _cache = _wire(store)
    _run(sync.drain(StateKind.PLAN))
    before = sync.cursor(StateKind.PLAN)

    _publish_plan(store, "org-4", seq=4, feed_seq=4, count=4)
    store.drop_record(StateKind.PLAN, "org-4")
    report = _run(sync.round_once(StateKind.PLAN))

    assert report.ok is False
    assert "record missing" in (report.error or "")
    assert sync.cursor(StateKind.PLAN) == before, "a partial generation is never banked"


def test_a_round_recovers_once_the_store_does() -> None:
    store = FakeStore()
    _publish_plan(store, "org-1", seq=1, feed_seq=1, count=1)
    sync, plans, _switches, _cache = _wire(store)
    store.forget_manifest(StateKind.PLAN)

    assert _run(sync.round_once(StateKind.PLAN)).ok is False

    _publish_plan(store, "org-1", seq=1, feed_seq=1, count=1)
    report = _run(sync.round_once(StateKind.PLAN))

    assert report.ok is True
    assert isinstance(plans.read("org-1"), ExecutionPlan)


def test_one_kind_failing_does_not_stall_another() -> None:
    """H7: per-kind isolation. A plan-store fault must not stop kill-switch propagation."""
    store = FakeStore()
    _publish_ks(store, "org:acme", on=True, seq=1, feed_seq=1, count=1, on_count=1)
    sync, _plans, switches, _cache = _wire(store)

    reports = {report.kind: report for report in _run(sync.drain_all())}

    assert reports[StateKind.PLAN].ok is False, "no plan manifest was ever published"
    assert reports[StateKind.KS].ok is True
    assert switches.org_killed("acme") is True


def test_an_applier_refusal_leaves_the_cursor_put() -> None:
    """The kill switch rejecting a delta on count drift must not bank the position.

    The manifest is internally consistent (on_count <= count), so the signature and sanity
    layers pass it. Only the snapshot can tell that the engaged scopes it just applied do not
    add up to what the manifest attests — one engaged scope against an attested two. That is
    the fail-open case this check exists for: an engage that never reached this worker.
    """
    store = FakeStore()
    _publish_ks(store, "org:acme", on=True, seq=1, feed_seq=1, count=1, on_count=1)
    _publish_ks(store, "org:other", on=False, seq=2, feed_seq=2, count=2, on_count=2)
    sync, _plans, switches, _cache = _wire(store)

    report = _run(sync.round_once(StateKind.KS))

    assert report.ok is False
    assert "engaged scopes" in (report.error or "")
    assert sync.cursor(StateKind.KS) == START
    assert switches.state(100.0) is KillSwitchState.STALE


def test_an_internally_inconsistent_manifest_is_caught_before_the_applier() -> None:
    """on_count > count never reaches the snapshot: the manifest layer refuses it."""
    store = FakeStore()
    _publish_ks(store, "org:acme", on=True, seq=1, feed_seq=1, count=1, on_count=9)
    sync, _plans, _switches, _cache = _wire(store)

    report = _run(sync.round_once(StateKind.KS))

    assert report.ok is False
    assert "9 engaged of 1 records" in (report.error or "")


def test_an_unregistered_kind_is_a_programming_error() -> None:
    store = FakeStore()
    sync = StateSynchroniser(FeedReader(store, SECRET), {})

    with pytest.raises(KeyError, match="no applier registered"):
        _run(sync.round_once(StateKind.PLAN))


# --- cursor floors (the GW05b seam) --------------------------------------------------------------


def test_a_floor_can_only_rise() -> None:
    """GW05b separated the acceptance FLOOR from the read CURSOR; this asserts the floor."""
    store = FakeStore()
    sync, _plans, _switches, _cache = _wire(store)

    sync.raise_floor(StateKind.PLAN, Cursor(Version(1, 9), 9))
    assert sync.floor(StateKind.PLAN) == Cursor(Version(1, 9), 9)

    sync.raise_floor(StateKind.PLAN, Cursor(Version(1, 4), 4))
    assert sync.floor(StateKind.PLAN) == Cursor(Version(1, 9), 9), "floors never fall"
    assert sync.cursor(StateKind.PLAN) == START, "and a floor applies nothing"


def test_a_floor_makes_a_lagging_replica_unavailable() -> None:
    """SP1: a fresh process on a lagging replica must not serve what the floor forbids."""
    store = FakeStore()
    _publish_plan(store, "org-1", seq=4, feed_seq=4, count=1)
    sync, _plans, _switches, _cache = _wire(store)

    sync.raise_floor(StateKind.PLAN, Cursor(Version(1, 9), 9))
    report = _run(sync.round_once(StateKind.PLAN))

    assert report.ok is False
    assert "older than" in (report.error or "")


def test_the_feed_round_type_is_what_appliers_receive() -> None:
    """Appliers are bound to FeedRound, so a new kind cannot invent its own shape."""
    store = FakeStore()
    seen: list[FeedRound] = []
    _publish_plan(store, "org-1", seq=1, feed_seq=1, count=1)
    reader = FeedReader(store, SECRET)
    sync = StateSynchroniser(reader, {StateKind.PLAN: seen.append})

    _run(sync.round_once(StateKind.PLAN))

    assert len(seen) == 1
    assert seen[0].kind is StateKind.PLAN
    assert seen[0].manifest.count == 1
    assert len(SURFACES) == 5
