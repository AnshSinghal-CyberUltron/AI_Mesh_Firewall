# Feature: observability-contract — shared Publisher fixture for the edge property/unit tests.
"""A tiny builder for a real :class:`gateway_v2.edge.publisher.Publisher` over injected producers.

NOT a test module (no ``test_`` prefix), so pytest does not collect it. The GW14d property and unit
tests import :func:`build_publisher` from here to assemble a Publisher from the shipped producers
with real zeros, under a contract whose derived K is caller-controlled (so the exposition-count
property can keep K small and fast while the real-zeros property can use the default). Test files
are not under the import-linter layer contract, so importing anything here is permitted.
"""

from __future__ import annotations

from collections.abc import Callable

from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from gateway_v2.audit.metrics import AuditMetrics
from gateway_v2.edge.event_log import StructuredEventLog
from gateway_v2.edge.publisher import Publisher
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.state_metrics import StateMetricsRecorder
from gateway_v2.runtime.stream_metrics import PerRequestExports
from state_control.metrics import RehydrationMetricsRecorder

_MIB = 1024 * 1024


def small_k_contract() -> ResourceContract:
    """A serviceable contract whose derived K is small (fast exposition for the count property).

    fd_limit=5 with one worker and a 0.75 utilization cap gives connection_budget=3 =>
    metrics_directory_slots()=12 => metrics_top_k()=3. The Top-K family therefore pads to only 3
    series, so ``exposition()`` renders a bounded body quickly for a 10k-iteration loop.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=1024 * _MIB * 8,
        fd_limit=5,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=64 * _MIB,
        worker_override=1,
    )


def default_contract() -> ResourceContract:
    """A representative larger contract (K in the thousands) — used where K size is irrelevant."""
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * _MIB * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 * _MIB,
    )


def build_publisher(
    contract: ResourceContract, clock: Callable[[], float]
) -> Publisher:
    """Assemble a Publisher over fresh, real-zero producers under ``contract`` + ``clock``."""
    state = StateMetricsRecorder().snapshot()
    rehydration = RehydrationMetricsRecorder().snapshot()
    event_log = StructuredEventLog.from_contract(contract, clock=clock)
    return Publisher.from_contract(
        admission=AdmissionMetrics(),
        quota=QuotaMetrics(),
        audit=AuditMetrics(),
        rehydration=rehydration,
        state=state,
        stream=PerRequestExports(),
        holdback=HoldbackMetrics(),
        event_log=event_log,
        contract=contract,
        clock=clock,
    )
