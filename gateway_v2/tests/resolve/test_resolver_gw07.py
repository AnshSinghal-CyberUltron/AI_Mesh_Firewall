"""GW07: the single pure resolver — property + example/edge tests (one file).

This is the ONE test file for the shipped ``gateway_v2.resolve`` package. It
carries, in a single coherent write (B6/B7/B8/B9 single-test-file lesson), ALL
of sub-tasks 2.1-2.9: the seeded generator strategy + per-iteration structural
invariants bundle (2.1), the six universal property tests P1-P6 (2.2-2.7), the
LGW07 example tests (2.8), and the edge/identity tests (2.9).

Idiom: a seeded ``random.Random`` driven for >= 10,000 iterations per property,
NO hypothesis (the gateway_v2 house idiom, matching every shipped B-card). The
resolver is pure and total, so determinism and the metamorphic no-ops are
checked directly on its output. The shipped domain and resolve types are
constructed directly and re-implemented nowhere; the expected winning
disposition is recomputed independently (not by calling the helper under test)
where a property demands a cross-check.
"""

from __future__ import annotations

import random

from gateway_v2.domain.category import Category
from gateway_v2.domain.decision import Decision, Disposition, most_restrictive
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.plan import (
    Action,
    ExecutionPlan,
    FailurePosture,
    Mode,
    Phase,
    Rule,
    RuleScope,
    StreamingMode,
)
from gateway_v2.resolve import PlanResolver, resolve

# The house-idiom budget: a seeded Random driven for >= 10,000 iterations per
# property, no hypothesis. Seeds are fixed so any counter-example reproduces
# from the file alone.
ITERATIONS = 10_000

# A SMALL detector pool so category/detector collisions and category-sharing
# occur frequently across generated findings.
_DETECTORS = ("d0", "d1", "d2", "d3")

_CATEGORIES = tuple(Category)
_STATUSES = (
    FindingStatus.EXECUTED,
    FindingStatus.SKIPPED,
    FindingStatus.UNAVAILABLE,
)
_MODES = (Mode.OFF, Mode.MONITOR, Mode.ENFORCE)
_ACTIONS = (Action.ALLOW, Action.FLAG, Action.REDACT, Action.BLOCK, Action.REWRITE)
_SCOPES = (RuleScope.INPUT, RuleScope.OUTPUT, RuleScope.BOTH)
_POSTURES = (FailurePosture.FAIL_OPEN, FailurePosture.FAIL_CLOSED)
_PHASES = (Phase.INPUT, Phase.OUTPUT)

# Mirror of the resolve-layer Action->Disposition map, recomputed independently
# here (NOT imported from the helper under test) so the properties cross-check
# the production mapping rather than echo it.
_ACTION_TO_DISPOSITION = {
    Action.ALLOW: Disposition.ALLOW,
    Action.FLAG: Disposition.FLAG,
    Action.REDACT: Disposition.REDACT,
    Action.BLOCK: Disposition.BLOCK,
    Action.REWRITE: Disposition.REDACT,
}


# --- 2.1 generators ------------------------------------------------------------------------------


def _rng_spans(rng: random.Random) -> tuple[Span, ...]:
    """0-3 ascending, non-overlapping spans."""
    count = rng.randint(0, 3)
    spans: list[Span] = []
    cursor = rng.randint(0, 5)
    for _ in range(count):
        start = cursor + rng.randint(0, 3)
        end = start + rng.randint(1, 4)
        spans.append(Span(start=start, end=end))
        cursor = end
    return tuple(spans)


def _rng_finding(rng: random.Random) -> Finding:
    """A random, valid ``Finding`` honouring the domain ``__post_init__``.

    Status roughly equal among EXECUTED/SKIPPED/UNAVAILABLE; an EXECUTED finding
    gets a confidence in [0, 1] and any other status gets ``None``; detectors are
    drawn from a small pool so collisions occur; occasional short evidence.
    """
    status = rng.choice(_STATUSES)
    confidence = rng.random() if status is FindingStatus.EXECUTED else None
    evidence = rng.choice((None, "e", "short-evidence")) if rng.random() < 0.3 else None
    return Finding(
        detector=rng.choice(_DETECTORS),
        detector_version=rng.choice(("v1", "v2")),
        category=rng.choice(_CATEGORIES),
        status=status,
        confidence=confidence,
        spans=_rng_spans(rng),
        evidence=evidence,
    )


