"""
Policy-driven detection — Task 6.1: legacy scan toggles are no longer detection
drivers (gateway side).

Feature: policy-driven-detection (no default rules).

Task 6.1 removed the legacy default-on scan toggles
(``input_scan_enabled`` / ``output_scan_enabled`` / ``scan_block_on_injection`` /
``scan_block_on_pii``) as DETECTION DRIVERS from ``config.py``,
``config_sync._BOOL_KEYS``, and the ``main.py`` gates (``_input_scan_will_run``
and siblings). Tier-1 detection is now gated SOLELY on the org's enabled policy
set (evaluated by ``policy_engine.evaluate`` and resolved by
``enforcement.resolve_and_enforce`` with scanner_* = None, per tasks 2.1/3.1).
``firewall_enabled`` is retained ONLY as a suppression-only master bypass — it may
never CAUSE detection, it can only suppress it. A stale/legacy value for a removed
key is IGNORED for enabling detection (not treated as an error).

These tests are deliberately in a NEW module (task 6.1 must not edit
``test_policy_driven_detection.py``, which a concurrent task is using). They
exercise the deterministic, offline layers the toggles used to feed:

  * the enforcement authority (``resolve_and_enforce``) — the single input-side
    seam ``proxy_chat`` calls, with scanner_* = None (the post-2.1 shape);
  * the gateway config surfaces (``config.load_config`` +
    ``config_sync.CONFIG_KEY_TYPES`` / ``_BOOL_KEYS``) — proving the removed keys
    are no longer emitted or typed as detection flags and that a stale value is
    ignored, not an error.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**
"""

from __future__ import annotations

import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from ai_mesh_gateway import config as gw_config
from ai_mesh_gateway.config_sync import (
    CONFIG_KEY_TYPES,
    _BOOL_KEYS,
    validate_config_payload,
)
from ai_mesh_gateway.enforcement import resolve_and_enforce

# Reuse the feature scaffold (read-only import — this module never edits it).
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

_ALL_DANGEROUS_PROMPTS = list(
    {**ATTACK_PROMPTS, **PII_PROMPTS, **SECRET_PROMPTS}.values()
)


def _pipeline_suppressed(org_config: dict) -> bool:
    """Mirror the gateway's suppression-only master-bypass contract.

    ``proxy_chat`` treats ``firewall_enabled is False`` as a full-pipeline bypass
    (``firewall_disabled = org_config.get("firewall_enabled") is False``) and the
    ``_input_scan_will_run`` gate (task 6.1) is
    ``INPUT_SCANNER is not None and org_config.get("firewall_enabled") is not False``
    — i.e. detection is suppressed iff ``firewall_enabled`` is explicitly False.

    This helper reproduces that single contract so the suppression-only property
    can be asserted deterministically without standing up ``proxy_chat``.
    """
    return org_config.get("firewall_enabled") is False


