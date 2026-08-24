# 100k RPS 9-stage pipeline — system design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **Status:** SUPERSEDED for the locked SLO. User locked **C** (100k completed 9-stage chats). Canonical C plan: `docs/plans/2026-08-21-100k-completed-chats-C-slo.md`. This file remains the A/B (enforcement + classifier) overlay. Do **not** implement until C plan is confirmed.

**Goal:** Reach a *defined* 100,000 requests/second peak on the chat firewall without putting a generative LLM on gateway workers, and without dropping the input/output checks Bedrock currently provides.

**Architecture:** Split the product into three different 100k claims (enforcement / semantic-guarded allow / completed customer chat). Keep T1 + policy + auth on the gateway. Replace per-request Haiku Converse with a local classifier sidecar for semantics. Keep shared Redis as the fleet source of truth, but stop doing 6–9 Redis RTTs on every chat. Original monolith (`i-07a9d65b103ac1a87`) stays UI/control/Postgres; gateway ASG scales 0 extras when idle.

**Tech Stack:** FastAPI/gunicorn UvicornWorker, existing T1 (`scanner.py` / `patterns.py` / `output_guard.py`), optional NVIDIA Prompt Guard or ProtectAI DeBERTa ONNX sidecar, ElastiCache Redis 7, NLB TLS:443 → `:8300`, two-tier rate limit (local GCRA + Redis quota lease).

---

## 0. Locked question (user must answer before coding)

**100k of what?** These are three different systems:

| ID | What 100k means | Honest today | Can we get there? |
|---|---|---|---|
| **A. Enforcement RPS** | Auth + kill-switch + rate-limit + policy + T1 input + T1 output, stub or pass-through to *customer* LLM | 6-stage stub ~1.2k through NLB+shared Redis; ~3.2k per isolated host with local Redis; `/health` ~11k through NLB | **Yes**, with T1 speedup + Redis two-tier + ~15–40 c8g boxes + in-region loadgens |
| **B. Semantic-guarded allow** | Every T1-allow still gets a *classifier* (today: Haiku Converse T2 in + T2 out) | Unique 9-stage ~8–40 **ok_rps** fleet-wide (Bedrock-bound, not per-box) | **Not with Haiku.** Possible with GPU classifier sidecar at tens of ms **and** T1 rewrite **and** a GPU fleet. Not 100k Haiku TPS in `ap-south-1`. |
| **C. Completed customer chats** | 100k full BYOK completions/s through our 9 stages | BYOK p50 ~0.65–1.4s; Little’s Law 100k×0.8s ≈ 80k in-flight provider calls | **No as a firewall promise.** That is the customer’s model capacity. We can *admit* 100k; we cannot *complete* 100k Haiku/GPT generations. |

**Recommendation (historical):** this file argued A+B and “never market C.” **Superseded:** C is the locked SLO. See `docs/plans/2026-08-21-100k-completed-chats-C-slo.md`. A+B remain **necessary but not sufficient** for C.

The 2026-08-14 hot-path plan (`docs/plans/2026-08-14-bedrock-hotpath-rps-latency.md`) optimized *in-flight Haiku* on one box. This plan does **not** try to make Haiku do 100k TPS.

---

## 1. Discovered architecture (9 stages)

Canonical names (`gateway/ai_mesh_gateway/pipeline_trace.py`):

`auth → rate_limit → policy → input_scan → kill_switch → model_routing → model_input → model_output → output_guardrail`

**Wall-clock in `proxy_chat` is different:** auth (middleware) → kill-switch Redis → model_state Redis (unmetered) → rate_limit → policy → input_scan → routing → model → output guard.

