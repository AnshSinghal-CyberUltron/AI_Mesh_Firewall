# Round-2 interim contradiction review — pass 1 — EDGE CASES (reviewer-edge-cases, 2026-09-24 ≈ 12:55Z)

## What I did
- **Read:**
  - FINDINGS_LEDGER §S (lines 346–445);
  - v3/v3_notes.md;
  - CONTROLLER_BULLETIN B1–B35;
  - ROUND2_SPEC (lane map, the r2chaos Part 1/2 plan, the r2mig/r2gke step plans);
  - lane evidence: r2-chaos scripts/notes, r2-mig scripts/actions.log, r2-gke manifests/actions.log, r2-alt survey, r2-impl USAGE.md.
- **Read the PROVEN RC2 code itself.**
  - Source: a private extract of `SP/evidence/r2-impl/rc2/rvproto2-rc2.tar.gz`, verified against `MANIFEST.sha256` (171 files; manifest sha 6093c8dd…).
  - Location: `EV/r2/rc2src/` (EV = SP/evidence/reviewer-edge-cases).
- **Ran two offline micro-measurements on the controller.** No GCP was used; I ran a throwaway local Redis container and deleted it.
  - Scripts and results are in `EV/r2/micro/`.
  - They use the unmodified RC2 code (`runtime/versioned.py`).
- **Nothing was touched:** no GCP resource was created or modified, no lane VM was touched, no IAM changed, and no secrets or kubeconfigs were read.
- **Verdicts:**
  - **MUST-TEST-NOW**: a v3 claim depends on it, no lane run can see it today, and it is cheap on the live fleet.
  - **TEST-IN-PREPROD**: it needs code that does not exist yet (RC3 or a product fix) or a vantage point we do not have.
  - **ACCEPT-WITH-NOTE**: design or configuration rules; the evidence is already in hand.

## Summary table
| ID | Scenario | Covered today? | Verdict |
|---|---|---|---|
| E2-01 | Many orgs / keys: O(N) state work per worker every refresh, O(N) store transaction per config write, identity cache flushed by any key write | NO (every lane seeds 3 orgs / 3 keys) | **MUST-TEST-NOW** |
| E2-02 | Cold start with many distinct keys (scale-out gateways, and every worker after a key write) | NO (olg sends one `-auth` per process; the RC2 functional run found it — RC3 single-flight) | **MUST-TEST-NOW** (quantify on RC2) |
| E2-03 | Long-tail prompt sizes at a FIFO owner: head-of-line blocking plus CoDel sheds | NO (HEADLINE ≤ 1,024 tokens only) | **MUST-TEST-NOW** |
| E2-04 | Client retries under shedding (SDK honours `retry-after-ms`; aggressive clients ignore it) | NO (olg never retries) | **MUST-TEST-NOW** |
| E2-05 | Scale-in or LB drain while streams longer than the drain window are live | PARTIAL (HEADLINE streams ≤ ~8 s; chaos LONG_RATE streams ≤ 60 s) | **MUST-TEST-NOW** |
| E2-06 | `/readyz`-based LB health check during a store gap: declared 503s turn into TCP failures | NO (Part 1 uses an nginx edge with passive health only) | **MUST-TEST-NOW** (Part 2) |
| E2-07 | Zone loss with the real pool shape (a 1 / b 7 / c 7) and the MIG distribution shape | PARTIAL (Part 2 "zone loss" exists, but not against the pool shape) | **MUST-TEST-NOW** (Part 2 spec) |
| E2-08 | Store data loss while Cloud SQL is unavailable (maintenance or failover overlap) | NO (f1/f1b and f2 run separately) | **MUST-TEST-NOW** |
| E2-09 | Rolling a new guard image, or a node upgrade, while the reserved pool is full (surge needs a spare L4) | NO (GKE test cluster has a maintenance exclusion; no rollout test) | **MUST-TEST-NOW** |
| E2-10 | TLS termination placement (round 2 is plaintext; the RC2 gateway has no TLS) | NO | **MUST-TEST-NOW** (option B) / TEST-IN-PREPROD (option A) |
| E2-11 | HMAC record-key rotation: mixed-key record sets fail closed, so the whole fleet goes not-ready | NO | TEST-IN-PREPROD (RC3 key id) |
| E2-12 | Provider silent > 120 s (reasoning TTFT or idle); no total stream bound | chaos f6c covers only the *fault* | ACCEPT-WITH-NOTE (+ TEST-IN-PREPROD with a real reasoning model) |
| E2-13 | Output channels / JSON validity / escaped tool arguments (round-1 E1/E4/E5) still present in RC2 | NO | TEST-IN-PREPROD (product fix first) — v3 blocker |
| E2-14 | Non-stream response size unbounded (round-1 E22; RC2 `respond.py:35 await resp.read()`) | NO | TEST-IN-PREPROD (needs a bound) |
| E2-15 | Mass client disconnect (cancel storm) | NO (mechanism proven at small scale in round 1) | TEST-IN-PREPROD |
| E2-16 | Budget-lease stranding at fleet scale (budget < workers × 25,680 tokens) | PARTIAL (r2console overshoot; RC3 revocation) | TEST-IN-PREPROD |
| E2-17 | Bulk onboarding / migration of existing tenants into C36 (every write republishes the full kind ⇒ O(N²)) | NO | ACCEPT-WITH-NOTE (needs a bulk path) |
| E2-18 | Clock skew and HMAC epochs | n/a (versions are logical) | ACCEPT-WITH-NOTE |
| E2-19 | Valkey primary moving zones | YES (r2-infra RTT; f1 post-window) | ACCEPT-WITH-NOTE |
| E2-20 | Quota exhaustion during scale-out | YES (evidence 06:38Z) | ACCEPT-WITH-NOTE |
| E2-21 | Spot / flex / cross-region guard capacity (r2-alt options) | NO | ACCEPT-WITH-NOTE / TEST-IN-PREPROD if adopted |
| E2-22 | Internet-vantage latency and the WAN detour | NO (all edge numbers are in-region) | TEST-IN-PREPROD |

