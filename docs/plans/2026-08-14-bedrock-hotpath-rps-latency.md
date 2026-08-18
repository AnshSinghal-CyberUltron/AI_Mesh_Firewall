# Bedrock hot-path latency + RPS Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **Status:** EXECUTING 2026-08-14 — subagent-driven TDD in this session. Do **not** commit unless the user asks.

**Goal:** Minimum unique-prompt latency on the 9-stage chat pipeline, and the maximum number of those pipelines **production** can run in parallel, with live Bedrock on every allowed request.

**Architecture:** Policy BLOCK and Tier-1 BLOCK both skip input Tier-2. Otherwise every allow-path request runs **separate** Bedrock calls for input T2, routing (if the org enabled routing), and output guard (if the org enabled T2) — never fused into one prompt when platform settings differ. Native **async** Bedrock (no thread-pool ceiling) so tens of thousands of in-flight requests wait on sockets, not OS threads. Pools and workers are sized from **the machine’s detected CPU / RAM / `ulimit -n`**, never from hardcoded 10 / 256 / 1000. User `FirewallConfig` flags are the highest priority.

**Tech Stack:** FastAPI/gunicorn `UvicornWorker`, async Bedrock Runtime Converse (`aiobotocore` or equivalent), cgroup detector `shared/ai_mesh_shared/resource_budget.py`, Redis ConfigSync, `pipeline_trace`.

---

## Environments (locked 2026-08-14)

| Role | Where | What we do there |
|---|---|---|
| **Dev / test** | This GCP workspace (`AI_Mesh_Firewall` on the current host) | Full CPU/RAM for TDD, unit tests, local docker benches. **Not** the SLA source of truth. |
| **Production** | EC2 `AIMeshFirewall` → `ec2-35-154-124-71.ap-south-1.compute.amazonaws.com` (`ec2-user`, key `ai-mesh-key.pem`). App dir `/home/ec2-user/AI_Mesh_Firewall`. Deploy via ECR + `scripts/sync-to-ec2.sh` / `make sync-ec2-deploy`. | **Official latency, RPS, reliability.** Do not treat the GCP box as prod. Do not lift prod cgroup limits until prod is measured. Size prod to **that** instance’s CPU/RAM/fds. |

Acceptance for any task: unit tests on **dev**; live unique-prompt proof on **prod** before calling the task done for customers.

---

## Locked product decisions (2026-08-14 user)

| # | Decision | Implication |
|---|---|---|
| 1 | Policy block **and** Tier-1 block **both skip input T2** | Keep `scanner.py` T1 `action=="block"` skip (~2027). Policy already 403s before the scanner. |
| 2 | Ignore Bedrock **cost**. Optimize latency and parallelism. | `GATEWAY_TIER2_CACHE_TTL_SECONDS=0`. Do not drop `GATEWAY_TIER2_SAMPLE_RATE` below 1.0. |
| 3 | Min unique-prompt 9-stage latency + **max parallel 9-stage pipelines** on this server | Full hardware: all cores, almost all RAM the **gateway cgroup** is given, no idle pin of 4–6 workers. |
| 4 | Enterprise: **≥20k customers start the 9-stage pipeline in the same second** without interruption | Target **≥20,000 in-flight** chat pipelines on this host. 100k RPS remains the fleet/HTTP aspiration; this box’s job is max honest in-flight × 1/latency. |
| 5 | **No artificial code-level cap** on concurrency, async, or connection pools | Delete hardcoded 10 / 16 / 32 / 256 / 1000 ceilings. Size to hardware (`nproc`, cgroup memory, `RLIMIT_NOFILE`). A true `inf` pool OOMs the box and **causes** interruption — that is forbidden. The cap is the **machine**, not a programmer constant. |
| 6 | Do **not** merge T2 + routing into one Bedrock call if platform settings differ | `tier2_enabled`, `routing_enabled`, `tier2_execution_mode`, routing weights, scanner model vs adjudicator model are **peak priority**. Overlap in **time** (`asyncio.gather`) is allowed only after T1 redact and join-before-org-LLM. A faster/smaller **adjudicator** model is allowed as a **separate** call. |

---

## Remaining implementation choices (user: whatever achieves the goal)

1. **Dev compose** may use full GCP capacity for tests. **Prod compose** stays sized to the EC2 cgroup until we measure that box.
2. Native async Bedrock: **yes** (required for 20k in-flight on prod).
3. Faster adjudicator model, T2 scanner unchanged: **yes if it cuts `converse_ms` on prod**.

---

## Code-level caps to remove (inventory)

These are **programmers’ limits**, not the kernel. They serialize ~40 RPS while CPU sits at 1%.

| Cap | Where | Today | Change |
|---|---|---|---|
| New boto3 client every adjudicator call | `llm_router.py:2356` `default_bedrock_client()` | New TLS/session per request | Per-worker singleton |
| boto3 `max_pool_connections` unset | `bedrock_client.py:160` | **Default 10** | Size to `min(nofile/workers, expected_inflight/workers)` — **no 10** |
| boto3 adaptive retries | same | Couples T2 throttle to routing | `standard`, attempts=2 |
| `_bedrock_executor` **max_value=256** | `scanner.py:1070` | Hard clamp 1–256 | Remove max clamp; size from detector/nofile |
| Detector `bedrock_pool = asgi_threads` | `resource_budget.py:305` | `asgi_threads` clamped **8–32** | Bedrock is network-bound: pool (or async conn target) = **inflight budget**, not `cpu*2` |
| Detector `scanner_pool` cap 16 | `resource_budget.py:304` | CPU T1 | Keep CPU-bound clamp (T1 is CPU). Do not confuse with Bedrock. |
| Detector `asgi_threads` 8–32 | `resource_budget.py:294` | Starves `to_thread` | Irrelevant once Bedrock is native async |
| `GUNICORN_WORKER_CONNECTIONS=1000` | `gateway/entrypoint.sh:61`, compose | 16×1000 if it applied | Raise to **nofile-derived** (e.g. 20000+). Confirm UvicornWorker actually honors it; if not, set uvicorn `limit_concurrency=None` (already default) + `backlog` + `ulimit -n`. |
| `WEB_CONCURRENCY` pin **6** (dev) / **4** (prod) | `docker-compose.yml:236`, `docker-compose.prod.yml:174` | Leaves cores idle | Unset on this host → detector `workers ≈ nproc` (16) |
| Prod gateway **cpus: 4, memory: 7GiB** | `docker-compose.prod.yml:215-219` | Cgroup lies to the detector | On this 16c/60GiB bench: **no CPU/mem limit** (or limit = host minus reserved for PG/Redis/control). Prod EC2 keeps its real size. |
| Prod T2 cache TTL 300, cache max 3000 | `docker-compose.prod.yml:185-186` | Skips Bedrock | TTL **0** |
| `GATEWAY_REDIS_MAX_CONNECTIONS` 100 / detector 64–256 | compose + `resource_budget.py:311` | Fine for Redis; not the chat bottleneck | Raise only if Redis wait shows in traces |
| Org burst 150 / RPM 1000 | Redis org config | **~17 RPS/tenant** | Capacity-profile for the 20k test; do not ship as silent prod default unless product agrees |
| Audit inflight 64/256 | `mcp_proxy.py` | MCP only | Out of scope for chat 9-stage |

