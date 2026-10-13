# Implementation Plan: GW14d Observability Contract v3

## Overview

This plan converts the GW14d design into a bottom-up, test-first implementation sequence for the
`gateway_v2/` package (Python 3.12, raw ASGI, `psycopg` + `redis` only). It follows the
import-linter Layer_Contract exactly as the shipped GW12 card did: each layer is built and committed
before the layer above it depends on it.

The build order is: **ResourceContract derivations** (the single capacity authority) → **producer
`SERIES_COUNT` fixed expressions** (four MODIFY tasks) → **the renderer/parser** (no deps above
runtime) → the six independent `edge` mechanisms (**cardinality, windowed, rates, counters,
event_log, readiness** — parallelizable) → **the Publisher** (merges all of them) → **wiring into
`edge/app.py`** → the **full gate + property-test sweep**.

The producers are NOT rebuilt. The shipped `Histogram`, `ShedReason`, `AdmissionReading`,
`QuotaReading`, `PerRequestReading`, `HoldbackReading`, `audit.record` vocabulary, `domain.posture`
codes, and `runtime.state_stamp.state_ready` are reused, not redefined.

All property tests use the house idiom: a seeded `random.Random`, no `hypothesis`, ≥10,000
iterations, one file per property, tagged `# Feature: observability-contract, Property N: <text>`,
printing a counterexample with its seed on failure.

## Tasks

- [x] 1. ResourceContract capacity derivations (the single capacity authority)
  - [x] 1.1 Add the four derivation methods to `runtime/resources.py` (MODIFIED)
    - Add `_METRICS_DIRECTORY_SLOTS_MULT`, `_METRICS_TOP_K_FRACTION`, `_METRICS_WINDOW_MULT`,
      `_EVENT_LOG_RATE_MULT` module literals in `resources.py` ONLY (the one capacity-literals-gate
      exempt module); these are the ONLY new capacity-position literals.
    - Implement `metrics_directory_slots()` (= f(queue_depth/connection_budget)),
      `metrics_top_k()` (= floor(fraction · directory_slots), ≤ directory_slots),
      `metrics_window_s()` (= (target_p99_ms/1000) · mult), `event_log_rate()` (= f(workers/target
      rate)), each a pure function of existing contract fields, mirroring the shipped
      `_stream_timeout_s(mult)` pattern.
    - Each method fails closed with `CapacityUnavailable` if its derived value is below its minimum
      (slots/K below 1, window ≤ 0, rate below 1), matching every shipped contract method.
    - _Requirements: 2.7, 3.3, 4.2, 9.3_
  - [ ]* 1.2 Write property test for contract-derived capacity
    - **Property 9: Directory capacity derives from the contract, never a literal**
    - **Validates: Requirements 2.7, 3.3, 4.2, 9.3, 15**
    - Same contract ⇒ same derived K/slots/window/rate; scaling a field scales the derivation; each
      fails closed below its minimum.
    - File: `tests/runtime/test_capacity_derivation_property.py`
  - [ ]* 1.3 Write unit tests for the derivation edge cases
    - Below-minimum contract fields raise `CapacityUnavailable`; `metrics_top_k() ≤
      metrics_directory_slots()` holds for a representative contract set.
    - _Requirements: 2.7, 3.3_

