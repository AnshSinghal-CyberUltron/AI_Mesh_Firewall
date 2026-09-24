"""Composition: concrete detect + resolve functions wired into the lower-layer ports."""

from __future__ import annotations

from collections.abc import Sequence

from rvproto.detect.canon import canonicalize
from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.detect.holdback import hold_start
from rvproto.detect.matcher import Matcher
from rvproto.domain.decision import Decision
from rvproto.domain.findings import Span
from rvproto.domain.plan import ExecutionPlan, Phase
from rvproto.domain.ports import Hit
from rvproto.resolve.resolver import resolve


class PlanInspector:
    """OutputInspector bound to one pinned plan version."""

    __slots__ = ("det", "matcher", "plan", "selected")

    def __init__(self, matcher: Matcher, det: DeterministicDetectors, plan: ExecutionPlan) -> None:
        self.matcher = matcher
        self.det = det
        self.plan = plan
        self.selected = plan.required(Phase.OUTPUT)

    def hits(self, text: str) -> list[Hit]:
        return self.matcher.scan(text, self.selected)

    def decide(self, hits_per_segment: Sequence[Sequence[Hit]]) -> Decision:
        by_det: dict[str, list[Span]] = {}
        for seg, hits in enumerate(hits_per_segment):
            for d, s, e in hits:
                by_det.setdefault(d, []).append(Span(seg, s, e))
        return resolve(self.det.findings_from(by_det, self.selected), self.plan, Phase.OUTPUT)

    def hold_start(self, buf: str) -> int:
        return hold_start(buf)


class NoScanInspector(PlanInspector):
    """EXPERIMENT ONLY (RV_EXP_OUTPUT_SCAN=off; proto-bench-unit holdback attribution): the
    output phase detects nothing and holds nothing, so every upstream piece is released as it
    arrives. Responses report the stage as out:S (skipped). Never a production posture."""

    __slots__ = ()

    def hits(self, text: str) -> list[Hit]:
        return []

    def hold_start(self, buf: str) -> int:
        return len(buf)


class Verifier:
    """PayloadVerifier: canonicalize + the same matcher, on the transformed texts."""

    def __init__(self, matcher: Matcher, decode_budget: int) -> None:
        self.matcher = matcher
        self.budget = decode_budget

    def residual_hits(self, texts: Sequence[str], detectors: frozenset[str]) -> int:
        n = 0
        for t in texts:
            if t:
                n += len(self.matcher.scan(canonicalize(t, self.budget).text, detectors))
        return n
