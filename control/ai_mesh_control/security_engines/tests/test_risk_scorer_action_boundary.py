"""Regression: RiskScorer._determine_action security-critical boundaries.

The highest-value invariant: PII + an agentic threat must BLOCK, never redact-
and-forward (the `and not has_agentic` guard). Easy to regress into a silent
"helpfully redact an exfiltration attempt" bug. risk_scorer.py is stdlib-only so
this runs standalone.
"""

import unittest

from security_engines.risk_scorer import RiskScorer


def _pii(score):
    return {"category": "PII/PHI/PCI", "score": score}


def _agentic(score):
    return {"category": "OWASP Agentic AI", "score": score}


def _other(score):
    return {"category": "OWASP LLM", "score": score}


class DetermineActionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.rs = RiskScorer()

    def test_pii_plus_agentic_blocks_never_redacts(self):
        # PII present + low non-PII score would normally redact — but an agentic
        # threat MUST force a block, not redact-and-forward.
        action = self.rs._determine_action(65, [_pii(60), _agentic(50)])
        self.assertNotEqual(action, "redact")
        self.assertTrue(action.startswith("block"), f"expected block, got {action}")

    def test_pii_only_low_nonpii_redacts(self):
        # PII + low non-PII score + NO agentic → redact (sanitise & forward).
        self.assertEqual(self.rs._determine_action(65, [_pii(60)]), "redact")

    def test_pii_plus_high_nonpii_blocks(self):
        # PII but non-PII score >= 70 → not redact; high total → block.
        action = self.rs._determine_action(90, [_pii(10), _other(80)])
        self.assertTrue(action.startswith("block"), f"expected block, got {action}")

    def test_agentic_alone_blocks(self):
        self.assertEqual(self.rs._determine_action(40, [_agentic(40)]), "block_and_alert")

    def test_score_85_block_immediately(self):
        self.assertEqual(self.rs._determine_action(85, [_other(85)]), "block_immediately")

    def test_low_score_allow(self):
        self.assertEqual(self.rs._determine_action(10, [_other(10)]), "allow")


if __name__ == "__main__":
    unittest.main()
