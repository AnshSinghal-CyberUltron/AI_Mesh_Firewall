# Round-2 interim contradiction review — pass 1 — lens: STATE PROPAGATION

Reviewer: reviewer-state-propagation · 2026-09-24, pass started 12:25Z, time-box ~60 min · read-only analysis.
- **Code:** RC2 (tarball `SP/evidence/r2-impl/rc2/rvproto2-rc2.tar.gz`, sha256 eb9d4da2…, verified), extracted to `EV/r2pass1/src`.
  All file:line references below are to that tree.
- **Read:** ledger §S, v3_notes, CONTROLLER_BULLETIN B1–B35, ROUND2_SPEC, r2-state runs (rc1-b/bgap, rc2-b-1 verdicts), r2-impl USAGE.
- **Paths:** EV = `SP/evidence/reviewer-state-propagation`. Local unit reproductions (real RC2 classes against a throwaway local
  Valkey 8.0 container, since removed) are in `EV/r2pass1/`.
- **Constraints:** no GCP resource, lane VM or lane store was touched; no IAM changes; no secrets printed.

Verdicts: **LIKELY** (mechanism in code, and reproduced locally or measured by a lane) · **POSSIBLE** (mechanism in code, not yet
shown) · **RULED OUT** (the code prevents it).

---

## SP1 — A NEW gateway process accepts a regressed (lagging-replica) store: revoked key admitted, killed org served — LIKELY
**Mechanism**
- The monotonic floor is per-process RAM and starts at ZERO: `admit/state_v2.py:38` (`VersionedIdentity.version = ZERO`),
  `:85` (`VersionedKillSwitch.version = ZERO`), and `plan/snapshot_v2.py:46`.
- `verify_meta(..., not_before=self.version)` (`runtime/versioned.py:100-114`) therefore refuses a regressed manifest only in a
  process that already applied the newer one.
- A worker started after the failover (crash restart, MIG/HPA scale-out, zone-loss recreation) has no floor. It verifies the older
  signed manifest as valid and serves the pre-write state: the key is not yet revoked and the kill switch is not yet engaged.

**Evidence for**
- `EV/r2pass1/repro_regressed_store_new_process.out` (RC2 StateWriter + MemDB + real Valkey). The lagging replica is modelled as
  DUMP/RESTORE of the store taken before write set B (revoke key R, engage org K):
  - EXISTING process after the failover: KS refresh REFUSED ("ks manifest 1.2 is older than 1.3"), org K still killed,
    key R → 503.
  - NEW process after the failover: refresh OK on manifest 1.2, `org_K_killed=false`, key R ADMITTED.
  - After the re-hydrator republishes, the new process converges (K killed, R 401).

**Evidence against / bounds**
- The exposure lasts until the re-hydrator's next round: `RV_REHYDRATE_PERIOD_MS` 1000 (`runtime/config_v2.py:62`), plus the
  250 ms "stale" grace (`control/rehydrate.py:90-92`), plus the new process's next refresh (≤ 500 ms). That is ≈ 1.75 s — but only
  if the re-hydrator is up and reachable (see SP2).
- r2-state's pre-RC notes show that a Memorystore Redis force-data-loss failover lost 0 acknowledged writes in the (a2) dry run.
  The replica-lag precondition may therefore be rare on Redis Standard HA, but zone loss and failover under write load can
  produce it.

**Why it matters for v3**
- Zone loss is the natural trigger. The Valkey primary and the zone's gateways die together. The platform recreates gateways (new
  processes, floor ZERO) while a lagging replica is promoted. If the single re-hydrator VM was in that zone too, there is no
  republish at all.

**Live test**
- r2state (a2), Redis STANDARD_HA force-data-loss: 20-write kill-switch/revocation storm straddling the failover (already planned),
  plus start ONE fresh gateway process (or `systemctl restart` one gateway) within 0–1 s after the failover completes.
  - Probe the storm's keys and orgs directly on the fresh gateway's port at 24/s.
  - PROVES: any 200 for a revoked key or killed org from the fresh process while the old processes return 503.
  - KILLS: 0 admits from the fresh process across 3 repeats. The run must also confirm the replica actually lost writes (compare the
    store manifests with Postgres rv2_version at +0.5 s); otherwise the run is inconclusive, not a PASS.
