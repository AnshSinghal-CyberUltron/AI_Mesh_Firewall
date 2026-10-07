# Part 0 — Round-2 validation result and v3 corrections

> **Status of this document.** v3 is the execution runbook after two rounds of live validation. Round 1 (23 September 2026) produced v2.1 (43 corrections, retained below as Part 0-A). Round 2 (24 September 2026) built the v2.1 target architecture as a measured prototype (**rvproto-2**, release candidates RC1 → RC2) and ran it on Google Cloud: `ai-mesh-firewall`, asia-south1 (Mumbai), plus asia-northeast1 (Tokyo) for the 2-GPU unit. It tested the four v2.1 fixes that carry the plan — C38 event-loop isolation, C10 owner admission, C43 load-aware guard routing and C36 durable state — against managed stores (Cloud SQL PostgreSQL 16 HA, Memorystore for Valkey 8 HA, Memorystore for Redis Standard HA), two platforms (MIG behind a passthrough NLB, and GKE), a reserved L4 pool, a fair edge bake-off, tenant scale, chaos faults and five contradiction reviewers.
>
> Nothing below is accepted on reading alone. Every figure is a measurement with its conditions and evidence path; where round 2 did **not** measure something, this Part says so and turns it into a gate (§0.5). Figures marked **RC2** were measured on the proven build (manifest `6093c8dd`, images `gateway@sha256:c4c63287…`, `guard@sha256:a7e6165a…`). Figures marked **RC1** were measured on the earlier build (`gateway@69f56e8d…`, `guard@2f2c292c…`) and are kept only where RC2 has no equivalent.
>
> **Precedence:** a **"v3 AMENDMENT"** block beats a **"v2.1 CORRECTION"** block, which beats the original v2 text.
>
> **Evidence:** the round-2 evidence index is `docs/plans/evidence/2026-09-24-runbook-v3-round2/`. The full evidence (154 GB of raw runs plus ≈ 8 GB of curated lane evidence and prototype trees, including the findings ledger §S) is kept on the controller VM's persistent disk. See §0.9 for the exact paths.
>
> **Scope boundary:** round 2 was stopped by the owner at ≈ 17:32Z, and every round-2 test resource in GCP was deleted (§0.9). The items it did not reach are listed in §0.5 as gates. They are not assumed to pass.

## 0.1 Verdict (v3)

**Proceed with the rebuild on the v2.1 architecture. Do not cut over, and do not sign capacity or cost figures, until the six v3-blocking items in §0.3 are implemented in the product and the gates in §0.5 pass.**

**Proven on GCP (RC2 unless stated).** The four structural fixes work:

- **C38 — event-loop isolation.**
  - One g2-standard-24 unit (2 × L4) passes the streaming SLO (C4 = the worst chunk of each stream, measured at the client, holdback excluded) at 200 RPS three times (18.45 / 18.22 / 18.17 ms) and at 250 RPS three times (19.33 / 19.31 / 19.28 ms). It also passes at 250 RPS with the stores on a separate host (19.22 ms).
  - With the fix switched off it fails at 200 RPS (24.3 ms).
  - A worker that is forced to burn CPU (50 ms every second) harms only its own streams: the other workers stay at p99 18.23 ms.
- **C10 — owner admission (CoDel).**
  - The split topology (c4-highcpu-16 gateway + g2-standard-4 guard over TCP) reaches 200 RPS three times (16.0–16.2 ms). The old 12 ms bound reaches 138.
  - Under a 4.3× overload burst, admitted requests stay at p99 17.8 ms. Every shed is a declared 503 with Retry-After, there are 0 FAIL_OPEN, and recovery takes 5 s.
  - The v2.1-as-specified admission (C10 off) admits requests at 23.5 ms and never recovers.
- **C43 — load-aware guard routing.**
  - Queue-wait ratio 0.16–0.20 with routing on vs 0.48–0.50 with it off (RC1, 7/7 runs).
  - Fleet: 3 gateways + 4 guards reach 760 RPS three times, which is 190 per guard (RC1 with a 20 ms owner bound).
  - RC2 fleets with routing on: 2 + 2 = 420, 2 + 3 = 675, 1 + 4 ≥ 570.
- **C36 — Postgres as the durable source of truth.**
  - 0 violations in all 15 RC2 fault runs: store flush ×3 (restored in 0.46–1.25 s), re-hydrator gap ×3, version and replay attacks ×3, Cloud SQL failover ×3 (0 acknowledged writes lost), Valkey planned-maintenance failover ×3.
  - The two redis-py 8 failure modes found in round 2 (D2: pub/sub spin to out-of-memory; D1: a dead pooled socket answers HTTP 500) are fixed and proven at fleet scale.

**Not ready: six defects that round 2 proved live or reproduced, each blocking v3.** See §0.3 for details.

1. **Tenant scale.** State propagation is O(tenants) on the serving event loop. With 10,000 tenants the SLO is exceeded 7×: C4 p99 145–153 ms. With 25,000 tenants it reaches 405–459 ms. Even at 1,000 tenants it sits at the limit (18.9–19.9 ms). *(R2-02)*
2. **Holdback.** The owner-signed limit of "≤ 3 upstream tokens held" is not implemented. A UUID is held for 36 tokens and a base64 blob is held whole, and the rescans block the loop for 17 ms per chunk. *(R2-06)*
3. **Stale state after failover.** A freshly started gateway process accepted a lagging replica: it admitted a revoked key 359× and a killed organisation 365× in 30 s. A committed but unpublished kill switch stayed unenforced for 60 s while `/readyz` was green. One idle database lock stalled re-hydration for 38 s, and two re-hydrators both blocked on it. *(R2-03, R2-04)*
4. **Audit fills the store.**
   - On the shared Valkey, audit streams grew from 1.5 to 6.4 GiB in 51 minutes, on course to evict the guard registrations.
   - The per-org stream cap (2,000,000 records ≈ 5.4 GiB per org) is not a memory bound.
   - `noeviction` alone does not help: the heartbeats are refused, so the registrations expire anyway. *(R2-05)*
5. **Metrics can fail requests.**
   - The shared-memory metric-name directory overflows at about 480 tenants with UUID-style ids. From then on, 40% of new tenants' first requests return HTTP 500 and `/metrics` returns 500 on every worker.
   - Below that, per-org series are silently dropped beyond 512 per worker. *(R2-10)*
6. **Observability cannot see the SLO.**
   - The gateway's own histogram under-reads the client C4 by 3.2–5.8 ms and would call a failing run a pass.
   - Its quantiles are lifetime, not windowed.
   - Untyped metrics leave the GKE autoscaler blind.
   - Counter resets publish negative rates.
   - Audit loss is invisible.
   - Reason codes are merged. *(R2-11)*

**Capacity must be re-baselined on realistic arrivals.** With random (Poisson) arrivals, one L4 behind a split gateway passes at 100 RPS (17.4 ms). It fails at 124 RPS (20.2 ms, 0 sheds), at 150 (0.16% shed) and at 180 RPS (1.7% shed). That is about half the 200 RPS constant-arrival limit. v2.1 recorded Poisson results too (98 RPS per L4 split; 312 RPS on a 3 + 4 fleet), but its headline capacity and cost figures (191 per L4, 520 per $5,000 fleet, $5.80 per RPS-month) use constant arrivals. *(R2-01)*

**Not proven in round 2.** These are gates, not passes:

