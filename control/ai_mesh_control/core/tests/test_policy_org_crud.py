"""Org-scoped policy enable/disable and CRUD tests."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class PolicyOrgCrudTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile
        from policy.models import Policy

        self.org_a = Organization.objects.create(name="Policy Org A", slug="policy-org-a")
        self.org_b = Organization.objects.create(name="Policy Org B", slug="policy-org-b")
        self.user_a = User.objects.create_user(username="policy_a", password="pass-a")
        self.user_b = User.objects.create_user(username="policy_b", password="pass-b")
        for user, org in ((self.user_a, self.org_a), (self.user_b, self.org_b)):
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.organization = org
            profile.save(update_fields=["organization"])

        self.policy_a = Policy.objects.create(
            organization=self.org_a,
            name="Org A Policy",
            code="ORG_A_TEST",
            policy_domain="pipeline",
            enabled=True,
        )
        self.policy_b = Policy.objects.create(
            organization=self.org_b,
            name="Org B Policy",
            code="ORG_B_TEST",
            policy_domain="pipeline",
            enabled=True,
        )
        self.system_policy = Policy.objects.create(
            organization=self.org_a,
            name="System Policy",
            code="SYS_A_TEST",
            policy_domain="mcp",
            enabled=True,
            is_system=True,
        )

    def test_disable_policy_via_patch(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        resp = client.patch(
            f"/api/policies/{self.policy_a.id}/",
            {"enabled": False, "version": self.policy_a.version},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.policy_a.refresh_from_db()
        self.assertFalse(self.policy_a.enabled)

    def test_org_b_cannot_patch_org_a_policy(self):
        client = APIClient()
        client.force_authenticate(user=self.user_b)
        resp = client.patch(
            f"/api/policies/{self.policy_a.id}/",
            {"enabled": False, "version": self.policy_a.version},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_system_policy_admin_can_disable(self):
        self.user_a.is_staff = True
        self.user_a.save(update_fields=["is_staff"])
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        resp = client.patch(
            f"/api/policies/{self.system_policy.id}/",
            {"enabled": False, "version": self.system_policy.version},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.system_policy.refresh_from_db()
        self.assertFalse(self.system_policy.enabled)

    def test_system_policy_non_admin_cannot_disable(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        resp = client.patch(
            f"/api/policies/{self.system_policy.id}/",
            {"enabled": False, "version": self.system_policy.version},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.system_policy.refresh_from_db()
        self.assertTrue(self.system_policy.enabled)

    def test_system_policy_cannot_change_code(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        resp = client.patch(
            f"/api/policies/{self.system_policy.id}/",
            {"code": "HACKED", "version": self.system_policy.version},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_system_policy_cannot_delete(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        resp = client.delete(f"/api/policies/{self.system_policy.id}/")
        self.assertEqual(resp.status_code, 403)

    def test_create_requires_org(self):
        from auth.models import UserProfile

        orphan = User.objects.create_user(username="orphan", password="pass-o")
        UserProfile.objects.get_or_create(user=orphan)
        client = APIClient()
        client.force_authenticate(user=orphan)
        resp = client.post(
            "/api/policies/",
            {
                "name": "No Org",
                "code": "NO_ORG",
                "severity": "LOW",
                "policy_domain": "pipeline",
                "enabled": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_list_filter_enabled(self):
        self.policy_a.enabled = False
        self.policy_a.save(update_fields=["enabled"])
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        resp = client.get("/api/policies/?policy_domain=pipeline&enabled=false")
        self.assertEqual(resp.status_code, 200)
        results = resp.json().get("results", resp.json())
        ids = {row["id"] for row in results}
        self.assertIn(self.policy_a.id, ids)
        self.assertNotIn(self.policy_b.id, ids)