| Stage | What it does | I/O | Typical cost |
|---|---|---|---|
| auth | `GET auth:apikey:{sha256}` | Redis | ~1 ms (p99 blows up under ElastiCache saturation) |
| kill_switch | 2–3 GET pipeline | Redis, fail-closed | ~13 ms p50 on shared ElastiCache |
| rate_limit | burst+RPM MULTI (serial), optional TPM Lua | Redis | ~32 ms p50 shared; ~0 local Redis |
| policy | in-process `POLICY_SYNC` + `evaluate()` | CPU, **no HTTP** if cache loaded | ~2–18 ms |
| input_scan T1 | regex, deobfuscation, G33/G53, PII/secrets | CPU thread pool | ~70–105 ms unique (next local ceiling) |
| input_scan T2 | **Haiku Converse JSON classifier** | Bedrock HTTP | **~1.0–1.6 s** |
| model_routing | deterministic CPU; optional KS GETs | CPU | ~0 if pinned |
| model_output | customer BYOK or stub | provider | stub ~0; live ~0.8–1.4 s |
| output_guardrail T1 | PII/exfil/IP/markdown-split | CPU | ms–tens of ms |
| output_guardrail T2 | **second Haiku Converse** | Bedrock HTTP | **~0.9–1.3 s** |

`ApplyGuardrail` is **not used**. T2 is `BedrockClient.aconverse` with a JSON-only system prompt. `GUARDRAILS_SERVICE_URL` is injected and **never read**.

There is **no 6-stage code path**. 6-stage benches skip T1/T2/guard/BYOK (`latency_ms=0`) on the same `proxy_chat`. Harness sets `honesty.full_nine_stages: true` whenever `MODE==chat` — a lie.

---

## 2. Current behavior (measured, 2026-08-21 and earlier)

Evidence: `mcp-parallel/findings/nlb-shared-redis-2026-08-21/summary.json`, `nlb-6stage-100k-2026-08-21/`, `nlb-100k-2026-08-21/`, Ruflo `full-vm-peak-9stage-2026-08-14`, `code-vs-aws-isolation-bench-2026-08-17`.

| Experiment | ok_rps | What it was |
|---|---:|---|
| NLB `/health` @768 inflight | **11,011** | No Redis, no scan |
| 6-stage stub, shared ElastiCache, NLB @768 | **1,214** | Redis wall (rate_limit 31.7 ms, KS 13.3 ms) |
| 6-stage stub, 3 isolated hosts **summed** @256 | **9,839** | **~3.2k per host**, local Redis, scans off — not one box |
| 6-stage stub @100k inflight, 3 hosts summed | **3,408** | Inflight collapse; still scans off |
| Unique 9-stage (T2+BYOK+guard) | **~8–40** | Bedrock + provider; 1 box ≈ whole fleet |
| T1-only unique chat, one c8g.2xlarge | **~85** | T1 p50 ~105 ms queued / ~70 ms quiet |
| Live 100k-inflight 9-stage retry | **41** ok, **98% errors** | TPM 429 (`101833/100000`), 422 catalog, ConnectTimeout, RemoteProtocolError |

Live TPM math: 100000 tokens/min ÷ ~36 tokens/req ÷ 60 ≈ **46 RPS theoretical** on that key. A 100k-TPM key cannot do 100k RPS.

---

## 3. What Bedrock is doing today vs “don’t use an LLM”

You asked: *if we don’t use an LLM, how do we check input and output?*

**You already do, on every request, without an LLM (Tier-1):**

- Input: `ATTACK_PATTERNS` (injection, jailbreak, tool_overreach, SQLi, …), deobfuscation, fuzzy, `detect_pii` / `detect_secrets` / credentials, G33 encoded PII, G53 markdown-split PII, keywords, policy regex, DoS length.
- Output: same detectors + exfil-beacon defang + markdown-split + IP leakage + `enforce_output` fail-closed on no-op scrub.

**Bedrock T2 is extra semantics**, not the only guardrail. It is a full Haiku generation used as a JSON classifier for paraphrased / multilingual / intent-based jailbreaks that T1 regex misses. Timeout 60s, `BEDROCK_MAX_TOKENS=1024`. Two serial Converse calls per allow (input + output).

If we skip Bedrock and keep T1: canned jailbreaks and PII/secrets/IP still block/redact. We **lose** paraphrased/multilingual jailbreak and semantic tool-overreach unless we add a **small classifier** (not a chat LLM).

