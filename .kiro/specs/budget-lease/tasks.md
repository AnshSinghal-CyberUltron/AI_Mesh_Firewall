# Implementation Plan: Budget Lease + Local GCRA (R2-09 / R2-14 / R2-19, card GW06)

## Overview

Convert the feature design into a series of prompts for a code-generation LLM that will implement
each step with incremental progress. Make sure that each prompt builds on the previous prompts, and
ends with wiring things together. There should be no hanging or orphaned code that isn't integrated
into a previous step. Focus ONLY on tasks that involve writing, modifying, or testing code.

Implementation is **bottom-up along the import-linter layer contract** (`admit` imports only
`runtime` + `domain`): first the shared `runtime/store_keys.py` key layout, then the pure
`admit/gcra.py`, then the store-backed `admit/lease.py`, then the chunk/watermark derivation +
`BudgetVerdict` value, then the `admit/quota.py` façade, then the R2-14 retry boundary, the
producer-only metrics, the R2-19/R2-09 partition regression in `runtime`, the local acceptance
harness, and finally the changelog + verification close-out.

House conventions enforced throughout: injectable `Callable[[], float]` clock, injected store
client (`fakeredis` in tests), injected `asyncio` task spawner for the refill, frozen slotted
dataclasses, `StrEnum`, fail-closed (no FAIL_OPEN), producer-only label-free metrics, no HTTP
objects in `admit` (`BudgetVerdict` is a value; `edge` renders), no module-level mutable state, and
no capacity literal in `admit` (all capacity derives from `ResourceContract`; the existing
`admit/` capacity-literal gate covers the new modules). Property tests use a seeded `random.Random`
(≥ 10,000 iterations, **no hypothesis**, async via `asyncio.run`) per the house idiom in
`tests/admit/test_lgw19_codel.py`, each tagged `# Feature: budget-lease, Property N` and
`# Validates: Requirements X.Y`.

## Tasks

- [x] 1. Add the budget-lease store key family to `runtime/store_keys.py`
  - Extend `StoreKeys` with `budget_remaining(org)`, `budget_generation(org)`, and
    `budget_lease(org, worker_id)` methods, all under the existing `{rv2}` namespace hash-tag so
    every op stays in one slot (cluster-safe) and the atomic script touches only keys in that slot
  - Keys: `{rv2}:budget:<org>:remaining`, `{rv2}:budget:<org>:generation`,
    `{rv2}:budget:<org>:lease:<worker_id>`; keep distinct from the existing published
    `StateKind.BUDGET` config record (this family is the live counter)
  - _Requirements: 3.1, 3.4_
  - _Design: Data Models — Store key layout_

  - [x] 1.1 Write unit tests for the new store keys
    - Assert each method returns the exact key string with the `{rv2}` hash-tag and that all three
      keys for one org share the same slot tag
    - _Requirements: 3.1, 3.4_

- [x] 2. Implement the pure `admit/gcra.py` LocalGCRA
  - Create `admit/gcra.py` with a frozen slotted `GcraParams(rate_per_s: float, burst: int)` and a
    `LocalGCRA` whose `__init__(self, params, *, clock: Callable[[], float])` carries a small
    mutable TAT (theoretical arrival time) carrier only; `admit() -> bool` returns True when within
    burst + rate at `clock()` now and advances the TAT only on admit
    - No store round trip, ever; the decision is a pure function of the injected clock and params
    - Hold no capacity literal — `rate_per_s`/`burst` are supplied by the façade
  - _Requirements: 1.1, 1.3, 1.4_
  - _Design: Components — `admit/gcra.py`_

  - [x] 2.1 Write property test for GCRA burst and rate bound
    - **Property 8: GCRA bounds burst and rate** — `# Feature: budget-lease, Property 8`
    - Seeded `random.Random` arrival streams over an injected clock; assert the windowed admission
      count never exceeds `burst + rate_per_s × window`, independent of any lease state
    - File: `tests/admit/test_lgw06_gcra.py`
    - **Validates: Requirements 1.1, 1.3, 1.4**

