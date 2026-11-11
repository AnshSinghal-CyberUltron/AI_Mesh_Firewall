# Implementation Plan: Bounded Holdback (R2-06 / GW12b)

## Overview

This plan implements the owner-signed streaming-output holdback bound ("no more than 3
upstream tokens held") in the active rewrite tree `gateway_v2/gateway_v2/`. It is built
bottom-up along the import-linter layer contract (`egress` → `detect` → `runtime`/`domain`),
so each task compiles and tests in isolation before the layer above depends on it:

1. The pure window helpers (`detect/windowing.py`) and pure scanner (`detect/holdback.py`),
   both testable with no I/O, no transport, and no provider.
2. The runtime config loader and metrics producers (`runtime/`), label-free, producer-only.
3. The output-decision glue (`egress/output_guard.py`) and the thin injectable streaming
   harness (`egress/stream.py`) that wires the scanner end-to-end.
4. The force-release / trade-off state machine and in-flight-kill handling inside the harness.
5. The three local gates — L12b-1 replay benchmark, L12b-3 detector regression, L12b-2 local
   concurrency equivalent — followed by the changelog/evidence doc task and the final
   verification gate.

Testing follows the house idiom: seeded `random.Random` loops of ≥ 10,000 iterations for every
correctness property (mirroring `tests/domain/test_lgw04.py`); **no `hypothesis` dependency is
added**. Each property-based test carries the tag comment
`# Feature: bounded-holdback, Property N: <text>`. Test files live under
`gateway_v2/tests/<layer>/test_lgw12b_*.py` and run with `pytest` from `gateway_v2/`.

## Tasks

- [x] 1. Window-bounding helpers in `detect/windowing.py` (pure, imports only `domain`)
  - [x] 1.1 Implement `max_pattern_length()`, `window_slice()`, and the `MAX_PATTERN_BYTES_FLOOR`/`MAX_PATTERN_BYTES_CEIL` bounds
    - Fill the `detect/windowing.py` stub: `max_pattern_length()` returns the longest bounded
      output-pattern length as a constant clamped to `[1, 65536]`; `window_slice(buf, window)`
      returns `(buf[max(0, len(buf)-window):], offset)` so callers inspect at most `window`
      trailing bytes and never block when fewer bytes exist
    - Keep the module pure (import only from `gateway_v2.domain`); this is the single
      definition of `Window`
    - _Requirements: 4.1, 4.2, 4.3_
  - [x]* 1.2 Write unit tests for windowing bounds in `tests/detect/test_lgw12b_windowing.py`
    - Create `tests/detect/__init__.py`; test the window value is in `[1, 65536]`, that
      `window_slice` returns at most `window` bytes with a correct offset, and that a buffer
      shorter than `window` returns all bytes with offset 0
    - _Requirements: 4.1, 4.2, 4.3_

- [x] 2. Pure holdback scanner in `detect/holdback.py` (new, imports `domain` + `detect.windowing`)
  - [x] 2.1 Implement `TokenIndex` (upstream-token counting model)
    - Add `TokenIndex.tokens_in(s)` (count of maximal `_WORD = [A-Za-z0-9._%+@/=-]` runs) and
      `TokenIndex.token_boundaries(s)` (start index of each run so the oldest tokens can be
      dropped); pure, deterministic, O(len(slice))
    - _Requirements: 1.3, 1.4_
  - [x] 2.2 Implement `hold_start(buf)` and `classify_hold(buf, hold_index)`
    - Adapt the reference `hold_start` into gateway_v2: earliest index whose suffix could still
      grow into a match (word-class runs incl. the `>=2 '@'` email case, numeric runs bounded by
      card max, PEM-header prefix bounded, generic-secret keyword tail over a 64-char window);
      `classify_hold` returns the held class and whether it is in `RELAXED_HOLDBACK_CLASSES`
    - _Requirements: 3.1_
  - [x] 2.3 Define the frozen per-pattern trade-off table and `ScanResult`
    - Add the frozen mapping (AWS/API/email → redact-the-remainder word-class; JWT/card →
      terminate-the-stream; uuid/sha256/url/base64 → relaxed, redact-the-remainder) as the single
      source consulted by scanner and resolver; define the `ScanResult` frozen dataclass
      (`hold_index`, `held_class`, `is_relaxed`, `forced_release`, `forced_release_index`)
    - _Requirements: 6.1_
  - [x] 2.4 Implement `scan(buf, *, final, hold_cap_tokens, token_index, window)` with token-cap force-release
    - Clamp the minimal `hold_start` to the trailing `window`; count held tokens; for a word-class
      hold with `tokens_in(held) > hold_cap_tokens` advance `hold_index` to the boundary leaving
      exactly `hold_cap_tokens` held (force-release oldest), setting `forced_release` and
      `forced_release_index`; for a relaxed hold permit up to `2 * hold_cap_tokens` then signal
      overflow; keep `scan` a pure function of its inputs
    - _Requirements: 1.3, 1.4, 1.5, 4.2, 4.3_
  - [x]* 2.5 Write unit + example tests for the scanner in `tests/detect/test_lgw12b_holdback.py`
    - Test `tokens_in`/`token_boundaries` counts (UUID = 1 run, "a b c" = 3), `hold_start` on
      each pattern class, classification, word-class force-release at cap boundaries, and the
      relaxed-ceiling overflow signal
    - _Requirements: 1.3, 1.4, 1.5, 3.1, 6.1_
  - [x]* 2.6 Write property test — Window-bounded work (Property 3)
    - **Property 3: Window-bounded work** — instrument inspected-byte count; seeded `random.Random`
      loop ≥ 10,000 asserting inspected bytes ≤ `Window` as released length grows
    - **Validates: Requirements 4.2, 4.3, 4.4**
  - [x]* 2.7 Write property test — Word-class hold cap (Property 2)
    - **Property 2: Word-class hold cap** — seeded loop ≥ 10,000 over word-class content and random
      chunk sizes asserting held token count never exceeds `hold_cap_tokens`
    - **Validates: Requirements 1.3, 1.4**
  - [x]* 2.8 Write property test — Idempotent re-scan (Property 7)
    - **Property 7: Idempotent re-scan** — seeded loop ≥ 10,000 re-invoking `scan` on the
      post-release state; assert zero new released bytes / transformations (`f(x) == f(f(x))`)
    - **Validates: Requirements 3.2, 3.5**

- [x] 3. Checkpoint — scanner and windowing
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Holdback config loader in the runtime pattern
  - [x] 4.1 Implement `HoldbackConfig` + `load_holdback_config(env)` in `runtime/holdback_config.py`
    - Follow the `runtime/resources.py::load_contract` env→dataclass+logs pattern: read
      `RV_HOLDBACK_MAX_TOKENS`, validate into `[1, 100]` (default 3 when unset), reject
      non-integer / `<1` / `>100` to default 3 with a warning naming the rejected value; set
      `relaxed_ceiling_tokens = 2 * hold_cap_tokens`; return `(config, logs)`
    - _Requirements: 1.1, 1.2, 1.5, 1.6_
  - [x]* 4.2 Write unit tests for config parse/validation in `tests/runtime/test_lgw12b_config.py`
    - Boundary values 0, 1, 3, 100, 101, "abc", unset → assert resulting cap and that a warning
      log line is emitted naming the rejected value
    - _Requirements: 1.1, 1.2, 1.6_

- [x] 5. Holdback metrics producers (label-free, producer-only)
  - [x] 5.1 Implement `HoldbackMetrics` + `HoldbackReading` in `runtime/holdback_metrics.py`
    - Follow the `audit/metrics.py` / `state_metrics.py` producer-vs-publisher split: fixed,
      label-free series (`held_tokens_hist`, `release_processing_ms`, `holdback_wait_ms`,
      `unbroken_run_bytes_max`, `incomplete_streams_total`, `overflow_total`,
      `forced_release_total`); `observe_stream_complete`/`observe_stream_incomplete` are O(1) and
      read nothing; `snapshot()` returns `HoldbackReading`; counters use real zeros
    - _Requirements: 5.1, 5.2, 5.3, 5.5_
  - [x]* 5.2 Write unit + aggregation tests in `tests/runtime/test_lgw12b_metrics.py`
    - Create `tests/runtime` entries as needed; assert held-tokens histogram records one sample
      per completed stream, incomplete streams increment `incomplete_streams_total` with the
      max-held sample, the two latency series are distinct, and p50 ≤ 2 / p99 ≤ 3 over ≥ 1,000
      synthetic word-class samples (R5.4)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

- [x] 6. Output-decision glue in `egress/output_guard.py` (imports `domain`)
  - [x] 6.1 Implement `OutputResolver` protocol, `enforcing_output_rules(plan)`, and a minimal in-process resolver
    - Fill the stub: `enforcing_output_rules(plan)` returns OUTPUT/BOTH rules with
      `mode is Mode.ENFORCE` and `action in {REDACT, BLOCK, REWRITE}` (empty ⇒ no enforcement);
      `OutputResolver.decide(matches)` maps completed findings to one `Decision` using
      `most_restrictive`; ship a declarative pattern→disposition resolver keyed off the
      trade-off table sufficient for the harness
    - _Requirements: 2.1, 2.2_
  - [x] 6.2 Implement `apply_decision(released, decision, base_offset)`
    - Apply each `Transformation.span` (relative to `base_offset`) replacing the redacted span
      with its placeholder; raise `OutputBlocked` when `decision.disposition is Disposition.BLOCK`;
      fail closed if a redaction cannot cover every byte of the span
    - _Requirements: 3.3, 3.4_
  - [x]* 6.3 Write unit tests for the resolver and decision application in `tests/egress/test_lgw12b_output_guard.py`
    - Create `tests/egress/__init__.py`; test `enforcing_output_rules` on/off, `most_restrictive`
      disposition selection, span-accurate redaction, and the BLOCK → `OutputBlocked` path
    - _Requirements: 2.1, 2.2, 3.3, 3.4_

- [x] 7. Thin injectable streaming harness in `egress/stream.py`
  - [x] 7.1 Define transport value types, exceptions, and `_Text` / `StreamStats`
    - Add `UpstreamChunk`, `DownstreamFrame` (with `error_code` for the Terminal_Error_Frame),
      the `Send` / `ChunkSource` / `KillSignal` injection aliases, and the exceptions
      (`OutputBlocked`, `HoldbackOverflow`, `TradeOffUndefined`, `ScanFailure`,
      `EnforcementUndetermined`); define `_Text` (pending/released/held_since/max counters/hits)
      and `StreamStats`
    - _Requirements: 5.1, 5.5_
  - [x] 7.2 Implement enforcing-output gating and the `StreamPipeline.run` release loop
    - Resolve and store `enforcing_output` once before the first chunk (R2.3); raise
      `EnforcementUndetermined` releasing no bytes when the plan is unresolved (R2.4); when no
      enforcing rule, pass each chunk through unheld within 10 ms (R2.2); otherwise drive each
      text stream through `scan` + `window_slice`, drop released bytes from `pending`, carry one
      char of left-context, apply the `Decision` to released bytes, and flush all held bytes on
      the final chunk
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.5_
  - [x] 7.3 Record per-chunk latency decomposition and per-stream held-token metrics
    - With the injectable clock record `t_ready`, `t_sent`, `oldest_arrival` so
      `T_release_processing = t_sent - t_ready` and `T_holdback_wait = t_ready - oldest_arrival`
      are separate; track `max_held_tokens` and `max_unbroken_run_bytes`; call
      `HoldbackMetrics.observe_stream_complete/_incomplete`
    - _Requirements: 4.6, 4.7, 5.1, 5.2, 5.3, 5.5_
  - [x]* 7.4 Write unit tests for the release path, gating, and final flush in `tests/egress/test_lgw12b_stream.py`
    - Test enforcing-rule gate on (holds) vs off (10 ms pass-through), the undetermined-plan
      `EnforcementUndetermined` withhold-with-no-release, minimal-suffix release, and the
      final-chunk flush of all held bytes
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.5_
  - [x]* 7.5 Write property test — Released equals input minus redacted (Property 4)
    - **Property 4: Released equals input minus redacted** — seeded loop ≥ 10,000 reconstructing
      expected output (input with redactions applied) and asserting byte-equality with
      concatenated released frames
    - **Validates: Requirements 3.2, 3.5**
  - [x]* 7.6 Write property test — Separate latency accounting (Property 8)
    - **Property 8: Separate latency accounting** — seeded loop ≥ 10,000 with a controllable
      injected clock asserting `T_release_lag == T_release_processing + T_holdback_wait` per
      released piece and that the two series populate independently
    - **Validates: Requirements 4.6, 4.7, 5.3**

- [x] 8. Checkpoint — config, metrics, output guard, release path
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Force-release trade-off state machine and error handling in `egress/stream.py`
  - [x] 9.1 Implement the trade-off outcomes and fail-closed error frames
    - On a match completing against already force-released bytes, apply the pattern's declared
      outcome: redact-the-remainder (replace every still-held remainder byte before releasing any
      further byte) or terminate-the-stream (`Terminal_Error_Frame` code `forced_release_tradeoff`);
      emit `holdback_overflow` on the relaxed ceiling releasing no in-progress bytes;
      `undefined_tradeoff` fail-closed for a table-less over-cap pattern; `scan_failure` retaining
      all pending; `output_blocked` withholding unreleased bytes — never emit content frames after
      a terminal frame
    - _Requirements: 3.4, 3.6, 6.2, 6.3, 6.4, 6.5, 6.6_
  - [x]* 9.2 Write unit tests for each trade-off outcome and error code
    - In `tests/egress/test_lgw12b_stream.py`: assert each terminal code
      (`forced_release_tradeoff`, `holdback_overflow`, `undefined_tradeoff`, `scan_failure`,
      `output_blocked`), the redact-the-remainder placeholder coverage, and that no content frame
      follows a terminal frame
    - _Requirements: 3.4, 3.6, 6.2, 6.3, 6.4, 6.5, 6.6_
  - [x]* 9.3 Write property test — No unredacted sensitive release (Property 1)
    - **Property 1: No unredacted sensitive release** — seeded loop ≥ 10,000 embedding sensitive
      values at random chunk splits; assert no raw sensitive value appears in concatenated
      `Released_Bytes` across every path including force-release
    - **Validates: Requirements 3.3, 3.4, 6.3, 6.4, 6.5, 6.6**
  - [x]* 9.4 Write property test — Trade-off completeness (Property 6)
    - **Property 6: Trade-off completeness** — seeded loop ≥ 10,000 over all table patterns forced
      over cap at random splits; assert the produced outcome equals the declared outcome and no
      pattern yields zero or two outcomes
    - **Validates: Requirements 6.1, 6.2**

- [x] 10. In-flight kill (`InFlightKill.CUT_NEXT_CHUNK`) handling in `egress/stream.py`
  - [x] 10.1 Implement chunk-boundary kill checks
    - Check `killed()` at each chunk boundary: cease emission no later than the next boundary,
      emit zero further chunks, discard held bytes that have not passed the `Decision`, terminate
      within the loop iteration signalling `DownstreamFrame` error code `killed`; when the kill is
      observed only after the final chunk was released, complete normally and retract nothing
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
  - [x]* 10.2 Write unit tests for kill semantics
    - Test cut-at-boundary (no further frames), held-byte discard, the `killed` signal, and the
      kill-after-final normal completion
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
  - [x]* 10.3 Write property test — Chunk-split invariance (Property 5)
    - **Property 5: Chunk-split invariance** — seeded loop ≥ 10,000 generating one text, splitting
      it two different random ways, asserting identical match set, outcomes, and released bytes
    - **Validates: Requirements 9.1, 9.4**

- [x] 11. Checkpoint — full pipeline behavior
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. L12b-1 replay benchmark in `tests/egress/test_lgw12b_replay_benchmark.py`
  - [x] 12.1 Implement the 24-combination replay benchmark
    - Replay all 8 content classes (prose, URL, UUID, sha256, paths, JSON, code, base64) at
      2 KB / 8 KB / 16 KB (24 combinations); assert max observed hold ≤ `Hold_Cap` for word-class
      classes (fail reporting class/chunk-size/hold), and max per-chunk holdback-loop block ≤ 0.5 ms
      measured with `time.perf_counter_ns` around the pure-compute block (fail reporting the
      measured value); aggregate pass only when all 24 satisfy both bounds; local resources only
    - _Requirements: 4.4, 4.5, 4.8, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

- [x] 13. L12b-3 detector regression in `tests/egress/test_lgw12b_detector_regression.py`
  - [x] 13.1 Implement the split-offset regression across real patterns
    - Split real AWS keys, API tokens, JWTs, emails, and payment card numbers at ≥ 3 offsets
      (first byte, a middle byte, last byte); assert byte-identical `Trade_Off_Outcome` across
      every offset and equal to single-chunk delivery; patterns split so no single chunk holds a
      complete match are buffered until assembled and never leaked; runs < 300 s, no cloud/fleet
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

- [x] 14. L12b-2 local concurrency/isolation equivalent in `tests/egress/test_lgw12b_concurrency.py`
  - [x] 14.1 Implement the heavy-vs-co-running isolation test
    - Run a heavy base64-16KB or UUID-heavy stream concurrently with exactly one co-running
      stream on the local host; assert the co-running stream's median per-chunk latency rises by
      ≤ 0.5 ms vs its no-concurrent-heavy baseline (fail reporting the degradation and the 0.5 ms
      threshold); fail if either stream produces no measurable chunk; local-host only, no network
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [x] 15. Record deferred cloud gates and the R2-06 four-memory changelog
  - [x] 15.1 Document deferred gates and append the R2-06 changelog entry + evidence
    - In the feature docs, record that the full 200 RPS fleet capacity run (`L12b-2`) and the
      `GW20b` fleet certification are deferred cloud gates out of local scope, each named by
      identifier (R10.5); then update the four-memory changelog per the AGENTS.md pipeline
      protocol — this is the R2 series, so add an **R2-06 / GW12b** entry to the R2 changelog in
      `AGENTS.md` (one-line pointer) and the canonical R2 changelog, store the Ruflo memory
      pointer, update the Cursor rules pointer, and create the plan + evidence location under
      `docs/plans/2026-*-r2-06-gw12b-bounded-holdback.md` and
      `docs/plans/evidence/2026-*-r2-06/`, consistent with how R2-04 and R2-05 were recorded
    - _Requirements: 10.5_

- [x] 16. Final verification gate (local scope)
  - [x] 16.1 Run the gateway_v2 gates and reconcile results
    - From `gateway_v2/`, run the full `pytest` suite (incl. all `test_lgw12b_*` files),
      `mypy --strict`, `ruff`, and `import-linter` — the same gates the R2-04 / R2-05 entries
      used — fix any failures, and record the local pass counts in the evidence location; the
      full 200 RPS fleet run and GW20b remain explicitly deferred (not run locally)
    - _Requirements: 8.6, 9.5, 10.2, 10.5_

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP, but the
  8 correctness properties and the three local gates (L12b-1/3/2) are the heart of this feature's
  provability — skipping them forfeits the local verification the requirements mandate.
- Every task references the specific requirement sub-clauses it satisfies; property tasks also
  name the design property they validate.
- The import-linter layering is honored by construction: `detect/windowing.py` and
  `detect/holdback.py` import only `domain`; `runtime` config/metrics import only `domain`;
  `egress/*` import `detect` + `domain` + `runtime`.
- Property-based tests use seeded `random.Random` loops of ≥ 10,000 iterations with the seed
  logged (house idiom from `tests/domain/test_lgw04.py`); **no `hypothesis` dependency is added**.
- Checkpoints (tasks 3, 8, 11) ensure incremental validation at each layer boundary.
- Deferred cloud gates (200 RPS L12b-2 fleet run, GW20b certification) are out of local scope and
  are recorded as deferred in task 15, not implemented.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "4.1", "5.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "4.2", "5.2"] },
    { "id": 2, "tasks": ["2.2", "6.1"] },
    { "id": 3, "tasks": ["2.3", "2.4", "6.2"] },
    { "id": 4, "tasks": ["2.5", "2.6", "2.7", "2.8", "6.3"] },
    { "id": 5, "tasks": ["7.1"] },
    { "id": 6, "tasks": ["7.2"] },
    { "id": 7, "tasks": ["7.3"] },
    { "id": 8, "tasks": ["7.4", "7.5", "7.6"] },
    { "id": 9, "tasks": ["9.1"] },
    { "id": 10, "tasks": ["9.2", "9.3", "9.4"] },
    { "id": 11, "tasks": ["10.1"] },
    { "id": 12, "tasks": ["10.2", "10.3"] },
    { "id": 13, "tasks": ["12.1", "13.1", "14.1"] },
    { "id": 14, "tasks": ["15.1"] },
    { "id": 15, "tasks": ["16.1"] }
  ]
}
```
