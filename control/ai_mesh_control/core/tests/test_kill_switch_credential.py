"""Control-plane tests for credential-scoped kill switches."""

from __future__ import annotations

from django.test import TestCase


class KillSwitchCredentialTests(TestCase):
    def test_killswitch_credential_redis_key(self):
        from auth.models import Organization
        from core.models import KillSwitch

        org = Organization.objects.create(name="Acme", slug="acme")
        ks = KillSwitch.objects.create(
            organization=org,
            model_name="gpt-4o",
            api_key_prefix="zs_abcd",
            action="disable",
        )
        assert ks.build_redis_key() == "kill_switch:acme:credential:zs_abcd:model:gpt-4o"

        payload = ks.build_redis_payload()
        assert payload["org_slug"] == "acme"
        assert payload["api_key_prefix"] == "zs_abcd"

    def test_killswitch_credential_wide_redis_key(self):
        """Module 2 SOC uses SCOPE_CREDENTIAL — Redis key gateway must read."""
        from auth.models import Organization
        from core.models import KillSwitch

        org = Organization.objects.create(name="Acme", slug="acme")
        ks = KillSwitch.objects.create(
            organization=org,
            model_name=KillSwitch.SCOPE_CREDENTIAL,
            api_key_prefix="zs_abcd",
            action="disable",
        )
        assert (
            ks.build_redis_key()
            == "kill_switch:acme:credential:zs_abcd:model:__credential__"
        )

    def test_killswitch_create_serializer_requires_prefix_for_credential_wide(self):
        from core.models import KillSwitch
        from core.serializers import KillSwitchCreateSerializer

        ser = KillSwitchCreateSerializer(
            data={
                "model_name": KillSwitch.SCOPE_CREDENTIAL,
                "api_key_prefix": "",
                "action": "disable",
                "reason": "test",
            },
        )
        assert not ser.is_valid()
        assert "api_key_prefix" in ser.errors

    def test_killswitch_create_serializer_rejects_credential_wide_reroute(self):
        from core.models import KillSwitch
        from core.serializers import KillSwitchCreateSerializer

        ser = KillSwitchCreateSerializer(
            data={
                "model_name": KillSwitch.SCOPE_CREDENTIAL,
                "api_key_prefix": "zs_abcd",
                "action": "reroute",
                "fallback_model": "gpt-4o",
                "reason": "test",
            },
        )
        assert not ser.is_valid()
        assert "action" in ser.errors

    def test_killswitch_global_redis_key_ignores_prefix(self):
        from auth.models import Organization
        from core.models import KillSwitch

        org = Organization.objects.create(name="Acme", slug="acme")
        ks = KillSwitch.objects.create(
            organization=org,
            model_name=KillSwitch.SCOPE_GLOBAL,
            api_key_prefix="should-not-appear",
            action="disable",
        )
        assert ks.build_redis_key() == "kill_switch:acme:global"

    def test_killswitch_create_serializer_rejects_global_with_prefix(self):
        from core.models import KillSwitch
        from core.serializers import KillSwitchCreateSerializer

        ser = KillSwitchCreateSerializer(
            data={
                "model_name": KillSwitch.SCOPE_GLOBAL,
                "api_key_prefix": "zs_abcd",
                "action": "disable",
                "reason": "test",
            },
        )
        assert not ser.is_valid()
        assert "api_key_prefix" in ser.errors

    def test_killswitch_create_serializer_rejects_global_reroute(self):
        from core.models import KillSwitch
        from core.serializers import KillSwitchCreateSerializer

        ser = KillSwitchCreateSerializer(
            data={
                "model_name": KillSwitch.SCOPE_GLOBAL,
                "api_key_prefix": "",
                "action": "reroute",
                "fallback_model": "gpt-4o",
                "reason": "test",
            },
        )
        assert not ser.is_valid()
        assert "action" in ser.errors
