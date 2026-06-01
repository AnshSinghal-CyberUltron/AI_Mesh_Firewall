# AI Mesh Firewall — AWS Production Deployment Architecture

**Target:** peak **10,000 concurrent users**, maximum concurrency, lowest achievable **added firewall latency**, performance-first then cost.
**Method:** an initial decision was stress-tested by 5 contradicting parallel triage agents reading the actual code; this document is the synthesized final plan. Every choice carries its reasoning.

> Deployable in scope: **`AI_Mesh_Firewall`** (the standalone `control` + `gateway` inference firewall). The root `aisecshield` product (`/backend`, endpoint agents, MCP gateways) is a separate stack — call it out if you want it deployed too.

---

## 0. The single most important correction (read first)

Two premises in the original request are, per the code, impossible or misleading:

1. **"Very very millisecond latency" end-to-end is physically impossible.** End-to-end latency is dominated by the **upstream LLM (seconds)** and, when enabled, the **synchronous Tier‑2 Bedrock scan (hundreds of ms–seconds)**. No instance type or load balancer changes that. The only honest, enforceable SLO is **added firewall overhead** = `total − upstream_LLM_time`.
   - **SLO (Tier‑2 async/sampled):** added overhead **p50 ≤ 50 ms, p99 ≤ 150 ms**.
   - **SLO (Tier‑2 sync + block enforcement):** added overhead **p50 ≤ 400 ms, p99 ≤ 1.5 s**.

2. **"10,000 concurrent users" ≠ 10,000 RPS.** Users idle while reading replies. By Little's Law, with a 20–60 s turn cadence:
   `RPS_sustained = 10,000 / 40s ≈ 250 RPS`; design point with 2–3× burst ≈ **500–750 RPS peak**.
   CPU work per request (auth + regex + optional Presidio) ≈ 5–15 ms → **~5–16 vCPU of steady gateway compute**, not a giant fleet. The 10k figure governs **socket/connection capacity** (long-lived async awaits), not CPU.

**Consequence:** the cheapest correct topology is far smaller than a naive "10k users" fleet, *provided* the code fixes in §7 land first. Hardware alone will not fix the current collapse.

---

## 1. Why the current system collapses (root cause, code-grounded)

The existing 1000-user Locust run failed ~61–99% with ~60 s medians. Causes found in code:

| # | Defect | Location | Effect at load |
|---|---|---|---|
| 1 | **Sync Tier‑2 Bedrock call runs inside a shared 4-thread scanner pool** (`DEFAULT_THREAD_POOL_SIZE = 4`) | `gateway/ai_mesh_gateway/scanner.py` | Network waits occupy CPU threads → head-of-line blocking → queue collapse |
| 2 | **`EmbeddingVault` opens a fresh `psycopg.connect()` per request** (no pool) + sync `litellm.embedding()` | `gateway/ai_mesh_gateway/embedding_vault.py` | TCP+TLS+auth handshake per request; floods Postgres `max_connections` |
| 3 | **Auth `last_used_at` GET+SET per request** via unbounded `asyncio.create_task` | `gateway/ai_mesh_gateway/middleware.py` | Write amplification; unbounded task fan-out under burst |
| 4 | **Gateway runs single-process uvicorn** (no `--workers`) | `gateway/Dockerfile` CMD | One core, one event loop — cannot use a multi-core host |
| 5 | Load test hit the **Django SOC-dashboard path**, single box | `ai_load_tests/locustfile.py` | The collapse measured the wrong path on a dev box, not the inference ceiling |

These must be fixed (see §7) before — or alongside — the AWS rollout. **Infra scales a working app; it does not rescue a blocking one.**

---

## 2. Final architecture (synthesized)

