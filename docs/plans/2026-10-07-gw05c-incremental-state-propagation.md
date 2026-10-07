# R2-02 / GW05c — Incremental state propagation: investigation and implementation plan

Date: 2026-10-07 · Branch: `suraj-revamp` @ `acae58f5` · Status: **investigation only, no code changed**
Card: GW05c (runbook v3 §0.4, line 214) · Depends on GW05, GW05b · Required by GW06, GW14d, GW20

---

## 1. Executive summary

R2-02 is real, reproduced, and its mechanism is fully located in code. Three independent defects
make serving cost grow with tenant count, and a fourth makes it grow with write rate:

| # | Defect | Where | Cost shape |
|---|---|---|---|
| D1 | Kill-switch refresh re-reads **and HMAC-re-verifies the entire record set** every 500 ms, inline on the serving event loop | `rvproto/admit/state_v2.py:103-121` (`VersionedKillSwitch.refresh_once`) | O(ks records) CPU × 2/s per worker |
| D2 | Plan reconcile re-reads the **whole plan index** and recomputes its **digest over every entry** every 1 s, inline on the serving event loop | `rvproto/plan/snapshot_v2.py:77-103` (`VersionedPlanSnapshot.reconcile_once`) | O(orgs) CPU × 1/s per worker |
| D3 | Every single-record write **republishes the complete kind** (`DELETE` + `HSET` of all records) in one `MULTI` | `rvproto/control/writer.py:121-165` (`StateWriter.publish` / `_stage`) | O(records) bytes + a store-wide stall per write |
| D4 | The key manifest version **is** the auth epoch, so any key write **wipes every worker's whole identity cache**, and cold misses have **no single-flight** | `state_v2.py:40-43` (`on_epoch`), `admit/admission.py:37-51` | O(active keys) store round trips per key write |

The measured consequence (round-2 lane `r2scale`, E2-01, 1 gateway × 12 workers + 1 guard,
120 RPS, 2,000 active keys — `evidence/.../r2-scale/levels/N{1,2,3}.analysis.out`):

| Level | Tenants | C4 p99 (A/B/C/D) | ks refresh | plan reconcile | publish p50 | loop lag p99.9 | store trips/req |
|---|---|---|---|---|---|---|---|
| N0 | 3 | 11.5–12.6 ms | — | — | — | — | — |
| N1 | 1 000 | 18.9 / 19.9 / 19.6 / 19.0 | 8.5–8.7 ms | 3.1–3.4 ms | 30–76 ms | **6.65–7.05** | A 0.134 / **B 0.986** |
| N2 | 10 000 | 145 / 153 / 150 / 147 | 74–76 ms | 24.6–25.5 ms | 114–623 ms | 79–84 | A 0.134 / **B 0.985** |
| N3 | 25 000 | 417 / ∞ / 459 / 406 | 203–223 ms | 70–73 ms | 255–**1 724** ms | 223–240 | A 0.132 / **B 0.969** |

Four things in that table decide the plan:

1. **Phase A (steady, zero writes) already fails at N2.** The periodic O(N) refresh is the
   primary cause; publish and cache-wipe are amplifiers, not the root.
2. `ks_cpu` tracks `ks_ms` almost exactly (189 of 212 ms at N3) — the refresh is **CPU on the
   loop**, not store latency. No amount of store tuning helps.
3. **Loop lag p99.9 already fails at N1** (6.7 ms > 5 ms) while C4 p99 still passes at 19.9 ms.
   N1 is not a pass with margin; it is a pass on the edge of two different gates.
4. **Publish already fails the ≤ 25 ms gate at N1** (p50 30–76 ms). D3 is not only a 25k problem.

The v3 rewrite (`gateway_v2`, GW00–GW05 landed) has **already reproduced D2** in
`gateway_v2/plan/snapshot.py:35-39` — `reconcile()` iterates `PlanStore.known()`, which is
`tuple(sorted(self._tenants))` over every tenant, under a lock that `lookup()` also takes. It has
no store layer at all yet (`pyproject.toml` → `dependencies = []`), so D1/D3/D4 do not exist there
yet. GW05c is therefore partly a *fix* and mostly a *don't build it that way* card.

**Proposed design, in one line:** keep C36's Postgres truth, per-record signed versions, explicit
OFF records and fail-closed semantics exactly as they are; replace the per-kind *complete-set
manifest digest* — the single construct that forces every read to be O(N) — with a per-kind
**version index (sorted set, score = monotone `feed_seq`)** plus a signed manifest carrying
`count` / `on_count` / `feed_seq`. Workers then read `ZRANGEBYSCORE idx (last_applied +inf`
(= exactly the changed record keys), fetch and verify only those, and prove completeness with
`ZCARD == count` in O(1). Writes publish one record and one index entry. The identity cache is
invalidated per key from the same feed, with single-flight on cold misses.

**Ready for implementation?** Architecture and root cause: yes, fully. One sequencing decision is
needed from the owner before coding — see §24.

---

## 2. Current architecture

Two trees matter and they are at different stages. Reading only one of them produces a wrong plan.

### 2.1 Where the code is

| Tree | What it is | R2-02 relevance |
|---|---|---|
`docs/plans/evidence/2026-09-24-runbook-v3-round2/prototype/rvproto2-rc2.tar.gz` | **RC2** — the build that produced every R2-02 measurement. Shipped as a tarball + `rc2_manifest.txt` in the evidence tree, not as working source. | Contains D1–D4. The only place the defects can be *re-measured*. |
| `gateway_v2/` | The **v3 rewrite**, built card by card (GW00…GW05 committed: `8d1230f3`…`6b70a2cc`). | Contains D2's shape already; D1/D3/D4 not yet written. The target for new code. |
| `gateway/ai_mesh_gateway/` | The **v1 production gateway**. Kill switch is a per-request pipelined `GET` (`kill_switch.py:check_kill_switch`), no snapshot, no manifest, no re-hydrator. | **Not in R2-02 scope.** Different (worse) design; replaced by the GW track. |
| `docs/plans/evidence/.../patches/rc3-state-p0.patch` | Reference implementation of **GW05b** (freshness stamp, bounded PG sessions, single-flight *reconcile*, two re-hydrators). Explicitly defers "SP3c, SP11, H6 and E2-01" to P1 = GW05c. | The thing GW05c builds on. Its own README states "At 50k records per kind, P1's O(changes) rounds are needed." |

### 2.2 RC2 runtime topology (the measured system)

```
                     CONTROL PLANE                          SOURCE OF TRUTH
  rv2 CLI / console ──► StateWriter.put()  ──────────────►  Postgres (Cloud SQL)
  (control/writer.py)      │                                rv2_version(kind → epoch,seq)
                           │                                rv2_record(kind,key → signed rec)
                           │                                rv2_log (append-only)
                           │                                rv2_budget_checkpoint
                           │
                           │ publish(kind): db.snapshot(kind)  ◄── FOR SHARE on the version row
                           ▼                                       (R2-04: blocks behind FOR UPDATE)
                 ┌──────────────────────────────────────┐
                 │  STORE  (Memorystore Valkey, 1 slot) │
   Rehydrator ──►│  {rv2}:meta:<kind>  signed manifest  │
 (control/       │  {rv2}:plan:<org>   signed record    │
  rehydrate.py)  │  {rv2}:plan_index   hash org→entry   │
  every 1 s      │  {rv2}:keys         hash hash→record │
                 │  {rv2}:ks           hash scope→record│
                 │  {rv2}:budget_meta  hash org→record  │
                 │  {rv2}:budget:<org> live counter      │
                 │  {rv2}:updates      pub/sub channel  │
                 └───────────────┬──────────────────────┘
                                 │  PUBLISH <kind>  (lossy, payload discarded)
         ┌───────────────────────┴───────────────────────┐
         ▼                                               ▼
   GATEWAY 1 (c4-highcpu-16)                       GATEWAY 2
   uvicorn, 12 worker PROCESSES                    12 worker PROCESSES
   each worker = ONE asyncio event loop:
     ├ RvApp.__call__           ← /v1/chat/completions   (serving)
     ├ task: ks.run()           ← D1, every 500 ms       (SAME LOOP)
     ├ task: plans.run()        ← D2, every 1 s          (SAME LOOP)
     ├ task: plans._listen()    ← PushListener on {rv2}:updates
     ├ task: audit.run()
     └ task: _loop_lag()        ← also calls ops._gauges() ≈1/s, O(orgs)
```

Every state reader is a per-worker-process RAM snapshot. There are 12 of them per gateway, each
doing its own full refresh — the per-worker cost multiplies by worker count for store and CPU
load, though each worker's *latency* damage is its own loop's blocking time.

### 2.3 gateway_v2 topology today

```
gateway_v2.edge  ──► gateway_v2.admit (3-line stubs) ──► gateway_v2.plan ──► gateway_v2.domain
                                                            │
                                                  PlanStore (in-process dict)
                                                  ReplicaSnapshot (in-process, threading.Lock)
```
No Redis, no Postgres, no publisher, no re-hydrator, no kill switch, no identity.
`import-linter` enforces layers `edge > admit > plan > detect > resolve > dispatch > egress >
audit > runtime > contracts > domain`, so any new store/feed module must live in
`gateway_v2.runtime` (or `domain`) to be importable from `plan` and `admit`.
Structural gates in force: ≤ 800 lines/module, ≤ 120 lines/function, no module-level mutable
literals, frozen dataclasses, no `HTTPException`/`JSONResponse` outside `edge`/`resolve`.

---

## 3. Current state-propagation flow

Traced end to end for each kind. "Loop?" = does this stage run on a request-serving event loop.

### 3.1 Plans

| Stage | Code | What it actually does | Loop? |
|---|---|---|---|
| Write | `writer.StateWriter.plan_set` → `put("plan", org, doc)` (`writer.py:72-108`) | `compile_plan(doc)` to reject invalid plans at save time; one PG tx: `version(kind, lock=True)` (`FOR UPDATE`), `nv = (epoch, seq+1)`, `make_record` (sha256 of canonical body + HMAC), `upsert` into `rv2_record` + `rv2_log`, `set_version`. | no |
| Publish | `publish_safe` → `publish("plan")` (`:121-149`) | `db.snapshot("plan")` reads the version `FOR SHARE` **and every plan record**; `make_meta` recomputes `digest()` over all of them; then under `WATCH {rv2}:meta:plan`: `SET {rv2}:plan:<org>` **for every org**, `DEL {rv2}:plan_index`, `HSET {rv2}:plan_index` with **every** entry, `SET {rv2}:meta:plan`, `PUBLISH {rv2}:updates plan` — one `MULTI/EXEC`. | no (but blocks the store for everyone) |
| Nudge | `runtime/push.PushListener` on `{rv2}:updates`, accepted by `_plan_nudge` (`snapshot_v2.py:152`) | Coalesces up to `drain_max` messages into **one** nudge and **discards the payload** — its docstring states "a nudge re-reads the complete state, so no message needs keeping". | yes |
| Reconcile | `VersionedPlanSnapshot.reconcile_once` (`:77-103`) | `MULTI`: `GET {rv2}:meta:plan` + `HGETALL {rv2}:plan_index`. Then `orjson.loads` **per entry**, `len(entries) != meta["count"] or digest(entries) != meta["digest"]` → **sorts and sha256s every entry**, then loops over **every** org comparing `(epoch, seq, hash)` against `_applied`, then checks for vanished orgs over the whole dict. | **yes** |
| Apply | `_load_one` (`:105-121`) | Only for changed orgs: `GET {rv2}:plan:<org>`, `verify_record` (sha256 + HMAC), index-entry match, `compile_plan`. | yes |
| Request | `edge/chat` → `plans.get(org_id)` (`:68-76`) | Pure RAM: staleness check, then `_plans.get(org_id)`; three distinct states. | yes |

The **apply** step is already O(changes). The **reconcile** step is O(orgs) and it is the one that
runs unconditionally once per second.

### 3.2 Kill switches

| Stage | Code | What it does | Loop? |
|---|---|---|---|
| Write | `writer.killswitch(scope, name, on)` → `put("ks", rkey, {"on": on})` | Same PG path. `"off"` is an **explicit record**, never an absence (C36). | no |
| Publish | `publish("ks")` → `_stage` else-branch (`writer.py:160-163`) | `DEL {rv2}:ks` + `HSET {rv2}:ks` with **every** scope record + `SET meta` + `PUBLISH`. | no |
| Nudge | — | **None.** `_plan_nudge` accepts only `b"plan"`/`b"all"`; `ks` messages are ignored. Kill switches are **poll-only**. | — |
| Refresh | `VersionedKillSwitch.refresh_once` (`state_v2.py:103-121`) | `MULTI`: `GET meta:ks` + **`HVALS {rv2}:ks`** + `GET meta:key`. Then `verify_set` → `verify_record` **per record** (orjson + sha256 + `hmac.compare_digest`) **plus** `digest(recs)` (sort all + sha256). Then rebuilds `engaged`/`orgs`/`models` frozensets from scratch. Then `identity.on_epoch(key_meta_version)`. | **yes, every 500 ms** |
| Request | `admission.admit` (`admit/admission.py:38-53`) | Pure RAM: `ks.state()` (staleness), `model_killed`, `org_killed`. | yes |

`RV_KS_REFRESH_MS=500`, `RV_KS_STALE_MS=5000`, validated as `period * 2 <= stale`
(`runtime/config.py:239-242`).

### 3.3 Identity / keys

| Stage | Code | What it does | Loop? |
|---|---|---|---|
| Write | `writer.key_add` / `key_revoke` | PG write; revoke is an explicit `deleted=true` record. | no |
| Publish | `publish("key")` | `DEL {rv2}:keys` + `HSET` of **every key record** + `SET meta:key` + `PUBLISH`. At 50k keys this is 17.4 MB in one `MULTI`. | no |
| Invalidate | `VersionedKillSwitch.refresh_once:120` → `VersionedIdentity.on_epoch` (`:40-43`) | `if v > self.version: self._cache = {}` — **the entire principal cache is dropped** on any key-kind version bump, on every worker, within one 500 ms refresh. | yes |
| Cold fetch | `VersionedIdentity.fetch` (`:49-70`) | `MULTI`: `HGET {rv2}:keys <hash>` + `GET meta:key`; `verify_meta(not_before=self.version)`; `verify_record`; epoch-checked cache fill. **No single-flight, no negative cache.** | yes, on the request path |
| Request | `admission.admit:37,48` | `identity.cached(api_key)` (sha256 of the key + dict get); on miss `await identity.fetch(...)`, each request independently. | yes |

### 3.4 Budget (for completeness — R2-09, not R2-02)

Live counters `{rv2}:budget:<org>` with `{rv2}:budget_gen:<org>` version tags; refilled **on the
request path** (`admit/quota.TokenLease`), checkpointed by the re-hydrator every 5 s. This is
R2-09/GW06, and the plan below must not disturb it.

---

## 4. Full refresh / reconciliation inventory

Every periodic or whole-set operation found, in the required format.

### F1 — kill-switch whole-set refresh (**primary root cause**)

```
File:            rvproto/admit/state_v2.py
Class/function:  VersionedKillSwitch.refresh_once (:103-121), driven by .run() (:123-134)
Trigger:         asyncio task started in RvApp._startup (edge/app.py:79)
Frequency:       every RV_KS_REFRESH_MS = 500 ms, per worker process
Data loaded:     GET {rv2}:meta:ks, HVALS {rv2}:ks (ALL records), GET {rv2}:meta:key
Records touched: every kill-switch record = 1 global + 1 per org + 1 per model
Execution:       SAME event loop as /v1/chat/completions; one MULTI await, then PURE SYNC CPU
Loop?            YES
Complexity:      O(R) store bytes + O(R) orjson.loads + O(R) sha256(body) + O(R) HMAC
                 + O(R log R) sort + O(R) sha256 in digest()  →  O(R log R), CPU-bound
Why expensive:   verify_set() re-verifies the COMPLETE set on every refresh even when nothing
                 changed. Measured 5.2 / 56.7 / 322.6 ms at 1k / 10k / 50k records
                 (= 1% / 11% / 65% of a core); live 8.5 / 75 / 213 ms at N1/N2/N3 with
                 ks_cpu ≈ 0.9 × ks_ms, i.e. it is CPU, not I/O.
```

### F2 — plan index whole-set reconcile (**primary root cause**)

