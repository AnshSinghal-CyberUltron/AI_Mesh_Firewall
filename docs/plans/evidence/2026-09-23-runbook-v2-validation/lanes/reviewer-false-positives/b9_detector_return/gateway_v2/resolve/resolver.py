"""PURE: (Finding[], ExecutionPlan) -> Decision. Imports only domain/ (downward)."""

from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.contracts.domain.decision import Decision
from gateway_v2.contracts.domain.findings import Finding
from gateway_v2.contracts.domain.plan import ExecutionPlan, Rule
from gateway_v2.contracts.domain.taxonomy import (
    Action,
    Disposition,
    FailurePosture,
    FindingStatus,
    Mode,
    Phase,
)


def _candidate(finding: Finding, rule: Rule) -> Action | None:
    if finding.status is FindingStatus.SKIPPED or rule.mode is not Mode.ENFORCE:
        return None
    if finding.status is FindingStatus.UNAVAILABLE:
        return Action.BLOCK if rule.on_unavailable is FailurePosture.FAIL_CLOSED else None
    return rule.action


def resolve(findings: Sequence[Finding], plan: ExecutionPlan, phase: Phase) -> Decision:
    del phase
    ordered = sorted(plan.rules, key=lambda r: (-r.priority, r.rule_id))
    winners = [
        (rule, act)
        for rule in ordered
        for f in findings
        if f.category is rule.category and (act := _candidate(f, rule)) is not None
    ]
    disposition = Disposition(winners[0][1].value) if winners else Disposition.ALLOW
    return Decision(
        disposition=disposition,
        transformations=(),
        findings=tuple(findings),
        plan_version=plan.version,
        deciding_rules=tuple(r.rule_id for r, _ in winners),
        unavailable_detectors=tuple(
            f.detector for f in findings if f.status is FindingStatus.UNAVAILABLE
        ),
    )
