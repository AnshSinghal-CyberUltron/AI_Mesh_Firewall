# Feature: observability-contract, Property 7: Real zeros never absent
# Validates: Requirements 2.5, 3.7, 6.4, 7.5, 8.1
"""Property 7 — a fresh Publisher's first exposition carries every bounded series as a real zero.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

For a freshly constructed Publisher with no observations, parsing the first exposition finds the
``other`` bucket, the Overflow_Counter, every ``reason`` bucket, every Posture_Code counter, every
histogram ``_count``, and the windowed-busy gauge present with value exactly 0 (or the documented
empty value). The 10k loop varies the clock reading each Publisher is built with (window
boundaries), proving the real-zeros property is independent of the clock.
"""

from __future__ import annotations

import random

from gateway_v2.edge.counters import (
    COVERAGE_GAP_SERIES,
    POSTURE_CODES,
    POSTURE_PREFIX,
    REASON_VOCAB,
    VOCAB_ERROR_SERIES,
)
from gateway_v2.edge.publisher import (
    ADDON_HISTOGRAM_BASE,
    TENANT_OTHER_SERIES,
    WINDOWED_BUSY_SERIES,
)
from tests.edge._publisher_fixture import build_publisher, small_k_contract

_SEED = 0x0B5E_0007
_ITERATIONS = 10_000

_TENANT_OVERFLOW_SERIES = "amf_tenant_magnitude_overflow_total"


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _parse_series(body: str) -> dict[str, float]:
    """Map every rendered series line to its value (last token)."""
    out: dict[str, float] = {}
    for line in body.split("\n"):
        if not line or line.startswith("# TYPE "):
            continue
        name, _sep, value = line.rpartition(" ")
        out[name] = float(value)
    return out


def test_property_7_real_zeros_never_absent() -> None:
    rng = random.Random(_SEED)
    contract = small_k_contract()
    for i in range(_ITERATIONS):
        clock_value = rng.uniform(0.0, 1_000_000.0)
        pub = build_publisher(contract, lambda cv=clock_value: cv)

        series = _parse_series(pub.exposition())

        # other bucket present and zero (R3.7).
        assert series.get(TENANT_OTHER_SERIES) == 0.0, _ctx(
            _SEED, i, note="other absent/non-zero", got=series.get(TENANT_OTHER_SERIES)
        )
        # Overflow_Counter present and zero (R2.5).
        assert series.get(_TENANT_OVERFLOW_SERIES) == 0.0, _ctx(
            _SEED, i, note="overflow absent/non-zero", got=series.get(_TENANT_OVERFLOW_SERIES)
        )
        # Every reason bucket present and zero at the canonical 503 status (R6.4).
        for reason in REASON_VOCAB:
            name = f'amf_rejected{{status="503",reason="{reason}"}}'
            assert series.get(name) == 0.0, _ctx(
                _SEED, i, note="reason bucket absent/non-zero", reason=reason, got=series.get(name)
            )
        assert series.get(VOCAB_ERROR_SERIES) == 0.0, _ctx(_SEED, i, note="vocab-error not zero")
        # Every Posture_Code counter present and zero (R7.5).
        for code in POSTURE_CODES:
            name = f"{POSTURE_PREFIX}_{code}_total"
            assert series.get(name) == 0.0, _ctx(
                _SEED, i, note="posture counter absent/non-zero", code=code, got=series.get(name)
            )
        assert series.get(COVERAGE_GAP_SERIES) == 0.0, _ctx(_SEED, i, note="coverage-gap not zero")
        # The windowed C4 add-on histogram _count present and zero (R8.1).
        assert series.get(f"{ADDON_HISTOGRAM_BASE}_count") == 0.0, _ctx(
            _SEED, i, note="addon _count absent/non-zero"
        )
        # The windowed-busy gauge present with the empty value (R11.4). 0.0 is the documented empty.
        assert series.get(WINDOWED_BUSY_SERIES) == 0.0, _ctx(
            _SEED, i, note="windowed-busy absent/non-empty", got=series.get(WINDOWED_BUSY_SERIES)
        )