```
File:            rvproto/plan/snapshot_v2.py
Class/function:  VersionedPlanSnapshot.reconcile_once (:77-103), driven by .run() (:123-133)
                 and by every push nudge via ._pushed() (:141-149)
Trigger:         asyncio task (edge/app.py:80) + pub/sub nudge
Frequency:       every RV_PLAN_RECONCILE_MS = 1000 ms, plus once per nudge
Data loaded:     GET {rv2}:meta:plan + HGETALL {rv2}:plan_index (ALL orgs)
Records touched: one index entry per org; plan bodies only for changed orgs
Execution:       SAME event loop as serving
Loop?            YES
Complexity:      O(N) bytes + O(N) orjson.loads + O(N log N) sort + O(N) sha256 (digest)
                 + O(N) dict copies (plans/_invalid/_applied are copied every round) + O(N) scan
Why expensive:   the manifest's completeness proof is a digest over EVERY entry, so it cannot be
                 checked without reading every entry. Measured 9.5 / 55 ms at 10k / 50k orgs;
                 live 3.1 / 24.6 / 72 ms at N1/N2/N3. Round-1 C28 measured 55 ms/s at 100k orgs.
```

### F3 — whole-kind publish

```
File:            rvproto/control/writer.py
Class/function:  StateWriter.publish (:121-149) + _stage (:151-165); called by publish_safe
                 from EVERY put() (:105)
Trigger:         every control-plane write (plan_set, key_add, key_revoke, killswitch, budget_set)
Frequency:       once per write (E2-01 phases B/C/D write every 5 s)
Data loaded:     db.snapshot(kind) = the version row FOR SHARE + EVERY record of the kind
Records touched: all of them, twice (read from PG, written to the store)
Execution:       control-plane process (not a gateway loop)
Loop?            No — but it occupies the single-threaded store for the whole MULTI
Complexity:      O(R) PG rows + O(R log R) digest + O(R) store writes in ONE MULTI/EXEC
Why expensive:   Valkey is single-threaded, so the MULTI stalls every other client. Measured
                 10k keys = 3.5 MB / 23.8 ms with a concurrent client blocked 13.6 ms;
                 50k keys = 17.4 MB / 124 ms with a concurrent client blocked 59.5 ms —
                 OVER the request-path store timeout RV_STORE_TIMEOUT_MS = 25, so requests
                 that need a store call during a publish get 503 shared_state_unavailable.
                 Live publish p50: 30-76 ms (N1), 114-623 ms (N2), 255-1724 ms (N3).
```

### F4 — global identity-cache invalidation (the "any key write empties every cache" path)

```
File:            rvproto/admit/state_v2.py
Class/function:  VersionedIdentity.on_epoch (:40-43), called from
                 VersionedKillSwitch.refresh_once:120 and from fetch():68
Trigger:         the key kind's manifest version rising for ANY reason (key_add, key rotate,
                 key_revoke, or a re-hydrator republish)
Frequency:       once per key-kind write, observed within <= 500 ms by every worker
Data dropped:    self._cache = {} — ALL cached principals on that worker
Records touched: every active key, indirectly: each must be re-fetched on its next request
Execution:       gateway serving loop
Loop?            YES
Complexity:      O(active keys) store round trips after each key write, amplified by
                 (workers x gateways) and by the absence of single-flight
Why expensive:   store round trips per request go 0.134 (steady) -> 0.986 (key-write phase);
                 identity fetches/s go 14 -> 118. At N2 phase B that produced 89 x 503
                 shared_state_unavailable; at N3 phase B, 640 (infra 1.78%, C4 p99 unbounded).
```

### F5 — startup state load

```
File:            rvproto/edge/state.py:build_state (:124-127)
                 await ks.refresh_once(); await plans.load_all()
Trigger:         every worker process start (12 per gateway), every deploy, every restart
Frequency:       once per process
Data loaded:     the complete ks set + the complete plan index + every plan body (nothing
                 is in _applied yet, so _load_one runs for EVERY org)
Records touched: all
Loop?            During startup only (before the server accepts), but it is also what makes
                 a redeploy a 12 x O(N) burst against the store
Complexity:      O(R + N) store + O(N) compile_plan
Why expensive:   gate G-04 ("cold start with many keys": 3x step with 2,000 keys over 200 orgs,
                 pass = 0 x 503 shared_state_unavailable on new gateways) is exactly this path.
```

### F6 — re-hydrator whole-kind diagnose + republish

```
File:            rvproto/control/rehydrate.py
Class/function:  Rehydrator.diagnose (:70-88) and check_once (:90-124)
Trigger:         its own process loop
Frequency:       every RV_REHYDRATE_PERIOD_MS = 1000 ms, for EACH of the 4 kinds
Data loaded:     db.snapshot(kind) (all PG rows of the kind) + GET {rv2}:meta:<kind>,
                 then digest(recs) over all of them, EVERY ROUND even when nothing changed
Records touched: all, every second, for every kind
Execution:       control-plane process
Loop?            No (off the serving loop) — but it is on the AVAILABILITY path once GW05b's
                 freshness stamp lands: a round slower than RV_STATE_FRESH_MS - period makes
                 stamps that are born stale and the whole fleet fails closed.
Complexity:      O(R) PG + O(R log R) digest per kind per second
Why expensive:   rc3-state-p0-README "Open risks": "A round slower than RV_STATE_FRESH_MS -
                 period makes stamps that are born old. At 50k records per kind, P1's
                 O(changes) rounds are needed." This is a GW05c dependency, not optional.
```

### F7 — per-org gauge rebuild on the loop (R2-10 boundary)

```
File:            rvproto/edge/ops.py:_gauges (:42-62), called from RvApp._loop_lag every ~1 s
                 (edge/app.py:111-113: "k % 10 == 0 ... ops._gauges(st)  # O(orgs)")
Frequency:       ~1/s per worker, plus on every metrics dump/scrape
Data touched:    st.plans.versions() -> a dict over EVERY plan; then a set comprehension
                 building one label string per org, a diff against the live gauge dict, and
                 one set() per org
Loop?            YES (the comment in app.py literally says "O(orgs)")
Complexity:      O(N) string formatting + O(N) dict/set ops + O(N) gauge slots
Why expensive:   plan_version_info{org=...,version=...} is one series PER TENANT. Round-1 C28
                 measured 50,003 series per worker at 50k tenants; RV_SHM_SLOTS defaults to
                 "96,512,512" so the gauge directory holds 512 entries per worker and the
                 r2scale lane flagged overflow at ~1,000 active orgs.
```

### F8 — gateway_v2's reconcile (the defect already present in the v3 tree)

```
File:            gateway_v2/gateway_v2/plan/snapshot.py
Class/function:  ReplicaSnapshot.reconcile (:35-39) -> _absorb (:41-49)
                 + PlanStore.known (plan/store.py:49) / PlanStore.read (:53)
Trigger:         not yet wired to any loop; default reconcile_period_s = 1.0
Frequency:       intended 1/s
Data loaded:     self._store.known() = tuple(sorted(self._tenants)) -> a SORT of every tenant,
                 then self._store.read(org_id) for EVERY tenant
Records touched: all
Execution:       holds ReplicaSnapshot._lock for the WHOLE loop; lookup() (:51-54) takes the
                 SAME lock, and each _absorb also takes PlanStore._lock
Loop?            Will be, as soon as it is wired
Complexity:      O(N log N) sort + O(N) reads + 2 lock acquisitions per tenant, with
                 request-path lookups BLOCKED for the whole duration
Why expensive:   this is D2's shape, with lock contention added. It is worse than RC2 in one
                 respect: RC2 only starved the loop with CPU, this also serialises lookups
                 behind the reconcile.
```

### Operations by interval — summary

| Interval | Operation | Per | Cost at 25k tenants (measured) |
|---|---|---|---|
| 500 ms | F1 kill-switch whole-set verify | worker (12/gateway) | 203–223 ms wall, 183–194 ms CPU |
| 1 s | F2 plan whole-index reconcile | worker | 70–73 ms |
| 1 s | F7 per-org gauge rebuild | worker | O(N), unmeasured separately |
| 1 s | F6 re-hydrator whole-kind diagnose ×4 kinds | fleet | O(R) PG + digest, ×4 |
| 5 s | F6 budget checkpoint | fleet | `mget` of 2 keys per org |
| per write | F3 whole-kind publish | fleet | 255–1 724 ms p50 |
| per key write | F4 whole-cache wipe + miss stampede | worker | trips/req 0.13 → 0.97 |

---

## 5. Write / publish behaviour

One table per question the brief asks, for each write path. All writes funnel through
`StateWriter.put()` → `publish_safe(kind)` → `publish(kind)`, so the answers are uniform by kind.

| Write path | DB operation | Published after commit | Whole kind republished? | `MULTI`? | Keys modified | Records touched | Workers notified | One record ⇒ global invalidation? |
|---|---|---|---|---|---|---|---|---|
| `plan_set(doc)` | `FOR UPDATE` version row, `(epoch, seq+1)`, upsert `rv2_record`, append `rv2_log`, bump version | the **entire** plan kind | **yes** | **yes** | `{rv2}:plan:<org>` × N, `DEL`+`HSET {rv2}:plan_index`, `SET {rv2}:meta:plan` | all plans | all, via `PUBLISH {rv2}:updates plan` | no (plans are per-org in RAM) |
| `plan_delete(org)` | same, `deleted=true`, `op=offboard` | entire plan kind | yes | yes | same | all plans | all | no |
| `key_add(...)` | same on kind `key` | the **entire** key kind | **yes** | **yes** | `DEL`+`HSET {rv2}:keys` (every key), `SET {rv2}:meta:key` | all keys | all, but **only via the 500 ms poll** (no `key` nudge) | **YES** — F4 |
| `key_revoke(...)` | same, `deleted=true` | entire key kind | yes | yes | same | all keys | all, via poll | **YES** — F4 |
| `killswitch(scope,name,on)` | same on kind `ks` | the **entire** ks kind | **yes** | **yes** | `DEL`+`HSET {rv2}:ks`, `SET {rv2}:meta:ks` | all ks records | all, **poll only** | no, but the *refresh* that observes it also calls `on_epoch`, so it is the delivery vehicle for F4 |
| `budget_set(org,n)` | same on kind `budget` | entire budget kind, **plus** a non-transactional `MSET {rv2}:budget:<org>, {rv2}:budget_gen:<org>` | yes | yes | `DEL`+`HSET {rv2}:budget_meta`, counter + gen | all budget records | all, poll | no |
| `rollback(log_id)` | `epoch+1, 0` | entire kind | yes | yes | kind-dependent | all | all | depends on kind |
| re-hydrator repair | `db.snapshot(kind)` only | entire kind, per kind that diagnosed non-equal | yes | yes | kind-dependent | all | all | same as the kind's write |

Concrete consequences, with code references:

- `_stage` (`writer.py:151-165`) is the whole-kind rewrite. For `plan` it issues N × `SET` plus
  `DEL`+`HSET` of the index; for `key`/`ks`/`budget` a single `DEL`+`HSET` of every record.
- `publish` is `WATCH`-guarded (`:126-148`) and skips when the store already holds a newer
  verified manifest — correct, and worth preserving. The retry loop is bounded at 20 attempts.
- Nothing about this is *incorrect*. R2-17 records 0 violations in 15 RC2 fault runs. It is
  purely a cost problem, which is why the fix must be surgical, not a redesign.
- The publish stall is also why N3 phase C (plan writes) shows the worst C4 p99 (459 ms) and
  221 store ops over 25 ms: a 1.7 s `MULTI` on a single-threaded store while 12 workers are
  trying to do request-path `HGET`s.

---

## 6. Identity-cache invalidation

| Property | Current behaviour | Code |
|---|---|---|
| Structure | `dict[str, Principal]`, per worker **process**, unbounded | `state_v2.py:38` |
| Key format | `hashlib.sha256(api_key.encode()).hexdigest()` | `:45-47` |
| Population | on a request miss only, in `fetch()`, and **only** when `mv == self.version` (epoch-checked fill, C36) | `:66-69` |
| Expiration | **none** — no TTL, no size bound, no LRU | — |
| Invalidation | **global wipe**: `self._cache = {}` whenever the key-kind manifest version rises | `:40-43` |
| Per-key or global? | **global** | `:42` |
| Epoch / global version used? | **yes** — the key manifest's `Version(epoch, seq)` *is* the auth epoch, by design: the module docstring says "The key manifest's version is the auth epoch: any key write (a revocation) clears every worker's principal cache within one refresh period." | `:8-9` |
| After one key changes | every worker drops **every** principal; the next request for each distinct active key does its own `MULTI` (`HGET keys` + `GET meta:key`) | `:49-56` |
| All workers wipe? | yes — delivered by `VersionedKillSwitch.refresh_once:120` calling `identity.on_epoch(version_of(key_meta))`, within ≤ `RV_KS_REFRESH_MS` on all 12 workers of all gateways | `state_v2.py:114-120` |
| Concurrent cold misses ⇒ duplicate fetches? | **yes.** `Admission.admit` calls `await self.identity.fetch(key_hash)` with no in-flight map (`admit/admission.py:46-51`). K concurrent requests for the same cold key ⇒ K round trips. | `admission.py:46-51` |
| Negative caching? | **no.** `fetch` returns `None` for a never-issued key and caches nothing, so a key-scanning client produces one store read per attempt (round-1 C26: 5 000 random keys ⇒ 5 000 reads). | `:60` |

**The exact code path for "any key write empties every worker's identity cache":**

```
StateWriter.key_add / key_revoke        writer.py:188 / :193
  └ put("key", ...)                    writer.py:72   → PG seq+1  → key manifest version rises
      └ publish_safe("key")            writer.py:105
          └ publish("key") → _stage    writer.py:121,160  → SET {rv2}:meta:key  (new version)
  ... ≤ 500 ms later, on EVERY worker ...
VersionedKillSwitch.refresh_once       state_v2.py:103
  ├ pipe.get(K2_META + "key")          state_v2.py:107
  ├ key_meta = verify_meta(..., "key") state_v2.py:114
  └ self.identity.on_epoch(version_of(key_meta))       state_v2.py:120
      └ VersionedIdentity.on_epoch     state_v2.py:40
          └ if v > self.version: self._cache = {}      state_v2.py:42   ← THE WIPE
  ... next request per distinct active key ...
Admission.admit → identity.cached() miss → await identity.fetch()   admission.py:37,48
  └ VersionedIdentity.fetch: MULTI HGET+GET, no single-flight       state_v2.py:49
```

Measured effect (`N2.analysis.out`, phase B vs A): `rt/req` 0.134 → 0.985, `idf/s` 14.07 → 118.19,
`503 shared_state_unavailable` 17 → 89. At N3 phase B: 640 × 503, infra 1.78 %, C4 p99 unbounded.
SP11 called this a "miss stampede" and rated it POSSIBLE because the round-1 harness used a single
key; harness v2.2's multi-key mode (50 000 keys, zipf) is what made it visible.

Note the subtlety that makes this *correct but costly*: the wipe is the mechanism that makes
revocation take effect without a per-request store read. Per-key invalidation must preserve that
guarantee — a revoked key must stop being admitted within the same bound — which is why the fix is
a *targeted* eviction driven by the change feed, not a longer cache lifetime.

---

## 7. Event-loop impact

The §2.1 rule GW05c must satisfy: *"No work proportional to the number of tenants, keys or records
on a serving event loop."* What actually runs on the serving loop today:

| Work | Kind | Proportional to | Blocking? |
|---|---|---|---|
| `verify_set` → N × (`orjson.loads` + `sha256(body)` + `hmac.compare_digest`) | CPU | ks records | **yes**, pure sync between awaits |
| `digest(recs)` → sort N strings + one sha256 over the join | CPU | ks records | **yes** |
| `orjson.loads` per plan index entry | CPU | orgs | **yes** |
| `digest(entries)` for the plan index | CPU | orgs | **yes** |
| `dict(self._plans)`, `dict(self._invalid)`, `dict(self._applied)` copied every reconcile (`snapshot_v2.py:86`) | CPU + alloc | orgs | **yes** |
| `[o for o in plans if o not in index]` vanished-org scan (`:98`) | CPU | orgs | **yes** |
| `ops._gauges` label-set build + gauge diff (`ops.py:54-60`) | CPU + alloc | orgs | **yes** |
| `compile_plan` per changed org (`_load_one:113`) | CPU | **changes** | yes, but correctly bounded |
| `await pipe.execute()` for the refresh `MULTI` | I/O | response bytes | no (awaited), but the response *parse* is O(N) |
| `await identity.fetch()` on a cold key | I/O | 1 round trip | no, but K duplicates under stampede |
| `TokenLease` store refill (R2-09) | I/O | 1 | no |

