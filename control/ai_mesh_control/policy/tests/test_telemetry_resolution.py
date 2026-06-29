"""Tests for telemetry policy/rule resolution at drain time."""

from __future__ import annotations

from django.test import TestCase

from policy.constants import ACTION_BLOCK, ACTION_MONITOR
from policy.models import Policy, Rule
from policy.telemetry_resolution import (
    metadata_policy_codes,
    metadata_rule_names,
    resolve_policy_rule_from_event,
)


class TelemetryResolutionTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(name="Resolve Org", slug="resolve-org")
        self.policy = Policy.objects.create(
            organization=self.org,
            name="Leak Policy",
            code="POL-9",
            category="data_leakage",
            enabled=True,
            priority=5,
        )
        self.rule = Rule.objects.create(
            policy=self.policy,
            name="No secrets",
            rule_type="keywords",
            enabled=True,
            priority=1,
            condition={"keywords": ["key"], "field": "prompt"},
            action=ACTION_BLOCK,
        )

    def test_resolve_by_ids(self):
        policy, rule = resolve_policy_rule_from_event(
            action=ACTION_BLOCK,
            organization_id=self.org.id,
            raw_metadata={
                "matched_policy_ids": [self.policy.id],
                "matched_rule_ids": [self.rule.id],
            },
        )
        self.assertEqual(policy.id, self.policy.id)
        self.assertEqual(rule.id, self.rule.id)

    def test_resolve_by_codes_and_names(self):
        policy, rule = resolve_policy_rule_from_event(
            action=ACTION_MONITOR,
            organization_id=self.org.id,
            raw_metadata={
                "matched_policies": ["POL-9"],
                "matched_rules": ["No secrets"],
            },
        )
        self.assertEqual(policy.id, self.policy.id)
        self.assertEqual(rule.id, self.rule.id)

    def test_metadata_helpers_read_extra_bucket(self):
        meta = {
            "extra": {
                "matched_policies": ["POL-9"],
                "matched_rules": ["No secrets"],
            }
        }
        self.assertEqual(metadata_policy_codes(meta), ["POL-9"])
        self.assertEqual(metadata_rule_names(meta), ["No secrets"])
