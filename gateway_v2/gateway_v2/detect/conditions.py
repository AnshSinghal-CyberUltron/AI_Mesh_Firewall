"""Customer rule conditions, compiled once per plan and scanned under the guard-wait budget.

A keyword, regex, or pattern condition emits one ``Finding`` whose ``detector`` is the
rule id, so the resolver joins that rule. A detector-kind condition emits nothing here:
the built-in detectors already emit the category. A nested-quantifier, quantified-wildcard,
or quantified-alternation pattern, a pattern nested deeper than ``MAX_GROUP_DEPTH``, a pattern
``re`` cannot compile (for any reason, not only ``re.error``), or a match that raises or exceeds
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

MAX_GROUP_DEPTH = 32
"""The deepest group nesting a customer pattern may have.

`re` parses and compiles a pattern recursively, about two Python frames per nesting level, so
a 999-character pattern of 499 nested groups raises `RecursionError` (not `re.error`), and a
~400-deep one compiles or raises depending on how deep the caller's stack already is. Checking
the depth BEFORE `re` sees the pattern makes the answer the same on every stack: 32 levels need
about 72 frames (measured on 3.12 and 3.13), far inside the default limit of 1000.

The twin of this limit and of `group_depth` is `control/ai_mesh_control/policy/serializers.py`
(the packages share no code); both suites pin the same vectors, so change them together."""

_INLINE_FLAGS = re.compile(r"\(\?([aiLmstux]*)(?:-([aiLmstux]*))?([:)])")
"""`(?flags)` (global: opens no group) or `(?flags-flags:` (scoped). Deliberately permissive:
anything `re` would reject as flags aborts its parse there, so it cannot nest deeper after it."""


def group_depth(pattern: str) -> int:
    """The deepest group nesting in ``pattern``, by one non-recursive left-to-right scan.

    It follows `re`'s own tokenisation, so it never under-counts what `re` nests; where it
    differs it over-counts, which only refuses more:

    * ``\\`` escapes the next character, so ``\\(`` is a literal;
    * a character class ``[...]`` is skipped: a ``]`` right after ``[`` or ``[^`` is a literal,
      ``[`` inside a class is a literal, and the class ends at the first other unescaped ``]``;
    * a comment group ``(?#...)`` is skipped up to its first unescaped ``)``, as `re` does;
    * VERBOSE is tracked per scope exactly as `re` does (global ``(?x)`` for the top level,
      ``(?x:...)`` / ``(?-x:...)`` for one group, inherited by every other group), and in a
      VERBOSE scope ``#`` to the end of the line is a comment and skipped;
    * the over-counts: ``(?P=name)`` (a back-reference, not a group) and the condition of
      ``(?(1)...)`` each count one level for the moment they are open;
    * an unbalanced ``)`` never takes the depth below zero; `re` refuses the pattern anyway.
    """
    scopes = [False]  # VERBOSE of every open scope; scopes[-1] is the current one
    deepest = 0
    index, end = 0, len(pattern)
    while index < end:
        char = pattern[index]
        if char == "\\":
            index += 2
        elif char == "[":
            index = _class_end(pattern, index + 1)
        elif char == "#" and scopes[-1]:
            index = _skip_past(pattern, index + 1, "\n")
        elif char == "(" and pattern.startswith("?#", index + 1):
            index = _skip_past(pattern, index + 3, ")")
        elif char == "(":
            verbose = scopes[-1]
            flags = _INLINE_FLAGS.match(pattern, index)
            if flags is not None and flags.group(3) == ")":
                scopes[-1] = verbose or "x" in flags.group(1)
                index = flags.end()
                continue
            if flags is not None:
                verbose = (verbose or "x" in flags.group(1)) and "x" not in (flags.group(2) or "")
            scopes.append(verbose)
            deepest = max(deepest, len(scopes) - 1)
            index = index + 1 if flags is None else flags.end()
        else:
            if char == ")" and len(scopes) > 1:
                scopes.pop()
            index += 1
    return deepest


def _class_end(pattern: str, index: int) -> int:
    """The index after the ``]`` closing the class whose body starts at ``index``."""
    end = len(pattern)
    if index < end and pattern[index] == "^":
        index += 1
    if index < end and pattern[index] == "]":
        index += 1
    return _skip_past(pattern, index, "]")


def _skip_past(pattern: str, index: int, stop: str) -> int:
    """The index after the first unescaped ``stop`` at or after ``index`` (or the end)."""
    end = len(pattern)
    while index < end:
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == stop:
            return index + 1
        index += 1
    return end


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
    if _redos_shape(condition.pattern) or group_depth(condition.pattern) > MAX_GROUP_DEPTH:
        return _Prepared(rule, None, True)
    try:
        compiled = re.compile(condition.pattern)
    except Exception:  # noqa: BLE001 - re also raises RecursionError/OverflowError: refuse the rule
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
    """Every span, or ``None`` (a failed scan) unless the worker ran ``finditer`` to the end.

    Only completion is trusted: a worker that raised anything, or is still running at the
    budget, leaves a partial list that must never be read as "these are all the matches".
    """
    found: list[Span] = []
    finished: list[bool] = []

    def _work() -> None:
        try:
            for match in pattern.finditer(text):
                start, end = match.span()
                if end > start:
                    found.append(Span(start=start, end=end))
        except Exception:  # noqa: BLE001 - any fault mid-scan is a refusal, never partial spans
            return
        finished.append(True)

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(budget_s)
    if worker.is_alive() or not finished:
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