- [x] 3. Checkpoint — foundation layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement the store-backed `admit/lease.py` BudgetLease state machine
  - [x] 4.1 Implement the lease config, state carrier, and atomic acquire
    - Create `admit/lease.py` with frozen slotted `LeaseConfig(org, chunk, low_watermark, ttl_s)`,
      the single mutable `_LeaseState(remaining, generation, refill_in_flight)` carrier (not frozen;
      no module-level mutable state), a `SpendResult` value, and `BudgetLease.__init__` taking the
      injected store `client`, `config`, injected `clock`, injected `spawn`, `keys`, and `metrics`
    - Implement `async acquire(generation) -> bool` as a single `EVAL` (Lua) compare-and-decrement:
      generation check (`-1` on stale → re-read gen + re-acquire), `grant = min(pool, chunk)`
      (never over-grant the pool), `DECRBY remaining`, `SET lease:<worker>` + `EXPIRE` to `ttl_s` on
      the store's clock; returns `{grant, current_gen}`, `0` when the pool is empty
    - _Requirements: 1.5, 3.1, 3.4, 6.1, 6.3, 6.4_
    - _Design: Components — `admit/lease.py`; Data Models — atomic acquire_

  - [x] 4.2 Implement local `try_spend` with single-flight off-path refill scheduling
    - `try_spend(cost, generation) -> SpendResult` is **synchronous and local** (no `await`, no
      store read): decrement `Remaining_Lease`; when the result is at or below `low_watermark`,
      schedule an Async_Refill via the injected `spawn` and return immediately; a stale generation
      is unspendable
    - Single-flight: a `refill_in_flight` guard ensures at most one refill is in flight; a
      `try_spend` that fires the watermark during an in-flight refill schedules nothing new and
      keeps serving from `Remaining_Lease`; `acquire` runs only at construction and from the refill
      task, never from `try_spend`
    - _Requirements: 3.2, 4.1, 4.2, 4.3, 4.4, 6.3_
    - _Design: Components — `admit/lease.py`; Purity boundaries_

  - [x] 4.3 Implement `return_unspent` and TTL-reclaim semantics
    - `async return_unspent() -> None` as a single `EVAL` that atomically adds the unspent
      `Remaining_Lease` back to the pool and deletes the per-worker lease record on clean shutdown;
      a crashed worker never runs this and its `lease:<worker>` key expires on the store's clock so
      the budget is reclaimed, never permanently lost
    - _Requirements: 3.3, 3.5_
    - _Design: Data Models — Return of unspent budget_

  - [x] 4.4 Write property test for no replica multiplication
    - **Property 1: No replica multiplication** — `# Feature: budget-lease, Property 1`
    - N in-process `BudgetLease` instances over one `fakeredis`, seeded `random.Random`
      ≥ 10,000 iters, any arrival pattern; assert `sum(admitted) ≤ limit + overshoot` (overshoot =
      at most one in-flight chunk per worker)
    - File: `tests/admit/test_lgw06_lease_no_multiplication.py`
    - **Validates: Requirements 7.1, 7.2, 7.3**

  - [x] 4.5 Write property test for budget conservation
    - **Property 2: Unspent budget is always returned** — `# Feature: budget-lease, Property 2`
    - Random acquire / spend / clean-shutdown / crash event sequences (crash = drop state without
      `return_unspent`, advance `fakeredis` TTL); assert spent + returned + TTL-reclaimed equals
      acquired
    - File: `tests/admit/test_lgw06_lease_conservation.py`
    - **Validates: Requirements 3.3, 3.4, 3.5**

  - [x] 4.6 Write property test for stale generation never spent
    - **Property 3: Stale generation is never spent** — `# Feature: budget-lease, Property 3`
    - Advance the generation mid-stream; assert no admission draws from the stale lease and a
      re-acquire under the new generation happens before further budget admission
    - File: `tests/admit/test_lgw06_generation.py`
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 13.4**

  - [x] 4.7 Write property test for refill never on the request path
    - **Property 5: Refill is never on the request path** — `# Feature: budget-lease, Property 5`
    - A counting `fakeredis` wrapper records every call made on the request path; assert that count
      is 0 across ≥ 10,000 `try_spend` calls, including during an in-progress refill
    - File: `tests/admit/test_lgw06_refill_offpath.py`
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**

