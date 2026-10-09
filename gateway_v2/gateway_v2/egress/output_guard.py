"""Output detect -> resolve -> emit glue (R2-06 / GW12b).

This module is the egress-layer glue between completed output matches and the
``Decision`` the streaming pipeline applies to released bytes. It provides:

* ``OutputResolver`` -- the protocol a resolver satisfies: map completed
  findings to one ``Decision`` whose disposition is the ``most_restrictive`` of
  the per-finding dispositions (R2.1/R2.2). GW08 ships the real resolver behind
  this same protocol; GW12b ships a minimal in-process one keyed off the
  holdback trade-off table so the harness and local tests have something to run.
* ``enforcing_output_rules(plan)`` -- the Enforcing_Output_Rule filter (R2): the
  OUTPUT/BOTH rules whose ``mode is Mode.ENFORCE`` and whose ``action`` redacts,
  blocks, or rewrites. An empty result means no output enforcement, so the
  pipeline holds nothing.
* ``apply_decision(released, decision, base_offset)`` -- apply a ``Decision``'s
  redaction ``Transformation``s to a released slice (R3.3), raising
  ``OutputBlocked`` on a BLOCK disposition (R3.4). It is fail-closed: a
  redaction whose span is not fully covered by the released slice raises rather
  than letting a partially-redacted match through.

Layering: this module imports only from ``gateway_v2.domain``. The import-linter
layer contract in ``pyproject.toml`` orders ``detect`` *above* ``egress``, so an
``egress -> detect`` import is forbidden; the per-class disposition map below is
therefore defined locally against the same class names as the holdback
trade-off table (``detect.holdback.TRADE_OFF``) rather than importing it. The
two must stay in agreement: a redact-the-remainder class maps to ``REDACT`` and
a terminate-the-stream class maps to ``BLOCK``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from gateway_v2.domain import (
    Action,
    Decision,
    Disposition,
    ExecutionPlan,
    Finding,
    FindingDisposition,
    FindingStatus,
    Mode,
    Rule,
    RuleScope,
    Span,
    Transformation,
    most_restrictive,
)

__all__ = (
    "DEFAULT_PLAN_VERSION",
    "REDACTION_PLACEHOLDER",
    "MinimalOutputResolver",
    "OutputBlocked",
    "OutputResolver",
    "apply_decision",
    "enforcing_output_rules",
)

#: The placeholder a redaction ``Transformation`` writes in place of a span. A
#: fixed sentinel is enough for the harness; GW08's resolver may vary it.
REDACTION_PLACEHOLDER: str = "[REDACTED]"

#: ``plan_version`` stamped on a ``Decision`` the minimal resolver produces when
#: it has no pinned plan to name. GW08's resolver uses the real plan version.
DEFAULT_PLAN_VERSION: str = "gw12b-minimal"

#: Rule actions that make an OUTPUT rule *enforcing* (R2): they change or stop
#: released bytes, as opposed to ALLOW/FLAG which only observe.
_ENFORCING_ACTIONS: frozenset[Action] = frozenset(
    {Action.REDACT, Action.BLOCK, Action.REWRITE},
)

#: OUTPUT-facing rule scopes (R2): a rule enforces output when it is scoped to
#: OUTPUT or BOTH.
_OUTPUT_SCOPES: frozenset[RuleScope] = frozenset(
    {RuleScope.OUTPUT, RuleScope.BOTH},
)


class OutputBlocked(Exception):
    """Raised when the output ``Decision`` blocks the stream (R3.4).

    Carries the deciding ``Decision`` so the caller can emit the terminal error
    frame and record the deciding rules without re-resolving.
    """

    def __init__(self, decision: Decision) -> None:
        super().__init__(
            f"output blocked by decision (deciding_rules={decision.deciding_rules})",
        )
        self.decision = decision


@runtime_checkable
class OutputResolver(Protocol):
    """Maps completed output matches to one ``Decision``.

    The disposition of the returned ``Decision`` is the ``most_restrictive`` of
    the per-finding dispositions (R2.1). GW08's real resolver implements this
    same surface; the pipeline depends only on this protocol.
    """

    def decide(self, matches: Sequence[Finding]) -> Decision: ...


def enforcing_output_rules(plan: ExecutionPlan) -> tuple[Rule, ...]:
    """Return the plan's Enforcing_Output_Rules (R2).

    A rule enforces output when its ``scope`` is OUTPUT or BOTH, its ``mode`` is
    ``Mode.ENFORCE``, and its ``action`` is one of REDACT/BLOCK/REWRITE. An empty
    result means the stream has no output enforcement, so the pipeline holds no
    bytes for disambiguation (R2.2).
    """
    return tuple(
        rule
        for rule in plan.rules
        if rule.scope in _OUTPUT_SCOPES
        and rule.mode is Mode.ENFORCE
        and rule.action in _ENFORCING_ACTIONS
    )


#: The disposition a finding whose detector names a trade-off class is given.
#: This mirrors ``detect.holdback.TRADE_OFF`` one-for-one -- redact-the-remainder
#: classes map to ``REDACT`` and terminate-the-stream classes to ``BLOCK`` -- but
#: is defined locally because ``detect`` sits above ``egress`` in the import
#: contract (an ``egress -> detect`` import is forbidden). The two tables must
#: stay in agreement; the test suite pins the class names and dispositions.
_CLASS_DISPOSITION: Mapping[str, Disposition] = MappingProxyType(
    {
        # redact-the-remainder classes -> REDACT.
        "aws": Disposition.REDACT,
        "api_token": Disposition.REDACT,
        "email": Disposition.REDACT,
        "uuid": Disposition.REDACT,
        "sha256": Disposition.REDACT,
        "url": Disposition.REDACT,
        "base64": Disposition.REDACT,
        # terminate-the-stream classes -> BLOCK.
        "jwt": Disposition.BLOCK,
        "card": Disposition.BLOCK,
    },
)


def _finding_disposition(finding: Finding) -> Disposition:
    """Disposition a single completed finding earns under the minimal resolver.

    A finding whose ``detector`` names a known trade-off class takes that
    class's disposition (REDACT for redact-the-remainder classes, BLOCK for
    terminate classes). An unknown executed finding is redacted by default
    (fail-safe: never silently ALLOW a sensitive match). A non-EXECUTED finding
    (SKIPPED/UNAVAILABLE) contributes no restriction and resolves to ALLOW.
    """
    if finding.status is not FindingStatus.EXECUTED:
        return Disposition.ALLOW
    mapped = _CLASS_DISPOSITION.get(finding.detector)
    if mapped is not None:
        return mapped
    return Disposition.REDACT


class MinimalOutputResolver:
    """A minimal in-process ``OutputResolver`` for the GW12b harness.

    It is a declarative pattern -> disposition map keyed off the holdback
    trade-off table (``redact_remainder`` -> REDACT, ``terminate`` -> BLOCK),
    producing a ``Decision`` whose disposition is the ``most_restrictive`` of the
    per-finding dispositions. GW08's resolver drops in behind the same protocol.
    """

    def __init__(
        self,
        *,
        plan_version: str = DEFAULT_PLAN_VERSION,
        placeholder: str = REDACTION_PLACEHOLDER,
    ) -> None:
        self._plan_version = plan_version
        self._placeholder = placeholder

    def decide(self, matches: Sequence[Finding]) -> Decision:
        """Resolve ``matches`` into one consistent ``Decision`` (R2.1/R2.2)."""
        per_finding: list[FindingDisposition] = []
        transformations: list[Transformation] = []
        for finding in matches:
            disposition = _finding_disposition(finding)
            per_finding.append(
                FindingDisposition(detector=finding.detector, disposition=disposition),
            )
            if disposition is Disposition.REDACT:
                for span in finding.spans:
                    transformations.append(
                        Transformation(
                            kind="redact",
                            span=span,
                            replacement=self._placeholder,
                        ),
                    )
        overall = most_restrictive(tuple(fd.disposition for fd in per_finding))
        deciding = tuple(
            fd.detector for fd in per_finding if fd.disposition is not Disposition.ALLOW
        )
        return Decision(
            disposition=overall,
            per_finding=tuple(per_finding),
            transformations=tuple(transformations),
            findings=tuple(matches),
            plan_version=self._plan_version,
            deciding_rules=deciding,
            unavailable_detectors=(),
        )


def _covers(span: Span, base_offset: int, length: int) -> bool:
    """Whether ``span`` (absolute coords) lies wholly inside the released slice.

    The released slice spans absolute bytes ``[base_offset, base_offset+length)``.
    A redaction may only run against a span every byte of which is present in the
    slice -- a partial or out-of-range span is a fail-closed condition (R3.3).
    """
    start = span.start - base_offset
    end = span.end - base_offset
    return 0 <= start <= end <= length


def apply_decision(released: str, decision: Decision, base_offset: int) -> str:
    """Apply ``decision`` to ``released`` and return the bytes to write downstream.

    ``decision.transformations`` carry absolute spans; ``base_offset`` is the
    absolute index of ``released[0]``, so a span is indexed into ``released`` by
    subtracting ``base_offset``. Each transformation's span is replaced by its
    ``replacement``.

    Fail-closed semantics:

    * ``Disposition.BLOCK`` raises ``OutputBlocked`` carrying ``decision`` -- no
      bytes are released (R3.4).
    * A redaction whose span is not fully covered by ``released`` (partial or
      out of range) raises ``ValueError`` rather than releasing a
      partially-redacted match (R3.3 fail-closed).

    Transformations that fall entirely outside the released slice (a match whose
    span has not yet entered this slice) are skipped -- they are applied when the
    slice that contains them is released.
    """
    if decision.disposition is Disposition.BLOCK:
        raise OutputBlocked(decision)

    length = len(released)
    # Collect in-range redaction spans; sort by start so replacements compose and
    # overlaps are detectable deterministically.
    edits: list[tuple[int, int, str]] = []
    for transformation in decision.transformations:
        span = transformation.span
        start = span.start - base_offset
        end = span.end - base_offset
        # A span wholly before or wholly after this slice is not ours to apply.
        if end <= 0 or start >= length:
            continue
        if not _covers(span, base_offset, length):
            raise ValueError(
                "fail-closed: redaction span is not fully covered by the released slice "
                f"(span={span.start}:{span.end}, slice={base_offset}:{base_offset + length})",
            )
        edits.append((start, end, transformation.replacement))

    if not edits:
        return released

    edits.sort(key=lambda item: (item[0], item[1]))
    out: list[str] = []
    cursor = 0
    for start, end, replacement in edits:
        if start < cursor:
            raise ValueError(
                "fail-closed: overlapping redaction spans in released slice",
            )
        out.append(released[cursor:start])
        out.append(replacement)
        cursor = end
    out.append(released[cursor:])
    return "".join(out)
