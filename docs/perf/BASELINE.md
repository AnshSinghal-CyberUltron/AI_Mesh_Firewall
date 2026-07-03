# Perf Baseline — before dynamic full-hardware sizing

Captured 2026-07-03 on the shared dev host (unconstrained: **16 cores / 58.85 GiB**).
Reproduce with `scripts/perf/loadtest.py`. This is the "before" for the dynamic
full-machine concurrency work (`scripts/ralph/perf_scratchpad.md`).

## How the stack runs today (measured, not assumed)

`docker top` on the live containers:

| Service | Server | Worker procs | Per-proc RSS | Max cores reachable |
|---------|--------|--------------|--------------|---------------------|
| gateway | gunicorn + UvicornWorker | **6** (`--workers 6`, from `.env WEB_CONCURRENCY=6`; Dockerfile default is 4) | ~300 MiB | 6 of 16 |
| control | **daphne** (single ASGI proc) | **1** | ~1.0 GiB | **1 of 16** |

The gateway Dockerfile CMD hardcodes `--workers ${WEB_CONCURRENCY:-4}`; control's
Dockerfile CMD is a single `daphne` — neither scales with the host. On a 16-core
box the backend can reach **at most ~7 of 16 cores**, and control alone caps at 1.

## Item 01 — Baseline load test (RPS / p50 / p99 / cores)

`loadtest.py`, 100 concurrent clients, 12 s, auth-exempt health endpoints. CPU% is
the peak from `docker stats` during the run (÷100 = cores used).

### Control — `GET /api/health/` (single Daphne)
```
requests=4406  rps=346  p50=247.4ms  p95=362.2ms  p99=384.2ms  max=1287ms  errors=0
control container: peak CPU 116.2%  =>  1.16 cores used   mem 924 MiB
```
A *trivial* health check takes **247 ms at p50** under load — the single event loop
serializes everything onto one core. This is the control-plane concurrency wall and
the root of the "control-1 oscillates unhealthy under load" symptom (requests queue
behind one process; add the 24–30 s soc-kpis query and the loop stalls).

### Gateway — `GET /health` (6 gunicorn workers)
```
requests=39079  rps=3146  p50=26.5ms  p95=56.9ms  p99=107.9ms  max=577ms  errors=69*
gateway container: peak CPU 432.9%  =>  4.33 cores used    mem 1.60 GiB
```
9× the control RPS on the same trivial work, because 6 workers spread across cores.
Still only **4.33 of 16 cores** — the 6-worker cap leaves >10 cores idle; heavier
work would saturate 6 but never more. (*69 errors = RemoteDisconnected/IncompleteRead
connection churn at peak, ~0.18%.)

### Verdict
Combined peak ≈ **5.5 of 16 cores (~34% of the machine)** under load; **~66% idle**.
Exactly the reported symptom: *"use the full potential, not throttling."* Control is
the worst offender (1 core, 9× slower). Fix = size workers/threads/pools from the
detector (`resource_budget.py`, done in P1) and give control a multi-worker async
server.

## Item 02 — Per-worker RSS (feeds the RAM ceiling)

| Service | Container RSS | Baseline (master+shared) | **Incremental / worker** | Formula input |
|---------|---------------|--------------------------|--------------------------|----------------|
| gateway | 1.60 GiB @ 6 workers | ~0.30 GiB | **~0.22–0.30 GiB** | measured ≈ 300 MiB |
| control | 0.92 GiB @ 1 daphne | — | ~0.5–0.6 GiB (est., per future worker) | est. 512 MiB |

`resource_budget.py` defaults `PER_WORKER_RSS_MB=512`, which is **conservative**
(≥ the ~300 MiB gateway actually uses), so the RAM-bound worker count errs on the
safe side of the OOM ceiling. On the constrained profiles this matters:

- **6c / 16 GiB**: `16·0.75 / 0.5 = 24` RAM-workers ≫ 6 cores → **CPU-bound at 6**. Even at the real ~300 MiB, 40 RAM-workers ≫ 6 → still CPU-bound. Safe.
- **12c / 60 GiB**: `60·0.75 / 0.5 = 90` ≫ 12 → **CPU-bound at 12**. Safe.
- Only a memory-heavy worker (>~1.5 GiB each) on a small box would let RAM bind first — which is the intended ceiling behavior.

Actual gateway per-worker RSS (~300 MiB) can be pinned via `PER_WORKER_RSS_MB=300`
once P4 de-blocking settles the steady-state footprint; keeping 512 for now.

## Next (P2+)
- **06**: gateway entrypoint computes `WEB_CONCURRENCY` from the detector (drop the hardcoded 4) — STACK-CHANGE.
- **07**: control runs a dynamically-sized async server (gunicorn+UvicornWorker, N≈cores) instead of one Daphne — STACK-CHANGE, the biggest single win here.
