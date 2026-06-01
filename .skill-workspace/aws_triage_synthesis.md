

================================================================================
# PERFORMANCE-MAX
================================================================================

# PERFORMANCE-MAX AWS Deployment — AI Mesh Firewall (10k Concurrent Users)

**Stance:** Performance priority over cost. Every 0.1% latency regression from under-provisioning is unacceptable. This document contradicts linear “10% of 100k” math and any single-worker gateway defaults.

---

## Executive Summary

| Metric | Target | This Config |
|--------|--------|-------------|
| Concurrent connected sessions | 10,000 | 10,000 + 3× burst headroom |
| Peak ingress RPS | ~350 req/s (derived from 100k baseline) | Sized for **1,200 req/s** burst |
| Active streaming connections (~8%) | ~800 SSE/WebSocket | Sized for **2,500** |
| Gateway added latency SLO | p95 ≤ **25 ms** | Budget **≤ 12 ms** at p95 |
| Availability | 99.95% data plane | 3-AZ, N+2 on every tier |

**Bottom line:** Deploy at **~55–65% of the 100k gateway footprint**, not 10%. The prod Dockerfile’s **single uvicorn worker** is a development artifact — not a production configuration.

---

## 1. Recommended AWS Services (Performance-First)

### Compute orchestration: **EKS on EC2 (dedicated node groups)** — NOT ECS Fargate, NOT bare EC2 without orchestration

| Option | Verdict | Why |
|--------|---------|-----|
| **EKS + EC2 dedicated nodes** | ✅ **WINNER** | Pod density control, `topologySpreadConstraints`, SR-IOV/`c7gn` enhanced networking, placement groups per node group, no Fargate cold-start / CPU steal |
| ECS on EC2 | ⚠️ Acceptable fallback | Simpler ops, but weaker pod anti-affinity and less mature networking tuning |
| ECS/EKS Fargate | ❌ Reject | Shared CPU, no placement groups, unpredictable p99 under load |
| Raw EC2 ASG | ❌ Reject | No fast rollouts, no bin-packing, manual health orchestration |

**Node group strategy:**
- **Gateway node group:** `c7gn.12xlarge` (network-optimized Graviton3), **On-Demand only** (no Spot on hot path)
- **Worker node group:** `c7g.8xlarge`, mixed On-Demand + 20% Spot for non-latency queues only
- **Control node group:** `c7g.4xlarge`, 3-AZ spread
- **Enable:** EKS Auto Mode **disabled** (explicit instance control), **Cluster Autoscaler** with aggressive scale-up (0→N in <60s)

### Database: **Aurora PostgreSQL I/O-Optimized** — NOT RDS PostgreSQL, NOT Aurora Serverless v2

| Option | Verdict | Why |
|--------|---------|-----|
| **Aurora PostgreSQL I/O-Optimized** | ✅ | Predictable sub-ms reader latency, 15 read replicas on demand, pgvector on same cluster, zero I/O charge surprises at burst |
| RDS PostgreSQL Multi-AZ | ❌ | Failover 60–120s; reader scaling requires replicas with replication lag |
| Aurora Serverless v2 | ❌ | ACU scaling lag during policy-compile / telemetry spikes |

**Config:** `apg.r7g.4xlarge` writer + **2× `apg.r7g.2xlarge` readers**, Aurora Global Database **disabled** for single-region (add later for DR).

### Cache: **ElastiCache Redis Cluster Mode** — NOT self-hosted Redis, NOT Serverless Redis

| Option | Verdict | Why |
|--------|---------|-----|
| **ElastiCache Redis 7.x Cluster Mode** | ✅ | Sub-ms policy bundle reads; gateway hot path hits Redis on every request (`GATEWAY_POLICY_CACHE_ENABLED=true`) |
| ElastiCache Serverless | ❌ | Cold-scale latency spikes; unsuitable for p95 ≤ 25 ms SLO |
| Self-hosted Redis on EC2 | ❌ | Ops burden, no automatic failover, no online resharding |

**Config:** 3 shards × 1 replica = **6 nodes**, `cache.r7g.2xlarge`, `cluster-enabled yes`, TLS in-transit, **noeviction** (policy cache must never evict under load).

### Message queue: **Amazon MQ for RabbitMQ (cluster, mq.m5.4xlarge)** — NOT self-hosted, NOT SQS for Celery

Celery queues (`policy.compile`, `platform.batch`, `scan.tier2`, `vector.index`, `mcp.audit`) require AMQP semantics. Amazon MQ Multi-AZ cluster: **3 brokers**, `mq.m5.4xlarge`.

### Vector / ChromaDB: **Dedicated EC2 + EBS io2 Block Express** — NOT Fargate

ChromaDB + pgvector ingest (`vector.index` queue) is memory- and IOPS-bound. **3× `r7gd.4xlarge`** (NVMe local) in cluster placement group, **20 TB io2 Block Express** per node if using EBS-backed persistence.

### Presidio PII sidecars: **Dedicated ECS/EKS tasks on `c7g.4xlarge`**

Presidio analyzer/anonymizer are synchronous HTTP hops on the gateway hot path (`PRESIDIO_ANALYZER_URL`, `PRESIDIO_ANONYMIZER_URL`). Cost-hawks co-locate with gateway — **wrong**. Isolate:

- **6× Presidio analyzer** (2/AZ)
- **6× Presidio anonymizer** (2/AZ)
- Internal **NLB** with cross-zone load balancing, target type IP, connection idle timeout **3600s** (streaming)

### LLM / Tier-2: **Bedrock Provisioned Throughput** — NOT on-demand only

`ENABLE_TIER2=true`, `GATEWAY_TIER2_EXECUTION_MODE=sync_pre_llm` means Tier-2 can block the hot path. On-demand Bedrock throttles under burst → 502/503 → retry storms → latency death spiral.

**Minimum:** 2 provisioned model units per guard model + on-demand overflow.

### Observability (non-negotiable for performance ops)

- **Amazon Managed Prometheus + Grafana** (or Datadog APM with 1s granularity)
- **X-Ray** on gateway + control
- **CloudWatch Container Insights** with **1-minute** granularity (not 5-minute cost-hawk default)

---

## 2. Exact Instance Types and Counts (10k Concurrent Users)

### Traffic derivation (from `docs/DEPLOYMENT_100K.md`)

| 100k baseline | 10k linear (cost-hawk) | **10k PERFORMANCE-MAX** |
|---------------|------------------------|-------------------------|
| 100k sessions, 2–3.5k req/s | 200–350 req/s | **350 req/s sustained, 1,200 req/s burst** |
| 18× `c7g.4xlarge` gateway | 1.8 → **2 pods** ❌ | **9× `c7gn.12xlarge`** ✅ |
| 6/AZ gateway | 0.6/AZ ❌ | **3/AZ + N+2** ✅ |

**Why not 2 gateways?** Scale trigger in your own doc: `in-flight per pod > 5k connections`. At 10k sessions with keep-alive + SSE, a single AZ failure + burst puts **>5k on remaining pods** → immediate SLO breach. Target **≤1,500 connections/pod** at steady state (6× headroom).

### Full sizing table

| Tier | Component | Instance | Count | AZ spread | Autoscale max |
|------|-----------|----------|-------|-----------|---------------|
| Edge | ALB + WAFv2 | Managed | 1 | Multi-AZ | — |
| Edge | NLB (gateway TCP) | Managed | 1 | Multi-AZ | — |
| Edge | Global Accelerator | Managed | 1 | 3 endpoints | — |
| **Data** | **Gateway** | **`c7gn.12xlarge`** | **9** | **3/AZ** | **18** |
| Data | Guardrails service | `c7i.8xlarge` | 6 | 2/AZ | 12 |
| Data | MCP broker | `c7g.4xlarge` | 6 | 2/AZ | 9 |
| Data | Vector retrieval | `r7gd.4xlarge` | 6 | 2/AZ | 9 |
| Data | Presidio analyzer | `c7g.4xlarge` | 6 | 2/AZ | 9 |
| Data | Presidio anonymizer | `c7g.4xlarge` | 6 | 2/AZ | 9 |
| Control | Django control API | `c7g.4xlarge` | 6 | 2/AZ | 9 |
| Control | Policy compiler (Celery) | `c7g.4xlarge` | 3 | 1/AZ | 6 |
| Async | Celery workers (mixed) | `c7g.8xlarge` | **12** | 4/AZ | **24** |
| Async | Celery beat | `c7g.large` | 2 | 2/AZ (active/passive) | — |
| Cache | ElastiCache Redis | `cache.r7g.2xlarge` | **6** (3 shard + 3 replica) | Multi-AZ | — |
| OLTP | Aurora PostgreSQL | `apg.r7g.4xlarge` + 2× `apg.r7g.2xlarge` | 1+2 | Multi-AZ | +2 readers |
| Queue | Amazon MQ RabbitMQ | `mq.m5.4xlarge` | 3 (cluster) | Multi-AZ | — |
| Vector DB | ChromaDB | `r7gd.4xlarge` | 3 | Multi-AZ | 6 |
| Frontend | Next.js (if self-hosted) | `c7g.2xlarge` | 3 | 1/AZ | 6 |

