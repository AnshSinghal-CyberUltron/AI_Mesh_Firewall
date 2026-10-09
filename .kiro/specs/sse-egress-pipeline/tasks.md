# Implementation Plan: SSE Egress Pipeline (GW12)

## Overview

Convert the GW12 design into a series of code-generation steps that implement the SSE
egress serving skin **bottom-up along the import-linter layer contract**
(`runtime → egress → dispatch → edge`), the same shape as the shipped R2 budget-lease and
admission-control cards. Each layer is validated by its own gates before the next layer
builds on it, so there is no orphaned code: every value type, bound, and seam is wired into a
caller in a later step, and the final step boots the ASGI app with the streaming chat route
live. The holdback engine (GW12b / R2-06) — `egress/stream.py::StreamPipeline`,
`detect/holdback.py`, `runtime/holdback_config.py`, `runtime/holdback_metrics.py`,
`domain/locks.py::InFlightKill.CUT_NEXT_CHUNK` — is **DONE** and is wired into, never rebuilt.

Implementation language: **Python** (the design is grounded in concrete `gateway_v2/*.py`
modules and the shipped house idiom). All code targets `mypy --strict` + `ruff` clean.

### Conventions enforced in EVERY coding task

- **Property tests**: a seeded `random.Random` driven for **≥10,000 iterations**, **NO
  hypothesis**; async paths via `asyncio.run`. Each property test is tagged
  `# Feature: sse-egress-pipeline, Property N: <text>` and `# Validates: Requirements X.Y`,
  with exactly one property ↔ one property-based test. Files are named
  `tests/{edge,egress,dispatch,runtime}/test_lgw12_*.py`.
- **Value types** are frozen slotted dataclasses or `StrEnum`; `FirstByteLatch` is the one
  documented mutable one-way-latch exception. No module-level mutable — tables use
  `MappingProxyType`.
- **No capacity literal outside `runtime/resources.py`** — all timeouts/durations/bounds
  derive from `ResourceContract`.
- **`egress` MUST NOT import `detect`** — the scanner and `ProviderClient` are injected from
  `dispatch`/`edge` (both above `detect` in the layer order).
- **Fail-closed everywhere**; the `FAIL_OPEN` counter is pinned at zero and never incremented.
- **File size gates**: ≤800 lines/module, ≤120-line functions. Splits are noted in the task.
- Each task references its requirement sub-clauses and the design section/property it
  satisfies (traceability), matching the budget-lease `tasks.md` style.

## Tasks

- [ ] 1. Capacity authority — `runtime/resources.py` derivations (bottom of the build order)
  - [ ] 1.1 Add the GW12 `ResourceContract` derivations
    - Add module-level literals `_INTER_CHUNK_TIMEOUT_MULT`, `_IDLE_TIMEOUT_MULT`,
      `_WRITE_TIMEOUT_MULT`, `_MAX_STREAM_DURATION_MULT`, `_MAX_CUT_LATENCY_MULT`,
      `_MAX_SNAPSHOT_AGE_MULT`, `_CANCEL_BOUND_MULT` (the ONLY module permitted capacity
      literals, gated by `lint/check_capacity_literals.py`)
    - Add methods `inter_chunk_timeout_s`, `idle_timeout_s`, `write_timeout_s`,
      `max_stream_duration_s`, `max_cut_latency_s`, `max_snapshot_age_s`,
      `cancellation_bound_s` deriving seconds from `target_p99_ms`; each clamps to a derived
      floor and raises `CapacityUnavailable` on a non-positive result (fail closed, matching
      shipped `stream_buffer_bytes`)
    - Extend `snapshot()` to surface every new derivation alongside `stream_buffer_bytes`
      (off-loop readable); keep `stream_buffer_bytes(active_streams)` unchanged as the sole
      high-water source
    - If the module would exceed 800 lines, split the GW12 derivations into
      `runtime/resources_stream.py` as a mixin and note the split
    - _Design: Components §1, Data Models (derivation formulas)_
    - _Requirements: 9.1, 9.5, 10.1, 16.5_

  - [ ]* 1.2 Write unit tests for the derivation formulas and fail-closed floors
    - `tests/runtime/test_lgw12_resources.py`: assert each method equals `p99_s × mult`, that
      a non-positive derivation raises `CapacityUnavailable`, and that `snapshot()` surfaces
      every new field
    - No literal timeout asserted outside the contract (read values from the methods)
    - _Design: Testing Strategy (supporting unit tests)_
    - _Requirements: 9.1, 10.1_

