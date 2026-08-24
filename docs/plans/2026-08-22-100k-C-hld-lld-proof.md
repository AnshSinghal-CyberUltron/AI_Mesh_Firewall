# SLO C HLD / LLD proof — TrueFoundry, Kafka, ClickHouse, CDN, DNS, API gateway, Kubernetes

> **Status:** PLAN ONLY. Companion to `docs/plans/2026-08-21-100k-completed-chats-C-slo.md`.
> **Swarm:** `swarm-1787381519817-jlkaj4` (TrueFoundry, edge, data-plane, K8s).
> **Does not replace C.** Strengthens *where each component sits*. No code until the C plan is confirmed.

**One-line proof:** Industry 100k-class LLM systems split **admit proxy** (in-memory, CPU, few ms) from **GPU inference** from **async analytics**. They do **not** put ClickHouse, Kafka, CDN cache, PowerDNS, or AWS API Gateway on the generation hold. TrueFoundry’s published 350 RPS/vCPU is a **fake-OpenAI hop**, the same class as our 6-stage stub — not 100k completed 9-stage chats.

---

## 1. What TrueFoundry actually does (primary docs)

Sources (fetched 2026-08-22):

- https://www.truefoundry.com/docs/platform/gateway-plane-architecture
- https://www.truefoundry.com/blog/truefoundry-llm-gateway-is-blazing-fast (Jan 2026)
- https://www.truefoundry.com/docs/ai-gateway/ratelimiting
- Guardrails: generate+cancel for injection; output rails skipped on `stream: true` (product docs)

### Their HLD (copy the *physics*, not the *security*)

```
client → stateless Hono gateway pods (in-memory JWT, RBAC, token-bucket, LB)
       → LLM provider OR self-hosted vLLM/SGLang/TRT-LLM
       ↘ NATS (async): usage events + config fanout
          → aggregator → NATS → pod RAM (eventual rate/budget)
          → ClickHouse + blob (traces)
Control plane: Postgres (users, models, YAML policies)
```

Documented principles:

1. **No external calls** on client→LLM unless cache (they contradict this for Azure Prompt Shield).
2. Auth / rate / budget / LB **in memory**.
3. Logs to a **queue**, async.
4. **Never fail the chat if the log queue is down** (fail-open observability).
5. Gateway is **CPU-bound, horizontally scalable**.
6. Control plane ≠ data plane.

**Published bench (vendor, stub backend):** fake OpenAI that does not produce tokens. TrueFoundry +3–4 ms, ~350 RPS on 1 vCPU / 1 GB; LiteLLM Proxy stalled ~50 RPS on the same box. They claim a `t2.2xlarge` ~3000 RPS. That is **gateway hop RPS**, identical in kind to our NLB `/health` ~11k and 6-stage stub ~1.2k–3.2k/host.

### Copy vs refuse

| Copy | Refuse |
|---|---|
| Three fleets: CPU admit / GPU serve / async analytics | Generate+cancel for injection (model already saw the prompt) |
| In-memory admit after a control snapshot | Pure in-memory TPM as source of truth (N pods ⇒ N× quota) |
| NATS/Kafka/ClickHouse **off** the HTTP decision | Fail-open if log queue down **and** call it audited |
| PII **mutate before** the model | Skip output rails on streaming |
| Inference HPA on **RPS / waiting queue**, not CPU | Azure Prompt Shield / Model Armor as the 100k T2 |
| Prefix sticky routing + **org `cache_salt`** | Using LiteLLM **Proxy** as the public front door |
| Readiness: no config snapshot ⇒ pod not Ready | Marketing 350 RPS/vCPU as completed 9-stage chats |

TrueFoundry’s always-on injection detector is **Azure Prompt Shield** (~349 ms Palit class), raced with generation. ZeroShield `enforcement_mode=block` **cannot** copy that: we must scan **before** `model_input` (PII/secrets) and at `[DONE]` (output taxonomy), fail-closed.

---

## 2. Target HLD (layers)

```
                         ┌──────── CDN (CloudFront / Cloudflare) ────────┐
                         │  SPA + /api only.  NOT /v1/chat/completions   │
                         └───────────────────────────────────────────────┘
 SDK / browser
    │  HTTP/2 (multiplex streams)
    ▼
 Route53 alias + health  (PowerDNS only if multi-cloud split-horizon appears)
    │
    ▼
 NLB TLS:443  (L4)   preserve_client_ip ON
    │  idle 350s; no API Gateway; no ALB; no WAF tax
    ▼
 nginx  http2 on; proxy_buffering off; gzip OFF for text/event-stream
        upstream keepalive → gunicorn   (MISSING today — must add)
    ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │  GATEWAY CPU FLEET (AI Mesh) — 9 stages stay HERE                   │
 │  local GCRA burst/RPM · RAM policy · T1 Vectorscan                  │
 │  Redis: auth/KS fail-closed · TPM quota-LEASE (not N local buckets) │
 │  Guard HTTP: PG2 in  ·  LG1B at [DONE]                              │
 │  produce audit/trace to Kafka (bounded, never await)                │
 └───────────────────────────────┬─────────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
        Guard GPU EKS      Inference GPU EKS     Redpanda/MSK
        PG2 + LG1B         vLLM 8B exclusive GPU   aim.audit / aim.traces
        KEDA queue         KEDA waiting+KV%            │
                           cache_salt=org              ▼
                           Envoy GIE only              ClickHouse
                           *behind* Mesh               (threat-feed, traces)
                                                       Postgres = CONTROL only
```

