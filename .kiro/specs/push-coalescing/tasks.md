# Implementation Plan: Push Coalescing (R2-23 / C43 push cost)

## Overview

Convert the feature design into a series of prompts for a code-generation LLM that will implement
each step with incremental progress. Make sure that each prompt builds on the previous prompts, and
ends with wiring things together. There should be no hanging or orphaned code that isn't integrated
into a previous step. Focus ONLY on tasks that involve writing, modifying, or testing code.

This is a control-plane card in `gateway_v2/`, built bottom-up like the shipped GW12 / GW14d cards.
The tasks follow the dependency chain the design lays out and preserve the per-layer commit
boundary: **StateKnobs authority → StatePublisher protocol → ValkeyPublisher → CoalescerCounters →
CoalesceConfig + CoalescingPublisher → service wiring → property / verification / microbench tests →
full gate sweep.**

Language: **Python** (the design document specifies concrete Python throughout — no pseudocode, so
no language-selection step is needed).

Path convention: package paths below are relative to the `gateway_v2/` project root
(`gateway_v2/gateway_v2/domain/state_knobs.py` on disk is the `gateway_v2.domain.state_knobs`
module; `state_control/` and `tests/state_control/` sit beside the inner `gateway_v2/` package, both
outside the import-linter `root_packages = ["gateway_v2"]`). Each task is test-first where the
component is pure logic.

Deferred-scope note (NOT a task): the fleet **Knee_Re_Measure** (36 workers / real Valkey / real
request load — Req 9) is an OPEN DEFERRED CLOUD GATE, out of local scope. The Microbenchmark (task 10)
is only its local representative and MUST NOT be marked as satisfying, passing, or closing it. No
task below closes Req 9.

## Tasks

- [x] 1. Add the push-coalescing authority to `StateKnobs` (MODIFIED: `gateway_v2/domain/state_knobs.py`)
  - [x] 1.1 Add the two named-constant defaults and the two `StateKnobs` fields
    - Add module constants `DEFAULT_COALESCE_WINDOW_MS = 250` and `DEFAULT_PER_OWNER_PUSH_BOUND = 1`,
      each with the documented rationale from the design (never a bare literal at the field, so the
      capacity-literals AST gate over `gateway_v2` stays green — mirror the existing `DEFAULT_*`
      period constants and `resources.py` `_MULT` naming)
    - Add frozen slotted fields `coalesce_window_ms: int = DEFAULT_COALESCE_WINDOW_MS` and
      `per_owner_push_bound: int = DEFAULT_PER_OWNER_PUSH_BOUND`
    - Extend `__post_init__` with: `coalesce_window_ms >= 0`, `per_owner_push_bound >= 1`, and the
      fail-at-start Freshness_Window check `coalesce_window_ms <= enforcement_bound_s * 1000`, each
      raising `ValueError` with the design's messages
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ]* 1.2 Write unit tests for the `StateKnobs` validation
    - Assert a valid config constructs; assert each invalid case raises `ValueError`: negative
      `coalesce_window_ms`, `per_owner_push_bound < 1`, and `coalesce_window_ms` strictly greater
      than `enforcement_bound_s * 1000`; assert the ≤ boundary is accepted
    - _Requirements: 7.3, 7.4, 7.5_

- [x] 2. Extend the `StatePublisher` protocol and in-memory / broken twins (MODIFIED: `state_control/publisher.py`)
  - [x] 2.1 Add `emit_nudge` and the `nudge=` keyword to the protocol and implementations
    - Add `emit_nudge(self, kind: StateKind, feed_seq: int) -> None` to the `StatePublisher`
      Protocol and a `nudge: bool = True` keyword-only argument to `publish_record` and
      `publish_batch` (default `True` keeps every existing caller unchanged)
    - `MemoryStore`: implement `emit_nudge` to append the `(kind, feed_seq)` nudge to `.nudges`;
      make `publish_record` / `publish_batch` honour `nudge=` — when `False`, perform the durable
      write + manifest but append NO nudge
    - `BrokenPublisher`: implement `emit_nudge` to raise (used by the fail-safe tests)
    - _Requirements: 1.1, 1.2, 3.1, 4.3_

  - [ ]* 2.2 Update existing publisher tests for the new keyword
    - Update affected existing `MemoryStore` / publisher tests so the `nudge=` keyword and
      `emit_nudge` are covered without regressing current assertions
    - _Requirements: 3.1, 3.5_

