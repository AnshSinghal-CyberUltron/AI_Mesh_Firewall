"""Validation-environment watchdog/alarm evaluators (local half of B9 / R2-24).

Round 2 measured test-environment waste: ≈ $120 of idle burn after the agents stopped, idle
reserved GPU slots still billing, and an audit backlog that nearly took down the shared stores.
The runbook correction (R2-24 register row + the §3 v3 AMENDMENT) requires every validation
environment to run an idle watchdog, a burn-rate monitor, a store-memory alarm, and an audit
guard. This module ships the LOW / PARTIAL local half: the four pure evaluators and nothing
else — the owner-visible teardown rule and all real cloud/store/metrics/billing wiring are
operations/deployer policy owned by T20 / GW25 and are explicitly deferred.

Each evaluator is a `@dataclass(frozen=True, slots=True)` holding its thresholds (passed in at
construction, never a numeric field default) plus an injected `clock: Callable[[], float]`
defaulting to `time.monotonic` — the shipped `ReplicaSnapshot` / `Rehydrator` idiom, NOT the
empty `runtime/clock.py` stub. Every `evaluate(...)` is pure: it reads plain values (scalars, or
the two shipped `runtime` reading types `StoreMemory` / `BudgetDecision`), does arithmetic, and
returns one frozen `AlarmVerdict`. It performs no I/O, does not sleep, does not reach the
network, and does not import a higher architectural layer.

Layer safety: the evaluators live in `runtime`. `StoreMemory` and `BudgetDecision` are
same-layer `runtime` types, so importing them is legal. The audit guard takes bare scalars
(`completeness_ratio`, `queued`, `capacity`, `records_lost`) rather than reaching up into the
higher `audit` layer, so this module never imports `gateway_v2.audit` and never names
`AuditMetrics`. The caller (edge / observability, above both layers) reads the live readings,
extracts the scalars/values, calls the evaluators, and decides the action — the verdicts here
are advisory only.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from gateway_v2.runtime.storemem import BudgetDecision, StoreMemory

__all__ = (
    "AlarmLevel",
    "AlarmSource",
    "AlarmVerdict",
    "IdleWatchdog",
    "BurnRateMonitor",
    "StoreMemoryAlarm",
    "AuditGuard",
)


class AlarmLevel(Enum):
    """Two members only — the runbook asks for an alarm, not a severity ladder."""

    OK = "ok"
    ALARM = "alarm"


class AlarmSource(Enum):
    """Which watchdog raised the verdict (a tag, not behaviour)."""

    IDLE = "idle"
    BURN_RATE = "burn_rate"
    STORE_MEMORY = "store_memory"
    AUDIT_GUARD = "audit_guard"


@dataclass(frozen=True, slots=True)
class AlarmVerdict:
    """One advisory verdict: an alarm level, the source that raised it, and a reason.

    A no-alarm verdict is `AlarmLevel.OK` with a neutral reason (``""``); a firing verdict is
    `AlarmLevel.ALARM` with a distinct reason per firing path. There is no action field — the
    caller owns the policy and decides what to do.
    """

    level: AlarmLevel
    source: AlarmSource
    reason: str


@dataclass(frozen=True, slots=True)
class IdleWatchdog:
    """Flag a validation environment with no activity for longer than the idle bound."""

    idle_bound_s: float
    clock: Callable[[], float] = time.monotonic

    def evaluate(self, last_activity_at: float) -> AlarmVerdict:
        """Fire iff the elapsed idle time exceeds the bound. Reads the clock once; pure."""
        elapsed = self.clock() - last_activity_at
        if elapsed > self.idle_bound_s:
            return AlarmVerdict(
                AlarmLevel.ALARM,
                AlarmSource.IDLE,
                f"idle for {elapsed:.3f}s, over the {self.idle_bound_s:.3f}s bound",
            )
        return AlarmVerdict(AlarmLevel.OK, AlarmSource.IDLE, "")


@dataclass(frozen=True, slots=True)
class BurnRateMonitor:
    """Flag projected spend exceeding the configured budget-per-window."""

    budget_per_window: float
    clock: Callable[[], float] = time.monotonic

    def evaluate(self, observed_spend: float, elapsed_window_s: float) -> AlarmVerdict:
        """Fire iff the projected burn rate exceeds the budget. Pure.

        A non-positive window has no measurable rate yet, so it returns OK rather than dividing
        by zero or firing spuriously.
        """
        if elapsed_window_s <= 0:
            return AlarmVerdict(AlarmLevel.OK, AlarmSource.BURN_RATE, "")
        rate = observed_spend / elapsed_window_s
        if rate > self.budget_per_window:
            return AlarmVerdict(
                AlarmLevel.ALARM,
                AlarmSource.BURN_RATE,
                f"burn rate {rate:.6f} over the {self.budget_per_window:.6f} budget-per-window",
            )
        return AlarmVerdict(AlarmLevel.OK, AlarmSource.BURN_RATE, "")


@dataclass(frozen=True, slots=True)
class StoreMemoryAlarm:
    """Flag a store approaching capacity, over its audit budget, or evicting.

    Consumes the shipped `StoreMemory` / `BudgetDecision` readings and re-implements none of
    them: it reads `memory.used_ratio`, `memory.used`, `memory.policy_unsafe`, `budget.known`,
    and `budget.budget_bytes`.
    """

    used_ratio_threshold: float
    clock: Callable[[], float] = time.monotonic

    def evaluate(self, memory: StoreMemory, budget: BudgetDecision) -> AlarmVerdict:
        """Fire on the FIRST matched condition in fixed order; OK only when none hold. Pure.

        An unknown budget (`budget.known` False) can never satisfy condition 2 on its own.
        """
        if memory.used_ratio >= self.used_ratio_threshold:
            return AlarmVerdict(
                AlarmLevel.ALARM,
                AlarmSource.STORE_MEMORY,
                f"used ratio over threshold: {memory.used_ratio:.6f} >= "
                f"{self.used_ratio_threshold:.6f}",
            )
        if budget.known and budget.budget_bytes is not None and memory.used > budget.budget_bytes:
            return AlarmVerdict(
                AlarmLevel.ALARM,
                AlarmSource.STORE_MEMORY,
                f"audit budget exceeded by used bytes: {memory.used} > {budget.budget_bytes}",
            )
        if memory.policy_unsafe:
            return AlarmVerdict(
                AlarmLevel.ALARM,
                AlarmSource.STORE_MEMORY,
                f"unsafe/evicting maxmemory policy: {memory.policy!r}",
            )
        return AlarmVerdict(AlarmLevel.OK, AlarmSource.STORE_MEMORY, "")


@dataclass(frozen=True, slots=True)
class AuditGuard:
    """Flag an audit completeness breach or a queue backlog from the shipped readings.

    Completeness is read from the passed-in `completeness_ratio` verbatim (the shipped
    DURABLE-high-water value) and never re-derived from the acknowledged high-water mark.
    """

    completeness_bound: float
    backlog_bound: float
    clock: Callable[[], float] = time.monotonic

    def evaluate(
        self,
        completeness_ratio: float,
        queued: int,
        capacity: int,
        records_lost: int,
    ) -> AlarmVerdict:
        """Fire iff completeness is below bound or backlog is above bound. Pure.

        A non-positive capacity has no measurable backlog ratio, so it never raises a backlog
        alarm by itself. `records_lost` is carried into the reason for operator context only; it
        does not change the firing decision.
        """
        if completeness_ratio < self.completeness_bound:
            return AlarmVerdict(
                AlarmLevel.ALARM,
                AlarmSource.AUDIT_GUARD,
                f"completeness {completeness_ratio:.6f} below the "
                f"{self.completeness_bound:.6f} bound (records_lost={records_lost})",
            )
        if capacity > 0:
            backlog_ratio = queued / capacity
            if backlog_ratio > self.backlog_bound:
                return AlarmVerdict(
                    AlarmLevel.ALARM,
                    AlarmSource.AUDIT_GUARD,
                    f"backlog ratio {backlog_ratio:.6f} over the {self.backlog_bound:.6f} "
                    f"bound: queued={queued} capacity={capacity} "
                    f"(records_lost={records_lost})",
                )
        return AlarmVerdict(AlarmLevel.OK, AlarmSource.AUDIT_GUARD, "")
