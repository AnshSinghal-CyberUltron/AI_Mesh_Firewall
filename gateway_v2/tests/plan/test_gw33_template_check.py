"""GW33 task 8.5 (D-30): a template that passes the syntactic check is clean under any detector."""
from __future__ import annotations

import random

import pytest

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.detectors import (
    InjectionSignalDetector,
    PiiDetector,
    SecretDetector,
)
from gateway_v2.detect.guard.backend import Budget
from gateway_v2.detect.output.exfil import ExfilDetector
from gateway_v2.detect.output.ip_leak import IpLeakDetector
from gateway_v2.domain import (
    Action,
    Category,
    FailurePosture,
    FindingStatus,
    Mode,
    Phase,
    Rule,
    RuleScope,
)
from gateway_v2.plan.rewrite_template import TemplateError, check_template
from gateway_v2.resolve.rewrite import render_template
from gateway_v2.runtime.resources import ResourceContract

_ITERATIONS = 400
_WORDS = ["removed", "redacted", "withheld", "[hidden]", "{category}", "{rule_id}", "for policy",
          "see admin", "n/a", "-", "(masked)", "internal", "address", "link", "content"]


def _contract(p99: float) -> ResourceContract:
    return ResourceContract(
        cpu_quota=4.0, memory_limit=1 << 34, fd_limit=4096, guard_capacity=None, target_p99_ms=p99,
        utilization_cap=0.75, per_worker_rss=400 << 20, worker_override=None, cpu_source="t",
        mem_source="t", fd_source="t",
    )


def _rule(template: str, category: Category = Category.PII, rule_id: str = "rw") -> Rule:
    return Rule(rule_id, category, Mode.ENFORCE, Action.REWRITE, None, 1, RuleScope.OUTPUT,
                FailurePosture.FAIL_CLOSED, frozenset(), None, template)


def test_random_templates_passing_the_check_are_never_flagged_at_any_contract_size() -> None:
    rng = random.Random(33_030)
    context = DetectorContext(Phase.OUTPUT, (), Budget(1.0))
    accepted = 0
    for _ in range(_ITERATIONS):
        template = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 5)))
        category = rng.choice(list(Category))
        rule = _rule(template, category)
        try:
            check_template(rule)
        except TemplateError:
            continue
        accepted += 1
        rendered = render_template(template, category, "rw")
        for p99 in (5.0, 20.0, 200.0):
            bounds = _contract(p99).output_bounds()
            detectors = (
                PiiDetector(), SecretDetector(), InjectionSignalDetector(),
                IpLeakDetector(bounds=bounds), ExfilDetector(bounds=bounds),
            )
            for detector in detectors:
                found = [f for f in detector.detect(rendered, context)
                         if f.status is FindingStatus.EXECUTED]
                assert not found, (rendered, p99, type(detector).__name__)
    assert accepted > 100


@pytest.mark.parametrize("bad", ["see http://x.co", "![a](b)", "mail a@b.co", "10.0.0.1 here",
                                 "host.internal", "AAAAAAAAAAAAAAAAAAAA", ""])
def test_unsafe_templates_are_refused(bad: str) -> None:
    with pytest.raises(TemplateError):
        check_template(_rule(bad))
