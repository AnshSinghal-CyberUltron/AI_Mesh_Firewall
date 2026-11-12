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

    # The org-wide ('__global__') scope is refused outright since 9f8c9489
    # (KillSwitchCreateSerializer.validate: "a single switch must never be able to
    # take down every model"), recorded as live behaviour N25 in
    # docs/plans/2026-11-03-gw36-s2-control-projector.md. The refusal is reported
    # on `model_name`, before the older per-field checks on `api_key_prefix` and
    # `action` that these tests used to expect (#106).
    def _assert_global_scope_refused(self, data):
        from core.serializers import KillSwitchCreateSerializer

        ser = KillSwitchCreateSerializer(data=data)
        assert not ser.is_valid()
        assert "model_name" in ser.errors, ser.errors
        assert "kill-switch scope is disabled" in str(ser.errors["model_name"][0])

    def test_killswitch_create_serializer_rejects_global_with_prefix(self):
        from core.models import KillSwitch

        self._assert_global_scope_refused(
            {
                "model_name": KillSwitch.SCOPE_GLOBAL,
                "api_key_prefix": "zs_abcd",
                "action": "disable",
                "reason": "test",
            }
        )

    def test_killswitch_create_serializer_rejects_global_reroute(self):
        from core.models import KillSwitch

        self._assert_global_scope_refused(
            {
                "model_name": KillSwitch.SCOPE_GLOBAL,
                "api_key_prefix": "",
                "action": "reroute",
                "fallback_model": "gpt-4o",
                "reason": "test",
            }
        )

    def test_killswitch_create_serializer_rejects_plain_global_disable(self):
        # The simplest org-wide switch (disable, no prefix) is refused too.
        from core.models import KillSwitch

        self._assert_global_scope_refused(
            {
                "model_name": KillSwitch.SCOPE_GLOBAL,
                "api_key_prefix": "",
                "action": "disable",
                "reason": "test",
            }
        )

    def test_killswitch_create_serializer_accepts_model_scope(self):
        # Control: the refusal is about the global scope, not every switch.
        from core.serializers import KillSwitchCreateSerializer

        ser = KillSwitchCreateSerializer(
            data={"model_name": "gpt-4o", "api_key_prefix": "", "action": "disable", "reason": "t"}
        )
        assert ser.is_valid(), ser.errors

    def test_orphan_credential_key_flagged_malformed(self):
        """kill_switch:{org}:credential:{prefix} without :model: is malformed."""
        from unittest.mock import MagicMock

        from core.redis_kill_switch_views import _scan_org_kill_switch_keys

        client = MagicMock()
        orphan = "kill_switch:acme:credential:vKhak0tJ"
        good = "kill_switch:acme:credential:vKhak0tJ:model:gpt-4o"
        client.scan_iter.return_value = [orphan, good]

        def _get(key):
            if key == orphan:
                return b'{"is_active": true, "action": "disable"}'
            return b'{"is_active": true, "action": "disable", "org_slug": "acme"}'

        client.get.side_effect = _get
        findings = _scan_org_kill_switch_keys(client, "acme")
        by_key = {f["key"]: f for f in findings}
        assert by_key[orphan]["malformed"] is True
        assert by_key[orphan]["status"] == "orphan_credential"
        assert by_key[good]["malformed"] is False
