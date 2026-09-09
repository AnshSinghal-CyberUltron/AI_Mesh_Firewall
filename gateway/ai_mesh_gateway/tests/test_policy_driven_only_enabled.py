"""
Policy-driven detection — Task 3.3 property test: only enabled rules fire.

**Feature: policy-driven-detection, Property 3: For any input and any set of
packages, a Rule produces a verdict if and only if it belongs to a package that
is enabled for that organization; a Rule in a disabled or non-enabled package
never contributes an action.**

**Validates: Requirements 1.3, 4.3, 4.4**

--------------------------------------------------------------------------------
How enabled vs disabled/non-enabled packages are modeled
--------------------------------------------------------------------------------
The Tier-1 source on the chat input path (design §"Route Tier-1 matching through
the policy engine only", task 3.2) is

    policy_engine.evaluate(prompt, "", enabled_compiled_policies)

and the compiled bundle passed to ``evaluate`` contains ONLY the organization's
ENABLED policy packages — the control-plane compiler/``policy_sync`` path emits an
enabled package into the org's bundle and omits a disabled one (design §2
"an enabled package simply appears in POLICY_SYNC.get_policies(org_slug)";
Requirement 4.4). So a package's enabled/disabled state is modeled structurally:

  * ENABLED package  ⇒ its compiled entry IS present in the list handed to
    ``evaluate``.
  * DISABLED package ⇒ its compiled entry is ABSENT from that list.
  * NON-ENABLED (never-enabled / seeded-but-off) package ⇒ also simply ABSENT —
    indistinguishable to Tier-1 from a disabled one, which is exactly the point:
    Tier-1 can only ever see the enabled set.

Each package here is a single distinctive keyword Rule (family "A" matches
keyword ``alpha-secret``; "B" matches ``bravo-secret``), so a rule's contribution
is observable as ``result.action != "allow"`` with the family's keyword present in
``matched_rule_names`` / ``matched_policy_names``.

The property asserted (Property 3, the IFF):

  (fires ⇒ enabled) a Rule contributes an action ONLY when its package is in the
    enabled set — proven by evaluating with the package EXCLUDED: even on input
    that WOULD match the rule, the engine yields ``allow`` and the rule name never
    appears.
  (enabled ⇒ can fire) when a package IS enabled and the input matches its rule,
    that rule (and ONLY that rule) contributes its action, and a co-present but
    NON-matching enabled package contributes nothing.
  (disabled/non-enabled never contributes) a rule whose package is not in the
    enabled set never contributes an action for ANY input, including input that
    matches its own keyword.

This is the Tier-1 policy-engine property; the full end-to-end surface behaviour
is Property 1/2 (test_policy_driven_detection.py) and the live E2E harness
(task 10). This module adds NO production code and does not edit the sibling
scaffold module (only imports reusable helpers from it).

Requirements: 1.3 (Tier-1 runs ONLY the Enabled_Policy_Set's rules, never a rule
not in it), 4.3 (an enabled package's rules run at Tier-1), 4.4 (a disabled
package contributes no rules).
"""

from __future__ import annotations

import unittest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ai_mesh_gateway.policy_engine import EvaluationResult, evaluate

# Reuse the representative content + scaffold helpers WITHOUT editing the sibling
# module (concurrent-subagent file isolation). We import only; we add nothing.
from ai_mesh_gateway.tests.test_policy_driven_detection import (
    ATTACK_PROMPTS,
    BENIGN_PROMPTS,
    PII_PROMPTS,
    SECRET_PROMPTS,
    PolicyDrivenDetectionScaffold,
)


# --------------------------------------------------------------------------- #
# Two independent single-rule packages. Each is a distinctive keyword so a
# rule's contribution is unambiguously attributable to its package.
# --------------------------------------------------------------------------- #
_PKG_A_KEYWORD = "alpha-secret"
_PKG_B_KEYWORD = "bravo-secret"

# Any distinctive substring that provably matches NEITHER package's keyword, so
# the "would only match B" / "matches neither" cases are exact.
_ALL_KEYWORDS = (_PKG_A_KEYWORD, _PKG_B_KEYWORD)