---

## MUST-TEST-NOW items

### E2-01 Many orgs and keys: per-worker O(N) refreshes, O(N) publishes, fleet-wide identity-cache flushes
**Scenario.** A production tenant population: thousands of orgs, tens of thousands of API keys, explicit kill-switch records, and routine key create/rotate/revoke churn.

**Why v3 depends on it.** C36 is the v3 state design. Every round-2 lane seeds 3 orgs and 3 keys, so its cost model has never been exercised at N > 3. Five O(N) paths sit in RC2:

1. **Kill-switch refresh.** Every worker, every `RV_KS_REFRESH_MS` = 500 ms, runs HVALS of the whole ks hash, then `verify_set`: JSON parse, sha256 body hash and HMAC-SHA256 for every record, plus a sorted digest. This runs on the serving event loop (`admit/state_v2.py:103-110`, `runtime/config.py:41`).
   - It runs even when nothing changed.
   - Offline cost per refresh (`EV/r2/micro/verify_cost.json`, unmodified RC2 code):

     | ks records | CPU per refresh | Share of one core |
     |---|---|---|
     | 1,000 | 5.2 ms | 1% |
     | 10,000 | 56.7 ms | 11% |
     | 50,000 | 322.6 ms | 65% |

   - Explicit OFF records are never removed, so N only grows.
2. **Plan reconcile.** Every worker, every 1 s: HGETALL of the whole plan index plus a digest over all entries (`plan/snapshot_v2.py:77-85`). Offline: 9.5 ms per reconcile at 10k orgs, 55 ms at 50k. Round 1 measured the same shape live on rvproto-1: loop-lag p99 65 ms and request p99 37 → 80 ms at 50k tenants (`EV/p09-tenant-scale`, reproduced on frozen-1).
3. **Identity cache flush.** Any key write (create *or* revoke) bumps the key manifest, which is the auth epoch. Every worker on every gateway then drops its entire identity cache within 500 ms (`admit/state_v2.py:40-42, :120`). Every active key's next request is a store round trip, all at once: the RC2 "cold stack ⇒ 503 shared_state_unavailable" stampede, but on the whole fleet.
4. **Publish.** Every write of any kind republishes the **complete** kind in ONE MULTI/EXEC: DEL plus HSET of all records, or a SET per org for plans (`control/writer.py:121-160`). Local lower bound on redis 7.4 over loopback, no replication (`EV/r2/micro/publish_block.json`):

   | key records | Transaction size | Publish time | Concurrent client blocked |
   |---|---|---|---|
   | 10,000 | 3.5 MB | 23.8 ms | 13.6 ms |
   | 50,000 | 17.4 MB | 124 ms | 59.5 ms (6 PINGs over the 25 ms `RV_STORE_TIMEOUT_MS`) |

   On Memorystore, with network and replica sync, it will be worse. So every config click by any tenant opens a window of request-path store timeouts, i.e. 503 `shared_state_unavailable`, fleet-wide.
