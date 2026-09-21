# GW03 Implementation Plan — ResourceContract

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Every v2 bound is a function of the environment; scaling is a deployment action, never a source edit.

**Architecture:** Copy cgroup v2→v1→affinity→RLIMIT detection into `gateway_v2.runtime.cgroup` (injectable root). `ResourceContract` in `runtime/resources.py` is the only module allowed to hold capacity-position literals. No clamps: derived workers &lt; 1 or fd budget unusable → refuse to start. Dummy pools reload on SIGHUP without dropping in-flight leases. AST gate flags `max_connections=64` outside `runtime/resources.py`.

**Tech Stack:** Python 3.12, pytest, Docker `--cpus`, GitHub Actions.

**Locks (2026-09-21):** full LGW03-1..6; copy detect into v2 (do not import/edit shared/resource_budget.py); this GCP host (16 CPU) for `--cpus` 2/4/8/16, throwaway containers deleted after; SIGHUP dummy pools strictly; edit v1 `gateway/entrypoint.sh` + `docker-compose.prod.yml` so WEB_CONCURRENCY logs detected vs override; refuse_start; formulas per §10.6; GHA `--cpus` 2 and 4; commit+push `revamp`; wipe allowed but prefer not to touch live compose if throwaway v2 suffices.

**Formulas:** `workers = floor(min(cpu*util, ram*util/rss))`; `queue_depth = ceil(offered_rate * p99_s / util)` with offered_rate from workers; `pool_size(kind)` from workers and fd (guard unset if no CapacityHint); `stream_buffer_bytes = floor(memory*util / max(streams,1))`. `target_p99_ms` and `utilization_cap` from env, logged as deployment-declared.

---

### Task 1: Detection + ResourceContract + refuse-to-start
### Task 2: Dummy pools + SIGHUP
### Task 3: AST capacity-literal gate + LGW03-4 negative
### Task 4: v1 entrypoint deviation log + prod compose pin removal
### Task 5: Live `--cpus` 2/4/8/16, memory, fd, SIGHUP; GHA 2 and 4; evidence; push