**Hardware-derived ceilings (allowed):** `RLIMIT_NOFILE`, cgroup memory, ephemeral ports, AWS Bedrock TPS. These are measured at boot and logged. They are not “code limits.”

---

## 20k same-second honesty

Little’s Law: `in_flight ≈ RPS × latency`.

| If unique-prompt 9-stage p50 is | 20k in-flight ⇒ RPS | 100k RPS ⇒ in-flight |
|---|---|---|
| 3.5 s (today, routing-dominated) | ~5,700 | 350,000 |
| 1.2 s (T2∥routing overlap + faster adj, Haiku still ~1s) | ~16,700 | 120,000 |
| 0.4 s (fast adj + connection reuse + overlap) | ~50,000 | 40,000 |

**This one 16-core / ~60 GiB VM:**

- `/health` already did **9.2k RPS** — HTTP accept path is not 20k-RPS-ready without keepalive + more hosts.
- **20k in-flight chats** is the right local target (your “same second, no interruption”).
- RAM: 20k requests × ~1–2 MiB buffers ≈ **20–40 GiB**. The box also runs control, Postgres, Redis, frontend. Gateway cgroup must be given **most** of remaining RAM; other services keep a reserved floor so the stack does not OOM.
- FDs: 20k inbound + 20k outbound Bedrock ≈ **40k+** fds. Boot: `ulimit -n` ≥ 65536 in the gateway container.
- **Threads cannot do this.** `ThreadPoolExecutor(256) × 16 workers = 4096` blocked syscalls. Native async HTTP to Bedrock is mandatory for 20k.
- AWS must accept ~20k concurrent Converse (or queue without resetting). That is **quota**, not Python. If Bedrock 429s, we fail **closed or retry with jitter** without a hardcoded “max 10 connections” that idle the CPUs.
- 100k completed RPS of 9-stage chat still needs a **fleet** (and provisioned throughput). This host’s acceptance test is: **≥20k in-flight unique-prompt pipelines with 0 code-level 503 from our semaphores/pools**, p99 not collapsing to connect-timeout.

---

## User settings vs merge vs faster routing

Platform fields (must win):

- `FirewallConfig.tier2_enabled` (tri-state), `tier2_execution_mode`, `tier2_stream_hold_*`
- `FirewallConfig.routing_enabled` + per-request routing weights / `allowed_models`
- `BEDROCK_TIER2_SCANNER_MODEL` vs `BEDROCK_ADJUDICATOR_MODEL` (already separate in `platform_models.py`)

**Never one fused Converse** when any of those differ (including “routing off, T2 on”).  
**Wall-clock overlap** (`asyncio.gather(input_t2, adjudicate)`) only when **both** are enabled for this request, T1 has already redacted, and **neither** result is applied to `body["model"]` / SSE / `acompletion` until **both** have joined. If T2 terminal-blocks, discard the routing result. If routing is disabled, do not call the adjudicator at all (saves the 3.5s).

**Faster adjudicator (separate call):** keep T2 on Haiku 4.5 (detection quality). Point `BEDROCK_ADJUDICATOR_MODEL` at a smaller JSON model (Nova Micro or Haiku with `BEDROCK_ADJUDICATOR_MAX_TOKENS=64` + shorter system + Converse prompt cache). Measure `converse_ms` A/B. Do not silently change T2’s model.

---

## Full-capacity sizing (this host)

1. Unset `WEB_CONCURRENCY` so `gateway/entrypoint.sh` uses the detector (`workers ≈ 16` on this VM).
2. Remove or raise gateway deploy CPU/memory limits on the **bench compose** so cgroup CPU/RAM = host minus reserved (PG/Redis/control).
3. `GUNICORN_WORKER_CONNECTIONS` and boto/async pool = `RLIMIT_NOFILE / workers` (logged at boot).
4. `GATEWAY_TIER2_CACHE_TTL_SECONDS=0`.
5. Native async Bedrock: one session per worker, connection pool = inflight budget, **no** `max_value=256`.
6. `somaxconn`, docker `ulimits nproc/nofile`, `ip_forward=1` preflight.
7. Org RPM lifted **only** under a named capacity profile for the 20k test.

Co-tenant honesty: “no unused RAM on the server” does **not** mean the gateway process `memory.max=60GiB` while Postgres is on the same box. It means **no 7GiB cgroup and 10 idle cores** while the gateway is the bottleneck.

---

## Honesty: why 8s and 40 RPS, and what 100k would take

Evidence: `mcp-parallel/findings/gateway-pipeline-bench-2026-08-14/verdict.json` (2026-08-14). Stub LLM so BYOK inference ≈ 0.

| Fact | Number |
|---|---|
| Gateway-local stages (auth, rate limit, policy, kill-switch, stub LLM) | **~6 ms** p50 |
| `model_routing` (Bedrock adjudicator) | **~3,474 ms** p50 — **~99% of addon** |
| Warm input_scan / output_guard (T2 **cache hit**) | 2.9 ms / 0.5 ms |
| Cold T2 (unique / uncached) | **~1.1 s in + ~1.1 s out** |
| Addon p50 / mean / p99 (c=1) | **3.5 s / 4.1 s / 8.1 s** |
| Full-pipeline peak | **39.6 ok RPS** at 256 in-flight, CPU **~1.6%** |
| HTTP `/health` peak (apachebench) | **9,182 RPS** |
| Prod org burst / RPM if not lifted | **150/s burst, 1000/min ≈ 17 RPS/tenant** |

