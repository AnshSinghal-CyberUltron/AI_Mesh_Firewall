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
- [ ] 07. Control plane runs a dynamically-sized async server (gunicorn+UvicornWorker or N Daphne) — replace the fixed 2 procs.
- [ ] 08. Verify all cores light up under load.

## P3 — Dynamic thread pools
- [ ] 09. Size Django ASGI_THREADS / asgiref executor from cpu_budget.
- [ ] 10. Size gateway scanner/bedrock/vault pools from cpu_budget (not fixed 4/8).
- [ ] 11. Verify total threads = workers × pool (no explosion).

## P4 — De-block async handlers (the #1 latency cause)
- [ ] 12. Audit for blockers (sync ORM, requests.get, CPU loops, sync file/lock) → docs/perf/BLOCKING_CALLS.md; claim shared files in the ledger + log.
- [ ] 13. Offload/convert: sync ORM → sync_to_async/async; requests → httpx async; CPU → run_in_executor.
- [ ] 14. Verify a slow request no longer stalls concurrent requests on the same worker.

## P5 — Scale the data layer  [STACK-CHANGE → log to 4 memories]
- [ ] 15. Postgres max_connections = workers × db-threads + margin (raise OR add pgbouncer); sane CONN_MAX_AGE.
- [ ] 16. Scale Redis/cache + Vault pools to worker count.
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