5. **Postgres snapshot.** `db.snapshot(kind)` reads the complete record set per write, O(N) on Cloud SQL.

**Covered today?** No. ROUND2_SPEC lanes, r2console E2E, and r2state C36 all use 3 orgs / 3 keys.

**Run spec** (lane **r2state**; it owns a destructive-safe store set and the C36 tooling; else r2fleet in its namespace).
- **Build:** RC2 digests.
- **Topology:** 1 gateway (c4-highcpu-16, 12 workers) + 1 guard (g2-standard-4), the lane's own Valkey + Cloud SQL, C36 on.
- **Seed:** bulk-load via SQL in one transaction (the writer has no bulk path, see E2-17), then ONE re-hydrator publish. Three levels:
  - N = 1k orgs / 2k keys / 1k explicit org kill-switch OFF records;
  - N = 10k / 20k / 10k;
  - N = 25k / 50k / 25k.
- **Load:** olg HEADLINE at 60% of the unit's RC2 knee (≈ 120 RPS), constant. The keys must rotate across 2,000 active keys:
  - 20 olg processes × 100 RPS each with distinct `-auth-file`s; or
  - a small Python open-loop client (hdrprobe.py style).
- **Phases, each 300 s after 30 s ramp + 60 s warm-up:**
  - **A** steady, no writes;
  - **B** one `key_add` or `key_revoke` every 5 s;
  - **C** one `plan_set` every 5 s;
  - **D** one kill-switch write every 5 s.
- **Measure:**
  - worker loop-lag p99/p99.9;
  - C4 p99 (all, and per phase);
  - infra % and the 503 code mix;
  - identity fetches/s and store round trips per request;
  - Valkey CPU and network (Cloud Monitoring);
  - writer publish duration and ks/plan refresh durations (exporter).
- **PASS** at 10k/20k/10k, all phases: C4 p99 < 20 ms, infra ≤ 0.1%, loop-lag p99.9 ≤ 5 ms, publish ≤ 25 ms. My expectation from the offline numbers: FAIL at 10k.
- **Duration:** about 25 min per N.

### E2-02 Cold start with many distinct keys (autoscaled gateways; every worker after a key write)
**Scenario.** A gateway joins under load (scale-out or restart) and immediately receives connections from many distinct API keys. The same state follows every key write (E2-01, path 3).

**Why v3 depends on it.** The owner's pass bar is "infra ≤ 0.1% throughout scale-out". RC2's functional run found a cold stack plus 200 concurrent new clients produces 503 `shared_state_unavailable` (FINDINGS_LEDGER.md:435; single-flight is an RC3 item). The r2mig/r2gke step tests use 3 keys, one `-auth` per olg process, so the step test passes by construction.

**Covered today?** No.

**Run spec** (lane **r2mig**, one extra run after E×3; or r2unit split unit).
- **Build:** RC2 digests, NLB edge, `RV_CONN_MAX_AGE_S` as used in the E runs.
- **Client:** 2,000 distinct keys across 200 orgs, uniformly drawn per request (same client as E2-01).
- **Profile:** the E-config 3× step (L0 → 3×L0), 3 repeats.
- **Measure:**
  - infra % in each new gateway's first 60 s of health;
  - identity fetches/s;
  - store pool waits and the 503 code mix;
  - per-backend RPS.
- **PASS:** infra ≤ 0.1% over the step, and 0 × 503 `shared_state_unavailable` on the new gateway.
- **Expected on RC2:** FAIL, which quantifies the RC3 single-flight item; re-run on RC3.

