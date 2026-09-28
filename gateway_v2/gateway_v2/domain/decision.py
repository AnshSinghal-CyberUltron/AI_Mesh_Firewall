"""Decision vocabulary. Each finding keeps its own disposition (C22)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gateway_v2.domain.finding import Finding, FindingStatus, Span


class Disposition(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"


def _rank(item: Disposition) -> int:
    if item is Disposition.BLOCK:
        return 3
    if item is Disposition.REDACT:
        return 2
    if item is Disposition.FLAG:
        return 1
    return 0


@dataclass(frozen=True, slots=True)
class Transformation:
    kind: str
    span: Span
    replacement: str


@dataclass(frozen=True, slots=True)
class FindingDisposition:
    detector: str
    disposition: Disposition


@dataclass(frozen=True, slots=True)
class Decision:
    disposition: Disposition
    per_finding: tuple[FindingDisposition, ...]
    transformations: tuple[Transformation, ...]
    findings: tuple[Finding, ...]
    plan_version: str
    deciding_rules: tuple[str, ...]
    unavailable_detectors: tuple[str, ...]

    def __post_init__(self) -> None:
        most = most_restrictive(tuple(item.disposition for item in self.per_finding))
        if self.per_finding and most is not self.disposition:
            raise ValueError(
                "request disposition must be the most restrictive per-finding disposition",
            )


def most_restrictive(items: tuple[Disposition, ...]) -> Disposition:
    if not items:
        return Disposition.ALLOW
    return max(items, key=_rank)


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Audit row. Carries every finding status, including SKIPPED and UNAVAILABLE."""

    request_id: str
    plan_version: str
    decision: Decision

    def statuses(self) -> tuple[FindingStatus, ...]:
        return tuple(finding.status for finding in self.decision.findings)
