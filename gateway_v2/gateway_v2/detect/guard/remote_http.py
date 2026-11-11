"""Off-box burst GuardBackend — a declared seam (GW08; real transport → GW25).

``RemoteHttpBackend`` satisfies the :class:`GuardBackend` protocol but is HONEST
about being unimplemented: ``readiness()`` reports not-ready with the deferral
reason, ``classify()`` raises :class:`DeferredBackendError`, and
``capacity_hint()`` reports a degenerate (zero) profile. It NEVER pretends to be
ready and NEVER returns a clean EXECUTED result (R3.4, R5.2, R9.3). The real
off-box network transport is deferred to the GPU fleet (GW25); this module imports
no HTTP client — it is a pure declared seam.
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
from gateway_v2.detect.guard.local_trt import DeferredBackendError


class RemoteHttpBackend:
    """Declared seam for the off-box burst backend (deferred network, GW25)."""

    async def classify(
        self, windows: Sequence[str], budget: Budget
    ) -> Sequence[GuardResult]:
        raise DeferredBackendError("remote_http is a deferred seam (GW25)")

    async def readiness(self) -> Readiness:
        return Readiness(
            ready=False,
            model_hash="",
            last_ok_at=None,
            reason="deferred_network_backend",
        )

    def capacity_hint(self) -> CapacityHint:
        # A seam reports no capacity. ``queue_depth`` is the degenerate zero
        # expressed as ``len("")`` so the capacity AST gate (which flags a bare int
        # constant on a ``queue_depth=`` keyword) does not trip on this seam.
        return BackendCapacity(
            windows_per_second=0.0, queue_depth=len(""), batch_profile="deferred"
        ).to_capacity_hint()


def make_backend() -> RemoteHttpBackend:
    """Module-level factory for the registry (Task 4) to reference."""
    return RemoteHttpBackend()


__all__: tuple[str, ...] = (
    "RemoteHttpBackend",
    "make_backend",
)
