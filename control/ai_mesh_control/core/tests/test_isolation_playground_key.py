"""Isolation playground gateway key provisioning tests."""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()


class IsolationPlaygroundKeyTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org_a = Organization.objects.create(name="Org A", slug="org-a")
        self.org_b = Organization.objects.create(name="Org B", slug="org-b")
        self.user_a = User.objects.create_user(username="user_a_pg", password="pass-a")
        self.user_b = User.objects.create_user(username="user_b_pg", password="pass-b")
        for user, org in ((self.user_a, self.org_a), (self.user_b, self.org_b)):
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.organization = org
            profile.save(update_fields=["organization"])

    def test_ensure_creates_playground_key(self):
        from core.models import GatewayAPIKey

        inst, raw = GatewayAPIKey.ensure_isolation_playground_for_org(self.org_a, self.user_a)
        self.assertIsNotNone(raw)
        self.assertEqual(inst.name, "isolation-playground")
        self.assertEqual(inst.project_id, "isolation-playground-org-a")
        self.assertEqual(inst.risk_score, 0.0)
        self.assertTrue(inst.permissions.get("playground"))

    def test_ensure_idempotent(self):
        from core.models import GatewayAPIKey

        first, raw1 = GatewayAPIKey.ensure_isolation_playground_for_org(self.org_a, self.user_a)
        second, _raw2 = GatewayAPIKey.ensure_isolation_playground_for_org(self.org_a, self.user_a)
        self.assertIsNotNone(raw1)
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            GatewayAPIKey.objects.filter(
                organization=self.org_a,
                project_id="isolation-playground-org-a",
                is_active=True,
            ).count(),
            1,
        )

    def test_rotate_resets_risk_score(self):
        from core.models import GatewayAPIKey

        inst, _ = GatewayAPIKey.ensure_isolation_playground_for_org(self.org_a, self.user_a)
        GatewayAPIKey.objects.filter(pk=inst.pk).update(risk_score=0.95)
        new_inst, raw = GatewayAPIKey.rotate_isolation_playground_for_org(self.org_a, self.user_a)
        self.assertIsNotNone(raw)
        self.assertNotEqual(inst.id, new_inst.id)
        self.assertEqual(new_inst.risk_score, 0.0)
        self.assertEqual(new_inst.project_id, "isolation-playground-org-a")

    @override_settings(DEBUG=True)
    def test_post_provisions_org_key(self):
        from rest_framework.test import APIClient

        with self.settings(SIMULATOR_DEFAULTS_ENABLED="true"):
            os.environ["SIMULATOR_DEFAULTS_ENABLED"] = "true"
            client = APIClient()
            client.force_authenticate(user=self.user_a)
            resp = client.post("/api/gateways/isolation-playground/")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("has_gateway_key"))
            self.assertIn("key", data)
            self.assertEqual(data.get("project_id"), "isolation-playground-org-a")

    @override_settings(DEBUG=True)
    def test_rotate_endpoint(self):
        from core.models import GatewayAPIKey
        from rest_framework.test import APIClient

        GatewayAPIKey.ensure_isolation_playground_for_org(self.org_a, self.user_a)
        with self.settings(SIMULATOR_DEFAULTS_ENABLED="true"):
            os.environ["SIMULATOR_DEFAULTS_ENABLED"] = "true"
            client = APIClient()
            client.force_authenticate(user=self.user_a)
            resp = client.post("/api/gateways/isolation-playground/rotate/")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("rotated"))
            self.assertIn("key", data)
            self.assertEqual(data.get("risk_score"), 0.0)

    @override_settings(DEBUG=True)
    def test_keys_are_org_isolated(self):
        from rest_framework.test import APIClient

        with self.settings(SIMULATOR_DEFAULTS_ENABLED="true"):
            os.environ["SIMULATOR_DEFAULTS_ENABLED"] = "true"
            client_a = APIClient()
            client_a.force_authenticate(user=self.user_a)
            key_a = client_a.post("/api/gateways/isolation-playground/").json()["key"]

            client_b = APIClient()
            client_b.force_authenticate(user=self.user_b)
            key_b = client_b.post("/api/gateways/isolation-playground/").json()["key"]

            self.assertNotEqual(key_a, key_b)
