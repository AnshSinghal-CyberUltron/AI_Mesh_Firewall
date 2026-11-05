"""Exploratory bug-condition test for the false ``stream_idle_timeout`` on withheld strict streams.

# Feature: strict-withhold-idle-timeout, Property 1: Bug Condition
# Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2

This is the task-1 bug-condition exploration test of the ``strict-withhold-idle-timeout``
bugfix spec. It is *written against the EXPECTED (fixed) behaviour*, so on the UNFIXED code it
MUST FAIL — the failure is the reproduction that confirms the bug. Once task 3 wires the
``upstream_liveness`` flag, this same file validates the fix (task 3.6).

Bug (bugfix.md §1.1–1.3): a STRICT_WITHHOLD stream with an enforcing output rule writes nothing
downstream until the whole response has been inspected (``StrictWithholdPipeline._run_enforcing``
accumulates every chunk and only calls ``send`` from ``_finalize``). ``StreamTimeouts`` models
downstream idle as ``now - _last_write_s`` and seeds ``_last_write_s`` at stream start. When the
withheld stream outlasts ``idle_timeout_s`` (3 s at the default 20 ms SLO), the first and only
content ``send`` trips the idle check and the stream is cut with ``STREAM_IDLE_TIMEOUT``
(HTTP 408 "The downstream client went idle") before any byte is sent — blaming a client that
never stopped reading.

Harness (design.md "Exploratory Bug Condition Checking"): a REAL ``StreamTimeouts`` built from a
``ResourceContract`` at ``target_p99_ms=20`` (so ``idle_timeout_s`` = 3 s,
``inter_chunk_timeout_s`` = 1 s, ``write_timeout_s`` = 0.5 s) and a REAL
``StrictWithholdPipeline`` with an enforcing output rule, driven through the shipped ``as_chunks``
adapter. An injected clock is advanced by 0.15 s on each upstream event, so the SLOWSTREAM (40
content chunks + ``final`` = 6 s total) runs with ZERO wall-clock time and exact determinism. The
upstream inter-event gap (0.15 s) is well under the 1 s inter-chunk bound, and the client always
accepts writes, so the ONLY thing that can cut the stream is the (buggy) idle check — the bug
condition ``isBugCondition`` from design.md holds.

The pipeline's content ``send`` is routed through ``StreamTimeouts.send`` exactly as the route's
``ChatStream._write_events`` does (``await timeouts.send(partial(response.write, ...))``), so the
idle check this test exercises is the production idle check.

Cases (design.md "Test Cases"):
1. ``test_bug_6s_healthy_strict_stream_is_not_cut_as_client_idle`` — the 6 s healthy stream.
   EXPECTED: no ``STREAM_IDLE_TIMEOUT``, the sanitized content frame delivered.
   UNFIXED (the reproduction): ``StreamTimeout(STREAM_IDLE_TIMEOUT)`` raised at the first ``send``,
   latch reason ``stream_idle_timeout``, zero content frames.
2. ``test_bug_asgi_408_withheld_strict_stream_returns_200_with_body`` — the same stream driven to
   the terminal-rendering boundary (the ``PendingError`` / latch the route reads to render a
   pre-byte HTTP error). EXPECTED: a 200-shaped outcome (no pre-byte failure code, content on the
   wire). UNFIXED: a pre-byte failure code ``stream_idle_timeout`` and no body bytes (the 408).
3. ``test_control_2_9s_strict_stream_is_delivered`` — a 2.9 s stream, just under the 3 s idle
   bound. PASSES on unfixed code (outside the bug condition), so it must keep passing after the
   fix.
4. ``test_bug_routed_first_event_does_not_cover_withheld_period`` — a routed stream
   (``first_event_s`` set, a 4 s first event then 6 s of 0.15 s chunks). The GW28 D-18 first-event
   re-seed covers only the first event; the withheld accumulation after it still trips the idle
   check. EXPECTED: delivered. UNFIXED: cut with ``stream_idle_timeout``.

Expected counterexamples (design.md): at the first ``send`` the idle check sees
``now - _last_write_s`` equal to the total elapsed time (6.0 s for case 1, 10.0 s for case 4), far
over the 3 s bound. The per-case assertion messages print that measured gap so the counterexample
is recorded. If instead the 408 came from something other than the write-based idle seed — for
example the inter-chunk bound — root cause 1 would be refuted and the fix re-hypothesised; the
assertions distinguish ``stream_idle_timeout`` from the other cut codes to make that visible.

Layering: this is a test file (outside the import-linter contract), so it imports the concrete
``detect``/``egress``/``edge`` seams directly and injects them, the same way the GW33 offline
harness (``tests/e2e/gw33_support.py``) does.
"""

