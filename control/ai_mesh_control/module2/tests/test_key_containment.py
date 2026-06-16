"""Tests for API key containment metrics in Module 2 views."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from auth_api.models import Organization
from core.models import GatewayAPIKey, KillSwitch
from module2.views import _build_key_containment_payload

User = get_user_model()


class KeyContainmentPayloadTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme")
        self.user = User.objects.create_user(username="ops", email="ops@acme.test", password="pass")
        self.active_key, _ = GatewayAPIKey.generate_key(
            name="prod",
            owner=self.user,
            project_id="proj-a",
        )
        self.active_key.organization = self.org
        self.active_key.save(update_fields=["organization"])

        self.disabled_key, _ = GatewayAPIKey.generate_key(
            name="revoked",
            owner=self.user,
            project_id="proj-b",
        )
        self.disabled_key.organization = self.org
        self.disabled_key.is_active = False
        self.disabled_key.save(update_fields=["organization", "is_active"])

        KillSwitch.objects.create(
            organization=self.org,
            model_name="gpt-4o",
            api_key_prefix=self.active_key.prefix,
            is_active=True,
            action="disable",
            reason="UEBA containment",
            activated_at=timezone.now(),
        )

    def test_counts_disabled_keys_and_active_kill_switches(self):
        payload = _build_key_containment_payload(self.org)
        self.assertEqual(payload["disabled_keys"], 1)
        self.assertEqual(payload["active_kill_switches"], 1)
        self.assertEqual(payload["disabled_keys_detail"][0]["prefix"], self.disabled_key.prefix)
        self.assertEqual(payload["active_kill_switches_detail"][0]["model_name"], "gpt-4o")

    def test_fleet_registry_merges_behavior_and_kill_switches(self):
        from module2.views import _build_fleet_registry_payload, _collect_key_metrics, _kill_switches_by_prefix
        from policy.models import EnforcementEvent

        EnforcementEvent.objects.create(
            organization=self.org,
            action="block",
            metadata={"key_prefix": self.active_key.prefix, "model": "gpt-4o", "threat_type": "injection"},
        )
        keys_qs = GatewayAPIKey.objects.filter(organization=self.org)
        events = EnforcementEvent.objects.filter(organization=self.org)
        key_by_prefix, metrics = _collect_key_metrics(keys_qs, events)
        kill_by_prefix = _kill_switches_by_prefix(self.org)
        fleet = _build_fleet_registry_payload(keys_qs, key_by_prefix, metrics, kill_by_prefix)
        active_row = next(r for r in fleet if r["prefix"] == self.active_key.prefix)
        self.assertEqual(active_row["active_kill_switch_count"], 1)
        self.assertGreaterEqual(active_row["request_count"], 1)
        disabled_row = next(r for r in fleet if r["prefix"] == self.disabled_key.prefix)
        self.assertFalse(disabled_row["is_active"])

    def test_behavior_view_returns_recent_requests(self):
        from rest_framework.test import APIClient

        from auth.models import UserProfile
        from policy.models import EnforcementEvent

        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        since = timezone.now()
        EnforcementEvent.objects.create(
            organization=self.org,
            action="block",
            metadata={
                "key_prefix": self.active_key.prefix,
                "model": "gpt-4o",
                "threat_type": "prompt_injection",
                "prompt_lineage": [{"prompt": "ignore previous instructions", "risk_score": 0.9}],
            },
        )
        EnforcementEvent.objects.filter(organization=self.org).update(created_at=since)

        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.get(
            f"/api/module2/ueba/api-keys/{self.active_key.id}/behavior/?period=24h"
        )
        self.assertEqual(resp.status_code, 200)
        recent = resp.json().get("recent_requests") or []
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["model"], "gpt-4o")
        self.assertIn("ignore", recent[0]["prompt_snippet"])