- Variant with the re-hydrator stopped for 30 s: the fresh process should admit for the whole 30 s.
- r2chaos Part 2 zone loss: record per-process first-refresh manifest versions and probe R/K on every recreated gateway.

**Fix direction**
- Give every process a durable floor. Options:
  - a signed "verified_at + versions" stamp that the re-hydrator writes each round, with gateways refusing state whose stamp is
    older than X s;
  - or fetch the floor from the control plane at start.

## SP2 — A committed-but-unpublished kill switch or revocation is invisible and NOT fail-closed while the single re-hydrator is absent — LIKELY
**Mechanism**
- The writer commits to Postgres, then publishes. A publish failure is reported honestly as `ok_publish_pending`
  (`control/writer.py:93-97, 110-119`), and only the re-hydrator republishes (`control/rehydrate.py:84-116`).
- Gateways verify the store against itself, never against Postgres. The older manifest verifies, refreshes succeed and `state()`
  stays "ok", so nothing ages or fails closed (`admit/state_v2.py:103-121`).
- Deployments run ONE re-hydrator on one control-plane VM. Ledger incident 09:06Z: the idle watchdog stopped `rv-r2state-cp-1`,
  which hosts it.

**Evidence for**
- `EV/r2pass1/repro_publish_pending_no_rehydrator.out`: the writer's store path is unreachable, so key revoke and org kill switch
  both report `ok_publish_pending`.
- For 6 refresh rounds, gateways show `ks_state=ok`, `org_K_killed=false`, key R ADMITTED, store manifests ks 1.2 / key 1.1
  versus Postgres 1.3 / 1.2. Nothing in the code bounds this.

**Evidence against / bounds**
- Bounded when the re-hydrator runs: period 1 s + 250 ms grace + one publish + one refresh ≤ 0.5 s.
- Any later successful write of the same kind also publishes the full kind, including this record.
- Running two or more re-hydrators is safe: WATCH-guarded publish and CAS budget repair; F-C36-RACE ran 2 re-hydrators.

**Live test** — r2state, own set, non-destructive to the store:
- Stop the re-hydrator.
- Block ONLY the control-plane VM → Valkey path (host iptables on cp-1; B7: not a VPC rule).
- Engage org K and revoke key R via the writer. Expect `ok_publish_pending`, exit 0.
- Unblock the writer path. Probe K and R for 60 s with the re-hydrator still stopped.
- Then start the re-hydrator and time restore → enforcement.
- PROVES: K and R admitted with `/readyz` 200 for the whole 60 s. KILLS: any fail-closed or enforcement before the re-hydrator
  starts.

**Fix direction**
- HA re-hydrators in ≥ 2 zones, plus the SP1 freshness stamp so gateways fail closed when the store has not been verified against
  Postgres for more than X s.
- Alarm on `ok_publish_pending` older than one period.

## SP3 — Kill-switch and revocation propagation (write→enforcement 73–724 ms measured) has four regimes; the worst case is not bounded — LIKELY
**Mechanism**
- Only plans get push nudges: `plan/snapshot_v2.py:152-153` accepts `b"plan"` / `b"all"`. Kill switches and keys are poll-only
  via `VersionedKillSwitch.run` every `RV_KS_REFRESH_MS` 500 (`admit/state_v2.py:123-134`, `runtime/config.py:41`).

| Regime | Condition | Bound |
|---|---|---|
| (a) | Publish OK | Writer (Postgres tx + a new connection for the snapshot read + Valkey MULTI) + ≤ 500 ms poll phase + refresh RTT. The measured 73–724 ms includes CLI start-up. |
| (b) | Writer publish failed | + re-hydrator period 1 s + 250 ms grace. Unbounded without it (SP2). |
| (c) | Store flushed / partially restored | The KS refresh also requires the KEY manifest (`admit/state_v2.py:114`). A kill switch written in the post-flush window is not applied until the key kind is republished too; with the re-hydrator stopped, the staleness ceiling (5 s) applies, then every request 503. |
| (d) | In-flight work | Never re-checked. No kill-switch or identity check in egress/dispatch (grep: no `org_killed` / `ks.state` in `egress/`, `edge/respond.py`, `dispatch/`), and the provider client has `ClientTimeout(total=None)` (`dispatch/provider.py:46`). Round-1 S7 and M3-E15 still open in RC2. |

