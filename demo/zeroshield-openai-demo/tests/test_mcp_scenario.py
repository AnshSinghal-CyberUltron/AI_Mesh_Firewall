"""Unit tests for MCP scenario contract in the demo client."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.gateway_client import ZeroShieldClient


def test_default_mcp_context_shape():
    ctx = ZeroShieldClient.default_mcp_context("VAL-1")
    assert ctx["customer_id"] == "VAL-1"
    assert ctx["profile"]["name"] == "Acme Corp"


def test_scenario_mcp_uses_respond_and_passes_context():
    client = ZeroShieldClient.__new__(ZeroShieldClient)
    custom = {"customer_id": "VAL-42", "profile": {"name": "Validation Corp"}}
    with patch.object(client, "respond", return_value={"content": "ok", "model": "auto"}) as respond:
        out = client.scenario_mcp("Summarize customer", mcp_context=custom, model="auto")
    respond.assert_called_once_with("Summarize customer", model="auto", mcp_context=custom)
    assert out["mcp_context"] == custom
    assert out["status_reason"]["code"] == "allowed"