Industry (PromptGuard three-tier, tianpan 2026, AWS Guardrails samples, Llama Guard benches):

- Fast path: in-process regex, a few ms, ~95% of traffic.
- ML path: DeBERTa / Prompt Guard / Llama Guard 1B, tens–low hundreds of ms on GPU (~50ms p99 Llama Guard 8B on A100; 1B is the 100k-relevant size).
- Slow path: LLM-as-judge, 6–10s, **opt-in / sample / async**. Not a per-request gate at high QPS.
- AWS `ApplyGuardrail` default **50 TPS** (2× from 25) in us-east-1/us-west-2 — **2000× too small** for 100k. Caching/batching helps enterprise gateways at hundreds of RPS, not 100k.

**Do not sit the customer model on the gateway.** Do not sit Haiku inside gunicorn. Previous review already locked: ONNX must not run in workers with `--preload`. Classifier = sidecar.

---

## 4. Suspected issues (ranked) and confidence

| # | Issue | Confidence | Evidence |
|---|---|---|---|
| 1 | Two serial Haiku Converse calls make 100k **completed 9-stage** physically impossible | **High** | ~3s wall; 1 box ≈ fleet ~30–40 ok_rps; `tier2_unavailable` at 512 inflight |
| 2 | Shared ElastiCache **hot-key** Redis (6–9 serial RTTs) caps stub chat ~1.2k through NLB | **High** | health 11k vs chat 1.2k; isolated local Redis ~3.2k/host |
| 3 | T1 CPU ~70–105 ms ⇒ ~80 RPS/8 vCPU even after Bedrock is gone | **High** | Aug 17 isolation 84.85 ok_rps plateau |
| 4 | CPython/gunicorn HTTP ceiling ~7–11k RPS/host | **High** | `/health` 9.7–11k, CPU pegged |
| 5 | Product TPM/RPM (100k TPM, 1000 RPM) cap a tenant at tens of RPS | **High** | live 429 101833/100000 |
| 6 | NLB SNAT ~55k conn/target; 100k **inflight** dies on connect reset | **Medium-high** | health-100k 73% ConnectTimeout; chat RemoteProtocolError |
| 7 | Observability lies (`full_nine_stages`, UI ≥6 stages = live) | **High** | bench.py L351–356 |
| 8 | GCP 16-core loadgen cannot emit 100k RPS | **High** | health 100k ramp ~4k ok_rps |

---

## 5. Devil’s advocate (what we will **not** do)

- **Will not** put Haiku or the customer LLM inside gateway workers to “save a hop.” Workers already wait on sockets; colocation does not remove 1s generation and risks multi-tenant GPU/noisy-neighbor.
- **Will not** switch T2 to `ApplyGuardrail` expecting 100k (quota ~50 TPS).
- **Will not** skip G33/G53/deobfuscation to fake 1 ms T1 (reopens obfuscated PII — PIPELINE-0011).
- **Will not** cache kill-switch/auth as last-good **fail-open** on Redis miss (inverts today’s 503 disable).
- **Will not** give each ASG member a full local org TPM bucket (N× limit).
- **Will not** drop `GATEWAY_TIER2_SAMPLE_RATE` on T1-allow to buy RPS (that is exactly the traffic T2 exists for).
- **Will not** treat 100k inflight, `/health`, stub 6-stage, or merged 3-IP sums as 100k 9-stage.

---

## 6. Target architecture

```
                    ┌──────────────┐
  clients ──TLS──►  │ NLB :443     │  preserve_client_ip ON
                    └──────┬───────┘
                           │ TCP 8300
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         gateway ASG   gateway ASG   original monolith
         (c8g, T1)     (c8g, T1)     UI/control/PG
              │            │            │
              │   local GCRA admit      │
              │   RAM policy bundle     │
              └────────────┼────────────┘
                           │
                    ┌──────▼──────┐
                    │ ElastiCache │  auth GET (or ≤50ms cache+pubsub)
                    │ Redis 7     │  KS GET fail-closed
                    │ (cluster)   │  TPM quota-lease INCRBY
                    └──────┬──────┘
                           │
              T1-allow only ▼
                    ┌──────────────┐
                    │ Classifier   │  Prompt Guard / DeBERTa / LG-1B
                    │ GPU sidecar  │  pinned version, own ASG
                    │ (g6e/L4)     │  timeout fail-CLOSED under block
                    └──────────────┘
                           │
                    customer BYOK (not our RPS SLO)
```

