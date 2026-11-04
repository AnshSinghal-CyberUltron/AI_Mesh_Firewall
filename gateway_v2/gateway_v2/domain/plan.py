"""Execution plan vocabulary. Compilation and distribution are GW05."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gateway_v2.domain.category import Category
from gateway_v2.domain.rag import RagSettings


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


class RuleKind(StrEnum):
    KEYWORDS = "keywords"
    REGEX = "regex"
    PATTERN = "pattern"
    DETECTOR = "detector"


@dataclass(frozen=True, slots=True)
class RuleCondition:
    """Customer match, compiled once with the plan. ``None`` on a rule keeps the category join."""

    kind: RuleKind
    field: str
    direction: RuleScope
    keywords: tuple[str, ...]
    pattern: str
    detector_class: str
    pipeline_stage: str


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
    condition: RuleCondition | None = None


@dataclass(frozen=True, slots=True)
class RateControl:
    """Org request-rate control (GW27). ``enabled`` gates request rate and burst only."""

    enabled: bool
    requests_per_minute: int
    burst: int


@dataclass(frozen=True, slots=True)
class AuditControl:
    """Org audit control (GW27). Hard blocks, SHED and REJECTED are recorded regardless."""

    enabled: bool
    incident_logging: bool
    retention_days: int


@dataclass(frozen=True, slots=True)
class OrgControls:
    """Operational org controls carried beside the rules (GW27).

    Absent means "no org-specific control": no org rate limit, the contract-default
    response ceiling, audit on. ``0`` / ``None`` / ``()`` are the unset values.
    """

    rate: RateControl | None = None
    org_tpm_limit: int = 0
    max_response_tokens: int = 0
    audit: AuditControl | None = None
    compliance_frameworks: tuple[str, ...] = ()


UNSET_CONTROLS = OrgControls()


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
    feed_seq: int = 0
    """Cursor position of the record this plan was compiled from. 0 = not store-sourced.

    Provenance only. Ordering stays (epoch, sequence) — see is_newer.
    """
    rag: RagSettings = RagSettings()
    """GW31: the six RAG settings. Defaults when the plan document carries none."""
    controls: OrgControls = UNSET_CONTROLS

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
    """Order a plan against itself by (epoch, sequence).

    Deliberately NOT by feed_seq. feed_seq is the per-kind cursor space shared by every tenant,
    so a write to another tenant advances it; using it here would declare an untouched plan
    newer than itself. An epoch bump raises epoch, so (epoch, sequence) is already monotone
    for one org.
    """
    if candidate.org_id != current.org_id:
        return False
    if candidate.epoch != current.epoch:
        return candidate.epoch > current.epoch
    return candidate.sequence > current.sequence
