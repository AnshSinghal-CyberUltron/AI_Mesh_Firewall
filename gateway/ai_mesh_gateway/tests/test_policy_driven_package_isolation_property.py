"""
Policy-driven detection — Task 5.5 property test: per-package isolation.

**Feature: policy-driven-detection, Property 4: For any input that would only
match package B, enabling package A yields passthrough; the decision depends
only on the packages enabled for that organization, not on which packages exist
or are enabled for other organizations.**

**Validates: Requirements 4.6, 2.4**

--------------------------------------------------------------------------------
Where this property is asserted (GATEWAY-side) and why
--------------------------------------------------------------------------------
Property 4 is about the DECISION an org's enabled-package set produces, so it is
asserted at the gateway enforcement seam — ``policy_engine.evaluate(prompt, "",
enabled_compiled_policies)`` (design §"Route Tier-1 matching through the policy
engine only", task 3.2). That call is the ONLY Tier-1 detection source on the
chat input path, and it is handed ONLY the org's ENABLED compiled packages
(design §2: an enabled package appears in ``POLICY_SYNC.get_policies(org_slug)``;
a disabled/non-enabled one is omitted; Requirement 4.4). So a package's
enabled/disabled state for an org is modeled STRUCTURALLY, per org:

  * ENABLED  package  ⇒ its compiled entry IS in the list handed to ``evaluate``.
  * DISABLED / never-enabled package ⇒ its compiled entry is ABSENT.

This is the same modeling the sibling task-3.3 module (test_policy_driven_only_
enabled.py) uses; we import its per-package compiled-entry helpers READ-ONLY and
do NOT edit it. Task 3.3 proves the single-org IFF ("a rule fires iff its package
is enabled"). Property 4 adds the two dimensions 3.3 does not cover:

  (1) ONLY-B input under A-enabled ⇒ PASSTHROUGH (allow), generalized with
      Hypothesis over WHICH package is enabled (A) and the matching input for a
      DIFFERENT package (B) — a rule in a non-enabled package never contributes,
      even on input crafted to match exactly that package (R4.6 isolation).
  (2) CROSS-ORG independence: org1's decision on the SAME input is a pure
      function of org1's enabled set — it does NOT change when a second org
      (org2) enables/disables a different package, nor with which packages
      "exist" (are defined) at all. Two orgs with the SAME enabled set reach the
      SAME decision; two orgs with DIFFERENT enabled sets are decided
      independently (Requirement 4.6 "enabling/disabling for one org SHALL NOT
      change detection for any other"; Requirement 2.4 the decision derives only
      from that org's Enabled_Policy_Set).

The catalog of "packages that exist" is grounded in the real re-homed families
(policy.builtin_packs_catalog, control-plane task 5.1) — each family is turned
into a single-keyword compiled package here so a rule's contribution is
unambiguously attributable, and "would only match package B" is exact (the
keywords are pairwise disjoint). This is the same shape the control-plane
compiler emits (``{"policy": {...}, "rules": [{...}]}``) and that
``policy_engine.evaluate`` consumes, so the gateway assertion is faithful to the
compiled-bundle-per-org mechanism the control plane produces.

This module adds NO production code and edits neither test_policy_driven_
detection.py nor the task-5.2 companion; it imports reusable scaffolds read-only.

Requirements: 4.6 (per-org package isolation preserved — one org's enable/disable
never changes another's detection), 2.4 (the decision derives solely from that
org's Enabled_Policy_Set).
"""

from __future__ import annotations

import unittest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ai_mesh_gateway.policy_engine import EvaluationResult, evaluate

# Reuse the sibling task-3.3 module's per-package compiled-entry builders
# READ-ONLY (concurrent-subagent file isolation: import only, add nothing).
# ``_package`` builds one enabled compiled package = one keyword rule, in the
# exact shape ``policy_engine.evaluate`` expects.
from ai_mesh_gateway.tests.test_policy_driven_only_enabled import _package

# The real re-homed families (control-plane task 5.1) ground "which packages
# exist"; we only need their keys/categories to model a realistic catalog of
# distinct, per-org-toggleable packages.
try:  # pragma: no cover - control plane is a sibling package; import defensively
    from policy.builtin_packs_catalog import family_keys as _catalog_family_keys

    _CATALOG_FAMILY_KEYS: tuple[str, ...] = tuple(_catalog_family_keys())
