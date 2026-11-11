"""GW05b phase 5: ride out a Postgres outage instead of failing the fleet.

Phase 4 made every kind refuse unverified state. On its own that is a REGRESSION, and this phase
is why: a Cloud SQL failover takes 11-16 s and is routine. No Postgres means no verification
means no stamp means the whole fleet refuses, and the reference patch measured about 7.4 s of
global 503 on a 10 s freeze before the ride-through existed. L05b-4 requires zero.

The three tests that define the boundary:

* `test_a_postgres_outage_keeps_stamping_while_the_store_is_intact` -- the ride happens.
* `test_a_store_that_lost_something_voids_the_ride_through` -- and stops the moment the premise
  fails, because the claim is "nothing previously enforced has been lost".
* `test_degraded_stamps_cannot_extend_the_window_indefinitely` -- the window is anchored on the
  last VERIFIED stamp, so the ride is bounded at PG_GRACE_MS rather than open-ended.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest

from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS
from gateway_v2.domain.plan import StreamingMode
from gateway_v2.domain.state import (
    Manifest,
    SignedRecord,
    StateKind,
)
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.runtime.state_stamp import StampView
from state_control.db import KindCounters, MemoryControlDB
from state_control.publisher import MemoryStore, StoredHead
from state_control.rehydrate import ControlPlaneUnavailable, Rehydrator
from state_control.writer import StateWriter
from tests.plan.test_lgw05c_delta import _draft
from tests.state_control.test_lgw05c_writer import SECRET

NAME = "rehydrator-a:4711"


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


class _DownableDB(MemoryControlDB):
    """A control database that can stop answering, the way a failover makes one stop."""

    def __init__(self) -> None:
        super().__init__()
        self.down = False
        self.reads = 0

    def counters(self, kind: StateKind) -> KindCounters:
        self.reads += 1
        if self.down:
            raise ConnectionError("injected postgres outage")
        return super().counters(kind)

    def snapshot(
        self,
        kind: StateKind,
    ) -> tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]:
        self.reads += 1
        if self.down:
            raise ConnectionError("injected postgres outage")
        return super().snapshot(kind)


class _Lab:
    def __init__(self, *, pg_grace_ms: int = PG_GRACE_MS) -> None:
        self.wall = 1_000.0
        self.db = _DownableDB()
        self.store = MemoryStore()
        self.writer = StateWriter(self.db, self.store, SECRET)
        self.rehydrator = Rehydrator(
            self.db,
            self.store,
            self.writer,
            SECRET,
            stale_grace_s=0.0,
            clock=lambda: self.wall,
            name=NAME,
            pg_grace_ms=pg_grace_ms,
        )

    def seed(self) -> None:
        self.writer.plan_set("org-a", _plan_body("org-a"))
        self.writer.key_add("hash-a", "org-a", key_id="k", rate_per_s=1.0, burst=1.0)
        self.writer.killswitch("global", on=False)
        self.writer.put(StateKind.BUDGET, "org-a", {"limit": 1})

    def advance(self, seconds: float) -> None:
        self.wall += seconds

    def view(self, *, fresh_ms: int = FRESH_MS) -> StampView:
        return StampView(
            SECRET, fresh_ms=fresh_ms, started_at=self.wall - 1, clock=lambda: self.wall,
        )


def _verified_lab(**kwargs: int) -> _Lab:
    """A lab with one clean, verified round already behind it."""
    lab = _Lab(**kwargs)
    lab.seed()
    first = lab.rehydrator.round_once()
    assert first.stamped is not None and first.stamped.degraded is False
    return lab


# --- the ride happens ----------------------------------------------------------------------------


def test_a_postgres_outage_keeps_stamping_while_the_store_is_intact() -> None:
    lab = _verified_lab()
    anchor = lab.rehydrator.last_verified_stamp
    assert anchor is not None

    lab.db.down = True
    lab.advance(3.0)
    summary = lab.rehydrator.round_once()

    stamp = summary.stamped
    assert stamp is not None, "the fleet must not fail closed on a routine failover"
    assert stamp.degraded is True
    assert stamp.verified_at == lab.wall, "the timestamp advances, or the ride achieves nothing"
    assert dict(stamp.cursors) == dict(anchor.cursors), "the cursors do NOT advance"
    assert summary.ok is False, "the round is still honestly reported as failed"


def test_a_gateway_stays_fresh_through_a_postgres_outage() -> None:
    """The point of the whole phase, measured where it matters: at the reader."""
    lab = _verified_lab()
    view = lab.view()
    assert view.observe(lab.store.read_stamp()) is True
    assert view.fresh() is True

    lab.db.down = True
    for _ in range(6):
        lab.advance(2.0)  # well past the 5 s freshness bound
        lab.rehydrator.round_once()
        view.observe(lab.store.read_stamp())
        assert view.fresh() is True, "a degraded stamp is still a valid freshness claim"

    assert view.degraded is True, "and the operator can see it was a ride-through"


def test_the_ride_through_ends_at_the_grace_window() -> None:
    lab = _verified_lab()

    lab.db.down = True
    lab.advance(PG_GRACE_MS / 1000 - 1)
    assert lab.rehydrator.round_once().stamped is not None, "inside the window"

    lab.advance(2.0)

    assert lab.rehydrator.round_once().stamped is None, "past it, the fleet fails closed"


def test_a_gateway_fails_closed_once_the_grace_expires() -> None:
    lab = _verified_lab()
    view = lab.view()
    view.observe(lab.store.read_stamp())

    lab.db.down = True
    lab.advance(PG_GRACE_MS / 1000 + 1)
    lab.rehydrator.round_once()
    view.observe(lab.store.read_stamp())

    assert view.fresh() is False
    assert "last verified against Postgres" in view.unverified()


def test_postgres_returning_writes_a_verified_stamp_again() -> None:
    lab = _verified_lab()
    lab.db.down = True
    lab.advance(3.0)
    assert lab.rehydrator.round_once().stamped is not None

    lab.db.down = False
    lab.advance(1.0)
    summary = lab.rehydrator.round_once()

    assert summary.ok is True
    stamp = summary.stamped
    assert stamp is not None and stamp.degraded is False
    assert lab.rehydrator.last_verified_stamp is stamp, "the anchor moved"


def test_a_write_that_lands_during_the_outage_is_published_on_recovery() -> None:
    """The ride-through declares a window of possible non-enforcement; it does not lose data."""
    lab = _verified_lab()
    lab.db.down = True
    lab.advance(2.0)
    lab.rehydrator.round_once()

    lab.db.down = False
    lab.writer.plan_set("org-b", _plan_body("org-b"))
    lab.advance(1.0)
    summary = lab.rehydrator.round_once()

    assert summary.ok is True
    stamp = summary.stamped
    assert stamp is not None
    assert stamp.cursors[StateKind.PLAN].feed_seq == lab.db.counters(StateKind.PLAN).feed_seq


# --- and stops the moment its premise fails ------------------------------------------------------


def test_a_store_that_lost_something_voids_the_ride_through() -> None:
    """The claim is "nothing previously enforced has been lost". If it was, there is no claim."""
    lab = _verified_lab()

    lab.db.down = True
    lab.store.forget_manifest(StateKind.PLAN)
    lab.advance(1.0)

    assert lab.rehydrator.round_once().stamped is None


def test_a_store_rolled_back_below_the_anchor_voids_the_ride_through(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lab = _verified_lab()
    lab.writer.plan_set("org-b", _plan_body("org-b"))
    lab.writer.plan_set("org-c", _plan_body("org-c"))
    lab.advance(1.0)
    assert lab.rehydrator.round_once().stamped is not None
    counters, records, engaged = lab.db.snapshot(StateKind.PLAN)
    behind = KindCounters(
        version=records[0].version, feed_seq=1, count=1, on_count=counters.on_count,
    )

    lab.store.publish_kind(
        StateKind.PLAN,
        records[:1],
        lab.writer.manifest_for(StateKind.PLAN, behind),
        engaged,
        # A deliberate ROLLBACK of the store. `publish_kind` refuses a regress unless asked
        # (R2-04), so a test that forces one has to declare it.
        allow_regress=True,
    )
    lab.db.down = True
    lab.advance(1.0)

    with caplog.at_level(logging.WARNING, logger="amf.state.rehydrate"):
        assert lab.rehydrator.round_once().stamped is None

    assert any("ride-through void" in r.getMessage() for r in caplog.records)


def test_a_store_ahead_of_the_anchor_does_not_void_the_ride_through() -> None:
    """A write published just before the outage leaves the store AHEAD, which is not a loss."""
    lab = _verified_lab()
    anchor = lab.rehydrator.last_verified_stamp
    assert anchor is not None
    lab.writer.plan_set("org-b", _plan_body("org-b"))

    lab.db.down = True
    lab.advance(1.0)
    stamp = lab.rehydrator.round_once().stamped

    assert stamp is not None and stamp.degraded is True
    assert stamp.cursors[StateKind.PLAN] == anchor.cursors[StateKind.PLAN], "still the anchor"


def test_nothing_is_claimed_before_anything_was_ever_verified() -> None:
    """A re-hydrator that starts up into an outage has no claim to extend."""
    lab = _Lab()
    lab.seed()
    lab.db.down = True

    summary = lab.rehydrator.round_once()

    assert summary.stamped is None
    assert lab.rehydrator.last_verified_stamp is None


def test_degraded_stamps_cannot_extend_the_window_indefinitely() -> None:
    """Anchored on the last VERIFIED stamp, so the ride is bounded rather than self-renewing.

    Anchoring on the last WRITTEN stamp instead would let each degraded round reset the clock,
    and a Postgres outage would never end as far as the fleet could tell -- an unbounded window
    of non-enforcement wearing the costume of a bounded one.
    """
    lab = _verified_lab()
    lab.db.down = True
    stamped = 0

    for _ in range(40):
        lab.advance(1.0)
        if lab.rehydrator.round_once().stamped is not None:
            stamped += 1

    assert stamped == PG_GRACE_MS // 1000, "exactly the declared window, then nothing"
    assert stamped < 40


# --- the ride is ONLY for Postgres ---------------------------------------------------------------


def test_a_store_fault_is_not_ridden_out() -> None:
    """The ride-through covers one failure. A store fault is not it: the store is the evidence."""
    lab = _verified_lab()
    original = lab.store.stored_head

    def dead(kind: StateKind) -> StoredHead:
        del kind
        raise ConnectionError("injected store failure")

    lab.store.stored_head = dead  # type: ignore[method-assign]
    lab.advance(1.0)

    assert lab.rehydrator.round_once().stamped is None
    lab.store.stored_head = original  # type: ignore[method-assign]


def test_a_data_fault_is_not_ridden_out() -> None:
    """A forged manifest with Postgres healthy is a real fault, so the stamp is withheld."""
    lab = _verified_lab()
    original = lab.store.publish_kind

    def refuse(
        kind: StateKind,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        engaged: Sequence[str] = (),
        *,
        allow_regress: bool = False,
    ) -> bool:
        if kind is StateKind.PLAN:
            raise ConnectionError("injected store failure")
        return original(kind, records, manifest, engaged, allow_regress=allow_regress)

    lab.store.forget_manifest(StateKind.PLAN)
    lab.store.publish_kind = refuse  # type: ignore[method-assign]
    lab.advance(1.0)

    assert lab.rehydrator.round_once().stamped is None, "postgres is fine; this is a real fault"


def test_a_partial_postgres_failure_is_not_ridden_out() -> None:
    """If ANY kind got a real comparison, Postgres is reachable and a failing kind is a fault."""
    lab = _verified_lab()
    original = lab.db.counters
    calls = {"n": 0}

    def only_the_first_works(kind: StateKind) -> KindCounters:
        calls["n"] += 1
        if calls["n"] > 1:
            raise ConnectionError("injected postgres outage")
        return original(kind)

    lab.db.counters = only_the_first_works  # type: ignore[method-assign]
    lab.advance(1.0)

    summary = lab.rehydrator.round_once()

    assert summary.stamped is None
    assert len(summary.errors) == len(StateKind) - 1


def test_a_programming_error_in_the_database_layer_is_not_ridden_out() -> None:
    """A bug must not wear an outage's costume and be ridden out for the whole grace window."""
    lab = _verified_lab()

    def broken(kind: StateKind) -> KindCounters:
        del kind
        raise TypeError("a bug, not an outage")

    lab.db.counters = broken  # type: ignore[method-assign]
    lab.advance(1.0)

    summary = lab.rehydrator.round_once()

    assert summary.stamped is None
    assert all("TypeError" in detail for _kind, detail in summary.errors)


