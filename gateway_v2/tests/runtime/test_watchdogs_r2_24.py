"""B9 / R2-24 (local half): the four validation-environment watchdog/alarm evaluators.

This is the single test file for the shipped `gateway_v2.runtime.watchdogs` module. It carries
BOTH the four property tests (one per design Correctness Property) AND the example/boundary
tests, written as one coherent file per the single-test-file convention (the B6/B7/B8
single-test-file-collision lesson).

The four evaluators are pure functions over their inputs, which is exactly where property-based
testing is strongest. Each firing condition is a monotone boundary: the WHEN (fires) and the
WHILE (does not fire) halves of each requirement fold into one "fires iff condition" property,
so the "clean reading => OK" boundary is the negative side of each property rather than a
separate one. That leaves one property per evaluator -- four in total.

Idiom: a seeded `random.Random` driven for >= 10,000 iterations, NO hypothesis. The clock is
injected as a fixed `lambda: now`, so time is a deterministic input rather than a side effect.
The shipped reading types (`StoreMemory`, `BudgetDecision`) are constructed directly -- the
alarm consumes their `.used_ratio` / `.policy_unsafe` / `.known` readings and re-implements
none of them.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from gateway_v2.runtime.storemem import BudgetDecision, StoreMemory
from gateway_v2.runtime.watchdogs import (
    AlarmLevel,
    AlarmSource,
    AlarmVerdict,
    AuditGuard,
    BurnRateMonitor,
    IdleWatchdog,
    StoreMemoryAlarm,
)

# The house-idiom budget: a seeded Random driven for >= 10,000 iterations per property, no
# hypothesis. The seed is fixed so a counter-example is reproducible from the file alone.
ITERATIONS = 10_000

# A mix of SAFE and evicting maxmemory policies. "noeviction" is the only safe one; everything
# starting with "volatile-" / "allkeys-" evicts (StoreMemory.policy_unsafe is True for those).
SAFE_POLICIES = ("noeviction", "")
EVICTING_POLICIES = ("allkeys-lru", "volatile-lru", "allkeys-lfu", "volatile-ttl")
ALL_POLICIES = SAFE_POLICIES + EVICTING_POLICIES


# --- Property 1 (Idle_Watchdog) ------------------------------------------------------------------


def test_property_1_idle_fires_iff_elapsed_exceeds_bound() -> None:
    # Feature: validation-watchdogs-r2-24, Property 1: Idle fires iff elapsed exceeds the bound.
    # For all idle bounds, clock readings, and last-activity timestamps, the Idle_Watchdog
    # returns ALARM (source IDLE) iff clock() - last_activity_at > idle_bound_s, else OK.
    # Validates: Requirements 1.2, 1.3, 1.4
    rng = random.Random(0xA11DE)
    for _ in range(ITERATIONS):
        idle_bound_s = rng.uniform(0.0, 1_000.0)
        now = rng.uniform(0.0, 1_000_000.0)
        # Draw last_activity across firing (far past) and non-firing (near/after now) regions.
        offset = rng.uniform(-2.0 * idle_bound_s - 5.0, 2.0 * idle_bound_s + 5.0)
        last_activity_at = now - offset
        watchdog = IdleWatchdog(idle_bound_s, clock=lambda n=now: n)

        verdict = watchdog.evaluate(last_activity_at)

        should_fire = (now - last_activity_at) > idle_bound_s
        if should_fire:
            assert verdict.level == AlarmLevel.ALARM
            assert verdict.source == AlarmSource.IDLE
            assert verdict.reason != ""
        else:
            assert verdict.level == AlarmLevel.OK
            assert verdict.source == AlarmSource.IDLE


# --- Property 2 (Burn_Rate_Monitor) --------------------------------------------------------------


def test_property_2_burn_rate_fires_iff_rate_exceeds_budget() -> None:
    # Feature: validation-watchdogs-r2-24, Property 2: Burn-rate fires iff the projected rate
    # exceeds the budget. For all observed-spend values, positive elapsed windows, and
    # budgets-per-window, BURN_RATE fires iff observed_spend / elapsed_window_s >
    # budget_per_window, else OK.
    # Validates: Requirements 2.2, 2.3, 2.4
    rng = random.Random(0xB027A)
    for _ in range(ITERATIONS):
        budget_per_window = rng.uniform(0.0, 1_000.0)
        elapsed_window_s = rng.uniform(1e-6, 10_000.0)
        # Draw spend so the projected rate straddles the budget on both sides.
        target_rate = rng.uniform(0.0, 2.0 * budget_per_window + 10.0)
        observed_spend = target_rate * elapsed_window_s
        monitor = BurnRateMonitor(budget_per_window, clock=lambda: 0.0)

        verdict = monitor.evaluate(observed_spend, elapsed_window_s)

        should_fire = (observed_spend / elapsed_window_s) > budget_per_window
        if should_fire:
            assert verdict.level == AlarmLevel.ALARM
            assert verdict.source == AlarmSource.BURN_RATE
            assert verdict.reason != ""
        else:
            assert verdict.level == AlarmLevel.OK
            assert verdict.source == AlarmSource.BURN_RATE


# --- Property 3 (Store_Memory_Alarm) -------------------------------------------------------------


def test_property_3_store_memory_fires_iff_any_condition_holds() -> None:
    # Feature: validation-watchdogs-r2-24, Property 3: Store-memory fires iff any of the three
    # conditions holds. For all StoreMemory readings, BudgetDecision values, and used-ratio
    # thresholds, STORE_MEMORY fires iff used_ratio >= threshold OR (budget.known AND used >
    # budget_bytes) OR policy_unsafe, else OK; the reason names the FIRST matched condition in
    # that fixed order.
    # Validates: Requirements 3.3, 3.4, 3.5, 3.6
    rng = random.Random(0x570E3)
    for _ in range(ITERATIONS):
        maxmemory = rng.randint(1, 1_000_000)
        # Draw used around maxmemory so used_ratio straddles any threshold on both sides.
        used = rng.randint(0, 2 * maxmemory)
        policy = rng.choice(ALL_POLICIES)
        evicted_keys = rng.randint(0, 1000)
        memory = StoreMemory(
            used=used, maxmemory=maxmemory, policy=policy, evicted_keys=evicted_keys
        )
        used_ratio_threshold = rng.uniform(0.0, 2.0)
        # A known budget drawn around used (so condition 2 straddles), or an unknown budget.
        if rng.random() < 0.3:
            budget = BudgetDecision(None, "none", ())
        else:
            budget = BudgetDecision(rng.randint(0, 2 * maxmemory), "test", ())

        alarm = StoreMemoryAlarm(used_ratio_threshold, clock=lambda: 0.0)
        verdict = alarm.evaluate(memory, budget)

        cond1 = memory.used_ratio >= used_ratio_threshold
        cond2 = (
            budget.known
            and budget.budget_bytes is not None
            and memory.used > budget.budget_bytes
        )
        cond3 = memory.policy_unsafe
        should_fire = cond1 or cond2 or cond3

        if should_fire:
            assert verdict.level == AlarmLevel.ALARM
            assert verdict.source == AlarmSource.STORE_MEMORY
            # The reason names the FIRST matched condition in the fixed order 1 -> 2 -> 3,
            # regardless of which other conditions also hold.
            if cond1:
                assert verdict.reason.startswith("used ratio over threshold")
            elif cond2:
                assert verdict.reason.startswith("audit budget exceeded by used bytes")
            else:
                assert verdict.reason.startswith("unsafe/evicting maxmemory policy")
        else:
            assert verdict.level == AlarmLevel.OK
            assert verdict.source == AlarmSource.STORE_MEMORY


# --- Property 4 (Audit_Guard) --------------------------------------------------------------------


def test_property_4_audit_fires_iff_completeness_low_or_backlog_high() -> None:
    # Feature: validation-watchdogs-r2-24, Property 4: Audit-guard fires iff completeness is
    # below bound or backlog is above bound. For all completeness ratios, queued/capacity pairs,
    # records-lost counts, completeness bounds, and backlog bounds, AUDIT_GUARD fires iff
    # completeness_ratio < completeness_bound OR (capacity > 0 AND queued / capacity >
    # backlog_bound), else OK; completeness is read from the passed-in ratio, never re-derived.
    # Validates: Requirements 4.3, 4.4, 4.5, 4.6
    rng = random.Random(0xADD17)
    for _ in range(ITERATIONS):
        completeness_ratio = rng.uniform(0.0, 1.0)
        completeness_bound = rng.uniform(0.0, 1.0)
        # Include capacity == 0 (and sometimes negative) so the no-backlog edge is exercised.
        capacity = rng.choice([0, 0, -1] + [rng.randint(1, 100_000)])
        queued = rng.randint(0, 200_000)
        records_lost = rng.randint(0, 10_000)
        backlog_bound = rng.uniform(0.0, 2.0)

        guard = AuditGuard(completeness_bound, backlog_bound, clock=lambda: 0.0)
        verdict = guard.evaluate(completeness_ratio, queued, capacity, records_lost)

        completeness_breach = completeness_ratio < completeness_bound
        backlog_breach = capacity > 0 and (queued / capacity) > backlog_bound
        should_fire = completeness_breach or backlog_breach

        if should_fire:
            assert verdict.level == AlarmLevel.ALARM
            assert verdict.source == AlarmSource.AUDIT_GUARD
            assert verdict.reason != ""
        else:
            assert verdict.level == AlarmLevel.OK
            assert verdict.source == AlarmSource.AUDIT_GUARD


# --- 2.5 construction example tests --------------------------------------------------------------


def test_each_evaluator_is_a_frozen_dataclass_accepting_an_injected_clock() -> None:
    # Each evaluator accepts its thresholds plus an injected clock, and is frozen.
    # Validates: Requirements 1.1, 2.1, 3.1, 4.1
    idle = IdleWatchdog(10.0, clock=lambda: 42.0)
    burn = BurnRateMonitor(5.0, clock=lambda: 42.0)
    store = StoreMemoryAlarm(0.75, clock=lambda: 42.0)
    audit = AuditGuard(0.99, 0.5, clock=lambda: 42.0)

    for evaluator in (idle, burn, store, audit):
        assert dataclasses.is_dataclass(evaluator)
        with pytest.raises(dataclasses.FrozenInstanceError):
            evaluator.clock = lambda: 0.0  # type: ignore[misc]

    # The injected clock is the one that is actually read.
    assert idle.clock() == 42.0
    assert audit.completeness_bound == 0.99
    assert audit.backlog_bound == 0.5
    assert store.used_ratio_threshold == 0.75
    assert burn.budget_per_window == 5.0
    assert idle.idle_bound_s == 10.0


def test_alarm_verdict_is_frozen() -> None:
    # AlarmVerdict is a frozen value; mutating a field raises.
    # Validates: Requirements 1.1, 2.1, 3.1, 4.1
    verdict = AlarmVerdict(AlarmLevel.OK, AlarmSource.IDLE, "")
    assert dataclasses.is_dataclass(verdict)
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.level = AlarmLevel.ALARM  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.reason = "mutated"  # type: ignore[misc]


# --- 2.6 shipped-reading-consumption example tests (no re-implementation) ------------------------


def test_store_memory_alarm_fires_on_shipped_used_ratio_reading() -> None:
    # Reacts to the shipped StoreMemory.used_ratio crossing the line, not a recomputation:
    # used=48, maxmemory=64 => used_ratio 0.75 >= threshold 0.75 fires on ratio.
    # Validates: Requirements 3.2
    memory = StoreMemory(used=48, maxmemory=64, policy="noeviction", evicted_keys=0)
    assert memory.used_ratio == pytest.approx(0.75)
    budget = BudgetDecision(10_000, "test", ())  # not exceeded (48 < 10000)
    verdict = StoreMemoryAlarm(0.75, clock=lambda: 0.0).evaluate(memory, budget)
    assert verdict.level == AlarmLevel.ALARM
    assert verdict.reason.startswith("used ratio over threshold")


def test_store_memory_alarm_fires_on_shipped_budget_decision() -> None:
    # A safe policy UNDER the ratio threshold, but used > budget_bytes => fires on the shipped
    # BudgetDecision, condition 2.
    # Validates: Requirements 3.2
    memory = StoreMemory(used=100, maxmemory=1000, policy="noeviction", evicted_keys=0)
    assert memory.used_ratio == pytest.approx(0.1)  # under a 0.75 threshold
    assert not memory.policy_unsafe
    budget = BudgetDecision(50, "test", ())  # used=100 > 50
    verdict = StoreMemoryAlarm(0.75, clock=lambda: 0.0).evaluate(memory, budget)
    assert verdict.level == AlarmLevel.ALARM
    assert verdict.reason.startswith("audit budget exceeded by used bytes")


def test_store_memory_alarm_fires_on_shipped_policy_unsafe_reading() -> None:
    # An evicting policy, under both the ratio threshold and the budget => fires on the shipped
    # StoreMemory.policy_unsafe reading, condition 3.
    # Validates: Requirements 3.2
    memory = StoreMemory(used=1, maxmemory=1000, policy="allkeys-lru", evicted_keys=5)
    assert memory.used_ratio == pytest.approx(0.001)  # under threshold
    assert memory.policy_unsafe
    budget = BudgetDecision(10_000, "test", ())  # not exceeded
    verdict = StoreMemoryAlarm(0.75, clock=lambda: 0.0).evaluate(memory, budget)
    assert verdict.level == AlarmLevel.ALARM
    assert verdict.reason.startswith("unsafe/evicting maxmemory policy")


def test_audit_guard_decision_is_a_function_of_passed_in_completeness_only() -> None:
    # The guard reads the passed-in completeness_ratio verbatim; it never re-derives it from an
    # acknowledged high-water mark. Two calls differing only in completeness_ratio differ only
    # in the completeness decision.
    # Validates: Requirements 4.2
    guard = AuditGuard(0.99, 10.0, clock=lambda: 0.0)  # backlog bound high => never backlog
    breach = guard.evaluate(0.5, queued=0, capacity=100, records_lost=7)
    clean = guard.evaluate(0.999, queued=0, capacity=100, records_lost=7)
    assert breach.level == AlarmLevel.ALARM
    assert "completeness" in breach.reason
    assert "records_lost=7" in breach.reason
    assert clean.level == AlarmLevel.OK


# --- 2.7 boundary tests --------------------------------------------------------------------------


def test_idle_elapsed_exactly_equal_to_bound_does_not_fire() -> None:
    # Strict `>`: elapsed == idle_bound_s is NOT over the bound.
    # Validates: Requirements 2.2 (strict boundary family)
    watchdog = IdleWatchdog(100.0, clock=lambda: 1_000.0)
    verdict = watchdog.evaluate(900.0)  # elapsed == 100.0 == bound
    assert verdict.level == AlarmLevel.OK


def test_burn_rate_exactly_equal_to_budget_does_not_fire() -> None:
    # Strict `>`: rate == budget is NOT over the budget.
    # Validates: Requirements 2.2
    monitor = BurnRateMonitor(10.0, clock=lambda: 0.0)
    verdict = monitor.evaluate(observed_spend=100.0, elapsed_window_s=10.0)  # rate == 10.0
    assert verdict.level == AlarmLevel.OK


def test_burn_rate_zero_and_negative_window_are_ok() -> None:
    # A non-positive window has no measurable rate => OK, no divide-by-zero.
    # Validates: Requirements 2.2
    monitor = BurnRateMonitor(10.0, clock=lambda: 0.0)
    assert monitor.evaluate(1_000_000.0, 0.0).level == AlarmLevel.OK
    assert monitor.evaluate(1_000_000.0, -5.0).level == AlarmLevel.OK


def test_store_memory_used_ratio_exactly_at_threshold_fires() -> None:
    # `>=`: used_ratio exactly == threshold DOES fire.
    # Validates: Requirements 3.3
    memory = StoreMemory(used=75, maxmemory=100, policy="noeviction", evicted_keys=0)
    assert memory.used_ratio == pytest.approx(0.75)
    budget = BudgetDecision(10_000, "test", ())
    verdict = StoreMemoryAlarm(0.75, clock=lambda: 0.0).evaluate(memory, budget)
    assert verdict.level == AlarmLevel.ALARM
    assert verdict.reason.startswith("used ratio over threshold")


def test_store_memory_unknown_budget_never_fires_condition_2_alone() -> None:
    # An unknown budget (budget.known False) can never satisfy condition 2 on its own: a
    # noeviction store under the ratio threshold with a huge used and an unknown budget => OK.
    # Validates: Requirements 3.4
    memory = StoreMemory(used=10**12, maxmemory=10**15, policy="noeviction", evicted_keys=0)
    assert memory.used_ratio < 0.75
    assert not memory.policy_unsafe
    budget = BudgetDecision(None, "none", ())
    assert not budget.known
    verdict = StoreMemoryAlarm(0.75, clock=lambda: 0.0).evaluate(memory, budget)
    assert verdict.level == AlarmLevel.OK


def test_audit_zero_and_negative_capacity_no_backlog_alarm_but_completeness_still_fires() -> None:
    # A non-positive capacity raises no backlog alarm by itself, but a completeness breach still
    # fires regardless of capacity.
    # Validates: Requirements 3.3, 3.4 (edge-input family)
    guard = AuditGuard(0.99, 0.0, clock=lambda: 0.0)  # backlog bound 0 => any positive backlog
    # Completeness OK, capacity 0 => no backlog alarm => OK.
    assert guard.evaluate(1.0, queued=10**9, capacity=0, records_lost=0).level == AlarmLevel.OK
    # Completeness OK, capacity negative => no backlog alarm => OK.
    assert guard.evaluate(1.0, queued=10**9, capacity=-1, records_lost=0).level == AlarmLevel.OK
    # Completeness breach with zero capacity STILL fires (completeness is independent of backlog).
    breach = guard.evaluate(0.5, queued=0, capacity=0, records_lost=0)
    assert breach.level == AlarmLevel.ALARM
    assert "completeness" in breach.reason
