# Shared-Infra Changelog — Dynamic Full-Hardware Concurrency lane

Every entry that alters the shared stack (worker counts, DB/Redis pools, an image
rebuild, a restart) is logged here AND in three mirrors: `AGENTS.md` (## Shared
Infra Changes), `.cursor/rules/shared-infra-changelog.mdc`, and Ruflo memory
(`shared/infra-changes`). Newest first.

---

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
