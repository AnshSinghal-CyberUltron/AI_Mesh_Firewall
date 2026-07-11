"""Contract tests for SDK scenario catalog and metadata attachment."""
from __future__ import annotations

from unittest.mock import patch

from app.gateway_client import ZeroShieldClient
from app.sdk_scenarios import (
    SDK_SCENARIO_CATALOG,
    attach_sdk_scenario_meta,
    get_sdk_scenario,
    list_sdk_scenarios_public,
)


def test_catalog_contains_six_scenarios_with_patterns():
    assert set(SDK_SCENARIO_CATALOG) == {"basic", "stream", "rag", "mcp", "routing", "guardrail"}
    for sid in SDK_SCENARIO_CATALOG:
        entry = SDK_SCENARIO_CATALOG[sid]
        assert entry["sdk_pattern"]
        assert entry["label"]
        assert entry["api_path"]
    routing = get_sdk_scenario("routing")
    assert routing["request_body"]["scenario"] == "routing"
    assert "routing_preferences" in routing["request_body"]


def test_list_sdk_scenarios_public_shape():
    public = list_sdk_scenarios_public()
    assert len(public) == 6
    assert all("sdk_pattern" in row and "id" in row for row in public)
    assert all("request_body" not in row for row in public)


def test_attach_sdk_scenario_meta_adds_fields():
    view = {"content": "hello"}
    out = attach_sdk_scenario_meta(view, "basic")
    assert out["sdk_scenario"] == "basic"
    assert out["sdk_label"] == SDK_SCENARIO_CATALOG["basic"]["label"]
    assert out["sdk_pattern"] == SDK_SCENARIO_CATALOG["basic"]["sdk_pattern"]
    assert out["sdk_code_snippet"]


def test_attach_sdk_scenario_meta_prefers_answer_status_reason():
    view = {
        "retrieval": {"status_reason": {"code": "rag_access_denied", "message": "no policy"}},
        "answer": {"status_reason": {"code": "allowed", "message": "ok"}},
    }
    out = attach_sdk_scenario_meta(view, "rag")
    assert out["status_reason"]["code"] == "allowed"


def test_scenario_basic_chat_echoes_sdk_meta():
    client = ZeroShieldClient.__new__(ZeroShieldClient)
    with patch.object(
        client,
        "respond",
        return_value={"content": "Quantum is cool.", "model": "auto", "zeroshield": {"action": "allow"}},
    ):
        out = client.scenario_basic_chat("hi", model="auto")
    assert out["sdk_scenario"] == "basic"
    assert out["sdk_pattern"]


def test_scenario_rag_echoes_sdk_meta_on_error():
    client = ZeroShieldClient.__new__(ZeroShieldClient)
    with patch.object(
        client,
        "rag_query",
        return_value={
            "error": True,
            "status": 403,
            "status_reason": {"code": "rag_access_denied", "message": "denied"},
        },
    ):
        out = client.scenario_rag("demo_knowledge", "q", model="auto")
    assert out["sdk_scenario"] == "rag"
    assert out["error"] is True


def test_sdk_examples_scenario_5_uses_responses_create():
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "scripts" / "sdk_examples.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "scenario_5_routing":
            text = ast.get_source_segment(src.read_text(encoding="utf-8"), node) or ""
            assert "responses.create" in text
            assert "chat.completions" not in text
            assert "routing_preferences" in text
            return
    raise AssertionError("scenario_5_routing not found")
