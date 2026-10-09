# Implementation Plan: Admission Control (R2-07 / R2-08 / R2-18 — card GW19)

## Overview

Convert the admission-control design into a series of incremental, test-paired coding steps in the
v3 rewrite tree `gateway_v2/gateway_v2/admit/`. The build is **bottom-up along the import-linter
`layers` contract** (`admit` imports only `runtime` + `domain`), starting with the codes-only
posture addition and the pure CoDel state machine (no I/O), then the pure value/bounds/metrics
modules, then the single event-loop seam (`AdmissionController`), then the local load harness and
acceptance gates, and finally the plan/changelog documentation and the full verification gate.

House conventions enforced throughout (confirmed against `killswitch.py`, `identity.py`,
`resources.py`, `holdback_metrics.py`): injectable `Callable[[], float]` clock (default
`time.monotonic`), injectable `random.Random`, frozen slotted dataclasses, `StrEnum` states,
fail-closed on any ambiguity, producer-only **label-free** metrics. **No HTTP objects in `admit`** —
the component returns a frozen `ShedVerdict`; `edge` renders the 503. **`q_safe` is injected, never
hardcoded; refuse-to-start if unset.** No capacity literal lives in `admit` — every bound is a
`ResourceContract` call.

Property tests are the house idiom: seeded `random.Random` loops, ≥ 10,000 iterations, **no
`hypothesis`**, seed logged in the assertion message, async via a local `_run[T](coro) =
asyncio.run(coro)` helper. Each property sub-task is tagged `# Feature: admission-control,
Property N`.

## Tasks

- [x] 1. Add the `OVERLOAD_SHED` posture code (codes-only, shared vocabulary)
  - [x] 1.1 Add `OVERLOAD_SHED` to `gateway_v2/gateway_v2/domain/posture.py`
    - Add `OVERLOAD_SHED = "overload_shed"` with an R2-08 docstring, as a code only (no status,
      no message, no `ErrorSpec` — `edge` owns the HTTP mapping, same as the existing posture codes)
    - Do NOT redefine `MIN_RETRY_AFTER_S`; it already exists and will be reused as the Retry-After floor
    - _Requirements: 5.1, 6.1_
  - [x]* 1.2 Unit test the new code + `MIN_RETRY_AFTER_S` reuse
    - Assert `OVERLOAD_SHED` value and that `MIN_RETRY_AFTER_S == 1.0` is imported, not redefined in `admit`
    - _Requirements: 5.1, 6.1_

- [x] 2. Implement the pure CoDel state machine (`admit/codel.py`, new)
  - [x] 2.1 Define `CoDelParams`, `CoDelReason`, `CoDelDecision`
    - Frozen slotted `CoDelParams(target_ms=5.0, interval_ms=100.0, hard_cap_ms=60.0)` (owner-signed)
    - `CoDelReason(StrEnum)`: `ADMIT`, `SHED_BACKOFF`, `SHED_HARD_CAP`
    - Frozen slotted `CoDelDecision(admit: bool, reason: CoDelReason, next_drop_at: float)`
    - _Requirements: 1.5_
  - [x] 2.2 Implement `CoDelController` with injected clock
    - `__init__(self, params, *, clock: Callable[[], float] = time.monotonic)`; per-owner state
      (`first_above_at`, `dropping`, `drop_next_at`, `count`, `last_below_at`)
    - `observe_and_decide(sojourn_ms, *, now=None)` implementing the design's exact algorithm:
      hard-cap short-circuit (≥ 60 ms → `SHED_HARD_CAP`); `sojourn <= target` clears the excursion
      and admits (quiescence/reset); first-above starts the excursion; shed only once the min
      sojourn has stayed above target for a full Interval; backoff via
      `drop_next_at = now + interval_ms / sqrt(count)`
    - `reset()` for quiescence; admission depends on sojourn + CoDel state ONLY, never prompt size
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 3.1, 3.2_
  - [x]* 2.3 Property test — CoDel quiescence
    - **Property 6: CoDel quiescence** — min sojourn ≤ Target for a full Interval ⇒ never sheds
    - **Validates: Requirements 1.2** — seeded `random.Random`, ≥ 10,000 iters, tagged `# Feature: admission-control, Property 6`
  - [x]* 2.4 Property test — shedding under sustained overload (CoDel half)
    - **Property 2: Shedding under sustained overload** — sustained sojourn above target ⇒ shed set
      non-empty; more overload does not reduce shedding (pure-controller portion)
    - **Validates: Requirements 1.3, 5.4, 12.2** — tagged `# Feature: admission-control, Property 2`
  - [x]* 2.5 Unit tests — constants, hard cap, clock injection, prompt-size independence
    - Assert Target=5/Interval=100/Hard_Cap=60; hard cap sheds regardless of backoff state (1.4);
      decisions read the injected clock (1.6); no 12 ms instantaneous bound exists (3.2); holding
      sojourn fixed while varying a prompt-size argument yields identical decisions (3.1 metamorphic)
    - _Requirements: 1.4, 1.5, 1.6, 3.1, 3.2_

