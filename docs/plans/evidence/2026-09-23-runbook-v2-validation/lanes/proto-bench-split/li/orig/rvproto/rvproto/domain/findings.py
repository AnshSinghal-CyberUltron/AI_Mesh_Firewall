"""Finding vocabulary. Bottom of the import graph: every layer may import it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Category(StrEnum):
    PII = "pii"
    SECRET = "secret"
    INJECTION = "injection"


class FindingStatus(StrEnum):
    EXECUTED = "executed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


# The single detector catalogue: detector id -> taxonomy category.
DETECTORS: MappingProxyType[str, Category] = MappingProxyType(
    {
        "pii.email": Category.PII,
        "pii.phone": Category.PII,
        "pii.ssn": Category.PII,
        "pii.card": Category.PII,
        "pii.ipv4": Category.PII,
        "secret.aws": Category.SECRET,
        "secret.github": Category.SECRET,
        "secret.slack": Category.SECRET,
        "secret.google": Category.SECRET,
        "secret.stripe": Category.SECRET,
        "secret.jwt": Category.SECRET,
        "secret.pem": Category.SECRET,
        "secret.generic": Category.SECRET,
        "injection.heuristic": Category.INJECTION,
        "injection.pg2": Category.INJECTION,
    }
)
SEMANTIC_DETECTOR = "injection.pg2"


@dataclass(frozen=True, slots=True)
class Span:
    """Char offsets into the ORIGINAL text of one request segment."""

    segment: int
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
        if DETECTORS.get(self.detector) is not self.category:
            raise ValueError(f"detector {self.detector!r} not in catalogue as {self.category}")
        if (self.confidence is None) != (self.status is not FindingStatus.EXECUTED):
            raise ValueError("confidence must be set iff status is EXECUTED")

    @property
    def hit(self) -> bool:
        return self.status is FindingStatus.EXECUTED and bool(self.spans)


def skipped(detector: str, version: str) -> Finding:
    return Finding(detector, version, DETECTORS[detector], FindingStatus.SKIPPED, None, (), None)


def unavailable(detector: str, version: str, why: str) -> Finding:
    return Finding(detector, version, DETECTORS[detector], FindingStatus.UNAVAILABLE, None, (), why)
