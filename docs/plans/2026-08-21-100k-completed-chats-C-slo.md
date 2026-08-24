# 100k completed 9-stage BYOK chats (SLO C) — system design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **Status:** PLAN ONLY — wait for user confirmation. Do **not** implement until they approve this C-SLO plan.
> **Locked SLO:** **C** — 100,000 **completed** customer chats per second through all 9 pipeline stages, with a real generation (not `/health`, not stub, not 6-stage with 0 ms scans).
> **Swarm:** `swarm-1787312560671-btox54` (C design) + `swarm-1787381519817-jlkaj4` (HLD/LLD). Review agents: false-positives, hidden-failures, state, observability, edge-cases. Devil's advocate below survived those reviews.
> **HLD/LLD proof (TrueFoundry, Kafka, ClickHouse, CDN, PowerDNS, API Gateway, K8s):** `docs/plans/2026-08-22-100k-C-hld-lld-proof.md`.

**Goal:** Make 100k finished chats/s through `auth → rate_limit → policy → input_scan → kill_switch → model_routing → model_input → model_output → output_guardrail` physically possible, with guardrails that still do what Bedrock T2 does today (and with an honest gap list for what a small classifier cannot do).

**Architecture:** Three fleets, never one process. (1) Gateway CPU ASG: admit + rewritten T1. (2) Guard GPU sidecar: Prompt Guard 2 22M on input + Llama Guard 3 1B on output. (3) Inference GPU/LPU fleet: vLLM/TensorRT-LLM/NIM (or a contracted provider TPS) serving a **canonical OpenAI-compatible model** that LiteLLM calls as `api_base`. Do **not** put the chat LLM or Haiku inside gunicorn. Do **not** relabel platform NIM as “customer BYOK.”

**Tech Stack:** Existing gateway T1 (`scanner.py` / `patterns.py` / `output_guard.py`) rewritten to Vectorscan/Rust; unused `GUARDRAILS_SERVICE_URL` wired to a real sidecar; Llama 3.1 8B FP8 on vLLM (prove C) or customer dedicated endpoint (true BYOK at their quota); ElastiCache two-tier rate limit; NLB + in-region loadgens.

---

## 0. Decision (you asked me to pick)

### 0.1 Sit the model on the gateway?

**No.** Putting Haiku, Prompt Guard, Llama Guard, or the customer chat model inside UvicornWorkers does not remove generation time. It adds GPU/noisy-neighbor risk on the multi-tenant admit path, and `--preload` + ONNX in workers is already forbidden. The generator stays a **separate fleet**. The classifier stays a **separate sidecar**. Same AZ / same VPC, not the same process.

### 0.2 What replaces Bedrock Haiku Converse (today’s T2)?

Today T2 is **not** AWS `ApplyGuardrail`. It is `BedrockClient.aconverse` — a full Haiku generation used as a JSON classifier (~1.0–1.6 s input + ~0.9–1.3 s output). Default ApplyGuardrail quota is ~**50 TPS**. That cannot be C.

| Tool | Role at 100k C | Decision |
|---|---|---|
| **Keep T1** (`patterns.py` / `scanner.py` / `output_guard.py`) | PII, secrets, canned injection, exfil defang, G33/G53, byte-verify fail-closed | **Keep, but rewrite** (~70–105 ms Python today → target p50 &lt; 3 ms). Skipping G33/G53 to fake 1 ms is forbidden. |
| **NVIDIA / PurpleLlama Prompt Guard 2 (22M)** ONNX/TensorRT sidecar | Input jailbreak/injection that regex misses; ~7–20 ms GPU, CPU ONNX tens of ms | **Pick for input T2** |
| **Llama Guard 3 1B** (vLLM/Triton sidecar) | Output S1–S13 safety taxonomy | **Pick for output T2** at stream `[DONE]` (not every 4 KB flush) |
| **ProtectAI DeBERTa v3 prompt-injection-v2** ONNX | Alternate/complement input classifier; g5 ONNX ~50k QPS in their table (chars/QPS, not chat RPS — size it ourselves) | **Bake-off vs PG2** in Phase 3; winner pinned by digest |
| **promptfoo** | Eval/CI red-team | **CI only.** Not a per-request runtime. |
| **llm-guard full suite** | Many Python scanners | **Do not put the suite on the hot path.** Palit bench ~1.59 s. Steal individual scanners only if T1 lacks them. |
| **NeMo Guardrails / Colang** | Dialog rails | **Avoid** on the 100k path (framework + hundreds of ms). |
| **Lakera / Vertex Model Armor / Azure Prompt Shield** | Hosted API | **Shadow/audit only.** Extra RTT + quota. Model Armor p99 ~450 ms is not C. |
| **Haiku / LLM-as-judge** | Paraphrased multilingual judge | **Async sample 0.1–1%** unique prompts for audit. Never on the admit path. |
| **Ollama / llama.cpp CPU 8B** | Dev | **Avoid.** |