- [x] 2. Producer `SERIES_COUNT` fixed expressions (four MODIFY tasks)
  - [x] 2.1 Add `SERIES_COUNT` to `AdmissionMetrics` and `QuotaMetrics` (`admit/metrics.py`, MODIFIED)
    - Add a fixed by-construction expression to each (scalar series + each `Histogram` family counted
      as `_bucket`+`_sum`+`_count` = 3 series under the chosen bucket set), matching the
      audit/state_control/state_metrics convention.
    - _Requirements: 15.1, 15.2_
  - [ ]* 2.2 Write test asserting the admit `SERIES_COUNT` equals rendered family count
    - Assert each fixed expression equals the actual series the producer can emit.
    - File: `tests/admit/test_metrics_series_count.py`
    - _Requirements: 15.2_
  - [x] 2.3 Add `SERIES_COUNT` to `PerRequestExports` (`runtime/stream_metrics.py`, MODIFIED)
    - Fixed expression counting scalars + the two `Histogram` families as 3 series each.
    - _Requirements: 15.1, 15.2_
  - [ ]* 2.4 Write test asserting the stream `SERIES_COUNT` equals rendered family count
    - File: `tests/runtime/test_stream_metrics_series_count.py`
    - _Requirements: 15.2_
  - [x] 2.5 Add `SERIES_COUNT` to `HoldbackMetrics` (`runtime/holdback_metrics.py`, MODIFIED)
    - Fixed expression counting scalars + the three `Histogram` families as 3 series each.
    - _Requirements: 15.1, 15.2_
  - [ ]* 2.6 Write test asserting the holdback `SERIES_COUNT` equals rendered family count
    - File: `tests/runtime/test_holdback_metrics_series_count.py`
    - _Requirements: 15.2_

- [x] 3. PrometheusRenderer + parser (`edge/render.py`, NEW)
  - [x] 3.1 Implement the renderer, parser, and histogram-bucketing helper
    - `metric_type(name)` — the exact `audit/metrics.py` suffix rule (`_total` ⇒ counter, else
      gauge); histograms typed `histogram` explicitly.
    - `render_scalar(name, value)` — `# TYPE` line + one series line.
    - `render_histogram(base, hist, bounds)` — native `_bucket`/`_sum`/`_count`: cumulative-monotone
      finite `le="b"` buckets, terminal `+Inf` bucket equal to `_count`, `_sum` = Σsamples, explicit
      finite bounds only.
    - `render_families(families)` — de-dup by family base name (one `# TYPE` per family), deterministic
      sorted order, byte-stable output.
    - `parse(text)` — read rendered text back into `ParsedFamily` tuples for the round-trip property.
    - Define `Family` and `ParsedFamily` as frozen+slotted dataclasses.
    - _Requirements: 1.4, 1.5, 1.6, 1.7, 8.1, 8.2, 11.1_
  - [ ]* 3.2 Write property test for exposition round-trip
    - **Property 8: Round-trip of the exposition text**
    - **Validates: Requirements 1.4, 1.5, 1.6, 1.7, 8.1, 8.2, 11.1**
    - `render(parse(render(state))) == render(state)` byte-for-byte; type matches suffix rule;
      histogram `_bucket` non-decreasing, `_bucket{le="+Inf"} == _count`, `_sum == Σsamples`.
    - File: `tests/edge/test_exposition_roundtrip_property.py`

- [x] 4. CardinalityDirectory — the R2-10 core (`edge/cardinality.py`, NEW)
  - [x] 4.1 Implement the bounded Top-K + other + Overflow_Counter directory
    - Slotted dataclass; `_capacity = contract.metrics_directory_slots()`, `_k =
      contract.metrics_top_k()`; `_overflow_total` real-zero from construction; `_available` flag.
    - `observe(tenant_key, magnitude)` guard-wraps its whole body: any internal failure routes to the
      Overflow_Counter and returns (NEVER raises). Size-check-before-insert: absent key AND
      `len(_slots) == _capacity` ⇒ increment overflow by exactly one, modify no slot, return. Existing
      key updates in place (no new slot).
    - `top_k_series()` — K highest by deterministic tie-break `(magnitude desc, tenant_key asc)`.
    - `other_value()` — sum of non-top-K (conservation: Σtop-K + other == Σall slots).
    - `overflow_total()`, `available()`.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.4, 3.5, 3.6, 3.7, 3.8, 12.3, 12.4, 15.3_
  - [ ]* 4.2 Write property test for no-raise-on-overflow
    - **Property 1: No request-path raise on overflow**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 12.3, 12.4**
    - No `observe()` raises; `overflow_total` == over-capacity/internally-failed count; `len(_slots) ≤
      capacity` always; existing-key update never allocates. Adversarial generators: duplicate keys, a
      key forcing an internal failure, capacity-1.
    - File: `tests/edge/test_publisher_overflow_property.py`
  - [ ]* 4.3 Write property test for Top-K magnitude preservation + conservation
    - **Property 3: Top-K preserves the largest magnitudes**
    - **Validates: Requirements 3.1, 3.4, 3.5, 3.6**
    - Retained set == K-largest under the tie-break; `other == Σrest`; `Σtop-K + other == Σall`;
      repeated exposition of the same input yields the same selection.
    - File: `tests/edge/test_cardinality_topk_property.py`
  - [ ]* 4.4 Write unit tests for cardinality edge cases
    - Capacity-1 directory; existing-key update does not allocate.
    - File: `tests/edge/test_cardinality_edge.py`
    - _Requirements: 2.6, 3.4_

