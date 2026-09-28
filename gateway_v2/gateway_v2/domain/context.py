"""Frozen request state. A stage returns a new context; it never edits the old one."""

from __future__ import annotations

from dataclasses import dataclass, replace

from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus
from gateway_v2.domain.plan import Phase


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    org_id: str
    phase: Phase
    input_text: str
    findings: tuple[Finding, ...]
    plan_version: str


@dataclass(frozen=True, slots=True)
class StageStep:
    """Declared inputs of one stage. The next context is a function of these alone."""

    detector: str
    detector_version: str
    category: Category
    status: FindingStatus
    confidence: float | None = None


def apply_stage(context: RequestContext, step: StageStep) -> RequestContext:
    finding = Finding(
        detector=step.detector,
        detector_version=step.detector_version,
        category=step.category,
        status=step.status,
        confidence=step.confidence,
        spans=(),
        evidence=None,
    )
    return replace(context, findings=context.findings + (finding,))
