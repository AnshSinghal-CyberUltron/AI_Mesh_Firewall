"""Hotfix: `check_template` refuses, never raises, when the rule's own regex cannot be compiled.

`check_template` compiles the REWRITE rule's condition to prove the rendering cannot re-emit
its finding. Before the fix a 499-deep condition raised `RecursionError` out of it, and so out
of `compile_plan` (which only expects `CompileError`) for the gateway and the projector alike.
"""

from __future__ import annotations

import re

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
    Surface,
)
from gateway_v2.plan.rewrite_template import TemplateError, check_template


def _rule(pattern: str) -> Rule:
    return Rule(
        rule_id="rw",
        category=Category.EXFIL,
        mode=Mode.ENFORCE,
        action=Action.REWRITE,
        threshold=None,
        priority=1,
        scope=RuleScope.OUTPUT,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
        condition=RuleCondition(
            kind=RuleKind.REGEX, field="", direction=RuleScope.OUTPUT, keywords=(),
            pattern=pattern, detector_class="", pipeline_stage="",
        ),
        rewrite_template="Removed by policy {rule_id}",
    )


@pytest.mark.parametrize(
    "pattern", ["(" * 499 + "a" + ")" * 499, "(" * 33 + "a" + ")" * 33, "a{4294967296}", "(a+)+"],
)
def test_a_refused_condition_is_a_template_error(pattern: str) -> None:
    re.purge()
    with pytest.raises(TemplateError, match="condition pattern is refused"):
        check_template(_rule(pattern))


def test_a_thirty_two_deep_condition_is_checked_normally() -> None:
    check_template(_rule("(" * 32 + "secret" + ")" * 32))
    with pytest.raises(TemplateError, match="matches the rule's own condition"):
        check_template(_rule("(" * 32 + "Removed" + ")" * 32))
