"""
PIPELINE-0006: Degraded scanner fails CLOSED — no raw PII to the model.

Tests the fail-closed contract for degraded/unavailable Tier-2 scanners:
  - tier2_degraded + Tier-1 detected PII/secret → REDACT
  - tier2_degraded + Tier-1 clean → monitor (no block)
  - tier2_degraded + PII + unmaskable → BLOCK
  - output scan_degraded → REDACT

Byte-verification: raw PII must never appear in model-bound payload when degraded.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ai_mesh_gateway.enforcement import (
    PipelineDecision,
    enforce_output,
    resolve_and_enforce,
)


class DegradedInputEnforcementTests(unittest.TestCase):
    """resolve_and_enforce fail-closed contract under tier2_degraded."""

    def test_degraded_with_tier1_pii_detected_redacts(self):
        """When degraded and patterns detect PII in text → redact."""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=True,
        )
        self.assertEqual(d.action, "redact")
        self.assertTrue(d.degraded)
        self.assertFalse(d.is_terminal_block)

    def test_degraded_with_tier1_pii_detected_secret_redacts(self):
        """Secret detection under degraded also triggers redact."""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=True,
            pii_detection_enabled=True,
        )
        self.assertEqual(d.action, "redact")
        self.assertTrue(d.degraded)

    def test_degraded_clean_prompt_monitors(self):
        """Clean prompt under degraded → monitor only (no block, no redact)."""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=False,
        )
        self.assertEqual(d.action, "monitor")
        self.assertTrue(d.degraded)
        self.assertFalse(d.is_terminal_block)
        self.assertFalse(d.is_redact)

    def test_degraded_pii_unmaskable_blocks(self):
        """Degraded + PII detected + redaction impossible → BLOCK (fail-closed)."""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=True,
            redaction_possible=False,
        )
        self.assertEqual(d.action, "block")
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.blocked_by, "input_scan")
        self.assertTrue(d.degraded)

    def test_degraded_pii_threat_type_still_redacts(self):
        """If verdict somehow has a PII threat_type while degraded → redact.
        (Validates the original L296 branch still works.)"""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="pii",
            tier2_degraded=True,
            tier1_pii_detected=False,
        )
        self.assertEqual(d.action, "redact")
        self.assertTrue(d.degraded)

    def test_degraded_reason_code_monitors_when_clean(self):
        """Degraded via reason_code (not bedrock_degraded) + clean → monitor."""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type=None,
            tier2_degraded=True,
            tier1_pii_detected=False,
        )
        self.assertEqual(d.action, "monitor")
        self.assertTrue(d.degraded)

    def test_degraded_pii_with_monitor_mode_still_redacts(self):
        """Even in monitor enforcement mode, degraded + PII → redact (security)."""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=True,
            enforcement_mode="monitor",
        )
        self.assertEqual(d.action, "redact")
        self.assertTrue(d.degraded)

    def test_non_degraded_tier1_pii_detected_ignored(self):
        """tier1_pii_detected has no effect when NOT degraded."""
        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type=None,
            tier2_degraded=False,
            tier1_pii_detected=True,
        )
        self.assertEqual(d.action, "allow")
        self.assertFalse(d.degraded)

    def test_degraded_exception_blocks(self):
        """An exception inside resolve_and_enforce → block (fail-closed)."""
        with patch(
            "ai_mesh_gateway.enforcement.resolve_enforcement",
            side_effect=RuntimeError("boom"),
        ):
            d = resolve_and_enforce(
                scanner_recommendation="allow",
                tier2_degraded=True,
                tier1_pii_detected=True,
            )
        self.assertEqual(d.action, "block")
        self.assertEqual(d.blocked_by, "enforcement_error")


class DegradedOutputEnforcementTests(unittest.TestCase):
    """enforce_output fail-closed contract under scan_degraded."""

    def test_output_degraded_redacts(self):
        """Output scan degraded → REDACT (not allow, not block)."""
        d = enforce_output(scan_degraded=True)
        self.assertEqual(d.action, "redact")
        self.assertTrue(d.degraded)
        self.assertFalse(d.is_terminal_block)

    def test_output_exception_blocks(self):
        """Exception in output guard → BLOCK (fail-closed, D-05 fix)."""
        d = enforce_output(exception=RuntimeError("guard crash"))
        self.assertEqual(d.action, "block")
        self.assertTrue(d.degraded)
        self.assertTrue(d.is_terminal_block)

    def test_output_normal_allows(self):
        """Non-degraded, clean verdict → allow."""
        d = enforce_output(verdict_action="allow")
        self.assertEqual(d.action, "allow")
        self.assertFalse(d.is_terminal_block)


class DegradedByteVerificationTests(unittest.TestCase):
    """Byte-level verification: raw PII must not survive degraded redaction.

    Mocks the scanner verdict as degraded, patches detect_pii to report PII,
    and verifies the redacted prompt has no raw PII bytes.
    """

    def test_redact_all_masks_ssn(self):
        """Direct byte-verify: redact_all masks SSN pattern."""
        try:
            from patterns import redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import redact_all
        raw = "My SSN is 123-45-6789 and email is john@corp.example"
        masked = redact_all(raw)
        self.assertNotIn("123-45-6789", masked)
        self.assertNotIn("john@corp.example", masked)

    def test_redact_all_masks_aws_key(self):
        """Direct byte-verify: redact_all masks AWS access key."""
        try:
            from patterns import redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import redact_all
        raw = "Key: AKIAIOSFODNN7EXAMPLE and secret"
        masked = redact_all(raw)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", masked)

    def test_detect_pii_finds_ssn(self):
        """detect_pii returns True for SSN patterns."""
        try:
            from patterns import detect_pii
        except ImportError:
            from ai_mesh_gateway.patterns import detect_pii
        self.assertTrue(detect_pii("My SSN is 123-45-6789"))
        self.assertFalse(detect_pii("Hello, world!"))

    def test_detect_pii_finds_aws_key(self):
        """detect_pii finds AWS access key (aws_access_key in PII_PATTERNS)."""
        try:
            from patterns import detect_pii
        except ImportError:
            from ai_mesh_gateway.patterns import detect_pii
        self.assertTrue(detect_pii("Key AKIAIOSFODNN7EXAMPLE here"))
        self.assertFalse(detect_pii("Hello, world!"))


class DegradedPipelineIntegrationTests(unittest.TestCase):
    """Integration: the enforcement→detection→redaction chain under degraded.

    Simulates what proxy_chat does: when verdict is degraded and PII is detected,
    the enforcement decision is 'redact' and redaction removes raw PII.
    """

    def test_degraded_with_pii_prompt_is_redacted(self):
        """Full chain: degraded + PII prompt → redacted before model."""
        try:
            from patterns import detect_pii, detect_secrets, detect_credential_exposure, redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import (
                detect_pii, detect_secrets, detect_credential_exposure, redact_all,
            )

        raw_prompt = "Please process my SSN 123-45-6789 and card 4111-1111-1111-1111"

        pii_detected = bool(
            detect_pii(raw_prompt) or detect_secrets(raw_prompt)
            or detect_credential_exposure(raw_prompt)
        )
        self.assertTrue(pii_detected, "PII should be detected in the raw prompt")

        decision = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=pii_detected,
        )
        self.assertEqual(decision.action, "redact")

        redacted = redact_all(raw_prompt)
        self.assertNotIn("123-45-6789", redacted)
        self.assertNotIn("4111-1111-1111-1111", redacted)
        self.assertNotEqual(raw_prompt, redacted)

    def test_degraded_clean_prompt_goes_through(self):
        """Full chain: degraded + clean prompt → monitor (no redaction)."""
        try:
            from patterns import detect_pii, detect_secrets, detect_credential_exposure
        except ImportError:
            from ai_mesh_gateway.patterns import (
                detect_pii, detect_secrets, detect_credential_exposure,
            )

        clean_prompt = "What is the capital of France?"

        pii_detected = bool(
            detect_pii(clean_prompt) or detect_secrets(clean_prompt)
            or detect_credential_exposure(clean_prompt)
        )
        self.assertFalse(pii_detected, "Clean prompt should have no PII")

        decision = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=pii_detected,
        )
        self.assertEqual(decision.action, "monitor")
        self.assertFalse(decision.is_redact)

    def test_degraded_credential_prompt_is_redacted(self):
        """Credentials (AWS key, API key) under degraded → redact."""
        try:
            from patterns import detect_pii, detect_secrets, detect_credential_exposure, redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import (
                detect_pii, detect_secrets, detect_credential_exposure, redact_all,
            )

        raw_prompt = "Use my AWS key AKIAIOSFODNN7EXAMPLE to deploy"

        pii_detected = bool(
            detect_pii(raw_prompt) or detect_secrets(raw_prompt)
            or detect_credential_exposure(raw_prompt)
        )
        self.assertTrue(pii_detected)

        decision = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=pii_detected,
        )
        self.assertEqual(decision.action, "redact")

        redacted = redact_all(raw_prompt)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted)


if __name__ == "__main__":
    unittest.main()
