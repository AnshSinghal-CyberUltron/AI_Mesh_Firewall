"""Rule / ExecutionPlan value types (rb.md §10.5.2)."""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.contracts.domain.taxonomy import (
    Action,
    Category,
    FailurePosture,
    Mode,
    RuleScope,
    StreamingMode,
)


@dataclass(frozen=True, slots=True)
class PlatformIntegrity:
    kill_switch_fail_closed: bool


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    category: Category
    mode: Mode
    action: Action
    threshold: float | None
    priority: int
    scope: RuleScope
    on_unavailable: FailurePosture


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    org_id: str
    version: str
    compiled_at: float
    rules: tuple[Rule, ...]
    required_detectors: frozenset[str]
    streaming_mode: StreamingMode
    integrity: PlatformIntegrity
