"""B8 / R2-13 (local half): the flush chaos drill against the shipped in-memory twins.

This file generalizes the shipped single-example flush -> restore assertion
(`test_lgw05b_rehydrate_stamp.py::test_a_repaired_kind_is_still_stamped`) into a seeded,
randomized, multi-kind property drill with a DEEP 0-violations verify. It reproduces the C36
"store flush, Postgres untouched" fault -- wipe the published copy, leave the durable truth
intact -- and asserts that ONE re-hydrator round restores every kind from the durable truth with
zero Restore_Violations.

The card ships ONLY this test file: no production code, no config change. Every mechanism the
drill exercises is already shipped -- `MemoryStore.flush()`, the re-hydrator-only whole-kind
`publish_kind`, `MemoryControlDB` (the durable twin a flush never touches),
`Rehydrator.round_once`, `StateWriter`, `ALL_KINDS` / `StateKind`. The drill rebuilds nothing.

Idiom: a seeded `random.Random` driven for >= 10,000 iterations, NO hypothesis. The five design
properties (Properties 1-5) map one-to-one onto five property tests. Seeding goes through the
real `StateWriter` path only (`_seed_random`) -- store contents are never fabricated.

The correctness bound is a deterministic ROUND COUNT (one round), never a wall-clock figure. The
live >= 2.05 s wall-clock flush-restore certification is a Deferred_Cloud_Gate owned by T21.
"""

from __future__ import annotations

import random

from gateway_v2.domain.plan import StreamingMode
from gateway_v2.domain.state import StateKind
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from state_control.db import KindCounters, MemoryControlDB
from state_control.publisher import MemoryStore, StoredHead
from state_control.rehydrate import (
    ALL_KINDS,
    MISSING,
    Rehydrator,
    RepairEvent,
    RoundSummary,
    StateWriter,
)
from tests.plan.test_lgw05c_delta import _draft
from tests.state_control.test_lgw05c_writer import SECRET

# A healthy run keeps iterations light and cheap; the point is coverage of the seed space, not a
# single enormous estate. Every property test runs the shipped house-idiom budget.
ITERATIONS = 10_000


def _plan_body(org_id: str) -> dict[str, object]:
    """One plan document body, exactly as the reference `_plan_body` builds it."""
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


# --- the three non-optional helpers --------------------------------------------------------------


def _lab() -> tuple[StateWriter, MemoryControlDB, MemoryStore, Rehydrator]:
    """Mirror of `test_lgw05b_rehydrate_stamp.py::_lab` -- the full-coverage drill lab.

    A full-coverage re-hydrator (every kind in `ALL_KINDS`) with `stale_grace_s=0.0` and a fixed
    deterministic clock, so a round never sleeps and the drill is reproducible from its seed.
    """
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    rehydrator = Rehydrator(
        db,
        store,
        writer,
        SECRET,
        stale_grace_s=0.0,
        clock=lambda: 1_000.0,
        kinds=ALL_KINDS,
    )
    return writer, db, store, rehydrator


def _seed_random(writer: StateWriter, rng: random.Random) -> None:
    """Randomized multi-kind seeding through the real `StateWriter` path only.

    Bounded small counts per kind; every kind gets at least one write. PLAN via `plan_set`, KEY
    via `key_add` with a random rate/burst, KS via `killswitch` with a random `on=` toggle (so
    `on_count` / `engaged_count` is non-trivial), BUDGET via `put(StateKind.BUDGET, ...)`. No
    store contents are fabricated: the durable truth is built the way the control plane builds it.
    """
    for org in range(1 + rng.randrange(3)):
        writer.plan_set(f"org-{org}", _plan_body(f"org-{org}"))

    for n in range(1 + rng.randrange(3)):
        writer.key_add(
            f"hash-{n}",
            f"org-{n}",
            key_id=f"k-{n}",
            rate_per_s=round(rng.uniform(1.0, 50.0), 3),
            burst=round(rng.uniform(1.0, 100.0), 3),
        )

    for n in range(1 + rng.randrange(3)):
        writer.killswitch(f"scope-{n}", on=rng.random() < 0.5)

    for n in range(1 + rng.randrange(3)):
        writer.put(StateKind.BUDGET, f"org-{n}", {"limit": rng.randrange(1, 10_000)})