- [x] 5. WindowedHistogram (`edge/windowed.py`, NEW)
  - [x] 5.1 Implement the rolling-window C4 add-on histogram + windowed-busy reuse
    - Slotted dataclass; `_window_s = contract.metrics_window_s()`; injected `_clock`; `_samples`
      deque of `(observed_at, ms)`; explicit finite `_bounds` 0..≥1000ms + terminal overflow (no
      infinite boundary other than that terminal).
    - `observe(ms)` records a C4 add-on sample. `prune(now)` discards samples older than `now −
      _window_s`. `p50()`/`p99()` computed from in-window samples ONLY (never lifetime/stale).
    - `EMPTY_WINDOW` — defined non-negative empty-window value reported when the window has no samples.
    - `buckets()` — native Prometheus buckets.
    - Generic windowed-busy gauge reuse: accepts externally supplied samples; emits `# TYPE gauge`
      even with no sample supplied; no GPU source fabricated, no synthesized value.
    - _Requirements: 4.1, 4.3, 4.4, 4.6, 4.7, 11.3, 11.4, 13.4_
  - [ ]* 5.2 Write property test for windowed p99 tracking
    - **Property 4: Windowed p99 tracks the injected window**
    - **Validates: Requirements 4.1, 4.3, 4.4, 4.5, 4.6, 4.7, 13.4**
    - `|server_p99 − direct_p99| ≤ 0.5 ms` and `server_p99 ≥ direct_p99 − 0.5 ms`; out-of-window
      samples never change p50/p99; empty window reports `EMPTY_WINDOW`, never < 0. Injected clock;
      generators place samples on both sides of window boundaries + include empty windows.
    - File: `tests/edge/test_windowed_histogram_property.py`

- [x] 6. ResetSafeRate (`edge/rates.py`, NEW)
  - [x] 6.1 Implement reset-safe rate book-keeping
    - Slotted dataclass with `_baselines`. `rate_or_omit(name, current, interval_s)`:
      no baseline ⇒ adopt current, return `None` (omit, never zero); `current < baseline` ⇒ reset:
      adopt current as new baseline, return `None` for this interval; otherwise return
      `(current − baseline) / interval_s ≥ 0` and advance the baseline.
    - Applied to counter-derived rate series ONLY; `ABSENT = -1.0` sentinel gauges are not rates and
      pass through unchanged.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 13.1, 13.2, 13.3_
  - [ ]* 6.2 Write property test for reset-safe rate
    - **Property 5: Reset-safe rate omits rather than publishes a negative**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 13.1, 13.2, 13.3**
    - Every emitted rate ≥ 0; first observation ⇒ omit; `current < baseline` ⇒ omit then next
      non-decreasing interval ≥ 0; restart modelled as these two cases produces no negative; `ABSENT`
      gauges pass through unchanged.
    - File: `tests/edge/test_reset_safe_rate_property.py`
  - [ ]* 6.3 Write unit test for sentinel gauge passthrough
    - `ABSENT = -1.0` sentinel gauges emitted unchanged, never rate-processed.
    - File: `tests/edge/test_rates_sentinel.py`
    - _Requirements: 5.5_

