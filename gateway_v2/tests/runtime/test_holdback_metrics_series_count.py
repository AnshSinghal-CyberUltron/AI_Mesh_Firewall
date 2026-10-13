# Feature: observability-contract
# Validates: Requirements 15.2
"""Holdback producer ``SERIES_COUNT`` equals the series the Publisher renders for it (task 2.6).

Asserts ``runtime.holdback_metrics.SERIES_COUNT`` equals the series count the Publisher's holdback
adapter emits (four scalars + three histograms counted as 3 each = 13), including the held-tokens
histogram (R8), so the fixed expression and the rendered family count cannot drift.
"""

from __future__ import annotations

from gateway_v2.runtime import holdback_metrics
from tests.edge._publisher_fixture import build_publisher, small_k_contract


def test_holdback_series_count_matches_rendered() -> None:
    pub = build_publisher(small_k_contract(), lambda: 0.0)
    _families, count = pub._holdback_families()  # type: ignore[attr-defined]
    assert count == holdback_metrics.SERIES_COUNT, (
        f"holdback adapter rendered {count} series, SERIES_COUNT is {holdback_metrics.SERIES_COUNT}"
    )
