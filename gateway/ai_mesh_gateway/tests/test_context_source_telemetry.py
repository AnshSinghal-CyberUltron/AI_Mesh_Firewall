"""Telemetry sub-tags for SDK mcp_context vs true MCP tool calls."""
from __future__ import annotations

import unittest

from ai_mesh_gateway.main import _resolve_context_source


class ContextSourceTelemetryTests(unittest.TestCase):
    def test_mcp_context_body_tags_mcp(self):
        body = {"messages": [], "mcp_context": {"customer_id": "123"}}
        self.assertEqual(_resolve_context_source(body, None), "mcp")

    def test_agent_data_only_tags_agent_data(self):
        body = {"agent_data": {"ticket": "T-1"}}
        self.assertEqual(_resolve_context_source(body, None), "agent_data")

    def test_mcp_context_wins_over_agent_data(self):
        body = {
            "mcp_context": {"customer_id": "1"},
            "agent_data": {"ticket": "T-1"},
        }
        self.assertEqual(_resolve_context_source(body, None), "mcp")

    def test_raw_body_fallback_after_normalizer_strip(self):
        body = {"messages": []}
        raw = {"mcp_context": {"plan": "enterprise"}}
        self.assertEqual(_resolve_context_source(body, None, raw), "mcp")

    def test_header_only_tags_header(self):
        self.assertEqual(_resolve_context_source({}, "dGVzdA=="), "header")

    def test_plain_chat_has_no_context_source(self):
        self.assertIsNone(_resolve_context_source({"messages": []}, None))


if __name__ == "__main__":
    unittest.main()
