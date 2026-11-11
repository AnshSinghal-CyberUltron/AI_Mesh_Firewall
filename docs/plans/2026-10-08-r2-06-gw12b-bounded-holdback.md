# R2-06 / GW12b — Bounded holdback

**Status:** implemented, local half CLOSED. **Card:** GW12b. **Severity:** CRITICAL (v3-blocking).
**Date:** 2026-10-08.
**Evidence:** `docs/plans/evidence/2026-10-08-r2-06/`.

---

## 1. Executive summary

The owner-signed streaming-output holdback limit — **"no more than 3 upstream tokens held"** — is
not implemented in any measured build. The streaming output-redaction path holds back any trailing
suffix that could still grow into a sensitive match until the next upstream token arrives, with no
cap. Round 2 measured the consequence: a **UUID was held for 36 upstream tokens, a sha256 for 34, a
URL for 15**; base64 blobs were held whole; and the per-chunk rescan loop blocked **17 ms per chunk
at a 16 KB chunk size.**

The prior "worst-chunk latency" metric (C4) hid these holds **by construction** — a byte waiting on
upstream disambiguation was never attributed to gateway compute, so the C4 clock did not run while a
suffix sat held. The hold was real latency the signed metric could not see.

The fix makes the bound real, measured, and testable inside the streaming egress path. The holdback
mechanism holds the **minimal** suffix that could still become a match, **bounds how many upstream
tokens** that suffix may cost (hard cap, default 3), **bounds per-chunk rescan work to a window**
(not the stream length), publishes a **held-tokens histogram** with latency accounting split into
`T_release_processing` and `T_holdback_wait`, and carries an **explicit, documented, testable
trade-off** for the owner-signed relaxed classes that are allowed to exceed the cap. The bound
engages only when the pinned plan carries an enforcing OUTPUT rule.

This card is built **bottom-up along the import-linter layer contract** (`egress` → `detect` →
`runtime`/`domain`): the pattern logic is a pure, I/O-free, transport-free function surface in the
`detect` layer, and the thin streaming harness that drives it lives above it in `egress`. The owner
locks were already decided and are **not** re-decided here — `domain/locks.py` defines
`RELAXED_HOLDBACK_CLASSES = {uuid, sha256, url, base64}` and `InFlightKill.CUT_NEXT_CHUNK`.

| # | Mechanism | Where |
|---|---|---|
| 1 | Window bound — single definition of `Window`, trailing-slice helper, no block on short buffers | `gateway_v2/detect/windowing.py` |
| 2 | Pure holdback scanner — `TokenIndex`, `hold_start`, `classify_hold`, frozen `TRADE_OFF` table, `scan` with token-cap force-release + relaxed-ceiling overflow | `gateway_v2/detect/holdback.py` |
| 3 | Config loader — `RV_HOLDBACK_MAX_TOKENS` (default 3, range 1–100, invalid → default + warning) | `gateway_v2/runtime/holdback_config.py` |
| 4 | Metrics producers — held-tokens histogram, separate `T_release_processing` / `T_holdback_wait`, unbroken-run byte ceiling, label-free | `gateway_v2/runtime/holdback_metrics.py` |
| 5 | Output-decision glue — `enforcing_output_rules`, `OutputResolver` protocol, `apply_decision` | `gateway_v2/egress/output_guard.py` |
| 6 | Thin injectable streaming harness — `StreamPipeline`, release loop, force-release trade-off state machine, in-flight-kill `CUT_NEXT_CHUNK` | `gateway_v2/egress/stream.py` |
| 7 | Trade-off mirror — egress-side `Trade_Off_Outcome` derivation + remainder-redaction folding, pinned to the `detect` table by a drift test | `gateway_v2/egress/holdback_tradeoff.py` |

---

## 2. What was built

### 2.1 The window bound (`detect/windowing.py`, pure)

