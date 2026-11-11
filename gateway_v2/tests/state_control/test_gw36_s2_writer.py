"""GW36 S2 — the writer pieces the projector needs, and the `store_ahead` repair it depends on.

* compare-and-skip (`if_changed`) never bumps a version for an identical write, and compares
  the deleted and engaged flags as well as the body;
* the fence refuses a write once the projector lease is gone, and renews it while held;
* `put_many(require_new=True)` refuses an existing key with nothing committed (R7/R8);
* `repair_epoch` re-signs every record byte-for-byte with its deleted/op/engaged state, above the
  store's epoch AND position, so a reader that applied the higher position accepts the repair
  and the next round is clean (F11, R5); an empty kind stays at position 0 (R6).
"""

from __future__ import annotations

import asyncio
import random

import pytest

from gateway_v2.admit.identity import principal_of
from gateway_v2.domain.state import StateKind, StateOp, Version
from gateway_v2.runtime.state_sig import decode_record
from state_control.db import KindCounters, MemoryControlDB
from state_control.publisher import MemoryStore
from state_control.rehydrate import STORE_AHEAD, Rehydrator
from state_control.writer import (
    OK,
    UNCHANGED,
    Fence,
    LeaseLost,
    RecordExists,
    StateWriter,
)
from tests.state_control.test_lgw05c_writer import SECRET, Gateway, _plan_body

KEY_A = "a" * 64


def _key_body(org: str = "org-a") -> dict[str, object]:
    return {"key_id": "k1", "org_id": org, "rate_per_s": 0.0, "burst": 0.0}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _lab() -> tuple[StateWriter, MemoryControlDB, MemoryStore, Clock]:
    clock = Clock()
    db = MemoryControlDB(clock=clock)
    store = MemoryStore()
    return StateWriter(db, store, SECRET), db, store, clock


# --- compare-and-skip ----------------------------------------------------------------------


def test_an_identical_write_is_unchanged_and_bumps_nothing() -> None:
    writer, db, store, _ = _lab()
    first = writer.put(StateKind.KEY, KEY_A, _key_body(), if_changed=True)
    before = db.counters(StateKind.KEY)

    again = writer.put(StateKind.KEY, KEY_A, _key_body(), if_changed=True)

    assert first.status == OK
    assert again.status == UNCHANGED and again.durable and not again.published
    assert db.counters(StateKind.KEY) == before, "an unchanged write must not move the head"
    assert store.stored_feed_seq(StateKind.KEY) == before.feed_seq


def test_the_deleted_flag_and_the_engaged_flag_are_part_of_the_comparison() -> None:
    writer, db, _, _ = _lab()
    writer.put(StateKind.KEY, KEY_A, _key_body(), if_changed=True)
    revoked = writer.put(
        StateKind.KEY, KEY_A, _key_body(), deleted=True, op=StateOp.REVOKE, if_changed=True,
    )
    assert revoked.status == OK, "same body, now deleted: that IS a change"

    writer.put(StateKind.KS, "org:org-a", {"on": True}, engaged=True, if_changed=True)
    assert db.counters(StateKind.KS).on_count == 1
    repeat = writer.put(StateKind.KS, "org:org-a", {"on": True}, engaged=True, if_changed=True)
    assert repeat.status == UNCHANGED


def test_without_if_changed_the_old_behaviour_is_kept() -> None:
    writer, db, _, _ = _lab()
    writer.put(StateKind.KEY, KEY_A, _key_body())
    seq = db.counters(StateKind.KEY).feed_seq
    writer.put(StateKind.KEY, KEY_A, _key_body())
    assert db.counters(StateKind.KEY).feed_seq == seq + 1


def test_unchanged_property_over_random_sequences() -> None:
    """Seeded: a write is UNCHANGED exactly when (body, deleted) equals the stored record."""
    seed = 0x36_5201
    rng = random.Random(seed)
    for iteration in range(10_000):
        writer, db, _, _ = _lab()
        stored: tuple[str, bool] | None = None
        for _step in range(rng.randint(1, 4)):
            org = rng.choice(("org-a", "org-b"))
            deleted = rng.random() < 0.3
            before = db.counters(StateKind.KEY)
            outcome = writer.put(
                StateKind.KEY, KEY_A, _key_body(org),
                deleted=deleted, op=StateOp.REVOKE if deleted else StateOp.PUT, if_changed=True,
            )
            expect_unchanged = stored == (org, deleted)
            assert (outcome.status == UNCHANGED) is expect_unchanged, (
                f"seed={seed} iteration={iteration} stored={stored} wrote={(org, deleted)}"
            )
            if expect_unchanged:
                assert db.counters(StateKind.KEY) == before, f"seed={seed} it={iteration}"
            stored = (org, deleted)


# --- the fence -----------------------------------------------------------------------------


def test_a_fenced_write_needs_the_lease_and_renews_it() -> None:
    writer, db, _, clock = _lab()
    fence = Fence("projector-a", 5_000)
    with pytest.raises(LeaseLost):
        writer.put(StateKind.KEY, KEY_A, _key_body(), fence=fence)
    assert db.counters(StateKind.KEY).feed_seq == 0, "a fenced-out write commits nothing"

    assert db.claim_lease("projector-a", 5_000)
    clock.now += 4.0
    writer.put(StateKind.KEY, KEY_A, _key_body(), fence=fence)
    clock.now += 4.0  # 8 s after the claim: alive only because the write renewed it
    assert db.lease_holder() == "projector-a"


