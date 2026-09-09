"""
Policy-driven detection — Task 4.3: Tier-2 / Tier-1 separation.

Feature: policy-driven-detection (no default rules).

Design §"Property 8: Tier-2 is model-only, Tier-1 is policy-only". The target
model keeps the two detection layers strictly disjoint:

  * Tier-1 = ``policy_engine.evaluate`` — a PURE, deterministic function of the
    ENABLED compiled policies + the input. Its signature exposes NO model / NO
    Tier-2 input, so no model can influence a Tier-1 decision.

  * Tier-2 = ``INPUT_SCANNER.scan_prompt_with_tier2`` — the MODEL scan. It takes
    NO policy / NO compiled-policies argument, so no policy can influence a
    Tier-2 decision. It is gated by ``resolve_tier2_enabled`` (task 4.1); when
    the resolved value is off, the Tier-2 model scan never runs and produces no
    verdict.

This module is FILE-ISOLATED per task 4.3 — it is a NEW module and does NOT edit
``test_policy_driven_detection.py`` or ``test_policy_driven_tier2_gate.py``. It
imports the 4.1 spy-scanner gate pattern read-only to reuse the exact
``proxy_chat`` dispatch structure for the gate assertion.

CONFIRMED SIGNATURES (from source, before asserting):

  policy_engine.evaluate(
      prompt: str,
      response_text: str,
      compiled_policies: list[dict],
      tool_name: str | None = None,
      actor: dict | None = None,
  ) -> EvaluationResult
    — NO ``tier2`` / ``model`` / ``scanner`` parameter anywhere in the signature.

  InputScanner.scan_prompt_with_tier2(
      self,
      text: str,
      is_rag: bool = False,
      org_tier2_override: bool | None = None,
      org_slug: str = "",
      org_tier2_strict: bool = True,
      toxicity_threshold: float | None = None,
      request_id: str = "",
  ) -> ScanVerdict
    — NO ``policy`` / ``policies`` / ``compiled_policies`` / ``rules`` parameter.

**Validates: Requirements 2.5, 3.4, 3.5** (Properties 8).
"""

from __future__ import annotations

import asyncio
import inspect
import unittest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ai_mesh_gateway.config_sync import resolve_tier2_enabled
from ai_mesh_gateway.policy_engine import EvaluationResult, evaluate
from ai_mesh_gateway.scanner import InputScanner, ScanVerdict

# Read-only reuse of the task-4.1 spy-scanner gate pattern (this module MUST NOT
# edit that file; importing its helpers is permitted).
from ai_mesh_gateway.tests.test_policy_driven_tier2_gate import (
    _SpyScanner,
    _run_input_scan_gate,
)


# --------------------------------------------------------------------------- #
# Compiled-policy builders (Tier-1 inputs). Shape matches ``evaluate``'s
# expected compiled entry (``{"policy": {...}, "rules": [{...}]}``).
# --------------------------------------------------------------------------- #
def _keyword_policy(*, action: str = "block", keyword: str = "sekrit") -> list[dict]:
    return [
        {
            "policy": {
                "id": 1,
                "code": "TEST_PKG_sep",
                "name": "Separation Test Package",
                "priority": 100,
                "category": "test_family",
                "severity": "high",
            },
            "rules": [
                {
                    "id": 11,
                    "name": "sep-keyword-rule",
                    "rule_type": "keywords",
                    "condition": {"keywords": [keyword], "field": "both"},
                    "action": action,
                }
            ],
        }
    ]


_TIER1_KEYWORD = "sekrit"