**Honest gap vs today’s Haiku:** PG2 is binary jailbreak/injection, not Haiku’s JSON surface (tool-overreach, toxicity, multilingual paraphrase). LG1B is safety categories, not PII/secrets/exfil (those stay T1). C with this stack is **9 named stages with different T2 backends**, not “Haiku equivalence.” If a tenant needs Haiku-class semantics, they stay on the **slow path** (not 100k).

### 0.3 How can C exist at all? (BYOK vs our GPUs)

**Customer BYOK to public OpenAI/Anthropic is not 100k RPS** unless that account’s contracted TPS is 100k. Live evidence: org **100k TPM** → ~**46 RPS** at ~36 tokens/req (`429 101833/100000`). Enterprise OpenAI is typically thousands of **RPM**, not 100k **requests/second**.

To **prove C** we must run a **platform inference fleet** (or a dedicated customer endpoint with a written TPS):

- LiteLLM `api_base` → same-AZ vLLM/NIM OpenAI-compatible server.
- That is **first-party (or dedicated) inference**, not “every tenant’s OpenAI key does 100k.”
- Product copy must say: *100k completed chats/s on the platform model / dedicated endpoint.* Customer SaaS BYOK remains quota-bound.

---

## 1. Discovered architecture (evidence)

Canonical stages (`gateway/ai_mesh_gateway/pipeline_trace.py`):

`auth → rate_limit → policy → input_scan → kill_switch → model_routing → model_input → model_output → output_guardrail`

Wall-clock in `proxy_chat` differs: middleware auth → kill-switch Redis → **unmetered model_state Redis** → rate_limit → policy → input_scan → routing → `LLM_ROUTER.acompletion` → `OUTPUT_GUARD.inspect`.

| Stage | Live cost | Blocks C how |
|---|---|---|
| auth | Redis GET ~1 ms; p99 blows up when ElastiCache saturates | 503 under load |
| kill_switch | 2–3 GET, fail-closed, **no RAM cache** | ~13 ms p50 shared Redis on every chat |
| rate_limit | serial burst+RPM MULTI; TPM Lua **not** gated on `rate_limit_enabled` | ~32 ms p50; **100k TPM ≈ 46 RPS** |
| policy | in-process `POLICY_SYNC` | CPU ~2–18 ms |
| input_scan T1 | Python regex + G33/G53 | ~70–105 ms → **~85 RPS / c8g.2xlarge** |
| input_scan T2 | Haiku Converse | **~1.0–1.6 s** |
| model_output | LiteLLM BYOK or `GATEWAY_LOADTEST_STUB_LLM` | stub ~0 (`id=chatcmpl-loadtest-stub`, `usage.total_tokens=9`); live **~0.65–1.4 s** |
| output T1 | PII/exfil in `SecureStreamingResponse` 4 KB buffer | already chunked; not whole-completion |
| output T2 | second Haiku; **fail-open** on error | **~0.9–1.3 s**; sidecar saturation today **skips** T2 |

`GUARDRAILS_SERVICE_URL=http://guardrails:8310` is injected and **never read**. Scaffold `services/guardrails` always `allow`.

There is **no 6-stage code path**. Benches skip work (`latency_ms=0`) on the same handler. `honesty.full_nine_stages = (MODE == "chat")` is a **lie**.

Streaming already holds the SSE until `[DONE]`. Llama Guard at DONE does **not** free the gateway FD during generation. Little’s Law still uses **full generation latency**.

---

## 2. Current behavior (measured)

Evidence dirs: `mcp-parallel/findings/nlb-shared-redis-2026-08-21/`, `nlb-6stage-100k-2026-08-21/`, `nlb-100k-2026-08-21/`, `code-vs-aws-isolation-bench-2026-08-17/`. Ruflo: `full-vm-peak-9stage-2026-08-14`, `local-gpu-oss-guardrails-2026-08-18`.