**8s (p99) and 3.5s (p50) are not “the VM is slow.”** The box is idle. The request **waits on AWS Bedrock Converse** (Haiku CRIS in `ap-south-1`). That wait is **product code**: `ROUTING_ADJUDICATOR_ALWAYS=true` (default) plus sequential T2.

**39.6 RPS is not 16 cores.** Little’s Law: `RPS ≈ in-flight / latency`. 256 / 3.5s ≈ 73 theoretical; measured 40 because of Bedrock TPS, default boto3 pool of **10** connections, and a **new `BedrockClient()` per adjudicator call** (`llm_router.py:2356–2358`). `/health` on the same process did **9k RPS**.

**Making Bedrock “async” without waiting** would violate your rule (every request must actually run those three inferences). `asyncio.gather` of input T2 + routing **does not** cut the 3.5s if routing is the long leg: wall ≈ `max(1.1, 3.5) + 1.1 = 4.6s` cold, still **3.5s** when T2 is cached. Reviewers also rejected naive gather: routing uses `scan_verdict` risk and today sends **raw** `body["messages"]` to Bedrock (`llm_router.py:2258–2265`). Starting routing before T2/redact can leak PII and pick the cheap model for a T2-malicious prompt. **Phase A does not gather.** Phase B may overlap only after T1 redact and a join-before-LLM contract.

**100k RPS math (must stay in the plan):**

| Target | What it means | Hosts / AWS |
|---|---|---|
| 100k `/health` | No Bedrock | **~11×** this gateway (9.2k/host), plus keepalive + `reuse_port` + not docker-proxy |
| 100k chat with **today’s** 3.5s adjudicator | `100k × 3.5s ≈ 350,000` concurrent Converse calls | **~2,500** gateways at 40 RPS **and** Bedrock quotas that do not exist on-demand |
| 100k chat if each of 3 Converse calls is **200 ms** serial | 600 ms/request → 100k needs **60,000** in-flight | Still a **fleet + provisioned Bedrock** problem, not one VM |
| Tenant RPM 1000 | Hard cap **~17 RPS/org** in prod | Config, not gunicorn |

**What we can actually deliver in code on this host:** (1) prove whether 3.5s is TLS/client-init vs Haiku tokens; (2) if client-init/pool, drop routing toward **hundreds of ms**; (3) if Haiku tokens, shrink the adjudicator prompt / enable Converse prompt cache on the **system** prompt; (4) unique-prompt T2 will be ~1.1s each, not 3ms; (5) fix 422s and host egress; (6) a **scale-out runbook** for 100k HTTP.

---

## Current request order (do not silently reorder)

`proxy_chat` (`gateway/ai_mesh_gateway/main.py` ~6288):

1. Auth, rate limit, kill-switch, keywords  
2. **Policy** — `action=="block"` + `enforcement_mode=="block"` → **403 before `INPUT_SCANNER`** (~8122). **This already matches “no input T2 on policy block.”**  
3. Input T1 then T2 (`scan_prompt_with_tier2`, `scanner.py:1990`). T1 block returns before T2 (`:2027`).  
4. Connected path: **routing adjudicator** Bedrock (`llm_router.py:2356`) → BYOK `acompletion` → **output guard** T2 (`OUTPUT_GUARD.inspect` → `scan_output_with_tier2`).  
5. Output T2 **cannot** start before model text exists (PIPELINE-0013).

Bedrock is **sync boto3** off threads (not native async HTTP). T2 uses `_bedrock_executor`. Adjudicator uses `asyncio.to_thread` (default pool) and **`default_bedrock_client()` every call**.

Host trap: `net.ipv4.ip_forward=0` made each Converse **~47s connect timeout** (~140s for three calls). Preflight **must** check forwarding before any latency claim.

---

## Devil’s advocate (reviewers 1–5) — accepted into the plan

| Challenge | Plan response |
|---|---|
| Warm 2.9ms is cache, not a missed Bedrock call | True. Unique-prompt benches required. Cache off is a **product** choice, not a “bugfix.” |
| Singleton boto3 will not remove Haiku RTT | True. Instrument `client_init_ms` vs `converse_ms` **before** claiming a win. |
| Gather T2+routing can leak / start LLM too early | **Phase A: no gather.** Join T2 before `body["model"]`, SSE, or `acompletion`. |
| 422 at 256 inflight is ConfigSync, not Bedrock | Dedicated phase: last-good catalog, no per-request Redis client, no `routing=[]` clobber. |
| Adaptive retries + pool 10 serialize 16 in-flight/worker | Raise `max_pool_connections`; consider `retries.mode=standard`; cap in-flight Bedrock per worker. |
| In-memory T2 breaker is per worker; adjudicator bypasses it | Gate adjudicator on the same breaker (or shared Redis); mutate breaker only on the event loop. |
| Overlap breaks `total_latency_ms` = sum(stages) | Wall clock stays `total_latency_ms`; overlapped stages must not double-count (PIPELINE-0015/0018). |
| Stream ≠ non-stream; scan-only still pays T2 today | Explicit tasks. `async_post_llm` under block is already forced sync. |
| docker-cp + recreate loses patches | Bake image or bind-mount; HUP all 16 workers. |
| Forcing T2 after T1 block fights PIPELINE-0005 tests | Keep T1-block skip unless you override decision 1. |

---

## Target SLAs (after this work)

**Latency (unique prompt, BYOK excluded from addon; T2 cache off):**

| Stage | Today (typical) | Target |
|---|---|---|
| Input T2 | ~1.1 s cold / ~3 ms cached | Live every allow request; **measure** connect vs tokens; overlap with routing when both enabled |
| Routing | ~3.5 s | **< 500 ms p50** after reuse + faster adjudicator model; else publish irreducible model floor |
| Output T2 | ~1.1 s cold | After org LLM; cannot overlap with model_output |
| Gateway-local | ~6 ms | Stay **< 15 ms** p50 at c=1 |
| 9-stage wall (allow, both flags on) | ~3.5–5.7 s serial | **≈ max(T2_in, routing) + T2_out** after overlap (output still after BYOK) |

**Parallelism / RPS:**

| Surface | Today | Target |
|---|---|---|
| In-flight 9-stage this host | 256 then 422s | **≥ 20,000** without our pool/semaphore 503s |
| Full 9-stage RPS this host | ~40 | `in_flight / p50_latency` (honest). If p50=1.2s and inflight=20k → **~16k RPS** if Bedrock keeps up |
| `/health` this VM | ~9.2k | Raise with keepalive; **100k HTTP** still multi-host |
| Org RPM | 1000/min | Named **capacity profile** for the 20k test |