def _rng_rule(rng: random.Random, index: int) -> Rule:
    """A random ``Rule`` spanning every Mode/Action/Scope/Posture.

    ``priority`` is drawn from a SMALL range (0-3) so equal-priority ties are
    frequent; ``surfaces`` is empty (the resolver ignores it).
    """
    return Rule(
        rule_id=f"r{index}",
        category=rng.choice(_CATEGORIES),
        mode=rng.choice(_MODES),
        action=rng.choice(_ACTIONS),
        threshold=rng.choice((None, rng.random())),
        priority=rng.randint(0, 3),
        scope=rng.choice(_SCOPES),
        on_unavailable=rng.choice(_POSTURES),
        surfaces=frozenset(),
    )


def _plan(rng: random.Random, rules: tuple[Rule, ...]) -> ExecutionPlan:
    """A valid ``ExecutionPlan`` wrapping ``rules`` with a well-formed version."""
    epoch = rng.randint(0, 9)
    sequence = rng.randint(0, 99)
    content_hash = f"{rng.randrange(16**20):020x}"
    return ExecutionPlan(
        org_id=rng.choice(("org-a", "org-b")),
        epoch=epoch,
        sequence=sequence,
        content_hash=content_hash,
        compiled_at=float(rng.randint(0, 1_000)),
        rules=rules,
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=bool(rng.getrandbits(1)),
    )


def _rng_rules(rng: random.Random) -> tuple[Rule, ...]:
    """A random set of 0-6 rules; multiple rules per category occur by design."""
    return tuple(_rng_rule(rng, i) for i in range(rng.randint(0, 6)))


def _rng_findings(rng: random.Random) -> tuple[Finding, ...]:
    """A random set of 0-5 findings."""
    return tuple(_rng_finding(rng) for _ in range(rng.randint(0, 5)))


def _rng_case(
    rng: random.Random,
) -> tuple[tuple[Finding, ...], ExecutionPlan, Phase]:
    """One adversarial ``(findings, plan, phase)`` case."""
    findings = _rng_findings(rng)
    plan = _plan(rng, _rng_rules(rng))
    phase = rng.choice(_PHASES)
    return findings, plan, phase


# --- 2.1 structural-invariants bundle ------------------------------------------------------------


def _phase_admits(scope: RuleScope, phase: Phase) -> bool:
    """INPUT admits INPUT/BOTH; OUTPUT admits OUTPUT/BOTH (recomputed here)."""
    if phase is Phase.INPUT:
        return scope is RuleScope.INPUT or scope is RuleScope.BOTH
    return scope is RuleScope.OUTPUT or scope is RuleScope.BOTH


def _selecting_enforce_rules(
    finding: Finding,
    plan: ExecutionPlan,
    phase: Phase,
) -> tuple[Rule, ...]:
    """ENFORCE rules selecting ``finding``: category join + phase admission."""
    return tuple(
        rule
        for rule in plan.rules
        if rule.mode is Mode.ENFORCE
        and rule.category == finding.category
        and _phase_admits(rule.scope, phase)
    )


def _expected_finding_disposition(
    finding: Finding,
    plan: ExecutionPlan,
    phase: Phase,
) -> Disposition | None:
    """The disposition the resolver should emit for ``finding`` (independent).

    Recomputed from scratch so the properties cross-check the production path.
    SKIPPED -> none. UNAVAILABLE -> BLOCK iff any ENFORCE selecting rule is
    FAIL_CLOSED, else none (MONITOR posture is non-enforcing). EXECUTED -> the
    winning ENFORCE action's disposition (priority desc, name-free
    most-restrictive tie-break); an ALLOW or no-ENFORCE result emits nothing.
    """
    if finding.status is FindingStatus.SKIPPED:
        return None
    if finding.status is FindingStatus.UNAVAILABLE:
        # Runbook step 224: only ENFORCE rules route an UNAVAILABLE finding; a
        # MONITOR rule's posture contributes nothing (P7).
        rules = _selecting_enforce_rules(finding, plan, phase)
        for rule in rules:
            if rule.on_unavailable is FailurePosture.FAIL_CLOSED:
                return Disposition.BLOCK
        return None
    enforcing = _selecting_enforce_rules(finding, plan, phase)
    if not enforcing:
        return None
    top = max(rule.priority for rule in enforcing)
    tied = tuple(_ACTION_TO_DISPOSITION[r.action] for r in enforcing if r.priority == top)
    winner = most_restrictive(tied)
    if winner is Disposition.ALLOW:
        return None
    return winner


