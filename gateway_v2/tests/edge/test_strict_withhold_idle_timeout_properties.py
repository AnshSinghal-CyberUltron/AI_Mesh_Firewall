"""The four correctness properties of the ``strict-withhold-idle-timeout`` bugfix (task 3.4).

# Feature: strict-withhold-idle-timeout, Properties 1-4
# Validates: Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8

This suite is the task-3.4 property-based proof that the shipped fix (tasks 3.1-3.3) satisfies
design.md "Correctness Properties" 1-4 across a randomised input domain. The fix adds a per-stream
``upstream_liveness`` flag to ``StreamTimeouts`` (``edge/stream_timeouts.py``), decided once in
``ChatStream._controls`` from ``withholds_until_decision(plan)`` (``egress/output_guard.py``). When
set, every upstream event ``next_event`` returns re-seeds the idle clock, so a withholding strict
stream is no longer blamed for the silence it creates on purpose.

Each property drives the FIXED code through the shipped seams exactly as the production serving
loop does: a REAL ``StreamTimeouts`` from a ``ResourceContract``, a REAL pipeline from
``select_pipeline``, and the shipped ``as_chunks`` adapter, with the liveness flag set the same way
``ChatStream._controls`` sets it -- ``upstream_liveness=withholds_until_decision(plan)``. Reusing
that one predicate (rather than hard-coding ``True``) is what pins these tests to the production
wiring: a strict-enforcing plan turns liveness on, every other plan leaves it off.

Determinism and runtime. The two clock-based bounds (idle, max-duration) are driven with an
injected ``_StubClock`` so they are exact and take zero wall time, even for a 250 s stream. The two
REAL-time ``asyncio.wait_for`` bounds (inter-chunk, write) are driven exactly as the shipped LGW12
property test (``tests/runtime/test_lgw12_timeouts.py``) and the task-2 preservation suite do: a
tiny ``target_p99_ms`` makes the derived bound tens of microseconds, and a never-resolving
awaitable / never-completing write trips it in a couple of real sub-millisecond waits. So every
property runs >= 100 iterations in well under a second of wall time, with no multi-second sleep.

House idiom: a module-constant seed, ``random.Random``, >= 100 iterations per property,
``asyncio.run`` per async case, no ``hypothesis``; each property tagged in source with a
``# Feature`` / ``Property N`` / ``# Validates`` comment block.

Layering: a test file (outside the import-linter contract), so it imports the concrete
``detect`` / ``egress`` / ``edge`` seams directly and injects them, exactly as the task-1/2
harnesses and the GW33 offline harness (``tests/e2e/gw33_support.py``) do.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable
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
    StreamingMode,
    Surface,
    posture,
)
from gateway_v2.edge.asgi_sse import ErrorFrameScan, as_chunks
from gateway_v2.edge.cancel import (
    InFlightControl,
    InFlightControlSource,
    InFlightSnapshot,
    KillLatch,
)
from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.edge.output_detectors import build_output_detectors
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.output_guard import withholds_until_decision
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

#: Seeded RNG base (logged + interpolated into failure messages; XORed per property).
_SEED = 0xF1_3D4_9A7
#: Iterations per property (house idiom: >= 100).
_ITERATIONS = 150
_CONTENT_CHANNEL = "output_content"
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
# Plans.
# --------------------------------------------------------------------------- #


def _ip_leak_condition() -> RuleCondition:
    return RuleCondition(RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", "output.ip_leak", "")


def _enforcing_out_rule(priority: int = 200) -> Rule:
    """A non-empty ``enforcing_output_rules`` member (OUTPUT / ENFORCE / BLOCK)."""
    return Rule(
        "out_ip_leak", Category.IP_LEAK, Mode.ENFORCE, Action.BLOCK, None, priority,
        RuleScope.OUTPUT, FailurePosture.FAIL_CLOSED, frozenset({Surface.CHAT}),
        _ip_leak_condition(), "[internal address]",
    )


def _redact_out_rule() -> Rule:
    """An enforcing OUTPUT rule with action REDACT."""
    condition = RuleCondition(RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", "", "")
    return Rule(
        "out_secret_redact", Category.SECRET, Mode.ENFORCE, Action.REDACT, None, 200,
        RuleScope.OUTPUT, FailurePosture.FAIL_CLOSED, frozenset({Surface.CHAT}), condition, "",
    )


def _flag_out_rule() -> Rule:
    """A non-enforcing OUTPUT rule (FLAG action): never promotes a strict plan to withholding."""
    condition = RuleCondition(RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", "output.ip_leak", "")
    return Rule(
        "out_flag", Category.IP_LEAK, Mode.ENFORCE, Action.FLAG, None, 200,
        RuleScope.OUTPUT, FailurePosture.FAIL_OPEN, frozenset({Surface.CHAT}), condition, "",
    )


def _strict_enforcing_plan(*, redact: bool = False) -> ExecutionPlan:
    """STRICT_WITHHOLD with one enforcing OUTPUT rule (the D-20 withholding shape)."""
    rule = _redact_out_rule() if redact else _enforcing_out_rule()
    plan = ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, (rule,), frozenset(),
        StreamingMode.STRICT_WITHHOLD, True,
    )
    assert withholds_until_decision(plan), "fixture must withhold until the decision"
    return plan


def _strict_passthrough_plan() -> ExecutionPlan:
    """STRICT_WITHHOLD with NO enforcing output rule (pass-through; 3.7)."""
    plan = ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, (), frozenset(),
        StreamingMode.STRICT_WITHHOLD, True,
    )
    assert not withholds_until_decision(plan), "pass-through strict must not withhold"
    return plan


def _strict_flag_only_plan() -> ExecutionPlan:
    """STRICT_WITHHOLD whose only OUTPUT rule is a non-enforcing FLAG (pass-through; 3.7)."""
    plan = ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, (_flag_out_rule(),), frozenset(),
        StreamingMode.STRICT_WITHHOLD, True,
    )
    assert not withholds_until_decision(plan), "flag-only strict must not withhold"
    return plan


def _incremental_plan(*, enforcing: bool) -> ExecutionPlan:
    """INCREMENTAL plan, optionally with an enforcing output rule."""
    rules = (_enforcing_out_rule(),) if enforcing else ()
    plan = ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, rules, frozenset(),
        StreamingMode.INCREMENTAL, True,
    )
    assert not withholds_until_decision(plan), "INCREMENTAL never withholds"
    return plan


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
# Capture + pipeline builder (shared with the task-1 / task-2 harness style).
# --------------------------------------------------------------------------- #


@dataclass
class _Capture:
    """Records emitted frames, the cut (if any), and bookkeeping for the assertions."""

    frames: list[DownstreamFrame] = field(default_factory=list)
    timeout_cut: StreamTimeout | None = None
    latch_reason: str | None = None
    first_send_gap_s: float | None = None
    send_attempts: int = 0
    cut_clock_s: float | None = None

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
        contract=contract, runner=_inline,
    )
    return select_pipeline(
        plan,
        scanner=holdback_scan,  # type: ignore[arg-type]
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


def _content_events(n: int) -> list[UpstreamEvent]:
    """``n`` clean content events then a final event (plain prose, resolves ALLOW)."""
    events = [
        UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, f"token{i} "),), final=False)
        for i in range(n)
    ]
    events.append(UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, "done."),), final=True))
    return events


@dataclass
class _Scenario:
    """One stream scenario driven through ``as_chunks`` + the selected pipeline.

    ``gaps_s[i]`` is the injected-clock advance applied just before upstream event ``i`` is
    yielded. ``liveness`` is set the way ``ChatStream._controls`` sets it:
    ``withholds_until_decision(plan)``.
    """

    plan: ExecutionPlan
    events: list[UpstreamEvent]
    gaps_s: list[float]
    control_source: InFlightControlSource = field(default_factory=_FreshControlSource)
    first_event_s: float | None = None
    send_advance_s: float = 0.0
    detector_override: object | None = None
    block_writes: bool = False
    #: Override the liveness flag; by default it is derived from the plan like production.
    liveness_override: bool | None = None


async def _drive(scenario: _Scenario, *, contract: ResourceContract, clock: _StubClock) -> _Capture:
    """Drive the scenario; content ``send`` goes through ``StreamTimeouts.send`` like the route."""
    executor = ScanExecutor(contract=contract)
    try:
        plan = scenario.plan
        liveness = (
            withholds_until_decision(plan)
            if scenario.liveness_override is None
            else scenario.liveness_override
        )
        pipeline = _build_pipeline(plan, contract)
        if scenario.detector_override is not None:
            pipeline._detector = scenario.detector_override  # noqa: SLF001
        kill_latch = KillLatch()
        control = InFlightControl(
            source=scenario.control_source, contract=contract, clock=clock, kill_latch=kill_latch,
        )
        timeouts = StreamTimeouts(
            contract=contract, kill_latch=kill_latch, clock=clock,
            first_event_s=scenario.first_event_s, upstream_liveness=liveness,
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
            capture.cut_clock_s = clock.now
        capture.latch_reason = kill_latch.reason()
        return capture
    finally:
        executor.shutdown()


async def _record(capture: _Capture, frame: DownstreamFrame) -> None:
    capture.frames.append(frame)


# --------------------------------------------------------------------------- #
# Property 1 — a healthy withheld strict stream is never cut as client idle.
# --------------------------------------------------------------------------- #


def test_property_1_healthy_withheld_strict_stream_is_never_cut_as_idle() -> None:
    """Random strict-enforcing streams within the stall bound are delivered, never idle-cut.

    # Feature: strict-withhold-idle-timeout, Property 1: Healthy withheld strict stream delivered
    # Validates: Requirements 2.1, 2.2

    For a withholding strict plan (liveness on, as ``ChatStream._controls`` sets it) with every
    upstream inter-event gap <= ``inter_chunk_timeout_s`` and a total withhold time sampled from
    0.1 s to 250 s on the injected clock, with a client that always accepts bytes: the stream is
    NEVER cut with ``stream_idle_timeout`` and the decided (here: sanitized, ALLOW) content frame
    is delivered. This is the fix working across the whole input domain of the bug condition.
    """
    seed = _SEED ^ 0x01
    print(f"test_property_1 seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        contract = _contract()
        inter_chunk = contract.inter_chunk_timeout_s()
        idle = contract.idle_timeout_s()
        # A random number of content chunks, each gap in (0, inter_chunk], scaled so the TOTAL
        # withhold time lands in [0.1, 250] s -- well past idle (3 s) for most draws.
        n_chunks = rng.randint(1, 60)
        total_s = rng.uniform(0.1, 250.0)
        # Even gaps that stay within the stall bound and sum (roughly) to the target total.
        base_gap = min(inter_chunk, max(total_s / max(n_chunks, 1), 1e-6))
        gaps = [min(inter_chunk, max(rng.uniform(0.0, base_gap), 1e-9)) for _ in range(n_chunks)]
        gaps.append(min(inter_chunk, base_gap))  # the final event's gap
        plan = _strict_enforcing_plan()
        clock = _StubClock()
        scenario = _Scenario(
            plan=plan, events=_content_events(n_chunks), gaps_s=gaps, send_advance_s=0.0,
        )
        capture = asyncio.run(_drive(scenario, contract=contract, clock=clock))
        detail = (
            f"i={i} seed={seed:#x} n_chunks={n_chunks} total~{sum(gaps):.3f}s idle={idle}s "
            f"first_gap={capture.first_send_gap_s!r} codes={capture.error_codes!r} "
            f"latch={capture.latch_reason!r}"
        )
        assert withholds_until_decision(plan), detail
        assert capture.timeout_cut is None, f"a healthy withheld stream was cut; {detail}"
        assert posture.STREAM_IDLE_TIMEOUT not in capture.error_codes, detail
        assert capture.latch_reason != posture.STREAM_IDLE_TIMEOUT, detail
        assert capture.content_frames, f"decided content was not delivered; {detail}"
        assert capture.released, f"no bytes were released; {detail}"


# --------------------------------------------------------------------------- #
# Property 2 — a stalled strict upstream is still cut by the upstream code.
# --------------------------------------------------------------------------- #


async def _check_single_stall(
    plan: ExecutionPlan,
    contract: ResourceContract,
    *,
    pre_gaps: list[float],
) -> _Capture:
    """A provider that yields ``len(pre_gaps)`` quick events then stalls forever on the next.

    The stall is the one inter-event gap that exceeds ``inter_chunk_timeout_s``: the next
    ``next_event`` never resolves, so ``asyncio.wait_for`` cancels it and the stream is cut with
    ``stream_inter_chunk_timeout``. Liveness is set from the plan, proving a re-seed on the earlier
    events cannot mask the stall (inter-chunk is stricter than idle and ``next_event`` owns it).
    """
    executor = ScanExecutor(contract=contract)
    try:
        liveness = withholds_until_decision(plan)
        pipeline = _build_pipeline(plan, contract)
        kill_latch = KillLatch()
        clock = _StubClock()
        control = InFlightControl(
            source=_FreshControlSource(), contract=contract, clock=clock, kill_latch=kill_latch,
        )
        timeouts = StreamTimeouts(
            contract=contract, kill_latch=kill_latch, clock=clock, upstream_liveness=liveness,
        )
        coalescer = Coalescer(contract=contract, active_streams=lambda: 1)
        capture = _Capture()

        async def _provider() -> AsyncIterator[UpstreamEvent]:
            for gap in pre_gaps:
                clock.now += gap
                yield UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, "x "),), final=False)
            await asyncio.Event().wait()  # the stall: this next event never arrives
            yield UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, "unreachable"),), final=True)

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


def test_property_2_stalled_strict_upstream_is_cut_with_inter_chunk_timeout() -> None:
    """A strict stream with one gap over the stall bound is cut with the upstream code, no content.

    # Feature: strict-withhold-idle-timeout, Property 2: Stalled strict upstream still cut
    # Validates: Requirements 2.3

    A withholding strict stream (liveness on) that delivers a random number of quick events then
    has one upstream gap exceeding ``inter_chunk_timeout_s`` is ALWAYS cut with
    ``stream_inter_chunk_timeout`` -- never ``stream_idle_timeout`` -- emits zero content frames,
    and releases resources (the ``ScanExecutor`` is shut down in ``_check_single_stall``'s
    ``finally``, and the breaching coroutine raises so the serving loop unwinds). A tiny sub-ms SLO
    makes the real ``wait_for`` bound microseconds, so the single real stall is a sub-ms wait.
    """
    seed = _SEED ^ 0x02
    print(f"test_property_2 seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        p99_ms = rng.uniform(0.0002, 0.02)
        contract = _contract(target_p99_ms=p99_ms)
        inter_chunk = contract.inter_chunk_timeout_s()
        # A few quick pre-stall events, each gap strictly within the stall bound.
        n_pre = rng.randint(0, 5)
        pre_gaps = [rng.uniform(0.0, inter_chunk * 0.5) for _ in range(n_pre)]
        plan = _strict_enforcing_plan(redact=rng.random() < 0.5)
        capture = asyncio.run(_check_single_stall(plan, contract, pre_gaps=pre_gaps))
        detail = (
            f"i={i} seed={seed:#x} p99_ms={p99_ms!r} n_pre={n_pre} inter_chunk={inter_chunk!r} "
            f"codes={capture.error_codes!r} latch={capture.latch_reason!r}"
        )
        assert capture.timeout_cut is not None, f"the stall did not cut; {detail}"
        assert capture.timeout_cut.code == posture.STREAM_INTER_CHUNK_TIMEOUT, detail
        assert capture.latch_reason == posture.STREAM_INTER_CHUNK_TIMEOUT, detail
        assert posture.STREAM_IDLE_TIMEOUT not in capture.error_codes, detail
        assert not capture.content_frames, f"content leaked on a stall; {detail}"


# --------------------------------------------------------------------------- #
# Property 3 — client back-pressure after the decision is still detected.
# --------------------------------------------------------------------------- #


def test_property_3a_blocked_decision_write_is_cut_with_write_timeout() -> None:
    """A withheld strict stream whose decision write blocks is cut with ``stream_write_timeout``.

    # Feature: strict-withhold-idle-timeout, Property 3: Back-pressure after the decision detected
    # Validates: Requirements 2.4

    After a healthy withheld accumulation (liveness refreshed the idle clock throughout, so the
    first write is NOT an idle cut), the single decision ``send`` never completes. The write bound
    (``write_timeout_s``, microseconds at a tiny SLO) trips first and the stream is cut with
    ``stream_write_timeout`` -- client back-pressure once writing begins is still caught. A random
    number of in-bound quick upstream gaps precede the decision.
    """
    seed = _SEED ^ 0x03
    print(f"test_property_3a seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        p99_ms = rng.uniform(0.0002, 0.02)
        contract = _contract(target_p99_ms=p99_ms)
        inter_chunk = contract.inter_chunk_timeout_s()
        n_chunks = rng.randint(1, 8)
        gaps = [rng.uniform(0.0, inter_chunk * 0.5) for _ in range(n_chunks + 1)]
        plan = _strict_enforcing_plan()
        clock = _StubClock()
        scenario = _Scenario(
            plan=plan, events=_content_events(n_chunks), gaps_s=gaps, block_writes=True,
        )
        capture = asyncio.run(_drive(scenario, contract=contract, clock=clock))
        detail = (
            f"i={i} seed={seed:#x} p99_ms={p99_ms!r} n_chunks={n_chunks} "
            f"codes={capture.error_codes!r} latch={capture.latch_reason!r} "
            f"send_attempts={capture.send_attempts} first_gap={capture.first_send_gap_s!r}"
        )
        assert capture.send_attempts >= 1, f"the decision write was never attempted; {detail}"
        assert capture.timeout_cut is not None, f"the blocked write did not cut; {detail}"
        assert capture.timeout_cut.code == posture.STREAM_WRITE_TIMEOUT, detail
        assert capture.latch_reason == posture.STREAM_WRITE_TIMEOUT, detail


def test_property_3b_inter_write_idle_gap_after_decision_is_cut_with_idle() -> None:
    """A withheld strict stream whose inter-write gap after the decision exceeds idle is idle-cut.

    # Feature: strict-withhold-idle-timeout, Property 3: Back-pressure after the decision detected
    # Validates: Requirements 2.4

    Once writing begins, the idle clock is write-based again (``send`` re-seeds on a completed
    write and no further upstream event arrives to refresh it). A unit on the shipped
    ``StreamTimeouts`` with the liveness flag ON proves the post-decision idle check is still live:
    a first write succeeds, then the injected clock advances past ``idle_timeout_s`` before the
    second write, which is cut with ``stream_idle_timeout``. This is the back-pressure guarantee
    the fix must NOT weaken -- liveness only refreshes on ``next_event``, never inside ``send``.
    """
    seed = _SEED ^ 0x13
    print(f"test_property_3b seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        contract = _contract()
        idle = contract.idle_timeout_s()
        overshoot = rng.uniform(1e-6, idle)
        clock = _StubClock()
        kill_latch = KillLatch()
        timeouts = StreamTimeouts(
            contract=contract, kill_latch=kill_latch, clock=clock, upstream_liveness=True,
        )

        async def _noop() -> None:
            return None

        async def _run() -> StreamTimeout | None:
            await timeouts.send(_noop)  # first decision write: accepted
            clock.now += idle + overshoot  # the client stops draining between writes
            try:
                await timeouts.send(_noop)  # second write: idle gap now exceeds the bound
            except StreamTimeout as exc:
                return exc
            return None

        cut = asyncio.run(_run())
        detail = (
            f"i={i} seed={seed:#x} overshoot={overshoot!r} idle={idle!r} "
            f"cut={cut!r} latch={kill_latch.reason()!r}"
        )
        assert cut is not None, f"the post-decision idle gap did not cut; {detail}"
        assert cut.code == posture.STREAM_IDLE_TIMEOUT, detail
        assert kill_latch.reason() == posture.STREAM_IDLE_TIMEOUT, detail


# --------------------------------------------------------------------------- #
# Property 4 — streams outside the bug condition are identical to the oracle.
# --------------------------------------------------------------------------- #


#: A scenario maker: given a contract, build the (scenario, clock) pair for one run. Uniform
#: signature so the FIXED and the oracle runs rebuild an independent, identical scenario.
_ScenarioMaker = Callable[[ResourceContract], tuple["_Scenario", "_StubClock"]]


def _idle_cut_scenario(
    plan: ExecutionPlan, send_advance_s: float, contract: ResourceContract,
) -> tuple[_Scenario, _StubClock]:
    """A write-based idle cut: a client that stops draining ``send_advance_s`` before each write."""
    return (
        _Scenario(
            plan=plan, events=_content_events(2), gaps_s=[0.0, 0.0, 0.0],
            send_advance_s=send_advance_s,
        ),
        _StubClock(),
    )


def _healthy_scenario(
    plan: ExecutionPlan, n_chunks: int, contract: ResourceContract,
) -> tuple[_Scenario, _StubClock]:
    """A healthy stream with a reading client: every chunk delivered, never cut."""
    return (
        _Scenario(
            plan=plan, events=_content_events(n_chunks), gaps_s=[0.0] * (n_chunks + 1),
            send_advance_s=0.0,
        ),
        _StubClock(),
    )


def _short_strict_scenario(
    gap_s: float, n_chunks: int, contract: ResourceContract,
) -> tuple[_Scenario, _StubClock]:
    """A withheld strict stream whose whole withhold fits inside the idle bound (no cut)."""
    return (
        _Scenario(
            plan=_strict_enforcing_plan(), events=_content_events(n_chunks),
            gaps_s=[gap_s] * (n_chunks + 1), send_advance_s=0.0,
        ),
        _StubClock(),
    )


def _inflight_trigger_scenario(
    snap: InFlightSnapshot, contract: ResourceContract,
) -> tuple[_Scenario, _StubClock]:
    """A strict stream whose control source reports a fired trigger (cut at a chunk boundary)."""
    return (
        _Scenario(
            plan=_strict_enforcing_plan(), events=_content_events(8), gaps_s=[0.1] * 9,
            control_source=_ScriptedControlSource(snap=snap),
        ),
        _StubClock(),
    )


def _max_duration_strict_scenario(contract: ResourceContract) -> tuple[_Scenario, _StubClock]:
    """A strict stream driven to ``max_stream_duration_s`` with the idle clock refreshed at 100 ms.

    Chunks arrive every 100 ms and never go ``final``; after ``max_stream_duration_s`` the
    in-flight control cuts at a chunk boundary with ``stream_max_duration`` even though liveness
    refreshes the idle clock throughout (Decision 3: the duration bound is independent of idle).
    """
    max_dur = contract.max_stream_duration_s()
    gap = 0.1
    n = int(max_dur / gap) + 5
    events = [
        UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, f"t{i} "),), final=False) for i in range(n)
    ]
    scenario = _Scenario(
        plan=_strict_enforcing_plan(), events=events, gaps_s=[gap] * n,
    )
    return scenario, _StubClock()


def _run_scenario_twice(
    make_scenario: _ScenarioMaker,
    contract: ResourceContract,
) -> tuple[_Capture, _Capture]:
    """Run a scenario under the FIXED wiring (liveness from the plan) and under the oracle (off).

    The oracle is a run with ``upstream_liveness=False`` -- the unfixed-behavior path for a
    non-bug-condition input (design.md "Preservation Checking": the False run is the recorded
    oracle). For every input OUTSIDE the bug condition the two runs must be identical.
    """
    scenario_fixed, clock_fixed = make_scenario(contract)
    fixed = asyncio.run(_drive(scenario_fixed, contract=contract, clock=clock_fixed))
    scenario_oracle, clock_oracle = make_scenario(contract)
    scenario_oracle.liveness_override = False
    oracle = asyncio.run(_drive(scenario_oracle, contract=contract, clock=clock_oracle))
    return fixed, oracle


def _summary(capture: _Capture) -> tuple[object, ...]:
    """The observable tuple the preservation property compares: code, latch, frame shape."""
    cut_code = capture.timeout_cut.code if capture.timeout_cut is not None else None
    frame_shape = tuple(
        (f.error_code, tuple(f.text_deltas)) for f in capture.frames
    )
    return (cut_code, capture.latch_reason, tuple(capture.error_codes), frame_shape)


def test_property_4_non_bug_inputs_match_the_unfixed_oracle() -> None:
    """Every input outside the bug condition is byte-for-byte identical to the oracle.

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation outside the bug condition
    # Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8

    The input domain is sampled across the non-bug-condition families named in design.md
    Property 4: INCREMENTAL (idle-cut and healthy), pass-through strict (flag-only and no-rule,
    idle-cut and healthy), short withheld strict inside the idle bound, a kill / revoke / plan /
    stale in-flight trigger, and a max-duration cut with the idle clock refreshed every 100 ms. For
    each draw the FIXED wiring (liveness derived from the plan, as ``ChatStream._controls`` does)
    is compared against the ``upstream_liveness=False`` oracle: identical cut code, latch reason,
    error codes and frame shape. The 3.8 derived bounds are checked to be the pinned multiples of
    the SLO, unchanged by the fix.
    """
    seed = _SEED ^ 0x04
    print(f"test_property_4 seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)

    # 3.8: the four derived bounds are the pinned multiples of ``target_p99_ms``, independent of
    # the fix (the fix never touches ``ResourceContract``). Checked over random SLOs.
    for _ in range(_ITERATIONS):
        p99_ms = rng.uniform(0.01, 500.0)
        c = _contract(target_p99_ms=p99_ms)
        p99_s = p99_ms / 1000.0
        assert c.idle_timeout_s() == max(p99_s * 150.0, p99_s)
        assert c.inter_chunk_timeout_s() == max(p99_s * 50.0, p99_s)
        assert c.write_timeout_s() == max(p99_s * 25.0, p99_s)
        assert c.max_stream_duration_s() == max(p99_s * 15000.0, p99_s)

    _triggers = {
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
    _trigger_items = list(_triggers.items())

    families = (
        "incremental_idle",
        "incremental_healthy",
        "passthrough_flag_idle",
        "passthrough_norule_healthy",
        "short_strict",
        "inflight_trigger",
        "max_duration",
    )

    for i in range(_ITERATIONS):
        family = rng.choice(families)
        contract = _contract()
        idle = contract.idle_timeout_s()
        make: _ScenarioMaker
        expect_latch: str | None = None

        if family == "incremental_idle":
            make = partial(
                _idle_cut_scenario,
                _incremental_plan(enforcing=True),
                idle + rng.uniform(1e-6, idle),
            )
            expect_latch = posture.STREAM_IDLE_TIMEOUT
        elif family == "incremental_healthy":
            make = partial(_healthy_scenario, _incremental_plan(enforcing=False), 4)
        elif family == "passthrough_flag_idle":
            make = partial(
                _idle_cut_scenario, _strict_flag_only_plan(), idle + rng.uniform(1e-6, idle),
            )
            expect_latch = posture.STREAM_IDLE_TIMEOUT
        elif family == "passthrough_norule_healthy":
            make = partial(_healthy_scenario, _strict_passthrough_plan(), 5)
        elif family == "short_strict":
            # A withheld strict stream whose whole withhold fits inside the idle bound: the first
            # write is not an idle cut even under the oracle, so both runs deliver identically.
            make = partial(_short_strict_scenario, idle / 20.0, rng.randint(1, 10))
        elif family == "inflight_trigger":
            code, snap = rng.choice(_trigger_items)
            make = partial(_inflight_trigger_scenario, snap)
            expect_latch = code
        elif family == "max_duration":
            make = _max_duration_strict_scenario
            expect_latch = posture.STREAM_MAX_DURATION
        else:  # pragma: no cover - defensive
            raise AssertionError(family)

        fixed, oracle = _run_scenario_twice(make, contract)
        detail = (
            f"i={i} seed={seed:#x} family={family} fixed={_summary(fixed)!r} "
            f"oracle={_summary(oracle)!r}"
        )
        # The core preservation assertion: fixed == oracle across code, latch, codes, frames.
        assert _summary(fixed) == _summary(oracle), f"fixed diverged from the oracle; {detail}"
        # And the fix must never introduce a FALSE idle cut on a non-bug input.
        if family in ("incremental_healthy", "passthrough_norule_healthy", "short_strict"):
            assert fixed.timeout_cut is None, f"a non-bug healthy stream was cut; {detail}"
        if expect_latch is not None:
            assert fixed.latch_reason == expect_latch, detail
        if family in ("inflight_trigger", "max_duration"):
            assert not fixed.content_frames, f"content leaked before the cut; {detail}"
