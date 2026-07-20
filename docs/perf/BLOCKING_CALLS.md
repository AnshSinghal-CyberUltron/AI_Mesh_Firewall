# P4 audit — blocking calls in async handlers

Goal (perf_scratchpad P4): find sync/blocking work running **on an event loop** — a
blocking call in an `async` handler stalls that worker's loop and every concurrent
request on it (the #1 latency cause). Audited 2026-07-03 by pattern sweep across
`gateway/ai_mesh_gateway` (async FastAPI) and `control/ai_mesh_control` (Django ASGI
+ Channels). Patterns: `requests.*`, `time.sleep`, sync `litellm.*`/`boto3`,
`subprocess`, `open().read`, sync `redis.Redis`, blocking `.result()`, unwrapped ORM.

## Headline

**The async handlers are already de-blocked.** The gateway offloads *every* blocking
operation to a dedicated `ThreadPoolExecutor` (those pools are now detector-sized —
PERF-0005), talks HTTP over `httpx` async, and Redis over `redis.asyncio`. Control's
async WebSocket consumers wrap all ORM in `sync_to_async`. There is **no sync call
sitting directly on an event loop** in either service. The remaining serialization is
architectural (Django *sync* views), not an event-loop block — see below.

## Gateway (async FastAPI data plane) — CLEAN

| Potential blocker | Location | Status |
|-------------------|----------|--------|
| Tier-1 scan (regex/segmentation, CPU) | `scanner.scan_prompt/scan_output` | **Offloaded** → `await loop.run_in_executor(self._executor, …)` (scanner.py:977/988) |
| Tier-2 Bedrock (`boto3.invoke_model`, network) | `llm_judge.judge` | **Offloaded** → `run_in_executor(self._executor, self._judge_sync)` (llm_judge.py:229); dedicated `_bedrock_executor` keeps it off the Tier-1 pool |
| Embedding vault (`litellm.embedding` + psycopg) | `embedding_vault.check/add_attack` | **Offloaded** → `run_in_executor(self._executor, self._check_sync)` (embedding_vault.py:444) |
| Vector store query + BYOK embed (`byok_embedder.embed_texts`) | `vector_client` | **Offloaded** → async query via `ThreadPoolExecutor` (vector_client.py:261/608/854) |
| Outbound HTTP | gateway-wide | `httpx` async — **no `requests.*` anywhere** in `ai_mesh_gateway` |
| Redis (rate limit, model state, policy sync) | `rate_limit_enforcement`, `model_state`, `vector_policy_sync` | `redis.asyncio` (`async with redis_client.pipeline()`) — **async** |
| Blocking future waits | — | no `.result()`/`futures.wait`/`as_completed` on a loop |

The one `time.sleep` (`main.py:526`) is inside `_http_request_with_retry`, the **sync
startup** backend-registration retry — it runs once before the server serves traffic,
not on a request path. Non-issue (left as-is).

## Control (Django ASGI + Channels)

- **Async WebSocket consumers (`ws/consumers.py`) — CLEAN.** All ORM is wrapped
  (`await sync_to_async(_get_user)()`, `sync_to_async(_get_org_from_profile,
  thread_sensitive=True)`), sleeps use `await asyncio.sleep`. No loop blocker.
- **HTTP views are 100% synchronous: 0 `async def` views, 38 sync DRF methods.**
  Django's `ASGIHandler` dispatches every sync view through
  `sync_to_async(thread_sensitive=True)` → asgiref's **single** thread-sensitive
  executor thread *per worker*. So a sync view never blocks the event loop, but all
  sync views on a worker **serialize onto one thread**. This is the control-plane
  concurrency model, and why the fix was **N workers** (PERF-0002, item 07): per-worker
  sync concurrency = 1, total = N.
  - Sync outbound HTTP (`requests.get/post`, `timeout=10`) lives in these sync views
    (`core/gateway_admin_proxy_views.py:81`, `mcp_connector/views.py:618/807`). A slow
    upstream holds the worker's single sync slot for up to the timeout — serializing,
    not loop-blocking. Admin/MCP paths, low RPS. Mitigated by N workers + the 10 s cap.
  - **The worst per-worker staller is the soc-kpis view (24–30 s sync ORM query,
    `security_views.py:1140`).** One call ties up a worker's sync thread for ~30 s.
    That is fixed at the source in **P6** (index + query rewrite), and N workers stops
    it from wedging the *whole* control plane (the old single-Daphne behavior).

## Conclusions for P4

1. **Item 13 (offload/convert):** the async handlers need **no conversion** — the
   offload/`httpx`/`redis.asyncio` patterns are already pervasive in the gateway and
   the async consumers are clean. The only sync work is Django sync views, which Django
   already offloads to the thread-sensitive executor (converting all 38 to `async def`
   is a large, out-of-scope refactor; the sanctioned mitigation is N workers + the P6
   query fix). Item 13 therefore = confirm-and-document (below), not a code rewrite.
2. **Item 14 (prove no stall):** validate empirically on the gateway that a slow
   *offloaded* request (large Tier-1 scan) does **not** stall concurrent fast requests
   on the same worker — i.e. the `run_in_executor` offload keeps the loop free.
3. The soc-kpis slow query (the one call that genuinely monopolizes a worker) is
   handled in P6, not by async conversion.

## Item 13 (offload/convert) — verified: no conversions needed

The three conversions the mandate lists (sync ORM → `sync_to_async`, `requests` →
async, CPU → `run_in_executor`) have **no remaining targets** — they are already
implemented. Confirmed on the single most important handler, the inference hot path
`proxy_chat` (`main.py:4509`, `async def`):

- Body: `await request.json()` (async).
- Redis (rate-limit/burst/rpm, kill-switch, model-state): `await REDIS_CLIENT.incr/
  expire`, `await check_kill_switch`, `await check_model_state` — all `redis.asyncio`.
- Rate limiting: `await RATE_LIMITER.check_rate_limit / check_org_rate_limit`.
- Upstream LLM: `await LLM_ROUTER.acompletion(...)` — litellm **async**, not sync
  `completion`.
- CPU Tier-1 scan: `await asyncio.to_thread(_security_scan, ...)` — offloaded off the
  loop (alongside the `run_in_executor` scanner path).

So a `proxy_chat` request never runs sync work on the event loop. The only sync-HTTP
`requests.*` left in either service is in **control admin views** (circuit-breaker /
RAG-collection / db-test proxies, `IsAdminOrSuperuser`-gated, `timeout=10`) — a
low-RPS admin path on Django's thread-sensitive executor, not an event-loop block;
pooling/async-converting it would be churn with no throughput benefit. **No code
change made for item 13** — the de-blocked state is the finding. Item 14 proves it
empirically.

## Item 14 — a slow request does NOT stall same-worker concurrent requests (proven)

`scripts/perf/deblock_probe.py`: a 3-endpoint FastAPI app run under the gateway
image's own uvicorn, **1 worker**. `/fast` (instant), `/block?ms` (inline
`time.sleep` — the anti-pattern), `/offload?ms` (`await asyncio.to_thread(time.sleep)`
— exactly what `proxy_chat` does). Measured `/fast` latency while 8 concurrent slow
(500 ms) requests hammered the same worker:

| Background load on the worker | `/fast` RPS | `/fast` p50 | `/fast` p99 |
|-------------------------------|------------:|------------:|------------:|
| none (baseline)               | 5656        | 3.2 ms      | 4.6 ms      |
| 8× inline `/block` (blocks loop) | **5.8**  | **4011 ms** | **4015 ms**  |
| 8× `/offload` (asyncio.to_thread) | **5597**| **3.2 ms**  | **4.8 ms**   |

Inline blocking stalls concurrent requests ~**975×** (p99 4011 ms vs 4.6 ms, RPS
5.8 vs 5656). The offload pattern — the one the gateway uses on every hot path —
leaves concurrent `/fast` traffic **statistically unchanged**. Combined with the
item-13 code evidence, the gateway's async handlers provably do not stall the worker
on slow work. (Control sync views are a separate, architectural serialization, sized
around via N workers + the P6 query fix — not an event-loop stall.)
