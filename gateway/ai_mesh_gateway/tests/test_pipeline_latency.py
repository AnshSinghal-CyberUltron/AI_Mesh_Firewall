"""PIPELINE-0015: per-stage latency reconciliation + skip-stage zero latency."""

from __future__ import annotations

import time
import unittest


class TestPipelineLatencyReconciliation(unittest.TestCase):
    def test_blocked_input_scan_model_output_skip_has_zero_latency(self):
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt="secret",
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
            stage_metrics={},
            zeroshield={"processing_time_ms": 11.6},
            response_text="",
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertEqual(stages["model_output"]["action"], "skip")
        self.assertEqual(stages["model_output"]["latency_ms"], 0.0)
        self.assertEqual(stages["model_input"]["latency_ms"], 0.0)

    def test_total_equals_stage_sum_plus_overhead(self):
        from pipeline_trace import build_pipeline_trace, finalize_stage_metrics

        wall_start = time.perf_counter()
        time.sleep(0.001)
        metrics = finalize_stage_metrics(
            {
                "auth_ms": 1.0,
                "rate_limit_ms": 0.5,
                "policy_ms": 2.0,
                "tier1_ms": 3.0,
                "tier2_ms": 4.0,
                "kill_switch_ms": 0.2,
                "model_routing_ms": 1.5,
                "model_input_ms": 0.1,
                "upstream_ms": 50.0,
                "model_output_ms": 50.0,
                "output_guardrail_ms": 5.0,
            },
            wall_start,
        )
        trace = build_pipeline_trace(
            prompt="hello",
            final_action="allow",
            stage_metrics=metrics,
            zeroshield={"processing_time_ms": metrics["total_ms"]},
            response_text="world",
        )
        stage_sum = sum(s["latency_ms"] for s in trace["stages"])
        self.assertAlmostEqual(
            trace["total_latency_ms"],
            stage_sum + trace["overhead_ms"],
            places=1,
        )
        self.assertAlmostEqual(trace["stage_latency_sum_ms"], stage_sum, places=1)

    def test_no_fake_defaults_when_metrics_empty(self):
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt="x",
            final_action="allow",
            stage_metrics={},
            response_text="y",
        )
        for stage in trace["stages"]:
            self.assertEqual(stage["latency_ms"], 0.0)

    def test_model_output_uses_upstream_not_processing_time(self):
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt="x",
            final_action="allow",
            stage_metrics={"upstream_ms": 42.0, "model_output_ms": 42.0},
            zeroshield={"processing_time_ms": 999.0},
            response_text="answer",
        )
        mo = next(s for s in trace["stages"] if s["name"] == "model_output")
        self.assertEqual(mo["action"], "allow")
        self.assertEqual(mo["latency_ms"], 42.0)

    def test_pipeline_stage_timer_accumulates(self):
        from pipeline_trace import PipelineStageTimer

        t0 = time.perf_counter()
        timer = PipelineStageTimer(t0)
        time.sleep(0.002)
        timer.mark_segment_end("auth")
        time.sleep(0.001)
        timer.add_ms("policy", 1.5)
        self.assertGreaterEqual(timer._durations.get("auth", 0), 1.0)
        self.assertEqual(timer._durations.get("policy"), 1.5)

    def test_stage_seq_matches_runtime_kill_switch_before_scans(self):
        from pipeline_trace import PIPELINE_STAGE_NAMES

        names = list(PIPELINE_STAGE_NAMES)
        self.assertEqual(names[0], "auth")
        self.assertLess(names.index("kill_switch"), names.index("rate_limit"))
        self.assertLess(names.index("kill_switch"), names.index("policy"))
        self.assertLess(names.index("kill_switch"), names.index("input_scan"))
        self.assertLess(names.index("input_scan"), names.index("model_routing"))

    def test_trace_stage_list_follows_pipeline_stage_names(self):
        from pipeline_trace import PIPELINE_STAGE_NAMES, build_pipeline_trace

        trace = build_pipeline_trace(
            prompt="hello",
            final_action="allow",
            stage_metrics={"auth_ms": 1.0, "kill_switch_ms": 0.2, "input_scan_ms": 3.0},
            response_text="world",
        )
        self.assertEqual([s["name"] for s in trace["stages"]], list(PIPELINE_STAGE_NAMES))

    def test_policy_block_does_not_skip_kill_switch_that_already_ran(self):
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt="secret",
            final_action="block",
            blocked_stage="policy",
            http_status=403,
            stage_metrics={"kill_switch_ms": 0.3, "policy_ms": 2.0},
            zeroshield={"processing_time_ms": 12.0},
            response_text="",
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertNotEqual(stages["kill_switch"]["action"], "skip")
        self.assertEqual(stages["input_scan"]["action"], "skip")
        self.assertEqual(stages["model_output"]["action"], "skip")

    def test_addon_split_on_trace_root(self):
        from pipeline_trace import build_pipeline_trace, finalize_stage_metrics

        wall_start = time.perf_counter()
        metrics = finalize_stage_metrics(
            {
                "auth_ms": 1.0,
                "rate_limit_ms": 0.5,
                "policy_ms": 2.0,
                "tier1_ms": 3.0,
                "tier2_ms": 40.0,
                "kill_switch_ms": 0.2,
                "model_routing_ms": 1.5,
                "model_input_ms": 0.1,
                "upstream_ms": 50.0,
                "model_output_ms": 50.0,
                "output_guardrail_ms": 5.0,
            },
            wall_start,
        )
        trace = build_pipeline_trace(
            prompt="hello",
            final_action="allow",
            stage_metrics=metrics,
            zeroshield={"processing_time_ms": metrics["total_ms"]},
            response_text="world",
        )
        self.assertIn("t_addon_pre_ms", trace)
        self.assertIn("t_addon_post_ms", trace)
        self.assertIn("t_t2_ms", trace)
        self.assertAlmostEqual(trace["t_addon_post_ms"], 5.0, places=1)
        self.assertAlmostEqual(trace["t_t2_ms"], 40.0, places=1)
        expected_pre = round(
            max(0.0, float(trace["total_latency_ms"]) - 50.0 - 5.0),
            1,
        )
        self.assertAlmostEqual(trace["t_addon_pre_ms"], expected_pre, places=1)
        stage_sum = sum(s["latency_ms"] for s in trace["stages"])
        self.assertAlmostEqual(
            trace["total_latency_ms"],
            stage_sum + trace["overhead_ms"],
            places=1,
        )


if __name__ == "__main__":
    unittest.main()
