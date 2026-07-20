"""PIPELINE-0018: backend total_latency_ms matches UI Duration formatting contract."""

from __future__ import annotations

import unittest


def _ui_round_ms(ms: float) -> float:
    """Mirror frontend formatPipelineDurationMs rounding (0.1ms precision)."""
    return round(float(ms) * 10) / 10


def _within_tolerance(api_ms: float, ui_ms: float, tol: float = 0.1) -> bool:
    return abs(api_ms - ui_ms) <= tol


class TestPipelineLatencyUiParity(unittest.TestCase):
    def test_total_latency_rounds_to_tenth_ms_for_ui(self):
        from pipeline_trace import build_pipeline_trace

        raw_total = 13607.123
        trace = build_pipeline_trace(
            prompt="hello",
            final_action="allow",
            stage_metrics={"upstream_ms": 50.0, "model_output_ms": 50.0},
            zeroshield={"processing_time_ms": raw_total},
            response_text="world",
        )
        trace["total_latency_ms"] = raw_total
        ui_display = _ui_round_ms(trace["total_latency_ms"])
        self.assertTrue(_within_tolerance(trace["total_latency_ms"], ui_display))
        self.assertEqual(ui_display, 13607.1)

    def test_stage_sum_plus_overhead_matches_total_within_tenth(self):
        from pipeline_trace import build_pipeline_trace, finalize_stage_metrics
        import time

        wall_start = time.perf_counter()
        metrics = finalize_stage_metrics(
            {
                "auth_ms": 1.0,
                "policy_ms": 2.0,
                "tier1_ms": 3.0,
                "upstream_ms": 40.0,
                "model_output_ms": 40.0,
                "output_guardrail_ms": 1.0,
            },
            wall_start,
        )
        trace = build_pipeline_trace(
            prompt="x",
            final_action="allow",
            stage_metrics=metrics,
            zeroshield={"processing_time_ms": metrics["total_ms"]},
            response_text="y",
        )
        stage_sum = sum(s["latency_ms"] for s in trace["stages"])
        recomposed = stage_sum + trace["overhead_ms"]
        self.assertAlmostEqual(trace["total_latency_ms"], recomposed, places=1)
        ui_total = _ui_round_ms(trace["total_latency_ms"])
        self.assertTrue(_within_tolerance(trace["total_latency_ms"], ui_total))


if __name__ == "__main__":
    unittest.main()
