"""Tests for analytics metadata fallbacks (top rules / top violators)."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from policy.analytics_views import _aggregate_top_rules
from policy.constants import ACTION_BLOCK, ACTION_MONITOR
from policy.models import EnforcementEvent, Policy, Rule

User = get_user_model()


class AnalyticsMetadataFallbackTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Meta Org", slug="meta-org")
        self.user = User.objects.create_user(username="meta_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.policy = Policy.objects.create(
            organization=self.org,
            name="PII Policy",
            code="POL-1",
            category="pii",
            enabled=True,
            priority=10,
        )
        self.rule = Rule.objects.create(
            policy=self.policy,
            name="Block SSN",
            rule_type="keywords",
            enabled=True,
            priority=1,
            condition={"keywords": ["ssn"], "field": "prompt"},
            action=ACTION_BLOCK,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_top_violators_includes_metadata_policy_codes(self):
        EnforcementEvent.objects.create(
            organization=self.org,
            policy=None,
            user_id=2,
            action=ACTION_MONITOR,
            metadata={
                "policy_violations": ["POL-1"],
                "matched_policies": ["POL-1"],
                "matched_rules": ["Block SSN"],
            },
        )

        response = self.client.get("/api/policies/top-violators/?days=7&limit=10")
        self.assertEqual(response.status_code, 200)
        user_rows = [r for r in response.data if r.get("endpoint_identifier") == "user-2"]
        self.assertEqual(len(user_rows), 1)
        self.assertIn("POL-1", user_rows[0]["violated_policy_codes"])

    def test_top_rules_aggregates_from_metadata_when_rule_fk_missing(self):
        EnforcementEvent.objects.create(
            organization=self.org,
            policy=None,
            rule=None,
            action=ACTION_BLOCK,
            metadata={
                "matched_policies": ["POL-1"],
                "matched_rules": ["Block SSN"],
            },
        )
        EnforcementEvent.objects.create(
            organization=self.org,
            policy=None,
            rule=None,
            action=ACTION_MONITOR,
            metadata={
                "extra": {
                    "matched_policies": ["POL-1"],
                    "matched_rules": ["Block SSN"],
                }
            },
        )

        since = timezone.now() - timedelta(days=1)
        events = EnforcementEvent.objects.filter(created_at__gte=since)
        rows = _aggregate_top_rules(events, limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule_name"], "Block SSN")
        self.assertEqual(rows[0]["triggered"], 2)
        self.assertEqual(rows[0]["blocked"], 1)
        self.assertEqual(rows[0]["monitored"], 1)

        response = self.client.get("/api/policy/top-rules/?days=7&limit=10")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["ruleName"], "Block SSN")
        self.assertEqual(response.data[0]["policyCode"], "POL-1")
        self.assertEqual(response.data[0]["triggered"], 2)

    def test_top_rules_uses_rule_fk_when_present(self):
        EnforcementEvent.objects.create(
            organization=self.org,
            policy=self.policy,
            rule=self.rule,
            action=ACTION_BLOCK,
            metadata={},
        )

        response = self.client.get("/api/policy/top-rules/?days=7&limit=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["ruleName"], "Block SSN")
        self.assertEqual(response.data[0]["policyCode"], "POL-1")
