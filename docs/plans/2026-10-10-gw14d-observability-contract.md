# GW14d — Observability contract (unified typed exposition, bounded cardinality, windowed SLO)

**Status:** implemented, local half CLOSED. **Card:** GW14d. **Phase:** Observability.
**Depends:** GW14 (producers) / GW05c (per-record state). **Required by:** GW20 / T20 / UI05.
**Date:** 2026-10-10. **Spec:** `.kiro/specs/observability-contract/`.
**Evidence:** `docs/plans/evidence/2026-10-10-gw14d/`.

Fixes **R2-10 ★ (CRITICAL)** and **R2-11 (HIGH)** of the v3 corrections register.

---

## 1. Executive summary

GW14d unifies the **six already-shipped, label-free metric PRODUCERS** in `gateway_v2` behind one
**Publisher** that implements the `MetricsRegistry.exposition()` contract the raw-ASGI `/metrics`
endpoint in `edge/app.py` already consumed but that nothing provided. The producers are **not
rebuilt** — they are read as-is. The Publisher merges their two reading shapes, types every family
with a single `# TYPE` line, renders native Prometheus histogram buckets, and owns the three
mechanisms that belong to no single producer: the bounded per-tenant **Cardinality_Directory**
(R2-10 core), the **Windowed_Histogram** that measures the C4 add-on latency the client synthetic
uses (R2-11), and the reason/posture counter families + rate-limited event log that keep each 503
cause and each fail-closed posture distinct (R2-11 M2).

**R2-10 (CRITICAL)** — metric registration ran on the request path and could raise. A 64 KiB name
directory meant ~40% of a new tenant's first requests returned HTTP 500 at ~480 UUID-named tenants,
and per-org series were silently truncated at 512/worker. The correction is a request-path operation
that NEVER raises: a size check before insert; overflow degraded to a counted, exported process-local
counter; per-tenant cardinality bounded by top-K + one `other`; the directory capacity derived from
the `ResourceContract` slot counts rather than a literal.

**R2-11 (HIGH)** — the server-side view could not certify the SLO: it under-read by 3.2–5.8 ms with
lifetime quantiles; emitted untyped exposition that blinded the GKE HPA; published negative rates on
counter reset; merged distinct 503 causes into `rejected_503`; left `plan_unavailable` uncounted;
counted FAIL_CLOSED as a policy BLOCK; and made audit loss invisible. The correction is a windowed
per-request add-on histogram carrying the C4 definition with native buckets; a held-tokens histogram;
`# TYPE` on every family; reset-safe rates (no baseline => omit, never negative); `rejected{status,
reason}`; posture counters separate from policy BLOCK; a rate-limited structured event log; and a
`/readyz` that fails when the metrics directory is missing.

The work is built **bottom-up along the import-linter `layers` contract**
(`edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts >
domain`), the same shape as the shipped GW12 / R2 cards. The Publisher lives in **`edge`**: it is the
only layer above `admit` (so the only in-contract layer that can legally read `admit.metrics`), it
already owns `/metrics` + `/readyz` and defines the `MetricsRegistry` protocol it satisfies, and it
already deployment-wires `state_control` across the `root_packages` boundary. The card fails closed
everywhere, holds **no capacity literal outside `runtime/resources.py`**, and attaches **no
tenant-derived label to any producer series** — the bounded top-K + `other` directory is the ONLY
place per-tenant visibility is restored, and it is bounded by construction.

