"""STRICT OPERATOR CONTROL (2026-07-23) — DIRECTION + SCOPE isolation for MCP scan controls.

The operator's scan-control selection is strictly bounded on two axes:

  DIRECTION (input / output / both): a row scoped to ONE direction must NOT cause
    enforcement on the OTHER. When the org HAS scan-control rows but none matches a
    given tier+direction, the control-plane returns that slot ``enabled=False``
    (scan_controls._pick_control: "an output-only row must not imply a baseline input
    scan, and vice versa"). The gateway must treat a disabled direction as OBSERVE-ONLY
    — no static hardening floor may fire on it.

    REGRESSION: ``_resolved_tier1_action`` read ``ctrl["action"]`` (via
    ``_resolve_tier_action``'s ``inherit`` fallback) WITHOUT checking ``enabled``, so a
    disabled OUTPUT slot resolved to the *server posture* (e.g. redact). That made
    ``_static_hardening_floors_enabled(output)`` True and ``_explicit_monitor_posture
    (output)`` False, so the cross-block-split / E12 floors REDACTED a tool RESULT under
    an INPUT-ONLY operator config. Fixed: a disabled direction resolves to ``monitor``.

  SCOPE (entire payload / specific key_path): a key-scoped row must mutate ONLY the
    bound key; other keys egress untouched. ``entire`` scans the whole payload.

These tests drive the REAL gateway resolution + scan/floor path (no stubs).
"""
from __future__ import annotations

import json
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
from mcp_scan_orchestrator import scan_mcp_payload  # noqa: E402

_SEC = "AKIAIOSFODNN7EXAMPLE"  # aws_access_key (SECRET)


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


def _slot(direction, enabled, action="inherit", *, target_mode="entire", key_path=""):
    return {
        "tier": "tier1", "enabled": enabled, "direction": direction, "scope_type": "org",
        "target_mode": target_mode, "key_path": key_path, "action": action,
        "priority": 0, "control_id": 1 if enabled else None,
    }


def _enabled_info(server_action, tier1_input, tier1_output):
    """Shape mirrors control-plane resolve_effective_controls() output."""
    return {
        "default_scan_action": server_action,
        "effective_scan_controls": {
            "scan_controls_configured": True,
            "tier1_input": tier1_input,
            "tier1_output": tier1_output,
            "tier2_input": {"tier": "tier2", "enabled": False, "action": "inherit"},
            "tier2_output": {"tier": "tier2", "enabled": False, "action": "inherit"},
        },
    }


def _effective_controls(tier1_input, tier1_output):
    return {
        "scan_controls_configured": True,
        "tier1_input": tier1_input,
        "tier1_output": tier1_output,
        "tier2_input": {"tier": "tier2", "enabled": False, "action": "inherit"},
        "tier2_output": {"tier": "tier2", "enabled": False, "action": "inherit"},
    }


# ────────────────────────── DIRECTION resolution ──────────────────────────
def test_disabled_output_direction_resolves_observe_only():
    """The core regression: an INPUT-ONLY config (output slot disabled) must resolve the
    OUTPUT action to observe-only — NOT leak the server posture into the output floors."""
    ei = _enabled_info("redact", _slot("input", True, "block"), _slot("output", False))
    assert mcp_proxy._resolved_tier1_action("fetch", ei, "input") == "block"
    assert mcp_proxy._resolved_tier1_action("fetch", ei, "output") == "monitor"
    # the floor GATES that consume the resolved action must both read observe-only
    assert mcp_proxy._static_hardening_floors_enabled("fetch", ei, "output") is False
    assert mcp_proxy._explicit_monitor_posture("fetch", ei, "output") is True


def test_disabled_input_direction_resolves_observe_only():
    """The reverse: an OUTPUT-ONLY config (input slot disabled) resolves INPUT observe-only."""
    ei = _enabled_info("redact", _slot("input", False), _slot("output", True, "block"))
    assert mcp_proxy._resolved_tier1_action("fetch", ei, "output") == "block"
    assert mcp_proxy._resolved_tier1_action("fetch", ei, "input") == "monitor"
    assert mcp_proxy._static_hardening_floors_enabled("fetch", ei, "input") is False


