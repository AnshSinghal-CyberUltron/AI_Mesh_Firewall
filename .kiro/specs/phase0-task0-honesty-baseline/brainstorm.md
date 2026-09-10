# Brainstorm / Plan: Phase 0 Task 0 — Honesty baseline of the live 9-stage pipeline

**Status:** Plan **shrunk after contradiction** (2026-09-10). See `PLAN_REVIEW.md` and `requirements.md`. **No production code will be changed.** Streaming vs 20 ms is instrument-invalid. Cell_A non-stream tax is the only 20 ms comparator.
**Worktree:** `/home/contact_cyberultron_com/aimesh-p0-task0` branch `feat/p0-task0-honesty-baseline` (from `ansh` commit `a67337fb`). The dirty `ansh` checkout is not used.
**Date:** 2026-09-09 (locked) / 2026-09-10 (review)

---

## 0. What "Phase 0 Task 0" is

There is **no row literally named "Task 0"** in `docs/plans/2026-08-27-task-tracker.md`. Mapping from the plan corpus:

| Source | Nearest ID | Meaning |
|---|---|---|
| `2026-08-22-MASTER-hot-path-capacity-architecture.md` §8 | **Phase 0 — Honesty and instrumentation** | Days of measurement, **no behaviour change** |
| `2026-08-25-FINAL-verification-and-platform-agnostic-scale-plan.md` | **0.1 / 0.2 / 0.3** | Unique-prompt + stub honesty; split `T_addon_*`; MB/in-flight |
| `2026-08-14-bedrock-hotpath-rps-latency.md` | **Task 0.1 Host preflight** | Fail the bench if the host/env is a lie |
| `2026-08-21-100k-rps-9stage-design.md` | **Phase 0 — Honesty + definition** | `full_nine_stages` must be *measured* |
| `2026-08-27-FINAL-evidence-based-hot-path-plan.md` | **Gate 0 / Gate 1 precede any SLO** | G0.1 corpus already committed on this SHA; G0.2 scoring and G1.1 stream-anchor are **later tasks** |

**This task (P0.0) is the honesty baseline:** bring up an isolated Docker stack from *this* tree, drive real `POST /v1/chat/completions` through the nine named stages, publish per-stage p50/p99 + CPU, and **score every latency/RPS claim in `docs/plans/` as PASS / FAIL / NOT-APPLICABLE with evidence**. It does **not** fix scanners, does **not** ship PG2, does **not** certify ≤20 ms.

G0.1 (labelled corpus) is already committed on `a67337fb`. G0.2 / G0.3 / G1.1 / Phase 1+ are **out of scope** for P0.0.

---

## 1. Goal the user stated vs what the plans allow us to claim

User goal: **&lt;20 ms latency** and **maximum CPU performance** for the **complete 9-stage pipeline**.

Plan facts that constrain that sentence (must be tested, not assumed):

1. **Firewall tax ≠ wall clock.** MASTER / dual-SLO: quote `T_addon = total_latency_ms − model_output_ms` (BYOK/provider excluded). A 20 ms *wall* including a real LLM is a different product than 20 ms *tax*.
2. **CPU-only ≤12 ms SLO was judged not achievable** (`2026-08-27-FINAL` §0–§9). §11 projects **≈4.1 ms p50 at ≤512 tokens / ≈5.9 ms at ≤1024 tokens** only with **in-process L4 TensorRT PG2-22M**, and states **74% of that number was unmeasured on hardware we control** at plan time.
3. **Tier-2 Bedrock Haiku on the path is 1.3–1.8 s** `[R]` — physically incompatible with a 20 ms *complete* 9-stage wall if `ENABLE_TIER2=true` and T2 is awaited.
4. **Streaming addon currently equals provider TTFT** until Gate 1.1 lands (`honest-stream-latency-metric`). Streaming numbers from this task are **instrument-suspect** unless we also record `ttft_ms` vs `t_addon_pre_ms`.
5. **Stub LLM runs are invalid capacity numbers.** `scripts/perf/gateway_pipeline_bench.py` `compute_full_nine_stages` + `capacity_predicate` must FAIL if `GATEWAY_LOADTEST_STUB_LLM=1` or completion id is `chatcmpl-loadtest-stub`. Stub is allowed **only** to isolate firewall tax.
6. A parallel worktree `aimesh-dev` / project `aimeshperf` already optimises toward 20 ms **and has changed product code**. **P0.0 must not attach to that stack or that branch.** Collision would contaminate both isolation and honesty.