### 6.1 Fast path (every request, gateway process)

1. Auth (Redis GET or cache ≤50ms with pub/sub revoke).
2. Kill-switch + model_state (prefer still Redis; if cached, TTL ≤50ms and Redis-down **disable**).
3. Local GCRA burst/RPM; Redis quota-lease for org/key TPM (chunk INCRBY, not per-request Lua).
4. Policy evaluate in RAM (already).
5. T1 scan **including** deobfuscation/G33 — but **reimplemented** so the hot path is µs–low-ms (Vectorscan/Hyperscan or Rust Aho-Corasick + a single canonicalize pass), not 70ms Python regex loops.
6. T1 output on completions (same engine).

### 6.2 ML path (T1-allow only, sidecar)

Local classifier replaces Haiku for injection/jailbreak/toxicity. Pin model hash. `policy_version` in cache key. Fail-**closed** when org `enforcement_mode=block` and sidecar 5xx/timeout. Fail-open only in `monitor`.

### 6.3 Slow path (not 100k)

Haiku Converse **async sample** (audit, 0.1–1% unique prompts) or high-stakes orgs only. Never on the 100k admit path.

### 6.4 Fleet math for **A** (enforcement)

After T1 is ~1–3 ms **and** Redis is off the hot path:

- HTTP ceiling ~8–11k RPS per c8g.2xlarge (`/health`).
- 100k / 8k ≈ **13 hosts** theoretical; plan **20–40** for T1 CPU, TLS, JSON, SSE.
- Loadgen: **in-region** fleet (not one 16-core GCP VM). 100k RPS at 5 ms needs ~500 client in-flight; at 50 ms needs 5k; at 3 s needs 300k — generate from many boxes.

After T1 stays at 70 ms: 8 cores / 0.07s ≈ 110 RPS/box → **900+ boxes**. That is why T1 rewrite is mandatory for A.

---

## 7. Proposed work (phased) — do not start until confirmed

### Phase 0 — Honesty + definition (1–2 days)

- Fix `honesty.full_nine_stages` to **measured** non-zero `input_scan` / `output_guardrail` / non-stub `id`.
- UI: 0ms stage = skipped, not allow.
- Meter `model_state_ms`. Pipeline burst+RPM into **one** MULTI (cheap, not sufficient).
- Chat body cap (parity with MCP `_mcp_read_body_capped`).
- Document SLO A vs B vs C in UI/status.

**Verify:** bench JSON with scans off must set `full_nine_stages: false`.

### Phase 1 — Redis off the hot path (correctness-preserving)

Files: `main.py` (burst/RPM), `rate_limiter.py`, `kill_switch.py`, `middleware.py`, `circuit_breaker.py`.

- Local GCRA for burst/RPM (soft).
- Org/key TPM: Redis **lease** (`INCRBY` chunk), not per-request EVAL. Overshoot bound = `instances × chunk`.
- CB CLOSED: skip Redis GET.
- Auth/KS: optional ≤50ms cache + pub/sub; Redis error still **fail-closed**.
- Policy/config: add 1–2s version heartbeat (already RAM; missed pub/sub hole).

**Verify:** 6-stage stub through **shared** ElastiCache approaches isolated per-host (~3k/host) **without** split-brain TPM (two orgs, one cannot steal the other’s budget). Kill-switch SET → next request ≤100ms disable.

### Phase 2 — T1 engine (do not drop G33)

- Single canonicalize + decode budget, then Vectorscan/Rust multi-pattern.
- Keep G33/G53/byte-verify fail-closed.
- Target p50 **< 3 ms** unique 2KB prompt on Graviton3.

