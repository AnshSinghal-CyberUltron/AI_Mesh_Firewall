# Implementation Plan

Paths are relative to `gateway_v2/gateway_v2/` for source and `gateway_v2/tests/` for tests unless noted. Work in the worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/bugfix-f1-strict-idle` on branch `bugfix-f1-strict-idle`. Do not push.

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Healthy withheld strict stream is cut as client idle
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the false 408 exists
  - **Scoped PBT Approach**: Deterministic bug, so scope to the concrete failing case: 40 chunks, 0.15 s apart on an injected clock (6 s total), then `final`
  - Isolation: a real `StreamTimeouts` (contract `target_p99_ms=20`, so `idle_timeout_s` = 3 s, `inter_chunk_timeout_s` = 1 s) plus `StrictWithholdPipeline` with an enforcing output rule, driven through `as_chunks` with a fake clock; `send` records writes
  - isBugCondition: STRICT_WITHHOLD AND enforcing output rule AND client accepts writes AND maxGap <= `inter_chunk_timeout_s` AND first write - last write/start > `idle_timeout_s`
  - Assert expected behavior: no `StreamTimeout(STREAM_IDLE_TIMEOUT)`, latch reason is not `stream_idle_timeout`, decided frames delivered
  - Also cover: the same stream through the ASGI harness in `tests/edge/test_lgw15_stream.py` style (expect 200 with body, unfixed gives 408 and no bytes); a routed variant (`first_event_s` set, 4 s first event then 6 s of chunks) to show D-18 does not cover the withheld period
  - Include the 2.9 s control case (passes on unfixed code)
  - Run on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document counterexamples (expected: `now - _last_write_s` equals total elapsed time at the first `send`); if the 408 has another cause, stop and re-hypothesize
  - Mark task complete when the test is written, run, and the failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Streams outside the bug condition behave as before
  - **IMPORTANT**: Follow observation-first methodology
  - Observe on UNFIXED code and record: cut code, frames, latch reason and metrics for non-C inputs
  - Observe: INCREMENTAL write gap > `idle_timeout_s` is cut with `stream_idle_timeout` before the write (3.1)
  - Observe: 1.5 s upstream gap in strict and INCREMENTAL yields `stream_inter_chunk_timeout`, no content (3.2)
  - Observe: `send` blocking 0.6 s yields `stream_write_timeout` in both modes (3.3)
  - Observe: kill switch, key revoke, plan change, stale snapshot, and `max_stream_duration_s` (chunks every 100 ms to 300 s) cut with their existing codes, first-cut-wins (3.4)
  - Observe: routed `first_event_s` bound and idle re-seed after the first event (3.5)
  - Observe: strict block, redact, overflow and kill frames, codes, zero-content-before-block and metrics (3.6)
  - Observe: pass-through strict (no enforcing output rule) keeps the write-based model (3.7)
  - Observe: the four derived bounds return the pinned values across random `target_p99_ms` (3.8)
  - Write property-based tests (seeded `random.Random`, >= 100 iterations, `asyncio.run`) asserting those observed outputs over the non-C input domain, using a recorded oracle (golden values captured from unfixed code) so the same file can be re-run after the fix
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [ ] 3. Fix for false `stream_idle_timeout` on withheld strict streams

  - [x] 3.1 Add `withholds_until_decision(plan)` in `egress/output_guard.py`
    - Pure function: `plan.streaming_mode is StreamingMode.STRICT_WITHHOLD and bool(enforcing_output_rules(plan))`
    - Reuse the existing `enforcing_output_rules`; no new import edge (egress gains no edge to `edge` or `detect`)
    - Truth-table test: INCREMENTAL; strict with enforcing rule; strict with no output rules; strict whose only output rules are `flag` or `mode=off`; strict with INPUT-scope-only rules
    - Drift test: for randomized plans (>= 100), `withholds_until_decision(plan)` equals the `enforcing_output` value `select_pipeline` hands to `StrictWithholdPipeline`
    - _Bug_Condition: isBugCondition(input) where plan is STRICT_WITHHOLD with a non-empty enforcing_output_rules(plan)_
    - _Preservation: INCREMENTAL and pass-through strict return False (3.1, 3.7)_
    - _Requirements: 2.2, 3.1, 3.7_

  - [x] 3.2 Add `upstream_liveness` to `StreamTimeouts` in `edge/stream_timeouts.py`
    - Add `_upstream_liveness` to `__slots__`; keyword-only `upstream_liveness: bool = False` argument stored in `__init__`
    - Add private `_note_upstream_activity()` that assigns `self._last_write_s = self._clock()` (same assignment the D-18 first-event branch performs)
    - In `next_event`, after the `wait_for` returns and after the existing `if first:` branch, add `if self._upstream_liveness: self._note_upstream_activity()`; leave the D-18 branch exactly as is
    - Do NOT edit `send`; a timeout cut in `next_event` must never re-seed (fail closed)
    - No new capacity literal; all bounds still from `ResourceContract`
    - Unit tests: flag True refreshes the idle clock per event; `send` after a 6 s withheld stream succeeds; a `next_event` timeout does not refresh; default and False never assign `_last_write_s` from `next_event` outside the D-18 branch; after liveness mode a second write with a gap > `idle_timeout_s` is cut with `stream_idle_timeout`; 1.5 s silence still gives `stream_inter_chunk_timeout`
    - _Bug_Condition: isBugCondition(input) from design_
    - _Expected_Behavior: expectedBehavior(result) from design (no stream_idle_timeout; decided response delivered)_
    - _Preservation: default False is byte-for-byte the old path (3.1, 3.2, 3.3, 3.5, 3.8)_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.5, 3.8_

  - [x] 3.3 Wire `ChatStream._controls(binding, kill_latch, plan)` in `edge/chat_stream.py`
    - Change the signature to take `plan` and update the single call site (around line 137, `plan` already in scope)
    - Pass `upstream_liveness=withholds_until_decision(plan)` to `StreamTimeouts`
    - Import `withholds_until_decision` next to the existing `enforcing_output_rules` import from `egress.output_guard`
    - If the predicate raises, `_controls` raises before the stream starts (no silently disabled guard)
    - Tests: flag set for strict+enforcing; not set for INCREMENTAL, pass-through strict, or strict with only `flag`/`mode=off` output rules; ASGI-level 6 s injected-time strict stream returns 200 with sanitized content and the `stream_idle_timeout` counter and event log stay at zero (1.3); block payload gives terminal `output_blocked` with zero content before it; 1.5 s upstream silence gives terminal `stream_inter_chunk_timeout`
    - _Bug_Condition: isBugCondition(input) from design_
    - _Expected_Behavior: expectedBehavior(result) from design_
    - _Preservation: Preservation Requirements from design (3.4, 3.6, 3.7)_
    - _Requirements: 2.1, 2.2, 2.3, 3.4, 3.6, 3.7_

  - [x] 3.4 Write the four property tests (>= 100 iterations each)
    - House idiom: seeded `random.Random`, no hypothesis, `asyncio.run`; tag each in source with `# Feature: strict-withhold-idle-timeout, Property N: <title>` and a `Validates:` line
    - Property 1: random strict-enforcing plans, upstream gaps <= `inter_chunk_timeout_s`, total time 0.1 s to 250 s, accepting client: never `stream_idle_timeout`, decided frames delivered. Validates 2.1, 2.2
    - Property 2: exactly one gap > `inter_chunk_timeout_s`: always `stream_inter_chunk_timeout`, zero content frames, cut time <= gap start + `inter_chunk_timeout_s`, resources released. Validates 2.3
    - Property 3: random post-decision write schedules: a blocked write over `write_timeout_s` gives `stream_write_timeout`; an inter-write gap over `idle_timeout_s` gives `stream_idle_timeout`. Validates 2.4
    - Property 4: random non-C inputs (INCREMENTAL, pass-through strict, short strict, random cut triggers, max duration with idle clock refreshed every 100 ms): identical to the recorded unfixed-behavior oracle from task 2. Validates 3.1-3.8
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [x] 3.5 Update the `edge/stream_timeouts.py` module docstring
    - "Modelling downstream idle": idle is the time since the last completed write, or, in liveness mode, since the last upstream event; state why (strict withholds by design, so write-based idle blames the client for gateway-created silence)
    - Document that upstream stall remains bounded by `inter_chunk_timeout_s` and total time by `max_stream_duration_s`
    - _Requirements: 2.2, 2.3_

  - [x] 3.6 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Healthy withheld strict stream is not cut as client idle
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2_

  - [x] 3.7 Verify preservation tests still pass
    - **Property 2: Preservation** - Streams outside the bug condition behave as before
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 4. Run the offline gate
  - `pytest tests/runtime/test_lgw12_timeouts.py tests/edge` first, then the full gateway_v2 suite
  - `mypy --strict`, `ruff`, `import-linter` (both contracts kept, no new edge)
  - AST gates: capacity literals, no module-level mutable, frozen dataclasses (each CLI exits 0)
  - Only known failure allowed: the `check_tenant_scale` finding at `pinecone_transport.py`; anything else is a regression to fix
  - _Requirements: 2.2, 3.4, 3.6, 3.8_

