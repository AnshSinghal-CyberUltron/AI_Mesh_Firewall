"""The bounded audit producer: a synchronous emit, a batched writer, and counted loss.

`emit()` is `put_nowait` and nothing else. It does not await, it does not raise, and it does not
touch the store — the request path hands a record over and continues. Everything expensive
happens in `run()`, on its own task.

The queue is BOUNDED, which means audit can be lost, which means the loss has to be counted.
That is the whole posture, and it is the opposite of the one R2-11 measured: a flush erased 36%
of the records while `audit_completeness_ratio` read 1.0. So:

* `produced` counts every emit, including the ones that were dropped.
* `dropped` counts the emits that found the queue full.
* `written` counts what the store ACKNOWLEDGED.
* `acknowledged_ratio` is `written / produced` and is never assumed to be 1.0.

`acknowledged_ratio` is deliberately not called completeness. Acknowledged means "the store took
it", and a record the store took can still be trimmed before anything durable has it. The
card's `audit_completeness_ratio` is measured against the DURABLE high-water mark, which only the
exporter can know; naming this one completeness is how the two get conflated, and conflating
them is the defect.

On every batch the writer also:

* folds each payload's size into the per-org byte model, so the caps follow the traffic;
* trims every stream it touched to that org's derived cap, counting exactly what left.

A store error fails the BATCH, counted, and never propagates: an exception here would kill the
writer task, and a dead writer loses everything silently instead of losing a batch loudly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from gateway_v2.audit.budget import StreamBudget
from gateway_v2.audit.record import AuditRecord
from gateway_v2.runtime.store_audit import AuditBatchResult

LOG = logging.getLogger("amf.audit.sink")

DEFAULT_BATCH_MAX = 2_000
"""Records in one pipelined write.

