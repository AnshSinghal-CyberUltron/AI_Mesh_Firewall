"""
Tests for the canonical pipeline enforcement authority (PIPELINE-0004).

Covers PipelineDecision, resolve_and_enforce, and enforce_output — the two
entry points that replace inline divergent logic across all chat pipeline paths.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_mesh_gateway.enforcement import (
    PipelineDecision,
    enforce_output,
    resolve_and_enforce,
)


class PipelineDecisionTests(unittest.TestCase):
    """PipelineDecision is frozen, serializable, and has correct helpers."""

    def test_frozen(self):
        d = PipelineDecision(action="block", blocked_by="input_scan")
        with self.assertRaises(AttributeError):
            d.action = "allow"  # type: ignore[misc]

    def test_is_terminal_block(self):
        self.assertTrue(PipelineDecision(action="block").is_terminal_block)
        self.assertFalse(PipelineDecision(action="redact").is_terminal_block)
        self.assertFalse(PipelineDecision(action="allow").is_terminal_block)

    def test_is_redact(self):
        self.assertTrue(PipelineDecision(action="redact").is_redact)
        self.assertFalse(PipelineDecision(action="block").is_redact)

    def test_as_dict_minimal(self):
        d = PipelineDecision(action="allow")
        out = d.as_dict()
        self.assertEqual(out["action"], "allow")
        self.assertNotIn("blocked_by", out)
        self.assertNotIn("degraded", out)

    def test_as_dict_full(self):
        d = PipelineDecision(
            action="block",
            blocked_by="input_scan",
            threat_type="pii",
            detection_tier="tier_2",
            degraded=True,
            stage_latency_ms=42,
        )
        out = d.as_dict()
        self.assertEqual(out["blocked_by"], "input_scan")
        self.assertTrue(out["degraded"])
        self.assertEqual(out["latency_ms"], 42)


class ResolveAndEnforceInputTests(unittest.TestCase):
    """resolve_and_enforce — canonical input enforcement."""

    def test_clean_allow(self):
        d = resolve_and_enforce(scanner_recommendation="allow")
        self.assertEqual(d.action, "allow")
        self.assertFalse(d.is_terminal_block)

    def test_pii_block_becomes_redact(self):
        """PII is redacted, not hard-blocked, on the input path."""
        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
        )
        self.assertEqual(d.action, "redact")

    def test_secret_block_becomes_redact(self):
        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="secret",
        )
        self.assertEqual(d.action, "redact")

    def test_injection_block_with_high_confidence(self):
        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.95,
            scan_block_on_injection=True,
            injection_threshold=0.80,
        )
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.blocked_by, "input_scan")

    def test_injection_block_below_threshold_becomes_monitor(self):
        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.50,
            scan_block_on_injection=True,
            injection_threshold=0.80,
        )
        self.assertFalse(d.is_terminal_block)

    def test_injection_block_disabled_becomes_monitor(self):
        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.99,
            scan_block_on_injection=False,
        )
        self.assertFalse(d.is_terminal_block)

    def test_org_policy_block_overrides(self):
        d = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="pii",
            org_policy_action="block",
        )
        self.assertTrue(d.is_terminal_block)

    def test_monitor_mode_downgrades_block(self):
        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.99,
            enforcement_mode="monitor",
        )
        self.assertFalse(d.is_terminal_block)

    def test_tier2_degraded_with_pii_redacts(self):
        """Fail-closed contract: degraded + PII threat → redact."""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="pii",
            tier2_degraded=True,
        )
        self.assertEqual(d.action, "redact")
        self.assertTrue(d.degraded)

    def test_tier2_degraded_clean_monitors(self):
        """Fail-closed contract: degraded + clean → monitor."""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type=None,
            tier2_degraded=True,
        )
        self.assertEqual(d.action, "monitor")
        self.assertTrue(d.degraded)

    def test_redaction_impossible_escalates_to_block(self):
        d = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="pii",
            redaction_possible=False,
        )
        self.assertTrue(d.is_terminal_block)

    def test_pii_detection_disabled_no_redact(self):
        """When org disables PII detection, PII is not redacted (secrets still are)."""
        d = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="pii",
            pii_detection_enabled=False,
        )
        self.assertFalse(d.is_redact)

    def test_secret_always_redacted_even_pii_disabled(self):
        d = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="secret",
            pii_detection_enabled=False,
        )
        self.assertTrue(d.is_redact)

    def test_has_latency(self):
        d = resolve_and_enforce(scanner_recommendation="allow")
        self.assertIsInstance(d.stage_latency_ms, int)

    def test_exception_fails_closed(self):
        """Any internal error → block (fail-closed)."""
        with patch(
            "ai_mesh_gateway.enforcement.resolve_enforcement",
            side_effect=RuntimeError("boom"),
        ):
            d = resolve_and_enforce(
                scanner_recommendation="block",
                scanner_threat_type="pii",
                scanner_confidence=0.9,
            )
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.blocked_by, "enforcement_error")


class EnforceOutputTests(unittest.TestCase):
    """enforce_output — canonical output enforcement."""

    def test_allow_verdict(self):
        d = enforce_output(verdict_action="allow")
        self.assertEqual(d.action, "allow")

    def test_block_verdict_non_redactable(self):
        d = enforce_output(verdict_action="block", verdict_threat_type="prompt_injection")
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.blocked_by, "output_guard")

    def test_redact_verdict(self):
        d = enforce_output(verdict_action="redact")
        self.assertEqual(d.action, "redact")

    def test_pii_block_becomes_redact_d14(self):
        """PIPELINE-0014: maskable PII block verdict → redact on output path."""
        d = enforce_output(verdict_action="block", verdict_threat_type="pii")
        self.assertEqual(d.action, "redact")
        self.assertFalse(d.is_terminal_block)

    def test_redact_noop_fails_closed(self):
        d = enforce_output(
            verdict_action="redact",
            verdict_threat_type="pii",
            redaction_possible=False,
        )
        self.assertTrue(d.is_terminal_block)

    def test_exception_fails_closed_d05(self):
        """D-05 fix: guard exception → block, never allow raw pass-through."""
        d = enforce_output(exception=RuntimeError("bedrock unavailable"))
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.blocked_by, "output_guard")
        self.assertEqual(d.threat_type, "guard_exception")
        self.assertTrue(d.degraded)

    def test_degraded_scan_redacts(self):
        d = enforce_output(scan_degraded=True, verdict_action="allow")
        self.assertEqual(d.action, "redact")
        self.assertTrue(d.degraded)

    def test_rewrite_streaming_preserved(self):
        """F-002: rewrite must stay rewrite on stream (DONE flush rewrites)."""
        d = enforce_output(verdict_action="rewrite", is_streaming=True)
        self.assertEqual(d.action, "rewrite")
        self.assertFalse(d.is_terminal_block)

    def test_rewrite_nonstreaming_preserved(self):
        d = enforce_output(verdict_action="rewrite", is_streaming=False)
        self.assertEqual(d.action, "rewrite")

    def test_flag_block_mode_preserved(self):
        """F-003: UI Flag must flag even when org enforcement_mode=block."""
        d = enforce_output(verdict_action="flag", enforcement_mode="block")
        self.assertEqual(d.action, "flag")
        self.assertFalse(d.is_terminal_block)

    def test_flag_monitor_mode_preserved(self):
        d = enforce_output(verdict_action="flag", enforcement_mode="monitor")
        self.assertFalse(d.is_terminal_block)

    def test_exception_in_enforce_output_fails_closed(self):
        """Internal enforcement error → block."""
        with patch(
            "ai_mesh_gateway.enforcement.normalize_action",
            side_effect=RuntimeError("boom"),
        ):
            d = enforce_output(verdict_action="block")
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.blocked_by, "output_guard")


if __name__ == "__main__":
    unittest.main()
