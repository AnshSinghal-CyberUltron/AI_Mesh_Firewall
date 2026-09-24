"""LGW00-4: size gate must fail a 130-line function and an 850-line module."""

from __future__ import annotations

from pathlib import Path

from lint.check_sizes import MAX_FUNCTION_LINES, MAX_MODULE_LINES, scan_tree


def test_limits_match_runbook() -> None:
    assert MAX_FUNCTION_LINES == 120
    assert MAX_MODULE_LINES == 800


def test_130_line_function_fails_with_file_and_line(tmp_path: Path) -> None:
    src = tmp_path / "too_long_fn.py"
    body = "\n".join(["def too_long() -> None:"] + ["    x = 1"] * 129)
    src.write_text(body + "\n", encoding="utf-8")
    hits = scan_tree(tmp_path)
    fn_hits = [h for h in hits if h.kind == "function"]
    assert fn_hits, hits
    hit = fn_hits[0]
    assert "too_long_fn.py" in hit.path
    assert hit.name == "too_long"
    assert hit.lines >= 130
    assert hit.start_line >= 1


def test_850_line_module_fails_with_file(tmp_path: Path) -> None:
    src = tmp_path / "too_long_mod.py"
    src.write_text("\n".join(["# pad"] * 850) + "\n", encoding="utf-8")
    hits = scan_tree(tmp_path)
    mod_hits = [h for h in hits if h.kind == "module"]
    assert mod_hits, hits
    assert "too_long_mod.py" in mod_hits[0].path
    assert mod_hits[0].lines >= 850


def test_empty_module_passes(tmp_path: Path) -> None:
    src = tmp_path / "ok.py"
    src.write_text('"""Tiny module."""\n\n__all__: tuple[str, ...] = ()\n', encoding="utf-8")
    assert scan_tree(tmp_path) == []
