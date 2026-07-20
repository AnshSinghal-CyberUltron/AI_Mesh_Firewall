"""Unit test for the cross-tenant audit-log leakage oracle (backstop CHG-0009).

The scale harness's ``foreign_org_events`` metric was a fabricated no-op
(``for fs in []`` -> ``any(...)`` always False -> structurally 0). This proves
the corrected ``count_foreign_events`` actually detects cross-tenant audit rows,
and documents that the old inline predicate missed a real leak.

Run:  gateway/.venv/bin/python scripts/test_mcp_scale_oracle.py
  or  gateway/.venv/bin/python -m pytest scripts/test_mcp_scale_oracle.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from mcp_scale_matrix_live import count_foreign_events  # noqa: E402


def test_detects_foreign_by_org_slug():
    # Auditing org-a; a row owned by org-b appearing in org-a's scoped audit is a leak.
    rows = [{"org_slug": "org-a", "path": "/mcp/org-a/srv"}, {"org_slug": "org-b"}]
    assert count_foreign_events(rows, {"org-b"}) == 1


def test_detects_foreign_by_mcp_path_content():
    rows = [{"org_slug": "org-a", "detail": "called /mcp/org-b/tool"}]
    assert count_foreign_events(rows, {"org-b"}) == 1


def test_clean_rows_zero():
    rows = [{"org_slug": "org-a", "path": "/mcp/org-a/srv"}, {"org_slug": "org-a"}]
    assert count_foreign_events(rows, {"org-b", "org-c"}) == 0


def test_no_other_slugs_zero():
    assert count_foreign_events([{"org_slug": "org-b"}], set()) == 0
    assert count_foreign_events([{"org_slug": "org-b"}], None) == 0


def test_old_dead_predicate_missed_the_leak():
    """Documents the fabricated metric: the OLD predicate returns 0 even for a
    genuinely foreign row; the corrected oracle catches it."""
    row = {"org_slug": "org-b", "detail": "/mcp/org-b/tool"}
    other = {"org-b"}
    old = sum(1 for r in [row]
              if any(f"/mcp/{fs}" in json.dumps(r) or r.get("org_slug") in other for fs in []))
    assert old == 0                              # BUG: never trips
    assert count_foreign_events([row], other) == 1   # FIX: detects the leak


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for _fn in _fns:
        _fn()
        print("ok", _fn.__name__)
    print(f"ALL {len(_fns)} ORACLE TESTS PASSED")
