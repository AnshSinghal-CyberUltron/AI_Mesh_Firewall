"""CHG-0081: CHG-0077 masks/blocks a poisoned tools/list tool-description leak but
recorded NO gateway audit event — so a tool-poisoning BLOCK or a secret/PII/IP REDACT on
the discovery path was INVISIBLE to the MCPEvent audit/SIEM trail (breaks the …→tag→AUDIT
chain for tools/list, which the tools/call path already audits).

`_scanned_tools_list_response` now records a block XOR redact gateway event (with the
compliance tags, findings, and the threaded request-id); a fully-clean tools/list is not
audited (avoids per-discovery noise).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402

_SECRET = "sk-ant-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"
_ZW = "​"  # zero-width space


async def _run(tools, scan_action="redact"):
    """``scan_action`` is the posture the OPERATOR selected. Enforcement is strictly
    operator-selected, so a test that expects a redact/block DECISION must select an
    enforcing posture; under tag/monitor the payload is never mutated."""
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}
    enabled_info = {"default_scan_action": scan_action} if scan_action else None
    with patch.object(mcp_proxy, "_record_gateway_event", new=AsyncMock()) as rec:
        await mcp_proxy._scanned_tools_list_response(
            payload, jsonrpc="2.0", msg_id=1, enabled_info=enabled_info,
            org_slug="o", server_slug="s", actor=None, request_id="req-123")
    return rec


# __PDD_PRESET_FIXTURE__

# policy-driven-detection cutover (task 9): the MCP built-in default detectors (the Tier-1
# PRESET pass) are now EFFECTIVE-DEFAULT OFF (mcp_scan_orchestrator._mcp_default_detection_enabled)
# so a zero-enabled-policy org is passthrough. This module exercises the RETAINED preset
# DETECTION MACHINERY (redaction / fail-closed byte-truth / exfil-defang / authz / audit), which
# stays reachable via the explicit opt-in env. Enable it for this module so those invariants are
# still tested. The default-OFF (Zero_Policy_State passthrough) contract is asserted by the
# dedicated test_policy_driven_* modules, not weakened here.
import os as _os_pdd


@pytest.fixture(autouse=True)
def _enable_builtin_mcp_presets(monkeypatch):
    monkeypatch.setenv("GATEWAY_MCP_DEFAULT_DETECTION", "true")
    monkeypatch.setenv("GATEWAY_MCP_REDACT_RESULT_ON_DETECT", "true")
    yield


@pytest.mark.asyncio
async def test_redact_is_audited():
    rec = await _run([{"name": "add", "description": f"Internal gw 10.9.8.7 key {_SECRET}"}])
    assert rec.await_count == 1
    kw = rec.await_args.kwargs
    assert kw["decision"] == "redact"
    assert kw["tool_name"] == "tools/list"
    assert kw["request_id"] == "req-123"
    assert "SECRET" in kw["compliance_tags"]


@pytest.mark.asyncio
async def test_redact_is_audited():
    # under the operator's redact posture a detected secret is audited as decision=redact
    rec = await _run([{"name": "x", "description": f"helper sk{_ZW}-ant{_ZW}-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"}])
    assert rec.await_count == 1
    assert rec.await_args.kwargs["decision"] == "redact"

@pytest.mark.asyncio
async def test_block_is_audited_under_block():
    rec = await _run([{"name": "x", "description": f"helper sk{_ZW}-ant{_ZW}-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"}], scan_action="block")
    assert rec.await_count == 1
    assert rec.await_args.kwargs["decision"] == "block"


@pytest.mark.asyncio
async def test_clean_tools_list_not_audited():
    rec = await _run([{"name": "ok", "description": "Fetches weather data for a city"}])
    assert rec.await_count == 0, "a clean tools/list must not emit audit noise"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
