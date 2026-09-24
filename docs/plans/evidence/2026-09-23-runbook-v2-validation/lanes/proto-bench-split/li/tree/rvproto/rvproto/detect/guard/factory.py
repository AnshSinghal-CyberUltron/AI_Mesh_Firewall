"""GuardBackend selection is deployment configuration, never a code change.

Engines are imported lazily: a gateway-only node (RV_GUARD_TOPOLOGY=remote) needs neither
onnxruntime nor tritonclient.
"""

from __future__ import annotations

import math
import os
import subprocess
from typing import TYPE_CHECKING

from rvproto.detect.guard import wire
from rvproto.detect.guard.ipc_client import OwnerClientBackend
from rvproto.detect.semantic import file_hash
from rvproto.runtime.config import Settings
from rvproto.runtime.metrics import Registry

if TYPE_CHECKING:
    from rvproto.detect.guard.local_onnx import LocalOnnxBackend
    from rvproto.detect.guard.triton_grpc import TritonBackend

    GuardImpl = LocalOnnxBackend | TritonBackend | OwnerClientBackend


def model_hash(s: Settings) -> str:
    pinned = os.environ.get("RV_GUARD_MODEL_SHA256", "")
    if pinned:
        return pinned
    return file_hash(s.guard_model) if s.guard_model else "unknown"


def gpu_count(s: Settings) -> int:
    if s.guard_n_gpus is not None:
        return s.guard_n_gpus
    out = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=True).stdout
    n = sum(1 for line in out.splitlines() if line.startswith("GPU "))
    if n < 1:
        raise RuntimeError("local_gpu selected but no GPU is visible")
    return n


def owner_count(s: Settings) -> int:
    """Owner processes on a node: one per GPU (local_gpu) or a single CPU owner (local_cpu)."""
    return gpu_count(s) if s.guard_backend == "local_gpu" else 1


def sharing(workers: int, owners: int) -> int:
    return max(1, math.ceil(workers / owners))


def build(s: Settings, metrics: Registry) -> GuardImpl:
    h = model_hash(s)
    workers = int(os.environ.get("RV_WORKER_COUNT", "1"))
    if s.guard_topology == "remote":  # split topology: every owner of the guard nodes, over TCP
        eps = [wire.Endpoint("tcp", host, port) for host, port in s.guard_owner_addrs]
        return OwnerClientBackend(s, metrics, endpoints=eps, sharing=s.guard_fleet_workers or workers,
                                  model_hash=h, name="remote(owner)")
    if s.guard_topology == "owner" and s.guard_backend in ("local_cpu", "local_gpu"):
        n = owner_count(s)
        ep = wire.Endpoint("unix", wire.socket_path(s.guard_socket_dir, s.worker_index % n))
        return OwnerClientBackend(s, metrics, endpoints=[ep], sharing=sharing(workers, n),
                                  model_hash=h, name=f"{s.guard_backend}(owner)")
    if s.guard_backend in ("local_cpu", "local_gpu"):
        from rvproto.detect.guard.local_onnx import LocalOnnxBackend

        gpu = s.guard_backend == "local_gpu"
        device = s.worker_index % gpu_count(s) if gpu else 0
        return LocalOnnxBackend(s, metrics, gpu=gpu, device_id=device, model_hash=h)
    if s.guard_backend == "triton_grpc":
        from rvproto.detect.guard.triton_grpc import TritonBackend

        return TritonBackend(s, metrics, model_hash=h)
    raise ValueError(f"unknown RV_GUARD_BACKEND={s.guard_backend!r}")