- [ ] 2. Stream posture codes — `domain/posture.py` (codes only, bottom layer)
  - [ ] 2.1 Add the GW12 value-codes
    - Add `STREAM_INTER_CHUNK_TIMEOUT`, `STREAM_IDLE_TIMEOUT`, `STREAM_WRITE_TIMEOUT`,
      `STREAM_MAX_DURATION`, `STREAM_KEY_REVOKED`, `STREAM_PLAN_CHANGED`,
      `STREAM_SNAPSHOT_STALE`, `STREAM_MALFORMED_UPSTREAM`, `STREAM_BUFFER_UNAVAILABLE` as
      module-level `str` constants (plus `stream_killed`/`scan_failure`/`output_blocked` reuse
      noted); REUSE existing `MIN_RETRY_AFTER_S` and existing codes, do not redefine
    - Codes only — no HTTP object, no rendering here
    - _Design: Components §7 (new posture codes), House conventions_
    - _Requirements: 3.3, 9.2, 10.2_

  - [ ]* 2.2 Write unit tests for code uniqueness and single-spelling
    - `tests/domain/test_lgw12_posture.py`: assert each new code is a unique non-empty string
      and no duplicate spelling exists across the module
    - _Design: Error Handling (codes-vs-render boundary)_
    - _Requirements: 3.3_

- [ ] 3. Bounded coalescer + credit flow control — `egress/backpressure.py`
  - [ ] 3.1 Implement `Coalescer` and `CreditFlowControl`
    - Frozen slotted `CreditState(granted, consumed)` (outstanding derived, never stored) and
      `CoalescerVerdict(code, high_water)`; imports only `runtime` + `domain` (below `detect`)
    - `Coalescer.high_water()` returns `contract.stream_buffer_bytes(active_streams())` — NO
      literal; never name `Semaphore(<int>)`/`Queue(<int>)` or `maxsize=/high_water=/
      buffer_size=` literals (gated)
    - `offer`/`release`/`buffered` enforce buffered ≤ high-water across the stream lifetime
      (backpressure on reach, resume on fall-below); `admit()` fails closed with
      `STREAM_BUFFER_UNAVAILABLE` and buffers zero payload bytes when high-water < 1
    - `CreditFlowControl.grant_on_consume(N)` grants exactly N, zero when nothing consumed;
      `outstanding()` = granted − consumed (never negative); `may_read()` gates reads;
      conserve `granted == outstanding + consumed` at every observation point
    - Injected: `contract`, `active_streams: Callable[[], int]`
    - _Design: Components §2, Data Models (CreditState/CoalescerVerdict)_
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.2, 5.3, 5.5, 5.7_

  - [ ]* 3.2 Write property test for the memory-bound invariant
    - `tests/egress/test_lgw12_coalescer.py` — **Property 1: Memory-bound invariant**
    - **Validates: Requirements 4.4, 5.3, 5.4**; seeded `random.Random` ≥10,000 iters, no
      hypothesis: total buffered ≤ `active_streams × stream_buffer_bytes(active_streams)` at
      every observation point; also assert fail-closed admit below 1 byte (R4.5)
    - _Design: Correctness Properties → Property 1_

  - [ ]* 3.3 Write property test for credit conservation
    - `tests/egress/test_lgw12_credit.py` — **Property 2: Credit conservation**
    - **Validates: Requirements 5.7**; ≥10,000 iters: outstanding + consumed = granted with
      zero deviation; grant-exactly-N and grant-zero-when-idle covered
    - _Design: Correctness Properties → Property 2_