**Total vCPU (gateway only):** 9 × 48 = **432 vCPU** dedicated to hot path.  
Cost-hawk 2× `c7g.4xlarge` = 32 vCPU → **13.5× under-provisioned** on compute alone.

---

## 3. Gateway Process Model: Uvicorn / Gunicorn / Thread Pools

### Current state (problem)

```38:38:gateway/Dockerfile
CMD ["python", "-m", "uvicorn", "ai_mesh_gateway.main:app", "--host", "0.0.0.0", "--port", "8300"]
```

**Single worker = single event loop = GIL contention** when `GATEWAY_SCAN_THREAD_POOL_SIZE` thread pools fire for Presidio, scanner, vector, embedding vault, LLM judge (all use `ThreadPoolExecutor(max_workers=scan_thread_pool_size)`).

Dev Dockerfile uses `--workers 9` — closer, but still not tuned to instance size.

### PERFORMANCE-MAX production command

**Per `c7gn.12xlarge` pod (48 vCPU, 96 GiB):**

```bash
gunicorn ai_mesh_gateway.main:app \
  --bind 0.0.0.0:8300 \
  --workers 24 \
  --worker-class uvicorn.workers.UvicornWorker \
  --threads 2 \
  --worker-connections 2000 \
  --timeout 120 \
  --keep-alive 75 \
  --max-requests 50000 \
  --max-requests-jitter 5000 \
  --preload
```

| Parameter | Cost-hawk | **PERFORMANCE-MAX** | Rationale |
|-----------|-----------|---------------------|-----------|
| Workers | 1 (prod Dockerfile) | **24** | 48 vCPU ÷ 2 (async I/O leaves headroom for thread pools) |
| Worker class | uvicorn solo | **UvicornWorker via gunicorn** | Graceful reload, worker isolation, crash containment |
| `--worker-connections` | default ~1000 | **2000** | 10k sessions ÷ 9 pods ≈ 1,111; 2000 gives burst room |
| `--keep-alive` | 5s | **75s** | Matches ALB idle timeout; avoids TCP re-handshake tax |
| `GATEWAY_SCAN_THREAD_POOL_SIZE` | **4** (default) | **32** | 4 threads × 6 scan call sites = queueing under parallel scans; 32 eliminates thread starvation on Presidio + tier-2 fan-out |
| `LITELLM_NUM_RETRIES` | 2 | **1** | Retries double tail latency; circuit breaker handles failures |
| `GATEWAY_TIER2_STREAM_HOLD_TIMEOUT_MS` | 1200 | **800** | Fail fast to async queue rather than block stream |
| Uvicorn `--limit-concurrency` | unset | **1800/worker** | Backpressure before OOM; 24×1800 = 43k theoretical (never hit) |

**Aggregate gateway capacity:**
- 9 pods × 24 workers = **216 async workers**
- 9 pods × 32 scan threads = **288 concurrent scan operations**
- Target steady connections: 10k ÷ 9 ≈ **1,111 conn/pod** (well under 5k scale trigger)

### Celery worker concurrency (async plane)

| Queue | Workers | Concurrency/worker | Instance |
|-------|---------|-------------------|----------|
| `scan.tier2` | 6 | `--concurrency=16` | `c7g.8xlarge` |
| `platform.batch` | 4 | `--concurrency=8` | `c7g.8xlarge` |
| `vector.index` | 2 | `--concurrency=4` | `c7g.8xlarge` |
| `policy.compile` | 2 | `--concurrency=4` | `c7g.4xlarge` |
| `mcp.audit` | 2 | `--concurrency=8` | `c7g.4xlarge` |
| `compute.heavy` | 2 | `--concurrency=4` | `c7i.8xlarge` |

Cost-hawk: 4 total workers → `scan.tier2` depth > 100 within minutes of launch (per your own scale signal in `DEPLOYMENT_100K.md`).

---

## 4. Network Architecture

```
                    ┌─────────────────────┐
                    │  Global Accelerator │  ← Static Anycast IPs, TCP edge
                    │  (Premium tier)     │
                    └──────────┬──────────┘
                               │
              ┌────────────────┴────────────────┐
              │         ALB (HTTPS/WSS)        │  ← WAFv2, OIDC, stickiness
              │    idle_timeout = 4000s        │
              └────────────────┬────────────────┘
                               │
              ┌────────────────┴────────────────┐
              │    NLB (TCP :8300 passthrough) │  ← Preserve source IP, ultra-low L4 latency
              │    cross_zone = enabled        │
              └────────────────┬────────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
    Gateway AZ-a           Gateway AZ-b           Gateway AZ-c
    (cluster PG)           (cluster PG)           (cluster PG)
    c7gn.12xlarge ×3       c7gn.12xlarge ×3       c7gn.12xlarge ×3
```

### Mandatory network optimizations

| Feature | Setting | Why it beats cheaper alternatives |
|---------|---------|-----------------------------------|
| **Cluster placement group** | `strategy=cluster` on gateway node group | **<10 µs** inter-node latency for internal fan-out; cost-hawk spread placement adds 200–400 µs |
| **Enhanced networking (ENA Express)** | Enabled on `c7gn` | Up to **25 Gbps** per instance, lower PPS tax |
| **ALB target group** | `least_outstanding_requests` | `round_robin` causes hot pods; LOR reduces p99 by 15–40% under skew |
| **Stickiness** | Disabled on gateway | SSE/WebSock

================================================================================
# COST-HAWK
================================================================================

# COST-HAWK: AWS Architecture for 10k Concurrent Users

**Product:** AI Mesh Firewall (gateway FastAPI, control Django, Celery, Redis, Postgres, RabbitMQ, Bedrock upstream)  
**Target:** 10k **connected** sessions, gateway p95 added latency ≤ 25 ms (per `docs/ARCHITECTURE.md`)  
**Stance:** Match SLOs with math; reject fleet sizing that treats 10k like 100k with a smaller multiplier.

---

## Executive verdict

| Approach | Gateway fleet | Monthly infra (order of magnitude) |
|----------|---------------|-------------------------------------|
| **PERFORMANCE-MAX mistake** | 6–18× `c7g.4xlarge` “because 100k doc” | **$2.5k–7.6k** gateways only |
| **Linear 1/10 of 100k doc** | ~2× each tier, same instance classes | **~$3.5k–4.5k** full stack |
| **COST-HAWK (recommended)** | **3–4× `c7g.xlarge`** (+ ASG to 6× `c7g.2xlarge`) | **~$1.8k–2.5k** on-demand; **~$1.2k–1.8k** with SP/RI on floor |

Bedrock/API spend is **usage-based** and excluded below; it often dominates at high token volume.

---

## 1. Minimum viable AWS architecture (10k)

### Traffic model (from `docs/DEPLOYMENT_100K.md`, scaled 10:1)

| Metric @ 100k | @ 10k (10:1) |
|---------------|--------------|
| Connected sessions | 10,000 |
| Peak ingress | **~200–350 req/s** |
| Active streams (~8%) | **~800** |
| Scale trigger: connections/pod | **> 5,000** (unchanged) |

Hot path: **ALB → gateway → Redis (policy bundle) → optional guardrails/MCP/vector → Bedrock**. Control plane and Celery are **off** the synchronous enforcement path.

```
                    ┌─────────────┐
  Clients ──► WAF ─►│     ALB     │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         GW x3-4      GW (ASG)     (3 AZ)
      c7g.xlarge    max 6×c7g.2xlarge
              │
    ┌─────────┼─────────┬──────────────┐
    ▼         ▼         ▼              ▼
 Redis    Guardrails  MCP      Vector*
 (2-node)  1-2×large   1×large   0-1×large
              │
    ┌─────────┴─────────────────────────┐
    ▼                                   ▼
 Control 2×large (ASG 3)          Celery 3-5×large SPOT
    │                                   │
 Aurora db.r7g.large              Amazon MQ
 writer + 1 reader                mq.m5.large MAZ
    │
 pgvector on Aurora** (preferred over 6× r7gd.4xlarge)

* Collapse vector tier into Aurora pgvector unless measured QPS to dedicated service.
** Aligns with docker-compose pgvector pattern.
```

