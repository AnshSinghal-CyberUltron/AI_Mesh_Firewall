# Shared-Infra Changelog — Dynamic Full-Hardware Concurrency lane

Every entry that alters the shared stack (worker counts, DB/Redis pools, an image
rebuild, a restart) is logged here AND in three mirrors: `AGENTS.md` (## Shared
Infra Changes), `.cursor/rules/shared-infra-changelog.mdc`, and Ruflo memory
(`shared/infra-changes`). Newest first.

---

## PERF-0012 — Shared control-1 RECREATED to the multi-worker image (was wedged)
- **Date:** 2026-07-03
- **What:** The running shared **control-1** container was recreated
  (`docker compose up -d --no-deps --force-recreate control`) from the old single
  **Daphne** image to the new image. It had been **wedged/unhealthy for ~46 min**
  (failing streak 277, CPU 0.19% idle, `/api/health/` timing out) — the classic
  single-event-loop failure: one blocking op stalls the sole Daphne process and no
  other worker can serve. It now runs **gunicorn + 16 UvicornWorker** (detector on
  the 16-core host), healthy in 2 s, **1.95 GiB RAM** (efficient CoW), CPU idle.
- **AFFECTS:** the **running shared control plane**. This deploys to the live stack
  all the control-side perf work: multi-worker server (PERF-0002), ASGI thread pool
  (PERF-0004), Redis/cache pool (PERF-0007), the BRIN index (PERF-0008, already
  applied), and the soc-kpis/hot-endpoint query fixes (PERF-0009/0010/0011). Control
  now uses all cores under load and is resilient (a wedged worker can't down the
  service) instead of the previous single-core oscillation.
