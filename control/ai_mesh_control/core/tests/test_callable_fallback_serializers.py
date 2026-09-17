"""Callable fallback validation for kill-switch / model-state reroute saves."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

User = get_user_model()


class CallableFallbackSerializerTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile
        from core.models import LLMModelConfig

        self.org = Organization.objects.create(name="Callable Org", slug="callable-org")
        self.user = User.objects.create_user(
            username="callable_admin", password="pass", is_staff=True,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="gpt-4o-mini",
            model_id="gpt-4o-mini",
            encrypted_api_key="enc-dummy-key",
            is_active=True,
        )
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="anthropic",
            model_name="haiku-empty-id",
            model_id="",
            encrypted_api_key="enc-dummy-key",
            is_active=True,
        )
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="anthropic",
            model_name="haiku-no-creds",
            model_id="anthropic/claude-haiku",
            encrypted_api_key="",
            api_key_env_var="",
            is_active=True,
        )

        self.factory = APIRequestFactory()
        self.request = self.factory.post("/")
        self.request.user = self.user

    def test_killswitch_rejects_empty_model_id_fallback(self):
        from core.serializers import KillSwitchCreateSerializer

        ser = KillSwitchCreateSerializer(
            data={
                "model_name": "gpt-4o-mini",
                "action": "reroute",
                "fallback_model": "haiku-empty-id",
                "reason": "vendor incident",
            },
            context={"request": self.request},
        )
        with self.assertRaises(ValidationError) as ctx:
            ser.is_valid(raise_exception=True)
        self.assertIn("fallback_model", ctx.exception.detail)

    def test_killswitch_rejects_empty_credentials_fallback(self):
        from core.serializers import KillSwitchCreateSerializer

        ser = KillSwitchCreateSerializer(
            data={
                "model_name": "gpt-4o-mini",
                "action": "reroute",
                "fallback_model": "haiku-no-creds",
                "reason": "vendor incident",
            },
            context={"request": self.request},
        )
        with self.assertRaises(ValidationError) as ctx:
            ser.is_valid(raise_exception=True)
        self.assertIn("fallback_model", ctx.exception.detail)

    def test_killswitch_rejects_auto_sentinel_fallback(self):
        from core.serializers import KillSwitchCreateSerializer

        ser = KillSwitchCreateSerializer(
            data={
                "model_name": "gpt-4o-mini",
                "action": "reroute",
                "fallback_model": "auto",
                "reason": "bad sentinel",
            },
            context={"request": self.request},
        )
        with self.assertRaises(ValidationError) as ctx:
            ser.is_valid(raise_exception=True)
        self.assertIn("fallback_model", ctx.exception.detail)

    def test_model_state_update_rejects_unknown_fallback(self):
        from core.models import ModelState
        from core.serializers import ModelStateUpdateSerializer

        state = ModelState.objects.create(
            organization=self.org,
            model_name="gpt-4o-mini",
            status="active",
        )
        ser = ModelStateUpdateSerializer(
            data={"action": "reroute", "fallback_model": "does-not-exist"},
            context={"instance": state, "request": self.request},
        )
        with self.assertRaises(ValidationError) as ctx:
            ser.is_valid(raise_exception=True)
        self.assertIn("fallback_model", ctx.exception.detail)

    def test_model_isolate_rejects_empty_model_id_fallback(self):
        from core.serializers import ModelIsolateSerializer

        ser = ModelIsolateSerializer(
            data={
                "model_name": "gpt-4o-mini",
                "action": "reroute",
                "fallback_model": "haiku-empty-id",
                "reason": "isolate",
            },
            context={"request": self.request},
        )
        with self.assertRaises(ValidationError) as ctx:
            ser.is_valid(raise_exception=True)
        self.assertIn("fallback_model", ctx.exception.detail)