from __future__ import annotations

import asyncio
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
    StreamingMode,
    Surface,
    posture,
)
from gateway_v2.edge.asgi_sse import ErrorFrameScan, as_chunks
from gateway_v2.edge.cancel import InFlightControl, InFlightSnapshot, KillLatch
from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.edge.output_detectors import build_output_detectors
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.output_guard import enforcing_output_rules, withholds_until_decision
from gateway_v2.egress.select import select_pipeline
from gateway_v2.egress.stream import DownstreamFrame
from gateway_v2.resolve import PlanResolver
from gateway_v2.runtime.holdback_config import load_holdback_config
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.strict_metrics import StrictMetrics

# --------------------------------------------------------------------------- #
# SLOWSTREAM parameters (the integration F1 reproduction).
# --------------------------------------------------------------------------- #

#: The SLOWSTREAM cadence: 40 content events, 0.15 s apart, then one ``final`` event. 40 x 0.15 s
#: = 6.0 s, which exceeds the 3 s idle bound while every inter-event gap (0.15 s) stays well under
#: the 1 s inter-chunk bound. These are scenario parameters, not capacity literals.
_SLOWSTREAM_CHUNKS = 40
_SLOWSTREAM_GAP_S = 0.15
_CONTENT_CHANNEL = "output_content"


def _contract() -> ResourceContract:
    """A serviceable contract at the default 20 ms SLO: idle 3 s, inter-chunk 1 s, write 0.5 s."""
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=1 << 34,
        fd_limit=4096,
        guard_capacity=None,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 << 20,
        worker_override=None,
        cpu_source="t",
        mem_source="t",
        fd_source="t",
    )


def _enforcing_strict_plan() -> ExecutionPlan:
    """A STRICT_WITHHOLD plan with one enforcing OUTPUT rule (the D-20 promotion shape).

    An ``out_ip_leak`` ENFORCE/BLOCK rule is a non-empty ``enforcing_output_rules(plan)``, so
    ``select_pipeline`` builds the ``StrictWithholdPipeline`` on its ``_run_enforcing`` path — it
    accumulates the whole response and writes nothing until ``_finalize``. That withholding is the
    precondition for the bug.
    """
    condition = RuleCondition(
        RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", "output.ip_leak", "",
    )
    out_rule = Rule(
        "out_ip_leak", Category.IP_LEAK, Mode.ENFORCE, Action.BLOCK, None, 200,
        RuleScope.OUTPUT, FailurePosture.FAIL_CLOSED, frozenset({Surface.CHAT}), condition,
        "[internal address]",
    )
    plan = ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, (out_rule,), frozenset(),
        StreamingMode.STRICT_WITHHOLD, True,
    )
    assert enforcing_output_rules(plan), "fixture must be a withholding strict plan"
    return plan


class _StubClock:
    """A manually-advanced seconds clock for the injected ``clock`` seam (zero wall time)."""

    __slots__ = ("now",)

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _fresh_snapshot() -> InFlightSnapshot:
    """A fresh, available snapshot with every in-flight cut signal clear (nothing fires)."""
    return InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=False,
    )


class _FreshControlSource:
    """An ``InFlightControlSource`` returning a fresh snapshot — no in-flight trigger fires."""

    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return _fresh_snapshot()


