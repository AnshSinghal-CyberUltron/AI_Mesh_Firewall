"""GW33 task 5: REWRITE in the resolver, the review flag, identity joins (Properties 3, 4, 8)."""
from __future__ import annotations

import random
from dataclasses import replace

from gateway_v2.domain import (
    Action,
    Category,
    Disposition,
    ExecutionPlan,
    FailurePosture,
    Finding,
    FindingStatus,
    Mode,
    Phase,
    Rule,
    RuleCondition,
    RuleKind,
    RuleScope,
    Span,
    StreamingMode,
    most_restrictive,
)
from gateway_v2.resolve import resolve
from tests.resolve.test_v2_leaks_resolver import (
    _frozen_pre_fix_resolve,
    _plan,
    _rng_disjoint_findings,
    _rng_pool_rules,
)

_ITERATIONS = 2_000


def _plan_of(*rules: Rule) -> ExecutionPlan:
    return ExecutionPlan(
        org_id="o", epoch=1, sequence=1, content_hash="abcdef0123456789abcdef", compiled_at=0.0,
        rules=rules, required_detectors=frozenset(), streaming_mode=StreamingMode.STRICT_WITHHOLD,
        integrity_locked=True,
    )


def _rule(
    rule_id: str,
    category: Category,
    action: Action,
    *,
    priority: int = 1,
    template: str = "",
    condition: RuleCondition | None = None,
    on_unavailable: FailurePosture = FailurePosture.FAIL_CLOSED,
) -> Rule:
    return Rule(
        rule_id, category, Mode.ENFORCE, action, None, priority, RuleScope.OUTPUT, on_unavailable,
        frozenset(), condition, template,
    )


def _finding(
    category: Category,
    detector: str,
    *spans: Span,
    status: FindingStatus = FindingStatus.EXECUTED,
) -> Finding:
    confidence = 1.0 if status is FindingStatus.EXECUTED else None
    return Finding(detector, "v", category, status, confidence, spans, None)


