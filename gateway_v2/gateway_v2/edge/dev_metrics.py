"""DEV PROFILE ``/metrics``: the GW14d Publisher over the producers this process writes to.

Every producer handed to the Publisher is the instance the request path writes to: the
admission controller's, the budget lease's, the audit sink's live counters, the one shared
stream-exports producer, and the one shared holdback producer. The Publisher is also the
request-path observer for tenants, rejections and postures.

The dev process runs no state writer and no re-hydrator, so those two producers are their
never-observed readings: freshness and last-success ages are the ``ABSENT`` sentinel (-1),
never a value that looks healthy. No GPU source exists, so windowed-busy has no sample, and
nothing measures the C4 add-on latency yet, so that histogram stays empty.
"""

from __future__ import annotations

import time
from collections.abc import Mapping

from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from gateway_v2.audit.metrics import AuditMetrics
from gateway_v2.edge.dev_audit import DevAudit
from gateway_v2.edge.event_log import StructuredEventLog
from gateway_v2.edge.publisher import Publisher
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.state_metrics import StateMetricsRecorder
from gateway_v2.runtime.stream_metrics import PerRequestExports
from state_control.metrics import RehydrationMetrics

__all__ = ("build_dev_publisher",)


class _LiveAudit:
    """The audit producer read from the sink on every scrape; no sink reads as zero traffic."""

    __slots__ = ("_audit",)

    def __init__(self, audit: DevAudit | None) -> None:
        self._audit = audit

    def series(self) -> Mapping[str, float]:
        if self._audit is None:
            return AuditMetrics().series()
        return AuditMetrics(sink=self._audit.counters()).series()


def build_dev_publisher(
    *,
    contract: ResourceContract,
    admission: AdmissionMetrics,
    quota: QuotaMetrics,
    audit: DevAudit | None,
    exports: PerRequestExports,
    holdback: HoldbackMetrics,
) -> tuple[Publisher, str]:
    """``(publisher, boot log line)``."""
    clock = time.monotonic
    publisher = Publisher.from_contract(
        admission=admission,
        quota=quota,
        audit=_LiveAudit(audit),
        rehydration=RehydrationMetrics(),
        state=StateMetricsRecorder().snapshot(),
        stream=exports,
        holdback=holdback,
        event_log=StructuredEventLog.from_contract(contract, clock=clock),
        contract=contract,
        clock=clock,
    )
    log = (
        f"boot: metrics_registry -> GW14d Publisher ({publisher.SERIES_COUNT} series: admission, "
        f"quota, audit {'sink' if audit is not None else 'none'}, stream, holdback, tenants, "
        "rejections, postures; state and re-hydration ABSENT (no writer in dev), add-on latency "
        "and windowed-busy have no source yet)"
    )
    return publisher, log
