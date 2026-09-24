"""DecisionRecord (one per phase) — rb.md L2159, L2874."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway_v2.detect.base import Finding
    from gateway_v2.resolve.decision import Decision, Phase

# L2874 'Emit exactly one DecisionRecord per phase, carrying plan version, deciding rules,
#        all findings including SKIPPED and UNAVAILABLE, ... transformation verification result'


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    request_id: str
    phase: Phase
    decision: Decision
    findings: tuple[Finding, ...]
    verified: bool
