# rc3-state-p0.patch: C36 state layer, RC3 P0 (lane r2-fix-c36m)

STATUS: delivered to proto-builder for RC3 integration.

| | |
|---|---|
| sha256 | see `rc3-state-p0.patch.sha256` |
| Base | RC2 (manifest `6093c8dd…`) + `c36m-rc2.patch` (sha256 `6e44be47…`), applied in that order with `patch -p1` |
| Also applies to | proto-builder's RC3 base `523ecb8` (RC2 + C43 coalescing) + `c36m-rc2.patch`. It applies with offsets only (3 hunks in `config_v2.py`). |
| Findings covered | SP1, SP2 (freshness stamp); H7 (bounded Postgres sessions, lock-free publish snapshot, per-kind isolation, fixed-rate rounds); SP6 (single-flight plan reconcile); two re-hydrators. SP3c, SP11, H6 and E2-01 belong to P1. |

## Changes

### (a) Signed freshness stamp

Files: `runtime/versioned.py`, `control/rehydrate.py`, `admit/state_v2.py`, `plan/snapshot_v2.py`.

**Writer side (re-hydrator).**
- A round in which every kind was found equal to Postgres, or restored, writes `{rv2}:stamp`: `{verified_at_ms, versions, by, sig}`.
  - HMAC with `RV_STATE_HMAC_KEY` over the domain `stamp\n` + canonical body.
  - `verified_at` is the round's start. `versions` are the Postgres versions compared.
- Invariant: every write committed before `verified_at` is in the store at a version at least as high as `versions`.
- A round with any error writes no stamp. It logs `state_stamp_withheld` once, and `state_stamp_resumed` on recovery.
- The stamp write is WATCH-guarded and never replaces a newer stamp (two or more re-hydrators).

**Gateway side (one `StampView` per worker, shared by identity, kill switch and plans).**
- Every kill-switch refresh and every plan reconcile reads the stamp in the same MULTI as the manifests.
- Floors: the floor of each manifest is the higher of (what this process applied, what any stamp verified). Floors only rise, so a new process never starts from ZERO.
- Freshness: state is fresh while `|gateway wall clock − verified_at| ≤ RV_STATE_FRESH_MS` (default 3000).
- A new process needs a stamp from a round that started after the process did. After a failover to a lagging replica, a recreated gateway therefore serves nothing until a re-hydrator has compared that store with Postgres and republished what it lost. This is stricter than the spec; it is what closes SP1 for new processes, not just bounds it.
- While the state is not fresh:
  - `ks.state()` is `"stale"`, so every request gets 503 `kill_switch_unavailable` with Retry-After (2 s at defaults).
  - No cached principal is used; `fetch()` raises, so `/v1/models` gets 503 `shared_state_unavailable`.
  - Plans are `PLAN_UNAVAILABLE`.
  - `/readyz` is 503 (`plans_fresh` false, `killswitch` "stale"). `edge/ops.py` is unchanged.
- While the store is healthy but the state is not yet fresh, the kill-switch refresh re-reads the stamp every 100 ms instead of every `RV_KS_REFRESH_MS`.
- Bound: a committed write is enforced, or the gateway fails closed, at most `RV_STATE_FRESH_MS` after its commit. This holds whatever lost the write: a failed publish with no re-hydrator (SP2), a lagging replica (SP1), or a store outage.

### (b) Postgres sessions bounded (H7)

File: `control/db.py`.

- Every PgDB session sets (knobs in ms; 0 = no limit):
  - `lock_timeout` (2000), `statement_timeout` (5000), `idle_in_transaction_session_timeout` (5000), `tcp_user_timeout` (5000). These are passed as `options` and appended to any options already in the DSN.
  - Client-side `tcp_user_timeout` and keepalives (5 s idle, 1 s interval, 3 probes).
- Publishes and re-hydration read a REPEATABLE READ, READ ONLY snapshot: no FOR SHARE and no INSERT. They never wait behind a held FOR UPDATE and publish the last committed state; the writer publishes its own commit.
- A writer queued behind a held version row fails after `lock_timeout`, reported as `error` (nothing committed). `logged()` keeps FOR SHARE, bounded by the session `lock_timeout`.
- `control/rehydrate.py` changes that go with this:
  - One kind's database or store error never stalls the other kinds.
  - Rounds run at a fixed rate: after a stuck round, the next one starts at once.
  - `diagnose` reads the store before Postgres. A write published in between now reads as "stale" rather than "store_ahead", so there is no needless epoch bump. That race was latent in RC2; a unit test fails on RC2.

