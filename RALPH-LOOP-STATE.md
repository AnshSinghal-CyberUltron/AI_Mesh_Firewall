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

- **1** — Created worktree + `dev/perf-9stage`; replicated ansh WIP (`a83d113f`). Measured the
  policy-engine cost curve and isolated the mechanism (`Thread` create+join = **0.0586 ms**, which is
  the entire 0.065 ms/rule slope). Proved G0.3 targets dead code and re-scoped it onto task-5
  re-homing (`ce4e8a64`). Wrote `hot-path-latency-20ms` requirements → design → tasks. **Corrected my
  own R2 by measurement**: a flat ≥3× from thread removal is false — it is 12.1× at 512 chars but
  only 2.4× at 4,096 chars, and the residual 10.43 ms there is real regex work needing a
  multi-pattern engine. Evidence: `docs/perf/evidence/2026-09-09-policy-engine-baseline.md`.

## Next action (iteration 2)

Start **task 1 — the Docker E2E harness**. It blocks every latency claim. Begin with 1.1
(`token_stub.py`) and 1.2 (`compose.perf.yml`), then 1.8 to capture the pre-change baseline matrix.
Do **not** start task 2 before task 1.8 has a recorded baseline — there would be no denominator.

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
