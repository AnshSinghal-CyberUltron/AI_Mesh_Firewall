"""
Block-short-circuit regression tests (PIPELINE-0005).

Invariant: a BLOCK decision at ANY input pipeline stage short-circuits the
request. The LLM is NEVER called, and the pipeline_trace marks downstream
stages "skip".

Tests:
  1. Structural: block-returns in proxy_chat source appear before any model call
  2. Pipeline trace: build_pipeline_trace with an upstream blocked_stage marks
     model_input / model_output as "skip"
  3. Enforcement authority: resolve_and_enforce → is_terminal_block for every
     block vector; monitor mode never terminal
  4. _build_block_response: returns the correct status and pipeline_trace
"""

from __future__ import annotations

import inspect
import os
import re
import unittest

import pytest


# ---------------------------------------------------------------------------
# 1. Structural invariant: INPUT-side block returns in proxy_chat source
#    appear on LOWER lines than ANY model call (LLM_ROUTER / stream launch).
# ---------------------------------------------------------------------------

class StructuralBlockShortCircuitTests(unittest.TestCase):
    """Source-level proof that INPUT block returns precede model calls."""

    @staticmethod
    def _proxy_chat_source_lines():
        """Return (start_line, source_lines) for proxy_chat."""
        import ai_mesh_gateway.main as gw
        src = inspect.getsource(gw.proxy_chat)
        start = inspect.getsourcelines(gw.proxy_chat)[1]
        return start, src.splitlines()

    def test_input_block_returns_precede_model_calls(self):
        """At least 3 ``return _build_block_response(403, `` sites in
        proxy_chat appear BEFORE the earliest ``LLM_ROUTER.acompletion`` /
        ``_launch_chat_stream_response``, proving the input-side block
        short-circuits before the model is called.

        Output-guard block returns (AFTER the model call) are expected and
        excluded from the "must precede model" assertion.
        """
        start, lines = self._proxy_chat_source_lines()

        block_return_offsets = []
        model_call_offsets = []
        firewall_disabled_region = False

        for idx, line in enumerate(lines):
            stripped = line.strip()

            if stripped.startswith("#"):
                continue

            if "firewall_disabled" in stripped and "if" in stripped:
                firewall_disabled_region = True
            if firewall_disabled_region and stripped.startswith("if not") and "AGENT_ID" in stripped:
                firewall_disabled_region = False

            if "return _build_block_response(" in stripped and "403" in stripped:
                block_return_offsets.append(idx)

            if firewall_disabled_region:
                continue
            if "LLM_ROUTER.acompletion" in stripped and "=" in stripped:
                model_call_offsets.append(idx)
            if "_launch_chat_stream_response(" in stripped and "return" in stripped:
                model_call_offsets.append(idx)

        self.assertTrue(
            block_return_offsets,
            "Expected at least one block-return in proxy_chat",
        )
        self.assertTrue(
            model_call_offsets,
            "Expected at least one LLM_ROUTER / stream call in proxy_chat",
        )

        earliest_model_call = min(model_call_offsets)

        input_block_returns = [
            br for br in block_return_offsets if br < earliest_model_call
        ]

        self.assertGreaterEqual(
            len(input_block_returns),
            3,
            f"Expected >=3 INPUT-side block-returns before the earliest "
            f"model call (line {start + earliest_model_call}), "
            f"found {len(input_block_returns)}. "
            f"Block offsets: {block_return_offsets}, "
            f"model offsets: {model_call_offsets}.",
        )

    def test_multiple_block_returns_exist(self):
        """At least 3 block-return sites exist in proxy_chat (policy,
        enforcement, unmaskable PII)."""
        _, lines = self._proxy_chat_source_lines()
        count = sum(
            1
            for line in lines
            if not line.strip().startswith("#")
            and "return _build_block_response(" in line
            and "403" in line
        )
        self.assertGreaterEqual(
            count, 3, "Expected >=3 block-return sites in proxy_chat"
        )

    def test_output_guard_block_exists_after_model(self):
        """At least one block-return exists AFTER the earliest model call
        (the output guard). This verifies we have the right mental model:
        output-guard blocks are expected post-model."""
        start, lines = self._proxy_chat_source_lines()

        block_offsets = []
        model_offsets = []
        fw_disabled = False

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "firewall_disabled" in stripped and "if" in stripped:
                fw_disabled = True
            if fw_disabled and stripped.startswith("if not") and "AGENT_ID" in stripped:
                fw_disabled = False
            if "return _build_block_response(" in stripped and "403" in stripped:
                block_offsets.append(idx)
            if fw_disabled:
                continue
            if "LLM_ROUTER.acompletion" in stripped and "=" in stripped:
                model_offsets.append(idx)
            if "_launch_chat_stream_response(" in stripped and "return" in stripped:
                model_offsets.append(idx)

        if not model_offsets:
            self.skipTest("No model calls found (unexpected)")

        earliest_model = min(model_offsets)
        post_model_blocks = [b for b in block_offsets if b > earliest_model]
        self.assertTrue(
            post_model_blocks,
            "Expected at least one output-guard block-return after the "
            "earliest model call.",
        )


