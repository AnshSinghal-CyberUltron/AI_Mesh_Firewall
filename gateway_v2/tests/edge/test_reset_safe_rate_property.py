# Feature: observability-contract, Property 5: Reset-safe rate omits rather than a negative
# Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 13.1, 13.2, 13.3
"""Property 5 — ``ResetSafeRate`` emits a non-negative rate or omits; never a negative.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

For any sequence of counter samples: every emitted rate is >= 0; the first observation of a counter
omits (returns ``None``, never zero); a backwards step (``current < baseline``) omits that interval
and adopts ``current`` as the new baseline so the next non-decreasing interval publishes >= 0; a
restart modelled as a baseline loss or a counter decrease produces no negative value; and a sentinel
``ABSENT`` gauge is recognised by ``is_sentinel_gauge`` and passed through unchanged (never
rate-processed).
"""

from __future__ import annotations

import random

from gateway_v2.edge.rates import SENTINEL_ABSENT, ResetSafeRate, is_sentinel_gauge

_SEED = 0x0B5E_0005
_ITERATIONS = 10_000


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def test_property_5_reset_safe_rate_never_negative() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        rates = ResetSafeRate()
        name = f"counter_{rng.randint(0, 4)}"
        interval_s = rng.choice([0.5, 1.0, 15.0, 60.0])

        # A sequence of counter readings, with resets injected (a drop to a smaller value) to model
        # restarts, plus the occasional big jump.
        n = rng.randint(1, 40)
        value = rng.randint(0, 1000)
        prev_baseline: int | None = None  # the test's shadow of what the baseline should be
        first_seen = True

        for step in range(n):
            roll = rng.random()
            if roll < 0.2:
                # Reset: counter goes backwards (a restart re-created the _total from a low value).
                value = rng.randint(0, max(value - 1, 0))
            else:
                value += rng.randint(0, 500)

            emitted = rates.rate_or_omit(name, value, interval_s)

            if first_seen:
                # First observation: omit (None), adopt as baseline (R5.1 / R13.3).
                assert emitted is None, _ctx(
                    _SEED, i, step=step, note="first obs did not omit", got=emitted
                )
                prev_baseline = value
                first_seen = False
                continue

            assert prev_baseline is not None
            if value < prev_baseline:
                # Backwards: omit this interval, adopt current as new baseline (R5.2/5.3/13.2).
                assert emitted is None, _ctx(
                    _SEED, i, step=step, note="reset did not omit", got=emitted, base=prev_baseline
                )
                prev_baseline = value
            else:
                # Non-decreasing: a non-negative rate == (current - baseline) / interval (R5.4).
                expected = (value - prev_baseline) / interval_s
                assert emitted is not None, _ctx(
                    _SEED, i, step=step, note="non-decreasing omitted", value=value
                )
                assert emitted >= 0.0, _ctx(
                    _SEED, i, step=step, note="negative rate emitted", got=emitted
                )
                assert abs(emitted - expected) < 1e-9, _ctx(
                    _SEED, i, step=step, note="rate mismatch", got=emitted, want=expected
                )
                prev_baseline = value


def test_property_5_sentinel_gauge_is_recognised() -> None:
    """The ``ABSENT`` sentinel is recognised and is never confused with a counter (R5.5).

    The boundary helper ``is_sentinel_gauge`` names the one value the Publisher must never route
    through ``rate_or_omit``. For any non-sentinel float it reports False; for exactly the sentinel
    it reports True — so the Publisher's wiring can keep the sentinel gauge unchanged.
    """
    rng = random.Random(_SEED ^ 0x5EED)
    assert is_sentinel_gauge(SENTINEL_ABSENT)
    for _ in range(10_000):
        value = rng.uniform(-1000.0, 1000.0)
        if value == SENTINEL_ABSENT:
            assert is_sentinel_gauge(value)
        else:
            assert not is_sentinel_gauge(value), f"non-sentinel {value!r} flagged as sentinel"