def test_the_postgres_fault_is_reported_as_its_own_class() -> None:
    lab = _verified_lab()
    lab.db.down = True
    lab.advance(1.0)

    summary = lab.rehydrator.round_once()

    assert all("ControlPlaneUnavailable" in detail for _kind, detail in summary.errors)


def test_a_bare_database_fault_raises_the_control_plane_class() -> None:
    lab = _Lab()
    lab.seed()
    lab.db.down = True

    with pytest.raises(ControlPlaneUnavailable, match="counters"):
        lab.rehydrator.diagnose(StateKind.PLAN)


# --- a degraded round claims nothing it did not do -----------------------------------------------


def test_a_degraded_round_never_claims_a_deep_comparison() -> None:
    """It compared nothing, so `deep` would be a lie even when the caller asked for one."""
    lab = _verified_lab()
    lab.db.down = True
    lab.advance(1.0)

    stamp = lab.rehydrator.round_once(deep=True).stamped

    assert stamp is not None and stamp.degraded is True and stamp.deep is False


def test_a_verified_deep_round_still_says_deep() -> None:
    lab = _verified_lab()
    lab.advance(1.0)

    stamp = lab.rehydrator.round_once(deep=True).stamped

    assert stamp is not None and stamp.deep is True and stamp.degraded is False


