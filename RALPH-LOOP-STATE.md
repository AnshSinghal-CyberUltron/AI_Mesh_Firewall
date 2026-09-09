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
| `hot-path-latency-20ms` | **NEW — requirements in progress** |

## Iteration log

- **1** — Created worktree + `dev/perf-9stage`; replicated ansh WIP (`a83d113f`). Measured the policy-engine cost curve. Proved G0.3 targets dead code and re-scoped it (`ce4e8a64`). Opened `hot-path-latency-20ms`.

## Completion promise — NOT yet true

`<20ms latency and maximum rps (maybe 100k) on a vcpu practically possible tested and proven end to end`

Blockers: no Docker E2E harness yet (policy-driven-detection task 10); no end-to-end latency measured
on this tree; 100k RPS/vCPU is **refuted** by measurement (best case ~426 RPS/vCPU at 60 rules on
c8i-class hardware). The RPS half of the promise must be stated as *measured maximum*, not 100k.
