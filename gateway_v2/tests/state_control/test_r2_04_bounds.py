"""R2-04 / GW05b — the four session bounds, and what happens when one of them fires.

The runbook's R2-04 remedy has two code clauses. One is already shipped and drilled: the publish
snapshot is a lock-free REPEATABLE READ read (`test_lgw05c_drills.py`). This file covers the other
one — *"sets `lock_timeout`, `statement_timeout`, `idle_in_transaction_session_timeout` and
`tcp_user_timeout` on EVERY control-plane connection"* — and the consequence the runbook implies
but does not spell out: a bound that fires must be a bounded failure, not a Postgres outage.

Two halves, deliberately in one file because they are one requirement:

* **Offline** (always runs): the classification. A `lock_timeout` or `statement_timeout` expiry
  must withhold the freshness stamp, NOT mint a degraded one off the PG_GRACE_MS ride-through.
  Before this, `_db_faults` caught `psycopg.Error` — the ancestor of every database error — so a
  held row lock was re-asserted as freshness for up to 16 s. That is H7 in the mechanism built for
  Cloud SQL failovers.
* **Live** (needs `AMF_PG_DSN`): the bounds actually reaching the server, read back out of
  `pg_settings`. Nothing verified this before, which is exactly why the PgBouncer `options` drop
  was invisible.
"""

from __future__ import annotations

import logging
import os

import pytest

from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS
from gateway_v2.domain.state import SignedRecord, StateKind
from state_control.db import KindCounters, MemoryControlDB
from state_control.publisher import MemoryStore
from state_control.rehydrate import (
    ControlPlaneBoundExceeded,
    ControlPlaneUnavailable,
    Rehydrator,
)
from state_control.writer import StateWriter

DSN = os.environ.get("AMF_PG_DSN", "")

SECRET = b"r2-04-bounds-secret"
NAME = "rehydrator-bounds:1"


# --- the fault classes, without a driver ----------------------------------------------------------


class _LockNotAvailable(Exception):
    """Stand-in with the real class's identity, so the offline half needs no live server."""


class _QueryCanceled(Exception):
    pass


@pytest.fixture(autouse=True)
def _as_bound_faults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat the two stand-ins as the declared bounds firing.

    Patching the module tuples rather than importing psycopg keeps this half of the file runnable
    in the offline suite, which is the shape CI runs.
    """
    monkeypatch.setattr(
        "state_control.rehydrate._BOUND_FAULTS", (_LockNotAvailable, _QueryCanceled),
    )


class _BoundedDB(MemoryControlDB):
    """A control database whose declared bound fires instead of answering."""

    def __init__(self, fault: type[Exception]) -> None:
        super().__init__()
        self._fault = fault
        self.firing = False

    def counters(self, kind: StateKind) -> KindCounters:
        if self.firing:
            raise self._fault("canceling statement due to lock timeout")
        return super().counters(kind)

    def snapshot(
        self,
        kind: StateKind,
    ) -> tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]:
        if self.firing:
            raise self._fault("canceling statement due to lock timeout")
        return super().snapshot(kind)


def _lab(fault: type[Exception] = _LockNotAvailable) -> tuple[_BoundedDB, Rehydrator]:
    wall = [1_000.0]
    db = _BoundedDB(fault)
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    rehydrator = Rehydrator(
        db,
        store,
        writer,
        SECRET,
        stale_grace_s=0.0,
        clock=lambda: wall[0],
        name=NAME,
        fresh_ms=FRESH_MS,
        pg_grace_ms=PG_GRACE_MS,
    )
    for kind, key, body in (
        (StateKind.PLAN, "org-a", {"org_id": "org-a"}),
        (StateKind.BUDGET, "org-a", {"limit": 1}),
    ):
        writer.put(kind, key, body)
    writer.key_add("hash-a", "org-a", key_id="k", rate_per_s=1.0, burst=1.0)
    writer.killswitch("global", on=False)
    # One clean round, so a ride-through has an anchor to extend. Without this the test would
    # pass for the wrong reason: nothing was ever verified, so nothing could be re-asserted.
    assert rehydrator.round_once().stamped is not None
    wall[0] += 1.0
    return db, rehydrator


# --- the classification: a bound firing is NOT an outage -----------------------------------------


@pytest.mark.parametrize("fault", [_LockNotAvailable, _QueryCanceled])
def test_a_declared_bound_firing_withholds_the_stamp(fault: type[Exception]) -> None:
    """The R2-04 regression. A bound that fires must never be ridden out as a Postgres outage.

    `lock_timeout` exists so a re-hydrator cannot wait behind a held `FOR UPDATE` for ever. If its
    expiry is then laundered into a degraded stamp, the bound has bought nothing: the fleet is told
    state is fresh when nothing was compared. Honest silence is the required outcome.
    """
    db, rehydrator = _lab(fault)
    db.firing = True

    summary = rehydrator.round_once()

    assert summary.stamped is None, "a bound that fired must not mint a stamp of any kind"
    assert summary.ok is False
    assert [kind for kind, _ in summary.errors] == list(StateKind)
    assert all("ControlPlaneBoundExceeded" in detail for _kind, detail in summary.errors)


def test_a_bound_firing_does_not_reach_the_ride_through() -> None:
    """The ride-through covers ONE failure: Postgres did not answer. Not "answered no"."""
    db, rehydrator = _lab()
    anchor = rehydrator.last_verified_stamp
    db.firing = True

    summary = rehydrator.round_once()

    assert summary.stamped is None
    # The anchor is untouched, so a later genuine outage still has its full grace window.
    assert rehydrator.last_verified_stamp is anchor


def test_a_bound_firing_is_logged_as_a_bounded_fault(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An operator must be able to tell a held lock from a dead database in the log.

    The two produce the same symptom — no stamp this round — and opposite diagnoses: one is a
    writer to go and find, the other is a database to go and restart.
    """
    db, rehydrator = _lab()
    db.firing = True

    with caplog.at_level(logging.WARNING, logger="amf.state.rehydrate"):
        summary = rehydrator.round_once()

    assert summary.stamped is None
    messages = [record.getMessage() for record in caplog.records]
    assert any("declared control-plane bound fired" in message for message in messages)
    assert not any("postgres unavailable" in message for message in messages)


