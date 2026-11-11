"""GuardBackend public surface for the GW08 detector framework (§10.5.4).

Re-exports the ``GuardBackend`` contract, its frozen value types, and the four
backend slots so the registry (Task 4) and the semantic detector (GW10) import one
cohesive surface. The detector sees only this surface — never CPU/GPU/TensorRT/
Triton/batch/replica detail (topology-blindness, C7/C23).

Factory choice (option a): the three seam modules (``local_trt``, ``triton_grpc``,
``remote_http``) each expose a module-level ``make_backend`` with the SAME name, so
re-exporting all three unqualified would collide. We therefore DO NOT re-export the
per-seam ``make_backend`` from this package; the registry references each seam
module directly (e.g. ``gateway_v2.detect.guard.local_trt.make_backend``). Only
``local_onnx``'s distinctly named ``build_local_onnx`` factory is surfaced here.
"""

from __future__ import annotations

from gateway_v2.detect.guard.backend import (
    BackendCapacity,
    Budget,
    GuardBackend,
    GuardResult,
    Readiness,
    ReadinessTracker,
)
from gateway_v2.detect.guard.local_onnx import (
    BackendUnavailable,
    LocalOnnxBackend,
    build_local_onnx,
)
from gateway_v2.detect.guard.local_trt import DeferredBackendError, LocalTrtBackend
from gateway_v2.detect.guard.remote_http import RemoteHttpBackend
from gateway_v2.detect.guard.triton_grpc import TritonGrpcBackend

__all__: tuple[str, ...] = (
    "GuardBackend",
    "Budget",
    "GuardResult",
    "Readiness",
    "BackendCapacity",
    "ReadinessTracker",
    "LocalOnnxBackend",
    "build_local_onnx",
    "BackendUnavailable",
    "LocalTrtBackend",
    "TritonGrpcBackend",
    "RemoteHttpBackend",
    "DeferredBackendError",
)
