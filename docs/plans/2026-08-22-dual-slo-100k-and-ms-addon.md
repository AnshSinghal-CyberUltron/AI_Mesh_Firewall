# Dual SLO plan — 100k completed chats/s + millisecond firewall tax

> **Status:** PLAN ONLY. Do not implement until you confirm this document.
> **Supersedes for “what do we actually change”:** the A/B overlay in `2026-08-21-100k-rps-9stage-design.md`. Complements C-SLO (`2026-08-21-100k-completed-chats-C-slo.md`) and HLD proof (`2026-08-22-100k-C-hld-lld-proof.md`).
> **Evidence:** live NLB benches 2026-08-14…21, TrueFoundry published hop (Jan 2026), current `proxy_chat` / `scanner.py` / `pipeline_trace.py`.
> **Addendum (Haiku parity, 512-token windows, Redis/RDS, vectors, MCP/RAG, KS order, GCP CDL):** `docs/plans/2026-08-22-haiku-parity-longprompt-edge.md`.

---

## 0. The two problems are not one number

| SLO | What it is | What it is **not** |
|---|---|---|
| **A. Addon latency** | Time **we** add besides the customer’s model. Industry 3–12 ms (TrueFoundry stub hop +3–4 ms; Kong/Helicone class). | Time-to-first-token of Llama/GPT. That stays hundreds of ms to seconds. |
| **B. 100k RPS** | 100,000 **finished** chats/s through all 9 named stages, real generation, scans not stubbed. | `/health` RPS, stub `chatcmpl-loadtest-stub`, 6-stage with `latency_ms=0`. |

**Law:** a generative LLM-as-judge on the admit path makes **both** SLOs impossible. Haiku Converse today is ~1.0–1.6 s input + ~0.9–1.3 s output. You cannot be “a few milliseconds like other gateways” while awaiting a second LLM before/after the customer LLM.

**Law:** millisecond hop ≠ 100k completed chats. TrueFoundry’s 350 RPS/vCPU is a **fake OpenAI** (same class as our `/health` ~11k and 6-stage stub ~1.2k–3.2k/host). Completed chats at 100k still need a **generation fleet** sized for tokens/s, plus enough gateway sockets for Little’s Law (`in_flight ≈ RPS × generation_seconds`).

We will hit **both** by making the firewall hop industry-class **and** splitting generation onto its own fleet. We will **not** claim Haiku-class multilingual JSON judging at 3 ms.

---

## 1. Proof: why we fail both SLOs today

Canonical stages (`pipeline_trace.py`):  
`auth → rate_limit → policy → input_scan → kill_switch → model_routing → model_input → model_output → output_guardrail`

Wall-clock in `proxy_chat` is worse: middleware auth → kill-switch Redis (no RAM cache) → extra **model_state Redis** → rate_limit (serial MULTI + TPM Lua) → policy → T1 then **Haiku T2** → LiteLLM → output T1 + **Haiku T2 fail-open**.

| What we measured | Number | Which SLO it kills |
|---|---:|---|
| Unique 9-stage + Haiku T2 + BYOK | **~8–40** ok_rps | Both (throughput + seconds of tax) |
| Input T2 Haiku | **1.0–1.6 s** | Addon |
| Output T2 Haiku | **0.9–1.3 s** | Addon (and fail-open under saturation → fake RPS) |
| T1 Python (unique, one c8g.2xlarge) | **~70–105 ms → ~85 RPS** | Both, once Haiku is gone |
| Shared ElastiCache 6-stage stub | **~1,214** RPS | Throughput (Redis wall) |
| Isolated IPs, scans off, 3 hosts **summed** | **~9,839** | Throughput (still not 100k) |
| NLB `/health` | **~11,011** | CPython/nginx ceiling per host |
| Org TPM 100k tokens/min ÷ ~36 tok/req | **~46 RPS** | Throughput (product quota, not CPU) |
| 100k-inflight 9-stage retry | **41** ok_rps, 98% errors (TPM 429, 422, ConnectTimeout) | Both |
| Health @ 100k inflight | **~138** RPS, 73% ConnectTimeout | NLB SNAT ~55k conn/target |
| `GUARDRAILS_SERVICE_URL` | injected, **never read**; scaffold always `allow` | Security theater |
| nginx `keepalive` to gunicorn | **absent** | Extra handshake tax + conn churn |
| Output T2 | **fail-open** on error (`scan_output_with_tier2`) | Security under load |
| Input T2 fail-closed | env **default OFF** | Availability vs evasion |