- autoscaling step tests (E / H);
- drain with long streams;
- rolling a guard update with the reserved pool full;
- cold start with many keys;
- long-prompt capacity;
- client-retry amplification;
- the holdback cap (never implemented);
- TLS cost;
- internet-vantage latency;
- zone loss;
- a store outage behind a health-checked NLB;
- tenant scale after the propagation fix;
- the integrated RC3 re-measure. *(§0.5)*

**Decisions only the owner can make:** listed in §0.7.

## 0.2 Measured facts (round 2)

All figures are C4 p99 unless stated. **infra** = the share of requests with infrastructure errors (sheds, timeouts, 5xx). "3/3" = three repeats of 30 s ramp + 60 s warm-up + 300 s measured with the READY harness (olg d22d2607, synthprov 1ee6bb5f, analyze.py 3874eaac, `c4_client_all --all`, 100% sampling). Harness floors (direct path, no firewall) were re-proven before each campaign: C4 p99 ≤ 0.38 ms, 0 drops.

| Quantity | Measured (conditions) | Evidence (under `evidence/`) |
|---|---|---|
| **C38** unit (g2-standard-24, 2 × L4, RC2 defaults, Tokyo) | 200 RPS ×3: 18.45 / 18.22 / 18.17. 250 ×3: 19.33 / 19.31 / 19.28. Network stores: 250 → 19.22, 200 → 18.24. Worst-worker loop lag p99.9 ≤ 1.68 ms. 39.4–39.7 CPU-ms per request. GPU 30% at 200. | r2-unit/runs/c38r2-* |
| C38 negative control (fix off) | 200 → 24.27 (variant 30.64); 78 → 19.17 (variant 23.91) | r2-unit/runs/c38r2-2* |
| C38 burst (one worker busy 50 ms every second) | Overall p99 65–67 ms (the burst worker's own streams: 111 ms). **Other workers: p99 18.23 / p99.9 19.95.** Fix off: 76.7 overall. | r2-unit/runs/c38r2-3*/c4_detail.json |
| **C10** split (c4-highcpu-16 gateway + g2-standard-4 guard, CoDel, Tokyo), constant arrivals | 150 → 11.24; 175 → 12.30; **200 ×3 → 16.03 / 15.98 / 16.15** (infra 0.008–0.020%); 225 FAIL (31.1 ms, 0.83%). | r2-unit/runs/c10r2-b-* |
| C10 split, Poisson arrivals | **100 PASS 17.43**; 124 FAIL 20.23 (0 sheds); 150 FAIL (26.45, 0.16%); 180 ×3 FAIL (1.70–1.75% sheds). The unit (2 × L4) at Poisson 225 ×3 fails at 22.3 ms with 0 sheds. | r2-unit/runs/c10r2-b-p*, c10r2-a-p225-* |
| C10 overload (140 → 600 → 140 RPS, 120 s burst) | **CoDel:** admitted p99 17.79 ms during the burst; 61% shed, all declared 503s with Retry-After (retry-after-ms 6–11); recovery 5 s; 0 FAIL_OPEN. **C10 off:** admitted 23.52 ms, 2% shed even at the 140 base, never recovers. **AMF_TARGET_P99_MS=1000 arm (knob separation):** 17.47 ms, sustained recovery 50 s. | r2-unit/runs/c10r2-b-ovl*/overload_summary.md |
| C10 rule vs parameter | Bound 12 ms at 150: 2.86% shed. Bound 20 / 30 ms at 150: **0 sheds**. At Poisson 124: bound 20 / 30 → 0.82 / 0.73%; CoDel 0%. At Poisson 180: bound 20 / 30 → 3.42 / 3.22%; CoDel 1.73%. ⇒ The 12 ms parameter caused the long-prompt bias; under random arrivals CoDel sheds least. | r2-unit/runs/u1-b-*, c10r2-b-bound-* |
| **C43** fleet (RC1 images; 3 × c4-highcpu-16 + 4 × g2-standard-4) | With RV_OWNER_QUEUE_MS=20: 600 / 680 PASS; **760 ×3 PASS 14.58 / 15.11 / 14.73 (190 per guard)**; 840 FAIL; Poisson 684 ×3 FAIL (0.60–0.62% sheds). At RC1 defaults (12 ms bound), routing off fails at 520 / 560 / 600 (infra 3.46 / 3.96 / 4.65%). Queue-wait ratio 0.16–0.20 on vs 0.48–0.50 off (7/7). | r2-fleet/plans/main.log, tables/c43-*.md |
| RC2 fleets with routing on | MIG + NLB 1 + 1: **190 ×3** (14.26–14.65); 2 + 2: **420 ×3** (210 per guard); 1 + 4: 570 ×3 (14.45–16.22), 760 FAIL 20.14 (gateway-bound). Chaos fleet 2 + 3: 675 PASS 16.14, 750 FAIL (3.21% sheds). GKE 1 + 1: **190 ×3** (17.73–17.92; 3.3 ms slower than MIG; 16.25 with the DCGM exporter off). | r2-mig/runs, r2-chaos/notes, r2-gke/notes |
| Scaling (RC1, bound 12 ms; single ladder runs) | 1 / 2 / 4 units (gateway + guard pairs): 130 / 280 / 520 RPS constant (C4 11.7 / 12.0 / 12.2 ms). Poisson at 90% fails at every scale (4.69 / 1.87 / 0.90% sheds; bound defect). | r2-fleet/plans/main.log |
| **C36** durable state (RC2, r2-state campaign) | 0 violations in all 15 runs. FLUSHALL restore 0.456 / 1.249 / 0.610 s (bound ≈ 2.05 s). Re-hydrator gap 10 s: tenants get only declared 503s (≈ 1,040 per run); first 200 at 11.6–11.9 s. Cloud SQL failover ×3: 0 × 503 to tenants, 0 acknowledged writes lost, 0 committed-but-unacknowledged. Valkey planned-maintenance failover ×3: ≈ 140 declared 503s, store back in ≈ 4.3 s. | r2-state/runs/rc2-*/analysis/verdict.json |
| C36 gaps (RC2, live on a Cloud SQL + Memorystore mini-stack) | **SP1** — a fresh process on a lagging replica admitted killed org K3 365× and revoked key R3 359× in 30 s (re-hydrator stopped), and still 10 admits with it running. **SP2** — `ok_publish_pending` writes invisible for 60.3 s (1,448 / 1,447 admits) with `/readyz` green in 1,206 / 1,206 polls. **H7** — one idle `FOR UPDATE` held re-hydration for 38 s (global 503 from +4.9 to +39.5 s); two re-hydrators both waited. | r2-state RAW/runs/mini-* |
| Reference fix for the C36 gaps (prototype patch `rc3-state-p0`, real PG16 + Valkey) | SP1: RC2 admitted the revoked key 146× and served the killed org 146× in 8 s (10 / 19 leaks with a re-hydrator running) → **0**. SP2: stale 10 s → fail-closed 3.0 s after the last stamp. H7: kinds restored in 0.54–0.79 s under a held lock; a queued writer fails `LockNotAvailable` in 2.25 s. **Costs as delivered:** both re-hydrators down → global 503 after 3 s; Postgres frozen 10 s → ≈ 7.4 s global 503 (RC2: 0); a 3.8 s store blip → ≈ 1.2 s global 503 (RC2: 0). | r2-fix-c36m/rc3/functional/RESULTS.txt |
| **Tenant scale** (RC2; 1 gateway × 12 workers + 1 guard; 120 RPS; 2,000 active keys; own Valkey + Cloud SQL) | **3 tenants:** 11.5–12.6 ms. **1k orgs / 2k keys:** 18.9–19.9 ms; loop lag p99.9 6.7–7.0 ms; kill-switch refresh 8.5–8.7 ms per 500 ms per worker; writer publish p50 30–76 ms. **10k / 20k:** **145–153 ms**; loop lag p99 60–64 ms; refresh 74–76 ms; publish p50 114–623 ms; infra ≤ 0.25%. **25k / 50k:** **405–459 ms** (unbounded while keys are written; infra 1.78%); refresh 203–223 ms; publish p50 up to 1.72 s. | r2-scale/levels/N*.analysis.out |
| Metrics directory overflow (RC2) | 480 production-shaped plans (`s-<uuid5>` / 40-hex versions): first requests of new tenants → **40 / 100 HTTP 500**; `/metrics.json` 500 on every worker afterwards. At 2,000 short-id orgs, series beyond 512 per worker are silently dropped. | r2-state RAW/runs/mini-shm-default-1 |
| Budget lease on the request path (RC2 fleet, 45 s store partition) | 211 × 503 `shared_state_unavailable` from +0.15 s, inside the declared 5 s RAM window: a lease refill needs the store. | r2-chaos/notes/decisions.txt |
| Store partitions (RC2 fleet, 450 RPS, 3 partitions per run) | 30 s → infra 5.44%; 60 s → 10.46%. **0 FAIL_OPEN, 0 revoked-key or killed-org admits.** Errors end 2–3 s after the heal; C4 is back inside the SLO **10–180 s** after the heal (not diagnosed). | r2-chaos RAW/runs/m-f5-* |
| D2 / D1 (redis-py 8) | Fleet smoke with a 45 s partition at 150 RPS: 24 / 24 workers survive; RSS +1.6 / +3.2 MB; 0 × 500; Retry-After 2 on all 3,414 declared 503s. | r2-fix-d2, r2-chaos |
| Audit memory (H1) | Shared Valkey (10.4 GiB, volatile-lru): **1.48 → 6.42 GiB in 51 min** at round-2 lane rates; the guard registrations are the only TTL keys. Per-org cap: 2,000,000 records ≈ 5.4 GiB at ≈ 2.7 KB per record. **Reference fix `rc3-audit-mem-v1`:** registrations 5/5 and 0 refused writes vs RC2 0/5 and 27/27 refused. `noeviction` alone also fails: heartbeats are refused, so the registrations expire. | controller-r2/kv_trim_shared_valkey.txt, r2-fix-d2/rc3 |
| Observability (M1 / M4 / M2 / M3) | **M1:** gateway `t_input` p99 under-reads client C4 by **3.2–3.9 ms** at 200–250 RPS and 4.7–5.8 ms at 300; the lifetime p99 moved 0.000 ms over 257 s of steady load. **M4:** untyped exposition lands as `…/unknown`, so an HPA that asks for the gauge type gets a 400 (blind); a restarted owner publishes `owner_requests_per_s` = −305.2 (true 70.3). **M2:** `rejected_503` merges 9,241 kill-switch 503s with 8 store-outage 503s. **M3:** a flush erased 36% of the audit records while `audit_completeness_ratio` read 1.0. | r2-unit/phaseF-m1, r2-gke/runs/m4c, r2-fix-d2/rc3-obs, reviewer-observability |
| Holdback (H4, RC2 code, replayed) | Longest hold, in upstream tokens: prose 2, JSON 7, file paths 15, markdown URL 15, sha256 34, UUID 36; base64 2 / 8 / 16 KB held whole, with 0.46 / 1.9 / 17 ms loop blocks per chunk (O(n²) rescans). C4 excludes holds by construction, and the headline output never holds more than 2 tokens, so no capacity run could see this. | reviewer-hidden-failures/r2p1/p04_holdback_rc2.out |
| **Edge** (fair bake-off: every edge driven from both loadgens, the same backends, one zone) | External-IP edges (direct / nginx / NLB → nginx / NLB; 100 / 300 / 600 RPS): Δ C4 p99 vs internal −0.12 … +0.27 ms (medians); Δ p99.9 −0.9 … +2.7 ms; max ≈ 40.3–40.7 ms (the lost-segment signature); 0–33 streams > 20 ms per run. Internal edges: max ≤ 17 ms, 0 streams > 20 ms. **Regional ALB: +8–10 ms p99**, 16–142 streams > 20 ms per run. 30-min soak: every edge < 1 ms at p99. | r2-edge/tables/eb_table.md |
| NLB balancing | Per connection: with recycling off, a scaled-out gateway got 0–0.2 req/s while the old one kept 40 (time-to-balance: never). MIG scale-in: NLB drain 300 s **before** VM deletion and SIGTERM, then RV_DRAIN_ACCEPT_S 15 s + RV_DRAIN_S 60 s ⇒ streams up to ≈ 375 s survive. | r2-mig/notes |
| Autoscaler signal | Cloud Monitoring points are visible 1–5 s after the push; ≈ 46 s of the 53 s scale-out decision time is the autoscaler's evaluation. Guard create → ready ≈ 90 s with a baked image (VM 34 s + boot 36 s + owner warm-up ≈ 20 s). | r2-mig/notes |
| Managed stores | Cloud SQL HA failover: 9.8–10.4 s probe outage **unloaded**, **11.6–15.5 s write/serve gap loaded**, 0 acknowledged writes lost. Valkey HA planned maintenance: 3.6–3.8 s outage, 6–8 min after the call. **Valkey has no manual failover** (no API method in v1 / v1beta / v1alpha; `FAILOVER` is a blocked command). Redis Standard HA forced data-loss failover: 8.7 s. Same-zone RTT 0.05–0.06 ms p50, cross-zone 0.30–0.35 ms. TLS + IAM adds 0.02–0.03 ms per op; redis-py builds an SSLContext per connection (12.4 ms loop block). | r2-infra/raw/drills, valkey_failover_api_recheck.txt |
| Cross-zone store round trips | ≈ 1e-4 of store round trips hit Linux's 200 ms minimum TCP RTO (gateway in zone c, Valkey primary in zone a). Any request-path store call on such a round trip exceeds RV_STORE_TIMEOUT_MS 25 and fails. | r2-scale/levels/N0 |
| **L4 supply** (Mumbai) | One persistent create loop: 203 on-demand attempts → **10 successes (4.9%)**; zone a 1/89, b 7/93, c 2/21 ⇒ ≈ 2 new L4 per hour. A 15-slot pool took ≈ 5 h. Automatic reservations backed by running VMs survive VM deletion and stock-outs; idle reserved slots bill at $0.7358 per hour each. Quotas were raised on request in ≈ 5 min (C4 800 vCPU, L4 32). | r2-infra/l4-pool.jsonl |
| GPU alternatives | CPU guard: **not feasible** against the ≤ 6 ms per-window need. Fastest configuration: 14.4 / 14.7 ms p50 / p99 per 512-token window (c4d-highcpu-32, OpenVINO int8, which fails decision parity at 99.37%); the exact gather-free graph rewrite (bf16, parity 99.95%) still takes 17.6 ms p99 (C4D) / 23.5 ms (C4 AMX); cost per token 6.5–7.5× the L4. OpenVINO bf16 turns every padded window into NaN (mask constant; fix −1e30). T4: 0.40× L4 throughput, 5.53 ms p99 per window, 2.93× L4 $/token. G4 (RTX PRO 6000): batch 1 p99 1.06–1.07 ms, 485–488k tok/s (2.0× L4); at ≤ 6 ms p99, 4.2× L4 throughput, Spot 0.75× / on-demand 1.9× L4 $/token; on-demand quota is 0 in every region. DWS flex-start: 1 L4 after 37.4 min. Tokyo and Seoul had on-demand L4 in both snapshots. | r2-alt/alternatives.json, gpu_sweep.json |
| Console (v1, customer regression; 3 browsers × 3 viewports) | 1,026 results, 19 bugs. **R2C-01 CRITICAL:** a module page or `/demo/` login silently wipes the org's blocked keywords. **R2C-02:** adding a model narrows an "all models" allowlist, and deleting it locks the org out. **R2C-03:** role-less members can write security config and read the simulator key. **R2C-04:** control OOM at ≈ 9 users. **R2C-05:** RAG page crash. | r2-console (report artifact KNE98QL3uc7rX82fFPacuW) |
| Round-2 spend | ≈ $435 at list prices (30-min burn samples of $21–44 per hour, about 05:00–17:45Z). ≈ $120 of it was idle burn after the lane agents stopped at 14:05–14:15Z. **Estimate:** the authoritative figure is Cloud Billing for 2026-09-24. | controller-r2/teardown |

## 0.3 Corrections register (v3)

The impact scale and "fix before" have the same meaning as in §0-A.3. **Six items are v3-blocking (★).**

| ID | Where | What round 2 proved (evidence) | Correction | Impact | Fix before |
|---|---|---|---|---|---|
| R2-01 | §1.3, §10.6, GW20, T01 | v2.1's headline capacity and cost figures use constant arrivals; its Poisson results (98 per L4 split, 312 on 3 + 4) were recorded but not used as the basis. On RC2 with CoDel, Poisson is still about half of constant: the split (1 L4) passes 100 RPS and fails 124. The 2-GPU unit fails Poisson 225 (its constant limit is 250). The RC1 3 + 4 fleet fails Poisson 684 (constant 760). | Size and sign capacity on Poisson arrivals, measured at fleet scale (§0.6). Publish constant-arrival limits only as upper bounds. GW20's knee procedure adds a Poisson ladder in steps of ≤ 10% and three repeats at the pass point. | HIGH | T01 re-sign, GW20 |
| R2-02 ★ | §2.1, §10.5.2, GW05, GW06 | Per worker, RC2 re-verifies the whole kill-switch set every 500 ms and reconciles the whole plan index every 1 s on the serving loop, and every write republishes a whole kind in one MULTI. **Measured:** at 1k tenants the SLO edge (19.9 ms); at 10k, 145–153 ms; at 25k, 405–459 ms; publish p50 up to 1.72 s; any key write empties every worker's identity cache. | New card **GW05c**: per-record versions with a change feed so refresh cost is O(changes), not O(tenants); per-record publish (never a whole-kind rewrite); per-key identity-cache invalidation; nothing proportional to the tenant count on a serving loop. Gate: C4 p99 < 20 ms at 10k and 25k tenants in all four write phases. | CRITICAL | GW05 |
| R2-03 ★ | GW05, §10.5.2 | The per-process version floor lives in RAM and starts at zero. A fresh process on a lagging replica serves revoked or killed state (SP1: 359 / 365 admits in 30 s). Unpublished committed writes never fail closed (SP2: 60 s). | New card **GW05b**: a signed freshness stamp written by the re-hydrator each round; a process trusts only a stamp from a round that started after it did; fail closed per kind when the stamp is older than the bound. Tune the bound to at least the RAM-serving window (5 s), and keep stamping during a Postgres outage while the store is not behind the last verified versions, so a Cloud SQL failover does not become a global outage. The reference patch cost ≈ 7.4 s of 503 without this (§0.2). | CRITICAL | GW05 |
| R2-04 ★ | GW05, §3 | One idle writer transaction holding `FOR UPDATE` stalls re-hydration indefinitely, and two re-hydrators block on the same lock (38 s global 503). | GW05b also sets `lock_timeout`, `statement_timeout`, `idle_in_transaction_session_timeout` and `tcp_user_timeout` on every control-plane connection; uses a lock-free REPEATABLE READ snapshot for publishing (reference: kinds restored in 0.54–0.79 s under a held lock); runs ≥ 2 re-hydrators in ≥ 2 zones (proven safe concurrently); and never lets an idle-stop policy touch them. | HIGH | GW05 |
| R2-05 ★ | GW14, §1.3 | Audit shares the hot store. Live: 1.48 → 6.42 GiB in 51 min on a 10.4 GiB instance, heading for eviction of the guard registrations (the only TTL keys). The per-org cap (2 M records ≈ 5.4 GiB) is not a memory bound. `noeviction` alone also fails. | New card **GW14c**: a global audit memory budget in the store (reference: per-org approximate MAXLEN derived from the budget; exact trim counter; store-policy self-check; memory gauges), a durable audit sink with an acknowledged-vs-durable high-water mark, and an alarm below the eviction point. | CRITICAL | GW14 |
| R2-06 ★ | §1.2, GW12, GW13 | The signed "holdback ≤ 3 upstream tokens" is not implemented: UUID 36, sha256 34, URL 15 tokens; base64 held whole; 17 ms loop blocks per chunk at 16 KB. C4 hides holds by construction. | New card **GW12b**: a hard hold cap in upstream tokens (default 3); bounded pattern lengths; O(window) tail rescans; a held-tokens-per-stream histogram; documented detector trade-offs for patterns longer than the cap, with a declared outcome when a match completes on already-released text; a capacity run with realistic output shapes (URL / UUID / base64 / code). | CRITICAL | GW12 |
| R2-07 | GW06, GW19, §10.5.4 | CoDel owner admission is the right rule (see §0.2). The 12 ms instantaneous bound caused prompt-size-biased sheds. The gateway-side per-worker cap as specified (C10 off) breaks the SLO under overload and never recovers. | GW19 specifies owner-level CoDel admission (target 5 ms, interval 100 ms, hard cap 60 ms, guard wait budget max(100, cap + 40) ms). Admitted work is answered late, never abandoned. The per-worker guard cap of GW03 is removed. | HIGH | GW19 |
| R2-08 | GW19 | Sheds carry retry-after-ms 6–11 ms (measured). The OpenAI SDK honours retry-after-ms in (0, 60] s with two retries, so shed requests return within milliseconds. Amplification was predicted at ≈ 2.6× and **not measured live** (gate G-06). | Minimum Retry-After on overload sheds (propose ≥ 1 s, jittered) and an `x-should-retry` policy; test with the SDK-semantics retry client (harness v2.3). | HIGH | GW19 |
| R2-09 | GW06 | Budget-lease refills run on the request path, so a store outage fails large prompts from +0.15 s instead of after the declared 5 s. | Low-watermark asynchronous refill off the request path; per-path outage semantics (identity / kill switch / plan from RAM for the declared window; budget = remaining lease, then a distinct `budget_unavailable` reason); lease records tagged with the budget generation and revoked on change (C29 unchanged). | HIGH | GW06 |
| R2-10 ★ | GW14, §2.1 | Metric registration runs on the request path and can raise (a 64 KiB name directory): 40% of new tenants' first requests get 500 at ≈ 480 UUID-named tenants; per-org series are silently truncated at 512 per worker. | New card **GW14d** (with R2-11): metrics never fail a request (size check before insert, overflow degrades to process-local and is counted and exported); per-tenant label cardinality bounded (top-K + "other"); directory sized from the slot counts. | CRITICAL | GW14 |
| R2-11 | GW14, GW14b, §1.2 | The server-side view cannot certify the SLO (under-read 3.2–5.8 ms; lifetime quantiles); untyped exposition blinds the GKE HPA; counter resets publish negative rates; `rejected_503` merges reasons; `plan_unavailable` is not counted; FAIL_CLOSED is counted as a policy BLOCK; audit loss is invisible. | GW14d: a windowed per-request add-on histogram with the C4 definition and native Prometheus buckets; a held-tokens histogram; `# TYPE` on every family; reset-safe rates (no baseline ⇒ omit); `rejected{status,reason}`; posture counters separate from policy BLOCK; a rate-limited structured event log; `/readyz` fails when the metrics shared memory is missing. The production SLO comes from a black-box synthetic client and provider canary (C39 retained). Reference patch `rc3-obs-v1` covers the typing, reset and GPU-busy parts. | HIGH | GW14 |
| R2-12 | §10.3, GW16b | Fair bake-off: a passthrough NLB adds nothing beyond external addressing; the regional ALB adds 8–10 ms p99. Every external path shows rare ≈ 40 ms lost-segment tails (≤ 0.02% of streams in-region); internal paths do not. The NLB balances per connection. | Edge = regional external passthrough NLB → gateways (option A), TLS terminated in the gateway tier (cost not measured: gate G-10), connection recycling on (RV_CONN_MAX_AGE_S; 30 s in the MIG S3 runs). The SLO is certified as the firewall-added delta against a direct path over the same network. Internet-vantage validation is a pre-production gate. ALBs are rejected for token streams. | HIGH | GW16b |
| R2-13 | §10.5.2, GW05, T21 | Store-failure semantics measured (§0.2): Cloud SQL failover has no data-plane impact with RC2's C36; Valkey planned maintenance costs ≈ 140 declared 503s; Valkey cannot be failed over manually, so a data-loss failover drill needs Redis Standard HA or a flush drill. | Publish these as the declared failure behaviour. Chaos drills use flush, partition and Redis forced failover. Maintenance windows for Cloud SQL and Memorystore must not overlap (E2-08 is gated). | MEDIUM | T21 |
| R2-14 | GW06, §3 | ≈ 1e-4 of cross-zone store round trips hit the 200 ms minimum TCP RTO, over the 25 ms request-path timeout. | Keep request-path store calls rare (R2-09); retry one idempotent read on timeout; place gateways in the store primary's zone where possible; test `rto_min` on the store route (untested). | MEDIUM | GW06 |
| R2-15 | §1.3, §7, T20 | L4 on-demand is routinely stocked out in Mumbai (4.9% create success for one loop; ≈ 2 per hour). Reservations backed by running VMs survive stock-outs; idle reserved slots bill. CPU guard infeasible; T4 degraded fallback; G4 blocked by quota. | New card **GW25** (GPU capacity operations): a reserved L4 baseline sized to the signed Poisson capacity plus measured headroom; a capture procedure; L4 Spot for surplus headroom only; T4 with a baked sm_75 engine as degraded fallback; DWS as a slow queue; a whole-cell failover region (Tokyo / Seoul), never a split gateway / guard across regions. | HIGH | T20 |
| R2-16 | GW20, T20 | Platforms: equal per-guard limits (190) on MIG + NLB and GKE, with GKE 3.3 ms slower (DCGM exporter ≈ 1.7 ms); MIG + NLB drains 300 s before SIGTERM; the autoscaler adds ≈ 46 s of evaluation; GKE's HPA needs typed metrics. **The step tests themselves were not run.** | Primary platform: MIG + regional passthrough NLB. GKE remains qualified at 190 per guard with DCGM off or tuned. Autoscaling claims wait for gate G-01. | HIGH | GW20 |
| R2-17 | GW05 | C36 as designed (Postgres truth, signed versioned records, manifests, fail-closed 503, re-hydrator) had 0 violations in 15 RC2 fault runs. | Keep the design; add R2-02 / 03 / 04. | — | — |
| R2-18 | GW19, T21 | After a store partition heals, errors stop in 2–3 s but C4 returns inside the SLO only after 10–180 s (not diagnosed). | GW19 adds a post-heal latency-recovery test with a bound; diagnose (lease refill storm, cold identity caches, audit backlog). | MEDIUM | GW19 |
| R2-19 | GW06 | redis-py 8 keepalive ETIMEDOUT is read as "no message", so `listen()` spins to out-of-memory (D2); a dead pooled socket answers 500 (D1). Fixed and proven (§0.2). | Keep the store-connection boundary and push listener as a GW06 requirement, with the partition regression test. | HIGH (fixed) | GW06 |
| R2-20 | §3, T20 | systemd-logind `RemoveIPC=yes` wipes a login user's `/dev/shm` (3.2% `/readyz` 503); editing shared harness scripts in place killed a running measurement. | Gateways run as a system user or in containers; the harness is released immutably (as bin/v2.N) and runs from frozen copies. | MEDIUM | T20 |
| R2-21 | GW20, §10.10 | The READY harness could not generate multi-key, long-prompt, retrying, TLS or secret-shaped outputs. Harness v2.2 / v2.3 adds multi-key (up to 50k keys), long-prompt corpora (1 / 5 / 25% of 4–7k tokens), SDK / aggressive retry modes and input-size strata (floors 0.29–0.34 ms, 0 drops). TLS client and output kinds are not built. | GW20 capacity runs use v2.3 or later, with multi-key, long-prompt, retry and Poisson strata. | HIGH | GW20 |
| R2-22 | GW14b, GW23, UI | Console regression: R2C-01 CRITICAL keyword wipe (code-verified; production exposure unverified), R2C-02 allowlist lockout, R2C-03 role-less config writes plus a plaintext key, R2C-04 control OOM at ≈ 9 users, R2C-05 RAG page crash. | Pre-cutover blockers on the revamp release checklist (owner decision: production is updated once, after the revamp). | CRITICAL | GW23 |
| R2-23 | GW08, §10.5.4 | C43 push cost: 72 pushes per request at 36 workers, +2.3 gateway CPU-ms per request. Push coalescing was deferred from RC2; it exists only in the unreleased RC3 base (`523ecb8`) and was never measured. | GW08 / GW19 bound push rate per owner (coalesced), with a microbenchmark and a targeted knee re-measure. | MEDIUM | GW19 |
| R2-24 | §7, program | Test-environment waste: ≈ $120 idle burn after agents stopped; idle reserved GPU slots bill; an audit backlog nearly took down shared stores. | Every validation environment runs an idle watchdog, burn-rate monitor, store-memory alarm and audit guard, plus an owner-visible teardown rule that survives agent or session loss. | LOW | T20 |

## 0.4 New and changed cards (v3)

Each card follows the §10.9 format (why, implementation, live acceptance tests, exit, evidence, rollback). Rows below give the binding content. Prototype patches under `docs/plans/evidence/2026-09-24-runbook-v3-round2/patches/` are **reference implementations and tests**, not product code.

### GW05b — State freshness and HA re-hydration (R2-03, R2-04)

| Field | Value |
|---|---|
| Phase | Core state |
| Depends on | GW04, GW05 |
| Required by | GW06, GW15, GW20, GW21 |
| Objective | No process ever serves revoked, killed or stale-version state because of replica lag, a missing publish or a stuck re-hydrator; and store or Postgres blips do not become global outages. |

**Implementation.**

- The re-hydrator signs a stamp each round: `{round_start, verified_at, per-kind versions}`. A process's version floor comes from the stamp, never from zero, and it trusts only stamps from rounds that started after the process did.
- Fail closed per kind when the stamp is older than `RV_STATE_FRESH_MS` (default = `RV_KS_STALE_MS`, 5 s).
- During a Postgres outage, keep stamping while no kind in the store is behind the last verified versions, for at most `RV_STATE_PG_GRACE_MS`. Size it to at least the measured loaded Cloud SQL gap (15.5 s), plus margin.
- Postgres connections set lock, statement, idle-in-transaction and TCP timeouts.
- The publish snapshot is a lock-free REPEATABLE READ read.
- Run ≥ 2 re-hydrators in ≥ 2 zones, and alarm when an `ok_publish_pending` write is older than one period.

**Live acceptance tests.**

| Test | Procedure | Pass |
|---|---|---|
| L05b-1 | Lagging-replica failover. Start a fresh gateway process at +0.5 s; probe the revoked key and killed org at ≥ 24 / s. | 0 admits, with and without a re-hydrator running. |
| L05b-2 | Writer store path blocked, so the write is `ok_publish_pending`; re-hydrator stopped. | Fail-closed within the stamp bound; enforcement ≤ 1 s after a re-hydrator returns. |
| L05b-3 | Idle `FOR UPDATE` held for 60 s, then FLUSHALL. | Every kind restored within the declared flush bound (RC2: ≈ 2.05 s) while the lock is still held; a queued writer fails fast (reference patch: restored in 0.54–0.79 s; writer failed in 2.25 s). |
| L05b-4 | Cloud SQL failover (≈ 11–16 s) at 60% load. | 0 × 503 for valid tenants. |
| L05b-5 | Valkey maintenance blip (3.8 s). | 0 × 503 beyond RC2's baseline. |
| L05b-6 | Both re-hydrators killed. | Global fail-closed at the declared bound; recovery ≤ 1 s after one returns. |

### GW05c — Incremental state propagation and tenant-scale certification (R2-02)

| Field | Value |
|---|---|
| Phase | Core state |
| Depends on | GW05, GW05b |
| Required by | GW06, GW14d, GW20 |
| Objective | Serving cost and C4 are independent of the number of tenants, keys and kill-switch records. |

**Implementation.**

- Per-record versions plus a change feed (a stream or sorted set per kind). Workers apply deltas; no periodic full re-verify or reconcile runs on a serving loop.
- Writes publish only the changed records. A whole-kind MULTI is forbidden.
- The identity cache is invalidated per key, not by a global epoch.
- Single-flight fetches on a cold cache.
- A bulk-onboarding path.
- Metric cardinality independent of tenant count (with GW14d).

**Live acceptance tests.** L05c-1: re-run E2-01 exactly as in round 2 (1 gateway × 12 workers + 1 guard, 120 RPS, 2,000 active keys; phases A steady, B key writes, C plan writes, D kill-switch writes, each every 5 s) at 1k, 10k and 25k tenants.

| Criterion | Pass (every level and phase) |
|---|---|
| C4 p99 | < 20 ms |
| infra | ≤ 0.1% |
| Loop lag p99.9 | ≤ 5 ms |
| Store publish per write | ≤ 25 ms |
| Kill-switch refresh cost | Flat with tenant count, within ±20% of 1k |

Round-2 baseline on RC2 at 10k tenants: 145–153 ms.

### GW12b — Bounded holdback (R2-06)

| Field | Value |
|---|---|
| Phase | Egress |
| Depends on | GW12 |
| Required by | GW13, GW20 |
| Objective | Implement and prove the owner-signed holdback bound. |

**Implementation.**

- `RV_HOLDBACK_MAX_TOKENS` (default 3).
- Pattern lengths bounded; rescans limited to a tail window, so per-chunk work is O(window).
- A held-tokens-per-stream histogram.
- A written trade-off for credential and PII patterns longer than the cap (AWS keys, tokens, JWTs, emails, card numbers), with the declared outcome when a match completes on already-released text: redact the remainder, or terminate with the terminal error frame.

**Tests.**

- **L12b-1 — replay benchmark.** Content classes: prose, URL, UUID, sha256, paths, JSON, code, base64 2 / 8 / 16 KB. Pass: max hold ≤ 3 tokens; per-chunk loop block ≤ 0.5 ms.
- **L12b-2 — capacity run.** 200 RPS headline with 1% base64-16 KB / UUID-heavy outputs. Pass: other streams' C4 p99 unchanged (Δ ≤ 0.5 ms).
- **L12b-3 — detector regression.** Real patterns split across chunks. Pass: every match produces its declared outcome.

### GW14c — Audit durability and store memory budget (R2-05)

| Field | Value |
|---|---|
| Phase | Observability |
| Depends on | GW14 |
| Required by | GW20, GW21 |
| Objective | Audit can never evict or starve security state; audit loss is visible and bounded. |

**Implementation.** Reference: `rc3-audit-mem-v1`.

- A global audit budget (`RV_AUDIT_STORE_BUDGET_MB`, default 0.5 × maxmemory) turned into per-org approximate MAXLEN.
- An exact `audit_trimmed_records` counter.
- A store-policy self-check (`store_policy_unsafe`).
- Memory gauges sampled off the request path.
- A durable sink (GCS, BigQuery or Postgres; choose and price at GW14c) with an acknowledged-vs-durable high-water mark and `records_lost`.
- Records for sheds and admission rejects (C40).
- An alarm at 60% of store memory.

**Tests.**

- **L14c-1 — 64 MB store, audit flood.** Pass: 5/5 registrations, 0 refused publishes or lease refills (reference: pass).
- **L14c-2 — sustained run.** 1 h at the fleet's Poisson knee. Pass: memory flat below the alarm; 0 evictions; durable sink complete against admitted requests.
- **L14c-3 — flush.** Pass: loss is counted exactly, never shown as 1.0 completeness.

### GW14d — Observability contract v3 (R2-10, R2-11)

| Field | Value |
|---|---|
| Phase | Observability |
| Depends on | GW14, GW05c |
| Required by | GW20, T20, UI05 |
| Objective | The operator sees the SLO, the reasons and the losses; monitoring never breaks serving. |

**Implementation.** As R2-10 / R2-11. Reference: `rc3-obs-v1` covers `# TYPE`, reset-safe rates and windowed GPU busy (suite 158 / 158; its new tests fail on RC2 and pass patched).

**Tests.**

- **L14d-1 — server vs client view.** Windowed per-request add-on histogram p99 vs client C4 over the same window. Pass: within 0.5 ms.
- **L14d-2 — tenant ids at scale.** 2,000 and 20,000 UUID-named tenants at default and raised slots. Pass: 0 × 500 from requests or `/metrics`; drops counted.
- **L14d-3 — restarts.** Owner and worker SIGKILL / restart. Pass: no negative or zero window.
- **L14d-4 — GKE autoscaler.** Pass: the HPA resolves the typed gauge metrics (ScalingActive).
- **L14d-5 — reason codes.** Every client error class has a counter and a log line.

### GW20b — Capacity certification on realistic traffic (R2-01, R2-16, R2-21)

GW20 amendment, binding. Knees are signed only from 3/3 repeats with Poisson arrivals at fleet scale (≥ 3 gateways + ≥ 4 guards), using harness v2.3 or later with these strata:

- multi-key (≥ 2,000 active keys over ≥ 200 orgs);
- long prompts (≥ the signed T01 share of 4–7k-token prompts);
- SDK retry clients (≥ the signed share);
- secret-shaped outputs (with GW12b);
- tenant count ≥ 10k (with GW05c).

Constant-arrival limits are published as upper bounds. GPU busy (windowed), owner queue wait, loop lag and the reason mix are reported per run.

### GW25 — GPU capacity operations (R2-15)

| Field | Value |
|---|---|
| Phase | Operations |
| Depends on | T20, GW20b |
| Required by | GW22, GW23 |
| Objective | The signed capacity is backed by GPUs that exist in Mumbai when needed. |

**Implementation.**

- Reserve the L4 baseline: SPECIFIC reservations in an isolated project, or automatic reservations with a documented capture procedure.
- Size the pool as signed Poisson capacity ÷ the measured per-guard Poisson limit, plus N+1 per zone.
- Headroom policy: L4 Spot for surplus only, with a preemption drain.
- Degraded fallback: T4 with a baked sm_75 engine.
- DWS resize requests as a slow queue.
- Whole-cell failover in Tokyo or Seoul.
- Rolling guard updates without a spare slot: either price one surge slot per zone or accept a measured capacity dip (gate G-03).

**Tests.**

- **L25-1 — capture.** Capture into a reservation during a stock-out.
- **L25-2 — Spot preemption.** At 60% load. Pass: 0 FAIL_OPEN; sheds declared.
- **L25-3 — T4 fallback.** Pass: capacity and C4 measured and published.
- **L25-4 — zone loss.** Against the pool shape. Pass: post-loss capacity equals the surviving reservations.

### Changed cards (v3 AMENDMENT blocks inline)

GW05, GW06, GW08, GW12, GW14, GW16b, GW19 and GW20; §1.2, §1.3, §2.1, §3, §10.3, §10.6 and §10.11.

## 0.5 Open gates (not measured in round 2)

Each gate must pass before the capacity, cost or cutover claims it names are signed. The run specifications are the round-2 lane plans (evidence: CONTROLLER_BULLETIN B37, B38 and the reviewer reports).

| Gate | What it proves | Run specification (summary) | Pass | Card |
|---|---|---|---|---|
| G-01 | Autoscaling holds the SLO through a 3× step | MIG + NLB, E (≈ 70% target) and H (≈ 33%) configs, 3 × each, plus a Poisson arm and a recycling-off arm; report time-to-capacity, shed volume and duration, admitted C4, and $/h | Admitted C4 p99 < 20 ms throughout; infra ≤ 0.1% (H); declared sheds only (E) | GW20b |
| G-02 | Scale-in and drain with long streams | 5% of SSE at ITL 750 ms (≈ 300 s) + 1% 16k-token streams; step-down while they are live | 0 dropped streams, or a declared maximum stream duration covered by the drain window | GW19 |
| G-03 | Guard rollout with the pool full | Roll a new guard revision at 60% load; surge 1 / unavailable 0 vs surge 0 / unavailable 1 | Completes within a declared window, infra ≤ 0.1% | GW25 |
| G-04 | Cold start with many keys | 3× step with 2,000 keys over 200 orgs | 0 × 503 `shared_state_unavailable` on new gateways | GW05c |
| G-05 | Long-prompt capacity | Headline + 1 / 5 / 25% of 4–7k-token prompts (harness v2.2 corpora), Poisson | C4 < 20 ms overall and for ≤ 1,024-token prompts; long / short shed ratio ≤ 2; publish the knee under the 5% mix | GW20b |
| G-06 | Client-retry amplification | Overload 140 → 600 → 140 with 30% SDK retry clients (+10% aggressive) | Admitted C4 < 20 ms; recovery ≤ 5 s; total / original offered ≤ 1.5 | GW19 |
| G-07 | Holdback cap | L12b-1 … L12b-3 | As GW12b | GW12b |
| G-08 | Tenant scale after the fix | L05c-1 | As GW05c | GW05c |
| G-09 | Integrated build re-measure | RC3-equivalent build with all v3 fixes: split knee (constant + Poisson), fleet 3 + 4 knee, C43 coalescing CPU | No regression vs RC2 beyond 5%; C43 push CPU ≤ 0.5 ms per request | GW20b |
| G-10 | TLS cost at the edge | TLS 1.3 at the gateway tier (and at nginx for comparison), tickets on / off, 100 / 300 / 600 RPS × 3, plus a 1,000-connection handshake storm | Δ C4 p99 ≤ 1 ms at 600; the storm keeps C4 < 20 ms | GW16b |
| G-11 | Internet-vantage latency | Client outside GCP (India ISP) vs a direct path | Published delta; firewall-added delta < 20 ms p99 | GW16b |
| G-12 | Zone loss against the pool shape | Stop one zone's VMs plus host-level DROP at 60% load; MIG distribution ANY vs BALANCED | 0 FAIL_OPEN; post-loss capacity = surviving reservations | GW25 |
| G-13 | Store gap behind a health-checked NLB | 60 s partition ×3 and a forced failover ×3 behind the NLB with `/readyz` health checks | Clients see only declared 503s, no connection failures | GW16b |
| G-14 | Postgres + store overlap | Cloud SQL failover + FLUSHALL at +3 s, ×3 | 0 violations; gap ≤ SQL outage + 2.1 + 0.5 s | GW05b |
| G-15 | Post-heal latency recovery | f5 partition 30 / 60 / 120 s | C4 inside the SLO ≤ 10 s after the heal (round 2: 10–180 s) | GW19 |
| G-16 | Output channels scanned (v2.1 C21) | logprobs / refusal / reasoning / tool arguments / structured outputs | Every text-bearing field scanned; REDACT effective | GW13 |
| G-17 | In-flight kill-switch semantics | 2,000-token stream; engage the org's kill switch at +2 s | Matches the owner's decision (§0.7-1) | GW12 |
| G-18 | Console blockers | R2C-01 … R2C-05 fixed and regressed in 3 browsers × 3 viewports | 0 regressions | GW23 |

## 0.6 v3 capacity and cost basis

Prices come from the Cloud Billing Catalog API (fetched 2026-09-23), on-demand, asia-south1, 730 h per month, excluding egress (the owner's decision: egress is reported separately; round 1 measured ≈ $17.4 per sustained RPS-month).

| Unit | $ / month (on-demand) | Committed use | Measured capacity per unit (RC2) |
|---|---|---|---|
| Guard: g2-standard-4 (1 × L4) + 200 GiB pd-balanced boot | 537.12 + 24.00 = **561.12** | 1-yr 338.38 + 24 = 362.38; 3-yr 241.70 + 24 = 265.70 | **Poisson: 100 RPS (pass; 124 fails)**; constant 190–210 (up to 225 in the 2 + 3 fleet) |
| Gateway: c4-highcpu-16 + 60 GiB Hyperdisk boot | 516.57 + 10.56 = **527.13** | 3-yr 232.46 + 10.56 = 243.02 | ≥ 570 RPS constant with 4 guards (760 fails at 20.14 ms) |
| Cloud SQL PostgreSQL 16 HA (4 vCPU / 16 GiB / 100 GiB) | 526.40 | — | failover 11.6–15.5 s loaded; 0 acknowledged writes lost |
| Memorystore for Valkey 8 HA (2 × highmem-medium, 13 GB) | 292.00 | — | audit budget per GW14c |
| Control plane: 2 × c4-standard-4 + 60 GiB Hyperdisk boot (2 re-hydrators, 2 zones) | 2 × (150.07 + 10.56) = 321.26 | — | R2-04 |
| Regional LB forwarding rule | 21.90 (re-price the passthrough NLB SKU at purchase) | — | R2-12 |
| Cloud Monitoring | 15.00 (doc parity) | — | — |
| **Fixed platform total** | **1,176.56** | | |

**Fleets within $5,000 per month (compute + fixed).** Capacity is arithmetic on measured units:

| Fleet | $ / month | Poisson capacity | Constant upper bound | What is measured |
|---|---|---|---|---|
| A — 2 gateways + 4 guards, on-demand | 4,475.30 | 4 × 100 = **400 RPS** | ≈ 760 (4 × 190; 840 failed) | The 4-guard shape passes 760 ×3 at constant arrivals (RC1, 3 gateways, 20 ms bound). At fleet scale with Poisson arrivals, 312 passed on the v2 prototype (v2.1) and 684 fails on RC1 (0.6%). The RC2 Poisson pass point at fleet scale is **not** measured (G-09). |
| B — 2 gateways (on-demand) + 7 guards (1-yr commitment + reservation) | 4,767.48 | 7 × 100 = **700 RPS** | ≈ 1,140 (gateway-bound: 2 × ≥ 570) | Linear scaling measured to 4 units (130 / 280 / 520 at 1 / 2 / 4, RC1, 12 ms bound, single ladder runs). Beyond 4 guards: extrapolated. |
| C — 3 gateways + 11 guards (3-yr commitments) | 4,828.32 | 11 × 100 = **1,100 RPS** | ≈ 1,710 (gateway-bound) | Extrapolated beyond 4 guards. Requires commitments and reservations (the owner's decision). |

- Commitment prices are the Catalog's "Commitment v1" SKUs for G2, L4 and C4 in Mumbai. A commitment is a billing discount; the GPU capacity itself must be secured with reservations (GW25), and reserved but unused slots bill.
- The v2.1 figure of $5.80 per qualified RPS-month (constant arrivals, 1 + 1) becomes **$10.88 per Poisson RPS-month** for a 1 + 1 pair ($1,088.25 per month at 100 RPS). In the 4-guard fleet A it is $11.19 per Poisson RPS-month including the fixed platform.
- Signing: T01 re-signs capacity from the GW20b Poisson measurement at fleet scale. The table above is the planning basis until then.

## 0.7 Decisions the owner must make

1. **In-flight kill-switch and revocation semantics (G-17, v2.1 C24).** RC2 lets a stream that started before a kill switch finish. Decide whether it continues, or is cut at the next chunk with the terminal error frame.
2. **Freshness vs ride-through (GW05b).** `RV_STATE_FRESH_MS` (proposed 5 s) and `RV_STATE_PG_GRACE_MS` (proposed ≥ 16 s) trade the exposure window for stale state against 503s during store and Postgres blips.
3. **GPU capacity commitments (GW25).** On-demand + reservations (fleet A) vs 1- or 3-year commitments (fleets B / C). Whether to request G4 on-demand quota (not a ≥ 2× win: best case 1.43× cheaper on Spot with batching). The whole-cell failover region: Tokyo or Seoul.
4. **Holdback trade-off (GW12b).** Accept the detector limitations of a 3-token cap (patterns longer than the cap can release a prefix), or relax the signed bound for specific pattern classes.
5. **Production console exposure (R2C-01).** Read the live control container's `SIMULATOR_DEFAULTS_ENABLED`, `SIMULATOR_CLEAR_BLOCKED_KEYWORDS` and `DEBUG` (read-only). Production is updated once, after the revamp (your decision of 24 September).
6. **Evidence retention.** Round-2 evidence is kept only on the controller VM's persistent disk (§0.9). Archiving it off-box would cost ≈ $0.4 per month (GCS Archive, 160 GiB).

## 0.8 Task index additions (v3)

The §0-A.5 table stays authoritative; these rows are added or replace the row for the same card:

| Card | Depends on |
|---|---|
| GW05b | GW04, GW05 |
| GW05c | GW05, GW05b |
| GW06 | GW03, GW04, GW05, **GW05b** |
| GW12b | GW12 |
| GW13 | GW07, GW09, GW10, GW12, **GW12b** |
| GW14c | GW14 |
| GW14d | GW14, GW05c |
| GW20 (GW20b) | GW14, GW14c, GW14d, GW15, GW16, GW16b, GW17, GW18, GW19, GW05c, GW12b |
| GW25 | T20, GW20 |
| GW22 | GW21, T21, GW25 |
| GW23 | GW22, T20, T21, T22, T23, T24, **R2-22 console blockers** |

## 0.9 Evidence and environment state

- **Repository index:** `docs/plans/evidence/2026-09-24-runbook-v3-round2/`. It holds:
  - README;
  - the findings ledger (§S);
  - the controller bulletin (the rules the lanes ran under);
  - the v3 working notes;
  - the RC3 tracker;
  - the teardown inventories;
  - the reference patches with READMEs and checksums.
- **Full evidence, on the controller VM `ai-mesh-firewall`'s persistent disk:**
  - `/home/contact_cyberultron_com/rv-evidence-raw/`: per-lane raw runs, 154 GB.
  - `/home/contact_cyberultron_com/rv-evidence-raw/round2-scratchpad-20260924/`: curated lane evidence and prototype trees, ≈ 8 GB. The `evidence/` paths in §0.2 are relative to this directory.
  - `/home/contact_cyberultron_com/rv-evidence-gcs-round1-20260923/`: a verified copy of round 1's former GCS bucket (56,497 objects, 59,442,533,740 bytes).
- **GCP state after the teardown** (2026-09-24 18:08Z, verified by a full inventory and re-checked live at 18:33Z, including Tokyo and Seoul). Every round-2 resource was deleted:
  - 70 VMs and 2 MIGs;
  - the GKE cluster;
  - 4 Cloud SQL instances (0 retained backups);
  - 4 Valkey and 2 Redis instances;
  - 3 reservations (15 L4 slots);
  - every load balancer component and 8 static IPs;
  - 11 templates and 2 custom images;
  - secrets, the service connection policy, 2 subnets and the PSA range;
  - the Artifact Registry repository `rv-r2`;
  - the round-1 evidence bucket (after the verified local copy).

  What remains in the project is not round-2 test infrastructure: the controller VM, its disk and the `ai-mesh` IP; four `aiguardx` snapshots belonging to another product; the default network. One zero-cost `servicenetworking` VPC peering remains until Google releases Cloud SQL's producer resources. Remove it later with `gcloud services vpc-peerings delete --service=servicenetworking.googleapis.com --network=default --project ai-mesh-firewall`.
- **Round-2 images are deleted.** The RC2 source tarball (sha256 `eb9d4da2…`) and manifest are in the evidence. Rebuilding produces new digests.
