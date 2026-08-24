# Haiku parity, long prompts, Redis/RDS, vectors, MCP/RAG, order, GCP edge

> **Status:** PLAN ONLY. Addendum to `2026-08-22-dual-slo-100k-and-ms-addon.md`. No code until confirmed.
> **Evidence:** `proxy_chat` / `bedrock_scanner.py` / `mcp_scan_orchestrator.py` (2026-08-22 tree), Meta Prompt Guard 2 model card, CDL pack `mcp-parallel/findings/cdl-aimesh-2026-08-21/`, origin `deploy/require-edge-https.inc`.

---

## 1. Haiku-level security without Haiku on the hop

**Haiku today is not a full-document omniscient judge.** It is a JSON classifier with a **10 000 character** budget (`BEDROCK_MAX_PROMPT_CHARS`). Over that, `_head_tail` keeps `budget-800` of the head + last **700** chars and **drops the middle**. T1 already ran on the full string and **blocks length > 10 000**. So the “small model 512-token window misses the middle of a long prompt” problem is **already true of Haiku** for anything past 10k chars.

Haiku is asked for this JSON only (`bedrock_scanner.py`):

`findings[].category ∈ {prompt_injection, jailbreak, data_leakage, goal_hijacking, social_engineering, pii, phi, pci, obfuscation}` + `risk_score` + `recommended_action`.

**Toxicity and tool_overreach are T1, not Haiku fields.** PII/PHI/PCI/obfuscation/data_leakage are **already T1** (`patterns.py`). T2 is skipped if T1 already `block`. Policy `block` skips T2. Default MCP/RAG/embeddings **do not run Haiku at all**.

So “current Haiku security” on a default tenant is:

| Surface | What actually runs today |
|---|---|
| Chat input | T1 **then** Haiku (if `ENABLE_TIER2` and not sampled off) |
| Chat output | T1 chunks + Haiku **fail-open** |
| MCP tools/list/call | T1 floor only (`mcp_tier2_enabled` default **false**) |
| `/v1/embeddings` | T1 only (comment: T2 intentionally not run) |
| RAG ingest / query / retrieved docs | T1; Haiku only if `rag_tier2_enabled` (default **false**) |
| EmbeddingVault (pgvector attack ANN) | **Removed.** Failed open. Comment: “T2 Bedrock covers it.” |

You cannot claim the whole product is Haiku-guarded. Chat is. The rest is already T1.

### 1.1 Replacement stack (same *jobs*, different *engines*)

Do **not** swap one model for one model. Split Haiku’s JSON into layers that already exist or that Meta designed for this:

| Haiku JSON category | Keep on 100k path | Engine | Honest gap |
|---|---|---|---|
| pii, phi, pci, obfuscation, data_leakage | **T1** (byte-verify fail-closed) | Vectorscan rewrite of `patterns.py` | None if G33/G53 stay |
| tool_overreach, toxicity, canned injection | **T1** `ATTACK_PATTERNS` | Same | Weak on paraphrase |
| prompt_injection, jailbreak | **PG2**, not ProtectAI DeBERTa-as-equal | Llama Prompt Guard 2 | See §1.2 |
| goal_hijacking, social_engineering (intent) | **Not** PG2’s job and **not** Llama Guard 3’s job (LG3 = MLCommons S1–S13 harm, not jailbreak) | Sync **slow pool**: Haiku or a second injection model. Fast pool: **accept measured FN** + shadow Haiku as **audit only** | Residual. Async Haiku is **not** fail-closed |
| recommended_action | Map PG2 score + T1 action through existing `resolve_and_enforce` | No second LLM | Lose free-form evidence strings (SOC still has T1 spans + sampler) |
| Output taxonomy | Llama Guard 3 at `[DONE]` | Replaces output Haiku | LG is S1–S13, not PII (T1 still on chunks) |

**ProtectAI DeBERTa v2 is not the Haiku stand-in.** Meta’s Prompt Guard 2 card (Attack Prevention Rate @ 3% utility reduction): PG2-22M **78.4%**, PG2-86M **81.2%**, ProtectAI **22.2%**. Bake-off is allowed; **default pick is PG2**. ProtectAI remains a shadow candidate only.

**PG2-22M vs 86M:** 22M = DeBERTa-xsmall, **19.3 ms / 512 tok on A100**, multilingual AUC **0.942**. 86M = mDeBERTa, **92.4 ms**, multilingual AUC **0.995**. English-heavy 100k pool → TensorRT **22M**. Tenants that need Haiku-like multilingual → **86M or slow-pool Haiku**, not a lie that 22M equals Haiku.

