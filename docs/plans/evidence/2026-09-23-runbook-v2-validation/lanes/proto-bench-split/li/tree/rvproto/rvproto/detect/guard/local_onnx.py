"""In-process ONNX Runtime guard: local_cpu (CPU EP) or local_gpu (TensorRT EP).

local_gpu: TensorRT EP with fp16 + engine/timing cache and an explicit shape
profile so no shape triggers a rebuild at request time; CUDA EP is registered
only when RV_GUARD_ALLOW_CUDA_FALLBACK=1. A session whose first provider is not
the selected one is REFUSED (never a silent CPU fallback presented as the model).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Sequence

import numpy as np
import onnxruntime as ort

from rvproto.detect.guard.batcher import MicroBatcher
from rvproto.domain.guard import Budget, GuardResult, Readiness
from rvproto.runtime.config import Settings
from rvproto.runtime.contract import CapacityHint
from rvproto.runtime.metrics import Registry


class LocalOnnxBackend:
    def __init__(
        self,
        s: Settings,
        metrics: Registry,
        *,
        gpu: bool,
        device_id: int,
        model_hash: str,
    ) -> None:
        self.name = "local_gpu" if gpu else "local_cpu"
        self.s = s
        self.metrics = metrics
        self.gpu = gpu
        self.device_id = device_id
        self.model_hash = model_hash
        self.max_seq = max(s.guard_seq_buckets)
        self.batch_cap = s.guard_batch  # the batcher slices larger items into batch_cap rows
        self.session: ort.InferenceSession | None = None
        self.batcher = MicroBatcher(
            self._infer,
            max_batch=s.guard_batch,
            max_wait_ns=s.guard_wait_us * 1000,
            buckets=s.guard_seq_buckets,
            metrics=metrics,
            name=f"{self.name}{device_id}",
        )
        self.tokens_per_s: float | None = None
        self.detail = "starting"
        self.providers: list[str] = []

    def _providers(self) -> list[object]:
        if not self.gpu:
            return ["CPUExecutionProvider"]
        os.makedirs(self.s.trt_cache_dir, exist_ok=True)

        def shape(n: int, seq: int) -> str:
            return f"input_ids:{n}x{seq},attention_mask:{n}x{seq}"

        # batch 1 + a single 512 bucket => ONE exact-shape 1x512 engine (guard-bench: fastest on L4)
        lo = min(self.s.guard_seq_buckets)
        trt = {
            "device_id": self.device_id,
            "trt_fp16_enable": True,
            "trt_engine_cache_enable": True,
            "trt_engine_cache_path": self.s.trt_cache_dir,
            "trt_timing_cache_enable": True,
            "trt_timing_cache_path": self.s.trt_cache_dir,
            "trt_profile_min_shapes": shape(1, lo),
            "trt_profile_opt_shapes": shape(self.batch_cap, self.max_seq),
            "trt_profile_max_shapes": shape(self.batch_cap, self.max_seq),
        }
        out: list[object] = [("TensorrtExecutionProvider", trt)]
        if self.s.guard_allow_cuda_fallback:
            out.append(("CUDAExecutionProvider", {"device_id": self.device_id}))
        return out

    def _make_session(self) -> ort.InferenceSession:
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.inter_op_num_threads = 1
        if not self.gpu:
            so.intra_op_num_threads = self.s.guard_cpu_threads
        else:
            so.intra_op_num_threads = 1
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            so.add_session_config_entry("session.inter_op.allow_spinning", "0")
        sess = ort.InferenceSession(self.s.guard_model, sess_options=so, providers=self._providers())
        # without this ORT's Python wrapper silently re-creates the session on the next EP after a
        # TensorRT error, i.e. a substituted backend presented as the selected one (LGW08-4)
        sess.disable_fallback()
        self.providers = list(sess.get_providers())
        want = "TensorrtExecutionProvider" if self.gpu else "CPUExecutionProvider"
        if self.providers[0] != want:
            raise RuntimeError(f"selected {want} but session runs {self.providers}")
        return sess

    def _infer(self, ids: np.ndarray, mask: np.ndarray) -> np.ndarray:
        assert self.session is not None
        return self.session.run(["logits"], {"input_ids": ids, "attention_mask": mask})[0]

    def _warmup(self) -> float:
        """Every (batch, bucket) shape the batcher can produce, then a timed throughput run."""
        for seq in self.s.guard_seq_buckets:
            for b in sorted({1, self.s.guard_batch, self.batch_cap}):
                self._infer(np.ones((b, seq), np.int64), np.ones((b, seq), np.int64))
        b, seq = self.s.guard_batch, self.max_seq
        ids = np.ones((b, seq), np.int64)
        reps = 0
        t0 = time.perf_counter()
        while reps < 3 or time.perf_counter() - t0 < 1.0:
            self._infer(ids, ids)
            reps += 1
        return reps * b * seq / (time.perf_counter() - t0)

    async def start(self) -> None:
        if self.s.guard_warmup_delay_s > 0:
            self.detail = f"warm-up delayed {self.s.guard_warmup_delay_s}s"
            await asyncio.sleep(self.s.guard_warmup_delay_s)
        try:
            self.session = await asyncio.to_thread(self._make_session)
            self.tokens_per_s = await asyncio.to_thread(self._warmup)
        except Exception as exc:
            self.detail = f"refused: {exc}"
            raise
        self.batcher.start()
        self.detail = f"ready providers={self.providers} tokens_per_s={self.tokens_per_s:.0f}"

    def submit(self, rows: Sequence[Sequence[int]], budget: Budget) -> asyncio.Future[GuardResult]:
        return self.batcher.submit(rows, budget)

    def queue_stats(self) -> tuple[int, int, float]:
        return self.batcher.queue_stats()

    async def classify(self, rows: Sequence[Sequence[int]], budget: Budget) -> GuardResult:
        return await self.submit(rows, budget)

    async def readiness(self) -> Readiness:
        ok = self.session is not None and self.batcher.alive
        return Readiness(ok, self.name, self.model_hash, self.detail)

    def capacity_hint(self) -> CapacityHint | None:
        if self.tokens_per_s is None:
            return None
        return CapacityHint(tokens_per_second=self.tokens_per_s)

    def kill(self, why: str) -> None:
        self.detail = f"killed: {why}"
        self.batcher.kill(why)

    def revive(self) -> None:
        if self.session is not None:
            self.batcher.start()
            self.detail = "revived"

    async def close(self) -> None:
        self.batcher.kill("shutdown")
