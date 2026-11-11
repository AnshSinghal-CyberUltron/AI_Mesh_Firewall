"""
PIPELINE-0035 / L14b-2 + C14: executed-vs-skipped is a PROPERTY OF THE RECORD.

Every pipeline_trace stage carries an explicit boolean ``ran`` so the Scan Detail UI
renders executed/skipped from the record instead of recomputing it in the browser
(e.g. from ``latency_ms === 0``, which is ambiguous — a genuine skip AND a sub-ms scan
both round to 0).

The governing scenario (user/screenshot-confirmed): a scan-only (no model call) request
with PII that the POLICY stage redacted, which then ends HTTP 503 with
``model_output = error``. The input_scan stage ran (analyzed the policy-redacted text,
allowed it) but reconciles to 0ms — it MUST read ran=True and NOT render "skip".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_mesh_gateway import main as gw
from pipeline_trace import build_pipeline_trace


class _FakeVerdict(SimpleNamespace):
    pass


def _stages_by_name(trace: dict) -> dict[str, dict]:
    return {s["name"]: s for s in trace.get("stages") or []}


def test_build_pipeline_trace_stamps_ran_on_every_stage():
    """Additive contract: `ran` present + boolean on every stage, derived from action."""
    trace = build_pipeline_trace(
        prompt="hello world",
        forwarded_prompt="hello world",
        final_action="allow",
        stage_metrics={"auth_ms": 0.4, "input_scan_ms": 0.3, "model_output_ms": 120.0},
        response_text="hi there",
    )
    stages = _stages_by_name(trace)
    for name, stage in stages.items():
        assert "ran" in stage, f"{name} missing ran"
        assert isinstance(stage["ran"], bool), f"{name}.ran not bool"
        # ran is false iff the resolved action is skip.
        assert stage["ran"] == (str(stage["action"]).lower() != "skip"), name


def test_input_scan_analyzed_at_zero_ms_is_ran_true_not_skip():
    """Policy-redacted PII → input_scan allow + scan_outcome=analyzed + 0ms → ran=True."""
    trace = build_pipeline_trace(
        prompt="my ssn is 123-45-6789",
        forwarded_prompt="my ssn is [SSN]",
        policy_redacted_prompt="my ssn is [SSN]",
        policy_redacted_flag=True,
        scanner_redaction_applied=False,
        final_action="allow",
        # input_scan reconciles to 0ms on the scan-only + Tier-2-disabled path.
        stage_metrics={"policy_ms": 1.0, "tier1_ms": 0.0, "tier2_ms": 0.0, "input_scan_ms": 0.0},
        scan_verdict=_FakeVerdict(
            action="allow",
            threat_type="pii",
            confidence=0.0,
            tier="tier_1",
            matched_patterns=["ssn"],
            detail="SSN detected",
        ),
    )
    input_scan = _stages_by_name(trace)["input_scan"]
    assert input_scan["action"] == "allow"
    assert input_scan["scan_outcome"] == "analyzed"
    assert input_scan["redact_noop"] is True
    assert input_scan["latency_ms"] == 0.0
    # The crux: it ran, so ran=True — the UI must never recompute this to skip.
    assert input_scan["ran"] is True


def test_blocked_upstream_stages_are_ran_false():
    """A block at input_scan → downstream stages skip → ran=False; upstream ran=True."""
    trace = build_pipeline_trace(
        prompt="ssn 123-45-6789",
        forwarded_prompt="",
        final_action="block",
        blocked_stage="input_scan",
        http_status=403,
        stage_metrics={"auth_ms": 0.5, "policy_ms": 1.0, "input_scan_ms": 2.0},
        scan_verdict=_FakeVerdict(
            action="block", threat_type="pii", confidence=0.95,
            tier="tier_1", matched_patterns=["ssn"], detail="SSN detected",
        ),
    )
    stages = _stages_by_name(trace)
    assert stages["auth"]["ran"] is True
    assert stages["policy"]["ran"] is True
    assert stages["input_scan"]["action"] == "block"
    assert stages["input_scan"]["ran"] is True
    # Everything after the block never ran.
    for name in ("model_routing", "model_input", "model_output", "output_guardrail"):
        assert stages[name]["action"] == "skip", name
        assert stages[name]["ran"] is False, name


def test_scan_only_probe_model_stages_ran_false():
    """skip_inference → model_input/model_output/output_guardrail did not run."""
    trace = build_pipeline_trace(
        prompt="hello",
        forwarded_prompt="hello",
        final_action="allow",
        stage_metrics={"auth_ms": 0.4, "input_scan_ms": 0.3},
        skip_inference=True,
    )
    stages = _stages_by_name(trace)
    assert stages["input_scan"]["ran"] is True
    for name in ("model_input", "model_output", "output_guardrail"):
        assert stages[name]["action"] == "skip", name
        assert stages[name]["ran"] is False, name


@pytest.fixture
def _scan_only_pii_error_ctx():
    """The exact bug scenario: scan-only + policy-redacted PII, then model error → 503."""
    ctx = {
        "stage_metrics": {
            "auth_ms": 0.6,
            "rate_limit_ms": 0.3,
            "policy_ms": 1.4,
            "tier1_ms": 0.0,
            "tier2_ms": 0.0,
            "input_scan_ms": 0.0,
            "model_routing_ms": 0.2,
            "model_input_ms": 0.0,
            "upstream_ms": 0.0,
            "model_output_ms": 0.0,
            "output_guardrail_ms": 0.0,
        },
        "prompt": "contact me at jane@example.com ssn 123-45-6789",
        "redacted_prompt": "contact me at [EMAIL] ssn [SSN]",
        "policy_redacted_prompt": "contact me at [EMAIL] ssn [SSN]",
        "route_metadata": None,
        "requested_model": "gpt-4o-mini",
        "scan_verdict": _FakeVerdict(
            action="allow",
            threat_type="pii",
            confidence=0.0,
            tier="tier_1",
            matched_patterns=["email", "ssn"],
            detail="PII detected",
        ),
        "output_scan_verdict": None,
    }
    token = gw._REQUEST_PIPELINE_CTX.set(ctx)
    try:
        yield ctx
    finally:
        gw._REQUEST_PIPELINE_CTX.reset(token)


def test_llm_error_trace_input_scan_ran_output_guard_not(_scan_only_pii_error_ctx):
    """The reported bug: on the 503 model-error + policy-redacted path the input_scan
    stage is ran=True (NOT skip) while the model stages reflect error/skip honestly."""
    body = gw._attach_pipeline_trace_to_llm_error(
        {"error": "service_unavailable", "code": 503},
        http_status=503,
    )
    trace = body["pipeline_trace"]
    stages = _stages_by_name(trace)

    # The defect was input_scan rendering skip. It ran (analyzed masked text, allowed).
    assert stages["input_scan"]["action"] == "allow"
    assert stages["input_scan"]["ran"] is True
    # Model output attempted but errored — NOT skipped.
    assert stages["model_output"]["action"] == "error"
    assert stages["model_output"]["ran"] is True
    # Output guard genuinely did not run (model output failed).
    assert stages["output_guardrail"]["action"] == "skip"
    assert stages["output_guardrail"]["ran"] is False
    assert trace["final_action"] == "error"
