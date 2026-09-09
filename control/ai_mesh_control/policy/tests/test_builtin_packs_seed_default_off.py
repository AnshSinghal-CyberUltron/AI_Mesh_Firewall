"""Built-in-pack DEFAULT-OFF / idempotency / compiled-bundle-absence tests
(policy-driven-detection task 5.3).

A NEW isolated module (does NOT edit the task-5.2 companion
``test_builtin_packs_seed.py``) that pins the three unit-level invariants task
5.3 calls out, at the control plane, against live Postgres:

  1. **Disabled on seed** — ``seed_builtin_packs`` creates EVERY re-homed family
     package with ``enabled=False`` (Requirement 4.2). Asserted at the DB row
     level, the return-summary level, AND for every rule's parent policy.
  2. **Idempotent re-seed** — running the seeder twice (and a third time)
     creates no duplicate ``Policy`` or ``Rule`` rows; policy/rule counts and
     per-family rule identities are stable.
  3. **Disabled package absent from the compiled bundle** — a seeded-but-disabled
     package contributes ZERO policies and ZERO rules to the org's compiled
     policy bundle (``PolicyCompiler.compile_all`` only includes
     ``enabled=True`` policies), so seeding alone yields no detection
     (Requirement 4.4). Cross-checked via ``rule_count`` and by scanning every
     compiled rule's ``condition["family"]`` for a built-in family tag.

These assertions overlap the 5.2 module by design intent but are authored
independently at a finer grain (summary + DB-row + compiled-rule-condition
level) so 5.3 stands on its own without editing the concurrently-running 5.2/5.4
modules. Helpers are imported read-only from the shared catalog/seed/compiler
modules.

_Requirements: 4.2, 4.4_
"""

from __future__ import annotations

from django.test import TestCase

from policy.builtin_packs_catalog import (
    FAMILIES,
    PACKAGE_ID,
    build_all_rule_dicts,
    build_family_rule_dicts,
    family_keys,
    policy_code_for_org,
)
from policy.builtin_packs_seed import seed_builtin_packs
from policy.compiler import PolicyCompiler
from policy.models import Policy, Rule


def _builtin_codes(org):
    """The per-org policy codes this seeder owns (scope every count to these so a
    platform-default system policy created for a new org cannot skew results)."""
    return {policy_code_for_org(org.id, k) for k in family_keys()}


def _builtin_policies(org):
    return Policy.objects.filter(organization=org, code__in=_builtin_codes(org))


def _builtin_rule_qs(org):
    return Rule.objects.filter(policy__in=_builtin_policies(org))


def _rule_identity(rule) -> tuple[str | None, str]:
    """(family, name) identity — matches the seeder's de-dup key."""
    cond = rule.condition or {}
    return cond.get("family"), rule.name


class BuiltinPacksDefaultOffTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(
            name="Default-Off Org", slug="default-off-org"
        )

    # -- ASSERTION 1: seeding creates the packages DISABLED -----------------

    def test_seed_summary_reports_every_family_disabled(self):
        summary = seed_builtin_packs(self.org)

        self.assertEqual(summary["package_id"], PACKAGE_ID)
        self.assertEqual(len(summary["families"]), len(FAMILIES))
        # The summary itself must never report an enabled built-in family.
        for entry in summary["families"]:
            self.assertFalse(entry["enabled"], f"summary: {entry['family']} enabled")

    def test_every_seeded_policy_row_is_disabled(self):
        seed_builtin_packs(self.org)

        pkgs = list(_builtin_policies(self.org))
        # Every family got a package...
        self.assertEqual(len(pkgs), len(FAMILIES))
        # ...and NONE of them is enabled (Requirement 4.2, the core invariant).
        for policy in pkgs:
            self.assertFalse(
                policy.enabled, f"{policy.code} must be seeded enabled=False"
            )
            self.assertTrue(policy.is_system, policy.code)
        # No enabled built-in package exists at all.
        self.assertEqual(
            _builtin_policies(self.org).filter(enabled=True).count(), 0
        )

    def test_every_seeded_rules_parent_policy_is_disabled(self):
        # Rules themselves are ``enabled=True`` (they are only inert because
        # their PARENT policy is disabled). Prove that inertness at the row level.
        seed_builtin_packs(self.org)

        rules = list(_builtin_rule_qs(self.org))
        self.assertGreater(len(rules), 0)
        for rule in rules:
            self.assertFalse(
                rule.policy.enabled,
                f"rule {rule.name!r} parent {rule.policy.code} is enabled",
            )

    # -- ASSERTION 2: re-seeding is IDEMPOTENT ------------------------------

    def test_reseed_creates_no_duplicate_policies_or_rules(self):
        s1 = seed_builtin_packs(self.org)
        self.assertEqual(s1["policies_created"], len(FAMILIES))
        self.assertGreater(s1["rules_added"], 0)

        pol_1 = _builtin_policies(self.org).count()
        rule_1 = _builtin_rule_qs(self.org).count()

        # Second run: nothing new.
        s2 = seed_builtin_packs(self.org)
        self.assertEqual(s2["policies_created"], 0)
        self.assertEqual(s2["rules_added"], 0)

        # Third run for good measure — still stable.
        s3 = seed_builtin_packs(self.org)
        self.assertEqual(s3["policies_created"], 0)
        self.assertEqual(s3["rules_added"], 0)

        self.assertEqual(_builtin_policies(self.org).count(), pol_1)
        self.assertEqual(_builtin_rule_qs(self.org).count(), rule_1)

    def test_reseed_preserves_per_family_rule_identities(self):
        # Idempotency is by (family, rule-name) identity: re-seeding must not
        # duplicate a single rule identity within any family package.
        seed_builtin_packs(self.org)
        seed_builtin_packs(self.org)

        for fam in FAMILIES:
            code = policy_code_for_org(self.org.id, fam.key)
            policy = Policy.objects.get(organization=self.org, code=code)
            identities = [_rule_identity(r) for r in policy.rules.all()]
            # No duplicate identity survived the re-seed.
            self.assertEqual(
                len(identities), len(set(identities)), f"{fam.key} has duplicate rules"
            )
            # And the count still equals the catalog for that family.
            self.assertEqual(
                policy.rules.count(), len(build_family_rule_dicts(fam)), fam.key
            )

    def test_reseed_total_rule_count_equals_catalog(self):
        seed_builtin_packs(self.org)
        seed_builtin_packs(self.org)

        expected_total = sum(len(r) for r in build_all_rule_dicts().values())
        self.assertEqual(_builtin_rule_qs(self.org).count(), expected_total)

    # -- ASSERTION 3: disabled package absent from the compiled bundle ------

    def test_seeded_disabled_packages_contribute_no_policies_to_bundle(self):
        seed_builtin_packs(self.org)
        bundle = PolicyCompiler().compile_all(organization=self.org)

        codes = {
            p.get("policy", {}).get("code") for p in bundle.get("policies", [])
        }
        for key in family_keys():
            self.assertNotIn(
                policy_code_for_org(self.org.id, key),
                codes,
                f"disabled built-in {key} leaked into compiled bundle",
            )

    def test_seeded_disabled_packages_contribute_no_rules_to_bundle(self):
        # Cross-check the policy-absence via the rule stream: not a single
        # compiled rule may carry a built-in family tag while every package is
        # still disabled (default-OFF => no detection, Requirement 4.4).
        seed_builtin_packs(self.org)
        bundle = PolicyCompiler().compile_all(organization=self.org)

        builtin_family_set = set(family_keys())
        leaked = []
        for pol in bundle.get("policies", []):
            for rule in pol.get("rules", []):
                cond = rule.get("condition") or {}
                if (
                    cond.get("package_id") == PACKAGE_ID
                    or cond.get("family") in builtin_family_set
                ):
                    leaked.append((pol.get("policy", {}).get("code"), rule.get("name")))
        self.assertEqual(leaked, [], f"built-in rules leaked into bundle: {leaked}")

    def test_compile_helper_reports_zero_builtin_rule_delta(self):
        # Compile the bundle BEFORE seeding, then AFTER seeding-but-still-disabled:
        # the built-in packages add ZERO rules to the compiled bundle because the
        # compiler only includes enabled policies. Using a delta keeps the check
        # robust to any unrelated platform-default policy the org may carry.
        before = PolicyCompiler().compile_all(organization=self.org)
        before_count = before.get("rule_count", 0)

        seed_builtin_packs(self.org)

        after = PolicyCompiler().compile_all(organization=self.org)
        after_count = after.get("rule_count", 0)

        self.assertEqual(
            after_count,
            before_count,
            "seeding default-OFF built-in packages changed the compiled rule_count",
        )
