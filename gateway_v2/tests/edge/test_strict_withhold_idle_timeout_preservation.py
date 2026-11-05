"""Preservation property tests for the ``strict-withhold-idle-timeout`` bugfix (task 2).

# Feature: strict-withhold-idle-timeout, Property 2: Preservation
# Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8

This is the task-2 PRESERVATION test suite of the ``strict-withhold-idle-timeout`` bugfix spec
(bugfix.md §3; design.md "Preservation Checking", "Correctness Properties" Property 4). It follows
the OBSERVATION-FIRST methodology: every assertion here is a behaviour the UNFIXED code already
exhibits for an input OUTSIDE the bug condition C, captured as a golden value so the SAME file
re-runs green after task 3 wires ``upstream_liveness``.

The bug (task 1) is that a withholding strict stream (``STRICT_WITHHOLD`` + a non-empty
``enforcing_output_rules(plan)``) whose time to its first downstream write exceeds
``idle_timeout_s`` is falsely cut with ``stream_idle_timeout``. The fix adds an upstream-liveness
re-seed of the idle clock in exactly that one mode. Therefore EVERYTHING ELSE must be byte-for-byte
unchanged, and that is what this suite pins:

* 3.1 — an INCREMENTAL stream whose gap since the last completed write exceeds ``idle_timeout_s``
  is still cut with ``stream_idle_timeout`` BEFORE the write.
* 3.2 — an upstream stall beyond ``inter_chunk_timeout_s`` is cut with
  ``stream_inter_chunk_timeout`` in BOTH modes, with no content emitted.
* 3.3 — a single downstream write that blocks beyond ``write_timeout_s`` is cut with
  ``stream_write_timeout`` in BOTH modes.
* 3.4 — ``max_stream_duration_s`` (chunks every 100 ms driven to 300 s on an injected clock), the
  kill switch, key revocation, plan change and a stale snapshot each cut with their existing
  posture code, and the one-way ``KillLatch`` keeps the first-cut-wins rule.
* 3.5 — a routed stream's wider ``first_event_s`` bound is honoured and the GW28 D-18 idle re-seed
  after the first event is observable (the idle clock restarts once the first event arrives).
* 3.6 — a strict BLOCK / REDACT / overflow / kill emits exactly the same frames, posture / error
  codes, the zero-content-before-block guarantee, and the same metric counters.
* 3.7 — a pass-through strict plan (no enforcing output rule) keeps the write-based idle model:
  a gap since the last completed write over ``idle_timeout_s`` is still an idle cut.
* 3.8 — ``idle_timeout_s`` / ``inter_chunk_timeout_s`` / ``write_timeout_s`` /
  ``max_stream_duration_s`` return the same derived values across random ``target_p99_ms``.

Harness (shared with the task-1 exploration test ``_drive_slowstream`` style): a REAL
``StreamTimeouts`` built from a ``ResourceContract``, a REAL pipeline from ``select_pipeline``, and
the shipped ``as_chunks`` adapter, driven with an injected ``_StubClock`` so the clock-based bounds
(idle, max-duration) are exact and have zero wall time. The two REAL-time bounds (inter-chunk,
write — they use ``asyncio.wait_for``) are driven exactly as the shipped LGW12 property test
(``tests/runtime/test_lgw12_timeouts.py``) does: a tiny ``target_p99_ms`` makes the derived bounds
tens of microseconds, and a never-resolving awaitable / never-completing write trips them in a
couple of real sub-millisecond waits.

House idiom: a module-constant seed, ``random.Random``, >= 100 iterations where a property ranges
over inputs, ``asyncio.run`` per async case, no ``hypothesis``, each property tagged in source.

Layering: a test file (outside the import-linter contract), so it imports the concrete
``detect`` / ``egress`` / ``edge`` seams directly and injects them, exactly as the task-1 harness
and the GW33 offline harness (``tests/e2e/gw33_support.py``) do.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import partial

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.dispatch.provider import UpstreamEvent
from gateway_v2.domain import (
    Action,
    Category,
    ExecutionPlan,
    FailurePosture,
    Finding,
    Mode,
    Phase,
    Rule,
    RuleCondition,
    RuleKind,
    RuleScope,
    Span,
    StreamingMode,
    Surface,
    posture,
)
from gateway_v2.domain.finding import FindingStatus
from gateway_v2.edge.asgi_sse import ErrorFrameScan, as_chunks
from gateway_v2.edge.cancel import InFlightControl, InFlightSnapshot, KillLatch
from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.edge.output_detectors import build_output_detectors
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.output_guard import enforcing_output_rules
from gateway_v2.egress.select import select_pipeline
from gateway_v2.egress.stream import DownstreamFrame
from gateway_v2.resolve import PlanResolver
from gateway_v2.runtime.holdback_config import load_holdback_config
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.strict_metrics import StrictMetrics

# --------------------------------------------------------------------------- #
# House-idiom constants.
# --------------------------------------------------------------------------- #

#: Seeded RNG for the property sweeps (logged + interpolated into failure messages).
_SEED = 0xF15751D1E
#: Iteration count for properties that range over inputs (house idiom: >= 100).
_ITERATIONS = 200
#: SLOWSTREAM cadence (scenario parameters, not capacity literals): content chunks 0.15 s apart.
_SLOWSTREAM_CHUNKS = 40
_SLOWSTREAM_GAP_S = 0.15
_CONTENT_CHANNEL = "output_content"
#: A per-worker RSS small enough to keep a tiny-SLO contract serviceable (see ``_tiny_contract``).
_PER_WORKER_RSS = 4 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Contracts.
# --------------------------------------------------------------------------- #


def _contract(*, target_p99_ms: float = 20.0) -> ResourceContract:
    """A serviceable contract. Default 20 ms SLO: idle 3 s, inter-chunk 1 s, write 0.5 s."""
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=1 << 34,
        fd_limit=4096,
        guard_capacity=None,
        target_p99_ms=target_p99_ms,
        utilization_cap=0.75,
        per_worker_rss=_PER_WORKER_RSS,
        worker_override=None,
        cpu_source="t",
        mem_source="t",
        fd_source="t",
    )


# --------------------------------------------------------------------------- #
# Plans: withholding strict, pass-through strict, INCREMENTAL.
# --------------------------------------------------------------------------- #


def _ip_leak_condition() -> RuleCondition:
    return RuleCondition(
        RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", "output.ip_leak", "",
    )


def _enforcing_out_rule() -> Rule:
    """A non-empty ``enforcing_output_rules`` member (OUTPUT / ENFORCE / BLOCK)."""
    return Rule(
        "out_ip_leak", Category.IP_LEAK, Mode.ENFORCE, Action.BLOCK, None, 200,
        RuleScope.OUTPUT, FailurePosture.FAIL_CLOSED, frozenset({Surface.CHAT}),
        _ip_leak_condition(), "[internal address]",
    )


def _strict_enforcing_plan() -> ExecutionPlan:
    """STRICT_WITHHOLD with one enforcing OUTPUT rule (the D-20 withholding shape)."""
    plan = ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, (_enforcing_out_rule(),), frozenset(),
        StreamingMode.STRICT_WITHHOLD, True,
    )
    assert enforcing_output_rules(plan), "fixture must be a withholding strict plan"
    return plan


def _redact_out_rule() -> Rule:
    """An enforcing OUTPUT rule with action REDACT, keyed to a customer rule-id join."""
    condition = RuleCondition(
        RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", "", "",
    )
    return Rule(
        "out_secret_redact", Category.SECRET, Mode.ENFORCE, Action.REDACT, None, 200,
        RuleScope.OUTPUT, FailurePosture.FAIL_CLOSED, frozenset({Surface.CHAT}), condition, "",
    )


def _strict_redact_plan() -> ExecutionPlan:
    """STRICT_WITHHOLD with one enforcing OUTPUT REDACT rule (the redact-path shape)."""
    plan = ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, (_redact_out_rule(),), frozenset(),
        StreamingMode.STRICT_WITHHOLD, True,
    )
    assert enforcing_output_rules(plan), "redact fixture must be a withholding strict plan"
    return plan


def _strict_passthrough_plan() -> ExecutionPlan:
    """STRICT_WITHHOLD with NO enforcing output rule (pass-through; 3.7)."""
    plan = ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, (), frozenset(),
        StreamingMode.STRICT_WITHHOLD, True,
    )
    assert not enforcing_output_rules(plan), "pass-through strict must have no enforcing rule"
    return plan


def _incremental_plan(*, enforcing: bool) -> ExecutionPlan:
    """INCREMENTAL plan, optionally with an enforcing output rule."""
    rules = (_enforcing_out_rule(),) if enforcing else ()
    return ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, rules, frozenset(),
        StreamingMode.INCREMENTAL, True,
    )


# --------------------------------------------------------------------------- #
# Injected seams.
# --------------------------------------------------------------------------- #


class _StubClock:
    """A manually-advanced seconds clock for the injected ``clock`` seam (zero wall time)."""

    __slots__ = ("now",)

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _fresh_snapshot() -> InFlightSnapshot:
    return InFlightSnapshot(
        available=True, age_s=0.0, kill_switch_engaged=False, key_revoked=False, plan_changed=False,
    )


@dataclass(frozen=True)
class _ScriptedControlSource:
    """An ``InFlightControlSource`` returning a fixed scripted snapshot (drives R10 triggers)."""

    snap: InFlightSnapshot

    def snapshot(self) -> InFlightSnapshot:
        return self.snap


class _FreshControlSource:
    """An ``InFlightControlSource`` returning a fresh, all-clear snapshot (nothing fires)."""

    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return _fresh_snapshot()


def _no_findings(_text: str) -> tuple[Finding, ...]:
    return ()


# --------------------------------------------------------------------------- #
# Capture + pipeline driver (shared with the task-1 exploration style).
# --------------------------------------------------------------------------- #


@dataclass
class _Capture:
    """Records the frames the pipeline emitted and how (and whether) the stream was cut."""

    frames: list[DownstreamFrame] = field(default_factory=list)
    timeout_cut: StreamTimeout | None = None
    latch_reason: str | None = None
    first_send_gap_s: float | None = None
    send_attempts: int = 0

    @property
    def content_frames(self) -> list[DownstreamFrame]:
        return [f for f in self.frames if f.error_code is None and f.text_deltas]

    @property
    def released(self) -> str:
        return "".join(
            t for f in self.frames if f.error_code is None for _c, t in f.text_deltas
        )

    @property
    def error_codes(self) -> list[str]:
        return [f.error_code for f in self.frames if f.error_code is not None]


def _build_pipeline(plan: ExecutionPlan, contract: ResourceContract):  # type: ignore[no-untyped-def]
    """Build the plan's stream pipeline with the real resolver + composed output detectors."""

    async def _inline(fn):  # type: ignore[no-untyped-def]
        return fn()

    cfg, _ = load_holdback_config({})
    out = build_output_detectors(
        plan, base=None, ticket=None, context_chunks=(), backend=None,
        contract=contract, runner=_inline,  # type: ignore[arg-type]
    )
    return select_pipeline(
        plan,
        scanner=holdback_scan,
        token_index=TokenIndex(),
        cfg=cfg,
        metrics=HoldbackMetrics(),
        window=max_pattern_length(),
        resolver=PlanResolver(plan=plan, phase=Phase.OUTPUT),
        detector=out.detector_for(plan.streaming_mode),
        max_buffer_bytes=1 << 24,
        strict_metrics=StrictMetrics(),
        delta_detector=out.canary_delta,
        final_detector=out.final,
        sync_runner=out.runner,
    )