- [x] 7. RejectionCounters + PostureCounters (`edge/counters.py`, NEW)
  - [x] 7.1 Implement the fixed-vocabulary counter sets
    - `REASON_VOCAB` = `(record.SHED, record.REJECTED)` from `audit.record`; `POSTURE_CODES` = the 5
      base codes + the 10 `STREAM_*` codes from `domain.posture` (exact single-spelled strings, no
      alternative spelling).
    - `RejectionCounters.observe(status, reason)` increments distinct `rejected{status,reason}` series;
      distinct reasons never merge into `rejected_503`; out-of-vocab reason ⇒ increment
      `publisher_vocab_error_total` + surface error, never a merged/default series.
    - `RejectionCounters.series()` / `PostureCounters.series()` — real zero per bucket/code from first
      exposition; posture counters distinct from Policy_BLOCK; unknown posture code ⇒ coverage-gap
      error.
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 14.1, 14.2_
  - [ ]* 7.2 Write property test for distinct counters vs policy BLOCK
    - **Property 6: Every posture code and admission reason has its own counter distinct from policy BLOCK**
    - **Validates: Requirements 6.1, 6.2, 6.3, 7.1, 7.2, 7.3, 7.4, 7.6, 14.1, 14.2**
    - For each code/reason `counter == events(code)`; `policy_block == count(blocks)`; no
      posture/admission event changes any Policy_BLOCK series; exact single-spelled names; out-of-vocab
      reason increments the vocab-error indicator, creates no merged series.
    - File: `tests/edge/test_counters_distinct_property.py`
  - [ ]* 7.3 Write unit test for out-of-vocab error indication
    - Out-of-vocab reason/class ⇒ error indicator, no merged series.
    - File: `tests/edge/test_counters_vocab.py`
    - _Requirements: 6.5, 14.3_

- [x] 8. StructuredEventLog (`edge/event_log.py`, NEW)
  - [x] 8.1 Implement the per-class rate-limited event log
    - Slotted dataclass; `_max_per_window = contract.event_log_rate()`; injected `_clock`; `_emitted`
      map class → in-window emission timestamps; one counter per class (classes ARE the
      Admission_Reason vocabulary ∪ the Posture_Codes).
    - `record(error_class, reason_or_posture)`: under the window limit ⇒ emit exactly one structured
      line; at the limit ⇒ increment the class counter and suppress the line (counter always
      increments). Line carries the error-class identifier + the Admission_Reason or Posture_Code; it
      NEVER contains raw request content or any secret.
    - An unmapped error class surfaces a coverage-gap error indication, never a silent drop.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 14.1, 14.3_
  - [ ]* 8.2 Write property test for bounded emission + redaction
    - **Property 10: Event log emission is bounded and never leaks** (supplementary)
    - **Validates: Requirements 9.2, 9.3, 9.4, 9.6**
    - `lines(class, window) ≤ max` for every class/window; `counter(class) == events(class)`
      regardless of suppression; no secret substring and no request-body byte appears in any emitted
      line. Injected clock.
    - File: `tests/edge/test_event_log_property.py`

