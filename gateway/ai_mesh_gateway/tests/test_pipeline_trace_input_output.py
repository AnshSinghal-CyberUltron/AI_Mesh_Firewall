"""
PIPELINE-0022: trace-root redacted-safe input/output for operator Input/Output panels.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_mesh_gateway.pipeline_trace import build_pipeline_trace


class _FakeVerdict(SimpleNamespace):
    pass


def test_allow_path_trace_root_io():
    metrics = {
        "auth_ms": 1.0,
        "input_scan_ms": 2.0,
        "model_output_ms": 50.0,
        "output_guardrail_ms": 1.0,
        "total_ms": 60.0,
    }
    trace = build_pipeline_trace(
        prompt="hello world",
        forwarded_prompt="hello world",
        stage_metrics=metrics,
        final_action="allow",
        response_text="Assistant reply here",
    )
    assert trace["final_action"] == "allow"
    assert trace["input_text"] == "hello world"
    assert trace["prompt_submitted"] == "hello world"
    assert trace["output_text"] == "Assistant reply here"
    assert trace["final_response"] == trace["output_text"]
    assert trace["output_withheld"] is False
    assert trace["input_was_redacted"] is False


def test_redact_path_shows_before_after():
    trace = build_pipeline_trace(
        prompt="email user@example.com",
        forwarded_prompt="email u***@example.com",
        policy_redacted_prompt="email u***@example.com",
        policy_redacted_flag=True,
        stage_metrics={"auth_ms": 1.0, "input_scan_ms": 2.0, "model_output_ms": 10.0, "total_ms": 15.0},
        final_action="redact",
        response_text="Sure, noted.",
        zeroshield={"action": "redact", "threat_type": "pii"},
    )
    assert trace["final_action"] == "redact"
    assert trace["input_was_redacted"] is True
    assert trace["input_text_before"] == "email u***@e***.com"
    assert trace["input_text_after"] == "email u***@example.com"
    assert trace["prompt_submitted"] == "email u***@example.com"
    assert trace["output_text"] == "Sure, noted."
    assert trace["output_withheld"] is False


def test_block_path_input_shown_output_withheld():
    trace = build_pipeline_trace(
        prompt="user secret 123-45-6789",
        stage_metrics={"auth_ms": 1.0, "policy_ms": 1.0, "input_scan_ms": 3.0, "total_ms": 6.0},
        final_action="block",
        blocked_stage="input_scan",
        blocked_detail="SSN detected",
        scan_verdict=_FakeVerdict(
            action="block",
            threat_type="pii",
            confidence=0.95,
            tier="tier_1",
            matched_patterns=["ssn"],
            detail="SSN detected",
        ),
    )
    assert trace["final_action"] == "block"
    assert trace["input_text"]
    assert "6789" not in trace["input_text"] or "***" in trace["input_text"]
    assert trace["output_text"] == ""
    assert trace["output_withheld"] is True
    assert "blocked at input scan" in trace["output_withheld_reason"].lower()
    assert trace["prompt_submitted"] == trace["input_text"]


def test_output_guard_block_withholds_response():
    trace = build_pipeline_trace(
        prompt="hello",
        forwarded_prompt="hello",
        stage_metrics={
            "auth_ms": 1.0,
            "input_scan_ms": 2.0,
            "model_output_ms": 80.0,
            "output_guardrail_ms": 2.0,
            "total_ms": 90.0,
        },
        final_action="block",
        blocked_stage="output_guardrail",
        response_text="leaked injection payload",
        zeroshield={"action": "block", "threat_type": "prompt_injection", "detection_tier": "output_guard"},
    )
    assert trace["output_withheld"] is True
    assert trace["output_text"] == "leaked injection payload"
    assert trace["final_response"] == trace["output_text"]
    assert "output guard" in trace["output_withheld_reason"].lower()