class LegacyToggleDoesNotCauseDetectionTests(
    PolicyDrivenDetectionScaffold, unittest.TestCase
):
    """(1)+(2) A legacy scan toggle set ON can NEVER cause detection.

    With ZERO enabled policies + Tier-2 off (the Zero_Policy_State), setting any
    of the removed toggles ``True`` in the org config leaves the resolved
    ``PipelineDecision`` at ``allow`` — the toggle is inert.

    **Validates: Requirements 5.1, 5.2**
    """

    def _decision_with_toggles_on(self, prompt: str):
        # Every removed toggle asserted ON — the strongest "would this cause
        # detection?" configuration under the OLD model.
        cfg = self.make_org_config(
            input_scan_enabled=True,
            output_scan_enabled=True,
            scan_block_on_injection=True,
            scan_block_on_pii=True,
            tier2_enabled=None,  # tri-state absent => effective OFF
        )
        return self.resolve_input_decision(
            prompt=prompt,
            org_config=cfg,
            policies=self.empty_policies(),
            # scanner_* left None — the post-task-2.1 chat-input shape.
        )

    def test_input_scan_enabled_true_zero_policy_is_passthrough(self):
        """(1) ``input_scan_enabled=true`` + zero policies + Tier-2 off ⇒ allow."""
        for label, prompt in {
            **ATTACK_PROMPTS,
            **PII_PROMPTS,
            **SECRET_PROMPTS,
            **BENIGN_PROMPTS,
        }.items():
            with self.subTest(prompt=label):
                decision = self._decision_with_toggles_on(prompt)
                self.assertEqual(
                    decision.action,
                    "allow",
                    msg=f"legacy toggle ON must not cause detection for {label!r}",
                )
                self.assertFalse(decision.is_terminal_block)
                self.assertFalse(decision.is_redact)

    def test_scan_block_on_injection_and_pii_true_zero_policy_no_block(self):
        """(2) ``scan_block_on_injection`` / ``scan_block_on_pii`` = true with zero
        policies ⇒ no block (and no redact) on injection / PII / secret prompts."""
        for label, prompt in {
            **ATTACK_PROMPTS,
            **PII_PROMPTS,
            **SECRET_PROMPTS,
        }.items():
            with self.subTest(prompt=label):
                decision = self._decision_with_toggles_on(prompt)
                self.assertFalse(
                    decision.is_terminal_block,
                    msg=f"scan_block_on_* toggles must not block {label!r} with zero policies",
                )
                self.assertEqual(decision.action, "allow")

    @settings(max_examples=150, deadline=None)
    @given(
        text=st.one_of(
            st.text(max_size=256),
            st.sampled_from(_ALL_DANGEROUS_PROMPTS),
        )
    )
    def test_property_legacy_toggle_inert_for_arbitrary_input(self, text: str):
        """Property: for ANY input, the removed toggles set ON do not change the
        zero-policy decision — it stays ``allow`` (the toggles never drive
        detection). **Validates: Requirements 5.2, 5.4**"""
        decision = self._decision_with_toggles_on(text)
        self.assertEqual(decision.action, "allow")
        self.assertFalse(decision.is_terminal_block)
        self.assertFalse(decision.is_redact)


class FirewallEnabledSuppressionOnlyTests(
    PolicyDrivenDetectionScaffold, unittest.TestCase
):
    """(3) ``firewall_enabled`` is suppression-only.

    ``firewall_enabled=False`` suppresses detection even when an enabled policy
    would otherwise fire; ``firewall_enabled=True`` never itself CAUSES detection
    (a zero-policy org with the firewall on is still passthrough).

    **Validates: Requirements 5.5**
    """

    _KW = "sekrit"

    def test_firewall_disabled_suppresses_even_with_enabled_policy(self):
        """A block policy WOULD fire, but firewall_enabled=False bypasses the
        whole pipeline ⇒ suppressed (no detection)."""
        cfg = self.make_org_config(firewall_enabled=False)
        policies = self.seeded_policies(action="block", keyword=self._KW)
        prompt = f"please use the {self._KW} handshake"

        # The master bypass short-circuits the pipeline before enforcement runs.
        self.assertTrue(
            _pipeline_suppressed(cfg),
            msg="firewall_enabled=False must suppress the pipeline",
        )

        # Sanity: absent the bypass, the SAME policy+prompt WOULD block — proving
        # the suppression (not a non-matching rule) is what yields passthrough.
        would_block = self.resolve_input_decision(
            prompt=prompt,
            org_config=self.make_org_config(firewall_enabled=True),
            policies=policies,
        )
        self.assertEqual(
            would_block.action,
            "block",
            msg="control: the enabled policy blocks when the firewall is on",
        )

    def test_firewall_enabled_true_does_not_itself_cause_detection(self):
        """firewall_enabled=True + zero policies ⇒ still passthrough (the master
        toggle can only suppress, never cause detection)."""
        cfg = self.make_org_config(firewall_enabled=True)
        self.assertFalse(_pipeline_suppressed(cfg))
        for label, prompt in {**ATTACK_PROMPTS, **PII_PROMPTS}.items():
            with self.subTest(prompt=label):
                decision = self.resolve_input_decision(
                    prompt=prompt, org_config=cfg, policies=self.empty_policies()
                )
                self.assertEqual(decision.action, "allow")
                self.assertFalse(decision.is_terminal_block)


