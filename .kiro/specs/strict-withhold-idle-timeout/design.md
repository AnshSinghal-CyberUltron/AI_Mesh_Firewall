# Strict-Withhold Idle Timeout Bugfix Design

## Overview

A STRICT_WITHHOLD stream with an enforcing output rule writes nothing downstream until the whole response has been inspected (`StrictWithholdPipeline._run_enforcing` accumulates every chunk and calls `send` only from `_finalize`, `_overflow`, `_terminate` or `_cut_on_kill`). `StreamTimeouts` models downstream idle as the gap since the last completed write, and `_last_write_s` is seeded with the stream-start clock. When the withheld stream outlasts `idle_timeout_s`, the first and only content write is refused by `StreamTimeouts.send` with `STREAM_IDLE_TIMEOUT` (HTTP 408, "The downstream client went idle"). No write was ever attempted or blocked, so the client is blamed for a gap the gateway created on purpose.

The inter-chunk stall guard already exists and works: `edge/asgi_sse.py::as_chunks` awaits every upstream event through `StreamTimeouts.next_event`, bounded by `inter_chunk_timeout_s()`. A stalled upstream is therefore already cut with `stream_inter_chunk_timeout`. What is missing is only a liveness signal for the idle clock in the one mode that cannot write while it waits.

Fix: a per-stream flag on `StreamTimeouts`, `upstream_liveness`, decided once in `ChatStream._controls` from the plan (`STRICT_WITHHOLD` and a non-empty `enforcing_output_rules(plan)`). When set, each upstream event that `next_event` returns re-seeds the idle clock, exactly as the GW28 D-18 first-event re-seed already does. When unset (INCREMENTAL, pass-through strict, the default for every existing construction) the code path is not entered and behavior is byte-for-byte unchanged. The owner decision stands: no global bound changes.

## Glossary

- **Bug_Condition (C)**: A STRICT_WITHHOLD stream with an enforcing output rule whose time from stream start (or from the last completed write) to its first downstream write exceeds `idle_timeout_s`, while every upstream inter-event gap stays within `inter_chunk_timeout_s` and the client is connected and accepting bytes.
- **Property (P)**: The stream is not cut with `stream_idle_timeout`; the decided response (sanitized content or terminal `output_blocked`) is delivered.
- **Preservation**: Every behavior outside C, including INCREMENTAL idle, upstream-stall, write-timeout, kill-switch and max-duration cuts, strict frames and metrics, and all `ResourceContract` derived values.
- **Idle clock**: `StreamTimeouts._last_write_s`, the injected-clock reading that `send` compares against `idle_timeout_s()`.
- **Liveness mode**: `StreamTimeouts` constructed with `upstream_liveness=True`. Only in this mode does an upstream event refresh the idle clock.
- **Withholding strict stream**: `plan.streaming_mode is STRICT_WITHHOLD and bool(enforcing_output_rules(plan))`. The same predicate selects `_run_enforcing` in the pipeline (`StrictWithholdPipeline._enforcing_output`) and the detector chain in `edge/output_detectors.py:188`.
- **`StreamTimeouts.next_event` / `send`**: `edge/stream_timeouts.py`. `next_event` bounds the wait for one upstream event; `send` applies the idle check, then the write timeout.
- **`ChatStream._controls`**: `edge/chat_stream.py`, builds the per-stream `InFlightControl` and `StreamTimeouts`.

Derived bounds at the default 20 ms SLO (`_DEFAULT_TARGET_P99_MS = 20.0`, formula `max(p99_s * mult, p99_s)` in `runtime/resources_stream.py`, multipliers in `runtime/resources.py:54-61`):

| Bound | Mult | Value |
|---|---|---|
| `inter_chunk_timeout_s` | 50 | 1 s |
| `idle_timeout_s` | 150 | 3 s |
| `write_timeout_s` | 25 | 0.5 s |
| `max_stream_duration_s` | 15000 | 300 s |
| routed first event (`_UPSTREAM_FIRST_EVENT_MULT`) | 1500 | 30 s |

The 6 s SLOWSTREAM stream (40 chunks, 150 ms apart) exceeds the 3 s idle bound, which is why it returns 408.

## Bug Details

### Bug Condition

