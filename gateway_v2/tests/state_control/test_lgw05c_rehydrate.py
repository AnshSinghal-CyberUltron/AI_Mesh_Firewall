"""GW05c phase 4b — re-hydration that costs nothing when nothing is wrong.

`test_a_healthy_round_reads_no_records` is the guard. It is not a micro-optimisation: GW05b puts
this component on the data plane's availability path, so a round slower than the freshness bound
produces stamps that are born stale and the fleet fails closed. RC2 read and digested the whole
record set of all four kinds once a second.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest

from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.domain.state import StateKind, Version
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from state_control.db import MemoryControlDB
from state_control.publisher import BrokenPublisher, MemoryStore
from state_control.rehydrate import (
    CONTENTS,
    COUNTS,
    INDEX,
    INVALID,
    MISSING,
    STALE,
    STORE_AHEAD,
    Rehydrator,
)
from state_control.writer import StateWriter
from tests.plan.test_lgw05c_delta import _draft
from tests.state_control.test_lgw05c_writer import SECRET, Gateway

ONE_KIND = (StateKind.PLAN,)


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


def _lab(
    *,
    kinds: tuple[StateKind, ...] = ONE_KIND,
) -> tuple[StateWriter, MemoryControlDB, MemoryStore, Rehydrator]:
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    rehydrator = Rehydrator(
        db,
        store,
        writer,
        SECRET,
        stale_grace_s=0.0,
        clock=lambda: 0.0,
        kinds=kinds,
    )
    return writer, db, store, rehydrator


# --- cost shape -------------------------------------------------------------------------------


def test_a_healthy_round_reads_no_records() -> None:
    """R2-02 / S3: the round that runs every second must not touch the estate."""
    writer, _db, store, rehydrator = _lab()
    writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 10_001)],
    )
    store.reset_counters()

    summary = rehydrator.round_once()

    assert summary.ok
    assert summary.repairs == ()
    assert summary.healthy == (StateKind.PLAN,)
    assert store.head_reads == 1, "one O(1) head read"
    assert store.index_scans == 0, "the index is never scanned on a healthy round"
    assert store.whole_kind_publishes == 0


def test_a_healthy_diagnose_costs_the_same_at_every_scale() -> None:
    costs: dict[int, tuple[int, int]] = {}
    for tenants in (10, 1_000, 10_000):
        writer, _db, store, rehydrator = _lab()
        writer.put_many(
            StateKind.PLAN,
            [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, tenants + 1)],
        )
        store.reset_counters()
        assert rehydrator.diagnose(StateKind.PLAN) is None
        costs[tenants] = (store.head_reads, store.index_scans)

    assert costs[10] == costs[1_000] == costs[10_000] == (1, 0)


def test_the_deep_check_is_the_only_path_that_scans() -> None:
    writer, _db, store, rehydrator = _lab()
    writer.plan_set("org-a", _plan_body("org-a"))
    store.reset_counters()

    rehydrator.round_once(deep=True)

    assert store.index_scans == 1, "deep verification is explicit and occasional"


# --- what diagnose catches, in O(1) -------------------------------------------------------------


def test_a_flushed_store_is_missing() -> None:
    writer, _db, store, rehydrator = _lab()
    writer.plan_set("org-a", _plan_body("org-a"))

    store.flush()

    assert rehydrator.diagnose(StateKind.PLAN) == MISSING


def test_an_unverifiable_manifest_is_invalid() -> None:
    writer, db, store, _rehydrator = _lab()
    writer.plan_set("org-a", _plan_body("org-a"))
    foreign = Rehydrator(
        db, store, writer, b"another-secret", stale_grace_s=0.0, kinds=ONE_KIND,
    )

    assert foreign.diagnose(StateKind.PLAN) == INVALID


def test_an_unpublished_write_is_stale() -> None:
    """The ok_publish_pending case: committed in Postgres, never published."""
    db = MemoryControlDB()
    store = MemoryStore()
    good = StateWriter(db, store, SECRET)
    good.plan_set("org-a", _plan_body("org-a"))
    broken = StateWriter(db, BrokenPublisher(), SECRET)
    outcome = broken.plan_set("org-b", _plan_body("org-b"))
    rehydrator = Rehydrator(db, store, good, SECRET, stale_grace_s=0.0, kinds=ONE_KIND)

    assert outcome.durable is True
    assert outcome.published is False
    assert rehydrator.diagnose(StateKind.PLAN) == STALE


def test_a_store_holding_a_version_postgres_never_issued_is_ahead() -> None:
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    rogue = StateWriter(MemoryControlDB(), store, SECRET)
    writer.plan_set("org-a", _plan_body("org-a"))
    for position in range(5):
        rogue.plan_set(f"org-rogue-{position}", _plan_body(f"org-rogue-{position}"))
    rehydrator = Rehydrator(db, store, writer, SECRET, stale_grace_s=0.0, kinds=ONE_KIND)

    assert rehydrator.diagnose(StateKind.PLAN) == STORE_AHEAD


def test_a_tampered_count_is_caught_without_reading_records() -> None:
    writer, db, store, rehydrator = _lab()
    writer.plan_set("org-a", _plan_body("org-a"))
    counters, _records, _engaged = db.snapshot(StateKind.PLAN)
    lying = writer.manifest_for(
        StateKind.PLAN,
        type(counters)(
            version=counters.version,
            feed_seq=counters.feed_seq,
            count=counters.count + 3,
            on_count=counters.on_count,
        ),
    )
    store.publish_kind(StateKind.PLAN, (), lying, ())
    store.reset_counters()

    assert rehydrator.diagnose(StateKind.PLAN) == COUNTS
    assert store.index_scans == 0


def test_a_short_index_is_caught_without_reading_records() -> None:
    writer, _db, store, rehydrator = _lab()
    writer.plan_set("org-a", _plan_body("org-a"))
    writer.plan_set("org-b", _plan_body("org-b"))
    store._index[StateKind.PLAN].pop("org-a")
    store.reset_counters()

    assert rehydrator.diagnose(StateKind.PLAN) == INDEX
    assert store.index_scans == 0


def test_a_missing_engaged_member_is_caught() -> None:
    """The fail-open case, caught at the control plane as well as at the worker."""
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    rehydrator = Rehydrator(
        db, store, writer, SECRET, stale_grace_s=0.0, kinds=(StateKind.KS,),
    )
    writer.killswitch("org:acme", on=True)
    assert rehydrator.diagnose(StateKind.KS) is None

    store._engaged[StateKind.KS].discard("org:acme")

    assert rehydrator.diagnose(StateKind.KS) == "engaged"


def test_a_missing_entry_masked_by_an_extra_needs_the_deep_check() -> None:
    """A10's residual, stated honestly and closed on a slow cadence.

    The counts agree, the manifest verifies, the index head is right -- and one record's
    position is wrong. Nothing short of comparing the index can see it.
    """
    writer, _db, store, rehydrator = _lab()
    writer.plan_set("org-a", _plan_body("org-a"))
    writer.plan_set("org-b", _plan_body("org-b"))
    store._index[StateKind.PLAN]["org-a"] = store._index[StateKind.PLAN]["org-b"]

    assert rehydrator.diagnose(StateKind.PLAN) is None, "O(1) cannot see this"
    assert rehydrator.verify(StateKind.PLAN) == CONTENTS


# --- repair -----------------------------------------------------------------------------------


def test_a_flushed_store_is_restored_from_postgres() -> None:
    writer, _db, store, rehydrator = _lab()
    for position in range(1, 51):
        writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    store.flush()

    summary = rehydrator.round_once()

    assert len(summary.repairs) == 1
    event = summary.repairs[0]
    assert (event.kind, event.reason, event.records) == (StateKind.PLAN, MISSING, 50)
    assert store.whole_kind_publishes == 1
    assert rehydrator.diagnose(StateKind.PLAN) is None


def test_a_restored_store_is_served_again_by_the_gateway() -> None:
    writer, _db, store, rehydrator = _lab()
    gateway = Gateway(store)
    writer.plan_set("org-a", _plan_body("org-a"))
    gateway.poll(StateKind.PLAN)

    store.flush()
    assert gateway.round_once(StateKind.PLAN).ok is False
    rehydrator.round_once()
    recovered = gateway.round_once(StateKind.PLAN)

    assert recovered.ok, recovered.error
    assert isinstance(gateway.plans.read("org-a"), ExecutionPlan)


def test_an_unpublished_write_is_published_by_the_round() -> None:
    db = MemoryControlDB()
    store = MemoryStore()
    good = StateWriter(db, store, SECRET)
    broken = StateWriter(db, BrokenPublisher(), SECRET)
    good.plan_set("org-a", _plan_body("org-a"))
    broken.plan_set("org-b", _plan_body("org-b"))
    rehydrator = Rehydrator(db, store, good, SECRET, stale_grace_s=0.0, kinds=ONE_KIND)
    gateway = Gateway(store)

    summary = rehydrator.round_once()
    gateway.poll(StateKind.PLAN)

    assert [event.reason for event in summary.repairs] == [STALE]
    assert isinstance(gateway.plans.read("org-b"), ExecutionPlan)


def test_a_store_ahead_repair_bumps_the_epoch_first() -> None:
    """Republishing at the old version would be a regress, which C36 refuses by design."""
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    rogue = StateWriter(MemoryControlDB(), store, SECRET)
    writer.plan_set("org-a", _plan_body("org-a"))
    for position in range(5):
        rogue.plan_set(f"org-x{position}", _plan_body(f"org-x{position}"))
    rehydrator = Rehydrator(db, store, writer, SECRET, stale_grace_s=0.0, kinds=ONE_KIND)
    before = db.counters(StateKind.PLAN).version

    summary = rehydrator.round_once()

    assert [event.reason for event in summary.repairs] == [STORE_AHEAD]
    after = db.counters(StateKind.PLAN).version
    assert after.epoch == before.epoch + 1
    assert after.seq == 0
    assert db.counters(StateKind.PLAN).feed_seq > 1, "the cursor still moved forward"


def test_the_stale_grace_rechecks_before_repairing() -> None:
    """A writer publishes right after its commit; that in-flight publish is not a fault."""
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    slept: list[float] = []

    rehydrator = Rehydrator(
        db,
        store,
        writer,
        SECRET,
        stale_grace_s=0.25,
        clock=lambda: 0.0,
        sleep=slept.append,
        kinds=ONE_KIND,
    )
    broken = StateWriter(db, BrokenPublisher(), SECRET)
    writer.plan_set("org-a", _plan_body("org-a"))
    broken.plan_set("org-b", _plan_body("org-b"))

    rehydrator.round_once()

    assert slept == [0.25], "stale is re-checked once after the grace window"


def test_a_kind_failing_does_not_stop_another() -> None:
    """H7: per-kind isolation, in the component GW05b puts on the availability path."""

    class HalfBroken(MemoryStore):
        def stored_head(self, kind: StateKind) -> object:  # type: ignore[override]
            if kind is StateKind.PLAN:
                raise ConnectionError("injected store failure")
            return super().stored_head(kind)

    db = MemoryControlDB()
    store = HalfBroken()
    writer = StateWriter(db, store, SECRET)
    writer.killswitch("org:acme", on=True)
    rehydrator = Rehydrator(
        db,
        store,
        writer,
        SECRET,
        stale_grace_s=0.0,
        kinds=(StateKind.PLAN, StateKind.KS),
    )

    summary = rehydrator.round_once()

    assert summary.ok is False
    assert [kind for kind, _ in summary.errors] == [StateKind.PLAN]
    assert summary.healthy == (StateKind.KS,)


def test_a_repair_event_carries_its_timings() -> None:
    # Reading 1 is the round's start (GW05b stamps at it); 2 and 3 bracket the repair.
    moments = iter([9.5, 10.0, 10.5])
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    writer.plan_set("org-a", _plan_body("org-a"))
    store.flush()
    rehydrator = Rehydrator(
        db, store, writer, SECRET, stale_grace_s=0.0,
        clock=lambda: next(moments), kinds=ONE_KIND,
    )

    event = rehydrator.round_once().repairs[0]

    assert event.detected_at == 10.0
    assert event.restored_at == 10.5
    assert event.took_ms == 500.0


def test_a_rollback_is_not_mistaken_for_a_divergence() -> None:
    """An epoch bump raises the version AND the cursor, so the store stays consistent."""
    writer, db, store, rehydrator = _lab()
    writer.plan_set("org-a", _plan_body("org-a"))
    first = db.log()[0]
    writer.plan_set("org-a", _plan_body("org-a"))

    writer.rollback(first.log_id)

    assert db.counters(StateKind.PLAN).version == Version(2, 0)
    assert rehydrator.diagnose(StateKind.PLAN) is None


def test_an_empty_kind_is_healthy_once_published() -> None:
    writer, _db, store, rehydrator = _lab()

    assert rehydrator.diagnose(StateKind.PLAN) == MISSING

    writer.plan_set("org-a", _plan_body("org-a"))
    assert rehydrator.diagnose(StateKind.PLAN) is None


def test_the_rehydrator_is_the_only_whole_kind_publisher() -> None:
    import ast
    import inspect

    import state_control.rehydrate as rehydrate_module
    import state_control.writer as writer_module

    def publisher_calls(module: object) -> set[str]:
        tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
        return {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "_publisher"
        }

    assert "publish_kind" not in publisher_calls(writer_module)
    assert "publish_kind" in publisher_calls(rehydrate_module)


def test_verify_passes_on_a_healthy_kind() -> None:
    writer, _db, _store, rehydrator = _lab()
    for position in range(1, 21):
        writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))

    assert rehydrator.verify(StateKind.PLAN) is None


def test_a_broken_store_reports_rather_than_raising() -> None:
    db = MemoryControlDB()
    writer = StateWriter(db, MemoryStore(), SECRET)
    rehydrator = Rehydrator(
        db, BrokenPublisher(), writer, SECRET, stale_grace_s=0.0, kinds=ONE_KIND,
    )

    summary = rehydrator.round_once()

    assert summary.ok is False
    assert "ConnectionError" in summary.errors[0][1]


def test_diagnose_raises_for_an_unreachable_store_so_the_round_can_classify_it() -> None:
    db = MemoryControlDB()
    writer = StateWriter(db, MemoryStore(), SECRET)
    rehydrator = Rehydrator(
        db, BrokenPublisher(), writer, SECRET, stale_grace_s=0.0, kinds=ONE_KIND,
    )

    with pytest.raises(ConnectionError):
        rehydrator.diagnose(StateKind.PLAN)
