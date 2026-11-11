"""GW14c phase 4: the bounded producer, the trim on every batch, and counted loss.

The assertions that carry the card:

* **`emit` never awaits and never raises.** It is called from the request path. A producer that
  can block a serving loop is the defect C31 is about, and one that can raise turns an audit
  problem into a client-visible failure.
* **Loss is counted, in both directions.** A full queue drops and counts; a store error fails
  the batch and counts. R2-11 measured the alternative: 36% of records erased while the ratio
  read 1.0.
* **`acknowledged_ratio` is not called completeness.** Acknowledged means the store took it; a
  record the store took can still be trimmed before anything durable holds it. Conflating the
  two IS the defect, so the name is asserted as part of the contract.
* **Every batch trims**, and the trim count comes from the store's own answer.
* **A store error never kills the writer.** A dead writer loses everything silently.
* **Evidence never reaches the stored bytes.** `Finding.evidence` is the detector's matched text;
  an audit stream is append-only, exported and retained, so evidence in a record would undo the
  redaction the pipeline just performed, in three places at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Mapping, Sequence

import pytest

from gateway_v2.audit.budget import StreamBudget
from gateway_v2.audit.record import (
    SCHEMA_VERSION,
    AuditPhase,
    AuditRecord,
    from_decision,
)
from gateway_v2.audit.sink import AuditSink, SinkCounters
from gateway_v2.domain.audit_knobs import MIB, AuditMemoryKnobs
from gateway_v2.domain.category import Category
from gateway_v2.domain.decision import (
    Decision,
    DecisionRecord,
    Disposition,
    FindingDisposition,
    Transformation,
)
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.store_audit import AuditBatchResult

SECRET = "123-45-6789"
"""Stands in for a matched value. It must never appear in the serialized record."""


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


class FakeAuditStore:
    """Records each append and scripts the answers, including a raise."""

    def __init__(self, *, trimmed: int = 0, fail: Exception | None = None) -> None:
        self.calls: list[tuple[tuple[tuple[str, bytes], ...], Mapping[str, int]]] = []
        self.trimmed = trimmed
        self.fail = fail

    async def append(
        self,
        records: Sequence[tuple[str, bytes]],
        maxlens: Mapping[str, int],
    ) -> AuditBatchResult:
        self.calls.append((tuple(records), dict(maxlens)))
        if self.fail is not None:
            raise self.fail
        return AuditBatchResult(
            written=len(records),
            trimmed=self.trimmed,
            trimmed_by_org={org: self.trimmed for org, _ in records[:1]} if self.trimmed else {},
            ids=tuple(f"1-{index}" for index in range(len(records))),
        )

    @property
    def payloads(self) -> list[bytes]:
        return [payload for call, _ in self.calls for _, payload in call]


def _record(org: str = "org-a", request: str = "req-1") -> AuditRecord:
    return AuditRecord(
        request_id=request,
        org_id=org,
        phase=AuditPhase.INPUT,
        outcome="allow",
        recorded_at_ns=1,
    )


def _sink(
    store: FakeAuditStore | None = None,
    *,
    queue_depth: int = 100,
    batch_max: int = 2_000,
    tenants: int = 2,
) -> tuple[AuditSink, FakeAuditStore]:
    real = store or FakeAuditStore()
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: tenants)
    return AuditSink(real, budget, queue_depth=queue_depth, batch_max=batch_max), real


# --- emit, on the request path --------------------------------------------------------------------


def test_emit_is_synchronous_and_does_not_touch_the_store() -> None:
    """No await, no coroutine, no round trip. The request path hands over and continues."""
    sink, store = _sink()

    assert sink.emit(_record()) is True

    assert store.calls == [], "emit must not write; that is the writer task's job"
    assert sink.counters().produced == 1
    assert sink.counters().queued == 1


def test_emit_outside_a_running_loop_still_works() -> None:
    """Proof it is not secretly async: this runs with no event loop at all."""
    sink, _ = _sink()

    assert sink.emit(_record()) is True


def test_a_full_queue_drops_and_counts_rather_than_blocking() -> None:
    sink, _ = _sink(queue_depth=2)

    assert [sink.emit(_record()) for _ in range(5)] == [True, True, False, False, False]

    counters = sink.counters()
    assert counters.produced == 5
    assert counters.dropped == 3
    assert counters.queued == 2


def test_a_full_queue_warns_once_not_once_per_drop(caplog: pytest.LogCaptureFixture) -> None:
    """A queue that is full is full for a while. One line plus a counter, not one line each."""
    sink, _ = _sink(queue_depth=1)

    with caplog.at_level(logging.WARNING, logger="amf.audit.sink"):
        for _ in range(50):
            sink.emit(_record())

    full = [r for r in caplog.records if "audit_queue_full" in r.getMessage()]
    assert len(full) == 1
    assert sink.counters().dropped == 49


def test_a_zero_depth_queue_is_refused_at_construction() -> None:
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=1), lambda: 1)
    with pytest.raises(ValueError, match="queue_depth must be positive"):
        AuditSink(FakeAuditStore(), budget, queue_depth=0)
    with pytest.raises(ValueError, match="batch_max must be positive"):
        AuditSink(FakeAuditStore(), budget, queue_depth=10, batch_max=0)


def test_the_queue_depth_has_no_default() -> None:
    """GW14's card states the bound as the contract's, at the writer's measured drain rate. A
    literal default here would be a capacity decision taken in the wrong module -- which the
    `check_capacity_literals` AST gate exists to prevent."""
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=1), lambda: 1)

    with pytest.raises(TypeError, match="queue_depth"):
        AuditSink(FakeAuditStore(), budget)  # type: ignore[call-arg]


def test_the_configured_capacity_is_reported_next_to_the_drops() -> None:
    sink, _ = _sink(queue_depth=3)
    for _ in range(5):
        sink.emit(_record())

    counters = sink.counters()

    assert counters.capacity == 3
    assert counters.dropped == 2


# --- the ratio, and what it is NOT ----------------------------------------------------------------


def test_the_ratio_is_never_assumed_to_be_one() -> None:
    sink, _ = _sink(queue_depth=1)
    for _ in range(4):
        sink.emit(_record())
    _run(sink.drain(1.0))

    counters = sink.counters()

    assert counters.produced == 4
    assert counters.written == 1
    assert counters.dropped == 3
    assert counters.acknowledged_ratio == 0.25


def test_an_idle_sink_reads_one_because_nothing_was_lost() -> None:
    assert SinkCounters().acknowledged_ratio == 1.0


def test_the_sink_does_not_publish_anything_called_completeness() -> None:
    """Acknowledged is not durable. The card's `audit_completeness_ratio` is measured against the
    DURABLE high-water mark, which only the exporter knows; a sink attribute by that name is how
    the two get conflated, and the conflation is the M3 defect itself."""
    names = set(dir(SinkCounters)) | set(SinkCounters.__annotations__)

    assert "acknowledged_ratio" in names
    assert not any("completeness" in name for name in names)


# --- the writer -----------------------------------------------------------------------------------


def test_the_writer_batches_what_is_already_queued_into_one_append() -> None:
    sink, store = _sink()
    for index in range(7):
        sink.emit(_record(request=f"req-{index}"))

    _run(sink.drain(1.0))

    assert len(store.calls) == 1, "one round trip for everything that was already waiting"
    assert store.calls[0][0] and len(store.calls[0][0]) == 7
    assert sink.counters().written == 7


def test_the_batch_cap_bounds_one_pipeline() -> None:
    """Unbounded batching looks free and makes one store error fail an arbitrary number of
    records, in an arbitrarily large pipeline."""
    sink, store = _sink(batch_max=3)
    for index in range(7):
        sink.emit(_record(request=f"req-{index}"))

    _run(sink.drain(1.0))

    assert [len(call) for call, _ in store.calls] == [3, 3, 1]


def test_every_batch_trims_the_streams_it_touched() -> None:
    sink, store = _sink()
    sink.emit(_record(org="org-a"))
    sink.emit(_record(org="org-b"))

    _run(sink.drain(1.0))

    _, maxlens = store.calls[0]
    assert sorted(maxlens) == ["org-a", "org-b"]
    assert all(value > 0 for value in maxlens.values())


def test_the_trim_count_comes_from_the_stores_own_answer() -> None:
    """Exact, not estimated. This is the number M3 needs to be trustworthy."""
    sink, _ = _sink(FakeAuditStore(trimmed=17))
    sink.emit(_record())

    _run(sink.drain(1.0))

    assert sink.trimmed == 17
    assert sink.counters().trimmed == 17


def test_each_payload_is_folded_into_the_byte_model() -> None:
    """The caps follow the traffic, and they do it without measuring the store per record."""
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 1)
    store = FakeAuditStore()
    sink = AuditSink(store, budget, queue_depth=10)
    sink.emit(
        AuditRecord(
            request_id="req-1",
            org_id="org-a",
            phase=AuditPhase.INPUT,
            outcome="allow",
            recorded_at_ns=1,
            detail={"padding": "p" * 4_000},
        ),
    )

    _run(sink.drain(1.0))

    assert budget.bytes_per_record("org-a") > 4_000


def test_a_store_error_fails_the_batch_and_not_the_writer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sink, store = _sink(FakeAuditStore(fail=OSError("connection reset")))
    sink.emit(_record())
    sink.emit(_record(request="req-2"))

    with caplog.at_level(logging.WARNING, logger="amf.audit.sink"):
        assert _run(sink.drain(1.0)) is True

    counters = sink.counters()
    assert counters.failed == 2
    assert counters.written == 0
    assert counters.acknowledged_ratio == 0.0
    assert any("audit_write_failed" in r.getMessage() for r in caplog.records)


def test_the_writer_keeps_running_after_a_failed_batch() -> None:
    """A dead writer loses everything silently; a failed batch loses a batch loudly."""
    store = FakeAuditStore(fail=OSError("down"))
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 1)
    sink = AuditSink(store, budget, queue_depth=10, batch_max=1)

    async def drive() -> None:
        task = asyncio.create_task(sink.run())
        sink.emit(_record())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        store.fail = None
        sink.emit(_record(request="req-2"))
        for _ in range(10):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(drive())

    counters = sink.counters()
    assert counters.failed >= 1
    assert counters.written >= 1, "the writer survived the failure and wrote the next record"


def test_run_cancellation_propagates() -> None:
    sink, _ = _sink()

    async def drive() -> None:
        task = asyncio.create_task(sink.run())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(drive())


def test_the_sampler_runs_beside_the_writer_and_dies_with_it() -> None:
    """Worker 0 only. They share a task group so losing one never leaves the other orphaned."""
    started = asyncio.Event()

    class Sampler:
        async def run(self) -> None:
            started.set()
            await asyncio.sleep(3600)

    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 1)
    sink = AuditSink(FakeAuditStore(), budget, queue_depth=10, sampler=Sampler())

    async def drive() -> None:
        task = asyncio.create_task(sink.run())
        await asyncio.wait_for(started.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(drive())
    assert started.is_set()


# --- shutdown -------------------------------------------------------------------------------------


def test_drain_flushes_the_queue_within_its_budget() -> None:
    sink, store = _sink()
    for index in range(5):
        sink.emit(_record(request=f"req-{index}"))

    assert _run(sink.drain(1.0)) is True
    assert sink.counters().queued == 0
    assert len(store.payloads) == 5


def test_drain_reports_failure_rather_than_blocking_a_deployment(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Shutdown is a bounded, known loss opportunity. It gets a bounded wait, and says so."""
    clock = [0]

    def now_ns() -> int:
        clock[0] += 10_000_000_000
        return clock[0]

    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 1)
    sink = AuditSink(FakeAuditStore(), budget, queue_depth=10, now_ns=now_ns)
    sink.emit(_record())

    with caplog.at_level(logging.WARNING, logger="amf.audit.sink"):
        assert _run(sink.drain(0.001)) is False

    assert any("audit_drain_timeout" in r.getMessage() for r in caplog.records)
    assert sink.counters().produced == 1
    assert sink.counters().written == 0