Confirmation that these share the serving loop: `RvApp._startup` creates `st.ks.run()` and
`st.plans.run()` with `asyncio.create_task` on the loop that `RvApp.__call__` serves
(`edge/app.py:76-81`). There is no executor, no separate thread, no separate process for state.
`rvproto/runtime/loopguard.py` exists and `RV_TOKENIZE_IN_THREAD` shows the codebase already knows
how to offload GIL-releasing CPU — state refresh simply never used it.

Evidence that it blocks rather than merely competes:
- `ks_cpu / ks_ms` = 0.83–0.90 at every level, so the refresh is CPU, and CPython holds the GIL
  through `orjson.loads`/`hashlib`/`hmac` for each record (these release the GIL only for large
  buffers; per-record bodies are tens of bytes).
- `loop_lag` p99 tracks the refresh cost almost 1:1: 2.7–3.5 ms (N1, ks 8.5 ms), 60–64 ms (N2,
  ks 75 ms), 188–209 ms (N3, ks 212 ms).
- C4 p99 ≈ 2× the single block (145 ms vs 75 ms at N2; 420 ms vs 212 ms at N3), consistent with a
  request crossing several await points and being caught by more than one blocking episode
  (two ks refreshes and/or one plan reconcile) within its ~15 ms of own work.
- `rejected_503` appears only from N2 upward, and `kv>25` (store ops over the 25 ms request-path
  timeout) is 29–221 per run, which is the publish stall (F3) rather than the refresh.

Target state:

```
Serving event loop
     ├──► request processing
     ├──► O(1) manifest read + O(changes) delta apply      (bounded per round, yields between chunks)
     ├──► O(1) index-head read to prove completeness
     ✗──► O(tenants) verify / digest / reconcile / gauge rebuild
     ✗──► whole-cache rebuild
```

---

## 8. Current complexity

Let `N` = tenants/orgs, `K` = issued keys, `S` = kill-switch records (≈ `1 + N + models`),
`A` = *active* keys in a window, `W` = workers per gateway, `G` = gateways, `C` = changed records
in a period, `E` = **engaged** kill-switch scopes (normally 0–few).

| Operation | Current | Depends on | Notes |
|---|---|---|---|
| Kill-switch refresh (per worker, 2/s) | **O(S log S)** CPU | kill-switch records ≈ tenants | `verify_set` + `digest` |
| Fleet kill-switch refresh load | **O(S log S · W · G · 2/s)** | tenants × workers × gateways | store egress also O(S·W·G·2/s) |
| Plan reconcile (per worker, 1/s) | **O(N log N)** CPU | orgs | index + digest + dict copies |
| Plan apply | O(C · compile) | **changes** | already correct |
| State publication (per write) | **O(R)** PG rows + **O(R log R)** digest + **O(R)** store writes in one `MULTI` | records of the kind | blocks the whole store |
| Identity invalidation (per key write) | **O(A)** store round trips, ×`W`×`G` | active keys | no single-flight ⇒ worst case O(in-flight) |
| Identity cold fetch | O(1) per request, but O(duplicates) under stampede | concurrency | |
| Worker synchronisation / convergence | **O(S + N)** per worker per period | tenants | poll-only for ks/key; pub/sub nudge for plan only |
| Startup state load | **O(S + N·compile)** per process, ×`W` on a deploy | tenants | gate G-04 |
| Re-hydrator round | **O(R log R)** per kind per second | records | puts GW05b's stamp freshness at risk at scale |
| Per-org gauges | **O(N)** CPU per worker per second, **O(N)** series | tenants | R2-10/GW14d |
| Per-(org,version) `PlanInspector` cache (`edge/state.py:80-86`) | **O(N)** RAM, never evicted | tenants × versions | secondary |
| `Gcra` per-org rate-limit state | O(N) RAM | tenants | secondary |

Every place where complexity is proportional to total state size, explicitly:

1. `state_v2.py:106` `HVALS {rv2}:ks` — O(S) bytes.
2. `state_v2.py:110` `verify_set(...)` — O(S) HMAC + O(S log S) digest.
3. `snapshot_v2.py:80` `HGETALL {rv2}:plan_index` — O(N) bytes.
4. `snapshot_v2.py:82` per-entry `orjson.loads` — O(N).
5. `snapshot_v2.py:85` `digest(entries)` — O(N log N).
6. `snapshot_v2.py:86` three dict copies — O(N).
7. `snapshot_v2.py:88` the `for org, e in index.items()` loop — O(N).
8. `snapshot_v2.py:97` vanished-org scan — O(N).
9. `writer.py:124` `db.snapshot(kind)` — O(R).
10. `writer.py:155-163` `DEL` + `HSET(mapping=all)` — O(R).
11. `versioned.py:make_meta`/`digest` — O(R log R) on both the write and read sides.
12. `state_v2.py:42` `self._cache = {}` — O(A) consequential round trips.
13. `rehydrate.py:71,86` `db.snapshot` + `digest(recs)` — O(R) per kind per second.
14. `ops.py:54-60` per-org label set — O(N) CPU and O(N) series.
15. `edge/state.py:124-127` startup `refresh_once` + `load_all` — O(S + N).
16. `gateway_v2/plan/store.py:49` `tuple(sorted(self._tenants))` — O(N log N) under a lock.
17. `gateway_v2/plan/snapshot.py:38` `for org_id in self._store.known()` — O(N) under a lock that
    request-path `lookup()` also takes.

---

## 9. R2-02 / GW05c gap analysis

Verified behaviour, not names. "Partial" is stated with what exactly is missing.

| GW05c requirement | Current implementation | Gap | Required change |
|---|---|---|---|
| **Per-record versions** | **Present.** Every record carries `(epoch, seq)` + `sha256(body)` + HMAC (`versioned.py:make_record`); the plan index entry carries `(epoch, seq, hash, deleted)`; `gateway_v2.domain.plan.ExecutionPlan` has `epoch`, `sequence`, `content_hash` and `is_newer()`. | None for the record model. The gap is that the **completeness proof is a whole-set digest**, so per-record versions cannot be used to read incrementally. | Add a per-kind **monotone `feed_seq`** that never resets across epochs, carry it on the record and in the signed manifest, and replace the whole-set digest on the read path with `count` + index-head checks. |
| **Change feed** | **Absent.** `{rv2}:updates` is a pub/sub *nudge* that carries only a kind name; `PushListener` coalesces messages and its docstring states the payload need not be kept because "a nudge re-reads the complete state". `ks` and `key` are not even nudged — poll-only. | No way to learn *which* records changed. | Per-kind **version index**: `ZSET {rv2}:idx:<kind>`, member = record key, score = that record's `feed_seq`. `ZRANGEBYSCORE` from the last applied `feed_seq` = exactly the changed keys. Keep pub/sub purely as a latency nudge. |
| **Incremental worker updates** | **Partial.** The *apply* step is already O(changes) (`_load_one` is skipped when `_applied[org] == want`). The *detect* step is O(N) and runs unconditionally. Kill switch has no incremental path at all — it rebuilds `engaged`/`orgs`/`models` from the full set every 500 ms. | Detection is O(N); kill switch is 100 % full-refresh. | Workers consume the delta; maintain `orgs`/`models` incrementally; bound per-round work with a delta budget and yield between chunks. |
| **No whole-kind `MULTI`** | **Violated, every write.** `_stage` issues `DEL` + `HSET(mapping=all records)` inside `publish`'s `MULTI/EXEC`. | Explicitly forbidden by the card. | Per-record publish: `SET`/`HSET` one field + `ZADD` one index entry + `SET` manifest + nudge, in one O(1) `MULTI`. Whole-kind republish survives **only** in the re-hydrator repair path. |
| **Per-key cache invalidation** | **Violated.** `on_epoch` wipes the entire cache on any key-kind version bump; this is the documented design. | A global epoch is exactly what the card forbids. | Evict only the key hashes present in the key delta; keep a per-cache `applied_feed_seq` for the correctness bound. |
| **Single-flight cold fetch** | **Absent.** `Admission.admit` awaits `identity.fetch` per request; `rc3-state-p0` added single-flight to the *plan reconcile* (SP6), not to identity. | Duplicate fetches on every cold key; 503s under stampede. | In-flight future map keyed by key hash, plus a bounded negative cache (GW06's C26 requirement). |
| **Bulk onboarding** | **Absent.** There is no batch write path. `put()` is one record + one whole-kind publish, so onboarding M tenants costs M full republishes (M × O(R), i.e. O(M²) store bytes). | No efficient path; onboarding at scale would be a fleet-wide 503 generator. | `put_many()`: one PG transaction, one `feed_seq` advance for the batch, chunked pipelined store writes, one nudge; plus a "durable-only" seed mode that lets the re-hydrator publish. |
| **No tenant-scale serving-loop work** | **Violated.** F1, F2, F7 all run on the serving loop and are O(tenants). | The headline defect. | Everything above, plus moving residual verify/compile CPU into a GIL-releasing executor and a bounded per-round budget. |
| **Tenant-independent metrics** | **Violated.** `plan_version_info{org,version}` and `killswitch_engaged{scope,key}` are one series per tenant/scope; `ops._gauges` rebuilds the label set O(N) on the loop; `RV_SHM_SLOTS` gauge capacity is 512/worker and the lane flagged overflow near 1 000 orgs. | O(N) series and O(N) loop work. | Replace per-org series with aggregates (`plan_versions_distinct`, `plan_snapshot_age_seconds`, `plan_applied_total`) plus a bounded top-K + `other` bucket, per GW14d; drop the O(N) rebuild from the loop. |

Things that look like they satisfy a requirement but do not:

- `PushListener` is *not* a change feed. It is a correct, hardened (D2/R2-19) nudge that
  deliberately throws the payload away.
- `_applied` in `VersionedPlanSnapshot` is *not* incremental propagation. It makes the apply cheap
  while leaving the detect O(N).
- `rc3-state-p0`'s "single-flight" is for the plan *reconcile* (preventing overlapping reconciles,
  SP6), not for cold identity fetches (SP11/H5).
- `gateway_v2`'s GW05 verdict says "PASS two in-process replicas converge" — true, and orthogonal:
  the convergence test has 2 tenants, so the O(N) reconcile is invisible.

---

## 10. Root causes

### Primary — directly responsible for the tenant-scale C4 p99

| ID | Root cause | Why it is primary |
|---|---|---|
| **P1** | The per-kind manifest proves completeness with a **digest over every record**, so no reader can validate the store without reading and hashing the whole kind. | This single design choice forces F1, F2, F3 and F6 to be O(N). It is the thing to change. |
| **P2** | The kill-switch refresh **re-verifies the complete set every 500 ms on the serving loop** instead of applying deltas. | Dominant term: 203–223 ms of loop block twice a second at N3; phase A (no writes) fails at N2 on this alone. |
| **P3** | The plan reconcile **re-reads and re-digests the whole index every second on the serving loop**. | Second term: 70–73 ms at N3. Also already present in `gateway_v2`. |
| **P4** | Both refreshers run as `asyncio` tasks on the request-serving loop with no executor and no per-round work budget. | Converts CPU cost directly into C4 p99 and loop lag. Loop lag p99.9 fails at N1 already. |

### Secondary — will become the bottleneck once the primaries are fixed

| ID | Issue | Becomes visible when |
|---|---|---|
| **S1** | Whole-kind publish (F3) — 255–1 724 ms p50 at N3, blocking a single-threaded store past the 25 ms request-path timeout. | Immediately: the ≤ 25 ms publish gate fails even at N1 (30–76 ms). Must be fixed in the same card; the gate names it. |
| **S2** | Global identity-cache wipe (F4) + no single-flight + no negative cache. | Phase B. Produces the 640 × 503 at N3 and will still produce 503 bursts after P1–P4. |
| **S3** | Re-hydrator O(R) rounds per kind per second (F6). | As soon as GW05b's freshness stamp is enforced: a round slower than `RV_STATE_FRESH_MS − period` makes born-stale stamps ⇒ fleet-wide 503. The rc3 README flags this for 50k records. |
| **S4** | Startup O(S + N) load × 12 workers (F5). | Gate G-04 (cold start, 2 000 keys over 200 orgs, 0 × 503). |
| **S5** | Per-org metric series and the O(N) gauge rebuild (F7); `RV_SHM_SLOTS` 512/worker. | Gate L14d-2 (2 000 and 20 000 UUID tenants, 0 × 500). Shared with GW14d. |
| **S6** | Unbounded per-tenant RAM: `PlanInspector` cache keyed `(org, version)`, `Gcra` per-org state, identity cache with no bound. | 25k tenants × versions; a slow leak, not a latency defect. |
| **S7** | `gateway_v2`'s `ReplicaSnapshot._lock` held across the whole reconcile while `lookup()` needs it. | As soon as the reconcile is wired to a loop. Fix while it is still cheap. |

### Related — interacts with R2-02 but belongs to another card. Do not fix here.

| Finding | Card | Interaction | Boundary |
|---|---|---|---|
| SP1/SP2 freshness stamp, version floor from RAM starting at zero | **R2-03 / GW05b** | GW05c's `feed_seq` floors must come from the stamp, and GW05c must make re-hydrator rounds cheap enough for stamps to be fresh (S3). | Implement the stamp in GW05b. GW05c only *consumes* it and makes the rounds O(changes). |
| `FOR SHARE` publish snapshot blocking behind `FOR UPDATE`; PG session timeouts | **R2-04 / GW05b** | GW05c's per-record publish removes the need for an O(R) snapshot read on the write path, which helps, but the lock-free REPEATABLE READ snapshot and the four PG timeouts are GW05b's. | Do not re-implement; depend on it. |
| Budget-lease refill on the request path; per-path outage semantics | **R2-09 / GW06** | Shares the store pool and the 25 ms timeout; a publish stall (S1) causes lease-refill 503s. | GW05c fixes the stall; GW06 moves the refill off the request path. |
| Metric registration on the request path can raise; per-tenant label cardinality; directory sizing | **R2-10 / GW14d** | GW14d explicitly `Depends on GW05c`. S5 is the overlap. | GW05c **removes** the O(N) per-org series it creates and the O(N) loop rebuild. GW14d owns the size-check-before-insert, the overflow counter and the top-K bucketing. |
| `rejected_503` merging reasons; `plan_unavailable` uncounted; windowed histograms | R2-11 / GW14d | Needed to *measure* GW05c honestly (store publish, refresh cost, reason mix). | Use the `rc3-obs-v1` reference for typing/reset-safety; do not expand GW05c into an observability card. |
| In-flight kill-switch enforcement (SP3d) — streams are not re-checked | GW06 / GW19, `InFlightKill.CUT_NEXT_CHUNK` already locked in `domain/locks.py` | Propagation speed is GW05c's; *applying* it mid-stream is not. | Out of scope. |
| Store memory / audit eviction (H1) | R2-05 / GW14c | A full store refuses publishes ⇒ propagation stops. | Out of scope; GW14c lands separately. |
| Poll-only propagation for `ks`/`key` (SP3a) | R2-02 | **In scope** — the feed fixes it as a side effect. | Keep the bound declared per regime. |

---

## 11. Proposed target architecture

Design principle: **change the completeness proof, not the trust model.** C36 is validated
(R2-17: 0 violations in 15 fault runs). Every invariant below is preserved verbatim:

- Postgres is the source of truth; the store holds a published copy.
- Every record is individually signed (HMAC-SHA256 over kind, key, version, body hash, deleted, op).
- Versions are strictly increasing per kind; a content rollback is a signed write at `(epoch+1, 0)`.
- Explicit OFF records: a revoked key, a disengaged switch, an offboarded org all exist as records.
- A missing, unverifiable or regressed copy is `StoreDataUnavailable`, handled exactly like an
  outage — never "no such tenant", "invalid key" or "switch off".

### 11.1 Versioning

