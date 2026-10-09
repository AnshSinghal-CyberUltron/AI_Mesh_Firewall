"""LGW12b output guard glue (R2-06 / GW12b), tasks 6.1 + 6.2.

Unit tests for ``enforcing_output_rules`` (R2.1/R2.2), the minimal
``OutputResolver`` (most-restrictive disposition selection), ``apply_decision``
span-accurate redaction with a base offset (R3.3), the BLOCK -> ``OutputBlocked``
path (R3.4), and the fail-closed behaviour on an out-of-range redaction span.
"""

from __future__ import annotations

import pytest

from gateway_v2.detect.holdback import TRADE_OFF, TradeOffOutcome
from gateway_v2.domain import (
    Action,
    Category,
    Decision,
    Disposition,
    ExecutionPlan,
    FailurePosture,
    Finding,
    FindingDisposition,
    FindingStatus,
    Mode,
    Rule,
    RuleScope,
    Span,
    StreamingMode,
    Surface,
    Transformation,
)
from gateway_v2.egress.output_guard import (
    MinimalOutputResolver,
    OutputBlocked,
    OutputResolver,
    apply_decision,
    enforcing_output_rules,
)

# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _rule(
    rule_id: str,
    *,
    scope: RuleScope,
    mode: Mode,
    action: Action,
    category: Category = Category.SECRET,
) -> Rule:
    return Rule(
        rule_id=rule_id,
        category=category,
        mode=mode,
        action=action,
        threshold=None,
        priority=0,
        scope=scope,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
    )


def _plan(*rules: Rule) -> ExecutionPlan:
    return ExecutionPlan(
        org_id="org-1",
        epoch=1,
        sequence=1,
        content_hash="0" * 32,
        compiled_at=0.0,
        rules=rules,
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=True,
    )


def _finding(
    detector: str,
    *spans: Span,
    status: FindingStatus = FindingStatus.EXECUTED,
) -> Finding:
    return Finding(
        detector=detector,
        detector_version="1",
        category=Category.SECRET,
        status=status,
        confidence=0.9 if status is FindingStatus.EXECUTED else None,
        spans=spans,
        evidence=None,
    )


# --------------------------------------------------------------------------- #
# Task 6.1 -- enforcing_output_rules
# --------------------------------------------------------------------------- #


def test_enforcing_output_rules_selects_output_enforce_redact() -> None:
    rule = _rule("r1", scope=RuleScope.OUTPUT, mode=Mode.ENFORCE, action=Action.REDACT)
    assert enforcing_output_rules(_plan(rule)) == (rule,)


def test_enforcing_output_rules_selects_both_scope() -> None:
    rule = _rule("r1", scope=RuleScope.BOTH, mode=Mode.ENFORCE, action=Action.BLOCK)
    assert enforcing_output_rules(_plan(rule)) == (rule,)


def test_enforcing_output_rules_accepts_rewrite() -> None:
    rule = _rule("r1", scope=RuleScope.OUTPUT, mode=Mode.ENFORCE, action=Action.REWRITE)
    assert enforcing_output_rules(_plan(rule)) == (rule,)


def test_enforcing_output_rules_excludes_input_scope() -> None:
    rule = _rule("r1", scope=RuleScope.INPUT, mode=Mode.ENFORCE, action=Action.REDACT)
    assert enforcing_output_rules(_plan(rule)) == ()


def test_enforcing_output_rules_excludes_monitor_mode() -> None:
    rule = _rule("r1", scope=RuleScope.OUTPUT, mode=Mode.MONITOR, action=Action.REDACT)
    assert enforcing_output_rules(_plan(rule)) == ()


def test_enforcing_output_rules_excludes_off_mode() -> None:
    rule = _rule("r1", scope=RuleScope.OUTPUT, mode=Mode.OFF, action=Action.BLOCK)
    assert enforcing_output_rules(_plan(rule)) == ()


def test_enforcing_output_rules_excludes_non_enforcing_actions() -> None:
    allow = _rule("r1", scope=RuleScope.OUTPUT, mode=Mode.ENFORCE, action=Action.ALLOW)
    flag = _rule("r2", scope=RuleScope.OUTPUT, mode=Mode.ENFORCE, action=Action.FLAG)
    assert enforcing_output_rules(_plan(allow, flag)) == ()


def test_enforcing_output_rules_empty_when_no_rules() -> None:
    assert enforcing_output_rules(_plan()) == ()


def test_enforcing_output_rules_filters_mixed_set() -> None:
    keep = _rule("keep", scope=RuleScope.OUTPUT, mode=Mode.ENFORCE, action=Action.REDACT)
    drop_input = _rule("in", scope=RuleScope.INPUT, mode=Mode.ENFORCE, action=Action.BLOCK)
    drop_flag = _rule("flag", scope=RuleScope.BOTH, mode=Mode.ENFORCE, action=Action.FLAG)
    assert enforcing_output_rules(_plan(drop_input, keep, drop_flag)) == (keep,)


# --------------------------------------------------------------------------- #
# Task 6.1 -- MinimalOutputResolver / most_restrictive selection
# --------------------------------------------------------------------------- #


def test_resolver_is_output_resolver_protocol() -> None:
    assert isinstance(MinimalOutputResolver(), OutputResolver)


def test_resolver_empty_matches_allow() -> None:
    decision = MinimalOutputResolver().decide([])
    assert decision.disposition is Disposition.ALLOW
    assert decision.per_finding == ()
    assert decision.transformations == ()