### 1.2 How we know we did not silently drop security

1. **Replay the existing adversarial suites** (`test_adv50_*`, injection/jailbreak fixtures, CISO-100) against T1+PG2. Publish the miss list vs Haiku on the same prompts.
2. **Shadow:** 0.1–1% unique prompts still go to Haiku **off hop**. Alert when Haiku would `block` and PG2 `allow`.
3. **Never sample-skip PG2** on the 100k pool (`GATEWAY_TIER2_SAMPLE_RATE=1` for the sidecar). Today’s sampler already skips Bedrock on T1-clean — that is a hole, not a feature to copy.
4. **Output:** delete fail-open skip. Timeout → withhold in block-mode.
5. **MCP/RAG:** keep T1 as the floor (it is already the real control). Optional PG2 on MCP **only** for orgs that turn `mcp_tier2_enabled` on — same flag, new engine, still not on the 12 ms default.

Haiku remains available on a **slow pool**. That is how you keep “current security” for a tenant that pays for it. The 100k pool is T1 + PG2 + optional LG, with a **written gap list**, not a slogan.

### 1.3 Fail-closed boundary vs Haiku-as-auditor (lock this language)

**Async Haiku is not a control.** If the CISO requirement is “nothing Haiku would block may reach the model,” Haiku must stay **synchronous** on that tenant. Shadow/sample Haiku can only **measure** misses after admit.

This is already how `enforcement_mode=block` behaves in code: `async_post_llm` is **forced sync** (`main.py` ~8302–8311, all branches `force_sync_tier2 = True`). In `monitor` mode, async is worse than “late Haiku”: `tier2_post_scan_task` is a **stub sink** (never calls Bedrock). Do not describe 0.1–1% Haiku as preserving today’s block-mode boundary.

**Llama Guard 3 is not the injection escalator.** It is an output (and optional input) **harm** taxonomy. Using it to catch PG2 jailbreak false negatives is a category error. Escalation for uncertain *injection* is PG2-86M, a second injection classifier, or Haiku — and any Haiku escalate rate at 100k RPS is bounded by live Bedrock ~**8–40 RPS**, not by a confidence diagram.

### 1.4 Defense in depth is real — and does not replace chat-input fail-closed

MCP pre-tool RBAC, RAG retrieved-chunk scan, and output redact are **already in this repo**. They are the right blast-radius story for **agents**. They do **not** un-send a jailbreak or an SSN that already hit the customer’s BYOK LLM on `/v1/chat/completions`.

TrueFoundry’s 3–10 ms hop is **not** a faster Haiku. Copy cheap admit + sync **PII mutate**. Do **not** copy generate+cancel injection or skip output on stream. Do **not** move the SLO from 12 ms to 30–40 ms without saying that is a different product.

---

## 2. 512-token windows and chunking latency

**Fact:** PG2 and ProtectAI DeBERTa are both **512-token** models. Meta’s card: *“For longer inputs, split prompts into segments and scan them in parallel.”* That is the official recipe. Serial chunking on CPU is the failure mode you are correctly afraid of.

**Fact:** our T2 budget is already capped at **10 000 characters** (~2.5k tokens). That is **5–8 windows** of 512 with overlap, not 50.

| Strategy | Latency on a 10k-char prompt | Security |
|---|---|---|
| Serial 8 × 19 ms | ~150 ms + overhead | OK, **kills** 12 ms SLO |
| **Batched** 8 windows, one GPU forward | ~1.2–2× a single 512 classify (typical), target **≤ 25 ms** after TensorRT | Meta-recommended; max score wins |
| Head+tail truncate to 512 | ~19 ms | **Same blind middle as Haiku `_head_tail`**, worse than batched windows |
| llm-guard SENTENCE / 256-char CPU chunks | Hundreds of ms | Do **not** use |
| Llama Guard 3 8k on input when tokens>512 | ~20–80 ms | Whole remaining prompt, heavier; **slow/high-risk path only** |

**Plan:**

