"""Unit tests for enforcement.resolve_enforcement precedence and lattice."""

from __future__ import annotations

import unittest

from ai_mesh_gateway.enforcement import (
    action_rank,
    max_action,
    normalize_action,
    resolve_and_enforce,
    resolve_enforcement,
    should_apply_redaction,
    should_hard_block,
)


class NormalizeActionTests(unittest.TestCase):
    def test_aliases_map_to_block(self):
        self.assertEqual(normalize_action("block_immediately"), "block")
        self.assertEqual(normalize_action("block_and_alert"), "block")

    def test_empty_defaults_allow(self):
        self.assertEqual(normalize_action(None), "allow")
        self.assertEqual(normalize_action(""), "allow")


class LatticeTests(unittest.TestCase):
    def test_allow_lt_redact_lt_block(self):
        self.assertLess(action_rank("allow"), action_rank("redact"))
        self.assertLess(action_rank("redact"), action_rank("block"))
        self.assertEqual(action_rank("monitor"), action_rank("flag"))

    def test_max_action_picks_highest(self):
        self.assertEqual(max_action("allow", "redact", "monitor"), "redact")
        self.assertEqual(max_action("redact", "block"), "block")


class ResolveEnforcementTests(unittest.TestCase):
    def test_redact_recommendation_enforces_redact(self):
        self.assertEqual(
            resolve_enforcement("redact", org_policy_action=None),
            "redact",
        )

    def test_org_policy_block_overrides_redact_recommendation(self):
        self.assertEqual(
            resolve_enforcement("redact", org_policy_action="block"),
            "block",
        )

    def test_redact_escalates_to_block_when_redaction_impossible(self):
        self.assertEqual(
            resolve_enforcement(
                "redact",
                org_policy_action=None,
                redaction_possible=False,
            ),
            "block",
        )

    def test_redact_does_not_block_on_guard_score_path_without_org_block(self):
        """B-ENF: tier-2 recommended_action=redact must not become block."""
        self.assertEqual(
            resolve_enforcement(
                "redact",
                org_policy_action="allow",
                enforcement_mode="block",
                redaction_possible=True,
            ),
            "redact",
        )

    def test_monitor_mode_downgrades_block_to_monitor(self):
        self.assertEqual(
            resolve_enforcement("block", enforcement_mode="monitor"),
            "monitor",
        )

    def test_policy_redact_before_input_scan_allow(self):
        """Case 1 contract: policy redact wins over later scanner allow."""
        self.assertEqual(
            resolve_enforcement(
                "allow",
                org_policy_action="redact",
            ),
            "redact",
        )

    def test_default_when_no_signal(self):
        self.assertEqual(resolve_enforcement(None), "allow")
        self.assertEqual(resolve_enforcement(None, default_action="monitor"), "monitor")


