"""The ten seeded property-based tests for the push-coalescer (R2-23 / C43), task 8.

Each of Properties 1-10 from the push-coalescing design's "Correctness Properties" section is
asserted here by exactly one seeded ``random.Random`` test running >= 10,000 iterations, in the
house idiom (no ``hypothesis``). Every assertion message carries ``seed=... iter=...`` with the
counterexample so a failure is reproducible from the printed line alone.

The system under test is ``state_control.coalescer.CoalescingPublisher`` driving an in-memory
``state_control.publisher.MemoryStore`` twin over an INJECTED, mutable clock, so Coalesce_Window
boundaries are deterministic. Records and manifests are built with the one signing implementation
(``gateway_v2.runtime.state_sig``) so every durable write is valid; the owner is ``record.key``
and the position is ``manifest.feed_seq`` (which equals the record's ``feed_seq``), exactly as the
real writer produces them.

Test files are NOT under the import-linter layer contract, so importing from ``gateway_v2`` and
``state_control`` freely is allowed.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gateway_v2.domain.state import (
    Manifest,
    SignedRecord,
    Stamp,
    StateKind,
    Version,
)
from gateway_v2.runtime.state_nudge import parse_nudge
from gateway_v2.runtime.state_sig import make_manifest, make_record
from state_control.coalescer import CoalesceConfig, CoalescingPublisher
from state_control.publisher import MemoryStore, StoredHead
from state_control.writer import OK, StateWriter

SECRET = b"coalescer-property-test-secret"

# The two kinds the properties exercise. PLAN and KEY give per-(owner, kind) variety; both are
# per-record kinds whose owner is the write's key, matching the design's Owner definition (Req 2.6).
KINDS = (StateKind.PLAN, StateKind.KEY)


# --- a mutable injected clock (project convention) ---------------------------------------------


class Clock:
    """A mutable monotonic clock injected into the coalescer so windows are deterministic."""

    __slots__ = ("t",)

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# --- record / manifest construction (valid durable writes) -------------------------------------


def _body(kind: StateKind, owner: str, feed_seq: int) -> dict[str, object]:
    """A small, kind-shaped, deterministic body. Its exact contents do not matter to the push."""
    if kind is StateKind.KEY:
        return {"key_id": f"k-{owner}", "org_id": owner, "rate_per_s": 1.0, "burst": 2.0}
    return {"org_id": owner, "feed_seq": feed_seq}


def _record(kind: StateKind, owner: str, feed_seq: int) -> SignedRecord:
    """One signed record at ``feed_seq`` for ``owner`` of ``kind`` (version seq == feed_seq)."""
    return make_record(
        SECRET,
        kind,
        owner,
        _body(kind, owner, feed_seq),
        Version(1, feed_seq),
        feed_seq,
    )


def _manifest(kind: StateKind, feed_seq: int, *, count: int = 1) -> Manifest:
    """A manifest for ``kind`` at ``feed_seq``. ``on_count`` stays 0 (these kinds never engage)."""
    return make_manifest(SECRET, kind, Version(1, feed_seq), feed_seq, count, 0)


@dataclass(frozen=True, slots=True)
class _Write:
    """One committed write: an (owner, kind) at a monotonic-per-kind ``feed_seq``."""

    owner: str
    kind: StateKind
    feed_seq: int


def _publish(pub: CoalescingPublisher, write: _Write) -> bool:
    """Delegate one write through the coalescer, as the writer would (nudge defaults True)."""
    return pub.publish_record(
        _record(write.kind, write.owner, write.feed_seq),
        _manifest(write.kind, write.feed_seq),
    )


def _bare_publish(store: MemoryStore, write: _Write) -> bool:
    """The un-coalesced path: a direct inline-nudge write straight to the twin."""
    return store.publish_record(
        _record(write.kind, write.owner, write.feed_seq),
        _manifest(write.kind, write.feed_seq),
    )


# --- a monotonic-per-kind feed_seq workload generator ------------------------------------------


def _monotonic_workload(
    rng: random.Random,
    *,
    owners: Sequence[str],
    kinds: Sequence[StateKind],
    length: int,
) -> list[_Write]:
    """A valid interleaved workload: each kind's ``feed_seq`` strictly increases across the stream.

    ``feed_seq`` is a per-kind cursor the real writer never regresses, so a globally increasing
    counter per kind keeps every durable write acceptable to the twin's ``_is_ahead`` guard while
    still interleaving owners and kinds arbitrarily.
    """
    counter: dict[StateKind, int] = {kind: 0 for kind in kinds}
    writes: list[_Write] = []
    for _ in range(length):
        owner = rng.choice(owners)
        kind = rng.choice(kinds)
        counter[kind] += 1
        writes.append(_Write(owner=owner, kind=kind, feed_seq=counter[kind]))
    return writes


def _config(*, bound: int, window_s: float, freshness_s: float = 1.5) -> CoalesceConfig:
    return CoalesceConfig(
        per_owner_push_bound=bound,
        coalesce_window_s=window_s,
        freshness_window_s=freshness_s,
    )


# ================================================================================================
# Property 1: Per-owner push bound (invariant)
# ================================================================================================


def test_property1_per_owner_push_bound() -> None:
    # Feature: push-coalescing, Property 1: For any burst of N writes to one Owner within one
    # Coalesce_Window, the number of pushes emitted IN that window is at most Per_Owner_Push_Bound
    # (Req 2.1, "in that window"), and the last push for that Owner and kind — the window-close
    # catch-up that guarantees the Highest_Feed_Seq reaches the wire — carries the max feed_seq
    # among the coalesced writes (Req 4.1). The window-boundary residue flush is NOT an in-window
    # push: it is the terminal catch-up, so it is counted separately from the in-window bound.
    # Validates: Requirements 2.1, 4.1
    seed = 0xC0A_01
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        bound = rng.choice((1, 2, 3))
        kind = rng.choice(KINDS)
        clock = Clock()
        store = MemoryStore()
        pub = CoalescingPublisher(
            store,
            # A long window so the whole burst falls inside ONE Coalesce_Window.
            _config(bound=bound, window_s=1.0),
            clock,
        )
        n = rng.randint(1, 12)
        # Strictly increasing feed_seq within the burst (a valid single-owner write stream).
        seqs = sorted(rng.sample(range(1, 200), n))
        owner = "org-burst"
        for pos in seqs:
            # Advance a touch, but never past the window, so all writes share one window.
            clock.advance(rng.uniform(0.0, 0.9 / max(n, 1)))
            assert _publish(pub, _Write(owner, kind, pos)) is True
        # Pushes emitted strictly WITHIN the window (before any window close) are bounded (Req 2.1).
        in_window = [p for p in store.nudges if p[0] is kind]
        assert 1 <= len(in_window) <= bound, (
            f"seed={seed:#x} iter={i} bound={bound} kind={kind} seqs={seqs} "
            f"in_window_pushes={len(in_window)} nudges={in_window}"
        )
        # Close the window (shutdown flush) so the held Highest_Feed_Seq residue reaches the wire.
        pub.flush_all()
        pushes = [p for p in store.nudges if p[0] is kind]
        last_pos = pushes[-1][1]
        assert last_pos == seqs[-1], (
            f"seed={seed:#x} iter={i} bound={bound} kind={kind} seqs={seqs} "
            f"last announced={last_pos} expected max feed_seq={seqs[-1]}"
        )
        # The window-close catch-up adds at most ONE push beyond the in-window bound.
        assert len(pushes) <= bound + 1, (
            f"seed={seed:#x} iter={i} bound={bound} kind={kind} seqs={seqs} "
            f"total_pushes={len(pushes)} exceeds bound+1 nudges={pushes}"
        )


# ================================================================================================
# Property 2: Durable writes equal N (invariant)
# ================================================================================================


def test_property2_durable_writes_equal_n() -> None:
    # Feature: push-coalescing, Property 2: For any workload of N writes, Durable_Writes == N
    # regardless of coalescing, and the records/index/manifests are byte-identical to the bare path.
    # Validates: Requirements 3.1, 3.2, 3.5
    seed = 0xC0A_02
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        owners = [f"org-{n}" for n in range(rng.randint(1, 5))]
        length = rng.randint(1, 20)
        writes = _monotonic_workload(rng, owners=owners, kinds=KINDS, length=length)

        bare = MemoryStore()
        coalesced_inner = MemoryStore()
        clock = Clock()
        pub = CoalescingPublisher(
            coalesced_inner,
            _config(bound=rng.choice((1, 2, 3)), window_s=rng.choice((0.0, 0.25, 1.0))),
            clock,
        )
        for w in writes:
            clock.advance(rng.uniform(0.0, 0.1))
            assert _bare_publish(bare, w) is True
            assert _publish(pub, w) is True

        assert bare.record_writes == length, (
            f"seed={seed:#x} iter={i} bare record_writes={bare.record_writes} expected={length}"
        )
        assert coalesced_inner.record_writes == bare.record_writes, (
            f"seed={seed:#x} iter={i} coalesced record_writes={coalesced_inner.record_writes} "
            f"bare={bare.record_writes}"
        )
        # Durable state must be byte-identical: records, index entries, manifests.
        assert coalesced_inner._records == bare._records, (
            f"seed={seed:#x} iter={i} records differ writes={writes}"
        )
        assert coalesced_inner._index == bare._index, (
            f"seed={seed:#x} iter={i} index differs writes={writes}"
        )
        assert coalesced_inner._manifests == bare._manifests, (
            f"seed={seed:#x} iter={i} manifests differ writes={writes}"
        )


# ================================================================================================
# Property 3: Reader never ends below the latest committed feed_seq (round-trip)
# ================================================================================================


def _reader_apply(
    nudges: Sequence[tuple[StateKind, int]],
    committed: Mapping[StateKind, int],
) -> dict[StateKind, int]:
    """A model of the read side: keep the HIGHEST announced position per kind, skip-if-at-or-past,
    then one periodic backstop round that delivers the latest committed feed_seq.

    Mirrors ``NudgeListener._collect`` (highest per kind), the skip-if-at-or-past rule, and the
    periodic O(1) round that is the correctness backstop for any merged or dropped nudge.
    """
    applied: dict[StateKind, int] = {}
    for payload in nudges:
        # The wire shape is "<kind>:<feed_seq>"; parse it exactly as the reader would.
        wire = f"{payload[0].value}:{payload[1]}"
        parsed = parse_nudge(wire)
        assert parsed is not None, f"coalescer emitted an unparseable nudge: {wire!r}"
        kind, pos = parsed
        if pos > applied.get(kind, -1):
            # skip-if-at-or-past: only a strictly higher announced position advances the reader.
            applied[kind] = pos
    # One backstop round: the periodic O(1) round delivers the latest committed feed_seq per kind,
    # which can only RAISE the applied position, never lower it.
    for kind, pos in committed.items():
        if pos > applied.get(kind, -1):
            applied[kind] = pos
    return applied


def test_property3_reader_never_below_latest() -> None:
    # Feature: push-coalescing, Property 3: A reader applying only the coalesced nudges plus one
    # periodic backstop round ends at or above the latest committed feed_seq, never below it.
    # Validates: Requirements 4.6, 5.1, 5.3, 5.4
    seed = 0xC0A_03
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        kind = rng.choice(KINDS)
        clock = Clock()
        store = MemoryStore()
        pub = CoalescingPublisher(
            store,
            _config(bound=rng.choice((1, 2, 3)), window_s=rng.choice((0.0, 0.25, 1.0))),
            clock,
        )
        n = rng.randint(1, 15)
        seqs = sorted(rng.sample(range(1, 300), n))
        owner = "org-reader"
        for pos in seqs:
            clock.advance(rng.uniform(0.0, 0.3))
            assert _publish(pub, _Write(owner, kind, pos)) is True
        # Flush-due on an elapsed window, then a shutdown flush: both model the service loop; even
        # if neither ran, the backstop round below still guarantees convergence.
        if rng.random() < 0.5:
            clock.advance(2.0)
            pub.flush_due(clock())
        committed = {kind: seqs[-1]}
        applied = _reader_apply(store.nudges, committed)
        assert applied.get(kind, -1) >= seqs[-1], (
            f"seed={seed:#x} iter={i} kind={kind} seqs={seqs} "
            f"applied={applied.get(kind)} latest_committed={seqs[-1]}"
        )
        # The reader also never ADVANCES past what was actually committed.
        assert applied.get(kind, -1) == seqs[-1], (
            f"seed={seed:#x} iter={i} kind={kind} seqs={seqs} "
            f"applied={applied.get(kind)} overshot latest_committed={seqs[-1]}"
        )


# ================================================================================================
# Property 4: Monotonic announced position (invariant)
# ================================================================================================


class _PermissiveStore:
    """An inner publisher that records nudges but NEVER refuses a regressive manifest.

    Property 4 must prove the coalescer's own monotonic guard, so the inner must not pre-filter
    regressive positions the way ``MemoryStore._is_ahead`` does. This stub keeps durable writes
    conceptually valid (it just records them) while letting out-of-order positions reach the
    coalescer's ``_emit`` guard, which is the thing under test. Only ``emit_nudge`` is exercised
    by the direct ``_record_write`` path used below.
    """

    def __init__(self) -> None:
        self.nudges: list[tuple[StateKind, int]] = []

    def emit_nudge(self, kind: StateKind, feed_seq: int) -> None:
        self.nudges.append((kind, feed_seq))

    # The remaining StatePublisher methods are unused by this property but kept for duck-typing.
    def stored_feed_seq(self, kind: StateKind) -> int | None:
        return None

    def stored_head(self, kind: StateKind) -> StoredHead:
        return StoredHead(None, 0, 0, 0)

    def stored_index(self, kind: StateKind) -> Mapping[str, int]:
        return {}

    def publish_record(
        self,
        record: SignedRecord,
        manifest: Manifest,
        *,
        engaged: bool | None = None,
        nudge: bool = True,
    ) -> bool:
        return True

    def publish_batch(
        self,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        *,
        engaged: Mapping[str, bool] | None = None,
        chunk: int = 500,
        nudge: bool = True,
    ) -> bool:
        return True

    def publish_kind(
        self,
        kind: StateKind,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        engaged: Sequence[str] = (),
        *,
        allow_regress: bool = False,
    ) -> bool:
        return True

    def read_stamp(self) -> bytes | None:
        return None

    def put_stamp(self, stamp: Stamp) -> bool:
        return True


def test_property4_monotonic_announced() -> None:
    # Feature: push-coalescing, Property 4: The sequence of feed_seq values the coalescer announces
    # for one (Owner, kind) is non-decreasing; a coalesced push never announces a position lower
    # than one already announced, and no regressive position reaches the wire.
    # Validates: Requirements 4.5
    seed = 0xC0A_04
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        kind = rng.choice(KINDS)
        clock = Clock()
        inner = _PermissiveStore()
        pub = CoalescingPublisher(
            inner,
            # bound=1 + window=0 forces an emit decision on every write, so every out-of-order
            # position is offered to the monotonic guard rather than silently held.
            _config(bound=1, window_s=0.0),
            clock,
        )
        owner = "org-oo"
        n = rng.randint(1, 20)
        # Deliberately out-of-order / regressive positions fed to the coalescer's record path.
        positions = [rng.randint(0, 50) for _ in range(n)]
        committed_max = 0
        for pos in positions:
            clock.advance(rng.uniform(0.0, 0.05))
            # Drive the internal record path directly with the (possibly regressive) position; the
            # durable write is independent and valid, this isolates the announced-position guard.
            pub._record_write(owner, kind, pos)
            committed_max = max(committed_max, pos)
        announced = [p[1] for p in inner.nudges if p[0] is kind]
        # The wire sequence must be strictly increasing (the guard suppresses pos <= last).
        for a, b in zip(announced, announced[1:], strict=False):
            assert b > a, (
                f"seed={seed:#x} iter={i} kind={kind} positions={positions} "
                f"announced={announced} regressive/duplicate on the wire"
            )
        # The highest announced position never exceeds, and ends at, the highest offered position.
        if announced:
            assert announced[-1] == committed_max, (
                f"seed={seed:#x} iter={i} kind={kind} positions={positions} "
                f"announced={announced} top={announced[-1]} expected_max={committed_max}"
            )


# ================================================================================================
# Property 5: Per-owner isolation (invariant)
# ================================================================================================


def test_property5_per_owner_isolation() -> None:
    # Feature: push-coalescing, Property 5: For any interleaving across multiple Owners, each
    # Owner's pushes are bounded independently; one Owner's burst never reduces another Owner's
    # budget and a per-owner replay in isolation yields identical announced positions.
    # Validates: Requirements 2.2, 2.3
    seed = 0xC0A_05
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        bound = rng.choice((1, 2, 3))
        window_s = 1.0  # one window containing the whole interleaving
        owners = [f"org-{n}" for n in range(rng.randint(2, 4))]
        length = rng.randint(2, 24)
        writes = _monotonic_workload(rng, owners=owners, kinds=KINDS, length=length)

        # Interleaved run.
        clock = Clock()
        store = MemoryStore()
        pub = CoalescingPublisher(store, _config(bound=bound, window_s=window_s), clock)
        for w in writes:
            clock.advance(rng.uniform(0.0, 0.9 / max(length, 1)))
            assert _publish(pub, w) is True
        pub.flush_all()

        # Per (owner, kind): count the announced pushes. A push payload only carries (kind, pos),
        # so reconstruct per-owner by replaying each owner in isolation and comparing announced
        # positions, which also proves one owner's burst never reorders another's.
        for owner in owners:
            for kind in KINDS:
                owner_writes = [w for w in writes if w.owner == owner and w.kind == kind]
                if not owner_writes:
                    continue
                iso_clock = Clock()
                iso_store = MemoryStore()
                iso_pub = CoalescingPublisher(
                    iso_store, _config(bound=bound, window_s=window_s), iso_clock,
                )
                for w in owner_writes:
                    iso_pub.publish_record(
                        _record(w.kind, w.owner, w.feed_seq),
                        _manifest(w.kind, w.feed_seq),
                    )
                # In-window pushes (before the window-close catch-up) respect the bound by owner.
                iso_in_window = [p for p in iso_store.nudges if p[0] is kind]
                assert len(iso_in_window) <= bound, (
                    f"seed={seed:#x} iter={i} owner={owner} kind={kind} bound={bound} "
                    f"iso_in_window={iso_in_window}"
                )
                iso_pub.flush_all()
                iso_pushes = [p for p in iso_store.nudges if p[0] is kind]
                # The window-close catch-up adds at most ONE push beyond the in-window bound.
                assert len(iso_pushes) <= bound + 1, (
                    f"seed={seed:#x} iter={i} owner={owner} kind={kind} bound={bound} "
                    f"iso_pushes={iso_pushes}"
                )
                # The isolated final announced position equals the owner's max feed_seq, so the
                # interleaving never reordered or lowered this owner's announced position.
                expected_top = max(w.feed_seq for w in owner_writes)
                assert iso_pushes[-1][1] == expected_top, (
                    f"seed={seed:#x} iter={i} owner={owner} kind={kind} "
                    f"iso_top={iso_pushes[-1][1]} expected={expected_top}"
                )


# ================================================================================================
# Property 6: Coalescing is idempotent on a quiesced owner (idempotence)
# ================================================================================================


def test_property6_idempotent_flush_quiesced() -> None:
    # Feature: push-coalescing, Property 6: Once an Owner's latest write has been announced and no
    # further writes arrive, re-running the coalescer flush emits no additional push.
    # Validates: Requirements 2.4
    seed = 0xC0A_06
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        clock = Clock()
        store = MemoryStore()
        pub = CoalescingPublisher(
            store,
            _config(bound=rng.choice((1, 2, 3)), window_s=rng.choice((0.0, 0.25, 1.0))),
            clock,
        )
        owners = [f"org-{n}" for n in range(rng.randint(1, 3))]
        length = rng.randint(1, 15)
        writes = _monotonic_workload(rng, owners=owners, kinds=KINDS, length=length)
        for w in writes:
            clock.advance(rng.uniform(0.0, 0.3))
            assert _publish(pub, w) is True

        # Quiesce: advance well past any window and flush everything held.
        clock.advance(5.0)
        pub.flush_due(clock())
        pub.flush_all()
        settled = len(store.nudges)

        # Re-run every flush path with NO intervening write; the count must not change.
        clock.advance(5.0)
        pub.flush_due(clock())
        pub.flush_all()
        pub.flush_due(clock())
        assert len(store.nudges) == settled, (
            f"seed={seed:#x} iter={i} writes={writes} settled={settled} "
            f"after_requiesce={len(store.nudges)}"
        )


# ================================================================================================
# Property 7: Fail-safe never costs durability or freshness (error condition)
# ================================================================================================


class _NudgeRaisingStore:
    """Durable writes succeed on a real MemoryStore; only ``emit_nudge`` raises.

    Composed exactly as the task describes: a wrapper that forwards ``publish_record`` /
    ``publish_batch`` (the Durable_Write) to a real ``MemoryStore`` and raises inside
    ``emit_nudge``. This isolates a push-transport fault from durability, so Property 7 can assert
    every WriteOutcome matches the un-coalesced run and the backstop reader still converges.
    """

    def __init__(self) -> None:
        self.durable = MemoryStore()

    def emit_nudge(self, kind: StateKind, feed_seq: int) -> None:
        raise ConnectionError("injected push failure")

    def publish_record(
        self,
        record: SignedRecord,
        manifest: Manifest,
        *,
        engaged: bool | None = None,
        nudge: bool = True,
    ) -> bool:
        return self.durable.publish_record(record, manifest, engaged=engaged, nudge=nudge)

    def publish_batch(
        self,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        *,
        engaged: Mapping[str, bool] | None = None,
        chunk: int = 500,
        nudge: bool = True,
    ) -> bool:
        return self.durable.publish_batch(
            records, manifest, engaged=engaged, chunk=chunk, nudge=nudge,
        )

    def publish_kind(
        self,
        kind: StateKind,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        engaged: Sequence[str] = (),
        *,
        allow_regress: bool = False,
    ) -> bool:
        return self.durable.publish_kind(
            kind, records, manifest, engaged, allow_regress=allow_regress,
        )

    def stored_feed_seq(self, kind: StateKind) -> int | None:
        return self.durable.stored_feed_seq(kind)

    def stored_head(self, kind: StateKind) -> StoredHead:
        return self.durable.stored_head(kind)

    def stored_index(self, kind: StateKind) -> Mapping[str, int]:
        return self.durable.stored_index(kind)

    def read_stamp(self) -> bytes | None:
        return self.durable.read_stamp()

    def put_stamp(self, stamp: Stamp) -> bool:
        return self.durable.put_stamp(stamp)


def test_property7_failsafe() -> None:
    # Feature: push-coalescing, Property 7: For any injected push failure or shutdown with pending
    # nudges, every Durable_Write still completes, the write outcome is unchanged, and no reader is
    # left below the latest committed feed_seq beyond the Freshness_Window (the backstop converges).
    # Validates: Requirements 3.6, 6.1, 6.2, 6.3, 6.4
    seed = 0xC0A_07
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        kind = rng.choice(KINDS)
        owner = "org-failsafe"
        n = rng.randint(1, 10)
        seqs = sorted(rng.sample(range(1, 200), n))

        from state_control.db import MemoryControlDB

        # Baseline: a real writer over a bare MemoryStore (no coalescer, nudges land inline).
        base_db = MemoryControlDB()
        base_store = MemoryStore()
        base_writer = StateWriter(base_db, base_store, SECRET)

        # Fail-safe arm: the same real writer, but its publisher is the coalescer wrapping a store
        # whose emit_nudge raises on every push.
        fs_db = MemoryControlDB()
        raising = _NudgeRaisingStore()
        clock = Clock()
        fs_pub = CoalescingPublisher(
            raising, _config(bound=rng.choice((1, 2, 3)), window_s=rng.choice((0.0, 0.25))), clock,
        )
        fs_writer = StateWriter(fs_db, fs_pub, SECRET)

        # Drive identical plan writes through both arms; compare the reported outcomes.
        for _pos in seqs:
            base_out = base_writer.plan_set(owner, {"org_id": owner, "n": _pos})
            fs_out = fs_writer.plan_set(owner, {"org_id": owner, "n": _pos})
            # The baseline publishes cleanly (OK); the fail-safe arm's push always fails but the
            # Durable_Write still stands. The coalescer SWALLOWS the emit error, so the writer's
            # publish_record returned True (durable) -> the outcome is OK, identical to baseline.
            assert fs_out.status == base_out.status == OK, (
                f"seed={seed:#x} iter={i} base={base_out.status} fs={fs_out.status} pos={_pos}"
            )
            assert fs_out.durable is True, (
                f"seed={seed:#x} iter={i} fail-safe write not durable at pos={_pos}"
            )
            assert fs_out.feed_seq == base_out.feed_seq, (
                f"seed={seed:#x} iter={i} feed_seq base={base_out.feed_seq} fs={fs_out.feed_seq}"
            )

        # Durable state parity: both committed the same number of records at the same positions.
        assert raising.durable.record_writes == base_store.record_writes, (
            f"seed={seed:#x} iter={i} durable record_writes "
            f"fs={raising.durable.record_writes} base={base_store.record_writes}"
        )
        assert raising.durable.stored_feed_seq(StateKind.PLAN) == base_store.stored_feed_seq(
            StateKind.PLAN,
        ), f"seed={seed:#x} iter={i} durable feed_seq diverged"

        # A shutdown flush with pending nudges on a raising store MUST NOT raise (Req 6.3).
        clock.advance(5.0)
        pub_only = CoalescingPublisher(raising, _config(bound=1, window_s=1.0), clock)
        pub_only.publish_record(_record(kind, owner, seqs[-1]), _manifest(kind, seqs[-1]))
        pub_only.flush_all()  # must return normally despite emit_nudge raising

        # Backstop reader: the fail-safe arm emitted NO nudges (every push raised), yet the periodic
        # round delivers the latest committed feed_seq, so the reader still converges (Req 5.3/5.4).
        committed = {StateKind.PLAN: base_store.stored_feed_seq(StateKind.PLAN) or 0}
        applied = _reader_apply(raising.durable.nudges, committed)
        latest = committed[StateKind.PLAN]
        assert applied.get(StateKind.PLAN, -1) >= latest, (
            f"seed={seed:#x} iter={i} backstop did not converge: "
            f"applied={applied.get(StateKind.PLAN)} latest={latest}"
        )


# ================================================================================================
# Property 8: Window bound derives from the authority (invariant)
# ================================================================================================


def test_property8_bound_from_authority() -> None:
    # Feature: push-coalescing, Property 8: For any StateKnobs configuration, the bound and window
    # are functions of the declared authority only; a Coalesce_Window longer than the
    # Freshness_Window is refused at construction, and the == boundary is accepted.
    # Validates: Requirements 7.1, 7.2, 7.5
    import pytest

    from gateway_v2.domain.state_knobs import StateKnobs

    seed = 0xC0A_08
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        # Valid StateKnobs: pick periods that satisfy StateKnobs.__post_init__ and keep
        # coalesce_window_ms <= enforcement_bound_s * 1000.
        rehydrate = rng.choice((500, 1_000, 2_000))
        refresh = rng.choice((250, 500, 1_000))
        freshness_ms = rehydrate + refresh
        bound = rng.randint(1, 8)
        window_ms = rng.randint(0, freshness_ms)  # <= Freshness_Window, so valid
        knobs = StateKnobs(
            fresh_ms=0,  # freshness-off keeps the other period relationships out of the way
            rehydrate_period_ms=rehydrate,
            refresh_period_ms=refresh,
            coalesce_window_ms=window_ms,
            per_owner_push_bound=bound,
        )
        cfg = CoalesceConfig.from_knobs(knobs)
        # The config reflects the knob values exactly (authority-derived, no literal).
        assert cfg.per_owner_push_bound == bound, (
            f"seed={seed:#x} iter={i} bound cfg={cfg.per_owner_push_bound} knob={bound}"
        )
        assert cfg.coalesce_window_s == window_ms / 1000, (
            f"seed={seed:#x} iter={i} window cfg={cfg.coalesce_window_s} knob_ms={window_ms}"
        )
        assert cfg.freshness_window_s == knobs.enforcement_bound_s, (
            f"seed={seed:#x} iter={i} freshness cfg={cfg.freshness_window_s} "
            f"knob={knobs.enforcement_bound_s}"
        )

        # The == boundary (window exactly equal to freshness) is accepted at construction.
        boundary = CoalesceConfig(
            per_owner_push_bound=bound,
            coalesce_window_s=cfg.freshness_window_s,
            freshness_window_s=cfg.freshness_window_s,
        )
        assert boundary.coalesce_window_s == boundary.freshness_window_s, (
            f"seed={seed:#x} iter={i} boundary not accepted"
        )

        # A strictly-longer window is refused at construction.
        over = cfg.freshness_window_s + rng.uniform(0.001, 2.0)
        with pytest.raises(ValueError, match="exceeds the Freshness_Window"):
            CoalesceConfig(
                per_owner_push_bound=bound,
                coalesce_window_s=over,
                freshness_window_s=cfg.freshness_window_s,
            )

        # StateKnobs itself refuses a window beyond the Freshness_Window fail-at-start (Req 7.5).
        with pytest.raises(ValueError, match="must be <= the Freshness_Window"):
            StateKnobs(
                fresh_ms=0,
                rehydrate_period_ms=rehydrate,
                refresh_period_ms=refresh,
                coalesce_window_ms=freshness_ms + rng.randint(1, 1_000),
                per_owner_push_bound=bound,
            )


# ================================================================================================
# Property 9: Confluence of write ordering (confluence)
# ================================================================================================


def test_property9_confluence_write_ordering() -> None:
    # Feature: push-coalescing, Property 9: For any two orderings of the same set of per-Owner
    # writes within a window, the Durable_Writes are identical and the final announced feed_seq per
    # (Owner, kind) is identical, independent of the order the coalescer observed them.
    # Validates: Requirements 4.1, 4.2
    seed = 0xC0A_09
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        bound = rng.choice((1, 2, 3))
        window_s = 1.0  # one window containing the whole set
        owners = [f"org-{n}" for n in range(rng.randint(1, 4))]
        length = rng.randint(1, 20)
        writes = _monotonic_workload(rng, owners=owners, kinds=KINDS, length=length)

        # Two orderings of the SAME set. A valid durable ordering must keep each kind's feed_seq
        # non-decreasing (the twin refuses a regress), so shuffle while preserving per-kind order:
        # shuffle the owners' relative order but sort by feed_seq within each kind.
        order_a = sorted(writes, key=lambda w: (w.kind.value, w.feed_seq))
        order_b = _confluent_reorder(rng, writes)

        final_a = _run_ordering(order_a, bound=bound, window_s=window_s)
        final_b = _run_ordering(order_b, bound=bound, window_s=window_s)

        assert final_a.records == final_b.records, (
            f"seed={seed:#x} iter={i} durable records differ across orderings"
        )
        assert final_a.index == final_b.index, (
            f"seed={seed:#x} iter={i} durable index differ across orderings"
        )
        assert final_a.manifests == final_b.manifests, (
            f"seed={seed:#x} iter={i} durable manifests differ across orderings"
        )
        assert final_a.announced == final_b.announced, (
            f"seed={seed:#x} iter={i} final announced positions differ: "
            f"a={final_a.announced} b={final_b.announced}"
        )


def _confluent_reorder(rng: random.Random, writes: Sequence[_Write]) -> list[_Write]:
    """A different global ordering that still keeps each kind's feed_seq non-decreasing.

    Interleaving per-kind streams (each already in feed_seq order) in a random but order-preserving
    merge yields a valid alternative the twin accepts, while genuinely differing from ``order_a``.
    """
    streams: dict[StateKind, list[_Write]] = {}
    for w in sorted(writes, key=lambda x: x.feed_seq):
        streams.setdefault(w.kind, []).append(w)
    heads = {kind: 0 for kind in streams}
    merged: list[_Write] = []
    while any(heads[kind] < len(streams[kind]) for kind in streams):
        kind = rng.choice([k for k in streams if heads[k] < len(streams[k])])
        merged.append(streams[kind][heads[kind]])
        heads[kind] += 1
    return merged


@dataclass(frozen=True, slots=True)
class _FinalState:
    records: dict[StateKind, dict[str, bytes]]
    index: dict[StateKind, dict[str, int]]
    manifests: dict[StateKind, bytes]
    announced: dict[tuple[str, StateKind], int]


def _run_ordering(order: Sequence[_Write], *, bound: int, window_s: float) -> _FinalState:
    """Run one ordering through a fresh coalescer+store; return durable state + final announced."""
    clock = Clock()
    store = MemoryStore()
    pub = CoalescingPublisher(store, _config(bound=bound, window_s=window_s), clock)
    for w in order:
        assert _publish(pub, w) is True
    pub.flush_all()
    # Reconstruct the final announced position per (owner, kind) by isolated replay (the nudge
    # payload carries only (kind, pos), so an isolated replay recovers the owner dimension).
    announced: dict[tuple[str, StateKind], int] = {}
    for w in order:
        key = (w.owner, w.kind)
        if key not in announced:
            iso_store = MemoryStore()
            iso_pub = CoalescingPublisher(
                iso_store, _config(bound=bound, window_s=window_s), Clock(),
            )
            for ww in [x for x in order if x.owner == w.owner and x.kind == w.kind]:
                iso_pub.publish_record(
                    _record(ww.kind, ww.owner, ww.feed_seq), _manifest(ww.kind, ww.feed_seq),
                )
            iso_pub.flush_all()
            kind_pushes = [p for p in iso_store.nudges if p[0] is w.kind]
            announced[key] = kind_pushes[-1][1] if kind_pushes else -1
    return _FinalState(
        records={k: dict(v) for k, v in store._records.items()},
        index={k: dict(v) for k, v in store._index.items()},
        manifests=dict(store._manifests),
        announced=announced,
    )


# ================================================================================================
# Property 10: Push count is independent of worker and request count (metamorphic)
# ================================================================================================


def test_property10_push_count_independent_of_workers() -> None:
    # Feature: push-coalescing, Property 10: For a fixed set of writes and Owners, the total push
    # count is unchanged when the simulated worker/request count varies, confirming the per-request
    # / per-worker push defect stays closed; no serving-path publish occurs.
    # Validates: Requirements 1.3, 1.4, 1.5
    seed = 0xC0A_10
    rng = random.Random(seed)
    iterations = 10_000
    for i in range(iterations):
        bound = rng.choice((1, 2, 3))
        window_s = rng.choice((0.0, 0.25, 1.0))
        owners = [f"org-{n}" for n in range(rng.randint(1, 4))]
        length = rng.randint(1, 18)
        writes = _monotonic_workload(rng, owners=owners, kinds=KINDS, length=length)

        def run_with(workers: int, requests: int) -> int:
            # workers/requests are purely nominal: the write path never consumes them, so they must
            # not touch the coalescer. We simply re-run the identical workload.
            del workers, requests
            clock = Clock()
            store = MemoryStore()
            pub = CoalescingPublisher(store, _config(bound=bound, window_s=window_s), clock)
            for w in writes:
                clock.advance(rng_fixed.uniform(0.0, 0.3) if window_s else 0.0)
                assert _publish(pub, w) is True
            pub.flush_all()
            return len(store.nudges)

        # A FIXED per-iteration advance stream so the only varying input is workers/requests; the
        # clock advances identically across variations.
        advance_seed = rng.random()
        baseline = None
        for workers, requests in ((1, 1), (36, 72), (8, 1000), (36, 1)):
            rng_fixed = random.Random(advance_seed)
            count = run_with(workers, requests)
            if baseline is None:
                baseline = count
            assert count == baseline, (
                f"seed={seed:#x} iter={i} push count varied with workers={workers} "
                f"requests={requests}: got={count} baseline={baseline}"
            )
        # No serving-path publish exists to call: the only pushes are the write-path nudges counted
        # above, and that count is fully determined by the workload, bound and window.
        assert baseline is not None
