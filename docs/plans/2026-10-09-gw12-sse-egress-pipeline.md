# GW12 — SSE egress pipeline with bounded buffers, backpressure and real cancellation

**Status:** implemented, local half CLOSED. **Card:** GW12. **Phase:** Protocol.
**Depends:** GW03 (per-worker caps) / GW11 (resolve). **Required by:** GW13 / GW14 / GW16b / GW19.
**Date:** 2026-10-09. **Spec:** `.kiro/specs/sse-egress-pipeline/`.
**Evidence:** `docs/plans/evidence/2026-10-09-gw12/`.

---

## 1. Executive summary

GW12 stands up the **serving skin** of the streaming egress path — the ASGI app, the SSE wire codec,
the dispatch/provider seam, bounded buffers with credit-based backpressure, real client-disconnect
cancellation, the C24 in-flight control triggers, the C27 stall timeouts, and mid-stream provider
error-frame scanning — **around the already-shipped bounded-holdback engine** (GW12b / R2-06). The
holdback engine itself — `egress/stream.py::StreamPipeline`, `detect/holdback.py`,
`runtime/holdback_config.py`, `runtime/holdback_metrics.py`, and the
`domain/locks.py::InFlightKill.CUT_NEXT_CHUNK` seam — is **DONE** and is **wired into, never
rebuilt**. GW12 is the layer that gives that engine a live transport, a provider to read from, a
buffer to bound, a cancellation path, and an off-loop place to scan.

The work is built **bottom-up along the import-linter `layers` contract**
(`edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts >
domain`), the same shape as the shipped R2 budget-lease and admission-control cards. Every value
type, bound, and seam built in a lower layer is wired into a caller in a later step, and the final
step boots the raw-ASGI app with the streaming chat route live. The pipeline **fails closed
everywhere** (the `FAIL_OPEN` counter is pinned at `0` and is never incremented), there is **no
capacity literal outside `runtime/resources.py`**, and **`egress` never imports `detect`** — the
scanner and the `ProviderClient` are injected from `edge` / `dispatch`, both above `detect` in the
layer order. All clocks, RNGs, scanners and provider sockets are injected, so every acceptance
target is locally reproducible under `asyncio.run` without a cloud fleet.

| # | Mechanism | Where |
|---|---|---|
| 1 | C27 timeouts + C24 durations/latencies + R6 cancellation bound derived from `target_p99_ms` (the only module with capacity literals); `snapshot()` extended | `gateway_v2/runtime/resources.py` |
| 2 | 9 stream posture codes + `STREAM_KILLED` single-spelling (codes only, no render) | `gateway_v2/domain/posture.py` |
| 3 | Bounded `Coalescer` + `CreditFlowControl` — high-water from `stream_buffer_bytes`, credit granted only on downstream consume, fail-closed below 1 byte | `gateway_v2/egress/backpressure.py` |
| 4 | `ProviderClient` protocol + `abort()` + `StubProviderClient`; `DispatchRouter` (deterministic selection); `Transform` (byte-verified round-trip) | `gateway_v2/dispatch/{provider,routing,transform}.py` |
| 5 | SSE codec / state machine — `SSEEncoder` (`[DONE]`, declared `Error_Frame`, no-content-after-error) + `SSEDecoder` (split-surrogate buffering, `malformed()` latch) | `gateway_v2/edge/wire/sse.py` |
| 6 | The single `Error_Envelope` — value-code → HTTP/SSE, `MappingProxyType`, generic fallback for unmapped codes | `gateway_v2/edge/errors.py` |
| 7 | Raw-ASGI app (no framework dep) — router, lifespan, off-loop `/metrics`, `/readyz`, live streaming chat route, registered-for-later placeholders | `gateway_v2/edge/app.py` + `edge/routes.py` |
| 8 | `FirstByteLatch` + `run_with_no_splice` (no post-first-byte splice) | `gateway_v2/edge/stream_control.py` |
| 9 | `CancellationController` (disconnect → `abort()` + `killed()` within bound, buffer/credit released, fail-closed) + `KillLatch` + `InFlightControl` (C24 triggers → the one `KillLatch`) | `gateway_v2/edge/cancel.py` |
| 10 | `StreamTimeouts` — C27 inter-chunk/idle/write bounds wrapping the serving loop's awaits, flipping the `KillLatch` | `gateway_v2/edge/stream_timeouts.py` |
| 11 | Mid-stream provider error-frame scanning — scan-before-forward, redact/withhold/fail-closed, byte-linear, offloaded | `gateway_v2/edge/error_frame_scan.py` |
| 12 | `ScanExecutor` — GIL-releasing executor sized by `ResourceContract.pool_size(SCANNER)`, off-loop scan, loop-lag as the SLO input (C38) | `gateway_v2/edge/executor.py` |
| 13 | `PerRequestExports` producer — label-free, zeros-not-absence, `fail_open_total` pinned 0 | `gateway_v2/runtime/stream_metrics.py` |

