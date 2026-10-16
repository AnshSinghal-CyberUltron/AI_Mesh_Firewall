# Implementation Plan: Output Streaming Modes (GW13)

## Overview

This plan builds the locally-buildable half of GW13 — the second explicit streaming mode,
`STRICT_WITHHOLD`, and the honest-distinctness glue — **alongside** the shipped INCREMENTAL
pipeline, never rewriting it. The design uses the project's existing Python (`gateway_v2/`),
so no implementation-language question is required.

The build is bottom-up and single-threaded through the layer contract. The independent
leaves land first: the new terminal code in `domain/posture.py` (C9), the one-line compiler
set change in `plan/compiler.py` (C3), and the `StrictMetrics` completion-overhead producer
in `runtime/` (C7). Then the shared transport in `egress/stream.py` gains two append-only
`StreamStats` fields and the degenerate flag (C5/C6), the new `StrictWithholdPipeline` lands
in `egress/strict.py` (C1), the `select_pipeline` factory lands in `egress` (C2), and the
streaming chat path at the edge is wired through it and stamps the Produced_Mode (C4/C5).
A single test task delivers all 11 property tests plus the LGW13 mechanism tests in one file,
then the exit gate runs the full suite and every repository gate.

Each task references the design component (C1–C9) and the specific requirement sub-clauses it
implements. Sub-tasks marked `*` are optional test tasks and are not auto-implemented.

## Tasks

- [x] 1. Add the STRICT buffer-overflow terminal code (C9)
  - In `gateway_v2/gateway_v2/domain/posture.py`, add one member to the single shared code
    vocabulary: `STREAM_STRICT_BUFFER_OVERFLOW = "strict_buffer_overflow"`, with a docstring
    tying it to R9.5 (STRICT accumulation exceeded `ResourceContract.stream_buffer_bytes`;
    fail closed, zero content).
  - Keep the C37 single-spelling discipline: reuse the shipped `output_blocked` and
    `stream_killed` spellings; do not add duplicates. Confirm `edge/errors.py` renders the
    new code via its generic terminal-code path (or note it does, if the mapping is already
    code-driven) — no new HTTP object in a non-edge layer.
  - House idiom: `from __future__ import annotations`, add the member to any `__all__` /
    code-set the module exports.
  - _Requirements: 9.5, 10.6_

- [x] 2. Accept STRICT_WITHHOLD in the compiler (C3)
  - In `gateway_v2/gateway_v2/plan/compiler.py`, change
    `SUPPORTED_STREAMING = frozenset({StreamingMode.INCREMENTAL})` to
    `frozenset({StreamingMode.INCREMENTAL, StreamingMode.STRICT_WITHHOLD})`.
  - Make no other change: `compile_plan` already threads `streaming_mode` onto the
    `ExecutionPlan`, a mode outside the set still raises `CompileError` with the previous
    plan intact, and the plan-document round-trip already encodes/decodes STRICT_WITHHOLD.
  - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [x] 3. Add the StrictMetrics completion-overhead producer (C7)
  - Create a `StrictMetrics` producer in `gateway_v2/gateway_v2/runtime/` (new module, e.g.
    `strict_metrics.py`), mirroring `runtime/holdback_metrics.py`: label-free, producer-only
    (no `# TYPE` exposition here), O(1) observation, read-only `snapshot` building a frozen
    reading, a structural `StrictStatsLike` Protocol declared locally (runtime sits below
    egress, so do NOT import `egress`), and a fixed module-level `SERIES_COUNT`.
  - Record **completion overhead only**: a single `completion_overhead_ms` histogram family
    (ns → ms on record) plus real-zero counters for incomplete/overflow/block. It MUST NOT
    carry any `T_release_processing`, `T_holdback_wait`, or time-to-first-token series — the
    STRICT profile never inherits the INCREMENTAL TTFT claim.
  - No tenant-derived label on any series (the R2-10 lesson).
  - _Requirements: 9.1, 9.2, 9.4, 10.6_

- [x] 4. Add produced-mode and degenerate observables to StreamStats (C5/C6)
  - In `gateway_v2/gateway_v2/egress/stream.py`, append two fields to the existing
    `@dataclass(slots=True) StreamStats` (append-only, keep it mutable like today):
    `produced_mode: StreamingMode = StreamingMode.INCREMENTAL` (R5.2 — the *selected* mode)
    and `degenerated_to_strict: bool = False` (R6.2).
  - In `_run_enforcing`, set `degenerated_to_strict = True` iff no content frame was emitted
    before the `final` chunk was processed and content was present (track
    `released_before_final` bytes; `True` when that is `0` with content present). A single
    `final=True` first chunk → `True`; a multi-chunk stream that released early → `False`.
    Make no other change to the release loop.
  - _Requirements: 5.2, 6.1, 6.2_

