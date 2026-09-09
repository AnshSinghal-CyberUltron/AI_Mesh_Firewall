# Full-project monthly cost — verified

> **Verified 2026-09-02** against the evidence corpus (`wf_1ccbbae1-69a` B4/ASSEMBLY, `wf_f840f647-3ee` W1) and [`2026-08-27-FINAL-evidence-based-hot-path-plan.md`](./2026-08-27-FINAL-evidence-based-hot-path-plan.md) §12.
> **Operating point:** Posture A — 4 × `g2-standard-24`, scan every request, **1,064 RPS**, asia-south1, 3-year CUD.
> **Volume:** 1,064 × 86,400 × 30 = **2,757,888,000** requests/month.
> **Not in the bill:** customer BYOK LLM tokens.
>
> **⚠ Correction to the previous revision.** That revision reconciled egress *down* to the plan's **$460.46** and called the earlier **$976** wrong. **It is the other way round.** $460.46 ÷ 2.758 B requests = **1.52 KiB/request**, which is below B4's response-only figure at coalesce ×64 (1,770 B) and carries **no prompt leg at all** — it silently assumes the tenant's provider is in-region. That is one of the six levers ASSEMBLY applied (worth **1.90×**), and it is **not enforceable**: the tenant chooses the provider, and OpenAI/Anthropic have no asia-south1 endpoint. **$976 was the first honest correction** (do not call it wrong). Banded first-principles on the prompt-over-internet byte model is **$1,090** (§4.1). This file is the source of truth; do not reconcile other documents down to §12.

---

## 1 · Total (full project: frontend + backend + data + GPU)

| | $/month |
|---|---:|
| **All-in, provider over the internet** *(plan on this)* | **$4,561.44** |
| Budget lock | $5,000 |
| **Headroom** | **$438.56** |
| Unit | **$1.65 / million requests** |
| Upside: tenant provider in-region / PSC | ~$3,970 |
| Optional: control plane on its own nodes (§4.2) | +$270 → **$4,831.44** |
| Cloud Logging @ 1 KiB/req instead of GCS | **$5,798.44 — OVER by $798** |
| G2 on-demand (no 3-yr CUD) | **$7,906 — OVER** |
| Quota to 14 L4 (posture A′, 1,862 RPS) | **$7,228.70 — far OVER** ⚠ |

Frontend, control/backend, workers, MCP, nginx, PgBouncer, Mongo, Chroma are **in this total** at **$0 extra compute** — 96 vCPU can do ~26,592 RPS of CPU work and the GPU only admits 1,064 RPS. Their disks are line 4.

> **Note on A′:** the plan's §12 put posture A′ at $4,979.76 using the $460 egress and a 4-node shape. Rebuilt honestly — 7 × `g2-standard-24` for 14 L4, plus egress scaling with RPS — it is **$7,228.70, far over the cap.** Raising the L4 quota is **not** the cheap next step it appeared to be; it needs the in-region provider lever or a larger budget.

---

## 2 · Cost matrix — every service

| # | Service | Spec | $/mo | Why (short) |
|---:|---|---|---:|---|
| **GATEWAY — the <12 ms path** |
| 1 | **GCE G2 × 4** | 96 vCPU + 384 GB + **8 × L4** | **2,736.76** | GPU+CPU in one VM. The only way to hit <12 ms — any off-box GPU starts at ≥16.1 ms RTT. `[V]` |
| 2 | NVIDIA L4 × 8 | bundled in the G2 VMs | **0 extra** | Semantic injection scan. CPU-only is 159.2 ms — 32× too slow. `[M]` |
| 3 | **Persistent disks** | 4 × 100 GiB pd-ssd + 250 GiB data | **68.00** | G2 boots + Mongo 200 GiB + Chroma 50 GiB. `[D from V]` |
| 4 | Regional ALB | Standard tier | **18.25** | TLS + SSE, ≥350 s. Global ALB forces Premium egress. `[V]` |
| 5 | **Egress + LB bytes** | 16,037 GiB banded + LB @ $0.010/GiB | **1,090.32** | Client SSE + **prompt leg over the internet**. Biggest non-compute line. §4.1 |
| 6 | External IPs | LB + node addresses | **15.00** | Public entry; no Cloud NAT. |
| **BACKEND / CONTROL** |
| 7 | **Cloud SQL Postgres HA** *(RDS-class)* | 4 vCPU / 16 GiB / 100 GiB | **273.91** | Orgs, keys, policies. Chat opens **0** connections. `[V]` |
| 8 | SQL backups | automated PITR | **15.00** | Restore the control DB. |
| 9 | **Memorystore Valkey HA** | 2 × 13 GB `highmem-medium` | **175.20** | Kill-switch + one `EVALSHA`/request. HA or every failover is a 503. `[V]` |
| 10 | Control + Celery + MCP + PgBouncer | on spare vCPU | **0.00** | `/api`, policy compile, rollups, tool path. CPU is ~25× idle vs the GPU. |
| 11 | MongoDB | on spare vCPU | **0.00** | Live telemetry sink. Disk in line 3. |
| 12 | ChromaDB | on spare vCPU | **0.00** | Tenant RAG only — `/v1` touches **zero** vector stores. |
| **FRONTEND** |
| 13 | SPA + nginx | on spare vCPU | **0.00** | Operator console. Never on the `/v1` edge. |
| **PLATFORM / OPS** |
| 14 | GKE regional | cluster fee | **73.00** | Multi-zone + GPU pools. Nodes are line 1. `[V]` |
| 15 | Artifact Registry | ~20 GB images | **10.00** | Gateway + TensorRT images. |
| 16 | **Audit → GCS** | Log Router sink, $0.02/GiB | **53.00** | Per-request verdict record. Cloud Logging @ 1 KiB = **$1,290**. |
| 17 | Cloud Monitoring | metrics + alerts only | **15.00** | Health/paging. **Risk line — §4.3.** |
| 18 | Disk snapshots | weekly | **18.00** | VM restore. |
| **NOT BOUGHT** |
| 19 | Cloud NAT | — | **0** | $0.045/GiB **both ways** would bill the free ~87 KiB provider body (~$10k+). `[V]` |
| 20 | Cloud Armor | — | **0** | **$15,180/mo** — 3× budget. `[V]` |
| 21 | Kafka / ClickHouse / BigQuery / Vertex | — | **0** | Not needed at this volume. |
| | **TOTAL** | | **$4,561.44** | Full product. **$438.56 left.** |

