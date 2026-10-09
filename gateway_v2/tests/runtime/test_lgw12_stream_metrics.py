"""LGW12 task 13.3 — unit tests for the per-request export surface (GW12 / sse-egress-pipeline).

# Validates: Requirements 12.1, 12.2, 12.3, 12.4

These exercise the producer-only, label-free per-request export surface in
``runtime/stream_metrics.py`` (:class:`PerRequestExports` / :class:`PerRequestReading` /
``PREFIX``). They mirror the HoldbackMetrics-split discipline the LGW12b producer tests
(``test_lgw12b_metrics.py``) assert, applied to the GW12 per-request series:

* **Zeros-not-absence (R12.3):** every scalar is a real ``0`` and both histograms are empty
  (``count == 0``) from the FIRST snapshot, so "no invocations / no withholdings / no
  fail-opens" and "no series" cannot look the same to an alarm.
* **Label-free (R12.3):** no ``observe_*`` / ``record_*`` method accepts an owner/org/tenant
  parameter — checked structurally via ``inspect.signature`` so no per-tenant series can ever
  be created — and ``PREFIX`` is the fixed ``amf_stream`` family.
* **Detector-count matches the schedule (R12.2):** ``detector_invocations`` is a plain running
  count, so N invocations snapshot to exactly N.
* **Withholding, never fail-open (R12.4 / R15.3):** ``record_withheld`` bumps ``withheld_total``
  ONLY and leaves ``fail_open_total`` pinned at ``0``.

These are supporting unit tests (the design's Testing Strategy), not property tests: a handful
of concrete observations and the structural label-free check. No ``hypothesis`` and no
≥10,000-iteration loop; the producer is pure and synchronous, so no event loop.
"""

from __future__ import annotations

import inspect

from gateway_v2.runtime.holdback_metrics import Histogram
from gateway_v2.runtime.stream_metrics import (
    PREFIX,
    PerRequestExports,
    PerRequestReading,
)

# The observation/recording surface the label-free check must sweep. Every method that lets a
# caller feed the producer is listed here so a newly added mutator cannot sneak a tenant label
# past the inspect-signature assertion below.
_MUTATOR_NAMES: tuple[str, ...] = (
    "observe_detector_invocation",
    "observe_release_lag_ns",
    "observe_buffer_high_water",
    "observe_memory_bound",
    "observe_loop_lag_ns",
    "record_withheld",
)

# Any parameter name that would introduce a tenant-derived dimension (R12.3). The producer must
# expose NONE of these on any mutator.
_TENANT_PARAM_NAMES: frozenset[str] = frozenset(
    {"owner", "org", "org_id", "org_slug", "tenant", "tenant_id", "label", "labels"}
)


def test_fresh_snapshot_is_all_real_zeros_not_absence() -> None:
    """A fresh producer snapshots every series as a real zero, present from the start (R12.3)."""
    reading = PerRequestExports().snapshot()

    assert isinstance(reading, PerRequestReading)
    # Scalar counters: real zeros, never an absent series.
    assert reading.detector_invocations == 0
    assert reading.buffer_high_water == 0
    assert reading.active_stream_memory_bound == 0
    assert reading.withheld_total == 0
    assert reading.fail_open_total == 0
    # Both latency histograms exist and are empty (count 0) — not missing.
    assert isinstance(reading.release_lag_ms, Histogram)
    assert isinstance(reading.loop_lag_ms, Histogram)
    assert reading.release_lag_ms.count == 0
    assert reading.loop_lag_ms.count == 0
    # p50/p99 of an empty histogram are a real 0, not an error (percentile-on-read).
    assert reading.release_lag_ms.p50() == 0
    assert reading.release_lag_ms.p99() == 0
    assert reading.loop_lag_ms.p50() == 0
    assert reading.loop_lag_ms.p99() == 0


def test_prefix_is_the_label_free_stream_family() -> None:
    """The series family is the fixed, label-free ``amf_stream`` prefix (R12.3)."""
    assert PREFIX == "amf_stream"


def test_no_mutator_accepts_a_tenant_label() -> None:
    """No observe_*/record_* method accepts an owner/org/tenant parameter (R12.3).

    Checked structurally via ``inspect.signature`` rather than by convention: if a mutator ever
    grew a tenant-derived parameter, a per-tenant series could be created and this test fails.
    """
    for name in _MUTATOR_NAMES:
        method = getattr(PerRequestExports, name)
        signature = inspect.signature(method)
        params = set(signature.parameters) - {"self"}
        offending = params & _TENANT_PARAM_NAMES
        assert not offending, f"{name} accepts tenant-derived parameter(s): {sorted(offending)}"


