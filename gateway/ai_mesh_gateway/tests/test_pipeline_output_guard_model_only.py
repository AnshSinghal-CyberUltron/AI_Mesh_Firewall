"""
PIPELINE-0013: Output guard evaluates ACTUAL model output only (L6/L7).

L7 — empty / whitespace-only model output must not trigger output-guard PII findings.
L6 — input-side redaction metadata must not be attributed to the output_guardrail stage;
      the guard scans completion choices only, never the user prompt echo.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

import ai_mesh_gateway.main as gm
from ai_mesh_gateway.output_guard import OutputGuard, OutputVerdict, normalize_output_scan_text
from pipeline_trace import build_pipeline_trace


class NormalizeOutputScanTextTests(unittest.TestCase):
    def test_none_and_empty_are_empty(self):
        self.assertEqual(normalize_output_scan_text(""), "")
        self.assertEqual(normalize_output_scan_text(None), "")

    def test_whitespace_only_is_empty(self):
        for sample in ("   ", "\n\t  ", " \n "):
            with self.subTest(sample=repr(sample)):
                self.assertEqual(normalize_output_scan_text(sample), "")

    def test_substantive_text_preserved(self):
        self.assertEqual(normalize_output_scan_text("hello world"), "hello world")


class ModelOutputScanTextTests(unittest.TestCase):
    def test_empty_completion_yields_empty_scan_text(self):
        self.assertEqual(gm._model_output_scan_text({}), "")
        self.assertEqual(
            gm._model_output_scan_text({"choices": [{"message": {"content": ""}}]}),
            "",
        )

    def test_whitespace_only_completion_yields_empty(self):
        comp = {"choices": [{"message": {"content": "   \n\t  "}}]}
        self.assertEqual(gm._model_output_scan_text(comp), "")

    def test_does_not_include_user_prompt(self):
        prompt_pii = "Contact alice@corp.example SSN 123-45-6789"
        comp = {"choices": [{"message": {"content": "Sure, I can help."}}]}
        scan = gm._model_output_scan_text(comp)
        self.assertNotIn("alice@corp.example", scan)
        self.assertNotIn("123-45-6789", scan)
        self.assertIn("Sure, I can help.", scan)
        # Sanity: prompt is separate from completion extraction
        self.assertNotEqual(prompt_pii, scan)

    def test_reasoning_content_included_when_present(self):
        secret = "sk-test-reasoning-secret-0123456789abcdef"
        comp = {
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_content": f"thinking: {secret}",
                },
            }],
        }
        self.assertIn(secret, gm._model_output_scan_text(comp))

    def test_tool_calls_included_when_present(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        comp = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {"name": "lookup", "arguments": f'{{"key":"{secret}"}}'},
                    }],
                },
            }],
        }
        self.assertIn(secret, gm._model_output_scan_text(comp))


class OutputGuardEmptyOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        scanner = MagicMock()
        scanner.scan_output = AsyncMock(return_value=MagicMock(threat_type="pii", action="redact"))
        self.guard = OutputGuard(scanner, config={"output_guard_enabled": True, "output_tier2_enabled": False})

    async def test_inspect_empty_returns_allow(self):
        v = await self.guard.inspect("")
        self.assertEqual(v.action, "allow")
        scanner = self.guard._scanner
        scanner.scan_output.assert_not_called()

    async def test_inspect_whitespace_returns_allow(self):
        v = await self.guard.inspect("  \n\t  ")
        self.assertEqual(v.action, "allow")
        self.guard._scanner.scan_output.assert_not_called()


class PipelineTraceOutputAttributionTests(unittest.TestCase):
    """L6: output_guardrail detail must not echo input-side zeroshield reason."""

    def test_input_redact_empty_output_no_input_reason_on_output_stage(self):
        input_reason = "Input scan redacted PII before forwarding to model."
        trace = build_pipeline_trace(
            prompt="SSN 123-45-6789 in user message",
            forwarded_prompt="SSN ***-**-6789 in user message",
            final_action="redact",
            http_status=200,
            zeroshield={
                "action": "redact",
                "reason": input_reason,
                "detail": input_reason,
                "detection_tier": "tier_1",
            },
            response_text="",
            output_scan_verdict=None,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        og = stages["output_guardrail"]
        self.assertEqual(og["action"], "allow")
        self.assertNotIn("Input scan", og["detail"])
        self.assertNotEqual(og["detail"], input_reason)

    def test_output_verdict_attributed_when_present(self):
        out_verdict = OutputVerdict(
            action="redact",
            threat_type="pii",
            detail="Output guard redacted email in model response.",
        )
        trace = build_pipeline_trace(
            prompt="hello",
            final_action="redact",
            http_status=200,
            zeroshield={
                "action": "redact",
                "reason": "Input-side reason must not win",
                "detection_tier": "output_guard",
            },
            response_text="reach me at bob@corp.example",
            output_scan_verdict=out_verdict,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        og = stages["output_guardrail"]
        self.assertEqual(og["action"], "redact")
        self.assertIn("Output guard", og["detail"])


if __name__ == "__main__":
    unittest.main()
