# Shared-Infra Changelog — Dynamic Full-Hardware Concurrency lane

Every entry that alters the shared stack (worker counts, DB/Redis pools, an image
rebuild, a restart) is logged here AND in three mirrors: `AGENTS.md` (## Shared
Infra Changes), `.cursor/rules/shared-infra-changelog.mdc`, and Ruflo memory
(`shared/infra-changes`). Newest first.

---

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