---

## Phase 0 — Preflight + unique-prompt baseline (no product behavior change)

**Files:** `scripts/perf/gateway_pipeline_bench.py`, `scripts/perf/gateway_pipeline_sweep.py`, `scripts/perf/bedrock_hotpath_preflight.sh` (new)

### Task 0.1: Host preflight

Script must fail the bench if any of: `ip_forward!=1`, gateway cannot TCP 443 to `bedrock-runtime.<region>.amazonaws.com` in **< 20 ms**, `GATEWAY_LOADTEST_STUB_LLM` not recorded, workers != expected.

### Task 0.2: Unique-prompt SLA

Each chat body includes a nonce (`ping <uuid>`). `N=16` serial + inflight 8/32/128. Record per-stage p50/mean/p99 and `errors_by_signature`.

**Pass:** input_scan p50 **not** ~3ms (proves cache vs live). Routing still has `x-amzn-request-id` once Phase 1 logs it.

### Task 0.3: Split Bedrock timing (observability first)

**Files:** Modify `gateway/ai_mesh_gateway/bedrock_client.py`, `llm_router.py`, `scanner.py`, `pipeline_trace.py`, `frontend/src/utils/liveGateway.js` (fallback `routing_ms` → `model_routing_ms`)

Add nested `model_routing.bedrock`: `client_init_ms`, `thread_wait_ms`, `connect_ms`, `converse_ms`, `parse_ms`, `tokens_in/out`, `cache_read_input_tokens`, `ran_inference`, `x_amzn_request_id`, `gateway_request_id=zs-*`.

Prometheus: `amf_gateway_bedrock_call_seconds{call_site,phase}` with `call_site∈{adjudicator,tier2_scan,output_guard}`.

**Tests:** `gateway/ai_mesh_gateway/tests/test_bedrock_phase_timing.py` — fake converse, assert phases sum to routing span ±1ms.

**Gate:** One live unique-prompt chat: logs + trace + UI Duration agree ±0.1ms on `total_latency_ms` (PIPELINE-0018). **Do not optimize until this exists** — otherwise we cannot tell TLS from Haiku.

---

## Phase A — Connection reuse (biggest safe latency lever)

**Files:** `gateway/ai_mesh_gateway/bedrock_client.py`, `llm_router.py`, `bedrock_scanner.py`, `llm_judge.py`, `main.py` (lifespan), `scanner.py`

### Task A.1: Per-worker singleton `BedrockClient`

- Create **after gunicorn fork** (lifespan, same as `INPUT_SCANNER` ~5720). Never in master.
- `BotoConfig(max_pool_connections=hardware-derived)` — **not** 10 and **not** a hardcoded 50. See Task 11.
- `default_bedrock_client()` becomes “return singleton” (tests can inject).
- Adjudicator **must not** `BedrockClient()` per request.

**Test:** two `converse` calls share `id(client._client)`; no second `__init__` log.

### Task A.2: Stop using the default `asyncio.to_thread` pool

**Superseded for 20k in-flight by Task 12 (`aconverse`).** Until Task 12 lands, route adjudicator off the default executor. After Task 12, delete Bedrock `run_in_executor` on the hot path.

### Task A.3: Retries

Change adaptive → **`standard`** with `max_attempts=2` unless a live 429 storm requires adaptive. Document: adaptive token-bucket on a shared client couples T2 throttle to routing.

### Task A.4: Enable Converse **system** prompt cache for adjudicator

Adjudicator system text is large and **stable**. Set `enable_prompt_cache=True` (already supported if `len(system_text)>=4096`; pad or keep system ≥4k). User/preview still unique.

**Test:** first call `cacheWriteInputTokens>0` (or skip if account/model lacks cache); later unique user texts still `ran_inference=true`.

### Task A.5: Re-bench unique prompts

Compare `client_init_ms` (should ≈0 after warmup) and `converse_ms`.

**Success:** routing p50 drops **or** we publish “irreducible Haiku floor = converse_ms ≈ X”. Either result is a win (honesty).

---

## Phase B — Overlap in time, never fused prompts

Implement as Task 10. User settings (`tier2_enabled`, `routing_enabled`) win. Overlap only when **both** would have run anyway.

**Contract (non-negotiable):**

1. T1 + policy redact first. Adjudicator `request_preview` = **post-redact** text only.  
2. May start T2 and routing in parallel **only** using T1 risk as a *provisional* score.  
3. **Join T2 before** any of: write `body["model"]` used for inference, `_launch_chat_stream_response`, `acompletion`, TPM reserve.  
4. If T2 `is_terminal_block`: drop routing result (thread may finish; do not cancel boto3 — just ignore). **No SSE bytes.**  
5. If T2 raises risk vs T1 provisional: **re-adjudicate or discard** cheap-model choice (PIPELINE-0031). Simplest: **do not use the parallel routing result when T2 risk differs** — sequential fallback.  
6. Stream: T2 complete (or hold) before first token. Do not inherit `async_post_llm` under block.  
7. `total_latency_ms` = wall clock, not sum of overlapped stages.

**Default recommendation:** skip Phase B until Phase A proves overlap would save **>200ms** p50. Reviewers: overlap saves **0ms** on cached T2 + 3.5s routing.

---

## Phase C — “Bedrock every request” vs cache

**Files:** `scanner.py` (`_tier2_cache`), compose/env

### Task C.1: Prod default TTL=0

`GATEWAY_TIER2_CACHE_TTL_SECONDS=0` in `docker-compose.yml` / prod compose. Keep code path for opt-in warm cache in non-prod.

**Tests:** identical prompt twice → two Bedrock `tier2_scan` calls (`ran_inference` / no `tier2_cache_hit`). Existing cache tests gated on TTL>0.

**Do not** set `GATEWAY_TIER2_SAMPLE_RATE<1` to recover RPS.

### Task C.2: Keep T1-block skip T2 (unless decision 1)

Do not delete `scanner.py:2027` without a new test that documents the extra cost.

### Task C.3: Scan-only

Today `max_tokens=0` still runs input T2 then skips LLM. Decide: **keep T2 on scan-only** (your “every request” rule) or skip T2 on scan-only (cheaper burst). **Default: keep T2.** Document burst UI will not be “3ms.”

---

## Phase D — Availability bugs that looked like “load”

### Task D.1: Last-good model catalog

**Files:** `config_sync.py` `_reload_llm_models`, `main.py` empty-catalog `reload_models_now`

