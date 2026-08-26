# FINAL — Verification of the MASTER plan + platform-agnostic scale plan (gateway, control plane, frontend)

> **Status:** PLAN ONLY. No code, no IaC, no infra mutation authorised by this document.
> **Date:** 2026-08-25 · **Verifies:** `docs/plans/2026-08-22-MASTER-hot-path-capacity-architecture.md` (adopted 2026-08-22)
> **Cost overlay (binding):** §13–§16 — **≤ $5 k/mo**, **CPU only (PG2-22M)**, gateway tax **p50 ≤ 12 ms**, GitHub phase issues. Where §13 conflicts with earlier AlloyDB / GPU / 100 k-provision text, **§13 wins**.
> **Relationship:** This document does **not** supersede MASTER. It (a) records the verification verdict, (b) closes three gaps MASTER does not cover, (c) re-expresses MASTER's cloud-specific decisions as platform-agnostic contracts, and (d) supplies the per-step test/verification matrix.
> **Scope constraint from product:** *Cloud migration is owned by DevOps and is explicitly out of scope.* This plan therefore selects **service classes**, not vendor SKUs, and supplies an AWS↔GCP equivalence table so the architecture is valid on either.

Provenance tags carried over from MASTER: **[M]** measured this session · **[R]** repo bench corpus · **[V]** vendor-published · **[I]** independent third party · **[D]** derived arithmetic · **[NF]** searched, not found. New tag: **[L]** *live-verified against running infrastructure during this verification, 2026-08-25*.

---

## 0. Verification verdict

**MASTER is sound and should proceed. Its diagnoses survived re-verification. It has three genuine gaps and four platform-coupling defects, none of which invalidate D1–D17.**

| | Finding | Class | Where |
|---|---|---|---|
| **V-1** | **The control plane and frontend are absent from the plan.** MASTER is a hot-path document end to end. The OOM you actually hit — gunicorn workers dying on SOC/vector analytics — has no root-cause entry, no budget line, and no capacity model anywhere in it. | **Gap — highest value to you** | §3 |
| **V-2** | **Security engines are never parallelised.** MASTER's §6.2 budget is a *serial sum* (~12 ms). The hot path in `main.py` is a strictly sequential chain of `await asyncio.to_thread(...)`; `asyncio.gather` appears **nowhere** in it [M]. MASTER's own Rule 5 permits "raced" judges but no decision implements it. | **Gap** | §4 |
| **V-3** | **No frontend capacity or payload contract.** The SPA is one box in the topology diagram. The endpoints that OOM'd are frontend-driven, unpaginated, full-window aggregations. | **Gap** | §3.4 |
| **V-4** | **D11 (LB) contradicts §10 (non-goals) once the platform changes.** §10 forbids "ALB on `/v1`"; the chosen GCP edge *is* an L7 Application LB. The prohibition was reasoned from AWS-specific properties and does **not** transfer unexamined. | **Coupling defect** | §5 |
| **V-5** | **D13 (ECS) inverts.** Its rationale is ENI trunking and "no control-plane fee" — both AWS-specific. Platform decision is GKE. | **Coupling defect** | §5.4 |
| **V-6** | **D5 (Valkey CME) assumed a 1 GiB Memorystore.** The live instance is **26 GiB but `BASIC` tier — no replica, AUTH disabled, transit encryption disabled, persistence disabled** [L]. MASTER's eviction argument weakens; its availability argument gets **much stronger**. | **Coupling defect + sequencing hazard** | §5.3 |
| **V-7** | **D2/D3's GPU tier is executable in-region — my first reading said otherwise and was wrong.** See the self-correction below. | **Corrected non-issue** | §6 |
| **V-8** | **"100 k" was selected as the target without disambiguating which of MASTER's three meanings.** MASTER is emphatic these differ by ~1000× in cost. | **Must be resolved before Phase 4** | §7 |

### 0.1 Self-correction, recorded in full

An earlier step of this verification asserted *"asia-south1 has no L4/A100/H100 quota."* **That was false.** It came from a region-quota dump piped through `head -25`, which truncated before the modern accelerator metrics. Authoritative re-query [L]:

| Accelerator | asia-south1 availability | On-demand quota |
|---|---|---|
| `nvidia-l4` | zones **a, b, c** | **8** (preemptible 8, committed 8) |
| `nvidia-h100-80gb`, `nvidia-h100-mega-80gb` | zone **c** | not in legacy region metrics — newer per-family quota, request required |
| `nvidia-h200-141gb`, `a3-ultragpu-8g` | zone **b** | as above |
| `nvidia-a100` | — | 1 on-demand / **16 preemptible** |
| `nvidia-tesla-t4` | a, b, c | 4 |
| `g2-standard-4 … 96` machine types | a, b, c | — |

**Consequence:** MASTER's Stage-2 GPU guard tier is regionally valid. The action is a **quota increase**, not a region move. Current quota of 8 L4 is enough for MASTER's Stage-2 entry point (~9 GPUs at 20 k RPS [D]) but **not** for 100 k RPS (§6.2). Also note region **`CPUS` quota = 500** and **`IN_USE_ADDRESSES` = 69** [L] — both bind the fleet before compute does (§7.3).

This correction is logged because MASTER's discipline is that evidence beats assertion, including my own.

### 0.2 What adversarial review changed (CLAUDE.md Phase 3)

Four independent reviewers were tasked to **prove this plan wrong** against the source. They succeeded on three counts. Every correction below is a claim I made that the code refutes.

| # | My original claim | Verdict | What the code actually says |
|---|---|---|---|
| **R-1** | "Embedding is already solved: `potion-base-8M` in-process; the store is Milvus; hot tier = in-process `hnswlib`." | **REFUTED — the most serious error in the first draft.** | `potion`, `model2vec`, `sentence-transformers`, `hnswlib`, `faiss`, `usearch` and **all local inference are absent from the repo** — zero occurrences, not in any manifest or lockfile. **Every embedding is a network call** (Bedrock Titan v2 256-d, Pinecone Inference, or a litellm provider). No Milvus server is deployed. MASTER's numbers were benchmarks of *proposed* libraries; I quoted them as shipped state. §9 is rewritten. |
| **R-2** | "Fan-out: policy ∥ T1 ∥ T2 ∥ threat ∥ routing, ≈13.6 ms → ≈8 ms." | **PARTIALLY REFUTED — the graph was wrong and the win is smaller and later.** | (a) **Policy MUTATES the prompt** (`effective_prompt`) that T1/T2 then scan (PIPELINE-0009) — policy ∥ T1 is *unsafe*, not safe. (b) **T2 already runs T1 internally** (`scanner.py:2027`) and merges, so they are not two independent tasks. (c) Today's T1 is **pure-Python regex** — Vectorscan/ONNX are *proposed*, so concurrent `to_thread` would be **GIL-bound, not parallel**. (d) The T1 pool is **4 threads** (`config.py:154`), not the 8 I assumed. §4 is rewritten with a smaller, real, immediately-available win. |
| **R-3** | "Haiku removal = two call sites." | **INCOMPLETE.** | The authoritative constant is `platform_models.py:11`, resolved at `:91`/`:99` for the T2 scanner **and adjudicator** — I named only its consumers. Offsetting good news: review proved removing Bedrock costs exactly **one** fail-closed control; every other consumer is already fail-open. §8 updated. |
| **R-4** | "The OOM is structural and will recur." | **CONFIRMED, and I understated it.** | **Ten** full-metadata hauls remain, **eight completely unbounded**; `.iterator()` provides **no** memory bound because server-side cursors are disabled; there is **no GIN index on any `metadata` column**; there is **no `statement_timeout` anywhere**. §3 is expanded, and two new defects surfaced that are not performance issues at all (§3.1a). |

Two findings arrived that were **not in the plan at all** and are now added: the **frontend is a demand-side amplifier** of the OOM (new §3.4), and a **cross-tenant scoping defect** plus a **silent metric regression** in the control plane (new §3.1a).

### 0.3 Second review round — including corrections to §0.2 itself

A second, deeper adversarial pass was run against the *revised* text. **It broke four more claims, two of which were fixes written in round one.** Recording this in full, because a plan that hides its own correction history is not verifiable.

| # | Claim (post-round-1) | Verdict | What the code says |
|---|---|---|---|
| **R-5** | "Fan-out is safe once policy is kept serial; Wave A can run the four independent lookups concurrently." | **STILL UNSAFE — round-one fix was insufficient.** | Every pre-scanner stage is a **terminal writer**: it emits telemetry, fires an audit record, increments `METRICS["blocked"]`, and `return`s. Today mutual exclusion is guaranteed *by the early `return`*. Concurrency removes it, so one request that trips keyword **and** policy **and** scanner emits **three** `input_blocked` telemetry events and three audit records — violating PIPELINE-0007's "ONE authoritative `final_action`" and pushing against the CHG-0094 audit-backpressure ceiling. Two further hazards: `_pii_detection_enabled` is set by testing whether **policy already wrote smart-mask markers into `scan_text`**, so fan-out flips it and re-opens PIPELINE-0012; and stages mutate `body["model"]` and `body["messages"]` **in place**. §4 is rewritten again — the safe transform is **prefetch, not reorder** (§4.2). |
| **R-6** | "Conditions 1–4 all hold; fixing any one alone leaves the failure reachable." | **REFUTED on three counts.** | (a) The control container has its **own cgroup** (`cpus: 2`, `memory: 2560M`), so `memory.max` fires regardless of host free RAM — **host co-tenancy and over-commit are not the mechanism** for a control-worker OOM, and the largest-RSS process is the **gateway** (12288M / 16 workers), not control. My own conditions 2 and 3 predicted different victims. (b) **"22.6 GiB" is a MiB→GiB conversion error** — the real sum is 22,656 MiB = **22.125 GiB**. (c) Worker count is **already** detector-derived, RAM-aware and cgroup-clamped, so half of C-4 was work already shipped. Consequence: **bounding the scans alone does eliminate the observed OOM.** |
| **R-7** | "It is structural, not a query bug — the query fix already landed." | **REFUTED.** | Every metric these endpoints compute is expressible as SQL: `Count(filter=Q(...))`, a `CASE` bucket, and a `GROUP BY` on the extracted `request_id`; the time series need `TruncHour`/`TruncDay`. There is **no `Trunc*` anywhere in the file.** What landed was a *projection narrowing*, not a *pushdown*. Calling it structural justified the expensive C-2/C-5/C-6 work while leaving a cheap, complete fix unclaimed. Worse: **C-1 as written is the wrong fix for the three endpoints that actually OOM'd** — their *responses* are already bounded (fixed-shape dicts, ≤31 buckets); the unbounded object is the **intermediate scan**, which response pagination does not touch. |
| **R-8** | "Milvus is declared but undeployed; potion/hnswlib are absent — so this is greenfield work in the right direction." | **DIRECTION ALSO WRONG, not just the baseline.** | Milvus's query method **unconditionally raises**, and the class has **no `add`/`upsert`/`delete` at all** — it cannot serve as a warm tier even with a server. pgvector was the system store and was **deliberately removed**. And the hot-tier design is *architecturally* impossible, not merely unbuilt: retrieval is **BYOK**, so the vectors live in the **customer's** index (Chroma even embeds server-side), and `hnswlib` has **no namespace primitive** — a shared in-process index reintroduces the exact `None__{collection}` cross-tenant collapse that RAG-03 was written to prevent. |

**The pattern across both rounds, stated plainly:** I repeatedly reasoned from declarations (a `pyproject` entry, a compose limit, a changelog title) instead of from call sites and cgroups. Round one caught it for the vector stack; round two caught it for the OOM mechanism and the fan-out safety argument. Every quantitative claim remaining in this document is now either tagged with a file:line or explicitly marked **UNVERIFIED**.

### 0.4 Claims that survived both rounds

Worth recording, so the plan is not read as wholly discredited:

- The **unbounded Python materialisation** diagnosis (§3.1 item 3) — confirmed, and **understated**: there is a fourth affected endpoint, and the row count is ~8.6 M at the 30-day ceiling.
- **`.iterator()` is inert** because server-side cursors are disabled — confirmed independently by both OOM reviewers, and named "the single biggest omission" of the original diagnosis.
- **No `statement_timeout`, no `--max-requests`** — confirmed; C-7 stands as the highest-leverage line in §3.
- `resolve_and_enforce()` **is** pure (`enforcement.py:230-340`, list copies, frozen return) — the one fan-out premise that held. It was simply never the risk.
- **Removing Bedrock costs exactly one fail-closed control** — confirmed, with an important sharpening in §8: the degraded path fails *closed* on PII but **fails open on injection**.

---

## 1. What was live-verified during this pass

All rows are **[L]**, gathered read-only. No infrastructure was modified.

| Subject | Verified state | Implication |
|---|---|---|
| Production serving path | All production hostnames resolve to addresses matching the **AWS ap-south-1** published ranges. `…gateway…` returns `server: uvicorn`. | Independently re-confirms MASTER §11.3: **NLB terminates into gunicorn; nginx is not in the `/v1` path.** D11 remains correct and unshipped. |
| GCP environment | Project in `asia-south1` exists; serves no production traffic. | Consistent with "DevOps owns migration." This plan stays agnostic. |
| Edge TLS | Managed certificate stuck `PROVISIONING` / `FAILED_NOT_VISIBLE` on all three domains. Root cause: **legacy** managed certs validate by requiring the domain's A record to already point at the LB. | **Not an architecture defect.** DevOps fix: Certificate Manager with **DNS authorization** (validates via CNAME/TXT, needs no traffic move). Recorded for them; out of our scope. |
| Cache tier | 26 GiB, **`BASIC`** tier, AUTH **off**, transit encryption **off**, persistence **off**. | §5.3 — **hard sequencing constraint** on fail-closed. |
| Managed SQL | No managed Postgres instance; the SQL Admin API is not enabled. Postgres runs **in-compose on the app host behind PgBouncer**. | §3 — this is the OOM's structural cause. |
| Edge WAF | **Zero** security policies. | §5.2 — no L7 rate limit or bot control in front of `/v1`. |
| Availability | Two instance groups, **1 node each, single zone**. | No HA anywhere yet. |
| Backend timeouts | HTTP backend **60 s**; TCP backend **300 s**. | §5.1 — **60 s severs long SSE streams**; MASTER §11.5 requires the whole chain ≥ 350 s. |
| Egress | **No Cloud Router / NAT exists**; instances carry external IPs. | §7.3 — a private LB-fronted fleet *will* need NAT, and it must be port-sized deliberately. |
| Engine sequencing | `main.py` hot path = serial `await asyncio.to_thread(...)`. `asyncio.gather` present only in `rag_collections.py` and `rag_pipeline/grounding_guard.py`. | §4 — the parallelisation gap. |
| Haiku footprint | **Corrected by review — see §0.2/R-3.** The authoritative default is `platform_models.py:11` (`DEFAULT_HAIKU_45`) with the resolution chain at `:91` (T2 scanner) and `:99` (adjudicator). `llm_judge.py:83` and `query_stage.py:410` are *consumers*, not the source. Control plane defaults to a **different** model (`openai.gpt-oss-120b-1:0`). | §8 — still bounded, but the central constant was missed on first pass. |
| Vector stack | **Refuted by review — see §0.2/R-1.** `pymilvus` is declared but **no Milvus server is deployed in either compose file**, and `MilvusClient` fails closed. Live stores are **Pinecone** (primary BYOK) and **ChromaDB** (profile-gated). `potion-base-8M`, `hnswlib`, `faiss` and all local inference are **absent from the codebase**. | §9 — rewritten. |

---

## 2. The service-class contract (how this plan stays platform-agnostic)

Because migration is DevOps-owned, every decision below names a **capability with a measurable acceptance property**, then maps it. If a substitute meets the property, it is conformant.

| # | Capability | Required property (this is the contract) | AWS instantiation | GCP instantiation |
|---|---|---|---|---|
| S1 | Chat-path edge | Terminates TLS; **streams SSE without buffering**; backend/stream timeout **≥ 350 s**; passes client IP; no per-target connection ceiling below fleet need | NLB + `preserve_client_ip` | Global external **Application LB** |
| S2 | Edge policy | L7 rate limit, bot/DDoS, per-path rules, **must not sit on the token stream** | WAF | Cloud Armor |
| S3 | Hot shared state | ≤ 1 atomic op/request; **replicated with automatic failover**; AUTH + TLS on | ElastiCache Valkey CME | Memorystore **`STANDARD_HA`** / Cluster |
| S4 | Control-plane OLTP | Managed Postgres, HA, migrations, transactions | RDS/Aurora PostgreSQL | **AlloyDB** |
| S5 | Analytics reads | Serves full-window SOC aggregations **without touching OLTP working set** | Aurora reader / ClickHouse | **AlloyDB read pool + columnar engine** |
| S6 | Telemetry bus | Non-blocking producer; bounded local ring; measured completeness | MSK | Pub/Sub or Managed Kafka |
| S7 | Analytical store | Columnar, retains 52 KB-class events at fleet rate | ClickHouse | ClickHouse / BigQuery |
| S8 | Compute orchestration | Per-fleet autoscaling; multi-zone; GPU node pools | ECS on EC2 | **GKE** (regional) |
| S9 | Guard accelerator | ≤ 8 ms p99 for the 86M classifier at fleet batch | g6/g6e (L4/L40S) | **g2 + `nvidia-l4`** |
| S10 | Vector recall | Hot path ≤ 2 ms p99; managed tier for the long tail | in-process + managed | in-process + Vertex Vector Search |