| Question | Answer |
|---|---|
| Where stored | **Postgres** is authoritative: `rv2_version(kind, epoch, seq, feed_seq, count, on_count)` — three new columns. Each record gains `feed_seq` in `rv2_record`. The store mirrors them in the signed manifest and in the per-kind index score. |
| Format | Record identity stays `(epoch, seq)` + `content_hash`. **New:** `feed_seq bigint`, a per-kind counter that **increases on every write and never resets, including across epoch bumps**. Ordering is total within a kind. |
| How it increments | In the same transaction that already locks the version row `FOR UPDATE`: `seq+1` (or `epoch+1, seq=0`), `feed_seq+1` always, and `count`/`on_count` adjusted by the record's transition (new/deleted/on→off). No extra scan, so publish stays O(1). |
| Why not reuse `seq` as the feed score | `seq` resets to 0 on an epoch bump (rollback, divergence repair), which would make scores non-monotone and silently hide changes from a worker whose cursor is above the new score. A separate never-resetting counter is the minimal safe addition. |
| How a worker detects a missed update | Three independent checks per round, all O(1): (a) `manifest.feed_seq > applied_feed_seq` ⇒ there are deltas; (b) `ZCARD idx:<kind> == manifest.count` ⇒ nothing is missing from the index; (c) the top index score (`ZREVRANGE idx 0 0 WITHSCORES`) `== manifest.feed_seq` ⇒ the index and manifest are the same publish generation. Any mismatch ⇒ `StoreDataUnavailable` ⇒ existing fail-closed path. |
| How ordering is guaranteed | The index score *is* the record's `feed_seq`, and the fetched record must satisfy `rec.feed_seq == score` and `(rec.epoch, rec.seq, rec.hash) == index entry` (the check `_load_one` already performs). A stale record served under a newer score fails. Deltas are applied in ascending score order. |
| Stale / out-of-order updates | Apply is idempotent and monotone per record: reuse `is_newer()` / `(epoch, seq)` comparison — an older record for a key already applied is dropped. The cursor only advances to the highest score **fully applied**, so a partial round is retried, never skipped. |
| Floors (GW05b interaction) | `applied_feed_seq` starts at the higher of (what this process applied, what the freshness stamp verified), so a new process never starts from zero on a lagging replica. The stamp's `versions` map gains `feed_seq` per kind. |

### 11.2 Change feed

**Mechanism: a sorted set per kind, not a stream.** The runbook allows "a stream or sorted set per
kind"; the sorted set is strictly better here for one reason: it is the *current* version index, not
a log, so it can never be trimmed into a gap. A worker that was away for an hour still gets exactly
the records that changed, and there is no retention knob to get wrong. (A Redis Stream would need
`MAXLEN` trimming, a trim-gap detector, and a full-reload fallback — all of which the sorted set
makes unnecessary.) Pub/sub keeps its existing role as a latency nudge only.

```
Store layout (additions marked NEW; hash tag {rv2} keeps everything in one slot)

{rv2}:meta:<kind>        signed manifest  {kind, epoch, seq, feed_seq, count, on_count, sig}   CHANGED
{rv2}:idx:<kind>         ZSET  member = record key, score = record feed_seq                    NEW
{rv2}:on:ks              SET   members = currently ENGAGED scopes                              NEW
{rv2}:plan:<org>         signed plan record (unchanged shape + feed_seq)
{rv2}:keys               hash  key hash -> signed key record (unchanged shape + feed_seq)
{rv2}:ks                 hash  scope    -> signed ks record  (unchanged shape + feed_seq)
{rv2}:budget_meta        hash  org      -> signed budget record (unchanged shape + feed_seq)
{rv2}:plan_index         RETAINED, re-hydrator/repair only; no longer read per reconcile
{rv2}:updates            pub/sub nudge; message becomes "<kind>:<feed_seq>"                    CHANGED
{rv2}:stamp              GW05b freshness stamp, extended with per-kind feed_seq
```

The feed *event* is the index entry itself. Read as:

```
ZRANGEBYSCORE {rv2}:idx:<kind> (applied_feed_seq +inf  WITHSCORES  LIMIT 0 <delta_budget>
   -> [(record_key, feed_seq), ...]        exactly the changed records, in order
```

and the record body is fetched by key (`GET {rv2}:plan:<org>` / `HMGET {rv2}:keys ...`). So the
logical event is `{kind, record_key, feed_seq}` and the payload is fetched by reference — which
keeps the index small (one entry per record, not per change) and avoids a second copy of signed
state. The operation type (`put` / `deleted` / `op`) is already inside the signed record, so it does
not need to be in the feed and cannot be spoofed there.

`{rv2}:on:ks` is the kill-switch **cold-start** optimisation and is the difference between "flat
refresh" and "flat refresh but O(N) startup". It is maintained in the same `MULTI` as the ks record
(`SADD` on engage, `SREM` on disengage), and its completeness is proven by `SCARD == manifest.on_count`
from the **signed** manifest — so a scope that is ON but missing from the set fails closed rather
than failing open. Each member's record is still individually verified, so the set is a hint about
*which* records to verify, never a source of truth about enforcement.

### 11.3 Worker synchronisation

```
control-plane write (plan / key / ks / budget)
   │  ONE Postgres tx: FOR UPDATE version row, seq+1, feed_seq+1, count/on_count delta,
   │                   upsert rv2_record, append rv2_log
   ▼
publish_record(kind, rec)      ── O(1) MULTI, WATCH {rv2}:meta:<kind> as today
   │  SET/HSET the one record
   │  ZADD {rv2}:idx:<kind> <feed_seq> <key>
   │  SADD/SREM {rv2}:on:ks <scope>            (ks only)
   │  SET {rv2}:meta:<kind>  (new manifest)
   │  PUBLISH {rv2}:updates "<kind>:<feed_seq>"
   ▼
worker: nudge received (or periodic wakeup, or the blocking read returns)
   │
   ├─ read head:  MULTI { GET meta:<kind>, ZCARD idx:<kind>, ZREVRANGE idx 0 0 WITHSCORES, GET stamp }
   │  verify_meta(not_before = max(applied, stamp))      ← anti-regress, unchanged semantics
   │  assert ZCARD == meta.count and top_score == meta.feed_seq   ← completeness, O(1)
   │
   ├─ if meta.feed_seq == applied_feed_seq:  DONE.  Cost is O(1). ← the steady state, 500 ms/1 s
   │
   ├─ else: ZRANGEBYSCORE idx (applied +inf WITHSCORES LIMIT 0 budget   ← O(changes)
   │        fetch those records (one HMGET / pipelined GETs)
   │        for each, ascending:  verify_record  →  rec.feed_seq == score
   │                              and (epoch,seq,hash) == index entry
   │                              and is_newer(rec, applied_record)   → else drop (idempotent)
   │        apply:  plans[org] = compile_plan(body)         | plan
   │                engaged/orgs/models flip for the scope   | ks
   │                identity cache: evict exactly this hash  | key
   │                budget gen handling unchanged            | budget
   │        advance applied_feed_seq to the highest FULLY applied score
   │        if the budget was hit, schedule another round immediately (no sleep)
   ▼
request path: pure RAM, unchanged
   ks.state() / org_killed / model_killed / plans.get(org) / identity.cached(hash)
```

Steady-state cost per worker per period: one `MULTI` of 4 O(1) commands and one `verify_meta`
(a single HMAC over a short string). That is the "flat with tenant count, within ±20 % of the 1k
baseline" requirement, satisfied by construction rather than by tuning.

Nudging and intervals:

- `{rv2}:updates` messages become `"<kind>:<feed_seq>"`, so the nudge is *informative*: a worker
  whose `applied_feed_seq >= feed_seq` can skip the head read entirely. `PushListener.accept` is
  extended to accept all four kinds (today it accepts only `plan`). This alone fixes SP3a
  (kill switches and keys are poll-only).
- The periodic wakeup stays as the bounded catch-up for a lost nudge, but because it is O(1) when
  nothing changed, the period can be **shortened** (propagation latency improves) rather than
  lengthened. Keep `RV_KS_REFRESH_MS=500` initially to avoid changing the declared SP3 bounds in
  the same card.
- `PushListener`'s D2/R2-19 hardening is preserved unchanged. No `listen()`, no unbounded buffer.

### 11.4 Identity cache

```
VersionedIdentity
  _cache:        dict[key_hash, (Principal, feed_seq)]      bounded (LRU, RV_IDENTITY_CACHE_MAX)
  _negative:     dict[key_hash, expires_monotonic]          bounded, RV_IDENTITY_NEG_TTL_MS
  _inflight:     dict[key_hash, asyncio.Future[Principal|None]]
  applied_feed_seq: int                                      from the key delta, not an epoch

  on_delta(changed: Iterable[key_hash], feed_seq):   evict ONLY those hashes from _cache and
                                                     _negative; applied_feed_seq = feed_seq
  fetch(key_hash):   if an inflight future exists -> await it  (single-flight)
                     else create it, do the ONE MULTI (HGET keys + GET meta:key [+ stamp]),
                     verify, resolve the future for every waiter, fill the cache only when
                     the manifest version is the one applied (epoch-checked fill, unchanged)
```

Correctness bound, unchanged in strength: a cached principal is valid as of `applied_feed_seq`, and
a revoked key is evicted within one feed round — the same bound the global wipe provided, because
the wipe was itself delivered by the same 500 ms refresh. GW05b's stamp still gates whether any
cached principal may be used at all.

The negative cache closes GW06's C26 ("5 000 random keys caused 5 000 shared-store reads") and must
be invalidated by the key delta too, otherwise a `key_add` would not take effect for a key someone
probed a moment earlier.

### 11.5 Bulk onboarding

- `StateWriter.put_many(kind, records)`: one Postgres transaction, one `FOR UPDATE`, `seq += M`,
  `feed_seq += M` (one score per record so deltas stay per record), `count/on_count` adjusted once,
  `execute_values`-style batch upsert, batch log append.
- Store publish in chunks of `RV_PUBLISH_CHUNK` (default 500) pipelined commands, each chunk its own
  small `MULTI`, with the manifest written **last** — so a reader either sees the old manifest
  (and skips, because `feed_seq` has not moved) or the new one (and the index is already complete).
  Readers are never exposed to a partial batch, which is the same ordering guarantee the whole-kind
  `MULTI` gave, without the single giant transaction.
- One nudge for the whole batch.
- Worker side: the delta budget (`RV_STATE_DELTA_BUDGET`, default 256 records/round) plus
  `await asyncio.sleep(0)` between chunks bounds per-round loop occupancy regardless of batch size;
  a large batch converges over several rounds instead of one long stall.
- A `--durable-only` seed mode writes Postgres and lets the re-hydrator publish, for first-time
  onboarding of tens of thousands of tenants with zero serving-path impact.

### 11.6 Off-loop execution

- The residual per-delta CPU (`verify_record`, `compile_plan`) runs in a GIL-releasing executor when
  a round's delta exceeds `RV_STATE_OFFLOAD_THRESHOLD` records, using the existing
  `ThreadPoolExecutor` pattern (`edge/state.py:144-145`, `RV_TOKENIZE_IN_THREAD`). Small deltas stay
  inline — a thread hop would cost more than the work.
- In `gateway_v2`, the snapshot becomes an **immutable object swapped by atomic reference**, so
  `lookup()` never takes a lock the reconcile holds. This removes S7 and makes the reconcile's
  duration invisible to the request path by construction.

### 11.7 Metrics (the GW14d seam)

Remove from the serving loop and from per-tenant cardinality:

| Remove | Replace with |
|---|---|
| `plan_version_info{org,version}` (one series per tenant) | `plan_versions_distinct` (gauge), `plan_applied_total` (counter), `plan_snapshot_age_seconds` (gauge), plus `plan_version_info` for a bounded top-K of tenants by traffic + an `other` bucket (GW14d owns the top-K machinery) |
| `killswitch_engaged{scope,key}` per scope | `killswitch_engaged_total{scope}` (gauge, = \|engaged\|) + the engaged list in a log line, not a metric |
| `ops._gauges` O(N) label-set rebuild on the loop | O(1) counters updated at apply time |

Add (all O(1), needed for the acceptance gate):

`state_delta_applied_total{kind}`, `state_delta_round_seconds{kind}` (histogram),
`state_refresh_seconds{kind}` (histogram — the "kill-switch refresh cost" the gate measures),
`state_feed_lag_records{kind}` (gauge = `manifest.feed_seq − applied_feed_seq`),
`state_feed_cursor{kind}`, `state_completeness_failures_total{kind,reason}`,
`identity_cache_evictions_total{reason}`, `identity_single_flight_joins_total`,
`identity_negative_hits_total`, `store_publish_seconds{kind}` (histogram, control plane),
`publish_records{kind}` (histogram — must stay at 1 for single writes).

---

## 12. Failure and recovery behaviour

| Event | Behaviour | Why it is safe |
|---|---|---|
| **Worker misses a nudge** | The periodic O(1) head read finds `manifest.feed_seq > applied` and applies the delta. Bound = one period. | The index is the current state, so nothing expires out of it. |
| **Worker restarts** | `applied_feed_seq` = the GW05b stamp's floor for that kind (never 0 on a lagging replica). ks: read `{rv2}:on:ks` (O(engaged)) + verify each member's record + `SCARD == on_count`. plans: load **lazily** per org on first request, single-flight, with the delta feed keeping them current afterwards. keys: empty cache, filled on demand with single-flight. | Startup becomes O(engaged) instead of O(S + N). A cold plan miss is one bounded store read; if it fails, the existing `PLAN_UNAVAILABLE` / 503 posture applies (declared, not new). |
| **Store (Valkey) restart / FLUSHALL** | `GET meta:<kind>` returns `None` ⇒ `StoreDataUnavailable` ⇒ unchanged fail-closed: RAM snapshot serves to `RV_KS_STALE_MS`, then 503 `kill_switch_unavailable` / `shared_state_unavailable` / `PLAN_UNAVAILABLE`. The re-hydrator republishes the kind (whole-kind, allowed here). Workers detect the new generation because `ZCARD`/top-score/`feed_seq` are consistent again and `feed_seq` has not regressed (Postgres `feed_seq` survives the flush). | Measured RC2 restore 0.456–1.249 s; the per-record path does not change the restore mechanism. Because `feed_seq` lives in Postgres, a flush does **not** reset cursors — workers resume with a delta, not a full reload. |
| **Store partially restored** | `ZCARD != manifest.count` or `top_score != manifest.feed_seq` ⇒ `StoreDataUnavailable`. | This is the replacement for the whole-set digest, and it is checked in O(1). It catches the same class: a partial or mixed-generation store. |
| **Index and records disagree** (record missing while indexed) | `GET` returns `None` ⇒ `StoreDataUnavailable`, exactly as `_load_one` does today (`snapshot_v2.py:108-109`). | Unchanged. |
| **Postgres unavailable** | Writes fail (`error`, nothing committed) or `CommitUnknown`. Workers are unaffected — they read the store. The re-hydrator keeps stamping while no kind in the store is behind the last verified versions, for `RV_STATE_PG_GRACE_MS` (GW05b). | GW05b's measured trade-off: ~7.4 s of global 503 during a 10 s PG freeze **without** the grace window. GW05c must not shorten that window. |
| **Duplicate events** | Idempotent apply: `is_newer`/`(epoch, seq)` comparison drops an already-applied record; the cursor is monotone. | Same mechanism `_applied` uses today. |
| **Out-of-order events** | Deltas are applied in ascending score order within a round; across rounds the cursor only rises. A nudge carrying a *lower* `feed_seq` than applied is ignored. | Total order per kind by construction. |
| **Worker disconnected (partition)** | `PushListener` declares the connection dead (D2 rules unchanged), backs off, re-subscribes, and runs one catch-up nudge. The snapshot ages; past the ceiling, fail closed. | Unchanged from RC2; the 45 s partition regression test (R2-19) still applies. |
| **Feed contains old entries** | There is no "old entry" — one entry per record, carrying its current `feed_seq`. A worker above that score simply does not select it. | This is the structural advantage over a stream. |
| **Epoch bump (rollback / divergence repair)** | `epoch+1, seq=0`, `feed_seq+1` (never reset). `verify_meta(not_before=...)` compares `(epoch, seq)`, so the new epoch is accepted as newer; the cursor logic uses `feed_seq`, which moved forward. | Both orderings stay consistent. This is precisely why `feed_seq` must not derive from `seq`. |
| **Two re-hydrators** | Unchanged (`WATCH`-guarded publish, stamps never regress, GW05b). A whole-kind republish rewrites the index atomically with the manifest. | Proven safe concurrently in round 2. |
| **Delta budget exhausted repeatedly** (e.g. 25k-record bulk onboard) | Rounds run back-to-back without sleeping until the cursor catches up; `state_feed_lag_records` rises and is alertable; enforcement for *unchanged* records is unaffected. | Bounded loop occupancy is the trade; the lag metric makes the trade visible. |