def test_drain_of_an_empty_queue_succeeds_immediately() -> None:
    sink, store = _sink()

    assert _run(sink.drain(0.0)) is True
    assert store.calls == []


# --- calibration ----------------------------------------------------------------------------------


def test_calibrate_measures_against_the_store_and_uses_its_own_stream() -> None:
    """A queue depth is a stall budget; a stall budget from a guessed drain rate is a guess. And
    the measurement must not pollute a tenant's audit."""
    sink, store = _sink()

    rate = _run(sink.calibrate(records=50))

    assert rate > 0
    orgs = {org for call, _ in store.calls for org, _ in call}
    assert orgs == {"_calibration"}
    assert sink.counters().produced == 0, "calibration is not audit traffic"


def test_calibrate_refuses_a_meaningless_sample() -> None:
    sink, _ = _sink()
    with pytest.raises(ValueError, match="positive record count"):
        _run(sink.calibrate(records=0))


# --- the record itself ----------------------------------------------------------------------------


def test_a_record_without_an_org_is_refused() -> None:
    """Unattributed means unbudgetable, untrimmable per tenant, and unexportable."""
    with pytest.raises(ValueError, match="needs an org_id"):
        AuditRecord(
            request_id="req-1",
            org_id="",
            phase=AuditPhase.INPUT,
            outcome="allow",
            recorded_at_ns=1,
        )


