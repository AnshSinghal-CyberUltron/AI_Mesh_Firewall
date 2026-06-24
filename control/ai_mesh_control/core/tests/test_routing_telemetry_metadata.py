"""Tests for model_routed metadata hoisting in telemetry drain."""

from ai_mesh_control.core.tasks import _build_enforcement_metadata


def test_model_routed_hoists_routing_fields_to_metadata_top_level():
    event = {
        "event_type": "model_routed",
        "model": "gpt-4o-mini",
        "risk_score": 0.12,
        "metadata": {
            "original_model": "gpt-4",
            "routed_model": "gpt-4o-mini",
            "selected_model": "gpt-4o-mini",
            "routing_reason": "cost_optimized",
            "decision_source": "weighted",
            "policy_summary": "Prefer lower cost under risk cap",
        },
    }

    meta = _build_enforcement_metadata(event)

    assert meta["source"] == "routing"
    assert meta["event_type"] == "model_routed"
    assert meta["module_id"] == "1.5"
    assert meta["requested_model"] == "gpt-4"
    assert meta["routed_model"] == "gpt-4o-mini"
    assert meta["routing_reason"] == "cost_optimized"
    assert meta["policy_summary"] == "Prefer lower cost under risk cap"
    assert meta["extra"]["original_model"] == "gpt-4"


def test_build_enforcement_metadata_hoists_scan_detail_fields():
    event = {
        "event_type": "request",
        "action": "allow",
        "prompt_snippet": "hello world",
        "metadata": {
            "request_id": "zs-abc123",
            "pipeline_trace": {"stages": [{"name": "auth", "action": "allow"}]},
            "prompt_submitted": "hello world",
            "response_snippet": "hi there",
        },
    }

    meta = _build_enforcement_metadata(event)

    assert meta["pipeline_trace"]["stages"][0]["name"] == "auth"
    assert meta["incident_id"] == "zs-abc123"
    assert meta["prompt_submitted"] == "hello world"
    assert meta["response_snippet"] == "hi there"