@dataclass
class _Scenario:
    """One stream scenario driven through ``as_chunks`` + the selected pipeline."""

    plan: ExecutionPlan
    events: list[UpstreamEvent]
    gaps_s: list[float]
    control_source: object = field(default_factory=_FreshControlSource)
    first_event_s: float | None = None
    #: A clock advance applied just before each downstream content write (models client stall).
    send_advance_s: float = 0.0
    #: A detector that overrides the plan's composed detector (used for block / redact tests).
    detector_override: object | None = None
    #: When set, a write ``do_send`` that never completes (drives the write-timeout bound).
    block_writes: bool = False


async def _drive(scenario: _Scenario, *, contract: ResourceContract, clock: _StubClock) -> _Capture:
    """Drive the scenario; content ``send`` goes through ``StreamTimeouts.send`` like the route."""
    executor = ScanExecutor(contract=contract)
    try:
        plan = scenario.plan
        pipeline = _build_pipeline(plan, contract)
        if scenario.detector_override is not None:
            # Replace the per-channel detector so the strict final inspection sees the override.
            pipeline._detector = scenario.detector_override  # type: ignore[attr-defined] # noqa: SLF001
        kill_latch = KillLatch()
        control = InFlightControl(
            source=scenario.control_source, contract=contract, clock=clock, kill_latch=kill_latch,
        )
        timeouts = StreamTimeouts(
            contract=contract, kill_latch=kill_latch, clock=clock,
            first_event_s=scenario.first_event_s,
        )
        coalescer = Coalescer(contract=contract, active_streams=lambda: 1)
        capture = _Capture()

        async def _provider() -> AsyncIterator[UpstreamEvent]:
            for index, event in enumerate(scenario.events):
                clock.now += scenario.gaps_s[index]
                yield event

        async def _never_complete() -> None:
            await asyncio.Event().wait()

        async def _send(frame: DownstreamFrame) -> None:
            if frame.error_code is not None:
                capture.frames.append(frame)
                return
            if capture.first_send_gap_s is None:
                capture.first_send_gap_s = clock.now - timeouts._last_write_s  # noqa: SLF001
            # Model a client that stops accepting bytes: advance the clock before this write so the
            # idle gap grows (used for the INCREMENTAL + pass-through idle cases).
            clock.now += scenario.send_advance_s
            capture.send_attempts += 1
            if scenario.block_writes:
                await timeouts.send(_never_complete)
                return
            await timeouts.send(partial(_record, capture, frame))

        chunks = as_chunks(
            _provider(),
            coalescer,
            control,
            timeouts,
            error_scan=ErrorFrameScan(
                scanner=_no_findings,
                resolver=PlanResolver(plan=plan, phase=Phase.OUTPUT),
                kill_latch=kill_latch,
                executor=executor,
            ),
        )
        try:
            await pipeline.run(chunks, _send, kill_latch)
        except StreamTimeout as exc:
            capture.timeout_cut = exc
        capture.latch_reason = kill_latch.reason()
        return capture
    finally:
        executor.shutdown()


