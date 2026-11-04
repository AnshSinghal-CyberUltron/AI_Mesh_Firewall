"""Shared helpers for the GW33 output detectors. Pure; imports `domain` and the GW09 decoder."""
from __future__ import annotations

from gateway_v2.detect.deterministic.canonicalize import SpanMap
from gateway_v2.domain import Category, Finding, FindingStatus, Span

__all__ = ("executed", "original_span", "unavailable")

_EXECUTED_CONFIDENCE = 1.0  # a deterministic exact confirm; a detection constant, not capacity


def original_span(span_map: SpanMap, start: int, end: int, offset: int = 0) -> Span:
    """Canonical ``[start, end)`` to the minimal covering ORIGINAL span (plus ``offset``).

    ``SpanMap.records[i]`` describes canonical character ``i``, so a slice replaces the shipped
    ``to_original`` linear scan and keeps many candidates linear in their own length.
    """
    records = span_map.records
    if not records:
        return Span(offset, offset)
    lo = max(start, 0)
    hi = max(end, lo)
    covering = records[lo:hi]
    if not covering:
        anchor = records[min(lo, len(records) - 1)][1]
        return Span(anchor + offset, anchor + offset)
    return Span(min(r[1] for r in covering) + offset, max(r[2] for r in covering) + offset)


def executed(
    rule_id: str,
    version: str,
    category: Category,
    spans: tuple[Span, ...],
    confidence: float = _EXECUTED_CONFIDENCE,
) -> Finding:
    """One EXECUTED finding; ``evidence`` is always ``None`` (spans locate, bytes never travel)."""
    return Finding(rule_id, version, category, FindingStatus.EXECUTED, confidence, spans, None)


def unavailable(rule_id: str, version: str, category: Category) -> Finding:
    """The single cannot-execute finding (D-18): never an empty tuple presented as clean."""
    return Finding(rule_id, version, category, FindingStatus.UNAVAILABLE, None, (), None)