def _restored_matches_durable(store: MemoryStore, db: MemoryControlDB) -> list[str]:
    """The exact per-kind 0-violations comparison. Empty list == 0 Restore_Violations.

    For every kind, the store's published head must agree with the durable truth on position,
    record count and engaged-set size, and -- DEEP -- the full per-key index must agree with the
    durable snapshot's positions. Each disagreement is one violation string.
    """
    violations: list[str] = []
    for kind in ALL_KINDS:
        counters = db.counters(kind)
        if store.stored_feed_seq(kind) != counters.feed_seq:
            violations.append(
                f"{kind.value}: feed_seq {store.stored_feed_seq(kind)} != {counters.feed_seq}",
            )
        head = store.stored_head(kind)
        if head.index_count != counters.count:
            violations.append(
                f"{kind.value}: index_count {head.index_count} != count {counters.count}",
            )
        if head.engaged_count != counters.on_count:
            violations.append(
                f"{kind.value}: engaged_count {head.engaged_count} != on_count "
                f"{counters.on_count}",
            )
        _, records, _ = db.snapshot(kind)
        durable_index = {record.key: record.feed_seq for record in records}
        if dict(store.stored_index(kind)) != durable_index:
            violations.append(f"{kind.value}: deep index mismatch against durable positions")
    return violations


# --- Property 1: flush then a round restores 0 Restore_Violations --------------------------------
# Feature: flush-drill-r2-13, Property 1: for any seeded multi-kind state, after a flush one
# re-hydrator round restores every kind from the durable truth with 0 Restore_Violations (position,
# counts, engaged-set size and the full per-key index all agree).
# Validates: Requirements 4.1, 4.2, 4.3, 4.4
def test_property_1_flush_then_a_round_restores_zero_violations() -> None:
    rng = random.Random(0x_B8_13_01)
    for _ in range(ITERATIONS):
        writer, db, store, rehydrator = _lab()
        _seed_random(writer, rng)

        store.flush()
        rehydrator.round_once(deep=True)

        assert _restored_matches_durable(store, db) == []


# --- Property 2: every flushed kind is repaired with reason MISSING ------------------------------
# Feature: flush-drill-r2-13, Property 2: after a flush a deep round repairs exactly every kind,
# each repair carrying reason MISSING, with no errors and an ok summary.
# Validates: Requirements 3.1, 3.2, 3.3
def test_property_2_every_flushed_kind_repaired_with_reason_missing() -> None:
    rng = random.Random(0x_B8_13_02)
    for _ in range(ITERATIONS):
        writer, _db, store, rehydrator = _lab()
        _seed_random(writer, rng)

        store.flush()
        summary = rehydrator.round_once(deep=True)

        assert {event.kind for event in summary.repairs} == set(StateKind)
        assert all(event.reason == MISSING for event in summary.repairs)
        assert summary.errors == ()
        assert summary.ok


# --- Property 3: one-round bound, the second round is a no-op ------------------------------------
# Feature: flush-drill-r2-13, Property 3: the restore completes within the Declared_Flush_Bound of
# ONE round; a second deep round immediately after performs no repair. The bound is a deterministic
# round count, never a wall-clock figure.
# Validates: Requirements 5.1, 5.2, 5.3
def test_property_3_restore_completes_in_one_round_second_is_a_noop() -> None:
    rng = random.Random(0x_B8_13_03)
    for _ in range(ITERATIONS):
        writer, db, store, rehydrator = _lab()
        _seed_random(writer, rng)

        store.flush()
        first = rehydrator.round_once(deep=True)
        assert {event.kind for event in first.repairs} == set(StateKind)
        assert _restored_matches_durable(store, db) == []

        second = rehydrator.round_once(deep=True)
        assert second.repairs == ()
        assert second.ok


# --- Property 4: a flush never touches the durable truth -----------------------------------------
# Feature: flush-drill-r2-13, Property 4: a flush leaves the Durable_Twin unchanged -- for every
# kind the counters and the snapshot are identical before and after the flush, so the restore
# always has the full durable state to recover from.
# Validates: Requirements 2.1, 2.3
def test_property_4_a_flush_never_touches_the_durable_truth() -> None:
    rng = random.Random(0x_B8_13_04)
    for _ in range(ITERATIONS):
        writer, db, store, _rehydrator = _lab()
        _seed_random(writer, rng)
        before = {kind: db.counters(kind) for kind in ALL_KINDS}
        before_snap = {kind: db.snapshot(kind) for kind in ALL_KINDS}

        store.flush()

        after = {kind: db.counters(kind) for kind in ALL_KINDS}
        after_snap = {kind: db.snapshot(kind) for kind in ALL_KINDS}
        assert after == before
        assert after_snap == before_snap


