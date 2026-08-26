"""Phase 0b.1: attack-vector-trends period gate + SQL-group counts (T-C1)."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()


class AttackVectorTrendsPeriodTests(AnalyticsAPITestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Trends Org", slug="trends-org")
        self.user = User.objects.create_user(username="trends_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_unknown_period_90d_is_400_not_silent_24h(self):
        resp = self.client.get("/api/security/attack-vector-trends/?period=90d")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("period", str(body).lower() + str(body.get("detail", "")).lower())

    def test_valid_24h_returns_200_with_bucket_keys(self):
        ev = EnforcementEvent.objects.create(
            organization=self.org,
            action=ACTION_BLOCK,
            metadata={
                "source": "security_scan",
                "threat_category": "prompt injection",
                "owasp_code": "LLM01",
            },
        )
        EnforcementEvent.objects.filter(pk=ev.pk).update(
            created_at=timezone.now() - timedelta(minutes=10)
        )
        resp = self.client.get("/api/security/attack-vector-trends/?period=24h")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 0)
        self.assertIn("promptInjection", rows[0])
        self.assertGreaterEqual(sum(r.get("promptInjection", 0) for r in rows), 1)

    def test_soc_and_module_kpis_reject_90d(self):
        for path in (
            "/api/security/soc-kpis/?period=90d",
            "/api/security/module-kpis/?period=90d",
            "/api/security/module-trends/?period=90d",
        ):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 400, msg=path)