def _package(*, policy_id: int, code: str, name: str, rule_id: int, rule_name: str,
             keyword: str, action: str = "block") -> dict:
    """One compiled-policy entry = one enabled package with a single keyword rule.

    Shape matches ``policy_engine.evaluate``'s expected compiled entry
    (``{"policy": {...}, "rules": [{...}]}``), same as the scaffold's
    ``seeded_policies`` helper.
    """
    return {
        "policy": {
            "id": policy_id,
            "code": code,
            "name": name,
            "priority": 100,
            "category": name,
            "severity": "high",
        },
        "rules": [
            {
                "id": rule_id,
                "name": rule_name,
                "rule_type": "keywords",
                "condition": {"keywords": [keyword], "field": "both"},
                "action": action,
            }
        ],
    }


def _pkg_a(action: str = "block") -> dict:
    return _package(
        policy_id=101, code="PKG_A", name="Package A", rule_id=1101,
        rule_name="pkg-a-rule", keyword=_PKG_A_KEYWORD, action=action,
    )


def _pkg_b(action: str = "block") -> dict:
    return _package(
        policy_id=202, code="PKG_B", name="Package B", rule_id=2202,
        rule_name="pkg-b-rule", keyword=_PKG_B_KEYWORD, action=action,
    )


# Fixed representative prompts the built-in scanner would once have acted on —
# used to exercise the property over known-adversarial content, not just noise.
_REPRESENTATIVE_PROMPTS = list(
    {**ATTACK_PROMPTS, **PII_PROMPTS, **SECRET_PROMPTS, **BENIGN_PROMPTS}.values()
)

# Arbitrary text that contains NEITHER package keyword (case-insensitive), so it
# provably cannot match either rule.
_text_without_keywords = st.text(max_size=200).filter(
    lambda s: not any(kw in s.lower() for kw in _ALL_KEYWORDS)
)