async def _record(capture: _Capture, frame: DownstreamFrame) -> None:
    capture.frames.append(frame)


def _content_events(n: int) -> list[UpstreamEvent]:
    """``n`` clean content events then a final event (plain prose, resolves ALLOW)."""
    events = [
        UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, f"token{i} "),), final=False)
        for i in range(n)
    ]
    events.append(UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, "done."),), final=True))
    return events


# --------------------------------------------------------------------------- #
# 3.8 — derived bounds unchanged across random ``target_p99_ms``.
# --------------------------------------------------------------------------- #


def test_derived_bounds_are_the_pinned_multiples_of_the_slo() -> None:
    """The four derived bounds are the pinned multiples of ``target_p99_ms`` (3.8).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.8

    ``idle = p99*150``, ``inter_chunk = p99*50``, ``write = p99*25``, ``max_duration = p99*15000``,
    each floored at one p99 period. Observed on the unfixed contract and re-checked after the fix:
    the fix never touches ``ResourceContract``, so these must be identical.
    """
    seed = _SEED
    print(f"test_derived_bounds seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        p99_ms = rng.uniform(0.01, 500.0)
        contract = _contract(target_p99_ms=p99_ms)
        p99_s = p99_ms / 1000.0
        expected = {
            "idle": max(p99_s * 150.0, p99_s),
            "inter_chunk": max(p99_s * 50.0, p99_s),
            "write": max(p99_s * 25.0, p99_s),
            "max_duration": max(p99_s * 15000.0, p99_s),
        }
        got = {
            "idle": contract.idle_timeout_s(),
            "inter_chunk": contract.inter_chunk_timeout_s(),
            "write": contract.write_timeout_s(),
            "max_duration": contract.max_stream_duration_s(),
        }
        assert got == expected, f"i={i} seed={seed:#x} p99_ms={p99_ms!r}: {got!r} != {expected!r}"
        # Ordering the preservation reasoning relies on: inter_chunk (1x50) < idle (1x150).
        assert got["inter_chunk"] < got["idle"], f"i={i}: inter-chunk must be stricter than idle"


# --------------------------------------------------------------------------- #
# 3.1 — INCREMENTAL idle cut before the write.
# --------------------------------------------------------------------------- #


def test_incremental_idle_gap_is_cut_with_stream_idle_timeout_before_the_write() -> None:
    """INCREMENTAL: a write gap over ``idle_timeout_s`` is an idle cut before the write (3.1).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.1

    An INCREMENTAL enforcing plan with a clean stream writes each chunk as it arrives. Advancing
    the injected clock past ``idle_timeout_s`` before a write makes the gap since the last completed
    write exceed the bound, so the NEXT ``send`` is refused with ``stream_idle_timeout`` before the
    write is attempted. This is the write-based idle model the fix must leave untouched.
    """
    seed = _SEED ^ 0x31
    print(f"test_incremental_idle seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        contract = _contract()
        idle = contract.idle_timeout_s()
        overshoot = rng.uniform(1e-6, idle)
        # The first write happens immediately (gap 0), then we advance past the idle bound before
        # the SECOND write: a steady upstream (small gaps) but a client that stopped reading.
        clock = _StubClock()
        scenario = _Scenario(
            plan=_incremental_plan(enforcing=True),
            events=_content_events(2),
            gaps_s=[0.0, 0.0, 0.0],
            send_advance_s=idle + overshoot,
        )
        capture = asyncio.run(_drive(scenario, contract=contract, clock=clock))
        detail = (
            f"i={i} seed={seed:#x} overshoot={overshoot!r} codes={capture.error_codes!r} "
            f"latch={capture.latch_reason!r} first_gap={capture.first_send_gap_s!r}"
        )
        assert capture.timeout_cut is not None, f"INCREMENTAL idle did not cut; {detail}"
        assert capture.timeout_cut.code == posture.STREAM_IDLE_TIMEOUT, detail
        assert capture.latch_reason == posture.STREAM_IDLE_TIMEOUT, detail


# --------------------------------------------------------------------------- #
# 3.2 — upstream stall > inter_chunk in both modes.
# --------------------------------------------------------------------------- #


def _tiny_contract(*, target_p99_ms: float) -> ResourceContract:
    """A serviceable contract with a sub-ms SLO so the REAL wait_for bounds are microseconds."""
    return _contract(target_p99_ms=target_p99_ms)


async def _check_inter_chunk_stall(plan: ExecutionPlan, contract: ResourceContract) -> _Capture:
    """A provider whose first next-event never resolves trips the inter-chunk bound (3.2)."""
    executor = ScanExecutor(contract=contract)
    try:
        pipeline = _build_pipeline(plan, contract)
        kill_latch = KillLatch()
        clock = _StubClock()
        control = InFlightControl(
            source=_FreshControlSource(), contract=contract, clock=clock, kill_latch=kill_latch,
        )
        timeouts = StreamTimeouts(contract=contract, kill_latch=kill_latch, clock=clock)
        coalescer = Coalescer(contract=contract, active_streams=lambda: 1)
        capture = _Capture()

        async def _provider() -> AsyncIterator[UpstreamEvent]:
            await asyncio.Event().wait()  # upstream never delivers an event (the stall)
            yield UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, "x"),), final=True)

        async def _send(frame: DownstreamFrame) -> None:
            if frame.error_code is not None:
                capture.frames.append(frame)
                return
            await timeouts.send(partial(_record, capture, frame))

        chunks = as_chunks(
            _provider(), coalescer, control, timeouts,
            error_scan=ErrorFrameScan(
                scanner=_no_findings,
                resolver=PlanResolver(plan=plan, phase=Phase.OUTPUT),
                kill_latch=kill_latch,
                executor=executor,
            ),
        )
        try:
            await pipeline.run(chunks, _send, kill_latch)
        except StreamTimeout as exc:
            capture.timeout_cut = exc
        capture.latch_reason = kill_latch.reason()
        return capture
    finally:
        executor.shutdown()


