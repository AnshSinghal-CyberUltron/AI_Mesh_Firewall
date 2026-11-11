"""#120 audit: a superuser with NO organization no longer gets an all-tenants view.

Several views fell through to "no filter" when the caller had no org but was a
superuser (`elif is_superuser: <everything>`). R11 (auth/models.py: "is_superuser alone
must NEVER grant global cross-org access") and Phase 0a X-1 (policy/security_views.py
`_enforcement_events_for_request`: "superuser-without-org always returns .none()") say
such a caller sees nothing. A superuser WITH an org keeps seeing its own org (controls).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()


class OrglessSuperuserScopeTests(AnalyticsAPITestCase):
    # /api/dashboard/ runs inside the analytics-alias transaction.

    def setUp(self):
        from auth.models import Organization, UserProfile
        from core.models import Agent, Endpoint, LLMModelConfig
        from mcp_connector.models import MCPServerRegistration
        from policy.constants import ACTION_BLOCK
        from policy.models import ComplianceViolation, EnforcementEvent, Policy, Rule

        self.org_a = Organization.objects.create(name="Orgless A", slug="orgless-a")

        def user(name, org):
            u = User.objects.create_user(
                username=name, email=f"{name}@example.com", password="pw",
                is_superuser=True, is_staff=True,
            )
            profile, _ = UserProfile.objects.get_or_create(user=u)
            profile.organization = org
            profile.save(update_fields=["organization"])
            return u

        self.orgless_super = user("orgless_super", None)
        self.super_a = user("orgless_super_a", self.org_a)

        self.policy = Policy.objects.create(
            name="Orgless A policy", code="ORGLESS_A_POL", organization=self.org_a,
            policy_domain="pipeline", severity="LOW", enabled=True,
        )
        self.rule = Rule.objects.create(
            policy=self.policy, name="orgless-a-rule", rule_type="regex",
            condition={"pattern": "x"}, action="block", priority=1, enabled=True,
        )
        endpoint = Endpoint.objects.create(
            name="ep-a", identifier="ep-a", organization=self.org_a,
            metadata={"agent_version": "9.9.9"},
        )
        Agent.objects.create(agent_type="gateway", name="orgless-a-gateway", endpoint=endpoint)
        event = EnforcementEvent.objects.create(
            organization=self.org_a, endpoint_id=endpoint.id, action=ACTION_BLOCK, metadata={}
        )
        ComplianceViolation.objects.create(enforcement_event=event, framework="GDPR")
        self.model = LLMModelConfig.objects.create(
            organization=self.org_a, provider="openai", model_name="orgless-a-model",
            model_id="openai/gpt-4o-mini", is_active=True,
        )
        self.mcp_server = MCPServerRegistration.objects.create(
            organization=self.org_a, name="orgless-a-mcp", transport="stdio", command="npx",
        )

    def _get(self, user, url):
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200, (url, resp.content))
        return resp

    def test_policies_and_rules(self):
        for url, marker in (
            ("/api/policies/?policy_domain=pipeline", b"ORGLESS_A_POL"),
            ("/api/policies/rules/", b"orgless-a-rule"),
        ):
            self.assertNotIn(marker, self._get(self.orgless_super, url).content, url)
            self.assertIn(marker, self._get(self.super_a, url).content, url)

    def test_rule_create_on_other_org_policy_refused(self):
        client = APIClient()
        client.force_authenticate(user=self.orgless_super)
        resp = client.post(
            "/api/policies/rules/",
            {"policy": self.policy.id, "name": "injected", "rule_type": "regex",
             "condition": {"pattern": "y"}, "action": "block", "priority": 1},
            format="json",
        )
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertFalse(self.policy.rules.filter(name="injected").exists())

    def test_policy_mcp_server_choices(self):
        from django.test import RequestFactory

        from policy.serializers import PolicyWriteSerializer

        for actor, expected in ((self.orgless_super, False), (self.super_a, True)):
            request = RequestFactory().post("/api/policies/")
            request.user = actor
            field = PolicyWriteSerializer(context={"request": request}).fields["mcp_server"]
            self.assertEqual(field.queryset.filter(pk=self.mcp_server.pk).exists(), expected)

    def test_admin_endpoint_stats(self):
        self.assertEqual(self._get(self.orgless_super, "/api/admin/agent-version-stats/").json(), [])
        self.assertEqual(self._get(self.orgless_super, "/api/admin/endpoint-health/").json()["total"], 0)
        self.assertEqual(self._get(self.super_a, "/api/admin/endpoint-health/").json()["total"], 1)

    def test_gateway_stats(self):
        self.assertEqual(self._get(self.orgless_super, "/api/gateways/stats/").json(), [])
        self.assertEqual(len(self._get(self.super_a, "/api/gateways/stats/").json()), 1)

    def test_dashboard_compliance(self):
        body = self._get(self.orgless_super, "/api/dashboard/compliance/").content
        self.assertNotIn(b"GDPR", body)
        self.assertIn(b"GDPR", self._get(self.super_a, "/api/dashboard/compliance/").content)

    def test_llm_models(self):
        self.assertNotIn(b"orgless-a-model", self._get(self.orgless_super, "/api/firewall/models/").content)
        self.assertIn(b"orgless-a-model", self._get(self.super_a, "/api/firewall/models/").content)
        client = APIClient()
        client.force_authenticate(user=self.orgless_super)
        self.assertEqual(client.get(f"/api/firewall/models/{self.model.pk}/").status_code, 404)

    def test_compile_status_of_the_all_orgs_bundle_refused(self):
        # With no org the view used to read the "default" bundle, which the
        # load_policies / compile_policies commands build from EVERY org's policies.
        client = APIClient()
        client.force_authenticate(user=self.orgless_super)
        self.assertEqual(client.get("/api/policies/compile/status/").status_code, 403)
        self._get(self.super_a, "/api/policies/compile/status/")
