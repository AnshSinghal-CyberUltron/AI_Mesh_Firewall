"""policy-driven-detection task 6.2 — legacy scan toggles removed from the
control-plane firewall-config surface.

Task 6.1 removed the four legacy default-on scan toggles
(``input_scan_enabled`` / ``output_scan_enabled`` / ``scan_block_on_injection`` /
``scan_block_on_pii``) as detection DRIVERS on the gateway side. Task 6.2 is the
control-plane half: the control plane must stop SERIALIZING those four keys into

  1. the per-org gateway firewall-config payload
     (``FirewallConfig.build_gateway_payload()``, synced to Redis), and
  2. the frontend-facing config serializer response
     (``FirewallConfigSerializer``, served by ``GET /api/firewall/config/``),

so an operator no longer sees/sets a "default input scan" switch that implies
built-in detection (Requirement 5.3).

These are gateway PAYLOAD keys that mapped from backing Django model FIELDS
(``content_filtering_enabled`` / ``pii_detection_enabled`` /
``jailbreak_detection_enabled`` / ``response_filtering_enabled``). The task
explicitly does NOT drop those model fields/DB columns (they still drive
compliance frameworks + the §1.7 output-guardrail master switch); it only
de-serializes the four gateway keys. These tests pin BOTH invariants: the four
gateway keys are absent from both surfaces, AND the retained model fields still
round-trip through the API.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from auth.models import Organization, UserProfile
from core.models import FirewallConfig

User = get_user_model()

# The four legacy scan toggles removed as detection drivers (Requirement 5.1/5.3).
_LEGACY_SCAN_TOGGLE_KEYS = (
    "input_scan_enabled",
    "output_scan_enabled",
    "scan_block_on_injection",
    "scan_block_on_pii",
)


class LegacyScanTogglesRemovedFromGatewayPayloadTests(TestCase):
    """``build_gateway_payload()`` must not emit the four legacy scan toggles."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="LegacyToggleOrg", slug="legacy-toggle-org"
        )

    def test_gateway_payload_omits_every_legacy_scan_toggle(self):
        payload = FirewallConfig.load(self.org).build_gateway_payload()
        for key in _LEGACY_SCAN_TOGGLE_KEYS:
            self.assertNotIn(
                key,
                payload,
                f"legacy scan toggle {key!r} must not be serialized into the "
                "gateway firewall-config payload (task 6.2 / Requirement 5.3)",
            )

    def test_gateway_payload_omits_toggles_even_when_backing_fields_true(self):
        """A stale/enabled backing field must not resurrect the removed key."""
        cfg = FirewallConfig.load(self.org)
        cfg.content_filtering_enabled = True
        cfg.pii_detection_enabled = True
        cfg.jailbreak_detection_enabled = True
        cfg.response_filtering_enabled = True
        cfg.save()

        payload = FirewallConfig.load(self.org).build_gateway_payload()
        for key in _LEGACY_SCAN_TOGGLE_KEYS:
            self.assertNotIn(key, payload)

    def test_backing_model_fields_are_retained(self):
        """The DB columns are NOT dropped — they still drive compliance + §1.7."""
        cfg = FirewallConfig.load(self.org)
        # Setting them must not raise (fields still exist) and must persist.
        cfg.content_filtering_enabled = False
        cfg.pii_detection_enabled = False
        cfg.jailbreak_detection_enabled = False
        cfg.response_filtering_enabled = False
        cfg.save()

        reloaded = FirewallConfig.load(self.org)
        self.assertFalse(reloaded.content_filtering_enabled)
        self.assertFalse(reloaded.pii_detection_enabled)
        self.assertFalse(reloaded.jailbreak_detection_enabled)
        self.assertFalse(reloaded.response_filtering_enabled)


class LegacyScanTogglesRemovedFromSerializerTests(TestCase):
    """The frontend-facing config API must not surface the four gateway keys."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="LegacyToggleApiOrg", slug="legacy-toggle-api-org"
        )
        self.user = User.objects.create_user(
            username="legacy-toggle-admin",
            email="legacy-toggle@example.com",
            password="Password123!",
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_get_config_omits_every_legacy_scan_toggle(self):
        res = self.client.get(reverse("firewall-config"))
        self.assertEqual(res.status_code, 200, res.data)
        for key in _LEGACY_SCAN_TOGGLE_KEYS:
            self.assertNotIn(
                key,
                res.data,
                f"legacy scan toggle {key!r} must not be surfaced to the frontend "
                "settings UI (task 6.2 / Requirement 5.3)",
            )

    def test_backing_model_field_still_round_trips_through_api(self):
        """The retained fields remain operator-controllable (compliance / §1.7)."""
        url = reverse("firewall-config")
        res = self.client.put(
            url, {"response_filtering_enabled": False}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(FirewallConfig.load(self.org).response_filtering_enabled)
        # ...and the removed gateway key is still not present in the response.
        for key in _LEGACY_SCAN_TOGGLE_KEYS:
            self.assertNotIn(key, res.data)
