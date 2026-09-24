"""Micro-batcher: cross-request batching, deadline/cancel skipping, kill => UNAVAILABLE."""

from __future__ import annotations

import asyncio
import threading
import time

import numpy as np

from rvproto.detect.guard.batcher import MicroBatcher
from rvproto.domain.guard import Budget
from rvproto.runtime.metrics import Registry


def _batcher(delay: float, calls: list[int]) -> MicroBatcher:
    gate = threading.Lock()

    def infer(ids: np.ndarray, mask: np.ndarray) -> np.ndarray:
        with gate:
            calls.append(len(ids))
            time.sleep(delay)
        out = np.zeros((len(ids), 2), np.float32)
        out[:, 1] = ids[:, 1] % 2  # token parity -> "malicious" for odd ids
        return out

    return MicroBatcher(infer, max_batch=4, max_wait_ns=2_000_000, buckets=(8, 16),
                        metrics=Registry(0), name="t")


def _far() -> Budget:
    return Budget(time.perf_counter_ns() + int(10e9))


async def test_batches_across_requests_and_splits_large_items() -> None:
    calls: list[int] = []
    b = _batcher(0.0, calls)
    b.start()
    futs = [b.submit([[1, k, 2]], _far()) for k in range(3)] + [b.submit([[1, 3, 2]] * 6, _far())]
    res = await asyncio.gather(*futs)
    assert all(r.ok for r in res)
    assert [len(r.p_malicious) for r in res] == [1, 1, 1, 6]
    assert sum(calls) == 9 and max(calls) <= 4
    b.kill("done")


async def test_cancelled_and_expired_items_are_skipped_not_run() -> None:
    calls: list[int] = []
    b = _batcher(0.2, calls)
    b.start()
    first = b.submit([[1, 1, 2]] * 4, _far())  # occupies the thread for 0.2 s
    await asyncio.sleep(0.05)
    cancelled = b.submit([[1, 1, 2]], _far())
    expired = b.submit([[1, 1, 2]], Budget(time.perf_counter_ns() + 1_000_000))
    cancelled.cancel()
    assert (await first).ok
    res = await expired
    assert not res.ok and "deadline" in res.detail
    await asyncio.sleep(0.05)
    assert calls == [4]  # neither the cancelled nor the expired item reached the model
    assert b.metrics.count.get("guard_cancelled_skipped") == 1
    b.kill("done")


async def test_kill_answers_pending_unavailable_and_start_is_idempotent() -> None:
    calls: list[int] = []
    b = _batcher(0.2, calls)
    b.start()
    b.start()
    busy = b.submit([[1, 1, 2]] * 4, _far())
    await asyncio.sleep(0.05)
    pending = b.submit([[1, 1, 2]], _far())
    b.kill("test")
    res = await pending
    assert not res.ok and "down" in res.detail
    assert not (await b.submit([[1, 1, 2]], _far())).ok
    await busy
    b.start()
    b.start()
    assert (await b.submit([[1, 1, 2]], _far())).ok
    b.kill("done")