| Experiment | ok_rps | Truth |
|---|---:|---|
| NLB `/health` @768 | **11,011** | HTTP stack only |
| 6-stage stub, shared ElastiCache, NLB | **1,214** | Redis hot-path wall |
| 6-stage stub, 3 isolated IPs **summed** | **9,839** | **~3.2k/host**, scans off, local Redis — not one box, not 9-stage |
| 6-stage @100k inflight, 3 hosts summed | **3,408** | Inflight collapse |
| Unique 9-stage T2+BYOK+guard | **~8–40** | Fleet-wide, Bedrock-bound |
| T1-only unique, one c8g.2xlarge | **~85** | Next ceiling after Bedrock |
| 100k-inflight 9-stage retry | **41** ok, **98% errors** | TPM 429, 422 `no_provider_configured`, ConnectTimeout |
| Health @100k inflight | **~138** ok, **73% ConnectTimeout** | NLB SNAT ~55k conn/target |

GCP 16-core loadgen **cannot emit 100k RPS**. Quote **`ok_rps`**, never inflight, never 3-IP sums.

---

## 3. Physics for C (Little’s Law + GPUs)

### 3.1 In-flight

`N = R × T`

| Generation p50 | In-flight completions at 100k RPS |
|---|---|
| 50 ms (tiny 8B, short max_tokens) | 5,000 |
| 200 ms | 20,000 |
| 800 ms (live BYOK band) | **80,000** |
| 3.2 s (today unique 9-stage wall) | 320,000 |

Each in-flight chat holds: client FD + gateway worker slot + LiteLLM + upstream generation slot + (if stream) SSE buffer. `GUNICORN_WORKER_CONNECTIONS` default 20,000. NLB without `preserve_client_ip` ≈ 55k conn/target.

**Classifier latency is not C.** Swapping Haiku for 10 ms PG2 leaves the 0.8 s generation hold. C is an **inference-concurrency** SLO with a **firewall tax**.

### 3.2 Inference fleet (canonical Llama 3.1 8B FP8)

Published 2026 benches (Spheron / RunInfra / inferenceengineering; unique prompts, H100 80GB):

- Llama-8B vLLM saturation ~**5.3k–12k output tok/s per H100** (depends on prefill mix).
- Llama-70B ~**1.8–2.8k tok/s per H100** (TP=1 FP8) — **wrong size for C**.

Token math (must pick a **token SLO**, not just RPS):

| Output tokens / completed chat | Token/s at 100k RPS | H100 count at 12k tok/s | H100 count at 5k tok/s |
|---|---|---|---|
| 16 (bench `max_tokens`) | 1.6M | ~130 | ~320 |
| 100 (short customer chat) | 10M | ~830 | **~2000** |
| 256 | 25.6M | ~2100 | ~5100 |

**150 H100 is a false floor** for 100-token chats (would require ~67k tok/s per GPU). Groq/Cerebras **per-stream** tok/s (hundreds–thousands) is not fleet RPS; public tiers have RPM caps. Use them only with a **written 100k TPS contract**, or self-host.

Cost order: hundreds of H100s on-demand is **millions USD/month**. Idle topology (1 original c8g + ASG extras 0) does **not** pay this bill. C is a **GPU factory** plus a **firewall overlay**.

### 3.3 Guard GPU fleet (PG2 + LG1B)

Prior session (`hotpath-22m-100k-inflight-2026-08-18`): PG2 22M overlapping 512-token windows — CPU ONNX first; **1–2 L4 ~3k RPS**; **4–8 L4 ~20k**; **15–40 L4 at 100k classify/s**. LG1B is heavier: separate pool, batch, HPA. **Do not pack onto gateway RAM.**

Input + output classify at 100k chats = **~200k classify/s** if both run on every allow. Window/batch or you double the L4 count.

### 3.4 Gateway CPU fleet

`/health` ceiling ~**8–11k RPS / c8g.2xlarge**. After T1 is 1–3 ms: **20–40 c8g** for 100k HTTP+JSON+TLS. If T1 stays 70 ms: **~900+ boxes**. T1 rewrite is **mandatory for C**, not optional polish.

---

## 4. Suspected issues (ranked)