- [x] 9. MetricsReadinessProbe (`edge/readiness.py`, NEW)
  - [x] 9.1 Implement `make_metrics_readiness(view, directory)`
    - Returns a `ReadinessProbe` (`Callable[[], tuple[bool, str | None]]`) that gates `state_ready(view)`
      FIRST (single state reason on failure), then `directory.available()` (reason
      `"metrics_directory"` on failure), ready only when both gates return ready. Extends rather than
      replaces `state_ready(view)`; evaluates both gates every call.
    - _Requirements: 10.1, 10.2, 10.4, 10.5_
  - [ ]* 9.2 Write property test for readiness composition precedence
    - **Property 11: Readiness composition gates state first** (supplementary)
    - **Validates: Requirements 10.1, 10.2, 10.4, 10.5**
    - `ready ⇔ (state_ready.ready ∧ directory.available)`; `¬state_ready.ready` ⇒ reason is the state
      reason regardless of directory; `state_ready.ready ∧ ¬directory.available` ⇒ reason names the
      metrics directory; a single reason is always returned.
    - File: `tests/edge/test_readiness_composition_property.py`
  - [ ]* 9.3 Write unit test for the readiness 2×2 matrix
    - The 2×2 readiness matrix + the 503 body naming the metrics directory.
    - File: `tests/edge/test_readiness_probe.py`
    - _Requirements: 10.3_

- [x] 10. Checkpoint - Ensure all mechanism and producer tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Publisher — merge, type, expose (`edge/publisher.py`, NEW)
  - [x] 11.1 Implement the Publisher implementing `MetricsRegistry`
    - Slotted class holding injected references to the six producers, the renderer, the
      `CardinalityDirectory`, the `WindowedHistogram`, the `ResetSafeRate`, the `RejectionCounters`,
      the `PostureCounters`, the `StructuredEventLog`, the `ResourceContract`, and the clock.
    - `exposition()` six-step merge with ONE explicit adapter per producer: (1) flat producers
      (`audit`/`state_control`/`state_metrics`) via `series()`; (2) Reading producers
      (`admit`/`stream`/`holdback`) via `snapshot()` + the histogram helper; (3) reset-safe rates;
      (4) bounded tenant families (Top-K + `other` + Overflow_Counter); (5) reason/posture/windowed-busy
      families as real zeros; (6) assert rendered family count == `SERIES_COUNT`. Each adapter is
      guarded: a raising producer is omitted + `publisher_producer_read_failures_total` increments;
      `exposition()` never raises.
    - Request-path methods that NEVER raise: `observe_tenant`, `observe_addon_latency_ms`,
      `observe_rejection`, `observe_posture`, `observe_windowed_busy`; plus `directory_available()`.
    - `SERIES_COUNT` fixed expression = Σ(six producer SERIES_COUNTs) + (K+1) + 1 (overflow) +
      `len(REASON_VOCAB)` + `len(POSTURE_CODES)` + 1 (windowed-busy).
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.9, 2.1, 2.2, 2.5, 4.1, 6.1, 7.1, 8.1, 8.2, 8.3, 11.3, 15.1, 15.2, 15.3_
  - [ ]* 11.2 Write property test for bounded + fully-typed exposition
    - **Property 2: Exposition is bounded and fully typed**
    - **Validates: Requirements 1.2, 3.3, 3.8, 15.1, 15.2, 15.3**
    - Rendered family count == `Publisher.SERIES_COUNT`; one `# TYPE` per family; `0 ≤ tenant_series ≤
      K + 1`; varying tenant count 0..well-beyond-capacity does not change the rendered family count.
    - File: `tests/edge/test_publisher_exposition_count_property.py`
  - [ ]* 11.3 Write property test for real zeros never absent
    - **Property 7: Real zeros never absent**
    - **Validates: Requirements 2.5, 3.7, 6.4, 7.5, 8.1**
    - On a freshly constructed Publisher, parsing the first exposition finds `other`, Overflow_Counter,
      every `reason` bucket, every Posture_Code counter, every histogram `_count`, and the
      windowed-busy gauge present with value exactly 0 (or the documented empty value).
    - File: `tests/edge/test_real_zeros_property.py`
  - [ ]* 11.4 Write unit tests for the publisher exposition contract
    - `exposition()` returns `str`; a representative gauge family carries `# TYPE gauge` (autoscaler
      resolves it); the windowed-busy gauge emits `# TYPE gauge` with no sample and no fabricated value;
      a raising producer is omitted with `publisher_producer_read_failures_total` incremented.
    - File: `tests/edge/test_publisher_contract.py`
    - _Requirements: 1.1, 1.3, 11.2, 11.3, 11.4_

