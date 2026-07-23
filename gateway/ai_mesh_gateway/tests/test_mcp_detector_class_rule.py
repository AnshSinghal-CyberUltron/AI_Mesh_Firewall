"""PHASE 2 (2026-07-23) — the ``detector`` rule type.

A single policy rule can invoke the built-in detector SUITE (the full 63-pattern detect_*
engine) for an operator-facing class — so a seeded policy can carry the coverage a server
posture used to provide (collapse-to-one-surface). detector_class ∈ {pii, credential/secret,
ip_leakage, all}. Detection reuses the SAME redact_all_scoped engine the rule redacts with,
so detection and redaction agree by construction — the detect-on-blob/mask-on-leaf asymmetry
that produced the PR#16-20 leak/false-block class cannot recur here.
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
from policy_engine import _resolve_detector_classes, evaluate_mcp_policies  # noqa: E402

_AWS = "AKIAIOSFODNN7EXAMPLE"
_EMAIL = "bob@corp.example"
_IP = "10.9.8.7"


class _FakePolicySync:
    def __init__(self, rules):
        self._rules = rules

    def get_policies_for_server(self, org_slug, server_slug, domain="mcp"):
        return [{"policy": {"id": 1, "code": "P", "name": "P",
                            "policy_domain": "mcp", "mcp_server_slug": None},
                 "rules": self._rules}]


def _slot(enabled, action="inherit", *, direction="input", target_mode="entire", key_path=""):
    return {"tier": "tier1", "enabled": enabled, "direction": direction, "scope_type": "org",
            "target_mode": target_mode, "key_path": key_path, "action": action,
            "priority": 0, "control_id": 1 if enabled else None}


def _effc(action="tag"):
    # server-posture `action` drives the PRESET pass; `tag` = presets observe-only, so a test
    # isolates the DETECTOR policy's own class-scoped masking.
    return {"scan_controls_configured": True,
            "tier1_input": _slot(True, action, target_mode="entire"),
            "tier1_output": _slot(False, direction="output"),
            "tier2_input": {"tier": "tier2", "enabled": False, "action": "inherit"},
            "tier2_output": {"tier": "tier2", "enabled": False, "action": "inherit"}}


def _drule(detector_class, action="redact"):
    return {"id": 1, "name": "d", "rule_type": "detector", "action": action,
            "condition": {"detector_class": detector_class, "direction": "input", "scope": "entire"},
            "redaction_config": {}}


async def _scan(payload, rules, *, posture="tag"):
    orig = orch._get_policy_sync
    orch._get_policy_sync = lambda: _FakePolicySync(rules)
    try:
        return await orch.scan_mcp_payload(
            payload, scan_direction="input", enforcement=posture,
            effective_controls=_effc(posture), tool_name="t", org_slug="o", server_slug="s", actor=None)
    finally:
        orch._get_policy_sync = orig


def test_class_map_resolution():
    assert _resolve_detector_classes("pii") == frozenset({"pii"})
    assert _resolve_detector_classes("secret") == frozenset({"credential"})  # alias
    assert _resolve_detector_classes("credential") == frozenset({"credential"})
    assert _resolve_detector_classes("ip_leakage") == frozenset({"ip_leakage"})
    assert _resolve_detector_classes("all") == frozenset({"pii", "credential", "ip_leakage"})
    assert _resolve_detector_classes("bogus") == frozenset({"pii", "credential", "ip_leakage"})  # safe default
    assert _resolve_detector_classes(["pii", "ip_leakage"]) == frozenset({"pii", "ip_leakage"})


@pytest.mark.asyncio
async def test_detector_all_masks_every_class():
    payload = {"a": {"key": _AWS, "ip": _IP, "email": _EMAIL}}
    scanned, res = await _scan(payload, [_drule("all")], posture="tag")
    blob = json.dumps(scanned)
    assert res.blocked is False
    assert _AWS not in blob and _IP not in blob and _EMAIL not in blob


@pytest.mark.asyncio
async def test_detector_pii_masks_only_pii():
    """Under tag (presets observe-only), detector=pii masks the email but leaves the credential."""
    payload = {"a": {"email": _EMAIL, "key": _AWS}}
    scanned, res = await _scan(payload, [_drule("pii")], posture="tag")
    blob = json.dumps(scanned)
    assert _EMAIL not in blob, "pii class masks the email"
    assert _AWS in blob, "pii class must NOT mask the credential"


@pytest.mark.asyncio
async def test_detector_credential_masks_only_credential():
    payload = {"a": {"email": _EMAIL, "key": _AWS}}
    scanned, res = await _scan(payload, [_drule("credential")], posture="tag")
    blob = json.dumps(scanned)
    assert _AWS not in blob, "credential class masks the key"
    assert _EMAIL in blob, "credential class must NOT mask the email"


@pytest.mark.asyncio
async def test_detector_block_action_blocks():
    payload = {"a": {"key": _AWS}}
    scanned, res = await _scan(payload, [_drule("all", action="block")], posture="tag")
    assert res.blocked is True, "a detector rule authored block must block on a match"


@pytest.mark.asyncio
async def test_detector_masks_numeric_leaf():
    """A credit card sent as a JSON NUMBER is masked by a pii detector rule (numeric-leaf parity)."""
    scanned, res = await _scan({"card": 4111111111111111}, [_drule("pii")], posture="tag")
    assert res.blocked is False
    assert "4111111111111111" not in json.dumps(scanned)


@pytest.mark.asyncio
async def test_detector_benign_is_noop_no_false_block():
    scanned, res = await _scan({"a": {"note": "the weather is sunny"}}, [_drule("all")], posture="tag")
    assert res.blocked is False
    assert "the weather is sunny" in json.dumps(scanned)


@pytest.mark.asyncio
async def test_detector_detection_redaction_agree_no_cannot_mask_block():
    """Because detection and redaction use the SAME engine, a detected detector match is ALWAYS
    maskable → the cannot-mask fail-closed never fires for a detector rule, even under redact."""
    payload = {"a": {"key": _AWS}}
    scanned, res = await _scan(payload, [_drule("all")], posture="redact")
    assert res.blocked is False, "detector detection⟺redaction agree — never a cannot-mask block"
    assert _AWS not in json.dumps(scanned)


def test_per_tool_exemption_downgrades_only_that_tool():
    """Phase 2b #5: a tool-scoped exemption (condition.exempt, target_tool) downgrades a broader
    server-wide enforcing rule to observe-only for THAT tool, while other tools still enforce.
    The additive model otherwise cannot un-enforce a tool. Detection/findings are preserved."""
    from policy_engine import evaluate_mcp_policies
    pols = [{"policy": {"id": 1, "code": "P", "name": "P", "policy_domain": "mcp", "mcp_server_slug": None},
             "rules": [
                 {"id": 1, "action": "redact", "rule_type": "detector",
                  "condition": {"detector_class": "all", "direction": "both", "scope": "entire"}},
                 {"id": 2, "action": "allow", "rule_type": "detector", "target_tool": "search_docs",
                  "condition": {"exempt": True}},
             ]}]
    ctx = {"prompt": "", "response": "", "input_args": {"q": f"key {_AWS}"}, "output_data": None}
    enf = evaluate_mcp_policies(pols, ctx, tool_name="wire_transfer")
    exm = evaluate_mcp_policies(pols, ctx, tool_name="search_docs")
    assert enf.action == "redact", "non-exempt tool still enforces the server-wide rule"
    assert exm.action == "monitor", "exempt tool downgraded to observe-only"
    assert exm.redaction_hints == [], "exempt tool carries no redaction"
    assert exm.matched_rule_ids, "exempt tool still detected (audit trail preserved)"


def test_exemption_is_tool_scoped_never_blanket():
    """An exempt rule with NO target_tool must not blanket-exempt everything."""
    from policy_engine import evaluate_mcp_policies
    pols = [{"policy": {"id": 1, "code": "P", "name": "P", "policy_domain": "mcp", "mcp_server_slug": None},
             "rules": [
                 {"id": 1, "action": "redact", "rule_type": "detector",
                  "condition": {"detector_class": "all", "direction": "both", "scope": "entire"}},
                 {"id": 2, "action": "allow", "rule_type": "detector", "target_tool": "",
                  "condition": {"exempt": True}},
             ]}]
    ctx = {"prompt": "", "response": "", "input_args": {"q": f"key {_AWS}"}, "output_data": None}
    r = evaluate_mcp_policies(pols, ctx, tool_name="anytool")
    assert r.action == "redact", "an untargeted exemption must NOT blanket-downgrade enforcement"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