# ---------------------------------------------------------------------------
# 2. Pipeline-trace verification: blocked_stage upstream of model → skip
# ---------------------------------------------------------------------------

class PipelineTraceBlockTests(unittest.TestCase):
    """When build_pipeline_trace is called with a blocked_stage upstream of
    the model, model_input / model_output stages have action='skip'."""

    @staticmethod
    def _build_trace(blocked_stage: str) -> list[dict]:
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt="test prompt",
            stage_metrics={"auth_ms": 0, "policy_ms": 1, "tier1_ms": 2},
            final_action="block",
            blocked_stage=blocked_stage,
            http_status=403,
        )
        return trace.get("stages", [])

    def test_policy_block_skips_model_input(self):
        stages = self._build_trace("policy")
        model_input = next(
            (s for s in stages if s.get("name") == "model_input"), None
        )
        self.assertIsNotNone(model_input, "model_input stage missing")
        self.assertEqual(model_input["action"], "skip")

    def test_policy_block_skips_model_output(self):
        stages = self._build_trace("policy")
        model_output = next(
            (s for s in stages if s.get("name") == "model_output"), None
        )
        self.assertIsNotNone(model_output, "model_output stage missing")
        self.assertEqual(model_output["action"], "skip")

    def test_input_scan_block_skips_model(self):
        stages = self._build_trace("input_scan")
        for name in ("model_input", "model_output"):
            stage = next((s for s in stages if s.get("name") == name), None)
            self.assertIsNotNone(stage, f"{name} stage missing")
            self.assertEqual(
                stage["action"],
                "skip",
                f"{name} should be 'skip' when blocked at input_scan",
            )

    def test_rate_limit_block_skips_model(self):
        stages = self._build_trace("rate_limit")
        model_input = next(
            (s for s in stages if s.get("name") == "model_input"), None
        )
        self.assertIsNotNone(model_input)
        self.assertEqual(model_input["action"], "skip")

    def test_output_guard_block_does_NOT_skip_model(self):
        """An output-guard block means the model DID run: model stages are
        NOT 'skip'."""
        stages = self._build_trace("output_guardrail")
        model_input = next(
            (s for s in stages if s.get("name") == "model_input"), None
        )
        model_output = next(
            (s for s in stages if s.get("name") == "model_output"), None
        )
        if model_input:
            self.assertNotEqual(
                model_input["action"],
                "skip",
                "model_input must NOT be 'skip' on an output-guard block",
            )
        if model_output:
            self.assertNotEqual(
                model_output["action"],
                "skip",
                "model_output must NOT be 'skip' on an output-guard block",
            )


# ---------------------------------------------------------------------------
# 3. _build_block_response status-code contract
# ---------------------------------------------------------------------------

