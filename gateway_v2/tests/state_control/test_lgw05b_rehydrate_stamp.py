"""GW05b phase 2: the re-hydrator writes the stamp. Gateways still ignore it.

The load-bearing test here is `test_a_round_that_cannot_verify_one_kind_stamps_nothing`. The
tempting alternative -- stamp the kinds that did succeed -- would let one permanently broken
kind sit behind a fresh-looking stamp forever, which is the defect dressed up as a fix.

`test_a_concurrent_publish_is_not_mistaken_for_a_store_ahead` is the ordering fix this phase
carries in from the rc3 reference: the store must be read BEFORE Postgres, or an ordinary
healthy write landing between the two reads bumps the epoch for nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import fakeredis
import pytest

from gateway_v2.domain.plan import StreamingMode
from gateway_v2.domain.state import (
    Cursor,
    Manifest,
    SignedRecord,
    Stamp,
    StateKind,
    StoreDataUnavailable,
    Version,
)
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.runtime.state_sig import decode_stamp, encode_stamp, make_stamp
from gateway_v2.runtime.store_keys import StoreKeys
from state_control.db import KindCounters, MemoryControlDB
from state_control.publisher import BrokenPublisher, MemoryStore, StoredHead
from state_control.rehydrate import (
    ALL_KINDS,
    STALE,
    STORE_AHEAD,
    Rehydrator,
    default_rehydrator_id,
)
from state_control.valkey import ValkeyPublisher
from state_control.writer import StateWriter
from tests.plan.test_lgw05c_delta import _draft
from tests.state_control.test_lgw05c_writer import SECRET

NAME = "rehydrator-a:4711"


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


def _lab(
    *,
    kinds: tuple[StateKind, ...] = ALL_KINDS,
    moments: list[float] | None = None,
) -> tuple[StateWriter, MemoryControlDB, MemoryStore, Rehydrator]:
    """A full-coverage re-hydrator by default, because a partial one cannot stamp at all."""
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)
    ticks = iter(moments) if moments is not None else None
    rehydrator = Rehydrator(
        db,
        store,
        writer,
        SECRET,
        stale_grace_s=0.0,
        clock=(lambda: next(ticks)) if ticks is not None else (lambda: 1_000.0),
        kinds=kinds,
        name=NAME,
    )
    return writer, db, store, rehydrator


def _seed(writer: StateWriter) -> None:
    """One write per kind, so every kind has a position worth attesting."""
    writer.plan_set("org-a", _plan_body("org-a"))
    writer.key_add("hash-a", "org-a", key_id="k-a", rate_per_s=10.0, burst=20.0)
    writer.killswitch("global", on=False)
    writer.put(StateKind.BUDGET, "org-a", {"limit": 1_000})


# --- a round that verified everything stamps -----------------------------------------------------


def test_a_clean_round_stamps_every_kind_with_the_postgres_cursor() -> None:
    writer, db, store, rehydrator = _lab()
    _seed(writer)

    summary = rehydrator.round_once()

    assert summary.ok and not summary.repairs
    stamp = summary.stamped
    assert stamp is not None
    assert set(stamp.cursors) == set(StateKind), "a stamp covers every kind or it is withheld"
    for kind in StateKind:
        counters = db.counters(kind)
        assert stamp.cursors[kind] == Cursor(counters.version, counters.feed_seq)


def test_the_stamp_lands_in_the_store_and_verifies_under_the_secret() -> None:
    writer, _db, store, rehydrator = _lab()
    _seed(writer)

    minted = rehydrator.round_once().stamped

    assert minted is not None
    assert decode_stamp(SECRET, store.read_stamp()) == minted
    assert store.stamp_writes == 1


def test_the_stamp_names_which_rehydrator_wrote_it() -> None:
    """Operators need to know WHICH re-hydrator last verified, with two of them running."""
    writer, _db, _store, rehydrator = _lab()
    _seed(writer)

    stamp = rehydrator.round_once().stamped

    assert stamp is not None and stamp.by == NAME


def test_the_default_rehydrator_id_is_host_and_pid() -> None:
    host, _, pid = default_rehydrator_id().rpartition(":")
    assert host and pid.isdigit()


def test_verified_at_is_the_rounds_start_not_its_end() -> None:
    """`verified_at` must not drift later than the round began, or the stamp over-claims.

    The round reads the store, then Postgres, so a position it compares covers every commit
    that landed before the round STARTED. Dating the stamp at the end would assert freshness
    for a window the round never looked at -- by the round's whole duration, which under load
    is exactly when the claim matters.
    """
    writer, _db, _store, rehydrator = _lab(moments=[2_000.0, 2_001.0, 2_002.0, 2_003.0])
    _seed(writer)

    stamp = rehydrator.round_once().stamped

    assert stamp is not None and stamp.verified_at == 2_000.0


def test_an_empty_estate_still_stamps() -> None:
    """A brand-new deployment has no records, which is not the same as being unverified.

    If an empty estate could not stamp, every fresh install would fail closed until its first
    write -- the fix refusing to let the system start.
    """
    _writer, _db, store, rehydrator = _lab()

    summary = rehydrator.round_once()

    assert summary.stamped is not None
    assert decode_stamp(SECRET, store.read_stamp()) == summary.stamped


# --- a repaired kind counts as verified ----------------------------------------------------------


def test_a_repaired_kind_is_still_stamped() -> None:
    """After a restore the store was rewritten FROM Postgres, so it matches by construction."""
    writer, db, store, rehydrator = _lab()
    _seed(writer)
    store.flush()

    summary = rehydrator.round_once()

    assert {event.kind for event in summary.repairs} == set(StateKind)
    stamp = summary.stamped
    assert stamp is not None
    for kind in StateKind:
        counters = db.counters(kind)
        assert stamp.cursors[kind] == Cursor(counters.version, counters.feed_seq)


def test_a_store_ahead_repair_stamps_the_post_bump_cursor() -> None:
    """The PRE-repair counters are the wrong answer: the epoch bump moved them."""
    writer, db, store, rehydrator = _lab()
    _seed(writer)
    before = db.counters(StateKind.PLAN)
    counters, records, engaged = db.snapshot(StateKind.PLAN)
    ahead = type(counters)(
        version=Version(counters.version.epoch, counters.version.seq + 5),
        feed_seq=counters.feed_seq + 5,
        count=counters.count,
        on_count=counters.on_count,
    )
    store.publish_kind(
        StateKind.PLAN, records, writer.manifest_for(StateKind.PLAN, ahead), engaged,
    )
    assert rehydrator.diagnose(StateKind.PLAN) == STORE_AHEAD

    summary = rehydrator.round_once()

    stamp = summary.stamped
    assert stamp is not None
    after = db.counters(StateKind.PLAN)
    assert after.version.epoch > before.version.epoch, "the repair bumped the epoch"
    assert stamp.cursors[StateKind.PLAN] == Cursor(after.version, after.feed_seq)


# --- honest silence (I4) -------------------------------------------------------------------------


def test_a_round_that_cannot_verify_one_kind_stamps_nothing() -> None:
    """Stamping the kinds that worked would hide the one that does not, permanently."""
    writer, _db, store, rehydrator = _lab()
    _seed(writer)
    original = store.stored_head

    def only_budget_fails(kind: StateKind) -> StoredHead:
        if kind is StateKind.BUDGET:
            raise ConnectionError("injected store failure")
        return original(kind)

    store.stored_head = only_budget_fails  # type: ignore[method-assign]

    summary = rehydrator.round_once()

    assert [kind for kind, _ in summary.errors] == [StateKind.BUDGET]
    assert summary.ok is False
    assert summary.stamped is None
    assert store.read_stamp() is None


def test_one_kinds_failure_does_not_stop_its_siblings() -> None:
    """H7, asserted again now that stamping runs after the loop."""
    writer, _db, store, rehydrator = _lab()
    _seed(writer)
    store.flush()
    original = store.publish_kind

    def plan_cannot_restore(
        kind: StateKind,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        engaged: Sequence[str] = (),
    ) -> bool:
        if kind is StateKind.PLAN:
            raise ConnectionError("injected store failure")
        return original(kind, records, manifest, engaged)

    store.publish_kind = plan_cannot_restore  # type: ignore[method-assign]

    summary = rehydrator.round_once()

    assert [kind for kind, _ in summary.errors] == [StateKind.PLAN]
    assert {event.kind for event in summary.repairs} == set(StateKind) - {StateKind.PLAN}
    assert summary.stamped is None, "plan was not verified, so nothing is claimed"


def test_the_withheld_warning_is_logged_once_not_once_a_round(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A per-round log line would bury the transition it exists to announce."""
    writer, _db, store, rehydrator = _lab()
    _seed(writer)
    store.stored_head = _always_fails  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="amf.state.rehydrate"):
        for _ in range(5):
            rehydrator.round_once()

    withheld = [r for r in caplog.records if "freshness stamp withheld" in r.getMessage()]
    assert len(withheld) == 1