# --- Property 5: in-flight durable writes around a flush still restore cleanly (L05b-3, honest) --
# Feature: flush-drill-r2-13, Property 5: with additional durable writes committed around the
# flush, a round still restores every kind with 0 Restore_Violations.
# Validates: Requirements 6.1, 6.2, 6.3
def test_property_5_in_flight_durable_writes_around_a_flush_still_restore() -> None:
    """L05b-3 flavour, honestly scoped.

    NO `FOR UPDATE` lock and NO queued-writer fail-fast is modelled here: the in-memory twin has
    neither, so this drill does not fake one. The held-lock + queued-writer fail-fast is deferred
    to the live L05b-3 / G-14 gate. What this asserts is the code-side guarantee the local half
    owns: durable writes landing around the flush are all recovered, because the durable truth is
    never the thing a flush wipes.
    """
    rng = random.Random(0x_B8_13_05)
    for _ in range(ITERATIONS):
        writer, db, store, rehydrator = _lab()
        _seed_random(writer, rng)

        # A durable write just BEFORE the flush -- committed to Postgres, published to the store.
        writer.plan_set("org-pre", _plan_body("org-pre"))
        store.flush()
        # And a durable write just AFTER the flush -- Postgres has it; the flushed store does not
        # yet, which is precisely what the re-hydrator round must reconcile.
        writer.put(StateKind.BUDGET, "org-post", {"limit": 1 + rng.randrange(10_000)})

        rehydrator.round_once(deep=True)

        assert _restored_matches_durable(store, db) == []


# --- example / integration tests -----------------------------------------------------------------


def test_missing_reason_per_kind_and_one_whole_kind_publish_each() -> None:
    """A representative seed: flush, round -> every repair reason is MISSING and the re-hydrator
    performed exactly one whole-kind publish per kind (its restore path, Req 3.2)."""
    writer, _db, store, rehydrator = _lab()
    _seed_random(writer, random.Random(1))

    store.flush()
    summary = rehydrator.round_once(deep=True)

    assert {event.kind for event in summary.repairs} == set(StateKind)
    assert all(event.reason == MISSING for event in summary.repairs)
    assert store.whole_kind_publishes == len(ALL_KINDS)


def test_one_round_bound_then_second_round_no_op() -> None:
    """The explicit Req-5 example: restore in one round, the second round repairs nothing."""
    writer, db, store, rehydrator = _lab()
    _seed_random(writer, random.Random(2))

    store.flush()
    rehydrator.round_once(deep=True)
    assert _restored_matches_durable(store, db) == []

    second = rehydrator.round_once(deep=True)
    assert second.repairs == ()
    assert second.ok


def test_l05b_3_honest_flavour_durable_writes_around_a_flush_restore_cleanly() -> None:
    # No `FOR UPDATE` lock is modelled: the in-memory twin has none, so this test does not fake
    # one. It asserts only the local-half guarantee -- durable writes around the flush all recover.
    writer, db, store, rehydrator = _lab()
    _seed_random(writer, random.Random(3))

    writer.plan_set("org-pre", _plan_body("org-pre"))
    store.flush()
    writer.put(StateKind.BUDGET, "org-post", {"limit": 500})

    rehydrator.round_once(deep=True)

    assert _restored_matches_durable(store, db) == []


def test_pre_flush_baseline_then_flushed_store_assertion() -> None:
    """A known-good start (Req 1.2) and the flushed-store assertion (Req 2.2).

    Pre-flush: the store holds exactly the durable position for every kind and the drill
    comparison is already empty. Post-flush: the store holds nothing for any kind while the
    durable counters survive untouched.
    """
    writer, db, store, _rehydrator = _lab()
    _seed_random(writer, random.Random(4))

    # Pre-flush baseline: a known-good start the drill measures the restore against.
    for kind in ALL_KINDS:
        assert store.stored_feed_seq(kind) == db.counters(kind).feed_seq
    assert _restored_matches_durable(store, db) == []

    durable_before = {kind: db.counters(kind) for kind in ALL_KINDS}
    store.flush()

    # Flushed store: no published position, no manifest -- the fault the drill reproduces.
    for kind in ALL_KINDS:
        assert store.stored_feed_seq(kind) is None
        head: StoredHead = store.stored_head(kind)
        assert head.manifest_raw is None
    # Durable truth survives the flush, so the restore always has something to recover from.
    assert {kind: db.counters(kind) for kind in ALL_KINDS} == durable_before


def test_round_summary_and_repair_event_types_are_the_shipped_frozen_types() -> None:
    """The drill reads the shipped frozen types directly; it introduces no new value type."""
    writer, db, store, rehydrator = _lab()
    _seed_random(writer, random.Random(5))

    store.flush()
    summary = rehydrator.round_once(deep=True)

    assert isinstance(summary, RoundSummary)
    assert all(isinstance(event, RepairEvent) for event in summary.repairs)
    assert isinstance(db.counters(StateKind.PLAN), KindCounters)
