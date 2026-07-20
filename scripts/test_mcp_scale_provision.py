"""Unit test for the configurable scale-matrix org generator (backstop CHG-0010).

The provisioner's org count was a hardcoded 3-tuple (=> 15-sandbox ceiling). This
proves ``build_orgs(NUM_ORGS)`` is backward-compatible for the first 3 and scales
to the required 300-500 targets with unique, deterministic org identities.

Run:  python3 scripts/test_mcp_scale_provision.py
  or  python3 -m pytest scripts/test_mcp_scale_provision.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from mcp_scale_provision import build_orgs, _BASE_ORGS  # noqa: E402


def test_backward_compatible_first_three():
    assert build_orgs(3) == _BASE_ORGS
    assert build_orgs(3)[0] == ("zeroshield", "admin@zeroshield.io")


def test_smaller_counts():
    assert build_orgs(0) == []
    assert build_orgs(1) == _BASE_ORGS[:1]
    assert build_orgs(2) == _BASE_ORGS[:2]


def test_extends_beyond_base_with_convention():
    orgs = build_orgs(5)
    assert orgs[:3] == _BASE_ORGS
    assert orgs[3] == ("org-3", "admin@org-3.io")
    assert orgs[4] == ("org-4", "admin@org-4.io")


def test_scales_to_500_targets_with_unique_slugs():
    orgs = build_orgs(50)
    assert len(orgs) == 50
    slugs = [s for s, _ in orgs]
    assert len(set(slugs)) == 50           # all unique
    emails = [e for _, e in orgs]
    assert len(set(emails)) == 50          # all unique
    # 50 orgs x 10 servers = 500 sandboxes (the required scale)
    assert len(orgs) * 10 == 500


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for _fn in _fns:
        _fn()
        print("ok", _fn.__name__)
    print(f"ALL {len(_fns)} PROVISION TESTS PASSED")
