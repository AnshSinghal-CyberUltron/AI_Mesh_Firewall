"""Per-server enablement override (server-centric "Manage Tier-1", 2026-07-24).

An operator can turn an individual policy/rule ON or OFF for a SPECIFIC server from that server's
"Manage Tier-1" view. The compiler stamps a ``server_states`` map ({server_slug: enabled}) onto each
policy and rule; ``PolicySync.get_policies_for_server`` consults it. This locks the gateway
resolution — including the ADDITIVE guarantee: an empty/absent map is byte-identical to the
pre-override behavior (invariant E: no cross-server bleed, org-wide applies to all, bound applies to
its server).
"""
import ai_mesh_gateway.policy_sync as ps


class _FixedSync(ps.PolicySync):
    def __init__(self, bundle):
        self._b = bundle

    def get_policies(self, org):
        return self._b


def _entry(code, server_slug, server_states=None, rules=None):
    return {"policy": {"code": code, "policy_domain": "mcp", "mcp_server_slug": server_slug,
                       "server_states": server_states or {}}, "rules": rules or []}


def _codes(sync, srv):
    return sorted(e["policy"]["code"] for e in sync.get_policies_for_server("o", srv))


def _rules(sync, srv, code):
    for e in sync.get_policies_for_server("o", srv):
        if e["policy"]["code"] == code:
            return sorted(r["name"] for r in e["rules"])
    return None


def test_no_override_is_byte_identical_default_behavior():
    # With no server_states, resolution is exactly the legacy behavior: org-wide → all servers,
    # bound → only its server (invariant E holds unchanged).
    s = _FixedSync([_entry("ORGWIDE", None), _entry("BOUND_A", "serverA")])
    assert _codes(s, "serverA") == ["BOUND_A", "ORGWIDE"]
    assert _codes(s, "serverZ") == ["ORGWIDE"]  # org-wide applies; bound-to-A does NOT bleed


def test_override_disables_orgwide_policy_for_one_server_only():
    s = _FixedSync([_entry("ORGWIDE", None, {"serverB": False})])
    assert _codes(s, "serverA") == ["ORGWIDE"]   # default applies
    assert _codes(s, "serverB") == []            # explicitly disabled for B only
    assert _codes(s, "serverC") == ["ORGWIDE"]   # unaffected


def test_override_enables_a_bound_policy_for_another_server():
    s = _FixedSync([_entry("BOUND_A", "serverA", {"serverB": True})])
    assert _codes(s, "serverA") == ["BOUND_A"]   # default (its server)
    assert _codes(s, "serverB") == ["BOUND_A"]   # override enables it for B
    assert _codes(s, "serverZ") == []            # no bleed to a server with no override


def test_per_server_rule_override_drops_only_that_servers_disabled_rule():
    s = _FixedSync([_entry("P", None, rules=[
        {"name": "r1", "server_states": {"serverA": False}},
        {"name": "r2", "server_states": {}},
    ])])
    assert _rules(s, "serverA", "P") == ["r2"]        # r1 disabled on A
    assert _rules(s, "serverB", "P") == ["r1", "r2"]  # both on B


def test_override_never_bleeds_across_servers():
    # A dense mix: overrides for one server must not affect another.
    s = _FixedSync([
        _entry("ORGWIDE", None, {"serverA": False}),
        _entry("BOUND_B", "serverB", {"serverA": True}),
    ])
    assert _codes(s, "serverA") == ["BOUND_B"]           # ORGWIDE off for A, BOUND_B on for A
    assert _codes(s, "serverB") == ["BOUND_B", "ORGWIDE"]  # B untouched by A's overrides