def test_resolver_redact_class_yields_redact() -> None:
    # "email" is a redact-the-remainder class in the trade-off table.
    assert TRADE_OFF["email"] is TradeOffOutcome.REDACT_REMAINDER
    decision = MinimalOutputResolver().decide([_finding("email", Span(0, 5))])
    assert decision.disposition is Disposition.REDACT
    assert len(decision.transformations) == 1


def test_resolver_terminate_class_yields_block() -> None:
    # "jwt" is a terminate-the-stream class -> BLOCK.
    assert TRADE_OFF["jwt"] is TradeOffOutcome.TERMINATE
    decision = MinimalOutputResolver().decide([_finding("jwt", Span(0, 5))])
    assert decision.disposition is Disposition.BLOCK
    # A blocking finding is not turned into a redaction transformation.
    assert decision.transformations == ()


def test_resolver_most_restrictive_block_wins_over_redact() -> None:
    decision = MinimalOutputResolver().decide(
        [_finding("email", Span(0, 5)), _finding("jwt", Span(6, 10))]
    )
    assert decision.disposition is Disposition.BLOCK


def test_resolver_skipped_finding_is_allow() -> None:
    decision = MinimalOutputResolver().decide(
        [_finding("email", status=FindingStatus.SKIPPED)]
    )
    assert decision.disposition is Disposition.ALLOW


def test_resolver_unknown_detector_defaults_to_redact() -> None:
    decision = MinimalOutputResolver().decide([_finding("mystery", Span(0, 3))])
    assert decision.disposition is Disposition.REDACT


# --------------------------------------------------------------------------- #
# Task 6.2 -- apply_decision span-accurate redaction with base_offset
# --------------------------------------------------------------------------- #


def _redact_decision(*spans: Span, placeholder: str = "[REDACTED]") -> Decision:
    per_finding = tuple(
        FindingDisposition(detector="email", disposition=Disposition.REDACT) for _ in spans
    )
    transformations = tuple(
        Transformation(kind="redact", span=span, replacement=placeholder) for span in spans
    )
    return Decision(
        disposition=Disposition.REDACT if spans else Disposition.ALLOW,
        per_finding=per_finding,
        transformations=transformations,
        findings=(),
        plan_version="test",
        deciding_rules=(),
        unavailable_detectors=(),
    )


def test_apply_decision_redacts_span_at_zero_offset() -> None:
    released = "hello world"
    decision = _redact_decision(Span(6, 11), placeholder="[X]")
    assert apply_decision(released, decision, 0) == "hello [X]"


def test_apply_decision_honors_base_offset() -> None:
    # released slice begins at absolute byte 100.
    released = "secret=abcdef"
    decision = _redact_decision(Span(107, 113), placeholder="[REDACTED]")
    assert apply_decision(released, decision, 100) == "secret=[REDACTED]"


def test_apply_decision_multiple_spans() -> None:
    released = "a=1 b=2 c=3"
    decision = _redact_decision(Span(2, 3), Span(6, 7), placeholder="*")
    assert apply_decision(released, decision, 0) == "a=* b=* c=3"


def test_apply_decision_no_transformations_is_identity() -> None:
    released = "untouched"
    decision = _redact_decision()
    assert apply_decision(released, decision, 0) == "untouched"


def test_apply_decision_span_outside_slice_is_skipped() -> None:
    # Span lies entirely after the released slice -> not yet ours to apply.
    released = "abc"  # absolute [0,3)
    decision = _redact_decision(Span(10, 13))
    assert apply_decision(released, decision, 0) == "abc"


def test_apply_decision_span_before_slice_is_skipped() -> None:
    released = "xyz"  # absolute [100,103)
    decision = _redact_decision(Span(0, 3))
    assert apply_decision(released, decision, 100) == "xyz"


# --------------------------------------------------------------------------- #
# Task 6.2 -- BLOCK -> OutputBlocked (R3.4)
# --------------------------------------------------------------------------- #


def test_apply_decision_block_raises_output_blocked() -> None:
    decision = Decision(
        disposition=Disposition.BLOCK,
        per_finding=(FindingDisposition(detector="jwt", disposition=Disposition.BLOCK),),
        transformations=(),
        findings=(),
        plan_version="test",
        deciding_rules=("jwt",),
        unavailable_detectors=(),
    )
    with pytest.raises(OutputBlocked) as exc:
        apply_decision("anything", decision, 0)
    assert exc.value.decision is decision


# --------------------------------------------------------------------------- #
# Task 6.2 -- fail-closed on partial / out-of-range span (R3.3)
# --------------------------------------------------------------------------- #


def test_apply_decision_fails_closed_on_partial_span() -> None:
    # Span starts inside the slice but extends past its end -> partial coverage.
    released = "abcdef"  # absolute [0,6)
    decision = _redact_decision(Span(3, 10))
    with pytest.raises(ValueError, match="fully covered"):
        apply_decision(released, decision, 0)


def test_apply_decision_fails_closed_on_span_starting_before_slice() -> None:
    # Span starts before the slice start but ends inside -> partial coverage.
    released = "abcdef"  # absolute [100,106)
    decision = _redact_decision(Span(98, 103))
    with pytest.raises(ValueError, match="fully covered"):
        apply_decision(released, decision, 100)


def test_apply_decision_fails_closed_on_overlapping_spans() -> None:
    released = "abcdefgh"
    decision = _redact_decision(Span(1, 4), Span(2, 6))
    with pytest.raises(ValueError, match="overlapping"):
        apply_decision(released, decision, 0)
