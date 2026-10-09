"""Audit and store-memory metrics. A FIXED series set, and loss that cannot read as zero.

Two rules inherited from `runtime/state_metrics.py` and `state_control/metrics.py`, for the same
measured reasons:

* **No tenant-derived label, anywhere.** R2-10 measured a full shared-memory metric directory
  making 40% of new tenants' first requests return HTTP 500, after RC2 exported one series per
  org. There is no `org` label here, not even on the trim counter — which is the one place it
  would be most tempting, since "which tenant is being trimmed" is a real question. It is a
  query, not a metric, and the store answers it.
* **`SERIES_COUNT` is a fixed expression**, not `len(series())`, and the test asserts the two
  agree. A series whose existence depends on the estate then fails a gate instead of shipping.

And one rule that is this card's own:

* **`audit_completeness_ratio` is measured against the DURABLE high-water mark, never the
  acknowledged one.** R2-11's M3 finding is that a flush erased 36% of the audit records while
  that exact metric read 1.0. So the ratio has a single source — the exporter's durable
  position — and `audit_acknowledged_ratio` is published next to it under a different name. An
  operator comparing the two sees the window of records the store took and nothing durable
  holds yet; collapsing them back into one number is how the defect returns.

Loss uses `0.0` rather than absence: a gauge that disappears when there is no loss cannot be
alarmed on, and "no series" and "no loss" must not look the same.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from gateway_v2.audit.budget import BudgetReading
from gateway_v2.audit.sink import SinkCounters
from gateway_v2.runtime.storemem import StoreMemory

PREFIX = "amf_audit"
STORE_PREFIX = "amf_store"

ABSENT = -1.0
"""Gauge value for "this has never happened", so the series exists from the first scrape."""


@dataclass(frozen=True, slots=True)
class DurableReading:
    """What the exporter knows and the sink cannot: what is actually durable.

    `records_lost` is records that were trimmed from the store before anything durable held
    them. It is a count, not an estimate: the exporter compares the trimmed range against its
    own durable cursor. Zero is a real answer and is published as zero.
    """

    exported: int = 0
    durable: int = 0
    records_lost: int = 0
    acknowledged_high_water: int = 0
    durable_high_water: int = 0
    export_failures: int = 0
    oldest_unexported_s: float = ABSENT

    @property
    def completeness_ratio(self) -> float:
        """THE `audit_completeness_ratio`: durable over produced-and-acknowledged.

        Deliberately the only thing in this package allowed to use the word completeness.
        """
        reference = self.acknowledged_high_water
        return self.durable / reference if reference else 1.0


@dataclass(frozen=True, slots=True)
class AuditMetrics:
    """A full reading. Every field is a scalar, so no dimension can be added by accident."""

    sink: SinkCounters = SinkCounters()
    budget: BudgetReading = BudgetReading()
    durable: DurableReading = DurableReading()
    store_used_bytes: int = 0
    store_maxmemory_bytes: int = 0
    store_used_ratio: float = 0.0
    store_evicted_keys: int = 0
    store_policy_unsafe: int = 0
    store_sample_errors: int = 0
    store_reachable: int = 0

    def series(self) -> dict[str, float]:
        """The flat, label-free series a registry would publish."""
        return {
            # --- the producer -------------------------------------------------------------
            f"{PREFIX}_produced_records_total": self.sink.produced,
            f"{PREFIX}_written_records_total": self.sink.written,
            f"{PREFIX}_dropped_records_total": self.sink.dropped,
            f"{PREFIX}_failed_records_total": self.sink.failed,
            f"{PREFIX}_batches_total": self.sink.batches,
            f"{PREFIX}_queue_capacity": self.sink.capacity,
            f"{PREFIX}_queued_records": self.sink.queued,
            f"{PREFIX}_acknowledged_ratio": self.sink.acknowledged_ratio,
            # --- the bound ----------------------------------------------------------------
            f"{PREFIX}_trimmed_records_total": self.sink.trimmed,
            f"{PREFIX}_budget_bytes": self.budget.budget_bytes,
            f"{PREFIX}_budget_tenants": self.budget.tenants,
            f"{PREFIX}_tracked_orgs": self.budget.tracked_orgs,
            f"{PREFIX}_stream_maxlen": self.budget.min_stream_maxlen,
            f"{PREFIX}_bytes_per_record": self.budget.max_bytes_per_record,
            # --- durability ---------------------------------------------------------------
            f"{PREFIX}_exported_records_total": self.durable.exported,
            f"{PREFIX}_durable_records_total": self.durable.durable,
            f"{PREFIX}_records_lost_total": self.durable.records_lost,
            f"{PREFIX}_export_failures_total": self.durable.export_failures,
            f"{PREFIX}_acknowledged_high_water": self.durable.acknowledged_high_water,
            f"{PREFIX}_durable_high_water": self.durable.durable_high_water,
            f"{PREFIX}_oldest_unexported_seconds": self.durable.oldest_unexported_s,
            f"{PREFIX}_completeness_ratio": self.durable.completeness_ratio,
            # --- the store ----------------------------------------------------------------
            f"{STORE_PREFIX}_used_memory_bytes": self.store_used_bytes,
            f"{STORE_PREFIX}_maxmemory_bytes": self.store_maxmemory_bytes,
            f"{STORE_PREFIX}_memory_used_ratio": self.store_used_ratio,
            f"{STORE_PREFIX}_evicted_keys": self.store_evicted_keys,
            f"{STORE_PREFIX}_policy_unsafe": self.store_policy_unsafe,
            f"{STORE_PREFIX}_memory_sample_errors_total": self.store_sample_errors,
            f"{STORE_PREFIX}_memory_reachable": self.store_reachable,
        }


SERIES_COUNT = 8 + 6 + 8 + 7
"""How many series this surface can ever produce. A constant, by construction.

