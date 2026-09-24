"""Triton Inference Server guard over gRPC (node-local or off-box, dynamic batching).

Each request's windows go in ONE infer call padded to a declared bucket so the
server's dynamic batcher can merge requests of equal shape across worker processes.
The per-call deadline is the request's guard budget; any RPC failure or server
unreadiness yields UNAVAILABLE, never a clean pass.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

import numpy as np

from rvproto.detect.guard.batcher import _bucket, _softmax_p1
from rvproto.domain.guard import Budget, GuardResult, Readiness
from rvproto.runtime.config import Settings
from rvproto.runtime.contract import CapacityHint
from rvproto.runtime.metrics import Registry


def _unavail(why: str) -> GuardResult:
    return GuardResult(False, (), 0, 0, 0, why)


class TritonBackend:
    name = "triton_grpc"

    def __init__(self, s: Settings, metrics: Registry, *, model_hash: str) -> None:
        self.s = s
        self.metrics = metrics
        self.model_hash = model_hash
        self.client: object | None = None
        self.ready = False
        self.detail = "starting"
        self.tokens_per_s: float | None = None
        self._poller: asyncio.Task[None] | None = None
        self._killed = False

    async def start(self) -> None:
        import tritonclient.grpc.aio as grpcclient

        self._grpc = grpcclient
        # Bound gRPC reconnect backoff by the declared refresh period: gRPC's default
        # (up to 120 s) left some workers UNAVAILABLE long after a Triton restart.
        backoff_ms = int(self.s.ks_refresh_ms)
        self.client = grpcclient.InferenceServerClient(
            url=self.s.triton_url,
            channel_args=[("grpc.initial_reconnect_backoff_ms", backoff_ms),
                          ("grpc.min_reconnect_backoff_ms", backoff_ms),
                          ("grpc.max_reconnect_backoff_ms", backoff_ms)],
        )
        deadline = time.monotonic() + self.s.provider_timeout_s * 10
        while not await self._model_ready():
            if time.monotonic() > deadline:
                raise RuntimeError("triton model never became ready")
            await asyncio.sleep(self.s.ks_refresh_ms / 1000.0)
        self.tokens_per_s = await self._measure()
        self.ready = True
        self.detail = f"ready url={self.s.triton_url} tokens_per_s={self.tokens_per_s:.0f}"
        self._poller = asyncio.create_task(self._poll())

    async def _model_ready(self) -> bool:
        try:
            return bool(await self.client.is_model_ready(self.s.triton_model))  # type: ignore[attr-defined]
        except Exception:
            return False

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(self.s.ks_refresh_ms / 1000.0)
            ok = (not self._killed) and await self._model_ready()
            if ok != self.ready:
                self.metrics.inc("guard_readiness_flips")
            self.ready = ok
            self.detail = "ready" if ok else "triton model not ready"

    async def _measure(self) -> float:
        rows = [[1] * max(self.s.guard_seq_buckets)] * self.s.guard_batch
        far = time.perf_counter_ns() + int(60e9)
        for _ in range(3):
            await self._infer(rows, Budget(far), record=False)
        t0 = time.perf_counter()
        calls = 0
        while calls < 8 or time.perf_counter() - t0 < 1.0:
            res = await asyncio.gather(*(self._infer(rows, Budget(far), record=False) for _ in range(4)))
            if not all(r.ok for r in res):
                raise RuntimeError(f"triton warm-up failed: {res[0].detail}")
            calls += 4
        return calls * len(rows) * len(rows[0]) / (time.perf_counter() - t0)

    async def _infer(self, rows: Sequence[Sequence[int]], budget: Budget, record: bool = True) -> GuardResult:
        remaining = (budget.deadline_ns - time.perf_counter_ns()) / 1e9
        if remaining <= 0:
            return _unavail("guard budget exhausted before dispatch")
        seq = _bucket(max(len(r) for r in rows), self.s.guard_seq_buckets)
        ids = np.zeros((len(rows), seq), dtype=np.int64)
        mask = np.zeros((len(rows), seq), dtype=np.int64)
        for i, r in enumerate(rows):
            ids[i, : len(r)] = r
            mask[i, : len(r)] = 1
        g = self._grpc
        inputs = [g.InferInput("input_ids", list(ids.shape), "INT64"),
                  g.InferInput("attention_mask", list(mask.shape), "INT64")]
        inputs[0].set_data_from_numpy(ids)
        inputs[1].set_data_from_numpy(mask)
        t0 = time.perf_counter_ns()
        try:
            resp = await self.client.infer(  # type: ignore[attr-defined]
                self.s.triton_model,
                inputs,
                outputs=[g.InferRequestedOutput("logits")],
                client_timeout=remaining,
            )
        except Exception as exc:
            if record:
                self.metrics.inc("guard_rpc_errors")
            return _unavail(f"triton rpc failed: {type(exc).__name__}")
        rpc = time.perf_counter_ns() - t0
        if record:
            self.metrics.observe("guard_rpc_ns", rpc)
            self.metrics.observe("guard_batch_windows", len(rows))
        probs = _softmax_p1(resp.as_numpy("logits"))
        return GuardResult(True, tuple(float(x) for x in probs), 0, rpc, len(rows), "")

    def submit(self, rows: Sequence[Sequence[int]], budget: Budget) -> asyncio.Future[GuardResult]:
        loop = asyncio.get_running_loop()
        if not self.ready:
            fut: asyncio.Future[GuardResult] = loop.create_future()
            fut.set_result(_unavail(f"guard backend not ready: {self.detail}"))
            return fut
        return asyncio.ensure_future(self._infer(rows, budget))

    async def classify(self, rows: Sequence[Sequence[int]], budget: Budget) -> GuardResult:
        return await self.submit(rows, budget)

    async def readiness(self) -> Readiness:
        return Readiness(self.ready, self.name, self.model_hash, self.detail)

    def capacity_hint(self) -> CapacityHint | None:
        if self.tokens_per_s is None:
            return None
        return CapacityHint(tokens_per_second=self.tokens_per_s)

    def kill(self, why: str) -> None:
        self._killed = True
        self.ready = False
        self.detail = f"killed: {why}"

    def revive(self) -> None:
        self._killed = False

    async def close(self) -> None:
        if self._poller is not None:
            self._poller.cancel()
        if self.client is not None:
            await self.client.close()  # type: ignore[attr-defined]