**Rule:** MASTER's D-numbers remain the reasoning of record. Where a D-number cites an AWS SKU, read it as the S-row above. Two D-numbers change *verdict* (not reasoning) under the platform decision: **D13 → GKE** (§5.4) and **D11 → L7 ALB, conditionally** (§5.1).

---

## 3. Gap V-1 — The control plane and frontend (this is your OOM)

MASTER never modelled this tier. The OOM is **not** a query bug you have left to fix; the query fix already landed. It is **structural**, and it will recur under a different query until the structure changes.

### 3.1 Root cause, stated precisely

Four conditions hold simultaneously:

> **⚠ Corrected after the second review round (R-6, R-7).** Three of the four original conditions were wrong or already-shipped. **The actual mechanism is narrower and more fixable than the first draft claimed.** The superseded text is preserved as items 1–2 with their refutations attached, because the plan's earlier cost estimates were built on them.

**The mechanism, corrected:** the control container runs in its **own cgroup** (`cpus: "2"`, `memory: 2560M`, `CONTROL_WEB_CONCURRENCY: 2` → a **1280 MB per-worker budget**). `memory.max` therefore fires **regardless of how much host RAM is free**. A single 30-day scan allocates several GB. That is a 2–4× overrun on one request, inside its own limit. Host-level contention is a **separate, real risk with a different likely victim** — the gateway, at `12288M` with 16 workers.