Industry hop we are comparing to: TrueFoundry **+3–4 ms** vs fake OpenAI, in-memory JWT/RBAC/token-bucket, logs on NATS, **no second LLM** on the hop. Their injection product is generate+cancel / Azure Prompt Shield raced with generation — **we refuse that** for `enforcement_mode=block` (model must not see raw PII/secrets).

---

## 2. Target numbers (honest)

Define:

```
T_addon_pre  = time from accept to first byte toward the model
               (auth + RL + policy + KS + route + input_scan)
T_addon_post = time after last model token until we close the stream
               (output_guardrail at [DONE] + final redact)
T_addon      = T_total − T_model
```

| Metric | Today (unique 9-stage) | Target | Why that target |
|---|---|---|---|
| `T_addon_pre` p50 | ~1.1–1.8 s | **≤ 12 ms** | Industry “few ms” with a **real** serial classifier (~8 ms GPU) + 3 ms T1 + 1 ms admit. Pure 3 ms TrueFoundry hop requires **no serial T2**. |
| `T_addon_pre` p99 | seconds (Redis + Bedrock) | **≤ 40 ms** | Hard timeout on classifier; fail-closed on timeout if tenant is block-mode. |
| `T_addon_post` p50 | ~0.9–1.3 s Haiku | **≤ 8 ms** streaming (T1 on 4 KB chunks only); optional LG1B at `[DONE]` **≤ 40 ms** extra | Industry stream gateways skip output LLM. We keep T1 always; LG1B is the security/latency knob. |
| Completed chats ok_rps | 8–40 | **100,000** | Separate GPU inference fleet + gateway ASG + guard ASG. Not public OpenAI BYOK. |
| Error rate | 98% at 100k inflight | **≤ 0.1%** | |
| Stub / 0 ms scans | benches lie today | **forbidden** in C gate | |

**If you insist on TrueFoundry +3–12 ms including T2:** physics says **no**. Pick one:

| Policy | Pre-model p50 | Semantic jailbreak coverage |
|---|---|---|
| **P0 (recommended)** T1 + always-on GPU classifier serial | **~10–15 ms** | Strong (not Haiku JSON) |
| **P-fast** T1 only on clean; GPU T2 only if T1 dirty/ambiguous | **~3–8 ms** on clean | Weaker on paraphrase-only attacks |
| **P-TF** generate+cancel / race T2 with first token | **~3–12 ms** TTFT | Model already saw the prompt — **reject for block-mode** |
| **P-Haiku** keep Converse | **~1.5 s** | Strongest judge, **0 of 2 SLOs** |

Default for this product: **P0**. Tenants who want Haiku stay on a **slow pool**, not the 100k pool.

---

## 3. Architecture — remove / add / keep

```
Client ──HTTP/2──► NLB TLS:443 (preserve_client_ip, no SNAT 55k trap)
                      │
                      ▼
                   nginx (keepalive → gunicorn; buffering off for SSE)
                      │
                      ▼
            ┌──────── GATEWAY CPU ASG (Python admit only) ────────┐
            │  in-memory auth/policy/GCRA after snapshot            │
            │  T1 Vectorscan/Rust (fail-closed PII/secrets/canned)  │
            │  HTTP to Guard sidecar (serial, budgeted)             │
            │  Redis: KS/auth fail-closed + TPM LEASE only          │
            │  httpx/OpenAI-compat to Inference (LiteLLM = config,  │
            │    not a second hop process)                          │
            └────────────┬─────────────────────┬────────────────────┘
                         │                     │
                         ▼                     ▼
              GUARD GPU ASG              INFERENCE GPU/LPU ASG
              PG2 or DeBERTa ONNX        vLLM/TRT-LLM/NIM
              LG1B at [DONE]             same-AZ OpenAI-compat
                         │
                         ▼
              Kafka/Redpanda ──► ClickHouse   (audit/traces)
              Postgres = control plane only
              Haiku = 0.1–1% async sample, never admit
```

### 3.1 REMOVE from the hot path (delete or quarantine)