class OnlyEnabledRulesFireProperties(
    PolicyDrivenDetectionScaffold, unittest.TestCase
):
    """Property 3 (only enabled rules fire) — Validates: Requirements 1.3, 4.3, 4.4."""

    def _evaluate(self, prompt: str, enabled: list[dict]) -> EvaluationResult:
        """Tier-1 = policy_engine.evaluate over the ENABLED compiled set only."""
        return evaluate(prompt, "", enabled)

    # ---- (enabled ⇒ can fire) an enabled package's rule contributes ---------- #

    @settings(max_examples=200, deadline=None)
    @given(prefix=st.text(max_size=80), suffix=st.text(max_size=80))
    def test_enabled_package_rule_fires_and_is_attributable(self, prefix: str, suffix: str):
        """Package A enabled + input matches its keyword ⇒ A's rule fires (and
        traces to A). This is the forward direction of the IFF."""
        enabled = [_pkg_a(action="block")]
        prompt = f"{prefix}{_PKG_A_KEYWORD}{suffix}"
        result = self._evaluate(prompt, enabled)
        self.assertEqual(
            result.action,
            "block",
            msg=f"enabled package A's rule must fire on its keyword: {prompt!r}",
        )
        self.assertIn("pkg-a-rule", result.matched_rule_names)
        self.assertIn("Package A", result.matched_policy_names)

    # ---- (fires ⇒ enabled) a NON-enabled package never fires, even on a match - #

    @settings(max_examples=200, deadline=None)
    @given(prefix=st.text(max_size=80), suffix=st.text(max_size=80))
    def test_disabled_package_rule_never_fires_even_on_match(self, prefix: str, suffix: str):
        """Package B DISABLED (absent from the enabled set) ⇒ input that WOULD
        match B's keyword still yields allow; B's rule never contributes. This is
        the contrapositive: a rule that is not in the enabled set produces no
        verdict for any input, including one matching its own keyword."""
        # Enabled set = {A} only; B is disabled/non-enabled (absent).
        enabled = [_pkg_a(action="block")]
        prompt = f"{prefix}{_PKG_B_KEYWORD}{suffix}"
        # Strip any accidental A keyword the random affixes introduced, so ONLY
        # B's keyword is present ⇒ the only thing that COULD fire is disabled.
        prompt_only_b = prompt.replace(_PKG_A_KEYWORD, "").replace(
            _PKG_A_KEYWORD.upper(), ""
        )
        # Re-insert B's keyword in case the strip removed an overlap (it can't,
        # the keywords are disjoint, but keep the invariant explicit).
        if _PKG_B_KEYWORD not in prompt_only_b:
            prompt_only_b = f"{prompt_only_b}{_PKG_B_KEYWORD}"
        result = self._evaluate(prompt_only_b, enabled)
        self.assertEqual(
            result.action,
            "allow",
            msg=f"disabled package B must never contribute: {prompt_only_b!r}",
        )
        self.assertNotIn("pkg-b-rule", result.matched_rule_names)
        self.assertNotIn("Package B", result.matched_policy_names)
        self.assertEqual(result.matched_rule_ids, [])

    # ---- per-package isolation within the IFF: enabling A does not fire B ----- #

    @settings(max_examples=200, deadline=None)
    @given(prefix=st.text(max_size=80), suffix=st.text(max_size=80))
    def test_enabled_A_does_not_fire_matching_disabled_B(self, prefix: str, suffix: str):
        """Enable ONLY A; feed input that matches B's keyword (not A's) ⇒ allow.
        The decision depends only on the ENABLED set: a rule in the non-enabled
        package B never contributes even though B's keyword is present."""
        enabled = [_pkg_a(action="block")]
        prompt = f"{prefix}{_PKG_B_KEYWORD}{suffix}"
        prompt = prompt.replace(_PKG_A_KEYWORD, "").replace(_PKG_A_KEYWORD.upper(), "")
        if _PKG_B_KEYWORD not in prompt:
            prompt = f"{prompt}{_PKG_B_KEYWORD}"
        result = self._evaluate(prompt, enabled)
        self.assertEqual(result.action, "allow", msg=f"only-B-match under A-enabled ⇒ allow: {prompt!r}")
        self.assertEqual(result.matched_rule_ids, [])

    # ---- both enabled: each fires ONLY on its own keyword (IFF, both dirs) ---- #

    @settings(max_examples=200, deadline=None)
    @given(prefix=st.text(max_size=60), mid=st.text(max_size=60), suffix=st.text(max_size=60))
    def test_both_enabled_only_matching_rule_contributes(self, prefix, mid, suffix):
        """Both A and B enabled: the rule that fires is exactly the one whose
        keyword is present. A's keyword ⇒ A fires and B does not; B's keyword ⇒
        B fires and A does not. Confirms the IFF holds when >1 package is enabled."""
        enabled = [_pkg_a(action="block"), _pkg_b(action="redact")]

        # Only A's keyword present.
        a_prompt = f"{prefix}{_PKG_A_KEYWORD}{mid}{suffix}"
        a_prompt = a_prompt.replace(_PKG_B_KEYWORD, "").replace(_PKG_B_KEYWORD.upper(), "")
        if _PKG_A_KEYWORD not in a_prompt:
            a_prompt = f"{a_prompt}{_PKG_A_KEYWORD}"
        a_res = self._evaluate(a_prompt, enabled)
        self.assertIn("pkg-a-rule", a_res.matched_rule_names)
        self.assertNotIn("pkg-b-rule", a_res.matched_rule_names)
        self.assertEqual(a_res.action, "block")

        # Only B's keyword present.
        b_prompt = f"{prefix}{_PKG_B_KEYWORD}{mid}{suffix}"
        b_prompt = b_prompt.replace(_PKG_A_KEYWORD, "").replace(_PKG_A_KEYWORD.upper(), "")
        if _PKG_B_KEYWORD not in b_prompt:
            b_prompt = f"{b_prompt}{_PKG_B_KEYWORD}"
        b_res = self._evaluate(b_prompt, enabled)
        self.assertIn("pkg-b-rule", b_res.matched_rule_names)
        self.assertNotIn("pkg-a-rule", b_res.matched_rule_names)
        self.assertEqual(b_res.action, "redact")

    # ---- non-matching input never fires any enabled rule --------------------- #

    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(
        text=st.one_of(
            _text_without_keywords,
            st.sampled_from(_REPRESENTATIVE_PROMPTS),
        )
    )
    def test_enabled_packages_do_not_fire_on_nonmatching_input(self, text: str):
        """Both packages enabled but the input matches NEITHER keyword ⇒ allow.
        An enabled rule fires only when the input matches it (the 'only' half of
        'a rule fires iff its package is enabled AND matches'); representative
        attack/PII/secret prompts (which no seeded keyword rule matches) pass
        through, proving no built-in default sneaks a verdict in."""
        # Guard: the representative set must not accidentally contain a keyword.
        cleaned = text
        for kw in _ALL_KEYWORDS:
            cleaned = cleaned.replace(kw, "").replace(kw.upper(), "")
        enabled = [_pkg_a(action="block"), _pkg_b(action="redact")]
        result = self._evaluate(cleaned, enabled)
        self.assertEqual(
            result.action,
            "allow",
            msg=f"non-matching input under enabled packages ⇒ allow: {cleaned!r}",
        )
        self.assertEqual(result.matched_rule_ids, [])

    # ---- empty enabled set: nothing ever fires ------------------------------- #

    @settings(max_examples=150, deadline=None)
    @given(
        text=st.one_of(
            st.text(max_size=200),
            st.sampled_from(_REPRESENTATIVE_PROMPTS),
            st.sampled_from([
                f"contains {_PKG_A_KEYWORD} here",
                f"contains {_PKG_B_KEYWORD} here",
                f"{_PKG_A_KEYWORD} {_PKG_B_KEYWORD}",
            ]),
        )
    )
    def test_no_enabled_package_never_fires(self, text: str):
        """Zero enabled packages ⇒ allow for ANY input, including text that
        matches a (non-enabled) package's keyword. The empty Enabled_Policy_Set
        produces no Tier-1 verdict (Requirement 1.3/4.4 boundary)."""
        result = self._evaluate(text, [])
        self.assertEqual(result.action, "allow", msg=f"empty enabled set ⇒ allow: {text!r}")
        self.assertEqual(result.matched_rule_ids, [])
        self.assertEqual(result.matched_policy_names, [])