---

## 13. Performance expectations

| Quantity | Now | After | Mechanism |
|---|---|---|---|
| Kill-switch refresh, steady state | O(S log S) CPU: 8.5 / 75 / 213 ms at 1k / 10k / 25k | **O(1)**: one `MULTI` of 4 commands + 1 HMAC; target ≤ 1 ms wall, ≤ 0.2 ms CPU, **flat** | `feed_seq` equality short-circuits before any record is read |
| Kill-switch refresh, one scope changed | same as steady (full re-verify) | O(1) + 1 record fetch + 1 HMAC | delta selection |
| Plan reconcile, steady state | O(N log N): 3.1 / 24.6 / 72 ms | **O(1)** | same |
| Plan reconcile, one plan changed | O(N) detect + 1 compile | O(1) + 1 fetch + 1 compile | same |
| Store publish per write | O(R): p50 30–76 / 114–623 / 255–1 724 ms | **O(1)**: ~5 commands, target p50 ≤ 5 ms, p99 ≤ 25 ms, flat | per-record publish |
| Store bytes per write | 3.5 MB at 10k keys, 17.4 MB at 50k | ~1 KB | per-record publish |
| Identity round trips after a key write | O(active keys): `rt/req` 0.134 → 0.986 | ~`O(1)`: one evicted key ⇒ at most one refetch (single-flighted) | per-key invalidation |
| Startup state load per worker | O(S + N) | O(engaged) + lazy plans | `{rv2}:on:ks` + lazy plan load |
| Re-hydrator round per kind | O(R) PG + O(R log R) digest, 1/s | O(1) compare (`feed_seq`, `count`, `ZCARD`, top score); O(R) only on a real mismatch | manifest comparison instead of digest comparison |
| Metric series per worker | O(N) (50 003 at 50k) | O(1) + bounded top-K | aggregate metrics |
| Loop lag p99.9 | 6.7 (1k) / 79–84 (10k) / 223–240 (25k) ms | ≤ 5 ms at every level | no O(N) work on the loop |
| C4 p99 | 18.9–19.9 / 145–153 / 405–459 ms | **< 20 ms at 1k, 10k and 25k, all four phases** | the above, combined |

Expected C4 p99 after the fix: the tenant-dependent terms vanish, so C4 should return to the N0
shape (11.5–12.6 ms at 3 tenants) plus the genuinely tenant-dependent residue — per-org `Gcra`
state and the `PlanInspector` cache, which are RAM lookups, not loop work. The honest prediction is
**12–15 ms at all three levels**, with the 20 ms gate carrying ~5 ms of margin. That prediction is a
hypothesis until L05c-1 runs; it is not evidence.

What the plan does **not** claim: that 25k tenants per gateway is a product capacity statement.
GW05c certifies that C4 is independent of tenant count under the E2-01 workload. Per-tenant RAM
(S6) still grows and is a separate sizing question.

---

## 14. Acceptance criteria

### L05c-1 (the card's live test)

Re-run E2-01 exactly as in round 2: **1 gateway × 12 workers + 1 guard, 120 RPS, 2 000 active
keys**, phases **A** steady / **B** key writes / **C** plan writes / **D** kill-switch writes, each
write phase writing every 5 s, at **1 000 / 10 000 / 25 000** tenants.

| Criterion | Pass, at every level and in every phase | Where it comes from |
|---|---|---|
| C4 p99 | < 20 ms | client-side harness record (`analyze.py`, the C4 definition), **not** the gateway's `t_input` — R2-11/M1 proved the server view under-reads C4 by 0.95–5.8 ms |
| infra | ≤ 0.1 % | status-code histogram: any 5xx that is not a declared posture 503 |
| Loop lag p99.9 | ≤ 5 ms | `loop_lag_ns` histogram per worker, worst worker reported (today it already fails at N1) |
| Store publish per write | ≤ 25 ms | control-plane writer timing (`pub_ms` p50/p99/max), one sample per write |
| Kill-switch refresh cost | **flat with tenant count, within ±20 % of the 1k baseline** | new `state_refresh_seconds{kind="ks"}` histogram, reported as wall and CPU (today's `ks_ms` / `ks_cpu`) |

### Additional gates the card is wired to

| Gate | Test | Pass |
|---|---|---|
| G-08 | L05c-1 | as above |
| G-04 | cold start with many keys: 3× step with 2 000 keys over 200 orgs | 0 × 503 `shared_state_unavailable` on new gateways |
| L14d-2 (GW14d) | 2 000 and 20 000 UUID-named tenants at default and raised slots | 0 × 500 from requests or `/metrics`; drops counted |

### How each measurement is obtained

| Measurement | Source | Procedure |
|---|---|---|
| C4 p99 / p99.9 | harness `bin/v2.3` (or later) client records, `analyze.py` | multi-key mode `-auth-file` with 2 000 keys; one run per (level, phase); 300 s measured after a warm-up; the harness is run from a **frozen immutable copy** (R2-20: editing a running harness killed a round-2 measurement) |
| infra % | the same records, status histogram | declared posture 503s (`kill_switch_unavailable`, `shared_state_unavailable`, `plan_unavailable`, `budget_unavailable`) are **not** infra; everything else is. Requires GW14d's `rejected{status,reason}` split to classify honestly |
| Loop lag p99.9 | `loop_lag_ns` histogram, per worker, via the node exporter | report the worst of 12 workers; `_loop_lag` already samples at `ks_refresh_ms/5000` s |
| Store publish per write | control-plane writer emits `store_publish_seconds{kind}` and `publish_records{kind}` per write | assert `publish_records == 1` for single writes — this is the structural proof that the whole-kind `MULTI` is gone, independent of timing |
| ks refresh cost, flat | `state_refresh_seconds{kind="ks"}` p50/p99 at 1k vs 10k vs 25k | pass = `p99(10k) ≤ 1.2 × p99(1k)` and `p99(25k) ≤ 1.2 × p99(1k)`; also record CPU time per round so a store-latency change cannot mask a CPU regression |
| Feed lag | `state_feed_lag_records{kind}` | must return to 0 between writes; its peak during phase B/C/D bounds propagation |
| Propagation correctness | the existing r2state canaries: killed org **K**, revoked key **R** | per phase, probe K and R at ≥ 24/s; 0 admits after the write's ack + the declared bound |
| Store op outliers | `kv>25` (store ops over `RV_STORE_TIMEOUT_MS`) | today 22–221 per run; target ≈ 0 in phases B/C/D, which is the publish-stall proof |

### Environment reality check

The round-2 GCP estate was torn down on 2026-09-24 (controller teardown: 70 VMs, 4 Cloud SQL,
4 Valkey, all reservations). L05c-1 needs a rebuilt lane: 1 × `c4-highcpu-16` gateway,
1 × `g2-standard-4` guard (L4 — subject to the R2-15 Mumbai stock-out problem, so reserve),
1 Cloud SQL, 1 Memorystore Valkey, 1 control-plane VM for the re-hydrators (×2, per GW05b),
2 loadgen VMs. The 25k-tenant seed must use the **bulk-onboarding path** (§11.5), which is
conveniently also a test of it. Per R2-24, the lane runs an idle watchdog, burn-rate monitor and
store-memory alarm from the start, and the store is UNLINKed between runs (B40) so audit growth
(H1/R2-05) does not contaminate the measurement.

---

## 15. Regression requirements

### What must not break, and the test that proves it

| Area | Existing coverage to keep green | New coverage required |
|---|---|---|
| State correctness (no violations) | the 15 RC2 fault runs behind R2-17: FLUSHALL ×3, re-hydrator gap ×3, versions ×3, Cloud SQL failover ×3, Valkey planned failover ×3 | re-run all 15 against the delta path; 0 violations |
| Plan updates | `gateway_v2/tests/plan/test_lgw05.py` — all 12 tests, specifically `test_lgw05_4_replicas_converge_without_regressing`, `test_lgw05_5_pin_ignores_a_later_push`, `test_version_cannot_regress`, `test_last_known_good_expires`, `test_flush_keeps_the_tenant_known` | delta-apply convergence; delta apply must not regress a plan; a nudge with a lower `feed_seq` is a no-op |
| Three plan states | `test_lgw05_1`, `test_lgw05_2`, `test_unknown_tenant_is_not_unavailable` | a cold lazy-load miss is `PLAN_UNAVAILABLE`, **never** `PLAN_UNKNOWN_TENANT` (this is the regression risk lazy loading introduces, and it is the exact v2.1 defect C36 recorded: a flush + re-seed wedged tenants at 403 forever) |
| Kill switches | RC2 `tests/unit` ks suite; the explicit-OFF invariant | engage/disengage via delta; `{rv2}:on:ks` with `SCARD != on_count` ⇒ fail closed; a scope ON in the record but missing from the set ⇒ fail closed (the fail-open trap) |
| Identity / key updates | RC2 identity suite; epoch-checked fill | per-key eviction evicts exactly one entry; a revoked key is refused within one round; a cached principal for an *unrelated* key survives a key write (the behaviour change, asserted) |
| Authorization | `test_lgw05_6_concurrent_tenants_do_not_leak` (8 threads × 50 lookups, zero cross-reads) | delta apply under concurrent lookups, 0 cross-tenant reads; single-flight must not hand org A's principal to org B's waiter |
| Cache consistency | — | single-flight: K concurrent misses ⇒ exactly 1 store round trip, K identical results; negative cache invalidated by a `key_add` |
| Startup / recovery | RC2 flush + gap runs | cold start reads O(engaged), not O(S); cursor floor comes from the stamp, never 0 |
| Worker restarts | `rc3-state-p0` SP1 functional scenario | a restarted worker on a lagging replica admits 0 revoked keys / 0 killed orgs |
| Re-hydration | `rc3-state-p0` `lock` scenario (all 4 kinds restored in 0.54–0.79 s under a held `FOR UPDATE`) | whole-kind republish rewrites index + manifest atomically; workers resume by delta, not full reload |
| Concurrent writes | `F-C36-RACE` (0/751 RC1, 0/360 RC2 mislabelled manifests) | two writers interleaved ⇒ `feed_seq` strictly increasing, no gap, `count` correct |
| Ordering / version guarantees | `test_version_cannot_regress`; `verify_meta(not_before=...)` | epoch bump keeps `feed_seq` monotone; a record whose `feed_seq` ≠ its index score is rejected |
| Existing API behaviour | `gateway_v2/tests/openai_conformance/*` (OpenAI SDK wire contract, GW01) and `tests/parity/*` (C2 replay differ, C3 scorecard) | unchanged — GW05c touches no wire surface |
| Structural gates | `gateway_v2/lint/*` + `tests/gates/*`: ≤ 800 lines/module, ≤ 120 lines/function, no module-level mutables, frozen dataclasses, import-linter layers | new modules must satisfy all of them; the feed client belongs in `gateway_v2.runtime` |

### New test files

```
gateway_v2/tests/runtime/test_lgw05c_feed.py        version index, cursor, completeness checks,
                                                     epoch-bump monotonicity, idempotent apply,
                                                     out-of-order and duplicate deltas
gateway_v2/tests/plan/test_lgw05c_delta.py          O(changes) reconcile; no O(N) scan (assert the
                                                     number of store calls and records touched);
                                                     lazy cold load is PLAN_UNAVAILABLE on miss;
                                                     reconcile does not block lookup (no shared lock)
gateway_v2/tests/admit/test_lgw05c_identity.py      per-key eviction, single-flight, negative cache,
                                                     unrelated-key survival, revocation bound
gateway_v2/tests/admit/test_lgw05c_killswitch.py    engaged-set cold start, SCARD/on_count fail-closed,
                                                     flat refresh (assert 0 records read when feed_seq
                                                     is unchanged), delta engage/disengage
gateway_v2/tests/runtime/test_lgw05c_complexity.py  THE GUARD TEST: with 10 000 registered tenants and
                                                     zero changes, a refresh round must touch 0 records
                                                     and issue a constant number of store commands.
                                                     This is the test that makes the defect
                                                     un-reintroducible.
```

Plus, on the control-plane side: per-record publish asserts `publish_records == 1`; `put_many`
batch semantics; manifest-last ordering; `count`/`on_count` maintenance across
put/delete/on→off/off→on transitions.

---

## 16. Implementation plan

The phases are ordered so that every phase is independently testable and no phase leaves the tree
in a state where correctness depends on a later phase. Phases 1–3 are worker-side and can be
developed against a fake store; phase 4 is the control plane; phase 5 removes the old paths.

### Phase 0 — prerequisite decision and GW05b

GW05c `Depends on GW05, GW05b`. GW05b is **not implemented** in `gateway_v2` (no store layer) and
exists only as the reference patch `rc3-state-p0.patch` against RC2. Nothing in phases 1–7 should
start until §24's question is answered. If GW05b lands first in `gateway_v2`, it brings the store
client, the manifest, the stamp and the re-hydrator — and GW05c then changes their *shape*, which
is a much smaller diff than building both at once.

### Phase 1 — state / version model

Files (new):
- `gateway_v2/gateway_v2/domain/state.py` — frozen `Version(epoch, seq)`, `FeedSeq = int`,
  `StateKind` StrEnum (`plan`, `key`, `ks`, `budget`), `SignedRecord`, `Manifest`,
  `StoreDataUnavailable`. Pure, no I/O, bottom layer (importable by `plan` and `admit`).
- `gateway_v2/gateway_v2/runtime/state_sig.py` — `make_record`, `make_meta`, `verify_record`,
  `verify_meta`, `record_sig`, `meta_sig`. Port of `rvproto/runtime/versioned.py` **minus**
  `digest()`/`verify_set()` on the read path.

Files (changed):
- `gateway_v2/gateway_v2/domain/plan.py` — add `feed_seq: int` to `ExecutionPlan`; extend
  `is_newer` to break ties on `feed_seq`. (Frozen dataclass: additive field, defaults not allowed
  by the frozen gate style here, so update the three test constructors.)

Changes:
- `Manifest` carries `{kind, epoch, seq, feed_seq, count, on_count, sig}`; the signature domain
  string is extended, which is a **wire-format change** to the manifest — see §22.
- No `digest` in the manifest. Completeness = `count` + `on_count` + index head.

Gate: unit tests for signature round-trip, tamper rejection, `not_before` regress rejection,
`feed_seq` monotonicity across an epoch bump.

### Phase 2 — change feed (store client)

Files (new):
- `gateway_v2/gateway_v2/runtime/store_keys.py` — the key layout of §11.2, namespaced
  (`RV_NAMESPACE`-ready, per the r3-ns note in the rc3 README) with the `{rv2}` hash tag preserved.
- `gateway_v2/gateway_v2/runtime/state_feed.py` — `FeedClient`:
  - `read_head(kind) -> Manifest | StoreDataUnavailable` — one pipeline:
    `GET meta`, `ZCARD idx`, `ZREVRANGE idx 0 0 WITHSCORES`, `GET stamp`; verifies the manifest,
    asserts `ZCARD == count` and `top_score == feed_seq`.
  - `read_delta(kind, after: FeedSeq, limit: int) -> tuple[tuple[str, FeedSeq], ...]` —
    `ZRANGEBYSCORE idx (after +inf WITHSCORES LIMIT 0 limit`.
  - `fetch_records(kind, keys) -> ...` — pipelined `GET`/`HMGET`, `verify_record` each.
  - `nudges(kinds) -> AsyncIterator[tuple[StateKind, FeedSeq]]` — port of
    `rvproto/runtime/push.PushListener` **verbatim in behaviour** (the D2/R2-19 hardening is
    non-negotiable), with `accept` extended to all four kinds and the message parsed as
    `"<kind>:<feed_seq>"`.
- `gateway_v2/gateway_v2/runtime/store_conn.py` — port of `rvproto/runtime/storeconn.py`
  (every transport fault → `ConnectionError`, dead sockets never reused) + `connect` /
  `connect_background` with contract-derived pool size and separate request-path vs background
  timeouts. **This is GW05b/GW06 territory**; if GW05b lands first, reuse it instead.

Changes:
- `gateway_v2/pyproject.toml` — add `redis>=8.1` and `orjson` to `dependencies` (currently `[]`).
  First runtime dependency in the tree; flag it for the owner.

Gate: tests against `fakeredis` (already used elsewhere in the repo) for head/delta/completeness,
and a nudge test that reproduces the D2 spin signature and asserts the connection is declared dead.

### Phase 3 — worker incremental synchronisation

Files (new):
- `gateway_v2/gateway_v2/plan/delta.py` — `PlanDeltaApplier`: head → short-circuit on
  `feed_seq` equality → delta → fetch → verify → `compile_plan` → `PlanStore.put`, with the
  per-round budget and `await asyncio.sleep(0)` between chunks.
- `gateway_v2/gateway_v2/admit/killswitch.py` (currently a 3-line stub) — `KillSwitchSnapshot`
  with the incremental `engaged` / `orgs` / `models` sets, `{rv2}:on:ks` cold start,
  `SCARD == on_count` fail-closed, `state()` staleness as RC2.
- `gateway_v2/gateway_v2/admit/identity.py` (currently a 3-line stub) — `Identity` per §11.4:
  bounded LRU, negative cache, `_inflight` single-flight, `on_delta` per-key eviction.
- `gateway_v2/gateway_v2/runtime/state_task.py` — the single background refresher that owns all
  four cursors, drives one round per nudge or per period, and offloads to an executor above the
  threshold. One task, not four, so per-round work is budgeted globally.

Files (changed):
- `gateway_v2/gateway_v2/plan/snapshot.py` — **remove** the O(N) `reconcile()`; replace with
  `apply(records)` (O(changes)) and make the served snapshot an immutable object swapped by atomic
  reference so `lookup()` takes no lock the writer holds (fixes S7).
- `gateway_v2/gateway_v2/plan/store.py` — `known()` must stop being `tuple(sorted(...))` on a hot
  path; keep it for tests/diagnostics only, or return a frozenset without sorting.

Gate: `test_lgw05c_complexity.py` — 10 000 tenants, no changes, one round ⇒ 0 records fetched and
a constant command count. Plus all 12 existing LGW05 tests still green.

### Phase 4 — control-plane per-record publish

Files (new, outside `gateway_v2` — the control plane needs a home; see §24):
- `control/.../state/writer.py` — `StateWriter.put` publishing **one** record:
  `WATCH meta` → `SET/HSET` record, `ZADD idx`, `SADD/SREM on:ks`, `SET meta`, `PUBLISH` — one
  O(1) `MULTI`. `publish_record(kind, rec)` replaces `publish(kind)` on the write path.
- `control/.../state/writer.py::put_many` — the bulk path of §11.5 (chunked, manifest last).
- `control/.../state/rehydrate.py` — `diagnose` compares `(epoch, seq, feed_seq, count, on_count)`
  and `ZCARD` / top score **without** `digest`; full `publish_kind` only on mismatch. Keeps
  GW05b's fixed-rate rounds, per-kind isolation and store-first diagnose.

Files (changed):
- schema: `rv2_version` + `feed_seq bigint NOT NULL DEFAULT 0`, `count bigint NOT NULL DEFAULT 0`,
  `on_count bigint NOT NULL DEFAULT 0`; `rv2_record` + `feed_seq bigint NOT NULL DEFAULT 0` and
  an index `(kind, feed_seq)`. See §19.

Changes:
- `publish_kind` (whole-kind) survives **only** here, reachable from the re-hydrator and an explicit
  `rv2 republish` command. A lint/test assertion keeps the write path from calling it.

Gate: `publish_records{kind} == 1` for every single write; `count`/`on_count` correct across all
record transitions; two concurrent writers ⇒ no `feed_seq` gap; `put_many` leaves no partial
generation visible (manifest-last proof).

### Phase 5 — remove the full-refresh paths

Files (changed):
- `gateway_v2/gateway_v2/plan/snapshot.py` — O(N) `reconcile` deleted (done in phase 3; this phase
  removes the compatibility shim and the test that exercised it).
- `gateway_v2/gateway_v2/runtime/state_sig.py` — `digest`/`verify_set` exist only for the
  re-hydrator's repair comparison, and are **not importable from `plan` or `admit`** (enforced by an
  import-linter contract, so the O(N) path cannot drift back onto the serving loop).
