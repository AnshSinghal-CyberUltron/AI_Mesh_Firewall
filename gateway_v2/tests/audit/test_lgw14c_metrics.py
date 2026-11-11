"""GW14c phase 5: the metrics surface. Fixed cardinality, typed, and loss that cannot read zero.

* **The key set is a function of nothing tenant-derived.** R2-10 measured a full metric directory
  making 40% of new tenants' first requests return HTTP 500. `SERIES_COUNT` is a fixed
  expression and the test asserts it equals what is emitted, so a tenant-dependent series fails
  a gate rather than shipping.
* **`# TYPE` on every family.** R2-11 (M4) measured untyped exposition landing as `.../unknown`,
  so a GKE HPA asking for a gauge got a 400 and was blind.
* **One `completeness_ratio`, sourced from DURABLE.** M3: a flush erased 36% of the records while
  that metric read 1.0. The acknowledged ratio is published too, under its own name, so the gap
  between "the store took it" and "something durable holds it" is visible rather than collapsed.
* **An unreachable store is not a healthy store.** Zeroed memory gauges must not read as "0 used,
  0 evictions, policy fine" when nobody has looked.
"""

from __future__ import annotations

from gateway_v2.audit.budget import BudgetReading
from gateway_v2.audit.metrics import (
    PREFIX,
    SERIES_COUNT,
    STORE_PREFIX,
    AuditMetrics,
    AuditMetricsRecorder,
    DurableReading,
    metric_type,
    render,
)
from gateway_v2.audit.sink import SinkCounters
from gateway_v2.runtime.storemem import StoreMemory

MIB = 1 << 20


# --- cardinality ----------------------------------------------------------------------------------


def test_series_count_is_a_fixed_expression_that_matches_what_is_emitted() -> None:
    assert len(AuditMetrics().series()) == SERIES_COUNT


def test_the_key_set_does_not_depend_on_the_estate() -> None:
    """Three tenants and twenty-five thousand must produce the same series."""
    small = AuditMetricsRecorder()
    small.observe_budget(BudgetReading(budget_bytes=64 * MIB, tenants=3, tracked_orgs=3))
    large = AuditMetricsRecorder()
    large.observe_budget(BudgetReading(budget_bytes=64 * MIB, tenants=25_000, tracked_orgs=25_000))

    assert set(small.snapshot().series()) == set(large.snapshot().series())
    assert len(large.snapshot().series()) == SERIES_COUNT


def test_no_series_name_carries_a_tenant() -> None:
    """Not even the trim counter, where it would be most tempting. "Which tenant is being
    trimmed" is a query; the store answers it."""
    recorder = AuditMetricsRecorder()
    recorder.observe_budget(BudgetReading(budget_bytes=1, tenants=2, tracked_orgs=2))

    names = set(recorder.snapshot().series())

    assert all("{" not in name for name in names), "no labels at all on this surface"
    assert f"{PREFIX}_trimmed_records_total" in names


def test_every_series_is_prefixed_consistently() -> None:
    for name in AuditMetrics().series():
        assert name.startswith((PREFIX, STORE_PREFIX))


# --- typing ---------------------------------------------------------------------------------------


def test_totals_are_counters_and_everything_else_is_a_gauge() -> None:
    assert metric_type(f"{PREFIX}_trimmed_records_total") == "counter"
    assert metric_type(f"{PREFIX}_completeness_ratio") == "gauge"
    assert metric_type(f"{STORE_PREFIX}_used_memory_bytes") == "gauge"


def test_render_types_every_family() -> None:
    """R2-11 (M4): untyped exposition lands as `.../unknown` and an HPA asking for a gauge
    gets a 400. A missing TYPE line is a blind autoscaler."""
    text = render(AuditMetrics())

    names = [line.split()[0] for line in text.splitlines() if not line.startswith("#")]
    typed = {line.split()[2]: line.split()[3] for line in text.splitlines()
             if line.startswith("# TYPE")}

    assert len(names) == SERIES_COUNT
    assert set(typed) == set(names), "every emitted series has its own TYPE line"
    assert set(typed.values()) <= {"counter", "gauge"}
    assert typed[f"{PREFIX}_trimmed_records_total"] == "counter"
    assert typed[f"{PREFIX}_completeness_ratio"] == "gauge"


