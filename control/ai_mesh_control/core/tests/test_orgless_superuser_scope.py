"""#120 / #122: views that fell through to "every tenant" for a caller with no org.

Owner decision (12 Nov 2026): "superuser = ZeroShield staff". An org-less staff member keeps
develop's fleet-wide READS (the Admin Portal: policies, rules, dashboards, agent-version
stats, endpoint health, gateway stats, LLM models, compile status, users), and every such
request writes one ``cross_org_access`` AuditLog row with ``scope: "all"``. WRITES never
fall through to another tenant, for staff too (develop let an org-less superuser inject a
rule into any org's policy and publish its gateway plan). A caller without an org who is
not staff sees nothing. A staff member WITH an org sees their own org, as on develop.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()


class OrglessStaffFleetReadTests(AnalyticsAPITestCase):
    # /api/dashboard/ runs inside the analytics-alias transaction.

    def setUp(self):
        from auth.models import Organization, Role, UserProfile
        from core.models import Agent, Endpoint, LLMModelConfig
        from mcp_connector.models import MCPServerRegistration
        from policy.constants import ACTION_BLOCK
        from policy.models import ComplianceViolation, EnforcementEvent, Policy, Rule

        self.org_a = Organization.objects.create(name="Orgless A", slug="orgless-a")
        self.org_b = Organization.objects.create(name="Orgless B", slug="orgless-b")
        padmin, _ = Role.objects.get_or_create(name="platform_admin")

        def user(name, org, *, superuser=True, roles=()):
            u = User.objects.create_user(
                username=name, email=f"{name}@example.com", password="pw",
                is_superuser=superuser, is_staff=superuser,
            )
            profile, _ = UserProfile.objects.get_or_create(user=u)
            profile.organization = org
            profile.save(update_fields=["organization"])
            for role in roles:
                profile.roles.add(role)
            return u

        self.orgless_staff = user("orgless_staff", None)
        self.staff_b = user("orgless_staff_b", self.org_b)
        self.orgless_admin = user("orgless_padmin", None, superuser=False, roles=(padmin,))

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

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _get(self, user, url):
        resp = self._client(user).get(url)
        self.assertEqual(resp.status_code, 200, (url, resp.content))
        return resp

    def _fleet_rows(self, user=None):
        from core.models import AuditLog

        qs = AuditLog.objects.filter(action="cross_org_access", organization__isnull=True)
        if user is not None:
            qs = qs.filter(user=user)
        return [json.loads(r.details) for r in qs.order_by("id")]

    def _assert_one_fleet_row(self, view):
        rows = self._fleet_rows(self.orgless_staff)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["scope"], rows[0]["action"]), ("all", f"fleet_read.{view}"))
        self.assertIsNone(rows[0]["target_org_id"])
        from core.models import AuditLog

        AuditLog.objects.filter(action="cross_org_access").delete()

    # ── reads: org-less staff see the fleet, one audit row per request ────────────

    def test_policies_and_rules(self):
        lists = (
            ("/api/policies/?policy_domain=pipeline", b"ORGLESS_A_POL", "policies"),
            ("/api/policies/rules/", b"orgless-a-rule", "rules"),
        )
        for url, marker, view in lists:
            self.assertIn(marker, self._get(self.orgless_staff, url).content, url)
            self._assert_one_fleet_row(view)
            self.assertNotIn(marker, self._get(self.staff_b, url).content, url)
            self.assertNotIn(marker, self._get(self.orgless_admin, url).content, url)
        details = (
            (f"/api/policies/{self.policy.pk}/", b"ORGLESS_A_POL", "policies"),
            (f"/api/policies/rules/{self.rule.pk}/", b"orgless-a-rule", "rules"),
        )
        for url, marker, view in details:
            self.assertIn(marker, self._get(self.orgless_staff, url).content, url)
            self._assert_one_fleet_row(view)
            self.assertEqual(self._client(self.staff_b).get(url).status_code, 404, url)
            self.assertEqual(self._client(self.orgless_admin).get(url).status_code, 404, url)
        self.assertEqual(self._fleet_rows(self.staff_b) + self._fleet_rows(self.orgless_admin), [])

    def test_admin_endpoint_stats(self):
        self.assertEqual(self._get(self.orgless_staff, "/api/admin/endpoint-health/").json()["total"], 1)
        self._assert_one_fleet_row("admin.endpoint_health")
        self.assertEqual(
            self._get(self.orgless_staff, "/api/admin/agent-version-stats/").json()[0]["version"], "9.9.9"
        )
        self._assert_one_fleet_row("admin.agent_version_stats")
        self.assertEqual(self._get(self.staff_b, "/api/admin/endpoint-health/").json()["total"], 0)
        self.assertEqual(self._get(self.orgless_admin, "/api/admin/endpoint-health/").json()["total"], 0)

    def test_gateway_stats(self):
        self.assertEqual(len(self._get(self.orgless_staff, "/api/gateways/stats/").json()), 1)
        self._assert_one_fleet_row("gateways.stats")
        self.assertEqual(self._get(self.staff_b, "/api/gateways/stats/").json(), [])

    def test_dashboard_compliance(self):
        self.assertIn(b"GDPR", self._get(self.orgless_staff, "/api/dashboard/compliance/").content)
        self._assert_one_fleet_row("dashboard")
        self.assertNotIn(b"GDPR", self._get(self.staff_b, "/api/dashboard/compliance/").content)
        self.assertNotIn(b"GDPR", self._get(self.orgless_admin, "/api/dashboard/compliance/").content)

    def test_llm_models(self):
        self.assertIn(b"orgless-a-model", self._get(self.orgless_staff, "/api/firewall/models/").content)
        self._assert_one_fleet_row("llm_models")
        detail = f"/api/firewall/models/{self.model.pk}/"
        self.assertIn(b"orgless-a-model", self._get(self.orgless_staff, detail).content)
        self._assert_one_fleet_row("llm_model_detail")
        self.assertNotIn(b"orgless-a-model", self._get(self.staff_b, "/api/firewall/models/").content)
        self.assertEqual(self._client(self.staff_b).get(detail).status_code, 404)

    def test_compile_status_of_the_all_orgs_bundle(self):
        # With no org the view reads the "default" bundle (every org's policies): staff only.
        self._get(self.orgless_staff, "/api/policies/compile/status/")
        self._assert_one_fleet_row("policy_compile_status")
        self.assertEqual(self._client(self.orgless_admin).get("/api/policies/compile/status/").status_code, 403)

    def test_one_row_per_request_even_when_several_helpers_ask(self):
        # /api/policies/ runs get_queryset more than once on some paths; still one row.
        self._get(self.orgless_staff, "/api/policies/?policy_domain=pipeline")
        self.assertEqual(len(self._fleet_rows(self.orgless_staff)), 1)

    # ── writes never fall through to another tenant, for staff too ────────────────

    def test_orgless_staff_cannot_write_another_tenants_objects(self):
        client = self._client(self.orgless_staff)
        resp = client.post(
            "/api/policies/rules/",
            {"policy": self.policy.id, "name": "injected", "rule_type": "regex",
             "condition": {"pattern": "y"}, "action": "block", "priority": 1},
            format="json",
        )
        self.assertEqual(resp.status_code, 404, resp.content)
        resp = client.post(
            f"/api/policies/{self.policy.pk}/rules/",
            {"name": "injected2", "rule_type": "regex", "condition": {"pattern": "y"},
             "action": "block", "priority": 1},
            format="json",
        )
        self.assertIn(resp.status_code, (404, 405), resp.content)
        self.assertFalse(self.policy.rules.filter(name__startswith="injected").exists())
        self.assertEqual(
            client.patch(f"/api/policies/{self.policy.pk}/", {"name": "pwn"}, format="json").status_code, 404
        )
        self.assertEqual(
            client.patch(f"/api/policies/rules/{self.rule.pk}/", {"enabled": False}, format="json").status_code, 404
        )
        self.assertEqual(
            client.patch(f"/api/firewall/models/{self.model.pk}/", {"is_active": False}, format="json").status_code,
            404,
        )
        self.assertEqual(client.delete(f"/api/firewall/models/{self.model.pk}/").status_code, 404)
        self.assertEqual(client.delete(f"/api/policies/rules/{self.rule.pk}/").status_code, 404)
        self.assertEqual(client.delete(f"/api/policies/{self.policy.pk}/").status_code, 404)
        self.policy.refresh_from_db()
        self.rule.refresh_from_db()
        self.model.refresh_from_db()
        self.assertEqual(self.policy.name, "Orgless A policy")
        self.assertTrue(self.rule.enabled)
        self.assertTrue(self.model.is_active)
        self.assertEqual(self._fleet_rows(), [])

    def test_policy_mcp_server_choices_stay_empty_for_orgless_staff(self):
        # A write serializer: org-less staff keep reads only.
        from django.test import RequestFactory

        from policy.serializers import PolicyWriteSerializer

        for actor, expected in ((self.orgless_staff, False), (self.staff_b, False)):
            request = RequestFactory().post("/api/policies/")
            request.user = actor
            field = PolicyWriteSerializer(context={"request": request}).fields["mcp_server"]
            self.assertEqual(field.queryset.filter(pk=self.mcp_server.pk).exists(), expected)
