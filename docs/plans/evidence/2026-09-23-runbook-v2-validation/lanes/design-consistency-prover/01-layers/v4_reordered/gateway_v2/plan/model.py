"""ExecutionPlan, Rule, Mode, Action, FailurePosture — rb.md §10.5.2 (L2196); tree L2159."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    """L2193: 'category is drawn from a single enum in plan/model.py'."""

    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    PII = "pii"
    SECRET = "secret"


class Mode(StrEnum):
    OFF = "off"
    MONITOR = "monitor"
    ENFORCE = "enforce"


class Action(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"


class FailurePosture(StrEnum):
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


class RuleScope(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    BOTH = "both"


class StreamingMode(StrEnum):
    INCREMENTAL = "incremental"
    STRICT_WITHHOLD = "strict_withhold"


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
