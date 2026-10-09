"""Per-request streaming export producer — a FIXED, label-free series set (GW12, R12).

This module is the GW12 per-request export surface promised in design Components §11. It is a
PRODUCER only and follows the gateway_v2 producer-vs-publisher split established by
``runtime/holdback_metrics.py`` (and ``admit/metrics.py`` / ``audit/metrics.py`` /
``runtime/state_metrics.py`` before it): it emits a fixed set of label-free series with no
tenant-derived dimension, and a separate publisher (GW14d) owns ``# TYPE`` exposition and
fleet-wide publication. The two rules inherited from the holdback producer apply here for the
same measured reasons:

* **No tenant-derived label, anywhere (R12.3).** R2-10 measured per-org series making 40% of new
  tenants' first requests return HTTP 500 once one series was exported per org. None of the series
  here carries an ``org`` / ``owner`` / tenant dimension — no method accepts such a parameter, so
  no per-tenant series can ever be created. "Which stream produced these" is a query the trace
  answers, not a metric.
* **Counters use real zeros, never absence (R12.3).** ``detector_invocations``, the release-lag
  histogram, ``buffer_high_water``, ``active_stream_memory_bound``, ``withheld_total`` and
  ``fail_open_total`` all exist from the first snapshot as ``0`` so "no invocations / no
  withholdings / no fail-opens" and "no series" cannot look the same to an alarm.

``fail_open_total`` in particular is pinned at ``0`` and there is NO path that increments it: GW12
fails closed everywhere (R15.1/R15.2) — an uncomputable export is recorded as a WITHHOLDING via
:meth:`PerRequestExports.record_withheld` and no raw bytes are forwarded to compensate (R12.4).
A non-zero ``fail_open_total`` is therefore a bug Property 7 (No-FAIL_OPEN, R15.3) asserts against;
the invariant test reads it through :meth:`PerRequestReading.fail_open_total`.

Recording is O(1) per observation and reads nothing on the hot path: ``observe_detector_invocation``
bumps a counter, ``observe_release_lag_ns`` records one latency sample (converted ns→ms), and
``observe_buffer_high_water`` / ``observe_memory_bound`` overwrite a running maximum / a value.
``snapshot`` is the only reader and builds a frozen :class:`PerRequestReading`; the release-lag
histogram percentiles (nearest-rank p50/p99) are computed on READ inside ``snapshot``, never during
an observation.

The release-lag distribution reuses :class:`gateway_v2.runtime.holdback_metrics.Histogram`
directly: this module sits in the SAME ``runtime`` layer, so importing it keeps the empty-is-zero /
percentile-on-read discipline identical to the holdback latency series instead of duplicating the
O(1)-observe / nearest-rank-on-read helper.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.runtime.holdback_metrics import Histogram

__all__ = (
    "PREFIX",
    "PerRequestExports",
    "PerRequestReading",
)

PREFIX = "amf_stream"
"""Prefix for the per-request streaming series (GW12 / R12). The fixed, LABEL-FREE series are
``amf_stream_detector_invocations``, ``amf_stream_release_lag_ms`` (histogram),
``amf_stream_buffer_high_water``, ``amf_stream_active_memory_bound``, ``amf_stream_withheld_total``
and ``amf_stream_fail_open_total`` — none tenant-derived (R12.3). It sits alongside the
``amf_quota`` STREAM/QUOTA producer prefixes used by the other label-free producers."""

_NS_PER_MS = 1_000_000
"""Nanoseconds per millisecond. Release-lag samples arrive in ns and are published in ms, mirroring
the 1 ms-resolution latency series in ``runtime/holdback_metrics.py``."""


def _ns_to_ms(nanos: int) -> int:
    """Convert nanoseconds to whole milliseconds, rounded, matching the holdback latency series.

    Rounds to the nearest millisecond so a 1 ms-resolution series does not systematically
    under-report sub-millisecond lag by truncating it to zero. A non-positive input reads ``0``.
    """
    if nanos <= 0:
        return 0
    return (nanos + _NS_PER_MS // 2) // _NS_PER_MS


@dataclass(frozen=True, slots=True)
class PerRequestReading:
    """A full reading of the per-request export producer. Every series is label-free (R12.3).

    ``detector_invocations`` is the count of output-detector invocations for the request; it is a
    plain running count so it can be asserted equal to the plan-derived detector schedule (R12.2).
    ``release_lag_ms`` is the upstream-read → downstream-send per-byte lag distribution (R12.1),
    carrying the holdback :class:`Histogram` percentile-on-read discipline. ``buffer_high_water`` is
    the maximum buffered bytes observed for the stream (R12.1).
    ``active_stream_memory_bound`` is the exported ``Active_Streams × stream_buffer_bytes``
    aggregate bound in bytes (R5.6), observed as a value whenever the Active_Streams count changes.

    ``withheld_total`` is the count of per-request exports that could not be computed and were
    recorded as a withholding (R12.4) — a real zero from the first snapshot. ``fail_open_total`` is
    pinned at ``0`` and no correct path increments it (R15.3); it is the series Property 7 reads.
    """

    detector_invocations: int
    release_lag_ms: Histogram
    buffer_high_water: int
    active_stream_memory_bound: int
    withheld_total: int
    fail_open_total: int


class PerRequestExports:
    """Producer for the per-request streaming series. O(1) per observation; reads nothing hot.

    ``observe_detector_invocation`` bumps the detector count; ``observe_release_lag_ns(nanos)``
    records one release-lag sample (converted ns→ms); ``observe_buffer_high_water(nbytes)`` raises
    the running buffer high-water maximum; ``observe_memory_bound(nbytes)`` overwrites the exported
    ``Active_Streams × stream_buffer_bytes`` bound (R5.6). ``record_withheld`` records that an
    export could not be computed — an uncomputable value is a WITHHOLDING, NEVER compensated with
    raw bytes (R12.4), and crucially it does NOT touch ``fail_open_total``. ``snapshot`` is the only
    reader and builds a frozen :class:`PerRequestReading`.

    Every counter is seeded to ``0`` at construction (real zeros, never absence — R12.3), including
    ``fail_open_total``. ``fail_open_total`` has NO incrementing method in correct operation: GW12
    fails closed (R15.1/R15.2) by withholding, so a non-zero value is a bug the No-FAIL_OPEN
    invariant test asserts against (R15.3). No method accepts an ``owner`` / ``org`` / ``tenant``
    parameter, so the label set stays fixed and finite (R12.3).
    """

    def __init__(self) -> None:
        # Real zeros from the first snapshot (R12.3): "no invocations / no withholdings / no
        # fail-opens / bound 0" is a reported value, never an absent series.
        self._detector_invocations = 0
        self._release_lag_ms = Histogram()
        self._buffer_high_water = 0
        self._active_stream_memory_bound = 0
        self._withheld_total = 0
        self._fail_open_total = 0

    def observe_detector_invocation(self) -> None:
        """Record one output-detector invocation. O(1).

        A plain count so the snapshot's ``detector_invocations`` can be asserted equal to the
        plan-derived detector schedule for the request (R12.2) — the producer just counts
        invocations and makes no judgement about the expected schedule.
        """
        self._detector_invocations += 1

    def observe_release_lag_ns(self, nanos: int) -> None:
        """Record one release-lag sample in nanoseconds (upstream-read → downstream-send). O(1).

        The sample is converted to whole milliseconds and appended to the release-lag histogram
        (R12.1); percentiles are computed on read in :meth:`snapshot`, never here.
        """
        self._release_lag_ms.observe(_ns_to_ms(nanos))

    def observe_buffer_high_water(self, nbytes: int) -> None:
        """Raise the running buffer high-water mark to at least ``nbytes``. O(1).

        Tracks the maximum buffered bytes observed for the stream (R12.1): the high-water never
        decreases within a stream's lifetime, so this takes the max of the current value and the
        newly observed buffered-byte count.
        """
        self._buffer_high_water = max(self._buffer_high_water, nbytes)

    def observe_memory_bound(self, nbytes: int) -> None:
        """Overwrite the exported ``Active_Streams × stream_buffer_bytes`` memory bound. O(1).

        Observed as a value in bytes whenever the Active_Streams count changes (R5.6): the latest
        derived aggregate bound wins. The caller (the coalescer) computes
        ``active_streams × stream_buffer_bytes`` from the :class:`ResourceContract`; this producer
        only reports the value and never holds a capacity literal of its own.
        """
        self._active_stream_memory_bound = nbytes

    def record_withheld(self) -> None:
        """Record that a per-request export could not be computed — a withholding (R12.4). O(1).

        An uncomputable export is recorded here and NEVER compensated with raw stream bytes
        (R12.4). This increments ``withheld_total`` ONLY; it deliberately does not touch
        ``fail_open_total``, which stays pinned at ``0`` (R15.3) — withholding IS the fail-closed
        behaviour, so it is the opposite of a fail-open.
        """
        self._withheld_total += 1

    def snapshot(self) -> PerRequestReading:
        """Build the current reading — the only method that reads the accumulated state.

        Returns a frozen :class:`PerRequestReading` carrying every fixed series. The release-lag
        histogram is handed over directly (its percentiles are computed on read); the scalar
        counters are plain values that cannot drift under later observations once the frozen
        reading is returned.
        """
        return PerRequestReading(
            detector_invocations=self._detector_invocations,
            release_lag_ms=self._release_lag_ms,
            buffer_high_water=self._buffer_high_water,
            active_stream_memory_bound=self._active_stream_memory_bound,
            withheld_total=self._withheld_total,
            fail_open_total=self._fail_open_total,
        )