def test_a_record_without_a_request_id_is_refused() -> None:
    with pytest.raises(ValueError, match="needs a request_id"):
        AuditRecord(
            request_id="",
            org_id="org-a",
            phase=AuditPhase.INPUT,
            outcome="allow",
            recorded_at_ns=1,
        )


def test_a_record_without_an_outcome_is_refused() -> None:
    with pytest.raises(ValueError, match="needs an outcome"):
        AuditRecord(
            request_id="req-1",
            org_id="org-a",
            phase=AuditPhase.INPUT,
            outcome="",
            recorded_at_ns=1,
        )


def test_the_serialized_form_is_stable() -> None:
    """Equal records must produce equal bytes: the byte model sizes streams from the length."""
    first = _record()
    second = _record()

    assert first.serialize() == second.serialize()
    assert json.loads(first.serialize())["v"] == SCHEMA_VERSION


def test_the_serialized_form_carries_the_schema_version_and_the_org() -> None:
    body = json.loads(_record(org="org-x", request="req-9").serialize())

    assert body["v"] == SCHEMA_VERSION
    assert body["org_id"] == "org-x"
    assert body["request_id"] == "req-9"
    assert body["phase"] == "input"


# --- evidence never reaches the stored bytes ------------------------------------------------------


def _decision_with_evidence() -> DecisionRecord:
    finding = Finding(
        detector="pii",
        detector_version="2026.09",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=0.97,
        spans=(Span(11, 22),),
        evidence=SECRET,
    )
    skipped = Finding(
        detector="semantic",
        detector_version="2026.09",
        category=Category.PROMPT_INJECTION,
        status=FindingStatus.SKIPPED,
        confidence=None,
        spans=(),
        evidence=None,
    )
    decision = Decision(
        disposition=Disposition.REDACT,
        per_finding=(FindingDisposition("pii", Disposition.REDACT),),
        transformations=(Transformation("mask", Span(11, 22), "***-**-****"),),
        findings=(finding, skipped),
        plan_version="v7",
        deciding_rules=("r-pii",),
        unavailable_detectors=("semantic",),
    )
    return DecisionRecord(request_id="req-1", plan_version="v7", decision=decision)