- [x] 5. Live check against the real stack
  - Use `docs/plans/evidence/2026-11-04-integration/fake_openai_upstream.py` (SLOWSTREAM: 40 chunks, 150 ms apart) and the `l34_python_sdk.py` strict-withhold cell, with a D-20-promoted org (for example in `V2_EXFIL_ORGS`, default action `block`, or an enforcing `out_ip_leak` rule)
  - Before (unfixed gateway): SLOWSTREAM returns 408 `stream_idle_timeout` before any byte (matches `l34-python-sdk-strict-withhold.jsonl`)
  - After: 200 with the full content after about 6 s, zero 408, idle counter unchanged
  - After, tripping payload: terminal `output_blocked`, no content before it
  - Stall variant (upstream pauses 1.5 s mid-stream): still cut with `stream_inter_chunk_timeout`
  - INCREMENTAL org: SLOWSTREAM unchanged; a client that stops reading is still cut with idle
  - Record results as `docs/plans/evidence/<date>-strict-idle/*.jsonl` in the existing integration format
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.3, 3.1, 3.2, 3.6_

- [x] 6. Add the repo-required changelog pointer lines
  - Add the one-line pointer in `AGENTS.md`, the Cursor rule `.cursor/rules/pipeline-changelog.mdc`, and the canonical `docs/pipeline/CHANGELOG.md`, plus the Ruflo memory store (`memory store --namespace pipeline/changes`), all in the SAME commit as the fix, following the existing PIPELINE-00NN format (date, root cause, fix, tests, VERIFY, evidence path)
  - Use the next free pipeline id after checking the current newest entry
  - _Requirements: 2.1, 2.2, 3.4_

