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
