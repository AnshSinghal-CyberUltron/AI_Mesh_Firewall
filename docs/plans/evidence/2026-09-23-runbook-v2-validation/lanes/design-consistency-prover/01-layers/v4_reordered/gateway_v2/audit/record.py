"""DecisionRecord (one per phase) — rb.md L2159, L2874."""

from __future__ import annotations

from dataclasses import dataclass

# L2874 'Emit exactly one DecisionRecord per phase, carrying plan version, deciding rules,
#        all findings including SKIPPED and UNAVAILABLE, ... transformation verification result'
from gateway_v2.detect.base import Finding
from gateway_v2.resolve.decision import Decision, Phase


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    request_id: str
    phase: Phase
    decision: Decision
    findings: tuple[Finding, ...]
    verified: bool