# --- configuration -------------------------------------------------------------------------------


def test_a_grace_shorter_than_the_freshness_bound_is_refused_at_construction() -> None:
    """It could not ride anything through: the fleet fails closed before the window ends."""
    db = MemoryControlDB()
    store = MemoryStore()
    with pytest.raises(ValueError, match="at least fresh_ms"):
        Rehydrator(
            db, store, StateWriter(db, store, SECRET), SECRET,
            fresh_ms=5_000, pg_grace_ms=1_000,
        )


def test_the_ride_through_can_be_disabled() -> None:
    """Zero means "never claim what was not just verified"; strictly safer, less available."""
    lab = _verified_lab(pg_grace_ms=0)

    lab.db.down = True
    lab.advance(1.0)

    assert lab.rehydrator.round_once().stamped is None


def test_the_owner_locked_window_covers_the_measured_cloud_sql_gap() -> None:
    """R2-03 sizes this from a measured 15.5 s loaded failover. 16 s is 3% of headroom.

    Pinned because the margin is thin: if L05b-4 ever fails on it, that is evidence for the
    owner, not licence to retune a locked constant.
    """
    assert PG_GRACE_MS == 16_000
    assert PG_GRACE_MS / 1000 > 15.5
    assert PG_GRACE_MS >= FRESH_MS


def test_the_anchor_is_exposed_for_the_operator() -> None:
    lab = _verified_lab()
    lab.db.down = True
    lab.advance(1.0)
    lab.rehydrator.round_once()

    assert lab.rehydrator.last_stamp is not None
    assert lab.rehydrator.last_stamp.degraded is True
    assert lab.rehydrator.last_verified_stamp is not None
    assert lab.rehydrator.last_verified_stamp.degraded is False
    assert lab.rehydrator.last_stamp is not lab.rehydrator.last_verified_stamp
