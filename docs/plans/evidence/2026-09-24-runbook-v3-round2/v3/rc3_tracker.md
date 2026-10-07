# RC3 + Phase-F pass-1 programme tracker (controller; times UTC 2026-09-24)
| item | owner | status | ETA / done |
|---|---|---|---|
| H4 holdback cap (RV_HOLDBACK_MAX_TOKENS=3, tail rescan, held-tokens histogram) | ttrack-verifier | accepted 12:4xZ | ~16:45Z (progress ~14:45Z) |
| H1/SP7 audit memory bound + policy self-check + memory gauges | r2-fix-d2 | DELIVERED rc3-audit-mem-v1.patch (2ae538a5) | 13:11Z |
| SP1/SP2/H7 freshness stamp + PG timeouts + 2 re-hydrators + reconcile single-flight | r2-fix-c36m | P0 DELIVERED rc3-state-p0.patch (142e312e) ≈13:38–13:55Z; P0.1 (FRESH=5 s default + Postgres-outage stamp continuity, avoid 7.4 s global 503 on SQL failover) requested 13:52Z; then P1 incremental propagation | P0.1 ETA pending; P1 was 18:00Z |
| RC3 core + H2/M1/M2/M4/M7 + SP12; integration of helper patches | proto-builder | sent 12:43Z | ETA pending |
| T1 multi-key / T5 corpora → T3 retry → T4 output kinds → T2 TLS | harness-builder | v2.2 (T1+T5) ≈13:30Z; v2.3 (+T3) ≈13:52Z; T4 → T2 next | pending |
| r2edge fair edge re-run + TLS | micro-claims | plan approved 12:51Z | build+checks ~13:50Z; 9 rounds to ~15:40Z; soak to ~16:25Z; optional Delhi to ~17:05Z; TLS +1h45m after T2 |
| FP7 Valkey failover API re-check | r2-infra | DONE: confirmed on valid evidence | 12:48Z |
| E2-01 tenant scale on RC2 (own store set; 3/1k/10k/25k × phases A–D) | config-capacity-verifier (lane r2scale) | sent 13:02Z (B38) | target N0–N2 by ~16:30Z |
| r2state Phase-F items (mini-stack SP1/SP2/H7/SP6 now; a2+SP1, neg ctrl, FP6, SP3, SP4 after campaign ~14:35Z) | r2-state | approved 13:00Z | ~5 h |
| M4a/b/c (# TYPE, reset-safe rates, windowed GPU busy) — M7 moved to proto-builder r3-shm | r2-fix-d2 (rc3-obs) | DELIVERED rc3-obs-v1.patch (b986c01a) | ≈13:45–13:55Z |
| M1 windowed per-request addon histogram (C4 definition) — added to the holdback patch | ttrack-verifier | sent 13:12Z | within ~16:45Z unless told |
| proto-builder remaining: C43 coalescing, RV_NAMESPACE, RemoveIPC preflight, drain+terminal SSE+MIG graceful, owner-death re-dispatch, audit spool, lease revocation + async refill, single-flight, H2, M2, SP12 | proto-builder | re-split 13:12Z | merged order/ETA pending |
| r2alt capacity survey + CPU-guard feasibility | r2-alt | DONE: Stage B KILL 17.6 ms (exact rewrite); T4/G4 batch sweeps done (no ≥2× win); hourly probes running to teardown | ≈13:55Z |
| shm metrics: per-process slot cap silent drop (512 gauges) + 64 KiB name-directory overflow → RuntimeError → HTTP 500 on request path (r2state live) | r2-fix-d2 (rc3-shm-overflow, on the r3-shm base) — reassigned 13:51Z (proto-builder unresponsive since 12:43Z) | P0 | ETA pending |
