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
