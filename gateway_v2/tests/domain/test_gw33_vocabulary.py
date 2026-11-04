"""GW33 task 1: domain vocabulary (REWRITE, review flag, codes, combine_decisions, D-28 predicate)."""
from __future__ import annotations

import random

import pytest

from gateway_v2.domain import (
    Action,
    Category,
    Decision,
    Disposition,
    FailurePosture,
    Finding,
    FindingDisposition,
    FindingStatus,
    GroundingMode,
    Mode,
    Rule,
    RuleCondition,
    RuleKind,
    RuleScope,
    Span,
    Transformation,
    builtin_output_detector,
    combine_decisions,
    most_restrictive,
)

_ITERATIONS = 2_000
_ORDER = (
    Disposition.ALLOW,
    Disposition.FLAG,
    Disposition.REDACT,
    Disposition.REWRITE,
    Disposition.BLOCK,
)


def _finding(category: Category = Category.PII) -> Finding:
    return Finding("d", "v", category, FindingStatus.EXECUTED, 1.0, (Span(0, 1),), None)


def _decision(
    dispositions: tuple[Disposition, ...],
    *,
    review: bool = False,
    codes: tuple[str, ...] = (),
    rules: tuple[str, ...] = (),
    transformations: tuple[Transformation, ...] = (),
) -> Decision:
    findings = tuple(_finding() for _ in dispositions)
    return Decision(
        disposition=most_restrictive(dispositions),
        per_finding=tuple(FindingDisposition("d", d, i) for i, d in enumerate(dispositions)),
        transformations=transformations,
        findings=findings,
        plan_version="1.1.x",
        deciding_rules=rules,
        unavailable_detectors=(),
        review_required=review,
        codes=codes,
    )


def test_new_categories_and_disposition_spellings() -> None:
    assert Category.IP_LEAK == "ip_leak"
    assert Category.HALLUCINATION == "hallucination"
    assert Category.CANARY == "canary"
    assert Disposition.REWRITE == "rewrite"
    assert {mode.value for mode in GroundingMode} == {"lexical", "model"}


def test_rank_order_is_allow_flag_redact_rewrite_block() -> None:
    for index, lower in enumerate(_ORDER):
        for higher in _ORDER[index + 1 :]:
            assert most_restrictive((lower, higher)) is higher
            assert most_restrictive((higher, lower)) is higher


def test_property_3_disposition_order_and_no_downgrade() -> None:
    """Feature: gw33-output-parity, Property 3: Disposition order and no downgrade. Validates 5.2."""
    # Feature: gw33-output-parity, Property 3: Disposition order and no downgrade
    rng = random.Random(33_003)
    for _ in range(_ITERATIONS):
        items = tuple(rng.choice(_ORDER) for _ in range(rng.randint(1, 8)))
        expected = max(items, key=_ORDER.index)
        assert most_restrictive(items) is expected
        assert most_restrictive((*items, Disposition.BLOCK)) is Disposition.BLOCK
        assert _ORDER.index(most_restrictive((*items, Disposition.REWRITE))) >= _ORDER.index(
            most_restrictive(items),
        )


def test_existing_constructions_stay_valid_with_defaults() -> None:
    decision = _decision((Disposition.REDACT,))
    assert decision.review_required is False
    assert decision.codes == ()
    rule = Rule("r", Category.PII, Mode.ENFORCE, Action.REDACT, None, 1, RuleScope.OUTPUT,
                FailurePosture.FAIL_CLOSED, frozenset())
    assert rule.rewrite_template == ""


def test_inconsistent_review_flag_and_unknown_code_are_rejected() -> None:
    with pytest.raises(ValueError):
        _decision((Disposition.ALLOW,), review=True)
    with pytest.raises(ValueError):
        Decision(Disposition.ALLOW, (), (), (), "v", (), (), review_required=True)
    with pytest.raises(ValueError):
        _decision((Disposition.FLAG,), codes=("not_a_code",))
    assert _decision((Disposition.FLAG,), review=True, codes=("edit_overlap_redacted",)).codes


def test_combine_decisions_semantics() -> None:
    kept = Transformation("redact", Span(0, 1), "[REDACTED]")
    a = _decision((Disposition.FLAG,), review=True, rules=("r1",), codes=("edit_overlap_redacted",))
    b = _decision(
        (Disposition.REWRITE, Disposition.REDACT),
        rules=("r2", "r1"),
        codes=("rewrite_template_missing",),
        transformations=(kept,),
    )
    merged = combine_decisions(a, b)
    assert merged.disposition is Disposition.REWRITE
    assert merged.review_required is True
    assert merged.deciding_rules == ("r1", "r2")
    assert merged.codes == ("edit_overlap_redacted", "rewrite_template_missing")
    assert merged.transformations == (kept,)
    assert len(merged.findings) == 3
    assert [item.finding_index for item in merged.per_finding] == [0, 1, 2]


def test_combine_decisions_property_most_restrictive_and_or() -> None:
    # Feature: gw33-output-parity, Property 3: Disposition order and no downgrade
    rng = random.Random(33_103)
    for _ in range(_ITERATIONS):
        left = tuple(rng.choice(_ORDER[1:]) for _ in range(rng.randint(1, 4)))
        right = tuple(rng.choice(_ORDER[1:]) for _ in range(rng.randint(1, 4)))
        a = _decision(left, review=rng.random() < 0.5)
        b = _decision(right, review=rng.random() < 0.5)
        merged = combine_decisions(a, b)
        assert merged.disposition is most_restrictive((*left, *right))
        assert merged.review_required == (a.review_required or b.review_required)


def _rule(kind: RuleKind | None, detector_class: str = "", category: Category = Category.EXFIL) -> Rule:
    condition = (
        None
        if kind is None
        else RuleCondition(kind, "", RuleScope.OUTPUT, ("k",), "p", detector_class, "")
    )
    return Rule("r", category, Mode.ENFORCE, Action.BLOCK, None, 1, RuleScope.OUTPUT,
                FailurePosture.FAIL_CLOSED, frozenset(), condition)


def test_builtin_output_detector_reserved_namespace_only() -> None:
    assert builtin_output_detector(_rule(RuleKind.DETECTOR, "output.exfil")) == "exfil"
    assert builtin_output_detector(_rule(RuleKind.DETECTOR, "output.ip_leak")) == "ip_leak"
    assert builtin_output_detector(_rule(RuleKind.DETECTOR, "output.canary")) == "canary"
    assert builtin_output_detector(_rule(RuleKind.DETECTOR, "output.grounding.lexical")) == "grounding"
    assert builtin_output_detector(_rule(RuleKind.DETECTOR, "output.grounding.model:m-1")) == "grounding"


@pytest.mark.parametrize("category", list(Category))
def test_customer_rules_never_have_a_family(category: Category) -> None:
    for kind in (RuleKind.KEYWORDS, RuleKind.REGEX, RuleKind.PATTERN):
        assert builtin_output_detector(_rule(kind, "output.exfil", category)) is None
    assert builtin_output_detector(_rule(None, "", category)) is None
    for klass in ("pii", "", "output.", "output.other", "outputxexfil", "x.output.exfil", "output.exfil X"):
        assert builtin_output_detector(_rule(RuleKind.DETECTOR, klass, category)) is None