def _expected_dispositions(
    findings: tuple[Finding, ...],
    plan: ExecutionPlan,
    phase: Phase,
) -> tuple[Disposition, ...]:
    """The expected per-finding dispositions, in finding order."""
    out: list[Disposition] = []
    for finding in findings:
        expected = _expected_finding_disposition(finding, plan, phase)
        if expected is not None:
            out.append(expected)
    return tuple(out)


def _assert_invariants(
    decision: Decision,
    findings: tuple[Finding, ...],
    plan: ExecutionPlan,
) -> None:
    """The structural-invariants bundle asserted on EVERY property iteration."""
    # disposition is the most-restrictive of the per-finding dispositions.
    assert decision.disposition is most_restrictive(
        tuple(fd.disposition for fd in decision.per_finding),
    )
    # every input finding appears in Decision.findings, order-preserving.
    assert decision.findings == findings
    # unavailable_detectors = de-duplicated detectors of UNAVAILABLE findings, in order.
    expected_unavail: list[str] = []
    for finding in findings:
        if finding.status is FindingStatus.UNAVAILABLE and (
            finding.detector not in expected_unavail
        ):
            expected_unavail.append(finding.detector)
    assert decision.unavailable_detectors == tuple(expected_unavail)
    # plan_version is the plan's version.
    assert decision.plan_version == plan.version
    # ALLOW is never emitted as a per-finding disposition.
    assert all(fd.disposition is not Disposition.ALLOW for fd in decision.per_finding)
    # BLOCK dominance: any per-finding BLOCK => request disposition BLOCK.
    if any(fd.disposition is Disposition.BLOCK for fd in decision.per_finding):
        assert decision.disposition is Disposition.BLOCK


# --- 2.2 Property 1 ------------------------------------------------------------------------------


def test_property_1_no_non_derivable_disposition() -> None:
    # Feature: pure-resolver-gw07, Property 1: every FindingDisposition is derivable
    # from a scope-admitted ENFORCE rule selecting that finding, or is a FAIL_CLOSED
    # UNAVAILABLE BLOCK; the request disposition is most_restrictive of exactly those.
    # Validates: Requirements 3.3, 5.1, 5.2, 9.1
    rng = random.Random(0x6704_0001)
    for _ in range(ITERATIONS):
        findings, plan, phase = _rng_case(rng)
        decision = resolve(findings, plan, phase)
        _assert_invariants(decision, findings, plan)
        expected = _expected_dispositions(findings, plan, phase)
        got = tuple(fd.disposition for fd in decision.per_finding)
        assert got == expected
        assert decision.disposition is most_restrictive(expected)


# --- 2.3 Property 2 ------------------------------------------------------------------------------


def test_property_2_monitor_rule_never_changes_disposition() -> None:
    # Feature: pure-resolver-gw07, Property 2: adding one MONITOR rule (any
    # category/action/priority/scope AND any on_unavailable posture) leaves the
    # request disposition unchanged. MONITOR is structurally non-enforcing per
    # runbook step 224 -- it is recorded but is never a candidate, INCLUDING via
    # its posture, so a FAIL_CLOSED MONITOR rule must not force a BLOCK on an
    # UNAVAILABLE finding (P7). The posture is therefore drawn randomly here and
    # the property holds unconditionally.
    # Validates: Requirements 4.1, 4.2, 9.2
    rng = random.Random(0x6704_0002)
    for _ in range(ITERATIONS):
        findings, plan, phase = _rng_case(rng)
        base = resolve(findings, plan, phase)
        _assert_invariants(base, findings, plan)
        monitor = Rule(
            rule_id="monitor-added",
            category=rng.choice(_CATEGORIES),
            mode=Mode.MONITOR,
            action=rng.choice(_ACTIONS),
            threshold=None,
            priority=rng.randint(0, 3),
            scope=rng.choice(_SCOPES),
            on_unavailable=rng.choice(_POSTURES),
            surfaces=frozenset(),
        )
        plan2 = _plan_with_rules(plan, (*plan.rules, monitor))
        after = resolve(findings, plan2, phase)
        assert after.disposition is base.disposition