Idle: original c8g + gateway extras **min 0**. Inference/guard **do not** scale to zero if C is a live SLO.

---

## 3. Component verdicts (evidence)

### 3.1 CDN

**UI/DDoS/TLS anycast: yes. Completions: no.** Unique POST + `Authorization` is uncacheable. CloudFront origin is HTTP/1.1 (loses H2 multiplex at the expensive hop). Origin read timeout default 30 s (quota 1–120 s) kills long SSE. Cloudflare 524 default 125 s. This repo already points CloudFront `/v1/*` TTL 0 with `compress=true` — compress can buffer SSE; keep `/v1` off CDN for C.

### 3.2 PowerDNS

**Not on the C path.** Route53 alias + Evaluate Target Health already does NLB failover. PowerDNS GeoIP/Views (5.0) is for self-hosted anycast / BIND-style split horizon. Single-region `ap-south-1` NLB does not need it. dnsdist is DNS, not SSE.

### 3.3 API Gateway (AWS HTTP APIs)

**Disqualify `/v1/chat/completions`.** Official quotas: integration timeout **30 s cannot increase**; **no response streaming**; 10 MB payload; account throttle **10k RPS default** (some regions 2.5k). REST STREAM still has edge idle 30 s and RPS/timeout tradeoff. Live BYOK p50 0.65–1.4 s can sneak under 30 s on happy path; 100k RPS and SSE cannot.

### 3.4 NLB vs ALB vs Kong vs Envoy vs APISIX vs Traefik

| Hop | C? |
|---|---|
| **NLB TLS:443 → :8300** | **Yes.** Already the data plane. Must turn on `preserve_client_ip` (`target_type=ip` today ⇒ ~55k SNAT/target). Live health@100k inflight: **138 ok_rps, 73% ConnectTimeout**. |
| ALB | No. L7 + WAF tax; H2 PING does not reset idle. |
| Envoy AI Gateway | **Behind** Mesh only: Endpoint Picker to vLLM. Do **not** replace T1/T2 with ext_proc body inspect. |
| Kong AI Gateway | Extra hop; **streaming currently doesn't work with HTTP/2**. Forces H1.1 = 80k TCP at 80k in-flight. |
| APISIX | Fast nginx+Lua; plugins overlap this product. Adds no GPU tokens/s. |
| Traefik Hub Content Guard | **Waits for the complete response** then one chunk. Opposite of SSE TTFT. |

`deploy/nginx.conf` already has `proxy_buffering off`. **No `upstream { keepalive }`** — today H2 client→nginx can still open **one backend TCP per request**. C needs a keepalive pool.

### 3.5 Kafka / NATS / queues (producer–consumer)

**NATS/Kafka = config + telemetry bus, not a chat work queue.**

TrueFoundry: NATS for config fanout + usage aggregates; chat still **HTTP-held**.

AI Mesh today: chat telemetry `LPUSH telemetry:events` → Postgres drain ~250/s. MCP used to `await` Django (~47 RPS). Same bug class at 100k.

| On the request (sync) | Off the request (produce, never await) |
|---|---|
| Auth, kill-switch, TPM lease, T1, PG2, **LLM generate to `[DONE]`**, T1 chunks, LG1B at DONE | Audit, `pipeline_trace`, threat-feed, Haiku 0.1–1% sample, MCP audit |

**LLM-on-Kafka with 202/ack-before-generate is not C.** vLLM’s *internal* batch queue is allowed because the client still waits.

Sizing: 100k chats/s × (audit+trace) ≈ **200k msg/s**. Start **12–24** partitions on audit, **16–32** on traces; key `org_id` except the 100k bench org (hot key → `org_id+worker`). Prefer **Redpanda or MSK**. ClickHouse **Kafka engine + MV**; `async_insert=1`; `wait_for_async_insert=1` on audit.

### 3.6 ClickHouse

**Analytics, not admit.** Postgres `EnforcementEvent` at 100k/s is **8.6B rows/day** — PERF-0008/0009 already struggle at hundreds of thousands of rows. ClickHouse for traces/threat-feed rollups; Postgres stays **control** (keys, policies, users). Redis lists are not Kafka-lite.

### 3.7 Redis

Stay on the hot path for **fail-closed** bits only: auth, kill-switch, **TPM lease**. Burst/RPM → local GCRA (TrueFoundry in-memory idea, but **TPM stays Redis** so N gateways cannot admit `N× org_tpm`). Do not put the telemetry list on the same hot keys.

### 3.8 Kubernetes LLD

Compose on c8g **cannot** host NVIDIA GPUs. EKS (or equivalent) for two GPU fleets:

