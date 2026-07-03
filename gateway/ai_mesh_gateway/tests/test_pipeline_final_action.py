"""
Tests for PIPELINE-0007: ONE authoritative final_action + blocked_by.

Verifies:
  - Input block → input_scan stage action=block, model stages=skip.
  - Output block → output_guardrail action=block, model stages NOT skip.
  - No double-block in trace stages (only ONE stage carries action=block).
  - Response body has ONE blocked_by (no duplicated pipeline_stage key).
  - blocked_by is passed through from _build_block_response (single derivation).
  - PipelineDecision is the authoritative source for final_action.
"""

from __future__ import annotations

import json
import unittest

from ai_mesh_gateway.pipeline_trace import build_pipeline_trace


class TestTraceInputBlock(unittest.TestCase):
    """Input block: input_scan=block, model stages=skip, one block total."""

    def test_input_scan_block_stage(self):
        trace = build_pipeline_trace(
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertEqual(stages["input_scan"]["action"], "block")

    def test_model_stages_skipped_on_input_block(self):
        trace = build_pipeline_trace(
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertEqual(stages["model_input"]["action"], "skip")
        self.assertEqual(stages["model_output"]["action"], "skip")

    def test_no_double_block_on_input(self):
        """Only input_scan should carry action=block; no secondary block stage."""
        trace = build_pipeline_trace(
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
        )
        block_stages = [s["name"] for s in trace["stages"] if s["action"] == "block"]
        self.assertEqual(len(block_stages), 1)
        self.assertEqual(block_stages[0], "input_scan")


class TestTracePolicyBlock(unittest.TestCase):
    """Policy block: policy=block, everything after=skip, one block."""

    def test_policy_block_stage(self):
        trace = build_pipeline_trace(
            final_action="block",
            blocked_stage="policy",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertEqual(stages["policy"]["action"], "block")

    def test_no_double_block_on_policy(self):
        trace = build_pipeline_trace(
            final_action="block",
            blocked_stage="policy",
            http_status=403,
        )
        block_stages = [s["name"] for s in trace["stages"] if s["action"] == "block"]
        self.assertEqual(len(block_stages), 1)
        self.assertEqual(block_stages[0], "policy")


class TestTraceOutputGuardBlock(unittest.TestCase):
    """Output guard block: output_guardrail=block, model stages NOT skip."""

    def test_output_guard_block_stage(self):
        trace = build_pipeline_trace(
            final_action="block",
            blocked_stage="output_guardrail",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertEqual(stages["output_guardrail"]["action"], "block")

    def test_model_stages_not_skipped_on_output_block(self):
        """Model ran successfully BEFORE the output guard blocked."""
        trace = build_pipeline_trace(
            final_action="block",
            blocked_stage="output_guardrail",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertNotEqual(stages["model_input"]["action"], "skip")
        self.assertNotEqual(stages["model_output"]["action"], "skip")


class TestTraceAllowAndRedact(unittest.TestCase):
    """Allow/redact paths: no block in any stage."""

    def test_allow_no_block_stage(self):
        trace = build_pipeline_trace(
            final_action="allow",
            blocked_stage="",
            http_status=200,
        )
        block_stages = [s["name"] for s in trace["stages"] if s["action"] == "block"]
        self.assertEqual(block_stages, [])

    def test_redact_no_block_stage(self):
        trace = build_pipeline_trace(
            final_action="redact",
            blocked_stage="",
            http_status=200,
        )
        block_stages = [s["name"] for s in trace["stages"] if s["action"] == "block"]
        self.assertEqual(block_stages, [])


class TestBlockResponseNoDuplication(unittest.TestCase):
    """Response body: ONE blocked_by, no pipeline_stage duplicate (PIPELINE-0007)."""

    def test_no_pipeline_stage_in_response(self):
        from ai_mesh_gateway.main import _build_safe_block_response

        resp = _build_safe_block_response(
            status_code=403,
            code="content_blocked",
            threat_category="prompt_injection",
            request_id="zs-test123",
            blocked_by="input_scan",
        )
        body = json.loads(resp.body.decode())
        self.assertEqual(body["blocked_by"], "input_scan")
        self.assertNotIn("pipeline_stage", body)

    def test_blocked_by_passed_through_no_rederivation(self):
        """When blocked_by is passed, uses it directly."""
        from ai_mesh_gateway.main import _build_safe_block_response

        resp = _build_safe_block_response(
            status_code=403,
            code="content_blocked",
            threat_category="prompt_injection",
            request_id="zs-abc",
            blocked_by="policy",
        )
        body = json.loads(resp.body.decode())
        self.assertEqual(body["blocked_by"], "policy")

    def test_blocked_by_falls_back_when_none(self):
        """When blocked_by is None, resolves from code/category/tier."""
        from ai_mesh_gateway.main import _build_safe_block_response

        resp = _build_safe_block_response(
            status_code=403,
            code="content_blocked",
            threat_category="prompt_injection",
            detection_tier="tier_2",
        )
        body = json.loads(resp.body.decode())
        self.assertEqual(body["blocked_by"], "input_scan")

    def test_build_block_response_passes_blocked_by(self):
        """_build_block_response passes blocked_stage to _build_safe_block_response."""
        from ai_mesh_gateway.main import _build_block_response

        resp = _build_block_response(
            403,
            "content_blocked",
            {
                "threat_type": "prompt_injection",
                "detection_tier": "tier_2",
                "action": "block",
            },
            prompt="test prompt",
        )
        body = json.loads(resp.body.decode())
        self.assertEqual(body["blocked_by"], "input_scan")
        self.assertNotIn("pipeline_stage", body)


class TestResolvePipelineBlockedBy(unittest.TestCase):
    """_resolve_pipeline_blocked_by is the single stage-name resolver."""

    def test_keyword_maps_to_firewall_keywords(self):
        from ai_mesh_gateway.main import _resolve_pipeline_blocked_by

        self.assertEqual(
            _resolve_pipeline_blocked_by(code="blocked_keyword", threat_category="blocked_keyword"),
            "firewall_keywords",
        )

    def test_tier2_maps_to_input_scan(self):
        from ai_mesh_gateway.main import _resolve_pipeline_blocked_by

        self.assertEqual(
            _resolve_pipeline_blocked_by(code="content_blocked", threat_category="prompt_injection", detection_tier="tier_2"),
            "input_scan",
        )

    def test_policy_tier_maps_to_policy(self):
        from ai_mesh_gateway.main import _resolve_pipeline_blocked_by

        self.assertEqual(
            _resolve_pipeline_blocked_by(code="policy_blocked", threat_category="policy_violation", detection_tier="policy"),
            "policy",
        )

    def test_output_guard_maps_to_output_guardrail(self):
        from ai_mesh_gateway.main import _resolve_pipeline_blocked_by

        self.assertEqual(
            _resolve_pipeline_blocked_by(code="content_blocked", threat_category="pii", detection_tier="output_guard"),
            "output_guardrail",
        )

    def test_rate_limit_maps(self):
        from ai_mesh_gateway.main import _resolve_pipeline_blocked_by

        self.assertEqual(
            _resolve_pipeline_blocked_by(code="rate_limit_exceeded", threat_category="rate_limit"),
            "rate_limit",
        )


class TestPipelineDecisionAuthority(unittest.TestCase):
    """PipelineDecision is the authoritative source for final_action."""

    def test_input_decision_action_used_for_trace(self):
        """Demonstrates that PipelineDecision.action is the canonical source
        for final_action, not zeroshield dict (which may carry stale values)."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        decision = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.95,
            enforcement_mode="block",
            scan_block_on_injection=True,
            injection_threshold=0.80,
        )
        self.assertEqual(decision.action, "block")
        self.assertEqual(decision.blocked_by, "input_scan")

        trace = build_pipeline_trace(
            final_action=decision.action,
            blocked_stage=decision.blocked_by or "",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertEqual(stages["input_scan"]["action"], "block")
        self.assertEqual(stages["model_input"]["action"], "skip")

    def test_redact_decision_produces_clean_trace(self):
        """PipelineDecision.action='redact' → no block stages in trace."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        decision = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
        )
        self.assertEqual(decision.action, "redact")
        self.assertIsNone(decision.blocked_by)

        trace = build_pipeline_trace(
            final_action=decision.action,
            blocked_stage=decision.blocked_by or "",
            http_status=200,
        )
        block_stages = [s["name"] for s in trace["stages"] if s["action"] == "block"]
        self.assertEqual(block_stages, [])


if __name__ == "__main__":
    unittest.main()