Unbounded batching looks free -- it is one round trip either way -- but it builds an arbitrarily
large pipeline in memory and makes one store error fail an arbitrarily large number of records.
"""

CALIBRATION_RECORDS = 2_000
CALIBRATION_ORG = "_calibration"


class AuditStore(Protocol):
    """What the sink needs from the store. Structural, so a test drives it with a stub."""

    async def append(
        self,
        records: Sequence[tuple[str, bytes]],
        maxlens: Any,
    ) -> AuditBatchResult:
        ...


@dataclass(frozen=True, slots=True)
class SinkCounters:
    """A reading of the producer. Scalars only; no tenant appears in it."""

    produced: int = 0
    written: int = 0
    dropped: int = 0
    failed: int = 0
    trimmed: int = 0
    batches: int = 0
    capacity: int = 0
    """The configured queue bound, reported so a scrape can read "dropped" next to "how many it
    could have held". Named `capacity` rather than `queue_depth` because the DECISION of what
    that number is belongs to `runtime/resources.py`; this is a reading of it."""

    queued: int = 0

    @property
    def acknowledged_ratio(self) -> float:
        """`written / produced`. 1.0 with nothing produced, because nothing was lost."""
        return self.written / self.produced if self.produced else 1.0


class AuditSink:
    """Bounded producer plus batched writer. One per worker; `run()` owns the writing."""

    def __init__(
        self,
        store: AuditStore,
        budget: StreamBudget,
        *,
        queue_depth: int,
        batch_max: int = DEFAULT_BATCH_MAX,
        now_ns: Callable[[], int] = time.monotonic_ns,
        sampler: Any | None = None,
    ) -> None:
        """`queue_depth` has no default on purpose.

        GW14's card states the bound: *"the contract's `queue_depth()` at the writer's measured
        drain rate"*. A depth is a stall budget in disguise — at a measured drain rate it is how
        long the writer may stall before the request path starts losing records — so a literal
        here would be a capacity decision taken in the wrong module. `queue_depth_for()` derives
        it from the `ResourceContract` and `calibrate()` measures the rate to feed it.
        """
        if queue_depth <= 0:
            raise ValueError("queue_depth must be positive; a zero-depth queue drops everything")
        if batch_max <= 0:
            raise ValueError("batch_max must be positive")
        self._store = store
        self._budget = budget
        self._queue: asyncio.Queue[AuditRecord] = asyncio.Queue(maxsize=queue_depth)
        self._queue_depth = queue_depth
        self._batch_max = batch_max
        self._now_ns = now_ns
        self._sampler = sampler
        self._produced = 0
        self._written = 0
        self._dropped = 0
        self._failed = 0
        self._trimmed = 0
        self._batches = 0
        self._warned_full = False
        self._written_by_org: dict[str, int] = {}

    # --- the request path ------------------------------------------------------------------------

    def emit(self, record: AuditRecord) -> bool:
        """Hand over one record. Never awaits, never raises. False means it was dropped.

        The return value exists so a caller that cares can act on it; nothing on the request path
        is expected to. What matters is that the drop was COUNTED, which happens either way.
        """
        self._produced += 1
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._dropped += 1
            if not self._warned_full:
                LOG.warning(
                    "audit_queue_full depth=%d: records are being DROPPED and counted in "
                    "audit_dropped_records. The writer is not draining fast enough -- check the "
                    "store, then the queue depth against the measured drain rate",
                    self._queue_depth,
                )
                self._warned_full = True
            return False
        return True

    # --- counters --------------------------------------------------------------------------------

    def counters(self) -> SinkCounters:
        return SinkCounters(
            produced=self._produced,
            written=self._written,
            dropped=self._dropped,
            failed=self._failed,
            trimmed=self._trimmed,
            batches=self._batches,
            capacity=self._queue_depth,
            queued=self._queue.qsize(),
        )

    @property
    def trimmed(self) -> int:
        """Records `XTRIM` removed to hold the budget. Exact, because XTRIM reports it."""
        return self._trimmed

    def acknowledged_by_org(self) -> Mapping[str, int]:
        """Lifetime records the store acknowledged, per tenant. The exporter's `acknowledged`.

        This exists because the information is destroyed by the thing it measures: once a record
        is trimmed, nothing in the store remembers it was ever there. The writer is the last
        component that can count it, so it does, at the moment of the append.
        """
        return dict(self._written_by_org)

    # --- the writer ------------------------------------------------------------------------------

    async def run(self) -> None:
        """Write until cancelled. On worker 0 the memory sampler runs beside this, in the same
        task group, so the two live and die together."""
        if self._sampler is None:
            await self._write_loop()
            return
        async with asyncio.TaskGroup() as group:
            group.create_task(self._sampler.run())
            group.create_task(self._write_loop())

    async def _write_loop(self) -> None:
        while True:
            first = await self._queue.get()
            await self._write(self._collect(first))

    def _collect(self, first: AuditRecord) -> list[AuditRecord]:
        """Take what is already queued, up to the batch cap. Never waits for more."""
        batch = [first]
        while len(batch) < self._batch_max:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _write(self, batch: Sequence[AuditRecord]) -> AuditBatchResult | None:
        """Serialize, size, append and trim. Returns None when the store refused the batch."""
        payloads: list[tuple[str, bytes]] = []
        for record in batch:
            payload = record.serialize()
            self._budget.observe(record.org_id, len(payload))
            payloads.append((record.org_id, payload))
        orgs = {org for org, _ in payloads}
        maxlens = self._budget.maxlens(orgs)

        self._batches += 1
        try:
            result = await self._store.append(payloads, maxlens)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a store error fails the batch, never the writer
            self._failed += len(batch)
            LOG.warning(
                "audit_write_failed records=%d error=%s (counted in audit_failed_records; the "
                "writer continues)",
                len(batch),
                f"{type(exc).__name__}: {exc}"[:300],
            )
            return None
        self._written += result.written
        self._trimmed += result.trimmed
        for org, count in result.written_by_org.items():
            self._written_by_org[org] = self._written_by_org.get(org, 0) + count
        return result

    async def drain(self, timeout_s: float) -> bool:
        """Flush what is queued, for shutdown. True if the queue emptied inside the timeout.

        Shutdown is a known, bounded loss opportunity, so it gets a bounded wait rather than
        either dropping the tail or blocking a deployment on it.
        """
        deadline = self._now_ns() + int(timeout_s * 1_000_000_000)
        while not self._queue.empty():
            if self._now_ns() >= deadline:
                LOG.warning(
                    "audit_drain_timeout queued=%d: the remaining records stay in the queue and "
                    "are lost with the process; they are counted as produced but not written",
                    self._queue.qsize(),
                )
                return False
            try:
                first = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._write(self._collect(first))
        return True

    # --- sizing the queue ------------------------------------------------------------------------

    async def calibrate(self, records: int = CALIBRATION_RECORDS) -> float:
        """Measured records/second of one pipelined batch against the REAL store.

        The queue depth is a stall budget, and a stall budget computed from a guessed drain rate
        is a guess. This writes to a dedicated calibration stream so no tenant's audit is
        polluted by a measurement.
        """
        if records <= 0:
            raise ValueError("calibration needs a positive record count")
        payload = b'{"calibration":true}'
        batch = [(CALIBRATION_ORG, payload)] * records
        started = time.perf_counter()
        await self._store.append(batch, {CALIBRATION_ORG: records})
        elapsed = time.perf_counter() - started
        return records / elapsed if elapsed > 0 else float("inf")
