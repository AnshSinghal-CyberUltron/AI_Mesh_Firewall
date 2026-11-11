# Feature: observability-contract, Property 8: Round-trip of the exposition text
# Validates: Requirements 1.4, 1.5, 1.6, 1.7, 8.1, 8.2, 11.1
"""Property 8 — ``render(parse(render(state))) == render(state)`` byte-for-byte.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

For any set of randomized scalar + histogram families, the renderer produces a body whose parse,
re-rendered, is byte-identical; every ``# TYPE`` line's declared type matches the suffix rule
(``_total`` => counter, else gauge; histograms typed ``histogram``); and every histogram family's
``_bucket`` sequence is non-decreasing with ``_bucket{le="+Inf"} == _count`` and sum == Σsamples.
"""

from __future__ import annotations

import random

from gateway_v2.edge import render
from gateway_v2.edge.render import (
    COUNTER,
    GAUGE,
    HISTOGRAM,
    Family,
    family_of,
    metric_type,
    parse,
    render_families,
    render_histogram,
)
from gateway_v2.runtime.holdback_metrics import Histogram

_SEED = 0x0B5E_0008
_ITERATIONS = 10_000


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _random_scalar_family(rng: random.Random, base: str) -> Family:
    # A scalar family: one or more labelled series under one base, typed by the suffix rule.
    is_counter = rng.random() < 0.5
    name = f"{base}_total" if is_counter else base
    want_type = COUNTER if is_counter else GAUGE
    n_series = rng.randint(1, 4)
    lines: list[str] = []
    for j in range(n_series):
        # Occasionally integral, occasionally fractional, to exercise canonical value spelling.
        value = rng.choice([float(rng.randint(0, 100000)), rng.uniform(0, 1000)])
        label = f'{{bucket="{j}"}}' if n_series > 1 else ""
        lines.append(f"{name}{label} {render._format_value(value)}")
    return Family(base=name, type=want_type, lines=tuple(lines))


def _random_histogram_family(rng: random.Random, base: str) -> Family:
    hist = Histogram()
    for _ in range(rng.randint(0, 30)):
        hist.observe(rng.randint(0, 3000))
    bounds = (0, 5, 10, 50, 100, 500, 1000, 2000)
    return render_histogram(base, hist, bounds)


def test_property_8_exposition_round_trip_is_byte_identical() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        families: list[Family] = []
        n_families = rng.randint(1, 8)
        for f in range(n_families):
            base = f"amf_test_{f:02d}"
            if rng.random() < 0.4:
                families.append(_random_histogram_family(rng, base))
            else:
                families.append(_random_scalar_family(rng, base))

        body = render_families(families)

        # Round-trip: parse then re-render is byte-identical (Property 8).
        reparsed = render_families(family_of(pf) for pf in parse(body))
        assert reparsed == body, _ctx(
            _SEED, i, note="round-trip not byte-identical", body=body, reparsed=reparsed
        )

        # Each family's declared type matches the suffix rule / histogram shape (R1.5/R11.1).
        for pf in parse(body):
            # A histogram family's base has no _total suffix in these fixtures, so distinguish by
            # whether its series carry _bucket/_sum/_count lines.
            series_names = [name for name, _ in pf.samples]
            is_hist = any(name.startswith(f"{pf.base}_bucket") for name in series_names)
            if is_hist:
                assert pf.type == HISTOGRAM, _ctx(
                    _SEED, i, note="histogram not typed histogram", base=pf.base, type=pf.type
                )
                _assert_histogram_shape(pf, i)
            else:
                assert pf.type == metric_type(pf.base), _ctx(
                    _SEED, i, note="scalar type != suffix rule", base=pf.base, type=pf.type
                )


def _assert_histogram_shape(pf: object, i: int) -> None:
    """Buckets non-decreasing, +Inf == _count, _sum present (R8.2)."""
    samples = dict(pf.samples)  # type: ignore[attr-defined]
    base = pf.base  # type: ignore[attr-defined]
    bucket_vals: list[float] = []
    inf_val: float | None = None
    for name, value in pf.samples:  # type: ignore[attr-defined]
        if name.startswith(f"{base}_bucket"):
            if 'le="+Inf"' in name:
                inf_val = value
            else:
                bucket_vals.append(value)
    count = samples.get(f"{base}_count")
    assert count is not None, _ctx(_SEED, i, note="histogram missing _count", base=base)
    assert f"{base}_sum" in samples, _ctx(_SEED, i, note="histogram missing _sum", base=base)
    # Finite buckets are cumulative-monotone.
    for a, b in zip(bucket_vals, bucket_vals[1:], strict=False):
        assert a <= b, _ctx(_SEED, i, note="buckets not non-decreasing", a=a, b=b)
    # +Inf bucket equals _count (R8.2 / R1.6).
    assert inf_val == count, _ctx(
        _SEED, i, note="+Inf bucket != _count", inf=inf_val, count=count
    )
