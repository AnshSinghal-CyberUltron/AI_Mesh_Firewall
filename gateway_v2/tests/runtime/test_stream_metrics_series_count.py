# Feature: observability-contract
# Validates: Requirements 15.2
"""Stream producer ``SERIES_COUNT`` equals the series the Publisher renders for it (task 2.4).

Asserts ``PerRequestExports.SERIES_COUNT`` equals the series count the Publisher's stream adapter
emits (five scalars + two histograms counted as 3 each = 11), so the fixed expression and the
rendered family count cannot drift.
"""

from __future__ import annotations

from gateway_v2.runtime.stream_metrics import PerRequestExports
from tests.edge._publisher_fixture import build_publisher, small_k_contract


def test_stream_series_count_matches_rendered() -> None:
    pub = build_publisher(small_k_contract(), lambda: 0.0)
    _families, count = pub._stream_families()  # type: ignore[attr-defined]
    assert count == PerRequestExports.SERIES_COUNT, (
        f"stream adapter rendered {count} series, SERIES_COUNT is {PerRequestExports.SERIES_COUNT}"
    )
