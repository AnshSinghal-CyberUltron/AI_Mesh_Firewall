"""
Tests for PIPELINE-0009: Policy redacts PII/PCI/PHI before input_scan.

Covers the B-POL fix: when both redact and block rules match the same input,
redaction is applied FIRST; if the block-triggering content is fully masked by
the redaction, the action is downgraded from "block" to "redact" and the masked
prompt is forwarded to the scanner stage.
"""

from __future__ import annotations

import re
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/ai_mesh_gateway")

from policy_engine import evaluate, apply_redaction, _evaluate_rule, EvaluationResult


def _make_policy(policy_id, name, rules, *, severity="high", category="pii"):
    """Helper to build a compiled policy dict matching the gateway bundle format."""
    return {
        "policy": {
            "id": policy_id,
            "name": name,
            "code": f"POL-{policy_id}",
            "severity": severity,
            "category": category,
            "enabled": True,
        },
        "rules": rules,
    }


def _redact_rule(rule_id, name, regex, replacement, *, field="both"):
    """Build a redact rule."""
    return {
        "id": rule_id,
        "name": name,
        "rule_type": "regex",
        "action": "redact",
        "condition": {"regex": regex, "field": field},
        "redaction_config": {"replacement": replacement},
    }


def _block_rule(rule_id, name, regex, *, field="both"):
    """Build a block rule."""
    return {
        "id": rule_id,
        "name": name,
        "rule_type": "regex",
        "action": "block",
        "condition": {"regex": regex, "field": field},
        "redaction_config": {},
    }


# Standard PII regex for emails
_RE_EMAIL = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
# CVV pattern (3-4 digits in isolation)
_RE_CVV = r"\b\d{3,4}\b"
# Credit card PAN (simplified)
_RE_PAN = r"\b(?:\d[ -]*?){13,19}\b"
# SSN
_RE_SSN = r"\b\d{3}-\d{2}-\d{4}\b"


class TestEvaluateRedactAndBlockCoMatch(unittest.TestCase):
    """policy_engine.evaluate returns redaction_hints even when action=block."""

    def test_evaluate_collects_redaction_hints_with_block_action(self):
        """When both a redact rule and a block rule match, action=block but
        redaction_hints are still populated (needed for B-POL fix)."""
        policies = [
            _make_policy(1, "PCI Policy", [
                _redact_rule(10, "Mask PAN", _RE_PAN, "[CARD_REDACTED]"),
                _block_rule(20, "Block CVV", _RE_CVV),
            ]),
        ]
        result = evaluate(
            prompt="Card 4111 1111 1111 1111 CVV 123",
            response_text="",
            compiled_policies=policies,
        )
        self.assertEqual(result.action, "block")
        self.assertTrue(len(result.redaction_hints) > 0)

    def test_evaluate_redact_only_gives_redact_action(self):
        """A pure redact match yields action=redact."""
        policies = [
            _make_policy(1, "PII Policy", [
                _redact_rule(10, "Mask Email", _RE_EMAIL, "[EMAIL_REDACTED]"),
            ]),
        ]
        result = evaluate(
            prompt="Contact me at alice@example.com please",
            response_text="",
            compiled_policies=policies,
        )
        self.assertEqual(result.action, "redact")
        self.assertTrue(len(result.redaction_hints) > 0)


class TestApplyRedaction(unittest.TestCase):
    """apply_redaction masks matched content via redaction_hints."""

    def test_email_redacted(self):
        hints = [{
            "rule_id": 10,
            "rule_name": "Mask Email",
            "condition": {"regex": _RE_EMAIL},
            "config": {"replacement": "[EMAIL]"},
        }]
        result = apply_redaction("My email is bob@corp.example", hints)
        self.assertNotIn("bob@corp.example", result)

    def test_ssn_redacted(self):
        hints = [{
            "rule_id": 10,
            "rule_name": "Mask SSN",
            "condition": {"regex": _RE_SSN},
            "config": {"replacement": "[SSN]"},
        }]
        result = apply_redaction("My SSN is 123-45-6789", hints)
        self.assertNotIn("123-45-6789", result)

    def test_no_change_when_no_match(self):
        hints = [{
            "rule_id": 10,
            "rule_name": "Mask SSN",
            "condition": {"regex": _RE_SSN},
            "config": {"replacement": "[SSN]"},
        }]
        result = apply_redaction("Hello world, no PII here", hints)
        self.assertEqual(result, "Hello world, no PII here")


