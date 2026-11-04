"""GW27 Task 7: org audit gating (``edge/audit_emit.py``): truth table, stamp types, callers."""
from __future__ import annotations

import asyncio
import itertools
import random
from pathlib import Path

import pytest

from gateway_v2.audit.record import REJECTED, SHED, AuditPhase, AuditRecord
from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.domain.plan import UNSET_CONTROLS, AuditControl, OrgControls
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.request_holds import RequestHolds
from tests.edge import _gw27_harness as h
from tests.edge._mcp_fixtures import Broker, Control, call, mcp_deps, server_row

_DISPOSITIONS = (Disposition.ALLOW, Disposition.FLAG, Disposition.REDACT, Disposition.BLOCK)


class _Observer:
    def __init__(self) -> None:
        self.suppressed = 0

    def count_tenant(self, tenant_key: str) -> None: ...

    def observe_rejection(self, status: int, reason: str) -> None: ...

    def observe_posture(self, code: str) -> None: ...

    def observe_audit_suppressed(self) -> None:
        self.suppressed += 1


def _decision(disposition: Disposition) -> Decision:
    return Decision(
        disposition=disposition, per_finding=(), transformations=(), findings=(),
        plan_version="1.1.abc", deciding_rules=(), unavailable_detectors=(),
    )


def _expected(enabled: bool, incident: bool, phase: AuditPhase, outcome: str) -> bool:
    """The design's truth table, restated independently of the implementation."""
    if outcome in ("block", SHED, REJECTED):
        return True  # hard blocks, sheds and admission rejects are never suppressed
    if not enabled:
        return False
    if phase is AuditPhase.OUTPUT and outcome in ("redact", "flag"):
        return incident
    return True


def _drive(
    controls: OrgControls, inp: Disposition | None, out: Disposition | None,
    refuse: str | None = None,
) -> tuple[list[AuditRecord], _Observer]:
    holds = RequestHolds()
    holds.note_request("rid")
    holds.note_tenant("org")
    holds.note_controls(controls)
    if inp is not None:
        holds.note_input(_decision(inp))
    if out is not None:
        holds.note_output(_decision(out))
    seen: list[AuditRecord] = []
    observer = _Observer()
    emit_chat_audit(lambda rec: seen.append(rec) or True, holds, refuse, observer=observer)
    return seen, observer


def _check(enabled: bool, incident: bool, inp: Disposition, out: Disposition | None) -> None:
    controls = OrgControls(audit=AuditControl(enabled, incident, 30))
    seen, observer = _drive(controls, inp, out)
    produced = [(AuditPhase.INPUT, inp.value)] + ([(AuditPhase.OUTPUT, out.value)] if out else [])
    wanted = [p for p in produced if _expected(enabled, incident, *p)]
    assert [(r.phase, r.outcome) for r in seen] == wanted
    assert observer.suppressed == len(produced) - len(wanted)  # once per suppressed record


def test_full_truth_table() -> None:
    outs: tuple[Disposition | None, ...] = (None, *_DISPOSITIONS)
    for enabled, incident, inp, out in itertools.product((True, False), (True, False),
                                                         _DISPOSITIONS, outs):
        _check(enabled, incident, inp, out)


def test_admission_records_are_always_emitted() -> None:
    off = OrgControls(audit=AuditControl(False, False, 30))
    for code, outcome in (("overload_shed", SHED), ("budget_unavailable", REJECTED)):
        seen, observer = _drive(off, None, None, refuse=code)
        assert [r.outcome for r in seen] == [outcome] and observer.suppressed == 0


# Feature: gw27-firewall-config-fields, Property 10: audit gating follows the section 7 truth table
# Validates: Requirements 5.3
def test_property_audit_gating() -> None:
    seed = 2710
    rng = random.Random(seed)
    outs: tuple[Disposition | None, ...] = (None, *_DISPOSITIONS)
    for i in range(10_000):
        enabled, incident = rng.random() < 0.5, rng.random() < 0.5
        inp, out = rng.choice(_DISPOSITIONS), rng.choice(outs)
        try:
            _check(enabled, incident, inp, out)
        except AssertionError as exc:  # keep the seed in the failure message
            raise AssertionError((seed, i, enabled, incident, inp, out)) from exc


