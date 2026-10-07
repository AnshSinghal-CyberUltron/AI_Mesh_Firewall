# RC2 specification (controller, 2026-09-24 ~10:15Z) — authoritative

Base: rvproto-2 "final" = SP/rvproto2 manifest b910f629ace4702a5fc954c4e6bda1cead7206c554a6c66eaa654aa83e8783d6
(images gateway@sha256:ed58fe88…, guard@sha256:96e5dd4a…). Owner of the tree and the RC2 cut: proto-builder (lane r2impl).
Helper lanes deliver unified-diff patches under SP/evidence/r2-impl/patches/ with a README + test evidence; proto-builder
applies them. Nobody else edits SP/rvproto2.

## RC2 MUST (each with a test that FAILS on the base and PASSES on RC2)
| # | Item | Evidence of the defect | Who |
|---|---|---|---|
| 1 | Apply patches/d2-d1.patch (partition OOM D2 + dead pooled socket 500 D1) + functional partition test (60 s host iptables DROP gateway↔store at ≥ 50 RPS ×3: worker RSS flat (< +100 MB), no worker death, 0 HTTP 500, statuses per USAGE §4, recovery ≤ 5 s after heal) | r2-chaos dev-D2 3/3 OOM; D1 64 tracebacks | r2-fix-d2 (patch, done) → proto-builder applies |
| 2 | C10 admission = CoDel-style sojourn rule: RV_OWNER_ADMISSION=bound\|codel (default codel); shed only when the owner's MINIMUM queue delay over RV_OWNER_INTERVAL_MS (100) stays above RV_OWNER_TARGET_MS (5); hard cap RV_OWNER_QUEUE_MS default 60; per-request budget-miss shed = optional knob default OFF; Retry-After = backlog/rate. Tests: bursty arrivals at ρ≈0.45 on ONE owner → 0 sheds; sustained 2× → sheds with bounded admitted wait; single-owner split at 150 RPS constant (functional) → infra ≤ 0.1% | r2unit xb-150 1.32% sheds at 44.5% owner util; r2fleet 0.44% at ρ 0.26 (C43 off); r2state 0.39% at 150 RPS; r2mig 0.3% at 40 RPS on one guard | proto-builder |
| 3 | C36 gap 503s (plan_unavailable / kill_switch_unavailable / shared_state_unavailable) carry Retry-After (and retry-after-ms) | r2-state | helper r2-fix-c36m (patch) |
| 4 | C36 writer reports honestly: committed+published → ok; committed + publish failed → ok with status "publish_pending" (re-hydrator publishes); not committed → error. Functional test: Postgres restart/failover mid write-stream → every "ok" present in Postgres, every "error" absent, 0 false failures | r2-state: 22/149 committed writes reported as failed during Cloud SQL failover (publish_safe doesn't catch psycopg errors) | helper r2-fix-c36m (patch) |
| 5 | /metrics = valid Prometheus exposition (one label set per sample) for gateway, guard and unit exporters; unit test parses the full output with prometheus_client's parser | r2-gke: 21/41 lines `rv_gc_pause_ns{gen="0"}{stat="n"}` → GMP drops the scrape → HPA gets no rv2_signal | helper r2-fix-c36m (patch) |
| 7 | C43 push coalescing (moved from RC3 — measured cost): owner pushes at most once per RV_C43_PUSH_MIN_US (default 1000) per subscriber and only when the backlog estimate changed by ≥ 1 window (RV_C43_PUSH_MIN_DELTA); keep routing quality (ratio ≤ 0.25 on the F-C43 functional test). Microbench: owner CPU, exec p99 and pushes/s at 36 / 120 / 240 subscribers @170 req/s; gateway CPU/req with C43 ON vs OFF | r2fleet C43-1 on RC1: 72 pushes per request at 36 workers, gateway +2.3 ms CPU/request (≈ 15% of 15.1 ms/req), ≈ 60% of requests +0.3 ms owner wait from push rounds; cost grows with workers × rate ⇒ not horizontally scalable as is | proto-builder |
| 6 | RV_CONN_MAX_AGE_S / RV_CONN_MAX_REQUESTS (default off): after the limit the gateway sends `connection: close` on the next response (never mid-stream) so L4 NLB clients rebalance after scale-out | B22; MEASURED r2-mig dry-as-1: new gateway 0–0.2 req/s vs 40 on the old one, time-to-balance NEVER | proto-builder |

## RC2 SHOULD (include if ≤ 60 min extra; otherwise RC3)
- Owner death: in-flight guard calls re-dispatched to another owner within the deadline; if none can answer in time → 503 (retryable), never 403 blocked_by_policy (r2-chaos f3 dry run: in-flight → FAIL_CLOSED 403).
- Durable audit: at-least-once local spool (service-owned dir, bounded, replay on store heal; declared spool-full policy) — r2-chaos/r2-state: 23k audit records erased by FLUSHALL, 320 dropped in a failover, 66 lost in a 30 s partition.

## RC3 (after PROVEN; not blocking the proofs)
RV_NAMESPACE (keys + pub/sub channels), RemoveIPC hardening + preflight subcommand, RV_DRAIN_S 300 + terminal SSE error event + MIG graceful-shutdown mechanism, budget-lease revocation on budget decrease (r2console: 1,106 admits after budget cut to 40 tokens).

## Deliverable
VERSION with manifest sha256; tarball SP/evidence/r2-impl/rc2/rvproto2-rc2.tar.gz (+sha256); images pinned by digest in IMAGES; USAGE.md updated (new knobs); functional results for items 1–6 (+ the prior suite) under functional/rc2/. Then SendMessage to the controller: "RC2 manifest: <sha256>".
