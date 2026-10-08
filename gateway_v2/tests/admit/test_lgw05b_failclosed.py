"""GW05b phase 4: each kind refuses what was not verified in time. This closes SP2.

The test that carries the phase is `test_a_cached_principal_is_not_served_while_unverified`.
Fail-closed that exempts the fast path does nothing, because the fast path is where the traffic
is: a warm worker keeps admitting a revoked key from RAM for as long as the revocation stays
unpublished, which is SP2 exactly -- 60 s of enforcement-free serving with no 503 anywhere.

The second is `test_an_unverified_plan_is_unavailable_and_never_an_unknown_tenant`. The wrong
answer here is not "stale", it is 403 "complete onboarding", which C36 records RC2 giving to live
tenants for ever after a flush.

Local staleness and stamp freshness are two independent tests and the table in
`test_the_two_staleness_tests_are_independent` is the point: each catches what the other cannot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest

from gateway_v2.admit.identity import IdentityCache, identity_applier
from gateway_v2.admit.killswitch import (
    KillSwitchSnapshot,
    KillSwitchState,
    killswitch_applier,
)
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import (
    ExecutionPlan,
    PlanUnavailable,
    PlanUnknownTenant,
    StreamingMode,
)
from gateway_v2.domain.posture import (
    BUDGET_UNAVAILABLE,
    KILL_SWITCH_UNAVAILABLE,
    MIN_RETRY_AFTER_S,
    PLAN_UNAVAILABLE,
    SHARED_STATE_UNAVAILABLE,
    gap_retry_after_s,
)
from gateway_v2.domain.state import Cursor, StateKind, StoreDataUnavailable, Version
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_sig import encode_stamp, make_stamp
from gateway_v2.runtime.state_stamp import NO_STAMP_SEEN, StampView

SECRET = b"gw05b-failclosed-secret"
BY = "rehydrator-a:4711"


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _raw(moment: float) -> bytes:
    cursors = {kind: Cursor(Version(1, 1), 1) for kind in StateKind}
    return encode_stamp(make_stamp(SECRET, moment, cursors, BY))


class _Clock:
    """A wall clock for the stamp and a monotonic one for the snapshots, moved together."""

    def __init__(self, wall: float = 1_000.0) -> None:
        self.wall = wall
        self.mono = 50.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


def _fresh_view(clock: _Clock, *, fresh_ms: int = 5_000) -> StampView:
    """A view that has adopted a stamp dated after the process started."""
    view = StampView(
        SECRET, fresh_ms=fresh_ms, started_at=clock.wall - 1, clock=lambda: clock.wall,
    )
    assert view.observe(_raw(clock.wall)) is True
    assert view.fresh() is True
    return view


def _principal(org_id: str = "org-a", *, feed_seq: int = 1) -> Principal:
    return Principal(
        key_id="k-a", org_id=org_id, rate_per_s=10.0, burst=20.0, feed_seq=feed_seq,
    )


# --- the kill switch -----------------------------------------------------------------------------


def _loaded_switch(clock: _Clock, view: StampView | None) -> KillSwitchSnapshot:
    snapshot = KillSwitchSnapshot(clock=lambda: clock.mono, stamp=view)
    snapshot.apply((), attested_feed_seq=1, attested_engaged=0)
    return snapshot


def test_an_unverified_kill_switch_is_stale_even_when_freshly_refreshed() -> None:
    """SP2. The local ceiling is nowhere near tripped; the stamp is what notices."""
    clock = _Clock()
    view = _fresh_view(clock)
    snapshot = _loaded_switch(clock, view)
    assert snapshot.state() is KillSwitchState.OK

    clock.advance(6.0)  # past the 5 s freshness bound, and the refresh is now 6 s old too
    assert snapshot.state() is KillSwitchState.STALE

    # Prove it is the STAMP, not the local ceiling: refresh again, keep the stamp old.
    snapshot.apply((), attested_feed_seq=1, attested_engaged=0)
    assert snapshot.refresh_age_seconds() == 0.0, "locally this snapshot is brand new"
    assert snapshot.state() is KillSwitchState.STALE
    assert "last verified against Postgres" in (snapshot.unavailable_reason() or "")


def test_a_verified_kill_switch_serves_normally() -> None:
    clock = _Clock()
    snapshot = _loaded_switch(clock, _fresh_view(clock))

    assert snapshot.state() is KillSwitchState.OK
    assert snapshot.unavailable_reason() is None


def test_an_engaged_switch_still_reads_engaged_when_verified() -> None:
    """Freshness must not mask the switch: engaged is a stronger answer than ok."""
    clock = _Clock()
    snapshot = KillSwitchSnapshot(clock=lambda: clock.mono, stamp=_fresh_view(clock))
    snapshot.apply((), attested_feed_seq=0, attested_engaged=0)
    snapshot._engaged = True  # noqa: SLF001 - the delta path is tested in test_lgw05c_killswitch

    assert snapshot.state() is KillSwitchState.ENGAGED


def test_a_kill_switch_with_no_stamp_at_all_is_stale() -> None:
    clock = _Clock()
    view = StampView(SECRET, started_at=clock.wall, clock=lambda: clock.wall)
    snapshot = _loaded_switch(clock, view)

    assert snapshot.state() is KillSwitchState.STALE
    assert NO_STAMP_SEEN in (snapshot.unavailable_reason() or "")


def test_the_two_staleness_tests_are_independent() -> None:
    """Each catches what the other cannot, which is why both are kept.

    | scenario                                  | local | stamp |
    |-------------------------------------------|-------|-------|
    | wedged refresh, re-hydrator healthy       | trips | no    |
    | unpublished write or lagging replica (SP2)| no    | trips |
    """
    clock = _Clock()

    # A wedged refresh: the stamp stays fresh because the re-hydrator is fine.
    wedged = KillSwitchSnapshot(clock=lambda: clock.mono, stamp=_fresh_view(clock))
    assert wedged.state() is KillSwitchState.STALE, "never refreshed"

    # The mirror: refreshed constantly, but nothing has verified the store.
    clock2 = _Clock()
    unverified = StampView(SECRET, started_at=clock2.wall, clock=lambda: clock2.wall)
    refreshed = KillSwitchSnapshot(clock=lambda: clock2.mono, stamp=unverified)
    refreshed.apply((), attested_feed_seq=1, attested_engaged=0)
    assert refreshed.refresh_age_seconds() == 0.0
    assert refreshed.state() is KillSwitchState.STALE


def test_a_kill_switch_without_freshness_wiring_behaves_as_before() -> None:
    """GW05c's contract is unchanged when no view is supplied."""
    clock = _Clock()
    snapshot = _loaded_switch(clock, None)

    assert snapshot.state() is KillSwitchState.OK

    clock.advance(6.0)

    assert snapshot.state() is KillSwitchState.STALE
    assert "over its 5 s ceiling" in (snapshot.unavailable_reason() or "")