- [x] 5. Implement the StrictWithholdPipeline and StrictStats (C1)
  - In `gateway_v2/gateway_v2/egress/strict.py` (currently an empty stub), implement
    `StrictWithholdPipeline` as a sibling of `StreamPipeline`: same injection-seam shape,
    reusing the `UpstreamChunk` / `DownstreamFrame` transport types imported from
    `egress.stream`, the `OutputResolver` + `apply_decision` + `enforcing_output_rules` seam
    from `egress.output_guard`, and the injected `Detector = Callable[[str], Sequence[Finding]]`.
    Do NOT import `detect` — the completed-match source is the injected detector (egress↛detect).
  - `__init__` takes `resolver`, `enforcing_output`, `max_buffer_bytes` (from
    `ResourceContract`, no literal), `metrics: StrictMetrics`, `clock`, and an optional
    injected `detector`. `for_plan` mirrors `StreamPipeline.for_plan`: raise the reused
    `EnforcementUndetermined` on an unresolved plan BEFORE construction (fail closed, release
    nothing); set `enforcing_output = bool(enforcing_output_rules(plan))`.
  - `run(chunks, send, killed)` loop: (a) when not enforcing → pass-through parity with
    `StreamPipeline._run_passthrough`, still reporting `produced_mode = strict_withhold`;
    (b) otherwise accumulate every `(channel, delta)` into per-channel `whole` buffers and
    release nothing; check `killed()` at the chunk boundary (same `CUT_NEXT_CHUNK` contract) —
    a kill before `final` discards all buffers and emits one terminal `stream_killed`;
    if accumulated bytes exceed `max_buffer_bytes` emit a terminal `strict_buffer_overflow`
    and fail closed; (c) on `final`, run `detector(whole)` per channel, resolve ONCE via
    `resolver.decide(findings)` at `Phase.OUTPUT`, then on BLOCK emit a single terminal
    `output_blocked` with zero content frames before it (LGW13-1), else emit
    `apply_decision(whole, decision, base_offset=0)` content frame(s) and complete cleanly.
  - Record the STRICT latency into `StrictMetrics` (clock at `final` → clock when the whole
    sanitized response is emitted). Do NOT use the holdback scanner, `TokenIndex`, or the
    hold cap — STRICT is a distinct mode (C25 not rebuilt).
  - Add `@dataclass(slots=True) StrictStats` in the same module per the design's Data Models
    (`upstream_chunks`, `frames_out`, `buffered_bytes`, `completion_overhead_ns`,
    `produced_mode = StreamingMode.STRICT_WITHHOLD`, `done`, `incomplete`, `error`). House
    idiom: `from __future__ import annotations`, tuple `__all__`, no module-level mutable,
    no capacity literal.
  - _Requirements: 1.4, 1.5, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 7.2, 9.5_

- [x] 6. Add the select_pipeline factory (C2)
  - Add `select_pipeline(plan, ...)` in `egress` (decision: appended to `egress/stream.py`
    so the edge reads one module and no new `egress → egress` sibling edge is introduced;
    if placed in a new `egress/select.py` instead, keep the layer contract intact). It
    returns `StrictWithholdPipeline.for_plan(...)` when
    `plan.streaming_mode is StreamingMode.STRICT_WITHHOLD`, else `StreamPipeline.for_plan(...)`,
    keeping the one-resolver seam identical for both and passing the STRICT deps
    (`max_buffer_bytes`, `strict_metrics`) and the shared `resolver` / `detector`.
  - Both returned pipelines expose a uniform `run(chunks, send, killed)` coroutine returning
    a stats value carrying `produced_mode`, so the edge driver treats them uniformly.
  - _Requirements: 1.3, 1.4, 5.3_