- [x] 3. Make the inline `PUBLISH` conditional in `ValkeyPublisher` (MODIFIED: `state_control/valkey.py`)
  - [x] 3.1 Move the trailing `PUBLISH` out of the durable MULTI behind `nudge=`
    - In `ValkeyPublisher._guarded`, make the inline `pipe.publish(...)` conditional on `nudge`;
      add the `nudge: bool = True` keyword to `publish_record` / `publish_batch`
    - Add `emit_nudge` as a bare `PUBLISH` of `"<kind>:<feed_seq>"` on `StoreKeys.updates`
    - Keep the durable WATCH/MULTI staging (`SET`/`HSET` + `ZADD` + `SADD`/`SREM` + manifest `SET`)
      BYTE-IDENTICAL — only the trailing `PUBLISH` line moves out
    - _Requirements: 1.1, 1.2, 3.1, 3.3, 3.5, 4.3_

  - [ ]* 3.2 Write a fakeredis test for the suppressed-push / `emit_nudge` split
    - Assert the durable staging is byte-identical with `nudge=False` (no inline `PUBLISH`), and
      that `emit_nudge` publishes exactly `"<kind>:<feed_seq>"` on the `updates` channel
    - _Requirements: 3.3, 3.5, 4.3_

- [x] 4. Implement `CoalescerCounters` (NEW: `state_control/coalescer.py`)
  - [x] 4.1 Add the frozen label-free counter type
    - Frozen slotted dataclass mirroring `ListenerCounters`: exactly two int series
      `coalesced_push: int = 0` and `pushes_saved: int = 0`, seeded 0; a `.plus(**fields)` monotonic
      accumulator returning a new value; a `.snapshot()` returning `Mapping[str, int]` that neither
      raises nor mutates; NO per-owner / tenant label and no registry import or self-registration
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

- [x] 5. Implement `CoalesceConfig` and `CoalescingPublisher` (NEW: `state_control/coalescer.py`)
  - [x] 5.1 Add `CoalesceConfig` with `from_knobs` and `__post_init__` re-validation
    - Frozen slotted `CoalesceConfig(per_owner_push_bound, coalesce_window_s, freshness_window_s)`;
      `__post_init__` re-validates `per_owner_push_bound >= 1`, `coalesce_window_s >= 0`, and
      `coalesce_window_s <= freshness_window_s`; `from_knobs(knobs)` reads
      `coalesce_window_ms` / `per_owner_push_bound` / `enforcement_bound_s` from `StateKnobs` (no
      capacity-position literal in this module)
    - _Requirements: 7.1, 7.2, 7.5_

  - [x] 5.2 Add the `_PendingNudge` accumulator and the `CoalescingPublisher` skeleton
    - Slotted MUTABLE `_PendingNudge(window_start, pushes_this_window, highest_pending,
      highest_announced)`; `CoalescingPublisher(inner, config, clock, metrics=CoalescerCounters())`
      with the `dict[tuple[str, StateKind], _PendingNudge]` accounting map; implement the whole
      `StatePublisher` protocol with reads (`stored_*`, `read_stamp`, `put_stamp`) and `publish_kind`
      as pass-through to `inner` (no coalescing)
    - _Requirements: 2.5, 2.6, 5.2_

  - [x] 5.3 Implement durability-first `publish_record` / `publish_batch` + the coalesce algorithm
    - Delegate the Durable_Write to `inner.publish_*(..., nudge=False)` FIRST, capture and return the
      bool unchanged (writer outcome mapping untouched); THEN inside a swallow-everything
      `try/except Exception` run the per-`(owner, kind)` window algorithm from the design: window
      roll flushes the previous window's residue at Highest_Feed_Seq and resets the push count;
      `highest_pending = max(highest_pending, feed_seq)`; emit now while
      `pushes_this_window < bound`, else hold
    - Owner is the write's per-tenant identity (`org_id` / key owner / scope — Req 2.6)
    - `_emit(owner, kind, pos)`: suppress and increment `pushes_saved` when `pos <= highest_announced`
      (monotonic guard); else `inner.emit_nudge`, set `highest_announced = pos`, bump
      `pushes_this_window`, increment `coalesced_push`
    - _Requirements: 2.1, 2.2, 2.3, 2.6, 3.1, 3.6, 4.1, 4.2, 4.5, 6.1, 6.4, 6.5_

  - [x] 5.4 Implement `flush_due(now)` and `flush_all()`
    - `flush_due(now)`: emit any held nudge whose window has elapsed (Highest_Feed_Seq), honouring
      the monotonic guard; `flush_all()`: emit every held pending position regardless of window,
      swallowing failures (shutdown fail-safe); neither emits for a quiesced owner with nothing
      pending
    - _Requirements: 2.4, 5.1, 5.3, 6.2, 6.3_

  - [ ]* 5.5 Write example / edge unit tests for the coalescer (NEW: `tests/state_control/test_coalescer_unit.py`)
    - `nudge=False` suppresses the inline push; `emit_nudge` payload round-trips through
      `parse_nudge`; `window=0, bound=1` reproduces the bare path exactly (one nudge per write);
      `flush_due` / `flush_all` / shutdown behaviour; `CoalescerCounters` present-and-zero,
      monotonic, `.snapshot()` no-raise / no-mutate
    - _Requirements: 2.4, 4.3, 10.4, 10.5, 10.6_

