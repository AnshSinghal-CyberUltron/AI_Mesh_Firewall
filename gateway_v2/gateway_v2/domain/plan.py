"""Execution plan vocabulary. Compilation and distribution are GW05."""

from __future__ import annotations

import re
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


class RuleKind(StrEnum):
    KEYWORDS = "keywords"
    REGEX = "regex"
    PATTERN = "pattern"
    DETECTOR = "detector"


class GroundingMode(StrEnum):
    """Closed set of grounding modes (GW33 C8). The compiler refuses any other value."""
    LEXICAL = "lexical"
    MODEL = "model"
LEGACY_IP_RULE_PREFIX = "output_ip_leakage"
"""D-2: the GW26 publisher's pre-GW33 rule id prefix for IP leakage (published under ``exfil``)."""
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
    # D-29: allowed host suffixes for ``output.exfil``; encoded and hashed only when non-empty.
    allow_hosts: tuple[str, ...] = ()


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
    # D-4: the declared REWRITE template. Hashed/encoded only when non-empty.
    rewrite_template: str = ""


_BUILTIN_OUTPUT = re.compile(r"^output\.(exfil|ip_leak|grounding|canary)(\.[a-z0-9_:-]+)?$")


def builtin_output_detector(rule: Rule) -> str | None:
    """D-28: the built-in output detector family a rule selects, else ``None``.

    Pure function of the signed rule, keyed on detector identity (``RuleKind.DETECTOR`` plus the
    reserved ``output.`` namespace), never on the category label: the publisher's ``exfil`` fallback
    category is a legacy catch-all that customer rows can land in.
    """
    condition = rule.condition
    if condition is None or condition.kind is not RuleKind.DETECTOR:
        return None
    match = _BUILTIN_OUTPUT.match(condition.detector_class)
    return None if match is None else match.group(1)


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
