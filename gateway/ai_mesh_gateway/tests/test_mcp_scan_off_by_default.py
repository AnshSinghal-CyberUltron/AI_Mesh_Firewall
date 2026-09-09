"""Off-by-default scan controls: an org with ZERO scan controls is NOT scanned.

Restored for chat-only OG lane (2026-07-10): MCP/RAG hardening is out of scope;
this file pins the pre-existing MCP product decision (0 controls => skip).
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

import mcp_proxy  # noqa: E402

_SSN_PAYLOAD = {"note": "patient SSN is 123-45-6789 on file"}

_EFFECTIVE_ON = {
    "scan_controls_configured": True,
    "tier1_input": {"enabled": True, "target_mode": "entire", "key_path": "",
                    "strict_mode": "fail_open", "action": "inherit"},
    "tier1_output": {"enabled": True, "target_mode": "entire", "key_path": "",
                     "strict_mode": "fail_open", "action": "inherit"},
    "tier2_input": {"enabled": False, "target_mode": "entire", "key_path": "",
                    "strict_mode": "strict", "action": "inherit"},
    "tier2_output": {"enabled": False, "target_mode": "entire", "key_path": "",
                     "strict_mode": "strict", "action": "inherit"},
}


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
@pytest.mark.parametrize("direction", ["input", "output"])
async def test_no_controls_observe_posture_detects_only(direction):
    """CHG-0119: scan_controls_configured=False + OBSERVE posture (tag/monitor) =>
    DETECT-ONLY. Findings surface for telemetry, but the payload is NEVER mutated and
    the call is NEVER blocked (FROZEN operator model: observe never acts). No scan_skipped."""
    enabled_info = {
        "scan_controls_configured": False,
        "default_scan_action": "tag",
        "effective_scan_controls": {},
        "effective_scan_controls_by_tool": {},
    }
    scanned, blocked, tags, findings, meta = await mcp_proxy._mcp_security_scan(
        _SSN_PAYLOAD,
        scan_direction=direction,
        tool_name="echo",
        enabled_info=enabled_info,
        org_slug="zeroshield",
        server_slug="everything-1",
    )
    # Detect-only: payload untouched + never blocked, but the SSN IS detected (visible).
    assert scanned == _SSN_PAYLOAD
    assert blocked is False
    assert findings, "observe posture must DETECT the SSN so it is visible in telemetry"
    assert meta.get("detect_only_observe") is True
    assert meta.get("scan_skipped") != "no_scan_controls_configured"
    # Belt-and-suspenders: an observe scan never reports redacted fields.
    assert meta.get("redacted_fields") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["input", "output"])
async def test_no_controls_kill_switch_off_skips(direction, monkeypatch):
    """With the CHG-0119 kill-switch OFF, zero controls restores the pure off-by-default
    skip (no scanning at all), regardless of posture."""
    monkeypatch.setattr(mcp_proxy, "_MCP_OBSERVE_SCAN_UNCONFIGURED", False)
    enabled_info = {
        "scan_controls_configured": False,
        "default_scan_action": "tag",
        "effective_scan_controls": {},
        "effective_scan_controls_by_tool": {},
    }
    scanned, blocked, tags, findings, meta = await mcp_proxy._mcp_security_scan(
        _SSN_PAYLOAD,
        scan_direction=direction,
        tool_name="echo",
        enabled_info=enabled_info,
        org_slug="zeroshield",
        server_slug="everything-1",
    )
    assert scanned == _SSN_PAYLOAD
    assert blocked is False
    assert tags == []
    assert findings == []
    assert meta.get("scan_skipped") == "no_scan_controls_configured"
    assert meta.get("monitored") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["input", "output"])
async def test_no_controls_enforcing_posture_still_skips(direction):
    """A non-observe (block/redact) posture with zero controls keeps the off-by-default
    skip: enforcement floors require EXPLICIT scan controls, so detect-only never turns
    an unconfigured enforcing posture into scanning."""
    enabled_info = {
        "scan_controls_configured": False,
        "default_scan_action": "block",
        "effective_scan_controls": {},
        "effective_scan_controls_by_tool": {},
    }
    scanned, blocked, tags, findings, meta = await mcp_proxy._mcp_security_scan(
        _SSN_PAYLOAD,
        scan_direction=direction,
        tool_name="echo",
        enabled_info=enabled_info,
        org_slug="zeroshield",
        server_slug="everything-1",
    )
    assert scanned == _SSN_PAYLOAD
    assert blocked is False
    assert findings == []
    assert meta.get("scan_skipped") == "no_scan_controls_configured"


@pytest.mark.asyncio
async def test_flag_absent_fails_safe_to_scanning():
    scanned, blocked, tags, findings, meta = await mcp_proxy._mcp_security_scan(
        _SSN_PAYLOAD,
        scan_direction="input",
        tool_name="echo",
        enabled_info=None,
        org_slug="zeroshield",
        server_slug="everything-1",
    )
    assert meta.get("scan_skipped") != "no_scan_controls_configured"
    assert findings, "fail-safe: Tier-1 must run when the flag is unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("server_default", ["tag", "monitor"])
async def test_configured_block_control_blocks_even_with_observe_server_default(server_default):
    """CHG-0119 regression: when scan controls ARE configured with a tier1_input
    action='block' row, the call MUST block on a finding — even if the SERVER default
    posture is observe (tag/monitor). Detect-only must NOT suppress a configured per-tier
    block (it applies only to the ZERO-controls case)."""
    eff = {
        "scan_controls_configured": True,
        "tier1_input": {"enabled": True, "target_mode": "entire", "key_path": "",
                        "strict_mode": "fail_open", "action": "block"},
        "tier1_output": {"enabled": True, "target_mode": "entire", "key_path": "",
                         "strict_mode": "fail_open", "action": "inherit"},
        "tier2_input": {"enabled": False, "target_mode": "entire", "key_path": "",
                        "strict_mode": "strict", "action": "inherit"},
        "tier2_output": {"enabled": False, "target_mode": "entire", "key_path": "",
                         "strict_mode": "strict", "action": "inherit"},
    }
    enabled_info = {
        "scan_controls_configured": True,
        "default_scan_action": server_default,
        "effective_scan_controls": eff,
        "effective_scan_controls_by_tool": {},
    }
    scanned, blocked, tags, findings, meta = await mcp_proxy._mcp_security_scan(
        _SSN_PAYLOAD,
        scan_direction="input",
        tool_name="echo",
        enabled_info=enabled_info,
        org_slug="zeroshield",
        server_slug="everything-1",
    )
    assert blocked is True, "configured tier1_input=block must BLOCK despite observe server default"
    assert meta.get("detect_only_observe") is not True
    assert findings


@pytest.mark.asyncio
async def test_configured_true_still_scans():
    enabled_info = {
        "scan_controls_configured": True,
        "default_scan_action": "monitor",
        "effective_scan_controls": _EFFECTIVE_ON,
        "effective_scan_controls_by_tool": {},
    }
    scanned, blocked, tags, findings, meta = await mcp_proxy._mcp_security_scan(
        _SSN_PAYLOAD,
        scan_direction="input",
        tool_name="echo",
        enabled_info=enabled_info,
        org_slug="zeroshield",
        server_slug="everything-1",
    )
    assert meta.get("scan_skipped") != "no_scan_controls_configured"
    assert findings
