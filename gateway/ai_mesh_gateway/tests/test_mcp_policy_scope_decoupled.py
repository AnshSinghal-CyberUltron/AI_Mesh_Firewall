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


# ───────── structured redaction: no serialize/reparse JSON corruption (2026-07-23) ─────────
# REGRESSION: the policy pass used to serialize the whole payload to JSON, apply_redaction
# (a blind regex.sub) over the STRING, then json.loads it back. A greedy/non-anchored redact
# regex (or a replacement containing a quote) produced invalid JSON, and the setter silently
# stored the raw corrupted STRING as the payload — which (a) forwarded a garbled string where
# the JSON-RPC arguments object belongs and (b) collapsed the key-scoped preset + Tier-2
# passes to ZERO targets, silently disabling ALL downstream scanning. Both hunters reproduced
# it. Fix: redact string leaves IN PLACE (structure preserved; a str stays a str).
def _greedy_rule():
    # non-anchored regex that, applied to a serialized JSON blob, would swallow a delimiter
    return {"id": 20, "name": "auth", "rule_type": "regex", "action": "redact",
            "condition": {"regex": r"Bearer\s+.*", "direction": "input", "scope": "entire"},
            "redaction_config": {"replacement": "[REDACTED]"}}


@pytest.mark.asyncio
async def test_greedy_policy_redaction_does_not_corrupt_payload_or_skip_presets():
    """A greedy redact regex must NOT corrupt the payload into a string nor skip the preset
    pass. The auth header is masked (own leaf only) AND the SSN in a key-scoped sibling is
    still scanned + redacted by the preset pass (payload stayed a dict)."""
    ec = _effc(_slot(True, "redact", target_mode="key_path", key_path="title"), _slot(False, direction="output"))
    payload = {"auth_header": "Bearer sk_live_abcdef1234567890", "title": "urgent SSN 123-45-6789"}
    scanned, res = await _scan(payload, ec, [_greedy_rule()], enforcement="tag")
    assert isinstance(scanned, dict), "payload structure must be preserved (not a corrupted string)"
    assert res.blocked is False
    assert "Bearer sk_live" not in json.dumps(scanned), "auth header masked in its own leaf"
    assert "123-45-6789" not in json.dumps(scanned), "sibling SSN still scanned by the preset pass"
    assert scanned["title"].startswith("urgent SSN"), "only the SSN masked, structure/text intact"


@pytest.mark.asyncio
async def test_quote_replacement_does_not_corrupt_payload():
    """An operator replacement containing a double-quote must not break JSON structure."""
    rule = {"id": 21, "name": "email", "rule_type": "regex", "action": "redact",
            "condition": {"preset": "email", "direction": "input", "scope": "entire"},
            "redaction_config": {"replacement": 'X"X'}}
    ec = _effc(_slot(True, "redact", target_mode="key_path", key_path="note"), _slot(False, direction="output"))
    payload = {"contact": "reach me at foo@bar.com", "note": "clean"}
    scanned, res = await _scan(payload, ec, [rule])
    assert isinstance(scanned, dict), "quote in replacement must not corrupt structure"
    assert "foo@bar.com" not in json.dumps(scanned)


# ───────── numeric leaves: detected-but-not-redacted silent leak (2026-07-23) ─────────
# REGRESSION: _redact_structured_leaves originally only masked isinstance(node, str) leaves.
# A secret transmitted as a JSON NUMBER (SSN/card/account as an integer) is DETECTED
# (detection runs on the serialized payload) and reported redacted, but the number was
# never masked → raw egress while findings claim a redact fired. Found by an adversarial
# review. Fix: stringify + redact numeric scalars, mask only if a hint matched.
@pytest.mark.asyncio
async def test_numeric_leaf_secret_is_masked_not_silently_passed():
    rule = {"id": 30, "name": "ssn9", "rule_type": "regex", "action": "redact",
            "condition": {"regex": r"\d{9}", "scope": "key", "key": "ssn", "direction": "input"},
            "redaction_config": {}}
    ec = _effc(_slot(True, "redact", target_mode="entire"), _slot(False, direction="output"))
    payload = {"name": "issue_write", "arguments": {"ssn": 123456789, "note": "clean"}}
    scanned, res = await _scan(payload, ec, [rule])
    assert res.blocked is False
    assert isinstance(scanned, dict)
    assert "123456789" not in json.dumps(scanned), "numeric SSN must be masked, not egress raw"
    assert scanned["arguments"]["note"] == "clean", "unrelated field untouched"


