"""Integration tests for policy analytics category aggregation."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from policy.analytics_views import _build_category_performance
from policy.constants import ACTION_BLOCK, ACTION_REDACT
from policy.models import EnforcementEvent, Policy

User = get_user_model()


class PolicyAnalyticsCategoryTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Analytics Org", slug="analytics-org")
        self.user = User.objects.create_user(username="analytics_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.policies = [
            Policy.objects.create(
                organization=self.org,
                name=f"PII Policy {i}",
                code=f"PII_TEST_{i}",
                category="pii",
                enabled=True,
                priority=10 - i,
            )
            for i in range(3)
        ]
        self.policy_ids = [p.id for p in self.policies]

    def test_build_category_performance_dedupes_same_category(self):
        since = timezone.now() - timedelta(days=1)
        events = EnforcementEvent.objects.filter(created_at__gte=since)
        policies_base = Policy.objects.filter(enabled=True, organization=self.org)

        rows = _build_category_performance(policies_base, events)

        pii_rows = [r for r in rows if r["categoryKey"] == "pii"]
        self.assertEqual(len(pii_rows), 1)
        self.assertGreaterEqual(pii_rows[0]["policies"], 3)
        self.assertEqual(pii_rows[0]["category"], "PII Detection")

    def test_api_returns_single_pii_category_row(self):
        now = timezone.now()
        for i, policy in enumerate(self.policies):
            EnforcementEvent.objects.create(
                organization=self.org,
                policy=policy,
                action=ACTION_REDACT if i < 2 else ACTION_BLOCK,
                created_at=now,
            )

        client = APIClient()
        client.force_authenticate(user=self.user)
        response = client.get("/api/policies/analytics/?days=7")

        self.assertEqual(response.status_code, 200)
        rows = response.data["category_performance"]
        pii_rows = [r for r in rows if r.get("categoryKey") == "pii"]
        self.assertEqual(len(pii_rows), 1)
        self.assertEqual(pii_rows[0]["totalViolations"], 3)
        self.assertEqual(pii_rows[0]["redacted"], 2)
        self.assertEqual(pii_rows[0]["blocked"], 1)
        self.assertEqual(pii_rows[0]["effectiveness"], 100.0)