### Component floor (HA, not toy)

| Tier | MV sizing | Why |
|------|-----------|-----|
| **Gateway** | **3× `c7g.xlarge`** (1/AZ), ASG max **6× `c7g.2xlarge`** | Meets conn + req/s math; headroom for TLS/streaming |
| **Guardrails** | **2× `c7g.large`** | Sidecar hop; scale on p95, not gateway count |
| **MCP broker** | **1× `c7g.large`** (+ 1 standby if WS-heavy) | Burst connector traffic, not 6× 2xlarge |
| **Vector** | **0 dedicated** → Aurora pgvector; else **1× `r7g.large`** | 100k doc’s 6× `r7gd.4xlarge` is indexing fleet, not 10k read path |
| **Control** | **2× `c7g.large`** (ASG max 3) | Admin/API; 99.9% SLO, not ms proxy SLO |
| **Celery** | **3–5× `c7g.large` Spot** + 1 on-demand base | Queues absorb telemetry/Tier-2; **scale workers first** (per doc) |
| **Redis** | **1 shard: `cache.r7g.large` + replica** (2 nodes) | Policy bundles + rate limit; not 6× xlarge cluster |
| **Postgres** | **Aurora `db.r7g.large` writer + 1 reader** | Not `r7g.4xlarge` + 2 readers |
| **RabbitMQ** | **Amazon MQ `mq.m5.large` Multi-AZ** | Not 3-node large cluster |
| **Mongo telemetry** | **Skip** or single `r7g.large` pilot | Optional per doc |
| **Edge** | ALB + WAF | Non-negotiable for public SaaS |

**Fargate vs EC2:** Use **EC2 (Graviton) for gateway** — long-lived connections and lower $/connection. Fargate is fine for **control** bursts at ~30–40% premium; poor default for 10k connection fan-in.

---

## 2. Cheapest instance types that meet SLO (with math)

### A. Connection capacity (doc’s own rule)

From `docs/DEPLOYMENT_100K.md`:

> Scale gateway when **in-flight per pod > 5k connections**

| Fleet | Capacity @ 5k/pod | Utilization @ 10k | Verdict |
|-------|-------------------|-------------------|---------|
| 18× `c7g.4xlarge` | 90,000 | **11%** | Absurd for 10k |
| 6× `c7g.4xlarge` | 30,000 | 33% | Still 3× over |
| 4× `c7g.xlarge` | 20,000 | 50% | Healthy + N+1 |
| **3× `c7g.xlarge`** | 15,000 | 67% | **Minimum HA** (1/AZ) |
| 2× `c7g.2xlarge` | 10,000 | 100% | **No N+1** — reject |

**Cheapest HA gateway:** **3× `c7g.xlarge`** (~$106/mo each → **~$318/mo** on-demand us-east-1).

### B. Request throughput (CPU)

Assumptions for enforcement-heavy proxy (Redis hit, optional guardrails, some streaming):

- Sustainable **~400–800 req/s** per `c7g.xlarge` (4 vCPU) with async I/O and warm policy cache  
- Conservative planning: **350 req/s** peak @ 10k → **1× xlarge** suffices for CPU; **3×** for AZ failure + spike

$$\text{Instances}_\text{req} = \lceil 350 / 500 \rceil = 1 \quad \Rightarrow \quad \text{HA floor} = 3$$

### C. Streaming memory

~800 concurrent streams × ~2–5 MB buffers (order of magnitude) → **~2–4 GB** cluster-wide, fits in **8 GiB × 3** hosts with headroom. Not a driver for `c7g.4xlarge`.

### D. Latency purist counter-argument

**Claim:** “You need 16 vCPU per node for sub-ms.”  
**Rebuttal:** Architecture SLO is **p95 ≤ 25 ms added**, not sub-ms end-to-end. Bedrock RTT is hundreds–thousands of ms. Bottleneck is **upstream + policy cache hit**, not raw EC2 GHz. Oversized CPU does not fix Bedrock.

### E. “5 cheaper EC2 same performance” check

| Option | Nodes | vCPU total | Conn headroom | Est. gateway $/mo |
|--------|-------|------------|---------------|-------------------|
| 5× `c7g.large` | 5 | 10 | 25k @ 5k/pod | **~$290** |
| 3× `c7g.xlarge` | 3 | 12 | 15k | **~$318** |
| 3× `c7g.2xlarge` | 3 | 24 | 15k | **~$630** |
| 6× `c7g.4xlarge` | 6 | 96 | 30k | **~$2,530** |

**Recommendation:** **5× `c7g.large`** or **3× `c7g.xlarge`** — both meet SLO with >40% connection headroom; pick xlarge if p95 tightens under load test. **Do not** jump to 4xlarge without metrics.

---

## 3. What NOT to over-provision

| Component | 100k doc | 10k mistake | Right-size |
|-----------|----------|-------------|------------|
| **Gateway** | 18× `c7g.4xlarge` | 6× 4xlarge “2/AZ” | **3–5× xlarge/large** |
| **Guardrails** | 12× `c7i.4xlarge` | 2× 4xlarge | **2× large**; add on p95 |
| **MCP broker** | 6× `c7g.2xlarge` | 2× 2xlarge | **1–2× large** |
| **Vector** | 6× `r7gd.4xlarge` | 2× large metal | **Aurora pgvector** |
| **Control** | 6× `c7g.2xlarge` | 3× 2xlarge | **2× large** |
| **Workers** | 20× mixed 2xlarge/4xlarge | 10× on-demand | **3–5× large Spot** |
| **Redis** | 6× `r7g.xlarge` | 3-shard cluster | **1 primary + 1 replica large** |
| **Aurora** | `r7g.4xlarge` + 2 readers | Same class | **`r7g.large` + 1 reader** |
| **Beat** | — | Dedicated large | **Share control or 1× small** |

**Rule:** At 10k, **gateway count is driven by connections (÷5k) and AZ spread, not by copying 6/AZ from 100k.** Scale **Celery** when queue age/depth triggers fire (doc lines 30–34), not when gateway CPU is idle.

---

## 4. Reserved vs Savings Plans vs Spot

| Workload | Strategy | Rationale |
|----------|----------|-----------|
| Gateway floor (3× xlarge) | **1-yr Compute Savings Plan** (~30–40% off) | Steady 24/7 |
| Gateway burst (ASG +3) | **On-demand** | Pay only when scaled |
| Control (2× large) | **Compute SP** (shared with gateway) | Predictable |
| Celery (3–5× large) | **Spot 70–90% off** + **on-demand base ≥1** | Fault-tolerant async; doc-aligned |
| Guardrails/MCP | **On-demand** or small SP after load test | |
| ElastiCache / Aurora | **1-yr RI or DB SP** on writer | Stable data plane |
| **Avoid** | 3-yr RI on gateway max, RI on Spot pools | Locks overspend |

**Order of purchase:** (1) Measure 2-week p95 + conn/pod, (2) **Compute SP** for gateway+control floor, (3) **DB/cache RI** for writer, (4) **Spot** worker ASG with diversified pools (`c7g.large`, `c7g.xlarge`).

**Do not** buy RIs for the **48-gateway autoscale max** in the 100k doc — that cap is for 100k, not your floor.

---

## 5. Challenge PERFORMANCE-MAX: 18 large gateways unnecessary for 10k

### Proof 1 — Doc’s scaling metric

$$\frac{10{,}000 \text{ sessions}}{5{,}000 \text{ sessions/pod}} = 2 \text{ pods minimum}$$

With **3 AZ + N+1:** **3–4 pods**, not 18.

18 pods @ 10k ⇒ **~556 connections/pod** ⇒ **~9%** of documented scale threshold ⇒ **~9× wasted gateway EC2** if someone copies fleet count.

### Proof 2 — Req/s

100k peak **2–3.5k req/s** ⇒ 10k **~200–350 req/s**. That is **one** well-tuned xlarge class node; three for HA.

### Proof 3 — 100k doc fleet implies 90k conn cap, not 100k magic

$$18 \times 5{,}000 = 90{,}000 \text{ connections at scale trigger}$$

100k design already plans **near limit** then autoscales to 48. For 10k you are **not** in that regime.

### Proof 4 — AZ fan-out ≠ instance class

“6 per AZ” at 100k is **load distribution + blast radius**, not “minimum 6 everywhere.” At 10k: **1 per AZ + spare** beats **6 per AZ on 4xlarge**.

### Common bad take

> “We use 6× `c7g.4xlarge` for 10k to be safe.”

