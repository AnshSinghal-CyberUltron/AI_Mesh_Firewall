# Validates: Requirements 7.2 (one audit record per incident event; closed vocabulary; no secrets)
"""GW29 audit constructors: closed vocab, secret-free, existing `admission` callers unchanged."""

from __future__ import annotations

import json

import pytest

from gateway_v2.audit import record
from gateway_v2.audit.record import (
    INCIDENT_REASONS,
    ISOLATION_CHANGE,
    REJECTED,
    REROUTED,
    SHED,
    TERMINATED,
    AuditPhase,
    admission,
    incident_change,
    incident_cut,
    incident_refusal,
    incident_reroute,
    shed,
)
from gateway_v2.domain.incident import INCIDENT_CAUSES
from gateway_v2.edge import counters

_FORBIDDEN = {"prompt", "content", "key", "api_key", "authorization", "token", "secret", "bearer"}


def _all_records() -> list[record.AuditRecord]:
    return [
        incident_refusal(request_id="r", org_id="o", code="model_killed", cause="model",
                         recorded_at_ns=5, retry_after_ms=1500),
        incident_refusal(request_id="r", org_id="o", code="kill_switch_unavailable",
                         cause="recheck", recorded_at_ns=5, retry_after_ms=1500,
                         underlying="stale"),
        incident_cut(request_id="r", org_id="o", code="stream_killed", cause="cut_killed",
                     recorded_at_ns=5),
        incident_reroute(request_id="r", org_id="o", requested="a", routed="b", recorded_at_ns=5),
        incident_change(org_id="global", cause="isolate", model="a", fallback="b",
                        reason="error_rate", recorded_at_ns=5),
    ]


def test_refusal_record_shape() -> None:
    rec = incident_refusal(request_id="r1", org_id="acme", code="model_killed", cause="model",
                           recorded_at_ns=9, retry_after_ms=2100)
    assert rec.phase is AuditPhase.ADMISSION and rec.outcome == REJECTED
    assert dict(rec.detail) == {"reason": "model_killed", "status": 503, "cause": "model",
                                "retry_after_ms": 2100}


def test_recheck_refusal_carries_the_underlying_code() -> None:
    rec = incident_refusal(request_id="r", org_id="o", code="kill_switch_unavailable",
                           cause="recheck", recorded_at_ns=1, retry_after_ms=1, underlying="stale")
    assert rec.detail["underlying"] == "stale" and rec.detail["cause"] == "recheck"


def test_cut_reroute_and_change_records() -> None:
    cut, reroute, change = _all_records()[2:]
    got = (cut.outcome, cut.detail["cause"], cut.detail["status"])
    assert got == (TERMINATED, "cut_killed", 200)
    assert reroute.outcome == REROUTED
    assert (reroute.detail["from"], reroute.detail["to"]) == ("a", "b")
    assert change.outcome == ISOLATION_CHANGE and change.detail["cause"] == "isolate"
    assert change.request_id == record.INCIDENT_REQUEST_ID and change.org_id == "global"
    assert change.detail["fallback"] == "b" and change.detail["model"] == "a"


def test_every_record_is_phase_admission_closed_vocab_and_secret_free() -> None:
    for rec in _all_records():
        assert rec.phase is AuditPhase.ADMISSION
        assert rec.outcome in INCIDENT_REASONS
        assert rec.detail["cause"] in INCIDENT_CAUSES
        assert not _FORBIDDEN & {k.lower() for k in rec.detail}
        json.loads(rec.serialize())  # stable, serialisable


@pytest.mark.parametrize("bad", ["", "nope", "MODEL"])
def test_unknown_cause_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        incident_refusal(request_id="r", org_id="o", code="c", cause=bad, recorded_at_ns=1,
                         retry_after_ms=1)
    with pytest.raises(ValueError):
        incident_cut(request_id="r", org_id="o", code="c", cause=bad, recorded_at_ns=1)


def test_change_cause_is_limited_to_the_three_system_events() -> None:
    with pytest.raises(ValueError):
        incident_change(org_id="global", cause="model", model="a", recorded_at_ns=1)


def test_the_counter_reason_vocab_is_not_extended() -> None:
    assert counters.REASON_VOCAB == (SHED, REJECTED)  # C13: audit reasons only


def test_existing_admission_callers_are_byte_identical() -> None:
    rec = admission(request_id="r", org_id="o", outcome=REJECTED, reason="plan_unavailable",
                    recorded_at_ns=3, status=503)
    assert rec.serialize() == (
        b'{"at_ns":3,"detail":{"reason":"plan_unavailable","status":503},"org_id":"o",'
        b'"outcome":"rejected","phase":"admission","request_id":"r","v":1}'
    )
    s = shed(request_id="r", org_id="o", reason="overload_shed", recorded_at_ns=3,
             retry_after_ms=1200)
    assert dict(s.detail) == {"reason": "overload_shed", "status": 503, "retry_after_ms": 1200}
