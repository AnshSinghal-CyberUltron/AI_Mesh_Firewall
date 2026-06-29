"""Version-gate robustness for policy propagation.

A redis version-counter RESET (reseed / failover / flush) restarts the per-org
version at 1, which would make a genuinely-newer compiled bundle look "stale"
by version alone and permanently wedge propagation until a gateway restart.
PolicySync._is_newer_notification must recover via the monotonic compiled_at
wall-clock while still rejecting truly out-of-order notifications.
"""

from ai_mesh_gateway.policy_sync import PolicySync


def _sync_with(org, version, compiled_at):
    s = PolicySync("redis://unused")
    s._org_versions[org] = version
    s._org_caches[org] = {
        "compiled_at": compiled_at,
        "version": version,
        "policy_count": 0,
        "policies": {},
    }
    return s


def test_higher_version_is_newer():
    s = _sync_with("acme", version=5, compiled_at=1000.0)
    assert s._is_newer_notification("acme", 6, 1001.0) is True


def test_same_version_same_time_is_stale():
    s = _sync_with("acme", version=5, compiled_at=1000.0)
    assert s._is_newer_notification("acme", 5, 1000.0) is False


def test_version_reset_with_newer_compiled_at_is_accepted():
    # version counter reset (154 -> 1) but the compile is genuinely newer in time
    s = _sync_with("acme", version=154, compiled_at=1000.0)
    assert s._is_newer_notification("acme", 1, 2000.0) is True


def test_lower_version_and_older_time_still_stale():
    # genuinely out-of-order / stale notification must still be skipped
    s = _sync_with("acme", version=154, compiled_at=2000.0)
    assert s._is_newer_notification("acme", 1, 1000.0) is False


def test_reset_without_cached_compiled_at_is_not_accepted():
    # cannot prove "newer" without a comparable timestamp -> stay safe (skip)
    s = PolicySync("redis://unused")
    s._org_versions["acme"] = 154
    s._org_caches["acme"] = {"version": 154}  # no compiled_at
    assert s._is_newer_notification("acme", 1, 2000.0) is False


def test_reset_without_incoming_compiled_at_is_not_accepted():
    s = _sync_with("acme", version=154, compiled_at=1000.0)
    assert s._is_newer_notification("acme", 1, None) is False


def test_unknown_org_first_bundle_is_newer():
    s = PolicySync("redis://unused")
    assert s._is_newer_notification("brand-new-org", 1, 1234.0) is True