---

## 2. What was built

### 2.1 Capacity authority (`runtime/resources.py`, bottom of the build order)

The `ResourceContract` gains the GW12 derivations: the C27 timeouts (`inter_chunk_timeout_s`,
`idle_timeout_s`, `write_timeout_s`), the C24 durations/latencies (`max_stream_duration_s`,
`max_cut_latency_s`, `max_snapshot_age_s`), and the R6 `cancellation_bound_s`. Every one derives
from `target_p99_ms` through a module-level multiplier literal — this is the **only** module
permitted a capacity literal, enforced by `lint/check_capacity_literals.py`. Each method clamps to a
derived floor and raises `CapacityUnavailable` on a non-positive result (fail closed, matching the
shipped `stream_buffer_bytes`). `snapshot()` is extended to surface every new derivation alongside
`stream_buffer_bytes` (off-loop readable); `stream_buffer_bytes(active_streams)` stays the sole
high-water source.

### 2.2 Stream posture codes (`domain/posture.py`, codes-only)

Nine new value-codes — `STREAM_INTER_CHUNK_TIMEOUT`, `STREAM_IDLE_TIMEOUT`, `STREAM_WRITE_TIMEOUT`,
`STREAM_MAX_DURATION`, `STREAM_KEY_REVOKED`, `STREAM_PLAN_CHANGED`, `STREAM_SNAPSHOT_STALE`,
`STREAM_MALFORMED_UPSTREAM`, `STREAM_BUFFER_UNAVAILABLE` — plus a single-spelling `STREAM_KILLED`,
added to the one shared posture vocabulary as **codes only**. No HTTP object, no rendering here;
`edge` owns the mapping. The existing `scan_failure` / `output_blocked` codes and `MIN_RETRY_AFTER_S`
are **reused**, not redefined.

### 2.3 Bounded coalescer + credit flow control (`egress/backpressure.py`)

Frozen slotted `CreditState(granted, consumed)` (outstanding derived, never stored) and
`CoalescerVerdict(code, high_water)`. The `Coalescer`'s high-water is
`contract.stream_buffer_bytes(active_streams())` — **no literal**; `offer`/`release`/`buffered`
enforce `buffered ≤ high_water` across the stream lifetime (backpressure on reach, resume on
fall-below), and `admit()` fails closed with `STREAM_BUFFER_UNAVAILABLE` buffering zero payload bytes
when the high-water is below 1. `CreditFlowControl.grant_on_consume(N)` grants exactly N (zero when
nothing was consumed); `outstanding() = granted − consumed` never goes negative; the invariant
`granted == outstanding + consumed` holds at every observation point. Imports only `runtime` +
`domain` (both below `detect`). **Properties 1 (memory-bound) + 2 (credit conservation).**

### 2.4 Dispatch layer (`dispatch/{transform,routing,provider}.py`)

`Transform.to_provider` / `from_provider` is a pure, clock/rng/I-O-free byte-verified round-trip of a
well-formed payload (**Property 8, transform half**). `DispatchRouter.select(plan)` is a pure
function of plan inputs — identical plan → identical destination (deterministic plan-identity
selection). `ProviderClient` is a `@runtime_checkable` protocol with `open(UpstreamRequest) ->
AsyncIterator[UpstreamEvent]` and `async abort()`; `UpstreamRequest` and
`UpstreamEvent(text_deltas, final, error_frame)` are frozen slotted. `StubProviderClient(script,
rng)` is in-process (no socket), driven by a seeded `random.Random`, and `abort()` halts iteration —
this is what the local tests inject. `dispatch/__init__.py` re-exports the surface.

### 2.5 SSE codec + state machine (`edge/wire/sse.py`, the only SSE-rendering layer)

`SSEEncoder.content(frame)` emits `data: {json}\n\n`; `done()` emits `data: [DONE]\n\n`;
`error(code)` emits a declared SDK-parseable `Error_Frame` and the state machine forbids any content
frame after an error. `SSEDecoder.feed(raw)` buffers a split UTF-16 surrogate escape across chunk
boundaries and decodes the code point once the event is complete; `malformed()` is a fail-closed
latch on undecodable bytes. **Properties 8 (round-trip) + 9 (split-surrogate confluence).**

