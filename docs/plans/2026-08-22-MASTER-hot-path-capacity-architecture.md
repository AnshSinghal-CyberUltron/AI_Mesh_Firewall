# MASTER PLAN — Hot-path latency, in-flight capacity, and target architecture

> **Status:** PLAN ONLY. **Canonical source of truth** for hot-path / capacity work (adopted 2026-08-22). CLAUDE.md Phases 1–4 complete. No code until this document is confirmed for execution.
> **Overlays:** §11 (product lock, Phase-1 gate correction, review-agent gaps). The Cursor plan `canonical_fast_gateway` is a pointer here — do not implement from it.
> **Supersedes the *framing*** of the other nine files in `docs/plans/`; it does not delete their task lists.
> **Date:** 2026-08-22 · **Branch:** `ansh` (HEAD `9ee714f4`)
> **Evidence base:** 6 read-only codebase agents (incl. two first-ever in-process T1 profiles), 12 internet-research agents (~230 primary sources, AWS Price List API pulled live for ap-south-1), and the repo's own bench corpus `mcp-parallel/findings/*`.
> **Full agent reports:** `scratchpad/codebase/C1-hotpath.md`, `C2-infra.md`, `C3-evidence.md`, `C4-data.md`, `C5-scan.md`, `C6-admit.md`; `scratchpad/research/R1..R12*.md`.

Provenance tags used throughout: **[M]** measured in this session · **[R]** measured in this repo's bench corpus · **[V]** vendor-published · **[I]** independent third party · **[D]** derived arithmetic (assumptions stated) · **[NF]** searched, not found.

---

## 0. Executive summary

**The nine existing plans diagnose the right symptoms and mis-attribute three of the four root causes.** The corrections change what we build first, and they make the headline goal cheaper than any plan assumed.

| # | What the plans say | What the evidence says | Consequence |
|---|---|---|---|
| 1 | "Tier-1 is 70–105 ms of Python **regex** → rewrite in Vectorscan/Rust" | T1 is **97 % `difflib.SequenceMatcher`**: `_segment_token` 73.8 ms + `_fuzzy_scan` 21.8 ms of a 102 ms scan; **every regex pass together is < 3 %** (0.77 ms attack + 1.7 ms PII) **[M, C5]** | Vectorscan alone buys ~3 %. The 3 ms target needs the **DP segmenter and fuzzy matcher replaced**, then Vectorscan for the rest. Both are in scope; the order is inverted from every plan. |
| 2 | "The box is idle; it waits on Bedrock" (`2026-08-14`) | In-wave `docker stats`: **590–765 % of 800 %** CPU at ~30 live 9-stage RPS **[R]**; the "1.6 %" was a cooldown snapshot | We are **CPU-bound**, not just I/O-bound. Adding async without cutting CPU adds nothing. |
| 3 | "6–9 Redis RTTs cause rate_limit p50 = 31.7 ms" | AWS same-AZ pooled Redis is **0.21 ms/command** [V]; 9 RTTs ≈ 1.5–3 ms. The other ~29 ms is **client-side**: event-loop queueing, full-text `EVAL` instead of `EVALSHA`, and a saturated shared pool **[R8, C6]** | A bigger ElastiCache node fixes nothing. Fix the client, then reduce RTTs to **one `EVALSHA`**. |
| 4 | *(absent from all nine plans)* | **The gateway logger is forced to DEBUG at startup and every log record performs a synchronous Redis `PUBLISH` from the event loop** — ≈**17 blocking round-trips per request** (`main.py:5987-5999`, `redis_log_handler.py:98-100`) **[C1]** | This is very likely the single largest serialisation point on the hot path and it is a **five-line fix**. |
| 5 | *(absent)* | Every chat builds a **~52 KB** telemetry event that embeds the prompt **25×** and the response **10×**, via ~20 `redact_all` regex passes **on the event loop** **[M, C4]** | Observability is a top-3 CPU consumer on the request path. |
| 6 | "Output Tier-2 runs once (optionally at `[DONE]`)" | Output Tier-2 Bedrock runs **per flush** — a 20-sentence answer makes **~20 Converse calls** **[C1, C5]** | Streaming is far more expensive than any plan modelled. |
| 7 | "100 k in-flight collapsed → NLB is the wrong LB / needs a fleet" | `preserve_client_ip` is **disabled by default for IP/TCP target groups**, capping each NLB-IP × target pair at **~55 000 connections** [V, R6] | The 73 % ConnectTimeout is a **one-line Terraform change**, not an architecture problem. |
| 8 | "100 k completed chats/s (SLO C) is the locked SLO" | 100 k completed 8B chats at 100 output tokens ≈ **1 447 H100** ≈ **181 × p5.48xlarge** ≈ **$4.99 M/month** on Capacity Blocks, **$164 k per proof-day**; Bedrock Mumbai is 600× short on quota with **zero** Provisioned-Throughput SKUs **[D from V, R7]** | SLO C is a **procurement decision**, not an engineering one. It must be separated from the firewall's own SLOs or it will block all work indefinitely. |

**The reframing.** "100 k RPS" is three different promises. We lock three separately provable SLOs:

| SLO | Definition | Who bounds it | Status |
|---|---|---|---|
| **F — Firewall tax** | `T_total − T_upstream`, p50/p99, with all guards ON and a **token-emitting** stub upstream | Our code | **Target: ≤ 12 ms p50 / ≤ 40 ms p99.** Today ≈ 2.2–3.0 s. |
| **G — Gateway capacity** | Concurrent in-flight requests and admitted RPS per host and per fleet, guards ON, error ≤ 0.1 % | Our code + AWS network config | **Target: ≥ 15 k in-flight/host, ≥ 4 k admitted RPS/host.** Today ≈ 85 RPS/host (T1-bound) and 55 k conn/NLB-pair (SNAT-bound). |
| **C — Completed chats** | End-to-end completions/s with a real generation | **The GPU fleet or the tenant's provider quota** — not the gateway | **Quote, never promise.** 100 k = $3.5–8.7 M/month of H100s [R7]. |

Only **F** and **G** are engineering targets. **C** is a price list. Every plan that fused them (`2026-08-21-100k-completed-chats-C-slo.md`, `2026-08-22-dual-slo-*.md`) inherited an unfundable blocker.

**The headline claim we can make after Phase 2 of this plan, honestly:** *"AI Mesh Firewall adds ~10 ms p50 to a chat completion while running fail-closed PII/secret redaction, canned-attack blocking, and an always-on semantic jailbreak classifier — comparable to TrueFoundry's published +3–5 ms hop, which does authentication and routing only, with no security scanning at all"* [V, R1].

**Cost of the target state at today's traffic:** ≈ **$1.4 k/month** incremental (Valkey cluster $1.07 k + MSK Standard $0.32 k + ClickHouse node $0.4 k, minus the retired Mongo pilot), with **zero GPU spend** until sustained classification exceeds ~150 classify/s per host. At 20 k RPS the GPU guard pool adds ≈ **$16 k/month** (9 × g6e.xlarge) [D from V, R3].

---

## 1. Audit of the nine existing plans

| Plan | Verdict | Keep | Correct or drop |
|---|---|---|---|
| `2026-08-14-bedrock-hotpath-rps-latency.md` | **Largely superseded.** Its central lever (per-worker boto singleton, native async, pool caps) **already landed** — `bedrock_client.py` has `_WORKER_CLIENT`, `aconverse`, and nofile-derived pools [C1] | The honesty gates (unique prompts, `ran_inference`, phase timings), the "no artificial code caps" principle, the Little's-Law table | "The box is idle" (false, §0 row 2). "20 k in-flight on one 8-vCPU box" is not reachable while one request costs ~100 ms of CPU. |
| `2026-08-14-burst-test-debug.md` | **Valid, unfinished, small.** Root causes still present: silent UI clamp, scan-only still infers, global `circuit:state:{model}` [C6] | All 8 tasks | Nothing wrong; it is simply low-priority relative to the hot path. Fold Task 7 (org-scoped breaker keys) into this plan's D6. |
| `2026-08-17-nginx-security-headers.md` | **Landed** in `9ee714f4`; working tree adds `deny-public-openapi.inc` [C2] | — | **New finding:** nginx is **not in the public gateway path** — NLB TLS:443 goes straight to gunicorn:8300 [C2]. Every header/`default_server` control therefore protects the UI/API vhosts only. `/v1/*` responses get **none** of them. Fix in D11. |
| `2026-08-18-deterministic-model-routing.md` | **Shipped and proven** (4 537 tests, 126/126 E2E, 1 771-vector fairness sweep). Removed the 3.5 s adjudicator — the single biggest latency win to date | Everything | Its `_merge_org_into_router` fix (the 422 cure) is **on `ansh` only, not on `origin/main`** [C4]. Ship it. |
| `2026-08-21-100k-rps-9stage-design.md` | **Superseded framing, sound content.** Its A/B/C taxonomy is the right instinct | The A/B/C split, the "no LLM on the admit path" law, the fleet math | Its T1 diagnosis is wrong (§0 row 1); its Redis diagnosis is half-wrong (§0 row 3). |
| `2026-08-21-100k-completed-chats-C-slo.md` | **Do not execute as written.** Locks an SLO whose floor is $3.5 M/month | The C acceptance predicate (§8) — it is the best anti-self-deception device in the repo | Un-lock C as *the* SLO. Re-scope to "the firewall must not be the bottleneck below N RPS." |
| `2026-08-22-100k-C-hld-lld-proof.md` | **Correct and useful.** Its component verdicts (no CDN/API-GW/Kafka on the generation path) match this research independently | All component verdicts | TrueFoundry's 350 RPS/vCPU is a mock-upstream number — already flagged; R1 confirms with the vendor's own methodology. |
| `2026-08-22-dual-slo-100k-and-ms-addon.md` | **Closest to right.** P0/P-fast policy framing is retained here | The stage-by-stage budget table, the REMOVE/ADD/KEEP lists, the P0 default | The 12 ms budget assumed T1 ≈ 3 ms via a regex rewrite; correct mechanism, wrong component. Its "≤ 8 ms GPU classifier" is achievable **only** on GPU for the 86M model [R3]. |
| `2026-08-22-haiku-parity-longprompt-edge.md` | **Best security analysis in the set.** Its Haiku-parity gap list is confirmed and *widened* by R2 | The whole §1 layering, the 512-token window recipe, the Memorystore/RDS verdicts, the KS-order finding | Add the three published bypasses it does not name: **Prompt Overflow dilution (100 % bypass of PG2-86M)**, **indirect-injection FNR 0.69–0.97**, **multi-turn ASR > 90 % across all 13 guardrails surveyed** [I, R2]. |

