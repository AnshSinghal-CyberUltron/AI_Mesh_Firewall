"""Applies Decision.transformations, verifies bytes — rb.md L2159, L2755."""

from __future__ import annotations

# L2755 'Apply Decision.transformations and verify the result against the finding spans'
from gateway_v2.detect.base import Span
from gateway_v2.resolve.decision import Decision, Transformation


def _apply_one(text: str, tx: Transformation) -> str:
    return text[: tx.span.start] + tx.replacement + text[tx.span.end :]


def apply_and_verify(decision: Decision, text: str, raw_spans: tuple[Span, ...]) -> str:
    out = text
    for tx in sorted(decision.transformations, key=lambda t: -t.span.start):
        out = _apply_one(out, tx)
    for span in raw_spans:
        if text[span.start : span.end] and text[span.start : span.end] in out:
            raise ValueError("verification failed: raw span survived transformation")
    return out
