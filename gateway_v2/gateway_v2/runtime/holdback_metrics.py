"""Holdback metrics producers — a FIXED, label-free series set (R2-06 / GW12b, R5).

This module is a PRODUCER only. It follows the gateway_v2 producer-vs-publisher split
established by ``audit/metrics.py`` and ``runtime/state_metrics.py``: it emits a fixed set
of label-free series with no tenant-derived dimension, and a separate publisher (GW14d)
owns ``# TYPE`` exposition and fleet-wide publication. The same two rules inherited there
apply here, for the same measured reasons:

* **No tenant-derived label, anywhere.** R2-10 measured a full shared-memory metric
  directory making 40% of new tenants' first requests return HTTP 500 after one series was
  exported per org. The held-tokens histogram and the latency series carry no ``org`` label;
  "which stream held the most" is a query the trace answers, not a metric.
* **Counters use real zeros, never absence.** ``incomplete_streams_total``,
  ``overflow_total`` and ``forced_release_total`` exist from the first snapshot as ``0`` so
  "no incompletes" and "no series" cannot look the same to an alarm.

Recording is O(1) per observation and reads nothing: ``observe_stream_complete`` and
``observe_stream_incomplete`` append a single sample / bump a counter on the hot path, and
only ``snapshot`` builds a reading. The histogram percentiles (nearest-rank p50/p99) are
computed on READ, inside ``snapshot``, never during an observation.

The ``stats`` argument is structurally typed (``StreamStatsLike``): a minimal Protocol with
exactly the fields a reading needs. It is declared HERE in runtime because ``runtime`` sits
below ``egress`` in the import-linter layer contract, so this module must NOT import
``gateway_v2.egress``. The ``egress.StreamStats`` dataclass that task 7 produces
structurally satisfies ``StreamStatsLike`` (``max_held_tokens``, ``release_processing_ns``,
``holdback_wait_ns``, ``max_unbroken_run_bytes``) and is passed in directly. Latency fields
arrive in nanoseconds and are converted to milliseconds (1 ms resolution per R5.3) when
recorded into the latency histograms.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

__all__ = (
    "PREFIX",
    "Histogram",
    "HoldbackMetrics",
    "HoldbackReading",
    "StreamStatsLike",
)

PREFIX = "amf_holdback"

_NS_PER_MS = 1_000_000
"""Nanoseconds per millisecond. Latency samples arrive in ns and are published in ms (R5.3)."""


class StreamStatsLike(Protocol):
    """The minimal per-stream reading the producer needs from the pipeline.

    Declared structurally rather than imported: ``egress`` sits ABOVE ``runtime`` in the
    layer contract, so importing ``egress.StreamStats`` here would invert the dependency.
    The egress ``StreamStats`` dataclass satisfies this as-is, and the import-linter contract
    stays intact. Latency fields are nanoseconds (converted to ms on record).
    """

    @property
    def max_held_tokens(self) -> int:
        ...

    @property
    def release_processing_ns(self) -> int:
        ...

    @property
    def holdback_wait_ns(self) -> int:
        ...

    @property
    def max_unbroken_run_bytes(self) -> int:
        ...


def _ns_to_ms(nanos: int) -> int:
    """Convert nanoseconds to whole milliseconds (1 ms resolution per R5.3), rounded.

    Rounds to the nearest millisecond so a 1 ms-resolution series does not systematically
    under-report sub-millisecond processing by truncating it to zero.
    """
    if nanos <= 0:
        return 0
    return (nanos + _NS_PER_MS // 2) // _NS_PER_MS


@dataclass(slots=True)
class Histogram:
    """A minimal integer-sample histogram with nearest-rank percentiles computed on read.

    ``observe`` is O(1): it appends one integer sample and bumps the running sum. Percentiles
    are computed only when asked (``p50`` / ``p99``), by sorting a copy of the samples and
    taking the nearest-rank element — never on the observation path. Empty is a real state:
    ``p50``/``p99`` of no samples is ``0`` and ``count`` is ``0``, so the series exists from
    the first snapshot.
    """

    samples: list[int] = field(default_factory=list)
    total: int = 0

    def observe(self, value: int) -> None:
        """Record one integer sample. O(1)."""
        self.samples.append(value)
        self.total += value

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def maximum(self) -> int:
        return max(self.samples) if self.samples else 0

    def _nearest_rank(self, quantile: float) -> int:
        if not self.samples:
            return 0
        ordered = sorted(self.samples)
        # Nearest-rank: rank = ceil(q * N), clamped to [1, N], 1-indexed.
        rank = math.ceil(quantile * len(ordered))
        index = min(max(rank, 1), len(ordered)) - 1
        return ordered[index]

    def p50(self) -> int:
        return self._nearest_rank(0.50)

    def p99(self) -> int:
        return self._nearest_rank(0.99)


@dataclass(frozen=True, slots=True)
class HoldbackReading:
    """A full reading of the holdback producer. Every latency/token series is label-free.

    ``held_tokens_hist`` carries one integer sample per observed stream (R5.1).
    ``release_processing_ms`` and ``holdback_wait_ms`` are TWO SEPARATE series (R5.3): the
    gateway-compute time and the upstream-disambiguation wait are never collapsed into one.
    ``unbroken_run_bytes_max`` is the largest unbroken run held across all streams (R5.5).
    The three ``*_total`` counters are real zeros from the first snapshot.
    """

    held_tokens_hist: Histogram
    release_processing_ms: Histogram
    holdback_wait_ms: Histogram
    unbroken_run_bytes_max: int
    incomplete_streams_total: int
    overflow_total: int
    forced_release_total: int


class HoldbackMetrics:
    """Producer for the holdback series. O(1) per observation; reads nothing on the hot path.

    ``observe_stream_complete`` and ``observe_stream_incomplete`` each record exactly one
    held-tokens sample plus the two latency samples and update the running byte ceiling; the
    incomplete path additionally increments ``incomplete_streams_total`` so an abnormally
    terminated stream's sample is distinguishable from a clean completion (R5.2). ``snapshot``
    is the only reader and builds a frozen ``HoldbackReading``.
    """

    def __init__(self) -> None:
        self._held_tokens = Histogram()
        self._release_processing_ms = Histogram()
        self._holdback_wait_ms = Histogram()
        self._unbroken_run_bytes_max = 0
        self._incomplete_streams_total = 0
        self._overflow_total = 0
        self._forced_release_total = 0

    def _record(self, stats: StreamStatsLike) -> None:
        """Record the held-tokens sample, the two latency samples, and the byte ceiling. O(1)."""
        self._held_tokens.observe(stats.max_held_tokens)
        self._release_processing_ms.observe(_ns_to_ms(stats.release_processing_ns))
        self._holdback_wait_ms.observe(_ns_to_ms(stats.holdback_wait_ns))
        self._unbroken_run_bytes_max = max(
            self._unbroken_run_bytes_max, stats.max_unbroken_run_bytes
        )

    def observe_stream_complete(self, stats: StreamStatsLike) -> None:
        """Record a cleanly completed stream: one held-tokens sample + latency (R5.1, R5.3)."""
        self._record(stats)

    def observe_stream_incomplete(self, stats: StreamStatsLike) -> None:
        """Record an abnormally terminated stream.

        Records the max held tokens observed up to termination AND increments
        ``incomplete_streams_total`` so the sample is distinguishable from a clean
        completion (R5.2).
        """
        self._record(stats)
        self._incomplete_streams_total += 1

    def observe_overflow(self) -> None:
        """A relaxed-class hold reached the ``2 * hold_cap`` ceiling. O(1)."""
        self._overflow_total += 1

    def observe_forced_release(self) -> None:
        """A word-class hold hit the token cap and force-released its oldest tokens. O(1)."""
        self._forced_release_total += 1

    def snapshot(self) -> HoldbackReading:
        """Build the current reading. The only method that reads the accumulated state."""
        return HoldbackReading(
            held_tokens_hist=self._held_tokens,
            release_processing_ms=self._release_processing_ms,
            holdback_wait_ms=self._holdback_wait_ms,
            unbroken_run_bytes_max=self._unbroken_run_bytes_max,
            incomplete_streams_total=self._incomplete_streams_total,
            overflow_total=self._overflow_total,
            forced_release_total=self._forced_release_total,
        )
