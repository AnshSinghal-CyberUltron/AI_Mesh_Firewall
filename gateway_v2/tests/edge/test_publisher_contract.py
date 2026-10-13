# Feature: observability-contract
# Validates: Requirements 1.1, 1.3, 11.2, 11.3, 11.4
"""Publisher exposition contract (task 11.4) — type str, gauge families, windowed-busy, isolation.

Example/unit tests complementing the Publisher property loops:

* ``exposition()`` returns a ``str`` (R1.1).
* a representative gauge family carries ``# TYPE <name> gauge`` so an autoscaler resolves it (11.2).
* the windowed-busy gauge emits ``# TYPE amf_windowed_busy gauge`` with NO sample supplied and no
  fabricated value (R11.3/R11.4).
* a producer that raises is OMITTED and ``publisher_producer_read_failures_total`` is incremented;
  the body is still complete and never raises to the caller (R1.3).
"""

from __future__ import annotations

from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from gateway_v2.edge.cardinality import CardinalityDirectory
from gateway_v2.edge.counters import PostureCounters, RejectionCounters
from gateway_v2.edge.event_log import StructuredEventLog
from gateway_v2.edge.publisher import (
    PRODUCER_READ_FAILURES_SERIES,
    WINDOWED_BUSY_SERIES,
    Publisher,
)
from gateway_v2.edge.rates import ResetSafeRate
from gateway_v2.edge.windowed import WindowedBusyGauge, WindowedHistogram
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.state_metrics import StateMetricsRecorder
from gateway_v2.runtime.stream_metrics import PerRequestExports
from state_control.metrics import RehydrationMetricsRecorder
from tests.edge._publisher_fixture import build_publisher, small_k_contract


def test_exposition_returns_str() -> None:
    pub = build_publisher(small_k_contract(), lambda: 0.0)
    body = pub.exposition()
    assert isinstance(body, str), "exposition() did not return a str"
    assert body.endswith("\n"), "exposition body is not newline-terminated"


def test_a_gauge_family_carries_type_gauge() -> None:
    """A representative gauge family is typed ``gauge`` so the HPA resolves it (R11.2)."""
    body = build_publisher(small_k_contract(), lambda: 0.0).exposition()
    # amf_windowed_busy is a non-_total family => gauge.
    assert f"# TYPE {WINDOWED_BUSY_SERIES} gauge" in body, (
        "the windowed-busy gauge family is not typed # TYPE ... gauge"
    )


def test_windowed_busy_emits_type_gauge_with_no_sample() -> None:
    """Windowed-busy emits ``# TYPE gauge`` + the empty value with no sample (R11.3/11.4)."""
    body = build_publisher(small_k_contract(), lambda: 0.0).exposition()
    assert f"# TYPE {WINDOWED_BUSY_SERIES} gauge" in body
    # The series line is present with the defined empty value (0.0 => rendered as 0), not absent and
    # not a fabricated reading.
    assert f"{WINDOWED_BUSY_SERIES} 0\n" in body, (
        "windowed-busy gauge is absent or carries a fabricated value with no sample"
    )


class _RaisingAudit:
    """An audit producer whose ``series()`` raises — exercises the per-producer isolation (R1.3)."""

    def series(self) -> dict[str, float]:
        raise RuntimeError("audit producer blew up")


def _publisher_with_raising_audit() -> Publisher:
    contract = small_k_contract()
    clock = lambda: 0.0  # noqa: E731
    window_s = contract.metrics_window_s()
    return Publisher(
        admission=AdmissionMetrics(),
        quota=QuotaMetrics(),
        audit=_RaisingAudit(),  # type: ignore[arg-type]
        rehydration=RehydrationMetricsRecorder().snapshot(),
        state=StateMetricsRecorder().snapshot(),
        stream=PerRequestExports(),
        holdback=HoldbackMetrics(),
        directory=CardinalityDirectory.from_contract(contract),
        addon=WindowedHistogram.from_contract(window_s=window_s, clock=clock),
        busy=WindowedBusyGauge.from_contract(window_s=window_s, clock=clock),
        rates=ResetSafeRate(),
        rejections=RejectionCounters(),
        postures=PostureCounters(),
        event_log=StructuredEventLog.from_contract(contract, clock=clock),
        contract=contract,
        clock=clock,
    )


def test_raising_producer_is_omitted_and_counted() -> None:
    """A raising producer is omitted + the failure counter increments; body complete (R1.3)."""
    pub = _publisher_with_raising_audit()
    # Never raises to the caller.
    body = pub.exposition()
    assert isinstance(body, str)

    # The read-failure indicator reflects the one failed producer.
    failure_line = f"{PRODUCER_READ_FAILURES_SERIES} 1\n"
    assert failure_line in body, (
        f"a raising producer did not bump {PRODUCER_READ_FAILURES_SERIES}: {body!r}"
    )

    # The body is still complete: other producers' families are present (e.g. the posture counters).
    assert "amf_posture_refusals_" in body, "surviving producers' families were dropped"
    # The failed audit producer's families are absent (its series start with amf_audit / amf_store).
    assert "amf_audit_" not in body, "the raising audit producer's families leaked into the body"
