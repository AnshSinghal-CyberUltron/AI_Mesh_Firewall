"""GW04 frozen-dataclass gate."""

from __future__ import annotations

from pathlib import Path

from lint.check_frozen_dataclasses import scan_tree

_PKG = Path(__file__).resolve().parents[2] / "gateway_v2"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_unfrozen_dataclass_in_plan_fails(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "plan/bad.py",
        "from dataclasses import dataclass\n\n@dataclass\nclass Box:\n    n: int\n",
    )
    hits = scan_tree(tmp_path)
    assert any(hit.name == "Box" and hit.reason == "not frozen" for hit in hits)
    assert any("plan/bad.py" in hit.path or hit.path.endswith("bad.py") for hit in hits)


def test_frozen_without_slots_fails(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "detect/semi.py",
        "from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Box:\n    n: int\n",
    )
    hits = scan_tree(tmp_path)
    assert any(hit.reason == "slots not set" for hit in hits)


def test_shipped_plan_detect_resolve_are_frozen() -> None:
    assert scan_tree(_PKG) == []