1. ~~**Co-tenancy.**~~ **TRUE as a fact, NOT CAUSAL for this OOM.** Django/gunicorn, Postgres, PgBouncer, Redis, RabbitMQ, gateway and Celery do share one host and do contend — but the control cgroup kills the worker before host pressure is relevant. Keep this on the risk register for the **gateway**; remove it from the OOM causal chain.
2. ~~**Memory over-commit, "22.6 GiB on a 15 GiB host."**~~ **Number wrong, causality wrong.** The real sum is **22,656 MiB = 22.125 GiB** (my figure divided MiB by 1000). Over-commit against a ~15 GiB host is genuine at ≈147 %, but the OOM-killer-picks-largest-RSS argument selects the **gateway**, not a control worker — my own conditions 2 and 3 predicted different victims, which should have been caught before writing. `no swap` is **UNVERIFIED** (no `memswap_limit` in either compose file; it is a host property). Also note the *base* compose file sets **no control memory limit at all** and the detector will start up to **16 workers** on a 16-core host, so dev and prod fail by different mechanisms.
3. **Unbounded result materialisation — the actual cause, and worse than first stated.** Confirmed by both reviewers. The `KeyTransform` change corrected the *projection*, not the *bound*. Scope: **ten** further full-metadata projections remain, **eight unbounded**, plus a **fourth** affected KPI endpoint the first draft missed (`ModuleTrendsView`). At the 30-day ceiling a `soc-kpis` scan is **≈ 8.6 M rows** [D, from the repo's own measured 288 k rows/24 h], each a 4-key dict plus fresh strings ⇒ **≈ 3.5–6 GB peak heap** for one request, roughly doubled by the per-request bucket dicts it then builds. *(Bytes-per-row is estimated; marked **UNVERIFIED** at runtime.)* Worst unbounded endpoints: `/api/dashboard/model-usage/` and `/api/dashboard/risk-distribution/` (`days` ≤ **365**, no row cap, **fire concurrently from the same dashboard**); `/api/security/module-charts/` (30-day window walked **six times**); `/api/security/rag-pipeline-trace/` (**no time filter at all**).
4. **No worker recycling — half of this was already shipped.** `--max-requests` is genuinely **absent** (confirmed: zero occurrences in any server config, and there is no `gunicorn.conf.py`). But worker count is **already** detector-derived, RAM-aware and cgroup-clamped, so C-4's "derive from the detector" describes completed work. **The real defect is the detector's premise:** it sizes on `DEFAULT_PER_WORKER_RSS_MB = 512`, and these views allocate multiple GB — a **~10× violation** of the assumption the safety arithmetic rests on. Note also that recycling **cannot prevent this OOM**: it fires after a request completes; the kill happens during one.
5. **Concurrency multiplies per *thread*, not per worker — and this alone is sufficient.** These are **sync DRF views**, so they execute on the ASGI thread pool (`ASGI_THREADS`, fallback **8**). One worker can hold **8 concurrent full-window row lists inside a single 2560 MB cgroup**. The dashboard fires four of these endpoints in parallel (§3.4/F-1), giving a 4–8× peak multiplier. **This means "no recycling" is not even a required ingredient — one concurrent burst is enough**, which is exactly the observed symptom ("the dashboard died," not "one endpoint died").
6. **`.iterator()` is not a bound here — this one is a trap.** Several views were "fixed" with `.iterator(chunk_size=…)`. That provides **no memory ceiling**, because `DISABLE_SERVER_SIDE_CURSORS` is forced true (explicitly by env, and automatically whenever the DB port is PgBouncer's 6432 — which is how both compose files are wired). With server-side cursors off, Django fetches the **entire** result set client-side; `chunk_size` only affects Python-level batching. Any remediation that relies on `.iterator()` is ineffective by construction.
7. **Nothing terminates a runaway query.** There is **no `statement_timeout`** and no `idle_in_transaction_session_timeout` anywhere in the repo; Django's `OPTIONS` carry no timeout; PgBouncer sets connection caps only. Gunicorn's `--timeout 120` under `UvicornWorker` is an **async heartbeat, not a request deadline** — a sync Django view offloaded to the ASGI thread pool keeps the event loop responsive, so the worker keeps ping­ing and is never killed while the query runs. **The only termination path in the entire system is the kernel OOM killer.**
8. **No index supports the JSONB predicates.** There is **no GIN index on any `metadata` column in the codebase**, yet endpoints filter on `metadata__source`, `metadata__request_id` and `metadata__event_type`, and one module builds a **7-way `OR` of JSONB predicates**. These seq-scan the 472 MB table.

**Correction to the first draft's conclusion.** I wrote "fixing any one of these alone leaves the failure reachable." **That is false.** Items 3 and 5 are the causal chain; **bounding the scans (or pushing them into SQL) eliminates the observed cgroup OOM on its own**, with no change to host over-commit. Items 6–8 are independent hardening with their own justifications, and 1–2 belong to the gateway's risk register. Defence in depth is still the right posture — but it must be argued on merit per layer, not on a false claim that no single fix suffices. The honest sequencing consequence: **C-7 (deadline) and the SQL pushdown are the whole fix; everything else is insurance.**

**Correction to the "structural, not a query bug" framing.** Also false. Every metric these endpoints compute is expressible in SQL — `Count(filter=Q(...))` for the counters, a `CASE` expression for the latency buckets, `GROUP BY` on the extracted `request_id` for the request-scoped partition, and `TruncHour`/`TruncDay` for the two time series. There is **no `Trunc*` anywhere in the file**. What shipped earlier was a projection narrowing; the *pushdown was never done*. Calling the residue "structural" justified the expensive rollup work (C-2/C-5/C-6) while leaving a cheap and complete fix unclaimed — see the revised C-1 below.

### 3.1a Two defects found during review that are *not* performance issues

These surfaced while inventorying the OOM and must not be filed under it — they need their own fixes.

| # | Defect | Severity | Why it matters |
|---|---|---|---|
| **X-1** | The shared org-scoping helper `_enforcement_events_for_request` returns the queryset **unscoped across all tenants** when the caller is a superuser with no organisation set. | **Cross-tenant exposure class** | This is the same class the MCP hardening lane has spent ~130 changelog entries eliminating (org-slug is the sole tenant selector; a helper that can silently drop the filter is exactly the CHG-0111 pattern). It must be *fail-closed*: no org ⇒ no rows, never all rows. Treat as a security fix, not an analytics fix. |
| **X-2** | `RAGPipelineStageKpisView.escalation_distribution` is **always `{normal:0, elevated:0, strict:0}`**. | Silent metric regression | Collateral damage from the earlier `KeyTextTransform` migration: the queryset stopped projecting `metadata`, but the loop still reads `ev.get("metadata")` and `continue`s when the key is missing — so **every row is skipped**. A dashboard has been reporting structural zeros as fact. This is the precise failure mode C-2's rollup-equivalence gate (T-C2) exists to catch, which is why that gate is written as an oracle diff rather than a smoke test. |

### 3.2 The fix, in dependency order

| Step | Change | Why it is load-bearing | Cost |
|---|---|---|---|
| ~~**C-1**~~ | ~~Bound every analytics response with a server-enforced `limit`.~~ **Superseded by C-1′ — this was the wrong fix for the three endpoints that actually OOM'd.** Their *responses* are already bounded (fixed-shape dicts, ≤ 31 time buckets); the unbounded object is the **intermediate scan**, which response pagination does not touch. Response caps remain worth having for the *list*-shaped endpoints (`threat-feed`, `owasp-events`) where they already exist. | — | — |
| **C-1′** *(replaces C-1)* | **Push the aggregation into SQL.** `Count(filter=Q(...))` for the counters, a `CASE` expression for the latency histogram, `GROUP BY` on the extracted `request_id` for the request-scoped partition, `TruncHour`/`TruncDay` + `GROUP BY` for the two time series. Also **clamp the `days` parameter** on the two 365-day dashboard endpoints and **validate the `period` parameter** instead of silently defaulting (see T-C1). | This is the complete fix, and it is **cheaper than the rollup work it was going to justify**. Peak memory becomes proportional to the *number of buckets*, not the number of rows — ~31 dicts instead of ~8.6 M. | Low–Medium |
| **C-2** | **Pre-aggregate.** SOC KPIs, attack-vector trends and module KPIs become **incrementally-maintained rollups** (materialized views refreshed on a schedule, or the S7 analytical store). Serve reads from the rollup; never scan raw events for a dashboard. **Demoted after review:** C-1′ makes these endpoints survivable, so C-2 is now a **latency and ingestion-headroom** optimisation, not an OOM fix. Schedule it on that justification or not at all. | Needed at fleet-rate ingestion, when even a bucketed `GROUP BY` over 8.6 M rows costs too much per poll. Not needed to stop the OOM. | Medium |
| **C-3** | **Per-worker recycling.** `--max-requests` with jitter, plus a hard per-worker RSS ceiling that recycles rather than letting the kernel choose. | Confirmed absent. **But note it cannot prevent this OOM** — recycling fires *after* a request completes and the kill happens *during* one. Its value is bounding glibc arena retention and slow leaks. Keep, on that honest justification. | Low |
| **C-4** | **Fix the detector's premise and stop over-committing.** ~~Derive worker count from the resource detector~~ — **already shipped** (`server-entrypoint.sh` calls `resource_budget --value workers`, then clamps to the cgroup CPU budget, and prod pins `CONTROL_WEB_CONCURRENCY: 2`). What remains: (a) the detector sizes on `DEFAULT_PER_WORKER_RSS_MB = 512` while these views allocate multiple GB — a **~10× violated assumption** that must either be raised or made true by C-1′; (b) **cap the ASGI thread pool for the analytics path** so one worker cannot hold 8 concurrent scans (§3.1 item 5); (c) sum of limits **≤ 80 %** of host RAM — retained, but re-scoped as **gateway** protection, since control is cgroup-bounded independently. | Item (b) is the one that matters and was missing entirely. Item (a) makes the existing safety arithmetic true rather than nominal. | Low |
| **C-5** | **Split OLTP from the app host** → S4 managed Postgres (AlloyDB). | Ends the page-cache-vs-heap contention permanently. | Migration-coupled |
| **C-6** | **Route analytics to a read replica / read pool** (S5), never the primary. | A dashboard query can no longer degrade enforcement writes. AlloyDB's columnar engine is well matched to exactly these aggregations. | Migration-coupled |
| **C-7** *(new, from review)* | **A database-enforced query deadline.** Set `statement_timeout` (and `idle_in_transaction_session_timeout`) on the analytics role/connection. | This is the **single highest-leverage line in §3.** Today the only thing that stops a runaway query is the OOM killer. A deadline converts "kill a random worker" into "return an error for one request." It is one config line and it is independent of every other step. | Trivial |
| **C-8** *(new, from review)* | **Retire the remaining ten metadata projections**, and **do not** use `.iterator()` to do it. Extract the specific keys in SQL (the pattern already proven by PERF-0009/0010/0011), and add the missing index support for the JSONB predicates that survive. | Root-cause completion of C-1. The `.iterator()` prohibition is explicit because server-side cursors are disabled — see §3.1 item 5 — so that approach *looks* fixed and is not. | Low–Medium |

| **C-9** *(new, from review)* | **Restore the `request_id` type gate.** The `rid = rid_raw if isinstance(rid_raw, str) else None` guard was a meaningful check against a raw JSON value; under `KeyTextTransform` **every present value is a string**, so the gate is now vacuous and a numeric `request_id` ≥ 8 characters will **collapse distinct requests into one bucket**, under-reporting `requests_inspected`. Validate shape explicitly instead of relying on the Python type. *(Whether any non-string `request_id` exists in production data is **UNVERIFIED** — check before deciding severity.)* | Same class as X-2: a correctness regression introduced by a performance fix. C-1′ touches this exact code, so fix it in the same change. | Trivial |

C-1′ through C-4, **C-7, C-8 and C-9** are **host-local and platform-independent** — they can land without waiting for migration. C-5/C-6 are migration-coupled by your instruction and are folded in per your `with_migration` selection.

**Sequencing note, revised.** Land **C-7 first**, then **C-1′**. C-7 is trivial and is the only step that makes the failure *observable as an error rather than a corpse*. C-1′ is then the actual fix. Everything else — C-2, C-3, C-5, C-6 — is insurance or headroom and should be justified on its own merits, **not** on the (now-refuted) claim that no single fix suffices. The one exception is **C-4(b)**, the thread-pool cap, which belongs with C-7 because it removes the 8× multiplier that makes a single burst fatal.

### 3.3 Verification for §3

| Test | Success | Fail |
|---|---|---|
| T-C1 Window sweep: 1 h / 24 h / 7 d / 30 d **+ an out-of-range value** on every analytics endpoint. **⚠ Corrected:** the first draft swept "90 d," which **passes vacuously** — the period map tops out at 30 d and an unrecognised period silently falls back to **24 h**, so the test would have measured a 24-hour query and called it a 90-day pass. The out-of-range case must now assert an **explicit `400`**, not a silent default. | Every in-range response `2xx`; **peak worker RSS growth < 150 MB**; no worker restart; out-of-range period rejected explicitly | Any `502`, any OOM-kill in `dmesg`, RSS growth scaling with window size, **or an out-of-range period silently served as 24 h** |
| **T-C1b** Concurrency burst (§3.1 item 5) | The four heavy endpoints fired **simultaneously at the 30-day window** against the production 2-worker config: all four return, peak cgroup memory stays **< 70 %** of `memory.max` | Any worker killed, or peak memory scaling with the number of concurrent requests. **This is the test that reproduces the actual incident** — the single-request sweep in T-C1 does not. |
| T-C2 Rollup equivalence | Rollup-served numbers **identical** to the raw-scan oracle across ≥ 30 sampled windows | Any mismatch not explained by a signed-off definition change |
| T-C3 Soak, 60 min, dashboard poll at production cadence | RSS plateaus; recycle count matches `max-requests`; zero OOM | Monotonic RSS growth, or recycling that produces user-visible `5xx` |
| T-C4 Limit arithmetic | `sum(mem_limit) ≤ 0.8 × host RAM`, asserted in CI. **⚠ The number in the first draft was wrong** — the real sum is **22,656 MiB = 22.125 GiB** (24,320 MiB with the profile-gated services), not "22.6 GiB." The CI assertion must compute the sum from the compose files, **never hard-code a figure**, precisely because the hand-computed one was wrong. | Any config where the sum exceeds the host, or an assertion that carries a literal instead of a computed sum |
| **T-C4b** Detector-premise check (C-4a) | Measured p99 per-worker RSS under T-C1b is **within** `DEFAULT_PER_WORKER_RSS_MB`; if not, the constant is raised or the workload is bounded until it is | The detector continues sizing on a premise the workload violates — the safety arithmetic is nominal, not real |
| T-C5 Contention control | With analytics under load, enforcement-write p99 **unchanged within 10 %** | Enforcement p99 degrades — proves the split (C-5/C-6) is still required |
| **T-C6** Deadline proof (C-7) | A deliberately pathological query (365-day `model-usage` on a seeded table) returns a **query-timeout error**; worker survives; `dmesg` clean | The request completes by exhausting memory, or the worker dies — the deadline is not actually applied to that connection path |
| **T-C7** Projection audit (C-8) | A repo-wide assertion that **zero** analytics queryset projects a raw `metadata` blob; **zero** rely on `.iterator()` for a memory bound | Any surviving full-blob projection, or any `.iterator()` justified as a bound |
| **T-C8** Tenant scoping (X-1) | Superuser-with-no-org against every scoped endpoint returns **no rows**; a cross-org canary row is **never** visible | Any request returns another tenant's row. **This gate is a security gate — it blocks the phase, not just the step.** |
| **T-C9** Zero-metric regression (X-2) | Every dashboard aggregate is diffed against a raw-scan oracle; **no metric is structurally zero** while source rows exist | Any aggregate returns all-zeros against non-empty source data |

**Rollback for all of §3:** each step is independently revertible; C-1/C-3/C-4/C-7 are configuration, C-2 is additive (the raw path remains until T-C2 passes three times). **X-1 is not rollback-eligible** — a tenant-scoping fix does not get reverted for convenience.

### 3.4 Gap V-3 — the frontend is a demand-side amplifier, not a bystander

Review measured the client that drives these endpoints. **The OOM has a supply side and a demand side, and only the supply side was in the plan.** Every item below is client-side and independently fixable.

| # | Measured behaviour | Consequence |
|---|---|---|
| **F-1** | One cold overview load fires **11 HTTP requests**, of which **4 are the heavy windowed analytics calls, issued concurrently**. One time-lens change fires **4 more, concurrently**. | Against a **2-worker** prod control plane, a single user changing the time window can occupy every worker with the most expensive queries in the system simultaneously. This is why the OOM presents as "the dashboard died," not "one endpoint died." |
| **F-2** | The shared fetch helper has **no timeout and no `AbortController`**. | A hung worker holds the browser request until nginx's `proxy_read_timeout` (**300 s**). Stale in-flight requests are never cancelled on navigation or window change, so **abandoned work keeps executing server-side** and superseded responses can still land. |
| **F-3** | **No caching or de-duplication layer** (no react-query/SWR equivalent). | Identical windows are re-fetched on every mount and every lens change. Nothing coalesces concurrent duplicate requests. |
| **F-4** | **Polling does not pause on a hidden tab** — ~28 requests/min per open overview tab, indefinitely. Module-page refetch has **neither debounce nor an in-flight guard**. | Background tabs generate permanent load. Worse: because refetch is triggered by live enforcement-event traffic, **an attack spike multiplies analytics load at exactly the moment the control plane is least able to absorb it.** That is a self-amplifying failure, and it is the mechanism most likely to have produced your incident. |
| **F-5** | Single ~1.77 MB JS chunk; no code splitting or manual chunking. | Cold-load cost, not an OOM contributor. Recorded for completeness. |
| **F-6** | The "Failed to load" banner comes from `Promise.allSettled`, so the page **partially renders**; the failed-endpoint list is **three hard-coded strings**, and one endpoint fails **silently**. | The banner under-reports. It named the two endpoints it knows about — it cannot report the ones not in the literal. Diagnosis based on that banner is therefore incomplete by construction, which is consistent with §3.1's finding that ten more hauls were never reported. |

**Fix, in order (all client-side, no backend dependency):**

| Step | Change |
|---|---|
| **F-a** | **Pause polling when the document is hidden**, and add a debounce + in-flight guard to event-triggered refetch. Highest leverage: it removes both the idle-tab baseline and the attack-spike amplification. |
| **F-b** | **Give every request a timeout and an `AbortController`**, and cancel superseded requests on lens change and unmount. Stops the client from funding server-side work nobody will read. |
| **F-c** | **Serialise or stagger the heavy windowed calls** — cap concurrent analytics requests (a small client-side semaphore is sufficient) so one user cannot occupy the whole worker pool. |
| **F-d** | **Add request de-duplication and short-TTL caching** keyed on endpoint+window. |
| **F-e** | **Make the error surface honest** — report the actual failing endpoint set rather than a hard-coded list, so the next incident is diagnosable from the UI. |

| Test | Success | Fail |
|---|---|---|
| **T-F1** | Hidden tab for 10 min issues **zero** analytics requests | Any request from a hidden tab |
| **T-F2** | A burst of live enforcement events produces a **bounded** refetch count (debounce honoured, no overlap) | Refetch count scales with event count |
| **T-F3** | Lens change during an in-flight load **aborts** the superseded requests (observable as client-cancelled) | Superseded requests run to completion |
| **T-F4** | Cold overview load issues **≤ 2 concurrent** heavy analytics requests | 4 concurrent, i.e. unchanged |
| **T-F5** | With a deliberately slow endpoint, the client surfaces a timeout **well before** nginx's 300 s | Browser hangs to the proxy timeout |

**Interaction with §3:** F-a/F-c reduce *arrival rate*; C-1/C-2/C-7/C-8 reduce *per-request cost*. Neither substitutes for the other, and the frontend fixes are the cheaper half. **Do F-a and F-b first** — they are small, carry no backend coupling, and shrink the load that every subsequent server-side measurement is taken under (which also makes C-1/C-2 benchmarks trustworthy rather than noisy).

---

## 4. Gap V-2 — Running the security engines in parallel

This is the largest *unclaimed* latency win in the system, and it is available without new infrastructure.

> **⚠ Rewritten twice.** Round one (R-2) showed the fan-out graph was **wrong in a way that would have caused a security regression**. Round two (R-5) showed the *corrected* graph was **still unsafe**, for a different reason I had not considered: the stages are not merely ordered, they are **mutually exclusive terminal writers**. The conclusion after both rounds is that **stage-level fan-out is the wrong transform for this pipeline**. The right one is **prefetch without reordering** (§4.2). Both refutation tables are kept.

### 4.0 What the first draft got wrong

| Claim | Refutation |
|---|---|
| "Policy engine and T1 input scan can run concurrently." | **False, and unsafe.** The policy stage **mutates the prompt** — it produces `effective_prompt` by applying redaction, and T1/T2 scan *that mutated text*. This is not incidental: PIPELINE-0009 exists **specifically** because redaction must land **before** `input_scan` sees the text (co-matching redact+block rules were producing hard blocks without masking). Running them concurrently would hand the scanners the **unredacted** prompt — reintroducing the exact defect PIPELINE-0009 fixed. **Policy is a producer, not a peer.** |
| "T1 ∥ T2 gives concurrency." | **They are not independent.** T2 **invokes T1 internally** and merges the findings. There is no "T1 task" and "T2 task" to overlap. |
| "This is real parallelism — hyperscan and ONNX release the GIL." | **Those components are proposals, not code.** Today's T1 is **pure-Python regex and heuristics**. Concurrent `to_thread` over pure-Python detectors is **GIL-bound: it adds scheduling overhead and buys nothing.** Vectorscan (D10) and the ONNX classifier (D3/PG2) must land *first* for CPU-detector fan-out to mean anything. |
| Thread-pool headroom assumed 8. | The Tier-1 scan pool defaults to **4** threads. Fanning out into a 4-thread pool under load risks **starvation**, converting a latency optimisation into a queueing regression. |

The general lesson, and it applies to the rest of this plan: **MASTER describes a target architecture, and I read some of its target-state components as though they were shipped.** Every remaining performance claim in this document that cites a library has been re-checked against the manifests for that reason.

### 4.0b What the *second* draft got wrong — the more important round

The round-one fix (keep policy serial, fan out the rest) is **also unsafe**. Four findings, in descending severity:

| # | Finding | Why it invalidates stage-level fan-out |
|---|---|---|
| **1** | **Every pre-scanner stage is a terminal writer.** On a hit, each one emits telemetry, writes an audit record, increments `METRICS["blocked"]`, and **`return`s**. | Today mutual exclusion is guaranteed **by the early `return`**, not by any explicit lock. Concurrency destroys it. One request that trips keyword **and** policy **and** the scanner would emit **three** `input_blocked` telemetry events, **three** audit records, and triple-count the Prometheus counter — directly violating PIPELINE-0007's "ONE authoritative `final_action` / single-writer `_build_safe_block_response`" and adding pressure to the CHG-0094 audit-backpressure ceiling. **The audit trail is a security artefact; duplicating it is a correctness defect, not a cosmetic one.** |
| **2** | **`_pii_detection_enabled` is decided by inspecting state the policy stage wrote.** It tests whether smart-mask markers are already present in `scan_text`. | Under fan-out that test races, so PII detection can be enabled or skipped nondeterministically — re-opening **PIPELINE-0012** (pre-masked values misclassified) and destabilising `tier1_pii_detected`, which is the **fail-closed lever** for the degraded path (PIPELINE-0006). A racing read on a fail-closed input is the worst possible place for nondeterminism. |
| **3** | **Stages mutate the request body in place**, not only the prompt: the model-downgrade path rewrites `requested_model` and `body["model"]`, and the rewrite path replaces `body["messages"]`. | My round-one fix quarantined *prompt* mutation and missed *body* mutation. The set of producers is larger than one stage. |
| **4** | **The policy engine spawns a raw daemon OS thread per regex evaluation**, inside a per-rule loop — with the CISO package that is **264 rules**, i.e. hundreds of pthreads per policy evaluation, already running inside a `to_thread` worker. Its watchdog `join(1.0)` can pin that worker for a full second. | Fan-out multiplies thread creation by the fan-out width. This is a **pre-existing stability problem that fan-out would amplify**, and it deserves its own fix (§4.7) independent of any concurrency work. |

Two smaller corrections worth recording:

- **My PIPELINE-0005 citation was a category error.** I cited the "structural line-number invariant" as evidence that fan-out is safe. It is the opposite: that invariant is a *proof about serial source order* — blocks precede model calls **because they appear earlier in the file**. Concurrency dissolves the very property being cited. Quoting it in support of fan-out inverted its meaning.
- **A `gather` at this layer was already tried and removed.** There is a comment in `main.py` marking a T2-vs-adjudicator `asyncio.gather` that was taken back out. The idea has prior art in this repo, and the prior art is a revert.

### 4.0c The transform that is actually safe: prefetch, don't reorder

The win I was chasing is real, but it is an **I/O** win, and it does not require running any stage concurrently:

> **Fetch the state concurrently; evaluate the gates serially, in the existing order, with the existing short-circuits.**

Concretely: issue the independent Redis reads (kill-switch / firewall-enabled, model state, org rate-limit counters) as **one pipelined round-trip** *before* the gate sequence, then run the gates exactly as they run today against the prefetched values. This:

- removes the network RTTs — which is where the measurable latency was;
- changes **no** stage ordering, so PIPELINE-0009 redaction-before-scan is untouched;
- preserves every early `return`, so mutual exclusion, single-writer telemetry and the audit trail are unchanged;
- introduces no thread concurrency, so the GIL, the 4-thread pool and the thread-per-regex behaviour are all irrelevant;
- leaves `pipeline_trace` stage timings non-overlapping, so the PIPELINE-0018 latency-parity gate keeps passing (see §4.7).

The only new correctness obligation is **staleness**: a prefetched kill-switch value is read microseconds earlier than before. That is bounded by the same TTL semantics the current read already tolerates, and it must be asserted (T-P8).

### 4.1 Why fan-out is safe here — restated precisely

Fan-out normally endangers a security pipeline because ordering carries meaning. **It is safe for a specific, provable subset**, and the repo's own changelog is the proof:

- `resolve_and_enforce()` is already a **pure function over the complete finding set** (PIPELINE-0004).
- `_build_safe_block_response` is already a **single-writer** authority (PIPELINE-0007).
- Terminal-block short-circuit is a **structural line-number invariant** that precedes all model calls (PIPELINE-0005).

Therefore: for detectors that are **pure functions of the same input text**, *gather → resolve once* produces **the same verdict** as *detect serially → resolve*. What review establishes is that the *membership* of that set is narrower than I claimed: it excludes anything that **produces** the text others consume.

### 4.2 The corrected dependency graph

```
   ┌─────────────────────────────────────────────────────────┐
   │  PREFETCH  (I/O only — no stage body runs here)          │
   │    kill-switch / firewall-enabled      (Redis)           │
   │    model-state lookup                  (Redis)           │
   │    org rate-limit counters             (Redis)           │
   │  → 3 sequential round-trips collapse to 1 pipeline       │
   └──────────────────────┬──────────────────────────────────┘
                          │   values in hand; nothing decided yet
        auth (T0 RAM) → admit snapshot
                          │
        keyword / kill-switch / model-state gates
        policy engine        ← producer: effective_prompt, body[…]
        threat-intel lookup
        advisory backend scan
        input scan (T1; T2 subsumes T1 internally)
                          │
        ── ALL of the above stay in EXACTLY today's order ──
        ── every early `return` preserved  → mutual exclusion ──
        ── PIPELINE-0009 redaction-before-scan preserved ──
        ── PIPELINE-0007 single-writer telemetry preserved ──
                          │
          resolve_and_enforce()   ← single authority, unchanged
                          │
             per-model rate limit → circuit breaker → upstream
```

**Nothing is reordered.** The only structural change is that the independent Redis reads are hoisted into one pipelined prefetch, and the gates then consult in-memory values instead of issuing a round-trip each.

**What is explicitly *not* attempted, and why:**

| Candidate | Status | Reason |
|---|---|---|
| Concurrent pre-scanner gates | **Rejected** | Terminal writers — duplicated telemetry/audit, violates PIPELINE-0007 (§4.0b-1) |
| `policy ∥ input_scan` | **Rejected** | Producer/consumer — violates PIPELINE-0009 (§4.0-1) |
| `T1 ∥ T2` | **Not a thing** | T2 invokes T1 internally |
| CPU-detector fan-out | **Deferred indefinitely** | GIL-bound today; *and* even after Vectorscan/ONNX it needs the terminal-writer problem solved first, which is a larger refactor than the win justifies |
| `asyncio.gather` at the T2/adjudicator boundary | **Already reverted in-repo** | Prior art exists and it was taken back out |

### 4.3 The arithmetic, restated honestly

**The prefetch win is real and it is the *only* win now claimed.** Three independent Redis reads currently issued sequentially collapse into one pipelined round-trip: **two network RTTs removed from every request**, single-digit milliseconds on a same-AZ cache. It is I/O-bound, so it is genuine concurrency; it carries no ordering risk because no ordering changes.

**The ~40 % detector-segment prize is withdrawn from this plan.** MASTER's §6.2 serial sum (~13.6 ms) against a T2-bound fan-out floor (≈ 8 ms) is arithmetically fine, but it is unreachable without (a) Vectorscan + ONNX to make the detectors GIL-releasing, **and** (b) refactoring every pre-scanner stage from "detect-and-return" into "detect-and-report," so that a single writer resolves the verdict. (b) was invisible in both earlier drafts and is the expensive part. Until someone chooses to pay for (b), **the honest position is that detector fan-out is not on the roadmap** — see O-9.

| | Prefetch (now) | Detector fan-out (withdrawn) |
|---|---|---|
| Bound by | network I/O | CPU |
| Real concurrency today? | **Yes** | No — GIL |
| Win | **2 network RTTs** | ≈ 40 % of the detector segment |
| Blocker | none | GIL **and** the terminal-writer refactor |
| Risk | staleness only (bounded, T-P8) | duplicated audit trail, PIPELINE-0012 race |

[D — the withdrawn figure derives from MASTER's *target* numbers and must not be quoted as an available win.]

### 4.4 Guards, reduced to what the prefetch design actually needs

Guards 1–4 of the previous draft existed to make fan-out safe. **With fan-out withdrawn, most of them are moot** — which is itself the argument for the prefetch design. What remains:

1. **Bounded staleness.** A prefetched value is read marginally earlier than the gate that consumes it. Assert the window is within the TTL the current read already tolerates, and that the **kill-switch specifically** is either re-read or provably TTL-safe. A stale kill-switch is a fail-*open* window and must be argued explicitly, not assumed (T-P8).
2. **Prefetch failure is not a bypass.** If the pipelined read fails or times out, each gate must fall back to its **current** behaviour — including its current fail-closed posture — never to a default that admits. Fail-open on infrastructure error is the exact class this codebase has spent 150 changelog entries removing.
3. **No stage may move.** Enforce as a test, not a convention: assert the observed stage sequence in `pipeline_trace` matches the expected order, so a future refactor that "helpfully" parallelises anything fails CI (T-P6).
4. *(Retained from R-2, still binding if anyone revisits fan-out later)* **No prompt- or body-mutating stage may enter a concurrent wave, and no terminal-writer stage may enter one either.** Both are shipped security properties (PIPELINE-0009, PIPELINE-0007).

### 4.5 Ordering constraint — revised again

**The prefetch change may land after MASTER's Phase 1 gate.** The ~17 synchronous Redis `PUBLISH` calls per request stall the event loop under RC-1; while they are on the hot path, an RTT saving is inside the noise and would not be measurable.

**Detector fan-out has no scheduled slot.** It is gated behind Vectorscan + ONNX *and* the terminal-writer refactor (§4.3). Do not carry it as a Phase-2 line item.

### 4.7 Two defects surfaced by this review that are *not* concurrency work

These were found while checking fan-out safety and are worth fixing on their own merits:

| # | Defect | Severity | Note |
|---|---|---|---|
| **P-1** | The policy engine spawns a **raw daemon OS thread per regex evaluation**, called inside the per-rule loop — **264 rules** with the CISO package means hundreds of thread creations per policy evaluation, already inside a `to_thread` worker, with a `join(1.0)` watchdog that can pin that worker for a full second. | **High** | This is a per-request stability and latency cost that exists **today**, with no concurrency change. The correct pattern is a shared bounded executor or a timeout-capable regex engine, not thread-per-match. Independently schedulable, and probably the largest single hot-path defect the review found. |
| **P-2** | The Tier-1 scan pool defaults to **4** threads, process-wide and shared. | Medium | Sized for serial use. Any future concurrency work must raise it first; but it is also worth reviewing against current load independently, since P-1 can occupy those workers for up to a second each. |

### 4.6 Verification for §4

| Test | Success | Fail |
|---|---|---|
| T-P1 **Verdict equivalence** — full adversarial corpus (≈ 1 150 cases) before and after the prefetch change | **Byte-identical** verdicts, blocked-stage, and compliance tags | *Any* divergence. Hard gate; no sign-off path. |
| T-P2 Critical path | Segment p50 drops by **≈ 2 network RTTs**; no stage's own timing changes | No measurable improvement (the RTTs were not the cost), or any stage timing shifts (something moved) |
| **T-P6** Ordering invariant (guard 3) | `pipeline_trace` stage sequence is asserted against the expected serial order; scanners receive the **redacted** `effective_prompt`; a co-matching redact+block fixture still redacts (PIPELINE-0009 lock) | Any reordering, or the PIPELINE-0009 fixture regresses. **Hard gate.** |
| **T-P8** *(new, R-5)* Staleness bound (guard 1) | Prefetch-to-consume window measured and **within** the existing TTL tolerance; a kill-switch flipped mid-request is honoured no later than today | A wider fail-open window than the pre-change behaviour |
| **T-P9** *(new, R-5)* Prefetch-failure posture (guard 2) | With Redis failing/timing out, every gate exhibits **exactly** its pre-change behaviour and posture | Any gate admits a request it would previously have blocked or deferred |
| **T-P10** *(new, R-5)* **Single-writer audit invariant** | For a request that would trip multiple gates: **exactly one** `input_blocked` telemetry event, **one** audit record, **one** metric increment | More than one of any — the PIPELINE-0007 property is broken. **Hard gate, and the test that would have caught the round-two defect.** |
| **T-P11** *(new, R-5)* **Latency-parity gate still green** | The shipped PIPELINE-0018 Playwright gate passes: backend `pipeline_trace.total_latency_ms` **==** UI Duration within 0.1 ms, and `overhead_ms` is not clamped to 0 | Stage sum exceeds wall-clock (overlapping stages), which silently breaks a shipped customer-visible parity guarantee |
| T-P5 Concurrency isolation — 300 concurrent, unique canary per request | Zero cross-request contamination (extends CHG-0090/0101) | Any canary appearing in another request's verdict |
| ~~T-P3 / T-P4~~ | Withdrawn with fan-out — cancellation and per-detector deadlines were fan-out-specific guards | — |
| **T-P12** *(new, from P-1)* Thread-creation bound | A single policy evaluation over the 264-rule CISO package creates **O(1)** threads, not O(rules) | Thread count scales with rule count |

---

## 5. Platform-coupling defects

### 5.1 V-4 — The edge: resolving the D11 / §10 contradiction honestly

MASTER §10 forbids "ALB on `/v1/chat/completions`." The selected GCP edge is an L7 Application LB. **This must be reconciled, not glossed.**

MASTER's stated evidence for forbidding L7/CDN/API-GW is: API Gateway's non-adjustable 30 s integration timeout, no response streaming, and CloudFront's HTTP/1.1 origin with uncacheable POST. **None of those properties belong to a GCP global external Application LB**, which supports HTTP/2, SSE, and a configurable backend timeout. The prohibition was reasoned from *specific* AWS product limits, so it does not transfer automatically — **but it does not automatically dissolve either.**

What the L7 choice **wins**:

- **RC-6 disappears.** The ~55 k-connection SNAT ceiling per NLB-IP × target pair is an AWS NLB artefact. Google's frontend architecture does not impose that per-target ceiling. MASTER's RC-6 and the `preserve_client_ip` one-liner become **non-issues** on this platform — one of MASTER's ten root causes is deleted by the platform decision.
- Cloud Armor (S2) and managed certs attach natively.
- Client IP arrives via `X-Forwarded-For` rather than requiring L4 preservation.

What it **costs**, and must be tested rather than assumed:

| Risk | Why | Acceptance test |
|---|---|---|
| **Response buffering breaks token streaming** | An L7 proxy may buffer; SSE requires immediate flush | **T-E1** below — this is the go/no-go |
| **Backend timeout severs streams** | Live HTTP backend is **60 s** [L]; MASTER §11.5 requires the entire chain ≥ 350 s | T-E2 |
| Added proxy hop latency | Real, but small relative to a 0.8–3 s generation | T-E3 |
| HTTP/1.1 downgrade on the backend leg | Loses multiplexing | T-E4 |

**Verdict:** L7 Application LB is **conditionally accepted**, gated on T-E1 passing. If T-E1 fails, fall back to the S1-conformant L4 option (external **passthrough** Network LB with TLS terminated at nginx) — which is MASTER D11's design, unchanged, and remains the safe default.

**The timeout chain must be aligned end to end** (MASTER §11.5). Every hop ≥ 350 s:

```
edge/backend-service ≥ 350 s   →   nginx 360 s   →   gunicorn ≥ 350 s (default 120 s — TOO LOW)
     →   uvicorn --timeout-keep-alive 90 (default 5 s — TOO LOW)   →   LiteLLM   →   provider
```

The chain's value is its **minimum**. Today the minimum is the 60 s backend [L], then gunicorn's 120 s.

Also carry MASTER §11.5's edge findings: an empty POST must **403**, not 411; **the HTTP frontend must not 301 a POST**; `gzip` **off** for `text/event-stream`; `proxy_buffering off`; `upstream { keepalive 512; }` (absent today).

| Test | Success | Fail |
|---|---|---|
| **T-E1 SSE flush fidelity** — stream 200 tokens at 30 tok/s through the real edge | **Inter-token arrival at the client matches origin within 250 ms**; first token ≤ origin + 250 ms | Tokens arrive batched or only at completion ⇒ **edge buffers ⇒ reject L7, use L4 passthrough** |
| T-E2 Long stream | A **340 s** stream completes with no truncation | Any mid-stream disconnect |
| T-E3 Hop tax | Added p50 ≤ 5 ms, p99 ≤ 20 ms vs origin-direct | Exceeds budget |
| T-E4 Protocol | HTTP/2 client→edge; backend leg negotiated as configured | Silent downgrade |
| T-E5 Client IP | True client IP present and correct in enforcement records | Absent or the proxy's IP ⇒ per-IP controls are broken |
| T-E6 Edge policy | Cloud Armor L7 rate limit trips at threshold **without touching in-flight streams** | An in-flight stream is severed by an edge rule |

### 5.2 Edge policy (S2)

There are **zero** edge security policies today [L]. `/v1` is therefore protected only by in-process limits — meaning **every** abusive request costs a full gateway admit cycle. An L7 rate limit and bot control in front of the fleet is the cheapest capacity you can buy. **Constraint:** it must be a connection/request-rate control, never a body-inspection rule on the token stream (that reintroduces the buffering risk of T-E1).

### 5.3 V-6 — Cache tier, and a sequencing hazard that can cause an outage

Live state: 26 GiB, **`BASIC`**, no replica, AUTH off, TLS off, persistence off [L].

MASTER's D5 rejected "GCP Memorystore 1 GiB BASIC" partly on eviction grounds. At 26 GiB the eviction argument is weak. **The availability argument is far stronger than MASTER stated**, because of an interaction MASTER could not see:

> MASTER §11.4 and your `fail_closed_ha` decision require **TPM/budget and kill-switch to fail CLOSED when Redis is unreachable**. On a `BASIC` single-node cache with no replica, *every* maintenance event, failover or node replacement makes Redis unreachable. **Fail-closed on a non-HA cache converts routine maintenance into a total request outage.**

**Therefore the order is not negotiable:**

1. Move to a **replicated tier with automatic failover** (S3), AUTH on, transit encryption on.
2. Verify failover behaviour under load (T-R2).
3. **Only then** enable fail-closed.

Enabling fail-closed first is a self-inflicted outage. This ordering is the single most important operational sequencing statement in this document.

Carry MASTER's client-side mandates regardless of tier — they are where the latency actually is (§0 row 3): `SCRIPT LOAD` + **`EVALSHA`**, hash-tagged `{org:<id>}` keys for single-slot scripts, per-concern pools with **bounded queueing instead of immediate raise** (RC-5), and reconsidered `retry_on_timeout`.

| Test | Success | Fail |
|---|---|---|
| T-R1 Tier | Replica present; AUTH enforced; TLS enforced; failover automatic | Any of these absent while fail-closed is enabled |
| **T-R2 Failover under load** | Forced failover at production RPS: error burst **< 2 s**, zero incorrect verdicts, no stuck breaker | Sustained errors, or a stale kill-switch serving traffic |
| T-R3 Round-trip count | Exactly **one** `EVALSHA` per request for limits; `MONITOR` shows no full Lua text on the wire | > 1 shared-state call, or full script text crossing the wire |
| T-R4 Fail-closed correctness | Redis unreachable ⇒ TPM/KS **deny**; burst/RPM may fail open per D6 | TPM ceiling disappears (today's inversion, RC-5) |
| T-R5 Pool exhaustion | Saturated pool **queues** within a bounded wait | Raises ⇒ `503` storm (RC-5) |

### 5.4 V-5 — Compute orchestration

MASTER D13 chose ECS on ENI-trunking and control-plane-fee grounds — both AWS-specific, so the verdict does not carry. Platform decision: **GKE, regional (multi-zone)**. MASTER's own escape clause anticipated this: *"Choose EKS only if we later adopt the Kubernetes inference ecosystem"* — the GPU guard pool (S9) and any platform inference are exactly that world.

Requirements the orchestrator must satisfy, which is what actually matters:

- **Separate node pools per hardware class** — CPU gateway pool and GPU guard pool are different failure domains (MASTER §4.3). Not "microservices"; D1's modular monolith is preserved: the gateway container still contains all business logic and the *only* permitted request-path hop is the guard classifier in Stage 2.
- **Regional, ≥ 3 zones**, with MASTER's ×1.5 headroom for AZ-loss tolerance.
- Scale on **in-flight requests / event-loop lag**, not CPU alone — CPU is a lagging indicator for a streaming workload.
- `PodDisruptionBudget` and a **termination grace period ≥ 350 s**, or a rolling update will guillotine in-flight streams.
- FD limits raised (MASTER §7.2: `LimitNOFILE` toward 1 048 576).

| Test | Success | Fail |
|---|---|---|
| T-K1 Zone loss | Kill one zone at load: error rate **< 0.1 %**, recovery without manual action | Sustained errors or capacity collapse |
| T-K2 Rolling update | Zero severed in-flight streams during a full rollout | Any client sees mid-stream truncation ⇒ grace period wrong |
| T-K3 Autoscale | Scales on in-flight metric before p99 degrades | Scales only after p99 breaches ⇒ wrong signal |
| T-K4 GPU pool isolation | GPU pool drain does not affect the CPU fleet; guard failure is fail-closed per policy | Guard unavailability silently fails open |

### 5.5 Control-plane database (S4/S5)

AlloyDB, per your selection. The reasoning that matters is **§3.6**: the SOC aggregations are analytical queries currently competing with transactional writes on one host. AlloyDB's **read pool + columnar engine** is a direct match — the analytics endpoints move to a read pool, the primary keeps enforcement writes.

Keep MASTER D7 intact otherwise: **the gateway opens zero database connections on the chat path** — verified by grep. This must be *held* as an invariant, not merely observed.

MASTER D7's rejection of Postgres as the **analytics** store also stands and is important: `EnforcementEvent.metadata` is JSONB ≈ the whole ~52 KB event; at fleet rate that is billions of rows and hundreds of TB/day. AlloyDB fixes the *dashboard aggregation* problem; it does **not** repeal the need for S7 (columnar) at 100 k RPS. Retain MASTER's payload-shrink requirement (D8: stage metrics + **one** content reference; the UI rehydrates the trace).

| Test | Success | Fail |
|---|---|---|
| T-D1 Chat-path DB isolation | **Zero** DB connections from the gateway during a chat, asserted in CI | Any connection appears |
| T-D2 Read/write split | Analytics on read pool; enforcement-write p99 unaffected under analytics load | Analytics degrades writes (repeat of today) |
| T-D3 Failover | Primary failover: writes resume automatically; no enforcement event lost | Data loss or manual intervention |
| T-D4 Migration parity | Full migration suite green; rollback proven on a clone | Any irreversible migration |

---

## 6. GPU guard tier (S9) — corrected

MASTER's D2/D3 staging is **retained without change**, now with verified in-region availability [L].

**Stage 1 (now):** **no GPU.** PG2-22M INT8 ONNX in-process. MASTER §11.5's warning is the binding constraint: in-process ONNX × 16 workers on an over-committed host **must** be RSS-counted first. If it does not fit — and per §3.1 it very likely does not today — use **one shared sidecar process**, not 16 copies. This interacts directly with §3's C-4 and must be sequenced after it.

**Stage 2 (≥ ~500 RPS sustained, or when p99 headroom vanishes):** L4 node pool, Triton + TensorRT, **PG2-86M FP16 first**. MASTER's INT8 warning stands and is sharp: a third-party INT8 export of PG2-86M lost **16 points of recall** (0.9625 → 0.8018) while 22M INT8 was lossless [I]. FP16 unless our own bake-off says otherwise.

### 6.2 Quota is the binding constraint at scale

| Admitted RPS | Classify/s (= RPS; sampling is a security hole) | L4 units needed [D] | Against quota 8 [L] |
|---|---|---|---|
| 500 | 500 | ~1 | fits |
| 20 k | 20 k | ~9 | **exceeds by 1** |
| 100 k | 100 k | **~45+** | **needs ~6× increase** |

Quota is a **lead-time** item, not a deploy-time item. File it at Phase 3, not Phase 5.

**Never** on the request path: Llama Guard 3 1B (53 ms/sample on A100 [I]) — `[DONE]` or async only, per MASTER D3.

| Test | Success | Fail |
|---|---|---|
| T-G1 Latency | 86M FP16 p99 **≤ 8 ms** at production batch, measured **on this fleet** | Exceeds 8 ms ⇒ Stage 2 not met; do not claim 12 ms |
| **T-G2 Recall table vs the outgoing engine** | Published table over the ≈ 1 150-case corpus **plus** MASTER D3's seven-item gap list. **Equality is explicitly not claimed.** | Table not published ⇒ Phase 3 fails regardless of latency |
| T-G3 **Prompt Overflow** — 4 malicious tokens per 512-token window | Detected via **stateful** window aggregation (sum/contiguity). `max()` alone is a **100 % bypass** [I] | Any bypass |
| T-G4 Character injection / homoglyph / emoji smuggling | Caught after canonicalisation (NFKC + Cf/ZW strip + **UTS-39 confusables**) | Bypass ⇒ canonicalisation incomplete (MASTER's R5 Cyrillic finding) |
| T-G5 Timeout storm | Benign traffic + 20 ms injected delay: **no self-DoS**; timeout reason distinct from `jailbreak`; `503` not `451` | Cascade, or a poisoned SDK error code |
| T-G6 Content-array split | Classifier scans flattened messages **and** `"".join(content parts)` | Jailbreak split across array items evades (MASTER §11.5) |
| T-G7 Stage-1 RSS | Measured RSS × worker count fits within C-4's budget | Does not fit ⇒ sidecar, not in-process |

---

## 7. The "100 k" target must be disambiguated before Phase 4

You selected 100 k. MASTER is emphatic that this names **three different promises** differing by ~1000× in cost, and that fusing them is what made earlier plans unexecutable.

| SLO | Meaning | Bounded by | 100 k cost |
|---|---|---|---|
| **F** — firewall tax | `T_total − T_upstream` | our code | engineering |
| **G** — gateway capacity | **admitted RPS** with guards on | our code + network | ~12 × 16-vCPU hosts ≈ **$3.8 k/mo** [D] |
| **C** — completed chats | end-to-end completions/s **with real generation** | **GPU fleet / tenant quota — not the gateway** | **$3.5–8.7 M/mo** [D] |

**This plan's reading, which needs your one-line confirmation:** the engineering target is **100 k admitted RPS (SLO G)**; **SLO C is a procurement line item**, quoted from MASTER D15, never promised. Every gate below is written against G.

Note the in-region availability of H100/H200/A3 [L] makes MASTER's self-hosted vLLM path *regionally* feasible — it does not make it cheaper. D15's arithmetic stands.

### 7.3 The ceilings that bind before compute does

MASTER §7.1 is the key insight and survives verification: **cutting our latency does not cut in-flight.** At 100 k RPS with 0.8 s generation, in-flight is **80 000** [D]. Each holds a client FD, an nginx upstream slot, a uvicorn task, and an upstream connection.

| Ceiling | Value | Status |
|---|---|---|
| Region **`CPUS` quota** | **500** [L] | ~31 × 16-vCPU hosts. Binds before MASTER's 12-host estimate does — but leaves little headroom. **Raise early.** |
| **`IN_USE_ADDRESSES`** | **69** [L] | Binds the fleet + LB + NAT address plan |
| **L4 GPU** | **8** [L] | §6.2 — needs ~6× for 100 k |
| Outbound ephemeral ports/host | ~28 k | **80 k in-flight upstream connections cannot fit on few hosts** |
| **Egress NAT port allocation** | **No NAT exists today** [L] | If the fleet goes private behind the LB, NAT port-per-VM allocation must be **explicitly sized** for 80 k concurrent upstream connections. A default allocation will silently throttle egress and present as upstream timeouts. |
| Memory per in-flight stream | **[NF]** — never measured | **MASTER's largest open number.** Phase 0 must measure it; the 80 k figure is unpriceable until then. |

| Test | Success | Fail |
|---|---|---|
| T-S1 In-flight memory | Measured MB per in-flight stream, published; fleet sizing derived from it | Still unmeasured at Phase 4 ⇒ capacity claims are unfounded |
| T-S2 Egress port headroom | At target in-flight, zero NAT/port-exhaustion errors; utilisation < 70 % | Any exhaustion ⇒ re-plan NAT/addressing |
| T-S3 Quota preflight | All four ceilings above verified ≥ 1.5× target **before** the load test | Load test run against insufficient quota ⇒ invalid result |
| **T-S4 Capacity, 3× consecutive** | **100 k admitted RPS, ≤ 0.1 % error, guards ON, unique prompts, in-region generators, token-emitting stub** | Any of: stub-LLM numbers, `/health` numbers, 6-stage runs, summed-IP arithmetic, in-flight counts quoted as RPS (all forbidden by MASTER §10) |

---

## 8. Removing Haiku completely

Your directive resolves an **internal inconsistency in MASTER**: D3 says "keep Haiku in two places" (opt-in slow pool + 0.1 % async sampler) while §11.1 says "Haiku is not a sold control… no customer slow-pool SKU" and notes the sampler *doesn't actually exist* (`tier2_post_scan` is a log sink that never calls Bedrock). **Remove entirely** — which also means **dropping the sampler claim**, exactly the option §11.1 left open.

Precisely bounded [L], **corrected after review (R-3)** — the first draft named consumers and missed the authoritative constant:

| Site | Action |
|---|---|
| **`gateway/ai_mesh_gateway/platform_models.py:11`** — `DEFAULT_HAIKU_45` | **The real source.** This is the central default, resolved at **`:91`** (`BEDROCK_TIER2_SCANNER_MODEL` → `BEDROCK_MODEL` → `DEFAULT_HAIKU_45`) for the **T2 scanner** and at **`:99`** for the **adjudicator**. Retiring this constant is the change; everything below follows from it. |
| `gateway/ai_mesh_gateway/llm_judge.py:83` — `BEDROCK_MODEL` default | Consumer of the above. Replace with the PG2 classifier interface |
| `gateway/ai_mesh_gateway/rag_pipeline/query_stage.py:410` — `rag_downgrade_model` | Replace or remove the downgrade path |
| `gateway/ai_mesh_gateway/bedrock_tier2_breaker.py` | Retire with its engine, or re-point at the classifier |
| `gateway/ai_mesh_gateway/main.py:1009,1063` | Cosmetic strings — update |
| `bedrock_scanner.py` | Audit for reachability after removal; delete if dead |
| **`control/.../security_engines/bedrock_client.py:9-12`** | **Separate default, not Haiku** (`openai.gpt-oss-120b-1:0`). The control plane is a **different removal decision** and must not be swept up silently in a "remove Haiku" change. Decide it explicitly. |
| Vendored `litellm` | **Do not touch.** Not our code. |

**Four more sites found in the second review round (R-5) — the removal is materially wider than a code edit:**

| Site | Action | Why it was missed |
|---|---|---|
| **`.env:66` — `BEDROCK_MODEL=<haiku>`**, re-asserted in `docker-compose.yml:231` | **The default is LIVE, not dormant.** Terraform and the deploy scripts set nothing, so production inherits it. Removing the code constant without changing the env leaves the pin in place. | I checked whether the constant existed, not whether it was *bound* at runtime. |
| **`control/.../core/simulator_seed.py:35-38`** — creates an **active** `LLMModelConfig` row for Haiku with `routing_priority=100`, invoked from `core/apps.py:67-68` **on every control boot** | Needs a **data migration** *and* a seed change. Deleting the row alone is futile: the next boot recreates it. | Database state is not visible to a code grep. |
| **`frontend/.../ModelConnectionPanel.jsx:32,41,44,72,84,86`** — customer-selectable BYOK option | Removing it **withdraws a customer capability**. That is a product decision with a changelog entry, not a cleanup. | I scoped "remove Haiku" as internal-only. |
| **De-leak scrubbers — `zeroshieldBrand.js:28-29,170-171`, `AttackSimulatorPanel.jsx:37-38`, `security_views.py:101-102` (`_RESERVED_MODEL_TOKENS`)** | **These must SURVIVE the purge.** They exist to prevent vendor names leaking into customer-visible output. A naive grep-and-delete removes the guard along with the usage. | — |

**A regression trap, recorded explicitly.** `security_views.py:93-100` documents that bare `"haiku"` tokens were **already removed once** from the reserved-token set, because they **over-matched customer BYOK model names and broke kill-switch management**. Any purge that re-broadens the matching re-introduces a known production defect. Match on fully-qualified identifiers only.

**The `BEDROCK_MODEL` contradiction — resolve this before touching anything.** There are six read sites and **two contradictory defaults**: Haiku in the gateway, `gpt-oss-120b` in the control plane and simulator. Because `.env` sets `BEDROCK_MODEL` globally, that single variable **pins the control plane and simulator to Haiku too**, overriding their own defaults. So deleting the two gateway lines does not remove Haiku — it **silently switches control and simulator to `gpt-oss-120b`**, a model change nobody requested. (`.env.sample:25-26` documents `gpt-oss`, so the sample and the live env disagree.) **The repo does not agree with itself about what the platform model is.** That ambiguity must be settled first, or the removal produces an unintended behaviour change in a second service.

**Failure modes on naive removal — why the 2-line edit looks safe and is not.** Deleting `DEFAULT_HAIKU_45` makes `default_tier2_scanner_model()` return `""` → a boto3 validation error on every call → the T2 breaker opens → the pipeline enters **degraded** mode. In degraded mode PII **fails closed** (PIPELINE-0006) — but **injection detection is simply gone, and injection fails open.** Meanwhile `llm_judge` falls back to `_regex_fallback` with only a `LOG.warning`. **Nothing crashes and no test fails**, which is precisely why this reads as a safe change. It is a silent security-envelope reduction.

**Test-suite blast radius:** **21 test files** reference Haiku, and **two `import DEFAULT_HAIKU_45` directly** — removing the constant is a **collection-time `ImportError`**, so the suite fails to start rather than failing informatively. Operational scripts and the leak-hunt harness also use a `haiku-cheap` alias as a genuinely allowlisted model, and `.skill-workspace/reverify_platform.py` asserts that `"haiku"` never leaks — that assertion must keep passing, which is another reason the scrubbers stay.

**Two review findings that change how this lands:**

1. **The security-envelope loss is smaller than feared, and now precisely known.** Removing Bedrock retires exactly **one** fail-**closed** control: the strict-mode Tier-2 **input** scan, and even that is gated on an org tier and an env flag that **defaults to false**. Every other Bedrock consumer — output guard (hardcoded non-strict), streaming, MCP, grounding, and the entire control plane — is **already fail-open**, meaning it contributes availability risk but no enforcement guarantee. **Consequence:** the honest framing is that removal costs one narrowly-scoped fail-closed path (which PG2 must replace *before* removal) and otherwise **removes fail-open dependencies, which is a reliability improvement.**
2. **The local-classifier landing zone is scaffolded but dead.** `GUARDRAILS_SERVICE_URL` is injected by compose and read by **zero** Python files; `services/guardrails` is an always-allow stub; there is no usable `onnxruntime` dependency (only a transitive one via `chromadb`). So PG2 is **a new dependency plus wiring a dead env var**, not "swap a model name." Budget it accordingly — and note the always-allow stub must never be reachable in a gate (MASTER's stub-fingerprint prohibition).

**Documentation is part of the change, not an afterthought.** MASTER D3's seven-item gap list (indirect injection FNR 0.69–0.97; Prompt Overflow; `goal_hijacking`/`social_engineering` labels Meta deliberately removed; character injection; multi-turn ASR > 90 %; over-defense 13–19 % on NotInject; confidence is not a safety signal) must ship in **product docs**. Removing a generative judge changes the security envelope; claiming parity would be dishonest.

| Test | Success | Fail |
|---|---|---|
| T-H1 Reachability | Zero Haiku/Bedrock-scanner calls under the full suite + adversarial corpus (assert at the network layer, not by grep) | Any call observed |
| T-H2 Recall delta | T-G2's table published, gap list in product docs | Removed without a published delta |
| T-H3 Dead code | No unreachable Bedrock scanner paths; `SAMPLE_RATE=1` on PG2 (**never** copy the T1-allow skip — MASTER §11.5 calls it a security hole) | Sampling introduced on the classifier |
| T-H4 Cache key | T2/PG2 cache key = `org + policy_version + classifier_digest + text` (**confirmed by review: today it is `org + text` only**) | Stale-policy verdicts served from cache |
| **T-H5** Fail-closed replacement *(new)* | The one fail-closed control (strict-mode T2 input scan) has an **equivalent PG2 fail-closed path proven by fixture** *before* Bedrock is removed | Bedrock removed while that path is fail-open or absent. **Hard gate — this is the only enforcement guarantee at stake.** |
| **T-H6** Stub unreachable *(new)* | The always-allow `services/guardrails` stub is **provably not** serving any gate or benchmark run (fingerprint assertion, per MASTER §8) | Any measurement taken against the stub |
| **T-H7** *(new, R-5)* **Runtime binding, not source absence** | After removal, `BEDROCK_MODEL` resolves to the intended value in **every** service (gateway, control, simulator) and in **every** env file and compose layer. Asserted at runtime, not by grep. | Any service silently switching model, or any env layer still pinning the old value |
| **T-H8** *(new, R-5)* **Seed idempotence** | After the data migration, a **control restart does not recreate** the Haiku `LLMModelConfig` row | The row returns on boot — the seed was not changed |
| **T-H9** *(new, R-5)* **Degraded-mode envelope** | With Bedrock unavailable, **injection detection is still present** (via the PG2 replacement), not merely PII-fail-closed | Injection fails open in degraded mode — the silent envelope reduction has shipped. **Hard gate.** |
| **T-H10** *(new, R-5)* **Scrubbers survive** | The de-leak token lists still block vendor-name leakage, and the `reverify_platform` no-leak assertion still passes; **and** kill-switch management still works for customer BYOK models whose names contain the token (the `security_views.py:93-100` regression) | Either the scrubbers were deleted with the usage, or matching was re-broadened and BYOK kill-switch breaks again |

---

## 9. Faster embedding and vector search

> **⚠ Rewritten after adversarial review (R-1).** The first draft described MASTER's *target* vector/embedding stack as though it were the shipped one. **It is not implemented at all.** The corrected baseline is §9.0; the design that follows is unchanged in direction but is now honestly labelled as **greenfield work**, not tuning.

### 9.0 The actual current state (corrected baseline)

| Claim in the first draft | Reality in the repo |
|---|---|
| "`potion-base-8M` in-process, 0.177 ms" | **`potion`, `model2vec` and `sentence-transformers` do not appear anywhere in the codebase or any manifest.** There is **no local embedding inference of any kind.** |
| "the store is Milvus" | `pymilvus` is *declared*, but **no Milvus server is deployed in either compose file**, and the client fails closed. |
| "hot tier = in-process `hnswlib`" | **`hnswlib`, `faiss`, `usearch` — all absent.** They exist only in comments and in MASTER's proposals. |
| implicit: "embedding is cheap" | **Every embedding is a network call** — Bedrock Titan v2 (256-d), Pinecone Inference, or a litellm provider. MASTER's own ≥ 297 ms hosted-RTT figure applies to the *current* system. |

**What is actually live:** **Pinecone** (primary, BYOK) and **ChromaDB** (profile-gated). `pgvector` is available (the Postgres image ships it) but the `EmbeddingVault` that used it is **dead code** — the instance is `None`.

**Sharpened in the second round (R-8) — the baseline is worse and the *direction* is also blocked:**

| # | Finding | Consequence for the proposed design |
|---|---|---|
| **W-5** | **The Milvus client cannot query *or* write.** Its query method **unconditionally raises**, and the class has **no `add`/`upsert`/`delete` at all**. | It is not "an undeployed backend" — it is a non-functional shell. It cannot serve as the warm tier even if a server were deployed. Remove it from the plan as an option. |
| **W-6** | **pgvector was the system store and was deliberately removed.** The vault instance is hard-set to `None`, and two call sites still branch on that permanently-`None` value. | "Re-enable pgvector" is a **reversal of a past decision**, not a quick win. Find out why it was removed before proposing it back. |
| **W-7** | **Embedding dimension is operator-pinned and enforced.** Orgs pin `embedding_dimension` (default 1536 via `text-embedding-3-small`; the grounding path uses 256-d Titan) and a mismatch **fails closed**. | **`potion-base-8M` (256-d) is not drop-in.** Substituting it under an org pinned at 1536 fails closed *and* mismatches every vector already in that tenant's index. A migration path per tenant is required, or a dimension-matched model. This alone probably invalidates the "swap the embedder" framing. |
| **W-8** | **BYOK: the gateway does not own the vectors.** Retrieval is per-org provider + per-org credentials; the corpus lives in the **customer's** Pinecone or Chroma. Chroma embeds **server-side**, so the gateway never even sees those vectors. | **The in-process hot-tier design is architecturally impossible, not merely unbuilt.** Building a local index would require bulk-exporting every tenant's corpus into gateway memory — a **data-residency and BYOK violation**, and for Chroma tenants the vectors are not obtainable at all. |
| **W-9** | **`hnswlib` has no namespace primitive.** The live code's `_tenant_namespace` **fails closed**, and RAG-03 exists specifically because a `None__{collection}` bug once collapsed tenants into a shared namespace. | One shared index ⇒ **cross-tenant ANN results**, reintroducing the exact defect RAG-03 fixed. Per-tenant indices ⇒ N × M memory, and MASTER's "100 k × 384" figure is **per tenant** with no tenant count stated anywhere. The memory budget for this design has never been computed. |
| **W-10** | **Caches the plan ignored already exist**: an org-scoped T2 verdict cache, an org-scoped Bedrock embedding cache (md5-keyed), a circuit breaker, a rate limiter, and Pinecone's server-side reranker. | The cheap wins may already be taken. **Measure the current cache hit rates before funding anything** — the real hot-path levers are the Bedrock grounding embed and the T2 scan, and **both are already cached.** |

**Three findings that matter independently of any optimisation:**

| # | Finding | Action |
|---|---|---|
| **W-1** | **Vector search is not on the chat hot path at all.** `POST /v1/chat/completions` touches **zero** vector stores. The only hot-path embedding is the grounding guard calling Bedrock Titan, and only when an org opts into semantic/hybrid grounding (**default is lexical**). | **This reframes the whole request.** "Faster vector search" is currently a **RAG-path** optimisation, not a chat-latency one. Optimising it will not move the ≤ 12 ms chat SLO, because it is not in that budget. Say so before spending on it. |
| **W-2** | The dead `EmbeddingVault` table **has no org/tenant column and no ANN index** (only a btree on `created_at`). | Latent **cross-tenant class** if that code is ever revived, and a guaranteed seq-scan. Given MASTER D9's explicit cross-tenant prohibition, this table should be **dropped, not left dormant** — dormant code with no tenant key is exactly how the CHG-0111 family of defects happens. |
| **W-3** | `services/vector-retrieval/` is a stub with **no Dockerfile and no dependency manifest**, and the prod compose **clears its profile gate — so it runs always-on in production**, serving one health check, called by nothing. | Delete it or finish it. An always-on unreferenced service is capacity spent on nothing and a stub that could be mistaken for a real backend in a benchmark. |
| **W-4** | **No BLAS/OMP thread pinning anywhere** (`OMP_NUM_THREADS` and friends unset). | Harmless today because there is no local inference. **Becomes a real hazard the moment PG2 or a local embedder lands**: unpinned BLAS spawns one thread per core *per worker*, which on a 16-core host with 16 workers is a thrash. Pin it **as part of** the PG2 change, not after. |

### 9.1 The design tension (unchanged, and still real)

Your ask was *"fully managed ScaNN, autoscaling — but fastest speed, 0 latency style."* **These two goals are in direct tension, and MASTER already measured the tension.** I will not paper over it.

- **In-process index:** `hnswlib`, **0.105 ms p50 / 0.163 ms p99** at recall@10 = 1.00 on 100 k × 384 [M].
- **Any managed/server index:** a network round trip. MASTER measured Qdrant at 3.07 ms including gRPC; pgvector p99 **≥ 13 ms** even on a large instance [V]; a managed service adds its own hop.

**A managed vector service is 10–100× slower than in-process on the hot path.** "Fully managed + autoscaling" and "0 latency" cannot both be satisfied by one tier. The resolution is to stop treating it as one tier:

| Tier | Role | Technology | Latency | Status after R-8 |
|---|---|---|---|---|
| **Hot (hot path)** | Known-attack memory, consulted **only** in the classifier's gray band | **in-process HNSW**, read-only, mmap'd, rebuilt offline and hot-swapped | **0.1–0.3 ms** [M] | **Viable *only* for a first-party attack-signature corpus that the platform itself owns.** It is **not** viable for tenant documents (W-8: BYOK, the vectors are the customer's and sometimes unobtainable; W-9: no namespace primitive, so a shared index is a cross-tenant defect). |
| **Warm (RAG / large corpus)** | Tenant document retrieval, large mutable corpora, filtered multi-tenant search | **the incumbent Pinecone / Chroma (BYOK)**, or managed ScaNN **only for corpora the platform owns** | 3–30 ms — acceptable, **not** on the admit path | Milvus is removed as an option entirely (W-5: cannot query or write). |

**The distinction that was missing and is now decisive:** an in-process index is fine for **platform-owned attack signatures** and impossible for **customer-owned documents**. The first draft conflated them, and the whole "0 latency" design rested on the conflation. Any hot-tier work must be scoped to a first-party signature corpus, with a **stated tenant-count-independent memory budget** — and even then it delivers nothing to the chat SLO unless the gray-band classifier that would consult it exists (which is D3/PG2, not this section).

**Freshness and fan-out, unaddressed in both drafts.** There is no local index, therefore no build step, no invalidation protocol, and no snapshot artefact. On multi-replica GKE that means: a cold-start build per replica, write fan-out to N replicas, and per-replica divergence between rebuilds. None of this is designed. It is a prerequisite, not a detail.

**On Faiss specifically:** Faiss is an in-process **library**, not a server. For the hot tier, MASTER's `hnswlib` choice is measured and better-suited (mmap + hot-swap). Faiss would be a lateral move, not an upgrade, and it is **not** a substitute for the warm tier because it offers no multi-tenant filtering, replication or durability. This part of the request should be redirected rather than implemented literally.

**Embedding — the target is right; it is *not* yet built.** MASTER measured `potion-base-8M` (model2vec static, 256-d, MIT) at **0.177 ms p50 @95 words / 0.328 @190 / 0.635 @390**, versus `all-MiniLM-L6-v2` INT8 ONNX at **10.57 ms** — MiniLM alone is *nearly the entire 12 ms budget*. A hosted embedding API adds ≥ 297 ms RTT [I]. Quality cost is acceptable: potion-8M = **91.96 %** of MiniLM on MTEB, and on near-duplicate detection specifically, AP **90.1 % vs 84.7 %** [I].

**Corrected status:** this is the right answer and **MASTER's measurement stands** — but the model is **absent from the codebase**, and today *every* embedding pays the hosted-RTT figure above. So this is **new implementation work** (add the dependency, ship the model artefact, wire it, pin BLAS threads per W-4), not a configuration change. The first draft called it "already solved," which was the same category error as R-2: reading a target as a shipped state.

**Second correction (W-7): it is not a drop-in even once implemented.** `potion-base-8M` is 256-d; orgs **pin** `embedding_dimension` (commonly 1536) and a mismatch **fails closed**. Introducing it therefore requires either a dimension-matched static model or a **per-tenant re-embedding migration** of every existing vector — because the tenant's stored vectors are in the old dimension and, under BYOK, are in the tenant's own index. That migration is the dominant cost of this item and was never costed.

**MASTER D9's prohibitions are retained and are security-critical:**
- Vector similarity is **positive evidence only** — it may **never** clear a request.
- **Never** reuse the index or embeddings as a cross-tenant semantic cache (documented cross-tenant leakage class).
- No `usearch` i8 quantisation for near-duplicate matching (**lost 14 points of recall** [M]).
- Not a primary detector: 90.84 % of jailbreak communities contain < 9 prompts [R4]; the strong embedding detectors are **classifiers over embeddings, not kNN** — a role PG2-22M already fills better.

| Test | Success | Fail |
|---|---|---|
| T-V1 Hot-tier latency | p99 **≤ 2 ms** including embedding, in-process | Exceeds ⇒ it does not belong on the hot path |
| T-V2 Gray-band-only | Index consulted on **0 requests** in the common allow path | Consulted on every request ⇒ budget breach |
| T-V3 Recall | recall@10 ≥ 0.99 vs exact search on the corpus | Below ⇒ retune; **never** ship quantisation that loses recall |
| T-V4 **Tenant isolation** | Tenant A's content never influences tenant B's verdict; canary-verified | Any leakage ⇒ **stop**, this is a disclosure class |
| T-V5 Positive-evidence-only | No code path lets a vector miss **clear** a request | Any clearing path exists |
| T-V6 Hot-swap | Index rebuild swaps with zero request errors | Errors during swap |
| **T-V7** Baseline honesty *(new)* | Before any optimisation, publish the **measured** current embedding RTT and confirm whether the chat path touches a vector store at all (per W-1) | Optimisation shipped against an assumed baseline |
| **T-V8** Dormant-table disposal *(new)* | The tenant-key-less embedding table is **dropped**, not merely unused (W-2) | Left dormant — a revival would be a cross-tenant defect |
| **T-V9** BLAS pinning *(new)* | Thread limits pinned and asserted **in the same change** that introduces local inference (W-4) | Local inference lands unpinned |
| **T-V10** *(new, R-8)* **Cache-first measurement** | Before any spend: publish measured hit rates for the existing org-scoped T2 verdict cache and Bedrock embedding cache (W-10), and the resulting p50/p99 *with* cache | Optimisation funded without knowing whether the existing caches already capture the win |
| **T-V11** *(new, R-8)* **Corpus ownership** | Any in-process index is provably built **only** from platform-owned signatures; **no tenant document vector is ever loaded into gateway memory** (W-8) | Any tenant corpus in the local index — a BYOK/data-residency violation. **Hard gate.** |
| **T-V12** *(new, R-8)* **Per-tenant memory budget** | A stated, measured memory budget as a function of tenant count, with the namespace strategy named (W-9); shared-index designs **rejected by construction** | A design whose memory is unbounded in tenant count, or a shared index (RAG-03 regression) |
| **T-V13** *(new, R-8)* **Dimension compatibility** | A pinned-dimension org is **either** unaffected **or** migrated, with the migration proven on a fixture tenant before rollout (W-7) | A dimension change lands and a pinned org fails closed in production |

---

## 10. Execution order and gates

MASTER's phases are retained. This plan **inserts** the control-plane track and the fan-out step, and states the cross-track ordering constraints.

| Phase | Content | Gate (fail ⇒ stop) |
|---|---|---|
| **0 — Honesty** (MASTER §8) | Fix `honesty.full_nine_stages`; 0 ms stage renders as *skipped*; **token-emitting** stub; split `T_addon_pre`/`T_addon_post`/`T_t2_ms`; stage `seq` matches runtime; multiprocess Prometheus registry; **measure memory per in-flight stream** (T-S1) | Scans-off run reports `full_nine_stages: false`; a stub run **fails** the capacity predicate |
| **0a — Query deadline + thread cap + tenant scoping** *(new, first, hours not weeks)* | **C-7** (`statement_timeout`), **C-4(b)** (cap the analytics ASGI thread pool — removes the 8× multiplier), **X-1** (fail-closed org scoping), **F-a/F-b** (pause hidden-tab polling, add request timeouts + abort) | **T-C6, T-C8, T-F1, T-F2, T-F5 green.** First because it is trivial, it makes the OOM *observable as an error*, and it closes a cross-tenant defect. **T-C8 is a security gate.** |
| **0b — Control plane: the actual OOM fix** *(revised, R-6/R-7)* | **C-1′ (SQL pushdown — this is the fix)**, **C-8**, **C-9**, **X-2**, then C-3 + C-4(a)/(c) + **F-c/F-d/F-e** | **T-C1 (incl. the out-of-range-period assertion), T-C1b (concurrency burst), T-C3, T-C4, T-C4b, T-C7, T-C9, T-F3, T-F4 green.** **C-1′ replaces the withdrawn C-1** (response caps were the wrong fix); **C-8 must not use `.iterator()`**; **T-C1b is the test that reproduces the incident.** |
| **0c — Control plane headroom** *(new, demoted from 0b)* | **C-2** rollups — now justified on **ingestion headroom and poll latency**, not on OOM | T-C2 rollup-equivalence green. **Schedule on its own merits or not at all** — C-1′ already removes the OOM (§3.1 correction) |
| **1 — Self-inflicted stalls** (MASTER) | RC-1 (detach log publishing, restore INFO), RC-4 (shrink telemetry, ring buffer), `EVALSHA`, bounded pool queueing, negative-cache empty catalogue, `orjson`, `--timeout-keep-alive`, **chat body cap**, gunicorn timeout → 350 s | **`T_addon_pre − T_t2` p50 < 50 ms** (MASTER §11.2's correction — 50 ms *cannot* include Haiku). If it does not fall, **the RC-1/RC-4 diagnosis is wrong and this plan must be revised** |
| **2 — T1 engine + admission** (MASTER) | D10 (Aho-Corasick + Vectorscan + UTS-39 confusables), D6 (local GCRA + one `EVALSHA` + snapshot admit), org-scoped breaker keys, shared T2 breaker | Unique-prompt T1 p50 **< 3 ms**; adversarial corpus **byte-identical** to the Python oracle; RPS/host ≫ 85 |
| **2b — Cache tier** *(new; blocks fail-closed)* | S3 replicated tier, AUTH, TLS — **then** fail-closed | **T-R1, T-R2 green *before* fail-closed is enabled.** Reversing this order causes an outage |
| **3 — Replace the judge** (MASTER + §8, **CPU-only lock**) | PG2-22M INT8 ONNX (sidecar if RSS demands), stateful window aggregation, cache key + `policy_version` + `classifier_digest`, **Haiku removed entirely**, output classifier at `[DONE]` fail-closed. **No GPU quota, no 86M, no Stage-2 GPU pool** (§13.2) | `T_addon_pre` p50 **≤ 12 ms** (hard); **T-G2 recall table published**; T-G3, T-G4, T-G6, T-H1, T-H5, T-H9 green |
| **1b — Redis prefetch** *(revised, R-5; after Phase 1)* | §4.0c: hoist the independent Redis reads into **one pipelined prefetch**. **No stage is reordered and no stage runs concurrently.** | **T-P1 byte-identical (hard, no sign-off path)**; **T-P6 (ordering invariant — hard)**, **T-P10 (single-writer audit — hard)**, **T-P11 (latency parity)**, T-P8, T-P9, T-P5 green |
| **1c — Policy thread-per-regex fix** *(new, P-1; independent)* | Replace thread-per-regex-match with a shared bounded executor or a timeout-capable engine | **T-P12** (thread count O(1) in rule count). Independently schedulable; probably the largest single hot-path defect this review found |
| ~~**3b — Fan-out Wave B**~~ | **Withdrawn (R-5).** CPU-detector fan-out needs both GIL-releasing detectors **and** a refactor of every terminal-writer stage into detect-and-report. Not scheduled — see **O-9**. | — |
| **4 — Fleet, edge, bus, store** *(cost-capped, §13)* | S8 GKE regional on **CPU only**; S1 edge **gated on T-E1**; S3 Memorystore **Valkey HA** then fail-closed; S4 **Cloud SQL HA** (not AlloyDB); S6 = Redis Streams first (no Kafka until measured); **no ClickHouse** until T-CH1 fails; C-5/C-6 as a Cloud SQL read replica only if T-C1b still hurts writes after C-1′ | **T-E1…T-E6, T-K1…T-K3, T-D1…T-D4, T-S2, T-S3 green**; monthly run-rate **≤ $5 k** (T-COST1); then T-S4 at **measured-need × 3**, never a 100 k provision inside this budget |
| ~~**5 — Scale-out / GPU**~~ | **Withdrawn.** GPU is off this plan. Horizontal CPU scale-out is Phase 4, gated by the $5 k ceiling and the 12 ms tax. SLO C remains a procurement quote, never an engineering promise. | — |

### 10.1 Ordering constraints that are not negotiable

1. **HA cache before fail-closed** (§5.3). Reversed ⇒ maintenance becomes outage.
2. **Redis prefetch after Phase 1** (§4.5). Reversed ⇒ the RTT saving is inside the event-loop noise floor and unmeasurable. *(Wave B is withdrawn, so its constraint is moot.)*
3. **C-4 (memory budget) before Stage-1 in-process ONNX** (§6). Reversed ⇒ new OOM class.
4. **T-E1 before committing to the L7 edge** (§5.1). Reversed ⇒ streaming may be broken in production.
5. **Quota preflight before any capacity test** (T-S3). Reversed ⇒ the result is meaningless.
6. **Phase 1 gate before Phase 3** (MASTER §11.7, retained).
7. **C-7 (query deadline) before C-1/C-2/C-8** (§3.2). *(new.)* Reversed ⇒ a mistake in the remediation kills a worker instead of returning an error, and you debug a corpse.
8. **F-a/F-b before any control-plane benchmark** (§3.4). *(new.)* Reversed ⇒ every measurement is taken under self-inflicted client load and is not reproducible.
9. **PG2 fail-closed path proven before Bedrock removal** (T-H5). *(new.)* Reversed ⇒ the one real enforcement guarantee is dropped with no replacement.
10. **BLAS thread pinning in the same change as local inference** (W-4). *(new.)* Reversed ⇒ thread thrash on a multi-worker host.
11. **C-7 + C-4(b) before C-1′** (§3.2). *(new, R-6.)* The thread-pool cap removes the multiplier that makes one burst fatal; without it, a partial C-1′ still dies under concurrency and you misread the result.
12. **Resolve the `BEDROCK_MODEL` ambiguity before any Haiku removal** (§8). *(new, R-5.)* Reversed ⇒ deleting two gateway lines silently switches the control plane and simulator to a different model.
13. **Assert the single-writer audit invariant (T-P10) before any concurrency change to the pipeline** (§4). *(new, R-5.)* This is the test that would have caught the round-two defect; it must exist before, not after.

---

## 11. What this plan will not do

Inherits MASTER §10 in full, and adds:

* Will not claim parity with the outgoing generative judge (§8).
* Will not put a managed vector service on the admit path (§9).
* Will not enable fail-closed on a non-replicated cache (§5.3).
* Will not accept an L7 edge on `/v1` without T-E1 (§5.1).
* Will not quote 100 k without stating **which** SLO (§7).
* Will not implement Faiss as a hot-path upgrade over the measured `hnswlib` choice, nor as a managed-tier substitute (§9).
* Will not use `.iterator()` as a memory bound while server-side cursors are disabled (§3.1 item 6).
* Will not describe a MASTER *proposal* as shipped state — the error corrected in §0.2 (§4.0, §9.0).
* Will not present vector-search work as a chat-latency improvement while the chat path touches no vector store (W-1).
* **Will not run any pipeline stage concurrently with another** (§4.0b). Every pre-scanner stage is a terminal writer; concurrency duplicates the audit trail and violates PIPELINE-0007. The only concurrency claimed is **I/O prefetch**, which reorders nothing.
* **Will not claim the ~40 % detector-segment win** while it is blocked on both the GIL and an unfunded terminal-writer refactor (§4.3).
* **Will not load any tenant document vector into gateway memory** — BYOK and data residency forbid it, and for server-side-embedding tenants the vectors are not obtainable at all (W-8).
* **Will not build a shared in-process index across tenants** — `hnswlib` has no namespace primitive and RAG-03 exists because of exactly that collapse (W-9).
* **Will not treat Milvus as an available backend** — its client cannot query or write (W-5).
* **Will not hard-code the memory-limit sum in CI** — the hand-computed figure in the first draft was wrong (T-C4).
* **Will not sweep a 90-day analytics window as a test** — the period map tops out at 30 d and silently serves 24 h, which made the original gate vacuous (T-C1).
* **Will not delete the vendor-name de-leak scrubbers as part of the Haiku purge**, nor re-broaden their matching (§8, `security_views.py:93-100`).

---

## 12. Open items requiring your decision

| # | Item | Why it blocks |
|---|---|---|
| **O-1** | Confirm **100 k = admitted RPS (SLO G)** | **Resolved 2026-08-25:** 100 k remains SLO G *on paper*. **Do not provision it.** $5 k/mo funds today's traffic + ~3–10× headroom with HA. 12 × 16-vCPU hosts ≈ $3.8 k compute *alone* [MASTER D4] — that plus HA Redis + HA SQL + GKE + LB **does not fit** $5 k. Autoscale inside the ceiling; alarm at 80 % of $5 k (T-COST1). |
| **O-2** | Sign off the kill-switch staleness contract | MASTER §11.4: unsigned, so Phase 2 must use **TTL ≤ 50 ms**, not 2 s. Still required before D6 snapshot-admit. |
| **O-3** | Accept that the markdown-backtick false-positive class gets fixed **deliberately and scored** during the T1 rewrite | It is a live behaviour change, not a silent improvement (MASTER D10) |
| ~~**O-4**~~ | Withdrawn — see O-10 | — |
| **O-5** | GPU / `CPUS` / address quota | **GPU half resolved: do not file GPU quota.** Still file `CPUS` + `IN_USE_ADDRESSES` before any scale test (T-S3). |
| **O-6** | Vector-search speed vs chat path | **Resolved: two different systems.** See §13.6. Customer Pinecone/Chroma stay as BYOK RAG connectors. Haiku is **not** replaced by a vector DB. |
| **O-7** | Control-plane Bedrock model (not Haiku) | Still open — settle with O-11 before any removal |
| **O-8** | Drop tenant-key-less embedding table + `vector-retrieval` stub | Still needs owner sign-off; recommended **yes** (saves the always-on 256 MiB stub) |
| **O-9** | Detector fan-out | **Resolved: off the roadmap** (R-5). Prefetch only. |
| **O-10** | Is `days=365` a real requirement? | Still open; cheapest part of C-1′. Default in this tracker: **clamp to 30 d** unless product says otherwise. |
| **O-11** | What is the platform model? | Still open — blocks Haiku removal (constraint 12) |
| **O-12** | Haiku in the customer model picker | Still open — product changelog |
| **O-13** | Hot-tier vector scope | **Resolved: no platform vector DB.** Optional later: in-process `hnswlib` of **platform-owned** attack signatures only, after PG2-22M exists, gray-band only, positive evidence only (MASTER D9). |

---

## 13. Cost-first lock (2026-08-25) — $5 k/mo, CPU only, ≤ 12 ms gateway tax

> **This section is now the binding overlay on MASTER and on §§1–12.** Where they conflict, this section wins. Cloud migration remains DevOps-owned; we still name **service classes**, not apply Terraform.

### 13.0 What you locked

| Constraint | Value | Consequence |
|---|---|---|
| Monthly infra | **≤ $5 000** for the **full product** (gateway + control + data stores + LB + GKE). Customer BYOK LLM spend is out of envelope. | 100 k admitted RPS is **not** a day-one provision. GPU is unaffordable *and* forbidden. AlloyDB, Kafka, ClickHouse Cloud, Vertex Vector Search are **not** in the starting bill. |
| Gateway tax | **`T_addon_pre` p50 ≤ 12 ms, strictly**, unique prompts, guards ON, token-emitting stub | Anything that adds a network hop on the admit path is rejected (MASTER D1). |
| GPU | **Never.** PG2-22M INT8 on CPU. Drop 86M and all GPU node pools. | MASTER Stage-2 is cancelled, not deferred. Scale-out is more CPU nodes, inside $5 k. |
| Dashboards | May be slower than the gateway. **Must not OOM or 502 in production.** | SQL pushdown + `statement_timeout` + frontend stagger. No new analytics warehouse required to stop the incident. |
| Ops load | Prefer **auto-managed** data stores | Memorystore / Cloud SQL, not self-run Redis/Postgres on GKE. |
| Open source | Allowed anywhere it does not add hops or ops | Vectorscan, ONNX Runtime, PG2, `hnswlib`, ClickHouse **OSS** (only if T-CH1 fails). |

### 13.1 The $5 k arithmetic, honest

MASTER's own numbers [D from V]:

| Item | Cost | Fits $5 k? |
|---|---|---|
| GPU guard pool at 20 k RPS (9 × L40S) | **~$15–16 k/mo** | **No** |
| 100 k admitted RPS compute (12 × 16-vCPU) | **~$3.8 k/mo compute alone** | Compute fits; **compute + HA Redis + HA SQL + GKE + LB + NAT does not** |
| Target-state incremental at *today's* traffic (Valkey + bus + CH, no GPU) | **~$1.4 k/mo** | **Yes** |
| AlloyDB HA vs Cloud SQL HA (2 vCPU analog) | AlloyDB ~**+39 %** over Cloud SQL Enterprise Plus [V] | Waste: chat path opens **zero** Postgres connections |

**Lean HA target (what this plan buys):**

| Line | Choice | Est. $/mo | Notes |
|---|---|---|---|
| Gateway | 1–3 × **16-vCPU ARM CPU** (`c4a-standard-16` if in-region, else cheapest 16-vCPU that passes T-CPU1) | **$400–1 500** | 1 node until 12 ms is proven; 3 AZ only after T-K1 is funded |
| Control / workers | 2–4 vCPU general pool (not E2 for gateway; E2 OK here) | **$150–400** | |
| Postgres | **Cloud SQL PostgreSQL Enterprise, HA**, 2 vCPU / 8–16 GiB | **$250–450** | Chat path unaffected (T-D1) |
| Cache | **Memorystore for Valkey**, 1 shard × (1+1 replica), `standard-small` (6.5 GiB) | **~$210** at $0.1425/node-hour × 2 × 730 [V, us-central1 card; **confirm asia-south1**] | AUTH+TLS+multi-zone. Not 26 GiB BASIC. |
| GKE | Regional Standard cluster | **~$73** control-plane | Autopilot tax is optional later |
| Edge | Global external Application LB + Cloud Armor, timeout ≥ 350 s | **$80–250** | Fallback L4 passthrough if T-E1 fails |
| NAT / addresses | Cloud NAT sized after T-S1 | **$50–150** | |
| ClickHouse / Kafka / GPU / Vertex | **$0** | — | Deferred or forbidden |
| **Sum** | | **~$1.2–3.0 k** | Headroom to $5 k is **scale-out**, not new products |

**T-COST1:** a monthly cost dashboard (or billing export) with an alert at **$4 000**. Fail = any new SKU that is not in the table above, or a run-rate ≥ $5 k.

### 13.2 CPU instance — no GPU

**Pick:** the **cheapest 16-vCPU ARM node that holds `T_addon_pre` p50 ≤ 12 ms** after Phase 3. That is MASTER D4's *reason* (best scan-throughput per dollar, same ARM image as current `c8g`) mapped onto GCP.

| Family | Role | Verdict |
|---|---|---|
| **C4A (`c4a-standard-16`, Axion / ARM)** | Gateway default **if** GCE offers it in `asia-south1` | Closest analog to `c8g.4xlarge`. PG2-22M INT8 + ONNX Runtime + Vectorscan all have aarch64 wheels. **Bake-off required** (T-CPU1). AlloyDB C4A is **not** in asia-south1 [V]; that does **not** decide GCE. |
| **C3 / C3D / N2D** | Fallback if C4A missing in-region | x86. Use only if T-CPU1 fails on C4A or the family is unavailable. |
| **N2** | Control-plane / Celery | Fine. Not the gateway. |
| **E2** | **Forbidden on the gateway** | Shared cores; 12 ms p99 will jitter. Allowed for demo/docs. |
| **g2 / L4 / A100 / H100** | **Forbidden** | Your lock. PG2-86M on CPU misses ≤ 8 ms [MASTER D2]; we are not running 86M. |

**Size:** start **`c4a-standard-16` (16 vCPU / 64 GiB)** or the fallback 16-vCPU SKU — MASTER's 16-real-core unit. Do **not** start at 32 vCPU: RSS × workers is the OOM risk (C-4 / T-G7), not lack of cores.

**Count:** 1 node until 12 ms is green; 3 (one per AZ) only when T-K1 is in budget. **Do not buy 12 nodes.**

| Test | Success | Fail |
|---|---|---|
| **T-CPU1** family bake-off | On the candidate SKU, unique-prompt 9-stage run, PG2-22M ON, stub upstream: **`T_addon_pre` p50 ≤ 12 ms, p99 ≤ 40 ms** | Misses 12 ms ⇒ try the next cheapest family; **do not add GPU** |
| **T-CPU2** $ per 1 M classifies | Measured $/1 M on this SKU ≤ MASTER's CPU figure in spirit (~$1/1 M order) | A cheaper SKU exists that also passes T-CPU1 and was not tried |
| **T-G7** RSS | PG2-22M × worker count fits cgroup; else **one sidecar**, not 16 copies | In-process copies OOM the node |

### 13.3 Redis service — auto-managed, then fail-closed

**Pick: Memorystore for Valkey, multi-zone, replica_count ≥ 1, AUTH on, in-transit TLS on, `standard-small` (6.5 GiB).**

| Option | Why / why not |
|---|---|
| **Memorystore for Valkey HA** | **Chosen.** Fully managed (your ops constraint). Valkey is MASTER D5's engine (io-threads, ~20 % cheaper than Redis OSS). Available in **asia-south1** [V]. Right-size: 6.5 GiB HA, **not** 26 GiB BASIC. |
| Memorystore for Redis Cluster | Later, only if T-R3 cannot land one `EVALSHA` on a single shard at measured RPS. Extra $. |
| Memorystore BASIC (live today) | **Forbidden** for fail-closed (every maintenance = total outage, §5.3). |
| Self-run Valkey on GKE | Cheaper list price, **you** own failover. Rejected by the maintenance constraint. |
| Dragonfly / KeyDB | MASTER D5 rejected; bottleneck is RTT count, not server QPS. |

**Latency is not bought with a bigger node.** Same-AZ pooled GET is sub-millisecond [MASTER §0]. The 31.7 ms p50 is **client-side** (RC-1, full Lua, pool raise). Spend engineering on `EVALSHA` + detach `PUBLISH`, not on `highmem-xlarge`.

**Order:** HA live → T-R2 → **then** fail-closed (§5.3).

### 13.4 OLTP database — Cloud SQL, not AlloyDB

**Pick: Cloud SQL for PostgreSQL, Enterprise edition, HA (regional), 2 vCPU / 8–16 GiB SSD, automated backups.** Optional read replica **only if** T-D2 fails after C-1′.

| Option | Why / why not |
|---|---|
| **Cloud SQL HA** | **Chosen.** Chat path uses **zero** DB connections [MASTER D7, T-D1]. AlloyDB's columnar engine and 39 % compute markup [V] **cannot improve the 12 ms tax**. Cloud SQL is the cheapest auto-managed Postgres that still has HA, PITR, and a read replica SKU. |
| AlloyDB | Previous pick. **Demoted.** Worth it only if, *after* C-1′, dashboard GROUP BY still cannot meet a **dashboard** SLO on Cloud SQL. That is a dashboard problem, not a gateway problem. |
| Postgres on GKE | Cheapest, you own backups/failover. Rejected (ops). |
| Aurora / RDS | AWS instantiation of the same class if the fleet is still on AWS when this lands. |

**Do not put `EnforcementEvent` JSONB on the chat path.** Keep the invariant.

### 13.5 ClickHouse — why not (yet), and when yes

**Verdict: do not add ClickHouse now.**

**Why not:**

1. The production break you hit is **Python materialising millions of rows in a 2560 MB cgroup**, not "Postgres is the wrong query engine." C-1′ + C-7 + frontend stagger **fix that class without a new store**.
2. ClickHouse (managed or OSS) is a **new operational surface** (schema, ingest, completeness metrics). At today's volume (≈ 288 k events / 24 h [PERF-0009]) a `GROUP BY` in Postgres after pushdown is the cheaper fix.
3. MASTER D7's "Postgres cannot be the analytics store" is true **at 100 k events/s (~432 TB/day)**. We are **not provisioning that**. Paying for a warehouse sized for a future we cannot buy inside $5 k is how the bill dies.
4. Kafka + ClickHouse (MASTER D8) is **~$700/mo** in MASTER's lean estimate — real, but **optional**. Redis Streams into Postgres is $0 extra infra after payload shrink.

**When yes (T-CH1 trigger):** after C-1′ + C-7 + F-a/F-c are green, if a 30-day dashboard poll still has p99 **> 5 s** *or* Cloud SQL CPU **> 70 %** under production poll cadence, add **ClickHouse OSS** on a small CPU VM (MASTER's ~$400/mo analog, or cheaper e2/n2). **Not** ClickHouse Cloud until OSS ops is proven worse than the bill. Chat path still must not wait on it (ring + completeness counter).

**Why not BigQuery:** pay-per-scan is cheap *today* and **unbounded** at fleet rate; also another hop for the UI. Rejected as the live dashboard store.

### 13.6 Vector — two different systems (devil's advocate)

You were right: **Pinecone / Chroma / Milvus in this repo are customer RAG connectors, not the Haiku replacement.** MASTER already split them. Conflating them is how the first draft wasted a section.

| System | What it is | On the chat admit path? | What we do |
|---|---|---|---|
| **A — Customer RAG / BYOK** | Org-configured Pinecone or Chroma; tenant owns vectors; Chroma may embed server-side | **No** (`POST /v1/chat/completions` touches **zero** vector stores [R-1/W-1]) | **Keep the connectors.** Do not deploy a platform Milvus/Qdrant/Vertex for tenants. Delete the non-functional Milvus stub and the always-on `vector-retrieval` service (O-8). Faster RAG search is a **tenant-index** problem; it does **not** move the 12 ms tax. |
| **B — Semantic scan that replaces Haiku** | Today: Bedrock Haiku T2 on the **admit path** (1.3–1.8 s). MASTER D3 replacement: **PG2-22M INT8 ONNX classifier**, in-process, every allow-path request | **Yes — this *is* the 12 ms budget** | **A vector database is the wrong tool.** Devil's advocate below. |

**Devil's advocate: "we need a vector DB to replace the LLM check."**

MASTER D9 asked this and answered **no, as a detector**:

- 90.84 % of jailbreak communities have **< 9 prompts**; nearest-neighbour-to-known-attacks is brittle [R4].
- The embedding detectors that *do* score well (e.g. NemoGuard F1 0.960) are **classifiers over embeddings**, not kNN — **the same role PG2-22M already fills, better** [R4].
- Any **server** (Qdrant 3.07 ms RPC, pgvector p99 ≥ 13 ms, Pinecone +62 ms to Singapore, Vertex Vector Search another hop) **eats the 12 ms budget before classification**. D1: no extra hop on the admit path.
- A shared platform index across tenants recreates **RAG-03 / CHG-0111** (no namespace primitive in `hnswlib`; BYOK vectors must never enter gateway RAM [W-8/W-9]).

**What replaces Haiku:** **PG2-22M**, not Faiss, not Milvus, not Vertex. Cost: **$0 infra** (runs on the gateway CPU you already pay for). Latency target: ~5 ms on 16 cores [MASTER D2] — inside 12 ms together with T1 after D10.

**Optional later (not in $5 k day-one, $0 extra SKU):** in-process **`hnswlib` + `potion-base-8M`**, **platform-owned attack-signature corpus only**, consulted **only in the classifier gray band**, **positive evidence only** (never clears a request), mmap'd, hot-swapped. Budget ≈ 0.3–1.0 ms [MASTER D9]. **Do not build this until PG2-22M is in production** (otherwise there is no gray band). **Do not** put tenant documents in it.

**Faiss:** a library, not a service. MASTER measured `hnswlib` better for mmap/hot-swap. Faiss is a lateral move. Not funded.

| Test | Success | Fail |
|---|---|---|
| **T-V-SEM1** | Haiku/Bedrock T2 **unreachable** (T-H1); PG2-22M is the admit-path semantic engine; **zero** vector-DB RPC on `/v1/chat/completions` | Any Qdrant/Milvus/Vertex/Pinecone call on the admit path |
| T-V7 / T-V10 | Cache hit rates published before any embedding spend | Spend without measurement |
| T-V11 | If gray-band index exists: **only** platform signatures in gateway memory | Tenant corpus in-process |

### 13.7 Telemetry bus — Redis Streams first, Kafka later

**Pick:** shrink payload (one content ref, not 25× prompt copies) → **bounded in-process ring** → **Redis Streams** on the Valkey instance (or a dedicated DB index) → control drain into Postgres. **Kafka / Pub/Sub / ClickHouse = $0 until T-BUS1 fails.**

Why: librdkafka is the right *API* (non-blocking `produce`), but MSK/Managed Kafka is **~$300+/mo** for a bus we do not need at 288 k events/day. Redis Streams reuses the store you already pay for. Fail if drain lag or eviction is measured (T-BUS1: p99 drain lag **< 30 s**, `audit_completeness_ratio` ≥ 0.999, no `auth:` eviction).

### 13.8 Load balancer (NLB / ALB class)

Unchanged from §5.1, cost-restated:

| Path | Class | Settings that matter |
|---|---|---|
| `/v1/*` chat | **S1** — Global external **Application LB** (GCP) or NLB + `preserve_client_ip` (AWS) | Backend timeout **≥ 350 s**; `proxy_buffering off`; gzip **off** for SSE; keepalive 512; **T-E1** go/no-go. If T-E1 fails → **L4 passthrough** (SSL at nginx). |
| UI + `/api` | Same L7 or a second HTTPS proxy | Cloud Armor **rate limit**, never body-inspect the token stream |
| Cost | One forwarding rule + one backend service | Do not add API Gateway / CloudFront / Cloud CDN in front of POST `/v1` (30–120 s timeouts, no streaming) |

### 13.9 What we will not buy (cost list)

* GPU node pools, TPU, inf2, g2.
* AlloyDB (until T-CH1-class evidence on Cloud SQL).
* ClickHouse Cloud, BigQuery-as-dashboard, OpenSearch.
* Vertex AI Vector Search / managed Milvus / Qdrant **for the admit path**.
* Kafka / Pub/Sub **before** T-BUS1.
* 12-node gateway fleet, 26 GiB cache, 365-day unbounded scans.

---

## 14. Phase tracker — task by task (success / fail)

**Legend:** **P** = pass gate to start next phase. **S** = security gate (blocks even if latency is green). **$** = cost gate.

Each task: files, steps, success, fail, rollback. Do not mark a task complete without the named test. @skill-verification-before-completion @skill-test-driven-development

### Phase 0 — Honesty (MASTER §8)

| ID | Task | Files / command | Success | Fail |
|---|---|---|---|---|
| **0.1** | Unique-prompt + token-emitting stub in the capacity harness | `scripts/perf/gateway_pipeline_bench.py`; stub must emit tokens | Scans-off run reports `full_nine_stages: false` for skipped stages; stub run **fails** if used as a capacity number | `/health` or non-emitting stub quoted as RPS |
| **0.2** | Split `T_addon_pre` / `T_addon_post` / `T_t2_ms`; 0 ms = skipped | `pipeline_trace` builders | UI Duration matches backend ±0.1 ms (PIPELINE-0018) | Silent 0 ms stages counted as work |
| **0.3** | Measure MB / in-flight stream (T-S1) | loadgen + `docker stats` / cgroup | Number published in this plan's evidence folder | Phase 4 started with this **[NF]** |

**P:** 0.1–0.3 green.

### Phase 0a — Stop the corpse (hours)

| ID | Task | Files | Success | Fail |
|---|---|---|---|---|
| **0a.1** | `statement_timeout` + idle-in-tx timeout on analytics role | `main_app/db_url.py`, Cloud SQL flags, PgBouncer | **T-C6:** pathological 365-d query returns error; worker lives; `dmesg` clean | Worker OOM |
| **0a.2** | Cap ASGI threads on analytics path (C-4b) | `control/server-entrypoint.sh`, `asgi.py` | **T-C1b:** 4 concurrent 30-d KPIs, cgroup **< 70 %** of 2560M | Burst kills worker |
| **0a.3** | X-1 fail-closed org scope | `security_views.py` `_enforcement_events_for_request` | **T-C8 (S):** superuser-no-org → **zero** rows; cross-tenant canary never visible | Any foreign row |
| **0a.4** | Frontend: pause hidden-tab poll; AbortController + timeout on `fetchWithAuth` | `useBackendHealth.js` / firewall data hooks, `AIMeshFirewallOverview.jsx` | **T-F1, T-F2, T-F5** | Hidden tab still polls; hang to 300 s |

**P:** T-C6, T-C8, T-F1, T-F2, T-F5. **S:** T-C8.

### Phase 0b — OOM root fix (SQL pushdown)

| ID | Task | Files | Success | Fail |
|---|---|---|---|---|
| **0b.1** | C-1′: `Count`/`CASE`/`TruncHour`/`GROUP BY` for soc-kpis, attack-vector-trends, module-kpis, module-trends | `policy/security_views.py`; tests `test_attack_vector_trends.py` | **T-C1** in-range 2xx; RSS growth **< 150 MB**; unknown period **400** not silent 24 h | Vacuous 90 d; RSS scales with rows |
| **0b.2** | Clamp `days` (default 30 unless O-10 says 365) | dashboard views | 365 rejected or capped | Unbounded `days` |
| **0b.3** | C-8: no raw `metadata` projection; **no** `.iterator()` as a bound | security + dashboard + analytics views | **T-C7** | Any full-blob haul |
| **0b.4** | C-9 request_id shape; X-2 escalation_distribution | `security_views.py` | **T-C9** non-zero when rows exist | Structural zeros |
| **0b.5** | `--max-requests` jitter (C-3); honest RSS premise (C-4a) | `server-entrypoint.sh` | **T-C3, T-C4b** | Recycling used as OOM fix (it cannot be) |
| **0b.6** | F-c/F-d/F-e: ≤ 2 concurrent heavy calls; dedupe; honest error list | overview + `fetchWithAuth` | **T-F3, T-F4** | 4 concurrent heavies |

**P:** T-C1, T-C1b, T-C3, T-C7, T-C9, T-F3, T-F4.

### Phase 0c — Optional dashboard headroom

| ID | Task | Success | Fail |
|---|---|---|---|
| **0c.1** | C-2 rollups **only if** 30-d GROUP BY p99 > 5 s after 0b | **T-C2** oracle match | Rollups funded without a measured miss |

### Phase 1 — Self-inflicted gateway stalls

| ID | Task | Files | Success | Fail |
|---|---|---|---|---|
| **1.1** | RC-1: stop sync Redis `PUBLISH` from the event loop; logger INFO | `main.py`, `redis_log_handler.py` | **`T_addon_pre − T_t2` p50 < 50 ms** | No drop ⇒ diagnosis wrong, **stop and revise** |
| **1.2** | RC-4: shrink telemetry; bounded ring; one content ref | telemetry builders | Event size << 52 KB; prompt not copied 25× | Still 52 KB on the loop |
| **1.3** | `EVALSHA` + hash tags `{org:}` + bounded pool queue | rate limiter / KS clients | **T-R3, T-R5** | Full Lua on the wire; raise-on-exhaustion |
| **1.4** | `orjson`, chat body cap, gunicorn ≥ 350 s, uvicorn keepalive 90 | gateway entrypoint, nginx | Timeouts aligned; 413 on oversized body | 120 s gunicorn severs streams |
| **1.5** | Negative-cache empty catalogue | `main.py` routing | No per-request rebuild on 422 | Catalogue miss storms |

**P:** Phase-1 latency gate. **Then** 1b prefetch.

### Phase 1b — Redis prefetch (no stage reorder)

| ID | Task | Success | Fail |
|---|---|---|---|
| **1b.1** | Pipeline KS / model_state / org RL reads; gates stay serial | **T-P1, T-P6 (S), T-P10 (S), T-P8, T-P9, T-P11** | Any extra `input_blocked`; scanners see unredacted text |

### Phase 1c — Policy thread-per-regex (P-1)

| ID | Task | Files | Success | Fail |
|---|---|---|---|---|
| **1c.1** | Shared executor or timeout regex; not thread-per-rule | `policy_engine.py` | **T-P12** O(1) threads on 264-rule package | Thread count ∝ rules |

### Phase 2 — T1 engine + admit

| ID | Task | Success | Fail |
|---|---|---|---|
| **2.1** | D10: Aho-Corasick + RapidFuzz + Vectorscan + UTS-39; Python oracle | Unique-prompt T1 p50 **< 3 ms**; byte-identical to Python oracle | Recall drop; markdown-backtick FP changed **unscored** (O-3) |
| **2.2** | D6 local GCRA + one `EVALSHA`; org-scoped breakers | Published overshoot bound; no global `circuit:state:{model}` | Cross-tenant breaker |

**P:** T1 < 3 ms + oracle. **Do not** start PG2 until this is green (MASTER §11.7).

### Phase 2b — Cache HA (blocks fail-closed) **$**

| ID | Task | Success | Fail |
|---|---|---|---|
| **2b.1** | Memorystore Valkey HA, AUTH, TLS, right-size | **T-R1, T-R2**; bill line ≈ table §13.1 | BASIC; fail-closed enabled first (**outage**) |
| **2b.2** | Then fail-closed TPM/KS | **T-R4** | TPM disappears on Redis down |

### Phase 3 — Replace Haiku with PG2-22M (CPU)

| ID | Task | Success | Fail |
|---|---|---|---|
| **3.0** | Settle O-11 / O-7 / O-12 (platform model + picker) | Written decision in changelog | Removal starts without it |
| **3.1** | PG2-22M INT8 ONNX; BLAS pin; sidecar if T-G7 fails | **T-CPU1, T-G7, T-H5 (S), T-H9 (S)** | GPU; 86M; injection fail-open in degraded |
| **3.2** | Window aggregation (Prompt Overflow); UTS-39 before classifier | **T-G3, T-G4, T-G6** | `max()`-only bypass |
| **3.3** | Remove Haiku/Bedrock T2; keep de-leak scrubbers; seed+env+tests | **T-H1, T-H7, T-H8, T-H10**; **T_addon_pre p50 ≤ 12 ms** | Collection-time ImportError; silent model switch; scrubbers deleted |
| **3.4** | Publish recall vs Haiku (no parity claim) | **T-G2, T-H2** | Removed without a table |

**P:** 12 ms **and** T-H5/T-H9. **$:** no new GPU SKU.

### Phase 4 — Fleet inside $5 k (DevOps applies; we specify)

| ID | Task | Success | Fail |
|---|---|---|---|
| **4.1** | GKE regional CPU pools; PDB; grace ≥ 350 s | **T-K1–T-K3** | GPU pool; grace < 350 s severs SSE |
| **4.2** | Edge T-E1…T-E6; Armor rate limit | T-E1 pass **or** documented L4 fallback | L7 buffers tokens |
| **4.3** | Cloud SQL HA; T-D1 zero gateway DB | **T-D1–T-D4** | AlloyDB without T-CH1 evidence |
| **4.4** | Cost cap + autoscale on in-flight, not 12 nodes | **T-COST1**; T-S4 at **need × 3**, not 100 k | Run-rate ≥ $5 k; 100 k quoted from `/health` |

**ClickHouse / Kafka:** only if **T-CH1** / **T-BUS1** fail. Separate change, still ≤ $5 k.

---

## 15. GitHub tracker

Issues in `AnshSinghal-CyberUltron/AI_Mesh_Firewall`:

| Issue | Scope |
|---|---|
| [#93](https://github.com/AnshSinghal-CyberUltron/AI_Mesh_Firewall/issues/93) | Epic — locks + service-class table |
| [#94](https://github.com/AnshSinghal-CyberUltron/AI_Mesh_Firewall/issues/94) | Phase 0a — OOM stop |
| [#95](https://github.com/AnshSinghal-CyberUltron/AI_Mesh_Firewall/issues/95) | Phase 0b — SQL pushdown |
| [#96](https://github.com/AnshSinghal-CyberUltron/AI_Mesh_Firewall/issues/96) | Phase 1 / 1b / 1c — gateway stalls |
| [#97](https://github.com/AnshSinghal-CyberUltron/AI_Mesh_Firewall/issues/97) | Phase 2 / 2b — T1 + Valkey HA |
| [#98](https://github.com/AnshSinghal-CyberUltron/AI_Mesh_Firewall/issues/98) | Phase 3 — Haiku → PG2-22M |
| [#99](https://github.com/AnshSinghal-CyberUltron/AI_Mesh_Firewall/issues/99) | Phase 4 — $5 k fleet spec |

**Do not close an issue until the Success column is evidenced** (test output or live metric), not a green unit file alone.

---

## 16. Remaining product sign-offs (do not block 0a/0b)

| ID | Decision | Default if unsigned |
|---|---|---|
| O-2 | KS staleness 50 ms vs 2 s | **50 ms** (stricter, safer) |
| O-3 | Score markdown-backtick FP during D10 | **Must score**; no silent change |
| O-8 | Delete stubs | **Yes** recommended |
| O-10 | `days=365` | **Clamp 30 d** |
| O-11 / O-7 / O-12 | Platform model + picker | **Block Phase 3** until written |

---

*End of cost-first overlay. MASTER remains canonical for hot-path *reasoning*; this overlay is canonical for **budget, CPU-only, 12 ms, and tracker**.*
| **O-2** | Sign off the kill-switch staleness contract | MASTER §11.4: unsigned, so Phase 2 must use **TTL ≤ 50 ms**, not 2 s. This is a security-control decision, and MASTER explicitly requires product sign-off |
| **O-3** | Accept that the markdown-backtick false-positive class gets fixed **deliberately and scored** during the T1 rewrite | It is a live behaviour change, not a silent improvement (MASTER D10) |
| ~~**O-4**~~ | ~~Confirm C-1's truncation semantics.~~ **Withdrawn** — C-1 is superseded by C-1′ (SQL pushdown), which needs no truncation because the response was already bounded. **Replaced by O-10.** | — |
| **O-5** | Owner + lead time for the GPU, `CPUS` and address quota raises | Lead-time item; late filing stalls Phase 5 |
| **O-6** *(new, R-1)* | **Is vector-search speed still a priority now that the chat path touches no vector store?** (W-1) The work is real but it improves **RAG**, not the ≤ 12 ms chat SLO. | Decides whether §9 is funded at all, or deferred behind §3/§4 |
| **O-7** *(new, R-3)* | The **control plane's** Bedrock model is a *different* default, not Haiku. Remove it too, or leave it? | "Remove Haiku completely" is ambiguous here and must not be resolved silently |
| **O-8** *(new, W-2/W-3)* | Approve **dropping** the tenant-key-less embedding table and **deleting** the always-on `vector-retrieval` stub | Both are dormant liabilities; deletion needs an owner's sign-off |
| **O-9** *(new, R-5)* | **Detector fan-out is withdrawn.** Reinstating it requires refactoring every pre-scanner stage from *detect-and-return* into *detect-and-report* so one writer resolves the verdict — a larger change than the ≈ 40 % segment win may justify. **Fund it, or accept that detector fan-out is off the roadmap?** | Determines whether the largest theoretical latency win stays on the table at all |
| **O-10** *(new, R-6)* | **Is `days=365` a real requirement** on the two dashboard endpoints, or an unintended default? Clamping it is the single cheapest part of C-1′. | Changes the scope of C-1′ and the size of the worst-case query |
| **O-11** *(new, R-5)* | **What *is* the platform model?** The repo carries two contradictory `BEDROCK_MODEL` defaults, `.env` overrides both, and `.env.sample` disagrees with `.env`. Settle it before removal. | Constraint 12 — otherwise Haiku removal silently changes the control plane's model |
| **O-12** *(new, R-5)* | Removing the Haiku option from the model picker **withdraws a customer-visible BYOK capability**. Approve, or keep the picker entry while removing the platform default? | Product decision with a changelog entry, not a cleanup |
| **O-13** *(new, R-8)* | Given W-7/W-8/W-9, **hot-tier vector work can only be scoped to a platform-owned attack-signature corpus** — and it delivers nothing until the gray-band classifier (D3/PG2) exists. **Confirm that narrower scope, or drop §9's hot tier entirely?** | Together with O-6, decides whether §9 survives as anything more than the cache measurement in T-V10 |
