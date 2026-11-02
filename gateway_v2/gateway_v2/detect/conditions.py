"""Customer rule conditions, compiled once per plan and scanned under the guard-wait budget.

A keyword, regex, or pattern condition emits one ``Finding`` whose ``detector`` is the
rule id, so the resolver joins that rule. A detector-kind condition emits nothing here:
the built-in detectors already emit the category. A nested-quantifier, quantified-wildcard,
or quantified-alternation pattern, a pattern ``re`` rejects, or a match that exceeds
``budget_s`` returns ``policy_regex_timeout`` and does not run the pattern.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.plan import Mode, Phase, Rule, RuleKind, RuleScope, Surface
from gateway_v2.domain.posture import POLICY_REGEX_TIMEOUT

_DETECTOR_VERSION = "gw26-condition-1"
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*]\s*\)\s*[+*{]")
_QUANTIFIED_WILDCARD = re.compile(r"\([^()]*\.[*+][^()]*\)\s*[+*{]")
_QUANTIFIED_ALTERNATION = re.compile(r"\([^()]*\|[^()]*\)[+*{]")


class PolicyRegexRefusal(Exception):
    """The customer matcher refused the text. ``code`` is the posture spelling."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class _Prepared:
    rule: Rule
    regex: re.Pattern[str] | None
    refused: bool


@dataclass(frozen=True, slots=True)
class ConditionProgram:
    """The once-compiled customer matcher for one plan."""

    prepared: tuple[_Prepared, ...]


@dataclass(frozen=True, slots=True)
class ConditionScan:
    findings: tuple[Finding, ...]
    refuse_code: str | None


def compile_conditions(rules: Sequence[Rule]) -> ConditionProgram:
    """Compile every customer condition on ``rules``. Call this when the plan is applied."""
    prepared: list[_Prepared] = []
    for rule in rules:
        item = _prepare(rule)
        if item is not None:
            prepared.append(item)
    return ConditionProgram(tuple(prepared))


def scan_conditions(
    program: ConditionProgram,
    text: str,
    *,
    budget_s: float,
    phase: Phase,
    field: str = "",
    stage: str | None = None,
    surface: Surface | None = None,
) -> ConditionScan:
    """Scan ``text`` with the compiled program. Does not recompile.

    ``surface`` limits the scan to rules that name it. ``None`` keeps the
    phase-only selection, matching the resolver when the caller omits it.
    """
    findings: list[Finding] = []
    for item in program.prepared:
        if not _applies(item.rule, phase, field, stage, surface):
            continue
        if item.refused:
            return ConditionScan((), POLICY_REGEX_TIMEOUT)
        found, timed_out = _match(item, text, budget_s)
        if timed_out:
            return ConditionScan((), POLICY_REGEX_TIMEOUT)
        if found is not None:
            findings.append(found)
    return ConditionScan(tuple(findings), None)


def raise_if_refused(scanned: ConditionScan) -> None:
    """Turn a refused scan into ``PolicyRegexRefusal`` for the request path."""
    if scanned.refuse_code is not None:
        raise PolicyRegexRefusal(scanned.refuse_code)


def _prepare(rule: Rule) -> _Prepared | None:
    condition = rule.condition
    if condition is None or condition.kind is RuleKind.DETECTOR or rule.mode is Mode.OFF:
        return None
    if condition.kind is RuleKind.KEYWORDS:
        return _Prepared(rule, None, False)
    if _redos_shape(condition.pattern):
        return _Prepared(rule, None, True)
    try:
        compiled = re.compile(condition.pattern)
    except re.error:
        return _Prepared(rule, None, True)
    return _Prepared(rule, compiled, False)


def _redos_shape(pattern: str) -> bool:
    probed = pattern.replace(r"\(", "(").replace(r"\)", ")")
    return bool(
        _NESTED_QUANTIFIER.search(probed)
        or _QUANTIFIED_WILDCARD.search(probed)
        or _QUANTIFIED_ALTERNATION.search(probed)
    )


def _applies(
    rule: Rule,
    phase: Phase,
    field: str,
    stage: str | None,
    surface: Surface | None,
) -> bool:
    condition = rule.condition
    if condition is None:
        return False
    if surface is not None and surface not in rule.surfaces:
        return False
    if not _phase_ok(condition.direction, phase):
        return False
    if condition.field and field and condition.field != field:
        return False
    if condition.pipeline_stage:
        return stage == condition.pipeline_stage
    return True


def _phase_ok(scope: RuleScope, phase: Phase) -> bool:
    if scope is RuleScope.BOTH:
        return True
    return scope.value == phase.value


def _match(item: _Prepared, text: str, budget_s: float) -> tuple[Finding | None, bool]:
    """Return the finding, if any, and whether the match timed out."""
    condition = item.rule.condition
    if condition is None:
        return None, False
    if condition.kind is RuleKind.KEYWORDS:
        spans = _keyword_spans(condition.keywords, text)
        if not spans:
            return None, False
        return _finding(item.rule, spans), False
    if item.regex is None:
        return None, True
    matched = _regex_spans(item.regex, text, budget_s)
    if matched is None:
        return None, True
    if not matched:
        return None, False
    return _finding(item.rule, matched), False


def _keyword_spans(keywords: tuple[str, ...], text: str) -> tuple[Span, ...]:
    haystack = text.casefold()
    spans: list[Span] = []
    for word in keywords:
        needle = word.casefold()
        if not needle:
            continue
        start = 0
        while True:
            index = haystack.find(needle, start)
            if index < 0:
                break
            end = index + len(needle)
            if text[index:end].casefold() == needle:
                spans.append(Span(start=index, end=end))
            start = end
    return tuple(spans)


def _regex_spans(pattern: re.Pattern[str], text: str, budget_s: float) -> tuple[Span, ...] | None:
    found: list[Span] = []
    failed: list[BaseException] = []

    def _work() -> None:
        try:
            for match in pattern.finditer(text):
                start, end = match.span()
                if end > start:
                    found.append(Span(start=start, end=end))
        except re.error as exc:
            failed.append(exc)

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(budget_s)
    if worker.is_alive() or failed:
        return None
    return tuple(found)


def _finding(rule: Rule, spans: tuple[Span, ...]) -> Finding:
    return Finding(
        detector=rule.rule_id,
        detector_version=_DETECTOR_VERSION,
        category=rule.category,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=spans,
        evidence=None,
    )