---

## 3 · Does this hit <12 ms?

| Band | p50 tax | <12 ms? | Tag |
|---|---:|---|---|
| ≤512 tok, in-process L4 | **≈4.1 ms** | **yes** | `[D]` — 74% not measured on our GPU |
| ≤1,024 tok, 2 windows | **≈5.9 ms** | **yes** | `[D]` |
| 10,000-char prose (4 windows) | ≈10.8 ms | **yes** | `[D]` |
| 10,000-char markdown/code (7 windows) | ≈17.2 ms | **no** | `[D]` |
| CPU + PG2-22M today | **159.2 ms** | **no** | `[M]` — 32× |
| CPU T1 rewrite, same verdicts | **63.4 ms** | **no** | `[M]` |
| CPU T1 with a redesigned detector | 0.34–0.54 ms | yes, but **a different product** | `[M]` — Gate 0 first |

**Rule:** <12 ms with a real semantic classifier is **only** the G2 in-process L4 design. Off-box GPU starts at ≥16.1 ms RTT. CPU-only semantic cannot.

**p99 at 1,064 RPS is 18.3 ms** `[D, queue-sim]` — "<12 ms" is a **p50** claim at ≤1,024 tokens, not a p99 claim and not at the deployed 10,000-char cap.

---

## 4 · Verification notes

### 4.1 Egress — plan on the prompt leg, not $460

§12's **$460.46 / 4,197 GiB** is **response-only**. ASSEMBLY `asm_bom.py` `eb=1634`:

`0.7 × 1,770 + 0.3 × 1,316 = 1,633.8 B/request`

That is B4's coalesce-×64 + gzip **response** blend. **The 3,294 B prompt leg is not in the 4,197 GiB.** 4,197 GiB ÷ 2.758 B requests = **1.52 KB/request** — below B4's response-only ×64 floor (1,770 B).

B4's measured byte model, blended 70% stream / 30% non-stream:

| Architecture | response bytes | + prompt leg | prompt-leg share |
|---|---:|---:|---:|
| today (per-token + trace frame) | 120,782 | 3,294 | 2.7% |
| **coalesce ×16 + gzip, no trace** | **2,950** | **3,294** | **52.8%** |
| coalesce ×64 + gzip, no trace | 1,770 | 3,294 | 65.1% |

6,244 B/request × 2.758 B requests = **16,037 GiB/month**. Through the real Standard-Tier bands (200 GiB free · 824 @ $0.085 · 9,216 @ $0.065 · 5,797 @ $0.045) = **$929.95**, plus LB outbound 16,037 × $0.010 = **$160.37** ⇒ **$1,090.32**. (An earlier draft estimated $976; the banded computation is $1,090.) B4's own words: *"the prompt leg is 2.7% of egress today but **65.1%** after the response levers"* — the in-region-provider lever is worth **1.90×**, and it is exactly the lever a BYOK gateway cannot pull. Quote ~$3,970 as the upside if a tenant's provider is in-region; **budget $4,561**.

### 4.2 Control plane on the GPU nodes — $0, but consider paying $270

