# Implementation Plan: validation-watchdogs-r2-24 (local half of B9 / R2-24)

## Overview

Convert the feature design into a series of prompts for a code-generation LLM that will
implement each step with incremental progress. Make sure that each prompt builds on the
previous prompts, and ends with wiring things together. There should be no hanging or
orphaned code that isn't integrated into a previous step. Focus ONLY on tasks that involve
writing, modifying, or testing code.

This is a LOW-priority, narrow optional card. The whole deliverable is **two files**:

1. One new production module — `gateway_v2/gateway_v2/runtime/watchdogs.py` (the value types +
   the four pure evaluators), written in **one pass by one task**.
2. One new test file — `gateway_v2/tests/runtime/test_watchdogs_r2_24.py` (4 property tests +
   the example/boundary tests), written in **one pass by one task**.

The module is written before the tests depend on it (serialized). The single test file is
owned by one coherent task — the property sub-tasks are listed only to pin design properties
to requirement clauses; they are all delivered by the one file-owning task and MUST NOT be
split across parallel sub-agents (the single-test-file-collision lesson from B6/B7/B8).

## Tasks

- [x] 1. Write the production module `gateway_v2/gateway_v2/runtime/watchdogs.py` in one pass
  - Create the single new module in the `runtime` layer holding every value type and all four
    evaluators, exactly as the design's Components and Data Models sections specify.
  - Define `AlarmLevel(Enum)` with members `OK = "ok"` and `ALARM = "alarm"`, and
    `AlarmSource(Enum)` with members `IDLE`, `BURN_RATE`, `STORE_MEMORY`, `AUDIT_GUARD`
    (tags only, no behaviour). _Requirements: 6.5; design Data Models / AlarmLevel / AlarmSource_
  - Define the shared frozen `AlarmVerdict` as `@dataclass(frozen=True, slots=True)` carrying
    `level: AlarmLevel`, `source: AlarmSource`, `reason: str`; OK = `level OK` + neutral reason
    (e.g. `""`), firing = `level ALARM` + a distinct reason per path; no action field (advisory).
    _Requirements: 6.1, 6.5; design AlarmVerdict (Decision 3)_
  - Implement `IdleWatchdog` as a frozen slotted dataclass with `idle_bound_s: float` and
    `clock: Callable[[], float] = time.monotonic`; `evaluate(last_activity_at: float)` fires
    (`ALARM`, source `IDLE`) iff `self.clock() - last_activity_at > self.idle_bound_s`, else `OK`;
    reads the clock once, pure, no I/O/sleep/network. _Requirements: 1.1, 1.2, 1.3, 1.4; design IdleWatchdog_
  - Implement `BurnRateMonitor` as a frozen slotted dataclass with `budget_per_window: float`
    and `clock: Callable[[], float] = time.monotonic`; `evaluate(observed_spend, elapsed_window_s)`
    treats a non-positive `elapsed_window_s` as "no measurable rate yet" → `OK` (no divide-by-zero),
    else fires (`ALARM`, source `BURN_RATE`) iff `observed_spend / elapsed_window_s > budget_per_window`,
    else `OK`; pure. _Requirements: 2.1, 2.2, 2.3, 2.4; design BurnRateMonitor + Error Handling_
  - Implement `StoreMemoryAlarm` as a frozen slotted dataclass with `used_ratio_threshold: float`
    and `clock: Callable[[], float] = time.monotonic`; `evaluate(memory: StoreMemory, budget: BudgetDecision)`
    consumes the shipped readings (`memory.used_ratio`, `memory.policy_unsafe`, `memory.used`,
    `budget.known`, `budget.budget_bytes`) and re-implements none of them; fires (`ALARM`, source
    `STORE_MEMORY`) on any of three conditions, naming the FIRST matched in fixed order with a
    distinct reason: (1) `used_ratio >= used_ratio_threshold`, (2) `budget.known and memory.used > budget.budget_bytes`,
    (3) `memory.policy_unsafe`; `OK` only when none hold; an unknown budget never fires alone.
    _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6; design StoreMemoryAlarm + Error Handling_
  - Implement `AuditGuard` as a frozen slotted dataclass with `completeness_bound: float`,
    `backlog_bound: float`, and `clock: Callable[[], float] = time.monotonic`;
    `evaluate(completeness_ratio, queued, capacity, records_lost)` reads the passed-in
    `completeness_ratio` verbatim (never re-derived from the acknowledged high-water mark);
    backlog ratio is `queued / capacity` for `capacity > 0` (a non-positive capacity → no backlog
    alarm by itself); fires (`ALARM`, source `AUDIT_GUARD`) iff `completeness_ratio < completeness_bound`
    OR (`capacity > 0` AND `queued / capacity > backlog_bound`), distinct reason per path,
    `records_lost` carried into the reason for operator context only; `OK` otherwise; pure.
    _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6; design AuditGuard + Error Handling_
  - Import `StoreMemory` and `BudgetDecision` from `gateway_v2.runtime.storemem` (same-layer,
    legal); NEVER import `gateway_v2.audit` or name `AuditMetrics` — the audit guard takes bare
    scalars so the `runtime` layer never reaches up into `audit`. _Requirements: 5.1, 5.2, 5.3; design Architecture (Decision 1)_
  - Follow the house idiom: all new types `@dataclass(frozen=True, slots=True)`; time only through
    the injected `clock` default `time.monotonic` (NOT `runtime/clock.py`); NO capacity/SLO integer
    literal as a dataclass field default (thresholds passed in at construction, no numeric defaults);
    no module-level mutable; no new HTTP type. _Requirements: 6.1, 6.2, 6.3, 6.4; design House-idiom constraints (Decision 4)_