| Remove | Why | Proof it unlocks |
|---|---|---|
| **Bedrock Haiku Converse as T2** (`scan_prompt_with_tier2` / `scan_output_with_tier2` `aconverse`) | 1–2.6 s tax; Bedrock TPS; fail-open output | 9-stage unique 8–40 RPS vs T1-only ~85 RPS vs stub thousands |
| **AWS ApplyGuardrail as T2** | ~50 TPS default | Cannot be 100k |
| **Serial ElastiCache MULTI for burst/RPM every chat** | 6–9 RTTs; 6-stage stub 1.2k vs health 11k | Local GCRA → hop ms + RPS |
| **TPM Lua as a 100k-token/min product default on the 100k pool** | 100k TPM ≈ **46 RPS** | Quota must be **requests or tokens/s** at C scale, or C is a dedicated pool with a written ceiling |
| **Unmetered `model_state` Redis GET** on every chat | Extra RTT, not a named stage | Cache in worker RAM with short TTL |
| **Kill-switch Redis with no RAM cache** | ~13 ms p50 shared Redis | 50 ms RAM cache, fail-closed refresh |
| **`services/guardrails` always-allow scaffold** | Dead URL, false sense of T2 | Wire or delete the env |
| **LiteLLM Proxy as a second process VIP** | TrueFoundry: ~50 RPS vs their Hono 350/vCPU | Keep LiteLLM **library** for routing table; hot path = pooled httpx to `api_base` |
| **Chat telemetry Redis LIST → PG drain (~250/s)** as SoT | Cannot write 100k rows/s | Kafka/CH async; never block admit |
| **NLB `target_type=ip` SNAT** (55k conn/target) | Health collapsed at 100k inflight | `preserve_client_ip` / instance targets + more targets |
| **CDN / API Gateway / ALB on `/v1/chat`** | 30s, no SSE, 10k RPS class | Stay NLB L4 |
| **promptfoo / llm-guard suite / NeMo Colang / Lakera / Model Armor** on admit | Palit-class hundreds of ms to seconds | CI or shadow only |
| **Ollama/llama.cpp 8B on the gateway box** | Steals CPU from admit | Separate fleet |
| **`--preload` ONNX inside gunicorn workers** | Fork + GPU contention | Sidecar process |
| **Output T2 fail-open that skips Haiku under saturation** | Inflates ok_rps, drops security | New classifier: timeout → **block** (block-mode) or **hold stream** (not skip) |

### 3.2 ADD

| Add | Role |
|---|---|
| **T1 rewrite** Vectorscan or Rust SIMD (`patterns.py` / G33/G53 stay semantically) | p50 ≤ 3 ms, keep byte-verify fail-closed |
| **Guard sidecar** Prompt Guard 2 22M **or** ProtectAI DeBERTa v2 (bake-off, pin digest) | Input T2, p50 ≤ 8 ms GPU, hard deadline 15 ms |
| **Llama Guard 3 1B** sidecar | Output taxonomy at `[DONE]` only, not every 4 KB |
| **Inference fleet** Llama-3.1-8B FP8 vLLM (prove C) or dedicated customer endpoint with **written TPS** | `model_output` |
| **Two-tier Redis** | RAM GCRA + Redis **lease** for TPM/budget (N pods must not multiply quota) |
| **nginx `upstream keepalive`** | Cut handshake tax |
| **Kafka/Redpanda + ClickHouse Kafka engine** | Traces/audit off hop |
| **EKS (or ASG) GPU node groups** | Guard + inference; CPU gateway can stay ASG |
| **In-region loadgens** | Driver GCP 16-core cannot emit 100k |
| **Haiku async sampler** 0.1–1% unique prompts | SOC / regression vs PG2, not SLO B path |

### 3.3 KEEP (do not gut)

| Keep | Why |
|---|---|
| **9 named stages in the trace** | Product/UI contract. Skipped work must show `latency_ms` honesty, not `full_nine_stages = MODE==chat`. |
| **T1 PII/secrets/canned injection/exfil/G33/G53** | Only fail-closed byte-truth we have without an LLM |
| **`enforcement_mode=block` ⇒ scan before `model_input`** | No generate+cancel |
| **Streaming T1 on 4 KB chunks** | Already the right shape for addon-post |
| **Fail-closed kill switch** | Safety |
| **Org isolation / `cache_salt`** | Prefix cache must not leak across tenants |
| **Canonical request id** (PIPELINE-0023) | No header-pinned collision |

---

## 4. Pipeline surgery — stage by stage

Do **not** delete stages from the public 9. Change **what runs inside** and **what is allowed to wait**.

