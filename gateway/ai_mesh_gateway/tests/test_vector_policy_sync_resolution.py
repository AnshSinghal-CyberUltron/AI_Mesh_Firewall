"""Executed coverage for VectorPolicySync.get_policy — the core vector-collection
policy resolution — which had ZERO direct tests (the test_policy_sync_*.py files
cover a different module). Security properties under POLICY VALIDATION +
VECTOR SECURITY:

* case-insensitive collection matching — a caller MUST NOT evade a `deny` /
  `block_sensitive` policy by requesting `Docs` against a policy stored for
  `docs` (the docstring's own "PII bypass" warning);
* org-first precedence — the collision-free `{organization_id}::{collection}`
  key wins over the legacy project_id key;
* tenant isolation in _lookup — a policy is only returned under the matching
  `{tenant}::` prefix (no cross-tenant policy match);
* fail-closed miss — an unknown collection/org returns None (the handler then
  denies named collections).
"""
from vector_policy_sync import VectorPolicySync


def _sync_with(policies: dict) -> VectorPolicySync:
    s = VectorPolicySync(redis_url="redis://localhost:6379/0")
    s._cache = {"policies": policies, "policy_count": len(policies)}
    return s


def test_case_insensitive_collection_blocks_deny_evasion():
    deny = {"default_action": "deny", "allowed_operations": []}
    s = _sync_with({"42::docs": deny})
    assert s.get_policy("proj", "docs", organization_id=42) == deny
    # Case variants MUST resolve to the SAME policy — no deny/PII-bypass via case.
    assert s.get_policy("proj", "Docs", organization_id=42) == deny
    assert s.get_policy("proj", "DOCS", organization_id=42) == deny
    # Whitespace is trimmed + case-folded.
    assert s.get_policy("proj", "  DoCs  ", organization_id=42) == deny


def test_org_id_precedence_over_project_id():
    org_pol = {"default_action": "deny", "_owner": "org"}
    proj_pol = {"default_action": "monitor", "_owner": "proj"}
    s = _sync_with({"42::docs": org_pol, "projX::docs": proj_pol})
    # org_id lookup is primary (collision-free) -> org policy wins.
    assert s.get_policy("projX", "docs", organization_id=42) == org_pol


def test_project_id_fallback_when_no_org_match():
    proj_pol = {"default_action": "monitor"}
    s = _sync_with({"projX::docs": proj_pol})
    # No org-keyed policy -> legacy project_id fallback resolves it.
    assert s.get_policy("projX", "docs", organization_id=42) == proj_pol


def test_lookup_is_tenant_isolated():
    org42 = {"default_action": "deny", "_owner": "42"}
    s = _sync_with({"42::docs": org42})
    # A DIFFERENT org querying the same collection name must NOT get org 42's policy.
    assert s.get_policy("proj", "docs", organization_id=99) is None
    # Prefix-collision guard: org 420 must not match org 42's "42::" prefix.
    s2 = _sync_with({"42::docs": org42})
    assert s2.get_policy("proj", "docs", organization_id=420) is None


def test_missing_collection_returns_none_fail_closed():
    s = _sync_with({"42::docs": {"default_action": "deny"}})
    assert s.get_policy("proj", "secret", organization_id=42) is None
