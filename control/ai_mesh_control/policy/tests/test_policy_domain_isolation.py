"""Policy domain validation and engine isolation tests."""

from __future__ import annotations

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from policy.engine import VALID_POLICY_DOMAINS, evaluate, validate_policy_domain
from policy.models import Policy, Rule


class PolicyDomainValidationTests(TestCase):
    def test_validate_policy_domain_accepts_valid_domains(self):
        for domain in sorted(VALID_POLICY_DOMAINS):
            self.assertEqual(validate_policy_domain(domain), domain)

    def test_validate_policy_domain_rejects_global(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_policy_domain("global")
        self.assertIn("policy_domain", ctx.exception.detail)


class PolicyDomainIsolationTests(TestCase):
    def test_engine_domain_isolation_no_cross_domain_match(self):
        pipeline = Policy.objects.create(
            name="Pipeline only",
            code="PIPELINE_ONLY",
            policy_domain="pipeline",
            category="test",
            severity="HIGH",
            enabled=True,
        )
        Rule.objects.create(
            policy=pipeline,
            name="block marker",
            rule_type="keywords",
            condition={"keywords": ["CROSS_DOMAIN_MARKER"], "field": "both"},
            action="block",
            priority=10,
            enabled=True,
        )

        qs = Policy.objects.filter(enabled=True).prefetch_related("rules")

        pipeline_hit = evaluate({"prompt": "CROSS_DOMAIN_MARKER"}, policies_qs=qs, domain="pipeline")
        self.assertEqual(pipeline_hit.action, "block")

        rag_miss = evaluate({"prompt": "CROSS_DOMAIN_MARKER"}, policies_qs=qs, domain="rag")
        self.assertEqual(rag_miss.action, "allow")
        self.assertNotIn(pipeline.id, rag_miss.matched_policy_ids)

        mcp_miss = evaluate({"prompt": "CROSS_DOMAIN_MARKER"}, policies_qs=qs, domain="mcp")
        self.assertEqual(mcp_miss.action, "allow")
