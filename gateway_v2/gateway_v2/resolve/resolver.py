"""The single pure resolver: ``resolve(findings, plan, phase) -> Decision``.

GW07 (§10.5.3 "The resolver -- the single authority", C22). This is the single
security-decision authority of the AI Mesh Firewall: every disposition outcome
is produced here, so any second decision site becomes a build failure.

Pure and TOTAL: no I/O, no clock, no globals, no logging, no HTTP, and it raises
nothing for any input of the declared types. Error-shaped conditions are data
(a FindingStatus, a FailurePosture), never exceptions. Imports ONLY
``gateway_v2.domain`` and the resolve-package helpers, keeping the "Resolve
imports only domain" import-linter contract green.
"""

from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.domain import (
    Action,
    Decision,
    Disposition,
    ExecutionPlan,
    FailurePosture,
    Finding,
    FindingDisposition,
    FindingStatus,
    Mode,
    Phase,
    Rule,
    Span,
    Transformation,
    most_restrictive,
)
from gateway_v2.resolve.conflict import _rules_for, _select_rules, _winning_action
from gateway_v2.resolve.decision import _action_to_disposition, _transformations_for


def _unavailable_candidate(rules: Sequence[Rule]) -> Disposition | None:
    """Candidate disposition for an UNAVAILABLE finding, per its rules' posture.

    Only ENFORCE selecting rules route an UNAVAILABLE finding; MONITOR/OFF rules
    contribute nothing to disposition per runbook step 224 ("For MONITOR rules,
    record the finding and contribute nothing to disposition"), so a MONITOR
    rule's posture never forces a BLOCK (P7). If ANY ENFORCE selecting rule's
    ``on_unavailable`` is FAIL_CLOSED -> BLOCK candidate; otherwise (all
    FAIL_OPEN, or no ENFORCE selecting rule) -> ``None`` (no candidate / ALLOW),
    per Requirement 3.3/3.4.
    """
    for rule in rules:
        if rule.mode is Mode.ENFORCE and rule.on_unavailable is FailurePosture.FAIL_CLOSED:
            return Disposition.BLOCK
    return None


def _executed_candidate(
    finding: Finding,
    rules: Sequence[Rule],
) -> tuple[Disposition, str] | None:
    """Candidate disposition + deciding ``rule_id`` for an EXECUTED finding.

    The winning ENFORCE action (priority desc, name-free tie-break) maps to a
    disposition. A non-selecting finding (``action is None``) or an ALLOW
    candidate contributes no restriction, so ``None`` is returned -- only a
    non-ALLOW candidate yields a ``FindingDisposition``, keeping ``per_finding``
    minimal and ``most_restrictive(per_finding)`` well-defined (Decision 3).
    """
    action = _winning_action(finding, rules)
    if action is None:
        return None
    disposition = _action_to_disposition(action)
    if disposition is Disposition.ALLOW:
        return None
    return disposition, _deciding_rule_id(rules, action)


def _deciding_rule_id(rules: Sequence[Rule], action: Action) -> str:
    """The ``rule_id`` of the winning ENFORCE rule (an audit record, not an input).

    Recording a name is not deciding by a name: the tie-break in
    ``_winning_action`` is name-free; this only names the rule that already won.
    """
    for rule in rules:
        if rule.action == action:
            return rule.rule_id
    return ""


def _mergeable(current: Transformation, nxt: Transformation) -> bool:
    """True iff ``nxt`` (sorted at or after ``current``) folds into ``current``.

    Both must share ``kind`` and ``replacement``, and their spans must overlap
    (``nxt.start < current.end``) or be identical (which also catches two equal
    zero-length spans). Spans that only touch (``current.end == nxt.start``) are
    NOT mergeable: they are two adjacent values, possibly in two chat deltas or
    two embeddings items, and fusing them would move a replacement across that
    boundary.
    """
    if current.kind != nxt.kind or current.replacement != nxt.replacement:
        return False
    return nxt.span == current.span or nxt.span.start < current.span.end