def test_upstream_stall_is_cut_with_inter_chunk_timeout_in_both_modes() -> None:
    """An upstream stall past ``inter_chunk_timeout_s`` cuts both modes, no content (3.2).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.2

    A tiny sub-ms SLO makes the derived inter-chunk bound tens of microseconds; the provider's
    first next-event never resolves, so ``asyncio.wait_for`` cancels it and the stream is cut with
    ``stream_inter_chunk_timeout`` — the upstream-stall code, never a client-idle code — in BOTH
    STRICT_WITHHOLD and INCREMENTAL, with zero content frames.
    """
    seed = _SEED ^ 0x32
    print(f"test_upstream_stall seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    plans = {
        "strict": _strict_enforcing_plan(),
        "incremental": _incremental_plan(enforcing=True),
    }
    for i in range(_ITERATIONS):
        p99_ms = rng.uniform(0.0002, 0.02)
        contract = _tiny_contract(target_p99_ms=p99_ms)
        for label, plan in plans.items():
            capture = asyncio.run(_check_inter_chunk_stall(plan, contract))
            detail = (
                f"i={i} seed={seed:#x} mode={label} p99_ms={p99_ms!r} "
                f"codes={capture.error_codes!r} latch={capture.latch_reason!r}"
            )
            assert capture.timeout_cut is not None, f"no stall cut; {detail}"
            assert capture.timeout_cut.code == posture.STREAM_INTER_CHUNK_TIMEOUT, detail
            assert capture.latch_reason == posture.STREAM_INTER_CHUNK_TIMEOUT, detail
            assert not capture.content_frames, f"content emitted on a stall; {detail}"
            assert posture.STREAM_IDLE_TIMEOUT not in capture.error_codes, detail