Eight producer, six bound, eight durability, seven store. R2-10 is why this is an expression
rather than a count of whatever happens to be emitted: a label whose domain is tenant-derived
then fails the gate instead of shipping.
"""

def metric_type(name: str) -> str:
    """Prometheus type by suffix: `_total` is a counter, everything else is a gauge.

    R2-11 (M4) measured untyped exposition landing as `.../unknown`, so a GKE HPA asking for a
    gauge got a 400 and was blind. A suffix rule rather than a table because a table is a second
    place to forget to update.
    """
    return "counter" if name.endswith("_total") else "gauge"


class AuditMetricsRecorder:
    """Consumes what the sink, the budget and the exporter already produced. O(1) per reading."""

    def __init__(self) -> None:
        self._metrics = AuditMetrics()

    def observe_sink(self, counters: SinkCounters) -> None:
        self._metrics = replace(self._metrics, sink=counters)

    def observe_budget(self, reading: BudgetReading) -> None:
        self._metrics = replace(self._metrics, budget=reading)

    def observe_durable(self, reading: DurableReading) -> None:
        self._metrics = replace(self._metrics, durable=reading)

    def observe_store_memory(self, memory: StoreMemory) -> None:
        """The sampler's callback. Off the request path, worker 0 only."""
        self._metrics = replace(
            self._metrics,
            store_used_bytes=memory.used,
            store_maxmemory_bytes=memory.maxmemory,
            store_used_ratio=memory.used_ratio,
            store_evicted_keys=memory.evicted_keys,
            store_policy_unsafe=1 if memory.policy_unsafe else 0,
            store_reachable=1,
        )

    def observe_store_unreachable(self, sample_errors: int) -> None:
        """A store that will not answer is a distinct condition from a store with no evictions.

        `store_memory_reachable=0` is what keeps the memory gauges from being read as "0 bytes
        used, 0 evictions, policy fine" when in fact nobody has looked.
        """
        self._metrics = replace(
            self._metrics, store_reachable=0, store_sample_errors=sample_errors,
        )

    def observe_sample_errors(self, sample_errors: int) -> None:
        self._metrics = replace(self._metrics, store_sample_errors=sample_errors)

    def snapshot(self) -> AuditMetrics:
        return self._metrics


def render(metrics: AuditMetrics) -> str:
    """Prometheus text exposition, WITH a `# TYPE` line per family.

    Deliberately minimal: no registry, no client library. GW14d owns how metrics are published
    fleet-wide; this produces the numbers in the one format a scrape already understands, and it
    types them because R2-11 measured what untyped exposition costs an autoscaler.
    """
    lines: list[str] = []
    for name, value in sorted(metrics.series().items()):
        lines.append(f"# TYPE {name} {metric_type(name)}")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"
