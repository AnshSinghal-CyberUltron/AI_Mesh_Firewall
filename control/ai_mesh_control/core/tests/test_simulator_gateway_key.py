"""Per-org simulator gateway key provisioning tests."""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()


class SimulatorGatewayKeyTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org_a = Organization.objects.create(name="Org A", slug="org-a")
        self.org_b = Organization.objects.create(name="Org B", slug="org-b")
        self.user_a = User.objects.create_user(username="user_a", password="pass-a")
        self.user_b = User.objects.create_user(username="user_b", password="pass-b")
        for user, org in ((self.user_a, self.org_a), (self.user_b, self.org_b)):
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.organization = org
            profile.save(update_fields=["organization"])

    def test_ensure_simulator_for_org_creates_key(self):
        from core.models import GatewayAPIKey

        inst, raw = GatewayAPIKey.ensure_simulator_for_org(self.org_a, self.user_a)
        self.assertIsNotNone(raw)
        self.assertEqual(inst.name, "simulator")
        self.assertEqual(inst.project_id, "simulator-org-a")
        self.assertEqual(inst.organization_id, self.org_a.id)

    def test_ensure_simulator_for_org_idempotent(self):
        from core.models import GatewayAPIKey

        first, raw1 = GatewayAPIKey.ensure_simulator_for_org(self.org_a, self.user_a)
        second, raw2 = GatewayAPIKey.ensure_simulator_for_org(self.org_a, self.user_a)
        self.assertIsNotNone(raw1)
        self.assertIsNone(raw2)
        self.assertEqual(first.id, second.id)

    @override_settings(DEBUG=True)
    def test_simulator_post_provisions_org_key(self):
        from rest_framework.test import APIClient

        with self.settings(SIMULATOR_DEFAULTS_ENABLED="true"):
            os.environ["SIMULATOR_DEFAULTS_ENABLED"] = "true"
            client = APIClient()
            client.force_authenticate(user=self.user_a)
            resp = client.post("/api/gateways/simulator-default/")
            self.assertEqual(resp.status_code, 201)
            data = resp.json()
            self.assertTrue(data.get("created"))
            self.assertIn("key", data)
            self.assertEqual(data.get("name"), "simulator")
            self.assertEqual(data.get("org_id"), self.org_a.id)

            resp2 = client.post("/api/gateways/simulator-default/")
            self.assertEqual(resp2.status_code, 200)
            self.assertFalse(resp2.json().get("created"))
            self.assertNotIn("key", resp2.json())

    @override_settings(DEBUG=True)
    def test_simulator_get_metadata_without_plaintext(self):
        from core.models import GatewayAPIKey
        from rest_framework.test import APIClient

        GatewayAPIKey.ensure_simulator_for_org(self.org_a, self.user_a)
        with self.settings(SIMULATOR_DEFAULTS_ENABLED="true"):
            os.environ["SIMULATOR_DEFAULTS_ENABLED"] = "true"
            client = APIClient()
            client.force_authenticate(user=self.user_a)
            resp = client.get("/api/gateways/simulator-default/")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("has_gateway_key"))
            self.assertIn("prefix", data)
            self.assertNotIn("key", data)

    @override_settings(DEBUG=True)
    def test_simulator_keys_are_org_isolated(self):
        from rest_framework.test import APIClient

        with self.settings(SIMULATOR_DEFAULTS_ENABLED="true"):
            os.environ["SIMULATOR_DEFAULTS_ENABLED"] = "true"
            client_a = APIClient()
            client_a.force_authenticate(user=self.user_a)
            key_a = client_a.post("/api/gateways/simulator-default/").json()["key"]

            client_b = APIClient()
            client_b.force_authenticate(user=self.user_b)
            key_b = client_b.post("/api/gateways/simulator-default/").json()["key"]

            self.assertNotEqual(key_a, key_b)
