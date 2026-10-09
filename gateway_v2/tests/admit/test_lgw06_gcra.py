"""Pure Local GCRA tests (R2-09 / GW06), task 2 / 2.1.

Covers the pure ``gateway_v2.admit.gcra`` module only (no I/O, no store, no event loop):

* **Property 8 — GCRA bounds burst and rate** (task 2.1): seeded ``random.Random`` arrival streams
  over an injected clock; the admission count in any window never exceeds the standard GCRA bound
  ``burst + 1 + rate_per_s * window`` (see ``_gcra_window_bound`` for the exact statement and why
  the ``+ 1`` boundary term is correct), independent of any lease state.
* **Unit tests**: the first arrival always admits; a burst of ``burst`` arrivals at the same instant
  admits then rejects; after an idle gap the allowance refills at ``rate_per_s``; a rejected arrival
  does NOT advance the TAT (so a reject never pushes the next legitimate arrival out).

House idiom: seeded ``random.Random`` loops of >= 10,000 iterations with the seed logged in the
assertion message; NO ``hypothesis``. Time is driven by an explicit ``now=`` so the machine is
deterministic. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import math
import random

from gateway_v2.admit.gcra import GcraParams, LocalGCRA

_ITERATIONS = 10_000


def _gcra_window_bound(burst: int, rate_per_s: float, window_s: float) -> float:
    """The exact bound asserted by Property 8.

    Standard GCRA admits an arrival at ``now`` when ``now >= TAT - tau`` with ``tau = burst * T``
    and ``T = 1 / rate``. Over a window of length ``W`` the admitted count is at most:

        number drained by rate   +   burst tolerance   +   one window-edge boundary
            rate * W             +       burst         +            1

    The ``rate * W`` term is the steady-state service; the ``burst`` term is the ``tau / T = burst``
    early arrivals the tolerance permits; the trailing ``+ 1`` is the standard GCRA boundary — a
    single admission can straddle the window's left edge (the TAT seated just before ``a`` still
    lets one arrival through at ``a``), so the count is bounded by the next integer. Asserting a
    tighter bound (dropping the ``+ 1``) is a known GCRA off-by-one that this term deliberately
    absorbs; asserting a looser bound would make the property vacuous.
    """
    return burst + 1 + rate_per_s * window_s


# --------------------------------------------------------------------------- #
# Task 2.1 — Property 8: GCRA bounds burst and rate
# Feature: budget-lease, Property 8
# Validates: Requirements 1.1, 1.3, 1.4
# --------------------------------------------------------------------------- #


def test_property8_gcra_bounds_burst_and_rate() -> None:
    """Windowed admission count never exceeds ``burst + 1 + rate * window`` (Property 8).

    Seeded arrival streams over an injected clock: random rates, bursts, and inter-arrival gaps
    (including dense bursts and long idle gaps). For the whole observed span ``[t0, t_last]`` the
    admitted count must stay within the standard GCRA bound. No store is ever touched.
    """
    seed = 0x06_08
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        rate_per_s = rng.uniform(0.5, 200.0)
        burst = rng.randint(0, 20)
        gcra = LocalGCRA(GcraParams(rate_per_s=rate_per_s, burst=burst), clock=lambda: 0.0)

        now = rng.uniform(0.0, 5.0)
        start = now
        steps = rng.randint(1, 60)
        admitted = 0
        for _ in range(steps):
            # Inter-arrival gap: a mix of dense bursts (0) and spread-out / idle arrivals.
            now += rng.choice((0.0, 0.0, rng.uniform(0.0, 0.5), rng.uniform(0.0, 3.0)))
            if gcra.admit(now=now):
                admitted += 1

        window = now - start
        bound = _gcra_window_bound(burst, rate_per_s, window)
        assert admitted <= bound + 1e-9, (
            f"seed={seed:#x} iter={i} admitted={admitted} > bound={bound:.6f} "
            f"(rate={rate_per_s:.4f} burst={burst} window={window:.6f})"
        )


def test_property8_subwindow_bound_holds_on_dense_bursts() -> None:
    """Every sliding sub-window also obeys the bound, stressed with same-instant bursts.

    A stricter form of Property 8: for every pair of admission timestamps ``(a, b)`` the count of
    admissions in ``[a, b]`` must obey ``burst + 1 + rate * (b - a)``. Same-instant bursts are the
    worst case for the burst term.
    """
    seed = 0x06_08A
    rng = random.Random(seed)
    for i in range(_ITERATIONS // 5):
        rate_per_s = rng.uniform(1.0, 100.0)
        burst = rng.randint(0, 10)
        gcra = LocalGCRA(GcraParams(rate_per_s=rate_per_s, burst=burst), clock=lambda: 0.0)

        now = 0.0
        admit_times: list[float] = []
        for _ in range(rng.randint(1, 40)):
            now += rng.choice((0.0, 0.0, 0.0, rng.uniform(0.0, 1.0)))
            if gcra.admit(now=now):
                admit_times.append(now)

        for a_idx, a in enumerate(admit_times):
            for b in admit_times[a_idx:]:
                count = sum(1 for t in admit_times if a <= t <= b)
                bound = _gcra_window_bound(burst, rate_per_s, b - a)
                assert count <= bound + 1e-9, (
                    f"seed={seed:#x} iter={i} subwindow [{a:.6f},{b:.6f}] count={count} "
                    f"> bound={bound:.6f} (rate={rate_per_s:.4f} burst={burst})"
                )


# --------------------------------------------------------------------------- #
# Task 2 — unit tests
# Feature: budget-lease
# Validates: Requirements 1.1, 1.3, 1.4
# --------------------------------------------------------------------------- #


def test_first_arrival_always_admits() -> None:
    """The first arrival is always admitted regardless of rate/burst (TAT is unseated)."""
    gcra = LocalGCRA(GcraParams(rate_per_s=1.0, burst=0), clock=lambda: 100.0)
    assert gcra.admit() is True


def test_burst_of_burst_arrivals_at_same_instant_then_rejects() -> None:
    """At one instant, ``1 + burst`` arrivals admit (first seat + burst tolerance), then reject.

    With ``burst = 3`` the first admit seats the TAT at ``now + T``; the burst tolerance
    ``tau = 3 * T`` then permits 3 more same-instant arrivals before the TAT - tau boundary is
    crossed and the next is rejected.
    """
    burst = 3
    gcra = LocalGCRA(GcraParams(rate_per_s=10.0, burst=burst), clock=lambda: 0.0)
    now = 0.0
    admits = [gcra.admit(now=now) for _ in range(burst + 1)]
    assert admits == [True] * (burst + 1), f"expected first+{burst} admits, got {admits}"
    assert gcra.admit(now=now) is False, "an arrival beyond the burst tolerance must be rejected"


def test_allowance_refills_at_rate_after_idle() -> None:
    """After an idle gap the allowance refills at ``rate_per_s`` (one admit per ``T`` elapsed)."""
    rate_per_s = 4.0  # T = 0.25 s
    t = 1.0 / rate_per_s
    gcra = LocalGCRA(GcraParams(rate_per_s=rate_per_s, burst=0), clock=lambda: 0.0)
    assert gcra.admit(now=0.0) is True  # seats TAT at T
    # Immediately again at the same instant: rejected (burst=0, no tolerance).
    assert gcra.admit(now=0.0) is False
    # After exactly one emission interval has elapsed, one more admit is allowed.
    assert gcra.admit(now=t) is True
    assert gcra.admit(now=t) is False
    # Idle for 5*T: still only one admit becomes available (burst=0 caps the refill).
    assert gcra.admit(now=t + 5 * t) is True
    assert gcra.admit(now=t + 5 * t) is False


def test_reject_does_not_advance_tat() -> None:
    """A rejected arrival leaves the TAT untouched, so the next legitimate arrival still serves.

    If a reject advanced the TAT, a client hammering a closed window would push its own next
    legitimate arrival out past the rate. Standard GCRA advances only on admit.
    """
    rate_per_s = 2.0  # T = 0.5 s
    t = 1.0 / rate_per_s
    gcra = LocalGCRA(GcraParams(rate_per_s=rate_per_s, burst=0), clock=lambda: 0.0)
    assert gcra.admit(now=0.0) is True
    # Hammer the closed window with many rejects.
    for _ in range(50):
        assert gcra.admit(now=0.1) is False
    # The rejects did not push the TAT out: at exactly t the next arrival is still admitted.
    assert gcra.admit(now=t) is True
    assert math.isclose(1.0 / gcra.params.rate_per_s, t), "params preserved on the instance"
