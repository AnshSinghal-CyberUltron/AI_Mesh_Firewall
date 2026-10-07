# r2 contradiction review, pass 1 — hidden failures (reviewer-hidden-failures, 2026-09-24 ~12:25–12:45Z)

**Lens:** assume the prototype and the v3 conclusions are broken. Look for failures that the round-2 runs cannot see:
- 5-minute windows;
- a HEADLINE corpus of 1,024 tokens or fewer;
- open-loop clients that never retry;
- 1–2 orgs and one API key per lane;
- audit streams UNLINKed after every run.

**Build reviewed:** RC2 = PROVEN. The tarball is `SP/evidence/r2-impl/rc2/rvproto2-rc2.tar.gz` (sha256 eb9d4da2…, re-verified). I extracted it to `EV/r2-rc2src` (EV = `SP/evidence/reviewer-hidden-failures`). File:line references below are to that tree.

**Everything I ran is local.** The only exception is reading raw run summaries. Scripts and outputs are in `EV/r2p1/`. The throwaway `redis:7.4` container `rv-review-oomredis` (maxmemory 48 MB) was created and removed, and both events are logged in `EV/e2e/resources.log`. I did not touch GCP resources, lane VMs, IAM or secrets.

**Verdict scale:**
- LIKELY: proven by execution of RC2 code or by code reading with no counter-evidence.
- POSSIBLE: a mechanism exists but its size is unmeasured.
- RULED OUT: the evidence says no.

---

## H1 — Audit streams fill Memorystore. volatile-lru then evicts ONLY the guard-owner registrations, and every control-plane publish is refused (kill switch / revocation never enforced) — LIKELY (hours-scale)

**Mechanism**
- The audit sink XADDs every DecisionRecord (2 per request) into `rv:audit:<org>` with MAXLEN≈2,000,000 per org.
  - `audit/sink.py:87-88`; `runtime/config.py:44`.
  - At 2.89 KB/record that is ≈ 5.8 GB per org stream.
- Streams have no TTL. `rv-r2-valkey` runs `volatile-lru` with maxmemory 10.4 GiB (r2-infra READY).
- The only keys WITH a TTL are the guard-owner registrations: `SET … PX` in `detect/guard/discovery.py:76` (`beat()`).
- When memory is full:
  - the owner registrations are evicted first;
  - every re-registration is refused with OOM;
  - every denyoom write is refused: audit XADD, writer and re-hydrator publish (SET/HSET MULTI), a new key/budget.
  - Reads still work.

**Consequences**
- Discovery reports 0 owners. Existing gateways keep their links (`guard_owner_kept_unregistered`), but scaled-out or restarted gateways and restarted owners cannot join.
- A kill switch or key revocation commits to Postgres, but its publish is refused. The writer reports `ok_publish_pending`, and the re-hydrator's republish is refused too.
- The gateways keep reading the OLD manifest, which is valid and fresh, so they keep serving the pre-kill state indefinitely. The killed org is admitted and the revoked key is accepted.

**Evidence for**
- `EV/r2p1/oom_chain.out` and `oom_chain2.out`: local redis 7.4, volatile-lru, the RC2 key shapes and scripts (`LEASE_LUA_V2`, discovery beat, MULTI publish).
  - Audit XADD was refused after 15,780 records.
  - `evicted_keys=6`, and the owner registrations went from 5/5 alive to 0/5.
  - Then for 6 s: every heartbeat SET PX was refused (OOM), discovery saw 0/5 every second, writer publish was refused, and XADD was refused 200/200.
  - Lease refill (DECRBY on an existing key) still succeeded.
- Rate arithmetic: at the C43-1 load of 520 RPS, about 1,040 records/s reach one org's stream. That stream hits its 2 M cap (≈5.8 GB) in about 32 min, so two busy orgs exceed 10.4 GiB within about an hour.
- ROUND2_SPEC tenancy rule: "export audit streams then UNLINK after each run". No run can reach this state.

**Evidence against**
- v3 already plans a durable audit sink and trim policy (C41, RC3 audit spool). But neither the plan nor the notes record that exhaustion evicts the discovery registry and blocks kill-switch publication.
- Memorystore's exact eviction behaviour at maxmemory was not tested on Valkey 8. Redis 7.4 is a stand-in with the same policy.