1. T1 always on the **full** text (Vectorscan is linear; length > 10k still blocks). Injection that is a regex still dies before the GPU.
2. PG2 sidecar: `windows = sliding(text, 512, overlap=64)`, **one batched infer**, `score = max(window_scores)`. Cap windows (e.g. 16). Over cap → fail-closed in block-mode (pathological paste), not silent truncate.
3. TensorRT/INT8 on L4 so **one** 512-token classify is **≤ 8 ms**; batch of 8 stays in the tens of ms, not hundreds.
4. Do **not** embed-then-classify each chunk (that would add an embedding model). PG2 **is** the classifier; windows are raw text.

This does **add** latency vs a 20-token prompt. It does **not** add Haiku’s 1.5 s. It does **not** require a second embedding model.

Short prompts (the 100k loadgen and most chat turns) are **one** 512 window. Long RAG-stuffed prompts are the batch case; they were never going to be 3 ms anyway, and Haiku was 1.5 s **plus** blind middle.

---

## 3. ElastiCache / Memorystore / RDS — what they fix and what they do not

**RDS/Postgres on the chat hop: no. Never.** Gateway chat does not query Postgres today. EmbeddingVault (pgvector) was the only in-process PG path and it was **removed** because it failed open. Putting RDS back on admit adds 1–5 ms **best case** and a hard dependency; it cannot create 100k RPS.

**ElastiCache/Memorystore: capacity ≠ hop latency.** Shared Redis today **is** the 6-stage stub wall (~1.2k RPS vs ~11k `/health`) because we do **6–9 RTTs per chat**, not because the node is 1 GiB vs 26 GiB. A bigger box does not make MULTI faster. **Local GCRA + RAM cache** is the latency fix. Redis stays for **leases** (TPM/budget so N pods don’t multiply quota), **kill-switch source of truth**, **auth miss path**.

**Where size *does* matter (your Memorystore 1 GiB BASIC finding):**

Live AWS: `cache.r7g.xlarge` ~26 GiB, Redis 7.1. Terraform default `cache.r7g.large`. Compose prod: `--maxmemory 768mb allkeys-lru`. Policy: `volatile-lru` on ElastiCache.

Hot Redis data that must **not** LRU-evict:

| Key | Size class | Eviction symptom |
|---|---|---|
| `auth:apikey:{sha256}` | small / key | 503 auth (Redis down already 503s) |
| `kill_switch:{org}:…` | tiny | fail-closed 503 or **missed disable** if you ever fail-open |
| `policies:compiled:{org}` | can be **MB/org** | 503 policy-unloaded or HTTP to control |
| `llm:model_configs*` | medium | 422 catalog |
| `ratelimit:*` | small, high churn | OK on 1 GiB |
| `telemetry:events` LIST | **unbounded** if drain ~250/s | **This is what eats 1 GiB** and evicts auth/policy |

**GCP Memorystore 1 GiB BASIC, AUTH off, transit encryption off:**

- **Capacity:** will evict under load if telemetry lists and compiled policies share the instance. **Do not cut over C or even prod-like traffic onto 1 GiB.** Match **r7g.xlarge-class** (Memorystore STANDARD_HA, **≥ 16–26 GiB**, replicas). Split **telemetry** onto Kafka; do not grow Redis LISTs.
- **AUTH/TLS off:** worse than AWS ElastiCache’s encryption-off posture only because it is also tiny. Turn **AUTH + in-transit TLS** on before names go public. Same for AWS eventually.
- **BASIC** = no replica. A node blip fail-closes every KS/auth check. Use HA.

RDS stays **control plane** (users, policies source, MCP catalog, EnforcementEvent). Chat hops never wait on it. Kafka → ClickHouse for 100k audit, not `LIST` → PG drain.

---

## 4. Vector search for security — do we need an embedding model?

**We already tried this.** `EmbeddingVault` was a pgvector store of attack patterns between T1 and T2. It is **gone** (`main.py` ~5733): disabled by default, **failed open** on credential/config, “T2 Bedrock covers it.”

Vector ANN answers: “is this prompt **near a known attack embedding**?”  
Haiku answers: “does this **novel paraphrase** look like jailbreak?”  

Those are different. ANN **does not replace Haiku**. It is a **T1.6 signature in vector space** — useful for clusters you have labeled, blind to attacks not in the index.

**If we bring it back (optional, not the 12 ms default):**

| Piece | Put where | Latency |
|---|---|---|
| Embedding model (e.g. all-MiniLM-L6 384d) | **Same GPU sidecar as PG2**, never gunicorn, never OpenAI embeddings API | CPU ~5–15 ms; GPU ~2–5 ms |
| Index | **FAISS/HNSW in sidecar RAM** (or Redis vector), **not** RDS pgvector on the hop | < 1 ms |
| When to run | Only if T1 clean **and** PG2 score in a gray band | Zero on the common path |

