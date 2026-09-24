"""DecisionRecord (one per phase) — carries the Decision (type from domain/)."""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.domain.decision import Decision
from gateway_v2.domain.findings import Finding
from gateway_v2.domain.taxonomy import Phase


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    request_id: str
    phase: Phase
    decision: Decision
    findings: tuple[Finding, ...]