`windowing.py` holds the **single definition of `Window`**. `max_pattern_length()` returns the
longest bounded output-pattern length as a constant clamped to `[1, 65536]` (R4.1).
`window_slice(buf, window)` returns `(buf[max(0, len(buf)-window):], offset)` so a caller inspects
**at most `Window` trailing bytes** (R4.2) and, when fewer than `Window` bytes exist, returns all of
them and **does not block** waiting for more (R4.3). The module imports only `domain`, and
`holdback.scan` takes `window` as a parameter so the scanner stays a pure function of its inputs.

### 2.2 The scanner (`detect/holdback.py`, pure)

The scanner is a pure function surface — no I/O, no clock, no transport — so it is directly
property-testable with arbitrary windows and caps.

* **`TokenIndex`** defines the `Upstream_Token` precisely for the cap and the histogram: a maximal
  run of the holdback word set `_WORD = [A-Za-z0-9._%+@/=-]`. `tokens_in(s)` counts those runs (a
  UUID is **one** run, `"a b c"` is three) and `token_boundaries(s)` returns each run's start index
  so the oldest tokens can be dropped. The pathological holds (UUID 36, sha256 34, URL 15) were all
  single word-set runs growing one upstream delta at a time, which is exactly what this model
  bounds.
* **`hold_start(buf)`** returns the earliest index whose suffix could still grow into a match
  (word-class runs incl. the `>=2 '@'` email case, numeric runs bounded by the card max, a bounded
  PEM-header prefix, and a generic-secret keyword tail over a 64-char window), or `len(buf)` when no
  such suffix exists. `classify_hold(buf, hold_index)` returns the held class and whether it is in
  `RELAXED_HOLDBACK_CLASSES`.
* **`TRADE_OFF`** is a frozen per-pattern mapping and is the **single source** consulted by both the
  scanner (classification) and, via the egress mirror, the resolver (outcome).
* **`scan(buf, *, final, hold_cap_tokens, token_index, window)`** clamps the minimal `hold_start` to
  the trailing `Window`, counts the held tokens, and: for a **word-class** hold with
  `tokens_in(held) > hold_cap_tokens`, advances `hold_index` to the boundary that leaves exactly
  `hold_cap_tokens` held — **force-releasing the oldest tokens** (R1.4) and setting `forced_release`
  / `forced_release_index`; for a **relaxed** hold, permits up to `2 * hold_cap_tokens` and then
  signals overflow (R1.5, R6.5). The reference `hold_start` from the v2-validation prototype is
  adopted in spirit; the material additions turning "hold the minimal suffix forever" into R1's
  bounded hold are `classify_hold`, the token-aware force-release, and the relaxed ceiling.

### 2.3 The config (`runtime/holdback_config.py`)

`load_holdback_config(env)` follows the `runtime/resources.py::load_contract` env→dataclass+logs
pattern. It reads `RV_HOLDBACK_MAX_TOKENS`, validates into `[1, 100]` (R1.1), defaults to **3** when
unset (R1.2), and rejects a non-integer / `<1` / `>100` value back to the default 3 **with a warning
log line naming the rejected value** (R1.6). `relaxed_ceiling_tokens = 2 * hold_cap_tokens` (R1.5).
It returns `(config, logs)` exactly like `load_contract`.

### 2.4 The metrics (`runtime/holdback_metrics.py`, producer-only)

Follows the gateway_v2 **producer-vs-publisher split** (`audit/metrics.py`,
`runtime/state_metrics.py`): a fixed, **label-free**, no-tenant-label series set; GW14d owns fleet
publication and `# TYPE` exposition. `observe_stream_complete` / `observe_stream_incomplete` are O(1)
and read nothing; counters use **real zeros, never absence**.

* **Held-tokens histogram (R5.1/R5.4):** one integer sample = `stats.max_held_tokens` per completed
  stream; a word-class stream reports p50 ≤ 2, p99 ≤ 3 over ≥ 1,000 completed streams.