def test_detector_invocations_matches_the_invocation_count() -> None:
    """``detector_invocations`` is a plain running count: N calls snapshot to exactly N (R12.2).

    A plain count is what lets the export be asserted equal to the plan-derived detector schedule
    for the request — the producer counts and makes no judgement about the expected schedule.
    """
    exports = PerRequestExports()
    n = 7
    for _ in range(n):
        exports.observe_detector_invocation()
    assert exports.snapshot().detector_invocations == n


def test_release_lag_records_samples_and_rounds_ns_to_ms() -> None:
    """``observe_release_lag_ns`` appends one sample per call, converting ns→ms rounded (R12.1).

    1_400_000 ns rounds down to 1 ms, 1_600_000 ns rounds up to 2 ms, 20_000_000 ns is 20 ms,
    and a non-positive sample reads 0 ms — the same nearest-ms discipline as the holdback series.
    """
    exports = PerRequestExports()
    exports.observe_release_lag_ns(1_400_000)
    exports.observe_release_lag_ns(1_600_000)
    exports.observe_release_lag_ns(20_000_000)
    exports.observe_release_lag_ns(0)
    hist = exports.snapshot().release_lag_ms
    assert hist.count == 4
    assert hist.samples == [1, 2, 20, 0]


def test_loop_lag_records_samples_and_rounds_ns_to_ms() -> None:
    """``observe_loop_lag_ns`` appends one serving-loop lag sample per call, ns→ms rounded (R12.1).

    The loop-lag histogram is a SEPARATE series from release-lag: a sample recorded on one must
    not appear on the other.
    """
    exports = PerRequestExports()
    exports.observe_loop_lag_ns(500_000)  # 0.5 ms -> rounds to 1 ms (nearest).
    exports.observe_loop_lag_ns(2_000_000)  # 2 ms exactly.
    reading = exports.snapshot()
    assert reading.loop_lag_ms is not reading.release_lag_ms
    assert reading.loop_lag_ms.count == 2
    assert reading.loop_lag_ms.samples == [1, 2]
    # The release-lag series is untouched by loop-lag observations.
    assert reading.release_lag_ms.count == 0


def test_buffer_high_water_is_a_running_maximum() -> None:
    """``observe_buffer_high_water`` tracks the maximum buffered bytes, never decreasing (R12.1)."""
    exports = PerRequestExports()
    for nbytes in (10, 4096, 128, 2048):
        exports.observe_buffer_high_water(nbytes)
    # High-water is the max over the stream's lifetime, not the latest value.
    assert exports.snapshot().buffer_high_water == 4096


def test_memory_bound_overwrites_latest_wins() -> None:
    """``observe_memory_bound`` overwrites the exported Active_Streams bound — latest wins (R5.6).

    Unlike the high-water mark, the memory bound is a derived value re-reported whenever the
    Active_Streams count changes, so a lower later value replaces a higher earlier one.
    """
    exports = PerRequestExports()
    exports.observe_memory_bound(8192)
    exports.observe_memory_bound(4096)
    assert exports.snapshot().active_stream_memory_bound == 4096
    exports.observe_memory_bound(16384)
    assert exports.snapshot().active_stream_memory_bound == 16384


def test_record_withheld_increments_withheld_total_only() -> None:
    """``record_withheld`` bumps ``withheld_total`` ONLY; ``fail_open_total`` stays 0 (R12.4/R15.3).

    An uncomputable export is a WITHHOLDING (the fail-closed behaviour), never a fail-open — so
    the fail-open counter the No-FAIL_OPEN invariant reads must remain pinned at zero.
    """
    exports = PerRequestExports()
    exports.record_withheld()
    exports.record_withheld()
    reading = exports.snapshot()
    assert reading.withheld_total == 2
    assert reading.fail_open_total == 0


def test_snapshot_is_a_frozen_reading_that_does_not_drift() -> None:
    """``snapshot`` returns a frozen reading whose scalars don't change under later observations."""
    exports = PerRequestExports()
    exports.observe_detector_invocation()
    first = exports.snapshot()
    assert first.detector_invocations == 1

    # Later observations must not retroactively mutate an already-returned frozen reading's scalars.
    exports.observe_detector_invocation()
    exports.record_withheld()
    assert first.detector_invocations == 1
    assert first.withheld_total == 0

    second = exports.snapshot()
    assert second.detector_invocations == 2
    assert second.withheld_total == 1