- [x] 7. Wire the streaming chat path through select_pipeline and stamp Produced_Mode (C4/C5)
  - In `gateway_v2/gateway_v2/edge/routes.py` (`ChatRoute._build_pipeline` / `_next_attempt`),
    replace the single `StreamPipeline.for_plan(plan, ...)` construction with
    `select_pipeline(plan, ...)`, passing the STRICT deps
    (`max_buffer_bytes = self.contract.stream_buffer_bytes(self.active_streams())`,
    `strict_metrics`). Leave the SSE `_sink`, `FirstByteLatch`, `KillLatch`, and
    `StreamTimeouts` wiring unchanged — `StrictWithholdPipeline.run` honours the same
    `send`/`killed` contract and simply never trips the first-byte latch until the whole
    response clears.
  - Stamp the Produced_Mode: on the streaming path emit a non-content SSE meta/trace frame
    `{"produced_mode": ..., "degenerated_to_strict": ...}` alongside the terminal marker
    (not content, so it does not break the no-content-after-terminal invariant); on the
    non-streaming path (`edge/openai_nonstream.py::collect_and_enforce`, already STRICT-shaped)
    stamp `produced_mode` from `plan.streaming_mode` and add the `x-amf-produced-mode` response
    header via the shipped `emit_response` header tuple (touch `edge/openai_chat.py` only as
    needed to thread the field).
  - Scope: the chat path is the tested proof. Other edge surfaces (`/v1/responses`, MCP,
    RAG/vector) remain the shipped `not_implemented` placeholders and will call the same
    factory when a later card wires them.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 5.1, 5.2, 5.3, 5.4_

- [x] 8. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x]* 9. Write the single GW13 property + mechanism test file (one file, one pass)
  - **SINGLE-TEST-FILE LESSON (serialized, same-file work owned by this one task — do NOT
    split across parallel sub-agents).** Create exactly one file
    `gateway_v2/tests/egress/test_output_streaming_modes_gw13.py` (co-located with the shipped
    GW12b `StreamPipeline` tests). Property 9 (compiler round-trip) lives in this same file
    rather than a `tests/plan/` sibling — the plan test dir is LGW05-themed and the GW13
    round-trip belongs with the GW13 suite; stated as the decision.
  - Follow the GW12b house idiom: seeded `random.Random`, `asyncio.run`-driven, **no
    `hypothesis`**, iteration counts per the design (≥ 100 iters for the GW13 async props).
    Build the fakes: a fake chunk source (`UpstreamChunk` generator with a parametrised split
    offset), a fake `Detector` returning completed `Finding`s with absolute spans over the
    whole text, a recording `OutputResolver` (scripted `Decision`, records every `decide`
    call), and a recording `send` sink capturing the frame sequence.
  - Deliver all 11 property tests, one per property, each tagged
    `# Feature: output-streaming-modes-gw13, Property N: <text>` and `# Validates: Requirements X.Y`:
    - **Property 1: STRICT releases nothing before the terminal result** — _Validates: Requirements 3.2, 3.4_
    - **Property 2: STRICT BLOCK yields zero prohibited bytes (LGW13-1)** — _Validates: Requirements 3.4, 1.5_
    - **Property 3: STRICT emitted bytes equal apply_decision of the whole buffer** — _Validates: Requirements 1.4, 1.5, 3.3, 3.5_
    - **Property 4: INCREMENTAL never presents truncation as block** — _Validates: Requirements 2.3, 2.5, 2.6_
    - **Property 5: INCREMENTAL truthful block before the first byte** — _Validates: Requirements 2.2_
    - **Property 6: INCREMENTAL redacts only not-yet-released content** — _Validates: Requirements 2.4_
    - **Property 7: Boundary safety in both modes (LGW13-6)** — _Validates: Requirements 7.1, 7.2, 7.3_
    - **Property 8: Degenerate flag set exactly on single-chunk responses** — _Validates: Requirements 6.1, 6.2_
    - **Property 9: Compiler round-trips both streaming modes** — _Validates: Requirements 4.1, 4.2, 4.3_
    - **Property 10: Resolver-owned UNAVAILABLE posture on the output path (LGW13-3)** — _Validates: Requirements 8.1, 8.2, 8.4_
    - **Property 11: MONITOR output rules never enforce (LGW13-4)** — _Validates: Requirements 8.3, 8.4_
  - Add the LGW13 mechanism / example tests in the same file: one-resolver wiring (both modes
    call the resolver at `Phase.OUTPUT`, nothing else decides, STRICT never calls the holdback
    scanner/`TokenIndex`); no-second-decision-site (no `enforce_output` symbol); mode selection
    (`select_pipeline` returns the right pipeline, chat path constructs it); Produced_Mode
    stamp (strict → `strict_withhold`; incremental multi-chunk → `incremental`+`false`;
    incremental single-chunk → `incremental`+`true`; present on stats and on the wire);
    latency labelling (`StrictMetrics` has a completion-overhead series and NO
    TTFT/release-lag/holdback-wait field, distinct from `HoldbackMetrics`, fixed `SERIES_COUNT`;
    INCREMENTAL still records into `HoldbackMetrics` unchanged); buffer bound
    (`max_buffer_bytes` from `ResourceContract`, overflow fails closed with
    `strict_buffer_overflow`); compiler guard (unsupported value still raises `CompileError`,
    `previous` not mutated); and the LGW13-1/2/3/4/6 mechanism scenarios.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.2, 2.3, 2.4, 2.5, 2.6, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 7.1, 7.2, 7.3, 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.4, 9.5_