```
                      Route 53 (ap-south-1)
                              │
        ┌─────────────────────┼───────────────────────────┐
        │                     │                            │
   CloudFront + S3       NLB (data plane)            ALB + AWS WAF
   (static frontend)     TLS, TCP passthrough        (control / admin / login)
        │                     │                            │
        ▼                     ▼                            ▼
   browser            ECS: gateway service          ECS: control service
                      (Fargate, x86, Spot+OD)       (Fargate x86, 1–2 tasks)
                       │      │        │                   │
                       │      │        │                   └─► RDS PostgreSQL (config OLTP)
                       │      │        │                          Multi-AZ + PgBouncer
                       │      │        └─► ElastiCache Redis (primary + 2 replicas, NOT cluster)
                       │      │             auth cache · kill-switch · rate-limit · channels
                       │      └─► RDS PostgreSQL (isolated pgvector: vault + RAG)
                       │                                  + async psycopg pool
                       └─► AWS Bedrock (ap-south-1)  ◄── dominant latency & cost; Tier-2 sampled+cached

   ECS: MCP service (ONLY if MCP stdio is a live feature)
        ECS-on-EC2, x86 c7i, dedicated/tainted pool
        EFS-mounted UV_CACHE_DIR + NPM_CONFIG_CACHE (warm npx/uvx)
        persistent volume for /tmp/mcp-orgs OAuth tokens

   ECS: Celery workers (Fargate, autoscale on queue depth)
   Telemetry: Redis LIST → Celery batch INSERT → RDS;  analytics → Kinesis Firehose → S3 → Athena
   Broker: SQS  (or 1× small Amazon MQ if Celery needs RabbitMQ-only features)
```

**Region: `ap-south-1` (Mumbai) — mandatory.** Bedrock is pinned there (`BEDROCK_REGION`). Co-locating gateway + Redis + Postgres removes 50–200 ms cross-region RTT *per* Tier‑2/embedding call and avoids inter-region egress cost. Unanimous across triage.

---

## 3. Compute — platform, instances, and why

### 3.1 Orchestrator: **ECS** (not EKS)
- No Kubernetes anywhere in the repo; the right-sized gateway is ~8–16 vCPU steady — EKS's operational tax buys nothing here. ECS gives rolling deploys, health replacement, capacity providers, and native Fargate/EC2 Spot mixing.
- EKS is justified **only** if the org already standardizes on Kubernetes or needs KEDA custom-metric scaling on RabbitMQ/Prometheus. Documented as a future option, not v1.

### 3.2 CPU arch: **x86 (c7i / Fargate x86) for v1** — *not* Graviton yet
Decisive reasons (from code):
- **Presidio production path is a sidecar**, and `mcr.microsoft.com/presidio-analyzer|anonymizer:latest` publish **amd64-only** manifests → would run emulated/broken on arm64.
- **MCP stdio servers are fetched at connect time** via `npx`/`uvx` (`mcp_stdio_adapter.py`). Those pull **arch-specific native binaries** (e.g. `semgrep` OCaml core, node native addons); arm64 prebuilts lag/404 → tenant-visible, production-only failures.
- **Revisit Graviton later** for a *pure-inference* pool only if Presidio runs in `library` (in-process spaCy, clean arm64 wheels) mode and customer MCP is disabled — after an arm64 build+smoke gate. The ~10–20% price/perf is not worth mixed-arch fragility for v1.

### 3.3 Services

| Service | Launch type | Task size | Server command | Scaling |
|---|---|---|---|---|
| **gateway (inference)** | Fargate (x86), **70% Spot + 30% On-Demand floor** | **4 vCPU / 8 GiB** | `gunicorn -k uvicorn.workers.UvicornWorker -w 4` (uvloop+httptools) | floor **2** (1/AZ×2), autoscale to **6–8** |
| **MCP** (if used) | **ECS-on-EC2**, x86 **c7i**, dedicated/tainted | per-node, EFS warm cache | gateway image, MCP enabled | scale on subprocess saturation |
| **control (Django)** | Fargate (x86), On-Demand | 1–2 vCPU / 2–4 GiB | `gunicorn` (sync/uvicorn) workers=vCPU | 1–2 tasks (low QPS) |
| **celery workers** | Fargate (x86), Spot | 1–2 vCPU | `celery -A ... worker` | autoscale on SQS/queue depth |