- Redis GET fail/timeout → **keep** `_model_routing_by_org[org]` (do not write `[]`).  
- Models-only payload **must not** set `routing=[]`.  
- `get_model_routing` returns a **copy**.  
- Coalesce **one** reload per org per worker (singleflight); reuse `REDIS_CLIENT` (no `from_url` per chat).  
- `reload_models_now(org)` **merges** that org into LiteLLM; never replace the whole router with one tenant.

**Tests:** fakeredis timeout → still 200 with last-good models; 256-inflight unique-prompt bench → **0** `no_provider_configured`.

### Task D.2: T2 breaker vs adjudicator

If T2 breaker OPEN + strict → 503. Adjudicator must **not** keep calling Bedrock during that outage (same allow() or shared Redis breaker). HALF_OPEN: **one probe per process**, lock on event loop.

### Task D.3: Inference CB before Bedrock (optional)

Today KS/inference CB OPEN is checked **after** T2+adjudicator. **Task:** check isolated/killed model **before** adjudicator (isolation reroute already skips adjudicator; inference CB OPEN should too).

---

## Phase E — HTTP 100k (devops + server, not Bedrock)

This is the only path that can approach **100k RPS** on a fleet.

1. Uvicorn `timeout_keep_alive`; clients must actually reuse connections (ab reported Keep-Alive **0**).  
2. `SO_REUSEPORT` / multiple sockets; avoid docker-proxy hairpin for benches (host network or `network_mode`).  
3. Nginx `worker_connections` >> 1024 if public vhost is in front.  
4. `/health` skip Redis `LLEN` or cache 50ms (tiny).  
5. Horizontal: **N ≈ 100000 / measured_health_rps_per_host** ALB targets.  
6. Persist `ip_forward=1` on the VM (`/etc/sysctl.d/99-docker-ip-forward.conf` already written in the bench).  
7. Prod compose today is **4 workers / 4 CPU** — smaller than this 16-worker bench; pin capacity in `docker-compose.prod.yml` honestly.  
8. Bedrock **provisioned throughput** / quota ticket — without it, chat RPS cannot scale with hosts (all hosts share one AWS account TPS).

---

## Phase F — Org rate limits

Prod `burst_limit=150`, `requests_per_minute=1000` cap a tenant at **~17 RPS**. Load-test lift must not ship as the customer default.

**Task:** a named **capacity profile** (Redis/org flag) for soak tests; restore after. Customer SLA stays burst/RPM unless they buy a higher tier.

---

## Tests and live gates (mandatory before “done”)

Unit (gateway venv):

```text
gateway/.venv/bin/python -m pytest ai_mesh_gateway/tests/test_bedrock_phase_timing.py \
  ai_mesh_gateway/tests/test_bedrock_client_singleton.py \
  ai_mesh_gateway/tests/test_config_sync_last_good.py \
  ai_mesh_gateway/tests/test_pipeline_block_shortcircuit.py \
  ai_mesh_gateway/tests/test_bedrock_routing.py -q
```

Live (unique prompts, `ip_forward=1`, stub LLM for addon, then one real BYOK sample):

1. c=1 N=16 unique — stage table + Bedrock `ran_inference` on all three sites (or skip input T2 only on policy 403).  
2. inflight 128 — **0** `no_provider_configured`.  
3. inflight 256 — 422 rate **= 0** (D.1) or documented residual.  
4. Policy block fixture — **zero** input T2 Bedrock logs.  
5. PIPELINE-0005: T1/policy block still **no** org LLM.  
6. Playwright Duration vs `total_latency_ms` ±0.1ms.  
7. Compare CPU: still not the bottleneck unless T1 regex dominates unique large prompts.

Do **not** mark complete on cached `"ping"` or `/health` alone.

---

## Non-goals

- 100k full-pipeline RPS on **one** 16-core Python gateway.  
- Returning HTTP 200 before T2/routing/output Bedrock finish.  
- `async_post_llm` under **block** (already forced sync; under monitor the Celery consumer is a stub — implement or leave as non-goal).  
- Skipping output T2 or running it before the model.  
- Dropping `ROUTING_ADJUDICATOR_ALWAYS` to fake latency (PIPELINE-0031).  
- MCP/RAG T2 rule changes.  
- Treating docker-cp as production deploy.

---

## Suggested implementation order

0 (preflight + unique baseline) → 1 (phase timings) → 2–3 (singleton **then** native async Bedrock, delete 256/10/1000 caps) → 5 (last-good catalog / 422) → 6 (TTL=0) → 7 (compose: unset worker pin, lift cgroup, ulimit, worker-connections) → 9 (faster adjudicator model, T2 model unchanged) → 8 (20k in-flight unique-prompt gate) → overlap gather **only** when both org flags on (Task 10).

**Owner files:** `bedrock_client.py`, `llm_router.py`, `scanner.py`, `config_sync.py`, `main.py`, `pipeline_trace.py`, `resource_budget.py`, `gateway/entrypoint.sh`, `docker-compose.yml` / `docker-compose.prod.yml` (bench vs prod cgroup), `scripts/perf/*`.

---

### Task 11: Delete artificial pool/thread/worker caps (TDD)

**Files:**
- Modify: `gateway/ai_mesh_gateway/scanner.py` (`_env_int` `max_value=256` on `GATEWAY_BEDROCK_THREAD_POOL_SIZE`)
- Modify: `shared/ai_mesh_shared/resource_budget.py` (`bedrock_pool` must not equal `asgi_threads` 8–32; derive from `RLIMIT_NOFILE` / workers)
- Modify: `gateway/entrypoint.sh` (`--worker-connections` from env or nofile)
- Modify: `gateway/ai_mesh_gateway/bedrock_client.py` (`max_pool_connections`)
- Test: `gateway/ai_mesh_gateway/tests/test_config.py` (stop asserting a 256 clamp if present)
- Test: `shared` resource_budget tests — `bedrock_pool` on 16c is **≫ 32**

**Step 1: Failing test**

```python
def test_bedrock_pool_not_clamped_to_256(monkeypatch):
    monkeypatch.setenv("GATEWAY_BEDROCK_THREAD_POOL_SIZE", "4096")
    # constructing InputScanner must honor 4096, not 256
```

```python
def test_detector_bedrock_pool_tracks_inflight_not_asgi_threads():
    # 16 CPU, 32GiB → bedrock_pool per worker in the thousands, not 32
```