### 2.6 Error envelope + ASGI app (`edge/errors.py`, `edge/app.py` + `edge/routes.py`)

`edge/errors.py` is the single value-code → HTTP/SSE renderer: a `MappingProxyType` `_ENVELOPE`
table, a framework-free `HTTPResponse` value, and a declared generic fallback for an unmapped code
(never raw internal detail); the SSE half is single-sourced through `edge/wire`'s `error_frame_for`.
Only `edge` / `resolve` may construct an HTTP object (gated).

`edge/app.py` assembles a **raw ASGI 3.0 app** (no HTTP framework dependency): router, lifespan,
`/metrics` (exposition computed off the serving loop via `asyncio.to_thread`), `/readyz` (wired to
the `state_ready` contract — success when every state dep is available, otherwise a non-success
status that names the missing dep), and the live streaming chat route wired to the coalescer + the
injected `StreamPipeline` (scanner + `ProviderClient` injected **here**, above `detect`).
Responses / misc / mcp / rag are registered-for-later placeholders. Route wiring is split into
`edge/routes.py` to keep each module ≤ 800 lines.

### 2.7 First-byte latch (`edge/stream_control.py`)

The mutable slotted one-way `FirstByteLatch` is the single documented mutable-latch exception:
`may_retry()` is true only while unset, and `set_on_release()` flips on the first released content
byte. After it is set, an upstream failure yields a clean termination or a declared `Error_Frame` —
**never** a spliced fallback — and the gateway never concatenates two upstream responses.
`run_with_no_splice` wires the latch into the stream orchestration above the egress loop so that
retry/fallback is gated before provider hand-off. **Property 3 (no-post-first-byte-splice).**

### 2.8 Cancellation controller + in-flight control (`edge/cancel.py`)

`CancellationController.on_disconnect()` starts the clock, calls `provider.abort()` **and** sets
`killed() → True` within `contract.cancellation_bound_s()`, stops upstream reads, releases the
buffer, returns the credit, measures elapsed ms, and exports per cancelled stream. On
`elapsed > bound` it force-releases the connection + state and records a bound-exceeded cancellation;
the released byte count equals buffered-at-disconnect and **no buffered bytes are forwarded
downstream**. `KillLatch` is the one-way cut seam carrying the reason code.
`InFlightControl` / `InFlightSnapshot` / `InFlightControlSource` drive the C24 triggers — max-stream-
duration, kill switch, key revocation, plan change, and a stale/unavailable snapshot (age >
`max_snapshot_age_s()`) — all onto the **one** `KillLatch` within `max_cut_latency_s()`, each
emitting the matching declared `Error_Frame` and forwarding no further bytes after the cut, with
**fail-closed-first precedence** on the snapshot path. **Property 4 (cancellation-within-bound).**

### 2.9 C27 timeout enforcement (`edge/stream_timeouts.py`)

`StreamTimeouts` wraps the serving loop's awaits with the derived C27 bounds — inter-chunk, idle,
write — and on a breach flips the `KillLatch` (sibling cut, one terminal site) with the matching
declared `Error_Frame` and releases resources. No literal timeout lives outside
`runtime/resources.py`. **Property 10 (timeout termination).**

### 2.10 Mid-stream provider error-frame scanning (`edge/error_frame_scan.py`)

When `UpstreamEvent.error_frame is not None` the frame is scanned through the **injected** scanner
**before any byte is forwarded**: `redact` → forward with redactions via the injected `apply_decision`;
`block` → withhold + a declared `Error_Frame`; a scan that **raises** → withhold + a declared
`Error_Frame` (fail closed). The scan cost is linear in byte length and is offloaded through the
executor. `egress` still does not import `detect` — the scanner stays injected. **Properties 5
(byte-linearity) + 6 (fail-closed-on-scan-error).**

### 2.11 Serving-loop discipline (`edge/executor.py`, C38)

`ScanExecutor` is a GIL-releasing executor sized by `ResourceContract.pool_size(SCANNER)` (no
literal) that offloads the error-frame scan off the serving loop and records loop-lag as the SLO
input. The forwarding loop runs no single CPU-bound slice longer than the declared slice bound; the
`/metrics` exposition is computed off the loop. **R13.3 CPU-burst isolation test.**

