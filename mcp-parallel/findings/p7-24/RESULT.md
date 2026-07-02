# P7.24 — Reaper/idle cleanup correctness

**Iteration:** cursor-ralph-iter18  
**Date:** 2026-07-02  
**Status:** VERIFIED ✅

## What was already in place (prior iterations)

- `reaper.py` `reap_idle_sandboxes`: per-entry `try/except` — one org's Docker error keeps that entry for retry, doesn't abort the sweep or kill the reaper
- `reaper.py` `_reaper_loop`: outer `try/except` around the entire sweep — any exception (including `reconcile_registry` raising) is logged and the loop continues next tick
- `reaper.py` `_reaper_loop`: calls `docker_manager.reconcile_registry()` each sweep tick — re-adopts running containers that the registry lost after a broker restart
- `docker_manager.reconcile_registry()`: scans Docker by role label, registers any running containers not in the registry (idempotent)
- `main.py` `_boot_reconcile()`: scheduled as `asyncio.create_task` on lifespan startup — re-adopts orphan containers immediately on broker restart without blocking first request

## New tests added (iter18)

| Test | File | What it proves |
|------|------|---------------|
| `test_reaper_multi_org_partial_failure` | `test_sandbox_reaper.py` | One org's stop error doesn't prevent other idle orgs from being reaped |
| `test_reaper_loop_survives_reconcile_error` | `test_sandbox_reaper.py` | Exception in `reconcile_registry` is swallowed; loop survives |
| `test_boot_reconcile_called_on_startup` | `test_main_health.py` | Lifespan creates the `_boot_reconcile` task |
| `test_boot_reconcile_tolerates_docker_error` | `test_main_health.py` | Docker error during boot reconcile doesn't crash the broker |

## Gate

`cd services/mcp-broker && .venv/bin/python -m pytest tests/ -q` → **81 passed / 0 failed** (was 75 before +6 new tests)