def test_no_audit_control_keeps_audit_on_and_unset_controls_carry_no_stamp() -> None:
    seen, observer = _drive(UNSET_CONTROLS, Disposition.ALLOW, Disposition.REDACT)
    assert len(seen) == 2 and observer.suppressed == 0
    assert all("retention_days" not in r.detail and "compliance_frameworks" not in r.detail
               for r in seen)


def test_stamp_types_are_int_and_list_not_strings() -> None:
    controls = OrgControls(audit=AuditControl(True, True, 90),
                           compliance_frameworks=("ISO27001", "SOC2"))
    seen, _ = _drive(controls, Disposition.ALLOW, Disposition.ALLOW)
    for record in seen:
        assert type(record.detail["retention_days"]) is int
        assert record.detail["retention_days"] == 90
        assert record.detail["compliance_frameworks"] == ["ISO27001", "SOC2"]
        assert isinstance(record.serialize(), bytes)


# --- one case per real caller, through its own ``finally`` ------------------------------------- #

_OFF = {"audit": {"enabled": False, "incident_logging": True, "retention_days": 30}}
_ON = {"audit": {"enabled": True, "incident_logging": True, "retention_days": 45},
       "compliance": {"frameworks": ["SOC2"]}}


def _app_with_capture(
    tmp_path: Path, controls: dict[str, object]
) -> tuple[object, list[AuditRecord], _Observer]:
    seen: list[AuditRecord] = []
    observer = _Observer()
    provider = h.Recording()
    app, deps = h.build(
        tmp_path, controls, provider, audit=lambda r: seen.append(r) or True, observer=observer
    )
    reply = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}}
    deps["mcp"] = mcp_deps(Broker(reply), Control([server_row("search")], {
        "known_tools": ["find"], "enabled_tools": ["find"], "disabled_tools": []}), "dev-org=acme")
    from gateway_v2.edge import serve

    return serve._assemble_app(deps), seen, observer  # noqa: SLF001


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/responses"])
def test_chat_and_responses_callers_gate_and_stamp(tmp_path: Path, path: str) -> None:
    body = h.CHAT[1] if "chat" in path else h.RESPONSES[1]
    app, seen, observer = _app_with_capture(tmp_path / "off", _OFF)
    assert asyncio.run(h.post(app, path, body))[0] == 200
    assert seen == [] and observer.suppressed >= 1
    app, seen, observer = _app_with_capture(tmp_path / "on", _ON)
    assert asyncio.run(h.post(app, path, body))[0] == 200
    assert seen and observer.suppressed == 0
    for record in seen:
        assert record.detail["retention_days"] == 45
        assert record.detail["compliance_frameworks"] == ["SOC2"]


def test_mcp_caller_gates_and_stamps(tmp_path: Path) -> None:
    payload = call("tools/call", {"name": "find", "arguments": {"q": "x"}})
    app, seen, observer = _app_with_capture(tmp_path / "off", _OFF)
    assert asyncio.run(h.post(app, "/v1/mcp/search", payload))[0] == 200
    assert seen == [] and observer.suppressed >= 1
    app, seen, _ = _app_with_capture(tmp_path / "on", _ON)
    assert asyncio.run(h.post(app, "/v1/mcp/search", payload))[0] == 200
    assert seen and all(r.detail["retention_days"] == 45 for r in seen)


def test_pre_plan_refusal_has_no_stamp_and_is_emitted(tmp_path: Path) -> None:
    seen, _ = _drive(UNSET_CONTROLS, None, None, refuse="overload_shed")
    assert [r.outcome for r in seen] == [SHED]
    assert "retention_days" not in seen[0].detail
