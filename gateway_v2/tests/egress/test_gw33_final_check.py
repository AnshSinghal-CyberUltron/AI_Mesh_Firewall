"""GW33 tasks 3.5/3.6: the whole-channel final check and the typed terminal arms (both modes)."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.domain import (
    Action,
    Category,
    Finding,
    FindingStatus,
    Phase,
    Span,
    StreamingMode,
    posture,
)
from gateway_v2.egress.output_errors import OutputLengthExceeded, RewriteVerificationFailure
from gateway_v2.egress.select import select_pipeline
from gateway_v2.egress.stream import DownstreamFrame, UpstreamChunk
from gateway_v2.resolve import PlanResolver
from gateway_v2.runtime.holdback_config import load_holdback_config
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.strict_metrics import StrictMetrics
from tests.e2e.gw33_support import plan_of, rule, run

_CONTEXT = ("The refund policy allows returns within thirty days of purchase.",)
_UNGROUNDED = "Purple elephants orchestrate quantum bakeries throughout Antarctica every Tuesday."
_STRICT = StreamingMode.STRICT_WITHHOLD
_INCR = StreamingMode.INCREMENTAL


def _grounding_plan(mode: StreamingMode, action: Action):  # type: ignore[no-untyped-def]
    return plan_of(mode, rule("out_ground", Category.HALLUCINATION, action,
                              "output.grounding.lexical", template="[unsupported claim]",
                              threshold=0.3))


def test_grounding_findings_are_merged_in_both_pipelines() -> None:
    for mode in (_INCR, _STRICT):
        result = run(_grounding_plan(mode, Action.FLAG), [("output_content", _UNGROUNDED)],
                     context=_CONTEXT)
        assert result.released == _UNGROUNDED  # flag-only: no byte altered
        assert result.stats.review_required is True  # type: ignore[attr-defined]
        assert result.stats.output_action == "flag"  # type: ignore[attr-defined]


def test_strict_applies_a_byte_altering_grounding_action_and_incremental_cannot() -> None:
    redact = run(_grounding_plan(_STRICT, Action.REDACT), [("output_content", _UNGROUNDED)],
                 context=_CONTEXT)
    assert _UNGROUNDED not in redact.released and "[REDACTED]" in redact.released
    rewrite = run(_grounding_plan(_STRICT, Action.REWRITE), [("output_content", _UNGROUNDED)],
                  context=_CONTEXT)
    assert rewrite.released == "[unsupported claim]"
    blocked = run(_grounding_plan(_STRICT, Action.BLOCK), [("output_content", _UNGROUNDED)],
                  context=_CONTEXT)
    assert blocked.released == "" and blocked.codes == ["output_blocked"]
    # A hand-built INCREMENTAL plan (the compiler refuses it) fails closed, never silently passes.
    hand_built = run(_grounding_plan(_INCR, Action.REDACT), [("output_content", _UNGROUNDED)],
                     context=_CONTEXT)
    assert hand_built.codes == [posture.FINAL_CHECK_UNAPPLIED]


def test_no_context_is_skipped_and_leaves_no_flag() -> None:
    result = run(_grounding_plan(_INCR, Action.FLAG), [("output_content", _UNGROUNDED)])
    assert result.released == _UNGROUNDED and result.stats.review_required is False  # type: ignore[attr-defined]


class _Counting:
    """An async final detector that records every (channel, whole) it is awaited with."""

    def __init__(self, findings: Sequence[Finding]) -> None:
        self.calls: list[tuple[str, str]] = []
        self._findings = findings

    async def __call__(self, channel: str, whole: str) -> Sequence[Finding]:
        await asyncio.sleep(0)
        self.calls.append((channel, whole))
        return self._findings


def _drive(
    plan, deltas, *, final=None, detector=None, source=None,  # type: ignore[no-untyped-def]
):
    cfg, _ = load_holdback_config({})
    pipeline = select_pipeline(
        plan, scanner=holdback_scan, token_index=TokenIndex(), cfg=cfg, metrics=HoldbackMetrics(),
        window=max_pattern_length(), resolver=PlanResolver(plan=plan, phase=Phase.OUTPUT),
        detector=detector, max_buffer_bytes=1 << 20, strict_metrics=StrictMetrics(),
        final_detector=final,
    )
    frames: list[DownstreamFrame] = []

    async def send(frame: DownstreamFrame) -> None:
        frames.append(frame)

    async def gen():  # type: ignore[no-untyped-def]
        if source is not None:
            async for chunk in source():
                yield chunk
            return
        for index, delta in enumerate(deltas):
            yield UpstreamChunk(text_deltas=(delta,), final=index == len(deltas) - 1)

    stats = asyncio.run(pipeline.run(gen(), send, lambda: False))
    return frames, stats


def _hit(category: Category, detector: str, status: FindingStatus = FindingStatus.EXECUTED) -> Finding:
    ok = status is FindingStatus.EXECUTED
    return Finding(detector, "v", category, status, 1.0 if ok else None,
                   (Span(0, 2),) if ok else (), None)


def test_the_final_detector_is_awaited_once_per_channel_and_an_absent_one_is_skipped() -> None:
    plan = _grounding_plan(_INCR, Action.FLAG)
    counting = _Counting(())
    _drive(plan, [("output_content", "ab"), ("output_reasoning", "cd"), ("output_content", "ef")],
           final=counting)
    assert sorted(counting.calls) == [("output_content", "abef"), ("output_reasoning", "cd")]
    frames, stats = _drive(plan, [("output_content", "ab")], final=None)
    assert stats.review_required is False and frames[0].text_deltas == (("output_content", "ab"),)  # type: ignore[attr-defined]


def test_incremental_unavailable_final_check_is_reported_never_enforced() -> None:
    """D-22/L-3 and Property 8: UNAVAILABLE + FAIL_OPEN forces the stats flag, not the Decision's."""
    # Feature: gw33-output-parity, Property 8: Review flag consistency
    plan = _grounding_plan(_INCR, Action.FLAG)
    unavailable = _Counting((_hit(Category.HALLUCINATION, "out_ground", FindingStatus.UNAVAILABLE),))
    frames, stats = _drive(plan, [("output_content", "hello")], final=unavailable)
    assert [f.error_code for f in frames if f.error_code] == []
    assert stats.review_required is True  # type: ignore[attr-defined]
    assert stats.final_check_unavailable == 1  # type: ignore[attr-defined]
    decision = stats.output_decision  # type: ignore[attr-defined]
    assert decision is None or decision.review_required is False  # the invariant stays intact


