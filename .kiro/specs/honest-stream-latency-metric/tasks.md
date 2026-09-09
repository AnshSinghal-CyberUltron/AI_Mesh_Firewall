# Implementation Plan: Honest Stream Latency Metric

## Overview

This is a single-function telemetry correctness fix in `gateway/ai_mesh_gateway/stream_orchestration.py` (`build_stream_trace_frame`): re-anchor the streaming `total_latency_ms` on the request-accept epoch (`StreamLaunchContext.start_time`) instead of `metrics.duration_ms`, and recompute the addon split from the re-anchored total. `model_output_ms`, `ttft_ms`, the trace key shape, the SSE frame shape, and the non-streaming path are all unchanged.

The plan is test-driven: scaffold the test module and clock injection first, apply the one production change, then add property tests, the synthetic-timing acceptance test, and edge/regression tests, and finish with the full-suite gate. All new tests live in `gateway/ai_mesh_gateway/tests/`.

## Tasks

- [x] 1. Scaffold the streaming-latency test module and injectable clock harness
  - Create `gateway/ai_mesh_gateway/tests/test_stream_latency_anchor.py`
  - Add a helper that builds a `StreamLaunchContext` with a synthetic `start_time` and a `StreamRunMetrics` with synthetic `provider_start_ts` / `first_token_ts`, and monkeypatches `time.perf_counter` (in `stream_orchestration`) to a controlled stream-close instant so `duration_ms` is deterministic
  - Add a Hypothesis timing-input strategy that generates request-accept epoch, provider-stream-open, first-token, and stream-close instants (including a near-zero gateway pre-provider `P` case for R1.4)
  - Add a small helper to drive `build_stream_trace_frame` (or `_rebuilt_stream_trace` + `compute_addon_split`) and parse the resulting `pipeline_trace` dict
  - _Requirements: 8.1_

- [x] 2. Re-anchor streaming total on the request-accept epoch and recompute the addon split
  - [x] 2.1 Invert the `elapsed_ms` anchoring in `build_stream_trace_frame`
    - Read `time.perf_counter()` ONCE per frame build; set `elapsed_ms = (perf_counter() - ctx.start_time) * 1000` when `ctx.start_time` is truthy, else fall back to `metrics.duration_ms`
    - Reuse the single `elapsed_ms` snapshot for `zeroshield.processing_time_ms`, the `_stage_sum` / `_overhead` reconciliation, and `pt["total_latency_ms"]` (no per-use clock re-read)
    - Leave `model_output_ms = duration_ms - ttft_ms` (in `_rebuilt_stream_trace`), `ttft_ms` sourcing, the enforcement/`zeroshield.action` block, and the frame shape unchanged
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 5.4, 5.5, 7.1, 7.2, 7.3, 7.4_
  - [x] 2.2 Recompute the addon split from the re-anchored total
    - After overwriting `pt["total_latency_ms"] = elapsed_ms`, call `compute_addon_split(pt_metrics, elapsed_ms)` and merge `{t_addon_pre_ms, t_addon_post_ms, t_t2_ms}` into `pt` (same keys, same numeric types)
    - Do not modify `compute_addon_split` itself; only feed it the re-anchored total
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 2.3 Write property tests for the timing algebra
  - **Property 1: Streaming total is the full wall-clock from the request-accept epoch** — assert `total_latency_ms ≈ close − start_time` and `total_latency_ms − duration_ms ≈ P` (±5 ms, ≥100 iterations)
  - **Property 2: The addon differs from provider TTFT by the gateway pre-model time** — with `P` chosen large, assert `|t_addon_pre_ms − ttft_ms| ≈ P` (> 5 ms) so `addon ≠ ttft`
  - **Property 3: The addon split is the total-minus-model algebra** — assert `t_addon_pre_ms == max(0, total − model_output_ms − t_addon_post_ms)` against the re-anchored total
  - **Property 4: TTFT is the provider delta and is independent of the request-accept epoch** — assert `ttft_ms == (first_token_ts − provider_start_ts) × 1000`, invariant across `start_time`, distinct from `t_addon_pre_ms`
  - **Property 5: model_output_ms is provider generation time and is invariant under re-anchoring** — assert `model_output_ms == duration_ms − ttft_ms`, invariant across `start_time`
  - Each test tagged with a comment: `Feature: honest-stream-latency-metric, Property {n}: {property text}`; use the injected clock; ±5 ms tolerance; ≥100 iterations
  - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.5, 3.1, 3.2, 3.4, 4.1, 4.2, 4.3**

- [x] 2.4 Write the Requirement 8 synthetic-timing acceptance test
  - Construct a finalization where the synthetic gateway pre-provider time and provider TTFT differ by ≥ 50 ms; drive `build_stream_trace_frame` with the injected clock
  - Assert (±5 ms): `total_latency_ms ≈ close − start_time`; `t_addon_pre_ms ≈ synthetic gateway pre-provider time`; `t_addon_pre_ms` differs from `ttft_ms` by > 5 ms; `model_output_ms ≈ duration_ms − ttft_ms`
  - On any failed assertion, report which timing value diverged from its expected bound
  - **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6**

- [x] 2.5 Write edge / fail-safe and shape unit tests
  - `ctx.start_time = 0` → `total_latency_ms == duration_ms`, non-negative, no raise (R1.5)
  - `first_token_ts = 0` → `ttft_ms` absent/null, not substituted (R3.3)
  - `ttft_ms ≥ duration_ms` → `model_output_ms == 0` (R4.4)
  - `model_output_ms > total_latency_ms` → `t_addon_pre_ms == 0`, other keys intact (R2.5)
  - Built frame contains all required latency keys (`total_latency_ms`, `ttft_ms`, `model_output_ms`, `stage_latency_sum_ms`, `overhead_ms`, `t_addon_pre_ms`, `t_addon_post_ms`, `t_t2_ms`), each numeric, none renamed/removed; top-level frame keys and `choices == []` unchanged (R5.1–5.5)
  - **Validates: Requirements 1.5, 2.5, 3.3, 4.4, 5.1, 5.2, 5.3, 5.4, 5.5**

- [x] 3. Full-suite gate
  - Run `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q` and confirm zero failures and zero errors, including the existing `test_stream_governance_fidelity.py` (enforcement + frame shape, R5/R7) and the existing non-streaming latency tests unchanged (R6)
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 5.5, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3, 7.4, 8.7_

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP, but they carry the Requirement 8 acceptance proof and the property coverage — recommended to keep.
- Task 2 is the only production-code change (one function in `stream_orchestration.py`); `compute_addon_split` (pipeline_trace.py) and the non-streaming path (main.py) are untouched.
- Each actionable task references specific requirement sub-clauses for traceability.
- Property tests validate the universal timing algebra; edge tests cover the fail-safe branches and the backward-compatible trace/frame shape.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3", "2.4", "2.5"] },
    { "id": 4, "tasks": ["3"] }
  ]
}
```
