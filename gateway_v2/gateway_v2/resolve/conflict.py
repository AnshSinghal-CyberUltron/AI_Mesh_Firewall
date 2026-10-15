"""Per-finding rule selection, priority ordering, and the name-free tie-break.

GW07 (§10.5.3 steps 222-227, C22). Pure helpers for the single resolver. Every
symbol here imports ONLY ``gateway_v2.domain`` (the "Resolve imports only domain"
import-linter contract). These helpers answer, for one finding: which rules
select it, and which ENFORCE action wins among them.

The equal-priority tie-break is the C22 critical fix: it is the most-restrictive
MAPPED action among the tied rules, decided via the shipped ``most_restrictive``
ranking over the Action->Disposition map -- NEVER by ``rule_id`` ordering, so a
rename can never flip the outcome.
"""

from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.domain import (
    Action,
    ExecutionPlan,
    Finding,
    Mode,
    Phase,
    Rule,
    RuleScope,
    most_restrictive,
)
from gateway_v2.resolve.decision import _action_to_disposition


def _phase_admits(scope: RuleScope, phase: Phase) -> bool:
    """Whether a rule of ``scope`` is admitted at ``phase``.

    INPUT admits INPUT/BOTH; OUTPUT admits OUTPUT/BOTH (Decision 1). A pure
    predicate rather than a module-level map (no module mutable).
    """
    if phase is Phase.INPUT:
        return scope is RuleScope.INPUT or scope is RuleScope.BOTH
    return scope is RuleScope.OUTPUT or scope is RuleScope.BOTH


def _select_rules(plan: ExecutionPlan, phase: Phase) -> tuple[Rule, ...]:
    """The phase-admitted, non-OFF rules of ``plan``.

    A rule is admitted when its ``scope`` admits ``phase`` (INPUT -> INPUT/BOTH,
    OUTPUT -> OUTPUT/BOTH). ``Mode.OFF`` rules never select -- OFF means no work
    (§10.5.3 step 222). Order is preserved from ``plan.rules``.
    """
    return tuple(
        rule
        for rule in plan.rules
        if _phase_admits(rule.scope, phase) and rule.mode is not Mode.OFF
    )


def _rules_for(finding: Finding, selected: Sequence[Rule]) -> tuple[Rule, ...]:
    """The category join: ``selected`` rules whose category matches ``finding``.

    ``category`` is the only shared, type-identical field between ``Finding`` and
    ``Rule``, so it is the join key (§10.5.3 step 222 / Decision 1). Order is
    preserved from ``selected``.
    """
    return tuple(rule for rule in selected if rule.category == finding.category)


def _winning_action(finding: Finding, rules: Sequence[Rule]) -> Action | None:
    """The winning ENFORCE action for ``finding`` among its selecting ``rules``.

    Among the ENFORCE rules (``rule.mode is Mode.ENFORCE``) order by ``priority``
    DESCENDING -- highest priority wins, so signed organisation intent (given the
    higher priority by GW05) overrides a detector recommendation. The
    equal-priority tie-break is NAME-FREE: among the tied rules pick the one
    whose action maps to the most-restrictive ``Disposition`` via
    ``most_restrictive``, and return THAT rule's action (C22; never ``rule_id``
    ordering). MONITOR rules contribute nothing (they are not ENFORCE). Returns
    ``None`` when no ENFORCE rule selects ``finding``.
    """
    enforcing = [rule for rule in rules if rule.mode is Mode.ENFORCE]
    if not enforcing:
        return None
    top_priority = max(rule.priority for rule in enforcing)
    tied = tuple(rule for rule in enforcing if rule.priority == top_priority)
    winner_disposition = most_restrictive(
        tuple(_action_to_disposition(rule.action) for rule in tied),
    )
    for rule in tied:
        if _action_to_disposition(rule.action) is winner_disposition:
            return rule.action
    return tied[0].action


__all__ = (
    "_rules_for",
    "_select_rules",
    "_winning_action",
)
