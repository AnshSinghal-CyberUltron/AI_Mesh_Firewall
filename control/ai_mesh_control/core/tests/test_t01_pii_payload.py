"""T01 L01-3: gateway payload must emit pii_detection_enabled including JSON false.

Distinct from the four removed legacy scan-toggle keys
(input_scan_enabled / output_scan_enabled / scan_block_on_injection / scan_block_on_pii).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from auth.models import Organization, UserProfile
from core.models import FirewallConfig

User = get_user_model()


class T01PiiPayloadTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="T01 PII Org", slug="t01-pii-org")
        self.user = User.objects.create_user(
            username="t01-pii-admin",
            email="t01-pii@example.invalid",
            password="Password123!",
            is_staff=True,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_gateway_payload_emits_pii_true(self):
        cfg = FirewallConfig.load(self.org)
        cfg.pii_detection_enabled = True
        cfg.save()
        payload = FirewallConfig.load(self.org).build_gateway_payload()
        self.assertIn("pii_detection_enabled", payload)
        self.assertIs(payload["pii_detection_enabled"], True)
        for legacy in (
            "input_scan_enabled",
            "output_scan_enabled",
            "scan_block_on_injection",
            "scan_block_on_pii",
        ):
            self.assertNotIn(legacy, payload)

    def test_gateway_payload_emits_pii_false_not_omitted(self):
        cfg = FirewallConfig.load(self.org)
        cfg.pii_detection_enabled = False
        cfg.save()
        payload = FirewallConfig.load(self.org).build_gateway_payload()
        self.assertIn("pii_detection_enabled", payload)
        self.assertIs(payload["pii_detection_enabled"], False)

    def test_api_put_false_round_trips_and_payload_false(self):
        url = reverse("firewall-config")
        res = self.client.put(url, {"pii_detection_enabled": False}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(FirewallConfig.load(self.org).pii_detection_enabled)
        payload = FirewallConfig.load(self.org).build_gateway_payload()
        self.assertIs(payload["pii_detection_enabled"], False)
        get = self.client.get(url)
        self.assertEqual(get.status_code, 200, get.data)
        self.assertFalse(get.data["pii_detection_enabled"])
