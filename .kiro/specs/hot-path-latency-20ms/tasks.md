# Implementation Plan: Hot-path latency ≤20 ms (9-stage)

## Overview

Drives the full nine-stage pipeline to ≤20 ms p50 firewall tax and establishes the measured maximum
RPS/vCPU, proven under Docker. Grounded in `docs/perf/evidence/2026-09-09-policy-engine-baseline.md`.

**Ordering law:** task 1 (harness) before any optimisation claim. Tasks 2/5/6 are independent and
carry no new dependency. Task 7 (multi-pattern engine) lands only after task 2 is measured.

## Tasks

- [ ] 1. Docker end-to-end verification harness (R7) — **blocks every claim below**
  - [x] 1.1 `scripts/perf/e2e/token_stub.py` — upstream stub emitting N tokens at a set rate; exits non-zero if it would emit zero
  - [x] 1.2 `scripts/perf/e2e/compose.perf.yml` — overlay on docker-compose.yml; six services healthy
  - [x] 1.3 `drive.py` — unique prompts, real HTTP, SSE parsing, streaming + non-streaming
  - [x] 1.4 Assert `|wall − addon − model_output| < ε`; fail the run on residual p50 ≠ 0
  - [x] 1.5 Distinguish *skipped* from *fast*: a 0 ms stage without `ran=false` fails the run
  - [ ] 1.6 `sweep.py` — rule count × prompt band × posture matrix
  - [ ] 1.7 `report.py` — p50/p90/p99, RPS, per-container CPU, commit SHA, host CPU model
  - [ ] 1.8 Record the **pre-change baseline** matrix to `docs/perf/evidence/`
    - [ ] 1.8a **PREREQUISITE**: seed policy packages — `policy_count: 0` today means Tier-1 costs ~0 and the matrix would measure nothing (finding F2)
  - _Requirements: 7.1–7.8, 10.2_

- [ ] 1B. **NEW — revisit optimisation order against measured evidence (before task 2)**
  - [ ] 1B.1 Output guard dominates BOTH modes (8.30 ms non-stream, 446.80 ms concurrent on stream); Tier-1 is 0.10 ms
  - [ ] 1B.2 Non-streaming already breaches 20 ms at p90 (20.50) / p99 (20.60)
  - [ ] 1B.3 Confirm task 2 (policy-engine threads) is still the right first lever, or re-sequence to the output guard

- [ ] 2. Change 1 — one thread per evaluation, not per regex (R2)
  - [ ] 2.1 Property test **first**: for any rule set × any text, new verdict ≡ current verdict (byte-identical)
  - [ ] 2.2 Add `_run_evaluation_with_deadline`; move the boundary from per-rule to per-evaluation
  - [ ] 2.3 Replace `_search_with_budget`'s per-call thread with a direct `.search()` under the shared deadline
  - [ ] 2.4 Monotonic deadline check **between** rules; on expiry set `truncated=True` and stop
  - [ ] 2.5 Export `policy_eval_truncated_total` and unevaluated-rule count; record in the pipeline trace
  - [ ] 2.6 ReDoS test: pathological pattern still returns within budget, still treated as no-match
  - [ ] 2.7 Re-run the task-1 matrix; assert **≥8×** at 512 ch and **≥2×** at 4,096 ch
  - _Requirements: 2.1–2.5, 3a_

- [x] 1A. **Streaming attribution — RESOLVED**
  - [x] 1A.1 Root cause: the loadtest-stub branch returns before `_track_chunk`, so `first_token_ts` stayed 0 and `model_output_ms` was never set. **Stub-path artifact, not a demonstrated production defect** (a real provider goes through `_track_chunk`)
  - [x] 1A.2 Fix: stub branch records `first_token_ts` on its first content frame
  - [x] 1A.3 Additive reconciliation is invalid on streams — `output_guardrail` is concurrent with `model_output`. Driver now reports ADDED WALL CLOCK (`wall − model_output`) separately from `guard_accum`
  - [x] 1A.4 Measured: streaming **148.89 ms p50 added**, non-streaming **15.20 ms p50** — streaming is ~10× worse and is the real target
  - _Evidence: docs/perf/evidence/2026-09-09-e2e-baseline.md §3_

- [ ] 3. Detection equivalence gate (R9) — run against tasks 2, 5, 6, 7
  - [ ] 3.1 Wire the G0.2 posture-scoring harness into CI as a before/after gate
  - [ ] 3.2 Assert per-family recall does not fall and benign FP rate does not rise
  - [ ] 3.3 Enumerate pre-existing gateway-suite failures so new breakage is distinguishable
  - _Requirements: 9.1–9.5_

- [ ] 4. Checkpoint — harness green, Change 1 measured, detection unchanged
  - [ ] 4.1 Publish the measured before/after matrix with `[M]` tags
  - [ ] 4.2 Do not proceed until 2.7 and 3.2 are both green