except Exception:  # pragma: no cover
    # The gateway suite must not hard-depend on the control-plane package being
    # importable; fall back to the known re-homed family set (design §2 / R4.1).
    _CATALOG_FAMILY_KEYS = (
        "prompt_injection", "jailbreak", "command_injection", "sql_injection",
        "data_leakage", "path_traversal", "goal_hijacking", "tool_overreach",
        "vector_injection", "pii_secret",
    )


# --------------------------------------------------------------------------- #
# Model each existing package as a single distinctive keyword rule. The keyword
# is derived from the family key so it is (a) unique per package and (b) provably
# disjoint from every other package's keyword — so "input that would ONLY match
# package B" is exact.
# --------------------------------------------------------------------------- #
def _keyword_for(pkg_key: str) -> str:
    # A distinctive, collision-free token per package. The ``pkgkw-`` prefix +
    # the family key guarantees pairwise-disjointness (no family key is a
    # substring of the decorated form of another).
    return f"pkgkw-{pkg_key}-secret"


# The universe of packages that "exist" (are defined) across the platform. Each
# maps to a stable policy id / code / rule id so evaluate() attributes cleanly.
_PACKAGES: dict[str, dict] = {}
for _idx, _key in enumerate(_CATALOG_FAMILY_KEYS):
    _PACKAGES[_key] = {
        "keyword": _keyword_for(_key),
        "policy_id": 1000 + _idx,
        "rule_id": 5000 + _idx,
        "code": f"PKG_{_key.upper()}",
        "name": f"Package {_key}",
        "rule_name": f"{_key}-rule",
    }

_ALL_PACKAGE_KEYS: tuple[str, ...] = tuple(_PACKAGES.keys())
_ALL_KEYWORDS: tuple[str, ...] = tuple(v["keyword"] for v in _PACKAGES.values())


def _compiled_package(pkg_key: str, action: str = "block") -> dict:
    """One ENABLED compiled package entry for ``pkg_key`` (evaluate() shape)."""
    spec = _PACKAGES[pkg_key]
    return _package(
        policy_id=spec["policy_id"],
        code=spec["code"],
        name=spec["name"],
        rule_id=spec["rule_id"],
        rule_name=spec["rule_name"],
        keyword=spec["keyword"],
        action=action,
    )


def _enabled_bundle(enabled_keys, action: str = "block") -> list[dict]:
    """The compiled bundle handed to evaluate() = ONLY the org's enabled pkgs."""
    return [_compiled_package(k, action=action) for k in enabled_keys]


def _only_matches(pkg_key: str, prefix: str, suffix: str) -> str:
    """Build an input that matches ONLY ``pkg_key``'s keyword.

    Insert the target keyword, then strip any OTHER package's keyword that the
    random affixes may have introduced, so exactly one package could ever fire.
    """
    text = f"{prefix}{_PACKAGES[pkg_key]['keyword']}{suffix}"
    for other_key, spec in _PACKAGES.items():
        if other_key == pkg_key:
            continue
        kw = spec["keyword"]
        text = text.replace(kw, "").replace(kw.upper(), "")
    # Re-assert the target keyword survived (disjoint keywords ⇒ the strips above
    # cannot remove it, but keep the invariant explicit and robust).
    if _PACKAGES[pkg_key]["keyword"] not in text:
        text = f"{text}{_PACKAGES[pkg_key]['keyword']}"
    return text


def _evaluate(prompt: str, enabled: list[dict]) -> EvaluationResult:
    """Tier-1 = policy_engine.evaluate over the org's ENABLED compiled set only."""
    return evaluate(prompt, "", enabled)


# Two distinct package keys (A, B) drawn from the existing catalog.
_distinct_pair = st.lists(
    st.sampled_from(_ALL_PACKAGE_KEYS), min_size=2, max_size=2, unique=True
)

# A subset of the catalog to act as an org's enabled set (may be empty).
_enabled_subset = st.lists(
    st.sampled_from(_ALL_PACKAGE_KEYS), unique=True, max_size=len(_ALL_PACKAGE_KEYS)
)

_affix = st.text(max_size=60)


