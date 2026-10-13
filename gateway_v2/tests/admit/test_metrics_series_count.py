# Feature: observability-contract
# Validates: Requirements 15.2
"""Admit producer ``SERIES_COUNT`` equals the series the Publisher renders for it (task 2.2).

A fixed, by-construction ``SERIES_COUNT`` is only honest if it matches what actually gets rendered.
These example tests assert ``AdmissionMetrics.SERIES_COUNT`` and ``QuotaMetrics.SERIES_COUNT`` each
equal the series count the Publisher's own adapter emits for that producer (a histogram family
counted as its three native series), so a tenant-derived series would fail the gate rather than
ship. Not a property test — a direct structural assertion.
"""

from __future__ import annotations

from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from tests.edge._publisher_fixture import build_publisher, small_k_contract


def _publisher() -> object:
    return build_publisher(small_k_contract(), lambda: 0.0)


def test_admission_series_count_matches_rendered() -> None:
    """``AdmissionMetrics.SERIES_COUNT`` == the Publisher's admit-adapter series count (R15.2)."""
    pub = _publisher()
    _families, count = pub._admit_families()  # type: ignore[attr-defined]
    assert count == AdmissionMetrics.SERIES_COUNT, (
        f"admit adapter rendered {count} series, SERIES_COUNT is {AdmissionMetrics.SERIES_COUNT}"
    )


def test_quota_series_count_matches_rendered() -> None:
    """``QuotaMetrics.SERIES_COUNT`` == the Publisher's quota-adapter series count (R15.2)."""
    pub = _publisher()
    _families, count = pub._quota_families()  # type: ignore[attr-defined]
    assert count == QuotaMetrics.SERIES_COUNT, (
        f"quota adapter rendered {count} series, SERIES_COUNT is {QuotaMetrics.SERIES_COUNT}"
    )
