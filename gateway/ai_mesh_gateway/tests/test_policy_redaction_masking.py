"""M-17: smart-masking pattern-kind classification + guarded maskers.

Covers the two defects:
  1. brittle substring-sniffing of the regex string replaced by structured
     classification (explicit kind > preset > rule-name tokens > regex-shape
     probe > full mask);
  2. the email mask no longer IndexErrors on values without '@' and no longer
     leaks fragments of multi-'@' values — both fall back to the full mask.

Invariant under test: uncertain classification NEVER produces a partial mask.
"""

import unittest

from ai_mesh_gateway.policy_engine import (
    apply_redaction,
    evaluate_mcp_policies,
)

EMAIL_REGEX = r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
SSN_REGEX = r"\b\d{3}-\d{2}-\d{4}\b"
CARD_REGEX = r"\b(?:\d[ -]*?){13,19}\b"
PHONE_REGEX = r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}"


def _hint(regex=None, replacement=None, **extra):
    config = {}
    if regex is not None:
        config["regex"] = regex
    if replacement is not None:
        config["replacement"] = replacement
    base = {"rule_id": 1, "rule_name": extra.pop("rule_name", ""), "config": config, "condition": {}}
    base.update(extra)
    return base


class EmailMaskGuardTests(unittest.TestCase):
    """Defect 2: unsafe '@'-split in the email mask lambda."""

    def test_valid_email_smart_masked(self):
        out = apply_redaction(
            "contact john.doe@example.com today",
            [_hint(regex=EMAIL_REGEX, rule_name="Redact email addresses")],
        )
        self.assertIn("j***@e***.com", out)
        self.assertNotIn("john.doe@example.com", out)

    def test_value_without_at_sign_full_masked_no_crash(self):
        # Old code: regex string contains '@' and '[A-Za-z' -> email lambda ->
        # split('@', 1)[1] raised IndexError on a match without '@'.
        hints = [_hint(regex=r"\bjohndoe\b", replacement="[REDACTED]", pattern_kind="email")]
        out = apply_redaction("user johndoe logged in", hints)
        self.assertEqual(out, "user [REDACTED] logged in")

    def test_old_sniffer_shaped_pattern_without_at_match_no_crash(self):
        # Regex that the OLD substring-sniffer classified as email ('@' in
        # normalized + '[A-Za-z' present) but that matches values with no '@'.
        hints = [_hint(regex=r"[A-Za-z]+@?", replacement="[REDACTED]")]
        out = apply_redaction("hello", hints)  # old code: IndexError
        self.assertEqual(out, "[REDACTED]")

    def test_multiple_at_signs_full_masked(self):
        hints = [_hint(regex=r"\S+@\S+\.\S+", replacement="[REDACTED]", pattern_kind="email")]
        out = apply_redaction("leak a@b@c.com here", hints)
        self.assertEqual(out, "leak [REDACTED] here")
        # Never leak fragments of the second segment (old behavior: a***@b***.com).
        self.assertNotIn("b***", out)
        self.assertNotIn("c.com", out)

    def test_email_without_dotted_domain_full_masked(self):
        hints = [_hint(regex=r"\S+@\S+", replacement="[REDACTED]", pattern_kind="email")]
        out = apply_redaction("ping admin@localhost now", hints)
        self.assertEqual(out, "ping [REDACTED] now")


