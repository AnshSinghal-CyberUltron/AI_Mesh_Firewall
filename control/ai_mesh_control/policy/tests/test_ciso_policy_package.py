"""CISO 100 policy catalog and seed idempotency tests."""

from __future__ import annotations

import re

from django.test import SimpleTestCase, TestCase

from policy.ciso_policy_catalog import (
    MIN_RULE_COUNT,
    PACKAGE_ID,
    build_rule_dicts,
    package_metadata,
    policy_code_for_org,
)
from policy.ciso_rules_data import RULES
from policy.ciso_seed import seed_ciso_policy_package
from policy.compiler import PolicyCompiler


class CisoPolicyCatalogTests(SimpleTestCase):
    def test_rules_data_count(self):
        self.assertEqual(len(RULES), 100)

    def test_build_rule_dicts_count_and_ids(self):
        built = build_rule_dicts()
        self.assertEqual(len(built), MIN_RULE_COUNT)
        ids = [(r["condition"] or {}).get("ciso_rule_id") for r in built]
        self.assertEqual(len(set(ids)), 100)
        self.assertEqual(ids[0], "CISO-001")
        self.assertEqual(ids[-1], "CISO-100")

    def test_all_regex_rules_compile(self):
        for spec in build_rule_dicts():
            if spec["rule_type"] != "regex":
                continue
            pattern = (spec["condition"] or {}).get("regex")
            self.assertIsNotNone(pattern, spec["name"])
            re.compile(pattern)

    def test_keyword_rules_have_phrases(self):
        for spec in build_rule_dicts():
            if spec["rule_type"] != "keywords":
                continue
            kws = (spec["condition"] or {}).get("keywords") or []
            self.assertTrue(kws, spec["name"])

    def test_package_metadata(self):
        meta = package_metadata()
        self.assertEqual(meta["package_id"], PACKAGE_ID)
        self.assertEqual(meta["rule_count"], 100)


class CisoSeedIdempotencyTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(name="CISO Seed Org", slug="ciso-seed-org")

    def test_seed_is_idempotent(self):
        policy1, created1, added1 = seed_ciso_policy_package(self.org)
        self.assertTrue(created1)
        self.assertEqual(added1, 100)
        self.assertEqual(policy1.rules.count(), 100)

        policy2, created2, added2 = seed_ciso_policy_package(self.org)
        self.assertFalse(created2)
        self.assertEqual(added2, 0)
        self.assertEqual(policy2.id, policy1.id)
        self.assertEqual(policy2.rules.count(), 100)

    def test_compile_includes_ciso_policy(self):
        seed_ciso_policy_package(self.org)
        bundle = PolicyCompiler().compile_all(organization=self.org)
        codes = [p.get("policy", {}).get("code") for p in bundle.get("policies", [])]
        self.assertIn(policy_code_for_org(self.org.id), codes)
        ciso_policy = next(
            p for p in bundle["policies"]
            if p.get("policy", {}).get("code") == policy_code_for_org(self.org.id)
        )
        self.assertEqual(len(ciso_policy.get("rules", [])), 100)
        self.assertGreaterEqual(bundle.get("rule_count", 0), 100)