def _no_findings(_text: str) -> tuple[Finding, ...]:
    """An error-frame scanner that finds nothing (this stream carries no error frame)."""
    return ()


def _slowstream_events() -> list[UpstreamEvent]:
    """The SLOWSTREAM script: 40 content events then a terminal ``final`` event (clean text).

    The clock-advance happens in the async generator as each event is yielded, not here; this
    just builds the event list. The content is plain prose with no secret / leak pattern, so the
    whole-buffer inspection in ``_finalize`` resolves ALLOW and the pipeline emits a single
    sanitized content frame — the decided response the client should receive.
    """
    events = [
        UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, f"token{i} "),), final=False)
        for i in range(_SLOWSTREAM_CHUNKS)
    ]
    events.append(UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, "done."),), final=True))
    return events


@dataclass
class _Capture:
    """Records the frames the pipeline emitted and whether the idle timeout cut the stream."""

    frames: list[DownstreamFrame] = field(default_factory=list)
    idle_cut: StreamTimeout | None = None
    first_send_gap_s: float | None = None

    @property
    def content_frames(self) -> list[DownstreamFrame]:
        return [f for f in self.frames if f.error_code is None and f.text_deltas]

    @property
    def released(self) -> str:
        return "".join(
            t for f in self.frames if f.error_code is None for _c, t in f.text_deltas
        )

    @property
    def codes(self) -> list[str]:
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
        detector=out.detector_for(plan.streaming_mode),  # whole-response strict detector
        max_buffer_bytes=1 << 24,
        strict_metrics=StrictMetrics(),
        delta_detector=out.canary_delta,
        final_detector=out.final,
        sync_runner=out.runner,
    )


async def _drive_slowstream(
    *,
    contract: ResourceContract,
    clock: _StubClock,
    gap_s: float,
    first_event_s: float | None,
    first_event_delay_s: float = 0.0,
) -> _Capture:
    """Drive the SLOWSTREAM through ``as_chunks`` + ``StrictWithholdPipeline``, idle-clock seeded.

    The content ``send`` is routed through ``StreamTimeouts.send`` exactly as the route's
    ``ChatStream._write_events`` does, so this exercises the production idle check. The injected
    clock advances by ``gap_s`` as each upstream event is consumed (``first_event_delay_s`` for the
    first, to model a slow routed time-to-first-token). A ``StreamTimeout`` raised from ``send`` is
    caught and recorded — on unfixed code it is the false idle cut.
    """
    executor = ScanExecutor(contract=contract)
    try:
        plan = _enforcing_strict_plan()
        pipeline = _build_pipeline(plan, contract)
        kill_latch = KillLatch()
        control = InFlightControl(
            source=_FreshControlSource(), contract=contract, clock=clock, kill_latch=kill_latch,
        )
        timeouts = StreamTimeouts(
            contract=contract, kill_latch=kill_latch, clock=clock, first_event_s=first_event_s,
            # task 3.6: the harness derives the liveness flag from the plan exactly as
            # ChatStream._controls does (upstream_liveness=withholds_until_decision(plan)), so this
            # fix-verification test routes through the same production wiring the fix added.
            upstream_liveness=withholds_until_decision(plan),
        )
        coalescer = Coalescer(contract=contract, active_streams=lambda: 1)
        capture = _Capture()

        events = _slowstream_events()

        async def _provider() -> AsyncIterator[UpstreamEvent]:
            for index, event in enumerate(events):
                # Advance the injected clock to model the upstream delivery cadence: the first
                # event may be slow (routed time-to-first-token), the rest arrive `gap_s` apart.
                clock.now += first_event_delay_s if index == 0 else gap_s
                yield event

        async def _send(frame: DownstreamFrame) -> None:
            # Mirror ChatStream._write_events: content frames go through timeouts.send; the
            # terminal error frame (pre-byte) is recorded on the latch, not written.
            if frame.error_code is not None:
                capture.frames.append(frame)
                return
            if capture.first_send_gap_s is None:
                # Record the gap the idle check sees at the FIRST content write (counterexample).
                capture.first_send_gap_s = clock.now - timeouts._last_write_s  # noqa: SLF001
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
            capture.idle_cut = exc
        if kill_latch.reason() == posture.STREAM_IDLE_TIMEOUT and capture.idle_cut is None:
            # Defensive: the idle cut may surface via the latch even if the raise was swallowed.
            capture.idle_cut = StreamTimeout(posture.STREAM_IDLE_TIMEOUT)
        return capture
    finally:
        executor.shutdown()