@pytest.mark.asyncio
async def test_unmatched_number_keeps_numeric_type():
    """A number that no hint matches must keep its original numeric type (not be coerced)."""
    rule = {"id": 31, "name": "aws", "rule_type": "regex", "action": "redact",
            "condition": {"preset": "aws_access_key", "direction": "input", "scope": "entire"},
            "redaction_config": {}}
    ec = _effc(_slot(True, "redact", target_mode="entire"), _slot(False, direction="output"))
    payload = {"name": "issue_write", "arguments": {"count": 42, "note": f"key {_SEC}"}}
    scanned, res = await _scan(payload, ec, [rule])
    assert scanned["arguments"]["count"] == 42, "unmatched number keeps its int type"
    assert isinstance(scanned["arguments"]["count"], int)
    assert _SEC not in json.dumps(scanned)


# ───── cannot-mask fail-closed: detected-but-unmaskable must withhold, not leak ─────
# ROOT CAUSE (adversarial review): the policy lane has NO downstream backstop — its findings
# carry threat_type='redact', which the E12 result floor's _findings_have_secret_or_pii never
# matches. So a redact rule that MATCHES detection (on the serialized payload) but that the
# leaf-walk cannot mask (a JSON-structural/cross-boundary regex, or a payload nested past the
# depth cap) would forward RAW while claiming a redact fired. Fix: fail closed (cannot-mask
# exception) rather than leak — WITHOUT escalating maskable content (which still redacts).
@pytest.mark.asyncio
async def test_cross_boundary_structural_regex_is_a_noop_not_a_false_block():
    """entire-scope detection scans VALUE LEAVES, not the serialized blob. A structural regex
    that needs the ``"key":"val"`` JSON context therefore matches NO value leaf → it is a
    no-op: no false 'redacted' claim and, crucially, no false-block of benign traffic. (Real
    secret VALUES are caught by their own value pattern / the preset + detector-class lanes;
    a structural regex is an anti-pattern the value-based model deliberately no-ops.)"""
    rule = {"id": 40, "name": "kv", "rule_type": "regex", "action": "redact",
            "condition": {"regex": r'"internal_api_key"\s*:\s*"[^"]*"', "direction": "input", "scope": "entire"},
            "redaction_config": {}}
    ec = _effc(_slot(True, "redact", target_mode="key_path", key_path="nonexistent"), _slot(False, direction="output"))
    payload = {"internal_api_key": "sk-live-abc123XYZ", "note": "clean"}
    scanned, res = await _scan(payload, ec, [rule])
    assert res.blocked is False, "a structural regex matching no value leaf must not false-block"


@pytest.mark.asyncio
async def test_value_pattern_regex_masks_the_secret_value():
    """Twin of the above: the CORRECT rule shape (a value pattern) matches the value leaf and
    masks it — proving the value-based model redacts real secrets, just not structural regexes."""
    rule = {"id": 41, "name": "sk", "rule_type": "regex", "action": "redact",
            "condition": {"regex": r"sk-live-\w+", "direction": "input", "scope": "entire"},
            "redaction_config": {}}
    ec = _effc(_slot(True, "redact", target_mode="key_path", key_path="nonexistent"), _slot(False, direction="output"))
    payload = {"internal_api_key": "sk-live-abc123XYZ", "note": "clean"}
    scanned, res = await _scan(payload, ec, [rule])
    assert res.blocked is False
    assert "sk-live-abc123XYZ" not in json.dumps(scanned), "value-pattern rule masks the secret value"


@pytest.mark.asyncio
async def test_keyword_matching_only_a_key_name_does_not_false_block():
    """F1 regression: a redact keyword that coincides with a JSON KEY NAME (arguments/password/
    url) must NOT match (keys aren't scanned) and must NOT block benign traffic."""
    rule = {"id": 42, "name": "kw", "rule_type": "keywords", "action": "redact",
            "condition": {"keywords": ["arguments"], "direction": "input", "scope": "entire"},
            "redaction_config": {}}
    ec = _effc(_slot(True, "redact", target_mode="entire"), _slot(False, direction="output"))
    payload = {"name": "issue_write", "arguments": {"title": "benign weather report"}}
    scanned, res = await _scan(payload, ec, [rule], enforcement="tag")
    assert res.blocked is False, "keyword matching only a key name must not false-block"
    assert "benign weather report" in json.dumps(scanned), "benign content forwarded intact"


