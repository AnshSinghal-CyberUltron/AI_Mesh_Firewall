"""THE single authority (runbook §10.5.3). Pure: no I/O, no clock, no globals.

Resolution order (total):
 1. Partition findings by the rule that selected the detector for this phase;
    a finding with no selecting rule is recorded and has no effect.
 2. Drop SKIPPED. Route UNAVAILABLE through the owning rule's on_unavailable:
    FAIL_CLOSED -> BLOCK candidate, DEGRADE_TO(a) -> a candidate,
    FAIL_OPEN -> no candidate (recorded in unavailable_detectors).
 3. MONITOR rules record and contribute nothing.
 4. ENFORCE rules with a positive finding contribute their action.
    Positive: EXECUTED and (confidence >= threshold if the rule has one, else spans).
 5. Conflicts resolve by explicit priority (lower number first), tie-break
    rule_id ascending. No severity maximum. The top candidate's action is the
    disposition; REDACT candidates at any priority contribute transformations
    when the disposition dispatches (REDACT and FLAG compose; a top-priority
    FLAG with redactions pending reports REDACT).
 6. BLOCK is terminal: no transformations, no authorization.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType

from rvproto.domain.decision import (
    _RESOLVER_CAPABILITY,
    Decision,
    DispatchAuthorization,
    Disposition,
    Transformation,
)
from rvproto.domain.findings import Finding, FindingStatus
from rvproto.domain.plan import Action, ExecutionPlan, Mode, Phase, Posture, Rule

_TO_DISPOSITION = MappingProxyType(
    {
        Action.ALLOW: Disposition.ALLOW,
        Action.FLAG: Disposition.FLAG,
        Action.REDACT: Disposition.REDACT,
        Action.BLOCK: Disposition.BLOCK,
    }
)


def _candidate(f: Finding, rule: Rule) -> Action | None:
    if f.status is FindingStatus.SKIPPED or rule.mode is not Mode.ENFORCE:
        return None
    if f.status is FindingStatus.UNAVAILABLE:
        posture = rule.on_unavailable
        if posture.kind is Posture.FAIL_CLOSED:
            return Action.BLOCK
        if posture.kind is Posture.DEGRADE_TO:
            return posture.degrade_to
        return None
    if rule.threshold is not None:
        positive = f.confidence is not None and f.confidence >= rule.threshold
    else:
        positive = bool(f.spans)
    return rule.action if positive else None


def resolve(findings: Sequence[Finding], plan: ExecutionPlan, phase: Phase) -> Decision:
    cands: list[tuple[int, str, Action, Finding]] = []
    unavailable: list[str] = []
    for f in findings:
        rule = plan.selection.get((phase, f.detector))
        if rule is None:
            continue
        if f.status is FindingStatus.UNAVAILABLE:
            unavailable.append(f.detector)
        act = _candidate(f, rule)
        if act is not None:
            cands.append((rule.priority, rule.rule_id, act, f))
    if not cands:
        return Decision(phase, Disposition.ALLOW, (), tuple(findings), plan.version, (),
                        tuple(unavailable))
    cands.sort(key=lambda c: (c[0], c[1]))
    top = cands[0][2]
    if top is Action.BLOCK or top is Action.ALLOW:
        return Decision(phase, _TO_DISPOSITION[top], (), tuple(findings), plan.version,
                        (cands[0][1],), tuple(unavailable))
    transforms: list[Transformation] = []
    rules: list[str] = []
    for _, rule_id, act, f in cands:
        if rule_id not in rules:
            rules.append(rule_id)
        if act is Action.REDACT and f.status is FindingStatus.EXECUTED:
            transforms.extend(Transformation(s, f.category, f.detector) for s in f.spans)
    disposition = Disposition.REDACT if transforms else _TO_DISPOSITION[top]
    return Decision(phase, disposition, tuple(transforms), tuple(findings), plan.version,
                    tuple(rules), tuple(unavailable))


def authorize(decision: Decision, request_id: str) -> DispatchAuthorization | None:
    """Mint the dispatch capability; None for BLOCK (BLOCK cannot reach a provider)."""
    if decision.disposition is Disposition.BLOCK:
        return None
    return DispatchAuthorization(
        request_id=request_id,
        plan_version=decision.plan_version,
        disposition=decision.disposition,
        n_transformations=len(decision.transformations),
        token=_RESOLVER_CAPABILITY,
    )
