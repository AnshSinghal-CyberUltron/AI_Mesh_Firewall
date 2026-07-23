"""Phase 2b — seed_mcp_detector_policies faithfully replicates a server's effective
enforcement (posture + Tier-1 scan-control actions) as detector-class policy rules, so no
org loses coverage when the posture is retired in Phase 3.
"""
from __future__ import annotations

from django.test import TestCase

from auth.models import Organization
from mcp_connector.models import MCPScanControl, MCPServerRegistration
from policy.mcp_seed import (
    _detector_rules_for_server,
    detector_policy_code,
    seed_mcp_detector_policies,
)
from policy.models import Policy


class DetectorRuleReplicationLogicTests(TestCase):
    """Pure-logic tests for the posture/scan-control → detector-rule mapping (no DB)."""

    def _eff(self, in_ctrl=None, out_ctrl=None, configured=False):
        base = {"tier": "tier1", "enabled": True, "action": "inherit", "target_mode": "entire", "key_path": ""}
        return {
            "scan_controls_configured": configured,
            "tier1_input": {**base, "direction": "input", **(in_ctrl or {})},
            "tier1_output": {**base, "direction": "output", **(out_ctrl or {})},
        }

    def test_posture_redact_no_controls_seeds_both_redact_all(self):
        specs = _detector_rules_for_server("redact", self._eff())
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["action"], "redact")
        self.assertEqual(specs[0]["condition"], {"detector_class": "all", "direction": "both", "scope": "entire"})

    def test_posture_tag_seeds_nothing(self):
        self.assertEqual(_detector_rules_for_server("tag", self._eff()), [])

    def test_posture_block_seeds_block(self):
        specs = _detector_rules_for_server("block", self._eff())
        self.assertEqual(specs[0]["action"], "block")

    def test_scancontrol_action_overrides_posture(self):
        # input redact (explicit), output disabled → one input-only redact rule
        eff = self._eff(in_ctrl={"action": "redact"}, out_ctrl={"enabled": False}, configured=True)
        specs = _detector_rules_for_server("tag", eff)  # posture tag, but explicit input redact
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["action"], "redact")
        self.assertEqual(specs[0]["condition"]["direction"], "input")

    def test_key_scoped_control_maps_to_key_scope(self):
        eff = self._eff(in_ctrl={"action": "redact", "target_mode": "key_path", "key_path": "arguments.body"},
                        out_ctrl={"enabled": False}, configured=True)
        specs = _detector_rules_for_server("tag", eff)
        self.assertEqual(specs[0]["condition"]["scope"], "key")
        self.assertEqual(specs[0]["condition"]["key"], "arguments.body")

    def test_disabled_direction_enforces_nothing(self):
        eff = self._eff(in_ctrl={"enabled": False}, out_ctrl={"enabled": False}, configured=True)
        self.assertEqual(_detector_rules_for_server("redact", eff), [])


class SeedMcpDetectorPoliciesDBTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme")

    def _server(self, name, posture):
        return MCPServerRegistration.objects.create(
            organization=self.org, name=name, default_scan_action=posture,
        )

    def test_seeds_detector_policy_for_redact_server(self):
        srv = self._server("gh", "redact")
        results = seed_mcp_detector_policies(self.org)
        self.assertEqual(len(results), 1)
        code = detector_policy_code(self.org.id, srv.id)
        pol = Policy.objects.get(code=code)
        self.assertTrue(pol.is_system)
        self.assertEqual(pol.mcp_server_id, srv.id)
        rule = pol.rules.get()
        self.assertEqual(rule.rule_type, "detector")
        self.assertEqual(rule.action, "redact")
        self.assertEqual(rule.condition.get("detector_class"), "all")

    def test_tag_server_seeds_no_policy(self):
        self._server("obs", "tag")
        results = seed_mcp_detector_policies(self.org)
        self.assertEqual(results, [])

    def test_idempotent_no_duplicate_rules(self):
        srv = self._server("gh", "block")
        seed_mcp_detector_policies(self.org)
        seed_mcp_detector_policies(self.org)  # second run
        pol = Policy.objects.get(code=detector_policy_code(self.org.id, srv.id))
        self.assertEqual(pol.rules.count(), 1, "re-run must not duplicate rules")
        self.assertEqual(pol.rules.get().action, "block")