# --- identity: the fast path is the whole point --------------------------------------------------


def test_a_cached_principal_is_not_served_while_unverified() -> None:
    """SP2's real shape. A warm worker must stop trusting RAM, or the fix does nothing.

    The revocation may be precisely the write that was never published, so a held principal is
    no more trustworthy than the store it came from.
    """
    clock = _Clock()
    view = _fresh_view(clock)
    cache = IdentityCache(clock=lambda: clock.mono, stamp=view)
    cache.mark_applied(1)
    assert _run(cache.resolve("hash-a", _fetches(_principal()))) == _principal()
    assert cache.cached("hash-a") == _principal(), "warm"

    clock.advance(6.0)

    assert cache.cached("hash-a") is None
    assert cache.unverified() is not None


def test_a_suppressed_principal_is_not_evicted() -> None:
    """Dropping 50,000 principals on a blip would make the recovery a fetch stampede (SP11)."""
    clock = _Clock()
    view = _fresh_view(clock)
    cache = IdentityCache(clock=lambda: clock.mono, stamp=view)
    cache.mark_applied(1)
    _run(cache.resolve("hash-a", _fetches(_principal())))
    clock.advance(6.0)
    assert cache.cached("hash-a") is None

    view.observe(_raw(clock.wall))  # a re-hydrator comes back

    assert cache.cached("hash-a") == _principal(), "the entry was suppressed, not dropped"
    assert cache.stats().held == 1


def test_resolve_refuses_rather_than_fetching_while_unverified() -> None:
    """Failing closed must not become "fetch harder": the store is the thing in doubt."""
    clock = _Clock()
    cache = IdentityCache(
        clock=lambda: clock.mono,
        stamp=StampView(SECRET, started_at=clock.wall, clock=lambda: clock.wall),
    )
    fetches: list[str] = []

    async def fetch(key_hash: str) -> Principal | None:
        fetches.append(key_hash)
        return _principal()

    with pytest.raises(StoreDataUnavailable, match="no freshness stamp"):
        _run(cache.resolve("hash-a", fetch))

    assert fetches == [], "no store read is attempted"


