"""Placeholder import smoke — empty v2 tree (GW00)."""

from __future__ import annotations


def test_edge_app_is_unset() -> None:
    from gateway_v2.edge import app

    assert app is None


def test_detect_base_exports_the_detector_surface() -> None:
    # GW00 shipped detect/base.py as an empty stub; GW08 (Tasks 1-5) filled it with the
    # Detector protocol, DetectorContext, and the re-exported domain finding surface.
    import gateway_v2.detect.base as base

    assert base.__all__ == (
        "Detector",
        "DetectorContext",
        "Finding",
        "FindingStatus",
        "Span",
    )