# --------------------------------------------------------------------------- #
# 3.3 — write blocked > write_timeout in both modes.
# --------------------------------------------------------------------------- #


def test_blocked_write_is_cut_with_write_timeout_in_both_modes() -> None:
    """A single write that blocks past ``write_timeout_s`` is a write-timeout cut (3.3).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.3

    The downstream ``do_send`` never completes, so ``asyncio.wait_for`` cancels it after the derived
    ``write_timeout_s`` (microseconds at a tiny SLO) and the stream is cut with
    ``stream_write_timeout`` — in BOTH modes. A short clean stream keeps the inter-chunk gaps tiny
    so only the write bound can trip.
    """
    seed = _SEED ^ 0x33
    print(f"test_blocked_write seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    plans = {
        "strict": _strict_enforcing_plan(),
        "incremental": _incremental_plan(enforcing=True),
    }
    for i in range(_ITERATIONS):
        p99_ms = rng.uniform(0.0002, 0.02)
        contract = _tiny_contract(target_p99_ms=p99_ms)
        for label, plan in plans.items():
            clock = _StubClock()
            scenario = _Scenario(
                plan=plan,
                events=_content_events(1),
                gaps_s=[0.0, 0.0],
                block_writes=True,
            )
            capture = asyncio.run(_drive(scenario, contract=contract, clock=clock))
            detail = (
                f"i={i} seed={seed:#x} mode={label} p99_ms={p99_ms!r} "
                f"codes={capture.error_codes!r} latch={capture.latch_reason!r} "
                f"send_attempts={capture.send_attempts}"
            )
            assert capture.send_attempts >= 1, f"no write was attempted; {detail}"
            assert capture.timeout_cut is not None, f"blocked write did not cut; {detail}"
            assert capture.timeout_cut.code == posture.STREAM_WRITE_TIMEOUT, detail
            assert capture.latch_reason == posture.STREAM_WRITE_TIMEOUT, detail


# --------------------------------------------------------------------------- #
# 3.4 — max-duration + the four in-flight triggers; first-cut-wins.
# --------------------------------------------------------------------------- #


def test_inflight_triggers_cut_with_their_existing_codes() -> None:
    """Kill switch / key revoke / plan change / stale snapshot cut with their codes (3.4).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.4

    A withholding strict stream whose control source reports one fired trigger is cut at the first
    chunk boundary with that trigger's posture code (precedence: stale snapshot dominates, then
    max-duration, then kill, revoke, plan change). No content leaves. The idle clock is irrelevant
    here, so the fix must not change any of these.
    """
    cases = {
        posture.STREAM_KILLED: InFlightSnapshot(
            available=True, age_s=0.0, kill_switch_engaged=True, key_revoked=False,
            plan_changed=False,
        ),
        posture.STREAM_KEY_REVOKED: InFlightSnapshot(
            available=True, age_s=0.0, kill_switch_engaged=False, key_revoked=True,
            plan_changed=False,
        ),
        posture.STREAM_PLAN_CHANGED: InFlightSnapshot(
            available=True, age_s=0.0, kill_switch_engaged=False, key_revoked=False,
            plan_changed=True,
        ),
        posture.STREAM_SNAPSHOT_STALE: InFlightSnapshot(
            available=False, age_s=0.0, kill_switch_engaged=False, key_revoked=False,
            plan_changed=False,
        ),
    }
    for expected_code, snap in cases.items():
        contract = _contract()
        clock = _StubClock()
        scenario = _Scenario(
            plan=_strict_enforcing_plan(),
            events=_content_events(_SLOWSTREAM_CHUNKS),
            gaps_s=[_SLOWSTREAM_GAP_S] * (_SLOWSTREAM_CHUNKS + 1),
            control_source=_ScriptedControlSource(snap=snap),
        )
        capture = asyncio.run(_drive(scenario, contract=contract, clock=clock))
        detail = f"code={expected_code!r} latch={capture.latch_reason!r} frames={capture.frames!r}"
        assert capture.latch_reason == expected_code, detail
        assert not capture.content_frames, f"content emitted after an in-flight cut; {detail}"


