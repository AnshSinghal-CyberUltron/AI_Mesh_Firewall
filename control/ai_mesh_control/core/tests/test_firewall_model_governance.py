"""Firewall config model governance validation."""

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from io import StringIO
from rest_framework import status
from rest_framework.test import APIClient

from auth.models import Organization, UserProfile
from core.firewall_model_governance import sanitize_allowlist_for_org
from core.models import FirewallConfig, LLMModelConfig

User = get_user_model()


class FirewallModelGovernanceApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Gov Org", slug="gov-org")
        self.user = User.objects.create_user(username="govuser", password="Password123!")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse("firewall-config")

    def test_get_includes_connected_models(self):
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="live-triage-openai",
            model_id="gpt-4o-mini",
            is_active=True,
        )
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="internal",
            model_name="zeroshield-guard-120b",
            model_id="zeroshield-guard-120b",
            is_active=True,
        )

        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        names = {m["model_name"] for m in res.data["connected_models"]}
        self.assertIn("live-triage-openai", names)
        self.assertNotIn("zeroshield-guard-120b", names)

    def test_put_rejects_unknown_allowed_model(self):
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="anthropic",
            model_name="anthropic/claude-haiku-4.5",
            model_id="claude-haiku-4-5",
            is_active=True,
        )

        res = self.client.put(
            self.url,
            {
                "allowed_models": "gpt-4, anthropic/claude-haiku-4.5",
                "default_model": "anthropic/claude-haiku-4.5",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("allowed_models", res.data)

    def test_put_accepts_connected_models_only(self):
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="live-triage-openai",
            model_id="gpt-4o-mini",
            is_active=True,
        )

        res = self.client.put(
            self.url,
            {
                "allowed_models": ["live-triage-openai"],
                "default_model": "live-triage-openai",
                "model_isolation_enabled": True,
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["allowed_models_list"], ["live-triage-openai"])
        self.assertEqual(res.data["default_model"], "live-triage-openai")
        self.assertEqual(res.data["governance_stale_models"], [])


class SanitizeFirewallAllowlistTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Sanitize Org", slug="sanitize-org")
        self.config, _ = FirewallConfig.objects.get_or_create(organization=self.org)
        self.config.allowed_models = "gpt-4, gpt-3.5-turbo, claude-3, zeroshield-guard-120b"
        self.config.default_model = "gpt-4"
        self.config.save(update_fields=["allowed_models", "default_model"])

    def test_sanitize_helper_strips_legacy_names(self):
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="live-triage-openai",
            model_id="gpt-4o-mini",
            is_active=True,
        )

        new_allowed, new_default, changed = sanitize_allowlist_for_org(
            self.org,
            allowed_models=self.config.allowed_models,
            default_model=self.config.default_model,
        )
        self.assertTrue(changed)
        # Legacy names are removed; connected models are not auto-added to the allowlist.
        self.assertEqual(new_allowed, "")
        self.assertEqual(new_default, "")

    def test_management_command_clears_stale_and_api_reports_empty_stale(self):
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="live-triage-openai",
            model_id="gpt-4o-mini",
            is_active=True,
        )

        out = StringIO()
        call_command("sanitize_firewall_allowlists", stdout=out)

        self.config.refresh_from_db()
        self.assertEqual(self.config.allowed_models, "")
        self.assertEqual(self.config.default_model, "")

        user = get_user_model().objects.create_user(username="sanitizeuser", password="Password123!")
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.get(reverse("firewall-config"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["governance_stale_models"], [])
        self.assertEqual(res.data["allowed_models_list"], [])

    def test_sanitize_keeps_intersection_with_connected(self):
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="live-triage-openai",
            model_id="gpt-4o-mini",
            is_active=True,
        )
        self.config.allowed_models = "gpt-4, live-triage-openai"
        self.config.default_model = "live-triage-openai"
        self.config.save(update_fields=["allowed_models", "default_model"])

        new_allowed, new_default, changed = sanitize_allowlist_for_org(
            self.org,
            allowed_models=self.config.allowed_models,
            default_model=self.config.default_model,
        )
        self.assertTrue(changed)
        self.assertEqual(new_allowed, "live-triage-openai")
        self.assertEqual(new_default, "live-triage-openai")