### (c) Two re-hydrators

- They were already safe (F-C36-RACE). New in this patch:
  - stamps never regress;
  - `RV_REHYDRATOR_ID` (default hostname:pid) is in every stamp and in gateway logs;
  - USAGE text below.
- Measured: with one of two re-hydrators killed, 0 × 503.

### (d) Single-flight plan reconcile (SP6)

File: `plan/snapshot_v2.py`.

- Push and the periodic loop both call `reconcile()`, and reconciles never overlap.
- A trigger already covered by a reconcile that started after it returns without another read (metric `plan_reconcile_coalesced`).
- `reconcile_once` also refuses to swap in an older manifest.

### Files outside my modules (small hunks, flagged for the integrator)

| File | Change |
|---|---|
| `edge/state.py` | 3 lines in `_state_readers`: one `StampView` passed to the identity and plan readers |
| `runtime/config_v2.py` | `RV_STATE_FRESH_MS` knob + `Durable.fresh_ms` + validation: 0 or ≥ 2 × `RV_REHYDRATE_PERIOD_MS` |
| `runtime/store.py` | `K2_STAMP = "{rv2}:stamp"` |
| `tests/functional/cutproxy.py` | new `SIGUSR2` failover (added by c36m-rc2) |

**RV_NAMESPACE integration (r3-ns):**
- add a `stamp` field to `StoreKeys` and use it wherever `K2_STAMP` appears (rehydrate, state_v2, snapshot_v2, f_c36_fresh.py's `STAMP`);
- `PgDB.__init__` / `_conn` and `control/__main__._db()` are touched by both patches.

## Knobs

| Knob | Where | Default | Meaning |
|---|---|---|---|
| `RV_STATE_FRESH_MS` | gateway | 3000 | Refuse state not verified within this window. 0 = not enforced (rc2 behaviour; stamp floors still apply). Must be 0 or ≥ 2 × `RV_REHYDRATE_PERIOD_MS`; set the same period on gateways and re-hydrators. |
| `RV_PG_LOCK_TIMEOUT_MS` | control plane | 2000 | Writer behind a held version row; `logged()` wait |
| `RV_PG_STATEMENT_TIMEOUT_MS` | control plane | 5000 | Any statement |
| `RV_PG_IDLE_TX_TIMEOUT_MS` | control plane | 5000 | Server ends our session when it stalls inside a transaction |
| `RV_PG_TCP_USER_TIMEOUT_MS` | control plane | 5000 | Unacknowledged data (server and client side) |
| `RV_REHYDRATOR_ID` | control plane | hostname:pid | Name in stamps and logs |

## Deployment order (must follow)

1. Upgrade the re-hydrators first. RC3 stamps are ignored by RC2 gateways.
2. Then upgrade the gateways.

An RC3 gateway with an RC2 (stamp-less) re-hydrator is 503 for everything. The escape hatch is `RV_STATE_FRESH_MS=0` on gateways until the re-hydrators are RC3.

## Tests

### Unit

- 12 new tests in `tests/unit/test_c36_freshness.py`:
  - stamp crypto and tampering;
  - `StampView` floors and freshness (bounded both ways, clock skew, `fresh_ms` 0);
  - withheld stamp and per-kind isolation;
  - two re-hydrators;
  - the reviewer's SP1 and SP2 repros, each with its rc2 control inside the test;
  - partial restore below the stamp;
  - SP6 rollback;
  - coalescing;
  - PgDB session options and lock-free snapshot;
  - store-first diagnose;
  - config validation.
- Full suite:
  - RC2 + c36m-rc2 + this patch: 159 passed, lint-imports 2 kept.
  - 523ecb8 + c36m-rc2 + this patch: 162 passed, 2 kept.
  - The C36 tests also pass under prometheus_client 0.25 (37 passed).
- The SP6 and store-first tests fail on RC2 + c36m-rc2 (the SP6 run ends at version 1.4 instead of 1.5): `evidence/r2-fix-c36m/rc3/unit/rc2_negative_subset.txt`.

### Functional

Real processes and containers:
- Postgres 16.15;
- Valkey 8.0 primary + replica, reached through `cutproxy.py` (`SIGUSR2` = failover);
- real `python -m rvproto.control` CLI and re-hydrators;
- real `python -m rvproto.serve --worker` gateways; the guard and provider are closed ports, and admission outcomes are classified by code.

Run with `evidence/r2-fix-c36m/rc3/run_fresh.sh <tree> <label> <scenario>`. Results are in `evidence/r2-fix-c36m/rc3/functional/RESULTS.txt`, with per-run `summary.json`, `probes.jsonl` and process logs.

The first four scenarios check correctness; `absent`, `pgdown` and `storeblip` measure the trade-off. Of the correctness scenarios, all fail on RC2 and pass on the patch (on both RC3 trees):

- **sp1** (reviewer repro_regressed_store_new_process; the re-hydrator is stopped over the failover)
  - RC2: the new process admitted R 146× and served K 146× over 8 s.
  - Patch: new process 0/0 (all 503); converged 0.9–1.0 s after a re-hydrator is back.
- **sp1-live** (the re-hydrator runs through the failover)
  - RC2: R admitted 10×, K served 19×.
  - Patch: 0/0; 8–11 × 503, then R 401 and K engaged.
- **sp2** (reviewer repro_publish_pending_no_rehydrator)
  - RC2: stale state served for the whole 10 s; never fails closed.
  - Patch: fail-closed 3.000–3.002 s after the last stamp; stale served ≤ 2.35 s after the writes; 0 served afterwards; Retry-After 2; enforced 0.86–1.08 s after a re-hydrator returns.
- **lock** (an operator session holds rv2_version(ks) FOR UPDATE, then FLUSHALL, lock held 12 s)
  - RC2: ks and budget are never restored while locked, G is refused 127× (`kill_switch_unavailable` from +5 s), and the queued writer waits 12.1 s.
  - Patch: all 4 kinds restored in 0.54–0.79 s, G never refused, the queued writer fails `LockNotAvailable` in 2.25 s (status error, exit 1). Session bounds verified with SHOW: 2s / 5s / 5s / 5000.

## Availability trade-off (asked for)

Measured in the functional runs:

| Event | RC2 | RC3 |
|---|---|---|
| Both re-hydrators gone (store and Postgres healthy) | never fails closed | global 503 in 2.04–2.97 s after the last kill (5 + 5 runs), i.e. 2.99–3.03 s after the last stamp; both gateways flip within about 30 ms. Recovery 0.27–0.43 s after a re-hydrator restarts. |
| One of two re-hydrators gone | – | 0 × 503 |
| Postgres frozen 10 s (TCP up, queries hang; about a Cloud SQL failover) | 0 × 503 | 503 from 2.8 s into the freeze until 0.15–0.23 s after thaw (≈ 7.4 s of global 503) |
| Store frozen 3.8 s (the round-2 Valkey maintenance blip) | 0 × 503 | ≈ 1.2 s of global 503 (from 2.8 s into the freeze to 0.18 s after) |
| New process start | ready at first refresh | + up to one re-hydrator round (≤ ~1 s) before /readyz 200 |

Worst-case time to fail-closed:
- **Formula:** `RV_STATE_FRESH_MS − (RV_REHYDRATE_PERIOD_MS + round duration + the 100–500 ms stamp observation)`.
- **At defaults:** about 1.5 s after the last good round; 3 s at best.

Consequences for operations:
- The re-hydrator is now on the data plane's availability path. Run two, in different zones.
- Exempt their VMs from the B24 idle-VM watchdog. On 09-24 it stopped the control-plane VM that hosts the single re-hydrator (r2state cp-1); under RC3 that would be a global outage within 3 s.
- Any lane that runs one re-hydrator and restarts it will see a global 503 of up to about 1–2 s per restart.

Options if the controller wants more ride-through:
- raise `RV_STATE_FRESH_MS` (it bounds the SP1/SP2 exposure 1:1);
- shorten `RV_REHYDRATE_PERIOD_MS` (P1 makes rounds O(changes), so this becomes cheap);
- in v3, add writer intent markers so that a Postgres outage alone need not stop stamps.

## Open risks / not done

- **Clock.** Freshness uses the gateway wall clock against the re-hydrator's `verified_at`, so it relies on NTP (GCP metadata NTP). A skew beyond `RV_STATE_FRESH_MS` fails closed (visible in `state_unverified`).
- **Lagging replica, old processes.** An old process that never applied a lost write can still serve the pre-write state from a lagging replica. This is bounded by `RV_STATE_FRESH_MS` and by the next re-hydrator round. New processes are fully covered.
- **Postgres hang case.** `tcp_user_timeout` does not cover a server that ACKs but never answers (the freeze case). `statement_timeout` is server-side, so a hung round lasts until thaw. The stamp it then writes is already stale, so the gateways stay closed until the next round (fixed-rate, so immediate).
- **Stamp age.** A round slower than `RV_STATE_FRESH_MS − period` makes stamps that are born old. At 50k records per kind, P1's O(changes) rounds are needed.
- **No pub/sub nudge.** The kill switch is still poll-only, and stamps have no push. Observation lag is ≤ 100 ms while unverified and ≤ `RV_KS_REFRESH_MS` otherwise.
- **SP3c/SP11** (the kill-switch refresh needs the key manifest; any key write clears every principal cache) are P1 (per-key invalidation).

## USAGE.md snippet (for proto-builder)

```markdown
### C36 freshness stamp and re-hydrators (RC3)
- Gateways refuse shared state that no re-hydrator has verified against Postgres within `RV_STATE_FRESH_MS`
  (default 3000; 0 = off). Refused requests get 503 `kill_switch_unavailable` (`/v1/models`: `shared_state_unavailable`)
  with Retry-After, and `/readyz` is 503. A new gateway process is not ready until a re-hydrator round that started
  after the process has stamped the store (normally < 1 s).
- Run TWO re-hydrators, one per zone, on the same Postgres + store. Both run
  `python -m rvproto.control rehydrate` with the gateways' `RV_STATE_HMAC_KEY` and `RV_REHYDRATE_PERIOD_MS`; set
  `RV_REHYDRATOR_ID` to tell them apart. No leader election: publishes are WATCH-guarded and stamps never regress.
  - With one of two down, nothing changes.
  - With both down, every gateway returns 503 within `RV_STATE_FRESH_MS` of the last stamp (measured 2.0–3.0 s after
    the kill); it recovers ~0.3 s after either comes back.
  - Exempt the re-hydrator VMs from idle-VM stops.
- Upgrade order: re-hydrators first, then gateways (an RC3 gateway without RC3 re-hydrators serves nothing), or set
  `RV_STATE_FRESH_MS=0` on gateways until the re-hydrators are upgraded.
- Postgres session bounds (control CLI and re-hydrator; ms, 0 = none): `RV_PG_LOCK_TIMEOUT_MS` 2000,
  `RV_PG_STATEMENT_TIMEOUT_MS` 5000, `RV_PG_IDLE_TX_TIMEOUT_MS` 5000, `RV_PG_TCP_USER_TIMEOUT_MS` 5000.
  - An operator session holding `rv2_version` rows no longer blocks re-hydration.
  - A writer queued behind it fails after the lock timeout, with `status: error` and exit 1.
- Metrics (worker):
  - `state_stamp_verified_at_seconds` (age = now − value);
  - `state_stamp_fresh` (0/1);
  - `state_stamp_missing` / `state_stamp_invalid`;
  - `state_unverified_transitions`;
  - `plan_reconcile_coalesced`.
- Logs:
  - worker: `state_verified` / `state_unverified` (age, by, reason);
  - re-hydrator: `state_stamp_first`, `state_stamp_withheld`, `state_stamp_resumed`.
- Negative controls: `RV_STATE_FRESH_MS=0` (no freshness); `RV_PG_*_MS=0` (unbounded sessions).
- Functional check: `tests/functional/f_c36_fresh.py sp1|sp1-live|sp2|lock|absent|pgdown|storeblip` (see its docstring).
```