# --- 2.4 Property 3 ------------------------------------------------------------------------------


def test_property_3_skipped_finding_never_changes_disposition() -> None:
    # Feature: pure-resolver-gw07, Property 3: adding a SKIPPED finding leaves the
    # request disposition unchanged AND the added finding appears in Decision.findings.
    # Validates: Requirements 3.1, 9.3
    rng = random.Random(0x6704_0003)
    for _ in range(ITERATIONS):
        findings, plan, phase = _rng_case(rng)
        base = resolve(findings, plan, phase)
        _assert_invariants(base, findings, plan)
        skipped = Finding(
            detector=rng.choice(_DETECTORS),
            detector_version="v1",
            category=rng.choice(_CATEGORIES),
            status=FindingStatus.SKIPPED,
            confidence=None,
            spans=_rng_spans(rng),
            evidence=None,
        )
        findings2 = (*findings, skipped)
        after = resolve(findings2, plan, phase)
        _assert_invariants(after, findings2, plan)
        assert after.disposition is base.disposition
        assert skipped in after.findings


# --- 2.5 Property 4 ------------------------------------------------------------------------------


def test_property_4_determinism() -> None:
    # Feature: pure-resolver-gw07, Property 4: two independent evaluations on the
    # same inputs return Decisions equal in every field.
    # Validates: Requirements 1.6, 8.2, 9.4
    rng = random.Random(0x6704_0004)
    for _ in range(ITERATIONS):
        findings, plan, phase = _rng_case(rng)
        first = resolve(findings, plan, phase)
        second = resolve(findings, plan, phase)
        _assert_invariants(first, findings, plan)
        assert first == second


# --- 2.6 Property 5 ------------------------------------------------------------------------------


def test_property_5_redact_flag_allow_rule_never_removes_block() -> None:
    # Feature: pure-resolver-gw07, Property 5: for inputs already BLOCK, adding any
    # ENFORCE rule mapping to REDACT/FLAG/ALLOW (any category/scope) keeps BLOCK.
    # BLOCK is terminal under most_restrictive ACROSS findings; a non-restrictive
    # rule cannot REMOVE a block. The added rule is given a priority strictly below
    # every existing rule so it cannot WIN a same-finding priority contest (an
    # intra-finding override is the C22 precedence mechanism, not a disposition
    # removal); with that, any surviving BLOCK must remain BLOCK.
    # Validates: Requirements 6.4, 7.1, 7.2, 9.5
    rng = random.Random(0x6704_0005)
    non_block_actions = (Action.ALLOW, Action.FLAG, Action.REDACT)
    for _ in range(ITERATIONS):
        findings, plan, phase = _rng_case(rng)
        base = resolve(findings, plan, phase)
        _assert_invariants(base, findings, plan)
        if base.disposition is not Disposition.BLOCK:
            continue
        low_priority = min((rule.priority for rule in plan.rules), default=0) - 1
        added = Rule(
            rule_id="non-block-added",
            category=rng.choice(_CATEGORIES),
            mode=Mode.ENFORCE,
            action=rng.choice(non_block_actions),
            threshold=None,
            priority=low_priority,
            scope=rng.choice(_SCOPES),
            on_unavailable=FailurePosture.FAIL_OPEN,
            surfaces=frozenset(),
        )
        plan2 = _plan_with_rules(plan, (*plan.rules, added))
        after = resolve(findings, plan2, phase)
        assert after.disposition is Disposition.BLOCK


# --- 2.7 Property 6 ------------------------------------------------------------------------------


def test_property_6_renaming_rules_never_changes_disposition() -> None:
    # Feature: pure-resolver-gw07, Property 6: consistently (injectively) renaming
    # every rule_id leaves the request disposition unchanged (name-independence).
    # Validates: Requirements 6.5, 9.6
    rng = random.Random(0x6704_0006)
    for _ in range(ITERATIONS):
        findings, plan, phase = _rng_case(rng)
        base = resolve(findings, plan, phase)
        _assert_invariants(base, findings, plan)
        renamed_rules = tuple(
            _rule_with_id(rule, f"renamed-{index}-{rule.rule_id}")
            for index, rule in enumerate(plan.rules)
        )
        plan2 = _plan_with_rules(plan, renamed_rules)
        after = resolve(findings, plan2, phase)
        assert after.disposition is base.disposition


