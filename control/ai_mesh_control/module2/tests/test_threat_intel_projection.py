from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import FirewallConfig
from module2.models import ThreatIntelEntry
from module2.tasks import build_threat_intel_sync_meta, safe_sync_threat_intel_to_redis
from module2.threat_intel_projection import apply_threat_intel_projection, parse_blocked_keywords_csv

User = get_user_model()


class _RedisStub:
    def __init__(self):
        self.set_calls = []
        self.publish_calls = []

    def set(self, key, value):
        self.set_calls.append((key, value))

    def publish(self, channel, value):
        self.publish_calls.append((channel, value))


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
        apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        self.assertIn("drop table users", parse_blocked_keywords_csv(self.cfg.blocked_keywords))

        entry.auto_block = False
        entry.save(update_fields=["auto_block"])
        apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        self.assertNotIn("drop table users", parse_blocked_keywords_csv(self.cfg.blocked_keywords))

        entry.delete()
        apply_threat_intel_projection(self.org)
        self.cfg.refresh_from_db()
        self.assertEqual(parse_blocked_keywords_csv(self.cfg.blocked_keywords), ["manual-keyword", "keep me"])

    def test_safe_sync_applies_projection_and_publishes_redis(self):
        from unittest.mock import patch

        ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="prompt_injection",
            indicator="credential dump",
            auto_block=True,
        )
        redis_stub = _RedisStub()
        with patch("module2.tasks._get_redis_client", return_value=redis_stub):
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
