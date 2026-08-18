"""Task 1: nested Bedrock phase timings on converse (observability only)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ai_mesh_gateway.bedrock_client import BedrockClient


def _bare_client() -> BedrockClient:
    client = BedrockClient.__new__(BedrockClient)
    client.region = "ap-south-1"
    client.model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    client.timeout = 60.0
    return client


def test_converse_returns_phase_timings_and_request_id():
    client = _bare_client()
    raw = {
        "output": {"message": {"content": [{"text": '{"ok":true}'}]}},
        "usage": {"inputTokens": 10, "outputTokens": 4},
        "ResponseMetadata": {"HTTPHeaders": {"x-amzn-request-id": "amzn-abc"}},
    }
    client._client = MagicMock()
    client._client.converse.return_value = raw
    out = BedrockClient.converse(
        client,
        model=client.model_id,
        system_text="sys",
        user_text="user",
        call_site="adjudicator",
        request_id="zs-test-1",
    )
    assert out["ran_inference"] is True
    assert out["tokens_out"] == 4
    assert out["x_amzn_request_id"] == "amzn-abc"
    assert out["gateway_request_id"] == "zs-test-1"
    assert "client_init_ms" in out["phases"]
    assert "converse_ms" in out["phases"]
    assert "parse_ms" in out["phases"]
    assert out["phases"]["converse_ms"] >= 0
    assert out["phases"]["parse_ms"] >= 0
    assert out["phases"]["client_init_ms"] == 0.0
    assert out["raw"]
    assert "choices" in out["raw"]
    assert out["tokens_in"] == 10
    assert out["elapsed_s"] >= 0
    assert out["model_id"] == client.model_id
    assert out["call_site"] == "adjudicator"
    assert out["api_method"] == "converse"


def test_converse_missing_response_metadata_yields_empty_amzn_id():
    client = _bare_client()
    raw = {
        "output": {"message": {"content": [{"text": '{"ok":true}'}]}},
        "usage": {"inputTokens": 1, "outputTokens": 2},
    }
    client._client = MagicMock()
    client._client.converse.return_value = raw
    out = BedrockClient.converse(
        client,
        model=client.model_id,
        system_text="sys",
        user_text="user",
        call_site="tier2_scan",
        request_id="zs-test-2",
    )
    assert out["ran_inference"] is True
    assert out["x_amzn_request_id"] == ""
    assert out["gateway_request_id"] == "zs-test-2"
    assert out["tokens_out"] == 2
    assert "converse_ms" in out["phases"]


def test_converse_reads_mixed_case_amzn_request_id_header():
    client = _bare_client()
    raw = {
        "output": {"message": {"content": [{"text": "ok"}]}},
        "usage": {"inputTokens": 1, "outputTokens": 1},
        "ResponseMetadata": {"HTTPHeaders": {"X-Amzn-Request-Id": "amzn-MIXED"}},
    }
    client._client = MagicMock()
    client._client.converse.return_value = raw
    out = BedrockClient.converse(
        client,
        model=client.model_id,
        system_text="sys",
        user_text="user",
        call_site="output_guard",
        request_id="zs-test-3",
    )
    assert out["ran_inference"] is True
    assert out["x_amzn_request_id"] == "amzn-MIXED"


def test_converse_amzn_request_id_falls_back_to_response_metadata_request_id():
    client = _bare_client()
    raw = {
        "output": {"message": {"content": [{"text": "ok"}]}},
        "usage": {"inputTokens": 1, "outputTokens": 1},
        "ResponseMetadata": {"RequestId": "from-meta"},
    }
    client._client = MagicMock()
    client._client.converse.return_value = raw
    out = BedrockClient.converse(
        client,
        model=client.model_id,
        system_text="sys",
        user_text="user",
        call_site="adjudicator",
        request_id="zs-test-4",
    )
    assert out["x_amzn_request_id"] == "from-meta"


def test_converse_logs_json_phase_object_without_prompt():
    client = _bare_client()
    raw = {
        "output": {"message": {"content": [{"text": '{"ok":true}'}]}},
        "usage": {"inputTokens": 2, "outputTokens": 5},
        "ResponseMetadata": {"HTTPHeaders": {"x-amzn-request-id": "amzn-log"}},
    }
    client._client = MagicMock()
    client._client.converse.return_value = raw
    user_text = "SECRET_USER_PROMPT_DO_NOT_LOG"
    required = (
        "call_site",
        "gateway_request_id",
        "x_amzn_request_id",
        "tokens_out",
        "client_init_ms",
        "converse_ms",
        "parse_ms",
    )
    with patch("ai_mesh_gateway.bedrock_client.LOG.info") as mock_info:
        BedrockClient.converse(
            client,
            model=client.model_id,
            system_text="sys",
            user_text=user_text,
            call_site="adjudicator",
            request_id="zs-json-1",
        )
    json_msgs = []
    for call in mock_info.call_args_list:
        if not call.args:
            continue
        msg = call.args[0]
        if not isinstance(msg, str):
            continue
        try:
            payload = json.loads(msg)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            json_msgs.append((msg, payload))
    assert json_msgs, "expected one JSON phase INFO log"
    msg, payload = json_msgs[0]
    for key in required:
        assert key in payload
    assert user_text not in msg
    assert user_text not in json.dumps(payload)
