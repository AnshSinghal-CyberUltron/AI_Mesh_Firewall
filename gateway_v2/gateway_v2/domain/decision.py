"""Decision vocabulary. Each finding keeps its own disposition (C22)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.posture import DECISION_CODES


class Disposition(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    REWRITE = "rewrite"
    BLOCK = "block"


def _rank(item: Disposition) -> int:
    if item is Disposition.BLOCK:
        return 4
    if item is Disposition.REWRITE:
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
    # Index into ``Decision.findings`` of the finding this disposition is for.
    # ``None`` means the producer did not key it (detector-keyed fallback).
    finding_index: int | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    disposition: Disposition
    per_finding: tuple[FindingDisposition, ...]
    transformations: tuple[Transformation, ...]
    findings: tuple[Finding, ...]
    plan_version: str
    deciding_rules: tuple[str, ...]
    unavailable_detectors: tuple[str, ...]
    # GW33 C1: computed once by the resolver (``resolve/review.py``); egress/edge/audit only copy it.
    review_required: bool = False
    # Declared codes only (``posture.DECISION_CODES``), never content.
    codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        most = most_restrictive(tuple(item.disposition for item in self.per_finding))
        if self.per_finding and most is not self.disposition:
            raise ValueError(
                "request disposition must be the most restrictive per-finding disposition",
            )
        if self.review_required and most is Disposition.ALLOW:
            raise ValueError("review_required needs a non-ALLOW per-finding disposition")
        unknown = [code for code in self.codes if code not in DECISION_CODES]
        if unknown:
            raise ValueError(f"unknown decision code {unknown[0]!r}")


def most_restrictive(items: tuple[Disposition, ...]) -> Disposition:
    if not items:
        return Disposition.ALLOW
    return max(items, key=_rank)


def _union(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *second)))
def combine_decisions(a: Decision, b: Decision) -> Decision:
    """Merge two decisions (D-22): most restrictive disposition, ``review_required`` OR, ordered
    union of rules/codes/unavailable detectors, per-finding concatenation, and the transformations of
    the KEPT (more restrictive; ``a`` on a tie) decision only. Pure; re-validated by ``Decision``.

    The result is an audit aggregate. Bytes are never taken from it: each channel applies its own.
    """
    kept = b if _rank(b.disposition) > _rank(a.disposition) else a
    offset = len(a.findings)
    shifted = tuple(
        FindingDisposition(
            item.detector,
            item.disposition,
            None if item.finding_index is None else item.finding_index + offset,
        )
        for item in b.per_finding
    )
    per_finding = (*a.per_finding, *shifted)
    disposition = (
        most_restrictive(tuple(item.disposition for item in per_finding))
        if per_finding
        else most_restrictive((a.disposition, b.disposition))
    )
    return Decision(
        disposition=disposition,
        per_finding=per_finding,
        transformations=kept.transformations,
        findings=(*a.findings, *b.findings),
        plan_version=kept.plan_version,
        deciding_rules=_union(a.deciding_rules, b.deciding_rules),
        unavailable_detectors=_union(a.unavailable_detectors, b.unavailable_detectors),
        review_required=a.review_required or b.review_required,
        codes=_union(a.codes, b.codes),
    )
@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Audit row. Carries every finding status, including SKIPPED and UNAVAILABLE."""

    request_id: str
    plan_version: str
    decision: Decision

    def statuses(self) -> tuple[FindingStatus, ...]:
        return tuple(finding.status for finding in self.decision.findings)