That delivers **30k** connection capacity and **96 vCPU** for **~350 req/s** — safety margin on the **wrong dimension** (CPU/RAM), while **Bedrock quotas and Redis** remain real limits.

---

## 6. Estimated monthly cost (us-east-1, infra only)

On-demand list approximations; **±15%** by region/discounts.

### COST-HAWK (recommended)

| Line item | Spec | ~$/mo |
|-----------|------|-------|
| Gateway | 3× `c7g.xlarge` | 318 |
| Gateway ASG buffer | +0–3× `c7g.2xlarge` avg 1 | 210 |
| Guardrails | 2× `c7g.large` | 116 |
| MCP | 1× `c7g.large` | 58 |
| Control | 2× `c7g.large` | 116 |
| Workers | 4× `c7g.large` Spot (~$0.025/hr eff.) | 72 |
| ElastiCache | `cache.r7g.large` × 2 | 250 |
| Aurora | `db.r7g.large` writer + reader | 520 |
| Amazon MQ | `mq.m5.large` MAZ | 280 |
| ALB + WAF + logs | — | 150–350 |
| NAT + data transfer | — | 100–300 |
| **Total** | | **~$2.0k–2.5k** |
| **With SP/RI on floor** | | **~$1.2k–1.8k** |

### Premium configs (contrast)

| Config | ~$/mo | Notes |
|--------|-------|-------|
| **“6× gateway 4xlarge for 10k”** | **~$2,530** gateways only | 3× connection headroom on wrong SKU |
| **“18× gateway 4xlarge” (copy-paste)** | **~$7,600** gateways only | **Marketing-tier waste** |
| **Linear 1/10 of full 100k doc** | **~$3.5k–4.5k** | Still oversized Redis/vector/guardrails |
| **Full 100k doc as written** | **~$35k–45k** | Reference only |

**Savings vs premium 10k:** **~40–55%** vs proportional doc; **~70%+** vs 18×4xlarge mistake.

---

## 7. Where cost cuts ARE unsafe

| Cut | Risk |
|-----|------|
| **Single AZ** | Violates HA; AZ failure = total outage |
| **1 gateway instance** | No N+1; deploy/blast radius kills 10k sessions |
| **Redis single node, no replica** | Policy bundle/cache loss; fail-closed or stale policy |
| **Remove WAF** | OWASP/API abuse on public ingress |
| **Aurora without PITR / backups** | Compliance + unrecoverable policy/tenant data |
| **mq.t3.micro / single-node broker** | Telemetry backlog, Tier-2 stall, cascading gateway timeouts |
| **Spot-only Celery with zero on-demand** | Spot reclaim → queue SLA miss |
| **Skip guardrails entirely** | Product/security regression, not “cost optimization” |
| **Under-provision Bedrock quotas** | 429/throttle presents as “gateway latency” |
| **NAT skimp → VPC endpoints removed** | Surprise egress + latency |
| **Collapse control + gateway on one fat box** | Noisy neighbor; control spikes hit proxy SLO |

**Safe optimizations:** Smaller Graviton SKUs, fewer gateway **replicas**, Spot workers, Aurora pgvector vs 6× vector metal, single Redis shard, **load-test gates** before each downgrade.

---

## Load-test gates before production (non-optional)

1. **10k idle/long-poll connections** — conn/pod < 4k, p95 added latency < 25 ms 

================================================================================
# SRE-SKEPTIC
================================================================================

# SRE-SKEPTIC Triage: AI Mesh Firewall on AWS (10k Concurrent)

**Role:** Challenge unrealistic latency expectations. Separate what the gateway controls (milliseconds) from what Bedrock/Anthropic controls (seconds).

**Verdict upfront:** “Very very millisecond latency” for 10k users is **only meaningful for gateway-added overhead**, not for chat completion end-to-end. Anyone selling sub-100 ms **total** LLM response time at 10k concurrency is ignoring physics and upstream token generation.

---

## 0. Rebuttal to LATENCY-PURIST

| Claim | Reality |
|-------|---------|
| “10k users need ms latency” | **Whose ms?** Auth + policy + Redis on the hot path: yes, target **p95 ≤ 25 ms added** (per `docs/ARCHITECTURE.md`). Full `/v1/chat/completions`: **1–30+ seconds** dominated by LLM. |
| “Redis-only hot path = fast everything” | Hot path avoids Postgres; it still does **Tier-1 regex**, optional **Presidio HTTP**, **policy eval**, optional **sync Tier-2 Bedrock scan** (another LLM call), then **upstream LLM**. Sync Tier-2 alone can add **500 ms–3 s**. |
| “Streaming fixes latency” | Streaming improves **TTFB** (time to first token), not total completion time. Post-response policy checks are **skipped** when `stream: true` — a security/latency tradeoff, not a free win. |
| “Scale gateway to 48 pods for 10k” | `DEPLOYMENT_100K.md` max (48) is for **100k connected** (~2–3.5k req/s). At 10k, starting with **6–9 gateways** (2–3/AZ) is sane; scaling to 48 without load evidence is **over-provisioning**. |

The codebase already encodes this split: Prometheus histogram buckets go to **10 s**, and `stage_metrics` tracks `auth_ms`, `policy_ms`, `tier1_ms`, `tier2_ms`, `upstream_ms`, `telemetry_enqueue_ms` separately — not a single “ms” number.

---

## 1. Honest Latency Budget Breakdown

### A. Gateway-added latency (what you can SLO)

These run **before/during** proxy setup; Postgres is **not** on this path.

| Stage | Typical (p50) | p95 target | Notes |
|-------|---------------|------------|-------|
| **Edge (ALB + WAF)** | 1–3 ms | 5–10 ms | TLS termination, rule eval; cross-AZ adds ~1 ms |
| **Auth (Redis lookup)** | 0.5–2 ms | 3–5 ms | SHA-256 + `auth:apikey:{hash}`; fail-closed 503 if Redis down |
| **Rate limit / kill switch / model state** | 0.5–2 ms | 3–5 ms | Additional Redis round-trips |
| **Policy bundle eval (in-memory + Redis cache)** | 1–5 ms | 8–15 ms | Signed bundle; fail-closed if cache unloaded |
| **Tier-1 input scan (regex/deterministic)** | 1–10 ms | 15–25 ms | Local CPU; grows with prompt size |
| **Presidio sidecar (if enabled)** | 5–30 ms | 50–150 ms | Extra HTTP hop; not “ms-class” at p95 |
| **Telemetry enqueue (Redis LPUSH)** | 0.5–2 ms | **≤ 5 ms** | Architecture SLO; async drain via workers |
| **Gateway framing / JSON / SSE setup** | 1–3 ms | 5 ms | Negligible vs LLM |

**Gateway-added p95 (Tier-1 only, no Presidio, no sync Tier-2):** **15–25 ms** — aligns with architecture SLO.

**Gateway-added p95 (sync Tier-2 Bedrock scan enabled):** **500 ms–3 s+** — this is a **second LLM invocation**, not gateway overhead. Do not fold into “25 ms added” without redefining the SLO.

### B. Upstream / end-to-end (what you cannot SLO at ms)

| Stage | Typical | Dominates? |
|-------|---------|------------|
| **Bedrock / Anthropic RTT + queue** | 200 ms–2 s (TTFB) | Yes for streaming |
| **Token generation** | 2–60+ s | Yes for completion |
| **LiteLLM proxy overhead** | 5–50 ms | Minor |
| **Output guard (non-streaming)** | 10–500 ms | Depends on response size |
| **RAG vector retrieval (if path used)** | 20–200 ms | Circuit-breaker protected |

**End-to-end p95 for a single chat completion:** **2–15 s** (model-dependent). **Not milliseconds.**

### C. Streaming-specific

- **TTFB SLO (user-perceived “fast”):** first SSE chunk after gateway preflight — budget **gateway_added + upstream_TTFB** (often **300 ms–2 s**).
- **Documented tradeoff:** streaming skips post-response policy checks; optional `tier2StreamHold` adds **500–2000 ms** hold before stream starts.
- **Concurrent streams at 10k users:** if ~8% active streaming (per 100k assumptions), expect **~800 long-lived connections** — connection memory and ALB idle timeout matter more than CPU.

---

## 2. Realistic SLOs for 10k Concurrent

Define **“10k concurrent”** precisely:

| Interpretation | Implied load | Gateway replicas (start) |
|----------------|--------------|--------------------------|
| 10k **connected** sessions (idle-heavy) | ~200–350 req/s peak | 6–9 (2–3/AZ) |
| 10k **in-flight** requests | Not realistic for LLM proxy | Would need hundreds of gateways + massive upstream quota |

