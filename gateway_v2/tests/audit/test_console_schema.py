"""GW14b schema: version-1 records stay readable, and the drain defaults are not invented."""

from __future__ import annotations

import pytest

from gateway_v2.audit.console_schema import (
    SchemaError,
    enforcement_fields,
    read_required,
    validate_audit_record,
    with_console,
)
from gateway_v2.audit.record import AuditPhase, admission, from_decision
from gateway_v2.domain.category import Category
from gateway_v2.domain.decision import Decision, DecisionRecord, Disposition, FindingDisposition
from gateway_v2.domain.finding import Finding, FindingStatus, Span

_SECRET = "123-45-6789"


def _record() -> DecisionRecord:
    finding = Finding(
        detector="pii",
        detector_version="2026.10",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=0.9,
        spans=(Span(0, 11),),
        evidence=_SECRET,
    )
    decision = Decision(
        disposition=Disposition.REDACT,
        per_finding=(FindingDisposition("pii", Disposition.REDACT),),
        transformations=(),
        findings=(finding,),
        plan_version="1.2.abc",
        deciding_rules=("pii",),
        unavailable_detectors=(),
    )
    return DecisionRecord(request_id="zs-1", plan_version="1.2.abc", decision=decision)


def _console() -> dict[str, object]:
    return {
        "source": "gateway_v2",
        "zeroshield": {"action": "redact", "request_id": "zs-1"},
        "pipeline_trace": {
            "final_action": "redact",
            "plan_version": "1.2.abc",
            "stages": [{"name": "input_scan", "action": "redact", "ran": True}],
        },
    }


def test_from_decision_and_admission_validate() -> None:
    decision = from_decision(
        _record(), org_id="dev-org", phase=AuditPhase.INPUT, recorded_at_ns=1,
    )
    admitted = admission(
        request_id="zs-1",
        org_id="dev-org",
        outcome="allow",
        reason="admitted",
        recorded_at_ns=2,
        status=200,
    )
    validate_audit_record(decision.as_mapping())
    validate_audit_record(admitted.as_mapping())
    stored = with_console(decision, _console())
    assert stored["v"] == 1
    assert "evidence" not in str(stored)


def test_unknown_top_level_field_fails() -> None:
    mapping = dict(from_decision(
        _record(), org_id="dev-org", phase=AuditPhase.OUTPUT, recorded_at_ns=1,
    ).as_mapping())
    mapping["extra"] = True
    with pytest.raises(SchemaError, match="extra"):
        validate_audit_record(mapping)


def test_missing_request_id_fails() -> None:
    mapping = dict(admission(
        request_id="zs-1",
        org_id="dev-org",
        outcome="allow",
        reason="admitted",
        recorded_at_ns=1,
        status=200,
    ).as_mapping())
    del mapping["request_id"]
    with pytest.raises(SchemaError, match="request_id"):
        read_required(mapping, "request_id")


def test_evidence_in_findings_fails() -> None:
    mapping = dict(from_decision(
        _record(), org_id="dev-org", phase=AuditPhase.INPUT, recorded_at_ns=1,
    ).as_mapping())
    detail = mapping["detail"]
    assert isinstance(detail, dict)
    detail["findings"] = [{"evidence": _SECRET}]
    with pytest.raises(SchemaError, match="evidence"):
        validate_audit_record(mapping)


def test_enforcement_fields_omit_drain_defaults() -> None:
    mapping = with_console(
        from_decision(
            _record(), org_id="dev-org", phase=AuditPhase.INPUT, recorded_at_ns=1,
        ),
        _console(),
    )
    fields = enforcement_fields(mapping)
    assert fields["action"] == "redact"
    metadata = fields["metadata"]
    assert isinstance(metadata, dict)
    assert "event_type" not in fields
    assert "event_type" not in metadata
    assert "module_id" not in metadata
    assert "risk_score" not in metadata
    assert metadata["source"] == "gateway_v2"