- [ ] 4. Dispatch layer — `dispatch/transform.py`, `dispatch/routing.py`, `dispatch/provider.py`
  - [ ] 4.1 Implement `Transform` (byte-verified round-trip)
    - `dispatch/transform.py`: `to_provider(GatewayRequest) -> UpstreamRequest` and
      `from_provider(UpstreamEvent) -> DownstreamFrame`; round-trip of a well-formed payload
      is equivalent; pure, no clock/rng/I/O
    - _Design: Components §5_
    - _Requirements: 8.4_

  - [ ] 4.2 Implement `DispatchRouter` (deterministic selection)
    - `dispatch/routing.py`: `select(plan) -> str` is a pure function of plan inputs; identical
      plan → identical destination; no clock/rng/I/O
    - _Design: Components §4_
    - _Requirements: 8.3_

  - [ ] 4.3 Implement `ProviderClient` protocol + `StubProviderClient`
    - `dispatch/provider.py`: `@runtime_checkable ProviderClient` with `open(UpstreamRequest)
      -> AsyncIterator[UpstreamEvent]` and `async abort()`; frozen slotted `UpstreamRequest`
      and `UpstreamEvent(text_deltas, final, error_frame)`
    - `StubProviderClient(script, rng)` is in-process (no socket), driven by a seeded
      `random.Random`; `abort()` stops reads — this is what local tests inject
    - _Design: Components §3, Data Models (UpstreamRequest/UpstreamEvent)_
    - _Requirements: 8.1, 8.2, 8.5, 8.6_

  - [ ]* 4.4 Write determinism + round-trip + abort unit tests
    - `tests/dispatch/test_lgw12_routing.py`: identical plan → identical destination
    - `tests/dispatch/test_lgw12_transform.py` — **Property 8 (transform half): round-trip**
    - **Validates: Requirements 8.4**; ≥10,000 iters, no hypothesis
    - `tests/dispatch/test_lgw12_provider.py`: `StubProviderClient` isinstance of protocol,
      `abort()` halts iteration (via `asyncio.run`)
    - _Design: Correctness Properties → Property 8; Testing Strategy_
    - _Requirements: 8.1, 8.2, 8.3, 8.5_

- [ ] 5. SSE codec + state machine — `edge/wire/sse.py`
  - [ ] 5.1 Implement `SSEEncoder` and `SSEDecoder`
    - `edge/wire/sse.py` (the ONLY layer allowed to render SSE): frozen slotted `SSEChunk`,
      `SSEChoice`, `SSEErrorFrame`
    - `SSEEncoder.content(frame)` → `data: {json}\n\n`; `done()` → `data: [DONE]\n\n`;
      `error(error_code)` → declared SDK-parseable `Error_Frame` with no content frame after
    - `SSEDecoder.feed(raw)` buffers a split UTF-16 surrogate escape across chunk boundaries
      and decodes the code point once complete; `malformed()` signals fail-closed
    - If `edge/wire/` would exceed 800 lines, split encoder/decoder into
      `edge/wire/encode.py` + `edge/wire/decode.py` and note the split
    - _Design: Components §6, Data Models (SSEChunk/SSEChoice/SSEErrorFrame)_
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [ ]* 5.2 Write property test for codec round-trip
    - `tests/edge/test_lgw12_sse_codec.py` — **Property 8: SSE codec round-trip**
    - **Validates: Requirements 1.6, 8.4**; ≥10,000 iters: decode-then-encode of a well-formed
      content frame yields an equivalent `DownstreamFrame`
    - _Design: Correctness Properties → Property 8_

  - [ ]* 5.3 Write property test for split-surrogate confluence
    - `tests/edge/test_lgw12_sse_codec.py` — **Property 9: Split-surrogate confluence**
    - **Validates: Requirements 1.4**; ≥10,000 iters: decoding the same byte stream under all
      chunk-boundary placements (including boundaries splitting a surrogate escape) yields the
      same code points; also assert `malformed()` fail-closed on undecodable bytes (R1.5)
    - _Design: Correctness Properties → Property 9_