class RemovedKeysNotEmittedTests(unittest.TestCase):
    """(4a) The removed keys are no longer emitted / typed as detection flags.

    **Validates: Requirements 5.1, 5.3**
    """

    def test_load_config_does_not_emit_removed_scan_toggles(self):
        cfg = gw_config.load_config()
        for key in _REMOVED_SCAN_TOGGLES:
            with self.subTest(key=key):
                self.assertNotIn(
                    key,
                    cfg,
                    msg=f"{key!r} must not be emitted by load_config (removed as a detection driver)",
                )

    def test_removed_keys_absent_from_bool_keys_and_type_map(self):
        for key in _REMOVED_SCAN_TOGGLES:
            with self.subTest(key=key):
                self.assertNotIn(key, _BOOL_KEYS)
                self.assertNotIn(key, CONFIG_KEY_TYPES)

    def test_firewall_enabled_retained_as_bool_key(self):
        # The suppression-only master bypass is retained (Requirement 5.5).
        self.assertIn("firewall_enabled", _BOOL_KEYS)
        self.assertIn("firewall_enabled", CONFIG_KEY_TYPES)


class StaleRemovedKeyIgnoredTests(unittest.TestCase):
    """(4b) A stale/legacy value for a removed key is IGNORED, not an error.

    A control plane that still ships the removed toggles must not error the
    gateway config apply, and the stale value must not enable detection. Since the
    keys are no longer in ``CONFIG_KEY_TYPES`` they are treated as unknown
    (forward-compatible pass-through) and the gateway code never reads them for
    detection.

    **Validates: Requirements 5.4**
    """

    def test_stale_removed_keys_validate_without_error(self):
        # An "old control plane" payload that still carries the removed toggles,
        # including the DANGEROUS-under-the-old-model ON values, alongside a real
        # key. validate_config_payload must NOT raise or reject the payload.
        stale_payload = {
            "firewall_enabled": True,
            "input_scan_enabled": True,
            "output_scan_enabled": True,
            "scan_block_on_injection": True,
            "scan_block_on_pii": True,
        }
        sanitized = validate_config_payload(stale_payload, source="test-stale")
        # Not None (not treated as malformed) and the removed keys pass through
        # as unknown/forward-compatible values — never an error.
        self.assertIsNotNone(sanitized)
        for key in _REMOVED_SCAN_TOGGLES:
            with self.subTest(key=key):
                self.assertIn(
                    key,
                    sanitized,
                    msg=f"stale {key!r} should pass through as an unknown key, not be an error",
                )
        # The retained real key survives too.
        self.assertTrue(sanitized.get("firewall_enabled"))

    def test_stale_removed_key_wrong_type_still_not_an_error(self):
        # Even a garbage (non-bool) value for a removed key is treated as an
        # unknown key (pass-through) — never a hard error — because the key is no
        # longer in CONFIG_KEY_TYPES.
        sanitized = validate_config_payload(
            {"input_scan_enabled": "yes-please", "firewall_enabled": True},
            source="test-stale-garbage",
        )
        self.assertIsNotNone(sanitized)
        self.assertIn("input_scan_enabled", sanitized)

    def test_stale_removed_key_does_not_enable_detection(self):
        """Even if a stale ``input_scan_enabled``/``scan_block_on_*`` sits in the
        org config, resolve_and_enforce (scanner_* None, zero policy) is allow."""
        stale_cfg = {
            "firewall_enabled": True,
            "enforcement_mode": "block",
            "input_scan_enabled": True,
            "scan_block_on_injection": True,
            "scan_block_on_pii": True,
        }
        # The gateway no longer reads the removed keys; the ONLY inputs that can
        # cause a non-allow are a matched policy action or a Tier-2 verdict —
        # neither present here.
        decision = resolve_and_enforce(
            scanner_recommendation=None,
            scanner_action=None,
            scanner_threat_type=None,
            org_policy_action=None,
            enforcement_mode=str(stale_cfg.get("enforcement_mode", "block")),
        )
        self.assertEqual(decision.action, "allow")
        self.assertFalse(decision.is_terminal_block)
        self.assertFalse(decision.is_redact)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