- [x] 3. Checkpoint — pure CoDel core
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement `ShedVerdict` + Retry-After jitter (`admit/grant.py`, fill)
  - [x] 4.1 Define `ShedVerdict`, `Admitted`, and the jitter helper
    - Frozen slotted `ShedVerdict(code: str, retry_after_s: float, should_retry: bool, request_id: str)`
      with `code = posture.OVERLOAD_SHED`, `should_retry` always `False` on a shed
    - Frozen slotted `Admitted(owner_id, request_id, admitted_at)` marker
    - `shed_retry_after_s(rng, *, floor_s=MIN_RETRY_AFTER_S, jitter_frac=0.5)` returning
      `floor_s + rng.random() * jitter_frac * floor_s` (import `MIN_RETRY_AFTER_S` from `domain.posture`)
    - Keep a `ResourceGrant` placeholder per the card's reservation; no HTTP object anywhere
    - _Requirements: 5.2, 5.3, 6.1, 6.2, 6.3_
  - [x]* 4.2 Property test — Retry-After floor and should-retry
    - **Property 5: Retry-After floor and should-retry** — every shed has `retry_after_s ≥ 1.0`,
      `should_retry is False`, a `request_id` present, and never a value in the 6–11 ms band
    - **Validates: Requirements 5.2, 5.3, 6.1, 6.2, 6.3, 6.4** — injected `random.Random`, ≥ 10,000
      iters, tagged `# Feature: admission-control, Property 5`

- [x] 5. Implement bounds derivation (`admit/quota.py`, fill)
  - [x] 5.1 Define `AdmissionBounds` and `derive_bounds`
    - Frozen slotted `AdmissionBounds(concurrency, request_depth, guard_depth, dispatch_depth, egress_depth, audit_depth)`
    - `derive_bounds(contract, *, q_safe, audit_drain_rate_per_s, audit_bytes_per_record)`:
      refuse-to-start (`CapacityUnset`) if `q_safe` is `None` or ≤ 0; `concurrency`/request/guard/
      dispatch via `contract.queue_depth(q_safe)`; egress slot bound via `queue_depth(q_safe)` +
      byte bound via `contract.stream_buffer_bytes(...)`; audit via
      `contract.audit_queue_depth(audit_drain_rate_per_s, audit_bytes_per_record)`
    - NO numeric capacity literal — every number is a `ResourceContract` call; uncomputable bound
      propagates `CapacityUnavailable`
    - _Requirements: 3.3, 4.1, 4.2, 4.3, 4.4, 4.5, 7.5_
  - [x]* 5.2 Unit tests — each queue→contract-method mapping + refuse-to-start
    - Assert each queue's depth comes from the mapped contract method; `q_safe` unset ⇒ `CapacityUnset`;
      an uncomputable bound ⇒ `CapacityUnavailable`; `q_safe` arrives by injection at construction
    - _Requirements: 3.3, 4.1, 4.2, 4.5, 7.5_
  - [x]* 5.3 AST capacity-literal gate for `admit/`
    - Assert no numeric capacity literal appears in `admit/` except via a `ResourceContract` call
      (mirrors the existing capacity-literal discipline)
    - _Requirements: 4.4, 12.6_

- [x] 6. Implement producer-only, label-free metrics (`admit/metrics.py`, new)
  - [x] 6.1 Define `AdmissionMetrics` and `QueueReport`
    - Fixed, **label-free** series following `runtime/holdback_metrics.py`: per-queue depth and
      oldest-item age (queue name is a fixed finite set, not tenant-derived), `admitted_total`,
      `shed_total{reason}` (fixed reason set), and `fail_open_total` pinned at `0` from the first
      snapshot; O(1) observe, percentiles on read; producer only (publisher GW14d owns exposition)
    - Frozen slotted `QueueReport(mapping queue_name -> (depth, oldest_age_s))`
    - _Requirements: 7.3, 7.4, 13.2_
  - [x]* 6.2 Unit tests — label-free series, real-zero counters, O(1) observe
    - Assert no tenant label on any series; counters exist as `0` from the first snapshot;
      `fail_open_total` starts and stays `0`; depth/age readings reflect observations
    - _Requirements: 7.3, 7.4, 13.2_