**Two repo-wide facts that no plan recorded and that change deployment risk:**

1. **Terraform has never been applied.** `infra/terraform/envs/prod` designs ECS on **x86 c7i.4xlarge** with an ALB+NLB and RDS/ElastiCache; `certificate_arn = "REPLACE_ME"` [C2]. Production is **one c8g.2xlarge running all 15 containers under docker-compose** [C2]. The IaC and reality have never agreed.
2. **Prod compose over-commits memory:** service limits sum to **22.6 GiB on a 15 GiB host**, gateway alone `mem_limit 12288M` with `WEB_CONCURRENCY=16` on 8 vCPU [C2].

---

## 2. Discovered architecture — what actually runs today

### 2.1 Topology (evidence: C2)

```
                    Cloudflare ──► ALB (TLS, 301) ──► nginx:80 ──┬──► SPA (frontend)
                                                                 ├──► control:8000  (/api)
                                                                 └──► gateway:8300  (UI-originated)

   SDK / API clients ──► NLB TLS:443 ──────────────────────────────► gunicorn:8300   ◄── THE INFERENCE PATH
                          (target_type=ip, preserve_client_ip OFF)      (nginx NOT in this path)

   ONE EC2 c8g.2xlarge  i-07a9d65b103ac1a87  ap-south-1b  8 vCPU Graviton3 / 15 GiB / no swap
   docker compose, 15 containers: gateway(16 workers) control workers beat postgres pgbouncer
   redis(768 MB allkeys-lru, no AUTH/TLS) rabbitmq mcp-broker mcp-sandbox nginx demo
   guardrails(stub) vector-retrieval(stub) telemetry-ingest(stub)
```

### 2.2 Per-request I/O budget, clean allow (evidence: C1 §8)

| Resource | non-stream | stream |
|---|---|---|
| **Awaited Redis RTTs** | **10** sequential | 10 + 1 pipeline + 1 GET per 16 chunks / 1 s |
| **Synchronous Redis `PUBLISH` from the event loop (logging)** | **≈ 17** | ≈ 7 + 7 per flush |
| **HTTP calls** | **3** (Bedrock in, provider, Bedrock out) | **2 + F** (F = flushes ≈ sentences) |
| **Thread-pool hops** | **5** (4-thread scanner pool used 3×) | 2 + 2·F |
| **`redact_all` passes on the loop** | **≈ 20** | ≈ 20, trace built **twice** |
| Postgres | 0 | 0 |
| Control-plane HTTP | 0 | 0 |

### 2.3 Where the 3.0–3.3 s live floor comes from (evidence: C3, C5)

```
 Tier-2 Bedrock input   1.3–1.8 s   ← Haiku Converse, 10 735-char system prompt, max_tokens 1024
 BYOK generation        0.6–1.0 s   ← the customer's model (not our tax)
 Tier-2 Bedrock output  0.8–1.3 s   ← per flush on streams
 Tier-1 CPU               52–105 ms ← 97 % difflib, not regex
 Everything else (auth, RL, policy, KS, routing, trace)  ≤ 13 ms at c=1
```
At concurrency the picture inverts: at 512 in-flight the **auth** stage alone shows p50 = 4 639 ms [R] — pure queueing behind the event loop, not Redis.

### 2.4 Data stores today (evidence: C4)

| Store | Role | On the chat path? |
|---|---|---|
| Redis (single node, 768 MB, `allkeys-lru`, no AUTH/TLS) | auth, kill-switch, model_state, rate limits, circuit breaker, config/policy bundles, **telemetry LIST** | **Yes — 9–12 RTTs** |
| Postgres 16 + PgBouncer | control plane, `EnforcementEvent` (JSONB ≈ event size) | **No** — confirmed by grep |
| Mongo | optional telemetry pilot, **off by default**, perma-disables on first failure | No (and therefore silently dropping operational audit events) |
| Chroma / Pinecone / Milvus | RAG only | No |

---

## 3. Root causes, ranked, with confidence

| # | Root cause | Confidence | Evidence | Cost today | Fix class |
|---|---|---|---|---|---|
| **RC-1** | **Synchronous Redis `PUBLISH` per log record from the event loop**, with the `gateway` logger forced to DEBUG | **High** | `main.py:5987-5999`, `redis_log_handler.py:71-100`; ≈17 RTTs/request [C1] | ~17 ms of event-loop stall/request (at 1 ms/RTT) | 5-line config |
| **RC-2** | **Tier-2 Haiku Converse on the synchronous admit path**, twice (and per-flush on streams) | **High** | 1.3–1.8 s + 0.8–1.3 s [R]; per-flush call site `secure_streaming.py:377` [C1] | ~2.2–3.0 s | Replace engine (D3) |
| **RC-3** | **Tier-1 `difflib` DP segmenter + fuzzy scan** | **High** | `_segment_token` 73.8 ms, `_fuzzy_scan` 21.8 ms of 102 ms; 110 100 `difflib.ratio` calls [M, C5] | 52–105 ms typical, **1 469 ms at 8.8 kB** | Algorithm swap (D10) |
| **RC-4** | **Telemetry/trace built synchronously**, ~52 KB with 25 prompt copies and ~20 `redact_all` passes | **High** | measured with the real serialisers [M, C4] | single-digit ms + GC pressure | Restructure (D8) |
| **RC-5** | **Redis pools raise instead of queue; KS/model_state/CB fail *closed*, limiters fail *open*** | **High** | `redis/asyncio/connection.py:1578-1581`; `kill_switch.py:130`; `rate_limiter.py:116-122` [C1, C6] | 503 storms at ~100 concurrent Redis ops/worker; TPM ceiling vanishes under load | Design (D5, D6) |
| **RC-6** | **NLB SNAT ceiling** — `preserve_client_ip` off by default for IP/TCP | **High** | AWS: "~55 000 connections … for each combination of NLB IP and unique target" [V, R6]; 73 % ConnectTimeout at 100 k in-flight [R] | hard cap ~55 k conn/pair | Terraform one-liner (D11) |
| **RC-7** | **Product quotas**: org burst 150/s, key TPM 100 k/min ⇒ **~46 RPS/tenant** | **High** | live `429 101833/100000` [R]; `middleware.py:164` default [C6] | tenant-visible ceiling far below the fleet's | Product (D6) |
| **RC-8** | **Per-worker state that lies at scale**: Tier-2 breaker (min_calls 5 × 16 workers), Tier-2 cache, `/metrics` (no multiprocess registry) | **High** | `bedrock_tier2_breaker.py:316`, `metrics.py:50-69` [C1] | breaker trips 16× later than configured; metrics under-report 16× | Design (D8) |
| **RC-9** | **4-thread scanner pool per worker used 3× per request**; policy spawns **one OS thread per regex rule per request** | **High** | `config.py:154-155`; `policy_engine.py:273-306` [C1, C6] | queueing latency masquerading as scan latency | Sizing + design (D10) |
| **RC-10** | **Catalogue-empty orgs do a Redis GET + router rebuild *per request*** before returning 422 | **Medium-high** | `main.py:6930-6932` [C1]; 422s at ≥256 in-flight in every environment [R] | amplifier under load | Cache negative result |

---

## 4. Target architecture (HLD)

### 4.1 The law we design to

Every gateway that publishes single-digit-millisecond overhead obeys the same five rules [R1, verified across TrueFoundry, Envoy AI Gateway, Kong, Bifrost, Helicone, LiteLLM-Rust, agentgateway, Portkey, Azure APIM]:

1. **Zero external calls on the hot path** except the upstream model. Keys, tenant config, policy, routing tables, kill-switches live in **process memory**, pushed from a control plane.
2. **At most one atomic shared-state call per request** — and only for the counters that must be globally exact. Nobody does 6–12 round-trips.
3. **Token accounting happens *after* the response** and is charged to the *next* request. Pre-checks use a `len/4` heuristic.
4. **Telemetry leaves through a bounded in-process queue** onto a broker; the request never waits on it, and loss is *counted*, not assumed to be zero.
5. **LLM-based judges are never synchronous by default.** Where they exist they are async, sampled, or raced — never a serial pre-model gate.