# --------------------------------------------------------------------------- #
# (a) Tier-1 is policy-only — structural + behavioral.
# --------------------------------------------------------------------------- #
class Tier1IsPolicyOnlyTests(unittest.TestCase):
    """Tier-1 (``policy_engine.evaluate``) is a pure function of the enabled
    compiled policies + the input; no model / Tier-2 can influence it.

    **Feature: policy-driven-detection, Property 8: For any request, no policy or
    Rule influences a Tier-2 decision, no Tier-2 verdict is produced when
    ``tier2_enabled`` is off, and no model influences a Tier-1 decision.**

    **Validates: Requirements 2.5, 3.4, 3.5**
    """

    def test_evaluate_signature_has_no_model_or_tier2_input(self):
        """STRUCTURAL: ``evaluate`` exposes no model / Tier-2 / scanner input, so
        a Tier-1 decision cannot be derived from a model verdict."""
        params = set(inspect.signature(evaluate).parameters)
        # Only these inputs exist — none names a model / Tier-2 / scanner source.
        self.assertEqual(
            params,
            {"prompt", "response_text", "compiled_policies", "tool_name", "actor"},
        )
        forbidden = {
            "tier2", "tier2_enabled", "tier2_verdict", "tier2_action",
            "model", "model_verdict", "model_action", "model_scan",
            "scanner", "scanner_verdict", "scanner_action", "bedrock",
        }
        self.assertEqual(
            params & forbidden,
            set(),
            msg="policy_engine.evaluate must take no model/Tier-2/scanner input",
        )

    def test_evaluate_is_deterministic_across_repeated_calls(self):
        """BEHAVIORAL: same policies + input => same action, every call (pure)."""
        policies = _keyword_policy(action="block", keyword=_TIER1_KEYWORD)
        prompt = f"please use the {_TIER1_KEYWORD} handshake"
        first = evaluate(prompt, "", policies)
        for _ in range(5):
            again = evaluate(prompt, "", policies)
            self.assertEqual(again.action, first.action)
            self.assertEqual(again.matched_rule_ids, first.matched_rule_ids)
        self.assertEqual(first.action, "block")

    def test_evaluate_action_independent_of_any_tier2_setting(self):
        """BEHAVIORAL: the Tier-1 decision is identical no matter what a
        ``tier2_enabled`` config value is — Tier-2 state cannot reach Tier-1.

        ``evaluate`` has no place to pass tier2 in, so we assert the decision is
        invariant across every tri-state (None/False/True/stale) resolution: the
        same (policies, input) always yields the same action."""
        policies = _keyword_policy(action="block", keyword=_TIER1_KEYWORD)
        matching = f"the {_TIER1_KEYWORD} value"
        non_matching = "nothing sensitive here"

        baseline_match = evaluate(matching, "", policies).action
        baseline_clean = evaluate(non_matching, "", policies).action

        for tier2_value in (None, False, True, "true", 1, "", "false"):
            with self.subTest(tier2=tier2_value):
                # Whatever the org's Tier-2 resolves to, Tier-1 does not see it.
                _ = resolve_tier2_enabled(tier2_value)  # resolves; irrelevant to Tier-1
                self.assertEqual(evaluate(matching, "", policies).action, baseline_match)
                self.assertEqual(evaluate(non_matching, "", policies).action, baseline_clean)

        self.assertEqual(baseline_match, "block")
        self.assertEqual(baseline_clean, "allow")

    @settings(max_examples=150, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        prefix=st.text(max_size=80),
        suffix=st.text(max_size=80),
        tier2_value=st.sampled_from([None, False, True, "true", 1, "", "false", "yes"]),
    )
    def test_property8_tier1_pure_of_input_and_policies_only(
        self, prefix: str, suffix: str, tier2_value
    ):
        """PROPERTY: for ANY input and ANY tier2 setting, the Tier-1 decision is a
        pure function of (enabled policies, input) — a matching input blocks and a
        cleaned input allows, unchanged by the tier2 value (no model reaches Tier-1).
        """
        policies = _keyword_policy(action="block", keyword=_TIER1_KEYWORD)
        # The tier2 value the org might carry — resolves to some effective bool
        # but is structurally unable to enter evaluate().
        _ = resolve_tier2_enabled(tier2_value)

        containing = f"{prefix}{_TIER1_KEYWORD}{suffix}"
        result_match: EvaluationResult = evaluate(containing, "", policies)
        self.assertEqual(
            result_match.action, "block",
            msg=f"input containing the enabled keyword must block: {containing!r}",
        )

        cleaned = f"{prefix}{suffix}"
        for sub in (_TIER1_KEYWORD, _TIER1_KEYWORD.upper(), _TIER1_KEYWORD.capitalize()):
            cleaned = cleaned.replace(sub, "")
        result_clean: EvaluationResult = evaluate(cleaned, "", policies)
        self.assertEqual(
            result_clean.action, "allow",
            msg=f"input not matching any enabled rule must allow: {cleaned!r}",
        )