| Stage | Today | Change | Addon budget |
|---|---|---|---|
| **auth** | Redis GET API key | Worker RAM cache of key→org (TTL 1–5 s); Redis miss only. JWKS in RAM. | **0.2–0.5 ms** |
| **rate_limit** | Serial burst+RPM MULTI; TPM Lua always if `org_tpm_limit` set | Local GCRA for burst/RPM. Redis **atomic lease** only for budget/TPM. **100k pool:** set TPM to 0 or to tokens/s that match fleet; do not ship 100k TPM as the C ceiling. | **0.1–0.5 ms** local; lease ~1 ms when it fires |
| **policy** | In-process `POLICY_SYNC` 2–18 ms | Keep RAM bundle. Precompile regex. No Redis on this stage. | **0.5–2 ms** |
| **input_scan** | T1 Python 70–105 ms **then** Haiku 1–1.6 s | (1) T1 Vectorscan ≤ 3 ms, **fail-closed**. (2) HTTP sidecar classifier ≤ 8 ms. Combine: T1 block wins; T2 block wins; T1 redact still applied before model (`test_tier2_preserves_tier1_redact`). Cache key = `org + policy_version + model_hash + text_hash` (today’s cache misses policy version — **fix that**). | **3–12 ms** |
| **kill_switch** | 2–3 Redis GET, no cache | RAM cache ≤ 50 ms; on Redis fail **fail-closed** (keep). | **< 0.1 ms** hit |
| **model_routing** | Adjudicator + Redis catalog; 422 under load | Pin catalog in worker; sticky prefix + `cache_salt=org`. No extra Redis GET on the hot path. | **0.2–1 ms** |
| **model_input** | Pass-through after redact | Unchanged. Must remain **after** T1(+T2). | ~0 |
| **model_output** | LiteLLM → public BYOK or stub | Pooled httpx to **same-AZ** OpenAI-compat. Stub forbidden in C gate. This time is **not** addon. | **not in T_addon** |
| **output_guardrail** | T1 chunks + Haiku fail-open 0.9–1.3 s | T1 on every 4 KB (keep). **Remove Haiku.** Optional LG1B **once** at `[DONE]`. If LG1B over deadline: **do not emit remaining tokens** in block-mode (fail-closed), never skip. | **1–3 ms** T1/chunk; **+0 or +20–40 ms** at DONE |

### 4.1 Steps to remove from `proxy_chat` (order)

1. Delete the Bedrock `aconverse` wait from input and output (keep the function only for the async sampler).
2. Stop calling Redis for burst/RPM (keep Lua module for lease/TPM).
3. Stop unmetered `model_state` GET or cache it.
4. Stop treating `GUARDRAILS_SERVICE_URL` as unused — either HTTP to sidecar or remove the env.
5. Stop output T2 fail-open skip (`return tier1` on exception) as the 100k behavior.
6. Stop bench lie: `honesty.full_nine_stages` must require `input_scan.p50>0` and `output_guardrail.p50>0` and non-stub body.

### 4.2 Steps to add in `proxy_chat` (order)

1. After T1, `POST /classify` to sidecar with 15 ms deadline (input).
2. On T1 redact, still mutate bytes **before** sidecar and before model.
3. After `[DONE]`, optional `POST /output-classify` once.
4. Enqueue audit to Kafka (never await ClickHouse/PG).
5. Record `T_addon_pre` / `T_addon_post` in `pipeline_trace` so UI cannot confuse them with `model_output`.

---

## 5. Tier-2: do not “completely remove”; remove the **LLM judge** from admit

### 5.1 What T2 is today

Not ApplyGuardrail. It is **Haiku as a JSON classifier** after T1. Input can fail-closed only if `GATEWAY_TIER2_INPUT_FAIL_CLOSED` is on (default **off**). Output is **always fail-open**. Sampling (`GATEWAY_TIER2_SAMPLE_RATE`) already skips Bedrock on T1-clean prompts — that is a **security hole** if used to fake latency.

### 5.2 What we lose if we delete T2 entirely

T1 catches: PII, secrets, canned English jailbreaks, obfuscation G33/G53, exfil beacons.  
T1 **does not** catch: paraphrased / multilingual / novel instruction-override with no keyword. That is why T2 exists.

Deleting T2 gets you closer to 3–8 ms and higher RPS. It is **not** “same security.”

### 5.3 Replacement (same *role*, different *engine*)

| Layer | Engine | Fail mode | Latency |
|---|---|---|---|
| T1 | Vectorscan/Rust + existing byte-verify | Fail-closed (noop scrub → block) | ≤ 3 ms |
| T2-in | PG2 22M or DeBERTa v2 ONNX/TRT **sidecar** | Block-mode: timeout → **block**. Monitor-mode: tag + admit. | ≤ 8 ms p50, 15 ms cap |
| T2-out | Llama Guard 3 1B at `[DONE]` | Block-mode: withhold. Never fail-open skip. | ≤ 40 ms after last token |
| T2-judge | Haiku 0.1–1% async | Offline SOC | not on hop |

