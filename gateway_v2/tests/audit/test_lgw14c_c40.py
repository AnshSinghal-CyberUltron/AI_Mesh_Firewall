"""GW14c / C40: every admitted request gets a record, sheds included, and the denominator is
measured against ADMITTED requests.

The measured defect: the prototype's completeness ratio read 1.0 while up to 5.75% of admitted
requests were shed WITHOUT a record. A denominator of "requests we managed to serve" cannot
surface that, because an unrecorded shed is missing from both halves of the fraction at once. So
the assertions here are about the denominator as much as about the record.

Also locked: a shed carries a REASON. R2-11's M2 finding is `rejected_503` merging 9,241
kill-switch 503s with 8 store-outage 503s into one counter, which is an alarm nobody can act on.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Mapping, Sequence

import pytest

from gateway_v2.audit.budget import StreamBudget
from gateway_v2.audit.metrics import AuditMetricsRecorder, DurableReading
from gateway_v2.audit.record import (
    REJECTED,
    SHED,
    AuditPhase,
    admission,
    shed,
)
from gateway_v2.audit.sink import AuditSink
from gateway_v2.domain.audit_knobs import AuditMemoryKnobs
from gateway_v2.runtime.store_audit import AuditBatchResult


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


class CountingStore:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    async def append(
        self, records: Sequence[tuple[str, bytes]], maxlens: Mapping[str, int],
    ) -> AuditBatchResult:
        del maxlens
        by_org: dict[str, int] = {}
        for org, payload in records:
            self.payloads.append(payload)
            by_org[org] = by_org.get(org, 0) + 1
        return AuditBatchResult(written=len(records), trimmed=0, written_by_org=by_org)


def _sink() -> tuple[AuditSink, CountingStore]:
    store = CountingStore()
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 1)
    return AuditSink(store, budget, queue_depth=1_000), store


# --- a shed is a decision, not an absence ---------------------------------------------------------


def test_a_shed_produces_exactly_one_record() -> None:
    sink, store = _sink()

    sink.emit(
        shed(
            request_id="req-1",
            org_id="org-a",
            reason="owner_queue_codel",
            recorded_at_ns=1,
            retry_after_ms=1_000,
        ),
    )
    _run(sink.drain(1.0))

    assert len(store.payloads) == 1
    body = json.loads(store.payloads[0])
    assert body["phase"] == AuditPhase.ADMISSION.value
    assert body["outcome"] == SHED
    assert body["detail"]["status"] == 503


def test_a_shed_carries_its_reason() -> None:
    """R2-11 (M2): `rejected_503` merged 9,241 kill-switch 503s with 8 store-outage 503s. One
    counter for every cause is an alarm nobody can act on."""
    record = shed(
        request_id="req-1",
        org_id="org-a",
        reason="shared_state_unavailable",
        recorded_at_ns=1,
        retry_after_ms=2_000,
    )

    assert record.detail["reason"] == "shared_state_unavailable"


def test_a_shed_carries_its_retry_after() -> None:
    """R2-08 measured sheds going out with 6-11 ms, which the OpenAI SDK honours into an
    immediate retry. The value is part of what the shed DID."""
    record = shed(
        request_id="req-1",
        org_id="org-a",
        reason="owner_queue_codel",
        recorded_at_ns=1,
        retry_after_ms=1_000,
    )

    assert record.detail["retry_after_ms"] == 1_000


def test_an_admission_reject_produces_a_record_with_its_own_outcome() -> None:
    record = admission(
        request_id="req-1",
        org_id="org-a",
        outcome=REJECTED,
        reason="killswitch_org",
        recorded_at_ns=1,
        status=403,
    )

    assert record.outcome == REJECTED
    assert record.phase is AuditPhase.ADMISSION
    assert record.detail == {"reason": "killswitch_org", "status": 403}


def test_a_reasonless_admission_record_is_refused() -> None:
    with pytest.raises(ValueError, match="needs a reason"):
        admission(
            request_id="req-1",
            org_id="org-a",
            outcome=REJECTED,
            reason="",
            recorded_at_ns=1,
            status=403,
        )


def test_an_admission_record_still_needs_a_tenant() -> None:
    """A shed is budgeted, trimmed and exported like any other record."""
    with pytest.raises(ValueError, match="needs an org_id"):
        shed(
            request_id="req-1", org_id="", reason="codel", recorded_at_ns=1, retry_after_ms=1,
        )


# --- the denominator ------------------------------------------------------------------------------


def test_sheds_count_towards_the_same_totals_as_served_requests() -> None:
    """They go through the same producer, so they are in the same `produced` and the same
    `acknowledged_by_org` the exporter subtracts against."""
    sink, _ = _sink()

    for index in range(3):
        sink.emit(
            shed(
                request_id=f"req-{index}",
                org_id="org-a",
                reason="owner_queue_codel",
                recorded_at_ns=index,
                retry_after_ms=1_000,
            ),
        )
    _run(sink.drain(1.0))

    assert sink.counters().produced == 3
    assert sink.acknowledged_by_org() == {"org-a": 3}


def test_an_unrecorded_shed_moves_the_completeness_ratio() -> None:
    """The exact defect C40 names: the prototype read 1.0 while 5.75% of admitted requests were
    shed without a record. Measured against ADMITTED, the gap is visible."""
    admitted = 10_000
    shed_without_record = 575  # 5.75%

    recorder = AuditMetricsRecorder()
    recorder.observe_durable(
        DurableReading(
            durable=admitted - shed_without_record,
            acknowledged_high_water=admitted,
            records_lost=shed_without_record,
        ),
    )

    ratio = recorder.snapshot().durable.completeness_ratio

    assert ratio == pytest.approx(0.9425)
    assert ratio < 1.0, "a denominator of served-requests would have reported 1.0 here"


def test_recording_every_shed_reads_complete() -> None:
    recorder = AuditMetricsRecorder()
    recorder.observe_durable(
        DurableReading(durable=10_000, acknowledged_high_water=10_000, records_lost=0),
    )

    assert recorder.snapshot().durable.completeness_ratio == 1.0
