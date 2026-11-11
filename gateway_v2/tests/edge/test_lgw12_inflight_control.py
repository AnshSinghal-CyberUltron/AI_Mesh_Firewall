"""LGW12 task 10.2 — unit tests mapping each R10 in-flight trigger to a cut + Error_Frame.

# Feature: sse-egress-pipeline
# Validates: Requirements 10.2, 10.3, 10.4, 10.5, 10.6, 10.7

The C24 in-flight control (:class:`~gateway_v2.edge.cancel.InFlightControl`) maps every R10
trigger onto the ONE :class:`~gateway_v2.edge.cancel.KillLatch` cut seam. The handler renders the
latch's recorded reason code (NOT the pipeline's boolean ``killed()``) as the terminal SSE
``Error_Frame``, so each trigger must flip the shared latch with a DIFFERENT posture code (the
design's Error Handling table):

* max stream duration (R10.2) → ``STREAM_MAX_DURATION``
* kill switch engaged (R10.3) → ``STREAM_KILLED``
* key revoked (R10.4) → ``STREAM_KEY_REVOKED``
* plan changed (R10.5) → ``STREAM_PLAN_CHANGED``
* snapshot stale / unavailable (R10.6, fail closed) → ``STREAM_SNAPSHOT_STALE``

These are supporting EXAMPLE/unit tests (one per trigger), not a ≥10k property loop. Each test
builds a scripted :class:`~gateway_v2.edge.cancel.InFlightControlSource` double, a controllable
clock for the max-duration deadline, and a real :class:`~gateway_v2.runtime.resources.
ResourceContract` via ``from_signals`` (the LGW06/LGW12 contract-fixture idiom). No ``hypothesis``;
the evaluator is pure and synchronous, so no event loop.

The ``max_cut_latency_s()`` budget framing is asserted STRUCTURALLY and kept unit-focused on the
trigger → latch → reason mapping: a cut fired at ``evaluate()`` sets the one-way latch immediately
(``is_killed()`` true the instant the trigger fires), so the shipped ``StreamPipeline`` cuts at the
next chunk boundary and forwards no further upstream bytes (R10.7). The byte-level "no further
bytes forwarded after the cut" is a ``routes.py`` integration concern, out of scope here.
"""

from __future__ import annotations

import random

from gateway_v2.domain import posture
from gateway_v2.edge.cancel import InFlightControl, InFlightSnapshot, KillLatch
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

# --------------------------------------------------------------------------- #
# Test doubles: a scripted control source + a controllable clock.
# --------------------------------------------------------------------------- #


class _ScriptedSource:
    """An :class:`InFlightControlSource` double returning one scripted :class:`InFlightSnapshot`.

    Pure read, no clock, no I/O — the evaluator polls :meth:`snapshot` at each chunk boundary. The
    scripted snapshot is swappable so a test can present a fresh/clear snapshot first and a fired
    signal later, exercising the one-way latch and the fail-closed precedence.
    """

    __slots__ = ("snap",)

    def __init__(self, snap: InFlightSnapshot) -> None:
        self.snap = snap

    def snapshot(self) -> InFlightSnapshot:
        return self.snap


class _Clock:
    """A controllable monotonic-seconds clock: ``now`` is read on every ``()`` call.

    ``InFlightControl`` captures ``clock() + max_stream_duration_s()`` as the deadline at
    construction, then compares the LIVE ``clock()`` against it in ``evaluate()`` — so advancing
    ``now`` past the deadline between construction and ``evaluate`` fires the max-duration trigger
    deterministically.
    """

    __slots__ = ("now",)

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _contract(*, target_p99_ms: float = 20.0) -> ResourceContract:
    """A serviceable contract via ``from_signals`` (same shape as ``test_lgw12_resources.py``).

    The signals clear ``from_signals``' refuse-to-start checks on both the CPU and RAM worker
    paths, so ``max_stream_duration_s()`` / ``max_snapshot_age_s()`` / ``max_cut_latency_s()`` are
    all valid derivations of ``target_p99_ms``.
    """
    per_worker_rss = 400 * 1024 * 1024
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=per_worker_rss * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=target_p99_ms,
        utilization_cap=0.75,
        per_worker_rss=per_worker_rss,
    )


def _clear_snapshot(*, age_s: float = 0.0) -> InFlightSnapshot:
    """A fresh, available snapshot with every signal clear — the stream keeps running."""
    return InFlightSnapshot(
        available=True,
        age_s=age_s,
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=False,
    )


def _build(
    snap: InFlightSnapshot,
    *,
    clock: _Clock | None = None,
    contract: ResourceContract | None = None,
) -> tuple[InFlightControl, KillLatch, _Clock, ResourceContract]:
    """Build ``(control, latch, clock, contract)`` around a scripted snapshot."""
    clk = clock if clock is not None else _Clock()
    ctr = contract if contract is not None else _contract()
    latch = KillLatch()
    control = InFlightControl(
        source=_ScriptedSource(snap),
        contract=ctr,
        clock=clk,
        kill_latch=latch,
    )
    return control, latch, clk, ctr


