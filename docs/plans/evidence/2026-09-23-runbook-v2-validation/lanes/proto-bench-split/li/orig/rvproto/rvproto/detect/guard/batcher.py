"""Cross-request micro-batcher: one dedicated inference thread per GPU (or CPU session).

Requests enqueue their windows without blocking the event loop. The thread forms a
batch of up to `max_batch` windows or waits at most `max_wait_ns` after the oldest
queued item, pads to the smallest declared sequence bucket, runs the session, and
resolves every future of the batch with ONE call_soon_threadsafe. Items past their
deadline, or whose request was cancelled, are answered without running. The queue is
bounded upstream by admission (admit/overload.py: pool_size(GUARD) in flight), so an
overloaded guard sheds requests with a 503 before any work, instead of degrading them.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from rvproto.domain.guard import Budget, GuardResult
from rvproto.runtime.metrics import Registry

Infer = Callable[[np.ndarray, np.ndarray], np.ndarray]  # (ids, mask) -> logits [n, 2]


@dataclass(slots=True)
class _Item:
    loop: asyncio.AbstractEventLoop
    fut: asyncio.Future[GuardResult]
    rows: Sequence[Sequence[int]]
    t_enq: int
    deadline: int
    tokens: int


def _bucket(n: int, buckets: tuple[int, ...]) -> int:
    for b in buckets:
        if n <= b:
            return b
    return n


def padded_cost(rows: Sequence[Sequence[int]], buckets: tuple[int, ...]) -> int:
    """Guard work of a request in padded tokens: what the backend actually computes."""
    return sum(_bucket(len(r), buckets) for r in rows)


Pairs = list[tuple[asyncio.Future[GuardResult], GuardResult]]
Obs = list[tuple[str, int]]


def _deliver(batch: Pairs, metrics: Registry, obs: Obs, incs: Obs) -> None:
    """Runs on the event-loop thread: metrics are only ever touched there."""
    for name, v in obs:
        metrics.observe(name, v)
    for name, v in incs:
        metrics.inc(name, v)
    for fut, res in batch:
        if not fut.done():
            fut.set_result(res)


def _softmax_p1(logits: np.ndarray) -> np.ndarray:
    z = logits.astype(np.float64)
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e[:, 1] / e.sum(axis=1)


class MicroBatcher:
    def __init__(
        self,
        infer: Infer,
        *,
        max_batch: int,
        max_wait_ns: int,
        buckets: tuple[int, ...],
        metrics: Registry,
        name: str,
    ) -> None:
        self.infer = infer
        self.max_batch = max_batch
        self.max_wait_ns = max_wait_ns
        self.buckets = buckets
        self.metrics = metrics
        self.name = name
        self._q: deque[_Item] = deque()
        self._queued_windows = 0
        self._queued_tokens = 0
        self._cv = threading.Condition()
        self._alive = False
        self._thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return self._alive

    def start(self) -> None:
        with self._cv:
            if self._alive:
                return  # idempotent: never join (or duplicate) a healthy consumer
        old = self._thread
        if old is not None and old.is_alive():
            old.join()  # a killed thread exits at its next wake-up; never run two consumers
        with self._cv:
            if self._alive:
                return
            self._alive = True
        self._thread = threading.Thread(target=self._run, name=f"guard-{self.name}", daemon=True)
        self._thread.start()

    def kill(self, why: str) -> None:
        """Simulated backend death: the thread exits, every pending item is UNAVAILABLE."""
        with self._cv:
            self._alive = False
            pending = list(self._q)
            self._q.clear()
            self._queued_windows = 0
            self._queued_tokens = 0
            self._cv.notify_all()
        self._fail(pending, f"guard backend down: {why}")

    def submit(self, rows: Sequence[Sequence[int]], budget: Budget) -> asyncio.Future[GuardResult]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[GuardResult] = loop.create_future()
        tokens = sum(len(r) for r in rows)
        with self._cv:
            if not self._alive:
                fut.set_result(_unavail("guard backend not ready"))
                return fut
            self._q.append(_Item(loop, fut, rows, time.perf_counter_ns(), budget.deadline_ns, tokens))
            self._queued_windows += len(rows)
            self._queued_tokens += tokens
            self._cv.notify()
        return fut

    def queue_stats(self) -> tuple[int, int, float]:
        """(queued items, queued windows, oldest queued age seconds) under the lock."""
        with self._cv:
            if not self._q:
                return 0, 0, 0.0
            return len(self._q), self._queued_windows, (time.perf_counter_ns() - self._q[0].t_enq) / 1e9

    def _take(self) -> list[_Item] | None:
        with self._cv:
            while self._alive and not self._q:
                self._cv.wait()
            if not self._alive:
                return None
            first = self._q[0].t_enq
            while self._alive and self._queued_windows < self.max_batch:
                remaining = first + self.max_wait_ns - time.perf_counter_ns()
                if remaining <= 0:
                    break
                self._cv.wait(remaining / 1e9)
            if not self._alive:
                return None
            batch: list[_Item] = []
            n = 0
            while self._q and (not batch or n + len(self._q[0].rows) <= self.max_batch):
                it = self._q.popleft()
                batch.append(it)
                n += len(it.rows)
                self._queued_windows -= len(it.rows)
                self._queued_tokens -= it.tokens
            return batch

    def _run(self) -> None:
        while True:
            batch = self._take()
            if batch is None:
                return
            now = time.perf_counter_ns()
            cancelled = [it for it in batch if it.fut.cancelled()]  # client went away
            if cancelled:
                self._fail(cancelled, "request cancelled", "guard_cancelled_skipped")
                batch = [it for it in batch if not it.fut.cancelled()]
            live = [it for it in batch if it.deadline > now]
            expired = [it for it in batch if it.deadline <= now]
            if expired:
                self._fail(expired, "guard deadline passed while queued", "guard_deadline_expired")
            if live:
                try:
                    self._execute(live)
                except Exception as exc:  # backend fault => UNAVAILABLE, never clean
                    self._fail(live, f"guard execution failed: {type(exc).__name__}", "guard_exec_errors")

    def _execute(self, items: list[_Item]) -> None:
        rows = [r for it in items for r in it.rows]
        seq = _bucket(max(len(r) for r in rows), self.buckets)
        ids = np.zeros((len(rows), seq), dtype=np.int64)
        mask = np.zeros((len(rows), seq), dtype=np.int64)
        for i, r in enumerate(rows):
            ids[i, : len(r)] = r
            mask[i, : len(r)] = 1
        t0 = time.perf_counter_ns()
        # an item larger than max_batch runs alone, in max_batch-sized slices
        parts = [
            _softmax_p1(self.infer(ids[i : i + self.max_batch], mask[i : i + self.max_batch]))
            for i in range(0, len(rows), self.max_batch)
        ]
        probs = parts[0] if len(parts) == 1 else np.concatenate(parts)
        t1 = time.perf_counter_ns()
        exec_ns = t1 - t0
        obs: Obs = [("guard_exec_ns", exec_ns), ("guard_batch_windows", len(rows))]
        incs: Obs = [("guard_batches", 1), ("guard_windows", len(rows))]
        by_loop: dict[asyncio.AbstractEventLoop, Pairs] = {}
        k = 0
        for it in items:
            p = tuple(float(x) for x in probs[k : k + len(it.rows)])
            k += len(it.rows)
            q_ns = t0 - it.t_enq
            obs.append(("guard_queue_ns", q_ns))
            res = GuardResult(True, p, q_ns, exec_ns, len(rows), "")
            by_loop.setdefault(it.loop, []).append((it.fut, res))
        for loop, pairs in by_loop.items():
            loop.call_soon_threadsafe(_deliver, pairs, self.metrics, obs, incs)
            obs, incs = [], []

    def _fail(self, items: list[_Item], why: str, counter: str = "guard_unavailable_batches") -> None:
        by_loop: dict[asyncio.AbstractEventLoop, Pairs] = {}
        for it in items:
            by_loop.setdefault(it.loop, []).append((it.fut, _unavail(why)))
        for loop, pairs in by_loop.items():
            try:
                loop.call_soon_threadsafe(_deliver, pairs, self.metrics, [], [(counter, len(pairs))])
            except RuntimeError:  # loop closed during shutdown
                pass


def _unavail(why: str) -> GuardResult:
    return GuardResult(False, (), 0, 0, 0, why)