- [ ] 6. Checkpoint — lower layers green
  - Ensure all tests pass, ask the user if questions arise. Run `mypy --strict`, `ruff`,
    `lint-imports`, and `check_capacity_literals`/`check_no_module_mutable`/
    `check_frozen_dataclasses` over `runtime`/`domain`/`egress`/`dispatch`/`edge/wire`.

- [ ] 7. Error envelope + ASGI app assembly — `edge/errors.py`, `edge/app.py`
  - [ ] 7.1 Implement `Error_Envelope`
    - `edge/errors.py`: the single value-code → HTTP/SSE renderer; `_ENVELOPE` table as a
      `MappingProxyType`; `render(code, as_sse)` maps a code to a declared HTTP status + SSE
      `Error_Frame`; an unmapped code renders a declared generic error, never raw internal
      detail; only `edge`/`resolve` may construct `HTTPException`/`JSONResponse`/`status_code=4xx`
    - _Design: Components §7 (Error_Envelope), Error Handling (codes-vs-render)_
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ] 7.2 Implement `build_app` ASGI assembly
    - `edge/app.py`: `build_app(contract, provider, scanner, resolver, metrics_registry,
      clock)` assembles router + lifespan + `/metrics` + `/readyz`, replacing `app = None`
    - Wire the streaming chat route to the coalescer + injected `StreamPipeline` (scanner +
      `ProviderClient` injected HERE, above `detect`); register responses/misc/mcp/rag as
      registered-for-later placeholders
    - `/readyz` returns success when all state deps are available, else a non-success status
      naming the missing dep; `/metrics` exposition computed off the serving loop
    - If `edge/app.py` would exceed 800 lines, split route wiring into `edge/routes.py` and
      note the split
    - _Design: Components §7 (ASGI_App), Architecture (layer map)_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 14.1, 14.2_

  - [ ]* 7.3 Write unit tests for the envelope and app plumbing
    - `tests/edge/test_lgw12_errors.py`: envelope mapping + generic fallback for an unmapped
      code (R3.4)
    - `tests/edge/test_lgw12_app.py`: router registers chat + placeholders, `/readyz` names
      the missing dep, `/metrics` returns exposition (in-process ASGI transport)
    - _Design: Testing Strategy (supporting unit tests)_
    - _Requirements: 2.1, 2.4, 2.5, 2.6, 3.4_

- [ ] 8. First-byte latch — `edge`
  - [ ] 8.1 Implement `FirstByteLatch` and wire the no-splice gate
    - Mutable slotted one-way `FirstByteLatch` (documented exception): `may_retry()` true only
      while unset; `set_on_release()` flips on the first released content byte; after set, an
      upstream failure yields clean termination or a declared `Error_Frame`, never a spliced
      fallback; the gateway never concatenates two upstream responses
    - Wire the latch into the stream orchestration (above the egress loop) so retry/fallback is
      gated before provider hand-off
    - _Design: Components §9, Data Models (FirstByteLatch)_
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [ ]* 8.2 Write property test for no-post-first-byte-splice
    - `tests/edge/test_lgw12_first_byte_latch.py` — **Property 3: No-post-first-byte-splice**
    - **Validates: Requirements 7.3, 7.4**; ≥10,000 iters: once set, downstream carries bytes
      from at most one upstream response and a splice attempt does not alter the first
      response's bytes
    - _Design: Correctness Properties → Property 3_

