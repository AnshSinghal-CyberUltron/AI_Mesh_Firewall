"""Dependency-inversion ports.

The runbook's layer order (edge > admit > plan > detect > resolve > dispatch >
egress > audit) forbids egress/ and dispatch/ from importing detect/ or resolve/,
yet §10.4 has egress run "output detect -> resolve -> emit" and dispatch
"verify transformed bytes". edge/ (the composition root) wires concrete
functions into these ports so the lower layers never import upward.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rvproto.domain.decision import Decision

Hit = tuple[str, int, int]  # (detector, start, end) char offsets


class OutputInspector(Protocol):
    def hits(self, text: str) -> list[Hit]:
        """One matcher pass restricted to the plan's OUTPUT detectors."""
        ...

    def decide(self, hits_per_segment: Sequence[Sequence[Hit]]) -> Decision:
        """Findings from hits -> resolve(findings, plan, OUTPUT)."""
        ...

    def hold_start(self, buf: str) -> int: ...


class PayloadVerifier(Protocol):
    def residual_hits(self, texts: Sequence[str], detectors: frozenset[str]) -> int:
        """Count matches of `detectors` still present in the transformed texts."""
        ...


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    request_id: str
    org_id: str
    key_id: str
    phase: str
    plan_version: str
    disposition: str
    deciding_rules: tuple[str, ...]
    unavailable_detectors: tuple[str, ...]
    findings: tuple[dict[str, object], ...]
    verification: str
    stages: str
    wall_ns: int
