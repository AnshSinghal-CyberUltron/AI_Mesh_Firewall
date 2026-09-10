# Implementation Plan: Hot-path latency ≤20 ms (9-stage)

## Overview

Drives the full nine-stage pipeline to ≤20 ms p50 firewall tax and establishes the measured maximum
RPS/vCPU, proven under Docker. Grounded in `docs/perf/evidence/2026-09-09-policy-engine-baseline.md`.

**Ordering law:** task 1 (harness) before any optimisation claim. Tasks 2/5/6 are independent and
carry no new dependency. Task 7 (multi-pattern engine) lands only after task 2 is measured.

## Tasks

- [x] 1. Docker end-to-end verification harness (R7) — built; five bring-up blockers cleared;
      produced the project's first real end-to-end nine-stage measurement
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

- [x] 1B. **Optimisation order re-checked — task 2 is NOT the first lever**
  - [x] 1B.1 Output guard dominates both modes: 8.30 ms non-stream, 454.6 ms concurrent on a 200-token stream. `input_scan` is 0.10 ms, `policy` 0.20 ms
  - [x] 1B.2 Non-streaming breaches 20 ms at p90 (20.50) / p99 (20.60)
  - [x] 1B.3 **Guard cost is super-linear, a ≈ 1.9** (100/200/300 tok ⇒ 112.5/454.6/793.2 ms). Mechanism: `⌈n/64⌉` flushes (`max_buffer_chunks=64`, `:285`) × `inspect(full_text)` over the **cumulative** buffer (`:329`)
  - [x] 1B.4 Added latency looks flat (~150 ms) only because guard work overlaps generation; the curves cross at **≈1,200 tokens**. The CPU cost is never hidden and caps RPS
  - _Evidence: docs/perf/evidence/2026-09-09-output-guard-superlinear.md_

- [x] 1C. **Flush trigger fixed — 31.9× less guard CPU**
  - [x] 1C.1 Diagnosis corrected: the scan window was already bounded (~525 chars). The bug was flush COUNT — a 512-byte lookahead is 75–88 token-sized chunks, exceeding `max_buffer_chunks=64`, so the queue never dropped below the limit and every chunk flushed
  - [x] 1C.2 Fix counts chunks **since the last flush**, so it changes only when a flush fires, never what is scanned. Lookahead / secret anchor / open-media holdback preserved
  - [x] 1C.3 Test-first: `test_stream_flush_per_token.py` (3 tests) — 152 vs 1 guard passes for identical bytes
  - [x] 1C.4 Re-measured: 112.5→6.70, 454.6→18.10, 793.2→24.90 ms; curve now linear
  - [x] 1C.5 **Throughput fix, not latency fix** — added wall clock only 150→~120-145 ms because guard work was already concurrent with generation
  - _Evidence: docs/perf/evidence/2026-09-09-1C-flush-fix-result.md_

- [x] 1D. **Located it — and it was not where the metric pointed.** The added time is at
      the HEAD (1393–3098 ms to first token), not the ~140 ms tail. Cause: an
      unconditional 512-byte retention composed with a 64-CHUNK flush trigger.
      _Evidence: docs/perf/evidence/2026-09-09-1D-streaming-head-latency.md_
- [x] 1E. **Fixed it** — content-derived retention + byte-denominated flush trigger.
      First token 3.2–4.0× sooner; 748 + 2244 documents byte-identical.
      _Spec: .kiro/specs/stream-first-token-latency/ · Evidence: …-1E-stream-first-token-fix.md_
- [x] 1F. **Per-guard-pass fixed cost** — canonicalisation was 67% of a scan and called
      `unicodedata.category` twice per character. ASCII identity fast path (generated,
      not hand-written) + 3×→1× cache. 1.4–1.7×, output byte-identical over every code
      point <0x2000. _Spec: .kiro/specs/canonicalisation-hot-path/_
  - [ ] 1D.1 It is NOT the output guard (now 24.9 ms concurrent), NOT Tier-1 (0.10 ms), NOT the policy engine (0.20 ms)
  - [ ] 1D.2 Non-streaming is 15.2 ms p50 while streaming adds ~140 ms — the gap is stream machinery, not scanning
  - [ ] 1D.3 Instrument the stream path end to end and attribute the gap before optimising anything

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


