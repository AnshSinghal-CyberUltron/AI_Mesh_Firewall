"""Tests for TopViolatorsView metadata policy-code fallback."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from policy.telemetry_resolution import metadata_policy_codes
from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent, Policy

User = get_user_model()


class MetadataPolicyCodesTests(TestCase):
    def test_metadata_policy_codes_prefers_policy_violations(self):
        meta = {
            "policy_violations": ["PII_PKG", "JB-01"],
            "matched_policies": ["IGNORED"],
        }
        self.assertEqual(metadata_policy_codes(meta), ["PII_PKG", "JB-01"])

    def test_metadata_policy_codes_reads_extra_nested(self):
        meta = {
            "extra": {"matched_policies": ["MCP_TOOL_INJECT"]},
        }
        self.assertEqual(metadata_policy_codes(meta), ["MCP_TOOL_INJECT"])


class TopViolatorsMetadataFallbackTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Violators Org", slug="violators-org")
        self.user = User.objects.create_user(username="violators_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.policy = Policy.objects.create(
            organization=self.org,
            name="PII Policy",
            code="PII_TEST",
            category="pii",
            enabled=True,
            priority=10,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_user_violator_uses_metadata_when_policy_fk_null(self):
        EnforcementEvent.objects.create(
            organization=self.org,
            policy=None,
            action=ACTION_BLOCK,
            user_id=2,
            endpoint_id=None,
            metadata={
                "security_risk_score": 85,
                "policy_violations": ["PII_PKG", "JB-01"],
            },
        )

        response = self.client.get("/api/policies/top-violators/?days=7&limit=10")

        self.assertEqual(response.status_code, 200)
        user_row = next(r for r in response.data if r["endpoint_name"] == "User 2")
        self.assertEqual(sorted(user_row["violated_policy_codes"]), ["JB-01", "PII_PKG"])

    def test_user_violator_merges_fk_and_metadata_codes(self):
        EnforcementEvent.objects.create(
            organization=self.org,
            policy=self.policy,
            action=ACTION_BLOCK,
            user_id=2,
            endpoint_id=None,
            metadata={"matched_policy_codes": ["MCP_EVASION"]},
        )

        response = self.client.get("/api/policies/top-violators/?days=7&limit=10")

        self.assertEqual(response.status_code, 200)
        user_row = next(r for r in response.data if r["endpoint_name"] == "User 2")
        self.assertEqual(sorted(user_row["violated_policy_codes"]), ["MCP_EVASION", "PII_TEST"])