Co-locating is technically sound (CPU is ~25× idle). It is architecturally weaker: every GPU drain, driver upgrade or node replacement takes the dashboard and Celery with it. **$270/mo (2–4 vCPU n2/e2 + disks) buys an independent blast radius.** Recommended if ops maturity matters more than the last $270; the $4,561 total assumes co-location.

### 4.3 Cloud Monitoring at $15 is a discipline, not a default

$15 ⇒ ≈208 MiB/month chargeable (first 150 MiB free, then **$0.2580/MiB** `[V]`). Reachable with health/paging metrics only. **Not** reachable with per-request or per-tenant label cardinality. Use Google Managed Service for Prometheus (per-sample pricing), cap cardinality, and alarm on the Monitoring bill itself. Treat anything above $50 as a cardinality bug.

### 4.4 Where the $460-era plan under-counted

SQL backups, external IPs, disk snapshots, and a realistic Artifact Registry figure were absent. All added above (+$48).

---

## 5 · Max RPS per vCPU — theoretical vs practical

Bottom-up CPU per request, rewritten hot path, 4,096-char prompt / 400-token answer:

| ms | item | tag |
|---:|---|---|
| 0.150 | framework + HTTP + orjson parse | `[I]` |
| 0.020 | one pure-ASGI middleware | `[D from M]` |
| 0.160 | non-classifier stages (auth, KS, rate limit, policy, routing) | `[M]` |
| 0.280 | Tier-1 input — native + dictionary gate | `[D from M]` |
| **0.864** | **tokenize 4,096 chars** | **`[M]`** |
| 0.300 | Tier-1 output scan, 8 coalesced chunks | `[D]` |
| 0.155 | gzip-6 `Z_SYNC_FLUSH` per answer | `[M]` |
| 0.240 | SSE frame build, orjson | `[M]` |
| **2.169** | **TOTAL CPU per request** | `[D]` |

### 5.1 The ladder

| Basis | RPS / vCPU | Meaning |
|---|---:|---|
| **Theoretical ceiling** | **461** | 2.169 ms of CPU at 100% util, perfect scaling, no queueing. Physics bound — not achievable. |
| **Practical planning figure** | **277** | 60% utilisation cap for tail control. **Size on this.** |
| **Conservative floor** | **10.25** | If the Tier-1 rewrite underdelivers and `redact_all` stays on the hot path. CPU then binds at ~590 RPS — the one case where the GPU-bound answer is wrong. |
| **Today, measured** | **0.17 – 5.4** | 50–1,600× worse. The whole gap is two items: Tier-1 63.40 → 0.28 ms, `redact_all` 32.76 → 0 ms. |
| **Actually used in posture A** | **≈11.1** | 1,064 ÷ 96 vCPU. **The CPU sits ~96% idle.** |
| Per **L4 GPU** (not a vCPU) | **133** | 1,064 ÷ 8. ≈490 windows/s ÷ 2 windows/request, 60% cap. |

**Struck:** MASTER's **520 RPS/vCPU** — no support anywhere in the corpus.

### 5.2 The finding

**RPS-per-vCPU is not the binding constraint anywhere in this design.** At the recommended point you consume ~11 of an available 277 — the GPU quota binds and the CPU idles. It only becomes the wall in **posture C** (deterministic only), where 7,200–9,400 RPS is egress-bound before it is CPU-bound.

The exception is the conservative floor: **without the Tier-1 rewrite, CPU binds at ~590 RPS and the GPUs idle instead.** That is what the rewrite is for — not latency, but stopping CPU from becoming the ceiling.

### 5.3 Against the industry

| Gateway | RPS / vCPU | What it was actually doing |
|---|---:|---|
| Kong (Lua/nginx) | ~6,000 | auth + rate-limit, **no policies configured** |
| LiteLLM Rust | ~1,695 | **no logging, spend tracking, or persistence** |
| Bifrost (Go) | ~1,250 | mock upstream, excludes upstream time |
| **TrueFoundry (Node/Hono)** | **~350** | **auth + routing only, zero security scanning**, mock upstream |
| **AI Mesh (target)** | **~277** | **full 9-stage pipeline + GPU semantic classifier, streaming on** |
| LiteLLM Python | ~113 | fake endpoint |

**The defensible claim: within ~0.8× of TrueFoundry's throughput per vCPU while running a security pipeline TrueFoundry does not run — and measured with streaming on, which none of the published numbers are.**

Never quote Kong as a peer: 6,000 RPS/core is Lua doing auth and rate-limiting with no policies loaded.

### 5.4 What $4,561 buys