**Live test while the infrastructure is up** (r2chaos Part 1 or r2state, on the lane's OWN store set; never on the shared set)
- Setup: 1 gateway + 1 guard at 300 RPS, 2 orgs (two olg loadgens, auth-a and auth-b), `RV_AUDIT_STREAM_MAXLEN` at its default, NO UNLINK, run until `used_memory` ≥ maxmemory (about 60–90 min at 300 RPS × 2 records).
  - To shorten it, lower RV_AUDIT_STREAM_MAXLEN is NOT equivalent. Instead pre-fill with a ~9 GB dummy stream (`XADD` loop) and then run 10 min.
- Record:
  - `INFO memory` / `evicted_keys` / count of `{rv2}:guard:owner:*`;
  - a writer `kill-switch org K on` issued at full memory, with its status and the gateway admits of org K 30 s later;
  - start one extra gateway: does it become ready?
- **Proves:** 0 registrations, publish refused, org K still admitted.
- **Kills:** registrations survive and the kill switch is enforced.

---

## H2 — Owner-shed Retry-After is milliseconds. The OpenAI SDK retries twice after 5–60 ms, so overload attempts roughly ×2.6, and every attempt burns full gateway input CPU before being shed — LIKELY

**Mechanism**
- The owner shed returns Retry-After = backlog drain time, at least its own execution time (`owner_queue.py:117`), i.e. 5–60 ms.
- The gateway sends `retry-after-ms` = ceil(ms) (`edge/errors.py:22-23`).
- openai-python 2.38.0:
  - reads `retry-after-ms` FIRST and honours any value in (0, 60] s (`_base_client.py:737-788`);
  - retries ≥500 (`:823`);
  - default max_retries = 2 (`_constants.py:10`).
- Canonicalization, tokenization and the deterministic scan all happen before the owner decides to shed (`edge/chat.py` order).
- Fixed point for a 3× step with immediate retries:
  - attempts ≈ 7.9 μ against 3 μ offered;
  - the per-attempt shed probability rises from 0.67 to about 0.87;
  - the final failure rate is about the same (≈0.66), so retries buy almost nothing.
  - Gateway input CPU for shed attempts is about 2.6× higher, which can saturate gateways and push up the C4 of admitted requests.

**Evidence for**
- Ledger overload run: "503 + Retry-After 1 + retry-after-ms 5–11".
- SDK source checked in the venv.
- olg has no retries, so every overload and step result so far is a no-retry measurement.

**Evidence against**
- Clients other than the SDK may ignore `retry-after-ms`.
- The gap-503 (store) path uses 2 s, which is fine. Only owner sheds are ms-scale.

**Live test** (r2unit split, RC2 defaults, the existing 97 / 414 / 97 RPS overload profile)
- Drive the same schedule with a retrying client: e.g. 400-coroutine `AsyncOpenAI(max_retries=2)` replaying the olg corpus at the scheduled times, or add retry-after-ms honouring to olg (retries recorded as separate rows with a parent id).
- Compare with olg:
  - gateway request rate (exporter `requests_per_s`) against offered;
  - gateway CPU-ms per SUCCESSFUL request;
  - admitted C4 p99;
  - time to recover after the burst.
- **Kills:** attempts/s ≈ offered × (1 + small), and gateway CPU < 70%.
- **Fix if proven:** shed Retry-After ≥ 1 s with jitter, or `x-should-retry: false`, or shed at the gateway before input work.

---

## H3 — CoDel with long-prompt or mixed tenants sheds >0.1% at 30% GPU utilisation; long prompts act as a noisy neighbour; HEADLINE Poisson at 90% of the knee is also at risk — LIKELY (long/mixed), POSSIBLE (HEADLINE C10 criterion)

**Mechanism**
- Parameters: hard cap 60 ms of work (`owner_queue.py:59,96-97`), target 5 ms, interval 100 ms (`:98`).
- A 16-window prompt (~7k tokens) runs about 34 ms on an L4, so it can only enter a queue whose backlog is below 26 ms.
- Its execution alone keeps every request behind it above the 5 ms target. Once 100 ms passes that way, the owner is "overloaded" and sheds any arrival while the backlog is ≥ 5 ms.

**Evidence for** (`EV/r2p1/codel_mix_sim.out`): the REAL RC2 `OwnerQueue` on a virtual clock, one FIFO executor at 2.15 ms per 512-token window (guard-bench L4), Poisson arrivals.

| Mix | ρ 0.3 | ρ 0.45 | ρ 0.6 | ρ 0.75 | ρ 0.9 |
|---|---|---|---|---|---|
| A — HEADLINE (100–1,024 tok) | 0% | 0% | 0.17% | 3.6% | 14.6% |
| B — 25% of prompts 4k–8k tok | 2.7% (short 0.4%, long 9%) | 4.4% | 6.4% | — | — |
| C — all 2k–4k tok | 0.43% | 1.7% | 5.2% | — | — |

**Evidence against**
- Every round-2 corpus is ≤ 1,024 tokens (T01 band). The "sim" arrivals are Poisson, while lane knees use constant arrivals.
- The model over-predicted sheds compared with round-1 reality at low ρ, by about 2–4×. But even a 4× over-prediction leaves mix B above 0.1% at ρ 0.3.

**Live test (r2unit split, RC2 defaults, while it runs its C10 codel arm)**
- (a) Report `owner_shed_reason{standing_queue|hard_cap}` for the planned Poisson@90%-of-constant-knee runs. If the constant knee sits at ρ ≥ 0.67, this model predicts FAIL.
- (b) Add ONE long-prompt corpus: `make_corpus.py` with 25% of prompts 4k–7k PG2 tokens (≤ 16 windows), 75% HEADLINE. Poisson at ρ 0.3 and 0.45, computed from the mean windows per request.
- **Kills:** infra ≤ 0.1% in (b) at ρ 0.3.
- **v3 impact:** the capacity model derived from HEADLINE does not transfer to long-context tenants. You need per-tenant or per-size admission (separate queues or cost-aware targets), or reject >N-window prompts by contract.

---

## H4 — The signed "holdback ≤ 3 upstream tokens" is not implemented and not measured; long word-runs are held whole and rescanned O(n²) — LIKELY (code and execution)

**Mechanism**
- RC2 `detect/holdback.py` is byte-identical to round 1.
- `egress/stream.py:_feed` is unchanged except for an experiment flag:
  - `hold_start` holds the whole trailing `[A-Za-z0-9._%+@/=-]` run with no bound;
  - every chunk rescans ctx+pending (`stream.py:223`).
- The ceiling is `stream_buffer_bytes`, which is 47 GB on this host (75.9 GB on the unit), compared against a character count. It never binds.
- `c4_client_all.py` measures d_k from the NEWEST covering piece, so any hold is excluded by construction.
- The HEADLINE provider emits 119 English words of ≤ 8 characters, so it never holds more than 2 tokens.

**Evidence for** (`EV/r2p1/p04_holdback_rc2.out`, RC2 code, o200k tokens, ITL 20 ms)

| Content | Longest hold (upstream tokens) |
|---|---|
| prose | 2 |
| markdown URL | 15 |
| UUIDs | 36 |
| sha256 | 34 |
| file paths | 15 |
| JSON | 7 |
| code | 4 |
| number table | 3 |

- Base64: at 2 / 8 / 16 KB the WHOLE blob is held. CPU per stream is 0.25 / 3.15 / 13.0 s (quadratic), with per-chunk loop blocks of 0.46 / 1.9 / 17 ms. Those blocks land in the C4 of every other stream on the same worker.

**Evidence against:** none. The owner decision says holdback is "reported separately", but nothing reports it in tokens, and realistic outputs are never exercised.

**Live test** (r2unit all-in-one, RC2, 200 RPS HEADLINE)
- Replace 1% of provider outputs with a base64/UUID-heavy output. A synthprov inject kind is needed, or add a second provider (`EV/probes/fakeprov_lp.py` style) on 10% of loadgen targets.
- Report C4 p99 of the OTHER streams, loop-lag p99.9, and max held upstream tokens per stream (harness `lag_max` / ITL).
- **Kills:** C4 unchanged and every hold ≤ 3 tokens.
- **Fix:** bounded pattern lengths plus a hard hold cap in tokens (the signed bound), with a tail-window rescan.

---

## H5 — Any key write anywhere flushes every worker's identity cache, which can turn into a cold-cache stampede of 503 shared_state_unavailable — POSSIBLE (LIKELY at tenant scale)

**Mechanism**
- `VersionedIdentity.on_epoch` clears the whole cache when the key-manifest version rises (`admit/state_v2.py:40-42`, called from `:120` on every kill-switch refresh).
- The key-manifest version rises on EVERY key add or revoke.
- The RC2 functional run found that a cold stack plus 200 concurrent new clients gives 503 `shared_state_unavailable`: store-pool exhaustion at a 25 ms wait (`RV_STORE_TIMEOUT_MS`). The fix, single-flight, is RC3.

**Evidence against**
- With few distinct keys, a flush costs about one refetch per key per worker. Every lane uses ONE key per org, so no run can show this.

**Live test** (r2fleet u1 at 90% of the knee)
- Seed 2,000 keys. Use 8 olg loadgens with 8 different keys, or add per-request key rotation to olg.
- Run the RC2 writer adding one new key per second for 5 min.
- Measure `identity_fetches`, 503 shared_state_unavailable and C4 p99 against a no-writes control.
- **Kills:** 0 × 503 and a C4 delta < 1 ms.

---

## H6 — Background refresh cost grows with durable record count (kill-switch set every 500 ms, plan index every 1 s, per worker, on the event loop) — LIKELY at ≥ 1k records

**Mechanism**
- `VersionedKillSwitch.refresh_once` runs HVALS on ALL kill-switch records, then `verify_set` does parse + sha256 + HMAC per record + a sorted digest (`state_v2.py:106-110`; `versioned.py:117-127`).
- Explicit OFF records are kept forever, so the set only grows.
- `VersionedPlanSnapshot.reconcile_once` parses and digests the whole plan index every second and on every push (`snapshot_v2.py:80-85`).

**Evidence for** (`EV/r2p1/ks_refresh_cost.out`, RC2 code)

| Records | Kill-switch verify per 500 ms | Plan parse + digest | Kill-switch payload per refresh, per worker |
|---|---|---|---|
| 1k | 6.8 ms | — | — |
| 10k | 59 ms (12% of a core, a 59 ms loop block twice a second) | 10.8 ms | 2.5 MB |
| 50k | 314 ms | 64 ms | — |

- At 10k records, 2.5 MB per refresh per worker is about 90 MB/s per 18-worker unit.
- Round-1 P08 measured the same shape on the plan reconcile (10k orgs: a 7 ms stall every second).

**Evidence against:** round 2 runs 3–5 records, so nothing measured contradicts this and nothing measured tests it.

**Live test** (any C38 point, RC2)
- Seed 10k org OFF kill-switch records and 10k plans into the lane's own DB through the writer.
- Re-run one C38 point (all-in-one, 200 RPS). Compare loop-lag p99.9, C4 p99 and Valkey network out.
- **Kills:** loop-lag p99.9 < 2 ms and C4 unchanged.

---

## H7 — The re-hydrator is a single point of failure with unbounded blocking; after a store data loss the whole fleet returns 503 for as long as it is stalled — LIKELY

**Mechanism**
- One synchronous loop per deployment (`control/rehydrate.py:run`; r2state cp-1, r2mig control VM).
- Postgres access has `connect_timeout=5` only (`control/db.py:137`): no statement_timeout, lock_timeout or tcp_user_timeout.
- `snapshot()` takes `FOR SHARE` on rv2_version (`db.py:117`), which waits behind any writer's `FOR UPDATE` (`db.py:93`).
- So an idle-in-transaction writer (a CLI SIGSTOP'd or partitioned mid-write; server-side keepalive is about 2 h by default) blocks re-hydration indefinitely.
- Detection is O(records) per kind per second (full snapshot + digest).
- Zone loss can take out the Valkey primary AND the Cloud SQL primary together. Recovery then equals the Cloud SQL failover (10–15 s) plus a re-hydrator round, and for the whole of it every tenant gets 503. The gap test already showed all tenants 503 from +5 s.

**Evidence against**
- F-C36-RACE proved that two re-hydrators can run concurrently and safely, so HA is possible. It is simply not deployed or specified.

**Live test** (r2state, own set)
- (a) `psql`: `BEGIN; SELECT * FROM rv2_version WHERE kind='ks' FOR UPDATE;` and leave the session idle. Then FLUSHALL.
  - Expect no `rehydrated` events and only 503s until COMMIT. Measure the gap.
- (b) The same with 2 re-hydrators running: does the second one also block?
- **Kills:** re-hydration completes within its bound despite the held lock.
- **Fix:** `SET lock_timeout` / `statement_timeout` / `idle_in_transaction_session_timeout`; ≥ 2 re-hydrators in separate zones.

---

## H8 — Guard deadline expiry turns into UNAVAILABLE, and so FAIL_OPEN, when the real GPU rate falls below the warm-up rate — POSSIBLE

**Mechanism**
- Owner admission models the backlog with the tokens/s measured once in a 1 s warm-up (`local_onnx.py:110-121,130`). It is never re-measured.
- The codel hard cap is 60 ms of MODELLED work, against a guard budget of 100 ms (`config_v2.py:277`).
- The batcher expires items past the deadline at dequeue (`batcher.py:181-184`). The result is UNAVAILABLE: FAIL_OPEN tenants skip the detector and FAIL_CLOSED tenants get a false 403. This is not a shed.
- The worker-side sweep also returns UNAVAILABLE after twice the budget (`ipc_client.py:239-254`).
- Throttled clocks (L4 power cap: guard-bench saw 1,395 → 630–870 MHz) would make 60 ms of modelled work longer than 100 ms of real time.
- `RV_INJECT_OWNER_DELAY_MS` CANNOT test this: the delay is included in the warm-up rate (`local_onnx.py:106-107`).

**Evidence against**
- 0 deadline expiries in every RC1/RC2 functional run and lane run so far, all short and on cool GPUs.

**Live test** (r2unit split, RC2, 2× overload, with a FAIL_OPEN org key)
- After the owner is READY, lock the GPU clocks: `sudo nvidia-smi -lgc 600,600` (or `-pl 40`).
- Count `guard_deadline_expired`, `guard_owner_timeouts`, and 200s with `sem:U`.
- **Kills:** 0.
- **Fix:** EWMA-updated tokens/s, and expiry turning into re-dispatch or 503, never UNAVAILABLE.

---

## H9 — Lease drift under autoscaling: every gateway worker exit strands its budget chunk — LIKELY

**Mechanism**
- There is no lease return path (`admit/quota.py` has only `try_local` / `refill`). Drain and crash (`serve.py`) do not return leases.
- The chunk is 25,680 tokens on c4-hc-16 gateways, 205,440 on g2-24 units (round-1 contract logs).
- Every scale-in, rolling deploy or crash strands workers × chunk tokens per org.
- Lease revocation on budget decrease is RC3 (r2console: a 40-token budget still admitted 1,106 requests).

**Live test** (r2mig step series)
- Record the Valkey `{rv2}:budget:<org>` counter before and after 3 scale-out/in cycles, against the sum of admitted costs from the olg rows.
- Report stranded tokens per scale event.
- **Kills:** stranded ≈ 0.

---

## H10 — Writer "unknown" is honest but unresolved; during a Cloud SQL failover most "unknown" revocations did NOT apply — LIKELY (operational security)

**Mechanism**
- The RC2 writer returns ok / ok_publish_pending / unknown / error. There is no read-back (the c36m patch "lost-COMMIT read-back" is not in RC2).
- USAGE wrongly says `verify` resolves unknown writes; only rv2_log does (c36m finding).
- c36m measured 90 "unknown" in 60 s of connection cuts, of which 86 had rolled back.
- So an "unknown" kill switch or revocation during the exact incident window is most likely NOT in force, and nothing retries it.

**Live test** (r2state (e) Cloud SQL failover ×3 on RC2, with its 20-write kill/revoke storm)
- For every "unknown", check rv2_log afterwards, and check gateway enforcement 10 s after recovery.
- Report the number of revocations reported "unknown" and not applied.
- **Kills:** every unknown was applied or auto-resolved.

---

## H11 — "0 dropped streams on scale-in" is only true for ≤ 8 s streams — LIKELY

**Mechanism**
- `RV_DRAIN_S`=60 (`config_v2.py:56`), plus the GCE/MIG shutdown window (~90 s, v3 notes §5; RC3 plans 300 s + terminal SSE error).
- HEADLINE streams last at most about 150 ms + 400 × 20 ms ≈ 8.2 s.
- Reasoning or long-output streams run for minutes and would be cut.

**Live test** (r2mig scale-in)
- Add a 5% long-stream cohort (`x-synth-tokens: 6000` → 120 s at ITL 20).
- Count cut or terminal-error streams in that cohort against the short cohort.
- **Kills:** 0 cut in the long cohort.

---

## H12 — Connection recycling (RV_CONN_MAX_AGE_S / MAX_REQUESTS) is measured without TLS — POSSIBLE

**Mechanism**
- Each recycle is a new client connection (`edge/connlimit.py`; the header only at response start, so SSE is not cut — that part is RULED OUT).
- olg counts connect time in T_addon_total.
- The v3 L4 edge moves TLS into the gateways (B22), and round 2 is plaintext. The handshake CPU on the gateway loop and the extra RTTs are unmeasured.

**Live test** (r2mig E config)
- `RV_CONN_MAX_AGE_S=30` with uvicorn TLS on the gateways against plaintext at the same rate.
- Report C4 p99, CPU-ms/req and per-backend balance.
- **Kills:** ΔC4 < 1 ms and ΔCPU < 5%.

---

## H13 — A shed goes to one owner and is never retried at another — POSSIBLE

**Mechanism**
- `ipc_client._deliver` resolves a SHED straight to 503. Only a REDIRECT re-routes (`ipc_client.py:210-236`).
- With C43 estimates up to one push stale, a request can be shed at a momentarily overloaded owner while another owner is idle.

**Live test** (r2fleet u4 Poisson on RC2)
- For each owner shed, join the other owners' backlog at that instant (attribution records or owner histograms).
- **Kills:** < 10% of sheds occurred while another owner's backlog was < 5 ms.

---

## Checked — RULED OUT or low

- **D2/D1 coverage for Redis — RULED OUT.** Every Redis client in RC2 goes through `runtime/storeconn.py`: `client` / `sync_client` with explicit timeouts. That covers discovery (`discovery.py:66`), plan/identity/kill switch, audit and the control plane. The pub/sub path is `runtime/push.py` with no `listen()`. The remaining non-Redis idle sockets:
  - Postgres (H7);
  - the provider pool: `sock_read` 120 s, `total=None` (`dispatch/provider.py:41-46`). A silently dead pooled provider connection hangs a request up to 120 s. LOW.
- **"A shed means 503, never FAIL_OPEN" — TRUE for sheds** (`chat.py:234-235`). The FAIL_OPEN exposure is on non-shed guard failures, all of which become UNAVAILABLE and then posture:
  - owner death / link loss (`ipc_client.py:161` fail_all; RC3 re-dispatch);
  - worker sweep timeout at twice the budget (`:239-254`);
  - batcher deadline expiry (H8);
  - no routable owner (`:385`);
  - redirect exhaustion (`:397`);
  - an invalid result (`:227`).
  - Store gaps do not produce FAIL_OPEN: kill switch, plan and identity all fail closed with 503 plus Retry-After (verified by c36m).
- **C4 script silently drops `sse_unmappable` streams** (not counted as +inf) — `c4_client_all.py:86-88`. 0 in all 103 round-2 C4 outputs found, so RULED OUT for current runs. It stays latent for any run with a sample-mod mismatch; assert it equals 0.
- **Other metric blind spots (context, not bugs):**
  - policy blocks (benign PG2 false positives: 208–1,036 per run, about 1.2–1.6%) are excluded from both C4 and infra;
  - lane C4 knees use constant arrivals; Poisson is only at 90%;
  - the `x-rv-stages` header still hard-codes `canon:E,det:E` (`chat.py:216`, round-1 finding A8).

---

## Top 5 by impact on v3

1. **H1 — audit-stream memory exhaustion.** volatile-lru evicts only the guard registry, and kill switch / revocation publishes are refused while gateways keep serving the old state. Security and availability, hours-scale; no round-2 run can see it (UNLINK per run).
2. **H3 — CoDel with long-prompt or mixed tenants.** More than 0.1% sheds at ρ 0.3, and a noisy-neighbour effect on short prompts. The HEADLINE-derived capacity and $/RPS do not transfer; the C10 HEADLINE Poisson@90% criterion is also at risk above ρ ≈ 0.6.
3. **H2 — client retry amplification.** The SDK honours `retry-after-ms`, so 5–60 ms owner Retry-After produces immediate retries: about 2.6× attempts with no success gain, and each shed attempt costs full input CPU. The 3× step pass bar was measured with a client that never retries.
4. **H4 — the signed ≤ 3-token holdback is not implemented, and C4 cannot see it.** URL 15, UUID 36, base64 whole-blob holds; O(n²) rescans block the worker loop by 17 ms per chunk on a 16 KB run.
5. **H7 / H6 — control-state scaling and the re-hydrator single point of failure.**
   - An unbounded Postgres lock wait stalls re-hydration, so every tenant gets 503 after a store data loss.
   - Refresh and verify cost grows with record count: 59 ms loop blocks twice a second per worker at 10k kill-switch records.

Next after the top 5: H8 (FAIL_OPEN via expiry under GPU throttling), H10 (unknown revocations not applied), H9 (lease drift), H11 (long streams at scale-in), H5 (identity cache flush stampede).
