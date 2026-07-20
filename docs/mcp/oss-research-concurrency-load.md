# Concurrency / load patterns for sandboxed subprocess MCP servers (P2 item #12)

> Deliverable for `scripts/ralph/mcp_progress.md` **P2 item #12**: pooling / warm-start / backpressure
> patterns for running MCP **stdio subprocess** servers at scale, mapped to the repo's current knobs, and
> the precise recommendations for **B3** (#19–21) and **P8/P9** (#26–31). Sources via WebSearch (Envoy
> circuit-breakers, backpressure-by-design, retry-storm). Grounds in the verified concurrency model
> (item #2 `broker-sandbox-lifecycle.md` §1, agent `stdio_manager.py`, `mcp_sandbox_client.py`).

## The repo already has a mature concurrency model (verified)
- **Process pool + reuse**: `stdio_manager._ensure_process` reuses a running child when command+env match;
  children are long-lived, not per-call. Registry guarded by `_registry_lock` (`:47/:214`).
- **Request multiplexing**: ONE subprocess serves many concurrent JSON-RPC calls — each request's `msg_id`
  is registered in `proc._pending` (`:76`) and a single `_reader_task` (`:120`) reads stdout and resolves
  the matching Future (`:152`). Stdin writes are serialized by `proc.lock` (`:372/:457`) so frames can't
  interleave-corrupt. **This is the throughput multiplier that makes a small pool sufficient.**
- **Bounded concurrency (3 limiters)**: global `_MAX_PROCESSES=20` (`:29`), per-org `_MAX_PROCESSES_PER_ORG=16`
  (`:34`, LRU-evict `:231-242`), init `Semaphore(_MAX_CONCURRENT_INITS=4)` (`:35/:390`).
- **Admission control**: broker `_check_org_quota` → **429** over `MAX_ORGS=50` (`routes.py:66`);
  `cached_docker_ok` → **503** if daemon not warm (`:63`, 15s TTL circuit-break on Docker).
- **Bounded retry + jittered backoff**: broker cold-start re-ensures with bounded backoff (`routes.py:117/140`);
  client `_sleep_backoff` has **jitter** `random.uniform(0, delay*0.25)` (`mcp_sandbox_client.py:82`).

## Canonical patterns → repo mapping (present / gap)
| Pattern (sourced) | Repo state | Gap / action |
|---|---|---|
| **Admission control** — reject at entry before consuming resources (429/503+Retry-After) | quota→429, docker→503 | ✅; **add `Retry-After` header** on 429/503 so clients pace instead of hot-loop (P9 #29) |
| **Bounded concurrency + bounded queue** — limiters not unbounded spawn | 3 limiters (20/16/4) | ✅ caps; init-semaphore waiters have **no queue-time SLO / shed timeout** — a burst of >4 cold inits queues unbounded-in-time. Add a max-wait → shed with 503+Retry-After (#29) |
| **Circuit breaker** — fail fast on upstream degradation, apply backpressure (Envoy) | `docker_ok` 15s cache = coarse daemon breaker | **No per-sandbox breaker**: repeated init failures for one server re-attempt every call (retry-storm risk). Add per-(org,server) breaker that trips to fast-fail + cooldown (#29) |
| **Retry-storm prevention** — bounded retries, backoff+**jitter**, honor Retry-After | client jitter ✅, broker bounded ✅ | client honors neither `Retry-After` nor caps *cross-request* retries; ensure total client attempts bounded (#21) |
| **Warm pool / pre-warm** — hide cold-start by provisioning ahead of first use | **reactive only** — `ensure` returns on container `running`; `warm` flag ignored (`routes.py:153`) | **= B3.** Pre-warm on register/authorize/first-sync (#19); readiness poll to agent `/health` until proc-capable (#20) |
| **Bulkhead per tenant** — isolate pools so one noisy tenant can't starve others | per-org cap 16 of global 20 | ✅ per-org bulkhead; note global 20 < 16×many-orgs, so many orgs still contend on the global — size for the fleet (P8) |
| **Load-shedding priority** — drop low-priority first | none | optional; not needed for the 15-MCP matrix but note for prod |

## B3 ROOT CAUSE (precise — this is the whole of #19–21)
The "MCP sandbox is temporarily unavailable" string is `mcp_sandbox_client._friendly_status` for **502**
(`:57-58`). The mismatch:
- Broker returns **502** for agent-connection errors during cold start (`routes.py:185/191/196`) and **503**
  only for "Docker unavailable" / "Sandbox not running" (`:63/163/166`).
- Client `_request_with_503_retry` retries **only on 503** (`:104`); a **502 is NOT retried** (`:57` maps it
  straight to the hard error).
- → The first tool call right after register races the agent cold-start (npx fetch + `initialize`); the
  agent socket isn't ready → broker 502 → client does **not** retry → user sees "temporarily unavailable".

**Fix (#19–21), root-cause not symptom:**
1. **#19 eager warm**: on register / oauth-authorize / first-sync, call broker `ensure` **and** poll the
   agent `/health` (or a cheap `initialize`) until proc-capable — honor the currently-ignored `warm` flag
   (`routes.py:153`). Steady-state first call then skips cold-start.
2. **#20 broker provisioning state**: report cold-start / not-yet-ready as **503 "provisioning"** (retryable)
   with a `Retry-After`, NOT 502. Reserve 502/hard-fail for genuine unrecoverable errors. Add a readiness
   poll + bounded backoff in `mcp_sandbox_client` that treats 503-provisioning distinctly from hard failure.
3. **#21 verify**: first tool call after register **succeeds** (warm hid the cold-start) OR shows a clear
   *provisioning/retrying* state — never the hard "temporarily unavailable" under normal use.

## P8/P9 implications (#26–31) — sizing & no-503-storm
- **Pool sizing for the 15-MCP matrix (3 orgs × 5 servers):** 15 ≤ global 20 and each org's 5 ≤ per-org 16,
  so **no eviction** in steady state. The real bottleneck is `MAX_CONCURRENT_INITS=4`: cold-starting all 15
  at once serializes 4-at-a-time. → **Pre-warm all 15 (B3) before the concurrency test** so steady-state
  parallel calls hit warm processes and skip the init semaphore entirely (multiplexing handles the rest).
- **Sustained load / sandbox reuse (#29):** because children are reused and requests are id-multiplexed,
  sustained parallel `Everything.echo/add` on warm servers should **not** spawn per-call or exhaust caps.
  Assert: process count stays ≤ caps; no LRU eviction thrash; no 503 storm. If 503s appear, they must carry
  `Retry-After` and the client must pace (not hot-loop) — add the breaker + Retry-After above.
- **503-storm avoidance:** combine (a) admission 429/503 **+ Retry-After**, (b) per-sandbox circuit breaker
  (fast-fail + cooldown on repeated init failure), (c) bounded client retries with jittered backoff (already
  present). Without the breaker, backpressure alone lets blocked attempts accumulate → cascade.
- **OAuth-under-load (#31):** warm + reuse means the OAuth token is fetched once per (org,server) and reused
  across the multiplexed calls — assert no stdio server ever triggers the browser OAuth flow (item #8/#10).

## Sources
- [Envoy Gateway — Circuit Breakers](https://gateway.envoyproxy.io/docs/tasks/traffic/circuit-breaker/) — concurrent-request limits, pending-queue overflow → 503, fail-fast backpressure.
- [Backpressure by Design (2025)](https://debugg.ai/resources/backpressure-by-design-2025-concurrency-limits-admission-control-queueing-patterns) — admission control, token-bucket, concurrency limiters, queue-time SLO, bulkheads.
- [What is a Retry Storm](https://jeffbailey.us/blog/2025/12/16/what-is-a-retry-storm/) / [Backpressure patterns](https://codelit.io/blog/backpressure-flow-control) — bounded retries + jitter + Retry-After; circuit breaker prevents blocked-thread cascade.
- Companion repo docs: `broker-sandbox-lifecycle.md` (#2, lifecycle+limits), `oss-research-docker-hardening.md` (#9), `oss-research-npm-untrusted-checklist.md` (#11).
