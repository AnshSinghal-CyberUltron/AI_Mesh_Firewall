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


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["input", "output"])
async def test_no_controls_skips_all_scanning(direction):
    """scan_controls_configured=False => payload untouched, no findings/tags, skip meta."""
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