- metrics: remove `plan_version_info{org,version}` and `killswitch_engaged{scope,key}`; add the
  O(1) families of §11.7; delete the O(N) label-set rebuild from the per-second gauge pass.
- startup: no eager full load; `{rv2}:on:ks` + lazy plans.

Gate: a grep/AST lint that fails the build if `verify_set`, `digest`, `HGETALL`, `HVALS` or
`SMEMBERS` appear anywhere under `gateway_v2/plan/` or `gateway_v2/admit/`. This is the durable
backstop for §2.1 rule (1).

### Phase 6 — testing

Tests: the five new files of §15, plus:
- re-run the 15 C36 fault scenarios (flush, gap, versions, Cloud SQL failover, Valkey failover).
- the `rc3-state-p0` functional scenarios `sp1`, `sp1-live`, `sp2`, `lock`, `absent`, `pgdown`,
  `storeblip` against the delta path — GW05c must not change any of their verdicts.
- the 45 s store-partition regression (R2-19 / D2).
- G-04 cold start: 3× step, 2 000 keys over 200 orgs, 0 × 503 `shared_state_unavailable`.
- a bulk-onboarding test: 25 000 records via `put_many` while 120 RPS is served; assert C4 p99
  stays < 20 ms and `state_feed_lag_records` returns to 0.

### Phase 7 — load validation

Run L05c-1 (§14) at 1k / 10k / 25k tenants × phases A/B/C/D, 3 repeats at each level, from a frozen
harness release (≥ v2.3 for multi-key). Report per run: C4 p99/p99.9, infra %, loop lag p99/p99.9
(worst worker), `state_refresh_seconds{ks}` p50/p99 wall **and** CPU, `store_publish_seconds` p50/p99,
`publish_records`, `state_feed_lag_records` peak, `kv>25`, the K/R canary admit counts, and the
reason mix. Then G-04 and L14d-2 (with GW14d).

Publish the evidence as `docs/plans/evidence/<date>-gw05c/` with `verdict.json` in the same shape
as `2026-09-29-gw05/verdict.json`, plus the per-level `analysis.out` files so the result is directly
comparable to `r2-scale/levels/N{1,2,3}.analysis.out`.

---

## 17. Database / schema changes

Minimal and additive. Justification for each, since the brief asks for it.

```sql
-- rv2_version: the per-kind counters a worker needs to validate completeness in O(1)
ALTER TABLE rv2_version ADD COLUMN IF NOT EXISTS feed_seq  bigint NOT NULL DEFAULT 0;
ALTER TABLE rv2_version ADD COLUMN IF NOT EXISTS count     bigint NOT NULL DEFAULT 0;
ALTER TABLE rv2_version ADD COLUMN IF NOT EXISTS on_count  bigint NOT NULL DEFAULT 0;

-- rv2_record: the record's own feed position, mirrored as its index score in the store
ALTER TABLE rv2_record  ADD COLUMN IF NOT EXISTS feed_seq  bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS rv2_record_kind_feed ON rv2_record (kind, feed_seq);

-- rv2_log: the audit trail should record the feed position too
ALTER TABLE rv2_log     ADD COLUMN IF NOT EXISTS feed_seq  bigint NOT NULL DEFAULT 0;
```

| Column | Why it is required |
|---|---|
| `rv2_version.feed_seq` | the monotone cursor space. Cannot be derived from `seq` because `seq` resets on an epoch bump, which would hide changes from a worker whose cursor is above the reset score. |
| `rv2_version.count` | replaces the whole-set `digest` as the completeness proof on the read path. Maintained in the write transaction so publish stays O(1); computing it with `SELECT count(*)` per write would reintroduce an O(R) scan. |
| `rv2_version.on_count` | the same proof for the kill-switch **engaged** set, which is what makes cold start O(engaged) instead of O(S). Without it, a scope that is ON but missing from `{rv2}:on:ks` would fail **open** — the exact C36 failure class ("missing key = off") that must not return. |
| `rv2_record.feed_seq` | lets `publish_kind` (repair) rebuild the index with correct scores, and lets the re-hydrator find changes since a position without a full scan. |
| the `(kind, feed_seq)` index | makes the re-hydrator's "changed since" query O(changes). |
| `rv2_log.feed_seq` | forensic completeness; a rollback needs to know the feed position it is restoring from. |

Backfill: a one-shot migration sets `feed_seq = row_number() over (partition by kind order by epoch, seq)`
and `count`/`on_count` from `count(*)` / `count(*) where not deleted and body->>'on' = 'true'`,
then one `publish_kind` per kind. `DEFAULT 0` keeps the old code readable during the rollout.

No change to `rv2_budget_checkpoint`.

---

## 18. Redis / Valkey changes

| Key | Change | Why |
|---|---|---|
| `{rv2}:meta:<kind>` | **Changed shape**: `digest` removed, `feed_seq` + `on_count` added; signature domain string extended. Breaking — see §22. | the digest is the O(N) forcing function |
| `{rv2}:idx:<kind>` | **New** ZSET, member = record key, score = `feed_seq` | the change feed |
| `{rv2}:on:ks` | **New** SET of engaged scopes | O(engaged) cold start without a fail-open hole |
| `{rv2}:plan_index` | **Retained**, written by `publish_kind` only; no longer read per reconcile | avoids a flag-day; can be dropped in a later card |
| `{rv2}:updates` | message becomes `"<kind>:<feed_seq>"`; all four kinds now nudge (today only `plan`) | informative nudge; fixes the poll-only ks/key regime (SP3a) |
| `{rv2}:stamp` | GW05b's stamp gains per-kind `feed_seq` in its `versions` map | cursor floors must never start at 0 |
| `{rv2}:plan:<org>`, `{rv2}:keys`, `{rv2}:ks`, `{rv2}:budget_meta` | unchanged shape; records gain a `feed_seq` field | per-record signatures unchanged |
| `{rv2}:budget:<org>`, `{rv2}:budget_gen:<org>` | untouched | R2-09/GW06 owns these |

Memory: `{rv2}:idx:<kind>` adds one ZSET entry per record. At 25k orgs a ZSET of 25k short members
is on the order of a few MB — immaterial next to the audit streams that filled 6.4 GiB in 51 minutes
(H1/R2-05), but it must be included in GW14c's memory budget accounting.

Cluster safety: every new key keeps the `{rv2}` hash tag, so all multi-key reads and `MULTI`s stay
in one slot on a cluster-mode store, as `runtime/store.py` already requires.

Commands introduced on the serving path: `ZCARD`, `ZREVRANGE … 0 0`, `ZRANGEBYSCORE … LIMIT`,
`SCARD`, `SMEMBERS` (cold start only, O(engaged)). All O(1) or O(log N + changes). **No**
`HGETALL`/`HVALS`/`KEYS`/`SCAN` on the serving path — this is the lint rule of phase 5.

---

## 19. Worker changes

| Component | Today | After |
|---|---|---|
| Background tasks | `ks.run()` (500 ms, O(S)) + `plans.run()` (1 s, O(N)) + `plans._listen()` | **one** `state_task` owning four cursors, driven by nudges + a period, O(1) when idle, O(changes) otherwise, with a global per-round budget |
| Kill-switch snapshot | rebuilt from the full set every refresh | incrementally maintained `engaged`/`orgs`/`models`; cold start from `{rv2}:on:ks` |
| Plan snapshot | `_plans`/`_invalid`/`_applied` dicts copied every reconcile; O(N) scans | immutable snapshot swapped by atomic reference; per-record apply; lazy cold load with single-flight |
| Identity | global cache wipe, unbounded cache, no single-flight, no negative cache | per-key eviction, bounded LRU, single-flight, negative cache with TTL |
| CPU placement | all verify/compile inline on the serving loop | inline for small deltas; GIL-releasing executor above `RV_STATE_OFFLOAD_THRESHOLD` |
| Startup | `await ks.refresh_once()` + `await plans.load_all()` = O(S + N) per worker | `read_head` + `{rv2}:on:ks` = O(engaged); plans lazy |
| Request path | unchanged (pure RAM) | **unchanged** — this is the point: no new store call on the request path except the already-declared cold identity/plan miss |
| Metrics | O(N) series + O(N) rebuild on the loop | O(1) families + bounded top-K |

New knobs (all with validated bounds at start-up, as `runtime/config.py:239-242` already does):

| Knob | Default | Meaning |
|---|---|---|
| `RV_STATE_DELTA_BUDGET` | 256 | max records applied per round per kind |
| `RV_STATE_OFFLOAD_THRESHOLD` | 32 | delta size above which verify/compile goes to the executor |
| `RV_IDENTITY_CACHE_MAX` | 50 000 | principal cache bound (LRU) |
| `RV_IDENTITY_NEG_TTL_MS` | 2 000 | negative-cache TTL; must be ≤ `RV_KS_STALE_MS` |
| `RV_PUBLISH_CHUNK` | 500 | records per `MULTI` in the bulk path (control plane) |

`RV_KS_REFRESH_MS` (500) and `RV_PLAN_RECONCILE_MS` (1000) keep their defaults so the declared SP3
propagation bounds do not change inside this card. They become candidates for shortening *after*
L05c-1 passes, because the rounds are then O(1).

---

## 20. Cache changes

Summarised because §11.4 has the detail:

1. `on_epoch` → `on_delta(changed_hashes, feed_seq)`. Per-key eviction. The global wipe is deleted.
2. Bounded LRU with `RV_IDENTITY_CACHE_MAX`, so 25k tenants × keys cannot grow a worker's RSS
   without limit (S6).
3. `_inflight: dict[str, Future]` single-flight; `identity_single_flight_joins_total` proves it.
4. Negative cache for never-issued keys, invalidated by the key delta.
5. Epoch-checked fill retained verbatim — a principal enters the cache only at the applied version.
6. Plan `PlanInspector` cache keyed `(org, version)` gains a bound (S6); out of scope to redesign,
   in scope to stop being unbounded.

---

## 21. Risks and trade-offs

| Risk | Severity | Mitigation |
|---|---|---|
| **Weakening the completeness proof.** The whole-set digest detects any discrepancy; `count` + `ZCARD` + top-score + per-record HMAC detects a different (narrower) set. | HIGH — this is the crux of the design | The set it must catch is "partial or mixed-generation store". `count`/`ZCARD` catches missing or extra records; `top_score == feed_seq` catches a mixed generation; `rec.feed_seq == score` and `(epoch,seq,hash) == index entry` catch a stale record served under a new score; per-record HMAC catches tampering. What it no longer catches in one read is *two compensating errors* (one record missing and one extra, with a consistent top score) — which requires the publisher to be byte-correct per record and wrong in aggregate. The re-hydrator still computes the full digest every round, off the serving loop, so that residue is detected within one round. This trade must be stated explicitly in the GW05c evidence package. |
| `on_count` / `{rv2}:on:ks` introduces a **fail-open** path if the set and the manifest can disagree | HIGH | `SCARD != on_count` ⇒ `StoreDataUnavailable` ⇒ fail closed. Both are written in the same `MULTI`. A dedicated negative test asserts the fail-closed direction. |
| Lazy plan loading turns a cold miss into a request-path store call | MEDIUM | Already the declared behaviour for identity; the posture is `PLAN_UNAVAILABLE`/503, never `PLAN_UNKNOWN_TENANT` (asserted). Single-flight bounds the count. Measured by gate G-04. |
| Manifest format change is **breaking** between planes | MEDIUM | Rollout order and a dual-read window — see §22. |
| Adding `redis`/`orjson` as `gateway_v2`'s first runtime dependencies | MEDIUM | Unavoidable for any store layer; pin exact versions; `redis>=8.1` is what the D2/R2-19 fix is written against. Flag for owner approval. |
| Shortening the propagation path changes SP3's declared regimes | LOW | Keep the periods unchanged in this card; re-declare the bounds in a follow-up with measurements. |
| The delta budget could let a worker lag indefinitely under a write storm | LOW | Back-to-back rounds when the budget is hit, plus `state_feed_lag_records` as an alertable gauge. A write storm large enough to saturate it is also a control-plane abuse signal. |
| `{rv2}:idx:<kind>` memory | LOW | A few MB at 25k; include in GW14c's budget. |
| Per-record publish loses the atomic "whole kind at one version" property | LOW | It was never needed by readers once the index exists: a reader's view is always a prefix of the feed, and `feed_seq`/`count` make a partial batch unobservable (manifest written last). |
| Re-measuring on a rebuilt lane may not reproduce the round-2 baseline exactly (different VMs, L4 availability) | MEDIUM | Re-run N1 (1k) as the control on the new lane **before** claiming the fix, and report the 1k baseline from the new lane, not the old one. The "±20 % of 1k" criterion is self-normalising for exactly this reason. |
| Scope creep into GW05b / GW06 / GW14d | MEDIUM | §10's "Related" table is the boundary. Phase 0 resolves the GW05b dependency explicitly rather than absorbing it. |