# --------------------------------------------------------------------------- #
# One test per R10 trigger: correct cut + reason code.
# --------------------------------------------------------------------------- #


def test_max_stream_duration_cuts_with_stream_max_duration() -> None:
    """R10.2: clock past the derived deadline → ``STREAM_MAX_DURATION`` cut.

    The deadline is captured as ``clock() + max_stream_duration_s()`` at construction; advancing
    the live clock to the deadline fires the trigger. The latch is killed and carries the
    max-duration reason code — the code the handler renders as the terminal ``Error_Frame``.
    """
    clk = _Clock(start=0.0)
    contract = _contract()
    control, latch, _clk, _ctr = _build(_clear_snapshot(), clock=clk, contract=contract)

    # Not yet at the deadline: nothing fires, the stream keeps running.
    assert control.evaluate() is None
    assert not latch.is_killed()

    # Advance the clock to exactly the derived deadline (clock() >= deadline).
    clk.now = contract.max_stream_duration_s()

    assert control.evaluate() == posture.STREAM_MAX_DURATION
    assert latch.is_killed()
    assert latch.reason() == posture.STREAM_MAX_DURATION


def test_kill_switch_cuts_with_stream_killed() -> None:
    """R10.3: kill switch engaged → ``STREAM_KILLED`` cut."""
    snap = InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=True,
        key_revoked=False,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build(snap)

    assert control.evaluate() == posture.STREAM_KILLED
    assert latch.is_killed()
    assert latch.reason() == posture.STREAM_KILLED


def test_key_revoked_cuts_with_stream_key_revoked() -> None:
    """R10.4: key revoked mid-stream → ``STREAM_KEY_REVOKED`` cut."""
    snap = InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=False,
        key_revoked=True,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build(snap)

    assert control.evaluate() == posture.STREAM_KEY_REVOKED
    assert latch.is_killed()
    assert latch.reason() == posture.STREAM_KEY_REVOKED


def test_plan_changed_cuts_with_stream_plan_changed() -> None:
    """R10.5: plan snapshot changed mid-stream → ``STREAM_PLAN_CHANGED`` cut."""
    snap = InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=True,
    )
    control, latch, _clk, _ctr = _build(snap)

    assert control.evaluate() == posture.STREAM_PLAN_CHANGED
    assert latch.is_killed()
    assert latch.reason() == posture.STREAM_PLAN_CHANGED


