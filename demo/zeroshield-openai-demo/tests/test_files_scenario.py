"""Unit tests for files analyze scenario contract in the demo client."""
from __future__ import annotations

from unittest.mock import patch

from app.gateway_client import ZeroShieldClient
from app.status_reason import attach_files_response


def test_build_files_analysis_prompt_includes_document_names():
    prompt = ZeroShieldClient.build_files_analysis_prompt(
        [{"name": "team.csv", "text": "name,role\nAlice,CEO"}],
    )
    assert "team.csv" in prompt
    assert "Alice" in prompt
    assert prompt.startswith("Analyze the following uploaded documents")


def test_build_files_analysis_prompt_caps_length():
    long_text = "x" * 130000
    prompt = ZeroShieldClient.build_files_analysis_prompt([{"name": "big.txt", "text": long_text}])
    assert len(prompt) <= 120000 + 200


def test_scenario_files_analyze_uses_respond_with_files_context():
    client = ZeroShieldClient.__new__(ZeroShieldClient)
    docs = [{"name": "notes.txt", "text": "Quarterly revenue grew 12%."}]
    with patch.object(
        client,
        "respond",
        return_value={"content": "Summary ok", "model": "auto", "zeroshield": {"action": "allow"}},
    ) as respond:
        out = client.scenario_files_analyze(docs, model="auto")
    respond.assert_called_once()
    assert "Quarterly revenue" in respond.call_args[0][0]
    assert out["status_reason"]["code"] == "allowed"


def test_attach_files_response_partial_warnings_keep_allowed():
    payload = attach_files_response(
        {
            "files_manifest": [
                {"name": "team.csv", "status": "ok", "chars": 20},
                {"name": "bad.exe", "status": "error", "error": "unsupported"},
            ],
            "file_warnings": ["bad.exe: Unsupported file type: .exe"],
            "analysis": {
                "content": "Team summary",
                "zeroshield": {"action": "allow"},
                "pipeline": {"action": "allow"},
            },
        }
    )
    assert payload["status_reason"]["code"] == "allowed"
    assert payload["status_reason"]["details"]
    assert payload["analysis"]["status_reason"]["code"] == "allowed"


def test_attach_files_response_total_failure_is_unreadable():
    payload = attach_files_response(
        {
            "files_manifest": [{"name": "bad.exe", "status": "error", "error": "unsupported"}],
            "analysis": None,
            "error": True,
            "status": 400,
        }
    )
    assert payload["status_reason"]["code"] == "file_unreadable"