| # | Mechanism | Where |
|---|---|---|
| 1 | `metrics_directory_slots()` / `metrics_top_k()` / `metrics_window_s()` / `event_log_rate()` — four derivations of existing contract fields, each a documented `_MULT`/`_FRACTION`, each fail-closed; the only new capacity literals | `gateway_v2/runtime/resources.py` |
| 2 | Fixed `SERIES_COUNT` on `AdmissionMetrics` (20) / `QuotaMetrics` (3) / `PerRequestExports` (11) / `HoldbackMetrics` (13) — a by-construction expression (a histogram family counts as its 3 native series) | `gateway_v2/admit/metrics.py`, `runtime/stream_metrics.py`, `runtime/holdback_metrics.py` |
| 3 | The one Prometheus renderer + inverse parser — `# TYPE` per family, native `_bucket`/`_sum`/`_count` + terminal `+Inf`, de-dup, byte-stable, `render(parse(render(x))) == render(x)` | `gateway_v2/edge/render.py` |
| 4 | `CardinalityDirectory` — the R2-10 core: `observe()` never raises, size-check-before-insert, top-K + `other` + Overflow_Counter, deterministic `(mag desc, key asc)` tie-break | `gateway_v2/edge/cardinality.py` |
| 5 | `WindowedHistogram` — rolling-window C4 add-on latency (in-window quantiles only, defined empty value) + generic windowed-busy gauge (no GPU source fabricated) | `gateway_v2/edge/windowed.py` |
| 6 | `ResetSafeRate` — no-baseline / counter-went-backwards => omit, never a negative; `ABSENT` sentinel gauges pass through | `gateway_v2/edge/rates.py` |
| 7 | `RejectionCounters` + `PostureCounters` — `rejected{status,reason}` + per-posture counters, real zeros, distinct from policy BLOCK, out-of-vocab => indicator (never a merged `rejected_503`) | `gateway_v2/edge/counters.py` |
| 8 | `StructuredEventLog` — per-class rate-limited (bound from the contract), counter increments even when a line is suppressed, carries only closed-vocab strings (no content/secret by construction) | `gateway_v2/edge/event_log.py` |
| 9 | `MetricsReadinessProbe` — `/readyz` composition gating `state_ready(view)` first, then directory availability | `gateway_v2/edge/readiness.py` |
| 10 | The `Publisher` — implements `MetricsRegistry.exposition()`; six explicit guarded per-producer adapters; per-producer failure isolation (`producer_read_failures_total`); request-path `observe_*` never raise; fixed `SERIES_COUNT` asserted == rendered count, invariant to tenant count | `gateway_v2/edge/publisher.py` |
| 11 | `build_app` wiring — `MetricsDirectorySource` protocol discriminates the Publisher from a plain registry; `/readyz` gate composed state-first then directory; `/metrics` to_thread offload + `/readyz` 503 path unchanged; backward-compatible | `gateway_v2/edge/app.py` |

---

## 2. What was built (bottom-up)

### 2.1 Capacity authority (`runtime/resources.py`, bottom of the build order)

The `ResourceContract` gains four GW14d derivations, each a pure function of existing fields through a
module-level multiplier/fraction literal — the **only** module permitted a capacity literal
(`lint/check_capacity_literals.py`): `metrics_directory_slots()` (a multiple of `connection_budget`),
`metrics_top_k()` (`floor(fraction x directory_slots)`, `<= directory_slots` by construction),
`metrics_window_s()` (`(target_p99_ms/1000) x mult`), and `event_log_rate()` (`floor(workers x mult)`).
Each fails closed with `CapacityUnavailable` below its minimum, matching every shipped contract method.

### 2.2 Producer `SERIES_COUNT` (four MODIFY tasks)

`AdmissionMetrics` (`2*5 + 3 + 1 + 5 + 1 = 20`), `QuotaMetrics` (3), `PerRequestExports`
(`5 + 2*3 = 11`) and `HoldbackMetrics` (`4 + 3*3 = 13`) each gain a fixed by-construction expression,
matching the shipped `audit` / `state_control` / `state_metrics` convention. A histogram family counts
as its three native-exposition series (`_bucket` + `_sum` + `_count`). R2-10 is why the count is a
fixed expression, not `len(series())`: a series whose existence depends on the tenant count fails the
gate instead of shipping.

### 2.3 The renderer + parser (`edge/render.py`)

The one place a `# TYPE` line is produced. `metric_type` reuses the shipped `audit/metrics.py` suffix
rule (`_total` => counter, else gauge); histograms are typed `histogram` explicitly.
`render_histogram` turns a shipped lifetime `Histogram` into cumulative-monotone `_bucket{le}` series
over explicit finite bounds plus a terminal `+Inf` equal to `_count`. `render_families` de-dups by
family base name (one `# TYPE` per family) in a deterministic sorted order. `parse` is the exact
inverse, so `render(parse(render(x))) == render(x)` byte-for-byte (Property 8).

### 2.4 The six mechanisms (`edge/`)

- **`cardinality.py` — the R2-10 core.** `observe()` guard-wraps its whole body: any internal failure
  routes to the Overflow_Counter and returns (never raises). Size-check-before-insert; existing-key
  updates in place. `top_k_series()` selects the K highest by `(magnitude desc, tenant_key asc)` so
  repeated exposition of the same input is identical; `other_value()` is the sum of the rest
  (conservation: top-K + other == all). The exported per-tenant series count is at most `K + 1`,
  invariant to the tenant count.