| # | Issue | Conf | Evidence |
|---|---|---|---|
| 1 | Two serial Haiku calls make completed 9-stage ~8–40 ok_rps | High | isolation + NLB 9-stage soaks |
| 2 | Output guard **after** `acompletion` → Little’s Law uses generation time | High | `main.py` ~9803–9921 |
| 3 | Default 100k TPM / 1000 RPM | High | live 429 `101833/100000` |
| 4 | T1 Python ~85 RPS/box | High | Aug 17 isolation C |
| 5 | Shared ElastiCache 6–9 serial RTTs | High | health 11k vs stub chat 1.2k |
| 6 | Output T2 **fail-open** → sidecar saturation **increases** ok_rps and drops semantics | High | `scanner.py` / `output_guard.py` |
| 7 | Catalog last-good per-worker → 422 `no_provider_configured` | High | 1105× in 100k-inflight retry |
| 8 | T2 cache key = org+text only (no `policy_version` / `model_hash`) | High | `scanner.py` ~2079 |
| 9 | NLB SNAT + GCP loadgen | High | health-100k ConnectTimeout |
| 10 | Observability lie `full_nine_stages` | High | `gateway_pipeline_bench.py` L351–356 |
| 11 | Mumbai GPU quota for p5/g6e | Med | procurement, not code |
| 12 | Shared vLLM prefix cache without `cache_salt` | Med | not in tree yet; must design in |

---

## 5. Target architecture

```
 clients ──TLS──► NLB :443 (preserve_client_ip ON)
                     │ TCP 8300
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   gateway ASG   gateway ASG   original monolith
   c8g T1+admit  c8g T1+admit  UI / control / Postgres
        │            │
        │ local GCRA burst/RPM
        │ RAM policy bundle
        └────────────┼────────────┘
                     ▼
              ElastiCache Redis 7
              auth GET or ≤50ms cache+pubsub (Redis down = disable)
              KS GET fail-closed
              TPM quota-lease INCRBY (not N local buckets)
                     │
          T1-allow   ▼
              Guard sidecar ASG (g6e/L4)
              PG2 22M input  +  LG1B output at DONE
              digest-pinned; fail-CLOSED if enforcement_mode=block
                     │
                     ▼
              Inference fleet (same AZ)
              vLLM / TensorRT-LLM / NIM  Llama-3.1-8B FP8
              LiteLLM api_base  (platform key or dedicated customer key)
              prefix cache: cache_salt=org  or per-org engine
                     │
                     ▼
              optional: Groq/Cerebras/Fireworks dedicated TPS contract
```

Idle: original `i-07a9d65b103ac1a87` always in TG; gateway ASG extras **min 0**. Inference/guard ASGs are **separate** and do not scale to zero if C is a live SLO (or C is only a scheduled peak with warm pool).

---

## 6. Devil’s advocate (survived 5 review agents)

**Rejected as false or incomplete:**

- “PG2+LG1B ≡ Haiku T2.” Binary/taxonomy models are not the JSON classifier. Keep Haiku on async sample.
- “Same-AZ NIM = customer BYOK.” BYOK in this repo is decrypted **tenant** keys (`api_key_encrypted`). Platform NIM is a different product.
- “150 H100 floor for 8B @ 100 tokens.” Needs ~10M tok/s; 150 GPUs is fantasy unless tokens/chat ≈ 16 **and** 12k tok/s/GPU.
- “Keep Python T1 and hit C.” ~85 RPS/box. Rewrite is mandatory.
- “T1 on chunks already solves output T2.” Current 4 KB `inspect()` + Haiku on flush is the wrong window for LG1B. LG1B at `[DONE]` only. T1 stays on chunks.
- “Fail-closed sidecar at 100k is free.” Saturation → 403 storm **or** fail-open leak. Must HPA the sidecar **and** shed with a visible `output_scan_degraded` metric that **fails the C gate** (ok_rps during shed is not C).
- “ApplyGuardrail / promptfoo / llm-guard suite / NeMo will do 100k.” Quota, CI-only, or latency.
- “Sit the LLM on the gateway to save a hop.” Workers already wait on sockets.

**Accepted as fatal if ignored:**

1. Generation hold time (Little’s Law).
2. TPM unit is tokens/min, not requests/s.
3. Customer OpenAI quota ≠ our RPS.
4. Mumbai GPU procurement.
5. Fail-open T2 under load green-washes C.
6. Bench lies (`MODE==chat`, stub id, unique prompt default off, stream first-byte, 3-IP merge, inflight=100k).

---

## 7. Proposed work — do not start until confirmed

Prerequisite skills when executing: `@skill-test-driven-development` `@skill-verification-before-completion` `@skill-defense-in-depth` `@production-live-verification`.

### Phase 0 — Honesty (1–2 days) — still required for C