- **ACTION FOR OTHERS:** none — control-1 is healthy and faster. It runs 16 workers
  (~2 GiB); set `CONTROL_WEB_CONCURRENCY` if you want fewer. The old "control-1
  oscillates unhealthy under load / wedges" problem is resolved. If you `docker
  compose up` control it will keep the new image + worker sizing.
- **PROOF:** health 200 (streak 0), `--workers 16`, mem 1.95 GiB; was streak 277
  wedged. Constrained-profile behavior proven separately (items 22/23).

## PERF-0011 — Apply the metadata-extraction fix to 3 more full-haul endpoints
- **Date:** 2026-07-03
- **Files:** `control/ai_mesh_control/policy/security_views.py` (module-level
  `KeyTextTransform` import; 3 views).
- **What (item 21):** Surveyed every `.values(..., "metadata")` full-window haul.
  Three more used ONLY bounded scalar fields (same anti-pattern as soc-kpis) and now
  extract them in SQL instead of hauling the whole JSON:
  - UserSecurityKpis (`high_risk_users`, 30–90d) — only `security_risk_score`.
  - Agent-risk breakdown (30–90d) — only `security_risk_score`.
  - RagPipelineStages — only `event_type` / `pipeline_stage` / `latency_ms`.
  The remaining ~10 full-haul views genuinely need the full metadata — they pass the
  whole dict to classifiers/taggers/serializers (`specialty_modules_for_event`,
  `_tag_event`, `_violation_tag_event`, `metadata_rule_names`, `_get_owasp_codes`,
  `_sanitized_meta_for_client`, nested `extra`) — so the 3-field trick doesn't apply;
  they still benefit from the direct-FK org filter (PERF-0010). The `[:50]`/`[:scan_cap]`
  views are already row-bounded.
- **AFFECTS:** `ai_mesh_firewall-control` image (behavior identical, just faster).
- **ACTION FOR OTHERS:** `docker compose build control`; output identical, no restart.
- **PROOF:** all 3 verified `IDENTICAL: True` (high_risk_users / by_type / stages)
  old-vs-new on live data; 30d-window fetch **13.24s → 0.67s (19.7×)**;
  `manage.py check` clean.

## PERF-0010 — soc-kpis: direct-FK org filter (drop legacy OR-with-joins) → <1s
- **Date:** 2026-07-03
- **Files:** `control/ai_mesh_control/policy/security_views.py`
  (`_enforcement_events_for_request`).
- **What (item 20):** The shared org-scoping helper (used by ~31 SOC views) filtered
  `Q(organization=org) | (Q(organization__isnull=True) & <endpoint/agent/policy
  joins>)` plus a separate `Endpoint.objects.filter(...)` subquery. The telemetry
  drain (and the eval path) set `organization_id` on every event — **0 of 288k rows
  are NULL-org** — so the legacy branch matched nothing while costing an extra query
  and three joins per call. Simplified to `base_queryset.filter(organization=org)`.
- **AFFECTS:** the `ai_mesh_firewall-control` image — **all ~31 SOC views** get the
  simplified filter (identical results, fewer queries/joins).
- **ACTION FOR OTHERS:** `docker compose build control` to adopt. **Output identical**
  (row sets verified equal). No restart. If a NULL-org event is ever introduced,
  backfill its `organization_id` rather than reviving the joins.
- **PROOF:** row sets identical (org=2: 109417 == 109417); full org-scoped soc-kpis
  compute (direct-FK + PERF-0009 extraction + loop) = **0.768s** for org 2's 109k
  rows → **<1s** (from the 24–30s baseline). `manage.py check` clean.
- **soc-kpis end-to-end:** 24–30s → <1s via BRIN index (PERF-0008) + JSON-haul fix
  (PERF-0009) + this direct-FK filter.

## PERF-0009 — soc-kpis: stop hauling metadata JSON (extract 3 fields in SQL)
- **Date:** 2026-07-03
- **Files:** `control/ai_mesh_control/policy/security_views.py` (SocKpisView).
- **What (item 19):** The view did `list(events.values("action", "metadata"))` —
  pulling the **entire** metadata JSONB (~1.6 KB/row × 288k = ~460 MB) to Python and
  decoding 288k dicts, then looping. It now extracts only the 3 fields the metrics
  use — `security_risk_score`, `latency_ms`, `request_id` — in SQL via
  `KeyTextTransform`, and the (unchanged) per-row loop reads those scalars. Metadata
  field types are uniform (risk/latency = JSON number, request_id = JSON string), so
  text extraction is exactly faithful.
- **AFFECTS:** the `ai_mesh_firewall-control` image (behavior identical, just faster).
- **ACTION FOR OTHERS:** `docker compose build control` to adopt. **Output is
  byte-identical** — no dashboard/API change; no restart required.
- **PROOF:** on the live 288k-row / 24h window: full view compute **14.67s → 0.98s
  (14.9×)**; `METRICS IDENTICAL: True` across every field (total/blocked/redacted/
  critical/actions/latency buckets/latency_sum/requests_*); per-row extraction 0
  mismatches / 288,216; `manage.py check` clean. Pairs with the BRIN index (PERF-0008)
  for the window seek; item 20 (org filter) is the remaining piece toward <1s.

## PERF-0008 — soc-kpis: BRIN index on EnforcementEvent.created_at (DB schema change)
- **Date:** 2026-07-03
- **Files:** `control/ai_mesh_control/policy/models.py` (Meta index),
  `control/ai_mesh_control/policy/migrations/0036_ev_created_at_brin.py`.
- **What (item 18):** The soc-kpis view (`security_views.py:1140`) filters
  `created_at >= since`, but the only created_at-bearing index was the composite
  `(organization, event_class, created_at DESC)` whose leading column is
  organization — useless for a created_at-only window. Added a standalone **BRIN**
  index `ev_created_at_brin` on `created_at`. BRIN because the table is an
  append-only event log (rows inserted in created_at order) → block-range pruning
  seeks the recent window and the index stays tiny as the table grows.
- **Applied CONCURRENTLY** (`AddIndexConcurrently` + `atomic=False`) — **no table
  lock** on the shared 288k-row / 472 MB table.
- **AFFECTS:** the shared Postgres schema — **the index is already live on the
  shared DB** (migration 0036 applied). Backward-compatible (old control code works;
  queries just faster).
- **ACTION FOR OTHERS:** `docker compose build control` at your convenience to get
  the migration file; `manage.py migrate` will then see 0036 already applied (no-op).
  No restart needed.
- **PROOF:** index = **40 kB**; selective 10-min window → `Bitmap Index Scan on
  ev_created_at_brin` (block-range pruning), **0.3 ms**. NB: the *dominant* soc-kpis
  cost (24–30 s) is hauling 288k × ~1.6 KB metadata JSON into Python — fixed in
  items 19–20; this index handles the window-seek half.

## PERF-0007 — Redis/cache pools sized from the detector (vault already done)
- **Date:** 2026-07-03
- **Files:** `shared/ai_mesh_shared/resource_budget.py` (+`redis_pool` field/CLI,
  additive), `shared/tests/test_resource_budget.py`, `gateway/entrypoint.sh`,
  `control/server-entrypoint.sh`.
- **What:** Two per-worker Redis pools were fixed statics: gateway middleware
  rate-limit pool `GATEWAY_REDIS_MAX_CONNECTIONS`=300, control Django cache
  `DJANGO_CACHE_MAX_CONNECTIONS`=200. The detector now derives
  `redis_pool = clamp(asgi_threads*4, 64, 256)` (6c=64, 12c=96, 16c=128) and the
  entrypoints export both env vars from it (explicit env overrides). Redis ops are
  sub-ms, so the real per-worker concurrent-connection count is tiny — this is a
  generous lazy ceiling that scales with cores and never bottlenecks. The gateway
  **hot-path** `REDIS_CLIENT` is intentionally left unbounded (self-scaling, safe —
  capping the hot path would risk errors). Vault pool was already detector-sized
  (PERF-0005). Live Redis is at 49/10000 clients — enormous headroom.
- **AFFECTS:** `ai_mesh_firewall-gateway` + `ai_mesh_firewall-control` images
  (rebuilt); resource_budget.py additive.
- **ACTION FOR OTHERS:** `docker compose build gateway control` to adopt. Running
  containers not recreated. On the shared host these values only take effect on a
  recreate; they are lower than the old 300/200 but far above real per-worker usage.
- **PROOF:** `--cpus=6` PID1 env: `GATEWAY_REDIS_MAX_CONNECTIONS=64`,
  `DJANGO_CACHE_MAX_CONNECTIONS=64`; control `/api/health/` 200; 20 detector tests green.

## PERF-0006 — Postgres max_connections budget: 400 confirmed sufficient (no restart)
- **Date:** 2026-07-03
- **Files:** `scripts/perf/pg_budget.py` (new helper). **No docker-compose / Postgres
  change — max_connections stays 400, no restart.**
- **What:** Item 15 = "max_connections = workers × db-threads + margin (raise OR
  pgbouncer)". **Measured** the real connection model instead of the detector's
  worst-case: control's Django sync views serialize on the thread-sensitive
  executor, so a worker holds **~2.5 DB connections** (15 conns for 6 workers), not
  the `asgi_threads`-based ceiling. `pg_budget.py` derives the stack recommendation
  from the detector (`workers*(control_per_worker + vault_pool) + celery + ops`):
  | profile | recommended | vs current 400 |
  |---------|-------------|----------------|
  | 6c/16G  | 107 | ✓ headroom 293 |
  | 12c/60G | 185 | ✓ headroom 215 |
  | 16c host | 257 | ✓ headroom 143 |
  | 32c/120G | 449 | ✗ (raise / add pgbouncer) |
  Live Postgres is at **16/400** in use. `CONN_MAX_AGE=60` (env `POSTGRES_CONN_MAX_AGE`)
  is sane.
- **AFFECTS:** nothing running — this is an analysis + a derivable helper. The
  earlier worker-count changes (PERF-0001/0002) do **not** risk Postgres exhaustion
  on any target profile.
- **ACTION FOR OTHERS:** none required — `max_connections=400` is confirmed
  sufficient through ~24 cores. For a bigger box, set
  `POSTGRES_MAX_CONNECTIONS=$(python3 scripts/perf/pg_budget.py | jq
  .recommended_max_connections)` before `docker compose up` (that DOES restart
  Postgres), or add pgbouncer. Do **not** raise it speculatively — every connection
  costs ~10 MB RAM.

## PERF-0005 — Gateway scanner/bedrock/vault pools sized from the detector
- **Date:** 2026-07-03
- **Files:** `shared/ai_mesh_shared/resource_budget.py` (+`scanner_pool`/`bedrock_pool`
  /`vault_pool` derived fields + CLI, additive), `shared/tests/test_resource_budget.py`,
  `gateway/entrypoint.sh`.
- **What:** The gateway's per-worker offload pools defaulted to fixed sizes
  (`GATEWAY_SCANNER_THREAD_POOL_SIZE`=8, `GATEWAY_SCAN_THREAD_POOL_SIZE`=4,
  `GATEWAY_BEDROCK_THREAD_POOL_SIZE`=16, `GATEWAY_VAULT_POOL_MAX`=8). The entrypoint
  now exports each from the detector unless the operator pinned it:
  `scanner=clamp(round(cpu),4,16)`, `bedrock=asgi_threads=clamp(round(cpu*2),8,32)`,
  `vault=clamp(round(cpu/2),2,8)`. Each pool multiplies by `workers`, so all three
  are **clamped** in the detector to keep `workers*pool` bounded (item 11). vault is
  a Postgres conn pool kept deliberately small (feeds pg sizing, item 15). Explicit
  env still overrides.
- **AFFECTS:** the `ai_mesh_firewall-gateway` image (rebuilt). `resource_budget.py`
  change is additive (new fields only — control/workers unaffected).
- **ACTION FOR OTHERS:** `docker compose build gateway`. On the shared 16-core host a
  rebuild+recreate would raise the scan/scanner pool from 4/8 → 16 and vault max
  8→8 (unchanged); running container NOT recreated by this change. Note vault max ×
  workers adds Postgres connections — accounted for in item 15.
- **PROOF:** `--cpus=6` PID1 env = scanner/scan=6, bedrock=12, vault=3; InputScanner
  logs `thread_pool_size=6`; `GATEWAY_SCANNER_THREAD_POOL_SIZE=3` override honored.
  19 detector unit tests green.

## PERF-0004 — Control ASGI sync-offload thread pool sized from the detector
- **Date:** 2026-07-03
- **Files:** `control/ai_mesh_control/main_app/asgi.py`, `control/server-entrypoint.sh`.
- **What:** Non-thread-sensitive sync offload (`sync_to_async(thread_sensitive=False)`,
  `loop.run_in_executor(None, ...)`) runs on the event loop's default thread pool,
  which Python sizes `min(32, os.cpu_count()+4)` — and `os.cpu_count()` is **not
  cgroup-aware** (returns the host's 16 even in a `--cpus=6` container → a 20-thread
  pool per worker). `asgi.py` now sizes that pool per worker from `ASGI_THREADS`
  (exported by the entrypoint from the detector: `clamp(cpu_budget*2, 8, 32)` →
  6c=12, 12c=24). `ASGI_THREADS` env overrides. Thread-*sensitive* sync (Django
  views/ORM, asgiref's hardcoded single-thread executor) is **untouched** — that
  serialization is the P4 de-block concern, not this item.
- **AFFECTS:** the `ai_mesh_firewall-control` image (rebuilt). Bounds/right-sizes the
  offload pool so total threads track the CPU budget (no over-provisioning in
  constrained containers; see item 11).
- **ACTION FOR OTHERS:** `docker compose build control` to adopt. No behavior change
  for the request path except the offload pool size; running container not recreated.
- **PROOF:** `--cpus=6` → boot log `asgi_threads: 12`; each worker logs "ASGI default
  thread-pool executor sized to 12 (ASGI_THREADS)" on first request; `/api/health/` 200.

## PERF-0003 — Harden gateway + control entrypoints against empty WEB_CONCURRENCY
- **Date:** 2026-07-03
- **Files:** `gateway/entrypoint.sh`, `control/server-entrypoint.sh`.
- **What:** gunicorn reads `WEB_CONCURRENCY` itself at config-import time
  (`int(os.environ.get("WEB_CONCURRENCY", 1))`) and **crashes on an empty string**
  (`ValueError: invalid literal for int() with base 10: ''`) before our `--workers`
  is ever parsed. Both entrypoints now `export WEB_CONCURRENCY="$WORKERS"` (the
  resolved integer) before exec'ing gunicorn, so gunicorn's own default always
  matches `--workers` and can never be `""`. Found while proving item 08 (a test
  passed `-e WEB_CONCURRENCY=`; the container exit-1'd at boot).
- **AFFECTS:** `ai_mesh_firewall-gateway` + `ai_mesh_firewall-control` images (rebuilt).
- **ACTION FOR OTHERS:** `docker compose build gateway control` to adopt. No behavior
  change for valid configs — this only prevents a boot crash when some env source
  sets `WEB_CONCURRENCY=` (empty). Running containers not recreated.

## PERF-0002 — Control plane: single Daphne → gunicorn + N UvicornWorker (detector-sized)
- **Date:** 2026-07-03
- **Files:** `control/Dockerfile` (CMD → entrypoint), `control/server-entrypoint.sh` (new).
- **What:** Control ran a **single Daphne** process (dev) / a hardcoded
  `--workers 5` (prod, `docker-compose.prod.yml`). It now runs
  `gunicorn main_app.asgi:application -k uvicorn.workers.UvicornWorker --workers N
  --forwarded-allow-ips *`, where **N comes from the detector** (one worker/core,
  RAM-bounded). Invocation mirrors the already-proven prod command; only `--workers`
  is dynamic. `CONTROL_WEB_CONCURRENCY` env overrides; falls back to 2 if the
  detector errors. Migrations are NOT run by this entrypoint (control's CMD never
  did — unchanged). Channels websockets still work (uvicorn[standard]; group sends
  via the Redis channel layer).
- **Multiproc safety (verified):** the telemetry drain is already per-hostname
  single-runner-locked (`drain_telemetry_from_redis`: atomic Lua dequeue + `SET NX
  EX` per `gethostname`), so N web workers in one container → only one drains per
  tick. Gateway-key resync + simulator seed are idempotent. Boot smoke test: 4
  workers serve `GET /api/health/` → 200 in 2 s.
- **AFFECTS:** the `ai_mesh_firewall-control` image (rebuilt). Baseline was 346 rps
  @ 1.16 cores on one Daphne; multi-worker lets control use all cores.
- **ACTION FOR OTHERS:** `docker compose build control` to adopt. The **running
  control container was NOT recreated** — the shared host still runs the old single
  Daphne, so no behavior change until you recreate it. ⚠️ **Before recreating on the
  16-core host, pin `CONTROL_WEB_CONCURRENCY` conservatively (e.g. 6)** in `.env`:
  an unpinned recreate starts 16 workers, and 16 × (asgi threads) DB connections can
  approach Postgres `max_connections=400` — that is sized properly in item 15
  (PERF-00xx, pending). Recreate command:
  `docker compose up -d --force-recreate control`.
- **Rollback:** revert the two control files and rebuild; the single-Daphne CMD returns.

## PERF-0001 — Gateway WEB_CONCURRENCY now derived from the cgroup-aware detector
- **Date:** 2026-07-03
- **Files:** `gateway/Dockerfile` (CMD → entrypoint), `gateway/entrypoint.sh` (new),
  `shared/ai_mesh_shared/resource_budget.py` (P1, already landed).
- **What:** The gateway image CMD was a hardcoded `gunicorn … --workers
  ${WEB_CONCURRENCY:-4}`. It now runs `gateway/entrypoint.sh`, which sets
  `--workers` from `python -m ai_mesh_shared.resource_budget --value workers`
  (~one UvicornWorker per detected core, RAM-bounded with 0.75 headroom). An
  explicit `WEB_CONCURRENCY` env **still overrides**; if the detector fails to
  run, it falls back to 4 so the gateway can never fail to boot over sizing.
- **AFFECTS:** the `ai_mesh_firewall-gateway` image (rebuilt). Worker count is now
  dynamic when `WEB_CONCURRENCY` is unset. Proven in throwaway containers:
  `--cpus=6`→6 workers/12 threads, `--cpus=12`→12 workers/24 threads,
  `WEB_CONCURRENCY=3`→3 (override honored). The detector reads real Docker
  cgroup-v2 `cpu.max`/`memory.max` (`cpu_source=cgroup-v2`).
- **ACTION FOR OTHERS:** run `docker compose build gateway` to pick up the new
  image. **Behavior is UNCHANGED on the shared dev host** — `WEB_CONCURRENCY=6` is
  pinned in `.env`, so the running gateway stays at 6 workers; no DB change, no
  restart required. To let the shared gateway use all host cores, unset
  `WEB_CONCURRENCY` in `.env` and recreate the gateway (16-core host → 16 workers,
  ~4.8 GiB RSS). The running container was **not** recreated by this change.
- **Rollback:** revert the two gateway files and rebuild; the old hardcoded CMD
  returns.