# --- plan/rule rebuild helpers -------------------------------------------------------------------


def _plan_with_rules(plan: ExecutionPlan, rules: tuple[Rule, ...]) -> ExecutionPlan:
    """``plan`` with its ``rules`` replaced (version source preserved)."""
    return ExecutionPlan(
        org_id=plan.org_id,
        epoch=plan.epoch,
        sequence=plan.sequence,
        content_hash=plan.content_hash,
        compiled_at=plan.compiled_at,
        rules=rules,
        required_detectors=plan.required_detectors,
        streaming_mode=plan.streaming_mode,
        integrity_locked=plan.integrity_locked,
    )


def _rule_with_id(rule: Rule, rule_id: str) -> Rule:
    """``rule`` with a new ``rule_id`` and everything else identical."""
    return Rule(
        rule_id=rule_id,
        category=rule.category,
        mode=rule.mode,
        action=rule.action,
        threshold=rule.threshold,
        priority=rule.priority,
        scope=rule.scope,
        on_unavailable=rule.on_unavailable,
        surfaces=rule.surfaces,
    )


# --- example-test construction helpers -----------------------------------------------------------


def _executed(
    category: Category,
    detector: str = "det",
    *,
    spans: tuple[Span, ...] = (),
) -> Finding:
    """An EXECUTED finding of ``category`` with high confidence."""
    return Finding(
        detector=detector,
        detector_version="v1",
        category=category,
        status=FindingStatus.EXECUTED,
        confidence=0.99,
        spans=spans,
        evidence=None,
    )


def _rule(
    category: Category,
    action: Action,
    *,
    rule_id: str = "rule",
    mode: Mode = Mode.ENFORCE,
    priority: int = 1,
    scope: RuleScope = RuleScope.BOTH,
    on_unavailable: FailurePosture = FailurePosture.FAIL_OPEN,
) -> Rule:
    """One well-formed rule for the example tests."""
    return Rule(
        rule_id=rule_id,
        category=category,
        mode=mode,
        action=action,
        threshold=None,
        priority=priority,
        scope=scope,
        on_unavailable=on_unavailable,
        surfaces=frozenset(),
    )


def _example_plan(rules: tuple[Rule, ...]) -> ExecutionPlan:
    """A deterministic, well-formed plan for the example tests."""
    return ExecutionPlan(
        org_id="org-example",
        epoch=1,
        sequence=2,
        content_hash="abcdef0123456789abcdef",
        compiled_at=0.0,
        rules=rules,
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=True,
    )


# --- 2.8 LGW07 example tests ---------------------------------------------------------------------


def test_lgw07_2_enforce_flag_resolves_to_flag_not_block() -> None:
    # LGW07-2: an EXECUTED PII finding + an ENFORCE PII FLAG rule -> disposition FLAG
    # (not BLOCK), finding retained in Decision.findings.
    # Validates: Requirements 10.1
    finding = _executed(Category.PII, "pii-det")
    plan = _example_plan((_rule(Category.PII, Action.FLAG),))
    decision = resolve((finding,), plan, Phase.INPUT)
    assert decision.disposition is Disposition.FLAG
    assert finding in decision.findings


def test_lgw07_3_org_allow_over_detector_block_recommendation() -> None:
    # LGW07-3: an EXECUTED finding whose detector "recommends BLOCK" but the org
    # ENFORCE rule for that category is ALLOW -> resolves per the signed rule (ALLOW
    # -> no restriction -> disposition ALLOW), NOT a severity max to BLOCK.
    # Validates: Requirements 10.2
    finding = _executed(Category.PROMPT_INJECTION, "injection-det")
    plan = _example_plan((_rule(Category.PROMPT_INJECTION, Action.ALLOW),))
    decision = resolve((finding,), plan, Phase.INPUT)
    assert decision.disposition is Disposition.ALLOW
    assert decision.per_finding == ()
    assert finding in decision.findings