---

## 22. Compatibility and rollout

The manifest shape change means an old gateway cannot read a new manifest and vice versa.

| Step | Action |
|---|---|
| 1 | Postgres migration (additive, `DEFAULT 0`) + backfill. Old writers keep working. |
| 2 | Deploy the control plane with **dual-write**: the manifest carries `digest` *and* `feed_seq`/`count`/`on_count`, and the publisher writes `{rv2}:idx:<kind>` + `{rv2}:on:ks` alongside the existing hashes. Signature domain versioned as `meta2\n…` with the old `meta\n…` still produced. |
| 3 | Re-hydrators first (per GW05b's deployment rule), so the index exists everywhere before any reader depends on it. |
| 4 | Deploy gateways reading the new fields. A gateway that finds no `{rv2}:idx:<kind>` falls back to the old whole-set path **for one release only**, behind `RV_STATE_FEED=off`. |
| 5 | Flip `RV_STATE_FEED=on`, verify, then drop the per-write whole-kind publish. |
| 6 | A later cleanup card removes `digest` from the manifest, the `meta\n` signature domain, the fallback path and `{rv2}:plan_index`. |

The escape hatch (`RV_STATE_FEED=off` restores RC2 behaviour) is also the negative control for the
L05c-1 measurement: the same lane, same seed, flag off ⇒ the round-2 numbers should reappear. That
is the cleanest possible proof that the fix is the cause of the improvement.

---

## 23. Files

### Expected to change

| File | Phase | Change |
|---|---|---|
| `gateway_v2/gateway_v2/domain/state.py` | 1 | **new** — version/kind/record/manifest vocabulary |
| `gateway_v2/gateway_v2/domain/plan.py` | 1 | `feed_seq` on `ExecutionPlan`; `is_newer` tie-break |
| `gateway_v2/gateway_v2/runtime/state_sig.py` | 1 | **new** — signing/verification, no read-path digest |
| `gateway_v2/gateway_v2/runtime/store_keys.py` | 2 | **new** — key layout |
| `gateway_v2/gateway_v2/runtime/store_conn.py` | 2 | **new** (or reuse GW05b's) — bounded store client |
| `gateway_v2/gateway_v2/runtime/state_feed.py` | 2 | **new** — head/delta/fetch/nudge |
| `gateway_v2/gateway_v2/runtime/state_task.py` | 3 | **new** — the single background refresher |
| `gateway_v2/gateway_v2/plan/delta.py` | 3 | **new** — O(changes) plan applier |
| `gateway_v2/gateway_v2/plan/snapshot.py` | 3, 5 | O(N) `reconcile` removed; immutable snapshot, atomic swap |
| `gateway_v2/gateway_v2/plan/store.py` | 3 | `known()` off the hot path |
| `gateway_v2/gateway_v2/admit/killswitch.py` | 3 | **implement** (stub today) — incremental engaged sets |
| `gateway_v2/gateway_v2/admit/identity.py` | 3 | **implement** (stub today) — per-key invalidation, single-flight, negative cache |
| `gateway_v2/gateway_v2/plan/__init__.py` | 3 | export surface |
| `gateway_v2/pyproject.toml` | 2 | first runtime dependencies; import-linter contract forbidding `state_sig.digest` in `plan`/`admit` |
| control-plane writer + re-hydrator (location per §24) | 4 | per-record publish, `put_many`, digest-free diagnose |
| control-plane schema/migration | 4 | the five `ALTER`s of §17 |
| metrics module (gateway + exporter) | 5 | drop per-org series, add the O(1) families |
| `gateway_v2/lint/` + `gateway_v2/tests/gates/` | 5 | the "no whole-set read in plan/admit" AST gate |
| `gateway_v2/tests/plan/test_lgw05.py` | 1 | constructor updates for the new `ExecutionPlan` field only |
| the five new test files | 6 | §15 |
| `docs/plans/evidence/<date>-gw05c/` | 7 | verdict + per-level analysis |

### Should NOT change

| File / area | Why |
|---|---|
| `gateway/ai_mesh_gateway/**` (v1 production gateway) | different design, not in R2-02 scope; the MCP/chat hardening work in `AGENTS.md` owns that tree and the index is shared |
| `gateway_v2/gateway_v2/contracts/**`, `gateway_v2/tests/openai_conformance/**`, `gateway_v2/tests/parity/**` | the frozen OpenAI wire contract (GW01) and the C2/C3 parity harness (GW02). GW05c touches no wire surface. |
| `gateway_v2/gateway_v2/resolve/**` | one enforcement authority; the import-linter contract forbids it importing anything but `domain`. GW05c must not give the resolver state access. |
| `gateway_v2/gateway_v2/runtime/resources.py`, `pools.py`, `cgroup.py` | GW03's `ResourceContract`. Pool sizes are derived there; GW05c consumes them. |
| `gateway_v2/gateway_v2/domain/locks.py` | owner-signed locks (`FRESH_MS=5000`, `PG_GRACE_MS=16000`, `InFlightKill`). GW05c reads them; changing them is a GW05b/GW19 decision. |
| budget / quota: `{rv2}:budget*`, `TokenLease` | R2-09 / GW06 |
| audit sink and streams | R2-05 / GW14c |
| guard discovery, owner queue, C43 push | GW08 / GW19 |
| `docs/plans/evidence/2026-09-24-runbook-v3-round2/**` | immutable round-2 evidence. The RC2 tarball and patches are read-only artifacts. |
| `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` | the SOT. GW05c implements it; it does not edit it. |

### Dependencies on other items

| Item | Direction | What is needed |
|---|---|---|
| **R2-03 / GW05b** | GW05c **depends on** it | the freshness stamp (cursor floors must not start at 0), the store client, the re-hydrator, PG session bounds. GW05c in return makes the re-hydrator's rounds O(changes) so stamps are not born stale (S3) — that is a *mutual* dependency and the reason Phase 0 exists. |
| **R2-04 / GW05b** | depends on | lock-free REPEATABLE READ publish snapshot + the four PG timeouts. Per-record publish reduces the exposure but does not replace them. |
| **R2-09 / GW06** | sibling, shares resources | GW06 moves lease refills off the request path. GW05c removes the publish stall that makes refills fail. Neither blocks the other; both touch the store pool and the 25 ms timeout, so they should be measured together at the end. |
| **R2-10 / R2-11 / GW14d** | GW14d **depends on** GW05c | GW05c deletes the O(N) per-org series and the O(N) loop rebuild. GW14d owns the size-check-before-insert, the overflow counter, the top-K bucketing and the typed/windowed histograms. The `state_*` metrics GW05c adds must follow GW14d's typing rules; if GW14d lands first, reuse `rc3-obs-v1`. |
| **GW20 / GW20b** | depends on GW05c | capacity runs must include tenant count ≥ 10k. Cannot be signed until L05c-1 passes. |
| **R2-05 / GW14c** | independent, but | a store at `maxmemory` refuses publishes, which stops propagation entirely. L05c-1 must UNLINK audit between runs or run with GW14c's budget in place. |
| **R2-21 / harness v2.3** | depends on | multi-key mode (2 000 keys) is required by L05c-1 and only exists from v2.2. |

---

## 24. Ready for implementation?

### Is the current implementation sufficiently understood?

**Yes.** Every defect in R2-02's description is located at a specific function and line, with the
measurement that proves it:

| Runbook claim | Code | Measurement |
|---|---|---|
| "re-verifies the whole kill-switch set every 500 ms … on the serving loop" | `state_v2.py:103-121`, task at `edge/app.py:79`, `RV_KS_REFRESH_MS=500` | `ks_ms` 8.5 / 75 / 213 ms, `ks_cpu` ≈ 0.9 × `ks_ms` |
| "reconciles the whole plan index every 1 s on the serving loop" | `snapshot_v2.py:77-103`, task at `edge/app.py:80`, `RV_PLAN_RECONCILE_MS=1000` | `plan_ms` 3.1 / 24.6 / 72 ms |
| "every write republishes a whole kind in one MULTI" | `writer.py:121-165` | `pub_ms` p50 30–76 / 114–623 / 255–1 724 ms |
| "any key write empties every worker's identity cache" | `state_v2.py:40-43` ← `state_v2.py:120` | `rt/req` 0.134 → 0.986, `idf/s` 14 → 118, 640 × 503 at N3-B |
| "C4 p99 18.9–19.9 / 145–153 / 405–459" | — | `r2-scale/levels/N{1,2,3}.analysis.out`, verified directly |

Two things the investigation found that the runbook row does not say, and that change the plan:

1. **Phase A already fails at 10k.** The periodic refresh alone breaks the SLO with zero writes, so
   the fix must start with the read path, not the publish path.
2. **`gateway_v2` has already reproduced the plan-reconcile defect** (`plan/snapshot.py:35-39`,
   `plan/store.py:49`), with lock contention added — and it has no store layer at all, so D1/D3/D4
   are *unwritten* rather than *broken*. GW05c is therefore mostly a design constraint on code not
   yet written, plus one concrete removal.

### Does the proposed design satisfy GW05c?

| Card requirement | Satisfied by | Confidence |
|---|---|---|
| per-record versions + change feed, refresh O(changes) | `feed_seq` + `ZSET {rv2}:idx:<kind>`; steady state is O(1) | high — structural, not tuned |
| no periodic full re-verify or reconcile on a serving loop | phase 3 + the phase 5 AST gate | high |
| writes publish only the changed record; whole-kind `MULTI` forbidden | phase 4; `publish_records == 1` assertion; `publish_kind` unreachable from the write path | high |
| identity cache invalidated per key, not by a global epoch | `on_delta` | high |
| single-flight on a cold cache | `_inflight` future map | high |
| a bulk-onboarding path | `put_many` + chunked publish + delta budget | medium — needs the live bulk test to confirm it does not disturb serving |
| metric cardinality independent of tenant count | phase 5 + GW14d | medium — the top-K machinery is GW14d's, so this is only complete when both land |
| C4 p99 < 20 ms at 10k and 25k in all four phases | the combination | **unproven until L05c-1 runs.** The design removes every measured tenant-dependent term; the residual is predicted 12–15 ms, which is a hypothesis. |

The one genuine design trade is the narrower completeness proof (§21, first row). It is defensible
and the re-hydrator still computes the full digest off the loop, but it must be written into the
evidence package rather than glossed.

### What must be clarified before coding

1. **Where does GW05c land, and does GW05b land first?** This is the blocking question.
   - (a) `gateway_v2`, after GW05b. Clean, matches the card's `Depends on`, but it means GW05b must
     first build the store client, manifest, stamp and re-hydrator in a tree that currently has
     `dependencies = []` — and then GW05c changes their shape. Recommended, with one amendment:
     **GW05b should adopt the `feed_seq`/`count`/`on_count` manifest shape up front**, so the
     manifest is written once instead of twice.
   - (b) `gateway_v2`, GW05b and GW05c together as one lane. Fewer rewrites, larger review surface.
   - (c) Patch RC2 (`rvproto2-rc2` + `c36m-rc2` + `rc3-state-p0`) to re-measure L05c-1 against the
     round-2 baseline on the same code, then port the design into `gateway_v2`. Gives the most
     credible measurement (identical build lineage, and the `RV_STATE_FEED=off` negative control)
     but produces throwaway code.
   - My recommendation: **(a) with the manifest amendment**, plus a scoped (c) if the owner wants
     the measurement before committing the rewrite.
2. **Control-plane home.** The RC2 writer/re-hydrator live in `rvproto/control/`. `gateway_v2` has no
   control-plane package, and `control/ai_mesh_control/` is the Django v1 control plane. Where do
   `StateWriter` and `Rehydrator` live in v3? This decides half of phase 4's file list.
3. **Runtime dependencies in `gateway_v2`.** `redis` and `orjson` would be the first ones. Pinned
   versions, and confirmation that this is acceptable under the GW00 structural regime.
4. **Lane budget for L05c-1.** The round-2 estate is gone. 1 gateway + 1 L4 guard + Cloud SQL +
   Valkey + 2 re-hydrator VMs + 2 loadgens, × 3 levels × 4 phases × 3 repeats. L4 availability in
   Mumbai is the R2-15 constraint; reservations are needed. Owner approval for spend, with the
   R2-24 watchdogs in place from the start.
5. **Does GW14d land before or after?** GW05c can delete the per-org series unilaterally, but the
   top-K replacement is GW14d's. If GW14d is later, GW05c ships aggregates only and the top-K is a
   follow-up — acceptable, but it should be a recorded decision rather than a gap.

### The exact first implementation step

Once question 1 is answered, and assuming (a):

> In `gateway_v2`, add `gateway_v2/gateway_v2/domain/state.py` containing the frozen
> `Version(epoch, seq)`, `StateKind` StrEnum (`plan`/`key`/`ks`/`budget`), `SignedRecord`,
> `Manifest(kind, epoch, seq, feed_seq, count, on_count, sig)` and `StoreDataUnavailable` —
> pure types, no I/O, in the bottom import layer — together with
> `gateway_v2/tests/runtime/test_lgw05c_feed.py::test_feed_seq_is_monotone_across_an_epoch_bump`,
> which asserts that a rollback write at `(epoch+1, 0)` still advances `feed_seq`.
>
> That single test is the whole design in miniature: it fails on any implementation that derives
> the feed cursor from `seq`, which is the one mistake that would silently reintroduce O(N) reloads.

If the answer is (c), the first step instead is: unpack `rvproto2-rc2.tar.gz` into a working tree,
apply `c36m-rc2.patch` then `rc3-state-p0.patch` (in that order, per the patch README), confirm the
159-test baseline, and add a failing test that asserts `VersionedKillSwitch.refresh_once` issues no
`HVALS` when the manifest version is unchanged.

---

### Appendix A — primary sources used

| Source | What was taken from it |
|---|---|
| `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` lines 157–159, 185–240, 290–300, 547, 555, 766–790, 4809–4890 | R2-02/03/04 rows, GW05b/GW05c/GW14d cards, C28, C36, §2.1 rules, the GW05 card |
| `docs/plans/evidence/2026-09-24-runbook-v3-round2/prototype/rvproto2-rc2.tar.gz` | RC2 source: `admit/state_v2.py`, `admit/killswitch.py`, `admit/identity.py`, `admit/admission.py`, `plan/snapshot_v2.py`, `control/writer.py`, `control/rehydrate.py`, `control/db.py`, `runtime/versioned.py`, `runtime/store.py`, `runtime/push.py`, `runtime/config.py`, `runtime/config_v2.py`, `edge/app.py`, `edge/state.py`, `edge/ops.py` |
| `.../evidence/r2-scale/levels/N{1,2,3}.analysis.out` | every tenant-scale number in this document |
| `.../evidence/reviewer-state-propagation/r2-pass1.md` | SP3 (propagation regimes), SP6, SP11, SP13, the flush-window table |
| `.../report/FINDINGS_LEDGER.md` lines 451–452, 489 | H6, E2-01 micro-benchmarks, the r2scale late-harvest results, the `RV_SHM_SLOTS` flag |
| `.../patches/rc3-state-p0-README.md` | GW05b's delivered scope, its explicit P1 deferrals (SP3c, SP11, H6, E2-01), its open risks, the availability trade-off table |
| `gateway_v2/` @ `6b70a2cc` | the current v3 tree: `plan/snapshot.py`, `plan/store.py`, `plan/lookup.py`, `domain/plan.py`, `domain/locks.py`, `admit/*` stubs, `pyproject.toml` layer contracts, `lint/*` gates |
| `docs/plans/evidence/2026-09-29-gw05/verdict.json` | GW05's exit state and its explicit "GW05b and GW05c are the next cards" |

### Appendix B — what was not verified

- No code was executed, built or measured. The round-2 GCP estate was torn down on 2026-09-24, so
  no measurement in this document is new; all figures are quoted from the round-2 evidence tree.
- The 12–15 ms post-fix C4 p99 prediction is a hypothesis derived from subtracting the measured
  tenant-dependent terms. It is not evidence and must not be quoted as such.
- The RC2 tarball was extracted to `/tmp` for reading only; nothing in the repository was modified
  by this investigation.

---

## 25. Implementation log

Approach of record: **option (a) with the manifest amendment** — build the state layer in
`gateway_v2`, with GW05b's manifest carrying `feed_seq` / `count` / `on_count` from the first
commit so the manifest format is written once, not twice.

### Decisions taken during implementation (deviations from §11–§19 as written)

| # | Decision | Why | Reversible? |
|---|---|---|---|
| A1 | A record's body travels as **canonical bytes**, carried in the envelope as an escaped JSON string, instead of being re-serialized from a parsed object at verify time (RC2's `verify_record` recomputed `body_hash(rec["body"])`). | RC2's approach makes every signature depend on the serializer producing byte-identical output forever: switching `orjson.dumps(OPT_SORT_KEYS)` ↔ `json.dumps(sort_keys=True)` changes the bytes and invalidates every signature in the store. Carrying the bytes means verification compares what was actually signed. Pinned by `test_body_bytes_are_not_reserialized_on_verify`. | yes, envelope-local |
| A2 | `is_newer(ExecutionPlan, ...)` is **unchanged** — ordering stays `(epoch, sequence)`; `feed_seq` is **not** a tie-break. | `feed_seq` is the per-kind cursor space shared by every tenant, so a write to another tenant advances it. Using it to order one org's plan against itself would declare an untouched plan "newer". An epoch bump raises `epoch`, so `(epoch, sequence)` is already monotone per org. §11.1's "extend `is_newer`" was wrong; the monotonicity requirement belongs to the record/cursor, not to plan ordering. | n/a |
| A3 | `ExecutionPlan.feed_seq: int = 0`, appended with a default. | Provenance and the second ordering check on delta apply, at zero cost to the 12 existing LGW05 tests — they build plans through `compile_plan`, so no test constructor needed editing. | yes |
| A4 | **No `digest` / `verify_set` anywhere in `gateway_v2`.** They land only in the control plane (phase 4), where the re-hydrator needs them off the serving loop. | §11 planned to add them and then fence them with an import-linter contract in phase 5. Not writing them into the data plane at all is strictly better: there is nothing to fence and nothing to drift back onto the serving loop. | n/a |
| A5 | **Zero new runtime dependencies so far.** Canonicalisation uses stdlib `json` (sorted keys, tight separators), not `orjson`. | Record bodies are tens of bytes; `orjson`'s speed is irrelevant at O(1) reads per round, and it keeps `gateway_v2` at `dependencies = []` for one more phase. | yes |
| A6 | Phase 2 will land the feed **logic** against a `StoreClient` **Protocol** with an in-memory fake, and the redis-py adapter as a separate, thinner step. | Removes the dependency decision from the critical path, matches the existing `DummyPool` / `DetectHooks` seams in `runtime/`, and — importantly — lets the complexity-guard test assert **exactly which store commands were issued**, which a real client or `fakeredis` would obscure. The guard test is the durable backstop for §2.1 rule (1), so it must be able to count commands. | yes |
| A7 | `feed_seq` is **inside the record signature**, and `record_matches_index(record, score)` is a first-class check. | Without it a store could serve an older, genuinely-signed version of a record under a newer index score: every individual check passes because the old record really is signed. This is the one new attack the index introduces, and it is closed at the signature level rather than by convention. Pinned by `test_record_must_match_the_index_entry_that_selected_it` and `test_tampered_feed_seq_is_unavailable`. | n/a |
| A8 | Control-plane home (open question 2, decided provisionally): `gateway_v2/state_control/` — a **separate package in the same workspace**, importing `gateway_v2.domain.state` and `gateway_v2.runtime.state_sig` so there is exactly one signing implementation across both planes, with no reverse import. | Shares one venv, one CI job and one test run; keeps control and data plane in different packages (§2.1 control/data separation); `import-linter`'s `root_packages = ["gateway_v2"]` leaves it unchecked while `gateway_v2` never imports it. A later move to a top-level workspace is a `git mv` plus a `pyproject` entry. | yes |
| A9 | GW14d ordering (open question 5, decided): GW05c ships **aggregate** metrics and deletes the per-org series; the bounded top-K + `other` bucket is left to GW14d. | GW05c can remove O(N) cardinality unilaterally; it cannot own GW14d's directory sizing and overflow accounting. Recorded as a decision rather than left as a gap. | yes |

### Phase status

| Phase | Status | Evidence |
|---|---|---|
| 1 — state / version model | **done** (`790930e1`) | `domain/state.py`, `runtime/state_sig.py`; 29 new tests; **95 passed, 4 skipped** (baseline 66/4); all 5 AST gates OK, import-linter 2 kept / 0 broken, ruff clean, mypy --strict clean on 90 files |
| 2 — change feed (Protocol + logic + fake) | **done** | `runtime/store_keys.py`, `runtime/state_feed.py`; 25 new tests; **120 passed, 4 skipped**; gates as above, mypy --strict clean on 92 files |
| 3a — plan delta, O(tenants) reconcile deleted | **done** (`6d16e68a`) | `plan/document.py`, `plan/delta.py`; `ReplicaSnapshot.reconcile` removed, `absorb(org_id)` added; `PlanStore.offboard`; **144 passed, 4 skipped** |
| 3b/3c — kill switch and identity on the delta | **done** (`b4813b05`) | `admit/killswitch.py`, `admit/identity.py`, `domain/identity.py`; **187 passed, 4 skipped** |
| 3d — cursor ownership, bounded rounds, offload | **done** | `runtime/state_task.py`, `FeedReader.bootstrap_engaged`, three applier adapters; **205 passed, 4 skipped**, mypy --strict on 96 files |
| 4 — control-plane per-record publish | pending | blocked on A8 confirmation |
| 5 — remove full-refresh paths | pending | — |
| 6 — testing | pending | — |
| 7 — load validation (L05c-1) | **blocked** | needs a provisioned lane; the round-2 estate was torn down 2026-09-24 |

### Phase 1 files

```
gateway_v2/gateway_v2/domain/state.py          NEW   StateKind, StateOp, Version, ZERO,
                                                     SignedRecord, Manifest, StoreDataUnavailable
gateway_v2/gateway_v2/runtime/state_sig.py     NEW   canonical_body, body_hash, record_signature,
                                                     manifest_signature, make_record, make_manifest,
                                                     encode/decode_record, encode/decode_manifest,
                                                     record_matches_index
gateway_v2/gateway_v2/domain/plan.py           EDIT  feed_seq field; is_newer rationale documented
gateway_v2/gateway_v2/domain/__init__.py       EDIT  exports
gateway_v2/gateway_v2/runtime/__init__.py      EDIT  exports
gateway_v2/tests/runtime/test_lgw05c_state.py  NEW   29 tests
```

The headline test, `test_feed_seq_is_monotone_across_an_epoch_bump`, was verified non-vacuous: a
cursor derived from `seq` (the mistake it exists to catch) cannot select a rollback written at
`(epoch + 1, 0)`, while the shipped separate counter can. Every tamper test routes through a
`_tamper()` helper that **fails if its target byte string is absent**, so a future envelope change
cannot silently turn the tamper battery into a no-op — the dead-oracle failure mode this repository
has hit before (CHG-0009, CHG-0012, CHG-0029).

### Phase 2 files

```
gateway_v2/gateway_v2/runtime/store_keys.py    NEW   StoreKeys (namespaced, single hash slot),
                                                     HASH_KINDS, ENGAGED_KINDS, KEYS
gateway_v2/gateway_v2/runtime/state_feed.py    NEW   Head, IndexPage, Cursor, START, FeedRound,
                                                     StateStore Protocol, FeedReader.poll
gateway_v2/gateway_v2/runtime/__init__.py      EDIT  exports
gateway_v2/tests/runtime/test_lgw05c_feed.py   NEW   25 tests incl. the cost-shape guard
```

### Round shape delivered

| Round | Trips | Commands | Records read |
|---|---|---|---|
| steady state (nothing changed) | **1** | `GET`, `ZCARD`, `ZREVRANGE` | **0** |
| one record changed | 3 | + `ZCOUNT`, `ZRANGEBYSCORE`, `MGET` | 1 |
| N records changed | 3 | the same six | min(N, budget) |

Asserted equal at 1 000 / 10 000 / 25 000 tenants by
`test_steady_state_cost_is_identical_at_one_thousand_and_twenty_five_thousand`, and the guard test
`test_steady_state_at_ten_thousand_tenants_reads_no_records` pins records-read at 0 and the command
list at exactly three. Negative control run: a reader that ignores its cursor (RC2's behaviour)
reads **10 000** records on the same fixture, so the guard is load-bearing, not decorative.

### A10 — correction to §11.5's publish ordering claim

§11.5 said a bulk publish is safe because the manifest is written last, so "a reader either sees
the old manifest (and skips, because `feed_seq` has not moved) or the new one (and the index is
already complete)". That is not sufficient, and taken with §11.2's strict `ZCARD == count` /
`top_score == feed_seq` checks it would have caused a **fleet-wide fail-closed window on every
bulk onboard**: a reader seeing the old manifest with new index entries already present finds
`index_top > manifest.feed_seq` and `index_count > manifest.count`, and strict equality rejects
both.

The feed reader as built treats the manifest as the only attestation and bounds everything by it:

| Check | Relation | Why this direction |
|---|---|---|
| `index_top >= manifest.feed_seq` | index may be AHEAD, never behind | behind means the manifest attests writes the index cannot name, so the reader would miss them |
| `index_count >= manifest.count` | cheap screen, every round | in-flight extras only make it larger, so it cannot false-positive |
| `ZCOUNT(-inf, manifest.feed_seq) == manifest.count` | **exact**, on any round that reads a delta | counting only at or below the attested position is immune to in-flight extras, which a plain `ZCARD` comparison is not |
| delta read bounded by `manifest.feed_seq` | — | the reader applies exactly the generation the manifest attests and nothing above it |

Pinned by `test_a_bulk_publish_in_flight_does_not_fail_closed` (three index entries ahead of the
manifest; the reader applies only the attested one) and
`test_a_missing_entry_masked_by_an_extra_is_caught_on_a_delta_round` (the case a `ZCARD` check
would pass and the exact count rejects).

**Residual, stated for the evidence package:** on a short-circuit round completeness rests on
`index_count >= manifest.count`, so one missing entry masked by one in-flight extra is not caught
until the next round that reads a delta. The re-hydrator's full digest comparison, off the serving
loop, is the backstop. This is the narrower-completeness-proof trade of §21 row 1, now with its
exact boundary.

### A11 — a partial round does not raise the version floor

`FeedRound.cursor` advances its `feed_seq` to the last record actually applied, but keeps the
previous `version` when the round was truncated by the delta budget. Raising the version floor
before the attested generation is fully applied would let a later regress go undetected. Pinned by
`test_the_budget_truncates_and_asks_to_run_again`.

### Phase 3 files

```
gateway_v2/gateway_v2/plan/document.py            NEW   PlanDocument, encode/decode_plan_body
gateway_v2/gateway_v2/plan/delta.py               NEW   PlanDeltaApplier, plan_from_record,
                                                        plan_applier, ApplyOutcome
gateway_v2/gateway_v2/plan/snapshot.py            EDIT  reconcile() DELETED; absorb(org_id) added
gateway_v2/gateway_v2/plan/store.py               EDIT  offboard(); known() marked O(tenants)
gateway_v2/gateway_v2/admit/killswitch.py         NEW   KillSwitchSnapshot (was a 3-line stub)
gateway_v2/gateway_v2/admit/identity.py           NEW   IdentityCache (was a 3-line stub)
gateway_v2/gateway_v2/domain/identity.py          NEW   Principal
gateway_v2/gateway_v2/runtime/state_feed.py       EDIT  bootstrap_engaged() — O(engaged) cold start
gateway_v2/gateway_v2/runtime/state_task.py       NEW   StateSynchroniser, DeltaBudget, RoundReport
gateway_v2/tests/plan/test_lgw05c_delta.py        NEW   24 tests
gateway_v2/tests/admit/test_lgw05c_killswitch.py  NEW   19 tests
gateway_v2/tests/admit/test_lgw05c_identity.py    NEW   24 tests
gateway_v2/tests/runtime/test_lgw05c_sync.py      NEW   18 tests
gateway_v2/tests/plan/test_lgw05.py               EDIT  7 reconcile call sites -> absorb
```

### What phase 3 actually removed

| RC2 / pre-GW05c | Now |
|---|---|
| `ReplicaSnapshot.reconcile()` iterating `PlanStore.known()` (a sort of every tenant) once a second, holding the lock request-path `lookup()` needs | `absorb(org_id)`, O(1), called by the delta applier for exactly the tenants whose records changed |
| Kill switch rebuilt from the complete record set every 500 ms (203–223 ms of loop block at 25k, `ks_cpu/ks_ms ≈ 0.9`) | engaged set moved by the delta; `attested_engaged` checked in O(1) on every complete round |
| Kill-switch cold start reading every record | `bootstrap_engaged()` reading the published engaged set — **1 record read with 25,000 published**, cursor jumps straight to the attested head |
| Any key write dropping every principal on every worker | per-key eviction from the key delta |
| One store read per cold-cache request (`trips/req` 0.134 → 0.986 in phase B) | single-flight: **50 concurrent callers for one cold key make 1 read** |
| One store read per never-issued key (C26: 5,000 keys → 5,000 reads) | negative cache, invalidated by the key delta |
| Unbounded delta work per round | `DeltaBudget(records=256, offload_above=32)`; above the threshold the apply runs in a GIL-releasing thread |
| A failure in one kind stalling the others | `drain_all` isolates per kind and reports, raising for none (H7) |

### A12 — the immutable-snapshot refactor turned out to be unnecessary

§11.6 and S7 called for making the served snapshot an immutable object swapped by atomic
reference, because `reconcile()` held `ReplicaSnapshot._lock` for the whole O(tenants) loop while
request-path `lookup()` needed the same lock. With the loop deleted, every remaining critical
section is O(1), so the lock is no longer a contention point and the refactor would be churn
without a measurable benefit. Dropped; S7 is closed by the deletion, not by the rewrite.

### A13 — `Applier` returns `object`, not `None`

`plan_applier` has a genuinely useful return value (`ApplyOutcome`, for metrics:
applied / offboarded / skipped / rejected), while the kill-switch and identity appliers return
nothing. Typing the alias as `Callable[[FeedRound], object]` lets an applier report an outcome
without the synchroniser knowing its shape, and avoids the variance workaround a `-> None` alias
forces at every call site.

### A14 — found while testing, not fixed here: `ReplicaSnapshot._pins` never shrinks

`pin(request_id, org_id)` records a plan per request id and nothing ever removes it, so the dict
grows without bound for the life of the process — one entry per request served. It is O(requests),
not O(tenants), so it is outside R2-02, and releasing a pin needs a request-completion hook in
`edge/`, which is GW05/GW06 wiring rather than state propagation. **Flagged for GW06**, deliberately
untouched here to keep this card's diff to propagation.

### Defects in the plan corrected during phase 3

| § | Said | Correct |
|---|---|---|
| §11.1 | "extend `is_newer` to break ties on `feed_seq`" | wrong — `feed_seq` is a per-kind cursor shared by every tenant, so another tenant's write advances it. Plan ordering stays `(epoch, sequence)`. (A2) |
| §11.5 | manifest-last makes a bulk publish safe under strict `ZCARD == count` | would have fail-closed the fleet on every bulk onboard. The index may run ahead; the reader bounds everything by the manifest and proves completeness with `ZCOUNT(-inf, feed_seq)`. (A10) |
| §11.6 / S7 | immutable snapshot swapped by atomic reference | unnecessary once the O(tenants) loop is gone. (A12) |
| §16 phase 3 | "`plan/snapshot.py` — make the served snapshot immutable" | replaced by deleting `reconcile()` and adding `absorb()`. |
| §15 | `test_lgw05_4` kept green unchanged | its 7 `reconcile()` call sites became `absorb("org-a", …)`. Every assertion is unchanged; only the mechanism that feeds the replicas changed, because that mechanism is what the card replaces. |