- [x] 6. Wire the coalescer into the service (MODIFIED: `state_control/service.py`)
  - [x] 6.1 Wrap the writer's publisher and add the flush hooks
    - In the service build, construct `CoalescingPublisher(publisher,
      CoalesceConfig.from_knobs(config.knobs), clock=time.monotonic)` and hand it to `StateWriter`;
      keep the re-hydrator's `Rehydrator` on the BARE publisher (`publish_kind` must not be
      coalesced); call `coalescing.flush_due(now)` each service round and `flush_all()` on shutdown
      (`StateWriter` itself is unchanged — it depends only on the protocol)
    - _Requirements: 2.1, 2.5, 5.3, 6.3_

  - [ ]* 6.2 Write an end-to-end integration test over `MemoryStore`
    - Burst-write to one owner through the wired service and assert the nudges collapse to the
      bounded count end-to-end; assert the re-hydrator path (`publish_kind`) stays uncoalesced
    - _Requirements: 2.1, 2.3, 6.3_

- [x] 7. Checkpoint — component + wiring tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Write the ten property-based tests (NEW: `tests/state_control/test_coalescer_properties.py`)
  - House idiom, seeded `random.Random`, **no hypothesis**, **>= 10,000 iterations** each, one
    property per test, each tagged `# Feature: push-coalescing, Property N: ...` and printing the
    seed + counterexample on failure; drive `CoalescingPublisher` over `MemoryStore` with an
    injected clock.
  - [x] 8.1 Property 1 — per-owner push bound; last push carries max `feed_seq`
    - **Property 1: Per-owner push bound (invariant)**
    - **Validates: Requirements 2.1, 4.1**
  - [x] 8.2 Property 2 — durable writes equal N; durable state byte-identical to the bare path
    - **Property 2: Durable writes equal N (invariant)**
    - **Validates: Requirements 3.1, 3.2, 3.5**
  - [x] 8.3 Property 3 — reader (nudges + one backstop round) ends at or above latest `feed_seq`
    - **Property 3: Reader never ends below the latest committed feed_seq (round-trip)**
    - **Validates: Requirements 4.6, 5.1, 5.3, 5.4**
  - [x] 8.4 Property 4 — monotonic announced position; no regressive push on the wire
    - **Property 4: Monotonic announced position (invariant)**
    - **Validates: Requirements 4.5**
  - [x] 8.5 Property 5 — per-owner isolation across interleaved multi-owner workloads
    - **Property 5: Per-owner isolation (invariant)**
    - **Validates: Requirements 2.2, 2.3**
  - [x] 8.6 Property 6 — idempotent flush on a quiesced owner
    - **Property 6: Coalescing is idempotent on a quiesced owner (idempotence)**
    - **Validates: Requirements 2.4**
  - [x] 8.7 Property 7 — fail-safe: injected push failure / shutdown never costs durability or freshness
    - **Property 7: Fail-safe never costs durability or freshness (error condition)**
    - **Validates: Requirements 3.6, 6.1, 6.2, 6.3, 6.4**
  - [x] 8.8 Property 8 — bound derives from the authority; over-long window refused at construction
    - **Property 8: Window bound derives from the authority (invariant)**
    - **Validates: Requirements 7.1, 7.2, 7.5**
  - [x] 8.9 Property 9 — confluence of write ordering (equal durable state + final announced position)
    - **Property 9: Confluence of write ordering (confluence)**
    - **Validates: Requirements 4.1, 4.2**
  - [x] 8.10 Property 10 — push count independent of simulated worker / request count
    - **Property 10: Push count is independent of worker and request count (metamorphic)**
    - **Validates: Requirements 1.3, 1.4, 1.5**

