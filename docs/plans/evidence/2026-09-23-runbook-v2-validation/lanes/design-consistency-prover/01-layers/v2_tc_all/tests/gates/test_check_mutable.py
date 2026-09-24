"""Module-level list/dict/set literals are forbidden (TYPE_CHECKING allowed)."""

from __future__ import annotations

from pathlib import Path

from lint.check_no_module_mutable import scan_tree


def test_module_dict_rejected(tmp_path: Path) -> None:
    src = tmp_path / "state.py"
    src.write_text("CACHE = {}\n", encoding="utf-8")
    hits = scan_tree(tmp_path)
    assert hits
    assert any(h.path.endswith("state.py") for h in hits)


def test_module_list_and_set_rejected(tmp_path: Path) -> None:
    src = tmp_path / "more.py"
    src.write_text("ITEMS = []\nSEEN = set()\n", encoding="utf-8")
    hits = scan_tree(tmp_path)
    assert len(hits) >= 1


def test_tuple_all_and_none_allowed(tmp_path: Path) -> None:
    src = tmp_path / "ok.py"
    src.write_text(
        '"""ok."""\nfrom typing import TYPE_CHECKING\n\n__all__: tuple[str, ...] = ()\n'
        "app: object | None = None\n"
        "if TYPE_CHECKING:\n    _hints: dict[str, str] = {}\n",
        encoding="utf-8",
    )
    assert scan_tree(tmp_path) == []


def test_function_local_mutables_allowed(tmp_path: Path) -> None:
    src = tmp_path / "fn.py"
    src.write_text("def f() -> list[int]:\n    buf = []\n    return buf\n", encoding="utf-8")
    assert scan_tree(tmp_path) == []
