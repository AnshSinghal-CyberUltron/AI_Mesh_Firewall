# Proposed target instance architecture — AI Mesh Firewall

> **Status:** PROPOSAL only. Derived from [`2026-08-22-MASTER-hot-path-capacity-architecture.md`](./2026-08-22-MASTER-hot-path-capacity-architecture.md) D1–D13, D8, D11.
> **Region:** `ap-south-1` (Mumbai). Three AZs.
> **Law:** split by **hardware class and failure domain**, not by business capability. Auth / policy / T1 stay **in-process** on the gateway (D1). Do not put CloudFront, ALB, API Gateway, or Kafka on `/v1/chat/*` (D11).

This document is the instance-level HLD: **what boxes exist, what they talk to, what is forbidden on the chat path.**

Diagrams below are **standard mermaid** (`graph` / `sequenceDiagram` / `classDiagram`) so they render in Cursor and GitHub preview. C4 macros, PlantUML, HTML `<br/>`, and `{…}` / `[…]` inside sequence messages do not.

The mermaid in this file is the in-repo source of truth. Cursor’s preview renders `graph` / `sequenceDiagram` / `classDiagram`; it does not render C4 or PlantUML.

---

## 1. What we are leaving

Today production is **one** `c8g.2xlarge` running **15 containers** (gateway, control, frontend, postgres, redis, rabbitmq, nginx, workers, MCP, stubs). Terraform in `infra/terraform/envs/prod` has **never been applied**. The target is the opposite: **named fleets + managed data stores**, still a **modular monolith** for chat enforcement.

---

## 2. Instance and managed-service inventory

Sizing below is the **Phase 4 / 20 k admitted RPS** layout. Stage-1 (today’s ~30–85 RPS) can start at the “lean” column and grow without changing topology.

| # | Name | Role | SKU / service | Count (lean → 20k RPS) | AZ | On `/v1` chat path? |
|---|---|---|---|---|---|---|
| 1 | **NLB gateway** | TLS:443 SDK / API. `preserve_client_ip=true`, idle 350 s | NLB, TCP/TLS | 1 (3 AZ) | all | **Yes** (edge) |
| 2 | **ALB console** | UI + control `/api`. Host rules. HTTP→HTTPS | ALB | 1 (3 AZ) | all | **No** |
| 3 | **CloudFront** | SPA static + optional `/api` cache-bust. **Never `/v1/*`** | CloudFront | 1 | global | **No** |
| 4 | **Gateway CPU fleet** | All 9 stages, nginx sidecar, gunicorn | ECS on EC2 **c8g.4xlarge** | 1 → **3** (1/AZ) | 1a/1b/1c | **Yes** |
| 5 | **Guard GPU fleet** | PG2-86M Triton. **Stage 2 only** (~>150 classify/s/host) | ECS on EC2 **g6e.xlarge** (x86) | 0 → 9 at 20 k | 1a/1b/1c | Optional hop |
| 6 | **Frontend fleet** | SPA origin (nginx or static host) | ECS **c8g.large** | 2 (2 AZ) | 1a/1b | **No** |
| 7 | **Control plane fleet** | Django/gunicorn `/api`, admin, policy compile | ECS **c8g.xlarge** | 2 → 3 | 1a/1b/1c | **No** |
| 8 | **Control workers** | Celery worker + beat (compile, drain consumers) | ECS **c8g.xlarge** | 2 + 1 beat | 1a/1b | **No** |
| 9 | **MCP broker + sandboxes** | Per-org MCP, not chat admit | ECS + gVisor sandboxes | 1 broker + N sandboxes | 1a/1b | **No** |
| 10 | **Valkey (ElastiCache)** | Auth miss, KS truth, quota `EVALSHA`. **Not telemetry** | Valkey 8.1 CME **r7g.xlarge** | 2×(1+1) → **3×(1+1)** | 3 AZ | **Yes** (1 RTT) |
| 11 | **Aurora / RDS PostgreSQL** | Orgs, keys, policies, users. **Gateway opens 0 connections** | Aurora Serverless v2 (2×0.5 ACU) or RDS Multi-AZ `db.r7g.large` | 1 cluster | 2 AZ | **No** |
| 12 | **PgBouncer / RDS Proxy** | Control→Postgres pooling only | RDS Proxy or ECS sidecar | 1 | with control | **No** |
| 13 | **Amazon MQ (RabbitMQ)** | Control celery broker (keep; not chat) | mq.m5.large or 3.13 | 1 Multi-AZ | 2 AZ | **No** |
| 14 | **MSK (Kafka)** | `aim.audit` / `aim.traces` from gateway ring | kafka.**m7g.large** | **3 brokers** | 3 AZ | Produce only (async) |
| 15 | **ClickHouse** | Analytics / Scan Detail rehydrate | EC2 **r7g.2xlarge** + 2 TB gp3 | 1 → 3 | 1a | **No** |
| 16 | **S3** | Cold traces, CH backups, SPA assets | Standard | 2 buckets | region | **No** |
| 17 | **ECR** | Gateway arm64 + GPU x86 images | Private | 1 registry, 2 tags | region | pull only |

