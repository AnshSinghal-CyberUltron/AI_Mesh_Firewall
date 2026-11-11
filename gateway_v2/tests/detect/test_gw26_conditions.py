"""GW26 customer conditions: round-trip, surface join, and regex fail-closed."""

from __future__ import annotations

import json
import time

from gateway_v2.detect.conditions import compile_conditions, scan_conditions
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
    StreamingMode,
    Surface,
)
from gateway_v2.domain.posture import POLICY_REGEX_TIMEOUT
from gateway_v2.plan.compiler import RuleDraft, compile_plan
from gateway_v2.plan.document import PlanDocument, decode_plan_body, encode_plan_body
from gateway_v2.resolve.resolver import resolve
from gateway_v2.runtime.resources import load_contract

_COMPETITOR = "AcmeRival"


def _budget() -> float:
    contract, _logs = load_contract(env={})
    return contract.guard_wait_budget_s()


def _condition(
    kind: RuleKind,
    *,
    keywords: tuple[str, ...] = (),
    pattern: str = "",
    stage: str = "",
    direction: RuleScope = RuleScope.INPUT,
) -> RuleCondition:
    return RuleCondition(
        kind=kind,
        field="",
        direction=direction,
        keywords=keywords,
        pattern=pattern,
        detector_class="",
        pipeline_stage=stage,
    )


def _draft(
    rule_id: str,
    surfaces: frozenset[Surface],
    condition: RuleCondition,
    *,
    action: Action = Action.BLOCK,
    scope: RuleScope = RuleScope.INPUT,
) -> RuleDraft:
    return RuleDraft(
        rule_id=rule_id,
        category=Category.EXFIL,
        mode=Mode.ENFORCE,
        action=action,
        threshold=None,
        priority=1,
        scope=scope,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=surfaces,
        condition=condition,
    )


def _plan_from(drafts: tuple[RuleDraft, ...]) -> ExecutionPlan:
    return compile_plan("dev-org", drafts, compiled_at=0.0)


def test_keyword_and_regex_round_trip() -> None:
    """A keyword list and a regex survive encode and decode."""
    keyword = _condition(RuleKind.KEYWORDS, keywords=("AcmeRival", "OtherCo"))
    regex = _condition(RuleKind.REGEX, pattern=r"card-\d{4}", direction=RuleScope.OUTPUT)
    document = PlanDocument(
        "dev-org",
        StreamingMode.INCREMENTAL,
        (
            _draft("kw", frozenset({Surface.CHAT}), keyword),
            _draft(
                "rx",
                frozenset({Surface.CHAT}),
                regex,
                action=Action.REDACT,
                scope=RuleScope.OUTPUT,
            ),
        ),
    )
    decoded = decode_plan_body(json.dumps(encode_plan_body(document)).encode())
    assert decoded.drafts[0].condition == keyword
    assert decoded.drafts[1].condition == regex
    assert (
        "condition"
        not in encode_plan_body(
            PlanDocument("dev-org", StreamingMode.INCREMENTAL, (_draft_without_condition(),))
        )["rules"][0]
    )


def _draft_without_condition() -> RuleDraft:
    return RuleDraft(
        rule_id="plain",
        category=Category.PII,
        mode=Mode.ENFORCE,
        action=Action.REDACT,
        threshold=None,
        priority=1,
        scope=RuleScope.INPUT,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
    )


def test_two_keyword_lists_are_different_plans() -> None:
    left = _plan_from(
        (_draft("kw", frozenset({Surface.CHAT}), _condition(RuleKind.KEYWORDS, keywords=("a",))),)
    )
    right = _plan_from(
        (_draft("kw", frozenset({Surface.CHAT}), _condition(RuleKind.KEYWORDS, keywords=("b",))),)
    )
    assert left.content_hash != right.content_hash


