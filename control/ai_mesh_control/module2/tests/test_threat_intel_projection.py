from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import FirewallConfig
from module2.models import ThreatIntelEntry, ThreatIntelProjectionState
from module2.serializers import ThreatIntelEntrySerializer
from module2.tasks import build_threat_intel_sync_meta, safe_sync_threat_intel_to_redis
from module2.threat_intel_projection import (
    apply_threat_intel_projection,
    parse_blocked_keywords_csv,
    resolve_projection_row,
)

User = get_user_model()


class _RedisStub:
    def __init__(self, config_payload=None):
        self.set_calls = []
        self.publish_calls = []
        self._config_payload = config_payload

    def set(self, key, value):
        self.set_calls.append((key, value))

    def publish(self, channel, value):
        self.publish_calls.append((channel, value))

    def get(self, key):
        if self._config_payload is None:
            return None
        if key.startswith("firewall:config:"):
            return self._config_payload
        return None


class ThreatIntelProjectionTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Projection Org", slug="projection-org")
        self.user = User.objects.create_user(username="projection-user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.cfg = FirewallConfig.load(self.org)
        self.cfg.blocked_keywords = "manual-keyword, keep me"
        self.cfg.save(update_fields=["blocked_keywords", "updated_at"])

    def test_projection_merges_manual_keywords_and_telemetry_only_rules(self):
        ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="prompt_injection",
            indicator="ignore previous instructions",
            auto_block=True,
        )
        ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="regex_probe",
            indicator="foo.*bar",
            auto_block=True,
        )
        ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="observe_only",
            indicator="telemetry marker",
            auto_block=False,
        )

        with patch(
            "module2.threat_intel_projection.read_redis_blocked_keyword_keys",
            return_value=None,
        ):
            summary = apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        keywords = parse_blocked_keywords_csv(self.cfg.blocked_keywords)

        self.assertIn("manual-keyword", keywords)
        self.assertIn("keep me", keywords)
        self.assertIn("ignore previous instructions", keywords)
        self.assertNotIn("foo.*bar", keywords)
        self.assertEqual(summary["blocking_entries"], 1)
        self.assertEqual(summary["telemetry_only_entries"], 2)

    def test_projection_updates_on_auto_block_toggle_and_delete(self):
        entry = ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="prompt_injection",
            indicator="drop table users",
            auto_block=True,
        )
        with patch(
            "module2.threat_intel_projection.read_redis_blocked_keyword_keys",
            return_value=None,
        ):
            apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        self.assertIn("drop table users", parse_blocked_keywords_csv(self.cfg.blocked_keywords))

        entry.auto_block = False
        entry.save(update_fields=["auto_block"])
        with patch(
            "module2.threat_intel_projection.read_redis_blocked_keyword_keys",
            return_value=None,
        ):
            apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        self.assertNotIn("drop table users", parse_blocked_keywords_csv(self.cfg.blocked_keywords))

        entry.delete()
        with patch(
            "module2.threat_intel_projection.read_redis_blocked_keyword_keys",
            return_value=None,
        ):
            apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        self.assertEqual(parse_blocked_keywords_csv(self.cfg.blocked_keywords), ["manual-keyword", "keep me"])

    def test_wipe_restores_managed_keywords_and_keeps_manuals(self):
        ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="custom",
            indicator="nooo",
            auto_block=True,
        )
        with patch(
            "module2.threat_intel_projection.read_redis_blocked_keyword_keys",
            return_value=None,
        ):
            apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        self.assertIn("nooo", parse_blocked_keywords_csv(self.cfg.blocked_keywords))

        # Simulate Attack Simulator keyword wipe (DB cleared; projection state still tracks managed).
        self.cfg.blocked_keywords = ""
        self.cfg.save(update_fields=["blocked_keywords", "updated_at"])
        state = ThreatIntelProjectionState.objects.get(organization=self.org)
        self.assertIn("nooo", state.managed_blocked_keywords)

        with patch(
            "module2.threat_intel_projection.read_redis_blocked_keyword_keys",
            return_value=set(),
        ):
            summary = apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        keywords = parse_blocked_keywords_csv(self.cfg.blocked_keywords)
        self.assertIn("nooo", keywords)
        # Manuals were wiped with the CSV; heal restores managed IOCs.
        self.assertTrue(summary["blocked_keywords_updated"])
        self.assertEqual(summary["blocking_entries"], 1)

    def test_redis_drift_forces_republish_when_db_already_correct(self):
        ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="custom",
            indicator="nooo",
            auto_block=True,
        )
        with patch(
            "module2.threat_intel_projection.read_redis_blocked_keyword_keys",
            return_value=None,
        ):
            apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        self.assertIn("nooo", parse_blocked_keywords_csv(self.cfg.blocked_keywords))

        # DB still has keywords; Redis live list empty → force save/republish.
        with patch(
            "module2.threat_intel_projection.read_redis_blocked_keyword_keys",
            return_value=set(),
        ):
            summary = apply_threat_intel_projection(self.org)
        self.assertTrue(summary["redis_drift_detected"])
        self.assertTrue(summary["redis_republished"])
        self.assertFalse(summary["blocked_keywords_updated"])

    def test_pending_gateway_sync_when_not_in_firewall_config(self):
        entry = ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="custom",
            indicator="nooo",
            auto_block=True,
        )
        row = resolve_projection_row(entry, live_keyword_keys=set())
        self.assertEqual(row.effective_mode, "pending_gateway_sync")
        self.assertEqual(row.effective_reason, "not_in_firewall_blocked_keywords")

        row_live = resolve_projection_row(entry, live_keyword_keys={"nooo"})
        self.assertEqual(row_live.effective_mode, "synced_for_blocking")

        ser = ThreatIntelEntrySerializer(
            entry,
            context={"live_blocked_keyword_keys": set()},
        )
        self.assertEqual(ser.data["effective_mode"], "pending_gateway_sync")

    def test_safe_sync_applies_projection_and_publishes_redis(self):
        ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="prompt_injection",
            indicator="credential dump",
            auto_block=True,
        )
        redis_stub = _RedisStub()
        with patch("module2.tasks._get_redis_client", return_value=redis_stub):
            with patch(
                "module2.threat_intel_projection.read_redis_blocked_keyword_keys",
                return_value=None,
            ):
                ok, err = safe_sync_threat_intel_to_redis(self.org.id)

        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(len(redis_stub.set_calls), 1)
        self.assertEqual(len(redis_stub.publish_calls), 1)
        self.cfg.refresh_from_db()
        self.assertIn("credential dump", parse_blocked_keywords_csv(self.cfg.blocked_keywords))

        meta = build_threat_intel_sync_meta(self.org)
        self.assertIn("projection_mode", meta)
        self.assertEqual(meta["blocking_entries"], 1)
        self.assertEqual(meta["telemetry_only_entries"], 0)
        self.assertEqual(meta.get("pending_gateway_sync_entries", 0), 0)
