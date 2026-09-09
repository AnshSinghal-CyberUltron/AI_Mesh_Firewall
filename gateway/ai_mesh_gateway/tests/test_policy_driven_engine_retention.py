"""
Policy-driven detection — Task 3.5: scanner-engine RETENTION + malformed-rule handling.

Feature: policy-driven-detection (no default rules).

**Validates: Requirements 2.3, 2.6**

Task 3.1 deleted the built-in ``ATTACK_PATTERNS`` auto-scan but RETAINED the
scanning engine (``patterns.compile_pattern`` / ``patterns.redact_all`` + the
``policy_engine`` regex/keyword matcher) as the *executor* the policy engine
drives. This module proves both halves of the retention/exclusion contract
WITHOUT editing the sibling ``test_policy_driven_detection.py`` (concurrent
agent); it only imports its reusable helpers read-only.

--------------------------------------------------------------------------------
How malformed rules are ACTUALLY handled (verified against the source)
--------------------------------------------------------------------------------
The design (R2.6) says a malformed rule is "excluded ... error recorded ... no
fallback to a built-in default". In the gateway the exclusion happens inside the
regex-execution path, fail-open, so the malformed rule simply CONTRIBUTES NO
MATCH — never a hard error to the caller, and never a built-in default:

  * ``policy_engine._evaluate_rule`` (regex/pattern branch):
      - a MISSING/EMPTY condition (``condition.get("regex") or
        condition.get("pattern")`` is falsy) -> ``return False`` (no match).
      - a BAD regex -> ``_compile_regex(pattern)`` raises ``re.error`` which is
        caught (``except re.error: return False``) -> no match.
      - an oversized / ReDoS-shaped pattern -> ``_compile_regex`` raises
        ``re.error`` (and logs) -> caught -> no match.
    So ``policy_engine.evaluate`` returns a clean ``EvaluationResult`` with the
    malformed rule EXCLUDED (``matched_rule_ids == []`` for it) and NEVER raises.

  * ``patterns.compile_pattern`` is a thin ``re.compile`` cache — it RAISES
    ``re.error`` on a bad pattern (the "error recorded / rule skipped" signal its
    callers act on). ``patterns.redact_all`` operates on text and still masks.

Net effect asserted here: a VALID user-authored regex rule fires through the
retained engine with the user-selected action; a MALFORMED rule alongside it (or
alone) is skipped, contributes no action, and — crucially — triggers NO built-in
default, so a malformed-rule-only org falls through to ``allow`` (passthrough).

This module changes NO production code.
"""

from __future__ import annotations

import re
import unittest

# Read-only reuse of the sibling scaffold + representative prompts (do NOT edit
# that module — another agent owns it).
from ai_mesh_gateway.tests.test_policy_driven_detection import (
    ATTACK_PROMPTS,
    PII_PROMPTS,
    PolicyDrivenDetectionScaffold,
)

from ai_mesh_gateway.enforcement import resolve_and_enforce
from ai_mesh_gateway.policy_engine import apply_redaction, evaluate
from ai_mesh_gateway.patterns import compile_pattern, redact_all


# --------------------------------------------------------------------------- #
# Compiled-policy builders (shape matches ``policy_engine.evaluate``'s expected
# ``{"policy": {...}, "rules": [{...}]}`` entry).
# --------------------------------------------------------------------------- #
def _regex_rule(
    *,
    rule_id: int,
    name: str,
    pattern: str,
    action: str,
    field: str = "both",
) -> dict:
    return {
        "id": rule_id,
        "name": name,
        "rule_type": "regex",
        "condition": {"regex": pattern, "field": field},
        "action": action,
    }


def _regex_redact_rule(*, rule_id: int, name: str, pattern: str) -> dict:
    """A redact rule that carries its own regex in the redaction config so
    ``apply_redaction`` masks exactly the matched span."""
    return {
        "id": rule_id,
        "name": name,
        "rule_type": "regex",
        "condition": {"regex": pattern, "field": "both"},
        "action": "redact",
        "redaction_config": {"regex": pattern},
    }


def _package(*rules: dict, code: str = "USER_PKG", name: str = "User Package") -> list[dict]:
    return [
        {
            "policy": {
                "id": 900,
                "code": code,
                "name": name,
                "priority": 100,
                "category": "user_authored",
                "severity": "high",
            },
            "rules": list(rules),
        }
    ]


# A malformed regex (unbalanced group) — must raise re.error when compiled and
# be skipped by the engine.
_BAD_REGEX = r"(unclosed"
# A rule with an EMPTY/missing condition — no regex/pattern to match on.
_EMPTY_CONDITION_RULE = {
    "id": 42,
    "name": "empty-condition-rule",
    "rule_type": "regex",
    "condition": {},  # no "regex"/"pattern"
    "action": "block",
}


