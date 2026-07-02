"""CHG-0087: MCP tool-call scan/enforcement decisions were AUDITED (MCPEvent via
mcp_proxy._record_gateway_event) but not METERED — metrics.py had no MCP counter and
mcp_proxy imported metrics nowhere. So the 1.4 guardrails (block/redact for tool-poisoning
/ credentials / PII / IP, + compliance-tag distribution) were invisible to Prometheus
dashboards + alerting (item 13 monitoring gap).

`record_mcp_scan_decision` now increments `amf_gateway_mcp_scan_decisions_total{org,
decision}` and `amf_gateway_mcp_compliance_tags_total{org,tag}`, wired into
`_record_gateway_event`. Fail-safe (no-op without prometheus_client).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import metrics as M  # noqa: E402


def _sample(name, **labels):
    if not M._PROM_AVAILABLE:
        return None
    return M.REGISTRY.get_sample_value(name, labels)


@pytest.mark.skipif(not M._PROM_AVAILABLE, reason="prometheus_client not installed")
def test_record_mcp_scan_decision_increments_counters():
    before = _sample("amf_gateway_mcp_scan_decisions_total", org="unittest", decision="block") or 0.0
    tbefore = _sample("amf_gateway_mcp_compliance_tags_total", org="unittest", tag="secret") or 0.0

    M.record_mcp_scan_decision("unittest", "block", ["SECRET", "SOC2"])

    assert _sample("amf_gateway_mcp_scan_decisions_total", org="unittest", decision="block") == before + 1
    assert _sample("amf_gateway_mcp_compliance_tags_total", org="unittest", tag="secret") == tbefore + 1
    # SOC2 tag also counted
    assert (_sample("amf_gateway_mcp_compliance_tags_total", org="unittest", tag="soc2") or 0.0) >= 1


@pytest.mark.skipif(not M._PROM_AVAILABLE, reason="prometheus_client not installed")
def test_empty_tags_and_no_org_are_safe():
    # no tags → decision still counted, no tag rows
    M.record_mcp_scan_decision("unittest2", "allow", None)
    assert (_sample("amf_gateway_mcp_scan_decisions_total", org="unittest2", decision="allow") or 0.0) >= 1
    # anonymous org fallback
    M.record_mcp_scan_decision("", "redact", ["PII"])
    assert (_sample("amf_gateway_mcp_scan_decisions_total", org="anonymous", decision="redact") or 0.0) >= 1


@pytest.mark.asyncio
async def test_audit_sink_wires_metrics():
    # _record_gateway_event should meter the decision (best-effort). Just assert it does
    # not raise and the counter moves.
    import mcp_proxy as MP
    before = _sample("amf_gateway_mcp_scan_decisions_total", org="wiretest", decision="block") or 0.0
    await MP._record_gateway_event(
        org_slug="wiretest", server_slug="s", tool_name="fetch",
        decision="block", reason="pii_blocked_inbound", compliance_tags=["SECRET"])
    if M._PROM_AVAILABLE:
        assert _sample("amf_gateway_mcp_scan_decisions_total", org="wiretest", decision="block") == before + 1


def test_record_is_failsafe_monkeypatched(monkeypatch):
    # if prometheus is unavailable the helper is a no-op (never raises)
    monkeypatch.setattr(M, "_PROM_AVAILABLE", False)
    M.record_mcp_scan_decision("x", "block", ["SECRET"])  # must not raise


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