| Fleet | CRD / engine | Autoscale | GPU mode |
|---|---|---|---|
| Chat model | KServe `LLMInferenceService` + vLLM 8B FP8 (NIM Operator alternate) | KEDA: `vllm:num_requests_waiting` + `gpu_cache_usage_perc` | **Exclusive** GPU. No time-slice. L4 has no MIG. |
| Guards | `InferenceService` PG2 + LG1B | KEDA classify queue | Exclusive L4s at 100k classify/s |
| Gateway | existing c8g ASG (or later Deployment) | in-flight / p95 added latency | CPU |

Do **not** autoscale inference on DCGM `GPU_UTIL` (pins ~100% at light and heavy decode).

Prefill/decode disaggregation (Mooncake / Dynamo / llm-d): **after** colocated C proof. Short 8B `max_tokens` 16–100 is colocated-bound, not P/D-bound. Needs RDMA.

Do **not** put TrueFoundry *in front of* Mesh (duplicates LiteLLM + part of the firewall). Do **not** replace Mesh with Envoy ext_proc guardrails.

---

## 4. Connection math (unchanged, now with edge proof)

`N = R × T`. 100k/s × 0.8 s = **80k in-flight** FDs (client + NLB + nginx + gunicorn + GPU).

- H1.1: 80k TCP. Kong-forbidding-H2 is fatal.
- H2 multiplex ~128 streams/conn: ~625–800 client TCP **if** clients multiplex. NLB still counts TCP flows.
- SNAT off: ~55k conn/target → **preserve_client_ip** or extra IP:ports.
- Keepalive mandatory for short JSON (100k new TCP/s would need ~110 SNAT targets at 55k conn/min).

---

## 5. Devil’s advocate (this research)

| Tempting add | Why it fails C |
|---|---|
| “Just use TrueFoundry / Kong / Envoy AI as the gateway” | Their hop is 3–12 ms **without** 9 fail-closed stages. We *are* the WAF. |
| “TrueFoundry did 350 RPS/vCPU so 300 replicas = 100k” | Bench used **fake OpenAI**. 300 replicas still wait on 80k GPU generations. |
| “Put chats on Kafka for scale” | Early ack = not completed. Late ack = same Little’s Law + extra hop. |
| “ClickHouse for rate limits” | OLAP p99 is not an admit oracle. |
| “CloudFront in front of `/v1`” | Uncacheable POST; origin H1.1; 30–120 s timeouts. |
| “API Gateway HTTP API” | 30 s, no SSE, 10k RPS default. |
| “PowerDNS anycast” | Zero tokens/s. |
| “Copy TF generate+cancel” | Prompt reaches the model; GPU still occupied; not fail-closed. |
| “Copy TF skip output on stream” | Dominant customer path has no semantic output stage. |
| “LiteLLM Proxy as front door” | Their own bench: ~50 RPS/vCPU. Keep LiteLLM as **library client** to `api_base`, not the VIP. |
| “P/D disagg on day one” | Ops + RDMA; not the 8B short-completion bottleneck. |
| “GPU time-slicing to save money” | Wrecks continuous batching; 8B+KV needs the card. |

---

## 6. Mapping onto AI Mesh (what changes vs what stays)

| Today | HLD change |
|---|---|
| NLB IP targets, no client-IP preserve | Enable `preserve_client_ip`; extra ports if needed |
| nginx `proxy_buffering off`, **no upstream keepalive** | Add keepalive pool; gzip off on SSE |
| CloudFront `/v1` compress | Chat VIP = NLB (or DNS-only). CDN = UI |
| FastAPI + LiteLLM inside workers | Keep Mesh as VIP. Point LiteLLM `api_base` at vLLM Service. Do not front with LiteLLM Proxy |
| Redis 6–9 RTTs + telemetry LIST → PG | Two-tier limiter; Kafka→CH for audit/traces |
| Haiku T2 inline | PG2/LG1B sidecar; Haiku 0.1–1% Kafka sample |
| `GUARDRAILS_SERVICE_URL` unused | Wire it |
| Compose + c8g ASG | Keep for CPU gateway; **add** GPU EKS fleets |
| Postgres EnforcementEvent | Control plane only |

---

## 7. Proof bar (still §8 of the C plan)

A 100k claim remains false unless `ok_rps ≥ 100000`, `error_rate ≤ 0.001`, non-stub bodies, non-zero `input_scan` / `output_guardrail`, `model_output.p50 ≥ 50`, in-region loadgen, streams to `[DONE]`. Adding Kafka/ClickHouse/K8s **without** that soak is not proof.

**Primary citations**

- TrueFoundry gateway plane + fake-OpenAI bench (URLs above)
- AWS HTTP API quotas; NLB target `preserve_client_ip` SNAT ~55k
- Envoy AI Gateway two-tier (edge vs inference cluster)
- Kong AI Gateway: streaming vs HTTP/2
- Traefik Hub: Content Guard waits for full body
- ClickHouse Kafka engine + `async_insert`
- KServe / NIM Operator: scale on `num_requests_waiting`, not GPU util
- This repo: `infra/terraform/modules/loadbalancers/main.tf` `target_type=ip`; `deploy/nginx.conf` `proxy_buffering off`; live `mcp-parallel/findings/nlb-100k-2026-08-21/`