def test_max_stream_duration_cuts_a_steady_strict_stream() -> None:
    """A strict stream driven to ``max_stream_duration_s`` is cut with the max-duration code (3.4).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.4

    Chunks arrive every 100 ms on the injected clock; after 300 s (the default-SLO bound) the
    in-flight control evaluates the wall-clock deadline at a chunk boundary and cuts with
    ``stream_max_duration``, even though the upstream is healthy and (after the fix) the idle clock
    would be refreshed throughout. No content leaves (strict withholds until final, which never
    comes).
    """
    contract = _contract()
    max_dur = contract.max_stream_duration_s()
    gap = 0.1
    # Enough non-final chunks to pass the deadline: a few extra boundaries beyond max_dur/gap.
    n = int(max_dur / gap) + 5
    events = [
        UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, f"t{i} "),), final=False) for i in range(n)
    ]
    clock = _StubClock()
    scenario = _Scenario(
        plan=_strict_enforcing_plan(),
        events=events,
        gaps_s=[gap] * n,
    )
    capture = asyncio.run(_drive(scenario, contract=contract, clock=clock))
    detail = f"latch={capture.latch_reason!r} codes={capture.error_codes!r} max_dur={max_dur!r}"
    assert capture.latch_reason == posture.STREAM_MAX_DURATION, detail
    assert not capture.content_frames, f"strict content leaked before max-duration cut; {detail}"


def test_kill_latch_is_first_cut_wins() -> None:
    """The one-way ``KillLatch`` keeps the first recorded reason (3.4).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.4

    A direct unit on the shared seam: the first ``kill`` sets the reason; a later ``kill`` with a
    different code is a no-op. The fix adds only a clock re-seed and cannot change this.
    """
    latch = KillLatch()
    latch.kill(posture.STREAM_KEY_REVOKED)
    latch.kill(posture.STREAM_MAX_DURATION)
    assert latch.is_killed()
    assert latch.reason() == posture.STREAM_KEY_REVOKED


# --------------------------------------------------------------------------- #
# 3.5 — routed first_event_s bound + idle re-seed after the first event.
# --------------------------------------------------------------------------- #


async def _first_event_seed_after(contract: ResourceContract, first_event_s: float) -> float:
    """Return ``_last_write_s`` after the first ``next_event`` returns on a routed stream."""
    clock = _StubClock()
    kill_latch = KillLatch()
    timeouts = StreamTimeouts(
        contract=contract, kill_latch=kill_latch, clock=clock, first_event_s=first_event_s,
    )
    seed_at_start = timeouts._last_write_s  # noqa: SLF001

    async def _event() -> str:
        clock.now += first_event_s * 0.5  # a slow but within-bound first event
        return "event"

    await timeouts.next_event(_event())
    return timeouts._last_write_s - seed_at_start  # noqa: SLF001


