"""PURE: (Finding[], ExecutionPlan) -> Decision — rb.md §10.5.3 (L2212, L2219-2225)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from collections.abc import Sequence

# L2212 'def resolve(findings: Sequence[Finding], plan: ExecutionPlan, phase: Phase) -> Decision'
# L2221 'Drop SKIPPED. Route UNAVAILABLE through that rule's on_unavailable posture'
from gateway_v2.detect.base import FindingStatus

# L2222-2223 MONITOR contributes nothing; ENFORCE action is the candidate
from gateway_v2.plan.model import Action, FailurePosture, Mode
from gateway_v2.resolve.conflict import by_priority
from gateway_v2.resolve.decision import Decision, Disposition, Phase

if TYPE_CHECKING:
    from gateway_v2.detect.base import Finding
    from gateway_v2.plan.model import ExecutionPlan, Rule


def _candidate(finding: Finding, rule: Rule) -> Action | None:
    if finding.status is FindingStatus.SKIPPED or rule.mode is not Mode.ENFORCE:
        return None
    if finding.status is FindingStatus.UNAVAILABLE:
        if rule.on_unavailable is FailurePosture.FAIL_CLOSED:
            return Action.BLOCK
        return None
    return rule.action


def resolve(findings: Sequence[Finding], plan: ExecutionPlan, phase: Phase) -> Decision:
    del phase
    winners: list[tuple[Rule, Action]] = []
    for rule in by_priority(plan.rules):
        for finding in findings:
            if finding.category is not rule.category:
                continue
            action = _candidate(finding, rule)
            if action is not None:
                winners.append((rule, action))
    disposition = Disposition(winners[0][1].value) if winners else Disposition.ALLOW
    unavailable = tuple(f.detector for f in findings if f.status is FindingStatus.UNAVAILABLE)
    return Decision(
        disposition=disposition,
        transformations=(),
        findings=tuple(findings),
        plan_version=plan.version,
        deciding_rules=tuple(r.rule_id for r, _ in winners),
        unavailable_detectors=unavailable,
    )
