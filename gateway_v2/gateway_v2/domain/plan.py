"""Execution plan vocabulary. Compilation and distribution are GW05."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gateway_v2.domain.category import Category


class Mode(StrEnum):
    OFF = "off"
    MONITOR = "monitor"
    ENFORCE = "enforce"


class Action(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"
    REWRITE = "rewrite"


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


class Phase(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class Surface(StrEnum):
    CHAT = "chat"
    MCP = "mcp"
    RAG = "rag"
    VECTOR = "vector"
    EMBEDDINGS = "embeddings"


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
    surfaces: frozenset[Surface]


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    org_id: str
    epoch: int
    sequence: int
    content_hash: str
    compiled_at: float
    rules: tuple[Rule, ...]
    required_detectors: frozenset[str]
    streaming_mode: StreamingMode
    integrity_locked: bool

    @property
    def version(self) -> str:
        return f"{self.epoch}.{self.sequence}.{self.content_hash[:16]}"


@dataclass(frozen=True, slots=True)
class PlanUnavailable:
    """Known tenant, plan missing, stale, or store unreachable. Never another tenant's plan."""

    org_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class PlanUnknownTenant:
    """Principal resolved and this org has never been registered."""

    org_id: str


def is_newer(candidate: ExecutionPlan, current: ExecutionPlan) -> bool:
    if candidate.org_id != current.org_id:
        return False
    if candidate.epoch != current.epoch:
        return candidate.epoch > current.epoch
    return candidate.sequence > current.sequence
