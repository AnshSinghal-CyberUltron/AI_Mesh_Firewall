"""ExecutionPlan types (runbook §10.5.2). Compiled off the hot path; immutable."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from rvproto.domain.findings import Category


class Mode(StrEnum):
    OFF = "off"
    MONITOR = "monitor"
    ENFORCE = "enforce"


class Action(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"


class Phase(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class Scope(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    BOTH = "both"


class Posture(StrEnum):
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"
    DEGRADE_TO = "degrade_to"


class StreamingMode(StrEnum):
    INCREMENTAL = "incremental"
    STRICT_WITHHOLD = "strict_withhold"


@dataclass(frozen=True, slots=True)
class FailurePosture:
    kind: Posture
    degrade_to: Action | None = None


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    category: Category
    detectors: frozenset[str]
    mode: Mode
    action: Action
    threshold: float | None
    priority: int  # lower number = higher precedence; tie-break: rule_id ascending
    scope: Scope
    on_unavailable: FailurePosture

    def applies(self, phase: Phase) -> bool:
        return self.scope is Scope.BOTH or self.scope.value == phase.value


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    org_id: str
    version: str
    compiled_at: float
    rules: tuple[Rule, ...]
    required_input: frozenset[str]
    required_output: frozenset[str]
    streaming_mode: StreamingMode
    integrity: str
    # (phase, detector) -> selecting rule; built by the compiler, read-only.
    selection: MappingProxyType[tuple[Phase, str], Rule]

    def required(self, phase: Phase) -> frozenset[str]:
        return self.required_input if phase is Phase.INPUT else self.required_output


@dataclass(frozen=True, slots=True)
class PlanUnavailable:
    org_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class PlanUnknownTenant:
    org_id: str


PlanLookup = ExecutionPlan | PlanUnavailable | PlanUnknownTenant