**Files:** `scripts/perf/gateway_pipeline_bench.py`, `frontend/src/utils/liveGateway.js`, `gateway/scripts/pipeline_9stage_verify.py`, `gateway/ai_mesh_gateway/main.py` (chat body cap).

- `honesty.full_nine_stages` = measured: `input_scan.p50>0` AND `output_guardrail.p50>0` AND sampled `id != chatcmpl-loadtest-stub`.
- UI: 0 ms stage = skipped.
- Chat body cap before `request.json()` (MCP already has `_mcp_read_body_capped`).
- Meter `model_state_ms`.

**Verify:** 6-stage JSON must have `full_nine_stages: false`. Stub soak must fail the C predicate.

### Phase 1 — Redis two-tier (correctness-preserving)

**Files:** `gateway/ai_mesh_gateway/main.py`, `rate_limiter.py`, `kill_switch.py`, `middleware.py`, `circuit_breaker.py`.

- Local GCRA burst/RPM.
- Org/key TPM: Redis **lease** `INCRBY` chunk (not per-request EVAL; not N independent buckets).
- Auth/KS: optional ≤50 ms cache + pub/sub; Redis error still **fail-closed**.
- Circuit breaker CLOSED: skip Redis GET.
- Policy/config: 1–2 s version heartbeat.

**Verify:** stub chat through **shared** ElastiCache approaches ~3k/host; two orgs cannot steal TPM; KS SET → ≤100 ms disable on original **and** an ASG clone.

### Phase 2 — T1 engine (do not drop G33)

**Files:** `gateway/ai_mesh_gateway/scanner.py`, `patterns.py`, `output_guard.py`; new `gateway/ai_mesh_gateway/t1_engine/` (Rust/Vectorscan FFI).

- One canonicalize + decode budget, then multi-pattern.
- Keep G33/G53/byte-verify fail-closed.
- Target p50 **&lt; 3 ms** unique 2 KB prompt on Graviton3.
- Python T1 remains **oracle** in tests; disagree → fail closed.

**Verify:** `test_pipeline_obfuscation_fp.py`, G33/G53, `gateway/tests/leakhunt`; microbench 10k unique prompts; T1-only unique chat ≫ 85 RPS/box.

### Phase 3 — Guard sidecar (replaces sync Haiku)

**Files:** `gateway/ai_mesh_gateway/scanner.py`, `output_guard.py`, `services/guardrails/` (replace always-allow stub), compose/terraform `GUARDRAILS_SERVICE_URL`.

- Wire the unused URL. Pin image digest + `model_hash`.
- Input: PG2 22M (bake-off DeBERTa v2). Output: LG1B at `[DONE]` only; T1 remains on stream chunks.
- T2 cache key: `org + text + policy_version + model_hash`. Unify TTL (compose 0 vs terraform 300).
- `enforcement_mode=block` + sidecar 5xx/timeout → **403/451**, not allow.
- `enforcement_mode=monitor` may fail-open **but C gate must fail** if `tier2_degraded_rate > 0.001`.
- Haiku async sample 0.1–1% to a queue (audit), not inline.

**Verify:** paraphrased jailbreak T1 misses → sidecar block; sidecar kill → block-mode 403, not 200; digest roll invalidates cache.

### Phase 4 — Inference fleet for C

**New:** GPU ASG or EKS in `ap-south-1` (or same-AZ as NLB). vLLM 0.23+ Llama-3.1-8B-Instruct FP8. TensorRT-LLM later if model is frozen.

**Files:** `gateway/ai_mesh_gateway/llm_router.py` (api_base to fleet; **do not** use `os.environ/NVIDIA_NIM_API_KEY` as the org key); LiteLLM catalog.

- Prefix cache: `cache_salt=<org>` or per-org engine. Never shared prefix across tenants.
- Disable `GATEWAY_LOADTEST_STUB_LLM` on the C key.
- Capacity-profile TPM/RPM for the **bench org only** so 100k TPM is not the ceiling (216M TPM at 36 tok × 100k RPS).
- Catalog last-good: empty `routing=[]` must not 422; do not inherit `default` org catalog on chat (`get_own_config`).

**Verify:** `id` not stub; `usage.completion_tokens >= 1` and not the 8+1+9 fingerprint; `nvidia-smi` util p50 ≥ 20 on the fleet during the window (or cloud dedicated TPS invoice).

### Phase 5 — Infra + loadgen

