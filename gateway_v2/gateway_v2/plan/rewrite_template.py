"""Save-time REWRITE template validation (GW33 C9, D-4, D-24, D-30).

A bad template must never reach a gateway, so everything is refused here, at compile time:

* non-empty after strip, at most `REWRITE_TEMPLATE_MAX_CHARS` (the one immutable limit; plan has no
  `ResourceContract` at its four `compile_plan` call sites, D-24);
* only the tokens `{category}` and `{rule_id}` may contain braces;
* a conservative SYNTACTIC check (D-30), independent of any bound, which is what makes the check
  sound: no URL scheme, markdown/HTML syntax, `@`, `www.`, dotted names or numbers, or long
  encoded-looking run. A template that passes it cannot be flagged by any contract-derived detector;
* the deterministic and GW33 detectors, built from `DEFAULT_OUTPUT_BOUNDS` (the most sensitive
  values), find nothing in the rendering, and for a customer-condition rule the rendering must not
  match the rule's own condition, so a rewrite cannot re-emit its finding.
"""
from __future__ import annotations

import re
from typing import Final

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.conditions import compile_conditions, scan_conditions
from gateway_v2.detect.deterministic.detectors import (
    InjectionSignalDetector,
    PiiDetector,
    SecretDetector,
)
from gateway_v2.detect.guard.backend import Budget
from gateway_v2.detect.output.exfil import ExfilDetector
from gateway_v2.detect.output.ip_leak import IpLeakDetector
from gateway_v2.domain import FindingStatus, Phase, Rule
from gateway_v2.resolve.rewrite import render_template
from gateway_v2.runtime.resources_output import DEFAULT_OUTPUT_BOUNDS, REWRITE_TEMPLATE_MAX_CHARS

__all__ = ("TemplateError", "check_template")

_TOKENS: Final = ("{category}", "{rule_id}")
_CHECK_BUDGET_S: Final = 0.25
"""How long `check_template` waits for the rule's own condition. It is a thread-join timeout, so
it cannot preempt `re`, which holds the GIL for the whole of one match: a slow pattern costs its
full time here, on the projector and on every gateway, and the late result is used. "Overran"
below means only a scan the join could interrupt between matches. Tracked as #125; until the F1
engine's CPU timeout lands, keep `V2_REWRITE_ORGS` empty."""
_FORBIDDEN_SUBSTRINGS: Final = ("://", "![", "](", "<", ">", "@", "www.")
_DIGIT_DOT_DIGIT: Final = re.compile(r"\d\.\d")
_DOT_BETWEEN_ALNUM: Final = re.compile(r"[A-Za-z0-9]\.[A-Za-z0-9]")
_LONG_RUN: Final = re.compile(r"[A-Za-z0-9+/=_-]{16,}")


class TemplateError(ValueError):
    """A REWRITE template was refused; the message names the problem, never a customer value."""


def _syntactic(neutral: str) -> str | None:
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        if forbidden in neutral:
            return f"template must not contain {forbidden!r}"
    if _DIGIT_DOT_DIGIT.search(neutral) or _DOT_BETWEEN_ALNUM.search(neutral):
        return "template must not contain a dotted name or number"
    if _LONG_RUN.search(neutral):
        return "template must not contain a long encoded-looking run"
    return None


def check_template(rule: Rule) -> None:
    """Raise `TemplateError` unless ``rule.rewrite_template`` is safe to publish."""
    template = rule.rewrite_template
    if not template.strip():
        raise TemplateError(f"rule {rule.rule_id}: rewrite action requires a rewrite_template")
    if len(template) > REWRITE_TEMPLATE_MAX_CHARS:
        raise TemplateError(
            f"rule {rule.rule_id}: rewrite_template exceeds "
            f"{REWRITE_TEMPLATE_MAX_CHARS} characters",
        )
    stripped = template
    for token in _TOKENS:
        stripped = stripped.replace(token, "X")
    if "{" in stripped or "}" in stripped:
        raise TemplateError(
            f"rule {rule.rule_id}: rewrite_template may only use {{category}} and {{rule_id}}",
        )
    problem = _syntactic(stripped)
    if problem is not None:
        raise TemplateError(f"rule {rule.rule_id}: {problem}")
    rendered = render_template(template, rule.category, rule.rule_id)
    context = DetectorContext(phase=Phase.OUTPUT, rules=(), budget=Budget(remaining_s=1.0))
    detectors = (
        PiiDetector(), SecretDetector(), InjectionSignalDetector(),
        IpLeakDetector(bounds=DEFAULT_OUTPUT_BOUNDS),
        ExfilDetector(bounds=DEFAULT_OUTPUT_BOUNDS, allow_hosts=frozenset()),
    )
    for detector in detectors:
        if any(f.status is FindingStatus.EXECUTED for f in detector.detect(rendered, context)):
            raise TemplateError(
                f"rule {rule.rule_id}: rewrite_template would re-emit a finding of its own",
            )
    if rule.condition is not None:
        scan = scan_conditions(
            compile_conditions((rule,)), rendered, budget_s=_CHECK_BUDGET_S, phase=Phase.OUTPUT,
        )
        if scan.refuse_code is not None:
            raise TemplateError(
                f"rule {rule.rule_id}: the rule's own condition pattern is refused (ReDoS shape, "
                "nested too deep, does not compile, faulted, or overran the check budget), so "
                "the template cannot be checked",
            )
        if scan.findings:
            raise TemplateError(
                f"rule {rule.rule_id}: rewrite_template matches the rule's own condition",
            )