- [x] 7. Checkpoint — pure value/bounds/metrics layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement the bounded queues and per-owner registry (`admit/admission.py`, new)
  - [x] 8.1 Implement `Bounded_Queue` depth/age accounting
    - Declared max depth from `AdmissionBounds`; enqueue rule `if depth >= max: shed at the door`;
      track current depth and oldest-item age (read against the injected clock); feed
      `AdmissionMetrics`/`QueueReport`
    - _Requirements: 7.1, 7.2, 7.3, 7.4_
  - [x]* 8.2 Property test — queue-depth bound
    - **Property 4: Queue-depth bound** — for every bounded queue at every step, depth ≤ declared
      max, and an over-max enqueue sheds at the door
    - **Validates: Requirements 7.1, 7.2, 10.1, 10.2** — seeded `random.Random`, ≥ 10,000 iters,
      tagged `# Feature: admission-control, Property 4`
  - [x] 8.3 Implement the per-owner registry
    - `owner_id -> CoDelController`, lazily created from one factory; the guard owner gets a
      controller from the SAME factory (no special global path); per-owner state isolated
    - _Requirements: 1.1, 1.7_
  - [x]* 8.4 Property test — per-tenant fairness
    - **Property 3: Per-tenant fairness** — a saturating owner cannot push another owner's shed rate
      above that owner's fair share; per-owner CoDel state is isolated; guard-owner parity
    - **Validates: Requirements 1.1, 1.7** — seeded `random.Random`, ≥ 10,000 iters, tagged
      `# Feature: admission-control, Property 3`

- [x] 9. Implement `AdmissionController.admit` (event-loop seam)
  - [x] 9.1 Implement the admit path
    - `__init__(*, bounds, params, clock=time.monotonic, rng, supervisor, metrics, drain_window_s)`;
      `async admit(owner_id, request_id, enqueued_at) -> Admitted | ShedVerdict`
    - Compute sojourn = `now - enqueued_at` (ms), consult the owner's `CoDelController`, enqueue
      (admitted — never dropped thereafter) or return a `ShedVerdict`; enqueue gated by the bounded
      queue depth (shed at the door when full); any undecidable path sheds (fail closed); increment
      `shed_total`, keep `fail_open_total` at 0
    - `queue_report()` exposing depth + oldest-age
    - _Requirements: 2.1, 2.2, 2.3, 5.4, 13.1, 13.2, 13.3_
  - [x]* 9.2 Property test — no abandonment
    - **Property 1: No abandonment** — every admitted request eventually produces a response; no
      admitted request is dropped; shedding only at admission before service begins
    - **Validates: Requirements 2.1, 2.2, 2.3** — seeded `random.Random`, ≥ 10,000 iters, tagged
      `# Feature: admission-control, Property 1`
  - [x]* 9.3 Property test — error conditions fail closed
    - **Property 10: Error conditions fail closed** — missing `q_safe`, uncomputable queue bound,
      undecidable admission all refuse-to-start or shed; `fail_open_total` stays 0
    - **Validates: Requirements 4.5, 13.1, 13.2, 13.3** — seeded `random.Random`, ≥ 10,000 iters,
      tagged `# Feature: admission-control, Property 10`

- [x] 10. Implement the drain state machine (`admit/admission.py`)
  - [x] 10.1 Implement `DrainState`, `DrainReport`, and `AdmissionController.drain`
    - `DrainState(StrEnum)`: `ACCEPTING`, `DRAINING`, `TERMINATING`, `FLUSHED`, `EXITED`; frozen
      slotted `DrainReport(duration_s, declared_terminations, audit_flushed)`
    - `async drain()`: SIGTERM stops accepting (ACCEPTING→DRAINING); finish in-flight within
      `Drain_Window`; past the window send a `Declared_Termination` terminal frame per unfinished
      stream (never truncated); flush the audit queue before exit; publish measured duration; all
      timing reads the injected clock
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_
  - [x]* 10.2 Property test — drain terminal guarantee
    - **Property 7: Drain terminal guarantee** — every in-flight stream is terminated within the
      Drain_Window with a Declared_Termination; no stream ends truncated
    - **Validates: Requirements 8.2, 8.3** — seeded `random.Random`, ≥ 10,000 iters, tagged
      `# Feature: admission-control, Property 7`
  - [x]* 10.3 Unit tests — drain transitions, ordering, injected clock
    - Assert ACCEPTING→DRAINING on SIGTERM (8.1), audit flushed before exit (8.4), duration
      published (8.5), all timing from the injected clock (8.6)
    - _Requirements: 8.1, 8.4, 8.5, 8.6_

