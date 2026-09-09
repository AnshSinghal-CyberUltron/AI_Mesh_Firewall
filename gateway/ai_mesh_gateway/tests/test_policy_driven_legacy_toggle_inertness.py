"""
Policy-driven detection — Task 6.3: PROPERTY test for legacy-toggle inertness.

Feature: policy-driven-detection (no default rules).

Task 6.1 (COMPLETE) removed the four legacy default-on scan toggles
(``input_scan_enabled`` / ``output_scan_enabled`` / ``scan_block_on_injection`` /
``scan_block_on_pii``) as DETECTION DRIVERS from ``config.py``,
``config_sync._BOOL_KEYS`` / ``CONFIG_KEY_TYPES``, and the ``main.py`` gates.
Detection is now gated SOLELY on the org's enabled policy set (Tier-1 via
``policy_engine.evaluate`` → ``enforcement.resolve_and_enforce`` with scanner_*
= None) plus opt-in Tier-2; those four keys are inert.

This is the PROPERTY test for the cutover invariant. The 6.1 unit-test module
``test_policy_driven_legacy_toggles.py`` is NOT edited here (it is imported
read-only for its scaffold re-export); this module owns the Hypothesis property
that the removed keys never change the decision for ANY of their possible
configuration values.

**Feature: policy-driven-detection, Property 9: For any configuration value of
the removed ``input_scan_enabled`` / ``output_scan_enabled`` /
``scan_block_on_injection`` / ``scan_block_on_pii`` keys, the detection decision
is unchanged; those keys never cause detection.**

**Validates: Requirements 5.2, 5.4**

The property is asserted along two independent axes, over arbitrarily-typed
values (True / False / None / stale strings / ints) of the four removed keys:

  (A) DECISION AXIS (Requirement 5.2 — detection gated solely on the enabled
      policy set): with ZERO enabled policies + Tier-2 off (scanner_*=None, the
      post-cutover chat shape), the resolved ``PipelineDecision`` is IDENTICAL to
      the baseline decision WITHOUT any legacy key — always ``allow``, never
      block / redact / flag — regardless of the keys' values. The keys are inert.

  (B) CONFIG AXIS (Requirement 5.4 — a stale/legacy toggle is ignored, not an
      error, and never becomes a typed detection flag): a config payload carrying
      arbitrary values of the four keys is accepted by
      ``config_sync.validate_config_payload`` (not rejected / not None) and the
      keys never appear in ``config_sync.CONFIG_KEY_TYPES`` (so they are treated
      as unknown / forward-compatible pass-through, not detection flags).
"""

from __future__ import annotations

import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from ai_mesh_gateway.config_sync import CONFIG_KEY_TYPES, validate_config_payload

# Read-only reuse of the feature scaffold (this module never edits it).
from ai_mesh_gateway.tests.test_policy_driven_detection import (
    ATTACK_PROMPTS,
    BENIGN_PROMPTS,
    PII_PROMPTS,
    SECRET_PROMPTS,
    PolicyDrivenDetectionScaffold,
)

# The four legacy scan toggles removed as detection drivers by task 6.1.
_REMOVED_SCAN_TOGGLES = (
    "input_scan_enabled",
    "output_scan_enabled",
    "scan_block_on_injection",
    "scan_block_on_pii",
)

# Representative content the OLD (built-in-scan) model would have blocked/redacted,
# mixed into the property so it is exercised over known-adversarial input too.
_REPRESENTATIVE_PROMPTS = list(
    {**ATTACK_PROMPTS, **PII_PROMPTS, **SECRET_PROMPTS, **BENIGN_PROMPTS}.values()
)

# Arbitrary values a stale/legacy control plane (or a corrupted config) could carry
# for a removed key: the tri-state bool, absent (None), stale strings, and ints.
# This is the "for any configuration value" quantifier of Property 9.
_legacy_toggle_values = st.one_of(
    st.booleans(),
    st.none(),
    st.sampled_from(["true", "false", "yes", "no", "on", "off", "1", "0", ""]),
    st.integers(min_value=-3, max_value=3),
    st.text(max_size=12),
)


