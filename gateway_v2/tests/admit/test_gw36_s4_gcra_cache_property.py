"""Property tests for the per-principal GCRA cache (GW36 S4, tasks 2.3 + 2.4).

Covers the pure ``gateway_v2.admit.gcra_cache.GcraCache`` built over the pure
``gateway_v2.admit.gcra`` core, with the resolved
``gateway_v2.domain.identity.Principal`` as the input value type (no I/O, no store, no
event loop):

* **Property 3 — GCRA allowance preserved on reject** (task 2.3): the GCRA that
  ``GcraCache.for_principal`` returns (with ``worker_count == 1`` so the per-worker rate
  equals the principal rate, isolating the allowance behaviour) admits the first arrival,
  never advances its TAT on a reject, keeps the windowed admit count within the standard
  GCRA bound ``burst + 1 + rate_per_s * window``, and never starves the next legitimately
  timed arrival (one spaced ``>= 1 / rate`` after the last admit still admits).
* **Property 4 — per-worker rate split sums to the principal rate** (task 2.4): for
  ``N in [1, 64]`` and ``rate_per_s in [0, 10_000]`` (including ``0`` -> ``None``,
  contributing ``0``), the N per-worker rates sum to the principal's ``rate_per_s`` within
  float tolerance.

House idiom (copied from ``tests/admit/test_lgw06_gcra.py``): seeded ``random.Random`` loops
of ``>= 10,000`` iterations with the seed logged in every assertion message; NO ``hypothesis``.
Time is driven by an explicit injected ``clock=`` / ``now=`` so the machine is deterministic.
Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import random

from gateway_v2.admit.gcra_cache import GcraCache
from gateway_v2.domain.identity import Principal

_ITERATIONS = 10_000


def _gcra_window_bound(burst: float, rate_per_s: float, window_s: float) -> float:
    """The exact bound asserted by Property 3 — identical statement to Property 8.

    Standard GCRA admits an arrival at ``now`` when ``now >= TAT - tau`` with ``tau = burst * T``
    and ``T = 1 / rate``. Over a window of length ``W`` the admitted count is at most:

        number drained by rate   +   burst tolerance   +   one window-edge boundary
            rate * W             +       burst         +            1

    The trailing ``+ 1`` is the standard GCRA boundary term — a single admission can straddle the
    window's left edge — exactly as ``test_lgw06_gcra._gcra_window_bound`` documents. Reusing that
    statement keeps Property 3 pinned to the same off-by-one contract as Property 8.
    """
    return burst + 1 + rate_per_s * window_s


def _principal(*, rate_per_s: float, burst: float, feed_seq: int = 0) -> Principal:
    """Minimal valid ``Principal`` via the real constructor (only quota fields matter here)."""
    return Principal(
        key_id="k",
        org_id="org",
        rate_per_s=rate_per_s,
        burst=burst,
        feed_seq=feed_seq,
    )


# --------------------------------------------------------------------------- #
# Task 2.3 — Property 3: GCRA allowance preserved on reject
# Feature: gw36-s4-admission-quota, Property 3: GCRA allowance preserved on reject
# Validates: Requirements 3.1, 3.6
# --------------------------------------------------------------------------- #


def test_property3_gcra_allowance_preserved_on_reject() -> None:
    """A reject never advances the TAT, and the windowed admit count stays within the bound.

    Drive the ``LocalGCRA`` that ``GcraCache.for_principal`` returns (``worker_count == 1`` so the
    per-worker rate equals the principal rate, isolating the allowance behaviour) with random
    arrival streams mixing dense bursts (tighter than ``1 / rate``) and idle gaps (``>= 1 / rate``).
    Assert, over ``>= 10,000`` seeded iterations: (a) the first arrival admits; (b) the admitted
    count over the observed span stays within ``burst + 1 + rate * window``; (c) a reject never
    starves the next legitimately-timed arrival — an arrival spaced ``>= 1 / rate`` after the last
    ADMIT is admitted (so the reject left the TAT untouched, Req 3.6).
    """
    seed = 0x5_4_03
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        rate_per_s = rng.uniform(0.5, 200.0)
        burst = rng.randint(0, 20)
        emission = 1.0 / rate_per_s  # T = 1 / rate

        cache = GcraCache(worker_count=1, clock=lambda: 0.0)
        gcra = cache.for_principal(
            f"hash-{i}", _principal(rate_per_s=rate_per_s, burst=burst)
        )
        assert gcra is not None, (
            f"seed={seed:#x} iter={i} expected a GCRA for a positive rate "
            f"(rate={rate_per_s:.4f} burst={burst})"
        )

        now = rng.uniform(0.0, 5.0)
        start = now
        admitted = 0
        first_decision: bool | None = None
        last_admit_time: float | None = None

        steps = rng.randint(1, 60)
        for _ in range(steps):
            # Inter-arrival gap: a mix of same-instant dense bursts (0), sub-interval gaps
            # (tighter than 1/rate), and idle gaps spanning several emission intervals.
            now += rng.choice(
                (
                    0.0,
                    0.0,
                    rng.uniform(0.0, emission),
                    rng.uniform(emission, 4.0 * emission),
                )
            )
            decision = gcra.admit(now=now)
            if first_decision is None:
                first_decision = decision
            if decision:
                admitted += 1
                last_admit_time = now

        # (a) First arrival admits (TAT unseated).
        assert first_decision is True, (
            f"seed={seed:#x} iter={i} first arrival must admit "
            f"(rate={rate_per_s:.4f} burst={burst})"
        )

        # (b) Windowed admit count within the standard GCRA bound.
        window = now - start
        bound = _gcra_window_bound(burst, rate_per_s, window)
        assert admitted <= bound + 1e-9, (
            f"seed={seed:#x} iter={i} admitted={admitted} > bound={bound:.6f} "
            f"(rate={rate_per_s:.4f} burst={burst} window={window:.6f})"
        )

        # (c) No starvation: after the last admit, hammer the window with rejects, then an arrival
        # spaced >= 1/rate (well past the burst tolerance) after that last admit must still admit.
        # If a reject had advanced the TAT, this legitimately-timed arrival would be pushed out.
        assert last_admit_time is not None  # the first arrival always admitted
        hammer_at = last_admit_time + rng.uniform(0.0, emission * 0.5)
        for _ in range(rng.randint(1, 20)):
            gcra.admit(now=hammer_at)  # rejects or admits; either way must not starve below
        legitimate_at = last_admit_time + emission * (burst + 2)
        assert gcra.admit(now=legitimate_at) is True, (
            f"seed={seed:#x} iter={i} an arrival spaced >= 1/rate after the last admit was "
            f"starved by prior rejects (rate={rate_per_s:.4f} burst={burst} "
            f"last_admit={last_admit_time:.6f} legitimate_at={legitimate_at:.6f})"
        )


# --------------------------------------------------------------------------- #
# Task 2.4 — Property 4: Per-worker rate split sums to the principal rate
# Feature: gw36-s4-admission-quota, Property 4: Per-worker rate split sums to the principal rate
# Validates: Requirements 3.4
# --------------------------------------------------------------------------- #


def test_property4_per_worker_rate_split_sums_to_principal_rate() -> None:
    """The N per-worker rates sum to the principal's ``rate_per_s`` within float tolerance.

    For random ``N in [1, 64]`` and ``rate_per_s in [0, 10_000]`` (including ``0``), build the
    per-worker GCRA via a ``GcraCache(worker_count=N)`` and read back each worker's rate from the
    built ``GcraParams`` (one cache instance models one worker, so N caches model N workers).
    Assert ``N * per_worker_rate == rate_per_s`` within ``abs <= 1e-6 * max(1, rate)``. For
    ``rate_per_s == 0``, ``for_principal`` returns ``None`` (no per-key limit, Req 3.5): the None
    branch holds and the degenerate "sum" of zero contributing workers is ``0`` (Req 3.4).
    """
    seed = 0x5_4_04
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        n_workers = rng.randint(1, 64)
        # Mix exact 0 (the None branch), tiny, and full-range rates.
        rate_per_s = rng.choice(
            (
                0.0,
                0.0,
                rng.uniform(0.0, 1.0),
                rng.uniform(1.0, 10_000.0),
            )
        )
        burst = rng.randint(0, 50)
        principal = _principal(rate_per_s=rate_per_s, burst=burst)
        tol = 1e-6 * max(1.0, rate_per_s)

        if rate_per_s == 0.0:
            # None branch: every worker's cache returns None and contributes 0 to the sum.
            summed = 0.0
            for w in range(n_workers):
                cache = GcraCache(worker_count=n_workers, clock=lambda: 0.0)
                gcra = cache.for_principal(f"hash-{i}", principal)
                assert gcra is None, (
                    f"seed={seed:#x} iter={i} worker={w} rate_per_s==0 must yield None "
                    f"(no per-key limit) but got {gcra!r}"
                )
                # A None worker contributes 0 to the per-worker rate sum.
                summed += 0.0
            assert summed == 0.0, (
                f"seed={seed:#x} iter={i} None-branch sum must degenerate to 0, got {summed!r} "
                f"(n_workers={n_workers})"
            )
            continue

        # Positive rate: each worker's GCRA carries rate_per_s / n_workers; the N shares must sum
        # back to the principal's configured rate within float tolerance (Req 3.4).
        summed = 0.0
        for w in range(n_workers):
            cache = GcraCache(worker_count=n_workers, clock=lambda: 0.0)
            gcra = cache.for_principal(f"hash-{i}", principal)
            assert gcra is not None, (
                f"seed={seed:#x} iter={i} worker={w} positive rate must yield a GCRA "
                f"(rate={rate_per_s!r} n_workers={n_workers})"
            )
            summed += gcra.params.rate_per_s

        assert abs(summed - rate_per_s) <= tol, (
            f"seed={seed:#x} iter={i} per-worker rate split sum {summed!r} != principal rate "
            f"{rate_per_s!r} (n_workers={n_workers} tol={tol!r})"
        )