**Verify:** existing `test_pipeline_obfuscation_fp.py`, G33/G53, leakhunt; new microbench 10k unique prompts.

### Phase 3 — Classifier sidecar (replaces sync Haiku)

- Implement the unused `GUARDRAILS_SERVICE_URL` against a real service (Prompt Guard ONNX or ProtectAI deberta-v3-base-prompt-injection-v2).
- Pin digest; health = model loaded.
- Wire `scan_prompt_with_tier2` / `scan_output_with_tier2` to sidecar first; Haiku fallback only if sidecar down **and** org allows (block orgs: 451/403, not fail-open).
- T2 cache key: `org + text + policy_version + model_hash`. Compose TTL 0 vs terraform 300: pick one.

**Verify:** paraphrased jailbreak that T1 misses is blocked by sidecar; sidecar kill → block-mode 403/451, not allow.

### Phase 4 — Infra for 100k **A**

- NLB: `preserve_client_ip` on (or extra target ports) to dodge 55k SNAT.
- nginx keepalive pool to gunicorn; TLS session tickets.
- Gateway ASG max sized for A (20–40 c8g); **min extras = 0**; original always in TG.
- ElastiCache cluster-mode if lease keys still hot.
- In-region loadgen ASG (c7g/c8g) running `gateway_pipeline_bench.py`.
- Do not generate 100k from the GCP workstation.

**Verify:** `/health` ≥80k ok_rps through NLB from in-region gens (proves LB+HTTP). Then 6-stage stub ≥50k. Then T1-on unique ≥20k. Then A target 100k.

### Phase 5 — Product limits

- Capacity-profile TPM/RPM for the bench **key only**, not silent prod default of 17 RPS/tenant.
- Catalog last-good: empty `routing=[]` must not clobber (422 `no_provider_configured`).
- SSE: gate 100k **A** on non-stream first; stream SLO is concurrent streams, not RPS.

---

## 8. Acceptance tests (mandatory — not unit tests alone)

A 100k claim is **false** unless all of:

1. Quote **`ok_rps` only**, `error_rate ≈ 0`.
2. Sampled 200s: `id != chatcmpl-loadtest-stub`, unique `TOK-` round-trips, `usage.total_tokens != 9`.
3. For **B**: `input_scan` and `output_guardrail` p50 **> 0** and classifier/T2 actually ran (`tier2_ms` or sidecar span).
4. For **A**: T1 detections still fire on G33/G53 fixtures under load.
5. Kill-switch live: SET in Redis → ≤100ms 403 on both original and an ASG clone.
6. TPM: two ASG members cannot admit `2 × org_tpm_limit`.
7. Client wall p50 reconcilable with `pipeline_trace.total_latency_ms` (no 12s client vs 39ms trace).
8. Loadgen is **in-region**, not one 16-core GCP box.
9. Stream=true soak (lower RPS OK) without OOM.
10. Repeat 3×; do not stop at first green.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| T1 rewrite misses obfuscation | Keep current Python T1 as oracle in tests; fail closed if new engine disagrees on fixtures |
| Sidecar becomes 80ms×100k = 8k inflight | Batch + HPA on GPU; fail-closed under block |
| Lease overshoot | Small chunks; sync 50–100ms |
| Scale-from-0 extras | Original always healthy on NLB; extras min 0 is OK |
| Marketing conflates A and C | Explicit SLO copy in UI and this doc |

---

## 10. Dependencies

- User picks A / B / C (or A+B).
- GPU quota in `ap-south-1` if B.
- ElastiCache resize/cluster mode.
- In-region loadgen account/VPC.
- Do **not** `scripts/sync-to-ec2.sh --deploy` until Phase 0–2 are green on a non-prod key.

---

## Execution choice (after confirmation)

1. **Subagent-driven (this session)** — one phase at a time, review between phases.
2. **Parallel session** — `executing-plans` in a worktree.

Which 100k (A / B / C / A+B) and which execution option?