- [x] 2. Write the single test file `gateway_v2/tests/runtime/test_watchdogs_r2_24.py` in one pass
  - Create the one new test file containing BOTH the 4 property tests and all example/boundary
    tests, written as one coherent file per the single-test-file convention. All sub-tasks below
    are delivered by THIS single file-owning task and MUST NOT be dispatched to parallel sub-agents.
  - [x]* 2.1 Write the Property 1 test (Idle_Watchdog)
    - **Property 1: Idle fires iff elapsed exceeds the bound** — generate `(idle_bound_s, now,
      last_activity_at)` across firing and non-firing regions via a seeded `random.Random`,
      inject a clock returning `now`, assert `ALARM` (source `IDLE`) iff `now - last_activity_at > idle_bound_s`, else `OK`.
    - Seeded `random.Random`, ≥ 10,000 iterations, no hypothesis; tag `# Feature: validation-watchdogs-r2-24, Property 1` and `# Validates: Requirements 1.2, 1.3, 1.4`.
    - **Validates: Requirements 1.2, 1.3, 1.4**
  - [x]* 2.2 Write the Property 2 test (Burn_Rate_Monitor)
    - **Property 2: Burn-rate fires iff the projected rate exceeds the budget** — generate
      `(observed_spend, elapsed_window_s > 0, budget_per_window)`, assert `ALARM` (source `BURN_RATE`)
      iff `observed_spend / elapsed_window_s > budget_per_window`, else `OK`.
    - Seeded `random.Random`, ≥ 10,000 iterations, no hypothesis; tag `# Feature: validation-watchdogs-r2-24, Property 2` and `# Validates: Requirements 2.2, 2.3, 2.4`.
    - **Validates: Requirements 2.2, 2.3, 2.4**
  - [x]* 2.3 Write the Property 3 test (Store_Memory_Alarm)
    - **Property 3: Store-memory fires iff any of the three conditions holds** — generate random
      `StoreMemory` (`used`, `maxmemory`, `policy` from safe and evicting families, `evicted_keys`),
      a `BudgetDecision` (known and unknown), and a `used_ratio_threshold`; assert `ALARM` (source
      `STORE_MEMORY`) iff `used_ratio >= threshold` OR (`budget.known` AND `used > budget_bytes`) OR
      `policy_unsafe`, else `OK`; assert the reason names the FIRST matched condition in fixed order.
    - Seeded `random.Random`, ≥ 10,000 iterations, no hypothesis; tag `# Feature: validation-watchdogs-r2-24, Property 3` and `# Validates: Requirements 3.3, 3.4, 3.5, 3.6`.
    - **Validates: Requirements 3.3, 3.4, 3.5, 3.6**
  - [x]* 2.4 Write the Property 4 test (Audit_Guard)
    - **Property 4: Audit-guard fires iff completeness is below bound or backlog is above bound** —
      generate `(completeness_ratio, queued, capacity, records_lost, completeness_bound, backlog_bound)`;
      assert `ALARM` (source `AUDIT_GUARD`) iff `completeness_ratio < completeness_bound` OR
      (`capacity > 0` AND `queued / capacity > backlog_bound`), else `OK`; completeness read from the
      passed-in `completeness_ratio`, never re-derived.
    - Seeded `random.Random`, ≥ 10,000 iterations, no hypothesis; tag `# Feature: validation-watchdogs-r2-24, Property 4` and `# Validates: Requirements 4.3, 4.4, 4.5, 4.6`.
    - **Validates: Requirements 4.3, 4.4, 4.5, 4.6**
  - [x]* 2.5 Write the construction example tests (one per evaluator)
    - Assert each evaluator (`IdleWatchdog`, `BurnRateMonitor`, `StoreMemoryAlarm`, `AuditGuard`)
      is a frozen dataclass that accepts its thresholds plus an injected `clock`, and that
      `AlarmVerdict` is frozen (mutation raises). _Requirements: 1.1, 2.1, 3.1, 4.1; design Testing Strategy (example tests)_
  - [x]* 2.6 Write the shipped-reading-consumption example tests (no re-implementation)
    - Store_Memory_Alarm reacts to the shipped `StoreMemory.used_ratio` / `.policy_unsafe` and the
      `BudgetDecision` rather than recomputing posture (Req 3.2); Audit_Guard's decision is a
      function of the passed-in `completeness_ratio` only, with no acknowledged-high-water
      recomputation (Req 4.2). _Requirements: 3.2, 4.2; design Testing Strategy (example tests)_
  - [x]* 2.7 Write the boundary tests
    - elapsed exactly == idle bound does not fire (strict `>`); rate exactly == budget does not fire
      (strict `>`); used_ratio exactly == threshold fires (`>=`); zero/negative window → `OK`;
      unknown budget (`budget.known` False) never fires alone; zero/negative capacity → no backlog
      alarm by itself. _Requirements: 2.2, 3.3, 3.4; design Error Handling + Testing Strategy (boundary tests)_

