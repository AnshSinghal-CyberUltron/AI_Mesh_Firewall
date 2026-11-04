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
    Category,
    ExecutionPlan,
    Finding,
    Mode,
    Phase,
    Rule,
    RuleKind,
    RuleScope,
    Surface,
    builtin_output_detector,
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


def _select_rules(
    plan: ExecutionPlan,
    phase: Phase,
    surface: Surface | None = None,
    stage: str | None = None,
) -> tuple[Rule, ...]:
    """The phase-admitted, non-OFF rules of ``plan`` for one surface.

    A rule is admitted when its ``scope`` admits ``phase`` (INPUT -> INPUT/BOTH,
    OUTPUT -> OUTPUT/BOTH). ``Mode.OFF`` rules never select -- OFF means no work
    (§10.5.3 step 222). When ``surface`` is set, the rule must also name that
    surface (GW26). ``None`` keeps the phase-only selection. A condition with a
    pipeline stage matches only that stage, so a chat request (no stage) skips it.
    Order is preserved from ``plan.rules``.
    """
    return tuple(
        rule
        for rule in plan.rules
        if _phase_admits(rule.scope, phase)
        and rule.mode is not Mode.OFF
        and (surface is None or surface in rule.surfaces)
        and _stage_admits(rule, stage)
    )


def _stage_admits(rule: Rule, stage: str | None) -> bool:
    condition = rule.condition
    if condition is None or not condition.pipeline_stage:
        return True
    return stage == condition.pipeline_stage


def _rules_for(finding: Finding, selected: Sequence[Rule]) -> tuple[Rule, ...]:
    """Join ``finding`` to the rule that produced it, else to category rules.

    A customer condition sets ``finding.detector`` to the rule id, so that rule
    is the only join. Built-in findings keep the category join, and a keyword
    rule of the same category is not selected by that join.
    """
    by_id = tuple(rule for rule in selected if rule.rule_id == finding.detector)
    if by_id:
        return by_id
    return tuple(
        rule for rule in selected if rule.category == finding.category and _category_join(rule)
    )


#: Categories only the platform built-in output detectors produce (GW33). Nothing produced them
#: before this card, so excluding non-built-in rules from the category join changes no pre-card
#: behaviour; it stops a customer rule that fell into the publisher's ``exfil`` catch-all from
#: governing a built-in finding (D-32). Built-in findings join by identity (``Finding.detector``).
_BUILTIN_ONLY = frozenset(
    {Category.EXFIL, Category.IP_LEAK, Category.HALLUCINATION, Category.CANARY},
)


def _category_join(rule: Rule) -> bool:
    if rule.category in _BUILTIN_ONLY and builtin_output_detector(rule) is None:
        return False
    condition = rule.condition
    return condition is None or condition.kind is RuleKind.DETECTOR


def _winning_rule(finding: Finding, rules: Sequence[Rule]) -> Rule | None:
    """The winning ENFORCE rule for ``finding`` among its selecting ``rules``.

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
            return rule
    return tied[0]


def _winning_action(finding: Finding, rules: Sequence[Rule]) -> Action | None:
    """The winning ENFORCE action (see :func:`_winning_rule`); ``None`` when none selects."""
    rule = _winning_rule(finding, rules)
    return None if rule is None else rule.action


__all__ = (
    "_rules_for",
    "_select_rules",
    "_winning_action",
    "_winning_rule",
)