def test_a_negatively_cached_key_is_not_denied_while_unverified() -> None:
    """A `key_add` may be the unpublished write, so a 401 from RAM would be wrong.

    Failing closed here is a 503 from `resolve`, not a 401 from the negative cache.
    """
    clock = _Clock()
    view = _fresh_view(clock)
    cache = IdentityCache(clock=lambda: clock.mono, stamp=view)
    cache.mark_applied(1)
    assert _run(cache.resolve("hash-x", _fetches(None))) is None
    assert cache.denied("hash-x") is True

    clock.advance(6.0)

    assert cache.denied("hash-x") is False


def test_identity_serves_normally_once_verified() -> None:
    clock = _Clock()
    cache = IdentityCache(clock=lambda: clock.mono, stamp=_fresh_view(clock))
    cache.mark_applied(1)

    assert _run(cache.resolve("hash-a", _fetches(_principal()))) == _principal()
    assert cache.unverified() is None


def test_identity_without_freshness_wiring_behaves_as_before() -> None:
    clock = _Clock()
    cache = IdentityCache(clock=lambda: clock.mono)
    cache.mark_applied(1)

    assert _run(cache.resolve("hash-a", _fetches(_principal()))) == _principal()
    assert cache.cached("hash-a") == _principal()
    assert cache.unverified() is None


def _fetches(principal: Principal | None) -> object:
    async def fetch(key_hash: str) -> Principal | None:
        del key_hash
        return principal

    return fetch


# --- plans: the wrong answer is 403, not "stale" -------------------------------------------------


def _plan(org_id: str = "org-a", *, sequence: int = 1) -> ExecutionPlan:
    return ExecutionPlan(
        org_id=org_id,
        epoch=1,
        sequence=sequence,
        content_hash="a" * 64,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
        feed_seq=1,
    )


def test_an_unverified_plan_is_unavailable_and_never_an_unknown_tenant() -> None:
    """C36: after a flush RC2 answered "complete onboarding" and wedged live tenants at 403.

    An unverified plan kind means we do not KNOW whether this tenant exists. A 403 claims we do.
    """
    clock = _Clock()
    view = _fresh_view(clock)
    store = PlanStore()
    snapshot = ReplicaSnapshot(store, clock=lambda: clock.mono, stamp=view)

    clock.advance(6.0)
    state = snapshot.lookup("org-a")

    assert isinstance(state, PlanUnavailable)
    assert not isinstance(state, PlanUnknownTenant)
    assert state.org_id == "org-a"
    assert "last verified against Postgres" in state.reason


def test_a_plan_we_are_holding_is_still_refused_while_unverified() -> None:
    """Refusing state we have is the point: the write that superseded it may be unpublished."""
    clock = _Clock()
    view = _fresh_view(clock)
    store = PlanStore()
    store.put(_plan())
    snapshot = ReplicaSnapshot(store, clock=lambda: clock.mono, stamp=view)
    assert isinstance(snapshot.lookup("org-a"), ExecutionPlan)

    clock.advance(6.0)

    assert isinstance(snapshot.lookup("org-a"), PlanUnavailable)


def test_a_pin_refuses_while_unverified() -> None:
    """`pin` goes through the same path, so an in-flight request cannot route around it."""
    clock = _Clock()
    view = _fresh_view(clock)
    store = PlanStore()
    store.put(_plan())
    snapshot = ReplicaSnapshot(store, clock=lambda: clock.mono, stamp=view)

    clock.advance(6.0)

    assert isinstance(snapshot.pin("req-1", "org-a"), PlanUnavailable)
    assert snapshot.pinned("req-1") is None


def test_a_genuinely_unknown_tenant_is_still_unknown_when_verified() -> None:
    """Freshness must not swallow the one case where 403 IS the right answer."""
    clock = _Clock()
    store = PlanStore()
    snapshot = ReplicaSnapshot(store, clock=lambda: clock.mono, stamp=_fresh_view(clock))

    assert isinstance(snapshot.lookup("org-nobody"), PlanUnknownTenant)


def test_plans_serve_normally_once_verified() -> None:
    clock = _Clock()
    store = PlanStore()
    store.put(_plan())
    snapshot = ReplicaSnapshot(store, clock=lambda: clock.mono, stamp=_fresh_view(clock))

    assert isinstance(snapshot.lookup("org-a"), ExecutionPlan)


def test_plans_without_freshness_wiring_behave_as_before() -> None:
    clock = _Clock()
    store = PlanStore()
    store.put(_plan())
    snapshot = ReplicaSnapshot(store, clock=lambda: clock.mono)

    clock.advance(6.0)

    assert isinstance(snapshot.lookup("org-a"), ExecutionPlan)