class OnlyEnabledRulesFireExampleAnchors(
    PolicyDrivenDetectionScaffold, unittest.TestCase
):
    """Deterministic example anchors for Property 3 (fast, no Hypothesis)."""

    def test_enable_A_disable_B_matrix(self):
        """Enable A, leave B disabled; check the full 2x2 keyword matrix."""
        enabled = [_pkg_a(action="block")]  # B disabled (absent)
        # A keyword present ⇒ A fires.
        r = evaluate(f"please use {_PKG_A_KEYWORD} now", "", enabled)
        self.assertEqual(r.action, "block")
        self.assertIn("pkg-a-rule", r.matched_rule_names)
        # B keyword present ⇒ nothing (B disabled).
        r = evaluate(f"please use {_PKG_B_KEYWORD} now", "", enabled)
        self.assertEqual(r.action, "allow")
        self.assertEqual(r.matched_rule_ids, [])
        # Both present ⇒ ONLY A fires (B disabled contributes nothing).
        r = evaluate(f"{_PKG_A_KEYWORD} and {_PKG_B_KEYWORD}", "", enabled)
        self.assertEqual(r.action, "block")
        self.assertIn("pkg-a-rule", r.matched_rule_names)
        self.assertNotIn("pkg-b-rule", r.matched_rule_names)
        # Neither present ⇒ allow.
        r = evaluate("nothing sensitive here", "", enabled)
        self.assertEqual(r.action, "allow")

    def test_toggling_enable_flips_contribution(self):
        """The SAME B-matching input is passthrough when B is disabled and blocks
        when B is enabled — the decision depends only on the enabled set."""
        prompt = f"the {_PKG_B_KEYWORD} value"
        # B disabled.
        r_off = evaluate(prompt, "", [_pkg_a(action="block")])
        self.assertEqual(r_off.action, "allow")
        # B enabled.
        r_on = evaluate(prompt, "", [_pkg_a(action="block"), _pkg_b(action="block")])
        self.assertEqual(r_on.action, "block")
        self.assertIn("pkg-b-rule", r_on.matched_rule_names)


if __name__ == "__main__":
    unittest.main()