- [x] 5. Checkpoint — lease state machine
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement chunk/watermark derivation and the `BudgetVerdict` value
  - [x] 6.1 Implement the `BudgetVerdict` value in `admit/grant.py`
    - Add a frozen slotted `BudgetVerdict` mirroring the existing `ShedVerdict` code-vs-render
      split: `code = posture.BUDGET_UNAVAILABLE`, `retry_after_s` from `gap_retry_after_s`
      (≥ `MIN_RETRY_AFTER_S`), `should_retry = False`, `request_id`; constructs no HTTP object
      (`edge` renders the 503 + `Retry-After` + `x-should-retry`)
    - Reuse `BUDGET_UNAVAILABLE`, `MIN_RETRY_AFTER_S`, `gap_retry_after_s` from `domain/posture.py`;
      define no second budget-outage code
    - _Requirements: 5.5, 5.6_
    - _Design: Components — `admit/grant.py`_

  - [x] 6.2 Implement pure chunk and watermark derivation in `admit/quota.py`
    - Add `derive_lease_chunk(contract, *, q_safe) -> int` computing the Lease_Chunk from the
      `ResourceContract` measured inputs (`offered_service_rate`, `target_p99_ms`,
      `utilization_cap`, injected `q_safe`) and `derive_low_watermark(chunk) -> int` as a function
      of the chunk only; both pure, no store, no clock; refuse-to-start (raise
      `CapacityUnset`/`CapacityUnavailable`) when uncomputable or `q_safe` is unset/non-positive;
      no capacity literal in the module; leave `derive_bounds` unchanged
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 13.3_
    - _Design: Components — `admit/quota.py`_

  - [x] 6.3 Write property test for contract-derived chunk
    - **Property 9: Lease chunk is contract-derived** — `# Feature: budget-lease, Property 9`
    - Random contracts → chunk and watermark are pure functions of the contract's measured inputs;
      uncomputable / unset `q_safe` → raises; the existing capacity gate covers the literal check
    - File: `tests/admit/test_lgw06_chunk_derivation.py`
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

  - [x] 6.4 Write example tests for `BudgetVerdict` and watermark
    - Assert `BudgetVerdict` carries `code = BUDGET_UNAVAILABLE`, `retry_after_s ≥ MIN_RETRY_AFTER_S`,
      `should_retry = False`, and constructs no HTTP object; assert `derive_low_watermark` is a
      function of the chunk only
    - _Requirements: 4.5, 5.6_

- [x] 7. Implement the `QuotaComponent` façade in `admit/quota.py`
  - Add `QuotaComponent.__init__(self, gcra, lease, *, generation_source, metrics)` and
    `evaluate(cost) -> BudgetVerdict | Admitted` composing: (1) local GCRA burst+rate check →
    refuse on over; (2) generation check → re-acquire under current gen if stale; (3)
    `lease.try_spend(cost, gen)` → admit, or `budget_unavailable` when the pool and Remaining_Lease
    are both exhausted under outage
  - `evaluate` is the request-path entry point and performs **no store I/O**; the façade owns the
    composition order (GCRA first, then budget) and the fail-closed catch-all (refuse on any
    undecidable decision); it owns only the narrow `budget_unavailable` posture and never returns
    `shared_state_unavailable`
  - _Requirements: 1.2, 1.3, 5.1, 5.2, 5.3, 5.4, 13.1_
  - _Design: Components — `admit/quota.py`; Error Handling_

  - [x] 7.1 Write property test for the narrow budget outage posture
    - **Property 4: Budget outage is narrow** — `# Feature: budget-lease, Property 4`
    - `fakeredis` partition; assert the Remaining_Lease is spent then `budget_unavailable` is
      returned, `shared_state_unavailable` is never returned, and identity/kill-switch/plan are not
      coupled to the budget posture (cross-referenced, not owned here)
    - File: `tests/admit/test_lgw06_outage_posture.py`
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**

  - [x] 7.2 Write property test for fail-closed under error
    - **Property 10: Fail-closed under error** — `# Feature: budget-lease, Property 10`
    - Assert a missing contract-derived chunk, an undecidable budget decision, and a stale-generation
      lease each refuse (never admit), and that the `fail_open_total`-style counter stays 0
    - File: `tests/admit/test_lgw06_failclosed.py`
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.4**

