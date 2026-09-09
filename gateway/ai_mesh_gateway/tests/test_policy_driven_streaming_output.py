"""
Policy-driven detection — Task 7.1: STREAMING output guard is policy-only + Tier-2-gated.

Feature: policy-driven-detection (no default rules).

Task 7.1 makes the streaming output guard (``SecureStreamingResponse`` /
``_launch_chat_stream_response`` in ``main.py``) run ONLY the org's enabled output
policies with NO default output patterns, and gates the Tier-2 output scan on the
resolved ``tier2_enabled``. It is the OUTPUT/streaming analogue of the already-cutover
chat INPUT seam (which passes ``scanner_*=None`` so no built-in guard recommendation
reaches the enforcement authority): here the built-in ``OUTPUT_GUARD`` is simply NOT
attached to the secure stream in the Zero_Policy_State, so it contributes no verdict
and the stream is Passthrough.

These tests target the two new gate helpers plus the wiring outcome
(``resolve_scan_mode`` producing ``NONE`` when detection is inactive and
``OUTPUT_GUARD`` when active), exercising:

  * zero enabled policy + Tier-2 off  => output guard NOT attached (Passthrough).
  * an enabled pipeline-domain policy => output guard attached (Tier-1).
  * ``tier2_enabled=True`` alone       => output guard attached (Tier-2 opt-in).
  * unresolvable policy cache / Tier-2 => fail-toward-no-detection (Passthrough).

Requirements: 6.1 (all surfaces incl. streaming output honor the model),
6.3 (a surface that previously ran a built-in default now runs detection only from
the enabled policy set + opt-in Tier-2).

This module is ISOLATED (task instruction): it imports the shared scaffold + prompt
sets from ``test_policy_driven_detection`` READ-ONLY and never edits that module.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import main
from stream_orchestration import StreamScanMode, resolve_scan_mode

# Read-only import of the shared scaffold + representative prompt sets (task rule:
# do NOT edit test_policy_driven_detection.py).
from ai_mesh_gateway.tests.test_policy_driven_detection import (  # noqa: F401
    ATTACK_PROMPTS,
    BENIGN_PROMPTS,
    PII_PROMPTS,
    SECRET_PROMPTS,
    PolicyDrivenDetectionScaffold,
)


def _pipeline_policy_entry() -> dict:
    """A single enabled pipeline-domain compiled policy entry.

    ``_org_has_enabled_pipeline_policies`` resolves the org bundle via
    ``filter_policies_by_domain(..., "pipeline")``, so the entry MUST declare
    ``policy_domain == "pipeline"`` to count as an enabled output policy.
    """
    return {
        "policy": {
            "id": 1,
            "code": "PKG_pipeline_seed",
            "name": "Seed Pipeline Package",
            "policy_domain": "pipeline",
            "category": "test_family",
        },
        "rules": [
            {
                "id": 11,
                "name": "seed-rule",
                "rule_type": "keywords",
                "condition": {"keywords": ["sekrit"], "field": "both"},
                "action": "redact",
            }
        ],
    }


def _mcp_only_policy_entry() -> dict:
    """An enabled policy in a NON-pipeline domain (mcp) — must NOT count as an
    enabled *output* (pipeline) policy for the chat streaming surface."""
    entry = _pipeline_policy_entry()
    entry["policy"] = {**entry["policy"], "policy_domain": "mcp", "code": "PKG_mcp"}
    return entry


def _make_policy_sync(*, loaded: bool, policies: list[dict] | None = None) -> MagicMock:
    sync = MagicMock()
    sync.is_loaded = loaded
    sync.get_policies.return_value = list(policies or [])
    return sync


class OrgHasEnabledPipelinePoliciesTests(unittest.TestCase):
    """``_org_has_enabled_pipeline_policies`` mirrors the input path's enabled-set
    resolution and fails toward no detection."""

    def test_none_policy_sync_is_zero_policy(self):
        with patch.object(main, "POLICY_SYNC", None):
            self.assertFalse(main._org_has_enabled_pipeline_policies("acme"))

    def test_not_loaded_cache_fails_toward_no_detection(self):
        # R6.5: enabled-policy set unresolvable => Passthrough (NOT fail-closed).
        sync = _make_policy_sync(loaded=False, policies=[_pipeline_policy_entry()])
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertFalse(main._org_has_enabled_pipeline_policies("acme"))

    def test_empty_bundle_is_zero_policy(self):
        sync = _make_policy_sync(loaded=True, policies=[])
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertFalse(main._org_has_enabled_pipeline_policies("acme"))

    def test_enabled_pipeline_policy_is_detected(self):
        sync = _make_policy_sync(loaded=True, policies=[_pipeline_policy_entry()])
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertTrue(main._org_has_enabled_pipeline_policies("acme"))

    def test_non_pipeline_domain_policy_does_not_count(self):
        # An MCP-domain policy is not an enabled output (pipeline) policy.
        sync = _make_policy_sync(loaded=True, policies=[_mcp_only_policy_entry()])
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertFalse(main._org_has_enabled_pipeline_policies("acme"))

    def test_lookup_exception_fails_toward_no_detection(self):
        sync = MagicMock()
        sync.is_loaded = True
        sync.get_policies.side_effect = RuntimeError("redis down")
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertFalse(main._org_has_enabled_pipeline_policies("acme"))


class StreamingOutputDetectionActiveTests(unittest.TestCase):
    """``_streaming_output_detection_active`` = enabled pipeline policy OR Tier-2 on."""

    def test_zero_policy_tier2_off_is_inactive(self):
        # Zero_Policy_State: no enabled policy AND Tier-2 absent (None) => inactive.
        sync = _make_policy_sync(loaded=True, policies=[])
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertFalse(
                main._streaming_output_detection_active("acme", {"tier2_enabled": None})
            )
            # missing key behaves the same as absent tri-state
            self.assertFalse(main._streaming_output_detection_active("acme", {}))
            self.assertFalse(main._streaming_output_detection_active("acme", None))

    def test_enabled_pipeline_policy_activates(self):
        sync = _make_policy_sync(loaded=True, policies=[_pipeline_policy_entry()])
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertTrue(
                main._streaming_output_detection_active("acme", {"tier2_enabled": None})
            )

    def test_tier2_enabled_alone_activates(self):
        # Tier-2 opt-in ON with NO enabled Tier-1 policy still runs the output guard.
        sync = _make_policy_sync(loaded=True, policies=[])
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertTrue(
                main._streaming_output_detection_active("acme", {"tier2_enabled": True})
            )

    def test_tier2_truthy_but_not_true_is_off(self):
        # resolve_tier2_enabled is strict: only the literal True enables (R3.7).
        sync = _make_policy_sync(loaded=True, policies=[])
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertFalse(
                main._streaming_output_detection_active("acme", {"tier2_enabled": "yes"})
            )
            self.assertFalse(
                main._streaming_output_detection_active("acme", {"tier2_enabled": 1})
            )

    def test_unresolvable_everything_is_inactive(self):
        # Policy cache not loaded AND Tier-2 absent => fail toward no detection.
        sync = _make_policy_sync(loaded=False, policies=[_pipeline_policy_entry()])
        with patch.object(main, "POLICY_SYNC", sync):
            self.assertFalse(
                main._streaming_output_detection_active("acme", {"tier2_enabled": None})
            )


class StreamScanModeWiringTests(unittest.TestCase):
    """The gate flag drives ``resolve_scan_mode`` exactly as
    ``_launch_chat_stream_response`` wires it: inactive => NONE (no output guard
    attached, Passthrough); active => OUTPUT_GUARD."""

    @staticmethod
    def _scan_mode_for(active: bool) -> StreamScanMode:
        # Mirror the task-7.1 wiring in _launch_chat_stream_response: the built-in
        # scanner + OUTPUT_GUARD are attached ONLY when detection is active.
        input_scanner = MagicMock() if active else None
        output_guard = MagicMock() if active else None
        return resolve_scan_mode(
            output_scan_enabled=True,
            input_scanner=input_scanner,
            output_guard=output_guard,
        )

    def test_inactive_yields_passthrough_none(self):
        self.assertEqual(self._scan_mode_for(active=False), StreamScanMode.NONE)

    def test_active_yields_output_guard(self):
        self.assertEqual(self._scan_mode_for(active=True), StreamScanMode.OUTPUT_GUARD)

    def test_zero_policy_stream_passes_representative_prompts_untouched(self):
        # For every representative attack/PII/secret prompt, a zero-policy + Tier-2-off
        # org resolves detection INACTIVE => scan_mode NONE => the streamed output is
        # never scanned (Passthrough on the streaming surface).
        sync = _make_policy_sync(loaded=True, policies=[])
        cfg = PolicyDrivenDetectionScaffold.make_org_config()  # tier2_enabled=None
        prompts = {**ATTACK_PROMPTS, **PII_PROMPTS, **SECRET_PROMPTS, **BENIGN_PROMPTS}
        with patch.object(main, "POLICY_SYNC", sync):
            for name, _prompt in prompts.items():
                with self.subTest(prompt=name):
                    active = main._streaming_output_detection_active("acme", cfg)
                    self.assertFalse(active)
                    self.assertEqual(self._scan_mode_for(active), StreamScanMode.NONE)

    def test_enabled_policy_stream_runs_output_guard(self):
        sync = _make_policy_sync(loaded=True, policies=[_pipeline_policy_entry()])
        cfg = PolicyDrivenDetectionScaffold.make_org_config()
        with patch.object(main, "POLICY_SYNC", sync):
            active = main._streaming_output_detection_active("acme", cfg)
            self.assertTrue(active)
            self.assertEqual(self._scan_mode_for(active), StreamScanMode.OUTPUT_GUARD)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