def _normalise_redactions(
    transformations: Sequence[Transformation],
) -> tuple[Transformation, ...]:
    """Sort REDACT transformations by span and coalesce same-replacement overlaps.

    Overlapping or identical spans with the same ``kind`` and ``replacement``
    collapse into one transformation over their union, so two patterns (or a
    single-part scan and a joined-parts scan) matching the same bytes yield one
    clean edit. Touching spans are never merged (see :func:`_mergeable`).

    Overlapping spans with DIFFERENT replacements are deliberately left as they
    are: the design specifies a union only for a shared replacement, and picking
    one replacement over another would be a second redaction policy. The
    overlap then reaches dispatch, whose ``apply_and_verify`` rejects it and
    the request fails closed (Property 4). The resolver only ever emits the
    GW09 ``REDACTION_PLACEHOLDER``, so this arm is unreachable from ``resolve``.

    Preservation (clauses 3.4/3.5): when no pair merges, the input order is
    returned unchanged, so a decision with non-overlapping transformations is
    identical to the pre-fix decision.
    """
    ordered = sorted(transformations, key=lambda t: (t.span.start, t.span.end))
    out: list[Transformation] = []
    merged = False
    for item in ordered:
        if out and _mergeable(out[-1], item):
            current = out[-1]
            out[-1] = Transformation(
                kind=current.kind,
                span=Span(
                    start=current.span.start,
                    end=max(current.span.end, item.span.end),
                ),
                replacement=current.replacement,
            )
            merged = True
        else:
            out.append(item)
    if not merged:
        return tuple(transformations)
    return tuple(out)


def resolve(
    findings: Sequence[Finding],
    plan: ExecutionPlan,
    phase: Phase,
) -> Decision:
    """Resolve ``findings`` under ``plan`` for ``phase`` into one ``Decision``.

    Steps (§10.5.3) in strict order: select phase-admitted non-OFF rules; build
    at most one candidate ``FindingDisposition`` per finding (SKIPPED records
    only; UNAVAILABLE routes through ``on_unavailable``; EXECUTED takes its
    winning ENFORCE action); the request disposition is ``most_restrictive`` of
    the per-finding dispositions (empty -> ALLOW, BLOCK terminal);
    transformations come from REDACT candidates, normalised by
    :func:`_normalise_redactions` (same-replacement overlaps coalesced, touching
    spans kept apart); ``findings`` records every input finding, in input order;
    each ``FindingDisposition.finding_index`` is its finding's index into that
    tuple; ``plan_version`` is ``plan.version``.
    """
    selected = _select_rules(plan, phase)
    per_finding: list[FindingDisposition] = []
    transformations: list[Transformation] = []
    deciding_rules: list[str] = []
    unavailable_detectors: list[str] = []

    # ``Decision.findings`` is ``tuple(findings)``: same order, nothing filtered,
    # so the enumerate index is the index into the final tuple (Property 3).
    recorded = tuple(findings)
    for index, finding in enumerate(recorded):
        rules = _rules_for(finding, selected)
        candidate: Disposition | None = None
        rule_id: str | None = None
        if finding.status is FindingStatus.SKIPPED:
            candidate = None
        elif finding.status is FindingStatus.UNAVAILABLE:
            if finding.detector not in unavailable_detectors:
                unavailable_detectors.append(finding.detector)
            candidate = _unavailable_candidate(rules)
        else:
            executed = _executed_candidate(finding, rules)
            if executed is not None:
                candidate, rule_id = executed

        if candidate is None:
            continue
        per_finding.append(
            FindingDisposition(
                detector=finding.detector,
                disposition=candidate,
                finding_index=index,
            ),
        )
        if rule_id is not None:
            deciding_rules.append(rule_id)
        if candidate is Disposition.REDACT:
            transformations.extend(_transformations_for(finding, candidate))

    disposition = most_restrictive(tuple(fd.disposition for fd in per_finding))
    return Decision(
        disposition=disposition,
        per_finding=tuple(per_finding),
        transformations=_normalise_redactions(transformations),
        findings=recorded,
        plan_version=plan.version,
        deciding_rules=tuple(deciding_rules),
        unavailable_detectors=tuple(unavailable_detectors),
    )


__all__ = ("resolve",)
