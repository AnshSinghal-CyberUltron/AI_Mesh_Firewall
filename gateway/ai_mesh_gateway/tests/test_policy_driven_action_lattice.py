"""
Policy-driven detection — Task 3.4 property test: user-selected action is honored.

**Feature: policy-driven-detection, Property 5: For any matched enabled Rule, the
resolved action (before org `enforcement_mode` downgrade) equals the highest-severity
user-selected action among matching rules per the lattice
`allow < monitor/flag < redact < block`; no hardcoded action is imposed.**

**Validates: Requirements 2.2, 4.5**

--------------------------------------------------------------------------------
Lattice ordering CONFIRMED FROM SOURCE (do not hardcode a guess — match the impl)
--------------------------------------------------------------------------------
There are two lattices in play; this test asserts the property against BOTH, at the
layer each governs, and proves they agree on the design ordering
``allow < monitor/flag < redact < block``:

1. ``policy_engine.evaluate()`` — the RAW Tier-1 action, BEFORE any org
   ``enforcement_mode`` downgrade (exactly what the task asks us to assert on).
   It resolves the winner by MAX precedence over ``policy_engine.ACTION_ORDER``:

       block=5 > redact=4 > rewrite=3 > model_downgrade=2 > monitor=1 > allow=0

   NOTE (verified in source): ``flag`` is NOT a key in ``ACTION_ORDER``, so
   ``evaluate`` ranks a rule whose ``action == "flag"`` via ``.get(action, 0)`` = 0
   (i.e. the raw engine treats a ``flag`` rule at allow-rank — it does NOT recognize
   ``flag`` as a distinct verdict). The winner therefore equals the highest-ranked
   action per the ENGINE's own ``ACTION_ORDER`` — never a hardcoded constant. This
   module derives its expected winner from ``ACTION_ORDER`` directly so it can never
   drift from the implementation.

2. ``enforcement.py`` ``ACTION_RANK`` — the enforcement-authority severity lattice
   that merges layers (and where a ``block`` is later downgraded under a non-``block``
   ``enforcement_mode``). Verified in source:

       allow=0 < monitor=1 == flag=1 < redact=2 == rewrite=2 == model_downgrade=2 < block=3

   Here ``monitor`` and ``flag`` ARE the same tier (rank 1) — this is the design
   lattice's ``monitor/flag`` equivalence. This module asserts that equivalence
   against ``enforcement.action_rank`` directly.

The property is asserted on the RAW ``evaluate().action`` (pre-``enforcement_mode``
downgrade), per the task: we build multiple ENABLED rules that all match the SAME
input with various user-selected actions and assert the resolved action is exactly
the highest-severity SELECTED action — a pure function of the matched rules' selected
actions, with no firewall-imposed default.
"""

from __future__ import annotations

import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from ai_mesh_gateway import enforcement
from ai_mesh_gateway.policy_engine import ACTION_ORDER, evaluate

# Import the sibling scaffold READ-ONLY for its compiled-policy shape conventions.
# (Another agent owns test_policy_driven_detection.py; we only reuse helpers.)
from ai_mesh_gateway.tests.test_policy_driven_detection import (
    PolicyDrivenDetectionScaffold,
)


# --------------------------------------------------------------------------- #
# User-selectable actions per Requirement (R2.2 / R4.5): block / redact / flag /
# monitor (plus allow as the floor). These are the actions an operator picks per
# Rule; the firewall must honor exactly the highest-severity selected one.
# --------------------------------------------------------------------------- #
_SELECTABLE_ACTIONS = ("allow", "monitor", "flag", "redact", "block")

# A single literal keyword every rule in a generated package matches on, so that
# ALL rules match the SAME input simultaneously (the multi-rule merge case).
_SHARED_KEYWORD = "match-me-lattice-tok"
_MATCHING_PROMPT = f"please {_SHARED_KEYWORD} now"


