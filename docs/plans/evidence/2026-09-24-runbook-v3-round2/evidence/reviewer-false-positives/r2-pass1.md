# Round-2 interim contradiction review — pass 1 (false-positive lens)

Reviewer: reviewer-false-positives · 2026-09-24 ~12:30–13:35Z · read-only (no GCP/IAM/VM actions).
Lens: assume the system and prototype are correct; hunt round-2 findings that are wrong, overstated or badly measured.
Ledger = SP/reports/FINDINGS_LEDGER.md §S (starts at L346); "Lnnn" = FINDINGS_LEDGER.md line number.
Scripts and recomputed outputs: SP/evidence/reviewer-false-positives/r2p1/.

Scorecard: HOLDS 6 · WEAK 5 · WRONG 0 as whole claims. Sub-claims that are wrong as stated:
- C36 restore range: quoted 0.27–0.42 s, measured 0.25–1.21 s. The lane's own bound check FAILED on rc1-b-3, and the ledger omits it.
- "FAILOVER NOPERM" evidence: the probe is invalid.
- C10 bin label: the ledger says "> 1024 tokens"; those prompts are exactly 1,024 tokens (n = 80).

---

## 1. ALB/L7 rejection (proxy-side holds, ~10 ms) — HOLDS; the NLB/nginx comparison is WEAK

**Claim.** L409–410, L412–413 and v3_notes §2:
- The regional external ALB adds a C4 p99 of about 8.9–10.6 ms.
- The hold root cause is on the proxy side: 74% of held pieces are in order but late, 26% are loss/reorder.
- "L4 passthrough NLB viable (zero added latency same-zone …; rare VIP-path tail events)".
- "nginx tier adds only ≈ 0.07 ms".
- "tail events hit both LBs, not nginx/direct".

**Why it might be wrong.**
- The C4 p99 is over streams of each stream's worst chunk, so it is very sensitive to rare events.
- The bake-off may compare different network paths rather than different LB types.
- The ALB ran with access logging at sampleRate 1.0 (notes/backend-service.json).

