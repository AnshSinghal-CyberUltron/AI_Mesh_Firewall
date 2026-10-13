# Feature: observability-contract, Property 2: Exposition is bounded and fully typed
# Validates: Requirements 1.2, 3.3, 3.8, 15.1, 15.2, 15.3
"""Property 2 — ``exposition()`` renders exactly ``SERIES_COUNT`` series, one ``# TYPE`` per family.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

For any observed tenant set, the rendered SERIES count equals ``Publisher.SERIES_COUNT`` (the fixed
expression); every family carries exactly one ``# TYPE`` line; the per-tenant + ``other`` series
count never exceeds K + 1; and varying the tenant count from 0 to well beyond capacity does not
change the rendered family or series count (invariant to the estate).
"""

from __future__ import annotations

import random

from tests.edge._publisher_fixture import build_publisher, small_k_contract

_SEED = 0x0B5E_0002
_ITERATIONS = 10_000


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _count_physical_lines(body: str) -> int:
    """Number of rendered SERIES lines (every non-blank, non-``# TYPE`` line)."""
    return sum(1 for line in body.split("\n") if line and not line.startswith("# TYPE "))


def _count_type_lines(body: str) -> int:
    return sum(1 for line in body.split("\n") if line.startswith("# TYPE "))


def _count_convention_series(body: str) -> int:
    """Count series the way the Publisher's ``SERIES_COUNT`` convention does.

    A histogram family (identified by its ``# TYPE <base> histogram`` line) counts as exactly 3
    series — ``_bucket`` + ``_sum`` + ``_count`` — regardless of how many finite bucket boundaries
    it renders; every non-histogram series line counts as one. This mirrors the design's
    §SERIES_COUNT convention that the Publisher asserts against internally.
    """
    lines = body.split("\n")
    hist_bases: set[str] = set()
    for line in lines:
        if line.startswith("# TYPE ") and line.endswith(" histogram"):
            hist_bases.add(line.split(" ")[2])

    def _is_hist_member(series_line: str) -> bool:
        return any(
            series_line.startswith(f"{hb}_bucket")
            or series_line.startswith(f"{hb}_sum ")
            or series_line.startswith(f"{hb}_count ")
            for hb in hist_bases
        )

    total = 0
    for line in lines:
        if not line or line.startswith("# TYPE "):
            continue
        # Non-histogram series: count one each. Histogram member lines are tallied as 3/family.
        if not _is_hist_member(line):
            total += 1
    total += 3 * len(hist_bases)
    return total


def test_property_2_exposition_bounded_and_fully_typed() -> None:
    rng = random.Random(_SEED)
    contract = small_k_contract()
    k = contract.metrics_top_k()
    capacity = contract.metrics_directory_slots()

    # Record the invariant rendered counts on a fresh (zero-tenant) Publisher.
    baseline_pub = build_publisher(contract, lambda: 0.0)
    baseline_body = baseline_pub.exposition()
    baseline_physical = _count_physical_lines(baseline_body)
    baseline_types = _count_type_lines(baseline_body)

    for i in range(_ITERATIONS):
        pub = build_publisher(contract, lambda: 0.0)

        # Observe a tenant count spanning 0 .. well beyond capacity.
        n_tenants = rng.choice([0, 1, k, capacity, capacity + rng.randint(1, 200)])
        for t in range(n_tenants):
            pub.observe_tenant(f"tenant_{t}", rng.randint(0, 1000))

        # exposition() internally asserts rendered-series == SERIES_COUNT (R15.2) under the design's
        # histogram-counts-as-3 convention; a return (no raise) therefore already proves R15.2. The
        # body-level checks here prove the invariance (R15.3) and one-# TYPE-per-family (R1.7).
        body = pub.exposition()
        convention_series = _count_convention_series(body)
        physical = _count_physical_lines(body)
        types = _count_type_lines(body)

        # Convention series count == the fixed SERIES_COUNT (R15.2) — the same count exposition()
        # asserts internally, recomputed from the body independently here.
        assert convention_series == pub.SERIES_COUNT, _ctx(
            _SEED,
            i,
            note="convention series != SERIES_COUNT",
            series=convention_series,
            want=pub.SERIES_COUNT,
            n=n_tenants,
        )
        # The physical rendered-line count is invariant to the observed tenant count (R15.3).
        assert physical == baseline_physical, _ctx(
            _SEED, i, note="physical count moved with tenants", got=physical, base=baseline_physical
        )
        # The family/# TYPE count is likewise invariant (R15.3).
        assert types == baseline_types, _ctx(
            _SEED, i, note="type-line count moved with tenants", types=types, base=baseline_types
        )

        # One # TYPE per distinct family base: no family emits two # TYPE lines (R1.7). Verified by
        # checking each # TYPE names a distinct base.
        type_bases = [
            line.split(" ")[2] for line in body.split("\n") if line.startswith("# TYPE ")
        ]
        assert len(type_bases) == len(set(type_bases)), _ctx(
            _SEED, i, note="duplicate # TYPE for a family base"
        )

        # Per-tenant visibility series count never exceeds K + 1 (R3.8): the K individual Top-K
        # series (``amf_tenant_magnitude{tenant="..."}``) plus the single ``other`` bucket. The
        # Overflow_Counter (``amf_tenant_magnitude_overflow_total``) is R2.5's separate counter, not
        # part of the per-tenant-visibility set, so it is excluded here.
        top_k_series = sum(
            1 for line in body.split("\n") if line.startswith("amf_tenant_magnitude{")
        )
        other_series = sum(
            1 for line in body.split("\n") if line.startswith("amf_tenant_magnitude_other ")
        )
        tenant_visibility = top_k_series + other_series
        assert tenant_visibility <= k + 1, _ctx(
            _SEED, i, note="tenant visibility exceeds K+1", got=tenant_visibility, k=k
        )
        assert top_k_series == k, _ctx(
            _SEED, i, note="Top-K family not padded to exactly K", got=top_k_series, k=k
        )