- [x] 9. Write the standing verification obligation tests (NEW: `tests/state_control/test_push_verification.py`)
  - [x] 9.1 Assert one PUBLISH per write / batch, zero on the serving path, count invariant to N and R
    - One `PUBLISH` per record write; exactly one per batch independent of record count; zero on any
      serving / request path (standing guard — `gateway_v2` serving code emits no publish); total
      push count a function of W and O only, unchanged as simulated N (workers) and R (requests) vary
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 10. Write the microbenchmark (NEW: `tests/state_control/test_lgw23_microbench.py`)
  - [x] 10.1 Build the deterministic in-process un-coalesced-vs-coalesced harness
    - One process, injected clock + `MemoryStore` twin, zero network; byte-reproducible across runs
      on the same workload + seed; drive the identical workload through the un-coalesced arm
      (`window=0, bound=1`) and the coalesced arm; assert each per-owner-per-window push count ≤
      Per_Owner_Push_Bound (fail with offending owner / window / observed / configured); report the
      push-count delta and a CPU-ms-shape proxy in R2-23 units ("+2.3 CPU-ms / 72 pushes"); assert
      Durable_Write parity (`record_writes` equal, fail with both counts + workload id on mismatch)
    - Include an in-file note that this is the LOCAL representative of the deferred Knee_Re_Measure
      (Req 9) and does NOT mark it satisfied / passed / closed
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

- [x] 11. Full gate sweep
  - Run the 10 property tests at >= 10,000 iterations, the verification + unit + integration +
    microbench tests, and the full existing `state_control` suite (the `nudge=` keyword change must
    not regress it); run `mypy --strict`, `ruff`, import-linter (both contracts kept — `state_control`
    is outside `root_packages`, and the `gateway_v2/domain/state_knobs.py` change keeps the
    `gateway_v2` contracts green), and the capacity-literals AST gate over `gateway_v2` (the
    `StateKnobs` defaults must be named constants). Confirm all green.
  - _Requirements: 1.1, 3.5, 7.2, 10.2_

- [x] 12. Final checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional pure-example / edge unit tests and can be skipped for a faster
  MVP. The ten property-based tests (task 8), the standing verification obligation (task 9), and the
  microbenchmark (task 10) are REQUIRED per house discipline and are NOT marked optional.
- Each task references specific granular requirement clauses and, for the property tests, the exact
  property number from the design's Correctness Properties section for traceability.
- Checkpoints (tasks 7, 12) ensure incremental validation at the component/wiring boundary and at
  the end.
- The per-layer commit boundary is preserved by the task order: StateKnobs → protocol → valkey →
  counters → coalescer → service → tests.
- NEW files: `state_control/coalescer.py`, `tests/state_control/{test_coalescer_properties.py,
  test_coalescer_unit.py, test_push_verification.py, test_lgw23_microbench.py}`. MODIFIED files:
  `gateway_v2/domain/state_knobs.py`, `state_control/publisher.py`, `state_control/valkey.py`,
  `state_control/service.py`. `state_control/writer.py` is deliberately UNCHANGED (it depends only
  on the `StatePublisher` protocol).
- OUT OF SCOPE (not a task): the fleet Knee_Re_Measure (Req 9) remains an OPEN deferred cloud gate;
  the microbenchmark is only its local representative.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1", "4.1"] },
    { "id": 3, "tasks": ["3.2", "5.1"] },
    { "id": 4, "tasks": ["5.2"] },
    { "id": 5, "tasks": ["5.3"] },
    { "id": 6, "tasks": ["5.4"] },
    { "id": 7, "tasks": ["5.5", "6.1"] },
    { "id": 8, "tasks": ["6.2", "9.1", "10.1"] },
    { "id": 9, "tasks": ["8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "8.9", "8.10"] }
  ]
}
```
