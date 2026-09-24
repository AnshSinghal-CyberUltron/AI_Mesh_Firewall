"""Decision, Disposition, Transformation — rb.md L2216; DispatchAuthorization L2184/L2603."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway_v2.detect.base import Finding, Span

# L2216 'findings: tuple[Finding, ...]  # ALL of them, including SKIPPED'


class Disposition(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"


class Phase(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class Transformation:
    span: Span
    replacement: str


@dataclass(frozen=True, slots=True)
class Decision:
    disposition: Disposition
    transformations: tuple[Transformation, ...]
    findings: tuple[Finding, ...]
    plan_version: str
    deciding_rules: tuple[str, ...]
    unavailable_detectors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DispatchAuthorization:
    """L2603: minted only for non-blocking dispositions (by resolve/)."""

    decision: Decision


def mint_dispatch_authorization(decision: Decision) -> DispatchAuthorization | None:
    if decision.disposition is Disposition.BLOCK:
        return None
    return DispatchAuthorization(decision)