def test_stamping_resumes_after_a_lapse(caplog: pytest.LogCaptureFixture) -> None:
    writer, _db, store, rehydrator = _lab()
    _seed(writer)
    healthy = store.stored_head
    store.stored_head = _always_fails  # type: ignore[method-assign]
    assert rehydrator.round_once().stamped is None
    store.stored_head = healthy  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="amf.state.rehydrate"):
        resumed = rehydrator.round_once()

    assert resumed.stamped is not None
    assert any("freshness stamp resumed" in r.getMessage() for r in caplog.records)


def test_a_store_that_cannot_accept_the_stamp_withholds_rather_than_raising() -> None:
    """A stamp write failure must not take down a round that otherwise succeeded."""
    db = MemoryControlDB()
    store = _StampRefusingStore()
    writer = StateWriter(db, store, SECRET)
    _seed(writer)
    rehydrator = Rehydrator(
        db, store, writer, SECRET, stale_grace_s=0.0, clock=lambda: 1_000.0, name=NAME,
    )

    summary = rehydrator.round_once()

    assert summary.ok, "every kind verified; only the stamp write failed"
    assert summary.stamped is None


def test_a_rehydrator_watching_a_subset_of_kinds_never_stamps(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """It cannot make the whole-store claim a stamp makes, so it makes none."""
    with caplog.at_level(logging.WARNING, logger="amf.state.rehydrate"):
        writer, _db, store, rehydrator = _lab(kinds=(StateKind.PLAN,))
    _seed(writer)

    summary = rehydrator.round_once()

    assert summary.ok and summary.stamped is None
    assert store.read_stamp() is None
    assert any("will write no freshness stamp" in r.getMessage() for r in caplog.records)


def test_a_rehydrator_id_with_a_newline_is_refused_at_construction() -> None:
    db = MemoryControlDB()
    store = MemoryStore()
    with pytest.raises(ValueError, match="must not contain a newline"):
        Rehydrator(db, store, StateWriter(db, store, SECRET), SECRET, name="a\nb")


# --- the deep flag -------------------------------------------------------------------------------


def test_a_deep_round_says_so_and_a_shallow_round_does_not() -> None:
    """Gateways treat both alike; an operator needs to see the gap between deep rounds.

    Tying freshness to the deep round instead would make global availability a function of
    record count, which is the rule GW05c exists to remove.
    """
    writer, _db, _store, rehydrator = _lab()
    _seed(writer)

    assert rehydrator.round_once().stamped is not None
    shallow = rehydrator.last_stamp
    deep = rehydrator.round_once(deep=True).stamped

    assert shallow is not None and shallow.deep is False
    assert deep is not None and deep.deep is True


def test_a_degraded_flag_is_not_set_yet() -> None:
    """Phase 5 owns the Postgres ride-through; a verified round must never claim degraded."""
    writer, _db, _store, rehydrator = _lab()
    _seed(writer)

    stamp = rehydrator.round_once().stamped

    assert stamp is not None and stamp.degraded is False


# --- read order (the rc3 fix) --------------------------------------------------------------------


def test_a_concurrent_publish_never_bumps_the_epoch() -> None:
    """The harm the read order prevents: a healthy write re-labelled as store_ahead.

    `repair` answers store_ahead with an epoch bump, and an epoch bump is not free -- it
    rewrites the kind wholesale and moves every reader's floor. So a round must never reach
    that conclusion from an ordinary concurrent write.

    The race is built by letting a write land immediately AFTER Postgres is read. With Postgres
    read first (RC2's order) the later store read then sees the newer manifest and the round
    concludes store_ahead. With the store read first, the manifest was captured before the
    write, so the round sees clean-or-stale and leaves the epoch alone.
    """
    writer, db, store, rehydrator = _lab()
    _seed(writer)
    before = db.counters(StateKind.PLAN).version.epoch
    original = db.counters
    landed = False

    def a_write_lands_right_after_postgres_is_read(kind: StateKind) -> KindCounters:
        nonlocal landed
        counters = original(kind)
        if kind is StateKind.PLAN and not landed:
            landed = True
            writer.plan_set("org-b", _plan_body("org-b"))
        return counters

    db.counters = a_write_lands_right_after_postgres_is_read  # type: ignore[method-assign]

    summary = rehydrator.round_once()

    assert landed, "the race was not actually constructed"
    assert STORE_AHEAD not in {event.reason for event in summary.repairs}
    assert db.counters(StateKind.PLAN).version.epoch == before


def test_a_genuinely_ahead_store_is_still_detected() -> None:
    """The control for the test above: the read order must not blind the real case."""
    writer, db, store, rehydrator = _lab()
    _seed(writer)
    counters, records, engaged = db.snapshot(StateKind.PLAN)
    ahead = type(counters)(
        version=Version(counters.version.epoch, counters.version.seq + 9),
        feed_seq=counters.feed_seq + 9,
        count=counters.count,
        on_count=counters.on_count,
    )
    store.publish_kind(
        StateKind.PLAN, records, writer.manifest_for(StateKind.PLAN, ahead), engaged,
    )

    assert rehydrator.diagnose(StateKind.PLAN) == STORE_AHEAD


def test_a_write_that_lands_mid_round_reads_as_stale_not_as_clean() -> None:
    """The store is read first, so a write committing after that read is visibly behind."""
    writer, _db, store, rehydrator = _lab()
    _seed(writer)
    original = store.stored_head
    landed = False

    def a_write_lands_right_after_the_store_is_read(kind: StateKind) -> StoredHead:
        nonlocal landed
        head = original(kind)
        if kind is StateKind.PLAN and not landed:
            landed = True
            writer.plan_set("org-b", _plan_body("org-b"))
        return head

    store.stored_head = a_write_lands_right_after_the_store_is_read  # type: ignore[method-assign]

    assert rehydrator.diagnose(StateKind.PLAN) == STALE


# --- put_stamp, which is what makes two re-hydrators safe ----------------------------------------


def _stamp_at(moment: float, *, by: str = NAME) -> Stamp:
    cursors = {kind: Cursor(Version(1, 1), 1) for kind in StateKind}
    return make_stamp(SECRET, moment, cursors, by)


def test_an_older_stamp_never_replaces_a_newer_one() -> None:
    """Two re-hydrators start rounds at different moments; the clock must not go backwards."""
    store = MemoryStore()
    assert store.put_stamp(_stamp_at(2_000.0, by="b")) is True

    assert store.put_stamp(_stamp_at(1_000.0, by="a")) is False

    assert decode_stamp(SECRET, store.read_stamp()).by == "b"
    assert store.stamp_races_lost == 1


def test_a_stamp_from_the_same_moment_does_not_replace() -> None:
    store = MemoryStore()
    assert store.put_stamp(_stamp_at(2_000.0, by="b")) is True
    assert store.put_stamp(_stamp_at(2_000.0, by="a")) is False


def test_a_newer_stamp_replaces_an_older_one() -> None:
    store = MemoryStore()
    assert store.put_stamp(_stamp_at(1_000.0, by="a")) is True

    assert store.put_stamp(_stamp_at(2_000.0, by="b")) is True

    assert decode_stamp(SECRET, store.read_stamp()).by == "b"


def test_an_unverifiable_stamp_is_overwritten_not_respected() -> None:
    """Otherwise one write of un-decodable bytes blocks every genuine stamp from then on."""
    store = MemoryStore()
    store.poison_stamp(b'{"verified_at_ms":99999999999999,"sig":"forged"}')

    assert store.put_stamp(_stamp_at(1_000.0)) is True

    assert decode_stamp(SECRET, store.read_stamp()).verified_at == 1_000.0


def test_a_flush_takes_the_stamp_with_it() -> None:
    """A flushed store has no stamp, so gateways age out rather than trusting a stale floor."""
    store = MemoryStore()
    store.put_stamp(_stamp_at(1_000.0))

    store.flush()

    assert store.read_stamp() is None


def test_losing_the_race_is_reported_without_being_an_error() -> None:
    writer, db, store, _unused = _lab()
    _seed(writer)
    store.put_stamp(_stamp_at(9_999.0, by="faster-peer"))
    rehydrator = Rehydrator(
        db, store, writer, SECRET, stale_grace_s=0.0, clock=lambda: 1_000.0, name=NAME,
    )

    summary = rehydrator.round_once()

    assert summary.ok, "losing the race is the healthy two-re-hydrator outcome"
    assert summary.stamped is not None, "the round still minted one"
    assert summary.stamp_race_lost is True
    assert decode_stamp(SECRET, store.read_stamp()).by == "faster-peer"


def test_a_broken_publisher_reports_the_stamp_write_as_a_failure() -> None:
    with pytest.raises(ConnectionError):
        BrokenPublisher().put_stamp(_stamp_at(1_000.0))
    with pytest.raises(ConnectionError):
        BrokenPublisher().read_stamp()


def test_a_stamp_the_store_rejects_is_never_mistaken_for_written() -> None:
    store = MemoryStore()
    newer = _stamp_at(5_000.0, by="peer")
    store.put_stamp(newer)
    older = _stamp_at(4_000.0)

    assert store.put_stamp(older) is False

    held = decode_stamp(SECRET, store.read_stamp())
    assert held == newer
    with pytest.raises(StoreDataUnavailable):
        decode_stamp(b"another-secret", store.read_stamp())


def _always_fails(kind: StateKind) -> StoredHead:
    del kind
    raise ConnectionError("injected store failure")


class _StampRefusingStore(MemoryStore):
    """Healthy for everything except the stamp slot."""

    def put_stamp(self, stamp: Stamp) -> bool:
        del stamp
        raise ConnectionError("injected stamp failure")


# --- the same contract over the real command set -------------------------------------------------
#
# Everything above drives the in-memory twin, whose `put_stamp` guard is a mutex. The Valkey
# adapter's guard is WATCH/MULTI/EXEC, which is a different mechanism, and it is the one that
# actually runs with two re-hydrators in two zones. So the guard is re-asserted here against a
# real Redis command implementation rather than trusted by analogy.


VALKEY_KEYS = StoreKeys(namespace="{t}")


def _valkey_publisher() -> tuple[fakeredis.FakeStrictRedis, ValkeyPublisher]:
    client = fakeredis.FakeStrictRedis()
    return client, ValkeyPublisher(client, SECRET, VALKEY_KEYS)


def _valkey_lab() -> tuple[ValkeyPublisher, StateWriter, MemoryControlDB, Rehydrator]:
    _client, publisher = _valkey_publisher()
    db = MemoryControlDB()
    writer = StateWriter(db, publisher, SECRET)
    rehydrator = Rehydrator(
        db, publisher, writer, SECRET, stale_grace_s=0.0, clock=lambda: 1_000.0, name=NAME,
    )
    return publisher, writer, db, rehydrator


def test_the_stamp_round_trips_through_a_real_store() -> None:
    publisher, writer, db, rehydrator = _valkey_lab()
    _seed(writer)

    minted = rehydrator.round_once().stamped

    assert minted is not None
    assert decode_stamp(SECRET, publisher.read_stamp()) == minted
    for kind in StateKind:
        counters = db.counters(kind)
        assert minted.cursors[kind] == Cursor(counters.version, counters.feed_seq)


def test_the_watch_guard_refuses_to_move_the_clock_backwards() -> None:
    publisher, _writer, _db, _rehydrator = _valkey_lab()
    newer = _stamp_at(2_000.0, by="zone-b")
    assert publisher.put_stamp(newer) is True

    assert publisher.put_stamp(_stamp_at(1_000.0, by="zone-a")) is False

    assert decode_stamp(SECRET, publisher.read_stamp()) == newer


def test_a_newer_round_replaces_the_held_stamp_over_a_real_store() -> None:
    publisher, _writer, _db, _rehydrator = _valkey_lab()
    publisher.put_stamp(_stamp_at(1_000.0, by="zone-a"))

    assert publisher.put_stamp(_stamp_at(3_000.0, by="zone-b")) is True

    assert decode_stamp(SECRET, publisher.read_stamp()).by == "zone-b"


def test_a_forged_stamp_never_blocks_a_real_stamp() -> None:
    """The mirror of the manifest rule: an unverifiable value must not become a permanent block."""
    client, publisher = _valkey_publisher()
    client.set(VALKEY_KEYS.stamp, b'{"verified_at_ms":99999999999999,"by":"x","sig":"forged"}')

    assert publisher.put_stamp(_stamp_at(1_000.0)) is True

    assert decode_stamp(SECRET, publisher.read_stamp()).verified_at == 1_000.0


def test_a_stamp_signed_under_another_secret_does_not_block_either() -> None:
    """A rotated or wrong key leaves a stamp that verifies for nobody; it must not wedge us."""
    client, publisher = _valkey_publisher()
    cursors = {kind: Cursor(Version(1, 1), 1) for kind in StateKind}
    foreign = encode_stamp(make_stamp(b"another-secret", 9_000.0, cursors, "x"))
    client.set(VALKEY_KEYS.stamp, foreign)

    assert publisher.put_stamp(_stamp_at(1_000.0)) is True

    assert decode_stamp(SECRET, publisher.read_stamp()).verified_at == 1_000.0


def test_an_empty_stamp_slot_reads_as_none_not_as_an_error() -> None:
    publisher, _writer, _db, _rehydrator = _valkey_lab()

    assert publisher.read_stamp() is None


def test_the_stamp_shares_the_namespace_hash_slot() -> None:
    """One MULTI with a manifest read must stay in one slot on a cluster-mode store."""
    assert VALKEY_KEYS.stamp == "{t}:stamp"
    assert VALKEY_KEYS.stamp.startswith("{t}")
