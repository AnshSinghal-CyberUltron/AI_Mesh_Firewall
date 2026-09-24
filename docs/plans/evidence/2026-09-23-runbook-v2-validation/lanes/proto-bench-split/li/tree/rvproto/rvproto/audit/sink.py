"""Bounded, never-awaited audit producer + background batched writer (Redis XADD).

emit() is synchronous put_nowait: the request path never waits on audit. On a
full queue the record is DROPPED AND COUNTED; audit_completeness_ratio =
written / produced is exported, never assumed 1. The queue bound is the
contract's queue_depth() at the writer's measured drain rate.
"""

from __future__ import annotations

import asyncio
import time

import orjson
import redis.asyncio as aioredis

from rvproto.domain.ports import DecisionRecord
from rvproto.runtime.metrics import Registry
from rvproto.runtime.store import K_AUDIT_PREFIX, StoreError

_CALIBRATION_RECORDS = 2000  # sample size for the drain-rate measurement
_CALIBRATION_STREAM = K_AUDIT_PREFIX + "_calibration"


class AuditSink:
    def __init__(self, r: aioredis.Redis, metrics: Registry, *, stream_maxlen: int) -> None:
        self.r = r
        self.metrics = metrics
        self.maxlen = stream_maxlen
        self.q: asyncio.Queue[tuple[int, DecisionRecord]] | None = None
        self.produced = 0
        self.dropped = 0
        self.written = 0
        self.failed = 0

    async def calibrate(self) -> float:
        """Measured records/s of one pipelined batch against the real store."""
        payload = orjson.dumps({"calibration": True, "pad": "x" * 512})
        t0 = time.perf_counter()
        pipe = self.r.pipeline(transaction=False)
        for _ in range(_CALIBRATION_RECORDS):
            pipe.xadd(_CALIBRATION_STREAM, {"r": payload})
        await pipe.execute()
        rate = _CALIBRATION_RECORDS / (time.perf_counter() - t0)
        await self.r.delete(_CALIBRATION_STREAM)
        return rate

    def configure(self, queue_size: int) -> None:
        self.q = asyncio.Queue(maxsize=queue_size)
        self.metrics.set("audit_queue_bound", queue_size)

    def emit(self, rec: DecisionRecord) -> bool:
        self.produced += 1
        if self.q is None:
            self.dropped += 1
            self.metrics.inc("audit_dropped")
            return False
        try:
            self.q.put_nowait((time.perf_counter_ns(), rec))
        except asyncio.QueueFull:
            self.dropped += 1
            self.metrics.inc("audit_dropped")
            return False
        self.metrics.inc("audit_enqueued")
        return True

    def queue_stats(self) -> tuple[int, float]:
        """(depth, oldest record age seconds); peeks the queue's FIFO without consuming."""
        if self.q is None or self.q.empty():
            return 0, 0.0
        oldest = self.q._queue[0][0]  # type: ignore[attr-defined]
        return self.q.qsize(), (time.perf_counter_ns() - oldest) / 1e9

    def completeness(self) -> float:
        return self.written / self.produced if self.produced else 1.0

    async def run(self) -> None:
        assert self.q is not None
        q = self.q
        while True:
            first = await q.get()
            batch = [first[1]]
            while not q.empty():
                batch.append(q.get_nowait()[1])
            pipe = self.r.pipeline(transaction=False)
            for rec in batch:
                pipe.xadd(K_AUDIT_PREFIX + rec.org_id, {"r": orjson.dumps(rec)},
                          maxlen=self.maxlen, approximate=True)
            t0 = time.perf_counter_ns()
            try:
                await pipe.execute()
                self.written += len(batch)
                self.metrics.inc("audit_written", len(batch))
            except StoreError:
                self.failed += len(batch)
                self.metrics.inc("audit_write_failed", len(batch))
            self.metrics.observe("audit_batch_write_ns", time.perf_counter_ns() - t0)
            self.metrics.set("audit_completeness_ratio", self.completeness())

    async def drain(self, timeout_s: float) -> None:
        if self.q is None:
            return
        end = time.monotonic() + timeout_s
        while not self.q.empty() and time.monotonic() < end:
            await asyncio.sleep(0.01)