- [ ] 5. Change 4 — log shipping off the request path (R5)
  - [ ] 5.1 Replace the synchronous `RedisLogPublisher` with `QueueHandler` + bounded queue + `QueueListener`
  - [ ] 5.2 Level from `GATEWAY_LOG_LEVEL`; remove the unconditional `setLevel(logging.DEBUG)` at `main.py:6019`
  - [ ] 5.3 Drop-count on overflow: `gateway_log_records_dropped_total`; silent loss fails the gate
  - [ ] 5.4 Test: Redis unreachable ⇒ request handling unaffected
  - [ ] 5.5 Assert **zero** synchronous `PUBLISH` on the event loop during a served request
  - _Requirements: 5.1–5.5_

- [ ] 6. Change 5 — pipeline trace off the allow path (R6)
  - [ ] 6.1 Add `GATEWAY_EMIT_PIPELINE_TRACE`, default **off**
  - [ ] 6.2 Allow path returns `{"trace_id": ...}`; blocked path unchanged
  - [ ] 6.3 Assert terminal SSE frame **< 4 KiB** with the flag off
  - [ ] 6.4 Console "Scan Detail" still rehydrates — UI-level test
  - _Requirements: 6.1–6.6_

- [ ] 7. Change 2 — single multi-pattern scan (R2.3a) — **only after task 4**
  - [ ] 7.1 Verify the `hyperscan` wheel for the target arch; record version + ELF arch
  - [ ] 7.2 Implement the relaxed-superset transform: drop `\b`/`\B`, widen `\s`/`\d`/`\w`, compile `UTF8|UCP`
  - [ ] 7.3 Recall-violation suite: **0 violations** across the probe set; a naive ASCII port must fail this test
  - [ ] 7.4 `HS_FLAG_PREFILTER` for lookaround patterns; `re` verify on every candidate
  - [ ] 7.5 Build the database on bundle change, never per request
  - [ ] 7.6 Byte-identical verdicts vs the Python loop across the G0.1 corpus
  - [ ] 7.7 Re-run the matrix; record the new slope
  - _Requirements: 2.3a, 9.1–9.4_

- [ ] 8. Change 3 — rule budget surfaced (R3)
  - [ ] 8.1 Export `policy_rules_enabled{org}`
  - [ ] 8.2 Derive the budget from the post-change measured slope — do not assert it
  - [ ] 8.3 Control-plane warning at configuration time, naming the measured cost
  - [ ] 8.4 Confirm it warns and never blocks
  - _Requirements: 3.1–3.4_

- [ ] 9. Change 4 (requirements R4) — Tier-2 mode never silently inert
  - [ ] 9.1 Replace the three-branch ladder at `main.py:8364-8373` (all branches assign `True`) with one explicit condition
  - [ ] 9.2 Structured operator-visible signal when async is downgraded to sync
  - [ ] 9.3 Record the reason in the pipeline trace
  - [ ] 9.4 Prove blocking semantics are unchanged
  - _Requirements: 4.1–4.4_

- [ ] 10. Maximum RPS/vCPU, measured (R8)
  - [ ] 10.1 Ramp to max sustained RPS at ≤0.1% error, guards on
  - [ ] 10.2 Name the binding constraint at the ceiling
  - [ ] 10.3 Report per posture; state the Bedrock quota bound (~167 RPS) for `block`
  - [ ] 10.4 Publish RPS/vCPU with its constraint. **No 100k claim** — see R8.4
  - _Requirements: 8.1–8.5_

- [ ] 11. Final checkpoint — SLO stated properly (R1)
  - [ ] 11.1 Publish: *"≤X ms p50 / ≤Y ms p99, posture P, band B, R rules, N RPS/host"*
  - [ ] 11.2 Every figure `[M]` or `[D]`-with-arithmetic; no `[D]` presented as `[M]`
  - [ ] 11.3 Evidence file committed with commit SHA and host CPU model
  - _Requirements: 1.1–1.5_

## Notes

- **`ansh` and `main` are never modified.** All work is on `dev/perf-9stage` in the
  `/home/contact_cyberultron_com/aimesh-dev` worktree.
- Task 2.1 (property test) is written **before** 2.2 — the equivalence proof must exist before the
  change it guards.
- Task 1.8's baseline is what every later claim is measured against; without it the numbers have no
  denominator.

## Task dependency graph

```
1 (harness) ──┬── 2 (one thread) ──┬── 4 (checkpoint) ── 7 (multi-pattern) ── 8 (rule budget)
              │                    │
              ├── 5 (log shipping) ┤
              ├── 6 (trace gate)   │
              ├── 9 (tier-2 signal)┘
              └── 3 (equivalence gate, runs against 2/5/6/7)
                                                              10 (max RPS) ── 11 (publish SLO)
```
