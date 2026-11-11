# Feature: observability-contract, Property 4: Windowed p99 tracks the injected window
# Validates: Requirements 4.1, 4.3, 4.4, 4.5, 4.6, 4.7, 13.4
"""Property 4 — the ``WindowedHistogram`` p99 reflects ONLY the current window.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations, an INJECTED clock.

For any injected sample window, the server-side p99 is within 0.5 ms of the quantile computed
directly over that same window's samples and does not under-read it by more than 0.5 ms; samples
placed outside the window never influence the reported quantile; and an empty window reports the
defined ``EMPTY_WINDOW`` value, never a stale / lifetime / negative quantile. The generator places
samples on both sides of the window boundary and includes empty windows.
"""

from __future__ import annotations

import math
import random

from gateway_v2.edge.windowed import WindowedHistogram

_SEED = 0x0B5E_0004
_ITERATIONS = 10_000
_TOLERANCE_MS = 0.5


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _nearest_rank(values: list[int], quantile: float) -> float:
    """The same nearest-rank rule the histogram uses, computed independently over ``values``."""
    if not values:
        return WindowedHistogram.EMPTY_WINDOW
    ordered = sorted(values)
    rank = math.ceil(quantile * len(ordered))
    index = min(max(rank, 1), len(ordered)) - 1
    return float(ordered[index])


class _Clock:
    """An injected clock whose reading the test sets explicitly before each observe/read."""

    __slots__ = ("now",)

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_property_4_windowed_p99_tracks_the_injected_window() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        clock = _Clock()
        window_s = rng.choice([1.0, 5.0, 60.0, 300.0])
        hist = WindowedHistogram.from_contract(window_s=window_s, clock=clock)

        # "now" is the read time; the window is (now - window_s, now].
        now = rng.uniform(window_s, 10_000.0)

        # Build the full sample set first, each as (observed_at, ms). The histogram's prune()
        # assumes the deque is time-ordered (samples appended with a monotone-forward clock, as the
        # module documents), so we must observe in ascending observed_at order — exactly how the
        # serving loop records them. Both in-window and out-of-window samples are generated, then
        # sorted by observed_at, then observed in that order.
        pending: list[tuple[float, int]] = []
        in_window: list[int] = []
        # Samples strictly inside the window: age in [0, window_s) => observed_at >= cutoff.
        for _ in range(rng.randint(0, 40)):
            age = rng.uniform(0.0, window_s - 1e-6)
            ms = rng.randint(0, 2500)
            pending.append((now - age, ms))
            in_window.append(ms if ms > 0 else 0)
        # Out-of-window samples: observed strictly before the cutoff (now - window_s). These must
        # never influence the reported quantile (R4.6, Property 4).
        for _ in range(rng.randint(0, 20)):
            age = rng.uniform(window_s + 1e-3, window_s + 1000.0)
            pending.append((now - age, rng.randint(0, 2500)))

        for observed_at, ms in sorted(pending, key=lambda s: s[0]):
            clock.now = observed_at
            hist.observe(ms)

        # Read at "now".
        clock.now = now
        server_p99 = hist.p99()
        server_p50 = hist.p50()

        direct_p99 = _nearest_rank(in_window, 0.99)
        direct_p50 = _nearest_rank(in_window, 0.50)

        if not in_window:
            # Empty window: EMPTY_WINDOW, never < 0 (R4.7 / R13.4).
            assert server_p99 == WindowedHistogram.EMPTY_WINDOW, _ctx(
                _SEED, i, note="empty p99 not EMPTY_WINDOW", got=server_p99
            )
            assert server_p50 == WindowedHistogram.EMPTY_WINDOW, _ctx(
                _SEED, i, note="empty p50 not EMPTY_WINDOW", got=server_p50
            )
            assert server_p99 >= 0.0 and server_p50 >= 0.0, _ctx(
                _SEED, i, note="empty quantile negative"
            )
            continue

        # Within tolerance and never under-reads by more than the tolerance (R4.5).
        assert abs(server_p99 - direct_p99) <= _TOLERANCE_MS, _ctx(
            _SEED, i, note="p99 out of tolerance", server=server_p99, direct=direct_p99
        )
        assert server_p99 >= direct_p99 - _TOLERANCE_MS, _ctx(
            _SEED, i, note="p99 under-reads", server=server_p99, direct=direct_p99
        )
        assert abs(server_p50 - direct_p50) <= _TOLERANCE_MS, _ctx(
            _SEED, i, note="p50 out of tolerance", server=server_p50, direct=direct_p50
        )
        # Never negative.
        assert server_p99 >= 0.0 and server_p50 >= 0.0, _ctx(_SEED, i, note="negative quantile")

        # The in-window count equals exactly the in-window sample set (out-of-window pruned).
        assert hist.count() == len(in_window), _ctx(
            _SEED, i, note="count includes out-of-window", got=hist.count(), want=len(in_window)
        )

        # The terminal +Inf bucket equals the in-window count (R4.4).
        buckets = hist.buckets()
        assert buckets[-1] == ('le="+Inf"', len(in_window)), _ctx(
            _SEED, i, note="+Inf bucket != in-window count", terminal=buckets[-1]
        )