**Evidence**
- Code as cited above.
- r2state rc2-b-1 writes a plan update in the gap (event `plan_update_issue … tag in_gap`) but no kill-switch or revocation write in
  the gap: storm writes = 0 in (b), and "admitted_after_ack_plus_1s" assumes a 1 s bound.

**Live tests**
- r2state (b) variant: at T+0.1 s after FLUSHALL, engage org K2 and revoke key R2. Measure first-503/401 time per worker from probe
  records (bound should be ≤ restore + 0.5 s); repeat with the re-hydrator stopped 10 s (expect ≤ 5 s ceiling, then global 503).
- r2chaos or r2state long-stream: 2,000-token SSE (ITL 20 ms, about 40 s) for org K3; engage K3 at +2 s. Count bytes delivered after
  the ack. PROVES regime (d): the stream completes with [DONE].

## SP4 — Budget leases: overshoot = Σ workers × chunk after a decrease; a flush restore double-counts up to one checkpoint period of whole-chunk grants — LIKELY
**Mechanism**
- `TokenLease` keeps granted chunks in process RAM (`admit/quota.py:35-74`). There is no link to the budget record version
  (`{rv2}:budget_gen`), no revoke, no return and no TTL.
- `budget_set` restarts the counter at the new limit (`control/writer.py:188-196`) and ignores both leases already held and
  consumption so far.
- After a flush, the counter is restored from the current-version checkpoint (`RV_BUDGET_CHECKPOINT_S` 5, `runtime/config_v2.py:63`)
  or else from the limit (`control/rehydrate.py:131-133`). Grants made since that checkpoint are handed out twice.
- Chunk = contract-derived: 25,680 tokens on the console lane; 205,440 per worker at a 24-vCPU gateway (round-1 p4e).

**Evidence for**
- r2console v2 path: budget cut to 40 tokens still admitted 1,106 requests (ledger §S line 42).
- Round 1: 92–93% of a 60,000-token budget stranded or lost across a restart.

**Evidence against**
- Aggregate issue never exceeds the store counter per budget version.
- The flush over-grant is bounded by one checkpoint period of grants, which can nevertheless be all W × chunk if every worker
  refilled in the last 5 s.

**Live test** — r2state:
- (i) Org Q budget 10 M; warm every worker (100 RPS × 60 s); `budget_set Q 1000`. Sum the admitted tokens after the ack from
  `quota_admitted_tokens` (per-worker exporter) and provider usage. PROVES: admitted ≫ 1,000, on the order of W × chunk.
- (ii) Budget-limited org at 100 RPS; FLUSHALL 1 s after a refill burst. Compare total admitted with limit and
  checkpoint + grants since.

**Fix direction**
- Tag leases with budget_gen and drop them on a gen change (delivered via the kill-switch refresh).
- Size the chunk from the remaining budget.
- Return leases on drain.

## SP5 — C43 backlog pushes go stale on a SILENT owner death: every gateway worker keeps routing to the dead owner until its own estimate grows — LIKELY (count unmeasured)
**Mechanism**
- `estimate_ns = max(backlog − elapsed, 0) + sent_since` (`detect/guard/ipc_client.py:92-97`). A dead owner's pushed backlog decays
  to 0, and `sent_since` rises only by what THIS worker sent it. Live owners reset theirs on every push (`:168-171`).
- Each worker therefore sends roughly 1–3 requests to the dead owner before it stops being the minimum.
- Those requests and the in-flight ones expire in the sweep at deadline + grace (`:239-254`, `:362-363`). The results are
  UNAVAILABLE, which the plan posture turns into FAIL_CLOSED `403 blocked_by_policy` or a FAIL_OPEN skip (USAGE §failure table).
- Silent death is detected by keepalive (idle `ks_stale/2` = 2.5 s + 2 × 1 s, `:110-114`) or by the sweep.
- The count scales with the number of gateway WORKERS: 108 at 3 gw × 36.

