"""Decision (runbook §10.5.3) and the dispatch capability only resolve/ can mint."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from rvproto.domain.findings import Category, Finding, Span
from rvproto.domain.plan import Phase


class Disposition(StrEnum):
    ALLOW = "ALLOW"
    FLAG = "FLAG"
    REDACT = "REDACT"
    BLOCK = "BLOCK"


DISPATCHABLE = frozenset({Disposition.ALLOW, Disposition.FLAG, Disposition.REDACT})


@dataclass(frozen=True, slots=True)
class Transformation:
    span: Span
    category: Category
    detector: str

    @property
    def replacement(self) -> str:
        return f"[REDACTED:{self.detector}]"


@dataclass(frozen=True, slots=True)
class Decision:
    phase: Phase
    disposition: Disposition
    transformations: tuple[Transformation, ...]
    findings: tuple[Finding, ...]
    plan_version: str
    deciding_rules: tuple[str, ...]
    unavailable_detectors: tuple[str, ...]


# Capability object. Gate-checked: only rvproto/resolve/ may import it
# (tests/unit/test_gates.py). DispatchAuthorization refuses any other token.
_RESOLVER_CAPABILITY = object()


@dataclass(frozen=True, slots=True)
class DispatchAuthorization:
    request_id: str
    plan_version: str
    disposition: Disposition
    n_transformations: int
    token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.token is not _RESOLVER_CAPABILITY:
            raise PermissionError("DispatchAuthorization can only be minted by resolve/")
        if self.disposition not in DISPATCHABLE:
            raise PermissionError(f"{self.disposition} is not dispatchable")