### Recommended SLO table

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Gateway availability** | 99.95% | `/health` + synthetic chat probe |
| **Gateway-added latency** | **p95 ≤ 25 ms** | `stage_metrics` sum excluding `upstream_ms` and `tier2_ms` when Tier-2 async |
| **Telemetry enqueue** | **p95 ≤ 5 ms** | `telemetry_enqueue_ms` |
| **Auth/policy fail-closed** | 503 with `Retry-After` | Redis/policy cache degraded |
| **End-to-end chat (non-streaming)** | **p95 ≤ 15 s** (model SLA) | `upstream_ms` + tokens; set per model in control (`latency_sla_ms` default 30s) |
| **Streaming TTFB** | **p95 ≤ 2 s** | First byte to client |
| **Error rate (gateway-origin 5xx)** | **< 0.3%** over 5 min | Excludes upstream 502 from Bedrock throttle |

### What “millisecond latency” can honestly mean at 10k

1. **Policy enforcement tax** on the hot path: **yes, tens of ms**.
2. **Time to first token:** **no**, unless models are tiny and prompts are short.
3. **P99 anything end-to-end:** **seconds**, full stop.

---

## 3. Autoscaling Policies (Gateway vs Workers)

From `docs/ARCHITECTURE.md` and `docs/DEPLOYMENT_100K.md`:

### Scale **gateway** when (data plane)

| Signal | Threshold | Why |
|--------|-----------|-----|
| **p95 added latency** | > 25 ms for 5 min | Hot path saturation |
| **In-flight per pod** | > 5k connections | Event-loop / memory pressure |
| **5xx rate** | > 0.3% over 5 min | Fail-closed storms, Redis, policy cache |

**Do not** scale gateway for RabbitMQ backlog — that’s async plane.

### Scale **workers** when (async plane)

| Queue | Scale signal (from architecture) |
|-------|-------------------------------|
| `policy.compile` | Depth > 100 for 2 min |
| `platform.batch` | Oldest message age > 10s (10 min sustained per deployment doc) |
| `compute.heavy` | CPU-saturated workers |
| `scan.tier2` | p95 task time > 30s **or** depth > 100 for 2 min |
| `vector.index` | Backlog growth |
| `mcp.audit` | Connector burst |

**Additional worker signal:** CPU > 70% **and** queue depth rising.

### What not to conflate

| Symptom | Wrong scale | Right scale |
|---------|-------------|-------------|
| Telemetry lag in UI | More gateways | More `platform.batch` workers |
| Tier-2 scan backlog | More gateways | More `scan.tier2` workers |
| Slow chat responses | More gateways | Upstream quota, model choice, disable sync Tier-2 |
| Redis latency spikes | More gateways | ElastiCache shard/replica, connection pool tuning |

Gateway jobs enqueue via **Redis LPUSH** (`gateway/ai_mesh_gateway/jobs.py`), not synchronous RabbitMQ — worker drain lag shows up in `telemetry:events` queue depth (surfaced on `/health`).

---

## 4. ECS vs EKS Recommendation

### Recommendation: **ECS on Fargate** (primary), EKS only if already standard

| Factor | ECS + Fargate | EKS |
|--------|---------------|-----|
| **Ops burden** | Lower — no control plane patching, no CNI tuning | Higher — node groups, upgrades, RBAC, add-ons |
| **Fit for this architecture** | Excellent for stateless gateway, guardrails, control, workers | Good if team already runs K8s |
| **Autoscaling** | Target tracking on ALB p95 latency + CPU | HPA/VPA + cluster autoscaler — more knobs, more foot-guns |
| **Stateful deps** | ElastiCache, Aurora, Amazon MQ — **managed either way** | Same |
| **Blue/green / canary** | CodeDeploy + ALB native | Argo Rollouts / Flagger — powerful, complex |
| **Cost at 10k** | Right-sized tasks; no idle nodes | Often over-provisioned node pools “for headroom” |

**Reasoning:** Hybrid split (gateway + control monolith + Celery workers) maps cleanly to ECS services. The team’s own triage notes **“POC/dev ready; production no-go until P0”** — adding EKS operational surface before production hardening is a distraction.

**When EKS wins:** Existing platform team, service mesh requirements, multi-cluster federation, or strict pod-level security policies already standardized.

**Multi-AZ (both):** Minimum **2 AZ**, target **3 AZ** for gateway (per deployment guide). Redis (ElastiCache cluster mode), Amazon MQ Multi-AZ, Aurora with reader — all required for AZ failure, not optional polish.

---

## 5. Health Checks, Circuit Breakers, Queue Backpressure

### Health checks (already in codebase)

| Endpoint / check | Behavior |
|------------------|----------|
| **`GET /health`** | 503 if signing key missing, policy cache required but unloaded |
| **Redis auth** | 503 + `Retry-After: 5` — fail-closed |
| **Dependency healthchecks** | postgres, redis, rabbitmq, chromadb, presidio in `docker-compose.yml` |
| **`telemetry_queue_depth`** | Exposed on `/health` — use as worker backpressure signal |

**Production probes:**
- **Liveness:** `/health` (or `/livez` if split) — process up
- **Readiness:** policy cache loaded + Redis reachable — **remove from ALB when degraded**
- **Do not** use upstream Bedrock health on every kube probe — rate limits and cost

### Circuit breakers (in code)

| Breaker | Scope | Effect |
|---------|-------|--------|
| **Model circuit breaker** | Per-model error rate in Redis | Block/reroute when OPEN |
| **Tier-2 Bedrock breaker** | Per (org, scanning_model) | Strict → HTTP 451; non-strict → degrade pass |
| **Vector retriever breaker** | RAG path | OPEN → block retrieval |
| **Kill switch / model isolation** | Org-scoped Redis | 503 block or reroute |

**SRE note:** Breakers protect **upstream and platform scan models**, not user latency. OPEN state **increases** perceived latency (blocks/reroutes) to prevent cascade.

### Queue backpressure

| Layer | Mechanism | Failure mode if ignored |
|-------|-----------|-------------------------|
| **Gateway TPM rate limit** | Per-org Redis counters | 429 + `Retry-After` |
| **Telemetry Redis list** | `telemetry:events` depth | Memory pressure; stale dashboards |
| **RabbitMQ** | Celery queues | Oldest message age > 10s → audit/alert lag |
| **Tier-2 async jobs** | `scan.tier2` queue | Deferred security scans pile up |

**Backpressure policy:** When `telemetry_queue_depth` sustained high, scale workers **before** gateways. When Redis memory high, increase drain batch or add workers — **not** disable telemetry silently.

---

## 6. Load Test Plan Before Production Sizing

Follow the deployment doc ladder: **10k → 25k → 50k → 100k**. Do not skip straight to 10k “concurrent” without defining workload mix.

### Phase 1 — Baseline (1 gateway, dev compose parity)

- Smoke: auth, policy block, allow, kill switch
- Measure `stage_metrics_ms` in telemetry metadata
- Confirm fail-closed: stop Redis, corrupt policy cache

### Phase 2 — 10k connected simulation (AWS staging)

**Workload mix (mandatory):**

| Mix | % | Purpose |
|-----|---|---------|
| Non-streaming, Tier-1 only | 40% | Baseline added latency |
| Non-streaming, sync Tier-2 | 20% | Expose second-LLM tax |
| Streaming | 30% | Connection count, TTFB |
| Blocked (policy/scan) | 10% | Fast-path vs full path |

**Tooling:** k6 or Locust with SSE support; separate scenarios for streaming.

**Metrics to capture:**

