"""
PIPELINE-0014: Output guard masks maskable PII byte-for-byte; actions are the
operator's exactly.

- enforce_output honors the operator's action EXACTLY: block stays block (the old
  maskable block → redact floor was removed in 268f0f92 because it silently
  downgraded an explicitly-selected action)
- noop scrub → fail-closed block via redaction_possible=False
- _apply_output_guard_nonstream delivers masked completion bytes
- sanitize_output_for_verdict changes bytes for SSN/email
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import ai_mesh_gateway.main as gm
from ai_mesh_gateway.enforcement import enforce_output
from ai_mesh_gateway.output_guard import OutputVerdict, sanitize_output_for_verdict
from pipeline_trace import build_pipeline_trace

try:
    from patterns import redact_all
except ImportError:
    from ai_mesh_gateway.patterns import redact_all


SSN = "123-45-6789"
EMAIL = "alice.jones@corp.example"
RAW_OUTPUT = f"Contact {EMAIL} with SSN {SSN} for verification."


class EnforceOutputRedactTests(unittest.TestCase):
    def test_pii_block_verdict_stays_block(self):
        """FULL OPERATOR CONTROL: block means BLOCK, even for maskable PII.

        This previously asserted block -> redact, the §1.7 "surgically redact, never
        whole-block" floor. That floor was REMOVED (commit 268f0f92): it silently
        downgraded an action the operator explicitly selected, making "block"
        indistinguishable from "redact" in the UI and pipeline trace. The operator is
        the sole owner of their org's actions — selecting block whole-response-blocks
        (403); selecting redact masks in place.
        """
        d = enforce_output(verdict_action="block", verdict_threat_type="pii")
        self.assertEqual(d.action, "block")
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.blocked_by, "output_guard")

    def test_pii_block_stays_block_under_strict_org_block_mode(self):
        """Same contract with an explicit org enforcement_mode=block."""
        d = enforce_output(
            verdict_action="block",
            verdict_threat_type="pii",
            enforcement_mode="block",
        )
        self.assertEqual(d.action, "block")
        self.assertTrue(d.is_terminal_block)

    def test_redact_noop_escalates_to_block(self):
        d = enforce_output(
            verdict_action="redact",
            verdict_threat_type="pii",
            redaction_possible=False,
        )
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.blocked_by, "output_guard")

    def test_redact_with_bytes_changed_stays_redact(self):
        d = enforce_output(
            verdict_action="redact",
            verdict_threat_type="pii",
            redaction_possible=True,
        )
        self.assertEqual(d.action, "redact")


class SanitizeOutputByteVerifyTests(unittest.TestCase):
    def test_redact_all_masks_ssn_and_email(self):
        sanitized = redact_all(RAW_OUTPUT)
        self.assertNotEqual(sanitized, RAW_OUTPUT)
        self.assertNotIn(SSN, sanitized)
        self.assertNotIn(EMAIL, sanitized)

    def test_sanitize_output_for_verdict_masks_pii(self):
        verdict = OutputVerdict(
            action="redact",
            threat_type="pii",
            detail="email and SSN in model output",
            matched_patterns=["email", "ssn"],
        )
        sanitized = sanitize_output_for_verdict(RAW_OUTPUT, verdict, redact_pii_fn=redact_all)
        self.assertNotEqual(sanitized, RAW_OUTPUT)
        self.assertNotIn(SSN, sanitized)
        self.assertNotIn(EMAIL, sanitized)


class ApplyOutputGuardNonstreamRedactTests(unittest.IsolatedAsyncioTestCase):
    async def test_redact_delivers_masked_completion(self):
        resp = {
            "choices": [{"message": {"content": RAW_OUTPUT}}],
        }
        verdict = OutputVerdict(
            action="redact",
            threat_type="pii",
            detail="PII in model output",
            matched_patterns=["email", "ssn"],
            compliance_tags=["PII"],
        )
        guard = MagicMock()
        guard.inspect = AsyncMock(return_value=verdict)

        with patch.object(gm, "OUTPUT_GUARD", guard), patch.object(
            gm, "CONFIG", {"output_guard_enabled": True, "pii_detection_enabled": True}
        ), patch.object(gm, "CONFIG_SYNC", None), patch.object(
            gm, "METRICS", {"blocked": 0}
        ), patch.object(
            gm, "_emit_telemetry"
        ), patch.object(
            gm, "_resolve_rag_context_chunks", AsyncMock(return_value=[])
        ):
            result = await gm._apply_output_guard_nonstream(
                resp,
                org_config={"enforcement_mode": "redact", "output_scan_enabled": True},
                org_slug="zeroshield",
                body={"model": "gpt-test"},
                user_id=1,
                project_id="p1",
                key_prefix="sk-test",
                prompt="hello",
                start=time.perf_counter(),
            )

        self.assertIsNone(result)
        egress = resp["choices"][0]["message"]["content"]
        self.assertNotEqual(egress, RAW_OUTPUT)
        self.assertNotIn(SSN, egress)
        self.assertNotIn(EMAIL, egress)

    async def test_noop_redact_fails_closed_to_block(self):
        resp = {
            "choices": [{"message": {"content": "The project lead is Dana Whitfield."}}],
        }
        verdict = OutputVerdict(
            action="redact",
            threat_type="pii",
            detail="tier-2 name with no regex span",
            matched_patterns=[],
        )
        guard = MagicMock()
        guard.inspect = AsyncMock(return_value=verdict)

        with patch.object(gm, "OUTPUT_GUARD", guard), patch.object(
            gm, "CONFIG", {"output_guard_enabled": True, "pii_detection_enabled": True}
        ), patch.object(gm, "CONFIG_SYNC", None), patch.object(
            gm, "METRICS", {"blocked": 0}
        ), patch.object(gm, "_emit_telemetry"), patch.object(
            gm, "_build_block_response", return_value=MagicMock(status_code=403)
        ) as mock_block, patch.object(
            gm, "_resolve_rag_context_chunks", AsyncMock(return_value=[])
        ), patch.object(
            gm,
            "_sanitize_output_for_verdict",
            return_value="The project lead is Dana Whitfield.",
        ):
            result = await gm._apply_output_guard_nonstream(
                resp,
                org_config={"enforcement_mode": "redact", "output_scan_enabled": True},
                org_slug="zeroshield",
                body={"model": "gpt-test"},
                user_id=1,
                project_id="p1",
                key_prefix="sk-test",
                prompt="hello",
                start=time.perf_counter(),
            )

        mock_block.assert_called_once()
        self.assertIsNotNone(result)
        self.assertEqual(resp["choices"][0]["message"]["content"], "The project lead is Dana Whitfield.")


class PipelineTraceOutputRedactTests(unittest.TestCase):
    def test_final_action_redact_with_masked_output(self):
        verdict = OutputVerdict(action="redact", threat_type="pii", detail="masked email")
        masked = redact_all(RAW_OUTPUT)
        trace = build_pipeline_trace(
            prompt="hello",
            final_action="redact",
            http_status=200,
            zeroshield={
                "action": "redact",
                "reason": "Output guard redacted pii",
                "detection_tier": "output_guard",
            },
            response_text=masked,
            output_scan_verdict=verdict,
        )
        og = {s["name"]: s for s in trace["stages"]}["output_guardrail"]
        self.assertEqual(og["action"], "redact")
        self.assertNotIn(SSN, trace.get("response_text") or masked)


if __name__ == "__main__":
    unittest.main()


class PiiDetectionToggleDoesNotNullifyRedactTests(unittest.TestCase):
    """An unrelated org toggle must not nullify one of the five selectable actions.

    `pii_detection_enabled=False` used to downgrade a §1.7 PII **redact** to
    "allow", so raw SSNs/emails egressed with no enforcement event — while the same
    toggle left PII block/rewrite/flag fully honoured. The §1.7 detector owns its own
    enable flag (output_pii_enabled), checked in OutputGuard.inspect.
    """

    def test_redact_survives_pii_detection_disabled(self):
        d = enforce_output(
            verdict_action="redact", verdict_threat_type="pii",
            pii_detection_enabled=False,
        )
        self.assertEqual(d.action, "redact")

    def test_other_actions_unchanged_by_toggle(self):
        for action in ("block", "rewrite", "flag"):
            with self.subTest(action=action):
                d = enforce_output(
                    verdict_action=action, verdict_threat_type="pii",
                    pii_detection_enabled=False,
                )
                self.assertEqual(d.action, action)

    def test_unmaskable_redact_still_fails_closed_to_block(self):
        # Deliberate fail-closed: cannot mask => must not deliver raw.
        d = enforce_output(
            verdict_action="redact", verdict_threat_type="pii",
            redaction_possible=False,
        )
        self.assertEqual(d.action, "block")