`StreamTimeouts.send` evaluates `now - _last_write_s > idle_timeout_s()` before the first write. In a withholding strict stream `_last_write_s` is still the stream-start value (or the post-first-event value on a routed stream, D-18), so the check measures how long the gateway withheld, not how long the client stopped reading.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type StreamScenario
         (plan, upstream_event_times[], client_accepts_writes: bool, clock)
  OUTPUT: boolean

  RETURN input.plan.streaming_mode = STRICT_WITHHOLD
         AND enforcing_output_rules(input.plan) is not empty
         AND input.client_accepts_writes = true
         AND maxGap(input.upstream_event_times) <= inter_chunk_timeout_s
         AND firstWriteTime(input) - lastWriteOrStart(input) > idle_timeout_s
END FUNCTION
```

### Examples

- Unfixed: strict org, 40 chunks at 150 ms (6 s total), client reading. Expected: 200 with sanitized content or `output_blocked`. Actual: HTTP 408 `stream_idle_timeout` before any byte, counted as a client idle fault.
- Unfixed: same stream with a 2 s total time. Expected and actual: delivered (gap under 3 s, outside C).
- Unfixed: strict org, upstream sends one chunk then goes silent for 1.5 s. Expected and actual: `stream_inter_chunk_timeout` at 1 s (outside C, must stay).
- Edge: strict stream whose upstream is healthy for 10 s and then the client stops accepting bytes after the decision. Expected: `stream_write_timeout` on the blocked write (0.5 s), or idle if writing had begun and a later gap exceeds 3 s.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- INCREMENTAL streams keep the write-based idle model: a gap since the last completed write over `idle_timeout_s` is cut with `stream_idle_timeout` before the write (3.1).
- Upstream stalls beyond `inter_chunk_timeout_s` are cut with `stream_inter_chunk_timeout` in every mode (3.2).
- A write blocked beyond `write_timeout_s` is cut with `stream_write_timeout` (3.3).
- `max_stream_duration_s`, kill switch, key revocation, plan change, stale snapshot, and first-cut-wins `KillLatch` are untouched (3.4).
- The GW28 `first_event_s` bound and its idle re-seed after the first event are untouched (3.5).
- Strict block, redact, overflow and kill emit the same frames, codes, zero-content-before-block guarantee and metrics (3.6).
- Strict plans with no enforcing output rule are pass-through and keep the write-based model (3.7).
- `idle_timeout_s`, `inter_chunk_timeout_s`, `write_timeout_s`, `max_stream_duration_s` return the same values (3.8).

**Scope:** every input outside C, including all INCREMENTAL streams, all pass-through strict streams, and any strict stream whose time to first write is within `idle_timeout_s`.

## Hypothesized Root Cause

Confirmed by reading the code (not yet by a failing test; the exploratory test below is the confirmation):

1. **Signal mismatch (primary).** `_last_write_s` is the only input to the idle check and is seeded at stream start. `_run_enforcing` makes no write until `final`, so the first `send` sees `now - start`.
2. **Misattributed cause.** The idle code is rendered as "downstream client went idle" although the idle clock never observed a client action.
3. **Ruled out: stall guard.** `as_chunks` already routes every upstream event through `next_event`; no stall protection is missing.
4. **Ruled out: pipeline.** `_run_enforcing` is correct to withhold. The fix does not change the pipeline or its frames.

## Correctness Properties

Property 1: Bug Condition - Healthy withheld strict stream is not cut as client idle

_For any_ stream scenario where the bug condition holds (isBugCondition returns true), the fixed `StreamTimeouts` SHALL NOT raise or record `stream_idle_timeout`, and the stream SHALL deliver the decided response (sanitized content, or the terminal `output_blocked` frame).

**Validates: Requirements 2.1, 2.2**

Property 2: Bug Condition - Stalled strict upstream still cut by the upstream code

_For any_ withholding strict stream where some upstream inter-event gap exceeds `inter_chunk_timeout_s`, the fixed code SHALL cut the stream within that bound with `stream_inter_chunk_timeout`, send no content, and release resources. The code SHALL NOT be `stream_idle_timeout`.

**Validates: Requirements 2.3**

Property 3: Bug Condition - Client back-pressure after the decision is still detected

_For any_ withholding strict stream whose decision write blocks beyond `write_timeout_s`, the fixed code SHALL cut with `stream_write_timeout`; and for any subsequent write whose gap since the last completed write exceeds `idle_timeout_s`, SHALL cut with `stream_idle_timeout`.

**Validates: Requirements 2.4**

Property 4: Preservation - Streams outside the bug condition behave as before

_For any_ input where the bug condition does NOT hold (INCREMENTAL, pass-through strict, strict with first write inside the idle bound, stalled upstream, blocked write, kill, max duration, routed first event), the fixed code SHALL produce the same cut code, frames, latch reason and metrics as the original, and the four derived bounds SHALL be unchanged.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**

## Fix Implementation

### Decision 1: Minimal liveness mechanism

Chosen: a keyword-only `upstream_liveness: bool = False` constructor argument on `StreamTimeouts`. When true, `next_event` calls a private `_note_upstream_activity()` after a successful await, which assigns `self._last_write_s = self._clock()`. This is the same assignment the D-18 first-event branch already performs. No new slot of state, no new timer, no new task.

Why this is sufficient: while a withholding stream accumulates, no write occurs, so the idle clock is purely "time since last activity". Refreshing it on every upstream event turns it into "time since last upstream event or completed write", which is the owner-specified liveness signal. The inter-chunk bound (1 s) is stricter than idle (3 s), so a silent upstream is always caught by `next_event` first. The idle check in `send` is not edited.

Rejected alternatives:

- **Raise global `idle_timeout_s`.** Owner rejected it. It also only moves the cliff: any answer longer than the new bound still fails, and it weakens client back-pressure detection for INCREMENTAL.
- **Skip the idle check for withholding strict streams.** Simplest, but removes idle detection after the decision when writing starts (multi-frame sanitized output could stall mid-write unnoticed except by the 0.5 s per-write bound). Re-seeding keeps the check live.
- **Emit SSE comment keepalives while withholding.** Breaks the zero-bytes-before-decision guarantee (3.6), changes the wire, and the deferred-start HTTP error path (a plain 4xx/5xx instead of a 200 plus error frame) would be lost.
- **Make `StrictWithholdPipeline` write or signal progress.** Crosses the egress/edge layer boundary (egress does not know `StreamTimeouts`) and risks changing frames and metrics (3.6).
- **Separate `_last_upstream_s` cell with `max()` in `send`.** Equivalent behavior, one more slot and a second source of truth for idle. Rejected as not minimal; the D-18 precedent already re-seeds the same cell.
- **A new `idle_timeout_strict_s` in `ResourceContract`.** A new capacity literal and a second knob for no behavior gain, and it conflicts with 2.2 and 3.8.

### Decision 2: Where the clock is re-seeded, and why the write model is unchanged

**File**: `gateway_v2/gateway_v2/edge/stream_timeouts.py`

**Function**: `StreamTimeouts.__init__`, `next_event` (and one new private method).

**Specific Changes**:
1. Add `_upstream_liveness` to `__slots__` and a keyword-only `upstream_liveness: bool = False` argument stored in `__init__`.
2. In `next_event`, after the `wait_for` returns and the existing `if first:` D-18 branch, add `if self._upstream_liveness: self._note_upstream_activity()`. The D-18 branch is left exactly as is (for a routed liveness stream both lines run; the second assignment is idempotent).
3. Update the module docstring "Modelling downstream idle" section: idle is the time since the last completed write, or, in liveness mode, since the last upstream event.
4. `send` is not modified. The re-seed happens only on the `next_event` return path, so a cut by `_cut` (timeout) never re-seeds.

**File**: `gateway_v2/gateway_v2/edge/chat_stream.py`

**Function**: `ChatStream._controls`

**Specific Changes**:
5. Change the signature to `_controls(self, binding, kill_latch, plan)` and the one call site (line 137, `plan` is already in scope). Pass `upstream_liveness=withholds_until_decision(plan)` to `StreamTimeouts`.

**File**: `gateway_v2/gateway_v2/egress/output_guard.py`

6. Add a pure function `withholds_until_decision(plan: ExecutionPlan) -> bool` returning `plan.streaming_mode is StreamingMode.STRICT_WITHHOLD and bool(enforcing_output_rules(plan))`. Egress already owns `enforcing_output_rules` and the edge layer may import egress. Using one predicate prevents drift from the pipeline selection; a drift test pins it to `StrictWithholdPipeline`'s `enforcing_output` argument as built by `select_pipeline`.

Why INCREMENTAL and pass-through strict are byte-for-byte unchanged (3.1, 3.7): for both, `upstream_liveness` is False, so `next_event` takes exactly the old statements, `_last_write_s` is assigned in exactly the old places (init, D-18 first event, completed `send`), and `send` is the unmodified function. The default value keeps every other constructor call (tests, routing boot) identical. A regression test pins the default and asserts no assignment to `_last_write_s` from `next_event` outside the D-18 branch when the flag is False.

Constraints check:
- Import-linter layers: `stream_timeouts` imports nothing new; `chat_stream` (edge) imports one more name from `egress.output_guard` (an existing downward edge, `chat_stream.py:47` already imports `enforcing_output_rules` from it). `egress` gains no edge to `edge` or `detect`.
- Fail closed: any `next_event` timeout still flips the latch and raises. The flag cannot suppress a cut; it only adds a refresh after a successful event. If the plan predicate raises, `_controls` raises before the stream starts (no stream, plain error), never a silently disabled guard.
- No module-level mutable state; one slot, per-request instance.
- Frozen/slotted: `StreamTimeouts` stays slotted; no dataclass added.
- No new capacity literal: no new number; all bounds still come from `ResourceContract` methods.

### Decision 3: Total duration is still bounded by `max_stream_duration_s`

`InFlightControl` flips the latch when `clock - start > max_stream_duration_s()`, and `as_chunks` evaluates it at stream start and after every chunk (`control.evaluate()`), which is independent of the idle clock. Re-seeding the idle clock does not touch it, so a withholding strict stream is still bounded in total time. A stream with a healthy upstream that never sends `final` is cut at the max duration with `stream_max_duration` at the next chunk boundary (within `max_cut_latency_s`, 2 x p99 = 40 ms at defaults), and a silent one by `inter_chunk_timeout_s`. A property test covers this.

Bound for a realistic long answer: `max_stream_duration_s = 15000 x 0.020 s = 300 s`. At 50 tokens/s that is about 15,000 output tokens; at 30 tokens/s about 9,000 tokens.

**Separate finding (not changed here, per instruction):** a long strict answer can still be cut at 300 s. Numbers: a reasoning model producing 20,000 tokens at 50 tokens/s takes 400 s, which exceeds 300 s and would be cut with `stream_max_duration` before any byte, because strict delivers nothing until the end. This bound is wall-clock from stream start and is the same for INCREMENTAL, where the client at least receives the bytes already sent; in strict the cut discards the entire answer. Whether to widen it for strict streams is an owner decision on `_MAX_STREAM_DURATION_MULT` (or a strict-specific multiplier in `runtime/resources.py`). Two related observations, also not changed: (a) `stream_buffer_bytes(active_streams)` caps the withheld buffer and a strict stream that overflows gets `_overflow`; the cap per stream shrinks as `active_streams` grows. (b) On non-routed streams the first upstream event is bounded by the plain `inter_chunk_timeout_s` (1 s), since `first_event_s` is only set when routing is on. A slow time-to-first-token model on an unrouted org is cut at 1 s with the stall code (existing behavior, outside this bug).

### Decision 4: Where the D-20 strict promotion lives, and who is affected

D-20 lives in the control plane publisher, `control/ai_mesh_control/core/firewall_v2_publish.py::streaming_mode_for(rules, strict_canary)`, called from `document_bytes`. It is not in `plan/compiler.py` or `edge/output_detectors.py`. The compiler is the other half: `_check_draft` refuses an INCREMENTAL plan with a byte-altering built-in exfil, ip_leak or grounding rule (D-9), and `edge/output_detectors.py:188` only builds the whole-response detector chain when STRICT and enforcing. The design record is `.kiro/specs/gw33-output-parity/design.md` D-9, D-20, D-28, D-29.

Affected orgs: any org whose published rules include an OUTPUT or BOTH scope, mode not `off`, action in `{redact, rewrite, block}` rule with `detector_class` starting `output.exfil`, `output.ip_leak` or `output.grounding`; plus `output.canary` only for orgs listed in `V2_CANARY_STRICT_ORGS` (D-26). In practice: orgs with an enforcing IP-leakage action (`out_ip_leak`), orgs enabled in `V2_EXFIL_ORGS` with `V2_EXFIL_ACTION` of redact, rewrite or block (default `block`), and orgs with an enforcing hallucination action. Customer keyword/regex rows never promote, whatever their category (D-28). Also affected, outside D-20: any org whose signed plan declares `strict_withhold` explicitly with an enforcing OUTPUT rule. Strict plans with no enforcing output rule are pass-through and are not affected (3.7). Only streaming requests are affected; non-streaming paths use no `StreamTimeouts`.

### Decision 5: Back-pressure after the decision (2.4)

After the decision, `_finalize` calls `send` for each frame, reaching `_write_events` and `timeouts.send`:
- The first write is checked against the idle clock, which was last refreshed by the final upstream event plus the scan time (bounded by the scan budget, a fraction of the p99 SLO, orders of magnitude under 3 s). It then runs under `write_timeout_s` (0.5 s). A client that does not drain is cut with `stream_write_timeout`.
- After that write completes the clock is write-based again and no more upstream events arrive, so multi-frame output stalls longer than `idle_timeout_s` between writes are cut with `stream_idle_timeout` exactly as today.

So the write bound (0.5 s) trips first for a blocked single write; idle covers the gap between writes. Neither is weakened.

## Testing Strategy

### Validation Approach

Surface the 408 on unfixed code with a deterministic injected-clock test, then verify the fix and preservation. Property tests are seeded `random.Random`, at least 100 iterations (house idiom, no hypothesis, `asyncio.run`), each tagged in source with `# Feature: strict-withhold-idle-timeout, Property N: <title>` and a `Validates:` line.

