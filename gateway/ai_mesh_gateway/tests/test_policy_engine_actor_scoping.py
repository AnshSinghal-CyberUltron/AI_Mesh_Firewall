"""M-04 actor-scoping tests for the gateway policy engine.

Contract under test (policy_engine._policy_applies_to_actor):
  * No allowlists on the policy -> applies to everyone.
  * A dimension constrains ONLY on a positive mismatch — when the actor's
    identity for that dimension is KNOWN and not allowlisted.
  * Unknown identity (no actor, missing key, None, empty string/list) leaves
    the policy applied (fail-closed: omission must not bypass a scoped policy).
  * Old bundles without allowed_* keys behave exactly as before M-04.
"""

from policy_engine import _policy_applies_to_actor, evaluate, evaluate_mcp_policies


def _policy(**allow):
    p = {"id": 1, "code": "P1", "name": "p1", "priority": 10, "severity": "high"}
    p.update(allow)
    return p


def _compiled(policy, action="block"):
    return [{
        "policy": policy,
        "rules": [{
            "id": 11,
            "name": "kw",
            "rule_type": "keywords",
            "condition": {"keywords": ["forbidden"]},
            "action": action,
        }],
    }]


# ── _policy_applies_to_actor unit semantics ──────────────────────────────

def test_no_allowlists_applies_to_everyone():
    assert _policy_applies_to_actor(_policy(), None)
    assert _policy_applies_to_actor(_policy(), {"user_id": 7})
    # Old bundle: keys entirely absent
    assert _policy_applies_to_actor({"id": 2}, {"user_id": 7})


def test_user_allowlist_positive_match_and_mismatch():
    p = _policy(allowed_user_ids=[5])
    assert _policy_applies_to_actor(p, {"user_id": 5})
    # str/int coercion both directions
    assert _policy_applies_to_actor(p, {"user_id": "5"})
    assert not _policy_applies_to_actor(p, {"user_id": 7})


def test_unknown_user_fails_closed_policy_applies():
    p = _policy(allowed_user_ids=[5])
    assert _policy_applies_to_actor(p, None)
    assert _policy_applies_to_actor(p, {})
    assert _policy_applies_to_actor(p, {"user_id": None})


def test_agent_allowlist():
    p = _policy(allowed_agent_ids=["zs_abc"])
    assert _policy_applies_to_actor(p, {"agent_id": "zs_abc"})
    assert not _policy_applies_to_actor(p, {"agent_id": "zs_other"})
    # Unknown agent (None / empty prefix) -> applies
    assert _policy_applies_to_actor(p, {"agent_id": None})
    assert _policy_applies_to_actor(p, {"agent_id": ""})
    assert _policy_applies_to_actor(p, {})


def test_roles_allowlist():
    p = _policy(allowed_roles=["analyst"])
    assert _policy_applies_to_actor(p, {"roles": ["analyst", "viewer"]})
    assert not _policy_applies_to_actor(p, {"roles": ["viewer"]})
    # Unknown roles -> applies
    assert _policy_applies_to_actor(p, {"roles": []})
    assert _policy_applies_to_actor(p, {})
    # String role accepted
    assert _policy_applies_to_actor(p, {"roles": "analyst"})


def test_multi_dimension_all_known_must_match():
    p = _policy(allowed_user_ids=[5], allowed_agent_ids=["zs_abc"])
    assert _policy_applies_to_actor(p, {"user_id": 5, "agent_id": "zs_abc"})
    assert not _policy_applies_to_actor(p, {"user_id": 5, "agent_id": "zs_x"})
    assert not _policy_applies_to_actor(p, {"user_id": 9, "agent_id": "zs_abc"})
    # One dimension known+match, other unknown -> applies
    assert _policy_applies_to_actor(p, {"user_id": 5})


# ── evaluate() end-to-end ────────────────────────────────────────────────

def test_evaluate_scoped_policy_enforces_for_listed_actor():
    compiled = _compiled(_policy(allowed_user_ids=[5]))
    result = evaluate(
        prompt="this is forbidden text",
        response_text="",
        compiled_policies=compiled,
        actor={"user_id": 5, "agent_id": "zs_abc", "roles": []},
    )
    assert result.action == "block"


def test_evaluate_scoped_policy_skips_for_other_actor():
    compiled = _compiled(_policy(allowed_user_ids=[5]))
    result = evaluate(
        prompt="this is forbidden text",
        response_text="",
        compiled_policies=compiled,
        actor={"user_id": 7, "agent_id": "zs_abc", "roles": []},
    )
    assert result.action == "allow"
    assert not result.matched_rule_ids


def test_evaluate_no_actor_still_enforces_scoped_policy():
    """The M-04 regression: a missing actor must NOT disable scoped policies."""
    compiled = _compiled(_policy(allowed_user_ids=[5]))
    result = evaluate(
        prompt="this is forbidden text",
        response_text="",
        compiled_policies=compiled,
    )
    assert result.action == "block"


def test_evaluate_old_bundle_without_actor_keys_unchanged():
    compiled = _compiled({"id": 3, "code": "OLD", "name": "old", "priority": 1, "severity": "low"})
    result = evaluate(
        prompt="this is forbidden text",
        response_text="",
        compiled_policies=compiled,
        actor={"user_id": 999, "agent_id": "zs_zzz", "roles": ["nobody"]},
    )
    assert result.action == "block"


def test_evaluate_mcp_policies_actor_scoping():
    compiled = _compiled(_policy(allowed_agent_ids=["zs_abc"]))
    ctx = {"prompt": "forbidden tool arg", "response": "", "input_args": None, "output_data": None}
    blocked = evaluate_mcp_policies(compiled, ctx, actor={"agent_id": "zs_abc"})
    assert blocked.action == "block"
    skipped = evaluate_mcp_policies(compiled, ctx, actor={"agent_id": "zs_other"})
    assert skipped.action == "allow"
    # No actor and no actor-ish context keys -> fail-closed, policy applies
    unknown = evaluate_mcp_policies(compiled, ctx)
    assert unknown.action == "block"