def test_the_matched_value_is_absent_from_the_stored_bytes() -> None:
    """An audit stream is append-only, exported to a durable sink, and retained. Evidence in a
    record is the user's secret at rest in three places, and it would undo the redaction the
    pipeline just performed."""
    record = from_decision(
        _decision_with_evidence(),
        org_id="org-a",
        phase=AuditPhase.INPUT,
        recorded_at_ns=99,
    )

    raw = record.serialize()

    assert SECRET.encode() not in raw
    assert b"evidence" not in raw


def test_the_finding_is_still_locatable_without_being_reproduced() -> None:
    """Offsets locate a finding; they do not reproduce it."""
    record = from_decision(
        _decision_with_evidence(),
        org_id="org-a",
        phase=AuditPhase.INPUT,
        recorded_at_ns=99,
    )
    body = json.loads(record.serialize())
    findings = body["detail"]["findings"]

    assert findings[0]["detector"] == "pii"
    assert findings[0]["confidence"] == 0.97
    assert findings[0]["spans"] == [[11, 22]]
    assert findings[0]["category"] == str(Category.PII)


def test_skipped_and_unavailable_findings_are_kept(
) -> None:
    """C3: absence is a status, not a missing entry. The prototype reported five of seven stages
    as executed unconditionally, and an omitted entry reproduces exactly that."""
    record = from_decision(
        _decision_with_evidence(),
        org_id="org-a",
        phase=AuditPhase.INPUT,
        recorded_at_ns=99,
    )
    body = json.loads(record.serialize())

    statuses = [finding["status"] for finding in body["detail"]["findings"]]
    assert statuses == ["executed", "skipped"]
    assert body["detail"]["unavailable_detectors"] == ["semantic"]


def test_the_outcome_is_the_decisions_disposition() -> None:
    record = from_decision(
        _decision_with_evidence(),
        org_id="org-a",
        phase=AuditPhase.OUTPUT,
        recorded_at_ns=99,
    )

    assert record.outcome == "redact"
    assert record.phase is AuditPhase.OUTPUT


def test_the_transformation_carries_the_mask_not_the_value() -> None:
    record = from_decision(
        _decision_with_evidence(),
        org_id="org-a",
        phase=AuditPhase.INPUT,
        recorded_at_ns=99,
    )
    body = json.loads(record.serialize())

    assert body["detail"]["transformations"] == [
        {"kind": "mask", "span": [11, 22], "replacement": "***-**-****"},
    ]