AI Mesh violates 1, 2, 4 and 5 today. The target architecture is defined by fixing exactly those.

### 4.2 Target topology

```
                          ┌──────────── CDN (CloudFront) ────────────┐
                          │  SPA + /api only.  NEVER /v1/chat/*      │
                          └──────────────────────────────────────────┘
  SDK / browser
     │ HTTP/2, client keep-alive REQUIRED
     ▼
  Route53 alias ──► NLB TLS:443   preserve_client_ip = TRUE   idle 350 s
     │                 (TLS terminated at nginx instead where handshake cost matters)
     ▼
  nginx (added to the /v1 path)  http2 on · proxy_buffering off · gzip OFF on text/event-stream
                                 upstream { keepalive 512; }  ← absent today
     ▼
 ┌───────────────────────────────────────────────────────────────────────────────┐
 │  GATEWAY CPU FLEET — ECS on EC2, c8g.4xlarge, all 9 stages stay HERE          │
 │                                                                               │
 │  ┌── T0 in-process, 0 RTT ────────────────────────────────────────────────┐   │
 │  │ auth cache (≤30 s, epoch-keyed)                                        │   │
 │  │ admit snapshot: kill-switch + model_state + breaker  (heartbeat 250 ms │   │
 │  │                 + SSUBSCRIBE push; 2 s soft / 10 s hard staleness)     │   │
 │  │ policy bundle (RAM, precompiled)   routing catalogue (RAM)             │   │
 │  │ T1 scan: Aho-Corasick prefilter → Vectorscan DB → `re` verify          │   │
 │  │ PG2-22M INT8 ONNX in-process  (Stage 1 only — see D2)                  │   │
 │  └────────────────────────────────────────────────────────────────────────┘   │
 │  ┌── T1 shared, exactly 1 EVALSHA ────────────────────────────────────────┐   │
 │  │ GCRA burst + RPM + TPM on hash-tagged slot {org:<id>}                  │   │
 │  └────────────────────────────────────────────────────────────────────────┘   │
 │  ┌── async, never awaited ────────────────────────────────────────────────┐   │
 │  │ bounded ring → librdkafka producer → MSK                               │   │
 │  └────────────────────────────────────────────────────────────────────────┘   │
 └───────────────┬─────────────────────────┬─────────────────────┬──────────────┘
                 │ (Stage 2 only)          │                     │
                 ▼                         ▼                     ▼
      GUARD GPU POOL (ECS/EC2 x86)   UPSTREAM MODEL        MSK (Kafka)
      g6e.xlarge L40S, Triton        LiteLLM → BYOK        aim.audit / aim.traces
      PG2-86M FP16 · LG3-1B          or platform vLLM              │
      loopback/VPC gRPC, ≤15 ms deadline                           ▼
                                                            ClickHouse (analytics)
      ElastiCache **Valkey 8.1 cluster-mode**                Postgres = CONTROL ONLY
      3 shards × (1 primary + 1 replica) r7g.xlarge          (Aurora Serverless v2)
      auth miss · KS truth · quota lease · NOT telemetry
```

### 4.3 Fleet count: three, and why that is not "microservices"

We split by **hardware class and failure domain**, not by business capability:

| Fleet | Why it is separate | Why it is not a microservice |
|---|---|---|
| Gateway CPU | Graviton, no GPU driver, scales on in-flight | Contains *all* business logic; no internal RPC between its stages |
| Guard GPU | Needs x86 + NVIDIA (no arm64 GPU in Mumbai [V, R3]); batches across requests | Stateless model server; one call, one deadline, fail-closed |
| Inference GPU / provider | Owned by procurement or the tenant | Already external today |

Splitting `auth-svc`, `policy-svc`, `scan-svc` is explicitly **rejected** — see D1.

---

## 5. Decision register

Every decision the brief asked for, with the options considered, the evidence, and the verdict. Numbers carry provenance tags.

### D1 — Microservices vs modular monolith → **MODULAR MONOLITH + exactly one optional network hop**

| Option | Added latency | Evidence |
|---|---|---|
| Split into auth/policy/scan services (Python) | **+0.4–0.5 ms CPU per hop, server-side alone**; 3 hops ≈ 1.5–3 ms in-host, **2.5–8 ms over VPC** | Python asyncio gRPC server = 4 492 req/s on one core vs Rust tonic 102 754 / Go 55 667 [I, grpc_bench 2026-04-23]; intra-AZ p50 ≈ 300 µs, cross-AZ ≈ 1.5 ms [V, AWS] |
| One extra proxy hop (C++, best case) | 167 µs HTTP / 195 µs gRPC / 39 µs raw TCP; **70 % of it is HTTP parsing repeated at every boundary** | MeshInsight, arXiv 2207.00592 [I] |
| Realistic service-mesh hop | Envoy 1.34 adds **0.78 ms** (echo, max policy) to **3.4 ms** (47 % of a 7.2 ms request) on DeathStarBench @1 500 rps | Beeline, arXiv 2605.31084, 2026 [I] |
| **Modular monolith (chosen)** | 0 | LiteLLM (Python, in-process) self-reports 2 ms median / 13 ms p99 at 1 170 rps [V] |

**Verdict:** a 12 ms budget cannot absorb 2–8 ms of hop tax before any work happens. Tail amplification compounds it (Dean & Barroso: 1-in-100 slow × 100 servers ⇒ 63 % of requests slow). **Keep one process.** The *only* permitted request-path hop is the guard classifier, and only in Stage 2 when it moves to GPU.

### D2 — Do we need GPU instances? → **NOT AT TODAY'S VOLUME. YES ABOVE ~150 CLASSIFY/S PER HOST.**

This is the decision the brief pressed hardest on, so here is the arithmetic in full.

**The classifier must run on every allow-path request** (that is the P0 policy; sampling it is a security hole [C5: `GATEWAY_TIER2_SAMPLE_RATE` already skips T1-clean prompts]). So classify-rate ≈ admitted RPS.

| Hardware | Model | Latency (batch 1, 512 tok) | Saturated throughput | $/h ap-south-1 | **$ per 1 M classifications** |
|---|---|---|---|---|---|
| c8g.4xlarge (16 Graviton cores) | **PG2-22M** INT8 ONNX | ~5 ms | **~150/s** | $0.4318 | **$0.78** [D, R3] |
| c8g.4xlarge | PG2-**86M** INT8 | ~19 ms | ~42/s | $0.4318 | $2.88 — **fails the ≤8 ms SLO** [D, R3] |
| c7i.4xlarge (AMX) | PG2-86M | ~19–20 ms | — | $0.7140 | ~$4.80 — fails [D, R3] |
| inf2.xlarge | PG2-86M | ~6–9 ms est. | 300–460/s est. | $0.9857 | $0.59–0.91 — **unproven** [D; DeBERTa on Neuron has **zero** published measurements, and its custom XSoftmax has broken tracing historically] |
| **g6.xlarge (L4)** | PG2-86M INT8, batch 4 | ~5.5 ms | ~720/s | $0.9664 | **$0.37** [D from TensorRT 10.8 tables, V] |
| **g6e.xlarge (L40S)** | PG2-86M INT8, batch 12 | ~5.0 ms | **~2 390/s** | $2.2350 | **$0.26** [D, same] |
| g6e.xlarge | PG2-86M **FP16**, batch 4 | ~4.0 ms | ~1 010/s | $2.2350 | $0.61 [D] |

Grounding data points, all vendor-published: TensorRT 10.8 BERT-base seq 384 p95 — L4 b1 2.10 ms FP16 / 1.31 INT8, b32 51.12 / 23.82; L40S b1 1.04 / 0.73, b32 13.35 / 6.11. NVIDIA's own DeBERTa demo (TRT 8.4, batch 1, 512 tokens, disentangled-attention plugin): deberta-v3-xsmall **1.8 ms on A100**, 5.6 ms on T4; PyTorch eager is **14.8× slower** — which is why Meta's card figure of 19.3 ms (22M) / 92.4 ms (86M) on A100 must **not** be used for capacity planning [V, R2/R3].

**Verdict, staged:**

* **Stage 1 (now → ~500 RPS admitted): NO GPU.** Run **PG2-22M INT8 ONNX in-process** on the gateway. ONNX Runtime releases the GIL during `Run`, so it uses the existing thread pool without blocking the loop [R11]. This eliminates the network hop entirely and costs **$0** in new infrastructure. It is sufficient because production today peaks at ~30–85 RPS [R].
* **Stage 2 (≥ ~500 RPS sustained, or when p99 headroom disappears): GPU pool.** `g6e.xlarge` (L40S) with Triton + TensorRT, **PG2-86M FP16 first**, INT8 only after a recall bake-off. At 20 k RPS: ~9 GPUs ≈ **$20/h ≈ $15 k/month**, versus **~125 × c8g.4xlarge ≈ $54/h** for the same work on CPU [D]. GPU wins on cost above ~1 k classify/s and is the *only* option that meets ≤8 ms with the 86M model.
* **Never**: Llama Guard 3 1B on the request path. 53 ms/sample on an A100 in HF transformers [I, arXiv 2605.00689]; ~52 ms on L4, ~18 ms on L40S [D]. It belongs at `[DONE]` with its own budget, or async.

