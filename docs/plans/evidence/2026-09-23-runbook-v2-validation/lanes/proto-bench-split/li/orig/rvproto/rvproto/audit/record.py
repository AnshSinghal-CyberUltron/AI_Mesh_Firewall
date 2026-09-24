"""Exactly one DecisionRecord per phase, built from the Decision the resolver returned."""

from __future__ import annotations

from rvproto.domain.decision import Decision
from rvproto.domain.findings import Finding
from rvproto.domain.ports import DecisionRecord


def _finding(f: Finding) -> dict[str, object]:
    return {
        "detector": f.detector,
        "version": f.detector_version,
        "category": f.category.value,
        "status": f.status.value,
        "confidence": f.confidence,
        "spans": [[s.segment, s.start, s.end] for s in f.spans],
        "evidence": f.evidence,
    }


def build(
    decision: Decision,
    *,
    request_id: str,
    org_id: str,
    key_id: str,
    verification: str,
    stages: str,
    wall_ns: int,
) -> DecisionRecord:
    return DecisionRecord(
        request_id=request_id,
        org_id=org_id,
        key_id=key_id,
        phase=decision.phase.value,
        plan_version=decision.plan_version,
        disposition=decision.disposition.value,
        deciding_rules=decision.deciding_rules,
        unavailable_detectors=decision.unavailable_detectors,
        findings=tuple(_finding(f) for f in decision.findings),
        verification=verification,
        stages=stages,
        wall_ns=wall_ns,
    )