**Retired:** in-box Redis 768 MB, in-box Postgres, Mongo telemetry pilot, `guardrails` / `vector-retrieval` / `telemetry-ingest` stubs as production processes, single-node Memorystore 1 GiB.

---

## 3. DNS and edge (must stay split)

| Hostname | Target | Why |
|---|---|---|
| `aimeshfirewall.zeroshield.ai` | CloudFront → ALB → **frontend** | Console SPA |
| `aimeshbackend.zeroshield.ai` | ALB → **control** | `/api`, admin, policy compile, WS |
| `aimeshgateway.zeroshield.ai` | **NLB** → nginx → **gateway :8300** | `/v1/chat/completions`, `/health` |

Do **not** add an ALB host rule for the gateway name. Do **not** send 9-stage traffic through CloudFront.

---

## 4. System context — who talks to what

```mermaid
graph LR
  SDK["SDK / API client"] --> NLB[NLB TLS 443]
  NLB --> MESH[AI Mesh Firewall]
  OP[Operator browser] --> CF[CloudFront SPA only]
  CF --> ALB[ALB HTTPS]
  ALB --> MESH
  MESH --> BYOK[Tenant / platform LLM]
```

---

## 5. Deployment — instance structure

Split into three graphs so the preview layout stays readable. Chat and console **do not share an edge**.

### 5.1 Chat hot path (SDK only)

```mermaid
graph TB
  subgraph pub [Public edge - chat]
    SDK[SDK clients]
    NLB[NLB TLS 443 preserve_client_ip]
  end
  subgraph gw [Gateway CPU fleet - c8g.4xlarge x3 AZ]
    NGX["nginx :443 http2 keepalive"]
    GUN["gunicorn 127.0.0.1:8300 - 9 stages"]
  end
  subgraph gpu [Guard GPU - Stage 2 only]
    TR[Triton PG2-86M on g6e.xlarge]
  end
  subgraph datahot [On the admit path]
    VAL[Valkey CME r7g.xlarge]
  end
  subgraph dataasync [Async only]
    MSK[MSK m7g.large x3]
  end
  BYOK["Upstream BYOK / LiteLLM / vLLM"]
  SDK --> NLB --> NGX --> GUN
  GUN -->|1 EVALSHA| VAL
  GUN -->|async produce| MSK
  GUN -.->|Stage 2 only| TR
  GUN --> BYOK
```

### 5.2 Console path (UI + control — never /v1)

```mermaid
graph TB
  subgraph pub2 [Public edge - console]
    BR[Browser]
    CF["CloudFront SPA and /api only"]
    ALB[ALB HTTPS host rules]
  end
  subgraph compute [Control and UI fleets]
    FE[Frontend ECS c8g.large]
    CTL[Control ECS c8g.xlarge]
    WRK[Workers and beat]
    MCP[MCP broker and sandboxes]
  end
  subgraph datacold [Not on chat path]
    RDS[Aurora PostgreSQL]
    MQ[Amazon MQ RabbitMQ]
    CH[ClickHouse r7g.2xlarge]
    S3[S3 cold and SPA]
  end
  BR --> CF --> ALB
  ALB --> FE
  ALB --> CTL
  CTL --> RDS
  CTL --> MQ
  CTL --> CH
  WRK --> RDS
  WRK --> MQ
  WRK --> CH
  MCP -.->|not on NLB v1| CTL
  FE --> S3
```

### 5.3 Trace / audit pipe (after the response)

```mermaid
graph LR
  GUN[Gateway ring] -->|async| MSK["MSK aim.audit aim.traces"]
  MSK --> CH[ClickHouse]
  CH --> CTL["Control APIs / Scan Detail"]
```

---

## 6. Component — gateway process (still one process)