**Two hard constraints that follow:** (a) **no arm64 GPU instances exist in ap-south-1** — the GPU tier needs an x86 image, so the ECR pipeline becomes multi-arch [V, R3]; (b) INT8 is **not** free accuracy-wise — a third-party INT8 export of PG2-86M lost **16 points of recall** (0.9625 → 0.8018), while PG2-**22M** INT8 was lossless (0.9579 vs 0.9564) [I, R2]. Ship FP16 for 86M unless our own bake-off says otherwise.

### D3 — Which classifier replaces Haiku? → **PG2-22M (Stage 1) → PG2-86M (Stage 2), plus a written gap list**

| Candidate | Accuracy | Latency | License | Verdict |
|---|---|---|---|---|
| **Llama Prompt Guard 2 86M** (mDeBERTa) | AUC 0.998; **recall 97.5 % @1 % FPR**; multilingual AUC 0.995; APR **81.2 %** on AgentDojo | 92.4 ms A100 eager [V] → ~4–5 ms L40S TRT [D] | Llama 4 Community | **Stage 2 default** |
| **Llama Prompt Guard 2 22M** (DeBERTa-xsmall) | AUC 0.995; recall **88.7 %** @1 % FPR; multilingual **0.942**; APR 78.4 % | 19.3 ms A100 eager [V]; **~5 ms on 16 Graviton cores** [D] | Llama 4 Community | **Stage 1 default** |
| ProtectAI deberta-v3-base-v2 | **APR 22.2 %**; NotInject 56.6 %; English-only; flags 40 % of benign docs | 7.65 ms mean / 14.75 ms p99 @384 tok on g5.xlarge (**only vendor-published ONNX table**) | Apache-2.0 | **Shadow candidate only** — Meta measured it at ~¼ of PG2's attack-prevention rate |
| Sentinel (ModernBERT-large 395M) | F1 avg 0.938 vs ProtectAI 0.709 | ~20 ms on an L4 [V] | "other" — verify | Bake-off entrant |
| PIGuard / InjecGuard | NotInject **87.3 %** (vs PG1 0.88 %) — fixes trigger-word over-defense | 15.3 ms (hw n/s) | [NF] | Bake-off entrant |
| Llama Guard 3 1B | En F1 0.899 / FPR 0.090, S1–S13 taxonomy | 53 ms/sample A100 [I] | Llama 3.2 | **Output only, at `[DONE]`, or async** |
| Qwen3Guard-Stream-0.6B | 119 languages, per-token streaming head, 3-tier | no wall-clock published [NF] | Apache-2.0 | Strong candidate for **streaming** output; must be measured |

**The honest gap versus Haiku — this must ship in the product docs, not just the plan** [all from R2, independent sources]:

1. **Indirect / behaviour-hijack injection**: PG2-86M FNR **0.69**, 22M **0.97**, ProtectAI 0.69 at frozen thresholds; PG2 on BIPIA FNR 0.97–0.985. LLM judges are also weak here (Llama-as-judge 7.1 %) — this is an open research problem, not a regression we are introducing.
2. **Long-context dilution ("Prompt Overflow", 2026)**: **100 % bypass** of PG2-86M with 4 malicious tokens per 512-token window; 50 %-overlap windows do **not** help. **Mitigation is mandatory**: aggregate window scores with a stateful sum/contiguity rule, never `max()` alone.
3. **`goal_hijacking` / `social_engineering`**: Meta removed these labels from PG2 deliberately. **No small model covers them.** They stay on the slow pool or become a documented residual.
4. **Character injection**: 70–73 % evasion of Meta PG, **100 %** with emoji smuggling — which is why the T1 canonicalisation pass (NFKC + Cf/ZW strip + **UTS-39 confusables**) must run *before* the classifier, and why R5's finding that plain NFKC misses Cyrillic homoglyphs is a blocker, not a nicety.
5. **Multi-turn**: > 90 % ASR against all 13 guardrails in the 2026 SoK. Our G6 multi-turn reassembly (`scanner.py:1470-1519`) is a real differentiator — keep it.
6. **Over-defense**: PG2 flags 13–19 % of NotInject benign prompts; per-tenant threshold calibration is required, not optional.
7. **Confidence is not a safety signal**: missed attacks carry severity 0.99–1.0. Escalation must key off *structural* signals (T1 hits, encoding, length, tenant risk), never classifier confidence.

**Keep Haiku** in two places only: an **opt-in synchronous "slow pool"** for tenants who contract for it (capped at ~160 req/s by the Bedrock Global-profile quota [V, R10]), and an **async 0.1 % sampler** for drift detection.

### D4 — Which instance, and how many? → **c8g.4xlarge, Graviton4, ECS on EC2**

| Family | All-core SCore per $ | Single-thread Passmark | $/h (ap-south-1, 4xlarge) | Verdict |
|---|---|---|---|---|
| **c8g (Graviton4)** | **≈ 63 k** | 1 943 | **$0.4318** | **Chosen** |
| c8i (Emerald Rapids) | ≈ 23 k | 3 264 | $0.7497 | Only if a *single* request's CPU time breaks the budget (1.68× faster per request, 0.37× throughput per $) |
| c7g (Graviton3) | — | 1 565 | $0.3926 | Current prod; step up |
| c7a / c8a (AMD) | — | — | — | **Not offered in Mumbai** [V] |
| c9g (Graviton5) | — | — | — | **Not in ap-south-1** (us-east-1/2, us-west-2, eu-central-1 only) [V] |

**Size:** `c8g.4xlarge` = 16 real cores, 7.5/15 Gbps, and **60 ECS tasks with ENI trunking** (vs 7 without). Step to `8xlarge` only if `conntrack_allowance_exceeded > 0` or fixed 15 Gbps is needed [V, R6].

**Counts** (keep CPU ≤ 60 %, cross-zone off ⇒ ×1.5 for AZ-loss tolerance):

| Target | Today's efficiency (r ≈ 1 375 RPS/vCPU on `/health`; ~4 RPS/vCPU with scans) | After this plan (est. r ≈ 4 000 `/health`, ~250 RPS/vCPU with scans) |
|---|---|---|
| 20 k RPS admitted | not reachable — 5 000 vCPU | **3 × c8g.4xlarge** (one per AZ) ≈ $1.30/h |
| 100 k RPS admitted | not reachable | **12 × c8g.4xlarge** (4/AZ) ≈ $5.18/h ≈ $3.8 k/month |

The second column is a **target, not a measurement** — it is the plan's central falsifiable claim, gated in Phase 2.

### D5 — Redis vs ElastiCache vs Memorystore vs Dragonfly → **ElastiCache for Valkey 8.1, cluster-mode, 3 × (1 + 1) r7g.xlarge**

| Option | Verdict | Why |
|---|---|---|
| **ElastiCache Valkey 8.1 CME** | **Chosen** | 20 % cheaper per node than Redis OSS; io-threads take a node from 360 k → **1.19 M RPS** with avg latency 1.79 → 0.54 ms [V]; 9.0 adds hash-field TTL. Redis OSS 7.1 is end-of-line on ElastiCache and already carries extended-support surcharges in the Mumbai price list. |
| ElastiCache Serverless | **Rejected for the hot path** | AWS's own GA measurement: p50 GET **751 µs**, SET 1 050 µs; **30 k ECPU/s per slot** — and a kill-switch key *is* that hot slot. Keyspace notifications unsupported. Fine for OAuth/jobs/vector-policy. |
| Bigger single node | **Rejected** | The 31.7 ms p50 is client-side, not server-side (§0 row 3). Buying RAM fixes eviction, not latency. |
| Dragonfly / KeyDB | **Rejected** | Dragonfly's 4–6 M QPS/node is vendor-measured and irrelevant — our bottleneck is RTT count and hot keys. An independent 2024 test found KeyDB *slower* than Redis 7 with io-threads. |
| GCP Memorystore 1 GiB BASIC | **Forbidden as prod** | No replica; 1 GiB will evict `auth:apikey:*` under telemetry growth; AUTH and transit TLS off. Confirmed by the 08-22 addendum plan; this plan agrees. |

**Cost:** 6 × r7g.xlarge Valkey = **$1 573/mo** on-demand, **$1 069/mo** 1-yr RI, $816 3-yr. Lean start: 2 shards × (1+1) = $1 049/mo, or 3 × (1+1) r7g.large = $785/mo [V, AWS price file 2026-08-21].

**Mandatory client changes regardless of node choice:** `SCRIPT LOAD` + `EVALSHA` (today the full Lua text crosses the wire on every call), hash-tagged keys `{org:<id>}` so a combined script is single-slot, a dedicated pool per concern with **bounded queueing rather than immediate raise**, and `retry_on_timeout` reconsidered (today it doubles the stall before a 503).

### D6 — Rate limiting → **two-tier: local GCRA + exactly one `EVALSHA`, post-response settle**

