"""LANE3 repro: a scope=key REDACT rule whose pattern matches nested structure /
key-names (which no single value leaf reproduces) hits the cannot-mask fail-closed
and BLOCKS — "redact" becomes "block". The value leaves are BENIGN (the match was
structural), so blocking is a frozen-invariant violation, not a leak defense.
"""
import pytest
import mcp_scan_orchestrator as orch
from policy_engine import EvaluationResult


# scope=key redact hint whose regex matches a nested KEY NAME serialized under the key,
# but never a string VALUE leaf -> _redact_structured_leaves cannot change anything.
STRUCTURAL_HINT = {
    "config": {"regex": r"password_field"},
    "condition": {"scope": "key", "key": "config"},
    "scope": "key",
    "key": "config",
}
PAYLOAD = {"config": {"password_field": "benign-nonsecret-value"}, "other": "keepme"}


def _run(enforcement):
    ev = EvaluationResult(
        action="redact",
        matched_rule_ids=[1],
        redaction_hints=[STRUCTURAL_HINT],
    )
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(orch, "evaluate_mcp_policies", lambda *a, **k: ev)
        return orch._mcp_policy_pass_sync(
            PAYLOAD,
            policies=[{"id": 1}],
            serialized=orch._safe_json(PAYLOAD),
            scan_direction="output",
            enforcement=enforcement,
            tool_name="echo",
            actor=None,
        )


def test_lane3_scope_key_redact_does_not_hardblock():
    new_payload, findings, blocked, rfields, redacted = _run("redact")
    # THE fix: redact never escalates to block.
    assert blocked is False, "redact rule scope=key hard-blocked (LANE3) — must forward"
    # Benign value forwarded (nothing to mask; the match was structural).
    assert new_payload["config"]["password_field"] == "benign-nonsecret-value"
    assert new_payload["other"] == "keepme"


def test_lane3_real_value_secret_still_masked():
    # Guard the other agent asked for: a REAL secret in a value leaf under the key IS
    # masked (detection<->redaction share redact_all_scoped), so nothing leaks raw.
    ev = EvaluationResult(
        action="redact", matched_rule_ids=[1],
        redaction_hints=[{"config": {"detector_class": ["credential"]},
                          "condition": {"scope": "key", "key": "config"},
                          "scope": "key", "key": "config"}],
    )
    payload = {"config": {"token": "AKIAIOSFODNN7EXAMPLE"}, "other": "keepme"}
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(orch, "evaluate_mcp_policies", lambda *a, **k: ev)
        new_payload, findings, blocked, rfields, redacted = orch._mcp_policy_pass_sync(
            payload, policies=[{"id": 1}], serialized=orch._safe_json(payload),
            scan_direction="output", enforcement="redact", tool_name="echo", actor=None,
        )
    assert blocked is False
    assert "AKIAIOSFODNN7EXAMPLE" not in orch._safe_json(new_payload), "real secret leaked!"
    assert new_payload["other"] == "keepme"  # scope fidelity: sibling untouched


def test_lane3_nested_list_secret_masked_through_not_blocked():
    # "nested secrets still caught": a real credential nested in a LIST under the
    # scope=key path is masked THROUGH the list by the policy leaf-walk (lists are
    # transparent to the key path) — forwarded masked, never blocked, never raw.
    ev = EvaluationResult(
        action="redact", matched_rule_ids=[1],
        redaction_hints=[{"config": {"detector_class": ["credential"]},
                          "condition": {"scope": "key", "key": "config"},
                          "scope": "key", "key": "config"}],
    )
    payload = {"config": {"items": [{"k": "AKIAIOSFODNN7EXAMPLE"}]}, "other": "keepme"}
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(orch, "evaluate_mcp_policies", lambda *a, **k: ev)
        new_payload, findings, blocked, rfields, redacted = orch._mcp_policy_pass_sync(
            payload, policies=[{"id": 1}], serialized=orch._safe_json(payload),
            scan_direction="output", enforcement="redact", tool_name="echo", actor=None,
        )
    assert blocked is False
    assert "AKIAIOSFODNN7EXAMPLE" not in orch._safe_json(new_payload), "list-nested secret leaked!"
    assert new_payload["other"] == "keepme"


def test_lane3_block_rule_still_blocks():
    # A rule the operator AUTHORED as block still blocks — unchanged.
    ev = EvaluationResult(action="block", matched_rule_ids=[1])
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(orch, "evaluate_mcp_policies", lambda *a, **k: ev)
        _np, _f, blocked, _rf, _r = orch._mcp_policy_pass_sync(
            PAYLOAD, policies=[{"id": 1}], serialized=orch._safe_json(PAYLOAD),
            scan_direction="output", enforcement="redact", tool_name="echo", actor=None,
        )
    assert blocked is True
