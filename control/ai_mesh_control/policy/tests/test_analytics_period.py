"""Phase 0b: period validation (T-C1 unknown → 400) and dashboard days clamp (0b.2)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()


class AnalyticsPeriodParseTests(SimpleTestCase):
    def test_valid_periods_are_accepted(self):
        from policy.analytics_period import parse_period, VALID_PERIODS

        self.assertEqual(
            VALID_PERIODS,
            frozenset({"1h", "6h", "24h", "7d", "30d"}),
        )
        for period in VALID_PERIODS:
            self.assertEqual(parse_period(period), period)
            self.assertEqual(parse_period(period.upper()), period)

    def test_missing_period_defaults_to_24h(self):
        from policy.analytics_period import parse_period

        self.assertEqual(parse_period(None), "24h")
        self.assertEqual(parse_period(""), "24h")

    def test_unknown_period_including_90d_raises(self):
        from policy.analytics_period import InvalidAnalyticsPeriod, parse_period

        with self.assertRaises(InvalidAnalyticsPeriod):
            parse_period("90d")
        with self.assertRaises(InvalidAnalyticsPeriod):
            parse_period("365d")
        with self.assertRaises(InvalidAnalyticsPeriod):
            parse_period("yesterday")


class DashboardDaysClampTests(SimpleTestCase):
    def test_days_default_30_and_365_is_capped(self):
        from policy.analytics_period import MAX_DASHBOARD_DAYS, clamp_days

        self.assertEqual(MAX_DASHBOARD_DAYS, 30)
        self.assertEqual(clamp_days(None), 30)
        self.assertEqual(clamp_days("30"), 30)
        self.assertEqual(clamp_days("365"), 30)
        self.assertEqual(clamp_days(90), 30)
        self.assertEqual(clamp_days("abc"), 30)
        self.assertEqual(clamp_days("-1"), 30)


class RequestIdShapeTests(SimpleTestCase):
    def test_letter_starting_gateway_ids_are_usable(self):
        from policy.analytics_period import is_usable_request_id

        self.assertTrue(is_usable_request_id("zs-aaaaaaaaaaaa"))
        self.assertTrue(is_usable_request_id("req_abcd1234"))

    def test_numeric_and_short_ids_are_not_usable(self):
        from policy.analytics_period import is_usable_request_id

        self.assertFalse(is_usable_request_id("12345678"))
        self.assertFalse(is_usable_request_id("123456789012"))
        self.assertFalse(is_usable_request_id("short"))
        self.assertFalse(is_usable_request_id(None))
        self.assertFalse(is_usable_request_id(12345678))


class DashboardDaysSourceTests(SimpleTestCase):
    def test_dashboard_views_do_not_allow_365_day_windows(self):
        path = Path(__file__).resolve().parents[2] / "core" / "dashboard_views.py"
        text = path.read_text()
        self.assertNotIn(", 365)", text)
        self.assertIn("clamp_days", text)


class DashboardDaysHttpClampTests(AnalyticsAPITestCase):
    """0b.2: ?days=365 is capped to 30, not an unbounded window."""

    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Days Org", slug="days-org")
        self.user = User.objects.create_user(username="days_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_days_365_excludes_events_older_than_30d(self):
        recent = EnforcementEvent.objects.create(
            organization=self.org,
            action=ACTION_BLOCK,
            metadata={"security_risk_score": 10, "model": "gpt-4o"},
        )
        old = EnforcementEvent.objects.create(
            organization=self.org,
            action=ACTION_BLOCK,
            metadata={"security_risk_score": 90, "model": "gpt-4o"},
        )
        now = timezone.now()
        EnforcementEvent.objects.filter(pk=recent.pk).update(
            created_at=now - timedelta(days=5)
        )
        EnforcementEvent.objects.filter(pk=old.pk).update(
            created_at=now - timedelta(days=40)
        )

        resp = self.client.get("/api/dashboard/risk-distribution/?days=365")
        self.assertEqual(resp.status_code, 200)
        total = sum(int(row.get("value") or 0) for row in resp.json())
        self.assertEqual(total, 1, msg="365 must cap to 30d so the 40d-old row is excluded")