- [x] 10. Exit gate — full suite and every repository gate
  - Run the full offline suite (`gateway_v2/`): it MUST stay at or above the baseline
    (1852 passed / 95 skipped / 1 xfailed on `suraj-revamp`) plus the new GW13 tests.
  - Run `mypy --strict`, `ruff check gateway_v2 state_control lint tests`, and `lint-imports`
    (3 contracts kept, including `egress` forbidden from importing `detect`).
  - Run each of the 7 AST gates and confirm all exit 0 — especially `check_capacity_literals`
    (no STRICT high-water literal; the bound comes from `ResourceContract.stream_buffer_bytes`),
    `check_no_module_mutable`, and `check_http_outside_edge_resolve` (no HTTP object in egress).
  - Confirm `domain/channels.py::unscanned_channels() == ()` still holds (R10.4) — GW13 adds
    no field the scanner skips.
  - _Requirements: 10.4, 10.6_

- [x] 11. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- **Single-test-file lesson.** The main GW13 Python test file (task 9) is ONE file written in
  one pass by a single owner. Its 11 property tests + the LGW13-1/2/3/4/6 mechanism tests +
  the latency-labelling, one-resolver-wiring, mode-selection, Produced_Mode, buffer-bound,
  and compiler-guard cases are serialized same-file work and MUST NOT be split across parallel
  sub-agents (the GW10 Task 7 / GW11 Task 8 lesson). Property 9 (compiler round-trip) lives in
  this same file, not a `tests/plan/` sibling.
- **No high-water literal.** The STRICT buffer bound is read from
  `ResourceContract.stream_buffer_bytes(active_streams)`. GW13 introduces no literal high-water
  mark; `check_capacity_literals` must stay green.
- **egress ↛ detect, injected detector.** `egress/strict.py` and `select_pipeline` live in
  `egress`, which must not import `detect`. The completed-match source is the injected
  `Detector = Callable[[str], Sequence[Finding]]`, exactly as the shipped `StreamPipeline`.
  `StrictMetrics` is a `runtime/` producer (label-free, fixed `SERIES_COUNT`), declaring a
  structural `StrictStatsLike` Protocol locally because `runtime` sits below `egress`.
- **Reused, not rebuilt.** The INCREMENTAL `StreamPipeline`, the one resolver + `apply_decision`
  seam, the plan vocabulary / document round-trip, the C21 output-field channels, the C4
  latency split, and the C25 bounded holdback engine are all shipped and untouched. STRICT is a
  distinct mode, not a holdback variant (R3.6).
- **Deferred boundaries (Requirement 10).** The measured latency numbers and the two signed
  latency profiles (LGW13-5) are DEFERRED to GW20 — GW13-local proves only mechanical
  distinctness + separate labelling (R10.1). The console mode-visibility UI is DEFERRED to a UI
  card; GW13 stamps the backend field only (R10.2). The real provider socket and live wire
  captures are a DEFERRED live gate (R10.5). Other edge surfaces beyond the chat path stay as
  shipped placeholders.
- **Validator note.** The design's Correctness Properties section raised 4 benign validator
  warnings on the italic restatement lines; all 11 properties DO carry `Validates:` refs. This
  does not block tasks.
- Tasks marked `*` are optional and are not auto-implemented; task 9 is the test task.
- Each task references specific requirements for traceability; checkpoints ensure incremental
  validation.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2", "3"] },
    { "id": 1, "tasks": ["4"] },
    { "id": 2, "tasks": ["5"] },
    { "id": 3, "tasks": ["6"] },
    { "id": 4, "tasks": ["7"] },
    { "id": 5, "tasks": ["9"] },
    { "id": 6, "tasks": ["10"] }
  ]
}
```
