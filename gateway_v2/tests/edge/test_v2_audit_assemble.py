"""One Scan Detail row per chat, using the console action, without v1 defaults."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from gateway_v2.audit.record import AuditRecord
from gateway_v2.domain.category import Category
from gateway_v2.domain.decision import Decision, Disposition, FindingDisposition
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.request_holds import RequestHolds

_SECRET = "123-45-6789"
_ADAPTER = (
    Path(__file__).resolve().parents[3]
    / "control"
    / "ai_mesh_control"
    / "core"
    / "v2_audit_adapter.py"
)


def test_output_allow_keeps_the_console_redact_action() -> None:
    module = _load()
    payload = _payload(Disposition.ALLOW)
    payload["detail"]["console"]["zeroshield"]["action"] = "redact"
    payload["detail"]["console"]["pipeline_trace"]["final_action"] = "redact"
    translated = module.translate(payload)
    assert translated.action == "redact"
    action, meta = module.merge_row(None, None, translated)
    assert action == "redact"
    assert meta["phases"] == ["input"]
    assert "event_type" not in meta
    assert "risk_score" not in meta
    assert meta["module_id"] == "1.1"
    assert _SECRET not in json.dumps(meta)


def test_a_later_allow_does_not_cover_a_block() -> None:
    module = _load()
    blocked = module.translate(_payload(Disposition.BLOCK))
    allowed = module.translate(_payload(Disposition.ALLOW))
    action, meta = module.merge_row(None, None, blocked)
    action, meta = module.merge_row(action, meta, allowed)
    assert action == "block"
    assert meta["pipeline_trace"]["final_action"] == "block"
    assert meta["phases"] == ["input"]


def test_org_map_parses_pairs_and_skips_blanks() -> None:
    module = _load()
    assert module.parse_org_map(" dev-org=zeroshield , ,bad, other=slug ") == {
        "dev-org": "zeroshield",
        "other": "slug",
    }
    assert module.parse_org_map("") == {}


def _payload(action: Disposition) -> dict:
    holds = RequestHolds()
    holds.note_tenant("dev-org")
    holds.note_request("zs-dev-org-1")
    finding = Finding(
        detector="pii",
        detector_version="2026.10",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=0.9,
        spans=(Span(0, 11),),
        evidence=_SECRET,
    )
    holds.note_input(
        Decision(
            disposition=action,
            per_finding=(FindingDisposition("pii", action),),
            transformations=(),
            findings=(finding,),
            plan_version="1.2.abc",
            deciding_rules=("pii",),
            unavailable_detectors=(),
        )
    )
    seen: list[AuditRecord] = []

    def _keep(record: AuditRecord) -> bool:
        seen.append(record)
        return True

    emit_chat_audit(_keep, holds, None)
    return json.loads(seen[0].serialize())


def _load():
    spec = importlib.util.spec_from_file_location("v2_audit_adapter", _ADAPTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