# --------------------------------------------------------------------------- #
# (b) Tier-2 off => no Tier-2 verdict is produced (the gate never calls the
#     model scan). Reuses the 4.1 spy-scanner gate pattern.
# --------------------------------------------------------------------------- #
class Tier2OffProducesNoVerdictTests(unittest.TestCase):
    """When ``tier2_enabled`` resolves off, the Tier-2 model scan never executes.

    **Feature: policy-driven-detection, Property 8: ... no Tier-2 verdict is
    produced when ``tier2_enabled`` is off ...**

    **Validates: Requirements 3.4, 3.5**
    """

    def _make_spy(self) -> _SpyScanner:
        tier1 = ScanVerdict(action="allow", tier="tier_1")
        tier2 = ScanVerdict(action="block", threat_type="jailbreak",
                            confidence=0.95, tier="tier_2")
        return _SpyScanner(tier1, tier2)

    @settings(max_examples=100, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(tier2_off_value=st.sampled_from([None, False, "true", 1, "", "false", "yes", [], {}]))
    def test_property8_tier2_off_never_invokes_model_scan(self, tier2_off_value):
        """PROPERTY: for any value that resolves Tier-2 OFF, the gate calls the
        Tier-1-only ``scan_prompt`` and NEVER the Tier-2 ``scan_prompt_with_tier2``
        — so no Tier-2 verdict is produced."""
        # Guard the hypothesis: every value here must resolve to effective OFF.
        self.assertIs(resolve_tier2_enabled(tier2_off_value), False)

        spy = self._make_spy()
        org_config = {
            "tier2_enabled": tier2_off_value,
            "tier2_execution_mode": "sync_pre_llm",
        }
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))
        self.assertEqual(
            spy.scan_prompt_with_tier2_calls, 0,
            msg="Tier-2 model scan must NOT run when tier2 resolves off",
        )
        self.assertEqual(spy.scan_prompt_calls, 1)
        # The returned verdict is the Tier-1 one — no Tier-2 verdict contributed.
        self.assertEqual(verdict.tier, "tier_1")
        self.assertEqual(verdict.action, "allow")

    def test_tier2_on_true_is_the_only_value_that_runs_the_model_scan(self):
        """Anchor: only explicit True runs the Tier-2 model scan (produces a Tier-2
        verdict); this is the ONLY case that does."""
        spy = self._make_spy()
        org_config = {"tier2_enabled": True, "tier2_execution_mode": "sync_pre_llm"}
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))
        self.assertEqual(spy.scan_prompt_with_tier2_calls, 1)
        self.assertEqual(spy.scan_prompt_calls, 0)
        self.assertEqual(verdict.tier, "tier_2")


# --------------------------------------------------------------------------- #
# (c) Tier-2 is model-only — no policy feeds it (structural on the real method).
# --------------------------------------------------------------------------- #
class Tier2IsModelOnlyTests(unittest.TestCase):
    """The Tier-2 model scan takes no policy — no Rule can influence its verdict.

    **Feature: policy-driven-detection, Property 8: ... no policy or Rule
    influences a Tier-2 decision ...**

    **Validates: Requirements 2.5, 3.5**
    """

    def test_scan_prompt_with_tier2_signature_takes_no_policy(self):
        """STRUCTURAL: ``InputScanner.scan_prompt_with_tier2`` exposes no policy /
        compiled-policies / rules parameter — a policy cannot enter the Tier-2
        scan, so no policy can derive a Tier-2 verdict."""
        params = set(inspect.signature(InputScanner.scan_prompt_with_tier2).parameters)
        # The real Tier-2 entry point's inputs (self + text + Tier-2/model knobs).
        self.assertEqual(
            params,
            {
                "self", "text", "is_rag", "org_tier2_override", "org_slug",
                "org_tier2_strict", "toxicity_threshold", "request_id",
            },
        )
        forbidden = {
            "policy", "policies", "compiled_policies", "compiled_policy",
            "rule", "rules", "policy_action", "org_policy_action",
            "matched_rules", "enabled_policies", "policy_set",
        }
        self.assertEqual(
            params & forbidden,
            set(),
            msg="scan_prompt_with_tier2 (Tier-2) must take no policy/rule input",
        )

    def test_tier1_scan_prompt_signature_also_takes_no_model_scan_arg(self):
        """STRUCTURAL companion: the Tier-1-only ``scan_prompt`` entry point takes
        no model/Tier-2 argument either (its own signature is model-free)."""
        params = set(inspect.signature(InputScanner.scan_prompt).parameters)
        forbidden = {
            "tier2", "tier2_enabled", "org_tier2_override", "policies",
            "compiled_policies", "policy",
        }
        self.assertEqual(params & forbidden, set())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