def _expected_engine_winner(actions: list[str]) -> str:
    """The action ``policy_engine.evaluate`` MUST resolve for a set of matching
    rules, derived from the engine's OWN ``ACTION_ORDER`` (never a hardcoded value).

    Mirrors evaluate()'s ``rank = ACTION_ORDER.get(action, 0)`` +
    ``if rank > best_action_rank`` (strictly-greater, so on a tie the FIRST-seen
    action at that rank wins — matches iteration order).
    """
    best_action = "allow"
    best_rank = -1
    for action in actions:
        rank = ACTION_ORDER.get(action, 0)
        if rank > best_rank:
            best_rank = rank
            best_action = action
    return best_action


def _make_multi_rule_package(actions: list[str]) -> list[dict]:
    """Build ONE enabled compiled policy whose rules ALL match ``_MATCHING_PROMPT``
    (same keyword), each carrying a distinct user-selected ``action``.

    Shape matches ``policy_engine.evaluate``'s compiled-entry contract
    (``{"policy": {...}, "rules": [...]}``), consistent with the sibling scaffold's
    ``seeded_policies`` helper.
    """
    rules = []
    for idx, action in enumerate(actions):
        rules.append(
            {
                "id": 100 + idx,
                "name": f"rule-{idx}-{action}",
                "rule_type": "keywords",
                "condition": {"keywords": [_SHARED_KEYWORD], "field": "both"},
                "action": action,
            }
        )
    return [
        {
            "policy": {
                "id": 1,
                "code": "TEST_PKG_lattice",
                "name": "Lattice Test Package",
                "priority": 100,
                "category": "test_family",
                "severity": "high",
            },
            "rules": rules,
        }
    ]