def test_an_outage_still_rides_through(caplog: pytest.LogCaptureFixture) -> None:
    """R2-03 is unchanged. Narrowing the outage classes must not narrow the ride-through.

    This is the companion assertion to the three above: a Cloud SQL failover (11-16 s) must still
    be ridden out, or R2-04's fix would have cost R2-03's. `ConnectionError` is an `OSError`, which
    is what the twin injects and what a real socket failure raises.
    """
    db, rehydrator = _lab()

    def unreachable(kind: StateKind) -> KindCounters:
        raise ConnectionError("injected postgres outage")

    db.counters = unreachable  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="amf.state.rehydrate"):
        summary = rehydrator.round_once()

    assert summary.stamped is not None, "a real outage must still be ridden out"
    assert summary.stamped.degraded is True
    assert all("ControlPlaneUnavailable" in detail for _kind, detail in summary.errors)


def test_a_programming_error_withholds_rather_than_riding_out() -> None:
    """`ControlPlaneUnavailable`'s docstring has always claimed this; now the code does it.

    A missing table or a typo in a column list is not an outage. Riding it out would re-assert
    cursors for the full grace window on a fault that will never clear on its own.
    """
    db, rehydrator = _lab()

    def broken(kind: StateKind) -> KindCounters:
        raise RuntimeError('relation "amf_state_counter" does not exist')

    db.counters = broken  # type: ignore[method-assign]

    summary = rehydrator.round_once()

    assert summary.stamped is None
    assert summary.ok is False


def test_the_two_fault_classes_are_not_related() -> None:
    """A bounded fault must not be catchable as an outage, or the distinction is cosmetic."""
    assert not issubclass(ControlPlaneBoundExceeded, ControlPlaneUnavailable)
    assert not issubclass(ControlPlaneUnavailable, ControlPlaneBoundExceeded)


# --- live: the bounds reach the server ------------------------------------------------------------


live = pytest.mark.skipif(not DSN, reason="AMF_PG_DSN is not set")