- [x] 8. Checkpoint — façade wired to GCRA + lease
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement the retry-once-on-idempotent-read boundary (R2-14)
  - Add a retry-once wrapper at the lease/store read boundary so an Idempotent_Read that times out
    (per `bounded_timeout_s`) is retried at most once and a second timeout surfaces; the mutating
    atomic acquire/return `EVAL` is **never** retried on timeout; document in the module that
    gateway placement in the store primary's zone is a deployment dependency and the
    retry-once-on-timeout is the code-side requirement
  - _Requirements: 8.1, 8.2, 8.3, 8.4_
  - _Design: Error Handling — store timeout rows; Property 6_

  - [x] 9.1 Write property test for idempotent read retry-once
    - **Property 6: Idempotent read retries at most once** — `# Feature: budget-lease, Property 6`
    - A client stub that times out N times; assert exactly one retry for reads, zero retries for the
      mutating op, and that a second timeout surfaces
    - File: `tests/admit/test_lgw06_retry_once.py`
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4**

- [x] 10. Implement the producer-only quota metrics in `admit/metrics.py`
  - Add a `QuotaMetrics` producer (following the existing `AdmissionMetrics` producer-vs-publisher
    split) exporting three fixed, label-free series seeded to zero at construction:
    `amf_quota_lease_overshoot` (declared aggregate overshoot), `amf_quota_async_refill_total`,
    `amf_quota_budget_unavailable_total`; no tenant-derived (owner/org) label on any series;
    GW14d publishes; wire the producer into `BudgetLease` (refill count) and `QuotaComponent`
    (budget_unavailable count, overshoot)
  - _Requirements: 7.4, 12.1, 12.2, 12.3, 12.4_
  - _Design: Components — `admit/metrics.py`_

  - [x] 10.1 Write unit tests for the quota metrics series
    - Assert the three series are present and zero from the first snapshot, that the Async_Refill
      and `budget_unavailable` counts increment on the respective events, and that no series carries
      a tenant-derived label
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

- [x] 11. Add the 45 s partition regression and confirm the store-connection boundary (R2-19 / R2-09)
  - Add `tests/runtime/test_lgw06_partition_regression.py` driving a 45 s simulated store partition
    (fakeredis + an injected clock + a keepalive-`ETIMEDOUT` / dead-socket simulation) covering:
    **D2 (Property 7)** — the existing `state_nudge.NudgeListener` surfaces the keepalive timeout as
    a dead signature (`no_wait`/`silent`/`error`), reconnects after backoff, and does not spin to
    unbounded memory; **D1** — a dead pooled read socket makes `store_valkey` reconnect rather than
    answer 500; **R2-09** — budget spends only the Remaining_Lease then returns `budget_unavailable`
    (never `shared_state_unavailable`) and no store read is issued on the request path to refill
  - This is **CONFIRM + REGRESSION-TEST, not a rewrite**: the `state_nudge` D2 fix already exists and
    the working `NudgeListener` MUST be left intact; add a small `store_valkey` dead-socket-reconnect
    guard **only if** D1 shows the adapter does not already reconnect
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 10.1, 10.2, 10.3, 10.4_
  - _Design: Store-boundary partition regression; Property 7_

