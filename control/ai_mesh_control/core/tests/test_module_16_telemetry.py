"""Module 1.6 telemetry classification and threat-feed merge tests."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent
from policy.module_16 import (
    audit_log_to_threat_feed_item,
    is_module_16_enforcement,
    merge_module_16_feed_items,
)

User = get_user_model()


class Module16ClassificationTests(TestCase):
    def test_kill_switch_at_risk_60_is_module_16(self):
        meta = {
            "event_type": "kill_switch",
            "source": "policy",
            "module_id": "1.6",
            "security_risk_score": 60,
        }
        self.assertTrue(is_module_16_enforcement(meta))

    def test_non_isolation_event_not_module_16(self):
        meta = {
            "event_type": "request",
            "source": "security_scan",
            "security_risk_score": 90,
        }
        self.assertFalse(is_module_16_enforcement(meta))

    def test_generic_policy_source_not_module_16(self):
        meta = {
            "event_type": "policy_violation",
            "source": "policy",
            "security_risk_score": 85,
        }
        self.assertFalse(is_module_16_enforcement(meta))

    def test_audit_metadata_sanitized(self):
        from auth.models import Organization
        from core.models import KillSwitchAuditLog

        org = Organization.objects.create(name="San Org", slug="san-org")
        log = KillSwitchAuditLog.objects.create(
            organization=org,
            event="kill_switch_triggered",
            model_name="gpt-4o",
            risk_score=0.6,
            action="block",
            reason="secret operator note with api_key_prefix=zs_abcd",
            metadata={"api_key_prefix": "zs_abcd", "reason": "must not leak", "fallback_model": "gpt-mini"},
        )
        item = audit_log_to_threat_feed_item(log)
        meta = item["metadata"]
        self.assertNotIn("api_key_prefix", meta)
        self.assertNotIn("reason", meta)
        self.assertEqual(meta.get("fallback_model"), "gpt-mini")
        self.assertEqual(item["record_type"], "control_plane_audit")

    def test_audit_log_item_flagged(self):
        from auth.models import Organization
        from core.models import KillSwitchAuditLog

        org = Organization.objects.create(name="Iso Org", slug="iso-org")
        log = KillSwitchAuditLog.objects.create(
            organization=org,
            event="kill_switch_triggered",
            model_name="gpt-4o",
            risk_score=0.6,
            action="block",
            reason="operator activated kill switch",
            request_id="req-abc",
        )
        item = audit_log_to_threat_feed_item(log)
        self.assertTrue(item["metadata"]["is_audit_log"])
        self.assertEqual(item["metadata"]["module_id"], "1.6")
        self.assertEqual(item["id"], f"audit-{log.pk}")

    def test_merge_dedupes_by_request_id_prefers_enforcement(self):
        enforcement = {
            "id": "ev-1",
            "timestamp": "2026-05-28T12:00:00Z",
            "metadata": {"request_id": "req-abc", "event_type": "kill_switch"},
        }
        audit = {
            "id": "audit-1",
            "timestamp": "2026-05-28T12:01:00Z",
            "metadata": {"request_id": "req-abc", "is_audit_log": True},
        }
        merged = merge_module_16_feed_items([enforcement], [audit])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["id"], "ev-1")


class Module16ApiTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org_a = Organization.objects.create(name="Org A", slug="org-a")
        self.org_b = Organization.objects.create(name="Org B", slug="org-b")
        self.user_a = User.objects.create_user(username="user_a", password="pass-a")
        self.user_b = User.objects.create_user(username="user_b", password="pass-b")
        for user, org in ((self.user_a, self.org_a), (self.user_b, self.org_b)):
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.organization = org
            profile.save(update_fields=["organization"])

    def _create_event(self, org, **meta):
        return EnforcementEvent.objects.create(
            organization=org,
            action=ACTION_BLOCK,
            metadata=meta,
        )

    def test_module_kpis_include_kill_switch_at_60(self):
        from rest_framework.test import APIClient

        since = timezone.now() - timedelta(hours=1)
        self._create_event(
            self.org_a,
            event_type="kill_switch",
            source="policy",
            module_id="1.6",
            security_risk_score=60,
        )
        EnforcementEvent.objects.filter(pk__isnull=False).update(created_at=since)

        client = APIClient()
        client.force_authenticate(user=self.user_a)
        resp = client.get("/api/security/module-kpis/?period=24h")
        self.assertEqual(resp.status_code, 200)
        modules = resp.json().get("modules", {})
        self.assertGreater(modules.get("1.6", {}).get("total", 0), 0)

    def test_threat_feed_module_16_merges_audit_log(self):
        from core.models import KillSwitchAuditLog
        from rest_framework.test import APIClient

        self._create_event(
            self.org_a,
            event_type="kill_switch",
            source="policy",
            module_id="1.6",
            security_risk_score=60,
        )
        KillSwitchAuditLog.objects.create(
            organization=self.org_a,
            event="model_isolated",
            model_name="gpt-4o",
            risk_score=0.7,
            action="block",
            reason="manual isolate",
        )

        client = APIClient()
        client.force_authenticate(user=self.user_a)
        resp = client.get("/api/security/threat-feed/?module_id=1.6&hours=48&limit=50")
        self.assertEqual(resp.status_code, 200)
        results = resp.json().get("results", [])
        ids = {row["id"] for row in results}
        self.assertTrue(any(str(i).startswith("audit-") for i in ids))
        self.assertGreaterEqual(len(results), 2)

    def test_org_b_cannot_see_org_a_audit_in_feed(self):
        from core.models import KillSwitchAuditLog
        from rest_framework.test import APIClient

        KillSwitchAuditLog.objects.create(
            organization=self.org_a,
            event="kill_switch_triggered",
            model_name="secret-model",
            risk_score=0.8,
            action="block",
            reason="org a only",
        )

        client = APIClient()
        client.force_authenticate(user=self.user_b)
        resp = client.get("/api/security/threat-feed/?module_id=1.6&hours=48&limit=50")
        self.assertEqual(resp.status_code, 200)
        for row in resp.json().get("results", []):
            meta = row.get("metadata") or {}
            self.assertNotEqual(meta.get("model"), "secret-model")
