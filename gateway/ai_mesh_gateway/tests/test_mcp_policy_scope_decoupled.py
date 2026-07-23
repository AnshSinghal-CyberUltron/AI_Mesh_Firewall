"""PHASE 1 (2026-07-23) — POLICY owns its scope, decoupled from the scan-control binding.

Collapse-to-one-surface, slice 1. A policy rule's OWN scope/direction must be honoured
independently of the scan-control Tier-1 ``target_mode``/``key_path`` binding.

THE LEAK THIS CLOSES: ``scan_mcp_payload`` used to evaluate policies PER scan-control-bound
target, and ``_build_mcp_context`` fed the engine ``prompt = <bound fragment>``. The policy
engine's ``scope=entire`` scans ``prompt or input_args`` (policy_engine.py:587), so ``prompt``
won → a policy authored ``scope=entire`` collapsed to whatever key the scan-control bound.
With a key-scoped scan-control (``arguments.body``), a secret in a SIBLING field (``title``)
was neither detected, tagged, redacted, nor blocked despite a matching entire-scope redact
policy — it egressed COMPLETELY RAW.

THE FIX: the POLICY lane runs ONCE against the FULL payload (its own scope), and its
redaction lands where the policy matched; the PRESET lane keeps the scan-control scope.
Each surface honours its OWN operator selection. Direction isolation is preserved — the
policy lane runs only for an ENABLED tier1 direction (after the disabled/direction skip).
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

import mcp_scan_orchestrator as orch  # noqa: E402

_SEC = "AKIAIOSFODNN7EXAMPLE"  # aws_access_key (SECRET)


class _FakePolicySync:
    def __init__(self, rules):
        self._rules = rules

    def get_policies_for_server(self, org_slug, server_slug, domain="mcp"):
        return [{
            "policy": {"id": 1, "code": "P", "name": "P",
                       "policy_domain": "mcp", "mcp_server_slug": None},
            "rules": self._rules,
        }]


def _slot(enabled, action="inherit", *, direction="input", target_mode="entire", key_path=""):
    return {"tier": "tier1", "enabled": enabled, "direction": direction, "scope_type": "org",
            "target_mode": target_mode, "key_path": key_path, "action": action,
            "priority": 0, "control_id": 1 if enabled else None}


def _effc(tier1_input, tier1_output):
    return {"scan_controls_configured": True, "tier1_input": tier1_input, "tier1_output": tier1_output,
            "tier2_input": {"tier": "tier2", "enabled": False, "action": "inherit"},
            "tier2_output": {"tier": "tier2", "enabled": False, "action": "inherit"}}


def _redact_rule(scope="entire", key=None, action="redact"):
    cond = {"preset": "aws_access_key", "direction": "input", "scope": scope}
    if key:
        cond["key"] = key
    return {"id": 10, "name": "aws", "rule_type": "regex", "action": action,
            "condition": cond, "redaction_config": {}}


def _block_rule(scope="entire"):
    return {"id": 11, "name": "aws-block", "rule_type": "regex", "action": "block",
            "condition": {"preset": "aws_access_key", "direction": "input", "scope": scope}}


async def _scan(payload, effc, rules, *, direction="input", enforcement="redact"):
    orig = orch._get_policy_sync
    orch._get_policy_sync = lambda: _FakePolicySync(rules)
    try:
        return await orch.scan_mcp_payload(
            payload, scan_direction=direction, enforcement=enforcement,
            effective_controls=effc, tool_name="issue_write", org_slug="o", server_slug="s", actor=None)
    finally:
        orch._get_policy_sync = orig


# ───────────────────────── the leak fix ─────────────────────────
@pytest.mark.asyncio
async def test_entire_policy_redacts_sibling_field_under_keyscoped_scancontrol():
    """THE LEAK: policy redact scope=entire, scan-control key-scoped to arguments.body,
    secret in a SIBLING field (title). The secret must be redacted (policy scope honoured)."""
    ec = _effc(_slot(True, "redact", target_mode="key_path", key_path="arguments.body"), _slot(False, direction="output"))
    payload = {"name": "issue_write", "arguments": {"body": "clean text", "title": f"note {_SEC} end"}}
    scanned, res = await _scan(payload, ec, [_redact_rule()])
    assert res.blocked is False
    assert _SEC not in json.dumps(scanned), "entire-scope redact policy must mask the sibling field"
    assert scanned["arguments"]["body"] == "clean text", "in-key field untouched (no secret there)"
    assert res.compliance_tags, "the policy match must be detected + tagged, not silent"


@pytest.mark.asyncio
async def test_entire_policy_blocks_sibling_field_under_keyscoped_scancontrol():
    """Block variant: an entire-scope BLOCK policy blocks even when the match is outside the
    scan-control's key scope."""
    ec = _effc(_slot(True, "redact", target_mode="key_path", key_path="arguments.body"), _slot(False, direction="output"))
    payload = {"name": "issue_write", "arguments": {"body": "clean", "title": f"note {_SEC}"}}
    scanned, res = await _scan(payload, ec, [_block_rule()])
    assert res.blocked is True, "entire-scope block policy must block a sibling-field match"


