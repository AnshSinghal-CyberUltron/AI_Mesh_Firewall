"""Gateway policy engine MCP preset/direction/scope parity tests."""

from policy_engine import EvaluationResult, evaluate_mcp_policies


def _policy_with_rule(rule: dict) -> list[dict]:
    return [{"policy": {"id": 1, "code": "MCP-TEST", "name": "Test"}, "rules": [rule]}]


def test_preset_email_matches_input_args():
    policies = _policy_with_rule(
        {
            "id": 10,
            "name": "email-preset",
            "rule_type": "regex",
            "action": "block",
            "condition": {"preset": "email", "direction": "input", "scope": "entire"},
        }
    )
    ctx = {
        "prompt": "",
        "response": "",
        "input_args": {"contact": "reach me at leak@example.com"},
        "output_data": None,
    }
    result = evaluate_mcp_policies(policies, ctx)
    assert result.action == "block"
    assert 10 in result.matched_rule_ids


def test_key_scope_targets_nested_field():
    policies = _policy_with_rule(
        {
            "id": 11,
            "name": "ssn-key",
            "rule_type": "regex",
            "action": "redact",
            "condition": {
                "preset": "us_ssn",
                "direction": "input",
                "scope": "key",
                "key": "ssn",
            },
            "redaction_config": {},
        }
    )
    ctx = {
        "prompt": "",
        "response": "",
        "input_args": {"ssn": "123-45-6789", "note": "hello"},
        "output_data": None,
    }
    result = evaluate_mcp_policies(policies, ctx)
    assert result.action == "redact"
    assert result.redaction_hints


def test_tool_target_skips_other_tools():
    policies = _policy_with_rule(
        {
            "id": 12,
            "name": "tool-bound",
            "rule_type": "keywords",
            "action": "block",
            "target_tool": "danger_tool",
            "condition": {"keywords": ["secret"], "direction": "both", "scope": "entire"},
        }
    )
    ctx = {"prompt": "my secret plan", "response": "", "input_args": None, "output_data": None}
    miss = evaluate_mcp_policies(policies, ctx, tool_name="safe_tool")
    assert miss.action == "allow"
    hit = evaluate_mcp_policies(policies, ctx, tool_name="danger_tool")
    assert hit.action == "block"
