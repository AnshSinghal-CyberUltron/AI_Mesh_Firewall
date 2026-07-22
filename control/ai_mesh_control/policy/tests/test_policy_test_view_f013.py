"""F-013: PolicyTestView infers policy_domain from policy_id when omitted."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from policy.models import Policy, Rule

User = get_user_model()


class PolicyTestViewDomainInferenceTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="F013 Org", slug="f013-org")
        self.user = User.objects.create_user(username="f013_user", password="pw", is_staff=True)
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.mcp_policy = Policy.objects.create(
            name="F013 MCP block",
            code="F013_MCP_BLOCK",
            policy_domain="mcp",
            organization=self.org,
            category="pii",
            severity="HIGH",
            enabled=True,
            priority=100,
        )
        Rule.objects.create(
            policy=self.mcp_policy,
            name="ssn block",
            rule_type="regex",
            condition={"preset": "us_ssn", "direction": "input", "scope": "entire"},
            action="block",
            redaction_config={},
            priority=10,
            enabled=True,
        )

        self.pipeline_policy = Policy.objects.create(
            name="F013 pipeline allow",
            code="F013_PIPELINE",
            policy_domain="pipeline",
            organization=self.org,
            category="test",
            severity="LOW",
            enabled=True,
            priority=1,
        )

    def test_policy_id_without_domain_uses_policy_row_domain(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.post(
            "/api/policies/test/",
            {
                "policy_id": self.mcp_policy.id,
                "prompt": "patient SSN is 123-45-6789",
                "input_args": {"message": "patient SSN is 123-45-6789"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["action"], "block")

    def test_policy_id_with_mismatched_domain_returns_400(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.post(
            "/api/policies/test/",
            {
                "policy_id": self.mcp_policy.id,
                "policy_domain": "pipeline",
                "prompt": "patient SSN is 123-45-6789",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mismatch", resp.json()["detail"])

    def test_all_policies_respects_explicit_mcp_domain(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.post(
            "/api/policies/test/",
            {
                "policy_domain": "mcp",
                "prompt": "patient SSN is 123-45-6789",
                "input_args": {"message": "patient SSN is 123-45-6789"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["action"], "block")
        self.assertIn(self.mcp_policy.id, resp.json().get("matched_policy_ids") or [])
