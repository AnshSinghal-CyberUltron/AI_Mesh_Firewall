"""Per-kind freshness: the stamp says the STORE was verified; this says THIS PROCESS caught up.

GW36 S3, N9. One global stamp cannot tell a worker that has drained the plan kind from one that
has not: a just-booted worker would see a fresh stamp and answer `PlanUnknownTenant` -- a 503
`plan_unavailable` -- for every org its PLAN feed had not reached yet, and a worker whose KEY
rounds keep failing would serve its cache as verified. A `KindGate` is fresh only when

* the stamp is fresh, and
* this process has completed a round of that kind that was ok AND not truncated (it reached
  the attested position, not merely made progress), and
* the newest such round is within the freshness bound, on a MONOTONIC clock -- the age of a
  local event, so wall-clock steps cannot age it or revive it.

Each reader (identity cache, plan snapshot, kill-switch snapshot) gets the gate for its own kind,
so a lagging kind refuses only what depends on it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable

from gateway_v2.domain.locks import FRESH_MS
from gateway_v2.domain.state import StateKind
from gateway_v2.runtime.state_stamp import FreshnessGate
from gateway_v2.runtime.state_task import RoundReport


class KindGates:
    """Records each kind's last caught-up round. Written by the state task, read per request."""

    def __init__(
        self,
        stamp: FreshnessGate,
        *,
        fresh_ms: int = FRESH_MS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stamp = stamp
        self._fresh_s = fresh_ms / 1000
        self._clock = clock
        self._lock = threading.Lock()
        self._caught_up: dict[StateKind, float] = {}

    def observe(self, reports: Iterable[RoundReport]) -> None:
        moment = self._clock()
        with self._lock:
            for report in reports:
                if report.ok and not report.truncated:
                    self._caught_up[report.kind] = moment

    def lag_reason(self, kind: StateKind) -> str | None:
        """None when `kind` is caught up and recent; otherwise why not."""
        with self._lock:
            at = self._caught_up.get(kind)
        if at is None:
            return f"{kind.value} state has not been read to its head by this process yet"
        if self._fresh_s > 0 and self._clock() - at > self._fresh_s:
            return (
                f"{kind.value} state was last read to its head {self._clock() - at:.1f} s ago, "
                f"over the {self._fresh_s:g} s bound"
            )
        return None

    def gate(self, kind: StateKind) -> KindGate:
        return KindGate(self, kind)

    def ready(self, kinds: Iterable[StateKind]) -> tuple[bool, str | None]:
        """The `/readyz` answer: the stamp, then every listed kind."""
        if not self._stamp.fresh():
            return False, self._stamp.unverified()
        for kind in kinds:
            reason = self.lag_reason(kind)
            if reason is not None:
                return False, reason
        return True, None

    @property
    def stamp(self) -> FreshnessGate:
        return self._stamp


class KindGate:
    """`FreshnessGate` for one kind. What a reader of that kind is handed instead of the stamp."""

    __slots__ = ("_gates", "_kind")

    def __init__(self, gates: KindGates, kind: StateKind) -> None:
        self._gates = gates
        self._kind = kind

    def fresh(self, now: float | None = None) -> bool:
        return self._gates.stamp.fresh(now) and self._gates.lag_reason(self._kind) is None

    def unverified(self) -> str:
        if not self._gates.stamp.fresh():
            return self._gates.stamp.unverified()
        return self._gates.lag_reason(self._kind) or "state verified"