class PerPackageIsolationProperties(unittest.TestCase):
    """Property 4 (per-package isolation) — Validates: Requirements 4.6, 2.4."""

    # ---- (1) only-B input under A-enabled ⇒ passthrough --------------------- #

    @settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(pair=_distinct_pair, prefix=_affix, suffix=_affix)
    def test_only_B_input_under_A_enabled_is_passthrough(self, pair, prefix, suffix):
        """Enable ONLY package A; feed an input that matches ONLY package B's
        rule. B is not in the enabled set ⇒ its rule never fires ⇒ the decision
        is allow (passthrough). Generalized over which A is enabled and which
        different B the input targets (R4.6)."""
        pkg_a, pkg_b = pair
        enabled = _enabled_bundle([pkg_a], action="block")
        prompt = _only_matches(pkg_b, prefix, suffix)
        result = _evaluate(prompt, enabled)
        self.assertEqual(
            result.action,
            "allow",
            msg=f"only-{pkg_b} input under {pkg_a}-enabled must pass through: {prompt!r}",
        )
        self.assertEqual(result.matched_rule_ids, [])
        self.assertNotIn(_PACKAGES[pkg_b]["rule_name"], result.matched_rule_names)
        self.assertNotIn(_PACKAGES[pkg_b]["name"], result.matched_policy_names)

    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(enabled_keys=_enabled_subset, target=st.sampled_from(_ALL_PACKAGE_KEYS),
           prefix=_affix, suffix=_affix)
    def test_input_matching_a_nonenabled_package_never_fires(self, enabled_keys, target, prefix, suffix):
        """For an arbitrary enabled subset and an input that matches ONLY
        ``target``: the decision is non-allow IFF target is in the enabled set;
        if target is NOT enabled the result is allow, regardless of which OTHER
        packages are enabled (a non-enabled package's rule never contributes)."""
        enabled = _enabled_bundle(enabled_keys, action="block")
        prompt = _only_matches(target, prefix, suffix)
        result = _evaluate(prompt, enabled)
        if target in enabled_keys:
            self.assertEqual(result.action, "block",
                             msg=f"enabled {target} must fire on its own input: {prompt!r}")
            self.assertIn(_PACKAGES[target]["rule_name"], result.matched_rule_names)
        else:
            self.assertEqual(result.action, "allow",
                             msg=f"non-enabled {target} must not fire: {prompt!r}")
            self.assertEqual(result.matched_rule_ids, [])

    # ---- (2) cross-org independence ---------------------------------------- #

    @settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(
        org1_enabled=_enabled_subset,
        org2_enabled=_enabled_subset,
        target=st.sampled_from(_ALL_PACKAGE_KEYS),
        prefix=_affix,
        suffix=_affix,
    )
    def test_decision_independent_of_other_orgs_enabled_set(
        self, org1_enabled, org2_enabled, target, prefix, suffix
    ):
        """org1's decision on a fixed input depends ONLY on org1's enabled set —
        it is identical no matter what org2 has enabled. We compute org1's
        decision once, then again with org2 given an arbitrary (independent)
        enabled set, and assert org1's outcome is unchanged (R4.6: a change for
        one org never changes another's detection)."""
        prompt = _only_matches(target, prefix, suffix)

        # org1 decided purely from its own enabled set.
        org1_bundle = _enabled_bundle(org1_enabled, action="block")
        org1_result = _evaluate(prompt, org1_bundle)

        # org2 is decided from ITS own set; org2's set must not affect org1.
        org2_bundle = _enabled_bundle(org2_enabled, action="block")
        _ = _evaluate(prompt, org2_bundle)  # evaluated independently (no shared state)

        # Re-decide org1: same input, same org1 set -> must be identical, whatever
        # org2 enabled. (evaluate is a pure function of its compiled_policies arg.)
        org1_again = _evaluate(prompt, _enabled_bundle(org1_enabled, action="block"))
        self.assertEqual(org1_result.action, org1_again.action)
        self.assertEqual(
            sorted(org1_result.matched_policy_codes),
            sorted(org1_again.matched_policy_codes),
            msg="org1's decision must not depend on org2's enabled set",
        )

        # The ground truth: org1 fires iff target ∈ org1's own enabled set,
        # regardless of org2.
        expected = "block" if target in org1_enabled else "allow"
        self.assertEqual(
            org1_result.action, expected,
            msg=(f"org1 decision must derive solely from org1's set "
                 f"(org1={sorted(org1_enabled)}, org2={sorted(org2_enabled)}, "
                 f"target={target})"),
        )

    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(enabled_keys=_enabled_subset, target=st.sampled_from(_ALL_PACKAGE_KEYS),
           prefix=_affix, suffix=_affix)
    def test_two_orgs_same_enabled_set_reach_same_decision(self, enabled_keys, target, prefix, suffix):
        """Two DIFFERENT orgs with the SAME enabled package set reach the SAME
        decision on the same input — the decision is a function of the enabled
        set, not of org identity (R2.4 / R4.6)."""
        prompt = _only_matches(target, prefix, suffix)
        # Distinct org identity is irrelevant to evaluate(): the SAME enabled set
        # is the only input that matters. Build two independent bundles.
        res_org_a = _evaluate(prompt, _enabled_bundle(enabled_keys, action="block"))
        res_org_b = _evaluate(prompt, _enabled_bundle(enabled_keys, action="block"))
        self.assertEqual(res_org_a.action, res_org_b.action)
        self.assertEqual(
            sorted(res_org_a.matched_policy_codes),
            sorted(res_org_b.matched_policy_codes),
        )

    # ---- decision depends only on the ENABLED set, not on which pkgs exist -- #

    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(enabled_keys=_enabled_subset, target=st.sampled_from(_ALL_PACKAGE_KEYS),
           prefix=_affix, suffix=_affix)
    def test_decision_unaffected_by_which_other_packages_exist(self, enabled_keys, target, prefix, suffix):
        """The decision does not depend on which packages *exist* (are defined) —
        only on which are ENABLED for the org. Evaluating with just the enabled
        set present yields the same decision as any superset of definitions would,
        because evaluate() only ever sees the enabled entries. Modeled by
        confirming a non-enabled target (which 'exists' in the catalog) never
        contributes and an enabled target always does."""
        enabled = _enabled_bundle(enabled_keys, action="block")
        prompt = _only_matches(target, prefix, suffix)
        result = _evaluate(prompt, enabled)
        fired = target in enabled_keys
        self.assertEqual(result.action, "block" if fired else "allow")
        # A package that merely EXISTS but is not enabled contributes nothing.
        if not fired:
            self.assertEqual(result.matched_policy_codes, [])