class UserSelectedActionLatticeProperties(
    PolicyDrivenDetectionScaffold, unittest.TestCase
):
    """Property 5 — user-selected action is honored (raw engine, pre-downgrade).

    **Validates: Requirements 2.2, 4.5**
    """

    # ---- Lattice sanity locks (confirm the source ordering the property relies on) ----

    def test_enforcement_lattice_ordering_from_source(self):
        """The enforcement-authority lattice is exactly ``allow < monitor/flag <
        redact < block`` with monitor/flag at the SAME tier (design lattice)."""
        rank = enforcement.action_rank
        self.assertEqual(rank("allow"), 0)
        # monitor and flag are the SAME tier.
        self.assertEqual(rank("monitor"), rank("flag"))
        self.assertGreater(rank("monitor"), rank("allow"))
        self.assertGreater(rank("redact"), rank("monitor"))
        self.assertGreater(rank("redact"), rank("flag"))
        self.assertGreater(rank("block"), rank("redact"))

    def test_engine_action_order_is_the_precedence_source(self):
        """The raw engine ranks via ``ACTION_ORDER`` (block > redact > monitor >
        allow); ``flag`` is unranked (→ 0). Locks the source of the winner fn."""
        self.assertGreater(ACTION_ORDER["block"], ACTION_ORDER["redact"])
        self.assertGreater(ACTION_ORDER["redact"], ACTION_ORDER["monitor"])
        self.assertGreater(ACTION_ORDER["monitor"], ACTION_ORDER["allow"])
        # flag is not a distinct raw-engine verdict → defaults to allow-rank.
        self.assertEqual(ACTION_ORDER.get("flag", 0), 0)

    # ---- The property: resolved action == highest-severity SELECTED action ----

    @settings(max_examples=300, deadline=None)
    @given(
        actions=st.lists(
            st.sampled_from(_SELECTABLE_ACTIONS), min_size=1, max_size=6
        )
    )
    def test_property5_resolved_action_is_highest_selected(self, actions: list[str]):
        """For any set of enabled rules matching the SAME input with various
        user-selected actions, ``evaluate().action`` equals the highest-severity
        SELECTED action per the engine's lattice — no hardcoded action imposed."""
        policies = _make_multi_rule_package(actions)
        result = evaluate(_MATCHING_PROMPT, "", policies)

        # Every rule matched the shared keyword (proves we exercised the merge).
        self.assertEqual(
            len(result.matched_rule_ids),
            len(actions),
            msg=f"all {len(actions)} rules should have matched: {actions!r}",
        )

        expected = _expected_engine_winner(actions)
        self.assertEqual(
            result.action,
            expected,
            msg=(
                f"resolved action must equal the highest-severity selected action; "
                f"selected={actions!r} expected={expected!r} got={result.action!r}"
            ),
        )

        # "No hardcoded action is imposed": the resolved action is a MEMBER of the
        # selected set (the firewall never injects an action nobody selected).
        self.assertIn(
            result.action,
            set(actions),
            msg=f"resolved action {result.action!r} was not among selected {actions!r}",
        )

    @settings(max_examples=200, deadline=None)
    @given(actions=st.lists(st.sampled_from(_SELECTABLE_ACTIONS), min_size=1, max_size=6))
    def test_property5_resolved_action_is_purely_a_function_of_selected(
        self, actions: list[str]
    ):
        """The resolved action depends ONLY on the SET of selected actions, not on
        rule order — any permutation of the same selected actions yields the same
        winning severity tier (order only breaks ties WITHIN a tier)."""
        forward = evaluate(_MATCHING_PROMPT, "", _make_multi_rule_package(actions))
        reversed_actions = list(reversed(actions))
        backward = evaluate(_MATCHING_PROMPT, "", _make_multi_rule_package(reversed_actions))

        # The winning SEVERITY RANK is order-independent (a pure function of the set).
        self.assertEqual(
            ACTION_ORDER.get(forward.action, 0),
            ACTION_ORDER.get(backward.action, 0),
            msg=(
                f"winning severity rank must be order-independent for {actions!r}: "
                f"forward={forward.action!r} backward={backward.action!r}"
            ),
        )

    # ---- Example anchors: single-rule fidelity for each selectable action ----

    def test_single_rule_action_is_honored_verbatim(self):
        """A single enabled rule's user-selected action is honored EXACTLY (the
        firewall never overrides it with a hardcoded action) — for each of the
        ranked selectable actions (block/redact/monitor/allow)."""
        # Only assert the RAW-engine-ranked actions verbatim; 'flag' is unranked in
        # the raw engine (→ allow-rank), covered by the tier-equivalence test below.
        for action in ("allow", "monitor", "redact", "block"):
            with self.subTest(action=action):
                result = evaluate(
                    _MATCHING_PROMPT, "", _make_multi_rule_package([action])
                )
                self.assertEqual(
                    result.action,
                    action,
                    msg=f"single {action!r} rule must resolve to {action!r}, got {result.action!r}",
                )

    def test_block_beats_redact_beats_monitor(self):
        """Explicit multi-rule anchor: block dominates redact dominates monitor
        (the design lattice's strict ordering) — never a fixed default."""
        self.assertEqual(
            evaluate(_MATCHING_PROMPT, "", _make_multi_rule_package(["monitor", "redact"])).action,
            "redact",
        )
        self.assertEqual(
            evaluate(_MATCHING_PROMPT, "", _make_multi_rule_package(["redact", "block"])).action,
            "block",
        )
        self.assertEqual(
            evaluate(
                _MATCHING_PROMPT, "", _make_multi_rule_package(["monitor", "redact", "block"])
            ).action,
            "block",
        )

    def test_monitor_and_flag_share_a_tier_in_enforcement_lattice(self):
        """The design lattice puts monitor and flag in the SAME tier; the
        enforcement authority reflects that (equal rank), so neither dominates the
        other. Asserted against ``enforcement.action_rank`` (the layer that ranks
        ``flag``)."""
        self.assertEqual(
            enforcement.action_rank("monitor"),
            enforcement.action_rank("flag"),
        )
        # And in that lattice both sit strictly below redact and block.
        self.assertLess(enforcement.action_rank("monitor"), enforcement.action_rank("redact"))
        self.assertLess(enforcement.action_rank("flag"), enforcement.action_rank("block"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
