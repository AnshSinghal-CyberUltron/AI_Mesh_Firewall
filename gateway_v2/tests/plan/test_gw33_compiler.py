"""GW33 task 8: REWRITE in the compiler and plan document (Property 11), D-9/D-22/D-28/D-29."""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace

import pytest

from gateway_v2.domain import (
    Action,
    Category,
    FailurePosture,
    Mode,
    Rule,
    RuleCondition,
    RuleKind,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.plan.compiler import CompileError, RuleDraft, compile_plan
from gateway_v2.plan.document import (
    PlanDocument,
    decode_plan_body,
    encode_plan_body,
)

_ITERATIONS = 400


def _draft(
    rule_id: str = "r1",
    category: Category = Category.PII,
    action: Action = Action.REDACT,
    *,
    scope: RuleScope = RuleScope.OUTPUT,
    template: str = "",
    condition: RuleCondition | None = None,
    posture: FailurePosture = FailurePosture.FAIL_CLOSED,
) -> RuleDraft:
    return RuleDraft(
        rule_id, category, Mode.ENFORCE, action, None, 100, scope, posture,
        frozenset({Surface.CHAT}), condition, template,
    )


def _builtin(klass: str, scope: RuleScope = RuleScope.OUTPUT, **kw: object) -> RuleCondition:
    return RuleCondition(RuleKind.DETECTOR, "", scope, (), "", klass, "", **kw)  # type: ignore[arg-type]


def _plan(*drafts: RuleDraft, mode: StreamingMode = StreamingMode.INCREMENTAL):  # type: ignore[no-untyped-def]
    return compile_plan("org", drafts, streaming_mode=mode, compiled_at=1.0)


def _old_hash(rules: tuple[Rule, ...]) -> str:
    """Frozen copy of the pre-GW33 ``_hash_rules`` (condition token without allow_hosts)."""
    lines = []
    for rule in sorted(rules, key=lambda r: r.rule_id):
        surfaces = ",".join(sorted(s.value for s in rule.surfaces))
        threshold = "" if rule.threshold is None else f"{rule.threshold:.4f}"
        parts: tuple[str, ...] = (
            rule.rule_id, rule.category.value, rule.mode.value, rule.action.value, threshold,
            str(rule.priority), rule.scope.value, rule.on_unavailable.value, surfaces,
        )
        if rule.condition is not None:
            c = rule.condition
            payload = {
                "detector_class": c.detector_class, "direction": c.direction.value,
                "field": c.field, "keywords": list(c.keywords), "kind": c.kind.value,
                "pattern": c.pattern, "pipeline_stage": c.pipeline_stage,
            }
            parts = (*parts, json.dumps(payload, separators=(",", ":")))
        lines.append("|".join(parts))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def test_rewrite_is_supported_with_a_template_and_changes_the_hash() -> None:
    a = _plan(_draft(action=Action.REWRITE, template="[{category} removed]"))
    b = _plan(_draft(action=Action.REWRITE, template="[{category} withheld]"))
    assert a.rules[0].rewrite_template == "[{category} removed]"
    assert a.content_hash != b.content_hash


def _reference_accepts(template: str) -> bool:
    """Independent restatement of the documented template rules (D-4, D-24, D-30)."""
    import re

    if not template.strip() or len(template) > 128:
        return False
    neutral = template.replace("{category}", "X").replace("{rule_id}", "X")
    if "{" in neutral or "}" in neutral:
        return False
    if any(f in neutral for f in ("://", "![", "](", "<", ">", "@", "www.")):
        return False
    if re.search(r"\d\.\d", neutral) or re.search(r"[A-Za-z0-9]\.[A-Za-z0-9]", neutral):
        return False
    return re.search(r"[A-Za-z0-9+/=_-]{16,}", neutral) is None


def test_property_11_template_safety() -> None:
    """Feature: gw33-output-parity, Property 11: Template safety. Validates 5.4, 5.5, 10.4."""
    # Feature: gw33-output-parity, Property 11: Template safety
    rng = random.Random(33_011)
    pieces = ["removed", "redacted", "withheld", "[hidden]", "{category}", "{rule_id}", " ",
              "http://x.co", "![a](b)", "<b>", "a@b.co", "www.x", "10.0.0.1", "a.b", "{", "}",
              "{other}", "A" * 20, "x" * 130, ""]
    accepted = refused = 0
    for _ in range(_ITERATIONS):
        template = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 4)))
        draft = _draft("r1", action=Action.REWRITE, template=template)
        if _reference_accepts(template):
            assert _plan(draft).rules[0].rewrite_template == template, template
            accepted += 1
        else:
            with pytest.raises(CompileError):
                _plan(draft)
            refused += 1
    assert accepted > 20 and refused > 20


