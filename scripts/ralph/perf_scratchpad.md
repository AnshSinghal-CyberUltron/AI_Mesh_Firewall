# Claude Code Ralph — Dynamic Full-Hardware Concurrency (parallel; four-memory log on conflicts/stack changes).
# ONE item per iteration. Never fake green. Goal: use all cores, RAM-bounded + headroom, dynamic, no static caps.

## C0 — Coordination (every iteration)
- [ ] 00. Join the Ruflo hive; read AGENTS.md + .cursor/rules + docs/perf/CHANGELOG.md. Before any shared-infra
      or shared-file change, post a FILE-CONFLICT/STACK-CHANGE entry to all four memories (§1). Rebase from main.

## P0 — Measure
- [x] 01. Baseline load test (control + gateway): RPS, p50/p99, per-core utilization (prove ~2 cores used) → docs/perf/BASELINE.md.
      → scripts/perf/loadtest.py (stdlib, reusable for P7). CONTROL single-daphne: 346 rps, p99 384ms, 1.16 cores.
      GATEWAY 6 workers: 3146 rps, p99 108ms, 4.33 cores. Combined ~5.5/16 cores (~66% idle) — the throttling proven.
- [x] 02. Measure per-worker RSS (control + gateway) under load → feeds the RAM cap.
      → gateway ~300MiB/worker (incremental ~0.22-0.30GiB), control ~1GiB single daphne. Detector default 512MiB is
      conservative (RAM-bound stays > core count on both 6c/16G and 12c/60G → CPU-bound, safe). See docs/perf/BASELINE.md.

## P1 — Container-aware detector
- [x] 03. resource_budget.py: read cgroup v2 (cpu.max, memory.max) + v1 (cfs_quota/period, limit_in_bytes); fall back to sched_getaffinity / /proc/meminfo.
      → shared/ai_mesh_shared/resource_budget.py (dependency-free stdlib; importable by gateway+control+workers via /app/shared on PYTHONPATH).
      Walks /proc/self/cgroup up to parent slices; min(quota, affinity) for CPU; min(cgroup_limit, meminfo) for RAM.
- [x] 04. Implement the sizing formula (workers/asgi_threads/pool conns) with headroom + clamps.
      → workers=clamp(min(round(cpu),floor(ram*0.75/rss)),2,round(cpu)); asgi_threads=clamp(round(cpu*2),8,32);
      pg_max_conns=max(100,workers*(db_threads+1)+margin). CLI: --json | --export | --value <field> for entrypoints.
- [x] 05. Unit-test for 6c/16G, 12c/60G, unconstrained host.
      → shared/tests/test_resource_budget.py, 16 tests green. 6c/16G→6w/12t/128pg; 12c/60G→12w/24t/350pg;
      unconstrained 16c→16w/32t. Covers RAM-bound, min-floor, affinity-caps-quota, bogus-limit, v1 path, env overrides.
      Real host (unconstrained 16c/58.85GiB) detects 16 workers / 32 threads — full-machine use confirmed.

## P2 — Dynamic workers (all cores)  [STACK-CHANGE → log to 4 memories]
- [x] 06. Gateway entrypoint computes WEB_CONCURRENCY from the detector (replace hardcoded 4); env still overrides.
      → gateway/entrypoint.sh + Dockerfile CMD. PERF-0001 logged to 4 memories (Ruflo store+notify, AGENTS.md,
      .cursor/rules, docs/perf/CHANGELOG.md). Image rebuilt. PROVEN in real Docker cgroup-v2: --cpus=6→6w/12t,
      --cpus=12→12w/24t, WEB_CONCURRENCY=3→3w (override). Shared host unchanged (.env pins 6). Fallback 4 if detector errors.
- [x] 07. Control plane runs a dynamically-sized async server (gunicorn+UvicornWorker or N Daphne) — replace the fixed 2 procs.
      → control/server-entrypoint.sh + Dockerfile. gunicorn -k uvicorn.workers.UvicornWorker --workers <detector>
      --forwarded-allow-ips * (mirrors proven prod cmd; only --workers dynamic). CONTROL_WEB_CONCURRENCY overrides.
      PERF-0002 logged to 4 memories. Multiproc-safe (drain per-hostname SET NX lock; resync/seed idempotent).
      PROVEN: --cpus=6→6w, --cpus=12→12w, override→3w; LIVE boot 4 workers serve /api/health/ 200. Shared control NOT recreated.
