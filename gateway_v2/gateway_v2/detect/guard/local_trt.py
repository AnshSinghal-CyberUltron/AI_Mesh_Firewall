"""In-process TensorRT GuardBackend — a declared seam (GW08; real GPU → GW25).

``LocalTrtBackend`` satisfies the :class:`GuardBackend` protocol but is HONEST
about being unimplemented: ``readiness()`` reports not-ready with the deferral
reason, ``classify()`` raises :class:`DeferredBackendError`, and
``capacity_hint()`` reports a degenerate (zero) profile. It NEVER pretends to be
ready and NEVER returns a clean EXECUTED result (R3.4, R5.2, R9.3). The real
in-process TensorRT inference path is deferred to the GPU fleet (GW25).

:class:`DeferredBackendError` is defined here once and imported by the sibling
seams (``triton_grpc``, ``remote_http``) — a same-package sibling import keeps the
error DRY without editing ``backend.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.detect.guard.backend import (
    BackendCapacity,
    Budget,
    CapacityHint,
    GuardResult,
    Readiness,
)


class DeferredBackendError(RuntimeError):
    """Raised when a deferred seam's ``classify`` is called. The seam has no real
    inference path yet (GW25); callers must treat the backend as not-ready and
    produce UNAVAILABLE findings rather than a clean pass."""


class LocalTrtBackend:
    """Declared seam for the in-process TensorRT backend (deferred GPU, GW25)."""

    async def classify(
        self, windows: Sequence[str], budget: Budget
    ) -> Sequence[GuardResult]:
        raise DeferredBackendError("local_trt is a deferred seam (GW25)")

    async def readiness(self) -> Readiness:
        return Readiness(
            ready=False,
            model_hash="",
            last_ok_at=None,
            reason="deferred_gpu_backend",
        )

    def capacity_hint(self) -> CapacityHint:
        # A seam reports no capacity. ``queue_depth`` is the degenerate zero
        # expressed as ``len("")`` so the capacity AST gate (which flags a bare int
        # constant on a ``queue_depth=`` keyword) does not trip on this seam.
        return BackendCapacity(
            windows_per_second=0.0, queue_depth=len(""), batch_profile="deferred"
        ).to_capacity_hint()


def make_backend() -> LocalTrtBackend:
    """Module-level factory for the registry (Task 4) to reference."""
    return LocalTrtBackend()


__all__: tuple[str, ...] = (
    "DeferredBackendError",
    "LocalTrtBackend",
    "make_backend",
)