**Evidence checked (`r2p1/edge_tails.out`, recomputed from each run's `c4_all.json`).**

The addressing differs by edge (`runs/eb-*-300-r1/step.json` targets):

| Edge | Target | Path |
|---|---|---|
| direct (D) | 10.160.0.122 | internal VPC IP |
| nginx (X) | 10.160.0.126 | internal VPC IP |
| NLB (N) | 35.234.213.2 | external VIP |
| ALB (A) | 34.14.160.230 | external VIP |

Direct and nginx never touched the external/VIP path that the NLB and ALB used.

Per-run C4 across the 9 runs of each edge:

| Edge | C4 p99 (9 runs) | C4 p99.9 (9 runs) | Max finite |
|---|---|---|---|
| NLB | 2.0–7.4 ms in 8 runs; 134.66 ms in run 100-r3 | 6.25, 61.50, 415.35, 31.78, 7.76, 415.36, 6.64, 479.79, 80.41 — 6/9 above 20 ms | up to 4,174.6 ms |
| ALB | 8.5–11.7 ms in 7 runs; 177.55 and 20.44 in 2 runs | 23.1–477.4 — 9/9 above 20 ms | up to 5,542.8 ms |
| nginx VM | ≤ 0.54 ms | ≤ 5.92 ms | ≤ 6.4 ms |
| direct | ≤ 0.36 ms | ≤ 2.02 ms | ≤ 4.2 ms |

- Under the 3-repeat rule, the NLB fails at 100 RPS on its own (runs: 2.02 / 4.00 / 134.66).
- The ALB result stands:
  - its p99 is 8.5–11.7 ms in 7/9 runs;
  - the NLB shares the external path and shows no p99-visible 1-ITL holds;
  - the loadgen is exonerated, because all four edges ran concurrently from the same loadgen and only the ALB streams held tokens;
  - the GKE node-side pcap shows the pod sent every token on time.

**Verdict.**
- ALB rejection: HOLDS.
- "NLB viable / zero added latency / rare tails" and "nginx adds 0.07 ms": WEAK.
  - The tails belong to the external VIP path, not only to "the LBs".
  - The nginx figure is an internal-path number, so it is not what option B (NLB → nginx) would cost an internet client.

**Re-test (r2mig; synthprov only; the same 4-concurrent-edge design; HEADLINE; 300 RPS × 3; `c4_client_all --all`).**
- Edges:
  - direct VM via its external IP;
  - nginx VM via its external IP;
  - NLB → nginx → synthprov (option B as built);
  - internal passthrough NLB (same backends);
  - optional control: ALB with logging sampleRate 0.
- Plus one 30-minute NLB soak at 300 RPS to count VIP-path events per hour.
- How the result settles it:
  - Direct/nginx through external IPs show the same p99.9/max tails as the NLB → the tails come from the external path, and v3 must budget them regardless of edge type.
  - The internal NLB is clean while the external NLB is not → the external VIP path is the cause.
  - Option B's C4 p99.9 is the figure v3 should quote.

## 2. NLB per-connection starvation ("time-to-balance NEVER") — WEAK

**Claim.** L423 and v3 §2: after scale-out, the new gateway got 0–0.2 req/s against 40 req/s on the old one. The ledger says "time-to-balance = NEVER … Applies equally to GKE Service LB eTP Local … Recycling (RC2) or option B is mandatory".

**Why it might be wrong.** "NEVER" may be an artefact of how the loadgen held its connections, the observation window may be too short, and the GKE equivalence may never have been measured.

**Evidence checked (`r2p1/nlb_starvation.out` from `runs/dry-as-1/platform_timeline.json`; `step.json`).**
- Bins confirm the claim: gw-8gpw served 0.0–0.2 req/s from 09:40 to 09:45; s79d served 39.8–40.0.
- The run was labelled "DRYRUN (RC1, not measured)", n = 1.
- The step traffic came from a second loadgen (`lg_step` = lg-2) that started at 09:39:12.550Z. The new gateway only became healthy at 09:40:44Z, 92 s later, so every step connection was opened while only the old backend existed. By construction this is the worst case.
- Starvation was observed for 5.5 min (until the step-down at 09:46:12).
- The old gateway carried 40 req/s with no SLO impact, so in this run the imbalance cost nothing. It matters only near the old gateway's capacity.
- There is no GKE measurement; "applies equally" is an inference.

**Verdict.** The mechanism HOLDS: an L4 NLB balances per connection, and pre-existing persistent pools never move. "NEVER" and "mandatory" are overstated. Balance time equals the client population's connection-churn time: never for a few long-lived persistent pools, minutes for many churning clients.

**Re-test (r2mig E-conn-off and E-conn-on arms, already planned — add the following).**
- Report per-backend RPS and time-to-balance for:
  - (a) the step loadgen starting after the new gateway is healthy;
  - (b) olg with connection churn (e.g. max 100 requests per connection or a 30 s idle close) to emulate a client population;
  - (c) `RV_CONN_MAX_AGE_S` 60 / `RV_CONN_MAX_REQUESTS` 1000;
  - (d) one GKE N-edge step (r2gke).
- Settled if (a) and (b) balance within about 1–2 × the churn interval and (c) balances within about the max-age. v3 then states the condition (persistent pools need recycling) instead of "NEVER".

## 3. C10 bound-rule defect (prompt-size-biased sheds) — HOLDS; "rule vs parameter" is WEAK

**Claim.** L426 and L436: at 150 RPS (split topology), 0% of prompts under 768 tokens are shed, 12% of 768–1023-token prompts, and 45% of prompts "> 1024 tokens". The ledger calls the bound rule a defect.

**Why it might be wrong.**
- The bins could be small-n artefacts.
- Any work-based admission sheds large requests more often.
- The 12 ms parameter, rather than the rule, might be the problem.

**Evidence checked (`r2p1/c10_shed_by_size.py/.out`; raw `~/rv-evidence-raw/r2-unit/runs/c10b-150/*/lg/requests.jsonl.zst`, measured phase ph=2, n = 45,000, 1,524 × 503).**

| Prompt tokens | Requests | Shed | Rate |
|---|---|---|---|
| < 512 | 19,480 | 0 | 0% |
| 512–767 | 13,012 | 0 | 0% |
| 768–1023 | 12,428 | 1,488 | 11.97% |
| 1,024 | 80 | 36 | 45.0% |

| PG2 windows | Requests | Shed | Rate |
|---|---|---|---|
| 1 | 19,446 | 0 | 0% |
| 2 | 25,328 | 1,421 | 5.61% |
| 3 | 226 | 103 | 45.58% |

- The HEADLINE maximum is 1,024 tokens, so the ledger's "> 1024" bin is really "= 1024", with n = 80.
- The bias is real and strong. In absolute terms, 1,421 of the 1,524 sheds are 2-window prompts.
- r2_summary confirms: owner GPU 43% busy, owner queue p99 1.27 ms, admitted C4 max finite 13.875 ms.

**Verdict.**
- The bias HOLDS (recomputed exactly; fix the bin label).
- "The rule is the defect" is WEAK. The planned `RV_OWNER_QUEUE_MS` 8/12/20 sweep was never reported. A 12 ms bound (2,832 padded tokens) cannot hold one running 3-window request plus one arriving 2-window request, so a larger bound might remove most of the bias without CoDel.

**Re-test (r2unit split, RC2, `RV_OWNER_ADMISSION=bound`).**
- `RV_OWNER_QUEUE_MS` 20 and 30 at 150 constant and Poisson at 124, ×1 each, reporting shed % by window count and admitted C4 p99.
- Settled:
  - if sheds fall below 0.1% with admitted C4 p99 under 20 ms, the parameter was the defect, and v3 may keep a simple bound or CoDel on cost grounds;
  - if the 3-window bias persists at 30 ms, the rule is the defect (CoDel justified).

## 4. C43 "per-guard knee 130 is bound-limited, not routing" — HOLDS (thin positive evidence)

**Claim.** L424: the knee is 130/guard; C10 queue_bound sheds cap it, not latency or routing. Dev C10-off calibration reached 190/guard.

**Why it might be wrong.**
- The only positive evidence (190/guard) is a 60 s dev-tree run, "not a claim".
- At higher rates the push fan-out cost could become the limiter: 72 pushes/request at 36 workers, +2.3 gateway CPU-ms/request.

**Evidence checked (`SP/evidence/r2-fleet/tables/c43-1.md`).**

| Rate (per guard) | Infra % | Runs passing |
|---|---|---|
| 520 (130) | 0.038 / 0.022 / 0.021 | 3/3 |
| 560 (140) | 0.086 / 0.127 / 0.076 | 2/3 |
| 600 (150) | 0.132 | 1 run, FAIL |

- M/G/1 ratio is 0.16–0.20 throughout, and owner queue p99 is 4.1–4.6 ms, so routing is doing its job.
- C43-2 OFF at the same rates: 3.46–4.65% infra (L431).
- Shed rate by size at 600: under 500 tokens 0.04%, 1,000–1,500 tokens 1.43%. This matches the §3 mechanism.
- The knee lies between 130 and 140; "130" is the conservative 3/3 reading.

**Verdict.** HOLDS as the most likely explanation. It is not yet proven that the knee reaches 170 under a better admission rule.

**Re-test (already queued: r2fleet C43-1 ladder on RC2 codel).**
- Also report: shed reasons, shed % by window count, gateway CPU-ms/request, and pushes/request at 150/170/190 per guard.
- Settled:
  - if the knee is at least 170 at 3/3 with sheds under 0.1%, confirmed;
  - if the knee stays at 130–140 with codel sheds or gateway CPU as the limiter, then "not routing" was wrong (routing cost or codel is the new limiter).

## 5. C38 claim met on RC1 (knee 250) — HOLDS for ≥ 200 RPS; the "knee 250" figure is WEAK

**Claim.** L425, L430 and v3 §6: on a g2-standard-24 unit, 200 RPS ×3 gave C4 p99 18.57 / 18.62 / 18.55 and 250 RPS ×3 gave 19.94 / 19.56 / 19.55. The ledger concludes "RC1 all-in-one C4 knee = 250 RPS".

**Why it might be wrong.** The headroom is tiny, the knobs are non-default, and the stores and region are not production-like.

**Evidence checked (`SP/evidence/r2-unit/runs/c38-1-*/r2_summary.md`, all figures re-read).**
- The SSE stratum p99 at 250 is 19.995 ms: 5 µs of headroom.
- Every C38 run used `RV_OWNER_QUEUE_MS=1000` (non-default).
- Stores were co-located on the SUT host at 127.0.0.1 (B14), in Tokyo. Managed stores cost 0.05–0.06 ms same-zone and 0.3 ms cross-zone per round trip (L359).
- 200 RPS has about 1.4 ms of headroom. RC2 at defaults gave 18.445 at 200 (c38r2-1-200-r1).

**Verdict.** "C38 claim (≥ 200 RPS 3/3)" HOLDS. "Knee 250" should not enter v3 capacity maths without a production-store re-measure.

**Re-test (r2unit queue-a on RC2).**
- 250 ×3 at RC2 defaults, plus one 250 run with the stores reached over the network (Valkey/Postgres on a separate VM in the same zone, or managed stores in Mumbai).
- Settled: all p99 under 20 with at least 0.3 ms headroom → knee 250 transferable; otherwise quote 200.

## 6. C36 RC1 results — WEAK (core "0 violations" HOLDS)

**Claim.** L429 and v3 §6: "FLUSHALL ×3 0 violations, re-hydrator restored all 4 kinds 0.27–0.42 s … 0 regressions".

**Why it might be wrong.** The restore range may have been cherry-picked, and the killed-org path may never have been exercised in the flush runs.

**Evidence checked (`runs/rc1-b-*/analysis/verdict.json`; `summary-rc1/runs_table.md`; `scripts/summarize.py:106-120`; `r2p1/c36_probe_power.out`).**

Restore times (`restored_at_s`):

| Build | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| RC1 | 0.252 | 0.417 | **1.211** |
| RC2 | 0.456 | **1.249** | — |

- rc1-b-3: the re-hydrator was mid-pass (ks/budget repaired at −0.017 → 0.058 s); plan and key were repaired only on the next pass (1.09 → 1.17 s).
- The lane's own table marks **BND = FAIL** for rc1-b-3. Its bound is `period 1 s + took` ≈ 1.15 s. verdict.json uses a different, looser declared bound of 2.04 s.
- Killed org (K): in all three flush runs, every K probe returned `503:kill_switch_engaged` from the worker's RAM snapshot (24/24 per second bin). The store-miss path for K was exercised only in the single gap run (rc1-bgap-1: 503 `kill_switch_unavailable` from about +4.5 s).
- Valid tenants saw no gap (0 not-ready polls, first 200 within 0.58 s), so the practical conclusion stands.

**Verdict.**
- "0 violations" HOLDS.
- The quoted restore range is WRONG: 0.25–1.25 s, bimodal by re-hydrator phase; the worst case is about 2 × period + took.
- "Killed org stays blocked under flush" rests on one run.

**Re-test (r2state, RC2 campaign now running).**
- Report all flush restore times and apply one bound definition (2 × `RV_REHYDRATE_PERIOD` + took, or restore all missing kinds in one pass).
- Add a gap variant ×3 with the re-hydrator stopped for 3 s, which exceeds `RV_KS_REFRESH_MS`, with K/R probes at ≥ 100/s during the fault window.
- Settled if 0 K admits across 3 exercised store-miss windows and every restore stays within the declared bound.

## 7. Store failover numbers — raw numbers HOLD; the v3 planning figures are WEAK; the NOPERM evidence is invalid

**Claims.**
- Cloud SQL outage 10.36 / 9.81 s, 0 acked writes lost, 409 cooldown.
- Valkey planned-failover outage 3.81 s, about 6 min after the call.
- "NO manual failover (… FAILOVER NOPERM)".
- v3 §3 quotes "failover outage 9.8–10.4 s".

**Evidence checked (`r2-infra/raw/drills/drill_{pg,valkey}_1.analysis.json`, `raw/valkey_acl_probe.txt`, `raw/memorystore_v1*_discovery.json`).**
- Recomputed windows match the claims: PG 10.362 s and 9.807 s (first failure +4.31 s after the trigger); Valkey 3.805 s starting 346.0 s after the call.
- These are probe outages from one connection at 100 ms, with no load. Under load the gaps were longer: chaos f2 PG down 11.6 s, and r2state (e) write gap 15.5 s.
- `valkey_acl_probe.txt` returns "NOPERM … 'acl|dryrun'" for every command, including PUBLISH, EVALSHA, SELECT and MULTI, which the lanes use successfully. So the probe only shows that ACL DRYRUN is blocked; it says nothing about FAILOVER.
- The three discovery JSONs are 403 "unregistered caller" errors, not API listings. "No failover API" therefore rests only on READY's statement that the bundled gcloud clients were checked.

**Verdict.**
- Drill numbers HOLD.
- v3 should quote the loaded figures (11.6–15.5 s write/serve gap) for planning: WEAK as written.
- The FAILOVER-NOPERM evidence is WRONG. The conclusion is probably still right, but it is unproven from the files.

**Re-test (read-only, r2infra).**
- Fetch the discovery documents with an authenticated token and record whether any failover method exists.
- Run `gcloud beta/alpha memorystore instances --help` and record the verbs.
- Optionally send a direct `FAILOVER` on the chaos Valkey (its own set) and record the error.
- Settled by an authenticated method listing with no failover RPC.

## 8. L4 stock-out statistics (4.9% success) — HOLDS

**Claim.** L440: 203 on-demand g2-standard-4 create attempts, 10 successes (4.9%): zone a 1/89, b 7/93, c 2/21. The ledger concludes "≈ 2 new L4 per hour".

**Evidence checked (`r2p1/l4_stats.py/.out` over `r2-infra/l4-pool.jsonl`).**
- Exact match: 203 / 10 / 4.93%; a 1/89, b 7/93, c 2/21; all 193 failures are ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS.
- Caveats:
  - The capture rate is bounded by the hunter's cadence (median 154–157 s between attempts per zone for a/b, 433 s for c), so it is a floor on supply, not supply itself.
  - Zone c was sampled about 4× less often.
  - Excluding the two outages (07:17–07:54 and 10:12–10:35, 59 min), the effective rate is 10 / 4.03 h ≈ 2.5 per hour.
  - Other lanes' concurrent creates (MIG, GKE autoscaler) mostly used held slots; no uncounted fresh captures were found.
  - This is a two-day sample (2026-09-23 and -24).

**Verdict.** HOLDS as measured. Present it as "one hunter at about 2.5 min per zone", not as a supply rate. The v3 conclusion (pre-reserve) stands on prudence.

**Optional re-test (r2alt).** A 60-min hunt in zone b at a 30 s cadence (affinity NONE), to see whether captures scale with cadence.

## 9. Console CRITICAL keyword wipe + prod-exposure inference — the bug HOLDS; the exposure probe is WEAK

**Claim.** L385 and L393: POST `/api/gateways/simulator-default/` wipes the requester's org Blocked Keywords. The ledger infers prod exposure from `docker-compose.prod.yml:150`, and proposes a "SAFE READ-ONLY PROBE: GET … 404 ⇒ off; 200 ⇒ on".

**Evidence checked (`git show 8ddb3db6:` `core/gateway_views.py`, `core/simulator_seed.py`, `docker-compose.prod.yml`).**
- The POST calls `ensure_simulator_dev_bootstrap(org)`, where `org` is `request.user.profile.organization` (the real org).
- `ensure_simulator_firewall_keywords_cleared` blanks `blocked_keywords` with `save(update_fields=["blocked_keywords"])`, so `updated_at` and the audit trail are not touched.
- `SIMULATOR_CLEAR_BLOCKED_KEYWORDS` defaults to "true". Compose line 150 is `${SIMULATOR_DEFAULTS_ENABLED:-true}`. The bug is confirmed.
- **Probe flaw:** the two gates default differently.
  - The GET/POST gate `_simulator_defaults_enabled()` defaults to "true".
  - The bootstrap reads the same variable but defaults to "true" only when DEBUG is on.
  - So if prod runs without compose line 150 (variable unset) and DEBUG=False, GET returns 200 while the wipe is actually OFF. "200 ⇒ on" holds only when the variable is set explicitly, or when DEBUG=True.
- Side finding: `ensure_firewall_excludes_guard_model` iterates `FirewallConfig.objects.all()`, so every simulator POST writes every org's `allowed_models`. The effect is benign (it strips the guard model), but it is a cross-tenant write.

**Verdict.**
- R2C-01 HOLDS.
- The prod-exposure inference is plausible (prod likely uses this compose file), but the proposed probe cannot confirm "on".

**Re-test (owner, read-only).**
- Read the running prod control container's environment (`docker inspect` / compose config) for `SIMULATOR_DEFAULTS_ENABLED`, `SIMULATOR_CLEAR_BLOCKED_KEYWORDS` and DEBUG.
- Or, in staging: unset the variable, set DEBUG=False, GET → 200, POST → keywords intact. That demonstrates the probe's blind spot.

## 10. RemoveIPC /dev/shm hazard — HOLDS

**Claim.** L370 and v3 §8: /readyz 503 on 3.2% of polls (153/4,837); with `RemoveIPC=no`, 400/400 polls returned 200.

**Evidence checked (`r2-chaos/notes/decisions.txt:67-71`, 06:33Z; RC2 preflight 12:28Z, 35/35 including /readyz 20/20 after logout).**
- 153/4,837 = 3.16%.
- P(0/400 | p = 3.2%) ≈ 2 × 10⁻⁶, so the before/after difference is not chance.
- `/dev/shm` was observed empty while the services ran. The services ran as the login user rv, and logind removes IPC objects owned by that UID at last logout, including those of system services running as that user. Documented behaviour.

**Verdict.** HOLDS. v3 wording could add `loginctl enable-linger` or UID < 1000 as alternatives to containers.

## 11. Idle-reserved-slot billing — HOLDS (policy and arithmetic; not yet seen in a bill)

**Claim.** L432, L445 and v3 §4: "Idle reserved slots bill at the full VM rate ($0.735/h per g2-standard-4 slot): 7 idle = $5.15/h".

**Evidence checked.**
- $0.7358/h = $537.12 per month (round-1 Mumbai on-demand g2-standard-4, compute + GPU, no disk) ÷ 730.
- 7 × 0.7358 = $5.15; 15 × 0.7358 = $11.04. Both match L439 and L445.
- This is consistent with GCP reservation billing, where reserved vCPU/RAM/GPU bill at on-demand rates whether or not they are used. It is not yet confirmed from the Cloud Billing export.

**Verdict.** HOLDS.

**Re-test (controller, read-only, after the export latency of about 24 h).** Sum the billing-export rows for g2 core/RAM/L4 in asia-south1 between 12:10 and 13:00Z. Settled if the 7 idle slots cost about $5.15/h ± 5%.

---

## Top 5 by impact on v3
1. **Edge choice (§1).** The NLB-vs-nginx-vs-ALB comparison mixes internal and external addressing. The NLB's p99.9 tails (6/9 runs above 20 ms; one run with p99 134.7 ms) are as bad as the ALB's. Re-run the bake-off with every edge on an external IP, plus NLB → nginx and an NLB soak, before v3 fixes the edge architecture and its latency budget.
2. **C36 bound (§6).** The restore range is misquoted (0.27–0.42 s against a true 0.25–1.25 s); the lane's own BND FAIL on rc1-b-3 is omitted; the killed-org store-miss path was exercised once. Fix the bound definition and add 3 exercised gap runs with dense probes on the RC2 campaign.
3. **C10 rule vs parameter (§3).** The bias is real (0% for 1-window prompts, 5.6% for 2-window, 45.6% for 3-window). An untested larger bound (20–30 ms) could remove it. This decides whether v3 needs CoDel or a tuned bound.
4. **NLB "time-to-balance NEVER" (§2).** It is a worst-case construction (the step pool was opened before the backend was healthy), observed for 5.5 min in a dry run, with GKE inferred. v3 should state the churn condition and quantify recycling with the planned E-conn-on/off runs plus a churned-client arm.
5. **C38 "knee 250" (§5).** The SSE stratum has 5 µs of headroom, with `RV_OWNER_QUEUE_MS=1000`, co-located stores and Tokyo. v3 capacity maths should use 200 RPS per g2-standard-24 unless 250 is re-measured on RC2 defaults with network stores.

Also for v3:
- Quote the loaded store-failover gaps (11.6–15.5 s), not the unloaded 9.8–10.4 s.
- Drop the FAILOVER-NOPERM evidence.
- Fix the C10 bin label ("= 1,024 tokens, n = 80").