def test_strict_unavailable_routes_by_on_unavailable() -> None:
    closed = plan_of(_STRICT, rule("g", Category.HALLUCINATION, Action.BLOCK,
                                   "output.grounding.lexical"))
    frames, _ = _drive(closed, [("output_content", "hello")],
                       final=_Counting((_hit(Category.HALLUCINATION, "g", FindingStatus.UNAVAILABLE),)))
    assert [f.error_code for f in frames if f.error_code] == ["output_blocked"]
    opened = plan_of(_STRICT, rule("g", Category.HALLUCINATION, Action.FLAG,
                                   "output.grounding.lexical"))
    frames, stats = _drive(opened, [("output_content", "hello")],
                           final=_Counting((_hit(Category.HALLUCINATION, "g", FindingStatus.UNAVAILABLE),)))
    assert frames[0].text_deltas == (("output_content", "hello"),)
    assert stats.review_required is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("mode", [_INCR, _STRICT])
def test_typed_exceptions_map_to_their_declared_terminal_codes(mode: StreamingMode) -> None:
    plan = plan_of(mode, rule("r", Category.PII, Action.REDACT))

    def raising(exc: Exception):  # type: ignore[no-untyped-def]
        def detector(_text: str) -> Sequence[Finding]:
            raise exc
        return detector

    frames, stats = _drive(plan, [("output_content", "abc")],
                           detector=raising(RewriteVerificationFailure("x")))
    assert [f.error_code for f in frames if f.error_code] == [posture.REWRITE_UNVERIFIED]
    assert stats.incomplete is True  # type: ignore[attr-defined]
    assert not any(f.text_deltas for f in frames if f.error_code)


@pytest.mark.parametrize("mode", [_INCR, _STRICT])
def test_output_length_exceeded_from_the_source_terminates_in_both_modes(mode: StreamingMode) -> None:
    plan = plan_of(mode, rule("r", Category.PII, Action.REDACT))

    async def source():  # type: ignore[no-untyped-def]
        yield UpstreamChunk(text_deltas=(("output_content", "abc"),), final=False)
        raise OutputLengthExceeded("too long")

    frames, _ = _drive(plan, [], source=source)
    assert [f.error_code for f in frames if f.error_code] == [posture.OUTPUT_LENGTH_EXCEEDED]
    # Pass-through (no enforcing rule) maps it too: never a silent truncation.
    passthrough = plan_of(mode, rule("r", Category.PII, Action.FLAG))
    frames, _ = _drive(passthrough, [], source=source)
    assert [f.error_code for f in frames if f.error_code] == [posture.OUTPUT_LENGTH_EXCEEDED]


@pytest.mark.parametrize("mode", [_INCR, _STRICT])
def test_an_unrelated_exception_still_reaches_the_generic_arm_and_re_raises(mode: StreamingMode) -> None:
    plan = plan_of(mode, rule("r", Category.PII, Action.REDACT))

    def detector(_text: str) -> Sequence[Finding]:
        raise RuntimeError("unrelated")

    with pytest.raises((RuntimeError, Exception)) as raised:
        _drive(plan, [("output_content", "abc")], detector=detector)
    assert "unrelated" in str(raised.value) or raised.type.__name__ == "ScanFailure"