def test_template_refusals_have_field_level_messages() -> None:
    with pytest.raises(CompileError, match="requires a rewrite_template"):
        _plan(_draft(action=Action.REWRITE))
    with pytest.raises(CompileError, match="only valid on a rewrite action"):
        _plan(_draft(action=Action.REDACT, template="[x]"))
    with pytest.raises(CompileError, match="exceeds"):
        _plan(_draft(action=Action.REWRITE, template="y " * 100))
    with pytest.raises(CompileError, match="may only use"):
        _plan(_draft(action=Action.REWRITE, template="[{nope}]"))
    cond = RuleCondition(RuleKind.KEYWORDS, "", RuleScope.OUTPUT, ("secretword",), "", "", "")
    with pytest.raises(CompileError, match="own"):
        _plan(_draft(action=Action.REWRITE, template="secretword", condition=cond))


def test_plans_without_templates_hash_exactly_as_before() -> None:
    drafts = (
        _draft("a", Category.PII, Action.REDACT),
        _draft("b", Category.SECRET, Action.BLOCK, scope=RuleScope.INPUT),
        _draft("c", Category.EXFIL, Action.REDACT, condition=_builtin("customer.thing")),
        _draft("d", Category.PII, Action.FLAG, condition=RuleCondition(
            RuleKind.KEYWORDS, "f", RuleScope.OUTPUT, ("k",), "", "", "")),
    )
    plan = _plan(*drafts)
    assert plan.content_hash == _old_hash(plan.rules)


def test_document_round_trip_and_requires_only_for_rewrite() -> None:
    plain = PlanDocument("org", StreamingMode.INCREMENTAL, (_draft(),))
    assert "requires" not in encode_plan_body(plain)
    assert "rewrite_template" not in json.dumps(encode_plan_body(plain))
    drafts = (
        _draft("rw", Category.IP_LEAK, Action.REWRITE, template="[ip removed]",
               condition=_builtin("output.ip_leak")),
        _draft("ex", Category.EXFIL, Action.FLAG, posture=FailurePosture.FAIL_OPEN,
               condition=_builtin("output.exfil", allow_hosts=("cdn.example.com",))),
    )
    body = encode_plan_body(PlanDocument("org", StreamingMode.STRICT_WITHHOLD, drafts))
    assert body["requires"] == ["rewrite/1"]
    decoded = decode_plan_body(json.dumps(body).encode())
    assert decoded.drafts == drafts
    plan = compile_plan("org", decoded.drafts, streaming_mode=decoded.streaming_mode,
                        compiled_at=1.0)
    assert {r.rule_id for r in plan.rules} == {"rw", "ex"}


def test_unknown_requires_is_refused() -> None:
    body = encode_plan_body(PlanDocument("org", StreamingMode.INCREMENTAL, (_draft(),)))
    body["requires"] = ["controls/9"]
    with pytest.raises(CompileError, match="unsupported capability"):
        decode_plan_body(json.dumps(body).encode())
    body["requires"] = "rewrite/1"
    with pytest.raises(CompileError, match="list of strings"):
        decode_plan_body(json.dumps(body).encode())