| Layer | Mechanism | Correctness |
|---|---|---|
| Burst / RPM | **Local GCRA** per host, reconciled by a Redis lease | Approximate by design. Overshoot bound = `N × L / Q`; with ε = 10 %, Q = 6 000/min, N = 40 hosts ⇒ L = 15 requests. **Publish the bound**; do not pretend it is exact. |
| Org / key TPM | **Central**, one `EVALSHA` on slot `{org:<id>}`, pre-charge `len/4 + max_tokens`, **settle the delta after the response** in a batched flush every 100–250 ms | Exact-ish; **never** multiplied by host count. Leasing does **not** work for TPM at N = 40 (5 % of 1 M TPM ÷ 40 = 1 250 tokens ≈ one request) [R8]. |
| Kill-switch / model_state | **Snapshot + 250 ms heartbeat + `SSUBSCRIBE`**, with a **2 s soft / 10 s hard** staleness contract; beyond hard ⇒ 503 `admit_stale` | Fail-closed preserved. Today's per-request read gives 0 s staleness; we are trading ≤2 s of propagation for ~4 RTTs. **This is a product decision and must be signed off.** |

**Two inversions to fix (RC-5):** limiters currently fail **open** (a Redis outage removes the TPM ceiling) while kill-switch/CB fail **closed** on the *same* pool exhaustion. Target: TPM/budget **fail-closed**, burst/RPM may stay fail-open, and pool exhaustion must **queue** rather than raise.