**Evidence for**
- r2chaos f3 dry run: owner SIGKILL (RST) and VM reset gave in-flight 403 blocked_by_policy.
- RC3 lists "owner-death re-dispatch" as not done.

**Evidence against**
- The attraction self-limits per worker (`sent_since` never resets without a push).
- SIGKILL is detected immediately by RST.

**Live test** — r2chaos Part 1 f3 variants at the per-guard knee with C43 ON (RC2), 4 owners × 3 gateways:
- (a) SIGKILL the owner;
- (b) silent death: `iptables -I INPUT -p tcp --dport 7070 -j DROP` plus OUTPUT drop on the owner VM, no RST;
- (c) the same with 12 vs 36 workers per gateway.
- Count per event: policy-posture outcomes, `sem:U`, and the time until the owner disappears from `/metrics`
  `guard_owners_routable`.
- PROVES: count(b) ≫ count(a) and count ∝ workers. KILLS: count(b) ≈ in-flight only.

## SP6 — Plan snapshot still runs push and periodic reconcile concurrently: a transient plan rollback (self-healing within one reconcile) and process-version regression — POSSIBLE
**Mechanism**
- `run()` (`plan/snapshot_v2.py:123-135`) and `_pushed()` (`:141-149`) both `await self.reconcile_once()`. Each copies
  `_plans` / `_invalid` / `_applied` after its MULTI (`:87`), awaits per-org GETs (`:97`, `:107`), then assigns all three plus
  `self.version` (`:101-102`). The slower task overwrites the faster one's newer plans and can move `self.version` back.

**Improvement vs round 1**
- `_applied` is now replaced together with `_plans`, so the reverted org is re-fetched on the next reconcile. The permanent wedge
  (round-1 S1) is gone; a ≤ 1 s rollback remains.

**Evidence**
- Code only; not reproduced in this time box.

**Live test** — r2state (d)-style bulk:
- Publish 2,000 org plans in one burst (one writer loop).
- Scrape each worker's `plan_version_info` every 100 ms for 10 s.
- PROVES: any per-worker per-org version sequence that goes backwards. KILLS: monotonic on all workers in 3 repeats.

## SP7 — Shared Valkey (`volatile-lru`, 10.4 GiB): guard owner registrations are the ONLY TTL keys, so they are evicted first when audit streams fill memory; then every lane's writes fail — POSSIBLE
**Mechanism**
- The only TTL keys are registrations (`detect/guard/discovery.py:76`, `SET ... px=RV_GUARD_REGISTER_TTL_MS` 3000).
- Audit streams: `RV_AUDIT_STREAM_MAXLEN` 2,000,000 per org (`runtime/config.py:44`), 2.89 KB per record, never evicted.
- At 600 RPS × 2 records/request ≈ 3.5 MB/s, one lane fills 10.4 GiB in ≈ 50 min without UNLINK. volatile-lru then evicts
  registrations: new gateways discover no owners and stay not-ready; existing gateways keep live links
  (`ipc_client.py:306-312`).
- After that, OOM write errors for all lanes: lease refill 503, audit loss, publishes pending, the re-hydrator cannot restore.

**Evidence for**
- The ledger §S tenancy rule exists because of the same arithmetic.

**Evidence against**
- The per-run export + UNLINK discipline (B3). Lanes' reported memory is not in the evidence I read.

**Live test** (controller / r2infra, read-only):
- Every 60 s during r2mig/r2gke/r2fleet runs, poll `INFO memory` (used_memory vs maxmemory) and `INFO stats` (`evicted_keys`) on
  rv-r2-valkey. Alarm at 70%.
- PROVES: `evicted_keys` > 0 or used ≥ 90%. KILLS: peak used < 50% over the platform phase.

## SP8 — Owner registration and discovery after a store failover (Valkey primary moves zones) — RULED OUT for correctness (residuals LOW)
**Mechanism that prevents it**
- A vanished registration never drops a connected owner (`ipc_client.py:306-312`, metric `guard_owner_kept_unregistered`).
- Owners re-register every TTL/3 = 1 s (`discovery.py:80-86`) through the stable PSC primary endpoint.
- Readers prune only ids whose key is gone (`:106-120`); a racing SREM is healed at the next beat.
- F-DRAIN (2.5 s wipe of all registrations) PASS on RC2; the post-zone-swap RTT is +0.25–0.30 ms, well under
  `RV_STORE_TIMEOUT_MS` 25.

