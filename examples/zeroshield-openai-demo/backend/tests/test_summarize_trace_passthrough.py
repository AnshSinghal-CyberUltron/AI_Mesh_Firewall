"""summarize_trace must pass through full stage + pipeline_trace transparency fields."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load_summarize_trace():
    """Load summarize_trace without requiring the openai package at import time."""
    path = ROOT / "zeroshield_client.py"
    if "openai" not in sys.modules:
        stub = ModuleType("openai")

        class _OpenAI:  # noqa: N801
            pass

        class _APIStatusError(Exception):
            pass

        stub.OpenAI = _OpenAI
        stub.APIStatusError = _APIStatusError
        sys.modules["openai"] = stub
    spec = importlib.util.spec_from_file_location("zeroshield_client_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod.summarize_trace


summarize_trace = _load_summarize_trace()


def test_stage_transparency_keys_survive_passthrough():
    body = {
        "zeroshield": {
            "request_id": "zs-demo-1",
            "action": "allow",
            "routing": {
                "original_model": "auto",
                "selected_model": "gpt-4o-mini",
                "routing_reason": "adjudicator pick",
                "decision_source": "policy_adjudicator",
            },
        },
        "pipeline_trace": {
            "final_action": "allow",
            "total_latency_ms": 42.5,
            "ttft_ms": 12.0,
            "input_text": "hello world",
            "output_text": "hi there",
            "input_was_redacted": False,
            "latency_breakdown": {
                "dominant_stage": "model_output",
                "by_stage": [{"stage": "model_output", "latency_ms": 30, "share_pct": 70}],
                "hints": [],
            },
            "stages": [
                {
                    "stage": "input_scan",
                    "action": "allow",
                    "detail": "clean",
                    "latency_ms": 4.5,
                    "guard_reason": "No threat detected",
                    "tier": "tier_1",
                    "confidence": 0.99,
                    "decision_source": "pattern_engine",
                    "decision_source_label": "ZeroShield Pattern Engine",
                    "matched_policies": ["PII"],
                    "matched_rules": ["email"],
                    "prompt_in": "hello world",
                    "prompt_out": "hello world",
                    "threat_type": "none",
                    "scan_outcome": "clean",
                },
                {
                    "name": "model_routing",
                    "action": "allow",
                    "latency_ms": 2.1,
                    "requested_model": "auto",
                    "selected_model": "gpt-4o-mini",
                    "routed_model": "gpt-4o-mini",
                    "route_destination": "llm",
                    "routing_reason": "best fit",
                    "decision_source": "policy_adjudicator",
                    "decision_factors": ["cost_component=0.8"],
                    "weights": {"cost": 0.4},
                    "candidate_count": 3,
                },
            ],
        },
    }
    out = summarize_trace(body)
    assert out["request_id"] == "zs-demo-1"
    assert out["action"] == "allow"
    assert out["processing_time_ms"] == 42.5
    assert out["routing"]["requested"] == "auto"
    assert out["routing"]["selected"] == "gpt-4o-mini"
    assert len(out["stages"]) == 2

    scan = out["stages"][0]
    assert scan["name"] == "input_scan"
    assert scan["guard_reason"] == "No threat detected"
    assert scan["prompt_in"] == "hello world"
    assert scan["matched_policies"] == ["PII"]
    assert scan["tier"] == "tier_1"

    route = out["stages"][1]
    assert route["candidate_count"] == 3
    assert route["weights"]["cost"] == 0.4

    pt = out["pipeline_trace"]
    assert pt["total_latency_ms"] == 42.5
    assert pt["input_text"] == "hello world"
    assert pt["output_text"] == "hi there"
    assert pt["latency_breakdown"]["dominant_stage"] == "model_output"
    assert pt["stages"][0]["guard_reason"] == "No threat detected"


def test_blocked_envelope_preserves_raw_stages():
    body = {
        "zeroshield": {"request_id": "zs-block", "action": "block", "threat_type": "pii"},
        "pipeline_trace": {
            "final_action": "block",
            "total_latency_ms": 8.0,
            "stages": [
                {
                    "name": "policy",
                    "action": "block",
                    "guard_reason": "SSN matched",
                    "matched_rules": ["CISO-012"],
                    "latency_ms": 1.2,
                }
            ],
        },
    }
    out = summarize_trace(body)
    assert out["stages"][0]["guard_reason"] == "SSN matched"
    assert out["stages"][0]["matched_rules"] == ["CISO-012"]
    assert out["pipeline_trace"]["final_action"] == "block"