**Honest gap vs Haiku JSON:** PG2/DeBERTa ≈ jailbreak/injection score. They are **not** tool-overreach / policy-language / multilingual paraphrase judges. Tenants who need that stay on **slow pool + Haiku**. The 100k pool is **T1 + small classifier**, which is how every millisecond-class gateway actually works.

### 5.4 How to keep security level *for the 100k pool*

1. **Never** sample-skip T2 on the 100k pool (`SAMPLE_RATE=1.0` for sidecar). Sampling is for Haiku audit only.
2. **Never** fail-open output classifier under load (today’s Haiku skip is how benches lie).
3. T1 redact **before** model always (PII must not wait for a classifier).
4. Cache T2 by `org + policy_version + classifier_digest + text`; wrong key = cross-policy leak.
5. Shadow Haiku vs sidecar for 2 weeks; promote sidecar only if recall on the red-team set is within the agreed band (document the misses — do not pretend equality).
6. Ambiguous T1 (redact/monitor) **always** runs T2; do not skip.

### 5.5 Optional fast path (only if P0 misses 12 ms)

**P-fast:** if T1 is clean **and** prompt is ASCII short **and** tenant opted in, skip sidecar. Default **off** for firewall tenants. This is the only way to print TrueFoundry-like 3–8 ms **and** keep a classifier for the rest.

---

## 6. Proof matrix — each change → which SLO

| Change | SLO A (ms tax) | SLO B (100k) |
|---|---|---|
| Kill Haiku on hop | **Necessary.** Removes ~2 s. | **Necessary.** Removes Bedrock TPS wall. |
| T1 Vectorscan | **Necessary.** 85 ms → 3 ms, else hop cannot be industry-class. | **Necessary.** 85 RPS/box is a fleet of **1,176** c8g at 100k; 3 ms T1 → hundreds of RPS/box, fleet shrinks to tens. |
| Local GCRA + KS RAM | **Necessary.** Redis 13–32 ms is already over a 12 ms budget. | **Necessary.** Shared Redis stub 1.2k vs health 11k. |
| TPM not 100k tokens/min | Indirect | **Necessary.** Else hard cap ~46 RPS. |
| GPU classifier sidecar | Adds ~8 ms (still ms-class) | **Necessary** if we refuse “T1-only security.” Scales with L4/L40S ASG, not Bedrock. |
| Inference fleet | Does not reduce T_addon | **Necessary.** Public BYOK ≠ 100k. 100k chats × 100 out tok = **10M tok/s** → on the order of **10²–10³ H100-class** (not a 150 floor). Prove C with 8B FP8 first at whatever TPS the box has, then scale. |
| nginx keepalive + NLB preserve_client_ip | 1–5 ms + fewer timeouts | **Necessary** at high inflight (55k SNAT). |
| Kafka not PG drain | Not on hop | **Necessary** for audit at 100k writes/s. |
| Keep 9 stage **names** | Neutral | Neutral (honesty in timers). |
| Generate+cancel | Makes A easier | Does not create B; **fails security**. |
| Stub LLM / 0 ms scans | Fake A | Fake B. Forbidden. |

**Little’s Law (SLO B):** 100k RPS × 0.8 s generation ≈ **80,000** in-flight streams. Gateway FDs, NLB targets, and GPU `num_requests_waiting` must be sized for that. Millisecond admit does **not** shrink in-flight; only **shorter generation** does.

**CPython ceiling (SLO B):** `/health` ~11k RPS/host. Even an empty FastAPI chat will be lower. Plan **20–40 c8g.2xlarge** (or more smaller) for admit after T1 rewrite — not one box.

---

## 7. Capacity (order of magnitude, not a PO)

| Fleet | Role | Rough size at 100k |
|---|---|---|
| Gateway CPU | Admit + T1 + HTTP to guard | **20–40** c8g.2xlarge after rewrite (measure; health 11k is upper bound) |
| Guard GPU | 100k classify/s in + 100k at DONE out | **15–40** L4-class (bake-off QPS; ProtectAI table is chars/QPS, remeasure) |
| Inference | Tokens/s | **Prove** on 8B FP8 in-AZ; **scale** to tok/s contract. 16-token tiny replies ≠ production 100-out-token. |
| Redis | Lease + KS | ElastiCache cluster mode; not one node as SoT for 100k GCRA |
| Kafka/CH | Audit | 12–32 partitions; not on hop |
| Loadgen | Emit 100k | Same AZ as NLB, many senders |