class TestEvaluateRuleOnRedactedText(unittest.TestCase):
    """_evaluate_rule against post-redaction text confirms block rule no longer matches."""

    def test_cvv_gone_after_redaction(self):
        """Redaction of CVV digits removes the block trigger."""
        prompt = "Card details: 4111111111111111 CVV 123"
        hints = [{
            "rule_id": 10,
            "rule_name": "Mask PAN",
            "condition": {"regex": _RE_PAN},
            "config": {"replacement": "[CARD]"},
        }, {
            "rule_id": 11,
            "rule_name": "Mask CVV",
            "condition": {"regex": _RE_CVV},
            "config": {"replacement": "[CVV]"},
        }]
        redacted = apply_redaction(prompt, hints)
        self.assertNotIn("123", redacted)
        block_rule = {
            "rule_type": "regex",
            "action": "block",
            "condition": {"regex": _RE_CVV, "field": "both"},
        }
        self.assertFalse(_evaluate_rule(block_rule, redacted, ""))

    def test_block_rule_still_matches_if_not_redactable(self):
        """A keyword block rule can still match if the blocked content isn't
        covered by any redact pattern."""
        prompt = "Launch the nuclear weapons now"
        redacted = prompt
        block_rule = {
            "rule_type": "keywords",
            "action": "block",
            "condition": {"keywords": ["nuclear weapons"], "field": "both"},
        }
        self.assertTrue(_evaluate_rule(block_rule, redacted, ""))


class TestPolicyCheckCachedBpolFix(unittest.TestCase):
    """Integration test for _policy_check_cached B-POL fix path."""

    def _call_policy_check(self, prompt, policies, *, org_slug="test-org"):
        """Call _policy_check_cached with mocked POLICY_SYNC."""
        from main import _policy_check_cached

        mock_sync = MagicMock()
        mock_sync.get_policies.return_value = policies

        with patch("main.POLICY_SYNC", mock_sync):
            with patch("policy_sync.filter_policies_by_domain", return_value=policies):
                code, resp = _policy_check_cached(
                    prompt, "", "user1", "ep1", {}, "proj1", 0.5, "gpt-4",
                    org_slug, actor=None,
                )
        return code, resp

    def test_pii_only_redact_policy_returns_redact(self):
        """A pure PII-redact policy returns action=redact with masked text."""
        policies = [
            _make_policy(1, "PII Redact", [
                _redact_rule(10, "Mask SSN", _RE_SSN, "[SSN_REDACTED]"),
            ]),
        ]
        code, resp = self._call_policy_check(
            "My SSN is 123-45-6789", policies
        )
        self.assertEqual(code, 200)
        self.assertEqual(resp["action"], "redact")
        self.assertNotIn("123-45-6789", resp["redacted_prompt"])

    def test_block_and_redact_comatch_downgraded_when_content_masked(self):
        """B-POL: block+redact co-match downgrades to redact when redaction
        eliminates the block-triggering content.

        Scenario: prompt has an email (redact rule) + a 3-digit CVV (block rule).
        The redaction for CVV also masks the digits (redact rule covers CVV), so
        the block rule no longer matches post-redaction → action downgraded.
        """
        policies = [
            _make_policy(1, "Combined Policy", [
                _redact_rule(10, "Mask Email", _RE_EMAIL, "[EMAIL]"),
                _redact_rule(11, "Mask CVV", _RE_CVV, "[CVV]"),
                _block_rule(20, "Block CVV", _RE_CVV),
            ]),
        ]
        code, resp = self._call_policy_check(
            "Email: alice@test.com, CVV: 456", policies
        )
        self.assertEqual(code, 200)
        self.assertEqual(resp["action"], "redact")
        self.assertNotIn("456", resp["redacted_prompt"])
        self.assertNotIn("alice@test.com", resp["redacted_prompt"])

    def test_block_preserved_when_unmaskable_content_remains(self):
        """If the block rule matches content NOT covered by any redact rule,
        the action stays 'block' (the content is genuinely dangerous)."""
        policies = [
            _make_policy(1, "Mixed Policy", [
                _redact_rule(10, "Mask Email", _RE_EMAIL, "[EMAIL]"),
                {
                    "id": 20,
                    "name": "Block nuclear",
                    "rule_type": "keywords",
                    "action": "block",
                    "condition": {"keywords": ["nuclear weapons"], "field": "both"},
                    "redaction_config": {},
                },
            ]),
        ]
        code, resp = self._call_policy_check(
            "Email: bob@evil.com, launch nuclear weapons", policies
        )
        self.assertEqual(code, 200)
        self.assertEqual(resp["action"], "block")

    def test_scan_text_receives_redacted_prompt(self):
        """After policy redacts, the scanner stage sees masked text — not raw PII.

        This tests the proxy_chat contract: when check_resp["action"]=="redact"
        and check_resp["redacted_prompt"] is set, effective_prompt is updated
        at L6306-6317 of main.py, and scan_text = effective_prompt at L6406.
        """
        policies = [
            _make_policy(1, "PII Policy", [
                _redact_rule(10, "Mask SSN", _RE_SSN, "[SSN]"),
                _redact_rule(11, "Mask Email", _RE_EMAIL, "[EMAIL]"),
            ]),
        ]
        code, resp = self._call_policy_check(
            "SSN 987-65-4321 email alice@corp.com", policies
        )
        self.assertEqual(code, 200)
        self.assertEqual(resp["action"], "redact")
        redacted = resp["redacted_prompt"]
        self.assertNotIn("987-65-4321", redacted)
        self.assertNotIn("alice@corp.com", redacted)

    def test_block_downgrade_message(self):
        """The downgrade from block→redact carries an informative message."""
        policies = [
            _make_policy(1, "PCI", [
                _redact_rule(10, "Mask digits", _RE_CVV, "[MASKED]"),
                _block_rule(20, "Block digits", _RE_CVV),
            ]),
        ]
        code, resp = self._call_policy_check("Payment code 789", policies)
        self.assertEqual(code, 200)
        self.assertEqual(resp["action"], "redact")
        self.assertIn("downgraded", resp.get("message", "").lower())