* **Incomplete flag (R5.2):** abnormal termination records the max held observed to that point and
  increments `incomplete_streams_total` so the sample is distinguishable from a clean completion.
* **Separate latency accounting (R5.3, R4.6, R4.7, Property 8):** `release_processing_ms` and
  `holdback_wait_ms` are **two distinct series**. `T_release_processing` is `t_sent - t_ready`
  (gateway compute, p99 < 20 ms, R4.6); `T_holdback_wait` is `t_ready - oldest_arrival` (time a byte
  waited for disambiguating upstream bytes), published **separately** (R4.7). The identity
  `T_release_lag = T_release_processing + T_holdback_wait` is recorded so Property 8 is directly
  verifiable.
* **Byte ceiling (R5.5):** `unbroken_run_bytes_max` publishes the max byte count held for the longest
  unbroken run per stream, a non-negative integer.

### 2.5 The output-decision glue (`egress/output_guard.py`)

`enforcing_output_rules(plan)` returns the OUTPUT/BOTH rules whose `mode is Mode.ENFORCE` and
`action in {REDACT, BLOCK, REWRITE}` — the `Enforcing_Output_Rule` of R2. An empty result means no
enforcement, so **no holdback**. The `OutputResolver` protocol maps completed findings to one
`Decision` via `most_restrictive`; `apply_decision(released, decision, base_offset)` applies each
`Transformation.span` (relative to `base_offset`), raises `OutputBlocked` on
`Disposition.BLOCK`, and **fails closed** if a redaction cannot cover every byte of the span. GW12b
ships a minimal in-process resolver keyed off the trade-off table; GW08's real resolver drops in
behind the same protocol without touching `stream.py`.

### 2.6 The harness (`egress/stream.py`, thin + injectable)

`StreamPipeline.run(chunks, send, killed)` is the one public entry point. Upstream chunks, the output
decision, and the kill probe are **injected**, so tests drive it with no provider, no SSE socket, and
no GW13 coalescer. It:

* resolves and records whether an `Enforcing_Output_Rule` is present **once, before the first chunk**
  (R2.3); raises `EnforcementUndetermined` releasing **no bytes** when the plan is unresolved (R2.4);
  passes each chunk through unheld within 10 ms when no rule enforces output (R2.2);
* otherwise drives each text stream through `scan` + `window_slice`, drops released bytes from
  `pending` so the buffer is bounded by `Window + one chunk` regardless of total released bytes
  (the R4.4 "per-chunk work does not grow with stream length" bound), carries **one char of
  left-context** so a match straddling the release boundary is preserved (chunk-split invariance,
  Property 5), applies the `Decision`, and flushes all held bytes on the final chunk (R3.5);
* records `t_ready` / `t_sent` / `oldest_arrival` with the injected clock for the separated latency
  decomposition, tracks `max_held_tokens` and `max_unbroken_run_bytes`, and calls
  `HoldbackMetrics.observe_stream_complete` / `_incomplete`.

**In-flight kill (R7).** `run` checks `killed()` at each chunk boundary. On a kill it cuts per
`InFlightKill.CUT_NEXT_CHUNK` — ceasing emission no later than the next boundary (R7.1), emitting
zero further chunks (R7.2), discarding held bytes that have not passed the `Decision` (R7.3),
terminating within the loop iteration with a `killed` error frame (R7.4); a kill observed only after
the final chunk already released completes the stream normally and retracts nothing (R7.5).

**Force-release trade-off state machine (R6).** When a match completes against bytes the cap already
force-released, the pattern's declared `Trade_Off_Outcome` applies: **redact-the-remainder** (replace
every still-held remainder byte before releasing any further byte, R6.3) or **terminate-the-stream**
(`Terminal_Error_Frame` code `forced_release_tradeoff`, R6.4). Every error path is **fail-closed**:
`holdback_overflow` on the relaxed ceiling releasing no in-progress bytes (R6.5); `undefined_tradeoff`
for a table-less over-cap pattern (R6.6); `scan_failure` retaining all pending (R3.6); `output_blocked`
withholding unreleased bytes (R3.4). No content frame is ever emitted after a terminal frame.

