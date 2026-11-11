"""L14b-1 local half: four decisions through the v2 adapter, against the recorded v1 event.

The recorded v1 side is docs/plans/evidence/2026-10-26-gw14b/l14b-1-recorded-diffs.json.
A field that differs is named there. A new difference fails this test.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from gateway_v2.audit.record import AuditRecord
from gateway_v2.domain.category import Category
from gateway_v2.domain.decision import Decision, Disposition, FindingDisposition
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.request_holds import RequestHolds

_REPO = Path(__file__).resolve().parents[3]
_LEDGER = _REPO / "docs" / "plans" / "evidence" / "2026-10-26-gw14b" / "l14b-1-recorded-diffs.json"
_ADAPTER = _REPO / "control" / "ai_mesh_control" / "core" / "v2_audit_adapter.py"
_FIELDS = ("action", "event_type", "module_id", "risk_score", "security_risk_score")


def test_handful_matches_the_recorded_v1_event_except_the_ledger() -> None:
    ledger = json.loads(_LEDGER.read_text(encoding="utf-8"))
    assert ledger["comparison_fields"] == list(_FIELDS)
    translate = _load_adapter()
    cases = cast(list[dict[str, object]], ledger["cases"])
    for case in cases:
        decision = cast(str, case["decision"])
        route = cast(str | None, case["route"])
        translated = translate(_payload(decision, route))
        observed = _observed(translated)
        assert observed == case["v2"], decision
        recorded = cast(dict[str, object], case["v1"])
        diffs = [field for field in _FIELDS if observed[field] != recorded[field]]
        assert diffs == case["registered_differences"], decision
        assert observed["action"] == recorded["action"]


def _observed(translated: object) -> dict[str, object]:
    metadata = cast(Mapping[str, object], getattr(translated, "metadata"))
    return {
        "action": getattr(translated, "action"),
        "event_type": metadata.get("event_type"),
        "module_id": getattr(translated, "module_id"),
        "risk_score": metadata.get("risk_score"),
        "security_risk_score": metadata.get("security_risk_score"),
    }


def _payload(decision: str, route: str | None) -> dict[str, object]:
    action = Disposition(decision)
    holds = RequestHolds()
    holds.note_tenant("dev-org")
    holds.note_request(f"zs-l14b1-{decision}")
    holds.note_input(
        Decision(
            disposition=action,
            per_finding=(FindingDisposition("pii", action),),
            transformations=(),
            findings=(
                Finding(
                    detector="pii",
                    detector_version="2026.10",
                    category=Category.PII,
                    status=FindingStatus.EXECUTED,
                    confidence=0.9,
                    spans=(Span(0, 3),),
                    evidence=None,
                ),
            ),
            plan_version="1.2.l14b1",
            deciding_rules=("pii",),
            unavailable_detectors=(),
        ),
    )
    seen: list[AuditRecord] = []
    def _keep(record: AuditRecord) -> bool:
        seen.append(record)
        return True

    emit_chat_audit(_keep, holds, None)
    payload = cast(dict[str, object], json.loads(seen[0].serialize()))
    if route is None:
        detail = cast(dict[str, object], payload["detail"])
        detail.pop("route")
    return payload


def _load_adapter() -> Callable[[object], object]:
    spec = importlib.util.spec_from_file_location("v2_audit_adapter_l14b1", _ADAPTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(Callable[[object], object], module.translate)