class LegacyToggleInertnessProperties(
    PolicyDrivenDetectionScaffold, unittest.TestCase
):
    """Property 9 — legacy-toggle inertness. Validates: Requirements 5.2, 5.4."""

    def _baseline_allow_decision(self, prompt: str):
        """The zero-policy + Tier-2-off decision WITHOUT any legacy key present."""
        return self.resolve_input_decision(
            prompt=prompt,
            org_config=self.make_org_config(tier2_enabled=None),
            policies=self.empty_policies(),
            # scanner_* default to None — the post-cutover chat-input shape.
        )

    # ------------------------------------------------------------------ #
    # (A) DECISION AXIS — Requirement 5.2.
    # ------------------------------------------------------------------ #
    @settings(max_examples=250, deadline=None)
    @given(
        prompt=st.one_of(
            st.text(max_size=256),
            st.sampled_from(_REPRESENTATIVE_PROMPTS),
        ),
        input_scan_enabled=_legacy_toggle_values,
        output_scan_enabled=_legacy_toggle_values,
        scan_block_on_injection=_legacy_toggle_values,
        scan_block_on_pii=_legacy_toggle_values,
    )
    def test_property9_removed_keys_do_not_change_decision(
        self,
        prompt: str,
        input_scan_enabled,
        output_scan_enabled,
        scan_block_on_injection,
        scan_block_on_pii,
    ):
        """For ANY values of the 4 removed keys, the zero-policy + Tier-2-off
        decision is IDENTICAL to the baseline (no key) decision — always ``allow``,
        never block / redact / flag. The keys are inert (never cause detection)."""
        cfg = self.make_org_config(
            tier2_enabled=None,
            input_scan_enabled=input_scan_enabled,
            output_scan_enabled=output_scan_enabled,
            scan_block_on_injection=scan_block_on_injection,
            scan_block_on_pii=scan_block_on_pii,
        )
        decision = self.resolve_input_decision(
            prompt=prompt,
            org_config=cfg,
            policies=self.empty_policies(),
            # scanner_* default to None — the removed keys are not detection inputs.
        )
        baseline = self._baseline_allow_decision(prompt)

        # Decision is UNCHANGED vs the no-key baseline.
        self.assertEqual(
            decision.action,
            baseline.action,
            msg=(
                "removed toggles must not change the decision: "
                f"got {decision.action!r} vs baseline {baseline.action!r} "
                f"for {prompt!r}"
            ),
        )
        # And that unchanged decision is passthrough — the keys never cause detection.
        self.assertEqual(decision.action, "allow")
        self.assertFalse(decision.is_terminal_block)
        self.assertFalse(decision.is_redact)
        self.assertFalse(decision.redaction_applied)
        self.assertIsNone(decision.threat_type)
        self.assertEqual(decision.matched_rules, [])
        self.assertEqual(decision.matched_policy_names, [])

    def test_property9_representative_prompts_inert_under_toggles_on(self):
        """Example anchor: every representative attack/PII/secret/benign prompt
        stays ``allow`` with ALL four removed toggles asserted ON (the strongest
        "would this cause detection?" config under the OLD model)."""
        cfg = self.make_org_config(
            tier2_enabled=None,
            input_scan_enabled=True,
            output_scan_enabled=True,
            scan_block_on_injection=True,
            scan_block_on_pii=True,
        )
        for label, prompt in {
            **ATTACK_PROMPTS,
            **PII_PROMPTS,
            **SECRET_PROMPTS,
            **BENIGN_PROMPTS,
        }.items():
            with self.subTest(prompt=label):
                decision = self.resolve_input_decision(
                    prompt=prompt, org_config=cfg, policies=self.empty_policies()
                )
                self.assertEqual(decision.action, "allow")
                self.assertFalse(decision.is_terminal_block)
                self.assertFalse(decision.is_redact)

    # ------------------------------------------------------------------ #
    # (B) CONFIG AXIS — Requirement 5.4.
    # ------------------------------------------------------------------ #
    @settings(max_examples=200, deadline=None)
    @given(
        input_scan_enabled=_legacy_toggle_values,
        output_scan_enabled=_legacy_toggle_values,
        scan_block_on_injection=_legacy_toggle_values,
        scan_block_on_pii=_legacy_toggle_values,
    )
    def test_property9_stale_payload_accepted_and_keys_not_typed_flags(
        self,
        input_scan_enabled,
        output_scan_enabled,
        scan_block_on_injection,
        scan_block_on_pii,
    ):
        """For ANY values of the 4 removed keys, a config payload carrying them is
        NOT rejected/errored by ``validate_config_payload`` and the keys never
        become typed detection flags (absent from ``CONFIG_KEY_TYPES``) — so a
        stale/legacy toggle is ignored (forward-compatible), never a driver."""
        payload = {
            "firewall_enabled": True,  # a real retained key alongside the stale ones
            "input_scan_enabled": input_scan_enabled,
            "output_scan_enabled": output_scan_enabled,
            "scan_block_on_injection": scan_block_on_injection,
            "scan_block_on_pii": scan_block_on_pii,
        }
        sanitized = validate_config_payload(payload, source="test-prop9-stale")

        # Not rejected/errored (not None) regardless of the stale values' types.
        self.assertIsNotNone(
            sanitized,
            msg=f"stale removed toggles must not reject the payload: {payload!r}",
        )
        # The retained real key survives.
        self.assertTrue(sanitized.get("firewall_enabled"))
        # The removed keys pass through as unknown (forward-compatible) values and
        # are NOT typed detection flags.
        for key in _REMOVED_SCAN_TOGGLES:
            with self.subTest(key=key):
                self.assertIn(
                    key,
                    sanitized,
                    msg=f"stale {key!r} should pass through as unknown, not error",
                )
                self.assertNotIn(
                    key,
                    CONFIG_KEY_TYPES,
                    msg=f"{key!r} must never be a typed detection flag",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