**Step 3:** Remove `max_value=256`. Detector: `bedrock_pool = max(asgi_threads, nofile_per_worker)` with **no 32 cap**. Log the chosen numbers at boot. If `nofile` is 1024, **raise ulimit in compose** rather than shrinking the pool.

### Task 12: Native async Bedrock (required for 20k in-flight)

**Files:**
- Create: `gateway/ai_mesh_gateway/bedrock_async.py` (or extend `bedrock_client.py` with `async def aconverse`)
- Modify: `scanner.py` `scan_prompt_with_tier2` / output T2 to `await aconverse` (no `run_in_executor` for Bedrock)
- Modify: `llm_router.py` adjudicator to `await aconverse`
- Test: `gateway/ai_mesh_gateway/tests/test_bedrock_aconverse.py`

**Why:** 20k concurrent `ThreadPoolExecutor` tasks = 20k OS threads = RAM death. Async wait on sockets.

**Step 1:** Test that 500 concurrent `aconverse` mocks complete without creating 500 threads.

**Step 3:** `aiobotocore` session per worker (create after fork). Connection pool = hardware-derived. Same Converse API, same `ran_inference` / phase timings. Sync `converse` remains for tests/health.

**Fail closed** on import/session errors (do not silently skip T2).

### Task 13: Full-VM compose for this host + 20k FD budget

**Files:** `docker-compose.yml` (bench), optionally a `docker-compose.bench.yml` overlay so **prod** `cpus: "4"` is not silently destroyed.

- Unset `WEB_CONCURRENCY` (detector).
- `ulimits: nofile: 65535` (or 1048576 if the host allows).
- `GUNICORN_WORKER_CONNECTIONS` ≥ 20000.
- `GATEWAY_TIER2_CACHE_TTL_SECONDS=0`.
- No gateway `deploy.resources.limits` on the bench overlay.
- `sysctl net.core.somaxconn` / `ip_forward=1` in preflight.

**Verify:** `docker exec gateway python -m ai_mesh_shared.resource_budget --json` shows `workers` ≈ host cores; `cat /proc/1/limits` nofile ≥ 65535; `nproc` inside cgroup ≈ 16.

### Task 14: 20k in-flight unique-prompt gate

**Files:** `scripts/perf/gateway_pipeline_bench.py` — `--inflight 20000 --unique-prompts --duration 30`

**Pass:**

- Preflight green.
- In-flight reaches **≥20k** (or the host FD/RAM ceiling, **logged**, not a code semaphore).
- **0** `no_provider_configured` from ConfigSync.
- **0** errors whose reason is our `Semaphore`, `max_pool_connections=10`, or `max_workers=256`.
- Policy/T1 block fixtures: **0** input T2 Bedrock calls.
- Org with `routing_enabled=false`: **0** adjudicator calls, T2 still runs.
- Org with `tier2_enabled=false`: **0** T2 calls, routing still runs if enabled.
- Unique-prompt p50/p99 published; if Bedrock 429s, counts go in `errors_by_signature` (AWS), not hidden.

---

## Bite-sized TDD tasks (implement in this order)

Skills: @skill-test-driven-development @skill-systematic-debugging @skill-defense-in-depth @skill-verification-before-completion @production-live-verification

Do **not** start these until the three product decisions at the top are confirmed. Each task: failing test → minimal code → pass → commit (only if the user asked to commit).

### Task 1: Nested Bedrock phase timings on `converse`

**Files:**
- Modify: `gateway/ai_mesh_gateway/bedrock_client.py` (`BedrockClient.__init__`, `converse`)
- Create: `gateway/ai_mesh_gateway/tests/test_bedrock_phase_timing.py`

**Step 1: Write the failing test**

```python
from unittest.mock import MagicMock, patch
from ai_mesh_gateway.bedrock_client import BedrockClient

def test_converse_returns_phase_timings_and_request_id():
    client = BedrockClient.__new__(BedrockClient)
    client.region = "ap-south-1"
    client.model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    client.timeout = 60.0
    raw = {
        "output": {"message": {"content": [{"text": '{"ok":true}'}]}},
        "usage": {"inputTokens": 10, "outputTokens": 4},
        "ResponseMetadata": {"HTTPHeaders": {"x-amzn-request-id": "amzn-abc"}},
    }
    client._client = MagicMock()
    client._client.converse.return_value = raw
    out = BedrockClient.converse(
        client,
        model=client.model_id,
        system_text="sys",
        user_text="user",
        call_site="adjudicator",
        request_id="zs-test-1",
    )
    assert out["ran_inference"] is True
    assert out["tokens_out"] == 4
    assert out["x_amzn_request_id"] == "amzn-abc"
    assert "client_init_ms" in out["phases"]
    assert "converse_ms" in out["phases"]
    assert out["phases"]["converse_ms"] >= 0
```

**Step 2: Run test to verify it fails**

```bash
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_bedrock_phase_timing.py::test_converse_returns_phase_timings_and_request_id -v
```

Expected: FAIL (`phases` / `ran_inference` KeyError).

**Step 3: Minimal implementation**

In `converse`, record `t0` before `self._client.converse`, `t1` after. Return extra keys:

```python
"ran_inference": True,
"x_amzn_request_id": ((response.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}).get("x-amzn-request-id", ""),
"phases": {
    "client_init_ms": 0.0,  # 0 when using an existing client
    "converse_ms": (t1 - t0) * 1000.0,
    "parse_ms": parse_elapsed_ms,
},
"gateway_request_id": reqid,
```

Log one JSON line keyed by `zs-*` + `call_site` + `x-amzn-request-id` + `tokens_out`.

**Step 4: Run test — PASS.** Do not commit unless asked.

---

### Task 2: Per-worker singleton + pool size

**Files:**
- Modify: `gateway/ai_mesh_gateway/bedrock_client.py` (`default_bedrock_client`, `__init__`)
- Create: `gateway/ai_mesh_gateway/tests/test_bedrock_client_singleton.py`

**Step 1: Failing test**