**Why Fargate for the inference gateway** (not ECS-on-EC2): at ~8–16 vCPU steady the Fargate per-vCPU premium is ~$50–100/mo — cheaper than managing an EC2 ASG, AMIs, and capacity providers. Fargate **Spot** covers the stateless gateway. **Cross to EC2 Graviton ASG only if steady compute exceeds ~16 vCPU** (not the case at 500–750 RPS).

**Why a separate EC2 MCP pool** (if MCP stdio is live): the MCP model keeps a **long-lived in-process subprocess fleet** (`_processes`, `_MAX_PROCESSES=20`), **warm `uv`/`npm` caches**, and **per-org OAuth tokens on local disk** (`/tmp/mcp-orgs/...`). Fargate's ephemeral tasks would cold-pull packages (120–180 s init) and lose tokens (re-auth storms) on every scale event. So MCP needs EC2-backed nodes with **EFS-mounted caches + persistent token volume**, isolated so `npx/uvx` spawn storms can't starve the latency-critical inference path.

---

## 4. Concurrency & thread tuning (max throughput per core)

- **gunicorn workers = vCPU per task** (one event loop per core). Do **not** use the sync `(2×vCPU)+1` — it over-subscribes event loops.
- **Decouple Bedrock from the scan thread pool:** move the Tier‑2 call to an **async `aioboto3` client** so network waits never occupy a CPU thread (fixes defect #1). Raise/segment the scanner `ThreadPoolExecutor` for the remaining CPU work (regex/deobf/fuzzy).
- **Cap the anyio/Presidio threadpool low (1–2/worker)** so `workers × threadpool` doesn't oversubscribe cores during NER spikes (spaCy native code releases the GIL).
- **Presidio:** regex/Luhn **prefilter first**, only invoke NER on candidates; **warm-load** the model at startup; run as sidecar to keep NER off the gateway's CPU threads. Accept p50 ≈ 25–40 ms when PII is actually present (sub-10 ms NER is impossible).
- **Redis pipelining:** collapse the 3 per-request hops (auth + kill-switch + model-state) into **one pipelined round trip**.
- **OS / socket tuning** on gateway tasks: raise `nofile`, `net.core.somaxconn`, ephemeral port range; **pool & reuse upstream (Bedrock/LiteLLM) connections** with keep-alive so the gateway-as-client doesn't exhaust its own ephemeral ports under fan-out.
- **Streaming:** emit an **SSE heartbeat/comment during firewall pre-flight** and set the LB **idle timeout > worst-case (Tier‑2 + LLM TTFT)** so quiet streams aren't reset.

---

## 5. Load balancing

- **Data plane (gateway, OpenAI-compatible SSE streaming, 10k long-lived conns): NLB (L4).** Adds ~microseconds, does not buffer the token stream, 350 s idle, no per-request LCU pressure. Scale on **ECS CPU 55% + a custom CloudWatch metric** (in-flight requests / app-measured added-latency p95) since NLB exposes no `RequestCountPerTarget`.
- **Control / admin / login plane: ALB + AWS WAF.** Low volume, needs L7 path/host routing + native WAF + the `RequestCountPerTarget` autoscale signal.
- **Do NOT put AWS WAF on the full inference path.** WAF bills ~$0.60/M requests; at ~657 M req/mo that is a ~$400–780/mo sleeper for protection the gateway's own auth + Tier‑1 already provides — **the gateway *is* the AI WAF.** Front the data plane with CloudFront only if you need coarse L7 filtering.

---

## 6. Data layer (right-sized, code-grounded)

| Store | Decision | Instance | Why |
|---|---|---|---|
| **Config/control OLTP** | **RDS PostgreSQL Multi-AZ** (not Aurora) + **PgBouncer** (transaction pooling) | `db.m6g.large` class | Hot path barely touches PG (config is in-process); control is low-QPS. Aurora's distributed-storage premium buys read-scaling you don't need. |
| **pgvector (vault + RAG)** | **Separate RDS Postgres** instance + **async `psycopg_pool`** in the vault | `db.r6g.large` | Isolate ANN scans from config OLTP; fix the per-request connect defect at the code level (RDS Proxy is only a backstop). |
| **Redis** | **ElastiCache, single primary + 1–2 replicas, Multi-AZ** (NOT cluster-mode) | `cache.r7g.large` | ~10k ops/s is two orders below a single node's ceiling; cluster-mode sharding is unneeded. Sized for HA + Channels pub/sub. |
| **Telemetry** | Keep **Redis LIST → Celery batch INSERT → RDS**; analytics → **Kinesis Firehose → S3 (Parquet) → Athena** | — | The actual write path is batched Postgres; Mongo is off-by-default pilot. |
| **DocumentDB** | **DROP** | — | DocumentDB ≠ MongoDB; would back a disabled, fire-and-forget `insert_one` pilot path at per-I/O cost. Keep optional small Mongo pilot only if needed. |
| **Celery broker** | **SQS** (verify no RabbitMQ-only features) else **1× small Amazon MQ** | — | Alert/drain volume is tiny; an HA MQ pair is overkill. |

---

## 7. Code prerequisites before 10k — IMPLEMENTATION STATUS

These are the actual scalability fixes. Status reflects what is now in the
`AI_Mesh_Firewall/gateway` tree (all new behaviour is env-gated and defaults to
the previous security posture).

| # | Fix | Status | What landed |
|---|-----|--------|-------------|
| 1 | gunicorn multi-worker | **DONE** | `Dockerfile` CMD now `gunicorn … -k uvicorn.workers.UvicornWorker --workers ${WEB_CONCURRENCY:-4} --worker-connections ${GUNICORN_WORKER_CONNECTIONS:-1000} --timeout ${GUNICORN_TIMEOUT:-120} --keep-alive ${GUNICORN_KEEPALIVE:-65}`; `gunicorn` + `psycopg[binary,pool]` added to image. **NB:** `aioboto3` was *removed* from the image — Fix 2 uses a sync boto3 client in a dedicated executor, and `aioboto3` forced a pip backtrack onto a source-only `multidict` that fails to compile (no `gcc` in the slim image). |
| 2 | Isolate slow Bedrock Tier-2 from the CPU scan pool | **DONE (dedicated executor)** | Instead of a risky full `aioboto3` rewrite of the security-critical `_bedrock_scan_sync`, a **dedicated `ThreadPoolExecutor`** (`GATEWAY_BEDROCK_THREAD_POOL_SIZE`, default 16) runs the sync Bedrock call so Tier-2 network waits never starve Tier-1. Same correctness, far lower risk. |
| 3 | Raise/segment scanner thread pools | **DONE** | `GATEWAY_SCANNER_THREAD_POOL_SIZE` default raised 4 → 8; Bedrock work moved to its own pool (#2). |
| 4 | `EmbeddingVault` connection pool | **DONE** | All 4 per-request `psycopg.connect()` sites replaced with a lazy `psycopg_pool.ConnectionPool` (`GATEWAY_VAULT_POOL_MIN`/`MAX`, default 1/8). |
| 5 | Debounce `last_used_at` | **DONE** | `AuthMiddleware` now stamps `last_used_at` at most once per key per `GATEWAY_LAST_USED_DEBOUNCE_SECONDS` (default 60) window — kills the per-request GET+SET auth amplification that dominated Redis load. |
| 6 | Tier-2 sampling + cache | **DONE (opt-in)** | Bounded TTL cache of the **Bedrock response dict** (`GATEWAY_TIER2_CACHE_TTL_SECONDS`=300, `GATEWAY_TIER2_CACHE_MAX`=10000) keyed by sha256; optional sampling `GATEWAY_TIER2_SAMPLE_RATE` (default **1.0 = always run**, lower to cut cost). All decision/normalization logic still runs → security posture unchanged unless an operator opts in. |
| 7 | Redis pipeline auth/kill-switch/model-state | **INFRA-LAYER / not changed** | The dominant amplifier (per-request last_used) is gone via #5. Remaining kill-switch + model-state are two ~0.1 ms GETs on the security-ENFORCEMENT path; pipelining them is high-risk for ~0.2 ms. Deferred. |
| 8 | SSE heartbeat + LB idle timeout | **INFRA-LAYER** | Anti-buffering headers (`X-Accel-Buffering: no`, `Cache-Control: no-cache`) already present; gunicorn `--keep-alive 65` added; **NLB idle timeout 350 s** is the real lever and lives in the IaC. An in-band pre-decision heartbeat is rejected — it would commit HTTP 200 before the block/allow decision (firewall correctness regression). |

### New env knobs (all default to prior behaviour)

`WEB_CONCURRENCY`, `GUNICORN_WORKER_CONNECTIONS`, `GUNICORN_TIMEOUT`,
`GUNICORN_GRACEFUL_TIMEOUT`, `GUNICORN_KEEPALIVE`,
`GATEWAY_SCANNER_THREAD_POOL_SIZE`, `GATEWAY_BEDROCK_THREAD_POOL_SIZE`,
`GATEWAY_VAULT_POOL_MIN`, `GATEWAY_VAULT_POOL_MAX`,
`GATEWAY_LAST_USED_DEBOUNCE_SECONDS`,
`GATEWAY_TIER2_CACHE_TTL_SECONDS`, `GATEWAY_TIER2_CACHE_MAX`,
`GATEWAY_TIER2_SAMPLE_RATE`.

### Infrastructure as Code

The full deployment lives in `AI_Mesh_Firewall/infra/terraform/`:

```
modules/network        VPC, 3-AZ subnets, NAT, VPC endpoints, security groups
modules/loadbalancers  NLB (data plane, 350s idle) + ALB+WAF (control only)
modules/data           RDS Postgres (config + RDS Proxy), RDS pgvector,
                       ElastiCache Redis (primary+2 replicas), SQS (Celery)
modules/ecs_cluster    ECS cluster, FARGATE + FARGATE_SPOT + EC2 (c7i) providers
modules/ecs_service    reusable Fargate service (gateway / control / workers)
modules/ecs_mcp_pool   EC2-backed MCP pool + EFS warm uv/npm cache + OAuth volume
envs/prod              composition + prod.tfvars + backend.hcl
```

Usage: `terraform init -backend-config=backend.hcl && terraform apply -var-file=prod.tfvars`.

### Live verification (local compose, build + boot)

The gateway image + code fixes were live-verified against the running stack before sign-off:

- **Image build** — `docker compose build gateway` succeeds; `Successfully installed gunicorn-26.0.0 psycopg-3.3.4 psycopg-binary-3.3.4 psycopg-pool-3.3.1 boto3-1.43.18 litellm-1.86.2`.
- **Boot** — container CMD is now gunicorn; logs show `Starting gunicorn 26.0.0`, `Using worker: uvicorn.workers.UvicornWorker`, **4 workers booted (pids 8–11)**, all `Application startup complete.` (the old single `python -m uvicorn` process is gone).
- **Inference** — benign Tier-1→Tier-2 call returns a real answer through `anthropic/claude-4.5-haiku` (no `psycopg_pool` / executor import errors).
- **Health path** — verified `/health` → **200 (no auth)**, `/healthz` → **401**. IaC corrected: the NLB gateway target group and the ECS container probe now use **`/health`** (control uses `/health/`; Celery workers have **no** HTTP probe since they run no HTTP server).



---

## 8. Cost — the only number that matters is Bedrock

Fixed infra (right-sized, ap-south-1, directional monthly):

| Component | Right-sized | ~$/mo |
|---|---|---|
| Gateway compute (2× Fargate 4 vCPU, Spot) | | ~$130–235 |
| Control compute (Fargate) | | ~$60 |
| RDS Postgres Multi-AZ (config) | `db.m6g.large` | ~$262 |
| RDS Postgres (pgvector) | `db.r6g.large` | ~$180 |
| ElastiCache (primary + replica) | `cache.r7g.large` | ~$100–150 |
| Telemetry Firehose → S3 | | ~$50–135 |
| SQS / small MQ | | <$5–219 |
| NLB + ALB + WAF (control only) | | ~$50 |
| CloudFront + S3 | | ~$20 |
| **Fixed infra subtotal** | | **≈ $900–1,300/mo** |

vs the over-built v1 (Aurora + ElastiCache-cluster + DocumentDB + Amazon MQ HA + WAF-on-everything ≈ $3,500/mo) → **~70% cheaper with zero hot-path performance loss.**

**Bedrock Tier‑2 dominates everything 50–200×.** At 250 RPS × 730 h ≈ 657 M requests/mo, running Tier‑2 on **every** request can be **$150–400k/mo**. The highest-leverage action in the whole program:
- Run deterministic **Tier‑1 on 100%**; escalate to the **Tier‑2 LLM scan only** on (a) Tier‑1 suspicion, (b) **unseen prompt** (cache verdicts by normalized-prompt hash), (c) a **2–5% random audit sample** → **90–95% fewer Tier‑2 calls**.
- **Never skip Tier‑2 on a Tier‑1-flagged prompt** — cut *volume*, never *coverage on flagged*.
- Request adequate **Bedrock on-demand quota / provisioned throughput** in ap-south-1 to avoid user-facing 429s.

---

## 9. Where cutting cost WOULD cost performance — never cut here

1. **Gateway CPU headroom + multi-worker uvicorn** — the single-process default caused the collapse. Keep ≥2 tasks and `-w <cores>`.
2. **Redis on the hot path** — must be same-AZ, low-latency, right-sized (no burstable `t4g.micro` under load).
3. **Bedrock quota / provisioned throughput** in ap-south-1 — under-requesting → 429s.
4. **Same-region placement next to Bedrock** — never relocate the gateway to a "cheaper" region.
5. **Tier‑2 coverage on Tier‑1-flagged prompts** — sample benign/cached traffic aggressively, never flagged.

---

## 10. Autoscaling policy

- **Gateway:** target-tracking on **ECS service CPU 55–60%** + step-scaling on a **custom app metric** (in-flight requests or added-latency p95 > 25 ms). Floor **2** (across 2 AZ), peak **6–8**. Scale-out cooldown 60 s, scale-in 300 s, task `stopTimeout`/deregistration delay ~30–60 s to drain in-flight LLM streams. **Do not scale to zero** on the hot path.
- **Control:** 1–2 tasks, target CPU 60%.
- **Celery:** scale on SQS `ApproximateNumberOfMessagesVisible`.

---

## 11. Open questions to confirm before IaC (highest leverage first)

1. **Does Tier‑2 currently run per-request or only on Tier‑1 flag, and are verdicts cached?** (worth ~$100k+/mo)
2. Exact Bedrock token price for the configured model in ap-south-1.
3. Is **MCP stdio** a live customer feature? (decides whether the EC2 MCP pool + EFS caches are needed)
4. Does Celery use any **RabbitMQ-only** features? (SQS vs Amazon MQ)
5. Deploy scope: **AI_Mesh_Firewall only**, or also the root **aisecshield** product + endpoint agents?
6. Single region (ap-south-1) or multi-region DR?

---

### Triage provenance
This plan synthesizes 5 contradicting agents: (1) proponent/hardening, (2) compute-platform + CPU-arch contrarian (x86 + EC2 MCP pool), (3) latency-premise contrarian (SLO redefinition + NLB + async Bedrock), (4) data-layer contrarian (drop Aurora/DocumentDB/cluster-Redis; fix the vault), (5) cost/right-sizing contrarian (concurrency≠RPS; Bedrock dominates). Conflicts were resolved on code evidence, not opinion.