### E2-03 Long-tail prompt sizes at the FIFO owner
**Scenario.** Real traffic contains multi-turn and RAG prompts.
- Round 1 (`EV/p14-over-band`): an ordinary chat reaches 4 windows at turn 3 and 16 windows at turn 12.
- On an L4, a 16-window request takes about 16 × 2.15–2.77 ms ≈ 34–44 ms at the owner (guard-bench). That is more than the whole 20 ms C4 budget for every request queued behind it (FIFO owner; recommended config "no cross-request batching").
- With CoDel, a burst of long prompts creates a standing queue and sheds short requests too.

**Why v3 depends on it.** Every per-guard knee (C10, C43, the $5k fleet) is measured on HEADLINE (≤ 1,024 tokens, ≤ 3 windows). M/G/1 wait scales with E[S²], so a 1–5% long-prompt tail can move the knee a lot. RC1 already showed prompt-size-biased shedding (45% of > 1,024-token prompts shed; FINDINGS_LEDGER.md:426).

**Covered today?** No. The harness corpora are headline, worst (1,024 in) and correctness.

**Run spec** (lane **r2unit** queue-b split unit, after the codel ladder).
- **Build:** RC2 digests.
- **Corpus:** HEADLINE plus p% long prompts of 4,000–7,000 tokens (9–16 windows). Build it with `make_corpus.py` from benign text. p = 1% and p = 5%.
- **Rate:** 90% of the RC2 HEADLINE split knee, constant and Poisson; 3 × 300 s each.
- **Measure:**
  - C4 p99 overall and per input-size stratum (< 1,024 / 1,024–4,000 / ≥ 4,000);
  - shed % per stratum;
  - owner wait p99;
  - FAIL_OPEN and UNAVAILABLE.
- **PASS:** C4 p99 < 20 ms over all offered AND for the < 1,024 stratum; infra ≤ 0.1%; long-to-short shed-rate ratio ≤ 2; 0 FAIL_OPEN.
- **Also report:** the knee under the p = 5% mix. This is the number v3 should size with.

### E2-04 Client retries under shedding
**Scenario.** Sheds are 503 + `Retry-After` (≥ 1 s) + `retry-after-ms` (drain time, as small as one execution). The official OpenAI SDK retries 408/409/429/≥500 twice and prefers `retry-after-ms` when 0 < v ≤ 60 s (`openai/_base_client.py:746-825`). So shed requests come back within milliseconds.
- Round 1 measured 5 SDK calls → 15 HTTP attempts under shedding (`EV/p08-noisy-neighbour`).
- Aggressive clients ignore the headers.
- RC2 never sends `x-should-retry` (0 hits in the tree), which the SDK honours at lines 797-805.

**Why v3 depends on it.**
- The C10 claim ("sheds at the owner, admitted p99 bounded, recovery immediate") and config E ("will shed during scale-out") are only measured with olg, which never retries.
- Real clients turn a shed into up to 3× offered load exactly while GPU capacity is capped by the reserved pool. That risks metastable overload.

**Covered today?** No.

**Run spec** (lane **r2unit**, reusing its C10 overload profile 97 / 414 / 97 RPS, 120 / 120 / 180 s, split unit, RC2 codel).
- **Arms, 3 runs each:**
  - **(a)** baseline, olg only;
  - **(b)** 30% of burst arrivals from an SDK-like client (open-loop arrivals; each request retried up to 2× on 429/5xx after `retry-after-ms`, else `Retry-After`, else 0.5·2ⁿ s with jitter);
  - **(c)** (b) plus 10% aggressive clients (immediate retry up to 5×).
- **Measure:**
  - original vs total offered;
  - shed %;
  - goodput;
  - admitted C4 p99 during the burst;
  - time from burst end until shed rate < 0.1%;
  - retries per original request.
- **PASS:** admitted C4 p99 < 20 ms during the burst; recovery ≤ 5 s after the burst in (b) and (c); total/original ≤ 1.5 in (b).
- **If FAIL:** v3 adds `x-should-retry: false` on overload sheds plus a minimum `retry-after-ms`, and retests.

