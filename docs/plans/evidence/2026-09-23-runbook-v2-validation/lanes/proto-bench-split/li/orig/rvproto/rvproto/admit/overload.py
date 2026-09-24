"""GW19 admission control: bounded per-worker concurrency, shed immediately when exceeded.

Three bounds, all from the ResourceContract (no literals), each with exported depth and age:
  inflight      every admitted request, all phases      <= connection_budget() (fd-derived)
  input phase   requests between admit and dispatch     <= queue_depth(measured guard windows/s)
  guard work    padded guard tokens submitted, unfinished <= pool_size(GUARD) (measured tokens/s x p99)
Guard bounds are unset until the guard has warmed up and published its CapacityHint (a request
then meets an unready guard -> UNAVAILABLE -> posture, which is a different outcome from overload).
A request larger than the whole guard bound is admitted only into an empty guard queue.
"""

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass

from rvproto.runtime.metrics import Registry


@dataclass(frozen=True, slots=True)
class Shed:
    reason: str
    retry_after_s: float


class LoadGate:
    def __init__(self, metrics: Registry, *, inflight_cap: int) -> None:
        self.metrics = metrics
        self.inflight_cap = inflight_cap
        self.input_cap: int | None = None
        self.guard_cap_tokens: int | None = None
        self.guard_tokens_per_s: float | None = None
        self.inflight = 0
        self.guard_tokens = 0
        self._ids = itertools.count()
        self._input: dict[int, int] = {}  # ticket -> enter ns (input-phase queue, for age)
        self._starts: dict[int, int] = {}  # ticket -> enter ns (all in-flight, for age)

    def configure_guard(self, *, input_cap: int, guard_cap_tokens: int, tokens_per_s: float) -> None:
        self.input_cap = input_cap
        self.guard_cap_tokens = guard_cap_tokens
        self.guard_tokens_per_s = tokens_per_s

    def _retry_after(self) -> float:
        if self.guard_tokens_per_s:
            return max(self.guard_tokens / self.guard_tokens_per_s, 0.0)
        return 0.0

    def _shed(self, reason: str) -> Shed:
        self.metrics.inc(f'shed{{reason="{reason}"}}')
        return Shed(reason, self._retry_after())

    def enter(self) -> int | Shed:
        if self.inflight >= self.inflight_cap:
            return self._shed("inflight")
        self.inflight += 1
        t = next(self._ids)
        self._starts[t] = time.perf_counter_ns()
        self.metrics.inc("admitted")
        return t

    def leave(self, ticket: int) -> None:
        if self._starts.pop(ticket, None) is not None:
            self.inflight -= 1
        self._input.pop(ticket, None)

    def enter_input(self, ticket: int) -> Shed | None:
        if self.input_cap is not None and len(self._input) >= self.input_cap:
            return self._shed("input_queue")
        self._input[ticket] = time.perf_counter_ns()
        return None

    def leave_input(self, ticket: int) -> None:
        self._input.pop(ticket, None)

    def take_guard(self, cost_tokens: int) -> Shed | None:
        cap = self.guard_cap_tokens
        if cap is not None and self.guard_tokens and self.guard_tokens + cost_tokens > cap:
            return self._shed("guard_queue")
        self.guard_tokens += cost_tokens
        return None

    def give_guard(self, cost_tokens: int) -> None:
        self.guard_tokens -= cost_tokens

    def downstream_shed(self, reason: str, retry_after_s: float) -> Shed:
        """A bound enforced below this worker (the GPU owner's single queue) refused the work."""
        self.metrics.inc(f'shed{{reason="{reason}"}}')
        return Shed(reason, retry_after_s)

    def gauges(self) -> dict[str, float]:
        now = time.perf_counter_ns()
        oldest_in = min(self._input.values(), default=now)
        oldest_all = min(self._starts.values(), default=now)
        out = {
            "inflight_requests": float(self.inflight),
            "inflight_cap": float(self.inflight_cap),
            "inflight_oldest_age_seconds": (now - oldest_all) / 1e9,
            "input_queue_depth": float(len(self._input)),
            "input_queue_oldest_age_seconds": (now - oldest_in) / 1e9,
            "guard_inflight_tokens": float(self.guard_tokens),
        }
        if self.input_cap is not None:
            out["input_queue_cap"] = float(self.input_cap)
        if self.guard_cap_tokens is not None:
            out["guard_queue_cap_tokens"] = float(self.guard_cap_tokens)
        return out


def retry_after_header_s(shed: Shed) -> float:
    """Retry-After is whole seconds on the wire; the precise drain estimate goes in retry-after-ms."""
    return max(shed.retry_after_s, 1e-3) if math.isfinite(shed.retry_after_s) else 1.0
