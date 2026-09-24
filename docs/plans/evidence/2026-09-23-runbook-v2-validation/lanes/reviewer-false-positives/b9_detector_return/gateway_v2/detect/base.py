"""Detector protocol, Finding, FindingStatus — re-exported from domain/ (downward)."""

from __future__ import annotations

from gateway_v2.contracts.domain.findings import Finding, Span
from gateway_v2.contracts.domain.plan import ExecutionPlan
from gateway_v2.contracts.domain.ports import Detector
from gateway_v2.contracts.domain.taxonomy import FindingStatus

__all__ = ("Detector", "Finding", "FindingStatus", "Span", "run_detectors")


def run_detectors(
    text: str, plan: ExecutionPlan, detectors: tuple[Detector, ...]
) -> tuple[Finding, ...]:
    return tuple(f for d in detectors for f in d.detect(text, plan))