**Honest P0.0 success:** a published evidence pack that says, for each claim, whether the *running* 9-stage path on this SHA meets 20 ms tax, 20 ms wall, or neither — with CPU, stage breakdown, image digests, and posture.

---

## 2. Isolation (non-negotiable)

| Surface | Rule |
|---|---|
| Git | Worktree `/home/contact_cyberultron_com/aimesh-p0-task0`, branch `feat/p0-task0-honesty-baseline`, SHA `a67337fb`. Do not edit `/home/contact_cyberultron_com/AI_Mesh_Firewall` (dirty `ansh`). |
| Docker project | `COMPOSE_PROJECT_NAME=aimf_p0` — **not** `ai_mesh_firewall`, **not** `aimeshperf`. |
| Host ports | Remap so we do not steal 5432/6379/6432/8100/8300 (already bound). Proposed: postgres `15432`, redis `16379`, pgbouncer `16432`, control `18100`, gateway `18300`, frontend `18180`, rabbitmq `5773`/`15773`, demo `18770`, mcp-stub `19999`. |
| Images | Build gateway + control **from this worktree** (or record digest + prove it matches this SHA). Reusing `aimeshperf-gateway` would measure a *different* tree. |
| Product code | **Frozen.** Overlay compose, harness scripts, `.kiro/specs/`, and `docs/perf/evidence/` only. |
| MCP broker | Do **not** start `mcp-broker` (hardcoded `container_name: ai_mesh_mcp_broker`). Chat 9-stage does not need it. |

---

## 3. The nine stages (runtime, not folklore)

From `gateway/ai_mesh_gateway/pipeline_trace.py`:

```
auth → kill_switch → rate_limit → policy → input_scan
    → model_routing → model_input → model_output → output_guardrail
```

MASTER still notes an older list that put kill_switch after input_scan. **P0.0 records both the name list and the observed `seq` / skip flags.** A 0 ms stage without `ran=false` / skip semantics is a **fail** for honesty (MASTER Phase 0; existing bench `input_scan`/`output_guardrail` p50 must be > 0 for `full_nine_stages`).

This SHA still wires `scanner_recommendation=_guard_rec` (not the uncommitted policy-driven `None`). Input scan is the shipped scanner, not the dirty-tree passthrough.

---

## 4. Claims inventory to test (from `docs/plans/`)

Each claim gets a row: claim text, source, measurement method, result, evidence path.

**Latency / SLO**

- ≤12 ms CPU-only tax — FINAL §0–§9 (expect FAIL on this hardware/path).
- ≈4.1 / 5.9 ms p50 with in-process L4 PG2 — FINAL §11 (expect **N/A**: PG2 not in this Docker image; GPU bake-off is a different host).
- ≤20 ms complete 9-stage (user) — test as **tax** and as **wall**, stub and (if creds exist) live provider, T2 on and T2 off.
- Unique-prompt input_scan p50 not ~3 ms cache artefact — 2026-08-14 Task 0.2.
- `T_addon_pre − T_t2` p50 < 50 ms with Haiku still on path — MASTER Phase 1 gate (P0.0 *measures*; does not implement RC-1).
- Client wall vs `pipeline_trace.total_latency_ms` ±0.1 ms — PIPELINE-0018 (non-stream). Stream: record suspected G1.1 bias.

**Honesty / bench**

- `full_nine_stages` false when scans skipped or timers 0 — already unit-tested in `scripts/perf/test_gateway_pipeline_bench_honesty.py`; P0.0 re-runs that **and** a live scans-off chat if a toggle exists.
- Stub completion id fails capacity predicate — live + unit.
- Host preflight: workers, stub flag recorded, Bedrock RTT if T2 on — 2026-08-14 0.1.

**Detection (read-only; no G0.3 fixes)**

- FINAL devil's advocate: T1 0/10 paraphrase, 5/5 inline-code block — **re-probe on this SHA via live `/v1/chat/completions`**, not scanner-only, because enforcement is `proxy_chat`.
- G0.1 corpus lint still green on this SHA (`test_detection_corpus_lint`).

**Non-claims (must not be quoted as product RPS)**

- `/health` RPS, 6-stage stub, summed multi-host IP, classifier-only GPU RPS, HTTP `/v1/scan` ≠ `proxy_chat`.

---

## 5. Measurement postures (matrix)

