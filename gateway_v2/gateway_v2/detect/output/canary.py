"""Canary-token detector (GW33 C7 detector half, D-16, D-23, D-26).

`CanaryDetector` holds the per-request token ONLY in its instance. It canonicalises the output
(without the base64 decoder, so the token's own alphabet cannot be re-read as base64),
casefolds both sides and finds every occurrence, so zero-width interleaving, case changes and
percent/entity encoding are found. `detect(text)` scans the whole text;
`detect_from(text, start)` scans ``text[start:]`` and returns ABSOLUTE original offsets. Both
share one core and neither keeps per-request mutable state, which is what makes the per-delta
overlap scan stateless (D-23).
The token never appears in `repr`, `str`, an exception message or a finding: `evidence` is `None`
and `detector_version` is a digest of the detector's code constants, not of the token.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Final

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.canonicalize import (
    DEFAULT_BUDGET,
    BudgetExceeded,
    canonicalize,
)
from gateway_v2.detect.output.common import executed, original_span, unavailable
from gateway_v2.domain import Category, Finding

__all__ = ("CanaryDetector",)

_VERSION: Final = hashlib.sha256(
    b"output.canary/1\x1fcasefold\x1fcanonicalize-no-base64",
).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class CanaryDetector:
    """Frozen detector; the token is a private field excluded from `repr`/`str`/equality."""

    _token: str = field(repr=False, compare=False)
    rule_id: str = "output.canary"
    required_capabilities: frozenset[str] = field(default_factory=frozenset)

    def __repr__(self) -> str:
        return "CanaryDetector(<redacted>)"

    __str__ = __repr__

    @property
    def detector_version(self) -> str:
        return _VERSION

    def detect(self, text: str, context: DetectorContext | None = None) -> tuple[Finding, ...]:
        return self.detect_from(text, 0)

    def detect_from(self, text: str, start: int) -> tuple[Finding, ...]:
        """Findings in ``text[start:]`` with offsets relative to the whole ``text``."""
        try:
            return self._scan(text, max(0, start))
        except Exception:  # noqa: BLE001 - D-18: any failure is data, never a clean result
            return (unavailable(self.rule_id, _VERSION, Category.CANARY),)

    def _scan(self, text: str, start: int) -> tuple[Finding, ...]:
        token = self._token.casefold()
        if not token:
            return (unavailable(self.rule_id, _VERSION, Category.CANARY),)
        try:
            canonical, span_map = canonicalize(text[start:], DEFAULT_BUDGET, decode_base64=False)
        except BudgetExceeded:
            return (unavailable(self.rule_id, _VERSION, Category.CANARY),)
        folded = canonical.casefold()
        if len(folded) != len(canonical):  # a casefold that changes length would skew offsets
            folded = canonical.lower()
        findings: list[Finding] = []
        at = folded.find(token)
        while at >= 0:
            span = original_span(span_map, at, at + len(token), start)
            findings.append(executed(self.rule_id, _VERSION, Category.CANARY, (span,)))
            at = folded.find(token, at + len(token))
        return tuple(findings)