def test_render_is_parseable_and_stable() -> None:
    first = render(AuditMetrics())
    second = render(AuditMetrics())

    assert first == second
    assert first.endswith("\n")
    for line in first.splitlines():
        if line.startswith("# TYPE"):
            assert len(line.split()) == 4
        else:
            assert len(line.split()) == 2


# --- the two ratios, and why they are two ---------------------------------------------------------


def test_the_completeness_ratio_is_sourced_from_durable_not_acknowledged() -> None:
    """M3, directly. The store acknowledged everything and a third of it is not durable; the
    metric that reported 1.0 through that must not read 1.0 here."""
    recorder = AuditMetricsRecorder()
    recorder.observe_sink(SinkCounters(produced=1_000, written=1_000, capacity=10_000))
    recorder.observe_durable(
        DurableReading(
            exported=640,
            durable=640,
            records_lost=360,
            acknowledged_high_water=1_000,
            durable_high_water=640,
        ),
    )

    series = recorder.snapshot().series()

    assert series[f"{PREFIX}_acknowledged_ratio"] == 1.0, "the store did take everything"
    assert series[f"{PREFIX}_completeness_ratio"] == 0.64, "and a third of it is gone"
    assert series[f"{PREFIX}_records_lost_total"] == 360


def test_the_two_ratios_are_separate_series() -> None:
    """Collapsing them back into one number is how M3 returns."""
    names = set(AuditMetrics().series())

    assert f"{PREFIX}_acknowledged_ratio" in names
    assert f"{PREFIX}_completeness_ratio" in names


def test_an_idle_sink_reads_complete_because_nothing_has_been_lost() -> None:
    series = AuditMetrics().series()

    assert series[f"{PREFIX}_completeness_ratio"] == 1.0
    assert series[f"{PREFIX}_records_lost_total"] == 0.0


def test_zero_loss_is_published_as_zero_not_omitted() -> None:
    """A gauge that disappears when there is no loss cannot be alarmed on, and "no series" must
    not look like "no loss"."""
    series = AuditMetricsRecorder().snapshot().series()

    assert series[f"{PREFIX}_records_lost_total"] == 0.0
    assert series[f"{PREFIX}_dropped_records_total"] == 0.0
    assert series[f"{PREFIX}_failed_records_total"] == 0.0


# --- the producer ---------------------------------------------------------------------------------


def test_the_sink_counters_reach_the_surface() -> None:
    recorder = AuditMetricsRecorder()
    recorder.observe_sink(
        SinkCounters(
            produced=10,
            written=7,
            dropped=2,
            failed=1,
            trimmed=99,
            batches=3,
            capacity=1_000,
            queued=4,
        ),
    )

    series = recorder.snapshot().series()

    assert series[f"{PREFIX}_produced_records_total"] == 10
    assert series[f"{PREFIX}_written_records_total"] == 7
    assert series[f"{PREFIX}_dropped_records_total"] == 2
    assert series[f"{PREFIX}_failed_records_total"] == 1
    assert series[f"{PREFIX}_trimmed_records_total"] == 99
    assert series[f"{PREFIX}_queue_capacity"] == 1_000
    assert series[f"{PREFIX}_queued_records"] == 4
    assert series[f"{PREFIX}_acknowledged_ratio"] == 0.7


def test_the_budget_reading_reaches_the_surface() -> None:
    recorder = AuditMetricsRecorder()
    recorder.observe_budget(
        BudgetReading(
            budget_bytes=32 * MIB,
            tenants=4,
            tracked_orgs=4,
            min_stream_maxlen=2_500,
            max_bytes_per_record=4_171.0,
        ),
    )

    series = recorder.snapshot().series()

    assert series[f"{PREFIX}_budget_bytes"] == 32 * MIB
    assert series[f"{PREFIX}_budget_tenants"] == 4
    assert series[f"{PREFIX}_stream_maxlen"] == 2_500
    assert series[f"{PREFIX}_bytes_per_record"] == 4_171.0


