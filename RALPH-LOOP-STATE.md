# Ralph loop state — dev/perf-9stage

**Goal:** <20 ms p50 for the full 9-stage pipeline; maximum practical RPS/vCPU; proven end-to-end under Docker.
**Branch:** `dev/perf-9stage` in worktree `/home/contact_cyberultron_com/aimesh-dev`.
**`ansh` and `main` must never be touched.** `ansh` is pinned at `a67337fb` with 42 uncommitted files.

## Ground truth measured on this tree (Xeon 8581C @ 2.30 GHz, Emerald Rapids)

| Fact | Value | Where |
|---|---|---|
| `_scan_prompt_sync` is a **passthrough** | `return ScanVerdict()` | `scanner.py:1160` |
| All detection now runs through | `policy_engine.evaluate` | — |
| Policy engine cost | **~0.065 ms per rule** @1024 tok, linear | measured |
| 10 / 30 / 60 / 120 / 264 rules | 0.68 / 1.89 / 3.99 / 7.95 / **17.06 ms** | measured |
| `_search_with_budget` spawns a thread **per regex**, `start()` then `join()` | serial — **zero parallelism, pure overhead** | `policy_engine.py:273-290` |
| `async_post_llm` is **inert in block mode** | all three branches set `force_sync_tier2 = True` | `main.py:8364-8373` |
| `ATTACK_PATTERNS["command_injection"]` | **dead code** — live consumers read only `(prompt_injection, jailbreak)` | `policy_engine.py:95,136`, `mcp_scan_orchestrator.py:272` |
| DEBUG logger + sync Redis PUBLISH | unconditional, **not** env-gated | `main.py:6019` |
| 41 KiB `pipeline_trace` on allow paths | `_scrub_trace_for_client` called only at `:957` (blocked path) | — |
| `redact_all` | 8.19 ms p50 per pass, 4–20 passes/request | prior corpus |

## Spec status (`.kiro/specs/`)

| Spec | State |
|---|---|
| `detection-corpus` (G0.1) | 11/11 ✅ |
| `posture-scoring` (G0.2) | 11/11 ✅ |
| `honest-stream-latency-metric` (G1) | 6/6 ✅ |
| `policy-driven-detection` | 3 done / 6 open (4,5,6,7,8,10) |
| `command-injection-fp-fix` (G0.3) | **re-scoped** — targets dead code; folded into policy-driven-detection task 5 |
| `hot-path-latency-20ms` | requirements + design + tasks written; **0/11 executed** |

## Iteration log

- **9** — **Task 1E: streaming first-token latency, 129 → 30 chunks.** Built the
  detection-equivalence gate FIRST (`scripts/detection/stream_retention_equivalence.py`),
  then changed retention from a flat 512 bytes to content-derived
  (`_min_retain_bytes`) and the flush trigger from chunks to bytes
  (`STREAM_FLUSH_BYTES=160`). Gate: 748/748 short + 2244/2244 long documents
  byte-identical, every enforced verdict unchanged. **Corpus alone would have been
  vacuous** — no item exceeds 93 chars, so none reaches the BUFFER_LIMIT trigger; hence
  the `--long` mode. Sized the retention from evidence: of 31 whitespace-crossing
  patterns, **25 are unbounded**, and the flat 512 never was a covering bound.
- **8** — **Task 1D: the added streaming time is at the HEAD, not the tail.** Splitting
  ADDED WALL CLOCK into HEAD/TAIL showed 3097 / 2042 / 1393 ms to first token for
  100/200/300 tokens — a 100-token answer streamed *nothing* before `[DONE]`. Also
  measured that **my own 1C fix regressed this** (first release chunk 89 → 129) and said
  so. Predicted first release at chunk 128; measured 129.
- **7** — Task 1C: flush on NEW chunks rather than queue depth. Guard CPU
  112.5 → 6.70 / 454.6 → 18.10 / 793.2 → 24.90 ms (**31.9×** at 300 tokens). Twice
  mis-diagnosed the mechanism (O(n²), then a growing buffer) before instrumenting
  refuted both: the scan window was already bounded at ~525 chars; the bug was flush
  COUNT.