### E2-05 Scale-in or LB drain while long streams are live
**Scenario.** Scale-in or drain with SSE streams longer than `RV_DRAIN_ACCEPT_S + RV_DRAIN_S` = 15 + 60 s (`runtime/config_v2.py:55-56`).
- USAGE:113 requires that sum to be ≤ ~80 s to fit the GCE shutdown window.
- GKE sets `terminationGracePeriodSeconds` 90.
- Longer streams are cut at the deadline. RC2 has no terminal SSE error frame (RC3 item). Round-1 f4 showed the output audit records of cut streams go missing.

**Why v3 depends on it.** Owner pass bar: "0 dropped streams on scale-in".
- HEADLINE streams last ≤ ~8.2 s, so the step tests pass by construction.
- r2chaos's LONG_RATE option gives 7.5–60 s streams (LONG_ITL_MS 150 × 50–400 tokens; `chaos_run.sh:77-84`), which is still ≤ the drain window.

**Covered today?** Partial: short streams only.

**Run spec** (**r2mig** E-config step-down, 1 run; **r2chaos Part 2** LB drain, 3 instances).
- **Setup:** add a long-stream stratum.
  - LONG_RATE = 5% of SSE with `LONG_ITL_MS=750`, so 400 tokens ≈ 300 s.
  - Plus 1% of SSE with 16,000 tokens at ITL 20 ms ≈ 320 s (synthprov `-tokens-cap` 16384).
- **Trigger:** scale-in (step-down) or a backend drain while those streams are live.
- **Measure:**
  - streams ending without `[DONE]` (eof_mid_stream), bucketed by elapsed time at the drain;
  - whether any terminal error frame arrives;
  - output audit records for cut streams;
  - drain timeline (SIGTERM → `/readyz` 503 → exit);
  - VM deletion time (GCE) or pod kill (GKE).
- **PASS (owner bar):** 0 dropped streams.
- **Expected on RC2:** FAIL for every stream > ~75 s.
- **v3 implication:** declare a maximum stream duration, and make the drain window cover it. For MIG this means GCE graceful shutdown (instance-template `--graceful-shutdown` with a max duration); verify MIG scale-in honours it. For GKE, grace ≥ maximum stream.

### E2-06 LB health checks and the declared store-gap semantics
**Scenario.** During a store outage longer than `RV_KS_STALE_MS` (5 s), or a flush/failover gap, every gateway's `/readyz` turns 503 (not fresh / kill switch stale). The v3 edge (passthrough NLB, or a GKE Service with eTP Local) health-checks `/readyz` and pulls every backend.
- Customers may then see TCP connect failures, timeouts or resets instead of the declared "503 + `Retry-After`" envelopes. The SDK maps those to `APIConnectionError` and retries with its own backoff.
- Recovery then also needs `interval × healthy-threshold` of health-check passes after the store heals.
- r2chaos noticed this in a dry run ("an LB would see every backend unhealthy"). Part 1 uses an nginx edge with passive health only, so the declared semantics pass through untouched there.

**Why v3 depends on it.** C36/D2 claims ("only declared 503s with Retry-After"; "recovered 0.02–1.57 s after heal") are measured behind nginx or directly. v3 serves through an NLB.

**Covered today?** No.