@live
def test_every_bound_reaches_the_server() -> None:
    """R2-04 clause 1, verified rather than asserted.

    The bounds were constructed correctly before this and reached no backend in the endorsed
    topology, because PgBouncer's `IGNORE_STARTUP_PARAMETERS` includes `options`. Nothing read them
    back, so the drop was silent for an entire card.
    """
    from state_control.pg import BOUND_NAMES, PostgresControlDB

    db = PostgresControlDB(
        DSN,
        lock_timeout_ms=250,
        statement_timeout_ms=800,
        idle_tx_timeout_ms=5_000,
        tcp_user_timeout_ms=2_000,
    )

    applied = db.verify_bounds()

    assert set(applied) == set(BOUND_NAMES)
    assert int(applied["lock_timeout"]) == 250
    assert int(applied["statement_timeout"]) == 800
    assert int(applied["idle_in_transaction_session_timeout"]) == 5_000
    assert int(applied["tcp_user_timeout"]) == 2_000


@live
def test_the_bounds_hold_on_the_read_only_snapshot_connection_too() -> None:
    """"Every control-plane connection" includes the lock-free snapshot path.

    A bound that only the write path carries is not a bound, and the snapshot path is the one the
    fleet's availability actually runs through.
    """
    from state_control.pg import BOUND_NAMES, PostgresControlDB

    db = PostgresControlDB(DSN, lock_timeout_ms=250, statement_timeout_ms=800)
    db.init_schema()

    connection = db._connect(read_only=True)  # noqa: SLF001 - asserting the private path's bounds
    try:
        with connection.cursor() as cursor:
            db._apply_bounds(cursor)  # noqa: SLF001
            cursor.execute(
                "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)",
                (list(BOUND_NAMES),),
            )
            applied = {str(name): str(setting) for name, setting in cursor.fetchall()}
    finally:
        connection.rollback()
        connection.close()

    assert int(applied["lock_timeout"]) == 250
    assert int(applied["statement_timeout"]) == 800


@live
def test_a_mismatched_bound_fails_start_up() -> None:
    """The read-back must be fatal. A bound nothing enforces is not a bound."""
    from state_control.pg import ControlPlaneBoundsNotApplied, PostgresControlDB

    db = PostgresControlDB(DSN, lock_timeout_ms=250)
    # Simulate the PgBouncer drop: the object believes in a value it never sent.
    db._bounds = (("lock_timeout", 999),)  # noqa: SLF001
    db._set_local = ()  # noqa: SLF001

    with pytest.raises(ControlPlaneBoundsNotApplied, match="lock_timeout"):
        db.verify_bounds()


@live
def test_statement_timeout_actually_cancels() -> None:
    """The canary, mirroring control's `analytics_timeout_probe`: a deadline that does not bite
    is indistinguishable from no deadline at all.
    """
    import psycopg

    from state_control.pg import PostgresControlDB

    db = PostgresControlDB(DSN, statement_timeout_ms=300)

    with pytest.raises(psycopg.errors.QueryCanceled), db.tx() as tx:
        tx.settings(["statement_timeout"])  # prove the connection works, then overrun it
        tx._cur.execute("SELECT pg_sleep(2)")  # noqa: SLF001


@live
def test_lock_timeout_actually_fires_behind_a_held_row_lock() -> None:
    """R2-04's own scenario, and the runbook's own figure: the queued writer must fail fast.

    The re-hydrator does not appear here on purpose — its snapshot takes no lock, which is the
    shipped fix drilled in `test_lgw05c_drills.py`. What this pins is the OTHER half of the
    runbook's reference measurement: *"a queued writer fails `LockNotAvailable` in 2.25 s"*.
    """
    import time

    import psycopg

    from state_control.pg import PostgresControlDB

    db = PostgresControlDB(DSN, lock_timeout_ms=250)
    db.init_schema()
    with db.tx() as tx:
        tx.counters(StateKind.PLAN, lock=False)  # ensure the counter row exists

    # The holder carries NONE of our options, so idle_in_transaction_session_timeout cannot
    # release the lock for us and the bound under test is the only thing that can end the wait.
    with psycopg.connect(DSN, autocommit=False) as holder, holder.cursor() as cursor:
        cursor.execute("SELECT 1 FROM amf_state_counter WHERE kind = 'plan' FOR UPDATE")
        started = time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable), db.tx() as tx:
            tx.counters(StateKind.PLAN, lock=True)
        waited = time.monotonic() - started
        holder.rollback()

    assert waited < 2.0, f"the queued writer waited {waited:.2f}s behind a 250ms lock_timeout"