Minimum cells (each unique-prompt, N≥16 serial after warmup, plus a small inflight burst):

| ID | LLM | Tier-2 | stream | What it answers |
|---|---|---|---|---|
| A | in-gateway stub, duration > 0 so output_guard actually runs | off | false | Firewall tax, 9-stage timers, CPU; **capacity_eligible must be false** |
| B | same stub | off | true | Streaming tax vs TTFT (G1.1 honesty check) |
| C | same stub | on (if Bedrock reachable) | false | Tax **with** T2; expect seconds if Haiku awaited |
| D | real catalog model (if org key + provider configured) | off | false | Wall vs tax; `id != chatcmpl-loadtest-stub` |

If Bedrock is unreachable, C is **FAIL-CLOSED as N/A-measured** with the TCP error, not a silent skip.

CPU: `docker stats` / cgroup `cpu.stat` during A at pinned `WEB_CONCURRENCY` and compose CPU limit. Report **RPS/vCPU of the tax path**, labelled invalid-for-capacity if stubbed.

---

## 6. Deliverables (this task only)

1. Kiro spec: this brainstorm → `requirements.md` → `design.md` → `tasks.md` (this folder).
2. Compose overlay `scripts/perf/e2e/compose.p0.yml` + `p0.env` (non-secret harness env; signing key explicit in `environment:` to avoid compose precedence trap documented in `aimesh-dev` compose.perf.yml).
3. Driver that POSTs unique prompts to `http://127.0.0.1:18300/v1/chat/completions`, parses `pipeline_trace`, records wall clock.
4. Evidence JSON + markdown under `docs/perf/evidence/2026-09-09-p0-task0-honesty/` (gitignored if it would contain API keys; redact).
5. Claim scorecard vs `docs/plans/`.

**Not delivered:** scanner/pattern changes, PG2 wiring, stream-anchor production fix (G1.1 already has its own spec), attaching to `aimeshperf`.

---

## 7. Risks already known (do not "fix" in P0.0)

- Policy signing key empty → `/health` 200 with `policy_count: 0` while detection is silently off (documented on the perf overlay). Harness must assert `policy_cache_loaded` / `policy_count` against the intended seed.
- Frontend bind-mounts `./frontend`; host `18180` is already taken — use `18181`. CORS/`FRONTEND_ORIGIN` must match.
- `mcp_sandbox_bridge` is `external: true` in base compose; create or ensure networks exist before `up`, even if broker is not started.
- Disk 82% full; do not pull unused profiles (chroma, telemetry mongo, transport-stubs).
- Dirty `ansh` has policy-driven detection WIP; **this SHA does not**. Mixing them would produce a false "HEAD" baseline.

---

## 8. Canonical TARGET (locked 2026-09-09)

The final architecture is **PLAN/TARGET**, not deployed-and-verified. Source of truth, in this order:

1. `docs/plans/2026-09-07-end-to-end-architecture-hld.md` (GCP `asia-south1`, four G2 + eight L4)
2. `docs/plans/2026-08-27-FINAL-evidence-based-hot-path-plan.md` **§11–§12** (GPU update)
3. `docs/plans/2026-09-02-hot-path-cost-matrix.md` (~**$4,561/mo**, **1,064 RPS**, BYOK tokens excluded)

**Not** in the target: Kafka, ClickHouse, off-box GPU sidecars, 100k RPS, Haiku as the firewall judge.

Product actions stay. Engines inside the nine stages change:

> Tier-1 detects identifiable threats and sensitive data. Prompt Guard supplies an injection/jailbreak score. `resolve_and_enforce()` / `enforce_output()` turn findings into ALLOW / BLOCK / REDACT / MONITOR. FLAG is an annotation in the implemented lattice, not a substitute for the traffic outcome. The gateway applies the action and records it. No extra LLM emits those labels.

PG2 ~2.14 ms TRT is **classifier invocation only**. It is not the 9-stage tax.

Policy and input_scan stay **serial**. Prefetch independent state only.

## 10. Review questions for the five agents

1. Is P0.0 the wrong first task (should we have started at G0.2 or G1.1 instead)?
2. Is a stub LLM enough to call the path "complete 9-stage"?
3. Does remapped-port isolation still count as "Docker images and containers up"?
4. Can we honestly talk about &lt;20 ms before G1.1?
5. Which plan claims are untestable on this host and must be N/A rather than FAIL?