- [x] 12. Checkpoint — boundary and metrics complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Build the local acceptance harness (LGW06-3 / LGW06-5 equivalents, Req 11)
  - [-] 13.1 Implement the LGW06-3 multi-replica harness
    - Add `tests/admit/test_lgw06_harness.py` simulating N in-process replicas sharing one
      `fakeredis` Budget_Lease; assert aggregate admission ≤ limit + declared overshoot, the
      overshoot is published, and quota does not multiply by replica count
    - _Requirements: 11.1_
    - _Design: Local acceptance equivalents — LGW06-3_

  - [x] 13.2 Implement the LGW06-5 injected-latency equivalent
    - In `test_lgw06_harness.py` (and/or `tests/runtime/`), inject 200 ms store latency; assert the
      op stays within `bounded_timeout_s`, the declared posture holds, and the connection pool does
      not grow unbounded
    - _Requirements: 11.2_
    - _Design: Local acceptance equivalents — LGW06-5_

  - [x] 13.3 Implement the R2-09 partition-behaviour harness assertion
    - Assert (reusing the outage-posture + refill-offpath coverage) that during a `fakeredis`
      partition budget spends the Remaining_Lease then returns `budget_unavailable`, and that the
      refill was never on the request path
    - _Requirements: 11.3_
    - _Design: Local acceptance equivalents — R2-09 partition behaviour_

- [x] 14. Document the feature and append the R2-09/R2-14/R2-19 changelog
  - Write the plan doc `docs/plans/2026-10-08-r2-09-r2-14-r2-19-gw06-budget-lease.md` and create the
    evidence directory `docs/plans/evidence/2026-10-08-r2-09-gw06/`; append a single R2-09/R2-14/R2-19
    pointer entry to `AGENTS.md` mirroring the R2-06/R2-07 closure style; record the DEFERRED
    cloud/scale gates as out of local scope (full live four-replica fleet quota run / full LGW06-3,
    live store failover LGW06-6, real cross-zone RTO measurement) and the R2-14 zone placement as a
    deployment dependency
  - This is the R2 series: **plan doc + AGENTS.md pointer + evidence only** — the R2 series does NOT
    use the four-memory pipeline protocol (no Ruflo / Cursor mirrors)
  - _Requirements: 8.4, 11.4_
  - _Design: Deferred cloud / scale gates_

- [x] 15. Final local verification gate
  - Run the full `gateway_v2` pytest suite plus the budget-lease subset (`tests/admit/test_lgw06_*`
    and `tests/runtime/test_lgw06_partition_regression.py`), `mypy --strict`, `ruff`,
    `import-linter`, and the reused AST gates
    (`tests/gates/test_lgw19_admit_capacity_literals.py`, `test_check_capacity.py`,
    `test_check_sizes.py`, `test_check_http.py`, `test_import_linter.py`, `test_check_frozen.py`,
    `test_check_mutable.py`); reconcile the recorded evidence in the evidence dir
  - _Requirements: 13.2_
  - _Design: Testing Strategy — Gates_

## Notes

- Tasks marked with `*` are optional (test sub-tasks) and can be skipped for a faster MVP; core
  implementation tasks are never optional.
- Each task references specific requirement sub-clauses and the design property/section it satisfies
  for traceability.
- Checkpoints (tasks 3, 5, 8, 12) sit at layer boundaries so each layer is validated before the next
  builds on it.
- Property tests (one per correctness property 1–10) use a seeded `random.Random` (≥ 10,000 iters,
  no hypothesis) and map to the design's named `test_lgw06_*.py` files and `Validates:` references.
- The store-connection boundary work (task 11) is confirm + regression-test, not a rewrite; the
  working `NudgeListener` is left intact and a `store_valkey` guard is added only if a gap is found.
- Deferred cloud/scale gates (full LGW06-3 live, LGW06-6, cross-zone RTO) have no implementation
  tasks; task 14 records them deferred.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["1.1", "2.1"] },
    { "id": 2, "tasks": ["4.1"] },
    { "id": 3, "tasks": ["4.2"] },
    { "id": 4, "tasks": ["4.3", "6.1"] },
    { "id": 5, "tasks": ["4.4", "4.5", "4.6", "4.7", "6.2"] },
    { "id": 6, "tasks": ["6.3", "6.4", "7"] },
    { "id": 7, "tasks": ["7.1", "7.2", "9"] },
    { "id": 8, "tasks": ["9.1", "10"] },
    { "id": 9, "tasks": ["10.1", "11"] },
    { "id": 10, "tasks": ["13.1"] },
    { "id": 11, "tasks": ["13.2", "13.3"] },
    { "id": 12, "tasks": ["14"] },
    { "id": 13, "tasks": ["15"] }
  ]
}
```
