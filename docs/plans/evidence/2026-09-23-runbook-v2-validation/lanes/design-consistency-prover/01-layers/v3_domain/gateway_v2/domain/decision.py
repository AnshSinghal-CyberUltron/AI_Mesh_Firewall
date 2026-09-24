"""Decision / DispatchAuthorization value types (rb.md §10.5.3). Minting lives in resolve/."""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.domain.findings import Finding, Span
from gateway_v2.domain.taxonomy import Disposition


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
    decision: Decision
