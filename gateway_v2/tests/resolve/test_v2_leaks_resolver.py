"""v2-enforcement-leaks Group A/E (resolve): ``finding_index`` + REDACT span coalescing.

Spec ``v2-enforcement-leaks`` task 17.2; design Property 11 (resolver half) and Property 14;
bugfix clauses 2.13, 3.4 and 3.5.

* Coalescing unit cases for ``_normalise_redactions``: overlapping and identical
  same-replacement spans fold into their union, spans that only touch (a delta or item
  boundary) are never merged, non-overlapping out-of-order input keeps its order, and
  different replacements are never fused. One end-to-end case runs through ``resolve``.
* ``finding_index`` correctness: every ``per_finding[i].finding_index`` names a finding in
  ``Decision.findings`` with the same detector; SKIPPED and non-selecting findings get no
  disposition and the indices stay aligned.
* Preservation (Property 14, clauses 3.4/3.5): for non-overlapping transformations the fixed
  ``resolve`` equals a FROZEN pre-fix reference once ``finding_index`` is stripped, and the
  transformation tuple is exactly equal. The C22 "adding a REDACT rule never removes a BLOCK"
  property is re-checked under overlap-prone findings so the coalescing arm is exercised.
* Property 11 (resolver half, clause 2.13): ``resolve(findings, plan, phase)`` equals
  ``resolve`` over the plan filtered to the rules admissible in ``phase``.

House idiom: seeded ``random.Random``, >= 10,000 iterations per property, no hypothesis. Each
iteration draws its own case seed from a fixed master seed and every assertion message carries
that case seed, so a counter-example reproduces with ``random.Random(case_seed)``. Generators
are REUSED from ``tests/resolve/test_resolver_gw07.py`` (rules, findings, plans, the
independent phase-admission and Action->Disposition maps); this file only narrows categories
to a small pool so rules select findings often, and lays spans out disjoint or overlapping.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import replace

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
    Phase,
    Rule,
    RuleScope,
    Span,
    Transformation,
)
from gateway_v2.resolve import resolve
from gateway_v2.resolve.resolver import _normalise_redactions
from tests.resolve.test_resolver_gw07 import (
    _ACTION_TO_DISPOSITION,
    _PHASES,
    ITERATIONS,
    _example_plan,
    _executed,
    _expected_finding_disposition,
    _phase_admits,
    _plan,
    _plan_with_rules,
    _rng_finding,
    _rng_rules,
    _rule,
    _unavailable,
)

_PLACEHOLDER = "[REDACTED]"

# A small category pool so random rules select random findings often; with all nine
# categories most generated cases would resolve to ALLOW and prove nothing.
_POOL = (Category.PII, Category.SECRET, Category.CREDENTIAL)
_REDACTING = (Action.REDACT, Action.REWRITE)


def _redact(start: int, end: int, replacement: str = _PLACEHOLDER) -> Transformation:
    return Transformation(kind="redact", span=Span(start=start, end=end), replacement=replacement)


# --- generators (narrowing the gw07 generators) --------------------------------------------------


def _rng_pool_rules(rng: random.Random) -> tuple[Rule, ...]:
    """gw07 random rules (every mode/action/scope/posture), categories from ``_POOL``.

    Half the rules are re-drawn as ENFORCE REDACT/REWRITE so redaction (the arm this card
    changed) is reached often; the other half keep gw07's full mode/action spread.
    """
    rules: list[Rule] = []
    for rule in _rng_rules(rng):
        rule = replace(rule, category=rng.choice(_POOL))
        if rng.random() < 0.5:
            rule = replace(rule, mode=Mode.ENFORCE, action=rng.choice(_REDACTING))
        rules.append(rule)
    return tuple(rules)


def _rng_pool_findings(rng: random.Random) -> list[Finding]:
    """0-5 gw07 random findings (every status), categories from ``_POOL``, spans cleared."""
    return [
        replace(_rng_finding(rng), category=rng.choice(_POOL), spans=())
        for _ in range(rng.randint(0, 5))
    ]


def _deal(
    rng: random.Random,
    findings: list[Finding],
    spans: list[Span],
) -> tuple[Finding, ...]:
    """Deal ``spans`` (shuffled) across ``findings`` at random."""
    if not findings:
        return ()
    rng.shuffle(spans)
    buckets: list[list[Span]] = [[] for _ in findings]
    for span in spans:
        buckets[rng.randrange(len(findings))].append(span)
    return tuple(
        replace(finding, spans=tuple(bucket))
        for finding, bucket in zip(findings, buckets, strict=True)
    )


def _rng_disjoint_findings(rng: random.Random) -> tuple[Finding, ...]:
    """Findings whose spans are pairwise non-overlapping ACROSS all findings.

    Gaps are often 0 (the spans touch, as at a delta boundary), never negative, and every
    span is non-empty, so no two spans overlap or coincide: nothing can coalesce.
    """
    findings = _rng_pool_findings(rng)
    spans: list[Span] = []
    cursor = rng.randint(0, 3)
    for _ in range(rng.randint(0, 8)):
        start = cursor + rng.choice((0, 0, 1, 2, 3))
        end = start + rng.randint(1, 4)
        spans.append(Span(start=start, end=end))
        cursor = end
    return _deal(rng, findings, spans)


def _rng_overlapping_findings(rng: random.Random) -> tuple[Finding, ...]:
    """Findings whose spans freely overlap, coincide, touch or are empty."""
    findings = _rng_pool_findings(rng)
    spans: list[Span] = []
    for _ in range(rng.randint(0, 8)):
        start = rng.randint(0, 10)
        spans.append(Span(start=start, end=start + rng.randint(0, 5)))
    return _deal(rng, findings, spans)


def _overlap(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


# --- frozen pre-fix reference --------------------------------------------------------------------


def _frozen_pre_fix_resolve(
    findings: Sequence[Finding],
    plan: ExecutionPlan,
    phase: Phase,
) -> Decision:
    """FROZEN copy of the pre-fix ``resolve`` (commit 8328a399): no index, no normalisation.

    Self-contained on purpose: it imports no resolve-package helper, so a later edit to
    ``resolve/conflict.py`` or ``resolve/decision.py`` cannot move the reference with it.
    Phase admission and the Action->Disposition map come from the independent gw07 copies.
    """
    selected = tuple(
        rule
        for rule in plan.rules
        if _phase_admits(rule.scope, phase) and rule.mode is not Mode.OFF
    )
    per_finding: list[FindingDisposition] = []
    transformations: list[Transformation] = []
    deciding_rules: list[str] = []
    unavailable_detectors: list[str] = []
    for finding in findings:
        rules = tuple(rule for rule in selected if rule.category == finding.category)
        candidate: Disposition | None = None
        rule_id: str | None = None
        if finding.status is FindingStatus.SKIPPED:
            candidate = None
        elif finding.status is FindingStatus.UNAVAILABLE:
            if finding.detector not in unavailable_detectors:
                unavailable_detectors.append(finding.detector)
            if any(
                rule.mode is Mode.ENFORCE and rule.on_unavailable is FailurePosture.FAIL_CLOSED
                for rule in rules
            ):
                candidate = Disposition.BLOCK
        else:
            action = _frozen_winning_action(rules)
            if action is not None and _ACTION_TO_DISPOSITION[action] is not Disposition.ALLOW:
                candidate = _ACTION_TO_DISPOSITION[action]
                rule_id = next((rule.rule_id for rule in rules if rule.action == action), "")
        if candidate is None:
            continue
        per_finding.append(FindingDisposition(detector=finding.detector, disposition=candidate))
        if rule_id is not None:
            deciding_rules.append(rule_id)
        if candidate is Disposition.REDACT:
            transformations.extend(
                Transformation(kind="redact", span=span, replacement=_PLACEHOLDER)
                for span in finding.spans
            )
    ranks = (Disposition.ALLOW, Disposition.FLAG, Disposition.REDACT, Disposition.BLOCK)
    disposition = max(
        (fd.disposition for fd in per_finding),
        key=ranks.index,
        default=Disposition.ALLOW,
    )
    return Decision(
        disposition=disposition,
        per_finding=tuple(per_finding),
        transformations=tuple(transformations),
        findings=tuple(findings),
        plan_version=plan.version,
        deciding_rules=tuple(deciding_rules),
        unavailable_detectors=tuple(unavailable_detectors),
    )


def _frozen_winning_action(rules: Sequence[Rule]) -> Action | None:
    """Pre-fix winner: highest-priority ENFORCE rule, name-free most-restrictive tie-break."""
    enforcing = [rule for rule in rules if rule.mode is Mode.ENFORCE]
    if not enforcing:
        return None
    top = max(rule.priority for rule in enforcing)
    tied = [rule for rule in enforcing if rule.priority == top]
    ranks = (Disposition.ALLOW, Disposition.FLAG, Disposition.REDACT, Disposition.BLOCK)
    best = max((_ACTION_TO_DISPOSITION[rule.action] for rule in tied), key=ranks.index)
    return next(rule.action for rule in tied if _ACTION_TO_DISPOSITION[rule.action] is best)


def _strip_index(decision: Decision) -> Decision:
    """``decision`` with every ``finding_index`` reset to the pre-fix default ``None``."""
    return replace(
        decision,
        per_finding=tuple(replace(fd, finding_index=None) for fd in decision.per_finding),
    )


# --- 1. coalescing unit cases --------------------------------------------------------------------


def test_overlapping_spans_merge_into_their_union() -> None:
    assert _normalise_redactions((_redact(0, 5), _redact(3, 8))) == (_redact(0, 8),)
    # Containment and a chain of overlaps also fold to one union.
    assert _normalise_redactions((_redact(0, 10), _redact(2, 4))) == (_redact(0, 10),)
    assert _normalise_redactions((_redact(4, 7), _redact(0, 3), _redact(2, 5))) == (_redact(0, 7),)


def test_identical_spans_collapse_to_one() -> None:
    assert _normalise_redactions((_redact(2, 6), _redact(2, 6))) == (_redact(2, 6),)
    assert _normalise_redactions((_redact(3, 3), _redact(3, 3))) == (_redact(3, 3),)


def test_touching_spans_across_a_delta_boundary_are_never_merged() -> None:
    # Two chat deltas "abcd" | "efgh": the boundary is offset 4.
    deltas = ("abcd", "efgh")
    boundary = len(deltas[0])
    touching = (_redact(0, boundary), _redact(boundary, boundary + len(deltas[1])))
    assert _normalise_redactions(touching) == touching

    # End to end: one PII value per delta, both REDACT, the spans touch at the boundary.
    left = _executed(Category.PII, "pii-a", spans=(Span(start=0, end=boundary),))
    right = _executed(Category.PII, "pii-b", spans=(Span(start=boundary, end=8),))
    plan = _example_plan((_rule(Category.PII, Action.REDACT),))
    decision = resolve((left, right), plan, Phase.INPUT)
    assert decision.transformations == touching
    for transformation in decision.transformations:
        # Each replacement stays inside exactly one delta.
        assert transformation.span.end <= boundary or transformation.span.start >= boundary


def test_non_overlapping_out_of_order_input_keeps_its_order() -> None:
    out_of_order = (_redact(10, 12), _redact(0, 3), _redact(5, 7))
    assert _normalise_redactions(out_of_order) == out_of_order
    assert _normalise_redactions(()) == ()


def test_overlaps_with_different_replacements_are_both_kept() -> None:
    different = (_redact(0, 5), _redact(3, 8, replacement="[MASKED]"))
    assert _normalise_redactions(different) == different


def test_resolve_two_overlapping_pii_findings_yield_one_union_transformation() -> None:
    first = _executed(Category.PII, "pii-email", spans=(Span(start=0, end=6),))
    second = _executed(Category.PII, "pii-phone", spans=(Span(start=3, end=10),))
    plan = _example_plan((_rule(Category.PII, Action.REDACT, rule_id="pii-redact"),))
    decision = resolve((first, second), plan, Phase.INPUT)
    assert decision.disposition is Disposition.REDACT
    assert decision.transformations == (_redact(0, 10),)
    assert tuple(fd.finding_index for fd in decision.per_finding) == (0, 1)
    assert decision.deciding_rules == ("pii-redact", "pii-redact")


# --- 2. finding_index correctness ----------------------------------------------------------------


def test_finding_index_skips_skipped_and_non_selecting_findings() -> None:
    skipped = Finding(
        detector="pii-skipped",
        detector_version="v1",
        category=Category.PII,
        status=FindingStatus.SKIPPED,
        confidence=None,
        spans=(Span(start=0, end=2),),
        evidence=None,
    )
    non_selecting = _executed(Category.TOXICITY, "tox-det")
    redacted = _executed(Category.PII, "pii-det", spans=(Span(start=4, end=7),))
    blocked = _unavailable(Category.SECRET, "secret-det")
    plan = _example_plan(
        (
            _rule(Category.PII, Action.REDACT, rule_id="pii-rule"),
            _rule(
                Category.SECRET,
                Action.BLOCK,
                rule_id="secret-rule",
                on_unavailable=FailurePosture.FAIL_CLOSED,
            ),
        ),
    )
    findings = (skipped, non_selecting, redacted, blocked)
    decision = resolve(findings, plan, Phase.INPUT)
    assert decision.findings == findings
    assert tuple(fd.finding_index for fd in decision.per_finding) == (2, 3)
    assert tuple(fd.detector for fd in decision.per_finding) == ("pii-det", "secret-det")
    assert decision.disposition is Disposition.BLOCK


def test_property_finding_index_points_at_a_same_detector_finding() -> None:
    # Validates: Requirements 2.1 (Property 3 precondition), 3.4
    master = random.Random(0x1702_0001)
    for _ in range(ITERATIONS):
        case_seed = master.getrandbits(64)
        rng = random.Random(case_seed)
        findings = _rng_overlapping_findings(rng)
        plan = _plan(rng, _rng_pool_rules(rng))
        phase = rng.choice(_PHASES)
        decision = resolve(findings, plan, phase)
        msg = f"case_seed={case_seed:#x}"
        expected = tuple(
            index
            for index, finding in enumerate(findings)
            if _expected_finding_disposition(finding, plan, phase) is not None
        )
        got = tuple(fd.finding_index for fd in decision.per_finding)
        assert got == expected, msg
        for fd in decision.per_finding:
            assert fd.finding_index is not None, msg
            assert decision.findings[fd.finding_index].detector == fd.detector, msg


# --- 3. preservation (Property 14, clauses 3.4/3.5) ----------------------------------------------


def test_property_14_non_overlapping_decision_equals_frozen_pre_fix() -> None:
    # Property 14 (resolver half): for non-overlapping transformations, resolve() equals the
    # frozen pre-fix decision apart from finding_index; transformations are exactly equal.
    # Validates: Requirements 3.4, 3.5
    master = random.Random(0x1702_0014)
    with_transformations = 0
    with_touching = 0
    for _ in range(ITERATIONS):
        case_seed = master.getrandbits(64)
        rng = random.Random(case_seed)
        findings = _rng_disjoint_findings(rng)
        plan = _plan(rng, _rng_pool_rules(rng))
        phase = rng.choice(_PHASES)
        msg = f"case_seed={case_seed:#x}"

        decision = resolve(findings, plan, phase)
        reference = _frozen_pre_fix_resolve(findings, plan, phase)

        spans = [t.span for t in reference.transformations]
        assert not any(_overlap(a, b) for i, a in enumerate(spans) for b in spans[i + 1 :]), msg
        assert decision.transformations == reference.transformations, msg
        assert _strip_index(decision) == reference, msg
        assert all(fd.finding_index is not None for fd in decision.per_finding), msg

        if spans:
            with_transformations += 1
        ends = {span.end for span in spans}
        if any(span.start in ends for span in spans):
            with_touching += 1
    # Non-vacuity: the property must have exercised redaction, including touching spans.
    assert with_transformations >= ITERATIONS // 20, with_transformations
    assert with_touching >= ITERATIONS // 100, with_touching


def test_property_c22_adding_a_redact_rule_never_removes_a_block() -> None:
    # C22 under the fixed resolver, with overlap-prone findings so coalescing runs. Reuses the
    # gw07 Property 5 approach: the added ENFORCE REDACT rule's priority is at or below every
    # existing rule, so it cannot win a same-finding priority contest (that is the C22
    # precedence mechanism, not a BLOCK removal); a tie goes to the most-restrictive action.
    # Validates: Requirements 3.4
    master = random.Random(0x1702_0022)
    blocked = 0
    for _ in range(ITERATIONS):
        case_seed = master.getrandbits(64)
        rng = random.Random(case_seed)
        findings = _rng_overlapping_findings(rng)
        plan = _plan(rng, _rng_pool_rules(rng))
        phase = rng.choice(_PHASES)
        base = resolve(findings, plan, phase)
        if base.disposition is not Disposition.BLOCK:
            continue
        blocked += 1
        floor = min((rule.priority for rule in plan.rules), default=0)
        added = Rule(
            rule_id="redact-added",
            category=rng.choice(_POOL),
            mode=Mode.ENFORCE,
            action=Action.REDACT,
            threshold=None,
            priority=floor - rng.randint(0, 1),
            scope=rng.choice((RuleScope.INPUT, RuleScope.OUTPUT, RuleScope.BOTH)),
            on_unavailable=rng.choice((FailurePosture.FAIL_OPEN, FailurePosture.FAIL_CLOSED)),
            surfaces=frozenset(),
        )
        after = resolve(findings, _plan_with_rules(plan, (*plan.rules, added)), phase)
        assert after.disposition is Disposition.BLOCK, f"case_seed={case_seed:#x}"
    assert blocked >= ITERATIONS // 20, blocked


# --- 4. Property 11 (resolver half, clause 2.13) -------------------------------------------------


def test_property_11_resolve_selects_only_phase_admissible_rules() -> None:
    # Property 11 (resolver half): resolve(findings, plan, phase) uses only rules scoped
    # `phase` or `both`; equivalently it equals resolve over the plan filtered to them.
    # Validates: Requirements 2.13
    master = random.Random(0x1702_0011)
    scope_mattered = 0
    for _ in range(ITERATIONS):
        case_seed = master.getrandbits(64)
        rng = random.Random(case_seed)
        findings = _rng_overlapping_findings(rng)
        plan = _plan(rng, _rng_pool_rules(rng))
        phase = rng.choice(_PHASES)
        msg = f"case_seed={case_seed:#x}"

        decision = resolve(findings, plan, phase)
        admissible = tuple(rule for rule in plan.rules if _phase_admits(rule.scope, phase))
        filtered = resolve(findings, _plan_with_rules(plan, admissible), phase)
        assert decision == filtered, msg
        admissible_ids = {rule.rule_id for rule in admissible}
        assert set(decision.deciding_rules) <= admissible_ids, msg

        # Non-vacuity: count cases where honouring an off-phase rule would have changed it.
        widened = tuple(replace(rule, scope=RuleScope.BOTH) for rule in plan.rules)
        if resolve(findings, _plan_with_rules(plan, widened), phase) != decision:
            scope_mattered += 1
    assert scope_mattered >= ITERATIONS // 50, scope_mattered