Also fold in: org-scope `circuit:state:{model}` (today global across tenants — one tenant's BYOK failures open the breaker for everyone [C6]), and make the Tier-2 breaker **shared** rather than per-worker (today `MIN_CALLS=5` is effectively 80 across 16 workers [C1]).

### D7 — RDS vs Postgres vs Mongo vs Aurora for the control plane → **PostgreSQL, keep it; Aurora Serverless v2 optional; retire Mongo**

| Option | Verdict | Evidence |
|---|---|---|
| **RDS/Aurora PostgreSQL** | **Keep** | The gateway opens **zero** Postgres connections on the chat path (verified by grep — only the dead `embedding_vault.py` imports psycopg) [C4]. Django ORM, migrations, transactional key/policy writes and PgBouncer are the product. Aurora Serverless v2 at 2 × 0.5 ACU idle ≈ **$131/mo**; RDS Multi-AZ `db.r7g.large` = $397/mo [V]. Aurora Multi-AZ cluster failover "typically under 35 seconds" vs Multi-AZ instance DNS flip. |
| **MongoDB** | **Retire the pilot** | It is off by default, has no batching, and **permanently disables itself after one init failure** — so the operational events routed *only* to it (breaker state changes, HMAC failures, D_G5 query audits) are silently dropped today [C4]. Re-model users/orgs/keys/policies in Mongo would trade transactions and migrations for nothing. |
| Postgres as the **analytics** store | **Reject** | `EnforcementEvent.metadata` is JSONB ≈ the whole 52 KB event; at 100 k/s that is **8.6 B rows and ~432 TB/day**; RDS caps at 64 TiB. Langfuse hit exactly this wall (ingestion p99 spiking to 50 s) before moving to ClickHouse [V]. |

### D8 — Telemetry bus and analytical store → **bounded ring → librdkafka → MSK → ClickHouse**

| Layer | Today (≤5 k RPS) | At 100 k RPS | Rejected |
|---|---|---|---|
| **Bus** | **MSK Standard 3 × kafka.m7g.large ≈ $319/mo** | MSK **Express** 3 × express.m7g.4xlarge (375 MB/s sustained) ≈ $10.8 k/mo | MSK Serverless (**hard 200 MB/s per cluster**); Kinesis (1 MB/s per shard ⇒ 600 shards, $12–19 k/mo); NATS JetStream (one Raft leader takes every write); Redis Streams (2.16 TB RAM for 1 h of buffer); SQS (~$59 k/mo in request charges) [all V] |
| **Store** | ClickHouse OSS on r7g.2xlarge + 2 TB gp3 ≈ **$400/mo** | 3–6 × r7g.8xlarge with S3-backed MergeTree ≈ $9–12 k/mo (ClickHouse Cloud is available in ap-south-1) | OpenSearch (index+`_source` ≈ raw ⇒ six figures/mo); Druid/Pinot (no managed path in Mumbai); S3+Athena is the **cold tier**, not the query tier |
| **Producer** | **`confluent-kafka`** (librdkafka): `produce()` returns immediately, raises `BufferError` when the local queue is full | same | **`aiokafka` is disqualified**: its `send()` **blocks the coroutine** when the per-partition batch is full [V] — that is a request-path stall |

**The emission contract** (this is what makes "audited" honest rather than aspirational):

* Request handler does `put_nowait()` into a **bounded byte-sized ring**; on overflow it increments `audit_dropped_total{reason}` and, for `enforcement` events only, appends to a bounded local spill file.
* Every event carries `(host_id, pid, epoch, seq)` so the consumer can **compute the gap count**, and we publish `audit_completeness_ratio` (alert < 0.999 over 5 min).
* Today's producer silently truncates its buffer from 200 → 100 events and drops the rest with a WARNING [C4]. That is replaced, not tuned.

**And the payload must shrink.** Emitting the prompt 25× and the response 10× per event [M] is the single largest avoidable CPU cost on the hot path. Target: stage metrics + **one** content reference; the UI rehydrates the trace from ClickHouse.

### D9 — Vector search: do we need it, which store, which embedding model? → **OPTIONAL, IN-PROCESS, POSITIVE-EVIDENCE-ONLY**

**Do we need it?** As a *detector*, **no**. The published evidence is that nearest-neighbour-to-known-attacks is brittle: 90.84 % of the 131 jailbreak communities in Shen et al. contain fewer than 9 prompts, and the embedding-based detectors that *do* report strong numbers (NemoGuard-JailbreakDetect F1 0.960) are **classifiers trained on embeddings, not kNN lookups** — a role PG2-22M already fills better [R4].

**Where it earns its place:** a **known-attack memory** consulted only when the classifier lands in a gray band, used as **positive evidence only** (never to clear a request), with a minutes-not-days feedback loop — add every confirmed Tier-2/human-reviewed detection to the index — and human-readable attribution ("matches DAN-family exemplar #1234 at 0.94").

| Component | Choice | Measured latency | Why not the alternatives |
|---|---|---|---|
| **Embedding** | **`potion-base-8M`** (model2vec static, 256-d, MIT) or `static-retrieval-mrl-en-v1` (Apache-2.0, MRL→256) | **0.177 ms p50 @95 words, 0.328 @190, 0.635 @390** — 1 thread [M] | `all-MiniLM-L6-v2` INT8 ONNX is **10.57 ms p50 @128 tok** [M] — 2–3× the entire budget on its own. `bge-small` ≈ 2× that. `text-embedding-3-small` adds ≥297 ms RTT from Mumbai plus a P90 of ~500 ms [I]. Quality cost is acceptable: potion-8M = **91.96 %** of MiniLM on MTEB, and on the actual task (near-duplicate detection) SwiftEmbed reports AP **90.1 % vs 84.7 %** for Sentence-BERT [I]. |
| **Index** | **`hnswlib`** in-process, one read-only global index per worker, mmap'd, rebuilt offline and hot-swapped | **0.105 ms p50 / 0.163 ms p99 at recall@10 = 1.00** on 100 k × 384 [M]; ann-benchmarks 1-core: 0.13 ms @R0.90 on SIFT-1M [I] | Any server (Qdrant 3.07 ms incl. gRPC, Weaviate p99 3–8 ms, Milvus needs etcd/minio/pulsar) eats the budget in RPC alone. **pgvector p99 is ≥13 ms even on an r8g.4xlarge** [V]. **Pinecone has no ap-south-1 region** ⇒ +62 ms to Singapore [V]. |
| Memory | 1 M × 256-d f32 HNSW(M=16) ≈ **1.15 GB**; 100 k ≈ **115 MB** | | |

**Total hot-path cost ≈ 0.3–1.0 ms p50, < 2 ms p99** — and it runs on **zero** requests in the common path.

**Hard prohibitions:** do not build a vector-DB service for this; do not reuse the index or the embeddings as a **cross-tenant semantic cache** (that is a documented cross-tenant leakage class); do not use `usearch` i8 quantisation for near-duplicate matching (**it lost 14 points of recall** on tight clusters [M]).

### D10 — The Tier-1 engine → **fix the algorithm first, then the regex engine**

Order matters, because the profile inverts the plans' assumption.

| Pass | Today | Target | Mechanism |
|---|---|---|---|
| `_segment_token` (DP word-splitter with `difflib` per cell) | **73.8 ms** of a 102 ms scan; 110 100 `difflib.ratio` calls [M] | **< 0.5 ms** | Replace with an **Aho-Corasick automaton over the 109-word vocab** (`ahocorasick_rs`: 486 keywords in **9.5 µs** [M]) + a cheap gate so only tokens that *fail* the vocab lookup reach a fuzzy path; RapidFuzz only on keyword-selected windows |
| `_fuzzy_scan` (37 anchor phrases × `SequenceMatcher` per word) | **21.8 ms** [M] | **< 0.5 ms** | Aho-Corasick candidate selection, then RapidFuzz on the selected span only |
| All regex passes (82 attack + 26 PII + 25 secret + 12 credential + G33/G53) | 0.77 ms + ~1.7 ms [M] | **~6 µs** | **Vectorscan via the `hyperscan` 0.8.2 wheel** (aarch64 manylinux wheels verified): 232 patterns scanned in **6.0 µs clean / 8.3 µs dirty** vs **3 694 µs** for the Python loop [M]. `HS_FLAG_PREFILTER` for the ~10 lookaround patterns (documented as a **superset** of matches) + `re` verify ⇒ recall cannot drop. |
| Canonicalisation | NFKC + Cf strip + leet | **add UTS-39 confusables** | R5 measured the prototype **missing** Cyrillic "ignоre аll previоus instructiоns" under NFKC alone; a 3 k-entry `str.translate` costs **79 µs** [M] |
| Body parse | `json` | `orjson` | 1.1 µs vs 4.4 µs for a 2.3 KB body [M] |

**Measured prototype of the whole redesigned T1** (232 real patterns, 2 KB prompt, Xeon 8581C): **47 µs clean, 217 µs dirty, 45 µs with a base64-encoded injection (still caught)** [M]. Even at 3× for Graviton that is **≈0.15 ms clean / ≈0.7 ms dirty** — 4–20× inside the 3 ms target.

**Non-negotiables carried over:** G33/G53 stay; byte-verify fail-closed stays; the Python engine remains the **oracle** in tests and any disagreement fails closed; the 10 000-char DoS block stays; `_MAX_DOC_SCAN_LEN`/timeouts stay. Note C5's finding that today's `command_injection` pattern hard-blocks *any* prompt containing markdown inline code (`` `npm install` `` → block) — that is a **live false-positive class**, and fixing it during the rewrite is a deliberate policy change that must be scored, not a silent "improvement".

### D11 — Load balancer and edge → **NLB with `preserve_client_ip`, nginx in the `/v1` path, TLS at nginx**

| Item | Today | Target | Evidence |
|---|---|---|---|
| SNAT ceiling | `preserve_client_ip` **off** (the default for IP/TCP target groups) ⇒ **~55 000 connections per NLB-IP × target pair** | **Enable it.** Fallbacks if it cannot be enabled: up to 7 secondary NLB IPs per subnet (8 × 55 k), and registering each host on several ports (each IP:port is a distinct target) | AWS target-group + troubleshooting docs [V]; 73 % ConnectTimeout at 100 k in-flight [R] |
| nginx | **not in the `/v1` path at all** | Put it there: `http2 on`, `proxy_buffering off`, **`upstream { keepalive 512; }`** (absent today ⇒ a new TCP connection per proxied request), gzip **off** for `text/event-stream`, and the security-header include that today only protects the UI vhosts | [C2], [C6], nginx docs [V] |
| TLS | NLB TLS listener | **Terminate at nginx** where handshake volume matters — NLB TLS is billed at **50 new TLS conn/s per NLCU** ⇒ ~$12/h at 100 k handshakes/s | AWS pricing [V] |
| Conntrack | c8g is Nitro v5 ⇒ ENI `TcpEstablishedTimeout` default **432 000 s (5 days)** vs NLB idle 350 s ⇒ abandoned flows pin the allowance | Set 350–600 s in the launch template; kernel keepalive 240/60/3; `uvicorn --timeout-keep-alive 90` (**default is 5 s**) | AWS docs + blog [V] |
| CDN / API Gateway / ALB on `/v1` | — | **Forbidden.** API Gateway: 30 s integration timeout (not adjustable), no response streaming, 10 k RPS default. CloudFront: origin H1.1, 30–120 s timeouts, uncacheable POST | [V]; agrees with `2026-08-22-100k-C-hld-lld-proof.md` |

### D12 — Server stack → **keep gunicorn+UvicornWorker now; A/B Granian; stay multi-process**

Measured today: **11 k RPS/host on `/health` ≈ 1 375 RPS/vCPU ≈ 0.73 ms of CPU per trivial request** [D from R] — that is **10–40× below** published single-process ceilings (Granian 125 539 RPS vs uvicorn+httptools 51 051 vs uvicorn+h11 14 501 on the same box [V]). So the *stack* is not the first lever; the per-request work is. But two free wins: confirm `uvicorn[standard]` is actually installed (otherwise `--http auto` silently falls back to h11, a 3.5× loss) and raise `--timeout-keep-alive` off its 5 s default.

**Free-threaded Python 3.14t: not in 2026.** PEP 779 makes it supported, but the single-thread penalty is 1–8 %, every C extension must be FT-safe, and uvicorn has no free-threading story (discussion #2830 unanswered) [V].

### D13 — ECS vs EKS vs plain ASG → **ECS on EC2 for both fleets**

No control-plane fee (EKS is $0.10/h + ~12 % Auto Mode overhead); ENI trunking gives 60 tasks on a `c8g.4xlarge` vs 7 without; the ECS GPU AMI ships NVIDIA drivers and runtime with no device plugin to manage; managed scaling via `CapacityProviderReservation`. **Choose EKS only** if we later adopt the Kubernetes inference ecosystem (vLLM/KServe/Ray, DRA/MIG) for a platform model — which is the SLO-C world, not this one [V, R6].

### D14 — Streaming and output guarding → **T1 per chunk (keep), classifier once at `[DONE]`, never per flush**

Today `OutputGuard.inspect` — including a **Bedrock Converse call** — runs on **every flush**, and flushes are triggered by any `.`/`!`/`?`/newline in the delta [C1, C5]. A 20-sentence answer therefore makes ~20 guard-model calls. Target:

* **T1 detectors stay per 4 KB chunk** — they are already cheap (0.11/1.49/13.9 ms at 65/844/8 775 chars [M]) and they are the fail-closed PII control.
* **The semantic output classifier runs once**, at `[DONE]`, with its own deadline. In block mode a deadline miss **withholds the remaining tokens**; it must never silently skip (today output T2 is fail-**open**, which inflates measured RPS while dropping the control [C1]).
* Keep the ≥512 B lookahead hold and the rewrite-until-`[DONE]` semantics.
* Consider **Qwen3Guard-Stream** later specifically because it has a per-token head designed for cut-off — but only after we have a wall-clock measurement, which no source publishes [NF].

### D15 — SLO C (100 k completed chats) → **price it, stage it, never bundle it**

| Path | Feasibility | Cost |
|---|---|---|
| Customer BYOK to public APIs | **Impossible** as an *our*-SLO — bounded by the tenant's quota (live: 100 k TPM ⇒ ~46 RPS) | — |
| **Bedrock in Mumbai** | **Impossible today**: Llama 3 8B = 800 RPM/300 k TPM; the best Mumbai model quota is 10 000 RPM (**167 RPS, not adjustable**); summing *every* Mumbai on-demand model ≈ 4 k RPS; **zero Provisioned-Throughput SKUs in the Mumbai price list** | — |
| **Self-hosted vLLM on H100 Capacity Blocks** | **The only credible path** — in-region, same-AZ, quota-free, ≤64 instances/block and ≤256/org (= 2 048 H100 hard cap) | 8B @100 out tokens: **1 447 H100 = 181 × p5.48xlarge = $4.99 M/mo (CB) / $8.73 M (on-demand) / $2.37 M (spot)**; a proof-day is **$164 k**. At 16 out tokens: 127 instances, $115 k/day. A 1B platform model: ~32 instances, **$29 k/day** |
| Groq / Cerebras / Fireworks / Together | Per-stream speed ≠ fleet RPS; **nobody sells a 100 k-RPS SKU**; none are in-AZ | — |

**Recommendation:** replace "100 k completed chats/s" with *"the firewall is proven not to be the bottleneck at N RPS,"* prove N with a **token-emitting stub** (not `chatcmpl-loadtest-stub`), and quote SLO C as a capacity purchase with the table above. Retain the C acceptance predicate from `2026-08-21-100k-completed-chats-C-slo.md` §8 verbatim for the day it is funded.

### D16 — Hosted guardrail APIs on the hot path → **NO, unanimously**

| Product | Default quota | Measured latency | Verdict |
|---|---|---|---|
| **Bedrock Guardrails, ap-south-1** | **100 RPS**; content filter **50 text-units/s** (Standard); sensitive-info 200 TUPS | **+120–500 ms** measured by two independents; AWS publishes none | 12 ms: **No**. 100 k RPS needs 2 000–4 000× the default |
| Azure Prompt Shields | S0 = 1 000 requests / 10 s (**100 RPS**) | none published; MS FAQ recommends **asynchronous** filtering | **No** |
| Google Model Armor | **1 200 queries/min (20 RPS)** | **[NF]** — the "p99 ≈ 450 ms" figure in our own plan could not be verified from any primary source | **No** |
| Lakera / Prisma AIRS / HiddenLayer / Arthur / NeMo | "sub-50 ms" marketing at best; NeMo ≈ 0.5 s for 5 rails | — | **No** |

Only **self-hosted small classifiers** meet the budget, and (for the 86M class) only on GPU — which is exactly D2/D3.

### D17 — Rewrite in Rust/Go? → **NO. Targeted PyO3 where it pays.**

The evidence for Rust *inside* Python is overwhelming (orjson 11–14×; pydantic-core 4–50×; rebar geometric means: `python/re` 42.10 vs `hyperscan` 2.37 vs `rust/regex` 3.08; PyO3 call overhead 20–40 ns; `Python::detach` releases the GIL) — and we are already taking it via the `hyperscan` and `ahocorasick_rs` wheels [R5, R11]. The evidence for a **full** rewrite is much weaker against this codebase: 4 500+ tests, FROZEN operator-visible behaviours, and Google's own data that engineers need 2–4 months to reach parity in Rust. LiteLLM's Stage-2 pattern — **Rust data plane, Python control plane** — is the model to keep in reserve if F/G targets are missed after Phase 2.

---

## 6. Critical path vs deferred path

### 6.1 The rule

**On the hot path** only work that can (a) change the decision for *this* request, and (b) complete inside its latency budget. Everything else is emitted to a queue with a bounded ring and a drop counter.

### 6.2 Stage-by-stage budget

| Stage | Today | Target p50 | What changes |
|---|---|---|---|
| auth | 1 Redis RTT, fail-closed, no cache | **0.05 ms** | RAM cache ≤30 s keyed by epoch; Redis only on miss; **no negative caching** (revocation is a DELETE with no notification) |
| kill_switch + model_state | 3–4 Redis RTTs, fail-closed | **< 0.05 ms** | Snapshot + 250 ms heartbeat + `SSUBSCRIBE`; 2 s soft / 10 s hard ⇒ 503 |
| rate_limit (burst/RPM/TPM) | 2 MULTI + 1–2 full-text `EVAL` | **≤ 1 ms** | Local GCRA + **one** `EVALSHA` on `{org:<id>}`; settle after response |
| policy | `to_thread` + **one OS thread per regex rule** | **0.5–2 ms** | Precompiled bundle; Vectorscan for rule regexes; no thread-per-rule |
| **input_scan T1** | 52–105 ms (97 % `difflib`) | **≤ 3 ms** (measured prototype 0.15–0.7 ms) | D10 |
| **input_scan T2** | **1.3–1.8 s Haiku** | **≤ 8 ms** | PG2-22M in-process (Stage 1) / PG2-86M on L40S (Stage 2), hard 15 ms deadline, fail-closed in block mode |
| model_routing | RAM + 2·N Redis for candidate filtering | **≤ 0.5 ms** | Candidates filtered from the admit snapshot, not per-request Redis |
| model_input | ~0 | ~0 | unchanged — must stay **after** T1 redaction |
| model_output | provider | **not our tax** | pooled httpx/LiteLLM; stub forbidden in gates |
| output_guardrail | T1 per flush + **Bedrock per flush** | **1–3 ms/chunk + ≤40 ms once at `[DONE]`** | D14 |
| trace + telemetry | ~52 KB, 25 prompt copies, ~20 `redact_all`, synchronous | **≤ 0.2 ms** | stage metrics + one content ref → ring → Kafka |
| **logging** | **≈17 synchronous Redis `PUBLISH`** | **0** | Detach the publisher to a background consumer; restore the `gateway` logger to INFO |
| **Total firewall tax** | **~2.2–3.0 s** | **≤ 12 ms p50 / ≤ 40 ms p99** | |

### 6.3 What moves off the request path

| Work | Today | Target |
|---|---|---|
| Telemetry / pipeline trace | built + scrubbed synchronously, `LPUSH` to Redis, drain ≤250/s | ring → librdkafka → MSK → ClickHouse; completeness counted |
| Enforcement audit rows, incidents, review items | drain thread creates rows per event | ClickHouse consumer materialises only actionable rows into Postgres |
| MCP tool-call audit | fire-and-forget HTTP → Django → 2 SELECT + 2 INSERT, shed above 64/256 in-flight | same Kafka topic |
| Usage / TPM reconcile | 1–2 Redis `INCRBY` per response | batched flush every 100–250 ms |
| Risk scoring, auto-isolation | 6–8 Redis RTTs on the error path | consumer-side, from the event stream |
| Haiku judge | synchronous, every request | **0.1 % async sample** (fits the 10 k RPM Global quota; 1 % does not) |
| Model-catalogue reload | per request for catalogue-empty orgs | negative-cache + pub/sub |

---

## 7. Capacity model

### 7.1 Little's Law is about generation, not about us

`in_flight = RPS × T_hold`. The firewall's own 10 ms is irrelevant to in-flight; the **model's 0.8–3 s** sets it.

| Generation p50 | In-flight at 1 k RPS | at 20 k RPS | at 100 k RPS |
|---|---|---|---|
| 0.2 s | 200 | 4 000 | 20 000 |
| 0.8 s | 800 | 16 000 | **80 000** |
| 3.0 s | 3 000 | 60 000 | 300 000 |

Each in-flight chat holds a client FD, an nginx upstream slot, a uvicorn task, and an httpx connection to the provider. **Cutting our latency does not cut in-flight — only shorter generations or fewer concurrent users do.** What our design *does* control is the **cost per in-flight request**, which today includes a pre-built 9-stage trace dict, a `StreamLaunchContext` holding prompt copies, 64 raw SSE frames plus their parsed deltas, and four `BaseHTTPMiddleware` task groups [C1].

### 7.2 Per-host ceilings to respect

| Ceiling | Value | Source |
|---|---|---|
| NLB per IP × target | **~55 k connections** unless `preserve_client_ip` | [V] |
| Outbound ephemeral ports per host | ~28 k default (32768–60999) — **relevant because every in-flight stream holds an upstream connection** | kernel default |
| ENI conntrack allowance | **unpublished per instance type** — must be measured via `ethtool conntrack_allowance_available` during the test | [NF] |
| `gunicorn --worker-connections` | 20 000 (already set) | [C1] |
| FDs | 65 535 today; raise `fs.nr_open`/`LimitNOFILE` to 1 048 576 | [C2] |
| Memory per in-flight stream | **not measured anywhere** — the plan's largest open number | [NF] |

---

## 8. Phased implementation

Each phase has a **falsifiable gate**. Stop if a gate fails.

### Phase 0 — Honesty and instrumentation (days, no behaviour change)
Fix `honesty.full_nine_stages` (today it is literally `MODE == "chat"` [R]); UI must render a 0 ms stage as *skipped*, not *allow*; add a **token-emitting** stub (30–100 tok/s for 2–10 s) so benches measure in-flight memory and event-loop lag rather than a `chatcmpl-loadtest-stub`; split `T_addon_pre` / `T_addon_post` / **`T_t2_ms`** in `pipeline_trace`; stage `seq` must match **runtime** (kill-switch already runs **before** input_scan in `proxy_chat` — the canonical name list is a lie); add a Prometheus **multiprocess** registry; measure memory per in-flight stream; capture the full-suite baseline (26 known failures).
**Gate:** a scans-off run **must** report `full_nine_stages: false`, and a stub run must fail the capacity predicate.

### Phase 1 — Remove the self-inflicted stalls (days, no new infrastructure)
RC-1 (detach log publishing; restore INFO), RC-4 (shrink the telemetry payload; ring buffer), `EVALSHA`, pool sizing with **bounded queueing**, negative-cache the empty catalogue, `orjson`, drop the second `_deobfuscate_text` call on the event loop, `uvicorn --timeout-keep-alive`, nginx into the `/v1` path with upstream keepalive, `preserve_client_ip`, chat body cap (MCP `_mcp_read_body_capped` parity), gunicorn timeout aligned to NLB 350 s (today default 120 s), bind gunicorn `127.0.0.1:8300` once nginx owns the public listener.
**Gate (corrected):** with Tier-2 **still on Haiku**, **`T_addon_pre − T_t2` p50 < 50 ms** (admit + T1 + Redis/log stalls only). Haiku itself is 1.3–1.8 s [R] and **cannot** be inside a 50 ms `T_addon_pre` while it remains on the path. Also: single-host non-stub RPS improves ≥3× on the same hardware **or** event-loop stall from logging/telemetry is proven gone via traces. If `T_addon_pre − T_t2` does not fall, the RC-1/RC-4 diagnosis is wrong and this plan must be revised before anything else is built.

### Phase 2 — T1 engine + admission redesign (weeks)
D10 (Aho-Corasick + Vectorscan + confusables), D6 (local GCRA + one `EVALSHA` + snapshot admit), org-scoped breaker keys, shared Tier-2 breaker.
**Gate:** unique-prompt T1 p50 **< 3 ms**; the full 1 150-case adversarial corpus produces **byte-identical verdicts** against the Python oracle (or every difference is individually signed off); RPS/host ≫ 85.

### Phase 3 — Replace the Haiku judge (weeks)
PG2-22M INT8 in-process; wire the dead `GUARDRAILS_SERVICE_URL` **or delete it**; window+aggregate long prompts with a **stateful** rule (Prompt Overflow mitigation); cache key gains `policy_version` + `model_digest`; Haiku → 0.1 % async sampler; output classifier moves to `[DONE]` and becomes fail-closed in block mode.
**Gate:** `T_addon_pre` p50 **≤ 12 ms**; a published **recall table vs Haiku** on the in-repo corpora (≈1 150 cases) plus the gap list from D3 — *equality is not claimed*.

### Phase 4 — Fleet, bus, store (weeks)
Valkey CME; MSK + ClickHouse with completeness metrics; ECS on c8g.4xlarge across 3 AZs; in-region load generators; kernel/ENI tuning.
**Gate:** 20 k RPS admitted at ≤0.1 % error with guards on, from in-region generators, **three times**.

### Phase 5 — Scale-out and (optionally) GPU
GPU guard pool when sustained classify rate > ~150/s/host; horizontal gateway scaling; the SLO-C conversation with a funded number.

---

## 9. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Admit snapshot introduces up to 2 s of kill-switch staleness** (today: 0 s) | **High** — it is a security control | Explicit product sign-off; `SSUBSCRIBE` push for immediate flips; hard-fail at 10 s; publish `admit_snapshot_age_seconds` |
| T1 rewrite changes verdicts | **High** | Python oracle in CI; byte-identical gate; the markdown-backtick false-positive class is fixed **deliberately and scored** |
| Small classifier misses what Haiku caught | **High** | Published gap list (D3); shadow Haiku at 0.1 %; slow pool for tenants who contract for Haiku-class judging |
| **Prompt Overflow dilution** (100 % bypass at 4 malicious tokens/window) | **High** | Stateful window aggregation, not `max()`; cap window count and fail closed above it |
| Fail-open output guard inflates measured RPS | **Medium-high** | Gate fails if `guard_degraded_rate > 0.001`; block mode withholds rather than skips |
| MSK/ClickHouse become a new operational burden | Medium | Start at $319 + $400/mo; identical interfaces at both scales; Redis Streams adapter behind the same `EventSink` for the first milestone |
| Multi-arch ECR pipeline for the GPU tier | Medium | Only needed at Stage 2 |
| GPU capacity in ap-south-1 is constrained | Medium | ODCR/Savings Plan for the base pool; CPU PG2-22M tier remains the fail-closed overflow |
| Estimates in this plan are estimates | — | Every projected number is tagged [D] and gated in a phase |

---

## 10. Non-goals — things this plan will not do

* Claim Haiku-equivalent semantics from a 22M/86M classifier.
* Promise 100 k completed chats/s without a funded GPU fleet.
* Put a generative LLM judge on the synchronous admit path by default.
* Split auth/policy/scan into network services.
* Put CDN, API Gateway, ALB, or Kafka on `/v1/chat/completions`.
* Ship a bigger ElastiCache node as a latency fix.
* Use sampling (`GATEWAY_TIER2_SAMPLE_RATE < 1`) to buy RPS.
* Quote `/health`, 6-stage, stub-LLM, summed-IP, or in-flight-count numbers as capacity.

---

## 11. Overlays folded in after adoption (2026-08-22)

This section does **not** reopen D1–D17. It records (a) a product lock from the planning thread, (b) one gate that was physically impossible as written, (c) code-verified amplifications of RC-1/RC-2, (d) review-agent gaps the body omitted.

### 11.1 Product lock overlay (Fast SKU)

Conversation lock: **Haiku is not a sold control.** Shadow/sample Haiku is **audit-only**. There is **no customer slow-pool SKU** in the Fast SKU.

D3's "opt-in synchronous slow pool (~160 req/s Bedrock quota)" remains an **internal/contract escape hatch**, not catalog copy. Phase 3 ships T1 + PG2-22M + written gap list. Do not describe 0.1 % async Haiku as fail-closed. Today's `tier2_post_scan` consumer is a **log sink** (never calls Bedrock) — Phase 3 must implement a real sampler or drop the claim.

MCP/RAG/embeddings stay default T1 (`mcp_tier2_enabled` / `rag_tier2_enabled` false). Chat `role=tool` still hits the **chat** T1+PG2 path.

### 11.2 Phase-1 gate was self-contradictory

`T_addon_pre` includes input T2. Live Haiku input is **1.3–1.8 s**. A 50 ms `T_addon_pre` while Haiku is still awaited is impossible. Correct gate is in §8 Phase 1: **`T_addon_pre − T_t2`**. The 12 ms SLO remains a **Phase 3** claim after D10 + PG2-22M, not Phase 1.

### 11.3 Code-verified amplifications

| Claim in §§0–3 | Rechecked in tree | Extra force |
|---|---|---|
| RC-1 Redis `PUBLISH` per log | [`shared/ai_mesh_shared/redis_log_handler.py`](shared/ai_mesh_shared/redis_log_handler.py) `emit()` uses **sync** `redis.Redis` + `client.publish`; `socket_timeout=1.0`. [`main.py:5999`](gateway/ai_mesh_gateway/main.py) forces `gateway` logger **DEBUG**. | A slow Redis can stall the **event loop up to 1 s per log line**, not 0.21 ms × 17. RC-1 is larger than the ~17 ms estimate when the node is busy. |
| Output T2 per flush | [`secure_streaming.py:377`](gateway/ai_mesh_gateway/secure_streaming.py) `OutputGuard.inspect` every flush; [`output_guard.py:1002-1008`](gateway/ai_mesh_gateway/output_guard.py) `scan_output_with_tier2` **fail-open**. | Confirmed. D14 is mandatory, not optional polish. |
| nginx not on NLB `/v1` | Live: NLB TLS:443 → gunicorn **:8300**. [`docker-compose.prod.yml`](docker-compose.prod.yml) gateway `0.0.0.0:8300:8300`. nginx `:80/:443` is ALB/UI. Terraform [`modules/loadbalancers/main.tf`](infra/terraform/modules/loadbalancers/main.tf) `target_type=ip`, **no** `preserve_client_ip`, and **terraform has never been applied** (prod is compose on one c8g). | D11 is a **listener change** (NLB target → nginx, health check must move with it). Do not "enable preserve_client_ip in terraform" and expect prod to change. |
| T1 is difflib not regex | [`scanner.py`](gateway/ai_mesh_gateway/scanner.py) `_segment_token` / `_fuzzy_scan` `difflib.SequenceMatcher` | Confirmed. Vectorscan-only is the wrong first T1 lever. |

### 11.4 Kill-switch snapshot — default until sign-off

D6's 2 s soft / 10 s hard KS staleness is **not** signed. Until product signs it, Phase 2 admit snapshot uses **TTL ≤ 50 ms positive cache** (or SSUBSCRIBE + ≤50 ms) and Redis-down still **disable**. Last-good-open is forbidden. Do not ship 2 s as the Fast SKU default.

TPM/budget: Redis-down **fail-closed** (today Lua fail-open). Burst/RPM GCRA overshoot bound = `WEB_CONCURRENCY × hosts × burst` — publish it; never apply that formula to TPM or KS.

### 11.5 Named edges the HLD must not drop

| Edge | Why it is in this overlay |
|---|---|
| Chat **chunked** body unbounded (`proxy_chat` `request.json()`) vs MCP `_mcp_read_body_capped` | 80 k in-flight OOM. Phase 1. |
| Empty HTTP POST **403** not nginx **411**; GCP HTTP frontend must not 301 POST | CDL pack `mcp-parallel/findings/cdl-aimesh-2026-08-21/`. |
| SSE timeout chain = `min(NLB 350, nginx 360, **gunicorn 120**, LiteLLM, GCP backend **30**, CDN origin)` | Align all ≥350 s or cap generation. |
| PG2 must scan flattened messages **and** `"".join(content parts)` (G70 is PII-only today) | Split jailbreak across content-array items. |
| Window aggregation is **stateful / contiguity**, never `max()` alone | D3 Prompt Overflow 100 % bypass. |
| vLLM prefix cache `cache_salt=org` | Cross-tenant KV. Only if Phase 5 stands up platform inference. |
| T2/PG2 cache key = `org + policy_version + classifier_digest + text` | Today `org+text` only. Phase 3. |
| `SAMPLE_RATE=1` on PG2; never copy `GATEWAY_TIER2_SAMPLE_RATE` skip on T1-allow | Security hole. |
| Classify timeout storm: 15 ms fail-closed on 100 k RPS = self-DoS; 451 poisons OpenAI SDKs (code already uses 503) | Separate timeout-block reason from `jailbreak`; storm-test benign +20 ms delay. |
| ONNX PG2 **in-process** × `WEB_CONCURRENCY=16` on a **15 GiB** host already over-committed (compose limits 22.6 GiB) | Stage 1 must count RSS (`22M INT8 × workers`). If it does not fit, drop workers or one shared sidecar **process** (not gunicorn `--preload` of GPU). |
| Policy/config pub/sub reconnect does not re-GET; no snapshot ⇒ not Ready | Else zero policies = allow. |
| `_merge_org_into_router` (422 cure) is on `ansh`, not `origin/main` | Ship it; do not rediscover. |

### 11.6 What the superseded Canonical Fast Gateway got wrong (keep the rest)

The 2026-08-22 Cursor "Canonical Fast Gateway" plan is **retired**. Useful corrections it missed that this MASTER already had: RC-1 logging, difflib T1, per-flush output Haiku, nginx not on NLB path, Valkey not "bigger Redis", potion-8M not MiniLM on the hop, Prompt Overflow vs `max()`, F/G/C SLO split vs locking C.

Where Canonical was **right** and is now overlay: Fast SKU / Haiku not sold; KS ≤50 ms until sign-off; Phase-1 50 ms cannot include Haiku; chat body cap; CDL empty POST; `"".join` scan; gunicorn 120 vs 350; compose `:8300` world-open; terraform ≠ prod.

### 11.7 Implementation order (unchanged, with overlay tasks inlined)

Phase 0 honesty → Phase 1 RC-1/RC-4/EVALSHA/nginx-on-/v1/body-cap → Phase 2 D10+D6 (50 ms KS cache unless signed) → Phase 3 PG2-22M + real 0.1 % sampler or drop it → Phase 4 Valkey/MSK/CH/ECS → Phase 5 GPU/g6e only above ~150 classify/s/host.

Do not start Phase 3 until Phase 1's **`T_addon_pre − T_t2`** gate passes. Do not start Phase 5 until Phase 3's 12 ms gate is measured on **this** fleet, not A100 cards.