- [ ] 9. Cancellation controller — `edge/cancel.py`
  - [ ] 9.1 Implement `CancellationController`
    - `edge/cancel.py`: frozen slotted `CancelOutcome(elapsed_ms, bound_exceeded,
      released_bytes)`; `on_disconnect()` starts the clock, calls `provider.abort()` AND sets
      `killed() → True` within `contract.cancellation_bound_s()`, stops upstream reads,
      releases the buffer, returns credit, measures elapsed ms, exports per cancelled stream
    - On elapsed > bound: force-release the connection + state and record a bound-exceeded
      cancellation; released byte count equals buffered-at-disconnect; no buffered bytes
      forwarded downstream
    - Injected: `provider`, `coalescer`, `contract`, `clock`, `metrics`
    - _Design: Components §8, Control path diagram, Data Models (CancelOutcome)_
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 9.2 Write property test for cancellation-within-bound
    - `tests/edge/test_lgw12_cancellation.py` — **Property 4: Cancellation-within-bound**
    - **Validates: Requirements 6.3, 6.5, 6.6**; ≥10,000 iters, async via `asyncio.run`:
      disconnect injected at any chunk boundary completes within `cancellation_bound_s()` and
      buffered bytes + credit return to baseline
    - _Design: Correctness Properties → Property 4_

- [ ] 10. In-flight control + max stream duration (C24) — drive the `CUT_NEXT_CHUNK` seam
  - [ ] 10.1 Implement the in-flight control triggers
    - In the controller (reusing `edge/cancel.py` + the shipped `InFlightKill.CUT_NEXT_CHUNK`
      seam): `max_stream_duration_s()`, kill switch, key revocation, plan change, and
      stale/unavailable snapshot (age > `max_snapshot_age_s()`) all drive `killed()` within
      `max_cut_latency_s()`, emit the matching declared `Error_Frame` (`STREAM_MAX_DURATION`,
      `stream_killed`, `STREAM_KEY_REVOKED`, `STREAM_PLAN_CHANGED`, `STREAM_SNAPSHOT_STALE`),
      and forward no further upstream bytes after the cut; snapshot path fails closed
    - _Design: Components §8 (one seam, many triggers), Error Handling table_
    - _Requirements: 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

  - [ ]* 10.2 Write unit tests mapping each R10 trigger to a cut + Error_Frame
    - `tests/edge/test_lgw12_inflight_control.py`: one test per trigger asserts a
      `CUT_NEXT_CHUNK` within `max_cut_latency_s()`, the correct code, no post-cut bytes, and
      fail-closed on a stale/missing snapshot
    - _Design: Error Handling table_
    - _Requirements: 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

- [ ] 11. C27 timeout enforcement wiring
  - [ ] 11.1 Wire inter-chunk / idle / write timeouts
    - In the controller/serving loop: compare against `inter_chunk_timeout_s()`,
      `idle_timeout_s()`, `write_timeout_s()`; on breach terminate with the matching declared
      `Error_Frame` (`STREAM_INTER_CHUNK_TIMEOUT`/`STREAM_IDLE_TIMEOUT`/`STREAM_WRITE_TIMEOUT`)
      and release resources; no literal timeout outside `runtime/resources.py`
    - _Design: Components §1, Error Handling table_
    - _Requirements: 9.2, 9.3, 9.4, 9.5_

  - [ ]* 11.2 Write property test for timeout termination
    - `tests/runtime/test_lgw12_timeouts.py` (+ a stall case in
      `tests/edge/test_lgw12_cancellation.py`) — **Property 10: Timeout termination**
    - **Validates: Requirements 9.2, 9.3, 9.4**; ≥10,000 iters, async via `asyncio.run`: any
      stall past a derived timeout terminates with a declared `Error_Frame` and releases
      resources
    - _Design: Correctness Properties → Property 10_

- [ ] 12. Mid-stream provider error-frame scanning
  - [ ] 12.1 Implement the error-frame scan loop
    - In the SSE read loop (`edge`/`dispatch`, above `detect`), reusing the injected
      `OutputResolver` + `apply_decision`: when `UpstreamEvent.error_frame is not None`, scan
      the frame through the injected scanner BEFORE forwarding any byte; `redact` → forward
      with redactions via `apply_decision`; `block` → withhold + declared `Error_Frame`; scan
      raises → withhold + declared `Error_Frame` (fail closed); scan cost linear in byte length
    - `egress` still does not import `detect` — scanner stays injected
    - _Design: Components §10, Error Handling table_
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 14.1, 15.1, 15.2_

  - [ ]* 12.2 Write property test for byte-linearity of mid-stream scan
    - `tests/edge/test_lgw12_error_frame_scan.py` — **Property 5: Byte-linearity of mid-stream
      scan**
    - **Validates: Requirements 11.5**; ≥10,000 iters: `cost(2n) ≈ 2·cost(n)` within tolerance
    - _Design: Correctness Properties → Property 5_

  - [ ]* 12.3 Write property test for fail-closed-on-scan-error
    - `tests/edge/test_lgw12_error_frame_scan.py` — **Property 6: Fail-closed-on-scan-error**
    - **Validates: Requirements 11.4, 15.2**; ≥10,000 iters: for any injected scan error no raw
      bytes of the affected frame are forwarded
    - _Design: Correctness Properties → Property 6_