### 2.12 Per-request exports (`runtime/stream_metrics.py`)

`PerRequestExports` is a producer-only, **label-free** reading alongside `HoldbackMetrics`
(zeros-not-absence, percentiles on read, publisher kept separate): frozen slotted
`PerRequestReading(detector_invocations, release_lag_ms, buffer_high_water,
active_stream_memory_bound)` plus `loop_lag_ms`, `withheld_total`, and a `fail_open_total` counter
**pinned at 0** (never incremented). The detector count matches the plan-derived schedule;
`active_stream_memory_bound = active_streams × stream_buffer_bytes`; there is **no tenant label**; an
uncomputable export is recorded as a withholding, never compensated with raw bytes. Wired into the
coalescer (memory bound on active-stream change) and the controller (release lag, cancel interval).
**Property 7 (no-FAIL_OPEN).**

---

## 3. Verification

### 3.1 Gates (all green, local scope)

| Gate | Result |
|---|---|
| `pytest tests/` (full offline suite) | **1194 passed**, 93 skipped, 1 xfailed, 0 failed |
| `pytest -k lgw12` (this card's subset) | **205 passed**, 1083 deselected |
| `mypy --strict gateway_v2` | clean, **117 source files** |
| `ruff check gateway_v2 tests` | clean |
| `lint-imports` | **2 contracts kept, 0 broken** (117 files, 211 dependencies); **no `egress → detect` edge**, layer order intact |
| `pytest tests/gates/` (AST gates) | **78 passed** |
| AST gate CLIs (`capacity_literals` / `no_module_mutable` / `frozen_dataclasses` / `http_outside_edge_resolve` / `tenant_scale`) | each exits **0** |

The 10 correctness properties are each exercised as a seeded `random.Random` loop of **≥ 10,000
iterations** (the house idiom; **no `hypothesis` dependency is added**), async paths via
`asyncio.run`, tagged `# Feature: sse-egress-pipeline, Property N` and
`# Validates: Requirements X.Y`.

A **known-benign** warning surfaces in the full-suite run: R2-09's
`coroutine 'BudgetLease._refill' was never awaited` (undrained single-flight refill coroutines in the
budget-lease property tests) — **unrelated to GW12**, carried over from the GW06 card, not a failure.

### 3.2 The 10 correctness properties

| # | Property | Where | Validates |
|---|---|---|---|
| 1 | Memory-bound invariant | `tests/egress/test_lgw12_coalescer.py` | 4.4, 5.3, 5.4 |
| 2 | Credit conservation | `tests/egress/test_lgw12_credit.py` | 5.7 |
| 3 | No-post-first-byte-splice | `tests/edge/test_lgw12_first_byte_latch.py` | 7.3, 7.4 |
| 4 | Cancellation-within-bound | `tests/edge/test_lgw12_cancellation.py` | 6.3, 6.5, 6.6 |
| 5 | Byte-linearity of mid-stream scan | `tests/edge/test_lgw12_error_frame_scan.py` | 11.5 |
| 6 | Fail-closed-on-scan-error | `tests/edge/test_lgw12_error_frame_scan.py` | 11.4, 15.2 |
| 7 | No-FAIL_OPEN | `tests/egress/test_lgw12_fail_closed.py` | 15.3 |
| 8 | SSE codec + transform round-trip | `tests/edge/test_lgw12_sse_codec.py`, `tests/dispatch/test_lgw12_transform.py` | 1.6, 8.4 |
| 9 | Split-surrogate confluence | `tests/edge/test_lgw12_sse_codec.py` | 1.4 |
| 10 | Timeout termination | `tests/runtime/test_lgw12_timeouts.py` (+ a stall case in `tests/edge/test_lgw12_cancellation.py`) | 9.2, 9.3, 9.4 |

### 3.3 Local acceptance equivalents

- **LGW12-1 (memory plateau), local equivalent — `tests/egress/test_lgw12_concurrency.py`:** N
  in-process streams over the injected harness assert
  `total buffered ≤ active_streams × stream_buffer_bytes(active_streams)` at every observation point
  and that buffered memory **does not grow with stream length** (pattern of the shipped
  `test_lgw12b_concurrency.py`). Validates Req 16.1.
- **LGW12-7 (both-SDK conformance), local equivalent — `tests/edge/test_lgw12_asgi_conformance.py`:**
  an in-process ASGI transport with recorded-frame checks for the `data: [DONE]` terminal marker, the
  `Error_Frame` shape, and split-surrogate decoding. Validates Req 16.2.

---

## 4. Open, and honestly partial

### 4.1 Deferred cloud / scale gates (out of local scope — no cloud resources)

| Item | Why it is not closed here | Local equivalent |
|---|---|---|
| **LGW12-1 full 1,000-stream memory plateau** | A **deferred cloud gate**: needs the fleet lane + a real serving gateway. | `tests/egress/test_lgw12_concurrency.py` (N in-process streams, no-growth-with-length) |
| **LGW12-7 real-TCP both-SDK conformance** (OpenAI Python + Node) | A **deferred cloud gate**: needs real TCP sockets + both SDKs. | `tests/edge/test_lgw12_asgi_conformance.py` (in-process ASGI recorded-frame conformance) |
| **200 RPS / fleet certification** (full LGW12-1/2/3 live) | A **deferred cloud gate**. | the scaled-down in-process harness |

### 4.2 GW13 follow-ups (scoped out by design)

- **The per-delta holdback scan stays INLINE** in the shipped `StreamPipeline` release loop.
  Offloading it would require re-architecting the shipped GW12b loop — **out of scope**, recorded as
  a GW13 follow-up. The **error-frame scan IS offloaded** and `/metrics` **IS off-loop**; the
  executor + sizing + loop-lag SLO input are all in place.
- **The streaming chat handler is THIN by design.** The full `/v1/chat/completions` semantics —
  request parse, plan resolution, auth, admission, the real `for_plan` output enforcement — are
  **GW15 / GW16**; GW12 proves the wiring end-to-end in **pass-through mode**. `STRICT_WITHHOLD` and
  the `INCREMENTAL` mode switch are **GW13** (`egress/strict.py` is still a stub).

### 4.3 Found, not fixed / honest notes

- **A fail-closed defect found during Property-7 work is now FIXED.** The `CancellationController`
  now fails closed on an `abort()` that **raises within the bound** (the buffer is still released,
  and the outcome is not misreported as bound-exceeded).
- **The GW05b edge-stub tripwire test (`test_the_edge_layer_is_still_stubs`) was DELETED** per its
  own instruction, now that the edge layer exists; its **14 sibling handoff tests still pass**. The
  "four uncalled pieces" it referenced are a **GW06 wiring concern**, not GW12's.
- **No real HTTP framework was added** — the app is a raw ASGI 3.0 app — and **the real provider
  socket is a stub** (`StubProviderClient`); a real upstream client is a **deferred live gate**.

---

## 5. Files

**New.** `gateway_v2/dispatch/provider.py`, `gateway_v2/dispatch/routing.py`,
`gateway_v2/dispatch/transform.py`, `gateway_v2/edge/wire/sse.py`, `gateway_v2/edge/errors.py`,
`gateway_v2/edge/routes.py`, `gateway_v2/edge/stream_control.py`, `gateway_v2/edge/cancel.py`,
`gateway_v2/edge/stream_timeouts.py`, `gateway_v2/edge/error_frame_scan.py`,
`gateway_v2/edge/executor.py`, `gateway_v2/egress/backpressure.py`,
`gateway_v2/runtime/stream_metrics.py`, and the test modules under
`gateway_v2/tests/{edge,egress,dispatch,runtime}/test_lgw12_*.py` (resources, posture, coalescer,
credit, routing, transform, provider, sse_codec, errors, app, first_byte_latch, cancellation,
inflight_control, timeouts, error_frame_scan, fail_closed, stream_metrics, serving_loop,
concurrency, asgi_conformance).

**Modified (derivations / codes / app assembly).** `gateway_v2/runtime/resources.py` (GW12
`ResourceContract` derivations + `snapshot()`), `gateway_v2/domain/posture.py` (9 stream codes +
`STREAM_KILLED`), `gateway_v2/edge/app.py` (`build_app` ASGI assembly, replacing `app = None`),
`gateway_v2/dispatch/__init__.py` (re-exports).

**Wired into (shipped GW12b / R2-06, not rebuilt).** `gateway_v2/egress/stream.py::StreamPipeline`,
`gateway_v2/detect/holdback.py`, `gateway_v2/runtime/holdback_config.py`,
`gateway_v2/runtime/holdback_metrics.py`, `gateway_v2/domain/locks.py::InFlightKill.CUT_NEXT_CHUNK`.

**Commit range (branch `suraj-revamp`).** `f3584151` (spec) → `2a0a9170` (final fix); 16 commits.
