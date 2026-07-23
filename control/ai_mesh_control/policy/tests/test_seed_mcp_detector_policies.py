"""Phase 2b — seed_mcp_detector_policies faithfully replicates a server's effective
enforcement (posture + Tier-1 scan-control actions) as detector-class policy rules, so no
org loses coverage when the posture is retired in Phase 3.
"""
from __future__ import annotations

from django.test import TestCase

from auth.models import Organization
from mcp_connector.models import MCPScanControl, MCPServerRegistration, MCPToolRegistration
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
        # block posture seeds a detector_class=all block rule + an injection block rule (#4)
        self.assertEqual(pol.rules.count(), 2, "re-run must not duplicate (detector + injection)")
        self.assertTrue(all(r.action == "block" for r in pol.rules.all()))


class DetectorSeedReconcileTests(TestCase):
    """Red-team #6/#7: re-seed converges to the CURRENT config (no stale rules)."""

    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme")

    def _server(self, name, posture):
        return MCPServerRegistration.objects.create(organization=self.org, name=name, default_scan_action=posture)

    def test_posture_change_reconciles_not_appends(self):
        srv = self._server("gh", "redact")
        seed_mcp_detector_policies(self.org)
        srv.default_scan_action = "block"
        srv.save()
        seed_mcp_detector_policies(self.org)  # re-seed after change
        pol = Policy.objects.get(code=detector_policy_code(self.org.id, srv.id))
        # reconcile: the stale redact rule is retired; block seeds detector_class=all + injection
        self.assertFalse(pol.rules.filter(action="redact").exists(), "stale redact rule retired")
        detector_all = [r for r in pol.rules.all() if (r.condition or {}).get("detector_class") == "all"]
        self.assertEqual(len(detector_all), 1, "one detector_class=all rule")
        self.assertEqual(detector_all[0].action, "block")

    def test_downgrade_to_tag_retires_seeded_rule(self):
        srv = self._server("gh", "redact")
        seed_mcp_detector_policies(self.org)
        srv.default_scan_action = "tag"
        srv.save()
        seed_mcp_detector_policies(self.org)
        pol = Policy.objects.filter(code=detector_policy_code(self.org.id, srv.id)).first()
        # policy may remain but must carry NO enforcing seeded rules
        n = pol.rules.count() if pol else 0
        self.assertEqual(n, 0, "posture redact->tag must retire the stale seeded enforcing rule")

    def test_operator_added_rule_preserved_on_reseed(self):
        from policy.models import Rule
        srv = self._server("gh", "redact")
        seed_mcp_detector_policies(self.org)
        pol = Policy.objects.get(code=detector_policy_code(self.org.id, srv.id))
        Rule.objects.create(policy=pol, name="operator", rule_type="keywords",
                            condition={"keywords": ["custom"]}, action="block",
                            description="operator-authored")
        seed_mcp_detector_policies(self.org)  # re-seed
        self.assertTrue(pol.rules.filter(description="operator-authored").exists(),
                        "operator-added rule must survive reconcile")


class PerToolSeedTests(TestCase):
    """Red-team #2 (raised) + #5 (lowered): per-tool overrides via MCPToolRegistration.scan_action."""

    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme")

    def _server(self, name, posture):
        return MCPServerRegistration.objects.create(organization=self.org, name=name, default_scan_action=posture)

    def _tool(self, server, name, action):
        return MCPToolRegistration.objects.create(server=server, tool_name=name, scan_action=action)

    def test_raised_tool_gets_per_tool_detector_rule(self):
        # server observe-only (tag), but a tool RAISED to redact -> must get coverage (else leak).
        srv = self._server("gh", "tag")
        self._tool(srv, "wire_transfer", "redact")
        seed_mcp_detector_policies(self.org)
        pol = Policy.objects.filter(code=detector_policy_code(self.org.id, srv.id)).first()
        self.assertIsNotNone(pol, "raised tool must produce a seeded policy even under tag posture")
        r = pol.rules.get()
        self.assertEqual(r.action, "redact")
        self.assertEqual(r.target_tool, "wire_transfer")
        self.assertEqual(r.condition.get("detector_class"), "all")

    def test_lowered_tool_gets_exemption(self):
        # server redact, a tool LOWERED to tag -> server-wide rule + a per-tool exemption.
        srv = self._server("gh", "redact")
        self._tool(srv, "search_docs", "tag")
        seed_mcp_detector_policies(self.org)
        pol = Policy.objects.get(code=detector_policy_code(self.org.id, srv.id))
        rules = list(pol.rules.all())
        server_wide = [r for r in rules if not r.target_tool]
        exemptions = [r for r in rules if (r.condition or {}).get("exempt") and r.target_tool == "search_docs"]
        self.assertTrue(server_wide, "server-wide detector rule present (covers all + future tools)")
        self.assertEqual(len(exemptions), 1, "lowered tool gets exactly one exemption")
        self.assertEqual(exemptions[0].action, "allow")

    def test_tool_matching_server_needs_no_per_tool_rule(self):
        srv = self._server("gh", "redact")
        self._tool(srv, "same", "inherit")  # inherits server redact -> no override
        seed_mcp_detector_policies(self.org)
        pol = Policy.objects.get(code=detector_policy_code(self.org.id, srv.id))
        self.assertFalse(pol.rules.filter(target_tool="same").exists(), "matching tool needs no per-tool rule")

    def test_per_tool_reseed_idempotent(self):
        srv = self._server("gh", "tag")
        self._tool(srv, "wire_transfer", "block")
        seed_mcp_detector_policies(self.org)
        seed_mcp_detector_policies(self.org)
        pol = Policy.objects.get(code=detector_policy_code(self.org.id, srv.id))
        # block tool seeds a per-tool detector rule + a per-tool injection rule; re-run must not duplicate
        wt = [r for r in pol.rules.filter(target_tool="wire_transfer")]
        detector_all = [r for r in wt if (r.condition or {}).get("detector_class") == "all"]
        injection = [r for r in wt if (r.condition or {}).get("detector_class") == "injection"]
        self.assertEqual(len(detector_all), 1, "no per-tool detector duplicate")
        self.assertEqual(len(injection), 1, "no per-tool injection duplicate")


class InjectionSeedTests(TestCase):
    """#4: a BLOCK posture also seeds an injection block rule."""
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme")

    def test_block_posture_seeds_injection_rule(self):
        srv = MCPServerRegistration.objects.create(organization=self.org, name="gh", default_scan_action="block")
        seed_mcp_detector_policies(self.org)
        pol = Policy.objects.get(code=detector_policy_code(self.org.id, srv.id))
        inj = [r for r in pol.rules.all() if (r.condition or {}).get("detector_class") == "injection"]
        self.assertEqual(len(inj), 1, "block posture seeds one injection block rule")
        self.assertEqual(inj[0].action, "block")

    def test_redact_posture_no_injection_rule(self):
        srv = MCPServerRegistration.objects.create(organization=self.org, name="gh", default_scan_action="redact")
        seed_mcp_detector_policies(self.org)
        pol = Policy.objects.get(code=detector_policy_code(self.org.id, srv.id))
        inj = [r for r in pol.rules.all() if (r.condition or {}).get("detector_class") == "injection"]
        self.assertEqual(inj, [], "redact posture seeds no injection rule")