# --- one view, one verdict -----------------------------------------------------------------------


def test_all_three_kinds_refuse_from_the_same_view() -> None:
    """One `StampView` per worker, so no two components can disagree about one fault.

    Two components taking opposite actions on the same fault is the v1 rate-limiter/breaker
    defect; sharing the view is what makes that unrepresentable rather than merely unlikely.
    """
    clock = _Clock()
    view = _fresh_view(clock)
    switch = _loaded_switch(clock, view)
    cache = IdentityCache(clock=lambda: clock.mono, stamp=view)
    cache.mark_applied(1)
    _run(cache.resolve("hash-a", _fetches(_principal())))
    store = PlanStore()
    store.put(_plan())
    plans = ReplicaSnapshot(store, clock=lambda: clock.mono, stamp=view)

    clock.advance(6.0)

    assert switch.state() is KillSwitchState.STALE
    assert cache.cached("hash-a") is None
    assert isinstance(plans.lookup("org-a"), PlanUnavailable)

    view.observe(_raw(clock.wall))
    switch.apply((), attested_feed_seq=1, attested_engaged=0)  # one gateway refresh

    assert switch.state() is KillSwitchState.OK
    assert cache.cached("hash-a") == _principal()
    assert isinstance(plans.lookup("org-a"), ExecutionPlan)


def test_recovery_needs_a_fresh_stamp_AND_a_refresh() -> None:
    """L05b-2/L05b-6 bound recovery at "one re-hydrator round plus one gateway refresh".

    Both halves are load-bearing and this pins the order: a returning re-hydrator alone does not
    clear a snapshot that has also gone locally stale, because nothing has re-read the store
    yet. Expecting the stamp alone to recover would have published a bound that is too tight.
    """
    clock = _Clock()
    view = _fresh_view(clock)
    switch = _loaded_switch(clock, view)
    clock.advance(6.0)
    assert switch.state() is KillSwitchState.STALE

    view.observe(_raw(clock.wall))
    assert switch.state() is KillSwitchState.STALE, "the snapshot is still 6 s old locally"

    switch.apply((), attested_feed_seq=1, attested_engaged=0)

    assert switch.state() is KillSwitchState.OK


# --- the vocabulary ------------------------------------------------------------------------------


def test_the_reason_codes_are_the_spellings_gw06_will_render() -> None:
    """Pinned so the two planes cannot agree on behaviour and differ on spelling (C37)."""
    assert KILL_SWITCH_UNAVAILABLE == "kill_switch_unavailable"
    assert SHARED_STATE_UNAVAILABLE == "shared_state_unavailable"
    assert PLAN_UNAVAILABLE == "plan_unavailable"
    assert BUDGET_UNAVAILABLE == "budget_unavailable"


def test_retry_after_is_a_period_plus_a_refresh_with_a_floor() -> None:
    assert gap_retry_after_s(rehydrate_period_ms=1_000, refresh_ms=500) == 1.5
    assert gap_retry_after_s(rehydrate_period_ms=4_000, refresh_ms=1_000) == 5.0


def test_retry_after_never_drops_below_a_second() -> None:
    """R2-08: 6-11 ms of retry-after made the SDK retry so fast the retries became the load."""
    assert gap_retry_after_s(rehydrate_period_ms=10, refresh_ms=5) == MIN_RETRY_AFTER_S


# --- the applier path still proves freshness -----------------------------------------------------


def test_an_empty_round_still_clears_local_staleness() -> None:
    """GW05c's rule, re-asserted: a round that found nothing changed is proof of a live refresh.

    Only the STAMP decides whether that store was worth reading.
    """
    clock = _Clock()
    view = _fresh_view(clock)
    switch = KillSwitchSnapshot(clock=lambda: clock.mono, stamp=view)
    cache = IdentityCache(clock=lambda: clock.mono, stamp=view)
    ks_apply = killswitch_applier(switch)
    key_apply = identity_applier(cache)

    ks_apply(_empty_round(StateKind.KS))
    key_apply(_empty_round(StateKind.KEY))

    assert switch.refresh_age_seconds() == 0.0
    assert cache.stats().feed_seq == 1
    assert switch.state() is KillSwitchState.OK


def _empty_round(kind: StateKind) -> object:
    from gateway_v2.runtime.state_feed import FeedRound
    from gateway_v2.runtime.state_sig import make_manifest

    return FeedRound(
        kind=kind,
        manifest=make_manifest(SECRET, kind, Version(1, 1), 1, 1, 0),
        records=(),
        cursor=Cursor(Version(1, 1), 1),
        truncated=False,
    )
