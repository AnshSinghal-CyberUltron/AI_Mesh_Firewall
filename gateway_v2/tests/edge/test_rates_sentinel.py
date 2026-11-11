# Feature: observability-contract
# Validates: Requirements 5.5
"""Sentinel-gauge passthrough (task 6.3) — ``ABSENT = -1.0`` gauges are never rate-processed.

Example/unit test: the ``ABSENT`` sentinel a ``state_metrics`` / ``state_control`` gauge uses to say
"never observed" is NOT a counter and must never be routed through the reset-safe rate book-keeper.
``is_sentinel_gauge`` is the boundary the Publisher consults; it recognises exactly the reserved
value and nothing else.
"""

from __future__ import annotations

from gateway_v2.edge.rates import SENTINEL_ABSENT, ResetSafeRate, is_sentinel_gauge
from gateway_v2.runtime.state_metrics import ABSENT


def test_sentinel_constant_is_the_shipped_absent() -> None:
    """The rate module re-exports the shipped ``ABSENT`` so the two surfaces cannot drift (R5.5)."""
    assert SENTINEL_ABSENT == ABSENT == -1.0


def test_sentinel_is_recognised_and_normal_values_are_not() -> None:
    assert is_sentinel_gauge(ABSENT)
    for value in (0.0, 1.0, -0.5, -2.0, 42.0, 1e9):
        assert not is_sentinel_gauge(value), f"{value!r} wrongly flagged as a sentinel"


def test_counter_rate_is_untouched_by_the_sentinel_rule() -> None:
    """A real counter still produces a reset-safe rate; the sentinel rule applies to gauges only."""
    rates = ResetSafeRate()
    assert rates.rate_or_omit("c", 100, 10.0) is None  # first obs: omit
    assert rates.rate_or_omit("c", 200, 10.0) == 10.0  # (200-100)/10