**Residuals**
- An asymmetric owner↔store partition makes owners' registrations expire; NEW gateways cannot discover them and stay not-ready
  (fail-safe; capacity loss).
- A draining flag lost in a force-data-loss failover is covered by the owner's REDIRECT (`ipc_client.py:219-221`).

**Live test** — r2chaos Part 1 f5 variant: partition owners ↔ Valkey only (host iptables on guard VMs), then start one new gateway.
Expect: existing gateways serve normally; the new gateway stays `/readyz` 503 until the heal; 0 policy 403s.

## SP9 — NLB connection recycling vs stale gateway membership — POSSIBLE (scale-in); scale-out LIKELY without recycling (measured)
**Mechanism**
- `RV_CONN_MAX_REQUESTS` / `RV_CONN_MAX_AGE_S` default 0 = off (`runtime/config_v2.py:75-76`). Recycling marks `connection: close`
  (`edge/app.py:42`, `edge/connlimit.py`).
- Measured: a new gateway received 0–0.2 req/s, never balanced (r2mig dry-as-1).
- Scale-in: SIGTERM starts `RV_DRAIN_ACCEPT_S` 15 s of readyz 503 + `connection: close`, then accepting stops and in-flight work
  finishes ≤ `RV_DRAIN_S` 60 (`serve.py:206-243`, `config_v2.py:55-56`).
- If the NLB health check marks the backend down later than 15 s (interval × unhealthy threshold + propagation), new connections
  arriving after the accept window are refused.
- `ConnLimits` keys connections by client (ip, port) per worker; this is correct behind a passthrough NLB (client IP preserved).

**Live test** — r2mig scale-in step with recorded backend-service HC settings (interval, threshold):
- Count client connect-refused / RST / 502 in [T_sigterm, T_sigterm + 60 s].
- Repeat with HC interval × threshold set to 20 s: expect errors after +15 s.
- KILLS: 0 connection errors with the production HC.

## SP10 — Shared-store tenancy without RV_NAMESPACE (RC2) — POSSIBLE / LOW for round-2 numbers; LIKELY v3 gap
**Mechanism**
- Keys are hard-coded `{rv2}:*` (`runtime/store.py:30-41`) and separated only by Valkey DB index. The channel `{rv2}:updates`
  (`store.py:39`) is instance-wide.
- A foreign "plan" nudge triggers a reconcile (MULTI GET meta + HGETALL index) on every worker of every lane on the instance
  (`snapshot_v2.py:141-153`). The payload is only the kind name, so this means load amplification, NOT fail-closed (the r2fleet
  worry about foreign publishes causing 503s does not apply to RC2).
- Discovery sets are per DB, so no cross-lane owner routing as long as every owner's URL carries its lane DB.

**Live test** (owning lane on its own DB, A/B):
- Publish "plan" to `{rv2}:updates` at 50/s from a client on an UNUSED DB (e.g. 0) during a 5-min steady run.
- Compare `plan_push_applied`, loop lag p99.9 and C4 p99 against the same run without it.
- Also confirm every guard's registration lives in the lane DB (`SMEMBERS {rv2}:guard:owners` per DB, read-only).

## SP11 — Any key write (add or rotate, not only revocation) clears EVERY worker's principal cache: a miss stampede — POSSIBLE
**Mechanism**
- The key manifest version is the auth epoch (`admit/state_v2.py:8-9, 40-43, 120`). Any key-kind write bumps it, and within one
  refresh (≤ 500 ms) all workers drop all principals.
- RC2 functional found that a cold stack with 200 concurrent new clients exhausts the request-path store pool
  (`RV_STORE_TIMEOUT_MS` 25): 503 `shared_state_unavailable`. RC3 single-flight is not in RC2.