class PatternKindClassificationTests(unittest.TestCase):
    """Defect 1: structured classification instead of regex-string sniffing."""

    def test_ssn_like_smart_masked_via_regex_shape(self):
        out = apply_redaction("ssn is 123-45-6789 ok", [_hint(regex=SSN_REGEX)])
        self.assertIn("***-**-6789", out)
        self.assertNotIn("123-45", out)

    def test_card_smart_masked_via_regex_shape(self):
        out = apply_redaction(
            "card 4111 1111 1111 1111 on file",
            [_hint(regex=CARD_REGEX, rule_name="Redact credit card numbers")],
        )
        self.assertIn("****-****-****-1111", out)
        self.assertNotIn("4111 1111", out)

    def test_phone_smart_masked_via_regex_shape(self):
        out = apply_redaction("call 555-123-4567 now", [_hint(regex=PHONE_REGEX)])
        self.assertIn("***-***-4567", out)
        self.assertNotIn("555-123", out)

    def test_generic_secret_fully_replaced(self):
        secret = "sk-abcdef1234567890abcdef"
        hints = [_hint(
            regex=r"\b(?:sk|api)[-_][A-Za-z0-9]{16,}\b",
            replacement="[REDACTED_SECRET]",
        )]
        out = apply_redaction(f"key {secret} end", hints)
        self.assertEqual(out, "key [REDACTED_SECRET] end")
        self.assertNotIn("abcdef", out)

    def test_uncertain_broad_regex_full_masked(self):
        # Fullmatches BOTH the SSN and phone exemplars -> ambiguous -> full mask.
        broad = r"\d{3}[-.\s]?\d{2,4}[-.\s]?\d{4}"
        out = apply_redaction(
            "id 123-45-6789 end",
            [_hint(regex=broad, replacement="[REDACTED]")],
        )
        self.assertEqual(out, "id [REDACTED] end")
        self.assertNotIn("6789", out)

    def test_conflicting_rule_name_tokens_full_masked(self):
        # Name claims two kinds -> uncertain -> full mask even for a clean email.
        hints = [_hint(
            regex=EMAIL_REGEX,
            replacement="[REDACTED]",
            rule_name="Email and SSN redaction",
        )]
        out = apply_redaction("mail a@b.com end", hints)
        self.assertEqual(out, "mail [REDACTED] end")

    def test_rule_name_kind_with_mismatched_value_full_masked(self):
        # Metadata says SSN, but the matched value is not SSN-shaped (10 digits):
        # the per-match guard refuses the partial mask.
        hints = [_hint(
            regex=r"\b\d{3}-\d{2}-\d{5}\b",
            replacement="[REDACTED]",
            rule_name="Redact SSN",
        )]
        out = apply_redaction("id 123-45-67890 end", hints)
        self.assertEqual(out, "id [REDACTED] end")

    def test_explicit_unknown_pattern_kind_full_masked(self):
        hints = [_hint(regex=EMAIL_REGEX, replacement="[REDACTED]", pattern_kind="dna_sequence")]
        out = apply_redaction("mail a@b.com end", hints)
        self.assertEqual(out, "mail [REDACTED] end")

    def test_invalid_regex_skipped(self):
        out = apply_redaction("text 123-45-6789", [_hint(regex=r"([unclosed")])
        self.assertEqual(out, "text 123-45-6789")

    def test_keyword_hints_still_replaced(self):
        hints = [{
            "rule_id": 2,
            "rule_name": "kw",
            "config": {"keywords": ["secret-project"], "replacement": "[REDACTED]"},
            "condition": {},
        }]
        out = apply_redaction("about secret-project now", hints)
        self.assertEqual(out, "about [REDACTED] now")


class McpPresetHintIntegrationTests(unittest.TestCase):
    """MCP hints carry a structured `preset` field — classification uses it."""

    @staticmethod
    def _policies(preset, action="redact"):
        return [{
            "policy": {"id": 1, "code": "MCP-RED", "name": "MCP Redact", "severity": "high",
                       "category": "data_protection"},
            "rules": [{
                "id": 11,
                "name": "Preset redact rule",
                "rule_type": "regex",
                "action": action,
                "condition": {"preset": preset, "direction": "both"},
                "redaction_config": {},
            }],
        }]

    def test_email_preset_hint_smart_masks(self):
        result = evaluate_mcp_policies(
            self._policies("email"),
            {"prompt": "reach me at jane@acme.io", "input_args": {}, "output_data": {}},
        )
        self.assertEqual(result.action, "redact")
        self.assertTrue(result.redaction_hints)
        out = apply_redaction("reach me at jane@acme.io", result.redaction_hints)
        self.assertIn("j***@a***.io", out)
        self.assertNotIn("jane@acme.io", out)

    def test_ssn_preset_hint_smart_masks(self):
        result = evaluate_mcp_policies(
            self._policies("us_ssn"),
            {"prompt": "ssn 123-45-6789", "input_args": {}, "output_data": {}},
        )
        out = apply_redaction("ssn 123-45-6789", result.redaction_hints)
        self.assertIn("***-**-6789", out)
        self.assertNotIn("123-45", out)

    def test_api_key_preset_hint_fully_masks(self):
        secret = "sk-abcdef1234567890abcdef"
        result = evaluate_mcp_policies(
            self._policies("api_key"),
            {"prompt": f"key {secret}", "input_args": {}, "output_data": {}},
        )
        out = apply_redaction(f"key {secret}", result.redaction_hints)
        self.assertEqual(out, "key [REDACTED_SECRET]")
        self.assertNotIn("abcdef", out)


if __name__ == "__main__":
    unittest.main()
