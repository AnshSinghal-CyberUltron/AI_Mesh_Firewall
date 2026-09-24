"""Shipped empty tree must pass size / HTTP / mutable gates."""

from __future__ import annotations

from pathlib import Path

from lint.check_capacity_literals import scan_tree as capacity_scan
from lint.check_http_outside_edge_resolve import scan_tree as http_scan
from lint.check_no_module_mutable import scan_tree as mutable_scan
from lint.check_sizes import scan_tree as size_scan

_PKG = Path(__file__).resolve().parents[2] / "gateway_v2"


def test_shipped_tree_within_size_limits() -> None:
    assert size_scan(_PKG) == []


def test_shipped_tree_has_no_http_outside_edge_resolve() -> None:
    assert http_scan(_PKG) == []


def test_shipped_tree_has_no_module_mutable_state() -> None:
    assert mutable_scan(_PKG) == []


def test_shipped_tree_has_no_capacity_literals() -> None:
    assert capacity_scan(_PKG) == []
