"""LGW19-5.3: the ``admit/`` package holds no capacity literal (R2-07 / GW19), task 5.3.

The admission-control design forbids any capacity-position literal in ``admit`` — every bound is a
:class:`~gateway_v2.runtime.resources.ResourceContract` call (Req 4.4, 12.6). The shipped-tree gate
in ``tests/gates/test_check_capacity.py`` already scans the whole ``gateway_v2`` package (which
includes ``admit/``); this file is the focused, admission-scoped assertion of the same discipline so
a regression in ``admit/`` is attributable to GW19, and proves the gate actually *fires* on a
planted literal inside ``admit/``.
"""

from __future__ import annotations

from pathlib import Path

from lint.check_capacity_literals import scan_tree

_PKG = Path(__file__).resolve().parents[2] / "gateway_v2"
_ADMIT = _PKG / "admit"


def _admit_hits(root: Path) -> list[str]:
    """Capacity-literal violations whose path lands inside the ``admit/`` package."""
    return [h.symbol for h in scan_tree(root) if "/admit/" in (h.path.replace("\\", "/") + "/")]


def test_admit_package_has_no_capacity_literals() -> None:
    """No numeric capacity literal in any shipped admit/ module (quota.py included)."""
    assert _admit_hits(_PKG) == []


def test_quota_module_has_no_capacity_literals() -> None:
    """quota.py derives every bound from a ResourceContract call, never a literal (Req 4.4)."""
    hits = [h for h in scan_tree(_PKG) if h.path.replace("\\", "/").endswith("admit/quota.py")]
    assert hits == []


def test_gate_fires_on_a_planted_admit_literal(tmp_path: Path) -> None:
    """A capacity literal planted inside a fake admit/ tree is caught (the gate is live)."""
    poison = tmp_path / "admit" / "poison.py"
    poison.parent.mkdir(parents=True, exist_ok=True)
    poison.write_text("def q() -> None:\n    size(queue_depth=512)\n", encoding="utf-8")
    hits = scan_tree(tmp_path)
    assert any("queue_depth=512" in h.symbol for h in hits)
    assert any(h.path.replace("\\", "/").endswith("admit/poison.py") for h in hits)