**Run spec** (**r2chaos Part 2** on the chosen platform's NLB edge).
- **Faults:** f5 iptables partition gateway↔store 60 s × 3, and f1b force-data-loss failover × 3.
- **Load:** 60% of the fleet knee, plus the K/R canaries.
- **Measure:**
  - client-side outcome mix: declared 503 envelopes vs connect-refused / timeout / reset / eof;
  - NLB backend health timeline (`get-health` every 1 s);
  - heal → first 200.
- **PASS:** during the gap clients see only declared 503 envelopes (0 connection-level failures); recovery ≤ 5 s + one health-check interval after heal.
- **If FAIL:** split liveness from readiness for store gaps, i.e. keep serving declared 503s. A v3 design item.

### E2-07 Zone loss against the real reserved-pool shape
**Scenario.** The L4 pool is a 1 / b 7 / c 7 (B35). Losing zone b or c removes 47% of guard capacity.
- Capacity cannot be replaced: reservations are zonal, and on-demand acquisition runs at about 2 L4 per hour under stock-out (v3_notes §4).
- A regional MIG with BALANCED shape was seen re-creating instances in a stocked-out zone every ~2 min instead of moving (FINDINGS_LEDGER.md:371).

**Why v3 depends on it.** v3 sizes guard capacity as reserved baseline plus headroom. Whether that headroom must be N+1 per zone is decided by this test.

**Covered today?** Partial. Part 2 has "zone loss", but not against the pool shape or the MIG distribution-shape comparison the controller asked for (FINDINGS_LEDGER.md:416).

**Run spec** (**r2chaos Part 2**, chosen platform, after E2-06).
- **Placement:** guards as the pool is (b + c), gateways in b + c.
- **Fault:** simulate loss of zone c by stopping every lane VM in c plus host-level iptables DROP from c for the gateways/edge, at 60% of the fleet knee.
- **Arms:** MIG/CA distribution shape ANY vs BALANCED (guards).
- **Hold:** 15 min.
- **Measure:**
  - admitted capacity and shed %;
  - C4 p99 of admitted;
  - FAIL_OPEN;
  - autoscaler behaviour (retries in c vs moves to a/b);
  - time until capacity from zone a's idle slot and any on-demand attempts;
  - which zones the Valkey/SQL primaries sit in (managed stores cannot be zone-failed by us; record them).
- **PASS:** 0 FAIL_OPEN; sheds declared; admitted C4 p99 < 20 ms; post-loss capacity equals the surviving reservations (a number v3 publishes).

### E2-08 Store data loss while Cloud SQL is unavailable
**Scenario.** A Memorystore maintenance or failover with data loss (or a flush) overlaps a Cloud SQL maintenance or failover.
- The re-hydrator cannot restore the store until Postgres returns. Every tenant therefore gets 503 from +5 s (`RV_KS_STALE_MS`) until SQL recovery + re-hydrate (2.1 s bound) + one refresh.
- Each is tested alone (f1/f1b, f2) with 10–11.6 s SQL outages. Maintenance windows default to "any time" unless configured.

**Why v3 depends on it.** C36's durability and availability claims assume the source of truth is up whenever the store needs repair.

**Covered today?** No.

**Run spec** (**r2chaos**, chaos set only, per B4).
- **Faults:** f2 (Cloud SQL failover); at +3 s, FLUSHALL `rv-r2-chaos-kv`. 3 instances, ≥ 2 min apart (409 cooldown).
- **Load:** 50% of knee plus canaries.
- **Measure:**
  - gap start and end vs the SQL outage;
  - status mix (must be declared 503 + `Retry-After`);
  - valid-tenant 401/403 (must be 0);
  - K/R admits (must be 0);
  - time from SQL recovery to restored.
- **PASS:** 0 violations; gap ≤ SQL outage + 2.1 s + 0.5 s.
- **v3 rule regardless:** non-overlapping maintenance windows for Cloud SQL and Memorystore, plus deny periods.

### E2-09 Rolling a new guard image, or a node upgrade, with the reserved pool full
**Scenario.**
- The GKE guard Deployment uses `maxSurge: 1, maxUnavailable: 0` (`r2-gke/manifests/20-sut.yaml.tmpl:40`). A surge pod needs a spare L4, and there is none when the pool is fully used and the zone is stocked out, so the rollout stalls.
- Node-pool auto-upgrades have the same need. The test cluster suppresses them with a maintenance exclusion (`r2-gke/scripts/platform_up.sh:20-24`).
- A MIG rolling replace with surge has the same problem; without surge it loses capacity.
- Every guard security fix, model or engine update, and driver update (round-1 E26) goes through this path.

**Why v3 depends on it.** An operations runbook for a fixed reserved pool.

**Covered today?** No.

**Run spec** (the platform currently holding the pool: **r2mig** after H, or **r2gke**; about 30 min).
- **Setup:** all reserved slots in use. Trigger a rollout of a new guard revision (same RC2 digest, one env change to force replacement).
- **Arms:**
  - **(a)** surge 1 / unavailable 0;
  - **(b)** surge 0 / unavailable 1.
- **Load:** 60% of the fleet knee.
- **Measure:**
  - rollout duration or stall (Pending surge pod / MIG action);
  - capacity dip;
  - infra %;
  - C4 of admitted.
- **PASS:** the rollout completes within a declared window with infra ≤ 0.1% at 60% load.
- **Else:** v3 must reserve a surge slot per zone and price it.

### E2-10 TLS termination placement
**Scenario.** v3 edge = L4 passthrough, which moves TLS into the tier behind the NLB (v3_notes §2, B22). Round 2 runs plaintext.
- The RC2 gateway cannot terminate TLS (`serve.py`: 0 ssl/tls references).
- Python TLS has already produced one v3 risk: redis-py's SSLContext per connection costs 12.4 ms of loop time each.
- Connection recycling (`RV_CONN_MAX_*`), which is mandatory for NLB balancing, multiplies handshakes. A synchronized max-age expiry or a failover causes reconnect storms.

**Why v3 depends on it.** The option A vs option B edge decision (NLB → gateways vs NLB → nginx tier) cannot be made without TLS cost at the knee.

**Covered today?** No.

**Run spec** (**r2mig**, its nginx edge VM, synthprov behind it, then the gateway).
- **Setup:** nginx TLS 1.3 (ECDHE P-256, session tickets on and off).
- **Rates:** 100/300/600 RPS × 3 vs plaintext.
- **Storm:** 1,000 new TLS connections within 1 s on top of 300 RPS.
- **Measure:** C4 p99, nginx CPU per request, handshakes/s.
- **PASS:** Δ C4 p99 ≤ 1 ms vs plaintext at 600 RPS; the storm keeps C4 p99 < 20 ms.
- **Option A** (TLS inside the Python gateway) is TEST-IN-PREPROD once implemented; it needs the same storm test.

---

## TEST-IN-PREPROD / ACCEPT-WITH-NOTE (brief)
- **E2-11 HMAC key rotation.**
  - USAGE:95: "Mixed-key record sets fail closed… the gateway stays not ready."
  - With one deployment-wide key, rotation equals a fleet-wide not-ready event.
  - RC3 adds a key id. The pre-prod test is: dual-key verification window, rotate under load, 0 not-ready seconds, 0 × 503.
- **E2-12 Provider silence > 120 s.**
  - RC2 still has aiohttp `sock_read` = 120 s and no total (USAGE:341; round-1 E20: 125 s silent TTFT cut at 120 s; non-stream 125 s → 502 then SDK retries).
  - v3 needs a per-plan idle timeout above the model's think time, and SSE comment heartbeats (`: ping`) to keep intermediaries alive during silence.
  - A declared maximum total stream duration is also needed, and must match E2-05.
- **E2-13 Output channels.** RC2 egress still has 0 references to logprobs, refusal or reasoning. Round-1 E1 (CRITICAL, client-controlled bypass of output REDACT), E4 and E5 are unchanged. This is a v3 blocker; functional pre-prod test after the fix, using the round-1 probe suite (`EV/p03-output`).
- **E2-14 Response size.** Non-stream upstream bodies are unbounded (`edge/respond.py:35`); round 1 measured worker RSS at +3.0× the body. A BYOK upstream can OOM a shared worker.
- **E2-15 Mass client disconnect.** A cancel storm (1,000 streams) hits provider and guard. The mechanism was proven at small scale; the pre-prod scale test must show no orphan work and complete audit.
- **E2-16 Budget leases.** 25,680 tokens per worker. At fleet scale, orgs with small budgets see worker-dependent 429s and overshoot (r2console: 1,106 requests over a 40-token budget; round-1 E17: a crashed lease holder loses its lease). RC3 revocation; pre-prod test with 36+ workers.
- **E2-17 Bulk onboarding.** The writer republishes the complete kind on every write and has no bulk path (`control/__main__.py` seeds 3 orgs). Migrating N existing v1 tenants and keys is O(N²) store writes; v3 needs a bulk import plus a single publish.
- **E2-18 Clock skew.**
  - Versions are logical (epoch, seq) from Postgres.
  - There is no cross-host wall-clock comparison in the C36 paths: `time.time()` appears only in logs/CLI output; the re-hydrator's "stale grace" is a sleep (`control/rehydrate.py:87-103`).
  - Residual wall-clock dependencies are IAM token expiry (TLS+IAM Valkey) and certificate validity. Keep NTP alarms; r2chaos preflight already checks offsets < 2 ms.
- **E2-19 Valkey primary moving zones.** +0.25–0.30 ms per round trip (r2-infra), 0–1 round trips per request. Negligible, except it multiplies E2-01's large transfers cross-zone.
- **E2-20 Quota exhaustion.** Seen at 06:38Z (MIG QUOTA_EXCEEDED) and a BALANCED MIG stuck in a stocked-out zone. v3 rule: per-environment quota budget = Σ max replicas × vCPU + surge, alerting on QUOTA_EXCEEDED / ZONE_RESOURCE_POOL_EXHAUSTED, and guard distribution shape ANY.
- **E2-21 Spot / flex / cross-region (r2-alt).**
  - Spot's ~30 s notice is shorter than `RV_GUARD_DRAIN_S` 30 plus REDIRECT.
  - On RC2, owner death turns in-flight guard calls into FAIL_CLOSED 403 `blocked_by_policy` (chaos f3; re-dispatch is RC3), so a preemption surfaces as false policy blocks.
  - DWS flex-start is queued and time-limited, so it suits only planned capacity.
  - Tokyo/Seoul have on-demand L4 (r2-alt survey table), but a cross-region guard adds RTT far above 20 ms, so only whole-stack regional failover is viable.
  - TEST-IN-PREPROD if adopted.
- **E2-22 Internet vantage.**
  - All edge numbers are in-region with a co-located synthetic provider.
  - For real customers, "AI-Mesh-added" time also includes the WAN detour (client → Mumbai → provider vs client → provider), which C4 does not see.
  - v3 must state the SLO scope, then measure from real vantage points with a real provider (T24 canary).
  - Optional cheap proxy now: a loadgen in another GCP region against the NLB IP. It only partly reflects the internet path.

## Top 5 by impact on v3
1. **E2-01 Many orgs/keys.** Per-worker O(N) kill-switch verification every 500 ms (57 ms at 10k records), an O(N) store transaction per config write (59.5 ms Redis block at 50k), and fleet-wide identity-cache flushes on every key write. This is a scaling cliff in the PROVEN state design that no lane can see with 3 orgs.
2. **E2-03 Long-tail prompt sizes.** Every capacity number in v3 (per-guard knee, $5k fleet) comes from HEADLINE ≤ 1,024 tokens. One 16-window request holds a FIFO owner for 34–44 ms, more than the whole C4 budget.
3. **E2-05 + E2-06 Drain and LB semantics.** The owner's "0 dropped streams on scale-in" and C36's "only declared 503s" both pass today only because streams are short and the chaos edge is nginx. The v3 NLB edge and real stream lengths can break both.
4. **E2-04 Client retries under shedding.** The C10 and config-E conclusions assume clients that never retry. The official SDK triples offered load within milliseconds of a shed.
5. **E2-02 + E2-09 Cold start and rollout under a full pool.** Autoscaled gateways start cold (RC2 stampede → 503), and guard rollouts or node upgrades need a surge L4 that a fully used reserved pool in a stocked-out region does not have.

## Evidence produced in this pass
- **RC2 source extract:** `EV/r2/rc2src/`, verified against `SP/evidence/r2-impl/rc2/MANIFEST.sha256` (171 files; manifest sha 6093c8dd10bd12f5d5058a963ac9db3ed9768985c4907d73d3a5272c01cff0fc).
- **`EV/r2/micro/verify_cost.py` + `.json`:** RC2 `verify_set` / plan-index digest CPU cost vs record count. Single thread on the controller (c4-standard-16), median of 5, no store/network time.
- **`EV/r2/micro/publish_block.py` + `.json`:** RC2-style full-kind MULTI/EXEC blocking time on a local redis:7.4 container (loopback, no replication = lower bound). The container was removed.
- **Round-1 evidence reused:** `EV/p08-noisy-neighbour` (SDK retry ×3), `EV/p09-tenant-scale` (plan reconcile vs tenants), `EV/p14-over-band` (multi-turn window growth), `EV/p03-output` (output channels), `EV/p11-long-streams` (120 s provider timeout), `EV/p15-huge-response`. All reproduced on rvproto-frozen-1 (`EV/rerun-frozen1/`).