**Live test** — r2fleet or r2mig steady run with ≥ 1,000 distinct active keys:
- Issue `key_add` for an unrelated key every 10 s.
- PROVES: 503 `shared_state_unavailable` bursts or C4 spikes aligned with the adds. KILLS: none across 30 adds.
- The current olg load uses one key, so this is invisible today.

## SP12 — Guard model identity: discovery filters on an advertised label, not the loaded bytes (round-1 S10, still in RC2) — LIKELY
**Mechanism**
- `model_hash()` returns `RV_GUARD_MODEL_SHA256` from the env (`detect/guard/factory.py:27-31`); the owner advertises it
  (`owner.py:98`); discovery compares label to label (`discovery.py:130`).
- A guard node whose baked engine differs but carries the same pinned env is accepted.
- Round-1 reproduction: 22M→86M swap, same hash, injection BLOCK→ALLOW with `sem:E`.

**Live test** — r2chaos: start one guard with a different engine file (e.g. the 86M engine) but the fleet's
`RV_GUARD_MODEL_SHA256`. Expect `guard_owner_model_mismatch` = 0 and verdict drift on a canary injection set.

## SP13 — Manifest vs record ordering, concurrent writers, clock/epoch assumptions — RULED OUT
- Writers lock the kind's version row FOR UPDATE before upserting (`control/db.py:91-95`). Publishers read version FOR SHARE, then
  records, in one transaction (`:115-119`), so under READ COMMITTED a snapshot can never pair version N with records of N ± 1.
- Publish is WATCH-guarded and skips when the store holds a newer verified manifest (`control/writer.py:121-140`).
- store_ahead is repaired by an epoch bump (`rehydrate.py:95-97`); it may take several rounds if the store epoch is far ahead.
- No wall clock in version ordering: (epoch, seq) come from Postgres counters, and checkpoints are version-tagged
  (`rehydrate.py:133, 147-163`).
- Measured: F-C36-RACE 0/751 (RC1) and 0/360 (RC2) mislabelled manifests; r2state (d) ×3 PASS; rc2-b-1 violations {}.

---

## What a worker serves in the 0.27–0.42 s flush → re-hydrate window (RC2)

| Path | Served during the window | Evidence |
|---|---|---|
| Kill switch / plans | RAM snapshot (last verified); a killed org stays killed | `state_v2.py:103-134`, `snapshot_v2.py:123-135` |
| Cached principals | Served; revoked keys are never cached (`fetch` returns None), so no stale admit | `state_v2.py:49-70` |
| Uncached keys (valid or revoked) | 503 `shared_state_unavailable` (manifest missing) — never 401 | R: 4–8 × 503 per run in rc1-b-1 / rc2-b-1 |
| Lease refill | 503 (counter missing → −1); local leases keep being spent | `store.py:44-52`, `quota.py:57-66` |
| Writes landing in the window | Only their own kind is published; the kill switch needs the key manifest too (SP3c), so it applies after the key kind's restore | — |

Measured: 0 violations (rc1-b ×3, rc2-b-1). The window is safe for pre-existing state. The unsafe cases are new processes (SP1),
unpublished writes (SP2) and the coupling in SP3c.

## Top 5 by impact on v3
1. **SP1 — regressed store accepted by new processes.** Zone loss recreates gateways exactly when a lagging replica is promoted.
   v3 needs a durable version floor or a re-hydrator freshness stamp.
2. **SP2 — committed-but-unpublished writes invisible, with no fail-closed and a single re-hydrator.** v3: HA re-hydrators across
   zones, a freshness stamp that gateways enforce, and an alarm on `ok_publish_pending`.
3. **SP3d + SP3a/b — kill switch and revocation do not reach in-flight work, and propagation is poll-only.** Declare and prove per-regime
   bounds; add a per-piece kill-switch check in egress and a max stream duration.
4. **SP4 — budget leases are not revocable, and flush restores double-grant whole chunks.** Gen-tagged leases, budget-sized chunks,
   return on drain.
5. **SP5 — silent owner death + stale C43 state turns an infra fault into ~1–3 policy outcomes per gateway worker.** Owner-death
   re-dispatch (RC3), and count ∝ workers must be measured before sizing workers per gateway.

Next: SP7 (shared-store eviction of discovery keys) and SP11 (key-write cache stampede).