```python
from unittest.mock import MagicMock, patch
from ai_mesh_gateway import bedrock_client as bc

def test_default_bedrock_client_returns_same_instance(monkeypatch):
    bc.reset_bedrock_client_for_tests()
    fake = MagicMock()
    with patch.object(bc, "boto3") as boto3:
        boto3.client.return_value = fake
        a = bc.default_bedrock_client()
        b = bc.default_bedrock_client()
    assert a is b
    assert boto3.client.call_count == 1

def test_boto_config_sets_pool_and_standard_retries(monkeypatch):
    bc.reset_bedrock_client_for_tests()
    captured = {}
    def _client(name, config=None, **kw):
        captured["config"] = config
        return MagicMock()
    with patch.object(bc, "boto3") as boto3:
        boto3.client.side_effect = _client
        monkeypatch.setenv("GATEWAY_BEDROCK_MAX_POOL_CONNECTIONS", "64")
        bc.default_bedrock_client()
    cfg = captured["config"]
    assert cfg.max_pool_connections == 64
    assert cfg.retries["mode"] == "standard"
```

**Step 2:** FAIL (`reset_bedrock_client_for_tests` missing; each call constructs a new client).

**Step 3: Minimal implementation**

```python
_WORKER_CLIENT: Optional[BedrockClient] = None

def default_bedrock_client() -> BedrockClient:
    global _WORKER_CLIENT
    if _WORKER_CLIENT is None:
        pool = int(os.getenv("GATEWAY_BEDROCK_MAX_POOL_CONNECTIONS", "0") or 0)
        if pool <= 0:
            threads = int(os.getenv("GATEWAY_BEDROCK_THREAD_POOL_SIZE", "16") or 16)
            pool = max(threads + 16, 50)
        _WORKER_CLIENT = BedrockClient(...)  # pass pool + retries mode standard
    return _WORKER_CLIENT

def reset_bedrock_client_for_tests() -> None:
    global _WORKER_CLIENT
    _WORKER_CLIENT = None
```

`BotoConfig(..., max_pool_connections=pool, retries={"max_attempts": 2, "mode": "standard"})`.

Call `reset_bedrock_client_for_tests()` from existing tests that assumed a fresh client if they break.

**Step 4:** PASS both tests.

---

### Task 3: Adjudicator uses singleton + Bedrock executor

**Files:**
- Modify: `gateway/ai_mesh_gateway/llm_router.py` ~2356–2367
- Modify: `gateway/ai_mesh_gateway/tests/test_bedrock_routing.py`

**Current bug:** every adjudicator call does `default_bedrock_client()` which today is `BedrockClient()` (new TLS/session) then `asyncio.to_thread` (default executor, not `_bedrock_executor`).

**Step 1: Failing test** (extend `test_bedrock_routing.py`)

```python
@pytest.mark.asyncio
async def test_adjudicator_reuses_client_and_bedrock_executor(monkeypatch):
    seen = {"executor": None, "client_ids": []}
    class _Client:
        def converse(self, **kw):
            seen["client_ids"].append(id(self))
            return {
                "raw": {"choices": [{"message": {"content": json.dumps({
                    "selected_model": "cheap",
                    "reason": "ok",
                    "policy_summary": "ok",
                    "decision_factors": ["x"],
                })}}]},
                "tokens_in": 1, "tokens_out": 1, "elapsed_s": 0.01,
                "ran_inference": True, "phases": {"converse_ms": 10},
            }
    client = _Client()
    monkeypatch.setattr(
        "ai_mesh_gateway.bedrock_client.default_bedrock_client",
        lambda: client,
    )
    real_to_thread = asyncio.to_thread
    async def _spy_to_thread(fn, *a, **k):
        return await real_to_thread(fn, *a, **k)
    # After the fix, adjudicator should use loop.run_in_executor(bedrock_exec, ...)
    # Assert the executor object identity matches INPUT_SCANNER._bedrock_executor.
```

Simpler lock: assert `default_bedrock_client` is called and `BedrockClient.__init__` is **not**.

**Step 3: Implementation in `adjudicate_model_selection`**

```python
from ai_mesh_gateway.bedrock_client import default_bedrock_client
bedrock_client = default_bedrock_client()
loop = asyncio.get_running_loop()
executor = getattr(INPUT_SCANNER, "_bedrock_executor", None)  # inject via arg to avoid circular import
result = await loop.run_in_executor(
    executor,  # None → default; tests patch INPUT_SCANNER
    functools.partial(
        bedrock_client.converse,
        model=adjudicator_bedrock_model,
        system_text=adjudicator_system,
        user_text=adjudicator_user,
        max_tokens=adjudicator_max_tokens,
        temperature=0.0,
        call_site="adjudicator",
        request_id=request_id,  # thread zs-* through
        enable_prompt_cache=True,
    ),
)
```

Avoid importing `main.INPUT_SCANNER` from `llm_router` if that is circular: pass `bedrock_executor` into `adjudicate_model_selection` from `proxy_chat` (already has `INPUT_SCANNER`).

**Also:** `request_preview` must use **post-redact** messages. Add a parameter `request_messages` that `proxy_chat` fills from the masked prompt, not raw `body["messages"]`.

**Step 4:** existing `test_bedrock_routing.py` still green + new test green.

---

### Task 4: Thread `zs-*` into pipeline_trace `model_routing`

**Files:**
- Modify: `gateway/ai_mesh_gateway/pipeline_trace.py` (`STAGE_TRANSPARENCY_KEYS` / routing stage)
- Modify: `gateway/ai_mesh_gateway/main.py` (where routing stage is finalized)
- Test: `gateway/ai_mesh_gateway/tests/test_pipeline_stage_transparency.py` (extend)

Store `bedrock.phases`, `ran_inference`, `x_amzn_request_id` on the `model_routing` stage. Frontend already falls back `routing_ms` → `model_routing_ms`; do not break PIPELINE-0018: `total_latency_ms` remains wall clock.

**Pass:** one unit test with a fake stage dict; live unique-prompt later in Task 8.

---

### Task 5: Last-good catalog + copy + no per-request Redis client

**Files:**
- Modify: `gateway/ai_mesh_gateway/config_sync.py` (`get_model_routing`, `reload_models_now`, `_reload_llm_models`)
- Create: `gateway/ai_mesh_gateway/tests/test_config_sync_last_good.py`
- Existing: `gateway/ai_mesh_gateway/tests/test_config_sync_validation.py` must stay green

**Root cause of 422 `no_provider_configured` at 256 in-flight:** each of 16 workers has its own `ConfigSync`. `proxy_chat` (~6904) on empty catalog calls `reload_models_now`, which opens a **fresh** `Redis.from_url` with 3s timeout, then `_reload_llm_models(org_slug)` may write `routing=[]` (`normalized.get("routing", [])` at line 617) and `LLM_ROUTER.reload_models(all_models)` **replaces the whole LiteLLM list with one org**. Under Redis blips, a worker that never primed last-good returns `[]` → 422.

