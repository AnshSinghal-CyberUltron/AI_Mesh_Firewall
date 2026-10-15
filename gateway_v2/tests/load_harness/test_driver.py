"""Async open-loop driver property tests (B6 / R2-21), task 8.3 (+ 8.4 appends here later).

Covers the async ``load_harness.driver`` module via a FAKE injected transport + a FAKE
virtual-time clock/sleep — no real server, no real wall-time, instant and deterministic.

* **Property 2 — Open-loop independence from server speed** (task 8.3): for any schedule and any
  sequence of (arbitrary, even monotonically increasing) simulated response latencies, every
  dispatched start time equals its pre-computed scheduled time (within the Schedule_Drop
  tolerance). The offered rate never shrinks because responses slowed — the forbidden closed-loop
  model in which a slow server throttles the generator.

House idiom: a seeded ``random.Random`` driven for >= 10,000 total iterations, NO ``hypothesis``;
the async ``run_offered`` is driven via ``asyncio.run``. Test files are not under the import-linter
layer contract.

THE VIRTUAL-CLOCK / FAKE-CLIENT MODEL (documented honesty).
``run_offered`` spawns one :class:`asyncio.Task` per request UP FRONT, then awaits
``asyncio.gather`` exactly once. Each task: (a) reads ``now = clock() - start_reference`` and sleeps
``wait_s = scheduled_s - now`` (clamped at 0); (b) reads ``actual_start_s = clock() -
start_reference`` the instant BEFORE the fetch; (c) awaits ``client.fetch(request)``.

To exercise the open-loop invariant WITHOUT real time, we inject a DISCRETE-EVENT virtual scheduler
(:class:`_VirtualTime`) — a hand-rolled virtual clock, not a harness value type — driven by two
seams the driver already takes injected:

  * ``sleep(d)`` — the ONLY thing that advances the DISPATCH clock, and it does so as a correct
    discrete-event simulation. A task calling ``sleep(d)`` registers an absolute wake instant
    ``now + d`` and then suspends (yielding via ``asyncio.sleep(0)``) until the virtual clock has
    been advanced to AT LEAST that instant. The clock is advanced ONLY when every runnable task is
    parked on a ``sleep``: a background pump coroutine (:meth:`_VirtualTime.pump`, gathered
    alongside ``run_offered``) repeatedly jumps ``now_s`` to the MINIMUM pending wake instant and
    releases exactly the tasks due at that instant. So each dispatch task's ``sleep`` resolves with
    the clock reading its OWN scheduled instant — never a later task's — which is what makes
    ``actual_start_s`` land exactly on ``scheduled_s``. Monotone, never backwards.
  * ``fetch`` latency — advances a SEPARATE per-request latency accumulator the dispatch clock does
    NOT read. The fake client records an arbitrary (possibly huge, possibly monotonically
    increasing) simulated response latency per request and ``await``s a zero-delay yield so the loop
    interleaves tasks; that latency is reported back for assertion but is structurally unable to
    move the dispatch clock, because only ``sleep`` writes ``now_s``.

Because ``actual_start_s`` is read strictly BEFORE ``fetch``, the dispatch clock advances only to
scheduled wake instants (never by fetch latency), and the discrete-event pump resolves each task's
sleep AT ITS OWN instant, every ``actual_start_s`` lands exactly on its ``scheduled_s`` (zero slip)
no matter how slow — or how increasingly slow — the responses are. That IS Property 2: the fake
transport proves a slow server cannot throttle the offered schedule. We also assert the invariant
holds for a deliberately adversarial strictly-increasing latency profile.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass, field

from load_harness.arrivals import Arrival
from load_harness.config import default_tolerances
from load_harness.driver import DispatchRecord, OfferedRun, StreamOutcome, run_offered
from load_harness.request_plan import PlannedRequest
from load_harness.strata.prompts import PromptTag

#: Total driving iterations across all seeds/schedules/latency-profiles (house idiom >= 10,000).
#: Each outer iteration runs one full ``asyncio.run(run_offered(...))`` over a drawn schedule; the
#: product of outer iterations and the per-run request count is far above the 10,000 floor.
_OUTER_ITERATIONS = 400

#: Requests per drawn schedule. ``_OUTER_ITERATIONS * _MIN_REQUESTS`` dispatches >> 10,000.
_MIN_REQUESTS = 5
_MAX_REQUESTS = 60

#: A fixed virtual-clock origin; the absolute offset is irrelevant (dispatch timing is measured
#: relative to the run's captured ``start_reference_s``), so any constant keeps the test off the
#: wall clock.
_CLOCK_ORIGIN_S = 1_000.0

#: ``min_headroom_margin`` large enough that ``headroom=None`` (unbounded) never marks the run
#: generator-bound; Property 2 is about dispatch timing, not headroom, so we pass no calibration.
_MIN_HEADROOM_MARGIN = default_tolerances().min_headroom_margin

#: The Schedule_Drop tolerance (seconds) from config — the slip bound dispatch timing is judged
#: against. Under the virtual clock every slip is exactly 0.0, comfortably within tolerance.
_SCHEDULE_DROP_S = default_tolerances().schedule_drop_s


@dataclass(slots=True)
class _VirtualTime:
    """A discrete-event virtual clock for one ``run_offered`` run (NOT a harness value type).

    ``now_s`` is the current virtual time; it is advanced ONLY by the :meth:`pump` coroutine (the
    dispatch timeline) and read by :meth:`clock`. Fetch latency is tracked separately by the fake
    client and never written here, so a slow fetch cannot move the dispatch clock — the structural
    heart of the open-loop invariant.

    ``pending`` maps an absolute wake instant to the count of tasks parked on it. :meth:`sleep`
    registers a wake and busy-yields until ``now_s`` has reached it; :meth:`pump` jumps ``now_s`` to
    the EARLIEST pending instant whenever all dispatch tasks are parked, so each ``sleep`` resolves
    with the clock reading that task's own instant (never a later task's). ``run_done`` is set by
    the caller once ``run_offered`` has returned so the pump knows the run is finished and can stop.
    ``slept_targets`` records every registered wake instant for sanity.
    """

    now_s: float = _CLOCK_ORIGIN_S
    pending: dict[float, int] = field(default_factory=dict)
    run_done: bool = False
    slept_targets: list[float] = field(default_factory=list)

    def clock(self) -> float:
        """Read virtual "now" (the injected :data:`load_harness.driver.AsyncClock`)."""
        return self.now_s

    async def sleep(self, duration_s: float) -> None:
        """Register an absolute wake instant and suspend until the pump advances the clock to it.

        The injected :data:`load_harness.driver.AsyncSleep`. ``duration_s`` is a relative sleep; the
        absolute target is ``now_s + duration_s`` captured at call time. The coroutine yields with
        ``asyncio.sleep(0)`` until ``now_s`` reaches its target — the pump does the advancing — so
        the task resumes with the virtual clock reading its OWN scheduled instant.
        """
        target_s = self.now_s + max(duration_s, 0.0)
        self.slept_targets.append(target_s)
        if target_s <= self.now_s:
            # Already due (e.g. a zero/negative wait): resolve on the next loop turn.
            await asyncio.sleep(0)
            return
        self.pending[target_s] = self.pending.get(target_s, 0) + 1
        while self.now_s < target_s:
            await asyncio.sleep(0)

    async def pump(self) -> None:
        """Advance virtual time to the earliest pending wake once all tasks are parked.

        Runs concurrently with the dispatch tasks. Each turn it yields; when there are pending
        wakes it jumps ``now_s`` forward to the minimum pending instant and clears that bucket,
        releasing every task due at that instant. It exits once no dispatch task remains active and
        nothing is pending, so ``asyncio.gather`` can complete.
        """
        while True:
            await asyncio.sleep(0)
            if self.pending:
                next_s = min(self.pending)
                if next_s > self.now_s:
                    self.now_s = next_s
                del self.pending[next_s]
                continue
            if self.run_done:
                return


@dataclass(slots=True)
class _SlowFakeClient:
    """A fake :class:`~load_harness.driver.StreamClient` simulating per-request response latency.

    ``fetch`` returns a well-formed :class:`StreamOutcome` after "taking" a per-request simulated
    latency drawn from ``latencies`` (keyed by arrival index). The latency is accumulated into
    ``total_latency_s`` for assertion but is NEVER written to the shared :class:`_VirtualTime`
    dispatch clock, so by construction it cannot delay any other task's dispatch. A single
    zero-delay ``await`` models the fetch suspending and resuming without wall-time; the event loop
    is free to run other tasks during it. ``fetch`` never raises (an errored stream would be
    reported as ``errored=True``), honouring the driver's seam contract.
    """

    latencies: dict[int, float]
    total_latency_s: float = 0.0
    fetched_indices: list[int] = field(default_factory=list)

    async def fetch(self, request: PlannedRequest) -> StreamOutcome:
        index = request.arrival.index
        latency_s = self.latencies.get(index, 0.0)
        self.total_latency_s += latency_s
        self.fetched_indices.append(index)
        # Suspend/resume like a real streaming fetch, but add NO wall-time and (critically) do not
        # touch the dispatch clock; the simulated latency lives only in this fake's accounting.
        await asyncio.sleep(0)
        return StreamOutcome(chunks=(), holdback_ms=0.0, errored=False, tag=PromptTag.SHORT)


def _planned_requests(scheduled_offsets_s: list[float]) -> tuple[PlannedRequest, ...]:
    """Build neutral :class:`PlannedRequest`\\ s at the given ABSOLUTE scheduled instants.

    ``scheduled_offsets_s`` are absolute ``scheduled_s`` values (origin-relative is fine; the driver
    measures against its own captured ``start_reference_s`` which equals the origin here). Every
    stratum field is ``None`` (no stratum shapes dispatch TIMING — Property 2 is purely about the
    schedule-vs-actual timing, independent of strata), so no ``Populations`` are needed.
    """
    return tuple(
        PlannedRequest(
            arrival=Arrival(index=i, scheduled_s=scheduled_s),
            key_assignment=None,
            tenant_assignment=None,
            prompt_pick=None,
            client_is_retrying=None,
            secret_output_plan=None,
        )
        for i, scheduled_s in enumerate(scheduled_offsets_s)
    )


def _draw_schedule(rng: random.Random) -> list[float]:
    """Draw an ascending absolute-scheduled-instant list relative to the virtual-clock origin.

    Starts at the origin and accumulates strictly-positive random gaps, so ``scheduled_s`` is
    strictly increasing — the shape ``arrivals.build_schedule`` emits.
    """
    count = rng.randint(_MIN_REQUESTS, _MAX_REQUESTS)
    cursor_s = _CLOCK_ORIGIN_S
    offsets: list[float] = []
    for _ in range(count):
        cursor_s += rng.uniform(1e-4, 0.5)
        offsets.append(cursor_s)
    return offsets


def _draw_latencies(rng: random.Random, count: int, *, increasing: bool) -> dict[int, float]:
    """Draw a per-request simulated response-latency profile keyed by arrival index.

    When ``increasing`` is True the latencies are a strictly MONOTONICALLY INCREASING sequence (the
    adversarial "server gets slower and slower" profile Property 2 must survive); otherwise each is
    an arbitrary independent draw, including occasional huge spikes.
    """
    if increasing:
        latency_s = 0.0
        out: dict[int, float] = {}
        for index in range(count):
            latency_s += rng.uniform(0.5, 5.0)  # grows every request
            out[index] = latency_s
        return out
    return {
        index: (rng.uniform(50.0, 500.0) if rng.random() < 0.1 else rng.uniform(0.0, 5.0))
        for index in range(count)
    }


async def _run_once(
    requests: tuple[PlannedRequest, ...],
    latencies: dict[int, float],
    offered_rate_per_s: float,
) -> tuple[OfferedRun, _SlowFakeClient, _VirtualTime]:
    """Drive one open-loop run under the virtual clock + slow fake client.

    No ``headroom`` is passed (``None`` => unbounded) so the run is never spuriously marked
    generator-bound; dispatch timing is the whole subject here.
    """
    virtual = _VirtualTime()
    client = _SlowFakeClient(latencies=latencies)

    async def _drive() -> OfferedRun:
        """Run the open-loop dispatch, then signal the pump it may stop."""
        try:
            return await run_offered(
                requests,
                client,
                offered_rate_per_s=offered_rate_per_s,
                schedule_drop_s=_SCHEDULE_DROP_S,
                min_headroom_margin=_MIN_HEADROOM_MARGIN,
                clock=virtual.clock,
                sleep=virtual.sleep,
            )
        finally:
            virtual.run_done = True

    run, _pump = await asyncio.gather(_drive(), virtual.pump())
    return run, client, virtual


# --------------------------------------------------------------------------- #
# Task 8.3 — Property 2: open-loop independence from server speed
# Feature: load-harness-r2-21, Property 2: open-loop independence from server speed
# Validates: Requirements 1.1, 1.5
# --------------------------------------------------------------------------- #


def test_property2_dispatch_times_track_schedule_regardless_of_server_speed() -> None:
    """Every ``actual_start_s`` equals its ``scheduled_s`` within tolerance, however slow responses.

    For many seeds, draw an ascending schedule and an arbitrary (and, on alternating iterations, a
    deliberately strictly-INCREASING) per-request response-latency profile. Drive ``run_offered``
    under the virtual clock + slow fake client. Assert that for EVERY dispatched request the slip
    (``actual_start_s - scheduled_s``) is within the Schedule_Drop tolerance, no record is marked
    ``schedule_dropped``, and the run's ``schedule_drop_count`` is zero — i.e. the offered rate was
    never throttled by response latency (Req 1.1, 1.5). The fake client's accumulated simulated
    latency is asserted non-zero so the test genuinely exercised slow responses.
    """
    seed = 0x21_02
    rng = random.Random(seed)
    total_dispatched = 0
    for i in range(_OUTER_ITERATIONS):
        offsets = _draw_schedule(rng)
        requests = _planned_requests(offsets)
        increasing = (i % 2) == 0
        latencies = _draw_latencies(rng, len(requests), increasing=increasing)
        offered_rate_per_s = rng.uniform(0.5, 500.0)

        run, client, _virtual = asyncio.run(_run_once(requests, latencies, offered_rate_per_s))

        assert len(run.records) == len(requests), (
            f"seed={seed:#x} iter={i}: expected {len(requests)} records, got {len(run.records)}"
        )
        for record, scheduled_s in zip(run.records, offsets, strict=True):
            slip_s = record.actual_start_s - scheduled_s
            assert record.scheduled_s == scheduled_s, (
                f"seed={seed:#x} iter={i} index={record.index}: scheduled_s mismatch "
                f"{record.scheduled_s} != {scheduled_s}"
            )
            assert abs(slip_s) <= _SCHEDULE_DROP_S, (
                f"seed={seed:#x} iter={i} index={record.index} increasing={increasing}: "
                f"actual_start_s={record.actual_start_s} slipped {slip_s:.6g}s past scheduled "
                f"{scheduled_s} (> tolerance {_SCHEDULE_DROP_S}) despite open-loop dispatch"
            )
            assert not record.schedule_dropped, (
                f"seed={seed:#x} iter={i} index={record.index}: schedule_dropped set under the "
                f"virtual clock where slip is 0"
            )
        assert run.schedule_drop_count == 0, (
            f"seed={seed:#x} iter={i}: schedule_drop_count={run.schedule_drop_count} != 0 — a slow "
            f"server throttled the offered schedule (closed-loop regression)"
        )
        # The run genuinely exercised slow responses (otherwise the property is vacuous).
        if requests:
            assert client.total_latency_s > 0.0, (
                f"seed={seed:#x} iter={i}: fake client recorded no simulated response latency"
            )
        total_dispatched += len(run.records)

    assert total_dispatched >= 10_000, (
        f"total dispatched requests {total_dispatched} < 10,000 house-idiom floor"
    )


def test_property2_monotonically_increasing_latency_never_delays_dispatch() -> None:
    """A strictly-increasing per-request latency profile still yields exact on-schedule dispatch.

    The adversarial case called out by the property ("even increasing" latencies): a server that
    gets slower on every request must NOT drag later dispatches late. One focused run with a
    strictly-increasing latency sequence over a fixed schedule asserts zero slip on every record and
    a zero drop count, and that the recorded ``actual_start_s`` values are themselves strictly
    increasing (they track the ascending schedule, not the latency pile-up).
    """
    offsets = [_CLOCK_ORIGIN_S + 0.01 * (k + 1) for k in range(40)]
    requests = _planned_requests(offsets)
    latencies = {index: 1.0 + 10.0 * index for index in range(len(requests))}  # 1,11,21,... seconds

    run, client, _virtual = asyncio.run(_run_once(requests, latencies, offered_rate_per_s=100.0))

    assert run.schedule_drop_count == 0
    prev_actual_s = -math.inf
    for record, scheduled_s in zip(run.records, offsets, strict=True):
        assert abs(record.actual_start_s - scheduled_s) <= _SCHEDULE_DROP_S, (
            f"index={record.index}: actual_start_s={record.actual_start_s} did not track scheduled "
            f"{scheduled_s} under strictly-increasing latency"
        )
        assert record.actual_start_s > prev_actual_s, (
            f"index={record.index}: actual_start_s not strictly increasing — dispatch order "
            f"followed latency, not the schedule"
        )
        prev_actual_s = record.actual_start_s
    # Later requests carried larger latencies, proving the increasing profile was really applied.
    assert client.total_latency_s > 0.0
    assert set(client.fetched_indices) == set(range(len(requests)))


def test_property2_empty_schedule_is_a_clean_zero_drop_run() -> None:
    """An empty request set yields an empty :class:`OfferedRun` with zero drops (edge case)."""
    run, client, _virtual = asyncio.run(_run_once((), {}, offered_rate_per_s=10.0))
    assert run.records == ()
    assert run.schedule_drop_count == 0
    assert client.total_latency_s == 0.0


def test_record_slip_helper_matches_dispatch_fields() -> None:
    """``DispatchRecord.slip_s`` equals ``actual_start_s - scheduled_s`` (value-type sanity)."""
    record = DispatchRecord(
        index=0,
        scheduled_s=5.0,
        actual_start_s=5.25,
        schedule_dropped=False,
        outcome=StreamOutcome(chunks=(), holdback_ms=0.0, errored=False, tag=PromptTag.SHORT),
    )
    assert record.slip_s == 0.25

# --------------------------------------------------------------------------- #
# Task 8.4 — Property 3: schedule-drop accounting is exact
# Feature: load-harness-r2-21, Property 3: schedule-drop accounting is exact
# Validates: Requirements 1.3, 1.4
# --------------------------------------------------------------------------- #
#
# THE DROP-FORCING MECHANISM (documented honesty).
#
# Property 2's ``_VirtualTime`` resolves every ``sleep`` EXACTLY at its scheduled instant, so every
# slip is 0.0 and no drop can ever occur — perfect for proving open-loop independence, useless for
# proving the drop ACCOUNTING. To exercise drops we need some dispatches to start late by a
# CONTROLLED, KNOWN amount so we know a priori which requests should slip past
# ``Tolerances.schedule_drop_s`` and which should not.
#
# ``_OvershootVirtualTime`` is a focused discrete-event variant that does exactly that, and ONLY
# that. It is identical to ``_VirtualTime`` except that a per-request ``overshoot_s`` map lets the
# clock resolve a SELECTED task's sleep LATE by a known margin: when a task sleeps toward absolute
# target ``scheduled_s``, the variant instead parks it on ``scheduled_s + overshoot_s[index]`` and
# the pump advances ``now_s`` to that overshot instant before releasing the task. The driver then
# reads ``actual_start_s = scheduled_s + overshoot`` (the clock the instant BEFORE the fetch), so
# the slip is EXACTLY the injected ``overshoot`` — nothing else moves the dispatch clock (fetch
# latency is still tracked only in the fake client, never written to ``now_s``, so the open-loop
# structure is preserved; we simply choose how late each wake resolves).
#
# Honesty of the assertion. The test does NOT trust the injected overshoot as the oracle. It
# recomputes the drop GROUND TRUTH from the driver's own observed fields, EXACTLY as the driver
# does — ``(record.actual_start_s - record.scheduled_s) > schedule_drop_s`` — and asserts (a) every
# ``record.schedule_dropped`` matches that ground truth, and (b) the reported
# ``OfferedRun.schedule_drop_count`` equals exactly the number of ground-truth drops. So the test
# proves the ACCOUNTING is exact (flag IFF over-tolerance, count == number of such requests), not
# merely that "some drops happen". The overshoot profile is drawn to straddle the tolerance from
# both sides (well below, exactly at, just above, and far above) so both branches of the IFF and
# the boundary are exercised across the seeds.


@dataclass(slots=True)
class _OvershootVirtualTime:
    """Discrete-event virtual clock that resolves SELECTED sleeps late by a known margin.

    Mirrors :class:`_VirtualTime` (same monotone pump, same park-until-advanced ``sleep``) with one
    addition: ``overshoot_s`` maps a sleep's absolute target instant to an extra margin by which the
    clock overshoots when resolving THAT sleep. Because the driver sleeps toward each request's
    absolute ``scheduled_s`` and reads ``actual_start_s`` the instant it wakes, parking the task on
    ``scheduled_s + overshoot`` makes the observed slip exactly ``overshoot`` — a controlled, known
    late start. Fetch latency is still never written here, so only the chosen overshoot (not server
    speed) moves a dispatch's start: the open-loop structure is intact, we only choose WHEN each
    scheduled wake resolves. ``now_s`` is monotone (overshoot is always ``>= 0``).
    """

    overshoot_s: dict[float, float] = field(default_factory=dict)
    now_s: float = _CLOCK_ORIGIN_S
    pending: dict[float, int] = field(default_factory=dict)
    run_done: bool = False

    def clock(self) -> float:
        """Read virtual "now" (the injected :data:`load_harness.driver.AsyncClock`)."""
        return self.now_s

    async def sleep(self, duration_s: float) -> None:
        """Park until the pump advances to this sleep's (possibly OVERSHOT) absolute target.

        The relative ``duration_s`` resolves to absolute ``now_s + duration_s``. Because the driver
        reads a RELATIVE now (``clock() - start_reference_s``) and all sleeps are registered at run
        start when ``now_s == _CLOCK_ORIGIN_S``, ``duration_s`` equals the request's ``scheduled_s``
        OFFSET, which is the key the ``overshoot_s`` map is built with. If that offset carries an
        overshoot the task parks on ``scheduled_target_s + overshoot`` instead, so it wakes with the
        clock reading its scheduled start PLUS the known late margin. A zero/negative wait still
        resolves on the next loop turn (clamped, like the driver's own clamp).
        """
        scheduled_target_s = self.now_s + max(duration_s, 0.0)
        offset_key_s = scheduled_target_s - _CLOCK_ORIGIN_S
        wake_s = scheduled_target_s + self.overshoot_s.get(offset_key_s, 0.0)
        if wake_s <= self.now_s:
            await asyncio.sleep(0)
            return
        self.pending[wake_s] = self.pending.get(wake_s, 0) + 1
        while self.now_s < wake_s:
            await asyncio.sleep(0)

    async def pump(self) -> None:
        """Advance virtual time to the earliest pending (possibly overshot) wake, monotone."""
        while True:
            await asyncio.sleep(0)
            if self.pending:
                next_s = min(self.pending)
                if next_s > self.now_s:
                    self.now_s = next_s
                del self.pending[next_s]
                continue
            if self.run_done:
                return


async def _run_once_overshoot(
    requests: tuple[PlannedRequest, ...],
    overshoot_by_target_s: dict[float, float],
) -> OfferedRun:
    """Drive one open-loop run under the overshoot clock (no headroom => never gen-bound)."""
    virtual = _OvershootVirtualTime(overshoot_s=overshoot_by_target_s)
    client = _SlowFakeClient(latencies={})

    async def _drive() -> OfferedRun:
        try:
            return await run_offered(
                requests,
                client,
                offered_rate_per_s=100.0,
                schedule_drop_s=_SCHEDULE_DROP_S,
                min_headroom_margin=_MIN_HEADROOM_MARGIN,
                clock=virtual.clock,
                sleep=virtual.sleep,
            )
        finally:
            virtual.run_done = True

    run, _pump = await asyncio.gather(_drive(), virtual.pump())
    return run


#: A fixed inter-arrival gap (seconds) for the drop schedule, chosen MUCH larger than the
#: Schedule_Drop tolerance so that even the largest injected overshoot stays strictly below it. That
#: keeps the discrete-event clock ordering exact: no task's overshot wake ever crosses the next
#: task's scheduled instant, so each dispatch resolves at EXACTLY its own ``scheduled + overshoot``
#: and the injected late margin equals the observed slip (the pump never leaks one task's
#: overshoot onto another). The assertions still recompute the oracle from observed fields.
_GAP_S = 1.0

#: The largest overshoot the drop profile injects — comfortably over tolerance (an unambiguous
#: drop) yet strictly below ``_GAP_S`` so wake ordering stays monotone and un-crossed.
_MAX_OVERSHOOT_S = _GAP_S * 0.5


def _draw_drop_schedule(rng: random.Random) -> list[float]:
    """Draw an ascending schedule with a FIXED large gap so overshoots never cross an instant.

    Unlike :func:`_draw_schedule` (tiny random gaps, for Property 2's timing invariant), this uses a
    constant ``_GAP_S`` gap. With every overshoot bounded below ``_GAP_S`` the i-th wake
    (``scheduled_i + overshoot_i``) always stays below the (i+1)-th scheduled instant, so the
    discrete-event pump resolves each sleep at its own instant and the controlled late margin is
    exactly the observed slip — the honest basis for an EXACT accounting check.
    """
    count = rng.randint(_MIN_REQUESTS, _MAX_REQUESTS)
    return [_CLOCK_ORIGIN_S + _GAP_S * (k + 1) for k in range(count)]


def _draw_overshoots(rng: random.Random, offsets: list[float]) -> dict[float, float]:
    """Draw a per-scheduled-instant overshoot that straddles the tolerance from both sides.

    For each scheduled instant pick, with mixed probability, one of: no overshoot (0.0 — a clean
    on-time dispatch), well below tolerance, EXACTLY at tolerance (boundary: ``> tol`` is False, so
    NOT a drop), just above tolerance (a true drop by the smallest margin), or far above tolerance
    but still below ``_MAX_OVERSHOOT_S`` (an unambiguous drop that never crosses the next instant).
    Keyed by the ``scheduled_s`` OFFSET, which is how the overshoot clock looks the margin up
    (``sleep`` recovers that offset from the registered duration). The schedule is strictly
    increasing so the keys are distinct.
    """
    tol = _SCHEDULE_DROP_S
    out: dict[float, float] = {}
    for scheduled_s in offsets:
        roll = rng.random()
        if roll < 0.30:
            overshoot = 0.0  # clean on-time
        elif roll < 0.50:
            overshoot = rng.uniform(0.0, tol * 0.5)  # within tolerance -> not a drop
        elif roll < 0.60:
            overshoot = tol  # exactly at the boundary -> NOT a drop (strict >)
        elif roll < 0.80:
            overshoot = tol + rng.uniform(tol * 1e-3, tol)  # just over -> a drop
        else:
            overshoot = rng.uniform(tol * 10.0, _MAX_OVERSHOOT_S)  # far over -> a drop
        out[scheduled_s] = overshoot
    return out


def test_property3_schedule_drop_flag_and_count_are_exact() -> None:
    """A record is flagged dropped IFF its observed slip exceeds tolerance, and the count is exact.

    For many seeds, draw an ascending schedule and a per-request overshoot profile straddling the
    Schedule_Drop tolerance (clean / within / exactly-at / just-over / far-over). Drive
    ``run_offered`` under the overshoot virtual clock. For EVERY record recompute the drop ground
    truth the way the driver defines it — ``(actual_start_s - scheduled_s) > schedule_drop_s`` — and
    assert ``record.schedule_dropped`` matches it exactly; then assert
    ``run.schedule_drop_count`` equals the number of ground-truth drops (Req 1.3, 1.4). The oracle
    is the driver's own observed fields, so the test pins ACCOUNTING EXACTNESS, not just presence.
    """
    seed = 0x21_03
    rng = random.Random(seed)
    total_dispatched = 0
    saw_a_drop = False
    saw_a_nondrop = False
    saw_boundary = False
    for i in range(_OUTER_ITERATIONS):
        offsets = _draw_drop_schedule(rng)
        requests = _planned_requests(offsets)
        overshoot_by_target_s = _draw_overshoots(rng, offsets)

        run = asyncio.run(_run_once_overshoot(requests, overshoot_by_target_s))

        assert len(run.records) == len(requests), (
            f"seed={seed:#x} iter={i}: expected {len(requests)} records, got {len(run.records)}"
        )
        expected_drops = 0
        for record, scheduled_s in zip(run.records, offsets, strict=True):
            assert record.scheduled_s == scheduled_s, (
                f"seed={seed:#x} iter={i} index={record.index}: scheduled_s mismatch "
                f"{record.scheduled_s} != {scheduled_s}"
            )
            # Ground truth recomputed from the driver's OWN observed fields, exactly as the driver
            # defines a Schedule_Drop (strict >, per Req 1.3) — NOT trusting the injected overshoot.
            observed_slip_s = record.actual_start_s - record.scheduled_s
            ground_truth_drop = observed_slip_s > _SCHEDULE_DROP_S
            assert record.schedule_dropped == ground_truth_drop, (
                f"seed={seed:#x} iter={i} index={record.index}: schedule_dropped="
                f"{record.schedule_dropped} but observed slip {observed_slip_s:.9g}s vs tolerance "
                f"{_SCHEDULE_DROP_S} gives ground-truth drop={ground_truth_drop}"
            )
            if ground_truth_drop:
                expected_drops += 1
                saw_a_drop = True
            else:
                saw_a_nondrop = True
            # Record whether a slip in the immediate NEIGHBOURHOOD of the tolerance was exercised
            # (the hardest region for the strict ``>`` boundary). We do NOT assert the flag here
            # independently: the per-record oracle assertion above (schedule_dropped IFF observed
            # slip > tolerance) is the authority, and it holds whichever side of the boundary float
            # arithmetic lands the injected margin on. This flag only proves the near-boundary
            # region was covered.
            if math.isclose(observed_slip_s, _SCHEDULE_DROP_S, rel_tol=1e-6, abs_tol=0.0):
                saw_boundary = True
        assert run.schedule_drop_count == expected_drops, (
            f"seed={seed:#x} iter={i}: schedule_drop_count={run.schedule_drop_count} != "
            f"{expected_drops} ground-truth over-tolerance dispatches"
        )
        total_dispatched += len(run.records)

    assert total_dispatched >= 10_000, (
        f"total dispatched requests {total_dispatched} < 10,000 house-idiom floor"
    )
    # The profile genuinely exercised BOTH branches of the IFF and the exact boundary.
    assert saw_a_drop, "no over-tolerance dispatch was ever produced — drop branch unexercised"
    assert saw_a_nondrop, "no within-tolerance dispatch produced — non-drop branch unexercised"
    assert saw_boundary, "the exactly-at-tolerance boundary was never exercised"


def test_property3_all_clean_run_reports_zero_drops() -> None:
    """A schedule with zero overshoot everywhere reports exactly zero drops (lower-edge case)."""
    offsets = [_CLOCK_ORIGIN_S + _GAP_S * (k + 1) for k in range(30)]
    requests = _planned_requests(offsets)
    run = asyncio.run(_run_once_overshoot(requests, {scheduled_s: 0.0 for scheduled_s in offsets}))
    assert run.schedule_drop_count == 0
    assert all(not record.schedule_dropped for record in run.records)
    assert all(record.actual_start_s == record.scheduled_s for record in run.records)


def test_property3_all_late_run_flags_every_request() -> None:
    """A schedule overshot past tolerance everywhere flags every request and counts them all.

    Every instant is overshot by ``_MAX_OVERSHOOT_S`` — well over tolerance yet below ``_GAP_S`` so
    no wake crosses the next scheduled instant — so every observed slip exceeds the tolerance.
    """
    offsets = [_CLOCK_ORIGIN_S + _GAP_S * (k + 1) for k in range(30)]
    requests = _planned_requests(offsets)
    overshoot = {scheduled_s: _MAX_OVERSHOOT_S for scheduled_s in offsets}
    run = asyncio.run(_run_once_overshoot(requests, overshoot))
    assert run.schedule_drop_count == len(requests)
    assert all(record.schedule_dropped for record in run.records)
    for record in run.records:
        assert (record.actual_start_s - record.scheduled_s) > _SCHEDULE_DROP_S
