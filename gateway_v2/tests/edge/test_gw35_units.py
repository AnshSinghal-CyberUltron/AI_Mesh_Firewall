"""GW35 units: the request ledger, the unauthenticated gate, and the audit scoping."""

from __future__ import annotations

import pytest

from gateway_v2.audit.record import UNAUTHENTICATED_ORG
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.unauth_audit import UnauthAuditCounters, UnauthAuditGate, build_unauth_gate
from gateway_v2.runtime.errors import CapacityUnavailable
from tests.runtime.test_unauth_audit_rate import _contract


def test_holds_are_slotted_with_no_dict() -> None:
    holds = RequestHolds()
    assert not hasattr(holds, "__dict__")
    with pytest.raises(AttributeError):
        holds.extra = 1  # type: ignore[attr-defined]


def test_note_failure_keeps_the_first_cause() -> None:
    holds = RequestHolds()
    holds.note_failure("upstream_error")
    holds.note_failure("scan_failure")
    holds.note_failure("")
    assert holds.failure_code == "upstream_error"


def test_note_output_action_accepts_only_dispositions_and_last_write_wins() -> None:
    holds = RequestHolds()
    for bad in ("error", "", None, "skip", "ALLOW"):
        holds.note_output_action(bad)
        assert holds.output_action == ""
    holds.note_output_action("allow")
    holds.note_output_action("block", "stream_key_revoked")
    holds.note_output_action("error")
    assert (holds.output_action, holds.output_rule) == ("block", "stream_key_revoked")


def test_the_gate_admits_at_most_rate_per_window_and_counts_the_rest() -> None:
    now = [0.0]
    gate = UnauthAuditGate(rate_per_window=3, window_s=10.0, clock=lambda: now[0])
    assert [gate.admit() for _ in range(5)] == [True, True, True, False, False]
    now[0] = 11.0
    assert gate.admit() is True
    assert (gate.accepted, gate.dropped) == (4, 2)
    assert UnauthAuditCounters(gate).series() == {
        "amf_audit_unauthenticated_total": 4.0,
        "amf_audit_unauthenticated_dropped_total": 2.0,
    }


def test_the_gate_never_raises_on_a_broken_clock() -> None:
    def broken() -> float:
        raise RuntimeError("clock")

    gate = UnauthAuditGate(rate_per_window=1, window_s=1.0, clock=lambda: 0.0)
    gate._clock = broken  # type: ignore[misc]
    assert gate.admit() is False and gate.dropped == 1


def test_an_underivable_bound_builds_a_gate_that_drops_everything() -> None:
    import dataclasses

    bad = dataclasses.replace(_contract(1), worker_override=0)
    with pytest.raises(CapacityUnavailable):
        bad.workers()
    gate = build_unauth_gate(bad, lambda: 0.0)
    assert gate.admit() is False and gate.dropped == 1


def test_mcp_never_gets_the_reserved_org() -> None:
    holds = RequestHolds()
    holds.note_request("zs-1")
    seen: list[object] = []
    gate = UnauthAuditGate(rate_per_window=10, window_s=1.0, clock=lambda: 0.0)
    emit_chat_audit(
        lambda r: seen.append(r) or True, holds, "invalid_api_key", route="mcp", gate=gate,
    )
    assert seen == [] and gate.accepted == 0


def test_chat_with_a_resolved_org_never_uses_the_sentinel() -> None:
    holds = RequestHolds()
    holds.note_tenant("org-a")
    holds.note_request("zs-1")
    seen = []
    emit_chat_audit(lambda r: seen.append(r) or True, holds, "overload_shed", gate=None)
    assert [r.org_id for r in seen] == ["org-a"] and UNAUTHENTICATED_ORG not in str(seen)