| Posture | Fleet RPS | Binds on | <12 ms semantic? |
|---|---:|---|---|
| **A — scan every request (buy this)** | **1,064** | 8 × L4 quota | **yes**, p50 ≤1,024 tok |
| A′ — 14 L4 | 1,862 | **budget — $7,229, far over** | yes |
| B — risk-gated ≥26% | 4,151 | egress | mixed |
| C — deterministic only | 7,200–9,400 | egress | only if T1 rewritten **and** Gate 0 passes |
| CPU-only, 100k coalesced | — | **$309,620/mo** | no |

---

## 6 · Not included

- Customer BYOK model spend
- `$7` one-time `trtexec` bake-off — **gates every GPU millisecond above**
- Cloud SQL backup beyond the included allocation `[NF]`
- Support plan
- AWS overlap while production is still on ap-south-1

---

## 7 · The two caveats that outrank the cost

1. **74% of the latency number is derived, not measured.** No GPU in this corpus was ours. The bake-off is $7 and 30 minutes.
2. **The deterministic tier does not detect what it claims** — 0/10 paraphrased injections, 5/5 benign inline-code prompts hard-blocked. And the per-request false-positive rate at 2 windows is **1.99%**, never previously stated. Cost is the easy part of this plan.

---

## 8 · What if the L4 quota is raised?

**Answer: within $5,000, almost nothing changes.** The 8-L4 quota and the budget ceiling land on the *same* number.

Cost model, verified against the line items above:

```
Cost(RPS) = $613.36 fixed
          + ceil(RPS / 266) × $701.19        GPU node + its disk
          + banded egress on RPS × 15.07 GiB/RPS-month
          + LB outbound @ $0.010/GiB
          + GCS audit @ $0.02/GiB
Marginal ≈ $3.50 per RPS-month.  One more node = +266 RPS for ~$948.
```

| RPS | nodes | L4 | vCPU | $/mo | What binds |
|---:|---:|---:|---:|---:|---|
| **1,064** | 4 | 8 | 96 | **$4,561** | — *(quota **and** budget, simultaneously)* |
| 1,330 | 5 | 10 | 120 | $5,401 | **budget** |
| 2,000 | 8 | 16 | 192 | $8,188 | budget |
| 5,320 | 20 | 40 | 480 | $19,519 | budget + **regional `CPUS` quota = 500** |
| 10,000 | 38 | 76 | 912 | $36,251 | budget + CPUS quota |

**The quota was never really the constraint.** Node 5 costs $701 + ~$247 of egress = ~$948, and there is only **$438.56** of headroom. Raising `NVIDIA_L4_GPUS` from 8 to anything larger buys **0 additional RPS** at $5,000. The earlier framing — *"$1,262 unspent, nothing left to buy"* — was an artifact of the $460 egress figure; with egress priced correctly there is no meaningful headroom to spend.

### 8.1 What each budget actually buys (unlimited quota, 100% scanned)

| Budget | Max RPS | Fleet |
|---:|---:|---|
| $5,000 | **1,064** | 4 nodes · 8 L4 |
| $7,500 | 1,862 | 7 nodes · 14 L4 |
| $10,000 | 2,466 | 10 nodes · 20 L4 |
| $25,000 | 6,770 | 26 nodes · 52 L4 |
| $50,000 | 13,832 | 52 nodes · 104 L4 |
| $100,000 | 28,196 | 106 nodes · 212 L4 |

Roughly **linear at $3.50/RPS-month**. There is no economy of scale worth waiting for — the egress band only improves from $0.065 to $0.045/GiB, and compute is strictly linear.

### 8.2 The lever that is actually cheap — and it needs no quota request

Risk-gate the semantic scan instead of scanning every request:

| Semantic coverage | Max RPS on $5,000 | vs 100% |
|---|---:|---:|
| 100% (posture A) | 1,064 | — |
| 50% | 1,596 | **+50%** |
| 26% (posture B) | 2,361 | **+122%** |
| 10% | 3,160 | **+197%** |

**Risk-gating triples throughput on the same money, today, with no quota ticket.** But it is a *security* decision, not a cost one: at 26% coverage, 74% of requests get the deterministic tier only — and that tier is measured at **0/10 on paraphrased injections**. Do not take this lever before Gate 0 establishes what the deterministic tier actually detects.

### 8.3 Other quotas that bind before the GPU one matters

| Quota | Live value | Binds at |
|---|---:|---|
| `NVIDIA_L4_GPUS` | 8 `[M]` | 1,064 RPS — *coincident with the budget* |
| **`CPUS` (regional)** | **500** `[M]` | **20 nodes ≈ 5,320 RPS** |
| `IN_USE_ADDRESSES` | 69 `[M]` | well beyond the above |

Raising `CPUS` matters *more* than raising `NVIDIA_L4_GPUS`, because at 24 vCPU per node the CPU quota caps the fleet at 20 nodes regardless of how many GPUs are approved. **File both, but file `CPUS` first** — and note that neither unlocks anything until the budget moves.