def test_both_directions_configured_each_honors_own_action():
    ei = _enabled_info("tag", _slot("input", True, "block"), _slot("output", True, "redact"))
    assert mcp_proxy._resolved_tier1_action("fetch", ei, "input") == "block"
    assert mcp_proxy._resolved_tier1_action("fetch", ei, "output") == "redact"


# ────────────────────────── DIRECTION enforcement (end-to-end) ──────────────────────────
@pytest.mark.asyncio
async def test_input_only_config_does_not_enforce_output_floor():
    """INPUT-ONLY (block) config: a cross-block-split secret in the tool RESULT must NOT be
    touched — the operator did not select output enforcement."""
    ei = _enabled_info("redact", _slot("input", True, "block"), _slot("output", False))
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": "key " + _SEC[:10]},
                     {"type": "text", "text": _SEC[10:] + " end"}]},
        tool_name="fetch", enabled_info=ei, org_slug="o", server_slug="s", actor=None)
    joined = "".join(b.get("text", "") for b in scanned["content"])
    assert blocked is False
    assert _SEC in joined, "output must egress untouched under an input-only config"
    assert not meta.get("cross_block_split_redacted")
    assert not meta.get("cross_block_split_secret")


@pytest.mark.asyncio
async def test_output_only_config_does_not_enforce_input():
    """OUTPUT-ONLY (block) config: a secret in the tool ARGUMENTS (input) must NOT be
    scanned/enforced — the operator did not select input enforcement."""
    ec = _effective_controls(_slot("input", False), _slot("output", True, "block"))
    payload = {"name": "issue_write", "arguments": {"body": f"key {_SEC}"}}
    scanned, res = await scan_mcp_payload(
        payload, scan_direction="input", enforcement="redact",
        effective_controls=ec, tool_name="issue_write", org_slug="o", server_slug="s", actor=None)
    assert res.blocked is False
    assert _SEC in json.dumps(scanned), "input must egress untouched under an output-only config"


@pytest.mark.asyncio
async def test_output_configured_still_enforces_output():
    """Positive control: an OUTPUT redact config DOES mask a result secret (isolation must
    not disable the direction the operator actually selected)."""
    ei = _enabled_info("tag", _slot("input", False), _slot("output", True, "redact"))
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": f"leak {_SEC} here"}]},
        tool_name="fetch", enabled_info=ei, org_slug="o", server_slug="s", actor=None)
    assert blocked is False
    assert _SEC not in json.dumps(scanned), "operator-selected output redact must mask"


# ────────────────────────── SCOPE (entire vs specific key) ──────────────────────────
@pytest.mark.asyncio
async def test_key_scoped_control_masks_only_bound_key():
    """key_path='arguments.body' redact: only ``body`` is masked; ``title`` egresses raw."""
    ec = _effective_controls(
        _slot("input", True, "redact", target_mode="key_path", key_path="arguments.body"),
        _slot("output", False))
    payload = {"name": "issue_write", "arguments": {"body": f"leak {_SEC}", "title": f"also {_SEC}"}}
    scanned, res = await scan_mcp_payload(
        payload, scan_direction="input", enforcement="redact",
        effective_controls=ec, tool_name="issue_write", org_slug="o", server_slug="s", actor=None)
    body = scanned["arguments"]["body"]
    title = scanned["arguments"]["title"]
    assert _SEC not in body, "in-scope key must be redacted"
    assert _SEC in title, "out-of-scope key must be untouched (operator scoped to body only)"


@pytest.mark.asyncio
async def test_entire_scope_control_masks_all_keys():
    """target_mode='entire' redact: every key carrying the secret is masked."""
    ec = _effective_controls(
        _slot("input", True, "redact", target_mode="entire"),
        _slot("output", False))
    payload = {"name": "issue_write", "arguments": {"body": f"leak {_SEC}", "title": f"also {_SEC}"}}
    scanned, res = await scan_mcp_payload(
        payload, scan_direction="input", enforcement="redact",
        effective_controls=ec, tool_name="issue_write", org_slug="o", server_slug="s", actor=None)
    blob = json.dumps(scanned)
    assert _SEC not in blob, "entire-payload scope must mask every occurrence"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
