"""GW14b step 3: one record per phase, and the control plane does not invent v1 defaults."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from gateway_v2.audit.record import AuditPhase, AuditRecord
from gateway_v2.domain.category import Category
from gateway_v2.domain.decision import Decision, Disposition, FindingDisposition
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, OVERLOAD_SHED
from gateway_v2.edge.audit_emit import emit_chat_audit, note_stream_output
from gateway_v2.edge.request_holds import RequestHolds

_SECRET = "123-45-6789"
_REPO = Path(__file__).resolve().parents[3]
_ADAPTER = _REPO / "control" / "ai_mesh_control" / "core" / "v2_audit_adapter.py"
_DRAIN = _REPO / "control" / "ai_mesh_control" / "core" / "tasks.py"


def _keep(seen: list[AuditRecord]) -> Callable[[AuditRecord], bool]:
    def _emit(record: AuditRecord) -> bool:
        seen.append(record)
        return True

    return _emit


def _decision(action: Disposition) -> Decision:
    finding = Finding(
        detector="pii",
        detector_version="2026.10",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=0.9,
        spans=(Span(0, 11),),
        evidence=_SECRET,
    )
    return Decision(
        disposition=action,
        per_finding=(FindingDisposition("pii", action),),
        transformations=(),
        findings=(finding,),
        plan_version="1.2.abc",
        deciding_rules=("pii",),
        unavailable_detectors=(),
    )


def _holds() -> RequestHolds:
    holds = RequestHolds()
    holds.note_tenant("dev-org")
    holds.note_request("zs-dev-org-1")
    holds.note_input(_decision(Disposition.REDACT))
    return holds


def test_a_stream_output_decision_is_audited() -> None:
    holds = _holds()
    output = _decision(Disposition.ALLOW)
    stats = type("Stats", (), {"output_decision": output, "output_action": "allow"})()
    note_stream_output(holds, stats, "ignored-when-a-decision-is-present")
    seen: list[AuditRecord] = []
    emit_chat_audit(_keep(seen), holds, None)
    assert [record.phase for record in seen] == [AuditPhase.INPUT, AuditPhase.OUTPUT]
    assert seen[1].outcome == "allow"


def test_a_passthrough_stream_records_allow() -> None:
    holds = _holds()
    stats = type("Stats", (), {"output_action": "allow"})()
    note_stream_output(holds, stats, "1.2.passthrough")
    decision = holds.output_decision
    assert decision is not None
    assert decision.disposition is Disposition.ALLOW
    assert decision.plan_version == "1.2.passthrough"
    seen: list[AuditRecord] = []
    emit_chat_audit(_keep(seen), holds, None)
    assert seen[1].phase == AuditPhase.OUTPUT
    assert seen[1].outcome == "allow"


def test_input_record_omits_the_secret_and_names_the_chat_route() -> None:
    seen: list[AuditRecord] = []
    emit_chat_audit(_keep(seen), _holds(), None)
    assert len(seen) == 1
    raw = seen[0].serialize()
    assert _SECRET.encode() not in raw
    body = json.loads(raw)
    assert body["phase"] == AuditPhase.INPUT
    assert body["outcome"] == "redact"
    assert body["detail"]["route"] == "chat"
    assert "event_type" not in body
    assert "risk_score" not in body
    assert "module_id" not in body


def test_output_phase_is_a_second_record() -> None:
    holds = _holds()
    holds.note_output(_decision(Disposition.BLOCK))
    seen: list[AuditRecord] = []
    emit_chat_audit(_keep(seen), holds, "output_blocked")
    phases = [record.phase for record in seen]
    assert phases == [AuditPhase.INPUT, AuditPhase.OUTPUT]


def test_budget_refusal_is_an_admission_record() -> None:
    holds = RequestHolds()
    holds.note_tenant("dev-org")
    holds.note_request("zs-dev-org-2")
    seen: list[AuditRecord] = []
    emit_chat_audit(_keep(seen), holds, BUDGET_UNAVAILABLE)
    assert len(seen) == 1
    body = json.loads(seen[0].serialize())
    assert body["phase"] == AuditPhase.ADMISSION
    assert body["detail"]["reason"] == BUDGET_UNAVAILABLE
    assert body["outcome"] == "rejected"


def test_overload_shed_outcome_is_shed() -> None:
    holds = RequestHolds()
    holds.note_tenant("dev-org")
    holds.note_request("zs-dev-org-3")
    seen: list[AuditRecord] = []
    emit_chat_audit(_keep(seen), holds, OVERLOAD_SHED)
    body = json.loads(seen[0].serialize())
    assert body["outcome"] == "shed"
    assert body["detail"]["status"] == 503


def test_a_full_queue_and_a_broken_callback_do_not_raise() -> None:
    holds = _holds()
    emit_chat_audit(lambda _record: False, holds, None)

    def _boom(_record: AuditRecord) -> bool:
        raise RuntimeError("sink down")

    emit_chat_audit(_boom, holds, None)


def test_pre_identity_refusal_emits_nothing() -> None:
    seen: list[AuditRecord] = []
    emit_chat_audit(_keep(seen), RequestHolds(), "malformed")
    assert seen == []


def test_adapter_does_not_invent_v1_defaults() -> None:
    translate = _load_adapter()
    holds = _holds()
    seen: list[AuditRecord] = []
    emit_chat_audit(_keep(seen), holds, None)
    payload = json.loads(seen[0].serialize())
    translated = translate(payload)
    assert getattr(translated, "module_id") == "1.1"
    assert getattr(translated, "action") == "redact"
    metadata = getattr(translated, "metadata")
    assert "event_type" not in metadata
    assert "risk_score" not in metadata
    payload["detail"].pop("route")
    without_route = translate(payload)
    assert getattr(without_route, "module_id") is None
    payload.pop("phase")
    rejected = translate(payload)
    assert getattr(rejected, "reason") == "phase"
    payload["phase"] = "input"
    payload["detail"]["evidence"] = _SECRET
    assert getattr(translate(payload), "reason") == "evidence"
    assert translate({"request_id": "plain"}) is None


def test_v1_drain_defaults_are_unchanged() -> None:
    text = _DRAIN.read_text(encoding="utf-8")
    assert 'event.get("event_type", "request")' in text
    assert 'event.get("risk_score", 0)' in text
    assert 'or "1.1"' in text
    assert "from_decision" not in text


def _load_adapter() -> Callable[[object], object]:
    spec = importlib.util.spec_from_file_location("v2_audit_adapter", _ADAPTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(Callable[[object], object], module.translate)