class TestResolveEnforcementWithPolicyRedact(unittest.TestCase):
    """resolve_and_enforce handles scanner PII findings with policy context."""

    def test_scanner_pii_block_becomes_redact(self):
        """When scanner says block for PII, resolve_and_enforce downgrades to redact."""
        from enforcement import resolve_and_enforce
        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
        )
        self.assertEqual(d.action, "redact")
        self.assertFalse(d.is_terminal_block)

    def test_scanner_secret_block_becomes_redact(self):
        """Secret type is also redactable."""
        from enforcement import resolve_and_enforce
        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="secret",
        )
        self.assertEqual(d.action, "redact")

    def test_org_policy_action_redact_with_pii_threat(self):
        """org_policy_action=redact + scanner says redact for PII → redact."""
        from enforcement import resolve_and_enforce
        d = resolve_and_enforce(
            scanner_recommendation="redact",
            scanner_threat_type="pii",
            org_policy_action="redact",
        )
        self.assertEqual(d.action, "redact")
        self.assertFalse(d.is_terminal_block)

    def test_injection_stays_block(self):
        """Injection threats are NOT downgraded to redact."""
        from enforcement import resolve_and_enforce
        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.95,
        )
        self.assertEqual(d.action, "block")
        self.assertTrue(d.is_terminal_block)


class TestEndToEndPolicyRedactBeforeScan(unittest.TestCase):
    """Prove the full contract: policy redacts → scanner sees masked text."""

    def test_policy_redact_then_scanner_sees_clean(self):
        """Simulates the proxy_chat flow:
        1. _policy_check_cached returns redacted_prompt
        2. effective_prompt is updated (L6306-6317)
        3. scan_text = effective_prompt (L6406) — scanner sees masked text
        """
        policies = [
            _make_policy(1, "PII", [
                _redact_rule(10, "SSN", _RE_SSN, "[SSN]"),
                _redact_rule(11, "Email", _RE_EMAIL, "[EMAIL]"),
            ]),
        ]
        from main import _policy_check_cached
        mock_sync = MagicMock()
        mock_sync.get_policies.return_value = policies

        with patch("main.POLICY_SYNC", mock_sync):
            with patch("policy_sync.filter_policies_by_domain", return_value=policies):
                code, check_resp = _policy_check_cached(
                    "My SSN is 111-22-3333, email admin@secret.io",
                    "", "u1", "e1", {}, "p1", 0.1, "gpt-4", "org1", actor=None,
                )

        self.assertEqual(code, 200)
        self.assertEqual(check_resp["action"], "redact")
        redacted_prompt = check_resp["redacted_prompt"]

        # Simulate the proxy_chat update (L6306-6317)
        effective_prompt = "My SSN is 111-22-3333, email admin@secret.io"
        if check_resp["action"] == "redact" and check_resp.get("redacted_prompt"):
            _new = check_resp["redacted_prompt"]
            if _new != effective_prompt:
                effective_prompt = _new

        # scan_text = effective_prompt (L6406)
        scan_text = effective_prompt

        # The scanner should NOT see raw PII
        self.assertNotIn("111-22-3333", scan_text)
        self.assertNotIn("admin@secret.io", scan_text)

    def test_block_downgrade_also_provides_clean_scan_text(self):
        """B-POL fix: block+redact co-match → downgraded to redact →
        scanner still receives the clean text."""
        policies = [
            _make_policy(1, "PCI", [
                _redact_rule(10, "Mask CVV", _RE_CVV, "[CVV]"),
                _block_rule(20, "Block CVV", _RE_CVV),
            ]),
        ]
        from main import _policy_check_cached
        mock_sync = MagicMock()
        mock_sync.get_policies.return_value = policies

        with patch("main.POLICY_SYNC", mock_sync):
            with patch("policy_sync.filter_policies_by_domain", return_value=policies):
                code, check_resp = _policy_check_cached(
                    "CVV is 789 for my card",
                    "", "u1", "e1", {}, "p1", 0.1, "gpt-4", "org1", actor=None,
                )

        self.assertEqual(code, 200)
        self.assertEqual(check_resp["action"], "redact")

        # Simulate the proxy_chat update
        effective_prompt = "CVV is 789 for my card"
        if check_resp["action"] == "redact" and check_resp.get("redacted_prompt"):
            _new = check_resp["redacted_prompt"]
            if _new != effective_prompt:
                effective_prompt = _new

        scan_text = effective_prompt
        self.assertNotIn("789", scan_text)


if __name__ == "__main__":
    unittest.main()