@pytest.mark.asyncio
async def test_control_entire_scancontrol_redacts_sibling():
    """Baseline: with an ENTIRE-scope scan-control the sibling field was already redacted."""
    ec = _effc(_slot(True, "redact", target_mode="entire"), _slot(False, direction="output"))
    payload = {"name": "issue_write", "arguments": {"body": "clean", "title": f"note {_SEC}"}}
    scanned, res = await _scan(payload, ec, [_redact_rule()])
    assert res.blocked is False
    assert _SEC not in json.dumps(scanned)


# ───────────────────────── policy's OWN key scope is honoured ─────────────────────────
@pytest.mark.asyncio
async def test_policy_key_scope_masks_only_its_key():
    """A policy scoped to key='title' redacts title; a secret in body is left to the preset
    lane (here presets also redact under the redact scan-control) — the policy's own key
    scope governs the policy lane."""
    ec = _effc(_slot(True, "redact", target_mode="entire"), _slot(False, direction="output"))
    payload = {"name": "issue_write", "arguments": {"title": f"t {_SEC}", "body": "clean body"}}
    scanned, res = await _scan(payload, ec, [_redact_rule(scope="key", key="title")])
    assert res.blocked is False
    assert _SEC not in scanned["arguments"]["title"], "policy key='title' must mask title"


# ───────────────────────── direction isolation preserved ─────────────────────────
@pytest.mark.asyncio
async def test_policy_lane_does_not_run_on_disabled_direction():
    """Direction isolation (last turn's security fix) must survive: with the INPUT direction
    DISABLED by the operator, the policy lane must NOT run on input — the disabled-skip fires
    before it, so an input secret egresses (the operator turned input scanning off)."""
    ec = _effc(_slot(False, direction="input"), _slot(True, "redact", direction="output"))
    payload = {"name": "issue_write", "arguments": {"title": f"note {_SEC}"}}
    scanned, res = await _scan(payload, ec, [_redact_rule()], direction="input")
    assert res.blocked is False
    assert _SEC in json.dumps(scanned), "input direction disabled → policy lane must not enforce input"


@pytest.mark.asyncio
async def test_policy_lane_runs_on_enabled_direction():
    """Twin: with the INPUT direction ENABLED, the same policy DOES enforce."""
    ec = _effc(_slot(True, "redact", direction="input"), _slot(False, direction="output"))
    payload = {"name": "issue_write", "arguments": {"title": f"note {_SEC}"}}
    scanned, res = await _scan(payload, ec, [_redact_rule()], direction="input")
    assert res.blocked is False
    assert _SEC not in json.dumps(scanned)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
