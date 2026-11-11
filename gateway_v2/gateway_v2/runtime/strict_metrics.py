"""STRICT_WITHHOLD latency producer — a FIXED, label-free series set (GW13, C7 / R9).

This module is a PRODUCER only. It follows the gateway_v2 producer-vs-publisher split
established by ``audit/metrics.py``, ``runtime/state_metrics.py`` and the sibling
``runtime/holdback_metrics.py``: it emits a fixed set of label-free series with no
tenant-derived dimension, and a separate publisher (GW14d) owns ``# TYPE`` exposition and
fleet-wide publication. The same two rules inherited there apply here, for the same measured
reasons:

* **No tenant-derived label, anywhere.** R2-10 measured a full shared-memory metric
  directory making 40% of new tenants' first requests return HTTP 500 after one series was
  exported per org. The completion-overhead histogram and the counters carry no ``org``
  label; "which stream took longest" is a question the trace answers, not a metric.
* **Counters use real zeros, never absence.** ``strict_streams_total``,
  ``incomplete_streams_total``, ``overflow_total`` and ``blocked_total`` exist from the first
  snapshot as ``0`` so "no incompletes" and "no series" cannot look the same to an alarm.

STRICT is a DISTINCT latency profile from INCREMENTAL and never inherits the INCREMENTAL
time-to-first-token claim (R9.2). This producer therefore records **completion overhead
only** — the single ``completion_overhead_ms`` histogram family, measured as the clock at the
``final`` chunk through the clock when the whole sanitized response is emitted. It carries NO
``T_release_processing``, NO ``T_holdback_wait`` and NO time-to-first-token series: those are
the INCREMENTAL C4 split, owned by ``HoldbackMetrics`` and left unchanged (R9.3). The STRICT
profile is thus labelled distinctly from the INCREMENTAL release-lag / first-content-delay
profile (R9.1, R9.4).

Recording is O(1) per observation and reads nothing: ``observe_stream_complete``,
``observe_stream_incomplete``, ``observe_overflow`` and ``observe_blocked`` each append a
single sample / bump a counter on the hot path, and only ``snapshot`` builds a reading. The
histogram percentiles (nearest-rank p50/p99) are computed on READ, inside ``snapshot``, never
during an observation.

The ``stats`` argument is structurally typed (``StrictStatsLike``): a minimal Protocol with
exactly the fields a reading needs. It is declared HERE in runtime because ``runtime`` sits
below ``egress`` in the import-linter layer contract, so this module must NOT import
``gateway_v2.egress``. The ``egress.StrictStats`` dataclass that task 5 produces structurally
satisfies ``StrictStatsLike`` (``completion_overhead_ns``) and is passed in directly. The
latency field arrives in nanoseconds and is converted to milliseconds (1 ms resolution) when
recorded into the histogram.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

__all__ = (
    "PREFIX",
    "SERIES_COUNT",
    "Histogram",
    "StrictMetrics",
    "StrictReading",
    "StrictStatsLike",
)

PREFIX = "amf_strict"

_NS_PER_MS = 1_000_000
"""Nanoseconds per millisecond. Latency samples arrive in ns and are published in ms."""


class StrictStatsLike(Protocol):
    """The minimal per-stream reading the producer needs from the STRICT pipeline.

    Declared structurally rather than imported: ``egress`` sits ABOVE ``runtime`` in the
    layer contract, so importing ``egress.StrictStats`` here would invert the dependency.
    The egress ``StrictStats`` dataclass satisfies this as-is, and the import-linter contract
    stays intact. ``completion_overhead_ns`` is nanoseconds (converted to ms on record).
    """

    @property
    def completion_overhead_ns(self) -> int:
        ...


def _ns_to_ms(nanos: int) -> int:
    """Convert nanoseconds to whole milliseconds (1 ms resolution), rounded.

    Rounds to the nearest millisecond so a 1 ms-resolution series does not systematically
    under-report sub-millisecond completion overhead by truncating it to zero.
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
class StrictReading:
    """A full reading of the STRICT producer. The one latency series is label-free.

    ``completion_overhead_ms`` carries one integer sample per observed stream — the whole
    STRICT latency profile (R9.4). There is deliberately NO release-processing, holdback-wait
    or time-to-first-token series here: STRICT inspects the whole buffer once and never claims
    a time-to-first-token (R9.2). The four ``*_total`` counters are real zeros from the first
    snapshot: ``strict_streams_total`` counts every observed stream, and the incomplete /
    overflow / block counters distinguish an abnormally terminated stream from a clean one.
    """

    completion_overhead_ms: Histogram
    strict_streams_total: int
    incomplete_streams_total: int
    overflow_total: int
    blocked_total: int


