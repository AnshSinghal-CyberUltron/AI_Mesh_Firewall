"""Detector protocol, Finding, FindingStatus — rb.md §10.5.1 (L2189); tree L2159."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

# L2189 'category: str  # taxonomy term, ONE spelling, from plan/model.py'
# L2193 'category is drawn from a single enum in plan/model.py'
# L2495 LGW04-3 'Construct a Finding with a category not in the enum -> Rejected at construction'
# L2640 GW08 'Detector protocol: takes text and plan context'
from gateway_v2.plan.model import Category, ExecutionPlan


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
        if not isinstance(self.category, Category):
            raise TypeError(f"category {self.category!r} is not a plan.model.Category")
        if (self.confidence is None) == (self.status is FindingStatus.EXECUTED):
            raise ValueError("confidence must be set iff status is EXECUTED (L2189)")


class Detector(Protocol):
    detector_id: str

    def detect(self, text: str, plan: ExecutionPlan) -> tuple[Finding, ...]: ...