def test_routed_first_event_bound_and_d18_idle_reseed() -> None:
    """The routed ``first_event_s`` bound is honoured and D-18 re-seeds the idle clock (3.5).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.5

    Two observations the fix must preserve:
    1. A first event slower than ``inter_chunk_timeout_s`` but within ``first_event_s`` does NOT
       trip the inter-chunk bound (the wider first-event budget applies).
    2. After the first event returns, the idle clock is re-seeded to the clock reading at that
       point (the GW28 D-18 branch), so the delta from the stream-start seed equals the first
       event's elapsed time.
    """
    seed = _SEED ^ 0x35
    print(f"test_routed_first_event seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        p99_ms = rng.uniform(1.0, 50.0)
        contract = _contract(target_p99_ms=p99_ms)
        first_event_s = contract.routing_pre_content_budget_s(contract.routing_max_attempts())
        inter_chunk = contract.inter_chunk_timeout_s()
        detail = f"i={i} seed={seed:#x} p99_ms={p99_ms!r} first_event_s={first_event_s!r}"
        # The first-event budget must be wider than the plain inter-chunk bound for this to matter.
        assert first_event_s > inter_chunk, f"first_event_s not wider than inter_chunk; {detail}"
        reseed_delta = asyncio.run(_first_event_seed_after(contract, first_event_s))
        # D-18: the idle clock jumped forward by the first event's elapsed time (half the budget).
        assert reseed_delta == first_event_s * 0.5, f"D-18 re-seed delta wrong; {detail}"


# --------------------------------------------------------------------------- #
# 3.6 — strict block / redact / overflow / kill frames, codes, metrics.
# --------------------------------------------------------------------------- #


def _ip_finding(text: str) -> Finding:
    """A completed EXECUTED ``ip_leak`` finding spanning the whole text (resolver → BLOCK)."""
    return Finding(
        detector="ip_leak",
        detector_version="t",
        category=Category.IP_LEAK,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(Span(start=0, end=len(text)),),
        evidence=None,
    )


def _aws_finding(text: str) -> Finding:
    """A completed EXECUTED ``aws`` finding spanning the whole text (resolver → REDACT)."""
    return Finding(
        detector="aws",
        detector_version="t",
        category=Category.SECRET,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(Span(start=0, end=len(text)),),
        evidence=None,
    )


def test_strict_block_emits_output_blocked_with_zero_content_before_it() -> None:
    """A strict BLOCK emits a single terminal ``output_blocked`` frame, zero content first (3.6).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.6

    A detector override marks the whole buffer as an ``ip_leak`` (BLOCK). The strict pipeline
    sanitizes every channel before emitting any byte, so the BLOCK yields exactly one terminal
    ``output_blocked`` frame and NO content frame. The block counter is bumped. The fix never
    reaches ``_finalize``, so this is untouched.
    """
    contract = _contract()
    strict_metrics = StrictMetrics()
    clock = _StubClock()
    plan = _strict_enforcing_plan()
    pipeline = _build_pipeline_with_metrics(plan, contract, strict_metrics)
    pipeline._detector = lambda text: (_ip_finding(text),)  # type: ignore[attr-defined] # noqa: SLF001, E731
    capture = asyncio.run(
        _drive_prebuilt(pipeline, plan, _content_events(3), contract=contract, clock=clock)
    )
    detail = f"codes={capture.error_codes!r} content={capture.released!r}"
    assert capture.error_codes == ["output_blocked"], detail
    assert not capture.content_frames, f"content emitted before an output_blocked; {detail}"
    assert strict_metrics.snapshot().blocked_total >= 1, f"block counter not bumped; {detail}"


def test_strict_redact_masks_the_whole_response() -> None:
    """A strict REDACT masks the matched bytes and delivers a content frame, no block (3.6).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.6

    An ``aws`` match resolves to REDACT; the strict pipeline emits one content frame with the span
    replaced by the redaction placeholder and no terminal error frame. This is the redact-path
    frame shape the fix must preserve.
    """
    contract = _contract()
    clock = _StubClock()
    plan = _strict_redact_plan()
    pipeline = _build_pipeline(plan, contract)
    pipeline._detector = lambda text: (_aws_finding(text),)  # type: ignore[attr-defined] # noqa: SLF001, E731
    capture = asyncio.run(
        _drive_prebuilt(pipeline, plan, _content_events(2), contract=contract, clock=clock)
    )
    detail = f"codes={capture.error_codes!r} content={capture.released!r}"
    assert not capture.error_codes, f"a redact must not emit a terminal error frame; {detail}"
    assert capture.content_frames, f"redact emitted no content frame; {detail}"
    assert "[REDACTED]" in capture.released, f"redaction placeholder missing; {detail}"


def test_strict_kill_emits_killed_with_zero_content() -> None:
    """A strict in-flight kill emits the ``killed`` terminal, no content (3.6, 3.4).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.6, 3.4

    The control source reports a kill switch; the strict pipeline cuts at the first chunk boundary
    with the ``killed`` terminal (mapped to ``stream_killed`` on the latch) and releases no content.
    """
    contract = _contract()
    clock = _StubClock()
    scenario = _Scenario(
        plan=_strict_enforcing_plan(),
        events=_content_events(3),
        gaps_s=[_SLOWSTREAM_GAP_S] * 4,
        control_source=_ScriptedControlSource(
            snap=InFlightSnapshot(
                available=True, age_s=0.0, kill_switch_engaged=True, key_revoked=False,
                plan_changed=False,
            ),
        ),
    )
    capture = asyncio.run(_drive(scenario, contract=contract, clock=clock))
    detail = f"codes={capture.error_codes!r} latch={capture.latch_reason!r}"
    # The pipeline emits the raw ``killed`` terminal frame; the latch carries ``stream_killed``.
    assert "killed" in capture.error_codes, detail
    assert capture.latch_reason == posture.STREAM_KILLED, detail
    assert not capture.content_frames, f"content emitted on a kill; {detail}"


# -- helpers for the metric-observing strict cases -------------------------- #


def _build_pipeline_with_metrics(
    plan: ExecutionPlan, contract: ResourceContract, strict_metrics: StrictMetrics,
):  # type: ignore[no-untyped-def]
    """Build the strict pipeline with a caller-supplied ``StrictMetrics`` so counters are seen."""

    async def _inline(fn):  # type: ignore[no-untyped-def]
        return fn()

    cfg, _ = load_holdback_config({})
    out = build_output_detectors(
        plan, base=None, ticket=None, context_chunks=(), backend=None,
        contract=contract, runner=_inline,  # type: ignore[arg-type]
    )
    return select_pipeline(
        plan,
        scanner=holdback_scan,
        token_index=TokenIndex(),
        cfg=cfg,
        metrics=HoldbackMetrics(),
        window=max_pattern_length(),
        resolver=PlanResolver(plan=plan, phase=Phase.OUTPUT),
        detector=out.detector_for(plan.streaming_mode),
        max_buffer_bytes=1 << 24,
        strict_metrics=strict_metrics,
        delta_detector=out.canary_delta,
        final_detector=out.final,
        sync_runner=out.runner,
    )


async def _drive_prebuilt(
    pipeline,  # type: ignore[no-untyped-def]
    plan: ExecutionPlan,
    events: list[UpstreamEvent],
    *,
    contract: ResourceContract,
    clock: _StubClock,
) -> _Capture:
    """Drive a PRE-BUILT pipeline (so a detector override / shared metrics stay in place)."""
    executor = ScanExecutor(contract=contract)
    try:
        kill_latch = KillLatch()
        control = InFlightControl(
            source=_FreshControlSource(), contract=contract, clock=clock, kill_latch=kill_latch,
        )
        timeouts = StreamTimeouts(contract=contract, kill_latch=kill_latch, clock=clock)
        coalescer = Coalescer(contract=contract, active_streams=lambda: 1)
        capture = _Capture()

        async def _provider() -> AsyncIterator[UpstreamEvent]:
            for event in events:
                clock.now += _SLOWSTREAM_GAP_S
                yield event

        async def _send(frame: DownstreamFrame) -> None:
            if frame.error_code is not None:
                capture.frames.append(frame)
                return
            await timeouts.send(partial(_record, capture, frame))

        chunks = as_chunks(
            _provider(), coalescer, control, timeouts,
            error_scan=ErrorFrameScan(
                scanner=_no_findings,
                resolver=PlanResolver(plan=plan, phase=Phase.OUTPUT),
                kill_latch=kill_latch,
                executor=executor,
            ),
        )
        try:
            await pipeline.run(chunks, _send, kill_latch)
        except StreamTimeout as exc:
            capture.timeout_cut = exc
        capture.latch_reason = kill_latch.reason()
        return capture
    finally:
        executor.shutdown()


# --------------------------------------------------------------------------- #
# 3.7 — pass-through strict keeps the write-based idle model.
# --------------------------------------------------------------------------- #


def test_passthrough_strict_keeps_the_write_based_idle_model() -> None:
    """Pass-through strict (no enforcing rule) still cuts on a write-based idle gap (3.7).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.7

    A STRICT_WITHHOLD plan with no enforcing output rule writes each chunk as it arrives (the
    pass-through model, identical to INCREMENTAL). A client that stops accepting bytes (clock
    advanced past ``idle_timeout_s`` before the second write) is still cut with
    ``stream_idle_timeout`` — pass-through strict is NOT withholding, so the liveness fix must not
    touch it.
    """
    seed = _SEED ^ 0x37
    print(f"test_passthrough_strict seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        contract = _contract()
        idle = contract.idle_timeout_s()
        overshoot = rng.uniform(1e-6, idle)
        clock = _StubClock()
        scenario = _Scenario(
            plan=_strict_passthrough_plan(),
            events=_content_events(2),
            gaps_s=[0.0, 0.0, 0.0],
            send_advance_s=idle + overshoot,
        )
        capture = asyncio.run(_drive(scenario, contract=contract, clock=clock))
        detail = (
            f"i={i} seed={seed:#x} overshoot={overshoot!r} codes={capture.error_codes!r} "
            f"latch={capture.latch_reason!r}"
        )
        assert capture.timeout_cut is not None, f"pass-through strict idle did not cut; {detail}"
        assert capture.timeout_cut.code == posture.STREAM_IDLE_TIMEOUT, detail
        assert capture.latch_reason == posture.STREAM_IDLE_TIMEOUT, detail


# --------------------------------------------------------------------------- #
# 3.7 control — pass-through strict with a healthy client delivers every chunk.
# --------------------------------------------------------------------------- #


def test_passthrough_strict_healthy_client_delivers_each_chunk() -> None:
    """Pass-through strict with a reading client delivers each chunk as content (3.7 control).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.7

    With tiny inter-write gaps (a client that keeps reading) a pass-through strict stream writes
    every chunk and is never cut — the write-based model behaving normally.
    """
    contract = _contract()
    clock = _StubClock()
    scenario = _Scenario(
        plan=_strict_passthrough_plan(),
        events=_content_events(5),
        gaps_s=[0.0] * 6,
        send_advance_s=0.0,
    )
    capture = asyncio.run(_drive(scenario, contract=contract, clock=clock))
    detail = f"codes={capture.error_codes!r} latch={capture.latch_reason!r}"
    assert capture.timeout_cut is None, f"healthy pass-through strict was cut; {detail}"
    assert not capture.error_codes, detail
    assert capture.content_frames, f"pass-through delivered no content; {detail}"
