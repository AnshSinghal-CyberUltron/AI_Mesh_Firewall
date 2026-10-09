"""LGW12b holdback metrics producer tests (R2-06 / GW12b, R5.1, R5.2, R5.3, R5.4, R5.5).

These exercise the label-free producer in ``runtime/holdback_metrics.py``: one histogram
sample per observed stream, the incomplete-stream flag + max-held sample, the two latency
series being distinct, the nearest-rank p50/p99 bound over >=1,000 word-class samples
(R5.4), and the nanosecond-to-millisecond conversion (R5.3).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from gateway_v2.runtime.holdback_metrics import (
    PREFIX,
    HoldbackMetrics,
    HoldbackReading,
    StreamStatsLike,
)


@dataclass(frozen=True, slots=True)
class _Stats:
    """A minimal structural stand-in for ``egress.StreamStats`` (latency in ns)."""

    max_held_tokens: int = 0
    release_processing_ns: int = 0
    holdback_wait_ns: int = 0
    max_unbroken_run_bytes: int = 0


def test_stats_stub_satisfies_protocol() -> None:
    # Structural check: the stub is a StreamStatsLike, mirroring egress.StreamStats.
    stats: StreamStatsLike = _Stats()
    assert stats.max_held_tokens == 0


def test_prefix_is_label_free_family() -> None:
    assert PREFIX == "amf_holdback"


def test_one_histogram_sample_per_completed_stream() -> None:
    metrics = HoldbackMetrics()
    for held in (1, 2, 3, 2):
        metrics.observe_stream_complete(_Stats(max_held_tokens=held))
    reading = metrics.snapshot()
    assert isinstance(reading, HoldbackReading)
    # Exactly one sample recorded per completed stream.
    assert reading.held_tokens_hist.count == 4
    assert sorted(reading.held_tokens_hist.samples) == [1, 2, 2, 3]
    # A clean completion must not touch the incomplete counter.
    assert reading.incomplete_streams_total == 0


def test_counters_are_real_zeros_on_fresh_snapshot() -> None:
    reading = HoldbackMetrics().snapshot()
    assert reading.incomplete_streams_total == 0
    assert reading.overflow_total == 0
    assert reading.forced_release_total == 0
    assert reading.unbroken_run_bytes_max == 0
    assert reading.held_tokens_hist.count == 0
    # p50/p99 of an empty histogram are a real 0, not an error.
    assert reading.held_tokens_hist.p50() == 0
    assert reading.held_tokens_hist.p99() == 0


def test_incomplete_stream_increments_counter_and_records_max_held() -> None:
    metrics = HoldbackMetrics()
    metrics.observe_stream_complete(_Stats(max_held_tokens=2))
    metrics.observe_stream_incomplete(_Stats(max_held_tokens=5))
    reading = metrics.snapshot()
    # The incomplete stream still contributes its max-held sample (R5.2) ...
    assert reading.held_tokens_hist.count == 2
    assert 5 in reading.held_tokens_hist.samples
    # ... and is distinguishable via the incomplete counter.
    assert reading.incomplete_streams_total == 1


def test_release_processing_and_holdback_wait_are_distinct_series() -> None:
    metrics = HoldbackMetrics()
    # 3 ms processing, 7 ms wait (in ns): the two series must not share a value.
    metrics.observe_stream_complete(
        _Stats(release_processing_ns=3_000_000, holdback_wait_ns=7_000_000)
    )
    reading = metrics.snapshot()
    assert reading.release_processing_ms is not reading.holdback_wait_ms
    assert reading.release_processing_ms.samples == [3]
    assert reading.holdback_wait_ms.samples == [7]
    assert reading.release_processing_ms.samples != reading.holdback_wait_ms.samples


def test_ns_to_ms_conversion_rounds_to_nearest() -> None:
    metrics = HoldbackMetrics()
    # 0 ns -> 0 ms; 1_400_000 ns -> 1 ms; 1_600_000 ns -> 2 ms; 20_000_000 ns -> 20 ms.
    metrics.observe_stream_complete(_Stats(release_processing_ns=0, holdback_wait_ns=1_400_000))
    metrics.observe_stream_complete(
        _Stats(release_processing_ns=1_600_000, holdback_wait_ns=20_000_000)
    )
    reading = metrics.snapshot()
    assert reading.release_processing_ms.samples == [0, 2]
    assert reading.holdback_wait_ms.samples == [1, 20]


def test_unbroken_run_bytes_max_is_running_maximum() -> None:
    metrics = HoldbackMetrics()
    for run in (10, 4096, 128, 2048):
        metrics.observe_stream_complete(_Stats(max_unbroken_run_bytes=run))
    assert metrics.snapshot().unbroken_run_bytes_max == 4096


def test_overflow_and_forced_release_counters_increment() -> None:
    metrics = HoldbackMetrics()
    metrics.observe_overflow()
    metrics.observe_forced_release()
    metrics.observe_forced_release()
    reading = metrics.snapshot()
    assert reading.overflow_total == 1
    assert reading.forced_release_total == 2


def test_word_class_percentiles_p50_le_2_p99_le_3_over_1000_streams() -> None:
    # R5.4: for word-class streams the held-tokens histogram must report p50 <= 2 and
    # p99 <= 3 over a sample window of at least 1,000 completed streams. Feed >= 1,000
    # synthetic word-class samples drawn from {1, 2, 3} with a realistic skew toward the
    # cap floor (most streams hold 1-2 tokens, a tail holds the full 3).
    seed = 12
    rng = random.Random(seed)
    metrics = HoldbackMetrics()
    n = 1_200
    for _ in range(n):
        # 70% hold 1, 25% hold 2, 5% hold 3: p50 lands at 1-2, p99 at 3.
        roll = rng.random()
        if roll < 0.70:
            held = 1
        elif roll < 0.95:
            held = 2
        else:
            held = 3
        metrics.observe_stream_complete(_Stats(max_held_tokens=held))
    hist = metrics.snapshot().held_tokens_hist
    assert hist.count >= 1_000, f"seed={seed}"
    assert hist.maximum <= 3, f"seed={seed} max={hist.maximum}"
    assert hist.p50() <= 2, f"seed={seed} p50={hist.p50()}"
    assert hist.p99() <= 3, f"seed={seed} p99={hist.p99()}"


def test_nearest_rank_percentiles_on_known_distribution() -> None:
    # Deterministic nearest-rank check: 100 samples, values 1..100 in order.
    metrics = HoldbackMetrics()
    for value in range(1, 101):
        metrics.observe_stream_complete(_Stats(max_held_tokens=value))
    hist = metrics.snapshot().held_tokens_hist
    # nearest-rank: p50 -> ceil(0.5*100)=50th element = 50; p99 -> ceil(0.99*100)=99th = 99.
    assert hist.p50() == 50
    assert hist.p99() == 99
    assert hist.maximum == 100
