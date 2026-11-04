"""GW35: a rejected credential writes an audit row under the reserved org, with no secret in it."""

from __future__ import annotations

import asyncio
import json
import secrets

import pytest

from gateway_v2.audit.console_schema import SchemaError, validate_audit_record
from gateway_v2.audit.record import UNAUTHENTICATED_ORG
from gateway_v2.edge.unauth_audit import UnauthAuditGate
from tests.edge import _gw35_harness as h


def _app(**kw):  # type: ignore[no-untyped-def]
    return h.build_app(h.ScriptedProvider(h.ok_events()), **kw)


def test_a_bad_key_is_one_rejected_row_under_the_reserved_org() -> None:
    app, records, _ = _app()
    res = asyncio.run(h.post(app, records, auth="Bearer wrong"))
    assert res.status == 401
    assert len(res.records) == 1
    row = res.records[0]
    assert row.org_id == UNAUTHENTICATED_ORG and row.outcome == "rejected"
    assert row.detail["reason"] == "invalid_api_key" and row.detail["status"] == 401
    console = row.detail["console"]
    assert console["pipeline_trace"]["stages"][0] == {  # type: ignore[index]
        "name": "auth", "action": "block", "ran": True, "deciding_rule": "invalid_api_key",
    }
    validate_audit_record(dict(row.as_mapping()))


def test_a_missing_header_is_audited_too() -> None:
    app, records, _ = _app()
    res = asyncio.run(h.post(app, records, auth=None))
    assert [r.org_id for r in res.records] == [UNAUTHENTICATED_ORG]


def test_the_row_carries_only_a_12_hex_fingerprint_never_the_token() -> None:
    token = secrets.token_hex(24)  # console-key shape: 48 characters
    app, records, _ = _app()
    res = asyncio.run(h.post(app, records, auth=f"Bearer {token}"))
    dump = json.dumps([dict(r.as_mapping()) for r in res.records]) + res.body.decode()
    assert token not in dump
    import hashlib

    expected = hashlib.sha256(token.encode()).hexdigest()[:12]
    assert [r.detail.get("key_fingerprint") for r in res.records] == [expected]


def test_a_junk_token_has_no_fingerprint() -> None:
    app, records, _ = _app()
    res = asyncio.run(h.post(app, records, auth="Bearer not-a-console-key"))
    assert "key_fingerprint" not in res.records[0].detail


def test_a_full_hash_in_a_record_is_rejected_by_the_schema() -> None:
    app, records, _ = _app()
    res = asyncio.run(h.post(app, records, auth="Bearer wrong"))
    bad = dict(res.records[0].as_mapping())
    bad["detail"] = {**bad["detail"], "key_fingerprint": "a" * 64}  # type: ignore[dict-item]
    with pytest.raises(SchemaError):
        validate_audit_record(bad)
    bad["detail"] = {"x": "a" * 64}
    with pytest.raises(SchemaError):
        validate_audit_record(bad)


def test_a_raising_sink_leaves_the_401_intact() -> None:
    def _boom(deps: dict[str, object]) -> None:
        def emit(record: object) -> bool:
            raise RuntimeError("sink down")

        deps["audit"] = emit

    app, records, _ = _app(mutate_deps=_boom)
    res = asyncio.run(h.post(app, records, auth="Bearer wrong"))
    assert res.status == 401 and res.json()["error"]["code"] == "invalid_api_key"  # type: ignore[index]


def test_a_saturated_gate_drops_and_counts_without_changing_the_401() -> None:
    gate = UnauthAuditGate(rate_per_window=2, window_s=1e9, clock=lambda: 0.0)
    app, records, _ = _app(mutate_deps=lambda d: d.update(unauth_audit=gate))
    results = [asyncio.run(h.post(app, records, auth="Bearer wrong")) for _ in range(5)]
    assert all(r.status == 401 for r in results)
    assert sum(len(r.records) for r in results) == 2
    assert gate.accepted == 2 and gate.dropped == 3


def test_no_gate_means_no_unauthenticated_rows() -> None:
    app, records, _ = _app(mutate_deps=lambda d: d.update(unauth_audit=None))
    res = asyncio.run(h.post(app, records, auth="Bearer wrong"))
    assert res.status == 401 and res.records == []


def test_the_metrics_series_count_the_gate() -> None:
    app, records, deps = _app()
    asyncio.run(h.post(app, records, auth="Bearer wrong"))
    text = deps["metrics_registry"].exposition()  # type: ignore[attr-defined]
    assert "amf_audit_unauthenticated_total 1" in text
    assert "amf_audit_unauthenticated_dropped_total" in text


def test_an_authenticated_request_never_uses_the_reserved_org() -> None:
    app, records, _ = _app()
    res = asyncio.run(h.post(app, records))
    assert {r.org_id for r in res.records} == {"dev-org"}


def test_the_token_is_absent_from_logs_and_metrics(caplog: pytest.LogCaptureFixture) -> None:
    token = secrets.token_hex(24)
    app, records, deps = _app()
    with caplog.at_level("DEBUG"):
        asyncio.run(h.post(app, records, auth=f"Bearer {token}"))
    assert token not in caplog.text
    assert token not in deps["metrics_registry"].exposition()  # type: ignore[attr-defined]
