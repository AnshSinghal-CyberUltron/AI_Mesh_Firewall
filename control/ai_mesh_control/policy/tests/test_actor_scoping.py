"""M-04 actor-scoping tests for the control-plane policy engine.

Contract under test (policy.engine._policy_applies_to_actor):
  * No allowlists on the policy -> applies to everyone.
  * A dimension constrains ONLY on a positive mismatch — when the actor's
    identity for that dimension is KNOWN and not allowlisted.
  * Unknown identity (missing context key, None, empty value) leaves the
    policy applied (fail-closed: identity arrives via client-supplied
    headers, so omission must never bypass a scoped policy).
"""

from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from policy.engine import _policy_applies_to_actor, evaluate
from policy.models import Policy, Rule


def _stub_policy(**allow):
    base = {"allowed_user_ids": [], "allowed_agent_ids": [], "allowed_roles": []}
    base.update(allow)
    return SimpleNamespace(**base)


class ActorScopingUnitTests(SimpleTestCase):
    def test_no_allowlists_applies_to_everyone(self):
        self.assertTrue(_policy_applies_to_actor(_stub_policy(), {}))
        self.assertTrue(_policy_applies_to_actor(_stub_policy(), {"user_id": 7}))
        # Older model rows without the attributes at all
        self.assertTrue(_policy_applies_to_actor(SimpleNamespace(), {"user_id": 7}))

    def test_user_allowlist_match_and_mismatch(self):
        p = _stub_policy(allowed_user_ids=[5])
        self.assertTrue(_policy_applies_to_actor(p, {"user_id": 5}))
        self.assertTrue(_policy_applies_to_actor(p, {"user_id": "5"}))
        self.assertFalse(_policy_applies_to_actor(p, {"user_id": 7}))

    def test_unknown_user_fails_closed(self):
        p = _stub_policy(allowed_user_ids=[5])
        self.assertTrue(_policy_applies_to_actor(p, {}))
        self.assertTrue(_policy_applies_to_actor(p, {"user_id": None}))

    def test_agent_allowlist(self):
        p = _stub_policy(allowed_agent_ids=["zs_abc"])
        self.assertTrue(_policy_applies_to_actor(p, {"agent_id": "zs_abc"}))
        self.assertFalse(_policy_applies_to_actor(p, {"agent_id": "zs_other"}))
        self.assertTrue(_policy_applies_to_actor(p, {"agent_id": ""}))
        self.assertTrue(_policy_applies_to_actor(p, {}))

    def test_roles_allowlist(self):
        p = _stub_policy(allowed_roles=["analyst"])
        self.assertTrue(_policy_applies_to_actor(p, {"roles": ["analyst", "viewer"]}))
        self.assertFalse(_policy_applies_to_actor(p, {"roles": ["viewer"]}))
        self.assertTrue(_policy_applies_to_actor(p, {"roles": []}))
        self.assertTrue(_policy_applies_to_actor(p, {}))
        self.assertTrue(_policy_applies_to_actor(p, {"roles": "analyst"}))

    def test_multi_dimension_known_must_all_match(self):
        p = _stub_policy(allowed_user_ids=[5], allowed_agent_ids=["zs_abc"])
        self.assertTrue(_policy_applies_to_actor(p, {"user_id": 5, "agent_id": "zs_abc"}))
        self.assertFalse(_policy_applies_to_actor(p, {"user_id": 5, "agent_id": "zs_x"}))
        self.assertFalse(_policy_applies_to_actor(p, {"user_id": 9, "agent_id": "zs_abc"}))
        self.assertTrue(_policy_applies_to_actor(p, {"user_id": 5}))


class ActorScopingEvaluateTests(TestCase):
    def _make_policy(self, **allow):
        policy = Policy.objects.create(
            name="Scoped block",
            code="ACTOR_SCOPED",
            policy_domain="pipeline",
            category="test",
            severity="HIGH",
            enabled=True,
            **allow,
        )
        Rule.objects.create(
            policy=policy,
            name="block marker",
            rule_type="keywords",
            condition={"keywords": ["ACTOR_MARKER"], "field": "both"},
            action="block",
            priority=10,
            enabled=True,
        )
        return Policy.objects.filter(enabled=True).prefetch_related("rules")

    def test_scoped_policy_enforces_for_listed_user(self):
        qs = self._make_policy(allowed_user_ids=[5])
        result = evaluate({"prompt": "ACTOR_MARKER", "user_id": 5}, policies_qs=qs, domain="pipeline")
        self.assertEqual(result.action, "block")

    def test_scoped_policy_skips_other_user(self):
        qs = self._make_policy(allowed_user_ids=[5])
        result = evaluate({"prompt": "ACTOR_MARKER", "user_id": 7}, policies_qs=qs, domain="pipeline")
        self.assertEqual(result.action, "allow")
        self.assertEqual(result.matched_policy_ids, [])

    def test_scoped_policy_enforces_when_actor_unknown(self):
        """The M-04 regression: missing identity must NOT disable the policy."""
        qs = self._make_policy(allowed_user_ids=[5])
        result = evaluate({"prompt": "ACTOR_MARKER"}, policies_qs=qs, domain="pipeline")
        self.assertEqual(result.action, "block")

    def test_agent_scoped_policy_via_context(self):
        qs = self._make_policy(allowed_agent_ids=["zs_abc"])
        hit = evaluate(
            {"prompt": "ACTOR_MARKER", "agent_id": "zs_abc"}, policies_qs=qs, domain="pipeline"
        )
        self.assertEqual(hit.action, "block")
        miss = evaluate(
            {"prompt": "ACTOR_MARKER", "agent_id": "zs_other"}, policies_qs=qs, domain="pipeline"
        )
        self.assertEqual(miss.action, "allow")

    def test_role_scoped_policy_via_context(self):
        qs = self._make_policy(allowed_roles=["analyst"])
        hit = evaluate(
            {"prompt": "ACTOR_MARKER", "roles": ["analyst"]}, policies_qs=qs, domain="pipeline"
        )
        self.assertEqual(hit.action, "block")
        miss = evaluate(
            {"prompt": "ACTOR_MARKER", "roles": ["viewer"]}, policies_qs=qs, domain="pipeline"
        )
        self.assertEqual(miss.action, "allow")
        unknown = evaluate({"prompt": "ACTOR_MARKER"}, policies_qs=qs, domain="pipeline")
        self.assertEqual(unknown.action, "block")
