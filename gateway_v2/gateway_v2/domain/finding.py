"""Finding — what a detector says. Frozen. Absence is a status, not None-means-clean."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gateway_v2.domain.category import Category, parse_category


class FindingStatus(StrEnum):
    EXECUTED = "executed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Finding:
    detector: str
    detector_version: str
    category: Category
    status: FindingStatus
    confidence: float | None
    spans: tuple[Span, ...]
    evidence: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", parse_category(self.category))
        if self.status is FindingStatus.EXECUTED:
            if self.confidence is None:
                raise ValueError("EXECUTED finding requires confidence")
        elif self.confidence is not None:
            raise ValueError("confidence is set only when status is EXECUTED")
        if self.evidence is not None and len(self.evidence) > 256:
            raise ValueError("evidence exceeds 256 characters")