### 2.7 The trade-off mirror (`egress/holdback_tradeoff.py`)

The `egress` layer sits **below** `detect` is forbidden to import it (import-linter), yet the
authoritative `Trade_Off_Outcome` table lives in `detect.holdback.TRADE_OFF`. This module is the
single egress-side mirror: `derive_trade_off_outcome(decision)` maps a resolver `Decision` to the
outcome (BLOCK → terminate, REDACT → redact-the-remainder, anything else → `None` → fail closed), and
`with_remainder_redactions` folds a force-released match's still-held remainder into the output
`Decision` as a redaction. A test in `tests/egress/test_lgw12b_stream.py` imports
`detect.holdback.TRADE_OFF` and **pins** that the egress-derived outcome agrees with it for every
class — so the two cannot drift.

### Per-pattern trade-off table (R6.1 — exactly one outcome per pattern)

| Pattern class | Hold category | Can exceed cap? | Declared `Trade_Off_Outcome` |
|---|---|---|---|
| AWS access/secret key | word-class | no (cap-bounded) | redact-the-remainder |
| API token (generic bearer / `api_key`) | word-class | no (cap-bounded) | redact-the-remainder |
| Email address | word-class | no (cap-bounded) | redact-the-remainder |
| JWT | word-class | no (cap-bounded) | terminate-the-stream |
| Payment card number | numeric-class | no (cap-bounded) | terminate-the-stream |
| `uuid` | relaxed | yes (to ceiling) | redact-the-remainder |
| `sha256` | relaxed | yes (to ceiling) | redact-the-remainder |
| `url` | relaxed | yes (to ceiling) | redact-the-remainder |
| `base64` | relaxed | yes (to ceiling) | redact-the-remainder |

Every row has exactly one outcome; none has zero or two (R6.1). JWT and card are
`terminate-the-stream` because a partially-released JWT or card prefix is itself disclosive; the rest
redact the remainder. The relaxed classes carry the documented trade-off because they are the
owner-signed exceptions to the cap (`RELAXED_HOLDBACK_CLASSES`).

### Layer note

`detect.windowing` and `detect.holdback` import only `domain`. `runtime.holdback_config` and
`runtime.holdback_metrics` import only `domain`. `egress.*` imports `detect` + `domain` + `runtime`
and **never** the reverse — the scanner is injected into the harness and the trade-off table is
mirrored in egress with a drift-pinning test, precisely because the `egress → detect` edge is
forbidden. The import-linter contract (`Gateway v2 layers`, `Resolve imports only domain`) verifies
this.

---

## 3. Verification

### 3.1 Gates (all green, local scope)