- `amf_gateway_request_latency_seconds` histogram (note: **includes upstream**)
- Derived: `auth_ms + policy_ms + tier1_ms` (exclude `upstream_ms`, `tier2_ms` for gateway SLO)
- `amf_gateway_active_connections`
- `telemetry_queue_depth` on `/health`
- RabbitMQ: oldest message age per queue
- ElastiCache: `EngineCPU

================================================================================
# SECURITY-AUDITOR
================================================================================

# AI Mesh Firewall — Security-First AWS Architecture (SECURITY-AUDITOR Triage)

**Scope:** Production deployment at ~100k concurrent sessions (see `docs/DEPLOYMENT_100K.md`).  
**Threat model:** Multi-tenant AI security gateway handling API keys, encrypted LLM provider credentials, policy bundles, PII (Presidio), Bedrock Tier-2 scanning, and control-plane admin access.

**Verdict:** POC/dev compose is **not production-ready**. Static `AWS_ACCESS_KEY_ID` on gateway, shared Redis without TLS, and plaintext policy Pub/Sub are explicit gaps. Below is the minimum secure baseline; anything that trades isolation for cost is flagged.

---

## Executive Summary

| Principle | Requirement |
|-----------|-------------|
| Zero-trust network | Only ALB (+ optional CloudFront) is internet-facing |
| Identity | ECS/EKS task roles (IRSA on EKS); **no long-lived AWS keys on gateway** |
| Secrets | AWS Secrets Manager for high-value keys; SSM Parameter Store for non-secret config |
| Defense in depth | WAF (coarse) + gateway auth/TPM/RPM (fine-grained, tenant-aware) |
| Encryption | KMS CMK for Aurora, ElastiCache, Secrets Manager, S3; TLS everywhere in transit |
| Audit | CloudTrail (org trail), VPC Flow Logs, ALB/WAF logs → immutable store |

---

## 1. VPC Design

### Recommended topology (3 AZ, N+1)

```
Internet
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Public subnets (per AZ)                                │
│  • Internet-facing ALB (gateway + control UI/API)       │
│  • NAT Gateway (1 per AZ — NOT shared single NAT)       │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Private subnets — Data plane (per AZ)                  │
│  • Gateway, Guardrails, MCP broker, Vector, Presidio    │
│  • No public IPs, no IGW routes                         │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Private subnets — Control plane (per AZ)             │
│  • Control API (Django), Celery workers/beat            │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Isolated subnets — Data tier (per AZ)                  │
│  • Aurora PostgreSQL                                    │
│  • ElastiCache Redis                                    │
│  • Amazon MQ (RabbitMQ)                                 │
│  • No NAT route — egress only via VPC endpoints         │
└─────────────────────────────────────────────────────────┘
```

### CIDR & segmentation

| Tier | Purpose | Route table |
|------|---------|-------------|
| Public | ALB, NAT | `0.0.0.0/0 → IGW` |
| Private (app) | ECS/EKS tasks | `0.0.0.0/0 → NAT` (minimal); prefer endpoints |
| Isolated (data) | Aurora, Redis, MQ | **No internet route** |

**Security groups (least privilege):**
- ALB SG: `443` from `0.0.0.0/0` (or CloudFront prefix list)
- Gateway SG: `8300` **only from ALB SG**
- Control SG: `8000` from ALB SG + gateway SG (internal proxy)
- Data SGs: DB/cache/MQ ports **only from app SGs**, not CIDR-wide

### VPC endpoints (mandatory for zero-trust data tier)

| Endpoint | Type | Why |
|----------|------|-----|
| `com.amazonaws.<region>.bedrock-runtime` | Interface | Tier-2 scanning; no NAT egress for Bedrock |
| `com.amazonaws.<region>.secretsmanager` | Interface | Secret fetch at task startup |
| `com.amazonaws.<region>.ssm` | Interface | Parameter Store |
| `com.amazonaws.<region>.kms` | Interface | Decrypt Secrets Manager / Aurora |
| `com.amazonaws.<region>.ecr.api` + `.ecr.dkr` | Interface | Image pull without public egress |
| `com.amazonaws.<region>.logs` | Interface | CloudWatch Logs |
| `com.amazonaws.<region>.sts` | Interface | IRSA / role assumption |
| `s3` | Gateway | Policy bundle artifacts, ALB logs, backups |

**COST-HAWK challenge:**  
- ❌ **Single NAT Gateway across 3 AZ** — AZ failure + bandwidth choke; acceptable only in non-prod. Prod: **1 NAT/AZ**.  
- ❌ **Gateway in public subnet with public IP** — removes ALB as sole choke point; direct DDoS on app.  
- ❌ **Skip Bedrock endpoint, use NAT** — exposes egress path; harder to restrict with SG/NACL; higher data-exfil risk.

---

## 2. IAM Roles Per Service

**Rule:** Gateway must **never** receive `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in prod (currently in `.env.sample` and `docker-compose.yml` — dev-only).

### Compute model

| Option | Recommendation |
|--------|----------------|
| **ECS on Fargate** | Task roles per service; no instance profile sharing |
| **EKS** | IRSA per Deployment; separate K8s ServiceAccounts |
| **EC2 ASG** (per DEPLOYMENT_100K) | Instance profile **only for SSM/CloudWatch**; app AWS calls via **task/container role** where possible |

### Role matrix

| Service | Task role permissions (minimum) | Must NOT have |
|---------|----------------------------------|---------------|
| **Gateway** | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` on specific model ARNs; `secretsmanager:GetSecretValue` on gateway secret; `kms:Decrypt` for those secrets | `s3:*`, `iam:*`, `secretsmanager:*`, cross-account |
| **Control** | Secrets read (Django, signing, encryption, Graph OAuth); optional S3 for exports | Bedrock (unless control invokes Tier-2 directly) |
| **Workers** | Same DB secrets as control; `ses:SendEmail` or Graph via external creds in Secrets Manager; no Bedrock unless worker path needs it | Broad write to Secrets Manager |
| **Guardrails / Presidio / Vector / MCP** | No AWS API unless embedding via Bedrock — then scoped Bedrock only | Shared role with gateway |
| **Amazon MQ** | AWS-managed; app uses username/password from Secrets Manager | — |
| **Aurora** | IAM DB auth optional; prefer password in Secrets Manager + rotation | Public accessibility |

### Internal service auth (application layer)

Product already uses:
- `GATEWAY_INTERNAL_API_KEY` — control → gateway server-to-server (`mcp_proxy.py`, control views)
- Gateway API keys — tenant inference auth
- `POLICY_SIGNING_KEY` — HMAC on policy bundles (must match control + gateway)

**IAM does not replace these.** Store in Secrets Manager; inject at runtime; rotate independently of AWS credentials.

### IRSA example policy snippet (gateway Bedrock)

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream"
  ],
  "Resource": "arn:aws:bedrock:*::foundation-model/openai.gpt-oss-120b-1:0"
}
```

Scope to org-approved model IDs per environment; use SCPs to deny unapproved regions.

---

## 3. Secrets Management

### Secrets Manager vs SSM Parameter Store

| Secret | Store | Rotation | Notes |
|--------|-------|----------|-------|
| `DJANGO_SECRET_KEY` | **Secrets Manager** | Lambda rotation or manual + rolling deploy | Django sessions, CSRF; rotation requires all control/worker/gateway pods reload |
| `POLICY_SIGNING_KEY` | **Secrets Manager** | **Dual-key grace period** (comma-separated keys per `UPGRADE.md` roadmap) | Must be **identical** on control + gateway; missing key → gateway `/health` 503 |
| `FIELD_ENCRYPTION_KEY` | **Secrets Manager** | Re-encrypt migration required | Fernet for LLM/OAuth keys in Postgres (`encrypted_fields.py`); **do not** fall back to `SECRET_KEY` in prod |
| `GATEWAY_INTERNAL_API_KEY` | **Secrets Manager** | On compromise or quarterly | HMAC compare on gateway |
| Aurora master / app password | **Secrets Manager** | RDS rotation integration | |
| Redis auth token | **Secrets Manager** | ElastiCache rotation | |
| RabbitMQ credentials | **Secrets Manager** | Amazon MQ rotation | |
| Microsoft Graph (`CLIENT_SECRET`) | **Secrets Manager** | Azure app rotation | Alert email path |
| Org LLM API keys | **Not in Secrets Manager** | Per-tenant in DB | Encrypted at rest via `FIELD_ENCRYPTION_KEY` |

**SSM Parameter Store (Standard):** Non-secret config — `BEDROCK_REGION`, feature flags, `ALLOWED_HOSTS`, autoscale thresholds.

**COST-HAWK challenge:**  
- ❌ **Single `.env` on shared EC2** — any container escape or SSH compromise exfiltrates signing key + DB + Bedrock-equivalent access.  
- ❌ **Reuse `DJANGO_SECRET_KEY` as `POLICY_SIGNING_KEY`** — explicitly removed in Phase 0.1; violates separation of duties.

### Rotation runbooks (critical)

**`POLICY_SIGNING_KEY` rotation (zero-downtime):**
1. Generate `key_new`; set env to `key_new,key_old` on control (sign with primary) and gateway (verify any).
2. Publish new bundles; confirm all gateways accept.
3. Remove `key_old` after TTL > max bundle propagation + Redis pub/sub lag.

**`DJANGO_SECRET_KEY` rotation:**
1. Rolling restart control + workers; existing sessions invalidate (acceptable maintenance window).
2. Gateway uses it only for ancillary crypto — audit usages before rotation.

**`FIELD_ENCRYPTION_KEY` rotation:**
1. Requires Django data migration re-encrypt (documented in `encrypted_fields.py`).
2. **Never** rotate without migration — permanent ciphertext loss.