async def _record(capture: _Capture, frame: DownstreamFrame) -> None:
    """The innermost write: record the content frame (the client accepted the byte)."""
    capture.frames.append(frame)


# --------------------------------------------------------------------------- #
# Case 1 — the 6 s healthy withheld strict stream (the reproduction).
# --------------------------------------------------------------------------- #


def test_bug_6s_healthy_strict_stream_is_not_cut_as_client_idle() -> None:
    """A 6 s healthy withheld strict stream must deliver, not be cut as a client idle (2.1, 2.2).

    # Feature: strict-withhold-idle-timeout, Property 1: Bug Condition
    # Validates: Requirements 1.1, 1.2, 2.1, 2.2

    EXPECTED (fixed): no ``STREAM_IDLE_TIMEOUT``; the sanitized content frame is delivered.
    UNFIXED: the first content ``send`` sees ``now - _last_write_s`` == 6.0 s > 3 s idle bound and
    is cut with ``stream_idle_timeout`` before any byte — the reproduction.
    """
    contract = _contract()
    clock = _StubClock()
    capture = asyncio.run(
        _drive_slowstream(
            contract=contract, clock=clock, gap_s=_SLOWSTREAM_GAP_S, first_event_s=None,
        )
    )

    idle_bound = contract.idle_timeout_s()
    gap = capture.first_send_gap_s
    detail = (
        f"first-send idle gap={gap!r}s vs idle_timeout_s={idle_bound!r}s; "
        f"codes={capture.codes!r}; content_frames={len(capture.content_frames)}"
    )

    # The counterexample: the idle check measured the whole 6 s withheld period at the first write.
    assert capture.idle_cut is None, (
        "BUG REPRODUCED — a healthy 6 s withheld strict stream was cut with "
        f"{capture.idle_cut.code if capture.idle_cut else None!r}; {detail}"
    )
    assert posture.STREAM_IDLE_TIMEOUT not in capture.codes, detail
    assert capture.released == "token0 " + "".join(
        f"token{i} " for i in range(1, _SLOWSTREAM_CHUNKS)
    ) + "done.", f"decided content not delivered; released={capture.released!r}; {detail}"


# --------------------------------------------------------------------------- #
# Case 2 — the ASGI-level 408: a withheld strict stream must return a body, not a 408.
# --------------------------------------------------------------------------- #


def test_bug_asgi_408_withheld_strict_stream_returns_200_with_body() -> None:
    """The withheld strict stream must render a 200 with body, not a pre-byte 408 (1.1, 1.3).

    # Feature: strict-withhold-idle-timeout, Property 1: Bug Condition
    # Validates: Requirements 1.1, 1.3, 2.1

    The route renders a pre-byte HTTP error from the kill-latch reason when no byte was written
    (``ChatStream.finish_stream`` -> ``response.fail_pre_byte``). EXPECTED: no pre-byte failure
    code and content on the wire (the 200). UNFIXED: the latch carries ``stream_idle_timeout`` and
    zero content frames were written — exactly the HTTP 408 with no body the F1 artifact shows.
    """
    contract = _contract()
    clock = _StubClock()
    capture = asyncio.run(
        _drive_slowstream(
            contract=contract, clock=clock, gap_s=_SLOWSTREAM_GAP_S, first_event_s=None,
        )
    )

    pre_byte_code = capture.idle_cut.code if capture.idle_cut is not None else None
    detail = (
        f"pre_byte_code={pre_byte_code!r}; wrote_body={bool(capture.content_frames)}; "
        f"first-send idle gap={capture.first_send_gap_s!r}s"
    )
    # A 200-with-body outcome: no pre-byte failure code, at least one content frame on the wire.
    assert pre_byte_code != posture.STREAM_IDLE_TIMEOUT, (
        f"BUG REPRODUCED — the withheld strict stream answered a pre-byte 408 "
        f"{posture.STREAM_IDLE_TIMEOUT!r} with no body; {detail}"
    )
    assert capture.content_frames, f"no body bytes were written (the 408 path); {detail}"


