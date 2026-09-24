"""Deterministic detectors: one matcher pass per segment -> one Finding per detector."""

from __future__ import annotations

from collections.abc import Sequence

from rvproto.detect.canon import Canonical
from rvproto.detect.matcher import Matcher
from rvproto.detect.patterns import RULESET_VERSION
from rvproto.domain.findings import (
    DETECTORS,
    SEMANTIC_DETECTOR,
    Finding,
    FindingStatus,
    Span,
    skipped,
)

DETERMINISTIC_IDS: tuple[str, ...] = tuple(d for d in DETECTORS if d != SEMANTIC_DETECTOR)


class DeterministicDetectors:
    def __init__(self, matcher: Matcher) -> None:
        self.matcher = matcher
        self.version = f"rules-{RULESET_VERSION}-{matcher.engine}"

    def scan(self, canon: Sequence[Canonical], selected: frozenset[str]) -> tuple[Finding, ...]:
        by_det: dict[str, list[Span]] = {}
        for seg, c in enumerate(canon):
            if not c.text:
                continue
            for det, s, e in self.matcher.scan(c.text, selected):
                a, b = c.to_orig(s, e)
                by_det.setdefault(det, []).append(Span(seg, a, b))
        return self.findings_from(by_det, selected)

    def scan_texts(self, texts: Sequence[str], selected: frozenset[str]) -> tuple[Finding, ...]:
        """Output side: raw texts (no canonicalization), spans index into each text."""
        by_det: dict[str, list[Span]] = {}
        for seg, text in enumerate(texts):
            if not text:
                continue
            for det, s, e in self.matcher.scan(text, selected):
                by_det.setdefault(det, []).append(Span(seg, s, e))
        return self.findings_from(by_det, selected)

    def residual_hits(self, texts: Sequence[str], detectors: frozenset[str]) -> int:
        return sum(len(self.matcher.scan(t, detectors)) for t in texts if t)

    def findings_from(self, by_det: dict[str, list[Span]], selected: frozenset[str]) -> tuple[Finding, ...]:
        out: list[Finding] = []
        for det in DETERMINISTIC_IDS:
            if det not in selected:
                out.append(skipped(det, self.version))
                continue
            spans = tuple(by_det.get(det, ()))
            out.append(
                Finding(
                    det,
                    self.version,
                    DETECTORS[det],
                    FindingStatus.EXECUTED,
                    1.0 if spans else 0.0,
                    spans,
                    f"matches={len(spans)}" if spans else None,
                )
            )
        return tuple(out)