def test_a_sink_round_trip_never_stores_the_matched_value() -> None:
    """End to end, through the real serializer and into the bytes the store would receive."""
    sink, store = _sink()
    sink.emit(
        from_decision(
            _decision_with_evidence(),
            org_id="org-a",
            phase=AuditPhase.INPUT,
            recorded_at_ns=99,
        ),
    )

    _run(sink.drain(1.0))

    assert store.payloads
    for payload in store.payloads:
        assert SECRET.encode() not in payload


def test_the_byte_model_sees_the_real_serialized_size() -> None:
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 1)
    store = FakeAuditStore()
    sink = AuditSink(store, budget, queue_depth=10)
    record = from_decision(
        _decision_with_evidence(), org_id="org-a", phase=AuditPhase.INPUT, recorded_at_ns=1,
    )
    sink.emit(record)

    _run(sink.drain(1.0))

    assert budget.bytes_per_record("org-a") > len(record.serialize())
    assert budget.maxlen("org-a") <= int(64 * MIB / budget.bytes_per_record("org-a")) + 1


# --- the queue bound comes from the contract ------------------------------------------------------


def _contract(
    *,
    memory_limit: int = 2 * 1024 * 1024 * 1024,
    target_p99_ms: float = 20.0,
    utilization_cap: float = 0.75,
) -> ResourceContract:
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=memory_limit,
        fd_limit=65_536,
        guard_capacity=None,
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=400 * 1024 * 1024,
        worker_override=None,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )


def test_the_audit_queue_is_sized_by_the_measured_drain_rate() -> None:
    """A depth is a stall budget: drain rate x how long the writer may be stuck."""
    contract = _contract(target_p99_ms=20.0, utilization_cap=0.75)

    # stall budget = 20ms / 0.75 = 26.67ms; at 100,000 rec/s that is ~2,667 records.
    depth = contract.audit_queue_depth(100_000.0, bytes_per_record=3_000)

    assert depth == pytest.approx(2_667, abs=2)


def test_the_audit_queue_is_also_bounded_by_memory() -> None:
    """Without this, a fast drain rate authorises a queue that OOMs the worker it protects --
    trading a COUNTED audit loss for an UNCOUNTED request loss."""
    contract = _contract(memory_limit=256 * 1024 * 1024)

    by_memory = contract.audit_queue_depth(10_000_000.0, bytes_per_record=3_000)

    # 2% of (256 MiB x 0.75) over 3 KB per record.
    assert by_memory == int(256 * 1024 * 1024 * 0.75 * 0.02 / 3_000)
    assert by_memory < contract.audit_queue_depth(10_000_000.0, bytes_per_record=300)


def test_the_audit_queue_is_not_the_request_queue() -> None:
    """`queue_depth()` is bounded by per_worker_rss because its slots are in-flight REQUESTS.
    Sizing a few-KB record by a 400 MB worker RSS would give a depth of tens."""
    contract = _contract()

    requests = contract.queue_depth(contract.offered_service_rate())
    records = contract.audit_queue_depth(50_000.0, bytes_per_record=3_000)

    assert records > requests * 10


def test_a_nonsensical_audit_queue_is_refused_rather_than_rounded_up() -> None:
    contract = _contract()

    with pytest.raises(CapacityUnavailable, match="drain rate must be positive"):
        contract.audit_queue_depth(0.0, bytes_per_record=3_000)
    with pytest.raises(CapacityUnavailable, match="bytes per record must be positive"):
        contract.audit_queue_depth(1_000.0, bytes_per_record=0)


def test_a_worker_too_small_to_hold_one_record_refuses_to_start() -> None:
    """Every record would be dropped and counted. That is a configuration error, not a posture."""
    contract = _contract(memory_limit=1024 * 1024)

    with pytest.raises(CapacityUnavailable, match="below minimum to serve"):
        contract.audit_queue_depth(10.0, bytes_per_record=10 * 1024 * 1024)


def test_a_calibrated_rate_feeds_the_contract_end_to_end() -> None:
    """The wiring the card describes: measure the drain rate, derive the depth, build the sink."""
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 1)
    store = FakeAuditStore()
    probe = AuditSink(store, budget, queue_depth=1)

    rate = _run(probe.calibrate(records=100))
    depth = _contract().audit_queue_depth(rate, bytes_per_record=3_000)
    sink = AuditSink(store, budget, queue_depth=depth)

    assert depth >= 1
    assert sink.counters().capacity == depth
