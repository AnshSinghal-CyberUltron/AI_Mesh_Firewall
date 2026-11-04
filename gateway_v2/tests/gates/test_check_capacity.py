"""LGW03-4: max_connections=64 outside runtime/resources.py fails the AST gate."""

from __future__ import annotations

from pathlib import Path

from lint.check_capacity_literals import scan_tree

_PKG = Path(__file__).resolve().parents[2] / "gateway_v2"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_lgw03_4_max_connections_outside_runtime_fails(tmp_path: Path) -> None:
    _write(tmp_path, "admit/poison.py", "def connect() -> None:\n    pool(max_connections=64)\n")
    hits = scan_tree(tmp_path)
    assert hits, "expected AST gate to fire"
    assert any("admit/poison.py" in h.path or h.path.endswith("poison.py") for h in hits)
    assert any("max_connections=64" in h.symbol for h in hits)
    assert all(h.line >= 1 for h in hits)


def test_semaphore_literal_fails(tmp_path: Path) -> None:
    _write(tmp_path, "plan/sem.py", "from threading import Semaphore\nS = Semaphore(32)\n")
    hits = scan_tree(tmp_path)
    assert any("Semaphore(32)" in h.symbol for h in hits)


def test_resources_module_is_allowed() -> None:
    hits = [h for h in scan_tree(_PKG) if h.path.endswith("runtime/resources.py")]
    assert hits == []


def test_shipped_tree_has_no_capacity_literals() -> None:
    assert scan_tree(_PKG) == []


# --- GW32: allowlist of runtime/resources_mcp.py (negative controls included) -------------------
_LITERAL = "def f() -> None:\n    pool(max_connections=64)\n"


def test_gw32_literal_allowed_in_resources_mcp(tmp_path: Path) -> None:
    _write(tmp_path, "runtime/resources_mcp.py", _LITERAL)
    assert scan_tree(tmp_path) == []


def test_gw32_literal_still_fails_elsewhere(tmp_path: Path) -> None:
    for rel in ("runtime/other.py", "admit/mcp_cap.py", "runtime/resources_x.py"):
        _write(tmp_path, rel, _LITERAL)
    hits = scan_tree(tmp_path)
    assert {Path(h.path).name for h in hits} == {"other.py", "mcp_cap.py", "resources_x.py"}


def test_gw32_new_mcp_modules_have_no_capacity_literals() -> None:
    new = [
        p
        for pattern in (
            "admit/mcp_*.py", "edge/mcp_*.py", "edge/oauth_routes.py", "dispatch/mcp_*.py",
        )
        for p in _PKG.glob(pattern)
    ]
    assert not [h for h in scan_tree(_PKG) if Path(h.path) in new]


def test_gw33_allowlist_contains_resources_output() -> None:
    from lint.check_capacity_literals import ALLOWED_RELS

    assert {"runtime/resources.py", "runtime/resources_output.py"} <= ALLOWED_RELS


def test_gw33_resources_output_is_allowed(tmp_path: Path) -> None:
    _write(tmp_path, "runtime/resources_output.py", "pool(max_connections=64)\n")
    assert scan_tree(tmp_path) == []


def test_gw33_other_runtime_module_still_fails(tmp_path: Path) -> None:
    _write(tmp_path, "runtime/resources_other.py", "pool(max_connections=64)\n")
    _write(tmp_path, "detect/output/ruleset.py", "pool(max_connections=64)\n")
    hits = scan_tree(tmp_path)
    assert len(hits) == 2