- [x] 12. Wire the Publisher and readiness probe into the edge skin (`edge/app.py`, MODIFIED)
  - [x] 12.1 Construct and inject the Publisher and MetricsReadinessProbe in `build_app`
    - Construct the Publisher in `build_app`, pass it as the `metrics_registry`; wire
      `make_metrics_readiness(view, directory)` into the existing `readiness` seam. The `/metrics`
      `asyncio.to_thread` offload and the `/readyz` 503 response path already exist and are unchanged.
    - _Requirements: 1.8, 10.3_
  - [ ]* 12.2 Write integration test for the wired endpoints
    - `/metrics` returns a typed body computed off the serving loop via `asyncio.to_thread`; `/readyz`
      returns 503 naming the metrics directory when the directory is unavailable.
    - Files: `tests/edge/test_metrics_offload.py`
    - _Requirements: 1.8, 10.3_

- [ ] 13. Scale harness — L14d-2 offline equivalent
  - [ ]* 13.1 Write the injected-clock in-process tenant-scale harness
    - Run 2,000 and 20,000 UUID-named tenants (the latter at a raised slot capacity); assert zero
      metric-attributable HTTP 500s across all tenant requests and all `/metrics` scrapes;
      over-capacity observations counted in the Overflow_Counter, not dropped silently.
    - File: `tests/edge/test_tenant_scale_harness.py`
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

- [x] 14. Full gate + property-test sweep
  - [x] 14.1 Run the complete offline verification suite and fix any failures
    - All 11 property tests at ≥10,000 iterations; the L14d-1..5 offline/injected-clock equivalents;
      `mypy --strict` over the new `edge` modules and modified `runtime/resources.py`; `ruff` clean;
      `import-linter` BOTH contracts kept (the Publisher in `edge` reading `state_control` across the
      `root_packages` boundary must not break layers); the five AST gates (capacity-literals,
      no-module-mutable, frozen-dataclasses, http-outside-edge-resolve, tenant-scale). Confirm the full
      offline suite passes.
    - _Requirements: 1.9, 2.7, 15.4_

- [x] 15. Final checkpoint - Ensure all tests and gates pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core
  implementation tasks are never optional. Per the house discipline, every property is still shipped as
  a ≥10,000-iteration seeded test before the card is considered done.
- Each task references the specific requirement sub-clauses and (where applicable) the property number
  it implements, for traceability.
- NEW vs MODIFIED is called out in each task title/body: `runtime/resources.py`, `admit/metrics.py`,
  `runtime/stream_metrics.py`, `runtime/holdback_metrics.py`, and `edge/app.py` are MODIFIED; every
  `edge/*.py` mechanism module is NEW.
- The per-layer commit boundary is the task boundary: ResourceContract (task 1) is committed before the
  producers' `SERIES_COUNT` (task 2); the renderer (task 3) before the mechanisms (tasks 4–9); the
  mechanisms before the Publisher (task 11); the Publisher before the app wiring (task 12).
- Property tests live one-file-per-property under `tests/edge/` (and `tests/runtime/` for the
  contract-derivation property), matching the shipped-card convention (e.g.
  `tests/edge/test_cardinality_topk_property.py`).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1", "2.3", "2.5", "3.1"] },
    { "id": 2, "tasks": ["2.2", "2.4", "2.6", "3.2", "4.1", "5.1", "6.1", "7.1", "8.1", "9.1"] },
    { "id": 3, "tasks": ["4.2", "4.3", "4.4", "5.2", "5.3", "6.2", "6.3", "7.2", "7.3", "8.2", "9.2", "9.3"] },
    { "id": 4, "tasks": ["11.1"] },
    { "id": 5, "tasks": ["11.2", "11.3", "11.4", "12.1"] },
    { "id": 6, "tasks": ["12.2", "13.1", "14.1"] }
  ]
}
```