def test_lgw07_5_monitor_is_non_enforcing() -> None:
    # LGW07-5: a MONITOR rule selecting a high-confidence EXECUTED finding leaves the
    # disposition unchanged (ALLOW) and records the finding; the same finding under an
    # ENFORCE BLOCK rule -> BLOCK, showing MONITOR is non-enforcing.
    # Validates: Requirements 10.3
    finding = _executed(Category.SECRET, "secret-det")
    monitor_plan = _example_plan(
        (_rule(Category.SECRET, Action.BLOCK, mode=Mode.MONITOR),),
    )
    monitored = resolve((finding,), monitor_plan, Phase.INPUT)
    assert monitored.disposition is Disposition.ALLOW
    assert finding in monitored.findings
    assert monitored.per_finding == ()

    enforce_plan = _example_plan(
        (_rule(Category.SECRET, Action.BLOCK, mode=Mode.ENFORCE),),
    )
    enforced = resolve((finding,), enforce_plan, Phase.INPUT)
    assert enforced.disposition is Disposition.BLOCK


def test_lgw07_6_pii_redact_plus_credential_block_resolves_block() -> None:
    # LGW07-6: a PII EXECUTED finding under an ENFORCE PII REDACT rule AND a CREDENTIAL
    # EXECUTED finding under an ENFORCE CREDENTIAL BLOCK rule -> disposition BLOCK, BOTH
    # findings retained, and per_finding has both a REDACT and a BLOCK entry.
    # Validates: Requirements 10.4
    pii = _executed(Category.PII, "pii-det", spans=(Span(start=0, end=3),))
    credential = _executed(Category.CREDENTIAL, "cred-det")
    plan = _example_plan(
        (
            _rule(Category.PII, Action.REDACT, rule_id="pii-rule"),
            _rule(Category.CREDENTIAL, Action.BLOCK, rule_id="cred-rule"),
        ),
    )
    decision = resolve((pii, credential), plan, Phase.INPUT)
    assert decision.disposition is Disposition.BLOCK
    assert pii in decision.findings
    assert credential in decision.findings
    dispositions = tuple(fd.disposition for fd in decision.per_finding)
    assert Disposition.REDACT in dispositions
    assert Disposition.BLOCK in dispositions


def test_lgw07_7_resolve_package_imports_only_domain() -> None:
    # LGW07-7 (reframed): gateway_v2.resolve imports only gateway_v2.domain. Walk every
    # resolve-package module's top-level imports (AST) and assert no gateway_v2.<non-domain>
    # import appears. Deterministic, no reliance on sys.modules state.
    # Validates: Requirements 10.5
    import ast
    import pathlib

    import gateway_v2.resolve as resolve_pkg

    pkg_dir = pathlib.Path(resolve_pkg.__file__).parent
    offenders: list[str] = []
    for module_path in sorted(pkg_dir.glob("*.py")):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [node.module]
            for name in names:
                if name.startswith("gateway_v2.") and not (
                    name == "gateway_v2.domain" or name.startswith("gateway_v2.domain.")
                ) and not name.startswith("gateway_v2.resolve"):
                    offenders.append(f"{module_path.name}: {name}")
    assert offenders == []


# --- 2.9 edge / identity tests -------------------------------------------------------------------


def test_empty_findings_resolve_to_allow() -> None:
    # Empty findings -> disposition ALLOW, empty per_finding.
    # Validates: Requirements 10.6
    plan = _example_plan((_rule(Category.PII, Action.BLOCK),))
    for phase in _PHASES:
        decision = resolve((), plan, phase)
        assert decision.disposition is Disposition.ALLOW
        assert decision.per_finding == ()
        assert decision.findings == ()


def test_rewrite_action_maps_to_redact_with_transformation_per_span() -> None:
    # REWRITE -> REDACT: an ENFORCE REWRITE rule on an EXECUTED finding with spans
    # yields disposition REDACT and one redact Transformation per span.
    # Validates: Requirements 1.3, 10.6
    spans = (Span(start=0, end=4), Span(start=6, end=9))
    finding = _executed(Category.PII, "pii-det", spans=spans)
    plan = _example_plan((_rule(Category.PII, Action.REWRITE),))
    decision = resolve((finding,), plan, Phase.OUTPUT)
    assert decision.disposition is Disposition.REDACT
    assert len(decision.transformations) == len(spans)
    assert all(t.kind == "redact" for t in decision.transformations)
    assert tuple(t.span for t in decision.transformations) == spans