@pytest.mark.asyncio
async def test_deeply_nested_unmaskable_redact_fails_closed():
    """A secret nested past the redaction depth cap is detected but the leaf-walk stops short
    → must fail closed rather than forward the deep subtree raw."""
    deep = {"x": _SEC}
    for _ in range(210):
        deep = {"n": deep}
    rule = _redact_rule()  # aws_access_key, scope=entire
    ec = _effc(_slot(True, "redact", target_mode="key_path", key_path="nonexistent"), _slot(False, direction="output"))
    scanned, res = await _scan(deep, ec, [rule])
    assert res.blocked is True, "unmaskable deep-nested redact match must fail closed"


@pytest.mark.asyncio
async def test_maskable_redact_still_forwards_not_blocked():
    """Control: ordinary maskable content redacts + forwards (the cannot-mask fail-closed must
    NOT escalate maskable redactions to a block)."""
    ec = _effc(_slot(True, "redact", target_mode="entire"), _slot(False, direction="output"))
    payload = {"name": "issue_write", "arguments": {"title": f"note {_SEC} end"}}
    scanned, res = await _scan(payload, ec, [_redact_rule()])
    assert res.blocked is False, "maskable content must redact + forward, never block"
    assert _SEC not in json.dumps(scanned)


# ───── Bug B: keyword detection ⟺ redaction word-boundary agreement ─────
def _kw_rule(word, action="redact"):
    return {"id": 50, "name": "kw", "rule_type": "keywords", "action": action,
            "condition": {"keywords": [word], "direction": "input", "scope": "entire"},
            "redaction_config": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("posture", ["tag", "redact", "block"])
async def test_keyword_substring_inside_word_does_not_false_block(posture):
    """A keyword that appears only as a SUBSTRING inside a larger word (secret→secretary) must
    NOT match — detection is word-bounded like the redactor, so no false 'unmaskable' block."""
    ec = _effc(_slot(True, "inherit", target_mode="entire"), _slot(False, direction="output"))
    payload = {"name": "notify", "arguments": {"note": "The secretary secretly filed it."}}
    scanned, res = await _scan(payload, ec, [_kw_rule("secret")], enforcement=posture)
    assert res.blocked is False, f"substring-only keyword must not false-block ({posture})"
    assert "secretary secretly" in json.dumps(scanned), "benign content forwarded intact"


@pytest.mark.asyncio
async def test_standalone_keyword_still_masks():
    """Control: a standalone keyword still matches (word-bounded) and is masked."""
    ec = _effc(_slot(True, "inherit", target_mode="entire"), _slot(False, direction="output"))
    payload = {"arguments": {"note": "the secret plan is ready"}}
    scanned, res = await _scan(payload, ec, [_kw_rule("secret")], enforcement="redact")
    assert res.blocked is False
    assert "secret" not in json.dumps(scanned), "standalone keyword masked"


# ───── Q4: tag/monitor (observe-only) never block, even on a genuine unmaskable match ─────
@pytest.mark.asyncio
async def test_unmaskable_match_forwards_under_tag_not_blocks():
    """Under the default observe-only ``tag`` posture, a genuinely-unmaskable redact match
    (secret nested past the depth cap) is FORWARDED, never blocked — 'tag never blocks'."""
    deep = {"x": _SEC}
    for _ in range(210):
        deep = {"n": deep}
    ec = _effc(_slot(True, "inherit", target_mode="entire"), _slot(False, direction="output"))
    scanned, res = await _scan(deep, ec, [_redact_rule()], enforcement="tag")
    assert res.blocked is False, "tag posture must never block, even on an unmaskable match"


@pytest.mark.asyncio
async def test_unmaskable_match_fails_closed_under_redact():
    """Twin: under an ENFORCING posture the same unmaskable match DOES fail closed."""
    deep = {"x": _SEC}
    for _ in range(210):
        deep = {"n": deep}
    ec = _effc(_slot(True, "inherit", target_mode="entire"), _slot(False, direction="output"))
    scanned, res = await _scan(deep, ec, [_redact_rule()], enforcement="redact")
    assert res.blocked is True, "enforcing posture fails closed on an unmaskable match"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