# --------------------------------------------------------------------------- #
# Case 3 — the 2.9 s control: just under the idle bound, delivered on unfixed code.
# --------------------------------------------------------------------------- #


def test_control_2_9s_strict_stream_is_delivered() -> None:
    """A 2.9 s withheld strict stream is OUTSIDE the bug condition and is delivered (control).

    # Feature: strict-withhold-idle-timeout, Property 1 control (Preservation)

    40 chunks at 0.0725 s = 2.9 s total, just under the 3 s idle bound, so the first content write
    sees a gap under the bound and is NOT cut. This PASSES on unfixed code and must keep passing
    after the fix — it pins that the fix does not change behaviour outside the bug condition.
    """
    contract = _contract()
    clock = _StubClock()
    # 2.9 s total across the 40 content events (the final event adds no further gap before send).
    gap = 2.9 / _SLOWSTREAM_CHUNKS
    capture = asyncio.run(
        _drive_slowstream(contract=contract, clock=clock, gap_s=gap, first_event_s=None)
    )

    detail = (
        f"first-send idle gap={capture.first_send_gap_s!r}s vs "
        f"idle_timeout_s={contract.idle_timeout_s()!r}s; codes={capture.codes!r}"
    )
    assert capture.idle_cut is None, f"control stream unexpectedly cut; {detail}"
    assert posture.STREAM_IDLE_TIMEOUT not in capture.codes, detail
    assert capture.content_frames, f"control stream delivered no content; {detail}"


# --------------------------------------------------------------------------- #
# Case 4 — routed first event: D-18 re-seed does not cover the withheld period.
# --------------------------------------------------------------------------- #


def test_bug_routed_first_event_does_not_cover_withheld_period() -> None:
    """A routed withheld strict stream is still cut: D-18 re-seeds only the first event (2.1, 2.2).

    # Feature: strict-withhold-idle-timeout, Property 1: Bug Condition
    # Validates: Requirements 2.1, 2.2

    ``first_event_s`` is set (routed), the first upstream event takes 4 s (within the wider
    first-event bound), and the GW28 D-18 branch re-seeds the idle clock when it returns. But the
    remaining 40 chunks then accumulate for another 6 s with no write, so the first content
    ``send`` still sees ~6 s > 3 s and is cut. EXPECTED: delivered. UNFIXED: cut with
    ``stream_idle_timeout``, confirming D-18 does not cover the withheld period.
    """
    contract = _contract()
    clock = _StubClock()
    # A routed first-event budget comfortably above the 4 s first event so next_event does not
    # trip the first-event bound; the 4 s is modelled by first_event_delay_s.
    first_event_s = contract.routing_pre_content_budget_s(contract.routing_max_attempts())
    capture = asyncio.run(
        _drive_slowstream(
            contract=contract,
            clock=clock,
            gap_s=_SLOWSTREAM_GAP_S,
            first_event_s=first_event_s,
            first_event_delay_s=4.0,
        )
    )

    detail = (
        f"first-send idle gap={capture.first_send_gap_s!r}s vs "
        f"idle_timeout_s={contract.idle_timeout_s()!r}s; codes={capture.codes!r}; "
        f"first_event_s={first_event_s!r}"
    )
    assert capture.idle_cut is None, (
        "BUG REPRODUCED — a routed withheld strict stream was cut as client idle after the "
        f"D-18 first-event re-seed; {detail}"
    )
    assert posture.STREAM_IDLE_TIMEOUT not in capture.codes, detail
    assert capture.content_frames, f"routed stream delivered no content; {detail}"