**Do not:** Bedrock Titan / OpenAI embed on admit (second billed RTT). Do not pgvector round-trip. Do not run ANN on every request if PG2 already ran.

Vectorscan in the T1 rewrite is **Hyperscan-class regex SIMD**, not embedding search. Different word, different layer. We need that for PII/secrets. We do **not** need ANN to hit 100k or 12 ms.

---

## 5. MCP, vector DB, RAG — other security scans

**Do not put Haiku or PG2 on every MCP/RAG/embed call by default.** That is how you miss both SLOs on those surfaces. The code already chose T1:

| Path | Today | 100k / ms plan |
|---|---|---|
| MCP result/args/tools.list | `mcp_scan_orchestrator` T1 + floors (PII, secrets, infra, exfil, injection patterns, depth/node caps). T2 Bedrock **opt-in** | Keep T1. Optional PG2 behind existing `mcp_tier2_enabled`. **Never** Haiku on the MCP hot path |
| Chat → MCP internal tools | `_scan_tool_result_floor` T1 | Unchanged |
| Embeddings | T1 redact/fail-closed, no T2 | Unchanged. Batch T1; no per-item GPU |
| RAG ingest | ContextGuard + T1; T2 default off | Unchanged; ingest is **not** the 100k chat SLO |
| RAG query | T1; T2 default off | Unchanged |
| Retrieved chunks before the model | T1; T2 default off | T1 must still **redact before** `model_input` (PII in a Pinecone hit). PG2 on retrieved text is optional/slow |
| Vector provider catalog | Redis `vector:providers:compiled` | RAM snapshot, same as policies |

MCP already has a **separate** 1.4 hardening chain (sandbox, SSRF, body caps, field RBAC). That does not move into the chat PG2 sidecar. Do not “unify” them into one GPU call — tool JSON is not a 512-token prompt.

---

## 6. Microservices? Complete architecture change?

**No to a 15-service rewrite.** Yes to **three fleets** that already match how the hop is failing:

| Keep as one process | Split out |
|---|---|
| FastAPI gateway: auth cache, GCRA, policy RAM, T1, KS check, routing, httpx to model | GPU **guard sidecar** (PG2/LG) |
| Control Django | GPU **inference** (vLLM) |
| nginx + NLB | **Kafka/CH** for audit |

Splitting `auth-svc`, `policy-svc`, `scan-svc` over HTTP **adds** hops and makes 12 ms impossible. TrueFoundry’s fast path is **in-process** JWT/RBAC/limit, logs async. Copy that.

Architecture change is **real** (Haiku off hop, Redis off the 6-RTT path, inference not BYOK-as-100k, NLB preserve_client_ip). It is **not** a greenfield mesh of microservices.

---

## 7. Kill-switch and routing **before** input_scan?

### Kill switch: already first

Runtime in `proxy_chat` (not the dashboard stage list):

**auth → KS (Redis) → model_state Redis → rate_limit → policy → input_scan T1/T2 → routing (uses `scan_verdict`) → LiteLLM → output guard**

`PIPELINE_STAGE_NAMES` prints KS **after** input_scan. That is a **trace lie**. Code at ~L7195 runs KS **before** scan.

On `action=disable` (or Redis down, fail-closed): **503 immediately**. Policy, T1, Haiku, routing, LiteLLM **do not run**. You already save T2 cost and latency when the model is killed.

On `reroute`: request **continues** through scan then routing. Correct: the **prompt** is still hostile; the fallback model must not see raw PII.

Scan-only probes skip KS on purpose.

### Routing before scan: do not

Routing calls `_extract_chat_routing_preferences(..., scan_verdict)` and `_risk_from_verdict` so a **malicious** verdict raises `request_risk_score` and steers off cheap models (PIPELINE-0031). Scan-before-route is the product.

If you route first:

- You pick a model **without** threat signal → cheap model on a jailbreak, or you scan anyway so you saved **nothing**.
- You still **must** T1-redact before **any** `model_input`.
- KS disable already skipped the expensive scan. The only extra skip would be “all candidates dead after routing” — rare; KS global/model already 503s first.

