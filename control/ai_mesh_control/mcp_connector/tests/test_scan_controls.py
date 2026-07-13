"""Tests for MCP scan control matrix resolution."""

from django.test import TestCase

from mcp_connector.scan_controls import resolve_effective_controls


class ScanControlResolutionTests(TestCase):
    def test_tool_scope_beats_server_and_org(self):
        rows = [
            {
                "id": "1",
                "tier": "tier1",
                "enabled": True,
                "direction": "input",
                "scope_type": "org",
                "server_id": None,
                "tool_name": "",
                "target_mode": "entire",
                "key_path": "",
                "strict_mode": "fail_open",
                "priority": 10,
            },
            {
                "id": "2",
                "tier": "tier1",
                "enabled": True,
                "direction": "input",
                "scope_type": "server",
                "server_id": "srv-1",
                "tool_name": "",
                "target_mode": "key_path",
                "key_path": "email",
                "strict_mode": "fail_open",
                "priority": 10,
            },
            {
                "id": "3",
                "tier": "tier1",
                "enabled": True,
                "direction": "input",
                "scope_type": "tool",
                "server_id": "srv-1",
                "tool_name": "send_mail",
                "target_mode": "key_path",
                "key_path": "body",
                "strict_mode": "fail_open",
                "priority": 5,
            },
        ]
        effective = resolve_effective_controls(
            rows,
            server_id="srv-1",
            tool_name="send_mail",
        )
        assert effective["scan_controls_configured"] is True
        inp = effective["tier1_input"]
        assert inp["target_mode"] == "key_path"
        assert inp["key_path"] == "body"
        assert inp["control_id"] == "3"

    def test_higher_priority_within_same_scope(self):
        rows = [
            {
                "id": "a",
                "tier": "tier2",
                "enabled": True,
                "direction": "output",
                "scope_type": "org",
                "server_id": None,
                "tool_name": "",
                "target_mode": "entire",
                "key_path": "",
                "strict_mode": "strict",
                "priority": 10,
            },
            {
                "id": "b",
                "tier": "tier2",
                "enabled": False,
                "direction": "output",
                "scope_type": "org",
                "server_id": None,
                "tool_name": "",
                "target_mode": "entire",
                "key_path": "",
                "strict_mode": "fail_open",
                "priority": 200,
            },
        ]
        effective = resolve_effective_controls(rows, server_id=None, tool_name="")
        out = effective["tier2_output"]
        assert out["enabled"] is False
        assert out["control_id"] == "b"

    def test_defaults_when_no_rows(self):
        effective = resolve_effective_controls([], server_id="x", tool_name="t")
        assert effective["scan_controls_configured"] is False
        assert effective["tier1_input"]["enabled"] is True
        assert effective["tier2_input"]["enabled"] is False

    def test_output_only_disables_input_tier(self):
        """F-009: an output-scoped row must not baseline-scan the input side."""
        rows = [
            {
                "id": "out-block",
                "tier": "tier1",
                "enabled": True,
                "direction": "output",
                "scope_type": "org",
                "server_id": None,
                "tool_name": "",
                "target_mode": "entire",
                "key_path": "",
                "strict_mode": "fail_open",
                "action": "block",
                "priority": 10,
            },
        ]
        effective = resolve_effective_controls(rows, server_id="srv-1", tool_name="echo")
        assert effective["scan_controls_configured"] is True
        assert effective["tier1_input"]["enabled"] is False
        assert effective["tier1_output"]["enabled"] is True
        assert effective["tier1_output"]["action"] == "block"