class PerPackageIsolationExampleAnchors(unittest.TestCase):
    """Deterministic anchors for Property 4 (fast, no Hypothesis)."""

    def test_enable_A_only_B_input_passthrough(self):
        a, b = _ALL_PACKAGE_KEYS[0], _ALL_PACKAGE_KEYS[1]
        enabled = _enabled_bundle([a], action="block")
        prompt = f"please handle {_PACKAGES[b]['keyword']} now"
        r = _evaluate(prompt, enabled)
        self.assertEqual(r.action, "allow")
        self.assertEqual(r.matched_rule_ids, [])

    def test_toggle_B_flips_only_that_orgs_decision(self):
        """Enabling B for org1 flips ONLY org1; org2 (which never enabled B)
        still passes the same B-matching input through — proving isolation."""
        b = _ALL_PACKAGE_KEYS[1]
        prompt = f"the {_PACKAGES[b]['keyword']} value"

        # org2 never enables B -> passthrough, before and after org1 changes.
        org2_enabled = _enabled_bundle([_ALL_PACKAGE_KEYS[0]], action="block")
        self.assertEqual(_evaluate(prompt, org2_enabled).action, "allow")

        # org1 enables B -> blocks for org1.
        org1_enabled_on = _enabled_bundle([_ALL_PACKAGE_KEYS[0], b], action="block")
        self.assertEqual(_evaluate(prompt, org1_enabled_on).action, "block")

        # org2 is unchanged by org1 enabling B.
        self.assertEqual(_evaluate(prompt, org2_enabled).action, "allow")

    def test_same_enabled_set_same_decision_across_orgs(self):
        target = _ALL_PACKAGE_KEYS[2]
        prompt = f"x {_PACKAGES[target]['keyword']} y"
        enabled = [target]
        r_a = _evaluate(prompt, _enabled_bundle(enabled, action="block"))
        r_b = _evaluate(prompt, _enabled_bundle(enabled, action="block"))
        self.assertEqual(r_a.action, "block")
        self.assertEqual(r_a.action, r_b.action)
        self.assertEqual(
            sorted(r_a.matched_policy_codes), sorted(r_b.matched_policy_codes)
        )

    def test_empty_enabled_set_passthrough_for_any_package_input(self):
        for key in _ALL_PACKAGE_KEYS:
            prompt = f"contains {_PACKAGES[key]['keyword']}"
            r = _evaluate(prompt, [])
            self.assertEqual(r.action, "allow", msg=f"empty set ⇒ allow for {key}")
            self.assertEqual(r.matched_rule_ids, [])


if __name__ == "__main__":
    unittest.main()
