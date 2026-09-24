"""Finding value type (rb.md §10.5.1). Construction validates the category enum (LGW04-3)."""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.domain.taxonomy import Category, FindingStatus


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
        if not isinstance(self.category, Category):
            raise TypeError(f"category {self.category!r} is not a Category")
        if (self.confidence is None) == (self.status is FindingStatus.EXECUTED):
            raise ValueError("confidence must be set iff status is EXECUTED")