- [ ] 13. Per-request exports — `runtime/stream_metrics.py` (HoldbackMetrics split)
  - [ ] 13.1 Implement `PerRequestExports`
    - New producer-only, label-free reading alongside `HoldbackMetrics` (zeros-not-absence,
      percentiles on read, publisher separate): frozen slotted `PerRequestReading(
      detector_invocations, release_lag_ms, buffer_high_water, active_stream_memory_bound)`
    - `observe_*` methods + `record_withheld()`; detector count matches the plan-derived
      schedule; `active_stream_memory_bound` = `active_streams × stream_buffer_bytes`; NO
      tenant label; an uncomputable export is recorded as a withholding, never compensated with
      raw bytes; expose a `FAIL_OPEN` counter pinned at zero (never incremented)
    - Wire into the coalescer (memory bound on active-stream change) and the controller
      (release lag, cancel interval)
    - _Design: Components §11, House conventions_
    - _Requirements: 5.6, 12.1, 12.2, 12.3, 12.4, 15.3_

  - [ ]* 13.2 Write property test for no-FAIL_OPEN
    - `tests/egress/test_lgw12_fail_closed.py` — **Property 7: No-FAIL_OPEN**
    - **Validates: Requirements 15.3**; ≥10,000 iters including every injected fault (scan
      error, timeout, cancel): the `FAIL_OPEN` counter stays zero
    - _Design: Correctness Properties → Property 7_

  - [ ]* 13.3 Write unit tests for the export surface
    - `tests/runtime/test_lgw12_stream_metrics.py`: zeros-not-absence, label-free,
      detector-count matches schedule, withholding recorded on an uncomputable value
    - _Design: Testing Strategy_
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

- [ ] 14. C38 serving-loop discipline
  - [ ] 14.1 Offload CPU-bound work off the serving loop
    - Run tokenization + scanning in a GIL-releasing executor (`loop.run_in_executor`, executor
      sized by `ResourceContract`, no literal), not inline; `/metrics` exposition computed off
      the loop; treat serving-loop lag as an SLO input; the forwarding loop runs no single
      CPU-bound slice longer than the declared slice bound
    - _Design: Components §12, House conventions_
    - _Requirements: 13.1, 13.2, 13.4, 13.5_

  - [ ]* 14.2 Write CPU-burst isolation test
    - `tests/edge/test_lgw12_serving_loop.py`: a CPU burst injected mid-stream keeps other
      concurrent streams' worst-chunk p99 within the derived budget
    - _Design: Correctness Properties context; Requirements 13.3_
    - _Requirements: 13.3_

- [ ] 15. Local acceptance harness (LGW12-1, LGW12-7)
  - [ ] 15.1 Build the N-stream memory-plateau harness (LGW12-1 local equivalent)
    - `tests/egress/test_lgw12_concurrency.py`: N in-process streams over the injected harness
      assert `total buffered ≤ Active_Streams × stream_buffer_bytes(active_streams)` and
      buffered memory does not grow (pattern: shipped `test_lgw12b_concurrency.py`)
    - _Design: Local acceptance equivalents, Deferred gates (LGW12-1)_
    - _Requirements: 16.1_

  - [ ] 15.2 Build the in-process ASGI conformance harness (LGW12-7 local equivalent)
    - `tests/edge/test_lgw12_asgi_conformance.py`: in-process ASGI transport + recorded-frame
      checks for the `data: [DONE]` terminal marker, the `Error_Frame` shape, and
      split-surrogate decoding
    - _Design: Local acceptance equivalents, Deferred gates (LGW12-7)_
    - _Requirements: 16.2_