def test_stale_snapshot_fails_closed_with_stream_snapshot_stale() -> None:
    """R10.6: ``age_s > max_snapshot_age_s()`` → fail-closed ``STREAM_SNAPSHOT_STALE`` cut.

    An available-but-stale snapshot must not be trusted even to report "all clear": the evaluator
    cuts rather than continue forwarding on an unverifiable snapshot.
    """
    contract = _contract()
    max_age = contract.max_snapshot_age_s()
    stale = InFlightSnapshot(
        available=True,
        age_s=max_age * 2.0,  # strictly older than the derived freshness bound
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build(stale, contract=contract)

    assert control.evaluate() == posture.STREAM_SNAPSHOT_STALE
    assert latch.is_killed()
    assert latch.reason() == posture.STREAM_SNAPSHOT_STALE


def test_fresh_snapshot_at_the_age_bound_does_not_fire() -> None:
    """R10.6 boundary: ``age_s == max_snapshot_age_s()`` is still fresh (strict ``>`` staleness).

    A snapshot exactly at the freshness bound is NOT stale (the evaluator uses ``age_s > bound``),
    so an otherwise-clear snapshot at the bound keeps the stream running.
    """
    contract = _contract()
    at_bound = _clear_snapshot(age_s=contract.max_snapshot_age_s())
    control, latch, _clk, _ctr = _build(at_bound, contract=contract)

    assert control.evaluate() is None
    assert not latch.is_killed()
    assert latch.reason() is None


def test_unavailable_snapshot_fails_closed_with_stream_snapshot_stale() -> None:
    """R10.6: ``available=False`` → fail-closed ``STREAM_SNAPSHOT_STALE`` cut (absence ≠ "off")."""
    unavailable = InFlightSnapshot(
        available=False,
        age_s=0.0,  # a young-but-unreadable snapshot still fails closed
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build(unavailable)

    assert control.evaluate() == posture.STREAM_SNAPSHOT_STALE
    assert latch.is_killed()
    assert latch.reason() == posture.STREAM_SNAPSHOT_STALE


# --------------------------------------------------------------------------- #
# Fail-closed precedence: an unverifiable snapshot dominates every other signal.
# --------------------------------------------------------------------------- #


def test_stale_snapshot_wins_over_a_concurrent_signal() -> None:
    """R10.6 precedence: stale snapshot + another fired signal → ``STREAM_SNAPSHOT_STALE`` wins.

    An unverifiable snapshot must not be trusted even to report the OTHER signal, so the
    fail-closed stale check dominates kill-switch / revocation / plan-change when both hold.
    """
    contract = _contract()
    max_age = contract.max_snapshot_age_s()
    stale_and_killed = InFlightSnapshot(
        available=True,
        age_s=max_age * 2.0,
        kill_switch_engaged=True,  # would otherwise map to STREAM_KILLED
        key_revoked=True,
        plan_changed=True,
    )
    control, latch, _clk, _ctr = _build(stale_and_killed, contract=contract)

    assert control.evaluate() == posture.STREAM_SNAPSHOT_STALE
    assert latch.reason() == posture.STREAM_SNAPSHOT_STALE


def test_unavailable_snapshot_wins_over_max_duration() -> None:
    """R10.6 precedence: unavailable snapshot + past-deadline clock → stale wins over duration.

    The fail-closed stale/unavailable check runs FIRST, ahead of the max-duration comparison, so
    even with the clock past the deadline the client is told the snapshot could not be verified.
    """
    clk = _Clock(start=0.0)
    contract = _contract()
    unavailable = InFlightSnapshot(
        available=False,
        age_s=0.0,
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build(unavailable, clock=clk, contract=contract)

    clk.now = contract.max_stream_duration_s()  # clock also past the max-duration deadline

    assert control.evaluate() == posture.STREAM_SNAPSHOT_STALE
    assert latch.reason() == posture.STREAM_SNAPSHOT_STALE


# --------------------------------------------------------------------------- #
# Idempotence: the one-way latch is set once; a second evaluate() does not rewrite it.
# --------------------------------------------------------------------------- #


def test_second_evaluate_returns_the_same_reason_and_does_not_overwrite() -> None:
    """R10.7 idempotence: after the first cut, a later trigger does not rewrite the reason.

    The first trigger to fire owns the client-facing code. Here key-revocation fires first and
    records ``STREAM_KEY_REVOKED``; a later poll that ALSO sees a kill switch / plan change must
    return the already-recorded reason, not re-read the source and overwrite it (one-way latch).
    """
    source = _ScriptedSource(
        InFlightSnapshot(
            available=True,
            age_s=0.0,
            kill_switch_engaged=False,
            key_revoked=True,
            plan_changed=False,
        )
    )
    latch = KillLatch()
    control = InFlightControl(
        source=source,
        contract=_contract(),
        clock=_Clock(),
        kill_latch=latch,
    )

    assert control.evaluate() == posture.STREAM_KEY_REVOKED
    assert latch.reason() == posture.STREAM_KEY_REVOKED

    # A later boundary now also shows a kill switch and a plan change (and even a stale age). The
    # one-way latch must NOT be overwritten: the client keeps being told about the revocation.
    source.snap = InFlightSnapshot(
        available=False,
        age_s=10_000.0,
        kill_switch_engaged=True,
        key_revoked=True,
        plan_changed=True,
    )

    assert control.evaluate() == posture.STREAM_KEY_REVOKED
    assert latch.reason() == posture.STREAM_KEY_REVOKED
    assert latch.is_killed()


def test_repeated_polls_after_a_cut_are_stable() -> None:
    """R10.7: many boundary polls after a cut keep returning the same reason (no thrash)."""
    rng = random.Random(0x10_02)
    snap = InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=True,
        key_revoked=False,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build(snap)

    first = control.evaluate()
    assert first == posture.STREAM_KILLED

    for _ in range(rng.randint(5, 20)):
        assert control.evaluate() == posture.STREAM_KILLED
        assert latch.reason() == posture.STREAM_KILLED
        assert latch.is_killed()


# --------------------------------------------------------------------------- #
# max_cut_latency_s() budget framing (structural): the cut lands immediately on the latch.
# --------------------------------------------------------------------------- #


def test_cut_sets_the_latch_immediately_at_evaluate() -> None:
    """R10.7 / ``max_cut_latency_s()``: a fired trigger kills the latch synchronously at evaluate.

    Structural budget framing: the cut takes effect at the next chunk boundary because the latch
    is set the instant the trigger fires — the shipped ``StreamPipeline`` reads the probe each
    boundary. We assert the latch flips immediately (and ``evaluate`` returns the same reason the
    latch now carries), keeping this unit-focused on the trigger → latch → reason mapping; the
    byte-level "no bytes after the cut" is a ``routes.py`` integration concern.
    """
    contract = _contract()
    # A positive, finite budget exists — the cut must land within it, enforced by boundary cadence.
    assert contract.max_cut_latency_s() > 0.0

    snap = InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=True,
    )
    control, latch, _clk, _ctr = _build(snap, contract=contract)

    assert latch.reason() is None  # unset before the trigger fires

    reason = control.evaluate()

    # Killed synchronously at evaluate(): the probe is already true, so the pipeline cuts at the
    # next boundary (within max_cut_latency_s()); the reason the latch carries == the returned one.
    assert latch.is_killed()
    assert reason == posture.STREAM_PLAN_CHANGED
    assert latch.reason() == reason


def test_clear_snapshot_leaves_the_latch_untouched() -> None:
    """A fresh, available, all-clear snapshot under the deadline fires nothing (R10.7 baseline)."""
    control, latch, _clk, _ctr = _build(_clear_snapshot())

    assert control.evaluate() is None
    assert not latch.is_killed()
    assert latch.reason() is None
    assert latch() is False  # the KillSignal probe the pipeline checks each boundary
