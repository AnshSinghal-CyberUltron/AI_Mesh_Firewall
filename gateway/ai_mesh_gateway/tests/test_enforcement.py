"""Unit tests for enforcement.resolve_enforcement precedence and lattice."""

from __future__ import annotations

import unittest

from ai_mesh_gateway.enforcement import (
    action_rank,
    max_action,
    normalize_action,
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
