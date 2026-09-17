"""T01 L01-2: control plane must reject operator REWRITE writes.

REWRITE is an unsupported capability. Saving it as allow/no-op is a fail.
model_downgrade must remain accepted.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from auth.models import Organization, UserProfile
from core.models import FirewallConfig
from policy.models import Policy, Rule
from policy.compiler import PolicyCompiler
from policy.serializers import RuleWriteSerializer

User = get_user_model()


class T01RewriteRejectTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="T01 Rewrite Org", slug="t01-rewrite-org")
        self.user = User.objects.create_user(
            username="t01-rewrite-admin",
            email="t01-rewrite@example.invalid",
            password="Password123!",
            is_staff=True,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.policy = Policy.objects.create(
            organization=self.org,
            name="T01 rewrite policy",
            code="T01_REWRITE",
            policy_domain="pipeline",
            category="test",
            severity="HIGH",
            enabled=True,
        )

    def test_firewall_config_put_rejects_rewrite(self):
        before = FirewallConfig.load(self.org).output_pii_action
        res = self.client.put(
            reverse("firewall-config"),
            {"output_pii_action": "rewrite"},
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("output_pii_action", res.data)
        self.assertEqual(FirewallConfig.load(self.org).output_pii_action, before)
        self.assertNotEqual(FirewallConfig.load(self.org).output_pii_action, "rewrite")

    def test_firewall_config_put_accepts_redact(self):
        res = self.client.put(
            reverse("firewall-config"),
            {"output_pii_action": "redact"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(FirewallConfig.load(self.org).output_pii_action, "redact")

    def test_rule_write_rejects_rewrite(self):
        ser = RuleWriteSerializer(
            data={
                "name": "bad-rewrite",
                "rule_type": "keywords",
                "condition": {"keywords": ["NOPE"], "field": "both"},
                "action": "rewrite",
                "priority": 10,
                "enabled": True,
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("action", ser.errors)

    def test_rule_write_accepts_model_downgrade(self):
        ser = RuleWriteSerializer(
            data={
                "name": "ok-downgrade",
                "rule_type": "keywords",
                "condition": {"keywords": ["NOPE"], "field": "both"},
                "action": "model_downgrade",
                "priority": 10,
                "enabled": True,
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)

    def test_compiler_remaps_existing_rewrite_to_redact(self):
        Rule.objects.create(
            policy=self.policy,
            name="legacy rewrite",
            rule_type="keywords",
            condition={"keywords": ["LEGACY_REWRITE"], "field": "both"},
            action="rewrite",
            priority=10,
            enabled=True,
        )
        snap = PolicyCompiler._build_snapshot(self.policy)
        actions = [r["action"] for r in snap["rules"]]
        self.assertNotIn("rewrite", actions)
        self.assertIn("redact", actions)