**BYOK:** customer OpenAI/Anthropic keys stay **quota-bound**. Product copy: *100k on platform/dedicated endpoint.* Do not relabel NIM as customer BYOK.

---

## 8. Phases (each phase has a proof; stop if proof fails)

**Phase 0 — Honesty (days, no GPU)**  
Fix bench: stub fingerprint fail; `full_nine_stages` requires real scan timers. Split `T_addon_*` in traces.  
**Gate:** cannot certify C with stub.

**Phase 1 — Kill hop waits that are not security (days)**  
KS RAM cache; drop model_state GET; nginx keepalive; local GCRA; TPM off the 100k test org.  
**Gate:** 6-stage **non-stub** hop p50 **< 15 ms** on isolated host (still T1 Python). RPS/host ≫ 1.2k.

**Phase 2 — T1 rewrite (the 85 RPS wall)**  
Vectorscan/Rust; G33/G53 stay; byte-verify tests stay green.  
**Gate:** unique T1-only p50 **< 3 ms**; RPS/c8g **≫ 85** (expect thousands if not generation-bound).

**Phase 3 — Replace Haiku with sidecar (security-preserving)**  
Bake-off PG2 vs DeBERTa; pin digest; fail-closed timeout; cache key includes policy version. Haiku → async 1%. Delete always-allow scaffold.  
**Gate:** `T_addon_pre` p50 **≤ 12 ms** with classifier on; red-team recall documented vs Haiku (gap list, not “equal”). Output: T1-only stream **≤ 8 ms** post; LG1B optional.

**Phase 4 — Inference fleet + NLB**  
vLLM 8B FP8 same AZ; `preserve_client_ip`; more targets.  
**Gate:** non-stub 9-stage, `model_output.p50 ≥ 50 ms`, error ≤ 0.1%, scale ok_rps toward C as GPUs add. **Do not** claim 100k until loadgen is in-region and tok/s matches.

**Phase 5 — 100k**  
Horizontal gateway + guard + inference; Kafka audit; in-region 100k emit.  
**Gate (3× live):** `ok_rps ≥ 100000`, error ≤ 0.001, non-stub, both scan p50 > 0, sidecar degraded ≤ 0.001, streams to `[DONE]`, `T_addon_pre` p50 ≤ 12 ms.

If Phase 3 misses 12 ms: enable **P-fast** for clean T1 only, keep P0 for default tenants.

---

## 9. Security vs industry (no theater)

| Control | Industry ms gateway | Us after this plan |
|---|---|---|
| PII/secrets before model | Often mutate or skip | **T1 mutate, fail-closed** (stronger) |
| Jailbreak | Regex, optional small model, or race with generate | **Serial small model** (slightly slower hop, fail-closed) |
| Output LLM judge | Often **off** on `stream:true` | T1 always; LG1B at DONE (stronger than TF stream) |
| Haiku-class JSON | Nobody does this at 3 ms | **Slow pool only** |
| Audit | Async queue, fail-open logs | Same; **do not** call fail-open logs “enforced” |
| Rate limit | In-memory (N× quota bug) | In-memory burst + **Redis lease** for money/TPM |

We are **not** matching TrueFoundry’s security. We are matching their **hop physics** while keeping a firewall’s fail-closed T1 and a **small** T2.

---

## 10. Explicit non-goals / lies we will not ship

- One c8g.2xlarge does 100k chats.
- Public BYOK OpenAI is 100k RPS.
- `T_addon` includes model generation.
- Skipping T2 + calling it “same security.”
- Generate+cancel as block-mode.
- ApplyGuardrail / Model Armor as the 100k classifier.
- Putting ONNX or vLLM inside gunicorn.
- CDN on `/v1`.
- Kafka ack-before-generate.
- Bench `full_nine_stages` because `MODE==chat`.

---

## 11. Decision required from you

1. **P0** (12 ms p50, always-on GPU T2) vs **P-fast** (3–8 ms clean, weaker paraphrase catch).
2. **LG1B at DONE** (extra 20–40 ms after last token) vs **T1-only output** on the 100k pool (closer to industry stream).
3. Confirm **100k is platform/dedicated inference**, not every tenant’s OpenAI key.

No code until those three are locked.
