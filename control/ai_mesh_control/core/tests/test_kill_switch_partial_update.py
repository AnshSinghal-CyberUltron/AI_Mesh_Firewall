"""Tests for the kill-switch PATCH self-loop bypass fix (M-23).

PATCH /api/kill-switches/{id}/ uses KillSwitchCreateSerializer; its
validate() previously read fields from ``attrs`` only, so a payload of just
{"fallback_model": "<instance.model_name>"} bypassed the self-loop check
(and the global-scope / M-22 prefix checks). The fix resolves every field
against the EFFECTIVE post-update state (payload value, else instance value).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class KillSwitchPartialUpdateTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile
        from core.models import KillSwitch

        from core.models import LLMModelConfig

        self.org = Organization.objects.create(name="KS Org", slug="ks-org")
        self.user = User.objects.create_user(
            username="ks_user", password="pass", is_staff=True,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        for name, provider, model_id in (
            ("claude-3-haiku", "anthropic", "anthropic/claude-3-haiku"),
            ("gpt-4o-mini", "openai", "openai/gpt-4o-mini"),
            ("gpt-4o", "openai", "openai/gpt-4o"),
        ):
            LLMModelConfig.objects.create(
                organization=self.org,
                provider=provider,
                model_name=name,
                model_id=model_id,
                encrypted_api_key="enc-dummy-key",
                is_active=True,
            )

        self.switch = KillSwitch.objects.create(
            organization=self.org,
            model_name="gpt-4o",
            action="reroute",
            fallback_model="claude-3-haiku",
            reason="initial",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_patch_self_loop_fallback_only_rejected(self):
        """PATCH with ONLY fallback_model == instance.model_name must 400."""
        resp = self.client.patch(
            f"/api/kill-switches/{self.switch.id}/",
            {"fallback_model": "gpt-4o"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("fallback_model", resp.json())

        self.switch.refresh_from_db()
        self.assertEqual(self.switch.fallback_model, "claude-3-haiku")

    def test_patch_self_loop_model_name_only_rejected(self):
        """PATCH with ONLY model_name == instance.fallback_model must 400."""
        resp = self.client.patch(
            f"/api/kill-switches/{self.switch.id}/",
            {"model_name": "claude-3-haiku"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("fallback_model", resp.json())

        self.switch.refresh_from_db()
        self.assertEqual(self.switch.model_name, "gpt-4o")

    def test_patch_clear_fallback_on_reroute_rejected(self):
        """PATCH blanking fallback_model while instance action is reroute must 400."""
        resp = self.client.patch(
            f"/api/kill-switches/{self.switch.id}/",
            {"fallback_model": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("fallback_model", resp.json())

    def test_patch_unrelated_field_still_works(self):
        """PATCH that doesn't touch the model/fallback pair succeeds."""
        resp = self.client.patch(
            f"/api/kill-switches/{self.switch.id}/",
            {"reason": "updated reason"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.switch.refresh_from_db()
        self.assertEqual(self.switch.reason, "updated reason")
        self.assertEqual(self.switch.model_name, "gpt-4o")
        self.assertEqual(self.switch.fallback_model, "claude-3-haiku")

    def test_legit_full_update_works(self):
        """A full PUT with a coherent payload succeeds."""
        resp = self.client.put(
            f"/api/kill-switches/{self.switch.id}/",
            {
                "model_name": "gpt-4o",
                "api_key_prefix": "",
                "action": "reroute",
                "fallback_model": "gpt-4o-mini",
                "reason": "rotate fallback",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.switch.refresh_from_db()
        self.assertEqual(self.switch.fallback_model, "gpt-4o-mini")
        self.assertEqual(self.switch.reason, "rotate fallback")

    def test_patch_prefix_onto_global_switch_rejected(self):
        """PATCH adding api_key_prefix to a global switch must 400.

        Before the fix, model_name was read from attrs only ("" on PATCH),
        so the global-scope prefix rejection never fired. A valid org-owned
        GatewayAPIKey is created so the M-22 ownership check passes and the
        failure can only come from the global-scope rule.
        """
        from core.models import GatewayAPIKey, KillSwitch

        GatewayAPIKey.objects.create(
            organization=self.org,
            prefix="zs_abcd",
            key_hash="a" * 64,
            name="test-key",
            owner=self.user,
            project_id="proj",
        )
        global_switch = KillSwitch.objects.create(
            organization=self.org,
            model_name=KillSwitch.SCOPE_GLOBAL,
            action="disable",
        )

        resp = self.client.patch(
            f"/api/kill-switches/{global_switch.id}/",
            {"api_key_prefix": "zs_abcd"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertTrue("api_key_prefix" in body or "model_name" in body)

        global_switch.refresh_from_db()
        self.assertEqual(global_switch.api_key_prefix, "")

    def test_patch_reroute_onto_global_switch_rejected(self):
        """PATCH switching a global switch to reroute must 400."""
        from core.models import KillSwitch

        global_switch = KillSwitch.objects.create(
            organization=self.org,
            model_name=KillSwitch.SCOPE_GLOBAL,
            action="disable",
        )

        resp = self.client.patch(
            f"/api/kill-switches/{global_switch.id}/",
            {"action": "reroute", "fallback_model": "gpt-4o-mini"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertTrue("action" in body or "model_name" in body)

    def test_create_self_loop_still_rejected(self):
        """Create-path self-loop rejection is unchanged by the instance fallback."""
        resp = self.client.post(
            "/api/kill-switches/",
            {
                "model_name": "gpt-4o-mini",
                "action": "reroute",
                "fallback_model": "gpt-4o-mini",
                "reason": "self-loop",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("fallback_model", resp.json())
