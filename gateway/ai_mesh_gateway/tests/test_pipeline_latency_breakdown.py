"""PIPELINE-0017: latency breakdown + reduction hints on pipeline_trace."""

from __future__ import annotations

import pytest

from pipeline_trace import attach_latency_breakdown, build_latency_breakdown, build_pipeline_trace


def _stages(model_output_ms: float, input_scan_ms: float = 12.0, policy_ms: float = 3.0):
    return [
        {"name": "auth", "latency_ms": 1.0},
        {"name": "policy", "latency_ms": policy_ms, "detail": "3 rules matched"},
        {"name": "input_scan", "latency_ms": input_scan_ms},
        {"name": "model_output", "latency_ms": model_output_ms},
        {"name": "output_guardrail", "latency_ms": 2.0},
    ]


class TestBuildLatencyBreakdown:
    def test_model_output_dominant_hint(self):
        stages = _stages(model_output_ms=7710.0)
        total = 7728.0
        bd = build_latency_breakdown(stages, total_latency_ms=total, overhead_ms=0.0)

        assert bd["dominant_stage"] == "model_output"
        assert bd["dominant_latency_ms"] == 7710.0
        assert bd["dominant_share_pct"] >= 99.0
        assert len(bd["hints"]) >= 1
        hint = bd["hints"][0]
        assert hint["stage"] == "model_output"
        assert hint["severity"] == "high"
        assert any("faster" in a.lower() or "caching" in a.lower() for a in hint["actions"])

    def test_input_scan_dominant_hint(self):
        stages = _stages(model_output_ms=50.0, input_scan_ms=800.0)
        total = 858.0
        bd = build_latency_breakdown(stages, total_latency_ms=total, overhead_ms=0.0)

        assert bd["dominant_stage"] == "input_scan"
        hint = bd["hints"][0]
        assert hint["stage"] == "input_scan"
        assert any("tier-2" in a.lower() or "tier 2" in a.lower() for a in hint["actions"])

    def test_policy_dominant_hint_includes_rule_count(self):
        stages = _stages(model_output_ms=20.0, input_scan_ms=10.0, policy_ms=500.0)
        total = 536.0
        bd = build_latency_breakdown(stages, total_latency_ms=total, overhead_ms=0.0)

        assert bd["dominant_stage"] == "policy"
        hint = bd["hints"][0]
        assert hint["stage"] == "policy"
        assert any("rule" in a.lower() for a in hint["actions"])

    def test_overhead_secondary_hint_when_large(self):
        stages = _stages(model_output_ms=100.0)
        total = 500.0
        bd = build_latency_breakdown(stages, total_latency_ms=total, overhead_ms=380.0)

        assert bd["overhead_share_pct"] >= 70.0
        overhead_hints = [h for h in bd["hints"] if h["stage"] == "overhead"]
        assert len(overhead_hints) == 1

    def test_no_hints_when_all_stages_fast(self):
        stages = [
            {"name": "auth", "latency_ms": 0.5},
            {"name": "policy", "latency_ms": 0.5},
        ]
        bd = build_latency_breakdown(stages, total_latency_ms=1.0, overhead_ms=0.0)
        assert bd["hints"] == []

    def test_by_stage_sorted_descending(self):
        stages = _stages(model_output_ms=100.0, input_scan_ms=200.0)
        bd = build_latency_breakdown(stages, total_latency_ms=305.0, overhead_ms=0.0)
        lats = [row["latency_ms"] for row in bd["by_stage"]]
        assert lats == sorted(lats, reverse=True)


class TestBuildPipelineTraceIntegration:
    def test_trace_includes_latency_breakdown(self):
        trace = build_pipeline_trace(
            prompt="hello",
            stage_metrics={
                "auth_ms": 1.0,
                "policy_ms": 2.0,
                "input_scan_ms": 15.0,
                "model_output_ms": 7710.0,
                "output_guardrail_ms": 3.0,
                "overhead_ms": 5.0,
                "total_ms": 7736.0,
            },
            final_action="allow",
        )
        assert "latency_breakdown" in trace
        bd = trace["latency_breakdown"]
        assert bd["dominant_stage"] == "model_output"
        assert len(bd["hints"]) >= 1

    def test_attach_latency_breakdown_recomputes(self):
        trace = {
            "stages": _stages(model_output_ms=500.0),
            "total_latency_ms": 520.0,
            "overhead_ms": 5.0,
        }
        attach_latency_breakdown(trace)
        assert trace["latency_breakdown"]["dominant_stage"] == "model_output"
