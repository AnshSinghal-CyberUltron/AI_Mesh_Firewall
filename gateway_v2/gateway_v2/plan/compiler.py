"""Compile a tenant selection into one immutable ExecutionPlan. No platform-default fill-in."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from gateway_v2.domain.category import Category
from gateway_v2.domain.plan import (
    Action,
    ExecutionPlan,
    FailurePosture,
    Mode,
    Rule,
    RuleScope,
    StreamingMode,
    Surface,
)

SUPPORTED_STREAMING = frozenset({StreamingMode.INCREMENTAL})
SUPPORTED_ACTIONS = frozenset(
    {Action.ALLOW, Action.FLAG, Action.REDACT, Action.BLOCK},
)


class CompileError(ValueError):
    """Save-time rejection. The previous plan keeps serving."""


@dataclass(frozen=True, slots=True)
class RuleDraft:
    rule_id: str
    category: Category
    mode: Mode
    action: Action
    threshold: float | None
    priority: int
    scope: RuleScope
    on_unavailable: FailurePosture
    surfaces: frozenset[Surface]


def detector_id(category: Category) -> str:
    return f"det.{category.value}"


def _hash_rules(rules: tuple[Rule, ...]) -> str:
    lines: list[str] = []
    for rule in sorted(rules, key=lambda item: item.rule_id):
        surfaces = ",".join(sorted(surface.value for surface in rule.surfaces))
        threshold = "" if rule.threshold is None else f"{rule.threshold:.4f}"
        lines.append(
            "|".join(
                (
                    rule.rule_id,
                    rule.category.value,
                    rule.mode.value,
                    rule.action.value,
                    threshold,
                    str(rule.priority),
                    rule.scope.value,
                    rule.on_unavailable.value,
                    surfaces,
                ),
            ),
        )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _check_draft(draft: RuleDraft) -> None:
    if not draft.rule_id.strip():
        raise CompileError("rule_id is required")
    if not isinstance(draft.category, Category):
        raise CompileError("category must be a Category member")
    if draft.action not in SUPPORTED_ACTIONS:
        raise CompileError(
            f"action {draft.action.value} is not supported; "
            "use allow, flag, redact, or block",
        )
    if draft.threshold is not None and not 0.0 <= draft.threshold <= 1.0:
        raise CompileError("threshold must be between 0 and 1")
    if not draft.surfaces:
        raise CompileError(f"rule {draft.rule_id} selects no surface")
    unknown = [item for item in draft.surfaces if not isinstance(item, Surface)]
    if unknown:
        raise CompileError(f"unsupported surface {unknown[0]!r}")


def compile_plan(
    org_id: str,
    drafts: tuple[RuleDraft, ...],
    *,
    streaming_mode: StreamingMode = StreamingMode.INCREMENTAL,
    previous: ExecutionPlan | None = None,
    compiled_at: float,
    epoch: int = 1,
) -> ExecutionPlan:
    """Drop OFF rules. Reject unsupported streaming and regressing versions."""
    if not org_id.strip():
        raise CompileError("org_id is required")
    if streaming_mode not in SUPPORTED_STREAMING:
        raise CompileError(
            f"streaming mode {streaming_mode.value} is not available yet; "
            "use incremental",
        )
    seen: set[str] = set()
    kept: list[Rule] = []
    detectors: set[str] = set()
    for draft in drafts:
        _check_draft(draft)
        if draft.rule_id in seen:
            raise CompileError(f"duplicate rule_id {draft.rule_id}")
        seen.add(draft.rule_id)
        if draft.mode is Mode.OFF:
            continue
        rule = Rule(
            rule_id=draft.rule_id,
            category=draft.category,
            mode=draft.mode,
            action=draft.action,
            threshold=draft.threshold,
            priority=draft.priority,
            scope=draft.scope,
            on_unavailable=draft.on_unavailable,
            surfaces=draft.surfaces,
        )
        kept.append(rule)
        detectors.add(detector_id(draft.category))
    sequence = 1 if previous is None else previous.sequence + 1
    plan_epoch = epoch if previous is None else previous.epoch
    if previous is not None and epoch < previous.epoch:
        raise CompileError("plan epoch regressed")
    if previous is not None and epoch > previous.epoch:
        plan_epoch = epoch
        sequence = 1
    ordered = tuple(sorted(kept, key=lambda rule: (rule.priority, rule.rule_id)))
    plan = ExecutionPlan(
        org_id=org_id,
        epoch=plan_epoch,
        sequence=sequence,
        content_hash=_hash_rules(ordered),
        compiled_at=compiled_at,
        rules=ordered,
        required_detectors=frozenset(detectors),
        streaming_mode=streaming_mode,
        integrity_locked=True,
    )
    if previous is not None and not _acceptable(plan, previous):
        raise CompileError("plan version regressed")
    return plan


def _acceptable(plan: ExecutionPlan, previous: ExecutionPlan) -> bool:
    if plan.epoch > previous.epoch:
        return True
    return plan.epoch == previous.epoch and plan.sequence > previous.sequence