---

## 4. WAF + Rate Limiting

### ALB WAF (AWS WAFv2) — coarse edge protection

Attach to **gateway ALB** (primary attack surface: `/v1/chat/completions`, streaming SSE).

| Rule group | Purpose |
|------------|---------|
| AWS Managed — Core rule set | SQLi, XSS baseline |
| AWS Managed — Known bad inputs | Log4j-style patterns |
| AWS Managed — Bot Control (optional) | Scraping, credential stuffing |
| **Rate-based rule** | e.g. 2000 req/5 min **per IP** (tune per expected client NAT) |
| **Geo block** (if applicable) | Restrict admin UI to corp regions |
| **Size restrictions** | Body > N MB → block (LLM payloads can be large; set 1–4 MB default, allowlist streaming paths) |
| **Custom — block paths** | `/.env`, `/admin` on wrong host, scanner fingerprints |

Enable WAF logging → S3 (KMS encrypted) + CloudWatch; sample rate 100% for security events.

### Gateway internal rate limiter — fine-grained, tenant-aware

Product implements (must remain authoritative for billing/fairness):

| Layer | Mechanism | Fail mode |
|-------|-----------|-----------|
| TPM (per API key) | Redis fixed-window Lua (`rate_limiter.py`) | **Fail-closed** in `rate_limit_enforcement.py` |
| Burst RPM (per org) | Redis per-second bucket (`main.py`) | Configurable block vs audit |
| Model RPM | Per-model ceiling | Tenant isolation |

**Division of responsibility:**

| Concern | WAF | Gateway |
|---------|-----|---------|
| DDoS / volumetric | ✅ Primary | Absorb remainder |
| Per-tenant TPM/RPM | ❌ No tenant context | ✅ Primary |
| Auth bypass attempts | ✅ IP throttle | ✅ 401/403 + telemetry |
| Streaming abuse | ✅ Connection/IP limits | ✅ Org burst limits |

**COST-HAWK challenge:**  
- ❌ **"WAF only, disable Redis rate limits"** — no per-org TPM; one tenant exhausts Bedrock quota for all.  
- ❌ **WAF rate limit = tenant limit** — clients behind corporate NAT get unfairly blocked; gateway key hash is the correct dimension.

**Recommendation:** WAF IP rate limit = **10–50×** expected per-IP legit traffic; gateway enforces contractual limits.

---

## 5. Encryption

### At rest

| Asset | Encryption | Key |
|-------|------------|-----|
| Aurora PostgreSQL | AES-256 | **Customer-managed KMS CMK** |
| ElastiCache Redis | At-rest encryption | Same CMK |
| Amazon MQ | AWS-managed | AWS owned |
| Secrets Manager | Automatic | CMK |
| S3 (logs, backups) | SSE-KMS | CMK |
| EBS (if EC2) | Encrypted volumes | CMK |
| Tenant LLM keys in DB | Fernet (`enc:` prefix) | `FIELD_ENCRYPTION_KEY` via HKDF |

**Gap (roadmap G13):** Policy bundles on Redis Pub/Sub are HMAC-signed but **not encrypted**. Anyone with Redis read sees rule metadata. Mitigation until G13 ships:
- Redis in isolated subnet + auth token + TLS
- Restrict SG to gateway/control only
- Enable ElastiCache RBAC if available

### In transit

| Hop | Requirement |
|-----|-------------|
| Client → ALB | TLS 1.2+ (ACM cert) |
| ALB → Gateway | TLS or HTTP on privat

================================================================================
# LATENCY-PURIST
================================================================================

# LATENCY-PURIST Triage: AI Mesh Gateway on AWS (10k Concurrent)

**Target:** Gateway-added p99 ≤ 25 ms (excludes upstream LLM inference time)  
**Codebase facts:** Single uvicorn worker (no `--workers`), `GATEWAY_SCAN_THREAD_POOL_SIZE=4`, default `BEDROCK_REGION=ap-south-1`, policies enforced from **in-memory** `PolicySync` (not per-request Redis GET), Tier-2 Bedrock runs **sync** in thread pool via `boto3.invoke_model`.

---

## Latency Budget (What Actually Runs on Hot Path)

| Stage | Mechanism | Typical p99 (same AZ) | Notes |
|-------|-----------|----------------------|-------|
| Auth | Redis `GET auth:apikey:{hash}` | 0.5–2 ms | Pooled (`GATEWAY_REDIS_MAX_CONNECTIONS=300`) |
| Rate limit | Redis Lua | 0.5–1.5 ms | Separate pool (100 conn) |
| Kill switch | Redis pipeline (2–3 GETs) | 0.5–2 ms | Shared `REDIS_CLIENT` |
| Policy | In-memory `policy_engine.evaluate()` via `asyncio.to_thread` | 0.2–3 ms | **Not** Redis on hot path |
| Tier-1 scan | Regex in `ThreadPoolExecutor(4)` | 1–8 ms | CPU-bound offload |
| Tier-2 Bedrock | Sync `invoke_model` in same thread pool | **150–800+ ms** | Dominates if `sync_pre_llm` |
| Presidio | **Not on `/v1/chat/completions`** | 0 ms | MCP path only |

**Verdict:** Sub-25 ms p99 is achievable **only when Tier-2 is off the pre-LLM path** (or rarely invoked). Default `tier2_execution_mode=sync_pre_llm` + `ENABLE_TIER2=true` makes sub-25 ms impossible for guarded requests.

---

## 1. Optimal AWS Region / AZ Strategy

### Region: **ap-south-1 (Mumbai)** — keep it

- `BEDROCK_REGION` default and docker-compose already point here.
- Gateway, ElastiCache, and Bedrock Runtime must be **same region**. Cross-region Bedrock adds 50–150 ms+ RTT and invalidates the 25 ms budget.
- Control plane (Django/backend) can live in the same region; hot path should not call backend for policy when `PolicySync` is loaded.

### AZ placement

| Component | Placement | Rationale |
|-----------|-----------|-----------|
| Gateway tasks (ECS/EKS) | **All 3 AZs** behind ALB | HA + even connection spread |
| ElastiCache | Multi-AZ with primary + replica | Failover; accept ~0.5–1 ms cross-AZ RTT to primary for writes |
| Bedrock | Regional service endpoint | No AZ pin; co-locate gateway in region |

**Cross-AZ cost:** ~1 ms RTT per Redis hop. At 4–6 sequential Redis ops, cross-AZ can add **4–6 ms** to p99. Mitigations:

- Deploy gateway replicas evenly across AZs (ALB does this by default).
- ElastiCache primary in the AZ with highest gateway density, **or** accept uniform cross-AZ latency (simpler ops).
- Do **not** stretch gateway to another region for “DR” on the hot path — use failover region as cold standby only.

**Optional:** AWS PrivateLink interface endpoint for Bedrock Runtime in VPC — removes public internet variance; latency gain is modest (~1–3 ms) but p99 stability improves.

---

## 2. ElastiCache: Cluster Mode vs Single Shard

### What Redis is used for (hot path)

Redis is **not** a per-request policy lookup. Policies are loaded at startup + Pub/Sub (`PolicySync`). Hot-path Redis:

- API key auth
- TPM rate limiting (Lua)
- Kill-switch reads
- Circuit breaker / telemetry (async buffered)

### Recommendation: **Single-shard primary + replica (non-cluster mode)**

| Option | When to use | Latency impact |
|--------|-------------|----------------|
| **Single shard (r7g.xlarge or r7g.large)** | ≤ ~100k ops/sec, ≤ 25 GB working set | **Lowest** — no MOVED/ASK redirects |
| Cluster mode | Memory > single node, or >250k sustained ops/sec | +0.1–0.5 ms per redirect; Lua slot constraints |

For 10k concurrent users:

- Assume 2–5k RPS peak (mix of chat + policy checks), ~4–6 Redis ops/request → **8–30k Redis ops/sec** — well within a single `cache.r7g.large`.
- Auth keys are high-cardinality but read-heavy; rate-limit keys are time-bucketed.

**Sizing:** `cache.r7g.large` (2 vCPU, 13 GB) minimum; `cache.r7g.xlarge` if large org/key cardinality or telemetry pressure.

**Do not** use cluster mode “for policy cache” — policy cache is **in-process memory** on each gateway replica. Redis cluster would only help auth/rate-limit key volume, which doesn’t justify cluster overhead at 10k concurrency.

**Connection pooling:** Auth middleware pools to 300; rate limiter to 100; shared `REDIS_CLIENT` uses `from_url` without explicit pool — **consolidate to one shared pool** in ops tuning (not a code change here, but ops should set `max_connections` ≥ replicas × concurrent coroutines).