- [x] 08. Verify all cores light up under load.
      → loadtest.py gained --procs (multiprocess generator) — single-process urllib capped offered load at
      ~3200rps (GIL), masking core scaling. PROVEN (constrained throwaway gateways, dummy POLICY_SIGNING_KEY):
      gw6 (--cpus=6→6 workers) = 4971rps, 6.14/6 cores SATURATED, 0 err; gw12 (--cpus=12→12 workers) = 5909rps
      (+19%), 9.16 cores (--procs6), 0 err. Baseline was 4.33 cores (6w single-gen) / 1.16 (1 daphne). Caveat:
      one 16-core host shared by generator+target caps gw12 <12 (a dedicated load box hits 12). Also fixed a real
      bug (PERF-0003): gunicorn crashes on empty WEB_CONCURRENCY at config-import → entrypoints now export resolved int.

## P3 — Dynamic thread pools
- [x] 09. Size Django ASGI_THREADS / asgiref executor from cpu_budget.
      → control/main_app/asgi.py sets loop.set_default_executor(ThreadPoolExecutor(ASGI_THREADS)) per worker;
      entrypoint exports ASGI_THREADS from detector (6c=12, 12c=24) unless pinned. Replaces Python's non-cgroup
      min(32,os.cpu_count()+4)=20-in-6c-container. Thread-SENSITIVE Django views untouched (asgiref 1-thread; P4).
      PERF-0004 logged to 4 memories. PROVEN: --cpus=6 → each worker logs "executor sized to 12"; /api/health/ 200.
- [x] 10. Size gateway scanner/bedrock/vault pools from cpu_budget (not fixed 4/8).
      → detector gains scanner_pool=clamp(round(cpu),4,16), bedrock_pool=asgi_threads, vault_pool=clamp(round(cpu/2),2,8)
      (additive fields+CLI, 19 tests green); gateway/entrypoint.sh exports GATEWAY_SCANNER/SCAN_THREAD_POOL_SIZE,
      GATEWAY_BEDROCK_THREAD_POOL_SIZE, GATEWAY_VAULT_POOL_MAX (was fixed 8/4/16/8). Clamped since ×workers (item 11);
      vault feeds pg (item 15). PERF-0005 → 4 memories. PROVEN --cpus=6: PID1 env scanner=6/bedrock=12/vault=3, InputScanner thread_pool_size=6, override→3.