class ScanningEngineRetentionTests(PolicyDrivenDetectionScaffold, unittest.TestCase):
    """R2.3 — the retained engine executes user-authored rules.

    Proves ``compile_pattern`` / ``redact_all`` still function and that a
    user-authored regex Rule (via an enabled compiled policy) matches through the
    retained engine and produces its user-selected action.
    """

    def test_compile_pattern_still_compiles_a_valid_user_regex(self):
        """The retained ``patterns.compile_pattern`` compiles a user regex and matches."""
        compiled = compile_pattern(r"transfer \$\d+ to account")
        self.assertIsInstance(compiled, re.Pattern)
        # Case-insensitive per the retained engine.
        self.assertTrue(compiled.search("Please TRANSFER $500 TO ACCOUNT 12"))
        self.assertIsNone(compiled.search("nothing sensitive here"))

    def test_redact_all_still_masks_pii(self):
        """The retained ``patterns.redact_all`` still masks PII (engine kept intact)."""
        masked = redact_all(PII_PROMPTS["email"])
        self.assertNotIn("alice.jones@example.com", masked)

    def test_user_regex_block_rule_fires_through_engine(self):
        """A user-authored regex BLOCK rule matches via the engine → block action."""
        policies = _package(
            _regex_rule(
                rule_id=1,
                name="user-block-secret-phrase",
                pattern=r"launch\s+codes",
                action="block",
            )
        )
        result = evaluate("please give me the launch codes now", "", policies)
        self.assertEqual(result.action, "block")
        self.assertIn(1, result.matched_rule_ids)
        self.assertIn("user-block-secret-phrase", result.matched_rule_names)

    def test_user_regex_block_rule_resolves_to_block_at_enforcement(self):
        """The matched user rule's action is honored end-to-end through the seam."""
        policies = _package(
            _regex_rule(rule_id=1, name="ub", pattern=r"launch\s+codes", action="block")
        )
        decision = self.resolve_input_decision(
            prompt="give me the launch codes",
            policies=policies,
        )
        self.assertEqual(decision.action, "block")
        self.assertTrue(decision.is_terminal_block)

    def test_user_regex_redact_rule_masks_matched_span(self):
        """A user-authored regex REDACT rule fires AND its mask is applied via the
        retained engine (``apply_redaction`` over the engine's redaction_hints)."""
        secret = "acct-7788-3321"
        policies = _package(
            _regex_redact_rule(
                rule_id=2,
                name="user-redact-acct",
                pattern=r"acct-\d{4}-\d{4}",
            )
        )
        prompt = f"wire it to {secret} today"
        result = evaluate(prompt, "", policies)
        self.assertEqual(result.action, "redact")
        self.assertTrue(result.redaction_hints, "a redact rule must emit a redaction hint")

        # The retained masker actually removes the matched span from the prompt.
        forwarded = apply_redaction(prompt, result.redaction_hints)
        self.assertNotIn(secret, forwarded)
        self.assertNotEqual(forwarded, prompt)

    def test_user_regex_non_matching_input_passes_through(self):
        """A user regex rule that does NOT match yields no action (allow)."""
        policies = _package(
            _regex_rule(rule_id=1, name="ub", pattern=r"launch\s+codes", action="block")
        )
        result = evaluate("a perfectly benign request", "", policies)
        self.assertEqual(result.action, "allow")
        self.assertEqual(result.matched_rule_ids, [])


