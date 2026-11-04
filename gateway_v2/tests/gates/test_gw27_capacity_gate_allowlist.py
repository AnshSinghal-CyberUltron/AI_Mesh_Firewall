"""GW27 D-13: the capacity gate allows exactly the declared modules; the allowlist cannot widen
silently (GW32 added runtime/resources_mcp.py alongside GW27's runtime/resources_org.py; GW33 added
runtime/resources_output.py)."""
from __future__ import annotations

from pathlib import Path

from lint.check_capacity_literals import ALLOWED_RELS, scan_tree

# Feature: gw27-firewall-config-fields, Property 13: capacity literals only in two allowlisted files
# Validates: Requirements NFR 2

_LITERAL = "def f() -> None:\n    pool(max_connections=64)\n"


def _write(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_LITERAL, encoding="utf-8")


def test_allowlist_is_exactly_the_declared_modules() -> None:
    assert ALLOWED_RELS == frozenset(
        {
            "runtime/resources.py",
            "runtime/resources_org.py",
            "runtime/resources_mcp.py",
            "runtime/resources_output.py",
        },
    )


def test_literal_in_resources_org_passes(tmp_path: Path) -> None:
    _write(tmp_path, "runtime/resources_org.py")
    assert scan_tree(tmp_path) == []


def test_literal_in_resources_passes(tmp_path: Path) -> None:
    _write(tmp_path, "runtime/resources.py")
    assert scan_tree(tmp_path) == []


def test_literal_elsewhere_fails(tmp_path: Path) -> None:
    for rel in ("runtime/other.py", "admit/org_limits.py", "runtime/resources_x.py"):
        _write(tmp_path, rel)
    flagged = {Path(h.path).name for h in scan_tree(tmp_path)}
    assert flagged == {"other.py", "org_limits.py", "resources_x.py"}