**Cost intuition:** Haiku is ~$ and ~1.5 s. KS is a Redis GET. Reordering routing does not remove Haiku unless you skip scan, which is unsafe. After PG2 is ~8–25 ms, skipping it to “save cost” is the wrong optimization; GPU classify is cheaper than a wrong route to GPT-4o.

**Keep:** KS first (already). Policy before T2 (already; policy block skips Bedrock). **Scan then route.** Fix the **trace order** so the UI matches runtime.

---

## 8. GCP / CDL edge — do not regress, do not undersize

GCP LB/Memorystore/SSL policy are **not in this repo** (AWS terraform + origin nginx only). Treat the following as **cutover gates**. Origin nginx already does the right HTTP method split.

### 8.1 Cleartext POST must stay 403, not 301

Origin (`deploy/nginx.conf` + `require-edge-https.inc`): GET/HEAD → **301** HTTPS; POST/PUT/PATCH/DELETE/OPTIONS → **403** (not 308: that would replay the body). CDL finding 11: EIP POST **403 nginx**; ALB hostname POST **301 `awselb/2.0`** (residual).

Google HTTP(S) load balancer **URL map that 301s everything to HTTPS** will look like the ALB residual and **fail the CDL HTTP-POST control** after names point at GCP.

**Required:** HTTP proxy/url-map:

- GET/HEAD → 301 HTTPS.
- POST/PUT/PATCH/DELETE/OPTIONS → **403** **before** the redirect (empty POST must not become **411** either — reject method first).
- Do not rely on origin nginx for this if clients hit the Google frontend first.

### 8.2 Memorystore 1 GiB BASIC

Not equivalent to prod `cache.r7g.xlarge`. See §3. **Resize + HA + AUTH/TLS** before public cutover. Confirm `INFO memory` + no `evicted_keys` growth under a policy-sync + telemetry soak. Split telemetry off Redis.

### 8.3 Backend timeout 30 s vs ALB 60 s+

GCP HTTPS backend default **30 s** will **kill SSE** earlier than AWS. NLB idle in terraform is **350 s**; nginx `/v1/` `proxy_read_timeout` **360 s**; ALB control idle default **4000 s**. Raise GCP backend service timeout to **≥ 60 s** as a floor; for chat/SSE match **≥ 350 s** or you will drop long generations. Gunicorn is 120 s today — align the lowest hop.

### 8.4 TLS policy

`mesh-firewall-https-proxy` with **no SSL policy** ≠ AWS `ELBSecurityPolicy-TLS13-1-2-Res-PQ-2025-09` (repo terraform still has `ELBSecurityPolicy-TLS13-1-2-2021-06` on NLB/ALB). Attach a **GCP SSL policy**: TLS 1.2 min, modern/restricted profile, disable TLS 1.0/1.1 (CDL #14 already proves origin rejects TLS1.0).

### 8.5 VPC firewall world-open

AWS SG in terraform: NLB/ALB **443** only; app **8300 from NLB SG**. Compose prod still publishes **`0.0.0.0:8300:8300`**. User finding: GCP VPC allows **22, 8300, 3389, 8100, 8180** from `0.0.0.0/0`; gateway bound `0.0.0.0:8300`.

**Before names go public:**

- **22 / 3389:** operator IPs only (match AWS SSH allowlist).
- **8300:** NLB/health-check / scrape ranges only, **not** 0.0.0.0/0. Public chat stays **443**.
- **8100 / 8180:** not public (compose prod already loopback for 8100).
- Prefer gateway listen **127.0.0.1:8300** behind nginx, not `0.0.0.0`.

None of this creates 100k RPS. All of it is **required** so a GCP cutover does not undo CDL.

---

## 9. Decision map (what we will not do)

| Idea | Verdict |
|---|---|
| One DeBERTa replaces Haiku | **No.** Stack in §1 |
| Serial chunking long prompts | **No.** Batched windows |
| Embedding model on every chat | **No.** Optional gray-band ANN only |
| RDS/pgvector on hop | **No.** |
| Bigger Redis instead of fewer RTTs | **No** for latency; **yes** for eviction (GCP 1 GiB) |
| Microservices for auth/policy/scan | **No.** |
| Routing before input_scan | **No.** |
| KS before scan | **Already done.** |
| Haiku on default MCP/RAG | **Already off.** Don’t turn on for 100k |
| GCP HTTP 301 for POST | **Forbidden** (CDL) |
| Memorystore 1 GiB as prod Redis | **Forbidden** |
| Generate+cancel to hide 512-window limits | **Forbidden** in block-mode |