| Gate | Result |
|---|---|
| `pytest` (offline, full suite) | **955 passed**, 93 skipped, 1 xfailed |
| `pytest -k lgw12b` (this card's subset) | **141 passed**, 908 deselected |
| `mypy --strict` | clean, 102 source files |
| `ruff` | clean |
| `import-linter` | **2 contracts kept, 0 broken** (`Gateway v2 layers`, `Resolve imports only domain`) |
| size / mutable-default AST gates | clean |

The 8 correctness properties are each exercised as a seeded `random.Random` loop of **≥ 10,000
iterations** (the house idiom from `tests/domain/test_lgw04.py`; **no `hypothesis` dependency is
added**), tagged `# Feature: bounded-holdback, Property N`.

### 3.2 L12b-1 — replay benchmark

8 content classes (prose, URL, UUID, sha256, paths, JSON, code, base64) × 3 chunk sizes
(2 KB / 8 KB / 16 KB) = **24/24 combinations passed**. Measured: **max observed hold ≤ 1–2 upstream
tokens** for every word-class class (bound is 3); **inspected bytes = 64 = Window** independent of
released length; **per-chunk holdback loop p99 ≈ 14–37 µs**, three orders of magnitude under the
0.5 ms per-chunk bound (and far under the original 17 ms/chunk at 16 KB). Local resources only.

### 3.3 L12b-3 — detector regression

Real AWS keys, API tokens, JWTs, emails, and payment card numbers split across chunk boundaries at
≥ 3 offsets each (first byte, a middle byte, last byte) across **5 pattern classes**: the raw
sensitive value **never appeared in `Released_Bytes`** at any offset, and each pattern produced a
**byte-identical `Trade_Off_Outcome`** across every split and equal to single-chunk delivery. **8
tests**, runs well under the 300 s local ceiling, no cloud/fleet/multi-zone infrastructure.

### 3.4 L12b-2 — local concurrency/isolation equivalent

A heavy base64-16KB / UUID-heavy stream run concurrently with exactly one co-running stream on the
local host: the co-running stream's **median per-chunk latency delta ≈ 0 ms**, well inside the
0.5 ms threshold. This is the local, scaled-down equivalent of the deferred fleet run and is an
O(window) isolation proof — per-chunk work is bounded by the window and does not couple across
streams. Local-host only, no network.

---

## 4. Open, and honestly partial

| Item | Why it is not closed here |
|---|---|
| **L12b-2 — the full 200 RPS fleet capacity run** | A **deferred cloud gate** (R10.5). Needs the fleet lane and a serving gateway; `gateway_v2/edge/app.py` is a stub (GW12). Represented locally only by the §3.4 scaled-down concurrency/isolation equivalent. |
| **GW20b — fleet certification** | A **deferred cloud gate** (R10.5), out of local scope. No cloud resources are reachable. |

**Found, not fixed.** The placeholder `detect.holdback` scanner is **non-confluent** for some
delimiter-containing classes (`url`, `uuid`, `card`, `jwt`, `api_token`): it can release a
`:` / `/` / `.` / `-`-delimited **prefix** before a match completes, so the exact redaction
byte-boundary is split-dependent for those classes. The **safety invariant holds unconditionally** —
no raw sensitive value ever reaches `Released_Bytes` (Property 1 passes across every split) — only
the byte boundary of the redaction placeholder is split-dependent, and that is tracked for the real
GW07/GW08 detector rather than the GW12b placeholder.

Also deliberately partial and recorded, not hidden:

* `classify_hold` is a **conservative placeholder classifier**, not the real GW07/GW08 detector. It
  over-holds rather than under-holds, so it is safe by construction; GW08's real resolver drops in
  behind the `OutputResolver` protocol.
* The egress harness (`StreamPipeline`) is **thin on purpose**: GW13 wires it to the real SSE
  transport, backpressure, and the coalescer. `egress/backpressure.py` and `egress/strict.py` are
  left as stubs; GW12b does not require them.
* Because the `egress → detect` layer edge is forbidden, the scanner is **injected** into the harness
  and the trade-off table is **mirrored** in `egress/holdback_tradeoff.py`, with a drift-pinning test
  asserting the mirror agrees with `detect.holdback.TRADE_OFF` for every class.

---

## 5. Files

**New.** `gateway_v2/detect/holdback.py`, `gateway_v2/runtime/holdback_config.py`,
`gateway_v2/runtime/holdback_metrics.py`, `gateway_v2/egress/holdback_tradeoff.py`, and eight test
modules under `gateway_v2/tests/{detect,egress,runtime}/test_lgw12b_*.py` (holdback, windowing,
config, metrics, output_guard, stream, replay_benchmark, detector_regression, concurrency).

**Modified (were stubs, now filled).** `gateway_v2/detect/windowing.py`,
`gateway_v2/egress/output_guard.py`, `gateway_v2/egress/stream.py`.
