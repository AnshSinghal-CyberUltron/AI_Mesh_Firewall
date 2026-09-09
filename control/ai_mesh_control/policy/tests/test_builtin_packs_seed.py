"""Built-in-family seeder tests (policy-driven-detection task 5.2).

Colocated with test_builtin_packs_catalog.py / test_ciso_policy_package.py.

Asserts the DEFAULT-OFF invariant (Requirement 4.2): seeding creates one system
``Policy`` per family with ``is_system=True`` + ``enabled=False`` + the right
per-org ``code``, each carrying its rules; re-seeding is idempotent (no duplicate
policies/rules, same counts); and a seeded-but-disabled package's rules do NOT
appear in the org's compiled policy bundle (the compiler only includes
``enabled=True`` policies).
"""

from __future__ import annotations

from django.test import TestCase

from policy.builtin_packs_catalog import (
    FAMILIES,
    build_all_rule_dicts,
    build_family_rule_dicts,
    family_keys,
    policy_code_for_org,
)
from policy.builtin_packs_seed import seed_builtin_packs
from policy.compiler import PolicyCompiler
from policy.models import Policy, Rule

# The set of per-org codes this seeder is responsible for. The platform may
# auto-create OTHER system policies for a new org (e.g. a ``PII_MCP_<id>``
# default via an Organization post_save signal), so tests scope every count to
# the built-in-pack codes rather than to "all system policies".
def _builtin_codes(org):
    return {policy_code_for_org(org.id, k) for k in family_keys()}


def _builtin_policies(org):
    return Policy.objects.filter(organization=org, code__in=_builtin_codes(org))


class BuiltinPacksSeedTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(
            name="Builtin Seed Org", slug="builtin-seed-org"
        )

    # -- one Policy per family, default-OFF, right code + rules -------------

    def test_seed_creates_one_policy_per_family(self):
        summary = seed_builtin_packs(self.org)

        self.assertEqual(len(summary["families"]), len(FAMILIES))
        self.assertEqual(summary["policies_created"], len(FAMILIES))

        # Exactly one built-in package Policy per family (scoped to our codes so a
        # platform-default system policy for the org does not skew the count).
        self.assertEqual(_builtin_policies(self.org).count(), len(FAMILIES))
        for p in _builtin_policies(self.org):
            self.assertTrue(p.is_system, p.code)

        for fam in FAMILIES:
            code = policy_code_for_org(self.org.id, fam.key)
            policy = Policy.objects.get(organization=self.org, code=code)
            # is_system=True + DEFAULT-OFF (Requirement 4.2, the key invariant).
            self.assertTrue(policy.is_system, fam.key)
            self.assertFalse(policy.enabled, f"{fam.key} must be seeded enabled=False")
            self.assertEqual(policy.category, fam.category)
            # Carries exactly its catalog rules.
            expected = len(build_family_rule_dicts(fam))
            self.assertEqual(policy.rules.count(), expected, fam.key)

    def test_every_family_key_maps_to_a_policy_code(self):
        seed_builtin_packs(self.org)
        for key in family_keys():
            code = policy_code_for_org(self.org.id, key)
            self.assertTrue(
                Policy.objects.filter(organization=self.org, code=code).exists(),
                code,
            )

    def test_total_rule_count_matches_catalog(self):
        seed_builtin_packs(self.org)
        expected_total = sum(len(r) for r in build_all_rule_dicts().values())
        # Count only rules under the built-in package policies.
        actual_total = Rule.objects.filter(
            policy__in=_builtin_policies(self.org)
        ).count()
        self.assertEqual(actual_total, expected_total)

    def test_all_seeded_families_report_disabled(self):
        summary = seed_builtin_packs(self.org)
        for entry in summary["families"]:
            self.assertFalse(entry["enabled"], entry["family"])

    # -- idempotency --------------------------------------------------------

    def test_seed_is_idempotent(self):
        s1 = seed_builtin_packs(self.org)
        self.assertEqual(s1["policies_created"], len(FAMILIES))
        self.assertGreater(s1["rules_added"], 0)

        policy_count_1 = _builtin_policies(self.org).count()
        rule_count_1 = Rule.objects.filter(policy__in=_builtin_policies(self.org)).count()

        s2 = seed_builtin_packs(self.org)
        # No new policies, no new rules on re-seed.
        self.assertEqual(s2["policies_created"], 0)
        self.assertEqual(s2["rules_added"], 0)

        policy_count_2 = _builtin_policies(self.org).count()
        rule_count_2 = Rule.objects.filter(policy__in=_builtin_policies(self.org)).count()
        self.assertEqual(policy_count_2, policy_count_1)
        self.assertEqual(rule_count_2, rule_count_1)

    def test_reseed_does_not_re_enable_operator_toggle(self):
        # Operator enables a package; a re-seed must NOT silently revert it OFF.
        seed_builtin_packs(self.org)
        code = policy_code_for_org(self.org.id, FAMILIES[0].key)
        policy = Policy.objects.get(organization=self.org, code=code)
        policy.enabled = True
        policy.save(update_fields=["enabled"])

        seed_builtin_packs(self.org)
        policy.refresh_from_db()
        self.assertTrue(policy.enabled)

    # -- default-OFF invariant in the compiled bundle ----------------------

    def test_seeded_disabled_packages_absent_from_compiled_bundle(self):
        # Default-OFF invariant (Requirement 4.2): seeding contributes NO rules to
        # the compiled bundle because every family is enabled=False and the
        # compiler only includes enabled policies. (The org may carry an unrelated
        # enabled platform-default policy; we assert none of OUR codes appear.)
        seed_builtin_packs(self.org)
        bundle = PolicyCompiler().compile_all(organization=self.org)

        codes = {p.get("policy", {}).get("code") for p in bundle.get("policies", [])}
        for key in family_keys():
            self.assertNotIn(policy_code_for_org(self.org.id, key), codes)

    def test_enabling_one_package_adds_only_its_rules_to_bundle(self):
        seed_builtin_packs(self.org)
        fam = next(f for f in FAMILIES if f.key == "command_injection")
        code = policy_code_for_org(self.org.id, fam.key)
        policy = Policy.objects.get(organization=self.org, code=code)
        policy.enabled = True
        policy.save(update_fields=["enabled"])

        bundle = PolicyCompiler().compile_all(organization=self.org)
        codes = {p.get("policy", {}).get("code") for p in bundle.get("policies", [])}
        # The enabled built-in family appears...
        self.assertIn(code, codes)
        # ...and none of the OTHER (still-disabled) built-in families do.
        for key in family_keys():
            if key == fam.key:
                continue
            self.assertNotIn(policy_code_for_org(self.org.id, key), codes)

        enabled_pkg = next(
            p for p in bundle["policies"] if p.get("policy", {}).get("code") == code
        )
        self.assertEqual(len(enabled_pkg.get("rules", [])), len(build_family_rule_dicts(fam)))

    # -- per-org isolation --------------------------------------------------

    def test_per_org_isolation(self):
        from auth.models import Organization

        other = Organization.objects.create(name="Other Org", slug="other-builtin-org")
        seed_builtin_packs(self.org)

        # Other org has no built-in-pack policies until seeded itself.
        self.assertEqual(_builtin_policies(other).count(), 0)
        # Codes are per-org distinct.
        self.assertNotEqual(
            policy_code_for_org(self.org.id, "jailbreak"),
            policy_code_for_org(other.id, "jailbreak"),
        )