---

## 3. Gateway Process Model: Uvicorn Workers vs Single Worker + Async

### Recommendation: **Single uvicorn worker per container — scale replicas, not workers**

This matches production Dockerfile and is correct for this codebase.

| Factor | Single async worker | Multiple uvicorn workers |
|--------|--------------------|-------------------------|
| Event loop | One loop handles 10k+ idle SSE connections efficiently | Each worker = duplicate loops, **duplicate memory** |
| `PolicySync` / `ConfigSync` | One in-memory cache per process | N copies; N× Redis pub/sub subscribers |
| Bedrock thread pool | 4 threads **per process** | 4×N threads → throttle storms, quota burn |
| Streaming / SSE | Pure ASGI middleware (no body buffering) | Sticky sessions required; connection stickiness fragile |
| CPU-bound work | Offloaded to `ThreadPoolExecutor(4)` | Same, but GIL + duplication |

**Why FastAPI async is the right model here:**

- I/O waits (Redis, upstream LLM via `litellm.acompletion` / `acompletion_stream`) are non-blocking.
- Sync work (regex policy engine, Tier-1, boto3 Bedrock) is explicitly offloaded to a **bounded** thread pool (`GATEWAY_SCAN_THREAD_POOL_SIZE=4`).
- Adding workers increases parallel Bedrock calls per host without increasing async connection capacity proportionally — it **worsens** Tier-2 tail latency under load.

**Horizontal scaling formula:** `replicas = f(concurrent_connections, tier2_in_flight)`, not `workers × replicas`.

---

## 4. Presidio: Sidecar vs Embedded vs Skip on Hot Path

### Chat hot path (`/v1/chat/completions`): **Already skipped**

Presidio is wired in `mcp_proxy.py` / `presidio_engine.py` for MCP tool payloads, not in `proxy_chat`. **No action needed for chat latency.**

### MCP hot path options

| Mode | Latency | Ops complexity | Recommendation |
|------|---------|----------------|----------------|
| **Skip** (`PRESIDIO_MODE=disabled`) | 0 ms | Lowest | Latency-purist default for MCP gateway pool |
| **Embedded library** | 5–50 ms (spaCy CPU, blocks thread) | Medium — image size + model load | Dev / low-QPS MCP only |
| **Sidecar HTTP** | 3–15 ms + network (same-host/same-AZ) | High — extra containers | Only if compliance mandates Presidio on MCP |

**Latency-purist stance:**

- **Dedicated MCP gateway pool** with Presidio enabled (embedded or sidecar on `localhost`/same task ENI).
- **Chat gateway pool** with Presidio disabled — Tier-1 regex already covers PII/secrets on chat path via `patterns.py`.
- If sidecar: reuse persistent `httpx` client (code already does for library mode probe); place analyzer in **same task** (ECS sidecar) not separate service AZ-hop.

---

## 5. ALB vs NLB for WebSocket / Streaming

### Chat completions: **SSE (not WebSocket to client)** — use **ALB**

| | ALB | NLB |
|---|-----|-----|
| SSE / chunked streaming | Native HTTP/1.1 | Pass-through, no path routing |
| WebSocket (MCP client ↔ gateway) | Supported with upgrade | Supported, lower L4 latency |
| TLS termination | Built-in | Need NLB + TLS on gateway or separate |
| Path routing (`/v1/chat/completions`, `/v1/mcp/*`) | Yes | No — need separate NLB per port or target groups |
| Idle timeout | Configurable up to **4000 s** | TCP idle |

**Recommendation:**

- **ALB** as front door for all HTTP/SSE/MCP WebSocket traffic.
- Set ALB idle timeout ≥ longest expected stream (default 60 s **will kill long SSE** — raise to 3600 s).
- Enable **HTTP/2** on ALB for client connections (multiplexing helps bursty clients).
- Target group: stickiness **off** for stateless chat (unless debugging); gateway is stateless per request except long-lived stream tied to one backend connection (ALB maintains TCP to one target for stream duration naturally).

**NLB** only if you need absolute minimum L4 overhead for a dedicated WebSocket-only MCP ingress — not worth the ops cost for a mixed SSE + REST API surface.

---

## 6. Exact Gateway Replica Count Math (10k Concurrent Connections)

### Definitions

- **C** = 10,000 concurrent connections (long-lived SSE + idle keep-alive)
- **c** = safe concurrent connections per gateway instance
- **R** = replicas = ⌈C / c⌉ × HA factor

### Per-instance capacity (single worker, async)

Conservative (production headroom):

```
c = 2,000 concurrent connections per c6i.xlarge
    (4 vCPU, 8 GiB — async I/O bound, mostly idle during stream)
```

Aggressive (with tuning: ulimit 65535, memory limits, Tier-2 mostly async):

```
c = 3,500 per c6i.2xlarge
```

### Calculation (conservative)

```
R_base = ⌈10,000 / 2,000⌉ = 5
R_HA   = R_base × 1.25  (one AZ loss + rolling deploy) ≈ 6–7
R_ops  = round up to 8   (20% burst + Tier-2 thread saturation headroom)
```

### Tier-2 thread saturation check

Each instance: **max 4 concurrent** Bedrock scans (`GATEWAY_SCAN_THREAD_POOL_SIZE=4`).

If 10% of requests hit Tier-2 sync at 200 RPS aggregate:

```
Tier-2 demand = 20 concurrent scans (avg 300 ms hold time)
Per instance at 8 replicas = 20/8 ≈ 2.5 in flight → OK
```

If Tier-2 hit rate rises or `sync_pre_llm` on streaming preflight:

```
At 500 RPS × 10% × 0.3s = 15 in-flight globally → 8 replicas ≈ 2/instance → OK
At 500 RPS × 50% × 0.5s = 125 in-flight → NEED 125/4 ≈ 32 replicas OR async_post_llm
```

**Final recommendation: 6–8 × `c6i.xlarge` (or 4–6 × `c6i.2xlarge`)** with HPA on:

- `gateway_active_connections` (already tracked in `METRICS`)
- ALB `ActiveConnectionCount` / `TargetResponseTime`

---

## 7. Connection Limits per Instance Type

| Instance | vCPU | RAM | Safe concurrent SSE | Redis pool budget | Notes |
|----------|------|-----|---------------------|-------------------|-------|
| c6i.large | 2 | 4 GiB | 1,000–1,500 | 300 auth + 100 RL | Minimum prod size |
| **c6i.xlarge** | 4 | 8 GiB | **2,000–2,500** | Same | **Sweet spot** |
| c6i.2xlarge | 8 | 16 GiB | 3,000–4,000 | Same | Diminishing returns |
| c6i.4xlarge+ | 16+ | 32+ GiB | Not linear | — | **Avoid for async I/O** |

**Other limits to configure:**

- **ALB:** Pre-warm for 10k connections (open AWS support ticket for large deployments).
- **ECS/EKS task:** `ulimit -n` ≥ 65535.
- **Security groups:** Ephemeral port range not a bottleneck at 10k.
- **Bedrock TPS/TPM quotas:** Tier-2 scans consume inference quota — size replicas for **Bedrock quota**, not CPU.

---

## 8. Challenge to PERFORMANCE-MAX: Why Bigger Instances Don’t Help

**Claim:** “Use c6i.8xlarge or more workers for 10k users.”  
**Rebuttal:** This workload is **async I/O + bounded sync offload**, not CPU-saturated compute.

| Bottleneck | Bigger instance helps? | Why |
|------------|------------------------|-----|
| Redis RTT | No | Network-bound; same AZ latency regardless of vCPU |
| Tier-2 Bedrock | No | External API 150–800 ms; boto3 sync in 4-thread pool caps parallelism **per process** |
| SSE fan-out | Partially | One event loop scales to 10k idle connections on modest CPU; memory matters more than vCPU |
| Tier-1 regex | Slightly | `to_thread` + 4 workers — more CPU helps only until thread pool saturates |
| Policy engine | No | Sub-ms in-memory; `lru_cache` on regex compile |

**What actually improves p99:**

1. **`tier2_execution_mode=async_post_llm`** for streaming (default is `sync_pre_llm`).
2. **Tier-1 short-circuit** before Bedrock (already implemented — policy block skips Tier-2).
3. **More replicas on c6i.xlarge**, not bigger boxes.
4. **Raise `GATEWAY_SCAN_THREAD_POOL_SIZE` only with Bedrock quota headroom** — 4 is intentional backpressure.
5. **Same-region Bedrock** + optional PrivateLink.
6. **Do not