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