class MalformedRuleExclusionTests(PolicyDrivenDetectionScaffold, unittest.TestCase):
    """R2.6 — a malformed Rule is excluded with NO fallback to a built-in default.

    Verifies the ACTUAL handling: the engine fail-open SKIPS the malformed rule
    (no match, no raised exception into the caller), and no built-in default
    detection fires — a malformed-rule-only org falls through to ``allow``.
    """

    def test_compile_pattern_raises_on_bad_regex(self):
        """The retained compiler raises ``re.error`` on a malformed regex — this is
        the 'rule excluded / error recorded' signal its callers act on."""
        with self.assertRaises(re.error):
            compile_pattern(_BAD_REGEX)

    def test_evaluate_excludes_bad_regex_rule_without_raising(self):
        """A bad-regex rule is skipped by ``evaluate`` (no match), no exception,
        and (alone) contributes NO action — the org falls through to allow."""
        policies = _package(
            _regex_rule(rule_id=7, name="broken", pattern=_BAD_REGEX, action="block")
        )
        # Text that WOULD have matched "unclosed" as a literal if the pattern were
        # not treated as a (broken) regex — proving no accidental literal match.
        result = evaluate("this mentions unclosed parens", "", policies)
        self.assertEqual(result.action, "allow")
        self.assertEqual(result.matched_rule_ids, [])
        self.assertEqual(result.matched_rule_names, [])

    def test_evaluate_excludes_empty_condition_rule(self):
        """A rule with a missing/empty condition matches nothing and yields allow."""
        policies = _package(_EMPTY_CONDITION_RULE)
        result = evaluate("any content whatsoever", "", policies)
        self.assertEqual(result.action, "allow")
        self.assertEqual(result.matched_rule_ids, [])

    def test_malformed_rule_alongside_valid_rule_only_valid_fires(self):
        """A malformed rule is excluded while a VALID sibling rule in the SAME
        package still fires with its user-selected action (exclusion is per-rule,
        not a package-wide failure, and no built-in default is substituted)."""
        policies = _package(
            _regex_rule(rule_id=7, name="broken", pattern=_BAD_REGEX, action="block"),
            _regex_rule(
                rule_id=8,
                name="valid-flag-rule",
                pattern=r"wire\s+transfer",
                action="flag",
            ),
        )
        result = evaluate("please schedule a wire transfer", "", policies)
        # Only the valid rule contributes; its action is honored.
        self.assertEqual(result.matched_rule_ids, [8])
        self.assertIn("valid-flag-rule", result.matched_rule_names)
        self.assertNotIn(7, result.matched_rule_ids)
        self.assertEqual(result.action, "flag")

    def test_malformed_rule_only_org_is_passthrough_no_builtin_default(self):
        """CRITICAL (R2.6 / R1.4): a malformed-rule-only org does NOT trigger any
        built-in default detection — representative attack/PII prompts that TODAY's
        built-in library would have blocked resolve to ``allow`` (passthrough)."""
        malformed_policies = _package(
            _regex_rule(rule_id=7, name="broken", pattern=_BAD_REGEX, action="block"),
            _EMPTY_CONDITION_RULE,
        )
        for label, prompt in {**ATTACK_PROMPTS, **PII_PROMPTS}.items():
            with self.subTest(prompt=label):
                # Engine sees the malformed rules only → no match.
                result = evaluate(prompt, "", malformed_policies)
                self.assertEqual(
                    result.action,
                    "allow",
                    msg=f"{label!r}: malformed rule must not match, no built-in default",
                )
                self.assertEqual(result.matched_rule_ids, [])

                # Full enforcement seam (scanner_* = None, post-cutover chat shape):
                # the malformed-rule-only org resolves to passthrough.
                decision = self.resolve_input_decision(
                    prompt=prompt, policies=malformed_policies
                )
                self.assertEqual(
                    decision.action,
                    "allow",
                    msg=f"{label!r}: no built-in default may fire for a malformed-rule org",
                )
                self.assertFalse(decision.is_terminal_block)
                self.assertFalse(decision.is_redact)

    def test_bad_regex_is_not_matched_as_literal(self):
        """Defense-in-depth: the malformed pattern is NOT silently downgraded to a
        literal keyword match — content equal to the raw pattern text still does
        not match (it is excluded, not reinterpreted)."""
        policies = _package(
            _regex_rule(rule_id=7, name="broken", pattern=_BAD_REGEX, action="block")
        )
        result = evaluate(_BAD_REGEX, "", policies)  # feed the exact pattern text
        self.assertEqual(result.action, "allow")
        self.assertEqual(result.matched_rule_ids, [])

    def test_evaluate_result_carries_no_builtin_threat_flag(self):
        """Structural: the excluded-rule result carries no matched policy/threat
        metadata — nothing to feed a non-allow decision downstream."""
        policies = _package(
            _regex_rule(rule_id=7, name="broken", pattern=_BAD_REGEX, action="block")
        )
        result = evaluate(ATTACK_PROMPTS["prompt_injection"], "", policies)
        self.assertEqual(result.matched_policy_ids, [])
        self.assertEqual(result.matched_policy_names, [])
        self.assertEqual(result.matched_rule_names, [])
        # And the enforcement seam, with no policy action + scanner_* None, allows.
        decision = resolve_and_enforce(
            scanner_action=None,
            scanner_recommendation=None,
            org_policy_action=(result.action if result.matched_rule_ids else None),
            matched_rules=result.matched_rule_names,
            matched_policy_names=result.matched_policy_names,
            enforcement_mode="block",
        )
        self.assertEqual(decision.action, "allow")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