def test_competitor_keyword_blocks_only_on_chat() -> None:
    """The finding joins the chat rule. MCP and RAG do not select it."""
    chat_rule = _draft(
        "competitor-chat",
        frozenset({Surface.CHAT}),
        _condition(RuleKind.KEYWORDS, keywords=(_COMPETITOR,)),
    )
    mcp_rule = _draft(
        "other-mcp",
        frozenset({Surface.MCP}),
        _condition(RuleKind.KEYWORDS, keywords=("unrelated",)),
    )
    plan = _plan_from((chat_rule, mcp_rule))
    program = compile_conditions(plan.rules)
    scanned = scan_conditions(
        program, f"buy {_COMPETITOR} today", budget_s=_budget(), phase=Phase.INPUT
    )
    assert scanned.refuse_code is None
    assert len(scanned.findings) == 1
    assert scanned.findings[0].detector == "competitor-chat"
    chat = resolve(scanned.findings, plan, Phase.INPUT, Surface.CHAT)
    mcp = resolve(scanned.findings, plan, Phase.INPUT, Surface.MCP)
    rag = resolve(scanned.findings, plan, Phase.INPUT, Surface.RAG)
    assert chat.disposition is Disposition.BLOCK
    assert chat.deciding_rules == ("competitor-chat",)
    assert mcp.disposition is Disposition.ALLOW
    assert rag.disposition is Disposition.ALLOW


def test_embeddings_only_refusal_does_not_withhold_chat() -> None:
    draft = _draft(
        "evil-embeddings",
        frozenset({Surface.EMBEDDINGS}),
        _condition(RuleKind.REGEX, pattern="(a+)+"),
    )
    plan = _plan_from((draft,))
    program = compile_conditions(plan.rules)
    budget = _budget()
    started = time.perf_counter()
    embeddings = scan_conditions(
        program, "a" * 30, budget_s=budget, phase=Phase.INPUT, surface=Surface.EMBEDDINGS
    )
    chat = scan_conditions(
        program, "a" * 30, budget_s=budget, phase=Phase.INPUT, surface=Surface.CHAT
    )
    omitted = scan_conditions(program, "a" * 30, budget_s=budget, phase=Phase.INPUT)
    elapsed = time.perf_counter() - started
    assert embeddings.refuse_code == POLICY_REGEX_TIMEOUT
    assert embeddings.findings == ()
    assert chat.refuse_code is None
    assert chat.findings == ()
    assert omitted.refuse_code == POLICY_REGEX_TIMEOUT
    assert elapsed < budget


def test_nested_quantifier_returns_the_declared_code_inside_the_budget() -> None:
    draft = _draft(
        "evil",
        frozenset({Surface.CHAT}),
        _condition(RuleKind.REGEX, pattern="(a+)+"),
    )
    plan = _plan_from((draft,))
    program = compile_conditions(plan.rules)
    budget = _budget()
    started = time.perf_counter()
    scanned = scan_conditions(program, "a" * 30, budget_s=budget, phase=Phase.INPUT)
    elapsed = time.perf_counter() - started
    assert scanned.refuse_code == POLICY_REGEX_TIMEOUT
    assert scanned.findings == ()
    assert elapsed < budget


def test_pipeline_stage_does_not_match_a_chat_request() -> None:
    draft = _draft(
        "staged",
        frozenset({Surface.RAG}),
        _condition(RuleKind.KEYWORDS, keywords=(_COMPETITOR,), stage="query"),
    )
    plan = _plan_from((draft,))
    program = compile_conditions(plan.rules)
    scanned = scan_conditions(
        program, _COMPETITOR, budget_s=_budget(), phase=Phase.INPUT, stage=None
    )
    assert scanned.findings == ()
    finding = Finding(
        detector="staged",
        detector_version="1",
        category=Category.EXFIL,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(),
        evidence=None,
    )
    decision = resolve((finding,), plan, Phase.INPUT, Surface.RAG, stage=None)
    assert decision.disposition is Disposition.ALLOW


def test_builtin_finding_still_joins_a_category_rule() -> None:
    rule = Rule(
        rule_id="fw-pii-input",
        category=Category.PII,
        mode=Mode.ENFORCE,
        action=Action.BLOCK,
        threshold=None,
        priority=1,
        scope=RuleScope.INPUT,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
    )
    plan = ExecutionPlan(
        org_id="dev-org",
        epoch=1,
        sequence=1,
        content_hash="b" * 64,
        compiled_at=0.0,
        rules=(rule,),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=True,
    )
    finding = Finding(
        detector="det.pii",
        detector_version="1",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(),
        evidence=None,
    )
    decision = resolve((finding,), plan, Phase.INPUT, Surface.CHAT)
    assert decision.disposition is Disposition.BLOCK