- [x] 11. Implement the `WorkerSupervisor` seam (`admit/admission.py`)
  - [x] 11.1 Implement the supervisor abstraction
    - `on_worker_exit(worker_id) -> respawn()` and `isolate(worker_id)` removing a crashed worker's
      slots from shared accounting without touching peers; real `fork`/respawn injected
      (`Callable[[], Worker]`), defaulting to a stub that records intent (real OS supervision is the
      serving-entrypoint card's job — declared dependency)
    - _Requirements: 3.4, 9.1, 9.2, 9.3_
  - [x]* 11.2 Unit tests — isolation + respawn + peers keep admitting
    - Drive a fake worker that crashes by raising; assert isolation, respawn-called, and peers keep
      admitting
    - _Requirements: 9.1, 9.2, 9.3_

- [x] 12. Implement admission-side backpressure (`admit/admission.py`)
  - [x] 12.1 Implement credit-based bounded stream-slot buffering
    - Cap a slow consumer's buffered bytes by the egress byte bound (`stream_buffer_bytes`); stop
      feeding a consumer once its credit is exhausted; total streaming memory bounded across any
      number of slow consumers; fast consumers draw from their own credit; cross-reference
      `egress/backpressure.py` / `egress/stream.py` as the eventual real transport (do NOT import
      `egress` — layer rule)
    - _Requirements: 10.1, 10.2, 10.3_
  - [x]* 12.2 Unit tests — bounded memory, fast consumers unaffected
    - Assert buffered bytes stay within the declared bound under a slow consumer and that fast
      consumers are unaffected by slow ones
    - _Requirements: 10.1, 10.2, 10.3_

- [x] 13. Checkpoint — event-loop seam complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Build the in-process open-loop `Load_Harness` and local acceptance gates
  - [x] 14.1 Implement the `Load_Harness` and LGW19-1 / LGW19-4
    - Open-loop generator driving `AdmissionController` directly with an injected clock and injected
      service rate; LGW19-1: 3× injected `q_safe` ⇒ explicit shedding, admitted p99 within budget,
      no unbounded memory; LGW19-4: concurrency cap binds and is observable (not inert as v1)
    - _Requirements: 12.1, 12.2, 12.6_
  - [x] 14.2 Implement LGW19-2 (open-loop sweep) and LGW19-5 (guard rate halved)
    - LGW19-2: ramp offered arrival rate until admitted p99 exceeds budget; record the highest
      passing and first failing arrival rate; LGW19-5: halve the injected guard service rate
      mid-run ⇒ shedding rises, queue age bounded
    - _Requirements: 12.3, 12.4_
  - [x] 14.3 Implement LGW19-6 (slow-consumer backpressure acceptance)
    - Slow consumers on 50% of streams ⇒ confirm backpressure, bounded memory, fast consumers
      unaffected
    - _Requirements: 12.5_
  - [x] 14.4 Implement the local SDK-retry-amplification simulation (G-06 local)
    - [x]* 14.4a Property test — retry-amplification bound
      - **Property 8: Retry-amplification bound** — shed-then-retry under the local SDK-retry
        semantics stays below the G-06 bound, contrasted with the 6–11 ms baseline that triples load
      - **Validates: Requirements 6.5** — seeded `random.Random`, ≥ 10,000 iters, tagged
        `# Feature: admission-control, Property 8`
  - [x] 14.5 Implement the injected-clock post-heal recovery gate (G-15 local)
    - [x]* 14.5a Property test — post-heal recovery bound
      - **Property 9: Post-heal recovery bound** — after an injected store heal, admitted latency
        returns to within the SLO within 10 s on the injected clock (CoDel resets on the first
        sub-target sojourn)
      - **Validates: Requirements 11.1** — seeded `random.Random`, ≥ 10,000 iters, tagged
        `# Feature: admission-control, Property 9`

- [x] 15. Wire the controller into the `admit` package surface
  - [x] 15.1 Export the admission public API from `admit/__init__.py`
    - Re-export `AdmissionController`, `AdmissionBounds`, `derive_bounds`, `ShedVerdict`, `Admitted`,
      `CoDelParams`, `DrainState`, `DrainReport`, `AdmissionMetrics` so `edge` consumes a stable
      surface; confirm nothing imports a layer above `admit`
    - _Requirements: 2.1, 5.1, 7.1_
  - [x]* 15.2 Edge-seam rendering test (503 boundary)
    - Assert the 503 + `Retry-After: ceil(retry_after_s)` + `x-should-retry: false` + echoed
      `request_id` are rendered from a `ShedVerdict` at the `edge` seam (admission constructs no HTTP)
    - _Requirements: 5.1, 6.3_

- [x] 16. Checkpoint — local harness and gates complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. Documentation + R2-07/R2-08 changelog (mirror the R2-06 closure)
  - Write the plan doc `docs/plans/2026-<date>-r2-07-r2-08-gw19-admission-control.md` and create the
    evidence dir `docs/plans/evidence/2026-<date>-r2-07-gw19/` (plan doc + evidence, matching the
    R2-04/R2-05/R2-06 precedent)
  - Append an AGENTS.md R2-07/R2-08 pointer under the R2 changelog section (plan doc + AGENTS.md
    pointer + evidence only — the R2 series does NOT use the four-memory pipeline protocol; do NOT
    add Ruflo/Cursor mirrors)
  - Record the DEFERRED cloud/scale gates as explicitly out of local scope: the full live fleet
    3×-`q_safe` run, the GW20 live `q_safe` measurement, live G-06 against the real OpenAI SDK, and
    live G-15; note the serving-entrypoint-card dependency (`--worker-connections` removal, uvicorn
    worker-class migration, real OS supervision)
  - _Requirements: 3.4, 11.2, 12.7_

- [x] 18. Final local verification gate
  - Run the full `gateway_v2` pytest suite + the admission subset (`pytest gateway_v2/tests/admit/ -q`),
    `mypy --strict`, `ruff`, and `import-linter` (both the `layers` and `forbidden` contracts must stay
    green — `admit` imports only `runtime` + `domain`); run the AST capacity-literal gate
  - Reconcile the recorded evidence numbers (admitted p99, 0 FAIL_OPEN, recovery window, highest-pass
    / first-fail arrival rates) into the plan doc's evidence dir
  - _Requirements: 12.2, 12.3, 13.2_

## Notes

- Tasks marked with `*` are optional (tests) and can be skipped for a faster MVP; core
  implementation tasks are never optional.
- The build is bottom-up along the import-linter layer contract: pure state machine → pure
  value/bounds/metrics → event-loop seam → harness/gates → docs/verification. Each task builds on
  prior ones and ends wired into the `admit` surface (task 15) — no orphaned code.
- Each property test is tagged `# Feature: admission-control, Property N` and uses seeded
  `random.Random` ≥ 10,000 iterations (no `hypothesis`), per the house idiom.
- `q_safe` is injected and the component refuses to start if unset; no capacity literal lives in
  `admit` (only `ResourceContract` holds them) — enforced by the AST gate in task 5.3.
- Admission returns a frozen `ShedVerdict`; `edge` renders the 503 (task 15.2) — no HTTP object in
  `admit`.
- Checkpoints (tasks 3, 7, 13, 16) sit at the layer boundaries, as the R2-06 plan did.
- The deferred cloud/scale gates are documented (task 17), not implemented.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "2.2"] },
    { "id": 2, "tasks": ["2.3", "2.4", "2.5", "4.1", "5.1", "6.1"] },
    { "id": 3, "tasks": ["4.2", "5.2", "5.3", "6.2", "8.1"] },
    { "id": 4, "tasks": ["8.2", "8.3"] },
    { "id": 5, "tasks": ["8.4", "9.1"] },
    { "id": 6, "tasks": ["9.2", "9.3", "10.1"] },
    { "id": 7, "tasks": ["10.2", "10.3", "11.1"] },
    { "id": 8, "tasks": ["11.2", "12.1"] },
    { "id": 9, "tasks": ["12.2", "14.1"] },
    { "id": 10, "tasks": ["14.2", "14.3", "14.4", "14.5"] },
    { "id": 11, "tasks": ["14.4a", "14.5a", "15.1"] },
    { "id": 12, "tasks": ["15.2", "17"] },
    { "id": 13, "tasks": ["18"] }
  ]
}
```