def _builtin(klass: str) -> RuleCondition:
    return RuleCondition(RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", klass, "")


def test_rewrite_renders_the_declared_template_per_span() -> None:
    rule = _rule("r-ip", Category.IP_LEAK, Action.REWRITE, template="[{category}:{rule_id}]",
                 condition=_builtin("output.ip_leak"))
    decision = resolve(
        (_finding(Category.IP_LEAK, "r-ip", Span(0, 4), Span(10, 14)),),
        _plan_of(rule), Phase.OUTPUT,
    )
    assert decision.disposition is Disposition.REWRITE
    assert [(t.kind, t.replacement) for t in decision.transformations] == [
        ("rewrite", "[ip_leak:r-ip]"), ("rewrite", "[ip_leak:r-ip]"),
    ]
    assert decision.deciding_rules == ("r-ip",)
    assert decision.review_required is True


def test_rewrite_with_no_template_falls_closed_to_block() -> None:
    rule = _rule("r-ip", Category.IP_LEAK, Action.REWRITE, template="",
                 condition=_builtin("output.ip_leak"))
    decision = resolve((_finding(Category.IP_LEAK, "r-ip", Span(0, 4)),), _plan_of(rule),
                       Phase.OUTPUT)
    assert decision.disposition is Disposition.BLOCK
    assert decision.codes == ("rewrite_template_missing",)
    assert decision.transformations == ()


def test_unlocalised_hallucination_redact_or_rewrite_blocks_but_other_categories_do_not() -> None:
    for action in (Action.REDACT, Action.REWRITE):
        rule = _rule("g", Category.HALLUCINATION, action, template="[x]",
                     condition=_builtin("output.grounding.lexical"))
        decision = resolve((_finding(Category.HALLUCINATION, "g"),), _plan_of(rule), Phase.OUTPUT)
        assert decision.disposition is Disposition.BLOCK
    pii = _rule("p", Category.PII, Action.REDACT)
    decision = resolve((_finding(Category.PII, "x"),), _plan_of(pii), Phase.OUTPUT)
    assert decision.disposition is Disposition.REDACT and decision.transformations == ()
    skipped = _finding(Category.HALLUCINATION, "g", status=FindingStatus.SKIPPED)
    rule = _rule("g", Category.HALLUCINATION, Action.BLOCK, condition=_builtin("output.grounding"))
    assert resolve((skipped,), _plan_of(rule), Phase.OUTPUT).disposition is Disposition.ALLOW


def test_identity_join_beats_a_higher_priority_customer_row() -> None:
    """D-32: a fallback-category customer DETECTOR row can never govern a built-in finding."""
    builtin = _rule("out_exfil", Category.EXFIL, Action.BLOCK, priority=200,
                    condition=_builtin("output.exfil"))
    for action in (Action.ALLOW, Action.REDACT):
        customer = _rule("cust", Category.EXFIL, action, priority=1000,
                         condition=_builtin("customer.thing"))
        plan = _plan_of(builtin, customer)
        by_id = resolve((_finding(Category.EXFIL, "out_exfil", Span(0, 5)),), plan, Phase.OUTPUT)
        assert by_id.disposition is Disposition.BLOCK
        # Even a finding with an unrelated detector id resolves through the built-in rule only.
        by_category = resolve((_finding(Category.EXFIL, "other", Span(0, 5)),), plan,
                              Phase.OUTPUT)
        assert by_category.disposition is Disposition.BLOCK


def test_legacy_condition_less_rule_is_reached_by_id_only() -> None:
    legacy = _rule("output_ip_leakage_x", Category.EXFIL, Action.REDACT)
    customer = _rule("cust", Category.EXFIL, Action.ALLOW, priority=1000,
                     condition=_builtin("customer.thing"))
    plan = _plan_of(legacy, customer)
    by_id = resolve((_finding(Category.EXFIL, "output_ip_leakage_x", Span(0, 3)),), plan,
                    Phase.OUTPUT)
    assert by_id.disposition is Disposition.REDACT
    unrelated = resolve((_finding(Category.EXFIL, "someone", Span(0, 3)),), plan, Phase.OUTPUT)
    assert unrelated.disposition is Disposition.ALLOW


def test_property_8_review_flag_consistency() -> None:
    """Feature: gw33-output-parity, Property 8: Review flag consistency. Validates 4.1, 4.5, 4.6."""
    # Feature: gw33-output-parity, Property 8: Review flag consistency
    master = random.Random(33_008)
    for _ in range(_ITERATIONS):
        rng = random.Random(master.getrandbits(64))
        findings = _rng_disjoint_findings(rng)
        plan = _plan(rng, _rng_pool_rules(rng))
        for phase in (Phase.INPUT, Phase.OUTPUT):
            decision = resolve(findings, plan, phase)
            expected = phase is Phase.OUTPUT and any(
                item.disposition is not Disposition.ALLOW for item in decision.per_finding
            )
            assert decision.review_required is expected
        # Same input resolved twice (INCREMENTAL vs STRICT planning) gives the same flag.
        strict = replace(plan, streaming_mode=StreamingMode.STRICT_WITHHOLD)
        assert (
            resolve(findings, plan, Phase.OUTPUT).review_required
            == resolve(findings, strict, Phase.OUTPUT).review_required
        )


def test_property_3_rewrite_never_lowers_redact_or_block() -> None:
    """Feature: gw33-output-parity, Property 3: Disposition order and no downgrade. Validates 5.2."""
    # Feature: gw33-output-parity, Property 3: Disposition order and no downgrade
    rng = random.Random(33_103)
    actions = (Action.FLAG, Action.REDACT, Action.REWRITE, Action.BLOCK)
    order = (Disposition.ALLOW, Disposition.FLAG, Disposition.REDACT, Disposition.REWRITE,
             Disposition.BLOCK)
    for _ in range(_ITERATIONS):
        chosen = [rng.choice(actions) for _ in range(rng.randint(1, 4))]
        rules = tuple(
            _rule(f"r{i}", Category.PII, action, priority=rng.randint(0, 2), template="[x]")
            for i, action in enumerate(chosen)
        )
        findings = tuple(
            _finding(Category.PII, f"r{i}", Span(i * 10, i * 10 + 3)) for i in range(len(chosen))
        )
        decision = resolve(findings, _plan_of(*rules), Phase.OUTPUT)
        dispositions = tuple(item.disposition for item in decision.per_finding)
        assert decision.disposition is most_restrictive(dispositions)
        assert order.index(decision.disposition) >= max(order.index(d) for d in dispositions)


def test_property_4_plans_without_rewrite_match_the_frozen_reference() -> None:
    """Feature: gw33-output-parity, Property 4: Preservation. Validates 5.8, 10.6."""
    # Feature: gw33-output-parity, Property 4: Preservation
    master = random.Random(33_004)
    compared = 0
    for _ in range(_ITERATIONS):
        rng = random.Random(master.getrandbits(64))
        findings = _rng_disjoint_findings(rng)
        plan = _plan(rng, _rng_pool_rules(rng))
        phase = rng.choice((Phase.INPUT, Phase.OUTPUT))
        decision = resolve(findings, plan, phase)
        reference = _frozen_pre_fix_resolve(findings, plan, phase)
        assert decision.disposition is reference.disposition
        assert decision.transformations == reference.transformations
        assert decision.deciding_rules == reference.deciding_rules
        assert decision.unavailable_detectors == reference.unavailable_detectors
        assert decision.codes == ()
        if phase is Phase.INPUT:
            assert decision.review_required is False
        compared += decision.disposition is not Disposition.ALLOW
    assert compared > _ITERATIONS // 10
