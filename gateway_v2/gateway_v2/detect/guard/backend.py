"""GuardBackend contract and its frozen value types (GW08, §10.5.4).

The semantic detector calls :class:`GuardBackend` and knows nothing about CPU,
GPU, TensorRT, Triton, batch size, or replica count — the backend is a deployment
choice the detector cannot observe. The C7/C23/C30/C35 corrections shape the value
types: frames carry the REMAINING budget, not a timestamp (C30); readiness is
reply-based so a hung-but-alive owner reads not-ready (C35); ``model_hash`` is the
identity computed from the bytes actually loaded, compared with a signed manifest.

``capacity_hint()`` returns the shipped ``runtime.kinds.CapacityHint`` (a lower
layer — a legal import from ``detect``) so ``runtime/resources.py`` derives the
guard pool bound from a measured rate. Capacity numbers stay out of ``detect``:
every value here is taken at construction or from measurement, never a literal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gateway_v2.runtime.kinds import CapacityHint  # lower layer — legal import


@dataclass(frozen=True, slots=True)
class Budget:
    """Remaining time/credit a ``classify`` may use.

    Carries REMAINING budget, not a timestamp (C30): ``remaining_s`` is a duration
    and there is no absolute-time field. ``derive`` is the ONLY place a deadline
    becomes a remaining budget, reading ``now_s`` from an injected clock supplied by
    the caller.
    """

    remaining_s: float

    @classmethod
    def derive(cls, *, deadline_s: float, now_s: float) -> Budget:
        return cls(remaining_s=max(0.0, deadline_s - now_s))


@dataclass(frozen=True, slots=True)
class GuardResult:
    """One classification per window; the semantic detector maps it to a Finding.

    It names a category and a score only — never CPU/GPU/batch/replica/topology.
    """

    window_index: int
    category: str  # a gateway_v2.domain.category.Category spelling
    score: float  # confidence in [0, 1]


@dataclass(frozen=True, slots=True)
class Readiness:
    """Reply-based readiness (C35).

    ``ready`` is True only when a successful reply arrived within the staleness
    bound (computed by :class:`ReadinessTracker` on an injected clock).
    """

    ready: bool
    model_hash: str  # computed from the bytes actually loaded
    last_ok_at: float | None  # injected-clock time of the last good reply
    reason: str  # why not-ready (e.g. "manifest_mismatch"); "" when ready


@dataclass(frozen=True, slots=True)
class BackendCapacity:
    """The owner's full measured profile (Decision B).

    ``to_capacity_hint`` derives the contract-facing ``tokens_per_second`` from the
    measured ``windows_per_second`` (one window ≈ one guard token for pool sizing);
    the richer profile is carried here for telemetry.
    """

    windows_per_second: float
    queue_depth: int
    batch_profile: str

    def to_capacity_hint(self) -> CapacityHint:
        return CapacityHint(tokens_per_second=self.windows_per_second)


@dataclass(frozen=True, slots=True)
class ReadinessTracker:
    """Pure, deterministic given injected time. A backend is ready iff:

    (1) the bytes actually loaded hash-match the signed manifest, AND
    (2) a successful reply was recorded within ``staleness_bound_s``.

    A hung/alive thread with no recent reply reads not-ready (C35); "thread alive"
    is never an input — only ``last_ok_at``.
    """

    staleness_bound_s: float
    manifest_hash: str

    def record_ok(self, now_s: float, last_ok_at: float | None) -> float:
        """Return the new ``last_ok_at`` (= ``now_s``); the caller holds the value."""
        return now_s

    def is_ready(
        self, *, now_s: float, last_ok_at: float | None, loaded_hash: str
    ) -> bool:
        if loaded_hash != self.manifest_hash:  # manifest / corrupt-cache mismatch
            return False
        if last_ok_at is None:  # no reply yet
            return False
        return (now_s - last_ok_at) <= self.staleness_bound_s


@runtime_checkable
class GuardBackend(Protocol):
    """The §10.5.4 contract. Detectors observe no backend-kind/topology detail."""

    async def classify(
        self, windows: Sequence[str], budget: Budget
    ) -> Sequence[GuardResult]: ...

    async def readiness(self) -> Readiness: ...

    def capacity_hint(self) -> CapacityHint: ...


__all__: tuple[str, ...] = (
    "Budget",
    "GuardResult",
    "Readiness",
    "BackendCapacity",
    "ReadinessTracker",
    "GuardBackend",
    "CapacityHint",
)