# --- the store ------------------------------------------------------------------------------------


def test_a_memory_sample_publishes_the_posture() -> None:
    recorder = AuditMetricsRecorder()
    recorder.observe_store_memory(
        StoreMemory(used=16 * MIB, maxmemory=64 * MIB, policy="volatile-lru", evicted_keys=5),
    )

    series = recorder.snapshot().series()

    assert series[f"{STORE_PREFIX}_used_memory_bytes"] == 16 * MIB
    assert series[f"{STORE_PREFIX}_maxmemory_bytes"] == 64 * MIB
    assert series[f"{STORE_PREFIX}_memory_used_ratio"] == 0.25
    assert series[f"{STORE_PREFIX}_evicted_keys"] == 5
    assert series[f"{STORE_PREFIX}_policy_unsafe"] == 1
    assert series[f"{STORE_PREFIX}_memory_reachable"] == 1


def test_noeviction_reads_as_a_safe_policy() -> None:
    recorder = AuditMetricsRecorder()
    recorder.observe_store_memory(
        StoreMemory(used=1, maxmemory=64 * MIB, policy="noeviction", evicted_keys=0),
    )

    assert recorder.snapshot().series()[f"{STORE_PREFIX}_policy_unsafe"] == 0


def test_an_unreachable_store_is_not_a_healthy_store() -> None:
    """Zeroed gauges must not read as "0 used, 0 evictions, policy fine" when nobody looked."""
    recorder = AuditMetricsRecorder()
    recorder.observe_store_unreachable(sample_errors=3)

    series = recorder.snapshot().series()

    assert series[f"{STORE_PREFIX}_memory_reachable"] == 0
    assert series[f"{STORE_PREFIX}_memory_sample_errors_total"] == 3


def test_a_never_sampled_store_reads_unreachable() -> None:
    """Before the first sample nobody has looked, and the surface must say so rather than
    publishing a confident zero."""
    assert AuditMetrics().series()[f"{STORE_PREFIX}_memory_reachable"] == 0


def test_sample_errors_are_counted_without_clearing_the_last_good_reading() -> None:
    recorder = AuditMetricsRecorder()
    recorder.observe_store_memory(
        StoreMemory(used=8 * MIB, maxmemory=64 * MIB, policy="noeviction", evicted_keys=0),
    )
    recorder.observe_sample_errors(2)

    series = recorder.snapshot().series()

    assert series[f"{STORE_PREFIX}_memory_sample_errors_total"] == 2
    assert series[f"{STORE_PREFIX}_used_memory_bytes"] == 8 * MIB
    assert series[f"{STORE_PREFIX}_memory_reachable"] == 1


# --- the sampler wiring ---------------------------------------------------------------------------


def test_the_recorder_satisfies_the_samplers_callback() -> None:
    """`MemorySampler(on_sample=...)` expects exactly this signature, so the wiring is one line
    and the metrics module stays out of `runtime/`."""
    import asyncio

    from gateway_v2.domain.audit_knobs import AuditMemoryKnobs
    from gateway_v2.runtime.storemem import MemorySampler

    class Store:
        async def info(self, section: str) -> dict[str, object]:
            if section == "memory":
                return {"used_memory": 4 * MIB, "maxmemory": 64 * MIB,
                        "maxmemory_policy": "noeviction"}
            return {"evicted_keys": 0}

    recorder = AuditMetricsRecorder()
    sampler = MemorySampler(Store(), AuditMemoryKnobs(), on_sample=recorder.observe_store_memory)

    asyncio.run(sampler.sample_once())

    assert recorder.snapshot().series()[f"{STORE_PREFIX}_used_memory_bytes"] == 4 * MIB