- [x] 7. Commit on branch `bugfix-f1-strict-idle` (do not push)
  - Confirm the branch with `git branch --show-current`; stage specific files by name (source, tests, spec, evidence, changelog files); no `git add .`
  - One commit with a message naming the fix and the verification results; do not push, do not amend
  - _Requirements: 2.1, 2.2_

- [x] 8. Write the separate `max_stream_duration_s` finding as an owner-decision note
  - Write `docs/plans/evidence/<date>-strict-idle/FINDING-max-stream-duration.md` (documentation only)
  - Content: `max_stream_duration_s` = 15000 x 0.020 s = 300 s at the default SLO; a 20,000-token strict answer at 50 tokens/s takes 400 s and is cut with `stream_max_duration` before any byte, discarding the whole answer; the bound is the same for INCREMENTAL, where already-sent bytes survive; capacity: about 15,000 tokens at 50 tok/s, 9,000 at 30 tok/s
  - List the options for the owner (widen `_MAX_STREAM_DURATION_MULT`, a strict-specific multiplier in `runtime/resources.py`, leave as is) without choosing one; also note the `stream_buffer_bytes(active_streams)` overflow shrink and the 1 s unrouted first-event bound as related, unchanged observations
  - **Do NOT change the bound** or any `ResourceContract` value (3.8)
  - Add the file to the commit from task 7 if not yet committed, otherwise a small follow-up commit; still no push
  - _Requirements: 3.4, 3.8_
