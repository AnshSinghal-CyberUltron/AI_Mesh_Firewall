"""scope=key redact must never hard-block on a KEY-NAME / structural match (red-team LANE3).

A ``scope=key`` redact rule detected on the serialized subtree blob (``_collect_key_values`` ->
``_safe_json(node)``), which INCLUDES key names and JSON punctuation, while its REDACTION only
masks value leaves. So a redact rule whose keyword/regex hit a KEY NAME matched in DETECTION but
changed nothing in the leaf walk (``changed=False``) — and the cannot-mask fail-closed escalated the
operator's ``redact`` selection into a hard BLOCK. Because ``changed=False`` means the match was
purely structural (no value leaf carried it), nothing sensitive survives, so the block was always
spurious. It was also remotely weaponizable: any scope=key redact rule present, plus an attacker-
controlled key name in a tool result/args, forces a DoS block.

Fix: scope=key detection now scans VALUE leaves only (``_leaf_value_texts``), identical to the
already-fixed scope=entire path — detection agrees with redaction. This file locks: (1) a key-name
match no longer blocks and forwards unchanged; (2) a real secret in the VALUE under the scoped key
is STILL masked (no leak, no regression); (3) scope=entire remains immune.

Run: cd gateway && .venv/bin/python -m pytest \
    ai_mesh_gateway/tests/test_mcp_keyscope_no_hardblock.py -q -p no:cacheprovider
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_scan_orchestrator as O  # noqa: E402
from mcp_scan_orchestrator import _safe_json  # noqa: E402


def _run(payload, rule, enforcement):
    pol = [{"policy": {"id": 5, "code": "P", "name": "p", "severity": "low"}, "rules": [rule]}]
    out, findings, blocked, redacted, meta = O._mcp_policy_pass_sync(
        payload, policies=pol, serialized=_safe_json(payload),
        scan_direction="output", enforcement=enforcement, tool_name="", actor=None)
    return out, blocked


def _rule(scope, key, *, regex=None, keywords=None, action="redact"):
    cond = {"direction": "output", "scope": scope, "key": key}
    if regex is not None:
        cond["regex"] = regex
    if keywords is not None:
        cond["keywords"] = keywords
    return {"id": 55, "name": "r", "rule_type": "regex" if regex else "keywords",
            "action": action, "condition": cond}


@pytest.mark.parametrize("enforcement", ["redact", "block"])
@pytest.mark.parametrize("matcher", [{"regex": "secret_field"}, {"keywords": ["secret_field"]},
                                     {"regex": "secret"}], ids=["regex", "keyword", "partial-regex"])
def test_keyname_match_under_redact_does_not_hardblock(matcher, enforcement):
    """A scope=key rule whose pattern matches a KEY NAME masks nothing (the value is benign) and
    must NOT block under redact — the match is purely structural, so nothing sensitive survives.
    Under an operator-selected ``block`` the rule is free to block (that is the operator's choice)."""
    payload = {"data": {"secret_field": "hello world"}}
    out, blocked = _run(payload, _rule("key", "data", **matcher), enforcement)
    if enforcement == "redact":
        assert blocked is False, "a redact selection must never escalate to a hard block on a key-name match"
        assert out == {"data": {"secret_field": "hello world"}}, "benign value forwarded unchanged"


def test_real_value_secret_still_masked_under_keyscope_redact():
    """The fix must not regress masking: a secret in the VALUE under the scoped key is still
    detected and masked (redact means redact), forwarded, never blocked, never leaked raw."""
    payload = {"data": {"x": "my key AKIAIOSFODNN7EXAMPLE end"}}
    out, blocked = _run(payload, _rule("key", "data", regex="AKIA[0-9A-Z]{16}"), "redact")
    assert blocked is False
    assert "AKIAIOSFODNN7EXAMPLE" not in _safe_json(out), "the value secret must be masked, not leaked"


def test_entire_scope_still_immune():
    """Control: scope=entire was already value-leaf; a key-name match there also never blocks."""
    payload = {"data": {"secret_field": "hello world"}}
    _out, blocked = _run(payload, _rule("entire", "", regex="secret_field"), "redact")
    assert blocked is False