- [x] 11. Verify total threads = workers × pool (no explosion).
      → MEASURED (sum /proc/*/status Threads in-container): gw6=39 threads (6w), gw12=75 (12w) — LINEAR ~6/worker.
      Key no-explosion proof: gw12 under 640 concurrent connections (--procs 8×80) = STILL 75 threads (== idle) —
      threads track workers, NOT connection count (uvicorn multiplexes conns on the loop). Scan/bedrock/vault pools
      are hard-capped ThreadPoolExecutors (lazy), so absolute ceiling = workers×Σ(clamped pools), linear in cores.
      Starlette/anyio offload pool separately capped 40/worker (didn't spawn under async /health). No code change.

## P4 — De-block async handlers (the #1 latency cause)
- [x] 12. Audit for blockers (sync ORM, requests.get, CPU loops, sync file/lock) → docs/perf/BLOCKING_CALLS.md; claim shared files in the ledger + log.
      → docs/perf/BLOCKING_CALLS.md. FINDING: async handlers ALREADY de-blocked. Gateway offloads ALL sync work
      (scanner/bedrock/vault/judge/vector) to run_in_executor, HTTP=httpx async, Redis=redis.asyncio; NO requests.*,
      no sync-redis, no .result() on a loop. Control ws consumers wrap ORM in sync_to_async. Control HTTP=100% sync
      views (0 async/38 sync) → serialize on the per-worker thread-sensitive thread (not loop-block); mitigated by N
      workers (item 07). Worst staller = soc-kpis 24-30s sync query (fixed in P6). Audit only, no code/stack change.
- [x] 13. Offload/convert: sync ORM → sync_to_async/async; requests → httpx async; CPU → run_in_executor.
      → VERIFIED no conversions needed — all three already implemented. Hot path proxy_chat (main.py:4509, async):
      await request.json(), await REDIS_CLIENT.incr/expire (redis.asyncio), await check_kill_switch/model_state/
      RATE_LIMITER, await LLM_ROUTER.acompletion (async upstream), await asyncio.to_thread(_security_scan) (CPU offloaded).
      Only sync requests.* left = control ADMIN views (IsAdminOrSuperuser, timeout=10, low-RPS, Django thread-sensitive —
      not a loop block); pooling would be churn. No code/stack change; documented in BLOCKING_CALLS.md. Item 14 proves empirically.
- [x] 14. Verify a slow request no longer stalls concurrent requests on the same worker.
      → scripts/perf/deblock_probe.py A/B under the gateway image's uvicorn (1 worker). While 8 concurrent 500ms
      requests hammer the SAME worker: inline time.sleep (anti-pattern) → /fast p99 4011ms, 5.8 rps (STALLED);
      asyncio.to_thread offload (the proxy_chat pattern) → /fast p99 4.8ms, 5597 rps (UNCHANGED vs 4.6ms baseline).
      ~975x difference. Proves the gateway's offload keeps the loop free. P4 COMPLETE. Test tool only, no stack change.

## P5 — Scale the data layer  [STACK-CHANGE → log to 4 memories]
- [x] 15. Postgres max_connections = workers × db-threads + margin (raise OR add pgbouncer); sane CONN_MAX_AGE.
      → MEASURED control ~2.5 DB conns/worker (sync-view serialization, not asgi_threads worst-case). scripts/perf/pg_budget.py
      derives stack rec from detector: 6c→107, 12c→185, 16c→257 — all < deployed 400 (live 16/400). CONN_MAX_AGE=60 sane.
      400 CONFIRMED SUFFICIENT ≤~24c → NO Postgres restart (evidence wins). 32c→449 = raise/pgbouncer threshold.
      PERF-0006 logged to 4 memories (informational; no stack change). No disruptive restart of the shared DB.
- [x] 16. Scale Redis/cache + Vault pools to worker count.
      → detector gains redis_pool=clamp(asgi_threads*4,64,256) (6c=64,12c=96,16c=128, 20 tests green). gateway/entrypoint.sh
      exports GATEWAY_REDIS_MAX_CONNECTIONS (middleware pool, was 300); control exports DJANGO_CACHE_MAX_CONNECTIONS (was 200).
      Generous per-worker lazy ceilings, scale w/ cores, never bottleneck (Redis live 49/10000). Gateway hot-path REDIS_CLIENT
      left unbounded (safe). Vault done in PERF-0005. PERF-0007 → 4 memories. PROVEN --cpus=6: PID1 both=64, control health 200.
- [ ] 17. Verify no connection starvation at peak.

## P6 — Fix hot endpoints
- [ ] 18. soc-kpis: standalone BRIN/btree index on EnforcementEvent.created_at (migration) so the window seeks.
- [ ] 19. soc-kpis: stop hauling metadata JSON — denormalize risk_score/latency_ms/request_id OR aggregate in SQL.
- [ ] 20. soc-kpis: simplify org filter to the direct FK; drop the legacy OR-with-joins. Verify <1s (from 24–30s).
- [ ] 21. Fix other endpoints with the same scan+JSON pattern.

## P7 — Prove dynamic scaling
- [ ] 22. Constrain --cpus=6 --memory=16g: detector down-scales; runs healthy; no OOM.
- [ ] 23. Constrain --cpus=12 --memory=60g: detector up-scales; USES all 12 cores under load.
- [ ] 24. Load-test both: throughput scales with hardware, all cores used, p99 stable, RAM under budget + headroom, ~0 errors → docs/perf/SCALING_RESULTS.md.

## P8 — Freeze
- [ ] 25. Re-run P7 3× consecutive on both profiles, stable; all changes logged to the four memories → <promise>COMPLETE</promise>.