- **`windowed.py`.** A rolling-window integer-ms histogram, DISTINCT from the lifetime `Histogram`:
  samples are stamped with an injected clock and pruned to the window before any quantile is read, so
  p50/p99 reflect only in-window samples (never lifetime) — the fix for the 3.2–5.8 ms under-read. An
  empty window reports a defined non-negative value. The windowed-busy gauge is a thin wrapper that
  accepts externally supplied samples and always emits a `# TYPE gauge` family even with no sample —
  no GPU source is fabricated.
- **`rates.py`.** `rate_or_omit`: no baseline => adopt + omit (never zero); counter went backwards
  (reset) => adopt + omit this interval, next non-decreasing interval publishes a non-negative rate;
  otherwise a non-negative quotient. Every emitted rate is `>= 0`. `ABSENT = -1.0` sentinel gauges are
  not rates and pass through unchanged.
- **`counters.py`.** `rejected{status,reason}` with `reason` drawn solely from the `audit.record`
  Admission_Reason vocabulary; per-posture counters named with the exact single-spelled
  `domain.posture` codes; each a real zero from the first exposition; each distinct from policy BLOCK.
  An out-of-vocabulary reason/code increments a dedicated indicator rather than falling back to a
  merged series.
- **`event_log.py`.** Per-class rate-limited (bound from `event_log_rate()`): under the window limit
  emit one line, at the limit suppress the line but STILL increment the class counter, so
  `counter(class) == events(class)` regardless of suppression. `record(error_class,
  reason_or_posture)` takes no raw-content parameter, so no request byte or secret can reach a line
  by construction.
- **`readiness.py`.** `make_metrics_readiness(view, directory)` gates `state_ready(view)` FIRST (its
  reason wins when both fail), then `directory.available()` (reason `metrics_directory`); ready only
  when both are. Extends, never replaces, `state_ready`.

### 2.5 The Publisher (`edge/publisher.py`, the keystone)

Implements `MetricsRegistry.exposition()`. A six-step merge with ONE explicit, guarded adapter per
producer: the **flat** producers (`audit` / `state_control` / `state_metrics`) expose
`series() -> dict[str, float]` with any label baked into the key string; the **Reading** producers
(`admit` / `quota` / `stream` / `holdback`) expose `snapshot() -> Reading` carrying `Histogram`
objects rendered through the one histogram code path. A producer that raises mid-read is OMITTED and
`producer_read_failures_total` increments — the body is still returned (R1.3); `exposition()` never
raises. The request-path `observe_*` methods (`observe_tenant`, `observe_addon_latency_ms`,
`observe_rejection`, `observe_posture`, `observe_windowed_busy`) each swallow any internal failure and
return — a metric operation can never turn a successful request into a 500.

`SERIES_COUNT` is a fixed expression:

```
Publisher.SERIES_COUNT =
      AdmissionMetrics.SERIES_COUNT        (20)
    + QuotaMetrics.SERIES_COUNT            (3)
    + audit.metrics.SERIES_COUNT           (8+6+8+7 = 29)
    + state_control.metrics.SERIES_COUNT
    + runtime.state_metrics.SERIES_COUNT
    + PerRequestExports.SERIES_COUNT       (11)
    + holdback_metrics.SERIES_COUNT        (13)
    + contract.metrics_top_k()             (K individual tenant series, padded to exactly K)
    + 1   (other) + 1 (Overflow_Counter)
    + len(REASON_VOCAB) + 1 (vocab-error indicator)
    + len(POSTURE_CODES) + 1 (coverage-gap indicator)
    + 3   (windowed C4 add-on histogram) + 1 (windowed-busy gauge)
    + 1   (producer_read_failures_total)
```

`exposition()` asserts the rendered count equals it when every producer read succeeds; the count is
invariant to the observed tenant count because the tenant term is the fixed `K + 1` (Property 2).

### 2.6 Layer placement and the rejected alternative

The Publisher must read producers in `admit`, `audit`, `runtime` and `state_control`. `admit` is
second from the top; the only layer above it is `edge`. `state_control` sits outside
`root_packages`, so import-linter does not govern reads of it — exactly as `edge/app.py` already
assembles the gateway from parts the contract does not model. The Publisher therefore lives in
`edge`. A rejected alternative — a new top layer `gateway_v2.observe` above `edge` — would force
`edge/app.py` (which constructs the Publisher) to import a layer above it, inverting the contract.

