"""
PIPELINE-0019: blocked events carry full pipeline_trace in telemetry metadata.

Invariant: input_blocked / output_guard telemetry events include stages[] with
auth through the blocking stage executed and downstream stages skipped (for
input-side blocks).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_mesh_gateway import main as gw

# Capture before live-uvicorn tests replace gw._emit_telemetry with a no-op stub.
_REAL_EMIT_TELEMETRY = gw._emit_telemetry


class _FakeVerdict(SimpleNamespace):
    pass


def _stage_actions(trace: dict) -> dict[str, str]:
    return {s["name"]: s["action"] for s in trace.get("stages") or []}


@pytest.fixture
def pipeline_ctx():
    ctx = {
        "stage_metrics": {
            "auth_ms": 1.2,
            "rate_limit_ms": 0.5,
            "policy_ms": 2.0,
            "tier1_ms": 3.1,
            "tier2_ms": 0.0,
            "input_scan_ms": 3.1,
            "model_routing_ms": 0.0,
            "model_input_ms": 0.0,
            "upstream_ms": 0.0,
            "model_output_ms": 0.0,
            "output_guardrail_ms": 0.0,
        },
        "prompt": "user secret 123-45-6789",
        "route_metadata": None,
        "requested_model": "gpt-4o-mini",
        "scan_verdict": _FakeVerdict(
            action="block",
            threat_type="pii",
            confidence=0.95,
            tier="tier_1",
            matched_patterns=["ssn"],
            detail="SSN detected",
        ),
        "output_scan_verdict": None,
    }
    token = gw._REQUEST_PIPELINE_CTX.set(ctx)
    try:
        yield ctx
    finally:
        gw._REQUEST_PIPELINE_CTX.reset(token)


def test_enrich_input_blocked_trace_stages_input_scan_block(pipeline_ctx):
    kwargs = {
        "status_code": 403,
        "event_type": "input_blocked",
        "action": "block",
        "threat_type": "pii",
        "risk_score": 0.95,
        "metadata": {"detail": "SSN detected", "matched_patterns": ["ssn"]},
    }
    gw._enrich_blocked_event_pipeline_trace(kwargs)

    trace = kwargs["metadata"]["pipeline_trace"]
    actions = _stage_actions(trace)
    assert actions["auth"] != "skip"
    assert actions["policy"] != "skip"
    assert actions["input_scan"] == "block"
    assert actions["model_input"] == "skip"
    assert actions["model_output"] == "skip"
    assert actions["output_guardrail"] == "skip"
    assert any(s["name"] == "input_scan" and s["action"] == "block" for s in trace["stages"])


def test_emit_telemetry_attaches_trace_for_input_blocked(pipeline_ctx):
    built: list[dict] = []
    mock_telemetry = MagicMock()

    def _capture_build(**kwargs):
        built.append(dict(kwargs))
        return {"event_type": kwargs.get("event_type"), "metadata": kwargs.get("metadata")}

    mock_telemetry.emit = MagicMock()

    with patch.object(gw, "TELEMETRY", mock_telemetry), patch.object(
        gw, "_org_audit_logging_enabled", return_value=True
    ), patch("telemetry.build_telemetry_event", side_effect=_capture_build):
        _REAL_EMIT_TELEMETRY(
            status_code=403,
            event_type="input_blocked",
            action="block",
            threat_type="pii",
            risk_score=0.95,
            metadata={"detail": "SSN detected"},
        )

    assert mock_telemetry.emit.call_count == 1
    assert len(built) == 1
    trace = (built[0].get("metadata") or {}).get("pipeline_trace")
    assert trace is not None
    assert _stage_actions(trace)["input_scan"] == "block"
    assert _stage_actions(trace)["model_output"] == "skip"


def test_output_guard_block_keeps_model_stages_run(pipeline_ctx):
    pipeline_ctx["output_scan_verdict"] = _FakeVerdict(
        action="block",
        threat_type="prompt_injection",
        confidence=0.9,
        tier="output_guard",
        matched_patterns=["ignore"],
        detail="Injection in output",
    )
    kwargs = {
        "status_code": 403,
        "event_type": "output_guard",
        "action": "block",
        "threat_type": "prompt_injection",
        "risk_score": 0.9,
        "metadata": {"detail": "Injection in output"},
    }
    gw._enrich_blocked_event_pipeline_trace(kwargs)
    actions = _stage_actions(kwargs["metadata"]["pipeline_trace"])
    assert actions["model_input"] != "skip"
    assert actions["model_output"] != "skip"
    assert actions["output_guardrail"] == "block"


def test_build_blocked_pipeline_trace_matches_block_response(pipeline_ctx):
    zs = gw._build_zeroshield_metadata(
        action="block",
        reason="Input blocked",
        detection_tier="tier_1",
        threat_type="pii",
        confidence=0.95,
        matched_patterns=["ssn"],
        original_prompt=pipeline_ctx["prompt"],
        detail="SSN detected",
    )
    trace = gw._build_blocked_pipeline_trace(
        403,
        "content_blocked",
        zs,
        stage_metrics=pipeline_ctx["stage_metrics"],
        prompt=pipeline_ctx["prompt"],
        scan_verdict=pipeline_ctx["scan_verdict"],
        requested_model="gpt-4o-mini",
    )
    assert any(s["name"] == "input_scan" and s["action"] == "block" for s in trace["stages"])
    assert len(trace.get("stages") or []) >= 7