### Exploratory Bug Condition Checking

**Goal**: Reproduce the false 408 before fixing, confirm or refute root cause 1.

**Test Plan**: Drive a real `StreamTimeouts` (contract with `target_p99_ms=20`) and a `StrictWithholdPipeline` through `as_chunks` with a fake clock. The upstream yields 40 chunks, advancing the clock by 0.15 s each, then `final`. The send callable records writes. Run on UNFIXED code.

**Test Cases**:
1. **6 s healthy strict stream**: expect `StreamTimeout(STREAM_IDLE_TIMEOUT)` raised at the first write and the latch reason `stream_idle_timeout` (will fail the "no idle cut" assertion on unfixed code).
2. **Same through `ChatStream`/ASGI harness** (`tests/edge/test_lgw15_stream.py` style): response status 408 and no body bytes (will fail on unfixed code).
3. **2.9 s stream**: control, passes on unfixed code (just under 3 s).
4. **Routed strict stream** (`first_event_s` set, 4 s first event, then 6 s of chunks): fails on unfixed code, confirming D-18 does not cover the withheld period.

**Expected Counterexamples**: `now - _last_write_s` equals total elapsed time at the first `send`. If instead the 408 persists after the fix, root cause 1 is wrong and something else (for example the route's own ASGI idle handling) must be re-hypothesized.

### Fix Checking

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := run_fixed(input)
  ASSERT result.cut_code != STREAM_IDLE_TIMEOUT
  ASSERT result.frames == expected_decided_frames(input)   // sanitized or output_blocked
END FOR
```

### Preservation Checking

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT run_original(input) = run_fixed(input)    // code, frames, latch reason, metrics
END FOR
```

**Testing Approach**: Property-based, because the input space (gap sequences, modes, write timings) is large. Record the original's outputs on unfixed code first, then assert equality after the fix, using an `upstream_liveness=False` run as the oracle for non-strict inputs.

**Test Cases**:
1. **INCREMENTAL idle**: a write gap over 3 s is cut with `stream_idle_timeout` before the write (3.1).
2. **Stalled upstream, strict and incremental**: a 1.5 s gap yields `stream_inter_chunk_timeout`, no content (3.2, 2.3).
3. **Write timeout**: a `send` that blocks 0.6 s yields `stream_write_timeout` in both modes (3.3, 2.4).
4. **Kill switch / key revoke / plan change / stale snapshot** at a chunk boundary of a strict stream: same code and zero content, first-cut-wins (3.4).
5. **Max duration**: a strict stream with chunks arriving every 100 ms is cut at 300 s (injected clock) with `stream_max_duration`, even though the idle clock is refreshed throughout (3.4, Decision 3).
6. **Routed first event** (3.5), **block/redact/overflow frames and metrics** (3.6), **pass-through strict** keeps the write-based model (3.7), **derived bounds equal to the pinned values** across random `target_p99_ms` (3.8).
7. **Predicate drift**: `withholds_until_decision(plan)` equals the `enforcing_output` value `select_pipeline` gives `StrictWithholdPipeline` for randomized plans.

### Unit Tests

- `StreamTimeouts` with `upstream_liveness=True`: `next_event` refreshes the idle clock; `send` after a 6 s withheld stream succeeds; a `next_event` timeout does not refresh.
- Default and `False`: `next_event` never assigns `_last_write_s` except the D-18 branch.
- After liveness mode, a second write with a gap over `idle_timeout_s` is cut with `stream_idle_timeout` (2.4).
- `_controls` sets the flag for strict+enforcing and not for INCREMENTAL, pass-through strict, or a strict plan whose only output rules are `flag`/`mode=off`.
- `withholds_until_decision` truth table.

### Property-Based Tests

- **Property 1** (>= 100 iterations): random strict-enforcing plans, random upstream cadence with gaps `<= inter_chunk_timeout_s` and total time sampled from 0.1 s to 250 s, accepting client: never `stream_idle_timeout`, decided frames delivered.
- **Property 2** (>= 100): random sequences with exactly one gap `> inter_chunk_timeout_s`: always `stream_inter_chunk_timeout`, zero content frames, cut time `<= gap_start + inter_chunk_timeout_s`.
- **Property 3** (>= 100): random post-decision write schedules, blocked writes over `write_timeout_s` give `stream_write_timeout`; inter-write gaps over `idle_timeout_s` give `stream_idle_timeout`.
- **Property 4** (>= 100): random non-C inputs (INCREMENTAL, pass-through strict, short strict, random cut triggers), compare fixed against the unfixed-behavior oracle: identical.

### Integration Tests

- ASGI-level strict stream with a fake provider emitting for 6 s of injected time: 200, sanitized content, `stream_idle_timeout` counter and event log at zero.
- Same with a block-triggering payload: terminal `output_blocked` frame, zero content before it.
- Strict stream with upstream silent for 1.5 s: terminal `stream_inter_chunk_timeout`.
- Idle-timeout counter and event log do not count a client fault in the fixed case (1.3).

### Live Check Plan

Use `docs/plans/evidence/2026-11-04-integration/fake_openai_upstream.py` (the `SLOWSTREAM` script: 40 chunks, 150 ms apart) and the cancellation cell of `l34_python_sdk.py`, with an org promoted by D-20 (for example `V2_EXFIL_ORGS` containing the test org, default action `block`, or an enforcing `out_ip_leak` rule).
1. Reproduce on the unfixed gateway: SLOWSTREAM through the Python SDK returns 408 `stream_idle_timeout` before any byte (matches `l34-python-sdk-strict-withhold.jsonl`).
2. After the fix: the same call returns 200 with the full content after about 6 s; zero 408; idle counter unchanged.
3. SLOWSTREAM with a payload that trips the enforcing rule: terminal `output_blocked`, no content before it.
4. Stall check: a variant of the fake upstream that pauses 1.5 s mid-stream yields `stream_inter_chunk_timeout`.
5. Non-regression: an INCREMENTAL org (no enforcing exfil/ip rule) streams SLOWSTREAM unchanged; a client that stops reading during an INCREMENTAL stream is still cut with idle.
6. Record results as `docs/plans/evidence/<date>-strict-idle/*.jsonl`, appended to the existing integration format (`l34-python-sdk-strict-withhold.jsonl` is the before artifact).
Gates before merge: `mypy --strict`, ruff, import-linter, and the AST gates (capacity literals, no module-level mutable, frozen dataclasses), with `pytest tests/runtime/test_lgw12_timeouts.py tests/edge` as the targeted set. The known `check_tenant_scale` gate failure at `pinecone_transport.py` is unrelated.
