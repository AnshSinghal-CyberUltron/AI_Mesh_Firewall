"""GW33 task 7.6 / Property 9: fail-closed unavailability for every new detector."""
from __future__ import annotations

import asyncio
import random

import pytest

from gateway_v2.detect.output import canary, exfil, grounding, ip_leak
from gateway_v2.domain import (
    Action,
    Category,
    Disposition,
    ExecutionPlan,
    FailurePosture,
    FindingStatus,
    GroundingMode,
    Mode,
    Phase,
    Rule,
    RuleScope,
    StreamingMode,
)
from gateway_v2.resolve import resolve

_ITERATIONS = 400


def _plan(rule: Rule) -> ExecutionPlan:
    return ExecutionPlan("o", 1, 1, "abcdef0123456789abcdef", 0.0, (rule,), frozenset(),
                         StreamingMode.STRICT_WITHHOLD, True)


def _boom(*_a: object, **_k: object) -> object:
    raise RuntimeError("boom")


def _detectors() -> list[tuple[str, Category, object]]:
    return [
        ("ip", Category.IP_LEAK, ip_leak.IpLeakDetector(rule_id="r")),
        ("exfil", Category.EXFIL, exfil.ExfilDetector(rule_id="r")),
        ("canary", Category.CANARY, canary.CanaryDetector("cnry_" + "a" * 26, rule_id="r")),
        ("grounding", Category.HALLUCINATION, grounding.GroundingDetector(
            context_chunks=("some context words here",), max_input_chars=10_000, rule_id="r",
            mode=GroundingMode.LEXICAL)),
    ]


def test_property_9_any_internal_failure_is_one_unavailable_finding_that_routes_by_posture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature: gw33-output-parity, Property 9: Fail-closed unavailability. Validates 3.4, 10.1."""
    # Feature: gw33-output-parity, Property 9: Fail-closed unavailability
    rng = random.Random(33_009)
    for module in (ip_leak, exfil, canary, grounding):
        monkeypatch.setattr(module, "canonicalize", _boom)
    monkeypatch.setattr(exfil, "canonicalize_structure", _boom)
    for _ in range(_ITERATIONS):
        text = "".join(rng.choice("abc 10.0.0.1 ![x](http://e.co/p) cnry_") for _ in range(30))
        name, category, detector = rng.choice(_detectors())
        findings = detector.detect(text)  # type: ignore[attr-defined]
        assert [f.status for f in findings] == [FindingStatus.UNAVAILABLE], name
        assert findings[0].category is category and findings[0].spans == ()
        assert findings[0].confidence is None and findings[0].evidence is None
        posture = rng.choice((FailurePosture.FAIL_CLOSED, FailurePosture.FAIL_OPEN))
        action = rng.choice((Action.BLOCK, Action.REDACT, Action.FLAG))
        rule = Rule("r", category, Mode.ENFORCE, action, None, 1, RuleScope.OUTPUT, posture,
                    frozenset())
        decision = resolve(findings, _plan(rule), Phase.OUTPUT)
        expected = (
            Disposition.BLOCK if posture is FailurePosture.FAIL_CLOSED else Disposition.ALLOW
        )
        assert decision.disposition is expected
        assert "r" in decision.unavailable_detectors


def test_grounding_backend_failures_never_look_clean() -> None:
    d = grounding.GroundingDetector(mode=GroundingMode.MODEL, context_chunks=("ctx text",),
                                    max_input_chars=1_000, rule_id="r")
    assert [f.status for f in asyncio.run(d.adetect("claim text"))] == [FindingStatus.UNAVAILABLE]


def test_unrelated_non_string_input_is_still_unavailable() -> None:
    for _name, _category, detector in _detectors():
        assert [f.status for f in detector.detect(None)] == [  # type: ignore[attr-defined]
            FindingStatus.UNAVAILABLE,
        ]