def test_a_takeover_fences_the_old_holder_out() -> None:
    writer, db, _, clock = _lab()
    assert db.claim_lease("projector-a", 5_000)
    assert not db.claim_lease("projector-b", 5_000), "a live lease is not taken over"
    clock.now += 6.0
    assert db.claim_lease("projector-b", 5_000), "an expired lease is"

    with pytest.raises(LeaseLost):
        writer.put(StateKind.KEY, KEY_A, _key_body(), fence=Fence("projector-a", 5_000))
    assert db.counters(StateKind.KEY).feed_seq == 0


def test_the_heartbeat_needs_the_lease_and_never_moves_backwards() -> None:
    _, db, _, clock = _lab()
    assert not db.mark_projected("projector-a", 990.0)
    assert db.source_lag_s() is None
    db.claim_lease("projector-a", 5_000)
    assert db.mark_projected("projector-a", 995.0)
    assert db.mark_projected("projector-a", 990.0)
    assert db.source_lag_s() == pytest.approx(clock.now - 995.0)


# --- bulk onboarding -----------------------------------------------------------------------


def test_put_many_require_new_refuses_an_existing_key_and_commits_nothing() -> None:
    writer, db, _, _ = _lab()
    writer.put(StateKind.KEY, KEY_A, _key_body())
    before = db.counters(StateKind.KEY)
    with pytest.raises(RecordExists):
        writer.put_many(
            StateKind.KEY, [("b" * 64, _key_body()), (KEY_A, _key_body())], require_new=True,
        )
    assert db.counters(StateKind.KEY) == before


# --- the store_ahead repair (F11, R5, R6) --------------------------------------------------


def test_a_warm_reader_that_applied_the_rogue_position_accepts_the_repair() -> None:
    """F11: the reader's floor is the rogue store's position 9; the repair must clear it."""
    writer, db, store, _ = _lab()
    rogue = StateWriter(MemoryControlDB(), store, SECRET)
    for index in range(9):
        rogue.killswitch(f"org:rogue{index}", on=False)
    gateway = Gateway(store)
    gateway.poll(StateKind.KS)  # applied position 9, version (1, 9)
    writer.killswitch("org:org-a", on=True)  # durable; the store refuses it (ahead)
    writer.killswitch("org:org-b", on=False)

    rehydrator = Rehydrator(db, store, writer, SECRET, stale_grace_s=0.0, kinds=(StateKind.KS,))
    first = rehydrator.round_once()

    assert [event.reason for event in first.repairs] == [STORE_AHEAD]
    counters = db.counters(StateKind.KS)
    assert counters.feed_seq > 9 and counters.version.epoch == 2
    assert db.snapshot(StateKind.KS)[2] == ("org:org-a",), "the engaged flag survives"
    assert counters.on_count == 1
    assert rehydrator.round_once().repairs == (), "a repaired kind reads clean next round"
    gateway.poll(StateKind.KS)
    assert gateway.switches.org_killed("org-a"), "the warm reader accepted the repair"
    assert not gateway.switches.org_killed("org-b")


def test_a_repair_resigns_a_revoked_key_as_revoked() -> None:
    writer, db, store, _ = _lab()
    writer.put(StateKind.KEY, KEY_A, _key_body())
    writer.put(StateKind.KEY, KEY_A, _key_body(), deleted=True, op=StateOp.REVOKE)
    moved = writer.repair_epoch(
        StateKind.KEY, store_version=Version(7, 3), store_feed_seq=40,
    )
    assert moved.version == Version(8, 1)
    assert moved.feed_seq == 41
    record = db.snapshot(StateKind.KEY)[1][0]
    assert record.deleted and record.op is StateOp.REVOKE
    assert principal_of(record) is None, "a revoked key must never come back from a repair"
    raw = asyncio.run(_publish_and_read(writer, db, store))
    assert principal_of(decode_record(SECRET, StateKind.KEY, raw)) is None


async def _publish_and_read(writer: StateWriter, db: MemoryControlDB, store: MemoryStore) -> bytes:
    counters, records, engaged = db.snapshot(StateKind.KEY)
    store.publish_kind(
        StateKind.KEY, records, writer.manifest_for(StateKind.KEY, counters), engaged,
        allow_regress=True,
    )
    (raw,) = await store.records(StateKind.KEY, (KEY_A,))
    assert raw is not None
    return raw


def test_an_empty_kind_repair_stays_at_position_zero() -> None:
    """Readers refuse count 0 at any position but 0, so the old +1 made BUDGET unreadable."""
    writer, db, _, _ = _lab()
    moved = writer.repair_epoch(StateKind.BUDGET, store_version=Version(1, 9), store_feed_seq=9)
    assert moved == KindCounters(version=Version(2, 0), feed_seq=0, count=0, on_count=0)


def test_plan_bodies_survive_a_repair_byte_for_byte() -> None:
    writer, db, _, _ = _lab()
    writer.plan_set("org-a", _plan_body("org-a"))
    before = db.snapshot(StateKind.PLAN)[1][0]
    writer.repair_epoch(StateKind.PLAN, store_version=Version(1, 5), store_feed_seq=5)
    after = db.snapshot(StateKind.PLAN)[1][0]
    assert after.body == before.body and after.content_hash == before.content_hash
    assert after.feed_seq == 6 and after.version == Version(2, 1)