- [x] 3. Run the full local acceptance gate with the new module and test included
  - From cwd `gateway_v2` with the `gateway_v2/.venv` venv, run the gate and confirm every check
    stays green with the new `runtime/watchdogs.py` module and `tests/runtime/test_watchdogs_r2_24.py`
    present.
  - Run `pytest`: the new watchdog tests pass and the full suite stays green at the baseline
    (1697 passed / 95 skipped / 1 xfailed) plus the new tests. _Requirements: 7.1_
  - Run `mypy --strict` over the `gateway_v2` tree — passes. _Requirements: 7.2_
  - Run `ruff check gateway_v2 state_control audit_control load_harness lint tests` — passes. _Requirements: 7.3_
  - Run `lint-imports` — reports 2/2 kept (no `runtime` → `audit` edge introduced). _Requirements: 5.4, 7.4_
  - Run each of the 6 AST gates — each exits 0: `check_capacity_literals`, `check_no_module_mutable`,
    `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_sizes`, `check_tenant_scale`.
    _Requirements: 6.1, 6.3, 6.4, 7.4_
  - Evaluate `unscanned_channels()` and confirm it equals `()`. _Requirements: 7.5_

- [x] 4. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and are all delivered by the single
  file-owning task (Task 2); they MUST NOT be dispatched to parallel sub-agents — the entire test
  file is one coherent write (the single-test-file-collision lesson from B6/B7/B8).
- The production module (Task 1) and the test file (Task 2) are each written in one pass; Task 1 is
  serialized before Task 2 because the tests depend on the module.
- Each task references specific requirement sub-clauses and the design element it implements.
- Property tests validate the four universal firing properties; example/boundary tests cover
  construction shape, shipped-reading consumption (Req 3.2, 4.2), and the explicit edge inputs.
- The gate must stay green with the new production module included (the card adds a module + a test).

### Deferred / out-of-scope — explicitly NOT tasks

These are named in the design's Deferred and Out of Scope section and are NOT implemented here:

- The owner-visible **teardown rule** that survives agent/session loss — operations/deployer policy
  owned by **T20** (Req 8.1).
- **Idle reserved GPU slot billing** — a cloud/GPU-capacity concern owned by **GW25** (Req 8.2).
- Any **real wiring**: real cloud environment wiring, real store or metrics I/O, real GCP billing
  API access (Req 8.3).
- The **live caller** (edge/observability) that reads live `AuditMetrics` / `StoreMemory`, extracts
  the scalars/values, calls the four evaluators, and acts on their verdicts — deferred (Req 8.4).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"] },
    { "id": 2, "tasks": ["3"] }
  ]
}
```
