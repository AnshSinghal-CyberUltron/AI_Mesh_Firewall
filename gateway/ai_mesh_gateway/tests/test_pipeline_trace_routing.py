"""Pipeline trace routing stage action and branding sanitization."""

from ai_mesh_gateway.pipeline_trace import (
    ROUTING_STAGE_KEYS,
    ZEROSHIELD_ADJUDICATOR_LABEL,
    _sanitize_routing_reason,
    build_pipeline_trace,
    normalize_routing_stage_fields,
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
    assert routing["action"] == "reroute"
    assert routing["requested_model"] == "gpt-5.2"
    assert routing["selected_model"] == "Haiku"
    assert routing["route_destination"] == "llm"
    assert routing["route_destination_label"] == "LLM inference"


def test_full_adjudicator_routing_stage_fields_populated():
    weights = {"cost": 0.25, "latency": 0.35, "quality": 0.4}
    factors = ["compliance=GDPR", "data_sensitivity=confidential", "latency_budget=800ms"]
    trace = build_pipeline_trace(
        route_metadata={
            "original_model": "auto",
            "selected_model": "Haiku",
            "routed_model": "Haiku",
            "route_destination": "llm",
            "routing_reason": "ZeroShield Policy Adjudicator selected 'Haiku' (score=0.87) from 3 candidates.",
            "decision_source": "policy_adjudicator",
            "policy_summary": "Weighted adjudication under org routing policy",
            "decision_factors": factors,
            "weights": weights,
            "routing_score": 0.87,
            "candidate_count": 3,
            "fallback_chain": ["Haiku", "Sonnet"],
            "evaluator_model": "zeroshield-model",
        },
        requested_model="auto",
        stage_metrics={"model_routing_ms": 12.5},
    )
    routing = next(s for s in trace["stages"] if s["name"] == "model_routing")
    for key in ROUTING_STAGE_KEYS:
        assert key in routing, f"missing routing key {key}"
    assert routing["decision_source"] == "policy_adjudicator"
    assert routing["policy_summary"] == "Weighted adjudication under org routing policy"
    assert routing["decision_factors"] == factors
    assert routing["weights"] == weights
    assert routing["routing_score"] == 0.87
    assert routing["candidate_count"] == 3
    assert routing["fallback_chain"] == ["Haiku", "Sonnet"]
    assert routing["evaluator_model"] == "zeroshield-model"
    assert "Factors:" in routing["guard_reason"]
    assert "Weights:" in routing["guard_reason"]
    assert trace["routing"]["routed_model"] == "Haiku"
    assert trace["requested_model"] == "auto"
    assert trace["routing"]["weights"] == weights


def test_normalize_routing_stage_fields_fills_defaults():
    stage = normalize_routing_stage_fields({"name": "model_routing", "action": "allow"})
    for key in ROUTING_STAGE_KEYS:
        assert key in stage
    assert stage["route_destination"] == "llm"
    assert stage["route_destination_label"] == "LLM inference"


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
    assert routing["action"] == "reroute"
    assert routing["decision_source"] == "kill_switch"