def test_redaction_placeholder_agrees_with_egress() -> None:
    # Sentinel agreement: the resolve-layer REDACTION_PLACEHOLDER equals the shipped
    # egress one (tests may import egress; the resolver itself must not).
    # Validates: Requirements 8.5, 10.6
    from gateway_v2.egress.output_guard import REDACTION_PLACEHOLDER as EGRESS_PLACEHOLDER
    from gateway_v2.resolve.decision import REDACTION_PLACEHOLDER

    assert REDACTION_PLACEHOLDER == EGRESS_PLACEHOLDER


def test_resolver_output_is_the_domain_decision_type() -> None:
    # Identity: resolve(...) returns a gateway_v2.domain.decision.Decision instance;
    # the resolve-reachable Decision/Disposition ARE the domain types (no redefinition).
    # Validates: Requirements 1.3, 8.5
    plan = _example_plan((_rule(Category.PII, Action.BLOCK),))
    finding = _executed(Category.PII, "pii-det")
    decision = resolve((finding,), plan, Phase.INPUT)
    assert isinstance(decision, Decision)
    assert isinstance(decision.disposition, Disposition)


def test_plan_resolver_satisfies_output_resolver_protocol() -> None:
    # Structural protocol: PlanResolver duck-types the shipped @runtime_checkable
    # OutputResolver, and its decide(matches) agrees with resolve(matches, plan, phase).
    # Validates: Requirements 8.5, 10.6
    from gateway_v2.egress.output_guard import OutputResolver

    plan = _example_plan((_rule(Category.PII, Action.REDACT),))
    adapter = PlanResolver(plan, Phase.INPUT)
    assert isinstance(adapter, OutputResolver)
    finding = _executed(Category.PII, "pii-det", spans=(Span(start=0, end=2),))
    assert adapter.decide((finding,)) == resolve((finding,), plan, Phase.INPUT)


# --- regression: GW07 runbook-strict correction (MONITOR posture non-enforcing) ------------------


def _unavailable(category: Category, detector: str = "det") -> Finding:
    """An UNAVAILABLE finding of ``category`` (confidence must be None)."""
    return Finding(
        detector=detector,
        detector_version="v1",
        category=category,
        status=FindingStatus.UNAVAILABLE,
        confidence=None,
        spans=(),
        evidence=None,
    )


def test_monitor_fail_closed_posture_does_not_force_block_on_unavailable() -> None:
    # Regression: GW07 runbook-strict correction -- MONITOR posture is non-enforcing (P7).
    # An UNAVAILABLE finding whose ONLY selecting rule is a MONITOR rule with
    # on_unavailable=FAIL_CLOSED must NOT become a BLOCK: a MONITOR rule contributes
    # nothing to disposition, including via its posture (runbook step 224). The same
    # UNAVAILABLE finding under an ENFORCE FAIL_CLOSED rule still BLOCKs (ENFORCE
    # posture routing is unchanged).
    # Validates: Requirements 4.1, 4.2, 3.2
    finding = _unavailable(Category.SECRET, "secret-det")

    monitor_plan = _example_plan(
        (
            _rule(
                Category.SECRET,
                Action.BLOCK,
                mode=Mode.MONITOR,
                on_unavailable=FailurePosture.FAIL_CLOSED,
            ),
        ),
    )
    monitored = resolve((finding,), monitor_plan, Phase.INPUT)
    assert monitored.disposition is Disposition.ALLOW
    assert finding in monitored.findings
    assert "secret-det" in monitored.unavailable_detectors
    assert monitored.per_finding == ()

    enforce_plan = _example_plan(
        (
            _rule(
                Category.SECRET,
                Action.BLOCK,
                mode=Mode.ENFORCE,
                on_unavailable=FailurePosture.FAIL_CLOSED,
            ),
        ),
    )
    enforced = resolve((finding,), enforce_plan, Phase.INPUT)
    assert enforced.disposition is Disposition.BLOCK
    assert "secret-det" in enforced.unavailable_detectors