- **2-6** — Built the Docker E2E harness from scratch and cleared five bring-up
  blockers (chunked-SSE framing in the stub; two compose `environment:` > `env_file:`
  precedence traps; control healthy with no schema; `422 no_provider_configured`
  because `ensure_default_llm_model` seeds the *guard* model, not an org inference
  model). Produced the project's first real end-to-end nine-stage measurement, and
  added a refusal for a harness bug of my own that reported 2,638 ms of "firewall tax"
  while claiming all honesty checks passed.
- **1** — Created worktree + `dev/perf-9stage`; replicated ansh WIP (`a83d113f`). Measured the
  policy-engine cost curve and isolated the mechanism (`Thread` create+join = **0.0586 ms**, which is
  the entire 0.065 ms/rule slope). Proved G0.3 targets dead code and re-scoped it onto task-5
  re-homing (`ce4e8a64`). Wrote `hot-path-latency-20ms` requirements → design → tasks. **Corrected my
  own R2 by measurement**: a flat ≥3× from thread removal is false — it is 12.1× at 512 chars but
  only 2.4× at 4,096 chars, and the residual 10.43 ms there is real regex work needing a
  multi-pattern engine. Evidence: `docs/perf/evidence/2026-09-09-policy-engine-baseline.md`.

## Next action

**Task 1D/1E are done.** Next is the per-guard-pass fixed cost: ~3.2 ms to scan ~525
characters. That single number now sets the floor on streaming first-token latency —
every lever left (flush cadence, retention) trades against it, and the Pareto table in
`secure_streaming.py` shows why: halving the flush threshold roughly doubles guard
passes. Reduce the per-pass cost and every point on that curve moves at once.

After that: task 10 (max RPS/vCPU measured) — still the only way to make the RPS half of
the promise a measured number rather than an aspiration.

## Completion promise — NOT yet true

`<20ms latency and maximum rps (maybe 100k) on a vcpu practically possible tested and proven end to end`

Blockers: no Docker E2E harness yet (policy-driven-detection task 10); no end-to-end latency measured
on this tree; 100k RPS/vCPU is **refuted** by measurement (best case ~426 RPS/vCPU at 60 rules on
c8i-class hardware). The RPS half of the promise must be stated as *measured maximum*, not 100k.

## ⚠ Concurrent work is landing on `ansh`

During iteration 1, six files appeared in the `ansh` working tree that I did **not** create
(mtimes 17:52–17:55):

```
control/ai_mesh_control/policy/builtin_packs_catalog.py          ← task 5 (re-homing)
control/ai_mesh_control/policy/tests/test_builtin_packs_catalog.py
gateway/.../tests/test_policy_driven_legacy_toggle_inertness.py  ← task 6
gateway/.../tests/test_policy_driven_sdk_tier2_parity.py         ← task 4
gateway/.../tests/test_policy_driven_tier2_gate_property.py      ← task 4
gateway/.../tests/test_policy_driven_tier_separation.py
```

These are exactly `policy-driven-detection` tasks 4/5/6 — another session is actively working them
on `ansh`. **The worktree isolation was the correct call**; it kept this branch stable while that
landed.

**Consequence for this branch:** the `dev/perf-9stage` baseline (`a83d113f`) was snapshotted *before*
those files existed, so it lacks them. `builtin_packs_catalog.py` matters here because the G0.3
re-scope depends on the task-5 seed. Before starting task 7 (multi-pattern engine) or any work that
touches policy packages, **re-sync from `ansh`** and re-check whether the seeded `command_injection`
pack carries the bare `` `[^`]+` `` pattern.

**Verification that `ansh` was not disturbed by me:** its 8 tracked modifications are byte-identical
to the snapshot taken at the start of iteration 1 (`git diff HEAD --binary` sha256 `32bb3c5083f7836f`
before and after). Only untracked files changed, and none by me.