class BuildBlockResponseTests(unittest.TestCase):
    """_build_block_response returns the expected HTTP status."""

    def test_threat_intel_block_keeps_403(self):
        """A non-content category (threat_intel) keeps 403 unchanged."""
        from ai_mesh_gateway.main import _build_block_response

        resp = _build_block_response(
            403,
            "threat_intel_blocked",
            {
                "action": "block",
                "reason": "test",
                "detection_tier": "threat_intel",
                "threat_type": "threat_intel",
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_content_block_remaps_to_gateway_block_status(self):
        """A content-category block (prompt_injection) gets remapped to
        GATEWAY_BLOCK_STATUS (default 400) per the D-a contract."""
        from ai_mesh_gateway.main import _build_block_response

        resp = _build_block_response(
            403,
            "content_blocked",
            {
                "action": "block",
                "reason": "test",
                "detection_tier": "input_scan",
                "threat_type": "prompt_injection",
            },
        )
        expected = int(os.environ.get("GATEWAY_BLOCK_STATUS", "400"))
        self.assertEqual(resp.status_code, expected)

    @pytest.mark.skipif(
        os.environ.get("GATEWAY_BLOCK_STATUS") == "403",
        reason="env forces 403",
    )
    def test_content_block_default_is_400(self):
        """Without GATEWAY_BLOCK_STATUS env, content blocks → 400."""
        from ai_mesh_gateway.main import _build_block_response

        resp = _build_block_response(
            403,
            "content_blocked",
            {
                "action": "block",
                "reason": "test",
                "detection_tier": "input_scan",
                "threat_type": "prompt_injection",
            },
        )
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# 4. Enforcement authority contract: block decisions
# ---------------------------------------------------------------------------

class EnforcementBlockContractTests(unittest.TestCase):
    """resolve_and_enforce → is_terminal_block → _build_block_response(403)
    is the ONE canonical path for input blocks."""

    def test_injection_block_is_terminal(self):
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.95,
            scan_block_on_injection=True,
            injection_threshold=0.80,
        )
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.action, "block")

    def test_pii_block_unmaskable_is_terminal(self):
        """PII with redaction_possible=False (unmaskable) → terminal block."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
            redaction_possible=False,
        )
        self.assertTrue(d.is_terminal_block)

    def test_pii_block_maskable_becomes_redact(self):
        """PII with redaction_possible=True → redact (NOT terminal block)."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
            redaction_possible=True,
        )
        self.assertFalse(d.is_terminal_block)
        self.assertEqual(d.action, "redact")

    def test_secret_block_maskable_becomes_redact(self):
        """Secret with redaction_possible=True → redact (NOT terminal)."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="secret",
            redaction_possible=True,
        )
        self.assertFalse(d.is_terminal_block)
        self.assertEqual(d.action, "redact")

    def test_generic_threat_block_is_terminal(self):
        """A non-injection non-PII threat (e.g. toxicity) with scanner
        recommendation 'block' → terminal block."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="toxicity",
        )
        self.assertTrue(d.is_terminal_block)

    def test_org_policy_override_is_terminal(self):
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="allow",
            org_policy_action="block",
        )
        self.assertTrue(d.is_terminal_block)

    def test_unmaskable_redaction_is_terminal(self):
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
            redaction_possible=False,
        )
        self.assertTrue(d.is_terminal_block)

    def test_monitor_mode_never_terminal(self):
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.99,
            scan_block_on_injection=True,
            injection_threshold=0.50,
            enforcement_mode="monitor",
        )
        self.assertFalse(d.is_terminal_block)

    def test_below_threshold_not_terminal(self):
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.30,
            scan_block_on_injection=True,
            injection_threshold=0.80,
        )
        self.assertFalse(d.is_terminal_block)


# ---------------------------------------------------------------------------
# 5. Enforcement → pipeline trace end-to-end contract
# ---------------------------------------------------------------------------

class EnforcementTraceLinkageTests(unittest.TestCase):
    """resolve_and_enforce produces is_terminal_block, and the corresponding
    blocked_stage in the pipeline_trace correctly skips model stages."""

    @staticmethod
    def _trace_model_actions(blocked_stage: str) -> dict[str, str]:
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt="test",
            final_action="block",
            blocked_stage=blocked_stage,
            http_status=403,
        )
        return {
            s["name"]: s["action"]
            for s in trace.get("stages", [])
            if s.get("name") in ("model_input", "model_output")
        }

    def test_policy_block_trace(self):
        actions = self._trace_model_actions("policy")
        self.assertEqual(actions.get("model_input"), "skip")
        self.assertEqual(actions.get("model_output"), "skip")

    def test_input_scan_block_trace(self):
        actions = self._trace_model_actions("input_scan")
        self.assertEqual(actions.get("model_input"), "skip")
        self.assertEqual(actions.get("model_output"), "skip")

    def test_kill_switch_block_trace(self):
        actions = self._trace_model_actions("kill_switch")
        self.assertEqual(actions.get("model_input"), "skip")
        self.assertEqual(actions.get("model_output"), "skip")

    def test_output_guard_does_not_skip_trace(self):
        actions = self._trace_model_actions("output_guardrail")
        self.assertNotEqual(actions.get("model_input"), "skip")
        self.assertNotEqual(actions.get("model_output"), "skip")


if __name__ == "__main__":
    unittest.main()
