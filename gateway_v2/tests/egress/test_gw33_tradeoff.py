# ruff: noqa: E501  (GW33 test file: long assertion/docstring lines kept readable)
"""GW33 task 3.3: REWRITE terminates on force-release; remainder folding with REWRITE."""
from __future__ import annotations

from gateway_v2.domain import (
    Category,
    Decision,
    Disposition,
    Finding,
    FindingDisposition,
    FindingStatus,
    Span,
    Transformation,
)
from gateway_v2.egress.holdback_tradeoff import (
    TRADE_OFF_REDACT_REMAINDER,
    TRADE_OFF_TERMINATE,
    derive_trade_off_outcome,
    with_remainder_redactions,
)
from gateway_v2.egress.output_guard import apply_decision


def _decision(disposition: Disposition, *edits: Transformation, review: bool = False) -> Decision:
    finding = Finding("d", "v", Category.IP_LEAK, FindingStatus.EXECUTED, 1.0, (Span(0, 3),), None)
    return Decision(
        disposition=disposition,
        per_finding=(FindingDisposition("d", disposition, 0),),
        transformations=edits,
        findings=(finding,),
        plan_version="v",
        deciding_rules=("r",),
        unavailable_detectors=(),
        review_required=review,
    )


def test_rewrite_terminates_and_existing_outcomes_are_unchanged() -> None:
    assert derive_trade_off_outcome(_decision(Disposition.REWRITE)) == TRADE_OFF_TERMINATE
    assert derive_trade_off_outcome(_decision(Disposition.BLOCK)) == TRADE_OFF_TERMINATE
    assert derive_trade_off_outcome(_decision(Disposition.REDACT)) == TRADE_OFF_REDACT_REMAINDER
    assert derive_trade_off_outcome(_decision(Disposition.FLAG)) is None
    assert derive_trade_off_outcome(_decision(Disposition.ALLOW)) is None


def test_remainder_fold_with_a_rewrite_decision_does_not_raise() -> None:
    rewrite = Transformation("rewrite", Span(0, 3), "[ip]")
    decision = _decision(Disposition.REWRITE, rewrite, review=True)
    folded = with_remainder_redactions(decision, ((10, 14),))
    # The folded decision keeps the stricter REWRITE disposition and carries the review flag.
    assert folded.disposition is Disposition.REWRITE
    assert folded.review_required is True
    assert {t.kind for t in folded.transformations} == {"rewrite", "redact"}
    assert apply_decision("abc 0123456789xyz", folded, 0) == "[ip] 012345[REDACTED]xyz"


def test_remainder_fold_on_redact_decision_is_unchanged() -> None:
    decision = _decision(Disposition.REDACT, Transformation("redact", Span(0, 3), "[R]"))
    folded = with_remainder_redactions(decision, ((10, 14),))
    assert folded.disposition is Disposition.REDACT
    assert len(folded.transformations) == 2


class _RewriteResolver:
    """A resolver that answers REWRITE (with a declared template edit) for any match."""

    def decide(self, matches):  # type: ignore[no-untyped-def]
        if not matches:
            return Decision(Disposition.ALLOW, (), (), (), "v", (), ())
        spans = tuple(s for f in matches for s in f.spans)
        return Decision(
            Disposition.REWRITE,
            (FindingDisposition(matches[0].detector, Disposition.REWRITE, 0),),
            tuple(Transformation("rewrite", s, "[x]") for s in spans),
            tuple(matches), "v", ("r",), (), review_required=True,
        )


def test_a_rewrite_whose_prefix_was_force_released_ends_with_forced_release_tradeoff() -> None:
    """D-8 through the real pipeline: REWRITE cannot be applied to released bytes, so it terminates."""
    import asyncio

    from gateway_v2.detect.holdback import TokenIndex
    from gateway_v2.egress.stream import DownstreamFrame, StreamPipeline, UpstreamChunk
    from gateway_v2.runtime.holdback_config import load_holdback_config
    from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
    from tests.egress.test_lgw12b_stream import _force_release_everything, _literal_detector

    cfg, _ = load_holdback_config({})
    pipeline = StreamPipeline(
        scanner=_force_release_everything(held_class="api_token"),  # type: ignore[arg-type]
        resolver=_RewriteResolver(), token_index=TokenIndex(), cfg=cfg, metrics=HoldbackMetrics(),
        enforcing_output=True, window=64,
        detector=_literal_detector("10.0.0.99", detector_class="ip", category=Category.IP_LEAK),
    )
    frames: list[DownstreamFrame] = []

    async def send(frame: DownstreamFrame) -> None:
        frames.append(frame)

    async def source():  # type: ignore[no-untyped-def]
        yield UpstreamChunk(text_deltas=(("output_content", "host 10.0.0.99 up"),), final=True)

    stats = asyncio.run(pipeline.run(source(), send, lambda: False))
    assert [f.error_code for f in frames if f.error_code] == ["forced_release_tradeoff"]
    assert stats.error == "forced_release_tradeoff"
    assert "10.0.0.99" not in "".join(t for f in frames for _c, t in f.text_deltas)
