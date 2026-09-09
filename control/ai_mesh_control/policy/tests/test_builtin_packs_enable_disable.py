"""Built-in-pack enable/disable end-to-end tests (policy-driven-detection task 5.4).

Control-plane end-to-end: seed the DEFAULT-OFF built-in family packages, then drive
the SAME compiled policy bundle the gateway consumes (the compiler behind the CISO
``compile+push`` / ``POLICY_SYNC`` path — :class:`policy.compiler.PolicyCompiler`) and
assert:

* ENABLING a seeded built-in package makes its rules appear in the org's compiled
  bundle with the **user-SELECTED action** — not a hardcoded one (Requirement 4.3,
  4.5). Enabling is the operator flipping ``Policy.enabled`` and (optionally) picking
  a per-rule/family action; the bundle must reflect exactly that choice.
* DISABLING the package removes those rules from the compiled bundle (Requirement
  4.4) — the compiler only emits ``enabled=True`` policies, so a disabled package
  contributes nothing.
* PER-ORG ISOLATION holds: enabling a package for org A does not add A's rules to
  org B's compiled bundle, and disabling for one org leaves the other's enabled
  package intact (Requirement 4.6).

Distinct from the task-5.2 companion module (``test_builtin_packs_seed.py``, which
asserts the seeding/default-OFF/idempotency invariants) and the concurrent task-5.3
module. Shared helpers from :mod:`policy.builtin_packs_catalog` /
:mod:`policy.builtin_packs_seed` are imported read-only.

_Requirements: 4.3, 4.4, 4.6_
"""

from __future__ import annotations

from django.test import TestCase

from policy.builtin_packs_catalog import (
    FAMILIES,
    build_family_rule_dicts,
    family_keys,
    policy_code_for_org,
)
from policy.builtin_packs_seed import seed_builtin_packs
from policy.compiler import PolicyCompiler
from policy.models import Policy


# --------------------------------------------------------------------------- #
# Helpers — mirror the task-5.2 module's per-org-code scoping so a platform
# post_save default system policy for a fresh org never skews an assertion.
# --------------------------------------------------------------------------- #
def _compiled_bundle(org) -> dict:
    """Compile the org's enabled policies into the gateway-consumed bundle.

    Same compiler the CISO ``compile+push`` / ``POLICY_SYNC`` path uses; we assert
    against ``compile_all`` (the pure in-memory bundle) so the test does not depend
    on a live Redis, while still exercising the exact snapshot shape the gateway
    receives via ``push_to_redis``.
    """
    return PolicyCompiler().compile_all(organization=org)


def _policy_entry(bundle: dict, code: str) -> dict | None:
    """The compiled snapshot for a given policy ``code`` (or None if absent)."""
    for entry in bundle.get("policies", []):
        if entry.get("policy", {}).get("code") == code:
            return entry
    return None


def _codes_in_bundle(bundle: dict) -> set[str]:
    return {e.get("policy", {}).get("code") for e in bundle.get("policies", [])}


def _rule_actions(policy_entry: dict) -> dict[str, str]:
    """Map each compiled rule ``name`` -> its ``action`` in a policy snapshot."""
    return {r.get("name"): r.get("action") for r in policy_entry.get("rules", [])}


def _enable(policy: Policy) -> None:
    policy.enabled = True
    policy.save(update_fields=["enabled"])


def _disable(policy: Policy) -> None:
    policy.enabled = False
    policy.save(update_fields=["enabled"])


class BuiltinPacksEnableDisableTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(
            name="Enable/Disable Org A", slug="builtin-enabledisable-org-a"
        )
        seed_builtin_packs(self.org)

        # Pick a representative block family and the redact-default family so both
        # a "block"-defaulting and a "redact"-defaulting package are exercised.
        self.block_family = next(f for f in FAMILIES if f.key == "command_injection")
        self.redact_family = next(f for f in FAMILIES if f.key == "pii_secret")

        self.block_code = policy_code_for_org(self.org.id, self.block_family.key)
        self.redact_code = policy_code_for_org(self.org.id, self.redact_family.key)
        self.block_policy = Policy.objects.get(
            organization=self.org, code=self.block_code
        )
        self.redact_policy = Policy.objects.get(
            organization=self.org, code=self.redact_code
        )

    # -- baseline: default-OFF => nothing in the bundle ---------------------

    def test_seeded_but_disabled_package_absent_from_bundle(self):
        """Requirement 4.4 baseline: a seeded-but-disabled package contributes no
        rules to the compiled bundle."""
        bundle = _compiled_bundle(self.org)
        present = _codes_in_bundle(bundle)
        for key in family_keys():
            self.assertNotIn(policy_code_for_org(self.org.id, key), present)

    # -- ENABLE => rules appear with the SELECTED action --------------------

    def test_enable_makes_rules_appear_with_default_selected_action(self):
        """Requirement 4.3: enabling a package makes ITS rules appear in the bundle,
        carrying the per-rule action the catalog seeded (the default user-selected
        action, e.g. ``block`` for command_injection)."""
        _enable(self.block_policy)
        bundle = _compiled_bundle(self.org)

        entry = _policy_entry(bundle, self.block_code)
        self.assertIsNotNone(entry, "enabled package must appear in the bundle")

        expected_rules = build_family_rule_dicts(self.block_family)
        self.assertEqual(len(entry["rules"]), len(expected_rules))

        actions = _rule_actions(entry)
        # Every command_injection rule seeds action="block"; the bundle carries it.
        for spec in expected_rules:
            self.assertEqual(
                actions.get(spec["name"]),
                spec["action"],
                f"{spec['name']} must carry its seeded action {spec['action']!r}",
            )

    def test_enable_reflects_operator_selected_action_not_hardcoded(self):
        """Requirement 4.5 (folded into 4.3): the action in the compiled bundle is
        the operator's SELECTED action, not a hardcoded one. Change one rule's action
        to a DIFFERENT valid action and assert the bundle reflects the change."""
        # A command_injection rule seeds "block"; the operator re-selects "monitor".
        rule = self.block_policy.rules.first()
        original_action = rule.action
        self.assertEqual(original_action, "block")

        rule.action = "monitor"
        rule.save(update_fields=["action"])
        _enable(self.block_policy)

        bundle = _compiled_bundle(self.org)
        entry = _policy_entry(bundle, self.block_code)
        self.assertIsNotNone(entry)
        actions = _rule_actions(entry)
        self.assertEqual(
            actions.get(rule.name),
            "monitor",
            "compiled bundle must carry the operator-selected action, not the default",
        )
        # The firewall imposes no hardcoded action: the changed value differs from
        # the seeded default and survives compilation verbatim.
        self.assertNotEqual(actions.get(rule.name), original_action)

    def test_enable_redact_family_carries_redact_action(self):
        """Requirement 4.3/4.5: the PII/secret family defaults to ``redact``; enabling
        it surfaces its rules with the redact action (proves per-family selected
        action is honored, not coerced to block)."""
        _enable(self.redact_policy)
        bundle = _compiled_bundle(self.org)
        entry = _policy_entry(bundle, self.redact_code)
        self.assertIsNotNone(entry)

        actions = _rule_actions(entry)
        for spec in build_family_rule_dicts(self.redact_family):
            self.assertEqual(
                actions.get(spec["name"]),
                spec["action"],
                spec["name"],
            )
        # At least one redact rule is present (the family is redact-by-default).
        self.assertIn("redact", set(actions.values()))

    def test_enable_one_package_does_not_add_other_disabled_packages(self):
        """Requirement 4.3/4.4: enabling ONE package adds only that package's rules;
        the other seeded-but-disabled families stay out of the bundle."""
        _enable(self.block_policy)
        bundle = _compiled_bundle(self.org)
        present = _codes_in_bundle(bundle)

        self.assertIn(self.block_code, present)
        for key in family_keys():
            if key == self.block_family.key:
                continue
            self.assertNotIn(policy_code_for_org(self.org.id, key), present)

    # -- DISABLE => rules removed ------------------------------------------

    def test_disable_removes_rules_from_bundle(self):
        """Requirement 4.4: disabling an enabled package removes its rules from the
        compiled bundle (round-trip enable -> present, disable -> absent)."""
        _enable(self.block_policy)
        self.assertIsNotNone(_policy_entry(_compiled_bundle(self.org), self.block_code))

        _disable(self.block_policy)
        bundle = _compiled_bundle(self.org)
        self.assertIsNone(
            _policy_entry(bundle, self.block_code),
            "disabled package must NOT appear in the compiled bundle",
        )
        self.assertNotIn(self.block_code, _codes_in_bundle(bundle))

    def test_disable_one_of_two_enabled_leaves_the_other(self):
        """Requirement 4.4: disabling one enabled package removes only its rules; a
        second enabled package remains in the bundle unaffected."""
        _enable(self.block_policy)
        _enable(self.redact_policy)
        present = _codes_in_bundle(_compiled_bundle(self.org))
        self.assertIn(self.block_code, present)
        self.assertIn(self.redact_code, present)

        _disable(self.block_policy)
        present = _codes_in_bundle(_compiled_bundle(self.org))
        self.assertNotIn(self.block_code, present)
        self.assertIn(self.redact_code, present)

    # -- PER-ORG ISOLATION --------------------------------------------------

    def test_enable_for_org_a_does_not_affect_org_b_bundle(self):
        """Requirement 4.6: enabling a package for org A does not add A's rules to
        org B's compiled bundle. Org B is seeded (all default-OFF) and enabling the
        SAME family for A leaves B's bundle empty of that family."""
        from auth.models import Organization

        org_b = Organization.objects.create(
            name="Enable/Disable Org B", slug="builtin-enabledisable-org-b"
        )
        seed_builtin_packs(org_b)

        _enable(self.block_policy)  # enable for org A only

        # Org A's bundle has the family; org B's does not.
        a_present = _codes_in_bundle(_compiled_bundle(self.org))
        b_present = _codes_in_bundle(_compiled_bundle(org_b))

        self.assertIn(self.block_code, a_present)
        b_block_code = policy_code_for_org(org_b.id, self.block_family.key)
        self.assertNotIn(b_block_code, b_present)
        # And org A's per-org code never leaks into org B's bundle either.
        self.assertNotIn(self.block_code, b_present)

    def test_isolation_symmetric_independent_toggles(self):
        """Requirement 4.6: org A and org B toggle the same family independently —
        A enabled + B disabled, then A disabled + B enabled — each org's bundle
        reflects only its OWN toggle."""
        from auth.models import Organization

        org_b = Organization.objects.create(
            name="Enable/Disable Org B2", slug="builtin-enabledisable-org-b2"
        )
        seed_builtin_packs(org_b)
        b_block_code = policy_code_for_org(org_b.id, self.block_family.key)
        b_block_policy = Policy.objects.get(organization=org_b, code=b_block_code)

        # A on, B off.
        _enable(self.block_policy)
        self.assertIn(self.block_code, _codes_in_bundle(_compiled_bundle(self.org)))
        self.assertNotIn(b_block_code, _codes_in_bundle(_compiled_bundle(org_b)))

        # Flip: A off, B on.
        _disable(self.block_policy)
        _enable(b_block_policy)
        self.assertNotIn(self.block_code, _codes_in_bundle(_compiled_bundle(self.org)))
        self.assertIn(b_block_code, _codes_in_bundle(_compiled_bundle(org_b)))

    def test_org_a_selected_action_does_not_bleed_to_org_b(self):
        """Requirement 4.6 + 4.5: an operator's per-rule action selection for org A
        does not change org B's compiled rules for the same family."""
        from auth.models import Organization

        org_b = Organization.objects.create(
            name="Enable/Disable Org B3", slug="builtin-enabledisable-org-b3"
        )
        seed_builtin_packs(org_b)
        b_block_code = policy_code_for_org(org_b.id, self.block_family.key)
        b_block_policy = Policy.objects.get(organization=org_b, code=b_block_code)

        # Org A re-selects a rule action to "monitor" and enables.
        a_rule = self.block_policy.rules.first()
        a_rule.action = "monitor"
        a_rule.save(update_fields=["action"])
        _enable(self.block_policy)

        # Org B enables the same family WITHOUT changing any action.
        _enable(b_block_policy)

        b_entry = _policy_entry(_compiled_bundle(org_b), b_block_code)
        self.assertIsNotNone(b_entry)
        b_actions = _rule_actions(b_entry)
        # Org B's same-named rule keeps its seeded "block" (A's "monitor" did not bleed).
        self.assertEqual(b_actions.get(a_rule.name), "block")