- NLB `preserve_client_ip` (or extra target ports).
- nginx keepalive to gunicorn; C also through gzipping vhost (`Accept-Encoding: gzip` and identity).
- Gateway ASG max 20–40 c8g; extras min 0; original always in TG.
- **In-region loadgen ASG** running `gateway_pipeline_bench.py`. Do not generate 100k from the GCP workstation.
- HTTP/1.1 NLB:8300 **and** HTTP/2 via nginx both measured.
- Stream C: drain to `[DONE]`, not first byte.

**Verify:** `/health` ≥ 80k ok_rps (LB+HTTP). Then T1-on unique. Then full C predicate below. Repeat **3×**.

---

## 8. C acceptance (mandatory — not unit tests)

Sample **N=200** HTTP 200 bodies from the **same** soak as the RPS number. `UNIQUE_PROMPT=1`, path `/v1/chat/completions`. Empty sample = fail.

```
C = (
  mode == "chat"
  and ok_rps >= 100_000
  and error_rate <= 0.001
  and codes[429] == 0 and codes[422] == 0 and codes[503] == 0
  and input_scan.p50 > 0
  and output_guardrail.p50 > 0
  and model_output.p50 >= 50          # real generation; stub/6-stage is 0.0
  and all(id != "chatcmpl-loadtest-stub"
          and usage.total_tokens != 9
          and content != "ok" for body in sample_200)
  and completion_tok_s ≈ ok_rps * mean(completion_tokens)
  and tpm_ceiling_rps >= ok_rps       # limiter cannot be the hidden cap
  and sidecar_degraded_rate <= 0.001  # fail-open T2 is not C
  and loadgen_in_region
  and not summed_across_nlb_ips
  and (stream implies drained_to_[DONE])
)
```

Also: KS live ≤100 ms; two ASG members cannot admit `2 × org_tpm`; G33/G53 fixtures still fire under load; client p50 reconcilable with `pipeline_trace.total_latency_ms` (no 12 s client vs 39 ms trace).

**Not C:** `/health`, 6-stage stub, 7-stage scans-off, 100k **inflight**, 3-IP merge, long-window “100k completions in 10 minutes,” monitor-mode fail-open, breaker-open skip T2, first-byte SSE.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Mumbai p5/g6e quota = 0 | Request quota **before** Phase 4; or dedicated Fireworks/Together/Groq TPS in-region; do not pretend C is a gateway PR |
| LG1B at DONE vs stream TTFT | T1 chunks keep fail-closed PII; LG1B after last token; do not buffer 100k full bodies |
| Sidecar 403 storm | HPA + queue depth SLO; C gate fails if degraded |
| T1 rewrite miss | Python oracle tests; fail closed on disagreement |
| Cross-tenant KV cache | `cache_salt` or per-org engine from day 1 |
| Cost | Peak C is scheduled + warm pool; idle extras 0 on **gateway** only |
| Marketing | UI/status must say platform/dedicated model, not “any BYOK key” |

---

## 10. Dependencies

- User **confirms this C plan** (this file). The A/B doc (`docs/plans/2026-08-21-100k-rps-9stage-design.md`) remains the firewall-only overlay; it does **not** supersede C.
- GPU quota `ap-south-1` (guard L4 + inference H100/H200 or equivalent).
- ElastiCache cluster-mode if lease keys stay hot.
- In-region loadgen VPC.
- Bench org TPM/RPM raised **explicitly**.
- Do **not** `scripts/sync-to-ec2.sh --deploy` until Phase 0–2 are green on a non-prod key.

---

## Execution choice (after confirmation)

1. **Subagent-driven (this session)** — one phase at a time, review between phases. Start Phase 0 only.
2. **Parallel session** — `executing-plans` in a worktree.

Do not mark C complete until §8 holds on a live soak, three times. Unit tests are not proof.

---

## 11. HLD/LLD addendum (2026-08-22)

Industry (TrueFoundry, Envoy AI Gateway, Kong, ClickHouse/Kafka) **confirms** three fleets and **rejects** putting CDN cache, PowerDNS, AWS API Gateway, Kafka-in-front-of-the-LLM, or ClickHouse SELECTs on the completed-chat path. TrueFoundry’s 350 RPS/vCPU is a **stub hop** (fake OpenAI), same class as our 6-stage bench — copy in-memory admit + async NATS/ClickHouse logs; **do not** copy generate+cancel injection, skip-output-on-stream, or LiteLLM Proxy as the VIP.

Full verdicts, diagrams, and citations: `docs/plans/2026-08-22-100k-C-hld-lld-proof.md`.
