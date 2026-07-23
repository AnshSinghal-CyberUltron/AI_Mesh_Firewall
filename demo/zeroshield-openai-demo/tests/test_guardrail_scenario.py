"""Unit tests for guardrail scenario contract in the demo client."""
from __future__ import annotations

from unittest.mock import patch

from app.gateway_client import ZeroShieldClient
from app.status_reason import REASON_GUARDRAIL_INPUT_BLOCKED


def test_default_guardrail_prompt_vectors():
    assert "Ignore previous instructions" in ZeroShieldClient.default_guardrail_prompt("attack")
    assert "123-45-6789" in ZeroShieldClient.default_guardrail_prompt("sensitive")
    assert "API key" in ZeroShieldClient.default_guardrail_prompt("safe")


def test_scenario_guardrail_probe_uses_respond_and_echoes_vector():
    client = ZeroShieldClient.__new__(ZeroShieldClient)
    prompt = ZeroShieldClient.default_guardrail_prompt("attack")
    with patch.object(
        client,
        "respond",
        return_value={
            "error": True,
            "status": 403,
            "zeroshield": {"action": "block", "blocked_by": "input_scan", "detail": "Prompt injection detected"},
            "pipeline": {"action": "block", "blocked_by": "input_scan"},
        },
    ) as respond:
        out = client.scenario_guardrail_probe(prompt, model="auto", vector="attack")
    respond.assert_called_once_with(prompt, model="auto")
    assert out["guardrail_vector"] == "attack"
    assert out["guardrail_prompt"] == prompt
    assert out["status_reason"]["code"] == REASON_GUARDRAIL_INPUT_BLOCKED


def test_scenario_guardrail_probe_safe_allow():
    client = ZeroShieldClient.__new__(ZeroShieldClient)
    prompt = ZeroShieldClient.default_guardrail_prompt("safe")
    with patch.object(
        client,
        "respond",
        return_value={
            "content": "Store keys in a vault.",
            "model": "auto",
            "zeroshield": {"action": "allow"},
            "pipeline": {"action": "allow", "stages": [{"id": "output_guardrail", "action": "allow"}]},
        },
    ):
        out = client.scenario_guardrail_probe(prompt, model="auto", vector="safe")
    assert out["status_reason"]["code"] == "allowed"
    assert out["guardrail_vector"] == "safe"