class StrictMetrics:
    """Producer for the STRICT series. O(1) per observation; reads nothing on the hot path.

    ``observe_stream_complete`` and ``observe_stream_incomplete`` each record exactly one
    completion-overhead sample and bump ``strict_streams_total``; the incomplete path
    additionally increments ``incomplete_streams_total`` so an abnormally terminated stream's
    sample is distinguishable from a clean completion. ``observe_overflow`` and
    ``observe_blocked`` are O(1) counter bumps for the two fail-closed terminal outcomes.
    ``snapshot`` is the only reader and builds a frozen ``StrictReading``.
    """

    def __init__(self) -> None:
        self._completion_overhead_ms = Histogram()
        self._strict_streams_total = 0
        self._incomplete_streams_total = 0
        self._overflow_total = 0
        self._blocked_total = 0

    def _record(self, stats: StrictStatsLike) -> None:
        """Record the completion-overhead sample and bump the stream total. O(1)."""
        self._completion_overhead_ms.observe(_ns_to_ms(stats.completion_overhead_ns))
        self._strict_streams_total += 1

    def observe_stream_complete(self, stats: StrictStatsLike) -> None:
        """Record a cleanly completed STRICT stream: one completion-overhead sample (R9.4)."""
        self._record(stats)

    def observe_stream_incomplete(self, stats: StrictStatsLike) -> None:
        """Record an abnormally terminated STRICT stream.

        Records the completion overhead observed up to termination AND increments
        ``incomplete_streams_total`` so the sample is distinguishable from a clean completion.
        """
        self._record(stats)
        self._incomplete_streams_total += 1

    def observe_overflow(self) -> None:
        """A STRICT buffer exceeded ``ResourceContract.stream_buffer_bytes``. O(1)."""
        self._overflow_total += 1

    def observe_blocked(self) -> None:
        """A whole STRICT response resolved to BLOCK (zero content emitted). O(1)."""
        self._blocked_total += 1

    def snapshot(self) -> StrictReading:
        """Build the current reading. The only method that reads the accumulated state."""
        return StrictReading(
            completion_overhead_ms=self._completion_overhead_ms,
            strict_streams_total=self._strict_streams_total,
            incomplete_streams_total=self._incomplete_streams_total,
            overflow_total=self._overflow_total,
            blocked_total=self._blocked_total,
        )


SERIES_COUNT = 4 + 1 * 3
"""How many series this surface can ever produce. A constant, by construction.

Four counters + one histogram family. The counters are ``strict_streams_total``,
``incomplete_streams_total``, ``overflow_total`` and ``blocked_total``. The ``1 * 3`` is the
single ``Histogram`` family — ``completion_overhead_ms`` — which the GW14d Publisher renders
as three native Prometheus series (``_bucket`` + ``_sum`` + ``_count``), matching the
SERIES_COUNT composition in the observability-contract design and the audit/state/holdback
producer convention. There is NO release-processing / holdback-wait / time-to-first-token
family: STRICT does not inherit the INCREMENTAL TTFT claim (R9.2), so the one latency family
is completion overhead and nothing else.

R2-10 is the reason this is a fixed expression rather than ``len(...)`` of whatever happens to
be emitted: a full shared-memory metric directory made 40% of new tenants' first requests
return HTTP 500 after per-org series were exported, so a series whose existence depends on the
estate must fail a gate instead of shipping. The GW14d test asserts the rendered family count
agrees with this constant.
"""
