"""Placeholder import smoke — empty v2 tree (GW00)."""

from __future__ import annotations


def test_edge_app_is_unset() -> None:
    from gateway_v2.edge import app

    assert app is None


def test_detect_base_exports_empty() -> None:
    import gateway_v2.detect.base as base

    assert base.__all__ == ()