- [ ] 16. Documentation + changelog (GW-card closure style; NO four-memory mirrors)
  - [ ] 16.1 Write the plan doc, evidence dir, and AGENTS.md pointer
    - Create `docs/plans/<date>-gw12-sse-egress-pipeline.md` (scope, build order, what wires
      into GW12b, deferred gates); create `docs/plans/evidence/<date>-gw12/README.md` +
      `gate-results.json` (recorded gate output)
    - Add ONE AGENTS.md pointer entry mirroring the R2 closure style (plan doc + evidence
      only); record the deferred cloud gates (full 1,000-stream plateau, real-TCP both-SDK
      conformance, 200 RPS fleet cert) as DEFERRED with their named local equivalents — these
      have NO implementation tasks
    - This is a GW card, NOT an R2 item: do NOT create the four-memory Ruflo/Cursor mirrors
    - _Design: Deferred cloud / scale gates_
    - _Requirements: 16.3_

- [ ] 17. Final verification gate
  - [ ] 17.1 Run the full gate and reconcile evidence
    - Run the full `gateway_v2` pytest suite + the `lgw12` subset; `mypy --strict`; `ruff`;
      `lint-imports`; and ALL AST gates: `check_capacity_literals`, `check_no_module_mutable`,
      `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_tenant_scale`,
      `import_linter` (assert no `egress → detect` edge, layer order intact)
    - Reconcile the recorded `gate-results.json` against the run; fix any gate failure before
      closing
    - _Design: AST / lint gates, Testing Strategy_
    - _Requirements: 14.1, 14.2, 14.3, 16.4, 16.5_

- [ ] 18. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks (property, unit, integration) and can be
  skipped for a faster MVP; core implementation sub-tasks are never optional. The agent MUST
  implement un-starred sub-tasks and MUST NOT implement starred ones.
- Each task references specific requirement sub-clauses and the design section/property it
  satisfies for traceability.
- Checkpoints (tasks 6, 18) ensure each layer's gates (`mypy --strict`, `ruff`, import-linter,
  AST gates) are green before the next layer builds on it.
- Property tests use a seeded `random.Random` for ≥10,000 iterations, no hypothesis, async via
  `asyncio.run`; one property ↔ one test; tagged `# Feature: sse-egress-pipeline, Property N`
  and `# Validates: Requirements X.Y`.
- `egress` never imports `detect`; the scanner and `ProviderClient` are injected from
  `dispatch`/`edge`. All capacity literals live only in `runtime/resources.py`. The pipeline
  fails closed everywhere and the `FAIL_OPEN` counter stays zero.
- Keep every module ≤800 lines and every function ≤120 lines; splits are noted in tasks 1.1,
  5.1, 7.2.
- The deferred cloud gates (1,000-stream plateau, real-TCP both-SDK conformance, 200 RPS fleet
  cert) have NO implementation tasks; task 16.1 records them deferred with local equivalents.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "2.2", "3.1", "4.1", "4.2", "4.3"] },
    { "id": 2, "tasks": ["3.2", "3.3", "4.4", "5.1"] },
    { "id": 3, "tasks": ["5.2", "5.3", "7.1", "7.2"] },
    { "id": 4, "tasks": ["7.3", "8.1", "13.1"] },
    { "id": 5, "tasks": ["8.2", "9.1", "13.2", "13.3"] },
    { "id": 6, "tasks": ["9.2", "10.1", "11.1", "12.1"] },
    { "id": 7, "tasks": ["10.2", "11.2", "12.2", "12.3", "14.1"] },
    { "id": 8, "tasks": ["14.2", "15.1", "15.2"] },
    { "id": 9, "tasks": ["16.1"] },
    { "id": 10, "tasks": ["17.1"] }
  ]
}
```