class RedactMappingTests(unittest.TestCase):
    """PIPELINE-0010: REDACT recommendation → REDACT; block only on
    org_policy=block+enforcement_mode=block OR redaction byte-impossible."""

    # --- resolve_enforcement atomic contract ---

    def test_redact_rec_preserved_under_monitor_posture(self):
        """Policy=block but mode=monitor: downgrade to redact, NOT monitor."""
        self.assertEqual(
            resolve_enforcement(
                "redact",
                org_policy_action="block",
                enforcement_mode="monitor",
                redaction_possible=True,
            ),
            "redact",
        )

    def test_redact_rec_preserved_under_redact_posture(self):
        """Policy=block but mode=redact: preserve redact."""
        self.assertEqual(
            resolve_enforcement(
                "redact",
                org_policy_action="block",
                enforcement_mode="redact",
                redaction_possible=True,
            ),
            "redact",
        )

    def test_unmaskable_pii_blocks_even_under_monitor(self):
        """Redaction byte-impossible overrides monitor posture (fail-closed)."""
        self.assertEqual(
            resolve_enforcement(
                "redact",
                org_policy_action=None,
                enforcement_mode="monitor",
                redaction_possible=False,
            ),
            "block",
        )

    def test_unmaskable_pii_blocks_with_policy_block_under_monitor(self):
        """Policy=block + unmaskable: fail-closed block ignores monitor."""
        self.assertEqual(
            resolve_enforcement(
                "redact",
                org_policy_action="block",
                enforcement_mode="monitor",
                redaction_possible=False,
            ),
            "block",
        )

    def test_injection_block_still_downgrades_to_monitor(self):
        """Injection block under monitor stays monitor (not redact)."""
        self.assertEqual(
            resolve_enforcement(
                "block",
                org_policy_action="block",
                enforcement_mode="monitor",
            ),
            "monitor",
        )

    def test_redact_rec_no_policy_monitor_mode_stays_redact(self):
        """No policy, rec=redact, mode=monitor: redact (max_action=redact)."""
        self.assertEqual(
            resolve_enforcement(
                "redact",
                org_policy_action=None,
                enforcement_mode="monitor",
                redaction_possible=True,
            ),
            "redact",
        )

    def test_redact_rec_policy_block_mode_block_is_block(self):
        """Policy=block + mode=block: block (org explicitly blocks)."""
        self.assertEqual(
            resolve_enforcement(
                "redact",
                org_policy_action="block",
                enforcement_mode="block",
                redaction_possible=True,
            ),
            "block",
        )

    # --- resolve_and_enforce end-to-end contract ---

    def test_e2e_pii_redact_under_monitor_posture(self):
        """PII record: policy=block, mode=monitor → action=redact."""
        decision = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="pii",
            scanner_confidence=0.95,
            org_policy_action="block",
            enforcement_mode="monitor",
            redaction_possible=True,
            pii_detection_enabled=True,
        )
        self.assertEqual(decision.action, "redact")
        self.assertFalse(decision.is_terminal_block)

    def test_e2e_pii_unmaskable_blocks_under_monitor(self):
        """Unmaskable PII under monitor: is_terminal_block=True (fail-closed)."""
        decision = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="pii",
            scanner_confidence=0.95,
            org_policy_action="block",
            enforcement_mode="monitor",
            redaction_possible=False,
            pii_detection_enabled=True,
        )
        self.assertEqual(decision.action, "block")
        self.assertTrue(decision.is_terminal_block)

    def test_e2e_pii_unmaskable_no_policy_blocks(self):
        """Unmaskable PII without explicit policy: fail-closed block."""
        decision = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="pii",
            scanner_confidence=0.95,
            org_policy_action=None,
            enforcement_mode="monitor",
            redaction_possible=False,
            pii_detection_enabled=True,
        )
        self.assertEqual(decision.action, "block")
        self.assertTrue(decision.is_terminal_block)

    def test_e2e_secret_redact_under_monitor(self):
        """Secret: always redacted regardless of enforcement mode."""
        decision = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="secret",
            scanner_confidence=0.99,
            org_policy_action=None,
            enforcement_mode="monitor",
            redaction_possible=True,
            pii_detection_enabled=False,
        )
        self.assertEqual(decision.action, "redact")
        self.assertFalse(decision.is_terminal_block)

    def test_e2e_pii_redact_under_redact_posture(self):
        """PII with enforcement_mode=redact: still redact."""
        decision = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="pii",
            scanner_confidence=0.90,
            org_policy_action="redact",
            enforcement_mode="redact",
            redaction_possible=True,
            pii_detection_enabled=True,
        )
        self.assertEqual(decision.action, "redact")
        self.assertFalse(decision.is_terminal_block)

    def test_e2e_pii_block_recommendation_becomes_redact(self):
        """Scanner says 'block' for PII → downgraded to redact in resolve_and_enforce."""
        decision = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
            scanner_confidence=0.99,
            org_policy_action=None,
            enforcement_mode="block",
            redaction_possible=True,
            pii_detection_enabled=True,
        )
        self.assertEqual(decision.action, "redact")
        self.assertFalse(decision.is_terminal_block)

    def test_e2e_injection_block_allowed_under_block_mode(self):
        """Injection with high confidence under block mode: terminal block."""
        decision = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.95,
            org_policy_action="block",
            enforcement_mode="block",
            redaction_possible=True,
            scan_block_on_injection=True,
            injection_threshold=0.80,
        )
        self.assertEqual(decision.action, "block")
        self.assertTrue(decision.is_terminal_block)

    def test_e2e_injection_monitor_under_monitor_mode(self):
        """Injection under monitor mode: just observe."""
        decision = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.95,
            org_policy_action="block",
            enforcement_mode="monitor",
            redaction_possible=True,
            scan_block_on_injection=True,
            injection_threshold=0.80,
        )
        self.assertIn(decision.action, ("monitor", "allow"))
        self.assertFalse(decision.is_terminal_block)


class HelperTests(unittest.TestCase):
    def test_should_apply_redaction(self):
        self.assertTrue(should_apply_redaction("redact"))
        self.assertTrue(should_apply_redaction("flag", "pii"))
        self.assertFalse(should_apply_redaction("allow"))

    def test_should_hard_block(self):
        self.assertTrue(should_hard_block("block", "block"))
        self.assertFalse(should_hard_block("block", "monitor"))
        self.assertFalse(should_hard_block("redact", "block"))


if __name__ == "__main__":
    unittest.main()