## Re-prioritised from measurement (2026-09-09)

The original order assumed the policy engine was the hot path. Measurement moved it:

| path | measured now | target | gap |
|---|---:|---:|---|
| non-streaming p50 | **15.2 ms** | <20 ms | **meets it** (stub upstream, 45 rules) |
| streaming first token | **358–962 ms** | <20 ms | ~20–50× |
| policy engine @45 rules | 0.20 ms | — | not the bottleneck at this rule count |
| policy engine @264 rules | 17.06 ms | — | becomes dominant only at scale |

So the ordering that follows the evidence is:

1. **Task 10 (max RPS/vCPU, measured)** — the only unmeasured half of the promise, and
   the one place where a claim is currently unsupported rather than merely short.
2. **Task 2 (thread-per-regex)** — `_run_with_timeout` starts a thread then joins it, so
   it is serial: 0.0586 ms of the 0.065 ms/rule slope is pure thread overhead. Biggest
   single win at high rule counts, and it is the prerequisite framing for task 7.
3. **Tasks 5/6 (log shipping, pipeline trace off the allow path)** — fixed per-request
   overhead that inflates every number above, streaming and non-streaming alike.
4. **Task 7 (multi-pattern engine)** — only after task 2, and only if measurement still
   shows regex time dominating; the 1F profile put `re.search` at 0.042 s of 0.482 s.


## Where this actually landed (2026-09-09)

The plan's ordering assumed stage work was the lever. Measurement said otherwise, twice
over: first that the tail was not in any single stage, then that **the gateway burned ~56 ms
of CPU per request while tracing 8.2 ms of it** — and that the missing work was
`redact_all`, called to build a pipeline_trace, *after* every stage timer had closed.

| | at session start | now |
|---|---:|---:|
| firewall tax p50 | 8.2 ms | **7.5 ms** |
| p90 | 51.6–56.8 ms | **10.0–11.2 ms** |
| p99 | 103–158 ms | **26.4–40.8 ms** |
| CPU per request | ~56 ms | **~27.3 ms** |
| RPS at conc 16 | 26.6–27.1 | **29.7** |

Non-streaming, 45 policies, block posture, Tier-2 off, 50-token answers, gateway 4 vCPU.

**Still short of the goal**: p99 32.60 ms against a 20 ms bound — a 1.6× gap, from 7.5×.

### What the plan should absorb

- **G4.1 is dead code** — do not implement (`…-G4.1-deobfuscation-cache-targets-dead-code.md`).
  Second Gate-4/Gate-0 item found aiming at code the policy-driven-detection work removed;
  the rest of Gate 4 needs a reachability check before scheduling.
- **G4.5 says ~17 synchronous PUBLISH per request. It is 7.0**, and `telemetry_ms` measures
  **0.00** in this stack — telemetry was never on the critical path here.
- **~426 RPS/vCPU is not achievable and was derived wrongly**: it divided a vCPU by the
  *traced* per-request cost, which measurement showed was 15% of the real one. At the
  current ~27.3 ms/request, one vCPU sustains **~37 RPS**.
- **Gate 2 / Phase 2 (multi-pattern engine, rule budgets, Tier-2 gating) all target stage
  time.** At 45 rules the nine stages are ~5 ms of a 7.5 ms tax. They matter at higher rule
  counts, not here.

### Still unmeasured

**Streaming.** Every number above is non-streaming. Streaming first-token was 358–962 ms at
task 1E, before any redaction work. `_redact_trace_text` runs on that path too, so tasks 8
and 8b may help — but that is a guess until measured, and guesses have not fared well here.
