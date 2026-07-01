"""Pipeline trace routing stage action and branding sanitization."""

from ai_mesh_gateway.pipeline_trace import (
    ZEROSHIELD_ADJUDICATOR_LABEL,
    _sanitize_routing_reason,
    build_pipeline_trace,
)


def test_model_routing_reroute_when_models_differ():
    trace = build_pipeline_trace(
        route_metadata={
            "original_model": "gpt-5.2",
            "selected_model": "Haiku",
            "routed_model": "Haiku",
            "rerouted": True,
            "routing_reason": "ZeroShield Policy Adjudicator selected 'Haiku'",
            "decision_source": "policy_adjudicator",
        },
        requested_model="gpt-5.2",
    )
    routing = next(s for s in trace["stages"] if s["name"] == "model_routing")
    assert routing["action"] == "allow"
    assert routing["requested_model"] == "gpt-5.2"
    assert routing["selected_model"] == "Haiku"


def test_sanitize_routing_reason_replaces_bedrock_branding():
    raw = (
        "Bedrock GPT OSS 120B adjudicator selected 'Haiku' "
        "(score=0.42) from 2 candidates."
    )
    sanitized = _sanitize_routing_reason(raw)
    assert "Bedrock" not in sanitized
    assert ZEROSHIELD_ADJUDICATOR_LABEL in sanitized

    trace = build_pipeline_trace(
        route_metadata={
            "original_model": "gpt-5.2",
            "selected_model": "Haiku",
            "rerouted": True,
            "routing_reason": raw,
            "decision_source": "policy_adjudicator",
        },
    )
    routing = next(s for s in trace["stages"] if s["name"] == "model_routing")
    assert "Bedrock" not in routing["routing_reason"]
    assert ZEROSHIELD_ADJUDICATOR_LABEL in routing["routing_reason"]


def test_kill_switch_stage_reroute_from_route_metadata():
    trace = build_pipeline_trace(
        route_metadata={
            "original_model": "gpt-5.2",
            "selected_model": "Haiku",
            "rerouted": True,
            "trigger_source": "kill_switch",
            "decision_source": "kill_switch",
            "routing_reason": "Kill-switch reroute: gpt-5.2 → Haiku. Operator disabled model",
        },
        requested_model="gpt-5.2",
    )
    ks = next(s for s in trace["stages"] if s["name"] == "kill_switch")
    routing = next(s for s in trace["stages"] if s["name"] == "model_routing")
    assert ks["action"] == "reroute"
    assert "Kill-switch" in ks["detail"] or "kill-switch" in ks["detail"].lower()
    assert routing["action"] == "allow"
    assert routing["decision_source"] == "kill_switch"


def test_auto_model_resolution_is_allow_not_reroute():
    trace = build_pipeline_trace(
        route_metadata={
            "original_model": "auto",
            "selected_model": "Haiku",
            "routed_model": "Haiku",
            "rerouted": True,
            "routing_reason": "Default model selected",
            "decision_source": "policy_adjudicator",
        },
        requested_model="auto",
    )
    routing = next(s for s in trace["stages"] if s["name"] == "model_routing")
    assert routing["action"] == "allow"