```mermaid
graph LR
  subgraph proc [Single gateway process]
    AUTH[Auth cache RAM]
    ADMIT[Admit snapshot]
    POL[Policy bundle RAM]
    T1[T1 Aho-Corasick]
    PG2[PG2-22M ONNX in-process]
    GCRA[Local GCRA plus 1 EVALSHA]
    RING[Bounded ring to Kafka]
    OG[Output T1 then classifier at DONE]
  end
  VAL[Valkey]
  MSK[MSK]
  GPU[GPU Triton Stage 2]
  LLM[Upstream LLM]
  AUTH --> ADMIT --> POL --> T1 --> PG2 --> GCRA
  GCRA --> LLM --> OG
  GCRA -.-> VAL
  RING -.-> MSK
  PG2 -.->|Stage 2| GPU
```

No `auth-svc` / `policy-svc` / `scan-svc`. The only permitted extra hop is **Stage 2 GPU**.

---

## 7. Sequence — one allow-path chat (hot path)

Curly braces and square brackets in messages are mermaid syntax (loops / notes). They are written in plain words here so the diagram parses.

```mermaid
sequenceDiagram
  autonumber
  participant C as SDK
  participant N as NLB
  participant X as nginx
  participant G as Gateway process
  participant V as Valkey
  participant L as Upstream LLM
  participant K as MSK async
  C->>N: POST v1 chat completions HTTP2
  N->>X: preserve_client_ip
  X->>G: keepalive upstream
  G->>G: auth cache hit, zero Redis
  G->>G: admit snapshot KS and breaker
  G->>V: one EVALSHA TPM lease
  V-->>G: lease
  G->>G: T1 plus PG2-22M in-process
  G->>L: redacted prompt
  L-->>G: tokens stream
  G->>G: T1 per chunk, classifier at DONE
  G-->>C: SSE or JSON
  G->>K: put_nowait audit never awaited
```

Postgres, ClickHouse, control HTTP, CloudFront, and ALB **do not appear** on this sequence.

---

## 8. User flows — two products, two edges

### 8.1 Operator console (not the 12 ms SLO)

```mermaid
graph TD
  A["Operator opens aimeshfirewall.zeroshield.ai"] --> B[CloudFront SPA]
  B --> C["Login POST aimeshbackend /api/auth/token"]
  C --> D[ALB to control]
  D --> E[Aurora session org RBAC]
  E --> F[Dashboard SOC policies]
  F --> G[Control reads ClickHouse and Aurora]
  F --> H[Policy compile push bundle to Valkey]
  H --> I[Gateway reloads RAM bundle]
```

### 8.2 Tenant SDK chat (SLO F/G)

```mermaid
graph TD
  A["App POST aimeshgateway /v1/chat/completions"] --> B[NLB]
  B --> C[nginx on gateway host]
  C --> D[9 stages in one process]
  D --> E{Block?}
  E -->|yes| F[403 plus audit ring]
  E -->|no| G[BYOK generate]
  G --> H[Output T1 plus DONE classifier]
  H --> I[Client completion]
  D --> J[async MSK to ClickHouse]
  J -.-> K[Later operator Scan Detail]
```

---

## 9. Data-store ownership (who may touch what)

| Store | Writer | Reader on chat path | Forbidden |
|---|---|---|---|
| Valkey | Gateway (quota, KS), control (bundle publish) | Gateway: miss + 1 EVALSHA | Telemetry LPUSH, 17× PUBLISH per log |
| Aurora | Control + workers only | **None** | Gateway ORM |
| MSK | Gateway ring, MCP audit | Workers / CH ingest | Awaited produce on request |
| ClickHouse | MSK consumers | Control APIs / UI | Gateway SELECT |
| RabbitMQ | Control / workers | Workers | Gateway chat |
| S3 | CH backups, SPA, spill files | Control, CloudFront | Request-path GET |

---

## 10. VPC sketch (3 AZ)

```mermaid
graph TB
  subgraph vpc [vpc 10.20.0.0-16 ap-south-1]
    subgraph public [public slash24 x3]
      NLB[NLB]
      ALB[ALB]
      NAT[NAT]
    end
    subgraph gwpriv [gw-priv slash24 x3]
      GW[c8g.4xlarge gateway plus nginx]
    end
    subgraph ctlpriv [ctl-priv slash24 x3]
      FE[frontend]
      CTL[control]
      WRK[workers]
      MCP[MCP broker]
    end
    subgraph datapriv [data-priv slash24 x3]
      VAL[Valkey]
      RDS[Aurora]
      MSK[MSK]
      MQ[Amazon MQ]
      CH[ClickHouse]
    end
    subgraph gpupriv [gpu-priv slash24 x3 Stage 2]
      GPU[g6e.xlarge x86]
    end
  end
  NLB --> GW
  ALB --> FE
  ALB --> CTL
  GW --> VAL
  GW --> MSK
  GW -.-> GPU
  CTL --> RDS
  CTL --> CH
```

