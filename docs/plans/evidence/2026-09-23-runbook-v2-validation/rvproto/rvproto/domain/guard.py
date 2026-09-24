"""GuardBackend protocol (runbook §10.5.4) and its value types."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from gateway_v2.runtime.kinds import CapacityHint


@dataclass(frozen=True, slots=True)
class Budget:
    deadline_ns: int  # absolute perf_counter_ns deadline


@dataclass(frozen=True, slots=True)
class GuardResult:
    ok: bool  # False => UNAVAILABLE for this call (never a clean pass)
    p_malicious: tuple[float, ...]  # one per window when ok
    queue_ns: int
    exec_ns: int
    batch_windows: int
    detail: str
    # set when the backend REFUSED the work because its bounded queue is full: an overload
    # (shed with 503 + Retry-After), never an UNAVAILABLE finding routed through posture
    retry_after_s: float | None = None


@dataclass(frozen=True, slots=True)
class Readiness:
    ready: bool
    backend: str
    model_hash: str
    detail: str


class GuardBackend(Protocol):
    name: str

    def submit(self, windows: Sequence[Sequence[int]], budget: Budget) -> asyncio.Future[GuardResult]:
        """Non-blocking enqueue; the future resolves to a GuardResult."""
        ...

    async def classify(self, windows: Sequence[Sequence[int]], budget: Budget) -> GuardResult: ...

    async def readiness(self) -> Readiness: ...

    def capacity_hint(self) -> CapacityHint | None: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...