@pytest.mark.parametrize("klass", ["output.exfil", "output.ip_leak", "output.grounding.lexical"])
@pytest.mark.parametrize("action", [Action.REDACT, Action.REWRITE, Action.BLOCK])
def test_d9_byte_altering_builtin_rules_are_strict_only(klass: str, action: Action) -> None:
    family = klass.split(".")[1]
    category = {"exfil": Category.EXFIL, "ip_leak": Category.IP_LEAK,
                "grounding": Category.HALLUCINATION}[family]
    draft = _draft(
        "b", category, action, condition=_builtin(klass),
        template="[x]" if action is Action.REWRITE else "",
    )
    with pytest.raises(CompileError, match="strict_withhold"):
        _plan(draft)
    assert _plan(draft, mode=StreamingMode.STRICT_WITHHOLD).rules[0].action is action


def test_d9_flag_and_allow_are_legal_in_incremental_but_not_fail_closed() -> None:
    for action in (Action.FLAG, Action.ALLOW):
        ok = _draft("b", Category.EXFIL, action, condition=_builtin("output.exfil"),
                    posture=FailurePosture.FAIL_OPEN)
        assert _plan(ok).rules
        closed = replace(ok, on_unavailable=FailurePosture.FAIL_CLOSED)
        with pytest.raises(CompileError, match="fail_closed"):
            _plan(closed)
        assert _plan(closed, mode=StreamingMode.STRICT_WITHHOLD).rules


def test_canary_is_exempt_from_the_strict_only_refusal() -> None:
    draft = _draft("c", Category.CANARY, Action.BLOCK, condition=_builtin("output.canary"))
    assert _plan(draft).rules[0].category is Category.CANARY


def test_gw26_shaped_plans_install_unchanged() -> None:
    """H-1: the shipped legacy IP rule and unmapped-category customer rows, redact and block."""
    kw = RuleCondition(RuleKind.KEYWORDS, "", RuleScope.OUTPUT, ("k",), "", "", "")
    regex = RuleCondition(RuleKind.REGEX, "", RuleScope.OUTPUT, (), "a+b", "", "")
    detector = _builtin("customer.thing")
    for action in (Action.REDACT, Action.BLOCK):
        drafts = (
            _draft("fw-exfil-output", Category.EXFIL, action),  # the shipped legacy IP rule
            _draft("pol-1-kw", Category.EXFIL, action, condition=kw),
            _draft("pol-1-rx", Category.EXFIL, action, condition=regex),
            _draft("pol-1-det", Category.EXFIL, action, condition=detector),
        )
        for mode in StreamingMode:
            plan = _plan(*drafts, mode=mode)
            assert len(plan.rules) == 4 and plan.content_hash == _old_hash(plan.rules)


def test_reserved_namespace_grounding_mode_and_allow_hosts() -> None:
    with pytest.raises(CompileError, match="reserved"):
        _plan(_draft("x", Category.EXFIL, Action.FLAG, condition=_builtin("output.bogus"),
                     posture=FailurePosture.FAIL_OPEN))
    for bad in ("output.grounding.fuzzy", "output.grounding", "output.grounding.model:"):
        with pytest.raises(CompileError):
            _plan(_draft("g", Category.HALLUCINATION, Action.FLAG, condition=_builtin(bad),
                         posture=FailurePosture.FAIL_OPEN))
    for hosts in (("http://x.co",), ("CDN.example.com",), ("a b",), ("",), (".x.co",)):
        with pytest.raises(CompileError, match="allow_hosts"):
            _plan(_draft("e", Category.EXFIL, Action.FLAG, posture=FailurePosture.FAIL_OPEN,
                         condition=_builtin("output.exfil", allow_hosts=hosts)))
    with pytest.raises(CompileError, match="only valid on output.exfil"):
        _plan(_draft("e", Category.IP_LEAK, Action.FLAG, posture=FailurePosture.FAIL_OPEN,
                     condition=_builtin("output.ip_leak", allow_hosts=("x.co",))))
    ok = _draft("e", Category.EXFIL, Action.FLAG, posture=FailurePosture.FAIL_OPEN,
                condition=_builtin("output.exfil", allow_hosts=("cdn.example.com",)))
    base = _plan(replace(ok, condition=_builtin("output.exfil")))
    assert _plan(ok).content_hash != base.content_hash  # the allow-list is covered by the hash
