"""One plan lookup for every surface and both phases."""

from __future__ import annotations

from gateway_v2.domain.plan import ExecutionPlan, Phase, Rule, Surface
from gateway_v2.plan.snapshot import PlanState, ReplicaSnapshot


def lookup_plan(snapshot: ReplicaSnapshot, org_id: str, surface: Surface) -> PlanState:
    """Surface is part of the call so every entry point shares this function."""
    del surface
    return snapshot.lookup(org_id)


def rule_applies(rule: Rule, surface: Surface, phase: Phase) -> bool:
    if surface not in rule.surfaces:
        return False
    if rule.scope.value == "both":
        return True
    return rule.scope.value == phase.value


def applicable_rules(plan: ExecutionPlan, surface: Surface, phase: Phase) -> tuple[Rule, ...]:
    return tuple(rule for rule in plan.rules if rule_applies(rule, surface, phase))