Security groups (intent):

- NLB SG → gateway nginx **443 only** (not world `:8300`).
- ALB SG → frontend 80 + control 8000.
- Gateway SG → Valkey 6379, MSK 9092, BYOK 443, (Stage 2) GPU 8001. **No Aurora.**
- Control SG → Aurora 5432, MQ, Valkey (publish), ClickHouse 8123.
- SSH/RDP: operator prefix lists only.

---

## 11. Stage-1 vs Stage-2 hardware (same topology)

| Phase | Gateway | GPU | Valkey | Why |
|---|---|---|---|---|
| **Stage 1** (now → ~500 RPS) | 1–3 × c8g.4xlarge, **PG2-22M in-process** | **0** | 2 shards × (1+1) r7g.large/xlarge | No arm64 GPU in Mumbai; volume does not pay for L40S |
| **Stage 2** (≥~500 RPS or p99 gone) | scale c8g.4xlarge | g6e.xlarge Triton PG2-86M | 3×(1+1) r7g.xlarge | Only way to keep 86M ≤8 ms |

---

## 12. Cost snapshot (MASTER, ap-south-1, not a quote)

At **today’s traffic**, incremental vs one-box compose ≈ **$1.4 k/month** (Valkey ~$1.07 k + MSK ~$0.32 k + ClickHouse ~$0.4 k, Mongo retired).

At **20 k admitted RPS**: 3 × c8g.4xlarge ≈ $1.30/h; GPU pool ~9 × g6e ≈ **$15 k/month** extra. SLO C (100 k **completed** chats) is a **GPU purchase**, not this diagram.

---

## 13. Non-goals (do not draw these boxes on `/v1`)

- Auth / policy / scan as separate ECS services
- API Gateway, ALB, or CloudFront in front of chat
- Bigger single Redis as a latency fix
- ClickHouse or Aurora on the admit path
- Memorystore 1 GiB BASIC as prod cache
- Llama Guard 3 1B on the synchronous admit path

---

## 14. Fleet UML (class view)

Same boxes as §5, as a class diagram. Cursor renders `classDiagram`; it does not render PlantUML.

```mermaid
classDiagram
  class NLB {
    TLS_443
    preserve_client_ip
  }
  class GatewayFleet {
    c8g_4xlarge_x3
    nginx_plus_gunicorn
    nine_stages_in_process
  }
  class GuardGPU {
    g6e_xlarge_stage2
    Triton_PG2_86M
  }
  class FrontendFleet {
    c8g_large
    SPA_origin
  }
  class ControlFleet {
    c8g_xlarge
    Django_gunicorn
  }
  class Valkey {
    CME_3x_primary_replica
    r7g_xlarge
  }
  class AuroraPG {
    control_plane_only
  }
  class MSK {
    m7g_large_x3
  }
  class ClickHouse {
    r7g_2xlarge
  }
  class CloudFront {
    SPA_only_never_v1
  }
  class ALB {
    UI_and_api
  }
  CloudFront --> ALB
  ALB --> FrontendFleet
  ALB --> ControlFleet
  NLB --> GatewayFleet
  GatewayFleet --> Valkey : EVALSHA
  GatewayFleet --> MSK : async
  GatewayFleet --> GuardGPU : Stage2
  ControlFleet --> AuroraPG
  MSK --> ClickHouse
  ControlFleet --> ClickHouse
```

---

## 15. Mapping from today’s compose

| Today (one VM) | Target |
|---|---|
| `gateway` + host `:8300` | Gateway fleet behind NLB; gunicorn loopback; nginx public |
| `nginx` UI/API vhosts | ALB + frontend + control; **also** nginx on `/v1` |
| `control` + `workers` + `beat` | Control ECS + worker ECS |
| `frontend` | Frontend ECS + CloudFront |
| `postgres` + `pgbouncer` | Aurora + RDS Proxy / pgbouncer |
| `redis` | ElastiCache Valkey CME |
| `rabbitmq` | Amazon MQ |
| telemetry via Redis LIST | MSK → ClickHouse |
| `mcp-broker` + sandboxes | MCP ECS pool (unchanged isolation story) |
| `guardrails` stub container | Deleted; PG2 in-process then GPU |
| `demo` | Optional, not prod |
| node-exporter / cadvisor | ECS + Container Insights / CH metrics |