### 2.7 The `build_app` wiring (`edge/app.py`, MODIFIED)

`metrics_registry` stays injected (a deployment builds the Publisher from the producers it already
owns and passes it in). `build_app` composes the `/readyz` metrics-directory gate on top of the
existing `readiness` seam: a new `MetricsDirectorySource` protocol discriminates the Publisher (which
exposes `directory()`) from a plain registry. State is gated first, then the directory. The `/metrics`
`asyncio.to_thread` offload and the `/readyz` 503 render path are unchanged; a plain registry falls
back to prior behaviour, so every existing caller/test is untouched.

---

## 3. Verification

| Gate | Result |
|---|---|
| GW14d new test sweep (22 new files) | **38 passed** |
| `pytest tests/edge` (existing suite intact) | **75 passed** (shipped GW12 + 17 new GW14d edge tests) |
| `mypy --strict` (full tree) | clean, **152 source files** |
| `ruff check` (gateway_v2 state_control audit_control lint tests) | clean |
| `import-linter` | **2 contracts kept, 0 broken** (Publisher in `edge` reading `state_control` does not break layers) |
| Five AST gates over `gateway_v2` | each exits 0 (capacity-literals / no-module-mutable / frozen-dataclasses / http-outside-edge-resolve / tenant-scale) |

The **11 correctness properties** each run as a seeded `random.Random` loop of >= 10,000 iterations
(house idiom; no `hypothesis`), one property <-> one file, printing a counterexample with its seed on
failure. **L14d-2** is certified locally by `tests/edge/test_tenant_scale_harness.py` (2,000 UUID
tenants at default slots + 20,000 at a raised-slot contract; zero metric-attributable failures;
over-capacity counted exactly in the Overflow_Counter).

---

## 4. Deferred cloud / scale gates (out of local scope)

- **L14d-2 full 20,000-tenant live scale** — needs a real serving gateway + the fleet lane.
  Represented locally by `tests/edge/test_tenant_scale_harness.py`.
- **L14d-4 GKE autoscaler ScalingActive** — needs a live HPA resolving the typed gauge families.
  The local half asserts the gauge families are typed `gauge`.
- **The production SLO** comes from a black-box synthetic client and provider canary (C39 retained) —
  GW14d's job is to make the server-side windowed view TRACK that client view (within 0.5 ms,
  Property 4), not to replace it.

---

## 5. Honest notes

- **`rc3-obs-v1` is a spec reference only.** The repo has no Prometheus client library; the typing,
  reset-safety and windowed machinery are reimplemented in the gateway_v2 producer/publisher idiom.
- **The windowed-busy MECHANISM is in scope; a GPU DATA SOURCE is out.** The windowed-histogram
  machinery is generic so GW20b/GW25 can feed GPU samples later. No GPU producer is fabricated; the
  gauge emits `# TYPE gauge` with no sample and no synthesized value.
- **"Metrics shared memory" is modelled as the Publisher's in-memory `CardinalityDirectory`
  availability**, not a new mmap segment — `gateway_v2` is single-process-per-worker with in-memory
  producers.
- The pre-existing, documented R2-05 `check_tenant_scale state_control` finding at
  `state_control/valkey.py:100` (the re-hydrator's deep-verify path) is unrelated to this card and
  **untouched** — it is on the `state_control` tree, not `gateway_v2`.
- One **test-harness** ordering bug surfaced during the Property 4 sweep (the generator appended
  out-of-window samples with an earlier clock, violating the deque's monotone-forward contract). It
  was fixed **in the test**; there was no product bug and **no property was weakened**.

---

## 6. Commits (per-layer, `suraj-revamp`)

```
380a146c feat(gw14d): ResourceContract metrics-directory/window/event-log derivations
ab991d38 feat(gw14d): fixed SERIES_COUNT on admit/stream/holdback producers
e0705bdb feat(gw14d): Prometheus renderer + inverse parser (edge/render.py)
5fcd9163 feat(gw14d): the six observability mechanisms (edge)
7aeb34e8 feat(gw14d): the Publisher — merge, type, expose (edge/publisher.py)
c47ced4d feat(gw14d): wire the Publisher + metrics-directory readiness into build_app
486b0b42 test(gw14d): 11 property suites (>=10k iters) + unit/integration/scale harness
```