**Step 1: Failing tests**

```python
def test_get_model_routing_returns_copy():
    sync = ConfigSync("redis://unused", {})
    sync._model_routing_by_org["acme"] = [{"model_name": "m"}]
    got = sync.get_model_routing("acme")
    got.clear()
    assert sync.get_model_routing("acme") == [{"model_name": "m"}]

@pytest.mark.asyncio
async def test_reload_timeout_keeps_last_good(monkeypatch):
    sync = ConfigSync("redis://unused", {})
    sync._model_routing_by_org["acme"] = [{"model_name": "kept"}]
    async def boom(*a, **k):
        raise TimeoutError("redis")
    monkeypatch.setattr("redis.asyncio.Redis.from_url", boom)
    await sync.reload_models_now(org_slug="acme")
    assert sync.get_model_routing("acme") == [{"model_name": "kept"}]

@pytest.mark.asyncio
async def test_empty_routing_payload_does_not_clobber(fake_redis, monkeypatch):
    # Redis has models:[] / routing:[] for acme — must keep last-good routing
    ...
    assert sync.get_model_routing("acme") == [{"model_name": "kept"}]

@pytest.mark.asyncio
async def test_org_reload_merges_not_replaces_router(fake_redis, monkeypatch):
    # reload_models_now("acme") must not call LLM_ROUTER.reload_models with ONLY acme
    # when other orgs are already loaded — merge or skip full replace.
    ...
```

**Step 3: Implementation**

1. `get_model_routing`: `return list(self._model_routing_by_org[...])` (shallow copy of the list; copy dicts if callers mutate).
2. `reload_models_now`: reuse a long-lived client (`self._redis` or `REDIS_CLIENT`), **do not** `from_url` per chat. Singleflight per `(org_slug,)` with `asyncio.Lock`.
3. If GET fails/times out: log, **return**, do not write `[]`.
4. If `normalized["routing"]` is empty and last-good exists: **keep last-good**.
5. `LLM_ROUTER.reload_models`: merge this org’s deployments into the existing list (filter previous `_zs_org==slug`, extend), never replace the world with one tenant.

**Step 4:** new tests + `test_config_sync_validation.py` PASS.

---

### Task 6: Prod T2 cache TTL=0 (only if decision 2 = yes)

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.prod.yml` (set `GATEWAY_TIER2_CACHE_TTL_SECONDS=0`)
- Modify: `gateway/ai_mesh_gateway/tests/test_config.py` if it asserts default 300 from **env unset** — keep code default 300 for unit tests; **compose** sets 0
- Test: extend scanner tests: with TTL=0, two identical prompts → two executor submissions

Do **not** change `GATEWAY_TIER2_SAMPLE_RATE`. Do **not** delete `scanner.py` T1-block skip unless decision 1 says so.

---

### Task 7: Host preflight script

**Files:**
- Create: `scripts/perf/bedrock_hotpath_preflight.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
fwd=$(sysctl -n net.ipv4.ip_forward)
test "$fwd" = "1"
# TCP 443 to bedrock-runtime.$BEDROCK_REGION.amazonaws.com from gateway namespace
docker exec "$(docker ps -qf name=gateway)" python -c "..."  # connect_ex < 20ms
# Print GATEWAY_LOADTEST_STUB_LLM, WEB_CONCURRENCY, TIER2 cache TTL
```

Exit 1 if any check fails. Bench scripts must call this first.

---

### Task 8: Unique-prompt live gate (mandatory before “done”)

**Files:**
- Modify: `scripts/perf/gateway_pipeline_bench.py` — default body `ping <uuid4>` per request; flag `--unique-prompts` (default on for this work)
- Evidence dir: `mcp-parallel/findings/bedrock-hotpath-YYYY-MM-DD/`

**Pass criteria:**

| Check | Pass |
|---|---|
| Preflight | `ip_forward=1`, Bedrock TCP < 20ms |
| c=1 N=16 unique | input_scan p50 **not** ~3ms if TTL=0; `ran_inference` on T2 + routing + output (allow path) |
| Policy-block fixture | **0** input T2 Bedrock logs |
| inflight 256 | **0** `no_provider_configured` |
| PIPELINE-0018 | UI Duration vs `total_latency_ms` ±0.1ms on one live event |
| Honesty | If `converse_ms` ≈ routing p50, publish “Haiku floor”; if `client_init_ms` dominated pre-fix and ~0 after, publish connection-reuse win |

Do **not** mark complete on cached `"ping"` or `/health` RPS alone.

---

### Task 9: Prompt-cache the adjudicator **system** text

**Files:** `llm_router.py` (already has `enable_prompt_cache` on `converse`)

If `len(adjudicator_system) < 4096`, pad with a stable comment block to cross the Bedrock cache threshold **or** keep the full instructions (already long). Set `enable_prompt_cache=True` on the adjudicator call only (not on unique user T2 prompts).

**Test:** mock converse kwargs include `cachePoint` in `system` when flag True and system ≥ 4096.

---

### Task 10: Overlap T2 ∥ routing in wall-clock only (settings-safe)

**Do not fuse prompts.** Two `aconverse` calls, `asyncio.gather`, only when:

- org `tier2_enabled` is on (or default-on) **and** this request will run input T2 (not policy/T1 block)
- org `routing_enabled` is on **and** adjudicator would have run (not single-candidate skip)
- T1 redact already applied; adjudicator preview is **masked**
- join **before** `body["model"]`, SSE, `acompletion`

If either flag is off, skip that call entirely (honor settings; also cuts latency).

If T2 `is_terminal_block`: drop routing result; no org LLM.

`total_latency_ms` = wall clock (PIPELINE-0018). Overlapped stages must not double-count.

**Test:** routing-off → adjudicator client `call_count==0`; T2-off → scanner Bedrock `call_count==0`; both on → two calls, wall < sum.

---

## Execution

Plan updated and saved to `docs/plans/2026-08-14-bedrock-hotpath-rps-latency.md`.

Product decisions 1–6 are **locked**. Reply **go implement** plus yes/no on the three remaining confirms (lift this host’s cgroup pin; native async Bedrock; faster adjudicator model, T2 unchanged).

Then pick:

**1. Subagent-Driven (this session)** — fresh subagent per task, review between tasks (`skill-subagent-driven-development`).

**2. Parallel Session (separate)** — worktree + `executing-plans`.
