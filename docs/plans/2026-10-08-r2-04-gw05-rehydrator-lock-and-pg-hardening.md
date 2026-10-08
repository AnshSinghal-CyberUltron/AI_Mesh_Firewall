# R2-04 / GW05 — Rehydrator lock stall and control-plane Postgres hardening

**Status:** investigation complete, implementation plan. **No code changed.**
**Card:** GW05b (the runbook folds R2-03 and R2-04 into one card). **Severity:** HIGH.
**Date:** 2026-10-08.

**Relationship to existing documents.** This plan *corrects and extends*
`docs/plans/2026-10-08-gw05b-handoff.md` §4, which reports all four Postgres timeouts as "done,
GW05c `state_control/pg.py`". That is true of the source and false of any deployment: the
mechanism `pg.py` uses is discarded by PgBouncer, and one of the four is set on the wrong side of
the socket. Two further defects — a fault-classification bug that converts a lock timeout into a
16 s degraded ride-through, and an unguarded whole-kind republish — are not recorded anywhere.
Everything the handoff settles (posture rendering, the knob loader's shape, the alarm severity
convention, the lane economics) is cross-referenced here rather than restated.

Prior reading, in order of usefulness: `docs/plans/2026-10-08-gw05b-handoff.md`,
`docs/plans/2026-10-07-gw05b-state-freshness-and-ha-rehydration.md`,
`docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` §0.3 row R2-04 and §0.4 card GW05b.

---

## 1. Executive summary

R2-04 is **not** an unimplemented card. The two headline mechanisms the runbook requires are
present and correct in `gateway_v2/state_control/pg.py`: the four session timeouts are
constructed, and the publish snapshot is a genuinely lock-free `REPEATABLE READ, READ ONLY` read
that takes no row lock. A local drill already holds `FOR UPDATE` on the `plan` counter row across
a store flush and asserts every kind is restored *and* stamped while the lock is still held.

What is wrong is narrower and more serious than "not built":

1. **The timeouts do not reach Postgres in any deployed configuration.** `pg.py` passes them as
   the libpq `options` startup parameter. Both compose files set
   `IGNORE_STARTUP_PARAMETERS: extra_float_digits,options` on PgBouncer, and the standing rule in
   three separate files is that applications reach Postgres *only* via `pgbouncer:6432`. The
   parameter is dropped silently. Nothing reads the settings back, so the drop is invisible.
2. **`tcp_user_timeout` is set on the wrong side of the socket.** `-c tcp_user_timeout=…` is a
   server GUC governing the server's socket. The rehydrator's own socket — the one that hangs
   when Postgres stops answering — is unbounded.
3. **A lock timeout is misclassified as "Postgres is unavailable".** `_db_faults()` catches
   `psycopg.Error` wholesale, so `LockNotAvailable` and `QueryCanceled` set `db_down=True` and
   become eligible for the 16 s `PG_GRACE_MS` degraded ride-through. A held lock would be
   *absorbed* and re-asserted as freshness for up to 16 s instead of surfacing as a bounded
   failure. This is H7 re-emerging in a new form, and `rehydrate.py`'s own docstring asserts the
   behaviour it does not implement.
4. **The whole-kind republish is unguarded.** `ValkeyPublisher.publish_kind` calls `_guarded(…,
   guard=False)`, so with two rehydrators a slower one's repair can move a kind's manifest
   *backwards*. Readers refuse a regress by design, so the kind goes unreadable until the next
   round.
5. **No rehydrator process exists.** `Rehydrator` and `PostgresControlDB` are constructed only in
   tests. There is no entrypoint, no loop, no DSN wiring, no env reader. `gateway_v2/Dockerfile`
   copies only `gateway_v2/gateway_v2`, so `state_control/` is not even in the image. The
   requirements "≥ 2 rehydrators in ≥ 2 zones" and "never let an idle-stop policy touch them"
   therefore have nothing to attach to, and `StateRehydratorSingleInstance` alarms on a Prometheus
   job that does not exist.
6. **The `ok_publish_pending` alarm the runbook names explicitly has no producer.**
   `StateWriter` returns `OK_PUBLISH_PENDING` without recording when, so
   `amf_state_publish_pending_oldest_seconds` cannot be computed.

Items 1–4 are code defects fixable in `state_control` with bounded, well-gated changes. Items 5–6
are the deployment and observability halves the handoff already classified as deployment work;
this plan makes them executable rather than aspirational.

**Decisions taken** (confirmed with the requester): two *named* rehydrator services rather than
replicas, with a declared multi-AZ spec; `SET LOCAL` as the authoritative timeout mechanism rather
than bypassing PgBouncer; and GW05b treated as one card, so no artificial R2-03/R2-04 boundary.

---

## 2. Current rehydration architecture

The flow, with every hop named. Paths are relative to the repository root; `state_control` and
`gateway_v2` are sibling packages inside the `gateway_v2/` workspace.

```
Postgres (amf_state_counter / amf_state_record / amf_state_log)
        │   state_control/schema.py : SCHEMA
        ▼
PostgresControlDB                       state_control/pg.py:178
  ├─ tx()        read-write, READ COMMITTED, autocommit=False      pg.py:229
  ├─ counters()  one short-lived read-write tx, lock=False          pg.py:247
  └─ snapshot()  READ ONLY + REPEATABLE READ, no locks              pg.py:251
        ▼
Rehydrator                              state_control/rehydrate.py:136
  ├─ _diagnose(kind)   store head first, THEN Postgres counters     rehydrate.py:195
  ├─ verify(kind)      deep: full index vs snapshot (O(records))    rehydrate.py:242
  ├─ repair(kind)      snapshot → manifest → publish_kind           rehydrate.py:273
  ├─ round_once(deep)  per-kind isolation, then _stamp              rehydrate.py:300
  └─ _stamp / _claimable / _ride_through / _store_holds             rehydrate.py:~360-470
        ▼
StatePublisher                          state_control/publisher.py:59 (Protocol)
  └─ ValkeyPublisher                    state_control/valkey.py
       ├─ publish_kind(...)  WATCH pipeline, guard=False            valkey.py:197
       ├─ put_stamp(stamp)   WATCH-guarded, never regresses         valkey.py:109
       └─ _guarded(...)      manifest written LAST                  valkey.py:~240
        ▼
Valkey / Redis  ({rv2}:meta:<kind>, index ZSET, engaged SET, {rv2}:stamp)
        ▼
Gateway workers
  StateSynchroniser.cycle / drain_all   gateway_v2/runtime/state_task.py:213
  StampView (freshness floor)           gateway_v2/runtime/state_stamp.py
  posture codes → 503 + Retry-After     gateway_v2/domain/posture.py
```

Per-step detail for the steps that matter to R2-04:

| Step | Source | Transaction | Isolation | Locks | Error handling |
|---|---|---|---|---|---|
| per-round head compare | `Rehydrator._diagnose` → `_counters` → `PostgresControlDB.counters` | `tx()`, **read-write**, committed | READ COMMITTED | none (`lock=False`) — but issues an `INSERT … ON CONFLICT DO NOTHING` first | wrapped to `ControlPlaneUnavailable` |
| deep verify | `Rehydrator.verify` → `_snapshot` → `PostgresControlDB.snapshot` | read-only, rolled back | REPEATABLE READ | **none** | wrapped to `ControlPlaneUnavailable` |
| repair / publish | `Rehydrator.repair` → `_snapshot` then `publish_kind` | read-only, rolled back | REPEATABLE READ | **none** | per-kind `try` in `round_once`; never aborts siblings |
| epoch repair | `StateWriter.repair_epoch` | `tx()`, read-write | READ COMMITTED | **`FOR UPDATE`** | propagates |
| writer commit | `StateWriter._commit` / `_commit_many` | `tx()`, read-write | READ COMMITTED | **`FOR UPDATE`** | `CommitUnknown` on commit failure |

`round_once` isolates each kind: one kind's failure appends to `errors` and the loop continues.
That is the direct fix for H7's "one kind's stall stops the round" and it is already in place. The
stamp is then withheld unless *every* kind was verified (`_claimable`), so a partial round makes
freshness lapse rather than claiming a half-truth.

**What happens today when a database query blocks.** `snapshot()` cannot block on a row lock — it
takes none. `counters()` can in principle block, because its `INSERT … ON CONFLICT DO NOTHING`
performs a uniqueness check against the conflicting row (see §4). If it blocks, it blocks for
`statement_timeout` if that setting arrived, and indefinitely if it did not. The resulting
exception is caught by `_counters`, re-raised as `ControlPlaneUnavailable`, recorded per kind, and
— via `db_down=True` — made eligible for the degraded ride-through.

---

## 3. Root cause of R2-04

### 3.1 The original RC2 failure (H7, 38 s)

Recorded in the runbook §0.2 as: *one idle `FOR UPDATE` held re-hydration for 38 s (global 503
from +4.9 to +39.5 s); two re-hydrators both waited.*

RC2's rehydrator read the version row `FOR SHARE` before publishing. `FOR SHARE` conflicts with
`FOR UPDATE`, so the read queued behind an idle writer transaction. With no `lock_timeout` the
wait was unbounded; with two rehydrators, both queued on the same row, so redundancy bought
nothing — the second instance was a second victim, not a backup. No rehydration meant no
republish and no stamp, so every gateway aged past its freshness bound and the fleet failed
closed globally.

`pg.py`'s module docstring states this precisely and is the best in-tree account of it.

### 3.2 Why the original path cannot recur

The `FOR SHARE` read is gone. The only lock request in the tree is
`" FOR UPDATE" if lock else ""` at `pg.py:95`, and `lock=True` is passed from exactly three
writer call sites — `StateWriter._commit`, `StateWriter._commit_many`,
`StateWriter.repair_epoch`. The rehydrator never passes it. `snapshot()` is `READ ONLY` +
`REPEATABLE READ` and reads the counter row with a bare `SELECT`.

Confirmed by **two** drills in `tests/state_control/test_lgw05c_drills.py`:

- `:453 test_drill_rehydration_under_a_held_row_lock` (GW05c) — holds the lock via
  `drill.db.tx()` → `holding.counters(StateKind.PLAN, lock=True)` and asserts the 30-record repair
  completes in under 5 s.
- `:819 test_drill_a_flush_under_a_held_row_lock_still_restores_and_stamps` (GW05b) — holds it via
  a raw `psycopg.connect(DSN)`, flushes the store, and asserts all four kinds are repaired **and**
  the stamp lands while the lock is still held, in under 5 s.

**A test-design subtlety worth fixing in R2-04-1.** The first drill's holder is created through
`PostgresControlDB.tx()`, so once C1 makes `idle_in_transaction_session_timeout = 5000 ms`
effective, the *holder itself* becomes subject to it — and the assertion is `took_s < 5.0`. The
test would then pass whether the repair completed under the lock or merely outlived the holder's
termination, which is precisely the distinction it exists to prove. The second drill avoids this
by using a connection that carries none of our options. R2-04-1 must hold the lock from an
unbounded session and assert a bound well below the idle timeout.

### 3.3 The four ways it can still recur

Ranked by how closely each reproduces the original symptom.

**RC-1 — lock timeout laundered into a 16 s degraded ride-through.** `rehydrate.py:81-96`:

```python
def _db_faults() -> tuple[type[BaseException], ...]:
    faults: list[type[BaseException]] = [OSError]
    try:
        import psycopg
    except ImportError:
        return tuple(faults)
    faults.append(psycopg.Error)      # ← the whole base class
    return tuple(faults)
```

`psycopg.Error` is the ancestor of every database error, including `LockNotAvailable`
(lock_timeout), `QueryCanceled` (statement_timeout), `UndefinedTable`, and every programming
error. The docstring immediately above `ControlPlaneUnavailable` claims the opposite:

> Raised only from the re-hydrator's own database call sites AND only for I/O-shaped errors, so a
> programming error in the database layer still withholds the stamp rather than being ridden out
> for the whole grace window.

That intent is not implemented. Consequence chain: a lock timeout on `counters()` →
`ControlPlaneUnavailable` → `db_down=True` in `round_once` → `_claimable` sees no verified kinds
and `db_down` → `_ride_through` re-asserts the last verified cursors → a **degraded** stamp is
minted. The fleet is told state is fresh when nothing was compared, for up to `PG_GRACE_MS` =
16 s. The one dimension in which this is better than RC2 is that it is bounded; the dimension in
which it is worse is that it is silent and the fleet keeps serving.

**RC-2 — the timeouts are not in effect.** See §5. If `options` is dropped by PgBouncer there is
no `lock_timeout`, so RC-1's bounded-but-wrong behaviour degrades to RC2's unbounded wait on any
path that does take a lock, and `statement_timeout` cannot rescue a blocked `counters()` call.

**RC-3 — the per-round path is read-write and performs a write.** `PostgresTx.counters`
(`pg.py:87-105`) begins with:

```sql
INSERT INTO amf_state_counter (kind, epoch, seq, feed_seq, count, on_count)
VALUES (%s, %s, %s, 0, 0, 0) ON CONFLICT DO NOTHING
```

This runs on every round, for every kind, on a read-write `READ COMMITTED` connection. Postgres
resolves `ON CONFLICT DO NOTHING` by probing the arbiter index; when the conflicting tuple's
`xmax` is lock-only (which is what `FOR UPDATE` leaves behind) the probe does not wait, so in the
common case this does not block. It is nonetheless the wrong shape for the availability-critical
loop: it is a write on a path that only needs to read, its no-wait behaviour depends on a subtle
visibility rule rather than on anything the code asserts, and it holds a read-write transaction
open against the primary four times a second per rehydrator. The runbook requires lock-freedom of
"the publish snapshot", which this is not, so fixing it is hardening rather than compliance — but
it is the cheapest remaining way for a lock to touch the loop at all.

**RC-4 — unguarded whole-kind republish under two rehydrators.** `valkey.py:197-222`:

```python
LOG.warning("republishing kind=%s records=%d", kind.value, len(records))
return self._guarded(kind, manifest, stage, guard=False)
```

`guard=False` disables the `_is_ahead` check that otherwise refuses to move a kind's manifest
backwards. It is *necessary* for the `STORE_AHEAD` repair: that path exists precisely because the
store holds a `feed_seq` Postgres never issued, so a guarded publish of the (lower) Postgres
position would be refused and the divergence would never heal. But it is applied to **every**
repair reason, and with two rehydrators that is a regress window:

```
store flushed; A and B both diagnose MISSING
A: snapshot(plan) → feed_seq 100
                              writer commits → 101, publishes 101
B: snapshot(plan) → feed_seq 101 → publish_kind(101)      manifest = 101
A: publish_kind(100)  guard=False                          manifest = 100   ← regress
readers refuse the regress (C36); plan is unreadable until the next round
```

Bounded — the next round diagnoses `STALE` and repairs — but it is a self-inflicted gap on the
exact code path the card's HA requirement introduces, and it is not covered by any test.

### 3.4 The implementation-specific failure path

```
Writer txn (StateWriter._commit, pg.py:95, lock=True)
      │ SELECT … FOR UPDATE on amf_state_counter WHERE kind='plan'
      │ then goes idle (client stall, debugger, psql, paused container)
      ▼
Counter row locked
      │
      ├─ Rehydrator.snapshot()  ──► NOT BLOCKED (READ ONLY, REPEATABLE READ, no lock)
      │                              repair proceeds; this is the fixed half
      │
      └─ Rehydrator._counters() ──► INSERT…ON CONFLICT probe + SELECT
                                     │ normally no wait (lock-only xmax)
                                     │ if it ever does wait:
                                     ▼
                              no lock_timeout (options dropped by PgBouncer)
                                     ▼
                              unbounded wait, or psycopg.Error if bounded
                                     ▼
                              _DB_FAULTS catches psycopg.Error
                                     ▼
                              ControlPlaneUnavailable → db_down=True
                                     ▼
                              _ride_through re-asserts last verified cursors
                                     ▼
                              DEGRADED stamp minted: fleet believes state is fresh
                                     ▼
                              16 s (PG_GRACE_MS) of serving on an unverified claim,
                              then global fail-closed
```

The honest summary: the *stall* is fixed, the *reporting* is not. R2-04's remaining code risk is
that a lock problem is indistinguishable from a Postgres outage and is therefore handled by the
mechanism built for outages.

---

## 4. Current database and transaction behaviour

**Client.** `psycopg` 3 (`psycopg[binary]>=3.2`, `gateway_v2/pyproject.toml`), imported lazily
inside `PostgresControlDB.__init__` so the in-memory twin needs no driver.

**Pooling.** None in `state_control`. One short-lived connection per operation, opened in
`_connect` and closed in the `finally` of `tx()` / `snapshot()`. No `psycopg_pool`, no min/max
size, no connection lifetime, no acquisition timeout. `connect_timeout_s` (default 5) is the only
acquisition-shaped bound. This is a deliberate design choice and it is the right one here: a
pooled connection that is handed over mid-transaction is exactly how an idle transaction holds a
lock on someone else's behalf, and the no-pool design makes that structurally impossible.

PgBouncer, however, *is* a pool, and it sits in front of Postgres for every other application in
the stack (§5.2). Whether `state_control` traverses it is currently undefined because nothing
wires its DSN.

**Transaction boundaries.**

- `tx()` — `autocommit=False`; yields a `PostgresTx`; commits on clean exit; a commit failure is
  re-raised as `CommitUnknown` (the write may be durable, and is reported as such rather than as
  a failure); any body exception rolls back and re-raises; the connection is always closed.
- `snapshot()` — `read_only=True`, `REPEATABLE_READ`; always `rollback()` then `close()` in
  `finally`, never commits. Correct for a read-only snapshot.
- `init_schema()` — one `tx()` executing `SCHEMA`.

**Isolation.** `READ COMMITTED` (server default) for `tx()`; `REPEATABLE READ` for `snapshot()`.
Set via `connection.isolation_level` *before* the first statement, which psycopg applies on
transaction begin, so it is effective rather than advisory.

**Row locking.** One site: `pg.py:95`. `lock=True` from `StateWriter._commit`,
`StateWriter._commit_many`, `StateWriter.repair_epoch` only. No `FOR SHARE`, no
`FOR NO KEY UPDATE`, no `LOCK TABLE`, no advisory locks anywhere in the tree. **No Django
`select_for_update` anywhere in `control/`** — verified by search — so the Django control plane
cannot hold the R2-04 lock.

**How long can the lock be held?** For an application writer, as long as the transaction runs.
`_commit` does bounded work (one counter read, one record read, one engaged read, two inserts, one
update) with no network calls inside the transaction, so a healthy hold is milliseconds.
`_commit_many` holds it for the whole batch — at the 25,000-record onboarding size named in
`writer.py`'s docstring, that is the one legitimately long hold in the system and the reason
`statement_timeout` must not be set aggressively on writer connections.

**Can an idle transaction retain it?** Yes. A client that commits nothing and sends nothing keeps
the lock until `idle_in_transaction_session_timeout` terminates the session — if that setting
reached the server. A session not created by `PostgresControlDB` (psql, a migration, an admin
tool, a paused container) is not covered by our settings at all, which is why §15 scenario B is
limited and why a server-side `ALTER ROLE` is recommended in §11.4.

**Can two rehydrators block each other?** Not on Postgres: neither takes a lock, and
`REPEATABLE READ` readers never block readers or writers. On the store, yes but safely — both
contend on `WATCH` over the manifest key, retried up to `WATCH_ATTEMPTS = 20`, which is
contention, not blocking. The real two-rehydrator hazard is RC-4, which is a correctness problem
rather than a liveness one.

---

## 5. Current timeout configuration

### 5.1 What the code constructs

`PostgresControlDB.__init__` (`pg.py:181-209`):

```python
lock_timeout_ms: int = 2_000,
statement_timeout_ms: int = 5_000,
idle_tx_timeout_ms: int = 5_000,
tcp_user_timeout_ms: int = 5_000,
connect_timeout_s: int = 5,
…
bounds = " ".join((
    f"-c lock_timeout={lock_timeout_ms}ms",
    f"-c statement_timeout={statement_timeout_ms}ms",
    f"-c idle_in_transaction_session_timeout={idle_tx_timeout_ms}ms",
    f"-c tcp_user_timeout={tcp_user_timeout_ms}",
))
existing = conninfo_to_dict(dsn).get("options") or ""
self._options = f"{existing} {bounds}".strip() if existing else bounds
```

Three things are right and should be preserved:

- All four are set at the single point where a connection is opened, so no connection can be
  created without them. The docstring's reasoning — "a bound that only some connections carry is
  not a bound" — is correct and is the right instinct.
- `options` is **appended** to any DSN-supplied `options` rather than replacing it. This was a
  real bug once: `docs/plans/2026-10-07-gw05c-incremental-state-propagation.md:1708` records that
  replacing it silently dropped a caller's `search_path` and broke per-test schema isolation. The
  drill fixture depends on it (`?options=-csearch_path%3D<schema>`).
- The unit suffixes are right: `ms` on the three GUCs that need it, bare integer on
  `tcp_user_timeout` whose unit is already milliseconds.

**Values today:** `lock_timeout` 2000 ms, `statement_timeout` 5000 ms,
`idle_in_transaction_session_timeout` 5000 ms, `tcp_user_timeout` 5000, `connect_timeout` 5 s.
No environment variable reaches any of them; they are constructor defaults and every production
call site that would override them does not exist. The only overriding caller is the drill's
`tightly_bounded_db()` (`test_lgw05c_drills.py:~140`), whose docstring is the most useful comment
in the tree on the subject:

> Measured here: 5.0 s per call at the defaults, 2.0 s at these. The bounds are what a 1 s
> re-hydrator period would be tuned to anyway — the shipped defaults suit the writer, not this
> loop.

That is an in-repo argument for role-specific bounds (§11.2), written by whoever shipped the
defaults.

### 5.2 Why they do not reach Postgres

**PgBouncer discards the `options` startup parameter.** `docker-compose.yml:37-55`:

```yaml
pgbouncer:
  image: edoburu/pgbouncer:v1.23.1-p1
  environment:
    POOL_MODE: transaction
    IGNORE_STARTUP_PARAMETERS: extra_float_digits,options     # ← line 52
```

`deploy/pgbouncer/pgbouncer.ini` matches (`pool_mode = transaction`,
`ignore_startup_parameters = extra_float_digits,options`, `server_reset_query = DISCARD ALL`) and
states the routing rule:

> Apps and migrations connect to pgbouncer:6432 only (never dial postgres:5432 from application
> containers).

reinforced at `docker-compose.yml:36`, `docker-compose.prod.yml:14`, and on every service's
`DATABASE_URL` (all `@pgbouncer:6432`). So a `state_control` DSN that follows the house rule has
all four settings discarded before they reach a backend, and under `pool_mode = transaction` a
session-level `SET` would not survive either — `DISCARD ALL` resets the server connection between
transactions.

**This is already known in this repository, for a different alias.**
`control/ai_mesh_control/main_app/analytics_db.py:1-21`:

> PgBouncer transaction pooling ignores startup `options` (`IGNORE_STARTUP_PARAMETERS` includes
> `options`). A connection-level `statement_timeout` set via Django OPTIONS therefore never
> reaches Postgres. Timeouts MUST be applied with SET LOCAL inside a transaction on this alias.

and the resolution, `analytics_db.py:16-21`:

```python
def analytics_set_local_timeout_sql() -> list[str]:
    return [
        f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}",
        f"SET LOCAL idle_in_transaction_session_timeout = {IDLE_IN_TX_MS}",
    ]
```

with `main_app/settings.py:245-246` labelling the startup `OPTIONS` as belt-and-braces and the
middleware as authoritative, and `policy/analytics_timeout_probe.py` providing a staff-only
`SELECT pg_sleep(6)` canary that returns 504 if and only if the deadline actually applied.

`state_control` has none of this: no `SET LOCAL`, no read-back, no canary. The settings are
constructed and then, in the only deployment topology the repository endorses, thrown away.

**`tcp_user_timeout` is additionally on the wrong side.** `-c tcp_user_timeout=5000` is a server
GUC: it bounds unacknowledged data on the *server's* socket. The failure it is meant to cover —
the rehydrator waiting forever for a reply from a host that has gone away — is on the *client's*
socket, and needs the libpq connection parameter of the same name, which PgBouncer does not
intercept because it governs the client↔PgBouncer TCP connection. There is a second subtlety:
`tcp_user_timeout` only acts on data awaiting acknowledgement, so a connection sitting idle with
nothing in flight has nothing to time out. Keepalives are what generate probes for that case, and
none of `keepalives`, `keepalives_idle`, `keepalives_interval`, `keepalives_count` is set
anywhere.

### 5.3 Current state, as a table

| Setting | Constructed | Reaches PG direct | Reaches PG via PgBouncer | Survives pooled reuse | Env-overridable | On rehydrator connections |
|---|---|---|---|---|---|---|
| `lock_timeout` | yes, 2000 ms | yes | **no** (`options` ignored) | n/a (no pool in `state_control`) | **no** | yes if it arrives |
| `statement_timeout` | yes, 5000 ms | yes | **no** | n/a | **no** | yes if it arrives |
| `idle_in_transaction_session_timeout` | yes, 5000 ms | yes | **no** | n/a | **no** | yes if it arrives |
| `tcp_user_timeout` | yes, 5000 | server side only | **no** | n/a | **no** | **wrong side** |
| `connect_timeout` | yes, 5 s | yes (libpq, client side) | yes | n/a | **no** | yes |
| TCP keepalives | **no** | — | — | — | — | **no** |

Django control plane, for contrast: `statement_timeout` 5000 ms and
`idle_in_transaction_session_timeout` 30000 ms on the **`analytics` alias only**, authoritative via
`SET LOCAL`; the **`default` alias has none of the four**, and no `lock_timeout` is set anywhere in
the repository outside `pg.py`.

---

## 6. Current rehydrator deployment

There is none. Precisely:

| Question | Answer | Evidence |
|---|---|---|
| How many rehydrators run? | **Zero** | no construction site outside tests |
| Separate processes / containers / VMs? | n/a | no service definition in any compose file |
| Availability zones? | n/a | — |
| How started / scheduled / supervised? | **not at all** — no entrypoint, no loop, no CLI verb | `gateway_v2/runtime/lifecycle.py:run_cli` handles only `--serve`; `gateway_v2/runtime/__main__.py` delegates to it |
| Where does the DSN come from? | nowhere | no env read for a state DSN; `AMF_PG_DSN` is test-only |
| Are the periods configurable? | **no** | `StateKnobs` is a pure value type; `AMF_REHYDRATE_PERIOD_MS` etc. appear only in plan docs |
| Shared connection pool? | n/a (no pool by design) | `pg.py` |
| Can both rehydrate / publish simultaneously? | yes, by design | `valkey.py:109` `put_stamp`, `_guarded` |
| Leader election? | **no**, and not required | §12 |
| Is it in the image? | **no** | `gateway_v2/Dockerfile:16-18` bind-mounts `gateway_v2/pyproject.toml`, `gateway_v2/gateway_v2`, `shared/ai_mesh_shared` — **`state_control/` is absent** |
| Would packaging include it? | yes, if the source were copied | `pyproject.toml`: `include = ["gateway_v2*", "state_control*", "lint*"]`, `psycopg[binary]>=3.2` declared |

The observability layer already assumes a deployment that does not exist.
`deploy/observability/gw05b-state-freshness-alerts.yml:187-201` alarms on
`count(up{job="amf-state-rehydrator"} == 1) < 2`, and the file's own header says why it is dormant:

> NOT LOADED YET… These rules read the `amf_state_*` family, which only the v3 `gateway_v2` tree
> exports, and that tree has no deployment: `gateway_v2/edge/` is still stubs, so nothing serves
> `/metrics`.

One piece of good news: `default_rehydrator_id()` (`rehydrate.py:66-69`) returns
`f"{socket.gethostname()}:{os.getpid()}"`, and distinct containers get distinct hostnames, so two
instances are distinguishable in the stamp's `by` field with zero configuration. `AMF_REHYDRATOR_ID`
is for operator legibility, not correctness. `Rehydrator.__init__` also rejects a newline in the
name at construction rather than per round, which is the right place for it.

**Health / readiness.** The rehydrator has no HTTP surface at all. Gateway-side readiness exists
as a predicate (`state_ready` in `gateway_v2/runtime/state_stamp.py`) and is uncalled, pending
GW06; see the handoff §1.1. When rehydration fails, the chain is: stamp withheld → gateways age
past `FRESH_MS` (5 s) → per-kind fail-closed with the `domain/posture.py` codes → 503 +
`Retry-After` from `gap_retry_after_s`. That posture is R2-03's and is already implemented.

---

## 7. Current idle-stop behaviour

Nothing in this repository can stop a rehydrator today, because no rehydrator runs. The hazard is
real for the target deployment, and the runbook contains both halves of a genuine conflict.

**The requirement to protect them** — runbook §0.3 row R2-04: *"and never lets an idle-stop policy
touch them."*

**The requirement to have an idle-stop policy** — runbook §0.3 row R2-24 and the §3 amendment
(line 798): *"Every cloud validation environment has an idle watchdog, a burn-rate monitor, a
store-memory alarm, an audit guard and an owner-visible teardown rule that still works if the
operator's session ends; round 2 lost ≈ $120 to idle burn without one."*

So the watchdog is mandatory and must not touch the rehydrators. The only reconciliation is a
**named exclusion list**, which is exactly what the alert annotation already instructs an operator
to verify (`gw05b-state-freshness-alerts.yml:200`: *"Verify the idle watchdog's exclusion list
still names both instances"*), and which the handoff §2.2 records as a rule.

**The incident this comes from.** Round-2 ledger 09:06Z: an idle watchdog stopped
`rv-r2state-cp-1`, which happened to host the only rehydrator
(`docs/plans/2026-10-07-gw05b-state-freshness-and-ha-rehydration.md:75,165-167`). Note that two
rehydrators would not have saved it if the watchdog's criterion were CPU-based, because both would
look equally idle.

**Why activity-based exclusion cannot work.** A rehydrator round is O(1) per kind by GW05c's
design — four indexed counter reads and four O(1) store head reads per second. The handoff §2.2
states the consequence plainly: *"An exclusion that relies on the instances looking busy will not
hold: an O(1) round is nearly idle by design."* Protection must be **identity-based** (instance
name, tag, or label), never activity-based.

**Mechanisms in this repository that are adjacent but not currently a threat**, checked so the
plan does not have to guess later: `docker-compose.yml` `restart:` policies (these *keep* services
up, including the gateway healthcheck added by BACKSTOP CHG-0027); the MCP sandbox reaper
(`docker_manager`, scoped to `mcp_sandbox_*` containers only); Celery beat schedules in
`control/`; `infra/terraform/` (AWS, and the v3 target is GCP, so it is not the deployment that
will host this). None reference `state_control` or a rehydrator. The threat is entirely in the
not-yet-written deployment.

---

## 8. Gap analysis

| # | Requirement (runbook GW05b / R2-04) | State | Gap | Severity |
|---|---|---|---|---|
| G1 | `lock_timeout` on every control-plane connection | constructed, **discarded by PgBouncer** | no `SET LOCAL`, no read-back verification | **HIGH** |
| G2 | `statement_timeout` on every control-plane connection | constructed, **discarded** | same | **HIGH** |
| G3 | `idle_in_transaction_session_timeout` on every control-plane connection | constructed, **discarded**; covers only our own sessions | same, plus no server-side `ALTER ROLE` | **HIGH** |
| G4 | `tcp_user_timeout` on every control-plane connection | **wrong side** (server GUC, not client param); no keepalives | client-side param + keepalives | **HIGH** |
| G5 | Lock-free `REPEATABLE READ` publish snapshot | **done and correct** | none (verify only) | — |
| G6 | A lock/statement timeout must not be treated as a Postgres outage | **defective** — `psycopg.Error` catch-all | narrow `_db_faults`; classify timeouts as faults, not outages | **HIGH** |
| G7 | Concurrent rehydrators safe | stamp + manifest guarded; **`publish_kind` unguarded** | scope `guard=False` to the `STORE_AHEAD` path | **MEDIUM** |
| G8 | ≥ 2 rehydrators | **no process exists** | entrypoint, loop, env reader, image | **HIGH** |
| G9 | ≥ 2 zones | no deployment | two named services + declared AZ spec | **HIGH** |
| G10 | Never let an idle-stop policy touch them | no deployment; R2-24 mandates a watchdog | identity-based exclusion list, asserted | **HIGH** |
| G11 | Alarm when `ok_publish_pending` is older than one period | alert exists, **no producer** | timestamp the pending write; export the gauge | **MEDIUM** |
| G12 | Rehydrator observability | **nothing** control-plane side | `/metrics` + counters; `up{job=…}` target | **MEDIUM** |
| G13 | Per-round head read should not be read-write | works, but writes on the hot path | route through a read-only snapshot | **LOW** (hardening) |
| G14 | L05b-3 as the runbook states it | drilled at < 5 s, lock held one round, writer fail-fast not asserted | 60 s hold; assert `LockNotAvailable` ≈ 2.25 s | **MEDIUM** |

G1–G4 and G6 are the code core of R2-04. G8–G10 are its deployment core. G5 is the part that is
genuinely finished.

---

## 9. Target architecture

```
          Zone A                                    Zone B
  ┌───────────────────────┐                 ┌───────────────────────┐
  │ state-rehydrator-a    │                 │ state-rehydrator-b    │
  │  RehydratorService    │                 │  RehydratorService    │
  │   round_once(deep=…)  │                 │   round_once(deep=…)  │
  │   every 1000 ms       │                 │   every 1000 ms       │
  │  /metrics  /healthz   │                 │  /metrics  /healthz   │
  │  idle-stop: EXCLUDED  │                 │  idle-stop: EXCLUDED  │
  └───────┬───────┬───────┘                 └───────┬───────┬───────┘
          │       │                                 │       │
          │       └────────────┐       ┌────────────┘       │
          │                    ▼       ▼                    │
          │            Valkey / Memorystore (primary)        │
          │            {rv2}:stamp  — WATCH-guarded,         │
          │            monotonic verified_at                 │
          │            {rv2}:meta:<kind> — manifest last,    │
          │            never regresses                       │
          ▼                                                  ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ Postgres PRIMARY (never a replica — pg.py docstring)         │
   │   reads:  REPEATABLE READ, READ ONLY, zero locks             │
   │   writes: FOR UPDATE on amf_state_counter, writer only       │
   │   every session: lock/statement/idle-tx via SET LOCAL        │
   │                  (authoritative, PgBouncer-proof)            │
   │                  + client tcp_user_timeout + keepalives      │
   │                  + start-up read-back assertion              │
   └──────────────────────────────────────────────────────────────┘
          ▲
          │ FOR UPDATE, bounded by lock_timeout → LockNotAvailable ≈ 2.25 s
   ┌──────┴───────────────┐
   │ StateWriter          │  control-plane writes
   └──────────────────────┘
```

Invariants the target must hold, each traceable to a test in §19:

1. **A rehydrator never waits on a row lock to obtain a consistent snapshot.** Already true via
   `snapshot()`; extended to the per-round head read (G13).
2. **Every control-plane session carries all four bounds, verified at run time, not asserted in a
   docstring.** The read-back assertion is the mechanism (§11.5).
3. **A lock timeout is a bounded failure, never a Postgres outage.** It withholds the stamp; it
   does not mint a degraded one (G6).
4. **Two rehydrators are redundant, not merely co-existent.** Neither blocks the other and neither
   can regress the other's published state (G7).
5. **Losing one rehydrator or one zone changes nothing observable.** No leader election, so there
   is no failover to go wrong.
6. **No idle policy can remove the last rehydrator**, and the exclusion is identity-based.

---

## 10. Lock-free REPEATABLE READ snapshot design

### 10.1 What exists and why it is already correct

`PostgresControlDB.snapshot(kind)` (`pg.py:251-280`) is the publish path and needs no redesign:

```python
connection = self._connect(read_only=True)        # REPEATABLE READ + read_only
try:
    with connection.cursor() as cursor:
        tx = PostgresTx(cursor)
        cursor.execute("SELECT epoch, seq, feed_seq, count, on_count "
                       "FROM amf_state_counter WHERE kind = %s", (kind.value,))
        row = cursor.fetchone()
        counters = ZERO_COUNTERS if row is None else KindCounters(…)
        return counters, tx.records(kind), tx.engaged_keys(kind)
finally:
    connection.rollback()
    connection.close()
```

Three properties matter:

- **No lock request.** The counter row is read with a bare `SELECT`, deliberately not reusing
  `PostgresTx.counters` — which would both request `FOR UPDATE` when asked and perform the
  `INSERT … ON CONFLICT`. The duplication of the `SELECT` is intentional and should be left alone;
  a comment saying so would be an improvement.
- **One snapshot across three statements.** `REPEATABLE READ` fixes the snapshot at the first
  statement, so the counter row, the records and the engaged keys are all read from the same
  generation. This is the consistency the manifest's `count` / `on_count` depend on: a
  `READ COMMITTED` read could see a counter row from before a write and records from after it, and
  publish a manifest whose counts disagree with the records it attests — which readers treat as a
  fault.
- **`READ ONLY` as well as `REPEATABLE READ`.** Belt-and-braces: a `READ ONLY` transaction cannot
  acquire a row lock even if a future edit asked it to, so the lock-freedom is enforced by the
  server rather than by code review.

The absence of `FOR SHARE` is the whole fix for H7, and `pg.py`'s docstring says so.

### 10.2 The required change: the per-round head read (G13)

`Rehydrator._counters` → `PostgresControlDB.counters` → `tx()` is read-write `READ COMMITTED` and
performs an `INSERT … ON CONFLICT DO NOTHING`. Target shape:

```
Rehydrator._counters(kind)
      ▼
PostgresControlDB.counters(kind)
      ▼
_connect(read_only=True)          REPEATABLE READ, READ ONLY
      ▼
SELECT epoch, seq, feed_seq, count, on_count
  FROM amf_state_counter WHERE kind = %s        -- no INSERT, no lock
      ▼
row is None → ZERO_COUNTERS                     -- the row-creation case
      ▼
rollback(); close()
```

The `INSERT … ON CONFLICT DO NOTHING` exists to create a missing counter row. The rehydrator does
not need to create it: `snapshot()` already handles `row is None` by returning `ZERO_COUNTERS`, and
the writer's `tx.counters(kind, lock=True)` still performs the insert on the write path, which is
the only path that needs the row to exist. So the row-creation responsibility moves entirely to the
writer, where it belongs, and the read path becomes a pure read.

This keeps `counters()` semantically identical for every existing caller (an absent row reads as
`ZERO_COUNTERS` either way) while removing the last way a lock can touch the per-round loop.

### 10.3 Flow after the change

```
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY
      │
      ├─ diagnose path:  SELECT counters                       (1 indexed row)
      │
      └─ repair path:    SELECT counters
                         SELECT records     WHERE kind ORDER BY key
                         SELECT engaged_keys WHERE kind AND engaged
      ▼
build complete in-memory snapshot
  (KindCounters, tuple[SignedRecord, ...], tuple[str, ...])
      ▼
ROLLBACK          ← never COMMIT; nothing was written
      ▼
StateWriter.manifest_for(kind, counters)      sign the manifest
      ▼
StatePublisher.publish_kind(...)              records+index, manifest LAST
      ▼
atomically visible: readers bound themselves by the manifest
```

Note the snapshot is fully materialised in memory *before* anything is published —
`repair` binds `counters, records, engaged` and only then calls `manifest_for` and `publish_kind`.
A failure during the read therefore publishes nothing at all.

### 10.4 Atomic publication

Publication atomicity is a store property, and it is already designed:

- **Manifest last, always.** `_guarded` stages records and index entries, then writes the manifest
  and the nudge inside one `MULTI`. A reader sees either the old manifest — and skips, because
  `feed_seq` has not advanced — or the new one, whose index entries are already present. Stated as
  the ordering rule in `publisher.py`'s docstring and honoured by both implementations.
- **Readers bound themselves by the manifest.** An index running ahead mid-batch is normal rather
  than a fault.
- **Record-level signatures.** Each record carries its own `feed_seq` inside its signature, so a
  concurrent newer publish writes a higher score and an older one cannot be mistaken for current.

The one hole is `publish_kind(guard=False)` — §12.2.

---

## 11. PostgreSQL connection hardening

### 11.1 Mechanism: `SET LOCAL`, authoritative; startup `options`, belt-and-braces

Keep the existing `options=-c …` (it works when connected directly and is harmless when ignored)
and add `SET LOCAL` as the authoritative application, exactly as `analytics_db.py` does. Why this
rather than bypassing PgBouncer:

- It is correct whether the DSN points at `pgbouncer:6432` or `postgres:5432`, so the deployment
  topology stops being a correctness variable.
- It survives `pool_mode = transaction` and `server_reset_query = DISCARD ALL`, because `SET LOCAL`
  is scoped to the transaction PgBouncer is currently pinning to a server connection.
- It is the pattern this repository already chose for this exact problem. One story, not two.
- Bypassing PgBouncer would contradict a rule asserted in `pgbouncer.ini:5`,
  `docker-compose.yml:36`, and `docker-compose.prod.yml:14`, and would buy nothing `SET LOCAL` does
  not already give.

Note `pg.py`'s "THE DSN MUST POINT AT THE PRIMARY" docstring forbids a *replica*, not a *pooler*;
PgBouncer in transaction mode routes to the primary, so there is no conflict to resolve.

Both transactions already satisfy `SET LOCAL`'s precondition of being inside a transaction:
`_connect` uses `autocommit=False`, so psycopg opens one on the first statement. The `SET LOCAL`
statements must therefore be the **first** statements executed in both `tx()` and `snapshot()`.

One ordering note for `snapshot()`, **corrected during implementation**: an earlier draft of this
plan warned that `SET LOCAL` would move the repeatable-read snapshot earlier, since it is a
statement. That is wrong — `SET` does not take a transaction snapshot in PostgreSQL, so the
snapshot is still taken at the first statement that actually reads and the isolation semantics are
untouched. The real constraint is only that the `SET LOCAL` must be in the *same* transaction: issued
on another connection, or after a commit, it silently does nothing.

### 11.2 `lock_timeout`

*How long may a rehydrator wait for a lock?* Ideally never, and after §10.2 it requests none. The
value is a safety net for a future edit and a hard bound for the writer.

- **Rehydrator: 250 ms.** Must be a small fraction of the 1000 ms round period; four kinds × the
  timeout must not consume a round. If a rehydrator ever hits a lock, the round should fail fast,
  withhold the stamp, and retry next period rather than eat the period.
- **Writer: 2000 ms (unchanged).** The runbook's own acceptance figure validates this: *"a queued
  writer fails `LockNotAvailable` in 2.25 s"* — 2000 ms plus overhead. Changing it would invalidate
  the reference measurement.

On expiry Postgres raises `LockNotAvailable` (SQLSTATE 55P03). After G6 this is classified as a
**bounded fault**, not an outage: the kind's error is recorded, the stamp is withheld, the
previously published state remains active and unchanged, and the next round retries. Readiness does
not change on a single failed round — the handoff §1.1 is explicit that `/readyz` keys on freshness
alone, so that one blip does not flap a worker out of the load balancer. If the condition persists
past `FRESH_MS` (5 s), gateways fail closed per kind. Stale state is **not** served past the
freshness bound; the gateway fails closed rather than serving stale.

### 11.3 `statement_timeout`

Applies to every statement on the connection, so it must accommodate the slowest legitimate one.
The relevant asymmetry: the rehydrator's statements are an indexed single-row read plus two
whole-kind scans; the writer's `_commit_many` can legitimately process 25,000 records in one
transaction.

- **Rehydrator: 800 ms.** Derived, not chosen. A round touches four kinds sequentially, and a round
  slower than `FRESH_MS − rehydrate_period_ms` produces stamps born stale — the failure mode
  `state_knobs.py` validates against. 4 × 800 ms = 3.2 s leaves ~0.8 s of headroom inside the 5 s
  bound after the 1 s period. The drill's `tightly_bounded_db()` uses 1000 ms for the same reason
  and measured 2.0 s per round against 5.0 s at the defaults.
  **Caveat:** the deep round is O(records) and reads every record of a kind. At the 25k-tenant
  scale the handoff §6 records first-start catch-up of 2.40 s, so 800 ms will abort a deep round on
  a large estate. The deep pass must therefore either get its own, larger bound or be moved to its
  own connection. **Recommendation:** give `verify`/`repair` a separate `snapshot_timeout_ms`
  (default 5000 ms) and keep 800 ms for the per-round head read. Do not make this a single number.
- **Writer: 5000 ms (unchanged)** for `put`, with bulk `put_many` needing an explicit override at
  its call site. Flag for the implementer: `_commit_many` at 25,000 records against a 5 s statement
  timeout is untested and may already be a latent failure; it is out of R2-04's scope but should be
  recorded.

Interaction with `lock_timeout`: `lock_timeout` applies per lock acquisition, `statement_timeout`
to the whole statement, and they run concurrently. `lock_timeout` must be strictly smaller or it
can never fire — the statement would be cancelled first and the error would be the less specific
`QueryCanceled`. The proposed pairs (250/800 rehydrator, 2000/5000 writer) both satisfy this.

### 11.4 `idle_in_transaction_session_timeout`

This is the one that releases the lock the writer is holding. Postgres terminates a session idle
*inside* a transaction past the limit, rolling it back and releasing every lock it held.

- **Writer: 5000 ms (unchanged).** Long enough that no healthy write is at risk — `_commit` does
  bounded local work with no network calls inside the transaction — and short enough that an
  abandoned writer releases the counter row in 5 s rather than never.
- **Rehydrator: 5000 ms.** Largely irrelevant, since it holds no locks and its transactions are
  short, but a rehydrator that hangs between statements should not pin a snapshot either
  (`REPEATABLE READ` holds back vacuum horizon). Keep it symmetric.

**Should writer and rehydrator differ?** On this setting, no. On `lock_timeout` and
`statement_timeout`, yes — see above.

**The limitation that must be written down.** `SET LOCAL` covers only sessions created by
`PostgresControlDB`. A `psql` session, a migration, an admin tool, or a paused container holding
`FOR UPDATE` is not covered. The runbook's L05b-3 holds the lock for **60 s**, which an application
writer bounded at 5 s cannot do — so that scenario is reachable only from an unbounded session.
Two consequences:

1. Add a server-side default as defence in depth:
   `ALTER ROLE <app_role> SET idle_in_transaction_session_timeout = '5s';`
   This covers every session using that role regardless of client, and is the only way to bound a
   lock holder we did not create.
2. The lock-free snapshot is what makes the rehydrator immune either way. The GUC is defence in
   depth, not the fix. Do not let a plan reviewer conclude otherwise.

### 11.5 `tcp_user_timeout`

Bounds how long the kernel retransmits unacknowledged data before giving up. It protects against
the network-level stall that no server-side GUC can: a peer that has vanished without closing the
socket, where `statement_timeout` cannot fire because the server is not running our query and
`connect_timeout` has already succeeded.

- **Set it as a libpq *connection parameter*, not a GUC.** psycopg 3 passes unknown connect kwargs
  through to libpq, and `tcp_user_timeout` is supported from libpq 12. Pass it in the connect call
  (or in the DSN), *in addition to* keeping the server GUC for the server's own socket:

  ```python
  connection = self._psycopg.connect(
      self._dsn,
      autocommit=False,
      connect_timeout=self._connect_timeout_s,
      options=self._options,
      tcp_user_timeout=self._tcp_user_timeout_ms,   # client side, milliseconds
      keepalives=1,
      keepalives_idle=self._keepalives_idle_s,
      keepalives_interval=self._keepalives_interval_s,
      keepalives_count=self._keepalives_count,
  )
  ```

- **Keepalives are not optional alongside it.** `tcp_user_timeout` only acts on data awaiting
  acknowledgement. A connection sitting idle with nothing in flight has nothing to time out, so
  keepalive probes are what create the unacknowledged data the timeout then bounds. Recommended:
  `keepalives_idle = 2 s`, `keepalives_interval = 1 s`, `keepalives_count = 3`.
- **Values:** rehydrator 2000 ms (below the round period); writer 5000 ms (unchanged).
- **PgBouncer does not interfere.** This governs the client↔PgBouncer TCP connection, which is
  precisely the socket that hangs. The server GUC continues to govern PgBouncer↔Postgres.

Implementer note: `tcp_user_timeout` is Linux-specific. `TZ`/platform is Linux in all deployment
targets, but passing an unsupported parameter raises at connect time on other platforms, so guard
it if local macOS development is expected.

### 11.6 The read-back assertion — the single most valuable addition

Every one of G1–G4 was invisible because nothing ever checked. The fix is one query on first
connect:

```python
EXPECTED = ("lock_timeout", "statement_timeout",
            "idle_in_transaction_session_timeout", "tcp_user_timeout")

# after the SET LOCAL statements, inside the same transaction
cursor.execute(
    "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)", (list(EXPECTED),)
)
```

Compare against the requested values and **fail start-up** on a mismatch, naming the setting and
both values. Rationale, which is `pg.py`'s own: *"a bound that only some connections carry is not a
bound"* — and a bound nothing verifies is not a bound either. This is the check that would have
caught the PgBouncer drop on day one, and it is the direct analogue of
`policy/analytics_timeout_probe.py`, the staff-only `pg_sleep(6)` canary that proves the analytics
deadline actually applies.

Do this at start-up (once, loudly, fatal), not per connection (hot-path cost, and a per-round
failure is the wrong blast radius). Pair it with a live canary endpoint on the rehydrator for
operators, mirroring the analytics probe.

### 11.7 Scope: should the Django `default` alias get the four settings?

The runbook says *"on every control-plane connection"*, which read literally includes Django's.
Two facts in tension:

- **It cannot cause H7.** There is no `select_for_update` anywhere in `control/`, and
  `amf_state_counter` is touched exclusively by `state_control`. Django is not a candidate lock
  holder for this defect.
- **The text is broader than the defect**, and the `default` alias currently has none of the four
  while `analytics` has two.

**Recommendation:** extend `postgres_database_from_url` and add `SET LOCAL` coverage for the
`default` alias as a *compliance* item, clearly labelled as satisfying the literal requirement
rather than fixing R2-04. Keep it in its own phase and its own commit so it can be dropped without
touching the defect fix. Add `lock_timeout` to `analytics_db.py`'s SQL list at the same time — it is
missing there too.

---

## 12. Concurrent rehydrator design

### 12.1 No leader election — and why that is the right answer

Two rehydrators must be *redundant*, not *coordinated*. The runbook requires "≥ 2 … (proven safe
concurrently)" and names no election, and introducing one would make global enforcement depend on a
lease — converting a redundancy mechanism into a new single point of failure with a failover window.
The repository's existing answer is better and already built:

| Hazard | Mechanism | Location |
|---|---|---|
| Older snapshot overwrites newer stamp | `put_stamp` WATCHes `{rv2}:stamp`, decodes the held stamp under the signing secret, returns `False` if `held.verified_at >= stamp.verified_at` | `valkey.py:109-137` |
| Unverifiable stamp blocks all future ones | an un-decodable held stamp is **overwritten**, not respected — otherwise anyone who can write the key once could plant a far-future value | `valkey.py:138-156` |
| Partial publication | records and index staged first, manifest written **last** inside one `MULTI` | `valkey.py:_guarded` |
| Manifest regress on normal publishes | `_is_ahead` under WATCH refuses a lower `feed_seq` | `valkey.py:_is_ahead` |
| Losing the stamp race read as an error | `RoundSummary.stamp_race_lost` is a distinct field; `ok` stays `True` | `rehydrate.py:119-133` |

`put_stamp` returning `False` is the *expected, healthy* outcome roughly half the time with two
instances — asserted by
`test_lgw05b_rehydrate_stamp.py::test_losing_the_race_is_the_healthy_two_rehydrator_outcome`
(*"losing the race is the healthy two-re-hydrator outcome"*). Publishing is idempotent in content:
both rehydrators read the same Postgres generation and publish byte-identical signed records.

**Postgres-side concurrency is a non-issue.** Neither takes a lock; `REPEATABLE READ` readers
block nobody and are blocked by nobody. They do not share a connection pool because there is no
pool — one short-lived connection per operation, per process.

### 12.2 The one real gap: `publish_kind(guard=False)` — G7

```python
# valkey.py:197-222
return self._guarded(kind, manifest, stage, guard=False)
```

`guard=False` is **necessary** for the `STORE_AHEAD` repair: that path exists because the store
holds a `feed_seq` Postgres never issued, so a guarded publish at the lower Postgres position would
be refused and the divergence would never heal. `Rehydrator.repair` bumps the epoch first
(`StateWriter.repair_epoch`) so the republished state is unambiguously newer by *version*, but
`_is_ahead` compares `feed_seq` only, so the guard would still refuse it.

But it is applied to **all eight** repair reasons (`MISSING`, `INVALID`, `STORE_AHEAD`, `STALE`,
`VERSION`, `COUNTS`, `INDEX`, `ENGAGED`, `CONTENTS`), and for the other seven a slower rehydrator
can move the manifest backwards — the sequence in §3.3 RC-4. Readers refuse a regress by design, so
the kind goes unreadable until the next round repairs it. Bounded, untested, and introduced by the
very HA requirement this card adds.

**Design change.** Thread the intent through rather than hard-coding the escape:

```python
# publisher.py — StatePublisher Protocol
def publish_kind(
    self,
    kind: StateKind,
    records: Sequence[SignedRecord],
    manifest: Manifest,
    engaged: Sequence[str] = (),
    *,
    allow_regress: bool = False,      # True ONLY for the STORE_AHEAD epoch repair
) -> bool: ...
```

- `ValkeyPublisher.publish_kind` passes `guard=not allow_regress`.
- `Rehydrator.repair` passes `allow_regress=(reason == STORE_AHEAD)`.
- `MemoryStore.publish_kind` gains the same `_is_ahead` check when `allow_regress` is False — it
  currently has none, so the twin is more permissive than the product and a regress bug would not
  reproduce in the fast tests.
- Default `False`, so a new caller is safe by default.

Under the change, a slower rehydrator's stale repair returns `False` (store already ahead), the
round records it as healthy-but-superseded rather than as a repair, and nothing regresses.

### 12.3 Required atomicity, stated

| Property | Required | Provided by |
|---|---|---|
| A kind's manifest never moves backwards | yes | `_is_ahead` under WATCH + G7 fix |
| `verified_at` never moves backwards | yes | `put_stamp` WATCH compare |
| Readers never see a partial generation | yes | manifest written last, inside `MULTI` |
| Two rehydrators never deadlock | yes | no locks on PG; WATCH retry (20) on the store |
| Mixed versions across kinds | **tolerated by design** | each kind has its own manifest; the stamp attests per-kind cursors, so a reader knows exactly what was verified for each |
| Temporary empty state | must not occur | `publish_kind` deletes and rewrites inside one `MULTI`, so the empty intermediate is never visible |

Version/generation identifiers already exist and should be used rather than re-invented:
`Version(epoch, seq)`, `feed_seq` (monotonic per kind), `Manifest.count` / `on_count`,
`Stamp.verified_at` / `by` / `deep` / `degraded`, and `Cursor` per kind inside the stamp. No new
identifier is needed for R2-04.

---

## 13. Multi-zone deployment

**Decision: two *named* services, not two replicas.** `deploy.replicas: 2` shares one placement
constraint by construction, and the requirement is two *zones*. Two named services each carry their
own placement, map 1:1 onto later systemd units or ASGs, and give Prometheus distinct `instance`
labels — which is what `StateRehydratorSingleInstance` counts.

### 13.1 What has to be built first

1. **A service entrypoint.** New `state_control/service.py` with a `RehydratorService` that owns
   the loop, plus `state_control/__main__.py` so `python -m state_control` runs it. Keep it out of
   `gateway_v2/runtime/lifecycle.py`: that is the gateway's CLI, and the import-linter layer
   contract plus the `check_http_outside_edge_resolve` gate both treat `state_control` as a
   separate tree.
2. **An env/config reader.** `StateKnobs` is pure by design and must stay so; the loader belongs
   in the service. Names are already settled by the handoff §1.5 — `AMF_STATE_FRESH_MS`,
   `AMF_STATE_PG_GRACE_MS`, `AMF_REHYDRATE_PERIOD_MS`, `AMF_STATE_REFRESH_MS`,
   `AMF_REHYDRATE_DEEP_EVERY`, `AMF_REHYDRATOR_ID` — plus new ones for the DSN and the five
   timeouts (§18). Log `StateKnobs.warnings` at WARNING on start-up.
3. **The image must contain `state_control`.** `gateway_v2/Dockerfile:16-18` bind-mounts only
   `gateway_v2/pyproject.toml`, `gateway_v2/gateway_v2`, and `shared/ai_mesh_shared`. Add a mount
   and copy for `gateway_v2/state_control`. `pyproject.toml` already declares
   `include = ["gateway_v2*", "state_control*", "lint*"]` and `psycopg[binary]>=3.2`, so packaging
   and the driver need no change — only the source copy.
4. **A minimal HTTP surface**: `/healthz` (process liveness) and `/metrics` (§16). Deliberately
   *not* `/readyz` — a rehydrator has no readiness semantics; it is either running or it is not,
   and the fleet-level readiness signal is the stamp itself.

### 13.2 Compose (local / single-host)

Two services sharing an anchor, differing only in identity and declared zone:

```yaml
x-rehydrator: &rehydrator
  build: { context: ., dockerfile: gateway_v2/Dockerfile }
  command: ["python", "-m", "state_control"]
  restart: unless-stopped
  environment: &rehydrator-env
    AMF_STATE_PG_DSN: postgresql://…@pgbouncer:6432/${POSTGRES_DB}
    AMF_STATE_VALKEY_URL: redis://redis:6379/0
    AMF_STATE_HMAC_KEY: ${AMF_STATE_HMAC_KEY:?required}
    AMF_REHYDRATE_PERIOD_MS: "1000"
    AMF_REHYDRATE_DEEP_EVERY: "60"
    AMF_PG_REHYDRATOR_LOCK_TIMEOUT_MS: "250"
    AMF_PG_REHYDRATOR_STATEMENT_TIMEOUT_MS: "800"
    AMF_PG_REHYDRATOR_SNAPSHOT_TIMEOUT_MS: "5000"
    AMF_PG_IDLE_TX_TIMEOUT_MS: "5000"
    AMF_PG_TCP_USER_TIMEOUT_MS: "2000"
  depends_on:
    pgbouncer: { condition: service_healthy }
    redis:     { condition: service_healthy }
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:9108/healthz')"]
    interval: 10s
    timeout: 5s
    retries: 3

services:
  state-rehydrator-a:
    <<: *rehydrator
    environment:
      <<: *rehydrator-env
      AMF_REHYDRATOR_ID: rehydrator-a
      AMF_DEPLOY_ZONE: zone-a
    labels:
      ai-mesh.idle-stop: "exempt"
      ai-mesh.role: "control-plane-rehydrator"

  state-rehydrator-b:
    <<: *rehydrator
    environment:
      <<: *rehydrator-env
      AMF_REHYDRATOR_ID: rehydrator-b
      AMF_DEPLOY_ZONE: zone-b
    labels:
      ai-mesh.idle-stop: "exempt"
      ai-mesh.role: "control-plane-rehydrator"
```

`restart: unless-stopped` follows the precedent BACKSTOP CHG-0027 set for the gateway, which was
the only core service with neither a healthcheck nor a restart policy. On one host, `zone-a` /
`zone-b` are **labels, not isolation** — they exist so the scrape config, the alert `instance`
labels, and the exclusion list are exercised end to end. The document must not claim zone
redundancy from compose.

### 13.3 Cloud target (specification, not manifests)

The handoff §2 is right that writing manifests now would be inventing infrastructure: `infra/`
is AWS/Terraform and the v3 target is GCP (GCE MIG, Cloud SQL, Memorystore). The binding spec:

- Two instances in two zones of the Cloud SQL primary's region. Either two single-instance MIGs
  pinned to distinct zones, or one regional MIG with `distribution_policy_zones` of exactly two and
  `target_size = 2`. **Prefer two single-zone MIGs**: a regional MIG may place both instances in one
  zone during a rebalance, which silently voids the requirement.
- `autoscaling: disabled`, `min = max = 1` per MIG. There is nothing to autoscale; the round is
  O(1).
- No load balancer. The instances are not addressed; they are observed via `/metrics`.
- Both scraped as `job="amf-state-rehydrator"` so the existing alert's `count(up{…}) < 2` works
  without modification.
- Maintenance windows must not overlap each other, nor Cloud SQL's, nor Memorystore's
  (handoff §2.4, R2-13, E2-08).
- Cost: two small VMs. The round is O(1) per kind since GW05c; the deep pass is the only
  O(records) work and runs off the critical path on a 60-round cadence.

---

## 14. Idle-stop protection

The requirement is a *conflict* to reconcile, not a setting to flip: R2-04 says never let an
idle-stop policy touch them, and R2-24 plus the §3 amendment make an idle watchdog **mandatory** on
every validation environment.

**The reconciliation is an identity-based exclusion list, enforced by an assertion rather than by
prose.** Three layers:

1. **Declare the exemption on the resource.** Compose labels `ai-mesh.idle-stop: "exempt"` and
   `ai-mesh.role: "control-plane-rehydrator"` above; on GCP, the equivalent instance labels, plus
   `deletionProtection` on the instances and `min = max = 1` on the MIG so no scale-in decision can
   remove them.
2. **Teach the watchdog to honour it.** Whatever script or policy implements R2-24's watchdog must
   skip resources carrying the label. **It must key on the label, never on activity.** An O(1)
   round is nearly idle by design — the handoff §2.2 is explicit — so any CPU, request-rate, or
   connection-count heuristic will classify a healthy rehydrator as idle *correctly*, and that is
   exactly how the 09:06Z ledger incident happened to `rv-r2state-cp-1`.
3. **Assert the exemption in CI, so it cannot rot.** A test that parses the compose files (and later
   the cloud manifests) and fails if either rehydrator service lacks the exemption label, or if the
   watchdog's exclusion list does not name both. This is the lesson the handoff draws from GW05c:
   *"Prose does not fail a build."* `tests/gates/test_lgw05b_handoff.py` is the established place
   for tripwires of this kind.

How the deployment guarantees availability:

```
                 idle watchdog (mandatory, R2-24)
                           │
                  reads ai-mesh.idle-stop label
                           │
            ┌──────────────┴──────────────┐
            ▼                             ▼
  state-rehydrator-a: exempt     state-rehydrator-b: exempt
  zone A, min=max=1              zone B, min=max=1
  deletionProtection             deletionProtection
  restart: unless-stopped        restart: unless-stopped
            │                             │
            └────────── never both removed ───────────┘
                           │
          StateRehydratorSingleInstance alarms at < 2 for 5m
```

The alarm is the backstop for all three layers failing, and it already exists. What it needs is a
scrape target — G12.

---

## 15. Failure scenarios

### Scenario A — idle writer holds `FOR UPDATE`

Expected and, for the snapshot path, **already true**.

The writer holds the counter row. `Rehydrator.round_once` → `_diagnose` → `counters()` reads the
row without requesting a lock (today via `INSERT…ON CONFLICT` + bare `SELECT`; after §10.2 via a
pure read-only `REPEATABLE READ` read). `repair` → `snapshot()` reads counters, records and engaged
keys in one `REPEATABLE READ, READ ONLY` transaction, taking no lock. The repair publishes, the
stamp is minted, and the fleet stays fresh **while the lock is still held**.

Reference figures: 0.54–0.79 s per kind (Cloud SQL, reference patch); the local drill asserts
< 5 s against a container on a laptop; RC2 took 38 s. A *queued writer* is expected to fail
`LockNotAvailable` in ≈ 2.25 s — that fail-fast is on the writer, not the rehydrator, and is an
acceptance criterion in its own right.

### Scenario B — writer transaction remains idle

```
idle txn → idle_in_transaction_session_timeout (5 s) → session terminated,
           txn rolled back, locks released → queued writers proceed
```

**Limitations that must be stated.** (i) `SET LOCAL` covers only sessions created by
`PostgresControlDB`; a `psql`, migration, admin tool, or paused container is unbounded, which is
why §11.4 recommends `ALTER ROLE … SET idle_in_transaction_session_timeout = '5s'`. (ii) The GUC
only fires when the session is idle *inside* a transaction — a session actively running a long
statement is `statement_timeout`'s job, and one idle *outside* a transaction holds no locks and is
correctly left alone. (iii) L05b-3 holds the lock for 60 s, which an application writer bounded at
5 s cannot do; that scenario is only reachable from an unbounded session, which is what the drill
constructs with a raw `psycopg.connect(DSN)` carrying no options. (iv) The rehydrator does not
*depend* on this timeout — the lock-free snapshot is the fix; this is defence in depth.

### Scenario C — rehydrator query exceeds `statement_timeout`

Postgres cancels the statement; psycopg raises `QueryCanceled` (SQLSTATE 57014). The transaction is
left in a failed state and must be rolled back — both `tx()` and `snapshot()` already do so in
`finally`, and the connection is then closed rather than reused, so there is no risk of handing a
poisoned connection onward.

- **Snapshot discarded?** Yes, entirely. `repair` materialises `counters, records, engaged` before
  it publishes anything, so a mid-read failure publishes nothing.
- **Stale state?** The previously published generation stays active and unchanged. It is *not*
  served indefinitely: once no stamp is fresher than `FRESH_MS`, gateways fail closed per kind.
- **Retry?** Next round, after `rehydrate_period_ms`. `round_once` records the per-kind error and
  continues to the other kinds.
- **Readiness?** Unchanged on a single round — `/readyz` keys on freshness alone (handoff §1.1).
- **503?** Only if the condition persists past the freshness bound.
- **After G6**, this is classified as a bounded fault, so the stamp is **withheld** rather than
  minted degraded. Today it would be laundered into a ride-through.

### Scenario D — rehydrator hits `lock_timeout`

`LockNotAvailable` (SQLSTATE 55P03).

- **Classification (the G6 fix):** a bounded fault, *not* `ControlPlaneUnavailable`. It must not
  set `db_down=True`, must not reach `_ride_through`, and must not produce a degraded stamp.
- **Retry:** next round. No immediate retry inside the round — a lock held by an idle transaction
  will still be held 10 ms later, and retrying inside the round risks exceeding the period.
- **Backoff:** the round period *is* the backoff (1 s). Recommended addition: after N consecutive
  failed rounds for the same kind, log at ERROR once rather than per round, to avoid a log flood
  during a sustained incident. Do not add exponential backoff — lengthening the period during an
  incident is how the stamp ages past the bound.
- **Temporary?** Yes, until the holder commits, rolls back, or is terminated by
  `idle_in_transaction_session_timeout`.
- **Known-good state:** remains active and unchanged; the fleet fails closed at the bound rather
  than serving it indefinitely.

### Scenario E — Postgres unavailable

**This is R2-03 behaviour and is already implemented.** Stated here only for the boundary.

`_counters`/`_snapshot` raise `ControlPlaneUnavailable` (correctly, for genuine I/O faults).
`round_once` sets `db_down=True`. `_claimable` sees no verified kinds plus `db_down`, and
`_ride_through` re-asserts the **last verified** cursors, if and only if: an anchor exists; the
anchor is no older than `PG_GRACE_MS` (16 s); and `_store_holds` confirms every kind's published
head is still at or above what was last verified. The stamp is minted with `degraded=True`, and
`_verified` is deliberately *not* advanced, so each degraded stamp cannot reset the clock and the
ride cannot extend itself indefinitely.

- **Both instances retry continuously?** Yes, once per period. The load is four indexed reads per
  rehydrator per second — negligible, and no amplification: no immediate retry, no thundering
  herd. (The gateway side has the matching rule: a worker that could not reach the store at all
  waits a full period rather than hammering it — handoff §1.4.)
- **Stale state usable?** Yes, for up to `PG_GRACE_MS`, which is exactly the point: a Cloud SQL
  failover measured at 11–16 s must not become a global outage, and L05b-4 requires zero 503s.
- **Readiness?** Unchanged while the ride-through holds; global fail-closed when it expires.
- **R2-04's only contribution here** is the G6 classification fix, which makes this path fire for
  *real* outages only. R2-04 must not change `PG_GRACE_MS`, `_ride_through`, `_store_holds`, or the
  degraded-stamp semantics.

### Scenario F — rehydrator A fails

```
A unavailable  →  B continues: diagnoses, repairs, stamps
                  B's put_stamp now always wins (no competitor)
                  fleet freshness unaffected
```

**Does the current architecture provide this?** The *code* does, fully: no leader election, no
lease, no shared mutable rehydrator state, `put_stamp` already tolerant of a missing peer, and
`test_drill_both_rehydrators_gone_fails_the_fleet_closed` demonstrates that one surviving instance
restores freshness within 0.2 s of returning. The *deployment* does not, because there is no
deployment — G8. The reference patch measured "with one of two re-hydrators killed, 0 × 503".

The failure that is **not** covered: both gone. The runbook accepts this explicitly (L05b-6:
global fail-closed at the declared bound, recovery ≤ 1 s after one returns), and it is listed in
the reference patch's "costs as delivered" — both rehydrators down → global 503 after 3 s. With the
locked `FRESH_MS` of 5000 ms rather than the patch's 3000 ms, our figure is ~5 s; the handoff §1.5
warns against quoting the patch's numbers as ours.

### Scenario G — one availability zone fails

```
Zone A: rehydrator-a ✗          Zone B: rehydrator-b ✓
                 → identical to Scenario F
```

Required deployment changes: everything in §13. Specifically — two instances in two distinct zones
of the Cloud SQL primary's region; two single-zone MIGs preferred over one regional MIG (a regional
MIG may co-locate both during a rebalance and silently void the requirement); `min = max = 1` each;
both scraped under `job="amf-state-rehydrator"`; non-overlapping maintenance windows.

Honest limitation: if the failed zone is the one hosting the Cloud SQL *primary*, this is Scenario E
until the failover completes, and the ride-through — not zone redundancy — is what covers it.

---

## 16. Observability

Nothing control-plane side exists today. `gateway_v2/runtime/state_metrics.py` is the gateway's
view (what a *worker* observed about the stamp) and must not be extended for this: its
`SERIES_COUNT` is a fixed expression precisely so a series cannot be added by accident, because
R2-10 measured a full metric directory making 40% of new tenants' first requests return HTTP 500.

**Create a separate `state_control/metrics.py`** with the same discipline: a closed label set, no
tenant-derived labels, O(1) recording. The only permitted label is `kind` (a four-member enum), plus
`reason` on the repair counter, drawn from the nine module-level constants in `rehydrate.py`
(`MISSING`, `INVALID`, `STORE_AHEAD`, `STALE`, `VERSION`, `COUNTS`, `INDEX`, `ENGAGED`,
`CONTENTS`).

| Metric | New/exists | Source | Labels | Cardinality | Alert |
|---|---|---|---|---|---|
| `amf_rehydration_attempts_total` | **new** | `state_control/metrics.py`, from `round_once` | — | 1 | rate ≈ 1/s; absence → see `up` |
| `amf_rehydration_success_total` | **new** | `RoundSummary.ok` | — | 1 | — |
| `amf_rehydration_failure_total` | **new** | `RoundSummary.errors` | `kind` | 4 | > 0 for 2m → warning |
| `amf_rehydration_duration_seconds` | **new** | `round_once` wall time | — | 1 (histogram) | p99 > 0.8 × period → warning |
| `amf_rehydration_repairs_total` | **new** | `RepairEvent` | `kind`, `reason` | 36 max | sustained > 0 → warning |
| `amf_rehydration_lock_timeout_total` | **new** | G6 classifier | `kind` | 4 | **> 0 → critical. This is R2-04's own detector.** |
| `amf_rehydration_statement_timeout_total` | **new** | G6 classifier | `kind` | 4 | > 0 for 5m → warning |
| `amf_rehydration_connection_timeout_total` | **new** | G6 classifier | `kind` | 4 | > 0 for 5m → warning |
| `amf_rehydrator_active` | **new** | 1 while the loop runs | — | 1 | — |
| `amf_rehydrator_last_success_timestamp` | **new** | last `RoundSummary.ok` | — | 1 | age > 4s → critical |
| `amf_rehydrator_info` | **new** | `by` id, zone, version | `id`, `zone` | 2 | — |
| `amf_snapshot_publish_success_total` | **new** | `publish_kind` → True | `kind` | 4 | — |
| `amf_snapshot_publish_failure_total` | **new** | publish raised | `kind` | 4 | > 0 → warning |
| `amf_snapshot_publish_superseded_total` | **new** | `publish_kind` → False (G7) | `kind` | 4 | expected > 0 with 2 instances |
| `amf_state_stamp_withheld_total` | **alert exists, no producer** | `Rehydrator._withhold` | — | 1 | **already defined** (`StateStampWithheld`) |
| `amf_state_stamp_races_lost_total` | **new** | `RoundSummary.stamp_race_lost` | — | 1 | ≈ 0 with one instance, ≈ half with two |
| `amf_state_publish_pending_oldest_seconds` | **alert exists, no producer** | needs G11 | — | 1 | **already defined** (`StatePublishPending`) |
| `up{job="amf-state-rehydrator"}` | **alert exists, no target** | Prometheus scrape | `instance` | 2 | **already defined** (`StateRehydratorSingleInstance`) |

**Do not duplicate what exists.** `amf_state_stamp_age_seconds`, `_stamp_fresh`, `_stamp_degraded`,
`_stamp_deep_age_seconds`, `_stamp_invalid_total`, `_unverified_transitions_total` and the per-kind
`_floor` are already produced gateway-side and already alarmed. The control plane should *not* emit a
second opinion on freshness — two planes disagreeing about the same fact is C37, which
`domain/posture.py` exists to prevent.

**G11 in detail.** The runbook requires an alarm when an `ok_publish_pending` write is older than
one rehydrator period, and `StatePublishPending` is written against
`amf_state_publish_pending_oldest_seconds`. Nothing produces it because `StateWriter._publish_one`
and `put_many` return `OK_PUBLISH_PENDING` without recording *when*. Two options:

- **(a) Schema-backed, authoritative.** Add a `published boolean NOT NULL DEFAULT false` column to
  `amf_state_record` (or `amf_state_log`), set it true on a successful publish, and have the
  rehydrator export `now() - min(updated_at) WHERE NOT published`. This is the fallback query shape
  the alert annotation already names. Survives a writer restart. Costs a migration and a column on
  the write path.
- **(b) In-process gauge.** The writer records the timestamp of its oldest unpublished write in
  memory and exports it. Cheap, no migration — but lost on restart, and the writer is a Django
  process with no `/metrics` surface, so it would need one.

**Recommendation: (a).** The defect being detected is "committed but invisible", which is a durable
fact; detecting it with a volatile counter that resets exactly when the writer crashes — a likely
cause — defeats the purpose. The rehydrator already connects to Postgres every second and is the
natural exporter.

**Logging.** `rehydrate.py` already logs well and at the right levels: `repair` logs at WARNING with
`kind`, `reason`, `records`, `took_ms`; `_withhold` logs once per lapse rather than per round;
`_stamp` logs resume/start transitions rather than every stamp; `_store_holds` logs a voided
ride-through with both positions. Two additions: log the read-back assertion's outcome at start-up
(§11.6), and log the G6 classification decision so an operator can see "lock timeout, bounded
fault, stamp withheld" rather than inferring it.

---

## 17. Exact files, classes and functions to change

### C1 — `SET LOCAL` as the authoritative timeout mechanism

```
File:             gateway_v2/state_control/pg.py
Class/module:     PostgresControlDB
Function:         __init__, _connect, tx(), snapshot()  (+ new _apply_bounds(cursor))
Current:          four bounds passed only as the libpq `options` startup parameter, which
                  PgBouncer discards (IGNORE_STARTUP_PARAMETERS includes `options`) and which
                  pool_mode=transaction + DISCARD ALL would reset anyway.
Required change:  keep `options` as belt-and-braces; add _apply_bounds(cursor) issuing
                  `SET LOCAL lock_timeout / statement_timeout /
                  idle_in_transaction_session_timeout` as the FIRST statements of both tx()
                  and snapshot(). Parameterise values per role (§18).
Reason:           G1-G3. The runbook requires the bounds on every control-plane connection;
                  today they reach no backend in the endorsed topology.
Dependencies:     none. Mirrors control/ai_mesh_control/main_app/analytics_db.py:16-21.
Tests:            T-C1a read-back asserts current_setting() matches per connection kind;
                  T-C1b snapshot() still sees one consistent generation after the extra
                  statement (REPEATABLE READ snapshot now taken at the SET LOCAL);
                  T-C1c a DSN-supplied search_path still survives (regression:
                  gw05c plan :1708).
```

### C2 — client-side `tcp_user_timeout` and keepalives

```
File:             gateway_v2/state_control/pg.py
Class/module:     PostgresControlDB
Function:         __init__ (new keepalive params), _connect
Current:          `-c tcp_user_timeout=5000` is a SERVER GUC governing the server's socket.
                  No TCP keepalives anywhere.
Required change:  pass tcp_user_timeout as a libpq CONNECTION parameter in _connect, plus
                  keepalives=1, keepalives_idle, keepalives_interval, keepalives_count.
                  Retain the server GUC for the server side.
Reason:           G4. The stall this must bound is on the client's socket; and
                  tcp_user_timeout only acts on unacknowledged data, so keepalives are what
                  generate the probes it then bounds.
Dependencies:     libpq >= 12 (satisfied by psycopg[binary]>=3.2). Linux-only — guard if
                  macOS development is expected.
Tests:            T-C2a the connect kwargs carry the client-side parameter (unit, mocked
                  connect); T-C2b against a real server, a connection still succeeds and
                  current_setting('tcp_user_timeout') reflects the server GUC.
```

### C3 — start-up read-back assertion *(highest value per line)*

```
File:             gateway_v2/state_control/pg.py
Class/module:     PostgresControlDB
Function:         new verify_bounds() -> Mapping[str, str]; called once by the service
Current:          nothing ever verifies the settings arrived. That is why G1-G4 were
                  invisible.
Required change:  SELECT name, setting FROM pg_settings WHERE name = ANY(...) inside the
                  bounded transaction; compare with what was requested; raise a fatal,
                  named error on mismatch. Called once at start-up, not per connection.
Reason:           pg.py's own rule — "a bound that only some connections carry is not a
                  bound" — plus: a bound nothing verifies is not a bound.
Dependencies:     C1.
Tests:            T-C3a a stub reporting a wrong value fails start-up and names the setting;
                  T-C3b live against Postgres, verify_bounds() returns the requested values;
                  T-C3c live THROUGH PgBouncer with options-only (C1 disabled), the
                  assertion FAILS — this is the test that proves the defect existed.
```

### C4 — narrow the database-fault classification *(the most serious defect)*

```
File:             gateway_v2/state_control/rehydrate.py
Class/module:     module level + Rehydrator
Function:         _db_faults(), new _classify(exc), _counters, _snapshot, round_once
Current:          _db_faults() returns (OSError, psycopg.Error). psycopg.Error is the base
                  of EVERY database error, so LockNotAvailable and QueryCanceled become
                  ControlPlaneUnavailable -> db_down=True -> eligible for the 16 s
                  PG_GRACE_MS degraded ride-through. The docstring at :76-82 asserts the
                  opposite behaviour.
Required change:  classify explicitly.
                    outage  -> OSError, psycopg.OperationalError MINUS the timeout classes
                               (and minus LockNotAvailable) => ControlPlaneUnavailable
                    fault   -> LockNotAvailable, QueryCanceled, and every other
                               psycopg.Error => a NEW BoundedControlPlaneFault that does
                               NOT set db_down
                  round_once records a fault per kind and withholds the stamp; only a real
                  outage may reach _ride_through.
Reason:           G6. Without this a held lock is laundered into 16 s of unverified
                  "freshness" — H7 in a new form, and worse because it is silent.
Dependencies:     none (independent of C1-C3; ship it first).
Tests:            T-C4a injected LockNotAvailable -> stamped is None, db_down not set,
                  no degraded stamp; T-C4b injected QueryCanceled -> same;
                  T-C4c injected OSError -> ride-through still applies (R2-03 unchanged);
                  T-C4d injected ProgrammingError -> withholds, never ridden out
                  (the docstring's own claim, now pinned);
                  T-C4e live: a 60 s held lock on a path that does lock -> bounded failure.
```

### C5 — scope `guard=False` to the `STORE_AHEAD` repair only

```
File:             gateway_v2/state_control/valkey.py, publisher.py, rehydrate.py
Class/module:     StatePublisher (Protocol), ValkeyPublisher, MemoryStore, Rehydrator
Function:         publish_kind(..., *, allow_regress: bool = False); Rehydrator.repair
Current:          ValkeyPublisher.publish_kind calls _guarded(..., guard=False)
                  unconditionally, for all nine repair reasons. Necessary only for
                  STORE_AHEAD (where the store legitimately holds a higher feed_seq).
                  With two rehydrators a slower repair can regress a kind's manifest;
                  readers refuse a regress, so the kind goes unreadable until the next
                  round. MemoryStore.publish_kind has no _is_ahead check at all, so the
                  twin is more permissive than the product.
Required change:  add allow_regress to the Protocol (default False); ValkeyPublisher passes
                  guard=not allow_regress; Rehydrator.repair passes
                  allow_regress=(reason == STORE_AHEAD); MemoryStore gains the same guard.
Reason:           G7. A self-inflicted gap on the exact path the HA requirement introduces.
Dependencies:     none.
Tests:            T-C5a two rehydrators, the slower one's stale repair returns False and the
                  manifest does not regress; T-C5b STORE_AHEAD still repairs (epoch bump
                  then republish at a lower feed_seq must still land);
                  T-C5c MemoryStore and ValkeyPublisher agree (twin parity).
```

### C6 — make the per-round head read lock-free and read-only

```
File:             gateway_v2/state_control/pg.py
Class/module:     PostgresControlDB
Function:         counters()
Current:          counters() -> tx() -> PostgresTx.counters(kind, lock=False), a read-write
                  READ COMMITTED transaction whose first statement is
                  INSERT ... ON CONFLICT DO NOTHING. Runs 4x per round per rehydrator.
Required change:  read via _connect(read_only=True) with a bare SELECT; row is None ->
                  ZERO_COUNTERS. Row creation stays in PostgresTx.counters, used by the
                  writer, which is the only path needing the row to exist.
Reason:           G13. Removes the last way a lock can touch the availability-critical
                  loop, and stops a read path from writing. Hardening, not compliance —
                  the runbook requires lock-freedom of "the publish snapshot" specifically.
Dependencies:     C1 (so the read-only path carries the bounds).
Tests:            T-C6a counters() returns ZERO_COUNTERS for an absent row without creating
                  it; T-C6b writer still creates the row; T-C6c live under a held
                  FOR UPDATE, counters() returns promptly.
```

### C7 — the rehydrator service

```
File:             NEW gateway_v2/state_control/service.py
                  NEW gateway_v2/state_control/__main__.py
Class/module:     RehydratorService
Function:         from_env(), run(), _round(), serve_http()
Current:          no entrypoint. Rehydrator/PostgresControlDB are constructed only in tests.
                  gateway_v2/runtime/lifecycle.py:run_cli handles only --serve.
Required change:  build knobs from env (names per handoff §1.5 + §18 below), log
                  StateKnobs.warnings at WARNING, call PostgresControlDB.verify_bounds()
                  (C3) and fail start-up on mismatch, then loop:
                      summary = rehydrator.round_once(deep=knobs.is_deep_round(n))
                  with period AMF_REHYDRATE_PERIOD_MS; record metrics (C8); serve /healthz
                  and /metrics. Handle SIGTERM for a clean shutdown.
Reason:           G8. Without a process, requirements 3-5 of R2-04 are untestable.
Dependencies:     C1-C6 for correctness; C8 for metrics.
Tests:            T-C7a from_env() rejects an incoherent knob set at start-up (StateKnobs
                  already validates; assert it is not swallowed); T-C7b one loop iteration
                  calls round_once and records; T-C7c deep cadence follows is_deep_round;
                  T-C7d SIGTERM exits without a partial publish.
Note:             keep OUT of gateway_v2/runtime/lifecycle.py — state_control is a separate
                  tree under the import-linter contract and the lint gates.
```

### C8 — control-plane metrics

```
File:             NEW gateway_v2/state_control/metrics.py
Class/module:     RehydrationMetrics / RehydrationMetricsRecorder
Function:         observe_round, observe_repair, observe_fault, observe_publish, series()
Current:          nothing control-plane side. gateway_v2/runtime/state_metrics.py is the
                  gateway's view and must not be extended (SERIES_COUNT is a fixed
                  expression on purpose — R2-10).
Required change:  the surface in §16, same discipline: closed label set (kind, reason),
                  no tenant-derived labels, O(1) recording, a SERIES_COUNT constant.
Reason:           G12, and it is what makes the three dormant alerts evaluable.
Dependencies:     C7 for the HTTP surface; C4 for the fault classes to count.
Tests:            T-C8a series() key set is constant regardless of estate size;
                  T-C8b lock-timeout counter increments on the C4 fault path.
```

### C9 — `ok_publish_pending` age

```
File:             gateway_v2/state_control/schema.py (+ migration), writer.py, pg.py,
                  rehydrate.py
Class/module:     SCHEMA, StateWriter, PostgresTx, Rehydrator
Function:         new PostgresTx.mark_published / oldest_unpublished_age_s
Current:          StateWriter._publish_one and put_many return OK_PUBLISH_PENDING without
                  recording when. amf_state_publish_pending_oldest_seconds has no producer,
                  so StatePublishPending can never fire.
Required change:  add `published boolean NOT NULL DEFAULT false` to amf_state_record; set
                  true on a successful publish; rehydrator exports
                  now() - min(updated_at) WHERE NOT published. (Option (a) in §16.)
Reason:           G11 — named explicitly in the runbook's GW05b implementation bullets.
Dependencies:     C7, C8.
Tests:            T-C9a a failed publish leaves published=false and the age rises;
                  T-C9b a rehydrator repair flips it and the age returns to 0;
                  T-C9c a fresh estate reports 0, not a sentinel.
```

### C10 — package `state_control` into the image

```
File:             gateway_v2/Dockerfile
Function:         the single RUN with bind mounts (lines 16-18)
Current:          mounts gateway_v2/pyproject.toml, gateway_v2/gateway_v2,
                  shared/ai_mesh_shared. state_control/ is NOT copied, so the image cannot
                  run a rehydrator.
Required change:  add a bind mount + copy for gateway_v2/state_control. Preserve the
                  SOURCE_DATE_EPOCH touch ordering for reproducibility.
Reason:           G8. pyproject.toml already declares include=["state_control*"] and
                  psycopg[binary]>=3.2 — only the source copy is missing.
Dependencies:     none.
Tests:            T-C10a `python -c "import state_control"` inside the built image;
                  T-C10b the gateway-v2 CI reproducibility check still passes (two builds,
                  identical image id).
```

### C11 — two named rehydrator services

```
File:             docker-compose.yml (+ docker-compose.prod.yml overlay)
Function:         new services state-rehydrator-a / state-rehydrator-b
Current:          neither exists; gateway_v2 has no service in any compose file.
Required change:  the anchor + two services in §13.2, with distinct AMF_REHYDRATOR_ID and
                  AMF_DEPLOY_ZONE, idle-stop exemption labels, healthcheck, and
                  restart: unless-stopped (precedent: BACKSTOP CHG-0027).
Reason:           G9. On one host the zones are labels, not isolation — see §13.3 for the
                  cloud spec. Must not be described as zone redundancy.
Dependencies:     C7, C10.
Tests:            T-C11a `docker compose config` renders both services with the labels and
                  the healthcheck; T-C11b both appear as healthy; T-C11c both stamp and
                  verified_at stays monotonic.
```

### C12 — idle-stop exemption, asserted

```
File:             tests/gates/test_lgw05b_handoff.py (extend)
                  + whatever implements R2-24's watchdog
Current:          the rule exists as prose in the handoff §2.2 and in an alert annotation.
                  Nothing enforces it. GW05c left require_bounded_client uncalled with a
                  docstring explaining where it belonged and a whole card went past —
                  "prose does not fail a build".
Required change:  a test parsing the compose files that FAILS if either rehydrator service
                  lacks ai-mesh.idle-stop=exempt, or if the watchdog's exclusion list does
                  not name both. Keyed on IDENTITY, never on activity.
Reason:           G10, and the 09:06Z ledger incident. An O(1) round is nearly idle by
                  design, so any activity heuristic classifies a healthy rehydrator as idle
                  correctly.
Dependencies:     C11.
Tests:            is the test.
```

### C13 — Django `default` alias compliance *(separate commit, droppable)*

```
File:             control/ai_mesh_control/main_app/db_url.py
                  control/ai_mesh_control/main_app/analytics_db.py
                  control/ai_mesh_control/main_app/settings.py
Function:         postgres_database_from_url (add lock_timeout_ms),
                  analytics_set_local_timeout_sql (add lock_timeout)
Current:          the `analytics` alias has statement_timeout 5000 / idle_in_tx 30000 via
                  SET LOCAL. The `default` alias has NONE of the four. No lock_timeout
                  anywhere outside pg.py.
Required change:  add lock_timeout to the analytics SET LOCAL list; extend the same
                  treatment to `default`.
Reason:           the runbook says "every control-plane connection". NOT a fix for H7 —
                  there is no select_for_update anywhere in control/ and amf_state_counter
                  is touched only by state_control, so Django cannot hold the R2-04 lock.
                  Compliance + hygiene.
Dependencies:     none. Keep separate so it can be dropped without touching the defect fix.
Tests:            extend control/ai_mesh_control/tests/test_analytics_db_timeout.py.
```

### C14 — server-side idle-transaction default

```
File:             deploy/ (new SQL/runbook step) + docker-compose postgres command
Current:          nothing bounds a session we did not create (psql, migration, admin tool,
                  paused container).
Required change:  ALTER ROLE <app_role> SET idle_in_transaction_session_timeout = '5s';
                  document it as a deployment precondition.
Reason:           Scenario B's limitation. Defence in depth — the lock-free snapshot is the
                  actual fix.
Dependencies:     none.
Tests:            T-C14a live: a raw psql session idle in a transaction is terminated and
                  the lock released.
```

### Files deliberately NOT changed

`gateway_v2/domain/locks.py` (`FRESH_MS`, `PG_GRACE_MS` are owner-locked — consume, do not
re-decide); `gateway_v2/domain/state_knobs.py` (pure by design; the loader lives in the service);
`gateway_v2/runtime/state_stamp.py` and `state_metrics.py` (R2-03/GW06);
`gateway_v2/domain/posture.py`; `Rehydrator._stamp` / `_claimable` / `_ride_through` /
`_store_holds` (R2-03 semantics — C4 changes only *what reaches* them);
`deploy/observability/gw05b-state-freshness-alerts.yml` (the thresholds are part of the signed
design; C8/C9 give them producers).

---

## 18. Configuration changes

| Setting | Current | Proposed | Scope | Reason |
|---|---:|---:|---|---|
| `lock_timeout` | 2000 ms, **discarded by PgBouncer** | **250 ms** | rehydrator conns | Must be a small fraction of the 1000 ms round; a round must not be consumed by a lock wait |
| `lock_timeout` | 2000 ms, discarded | **2000 ms** (unchanged, now effective) | writer conns | Validated by the runbook's own figure: queued writer fails `LockNotAvailable` in ≈ 2.25 s |
| `statement_timeout` | 5000 ms, discarded | **800 ms** | rehydrator head read | 4 kinds × 800 ms = 3.2 s inside `FRESH_MS` 5000 ms with headroom; drill measured 2.0 s/round at 1000 ms vs 5.0 s at defaults |
| `statement_timeout` | 5000 ms, discarded | **5000 ms** (new separate knob) | rehydrator snapshot / deep verify | The deep pass is O(records): 2.40 s at 25k. 800 ms would abort it. Must not be one number |
| `statement_timeout` | 5000 ms, discarded | **5000 ms** (unchanged) | writer conns | `_commit_many` legitimately processes 25k records |
| `idle_in_transaction_session_timeout` | 5000 ms, discarded | **5000 ms** both roles | all control-plane conns | Releases an abandoned writer's `FOR UPDATE` in 5 s; no healthy write is at risk |
| `idle_in_transaction_session_timeout` | not set | **5s** via `ALTER ROLE` | Postgres role | Covers sessions we did not create — the only way to bound a foreign lock holder |
| `tcp_user_timeout` | 5000, **server GUC only** | **2000 ms client param** (rehydrator), 5000 ms (writer); keep the GUC | libpq connection | The stall is on the client's socket |
| `keepalives` / `_idle` / `_interval` / `_count` | **not set** | 1 / 2 s / 1 s / 3 | libpq connection | `tcp_user_timeout` only bounds unacknowledged data; keepalives create it |
| `connect_timeout` | 5 s | **2 s** (rehydrator), 5 s (writer) | libpq connection | Below the round period |
| Timeout application | startup `options` only | **`SET LOCAL` authoritative** + `options` belt-and-braces | all control-plane conns | PgBouncer `IGNORE_STARTUP_PARAMETERS` includes `options`; precedent `analytics_db.py` |
| Bounds verification | **none** | **`pg_settings` read-back, fatal at start-up** | rehydrator start-up | The absence of this check is why G1-G4 were invisible |
| Postgres isolation (publish) | REPEATABLE READ + READ ONLY | **unchanged** | rehydrator snapshot | Already correct; the R2-04 fix |
| Postgres isolation (head read) | READ COMMITTED, read-write, + `INSERT…ON CONFLICT` | **REPEATABLE READ + READ ONLY**, pure `SELECT` | rehydrator per-round | Last path by which a lock can touch the loop |
| `publish_kind` regress guard | always off (`guard=False`) | **on, except `STORE_AHEAD`** | publisher | Prevents a slower rehydrator regressing a manifest |
| DB fault classification | `(OSError, psycopg.Error)` | **outage vs bounded fault, explicit** | rehydrator | A lock timeout must not become a 16 s degraded ride-through |
| Rehydrator replicas | **0** | **2** (named services) | deployment | One is a single point of global non-enforcement |
| Availability zones | **n/a** | **2** (labels locally; two single-zone MIGs in cloud) | deployment | Zone-failure protection; a regional MIG may co-locate both |
| Idle-stop | no policy, no exemption | **identity-based exemption, CI-asserted** | deployment | R2-24 mandates a watchdog; ledger 09:06Z |
| `state_control` in image | **absent** | **copied** | `gateway_v2/Dockerfile` | The image cannot run a rehydrator today |
| `AMF_REHYDRATE_PERIOD_MS` | not read | 1000 | rehydrator | handoff §1.5 |
| `AMF_REHYDRATE_DEEP_EVERY` | not read | 60 | rehydrator | handoff §1.5 |
| `AMF_REHYDRATOR_ID` | not read (default `hostname:pid`) | per instance | rehydrator | Stamp `by` field; already distinct per container |
| `AMF_STATE_FRESH_MS` / `AMF_STATE_PG_GRACE_MS` | not read | 5000 / 16000 | both planes | **Owner-locked** in `domain/locks.py` — consume only |
| `AMF_STATE_PG_DSN` | **does not exist** | required, no default | rehydrator | Must point at the PRIMARY, never a replica |
| `AMF_STATE_HMAC_KEY` | not read | required, no default | both planes | Must match the gateways' or every stamp fails its signature (`StateStampInvalid`) |

New env knobs introduced by this plan, all prefixed `AMF_PG_` so they are greppable as one family:
`AMF_PG_REHYDRATOR_LOCK_TIMEOUT_MS`, `AMF_PG_REHYDRATOR_STATEMENT_TIMEOUT_MS`,
`AMF_PG_REHYDRATOR_SNAPSHOT_TIMEOUT_MS`, `AMF_PG_WRITER_LOCK_TIMEOUT_MS`,
`AMF_PG_WRITER_STATEMENT_TIMEOUT_MS`, `AMF_PG_IDLE_TX_TIMEOUT_MS`, `AMF_PG_TCP_USER_TIMEOUT_MS`,
`AMF_PG_CONNECT_TIMEOUT_S`, `AMF_PG_KEEPALIVES_IDLE_S`.

---

## 19. Acceptance test plan

Existing coverage to build on, not duplicate:

| Exists | Location | Covers |
|---|---|---|
| repair under a held `FOR UPDATE` < 5 s | `test_lgw05c_drills.py:453` | R2-04-1 partially — **holder is bounded by our own idle timeout after C1; see §3.2** |
| flush under a held `FOR UPDATE` restores + stamps < 5 s | `test_lgw05c_drills.py:819-846` | R2-04-1 partially |
| both rehydrators gone → fail closed at 5.0-5.3 s; recovery ≤ 0.2 s | same file, `test_drill_both_rehydrators_gone_…` | R2-04-7 partially |
| losing the stamp race is healthy | `tests/state_control/test_lgw05b_rehydrate_stamp.py` | R2-04-2 partially |
| 50 interleaved rounds, two rehydrators, `verified_at` monotonic | `tests/state_control/test_lgw05c_live_stack.py` | R2-04-2 partially |
| store-timeout bound refused if unset or too large | drills, `require_bounded_client` | adjacent |

### R2-04-1 — idle writer lock

Hold `FOR UPDATE` on `amf_state_counter WHERE kind='plan'` from a session **not** created by
`PostgresControlDB` (so `idle_in_transaction_session_timeout` does not release it), for **60 s** as
L05b-3 specifies, then `FLUSHALL`.

Verify: rehydration does not hang; all four kinds restored while the lock is held; a stamp is
minted; no global 503; `amf_rehydration_duration_seconds` within one period. Also assert a **queued
writer fails `LockNotAvailable` in ≈ 2.25 s** — the runbook's figure and currently unasserted.
Extends the drill at :819 from a one-round hold to 60 s and adds the writer assertion. **The holder
must not be created through `PostgresControlDB`** — after C1 it would be terminated at 5 s by its
own `idle_in_transaction_session_timeout`, making a 60 s hold impossible. Assert a restore bound
well under 5 s, so the result cannot be explained by the holder dying rather than by lock-freedom.
The drill at :453 carries exactly this ambiguity today and should be tightened alongside it.

### R2-04-2 — two concurrent rehydrators

Two `Rehydrator` instances, offset clocks, same Postgres and store, ≥ 50 interleaved rounds, with a
writer committing throughout.

Verify: both read the same generation; neither blocks the other (bounded round duration for both);
both publish safely; `verified_at` strictly monotonic in the store; no corruption; `put_stamp`
returns `False` for the loser without marking the round failed. **New assertion (C5):** force a
stale repair from the slower instance and assert the manifest does **not** regress — this is the
G7 regression test and it fails today.

### R2-04-3 — lock timeout

Force a lock on a path that does request one. Verify: `LockNotAvailable` within
`lock_timeout` + overhead; classified as a **bounded fault**, so `db_down` is not set, `_ride_through`
is not reached, and **no degraded stamp is minted**; the stamp is withheld;
`amf_rehydration_lock_timeout_total` increments; the next round retries; the previously published
state is unchanged. This is C4's primary gate and the single most important new test.

### R2-04-4 — statement timeout

`SELECT pg_sleep(…)` beyond `statement_timeout` (directly mirroring
`policy/analytics_timeout_probe.py`). Verify: `QueryCanceled`; the transaction is rolled back; the
connection is closed rather than reused; the rehydrator stays healthy and the next round succeeds;
no partial publish; `amf_rehydration_statement_timeout_total` increments. Add a negative control:
with `SET LOCAL` disabled and only `options` set, **through PgBouncer**, the sleep completes — the
test that demonstrates G2 was real.

### R2-04-5 — idle transaction timeout

Open a transaction via `PostgresControlDB`, take `FOR UPDATE`, go idle past
`idle_in_transaction_session_timeout`. Verify: Postgres terminates the session; the lock is
released; a queued writer proceeds; rehydration continues throughout. Second case with a raw
`psql`-equivalent session proving the `ALTER ROLE` default (C14) covers what `SET LOCAL` cannot.

### R2-04-6 — network timeout

`docker pause` the Postgres container (the drills already use this shape, and
`tightly_bounded_db()` exists for it). Verify: the round fails within
`tcp_user_timeout` + keepalive probe time rather than hanging; the rehydrator does not hang
indefinitely; classified as an **outage** (so the R2-03 ride-through correctly applies); recovery on
unpause within one period. Contrast with R2-04-3: the same symptom, a different classification —
that distinction is C4's whole point.

### R2-04-7 — two-zone deployment

Both services up; `SIGKILL` one. Verify: the survivor keeps stamping; freshness never lapses;
`StateRehydratorSingleInstance` fires after 5 m; restarting the dead one restores two without
intervention; `verified_at` never regresses across the transition.

### R2-04-8 — idle-stop protection

Idle the stack past the watchdog interval. Verify both rehydrators are still running; the exemption
label is present on both; the CI assertion (C12) fails when the label is removed from either.
Critically, assert the watchdog's decision is **label-driven**: a test in which both instances
report near-zero CPU and are still not selected.

### R2-04-9 — concurrent writer + rehydrator load

Continuous control-plane writes (including a `put_many` bulk) plus two rehydrators at the shipped
period, sustained.

Verify: no deadlocks; no indefinite waits; no global 503; no corrupted published state (every
kind's manifest counts agree with its records, and `verified_at` is monotonic); bounded rehydration
latency — `amf_rehydration_duration_seconds` p99 below `FRESH_MS − rehydrate_period_ms`.

### Mapping to the runbook's live tests

| Runbook | Covered by | Lane needed? |
|---|---|---|
| L05b-3 | R2-04-1, -3, -4, -5 | partially local; the ≈ 2.05 s flush bound and the 0.54-0.79 s figure are Cloud SQL's |
| L05b-6 | R2-04-7 | local per process; "global" needs a fleet |
| L05b-4 | R2-04-6 (shape only) | **yes** — Cloud SQL failover at 60% load |
| G-14 | — | **yes** — Cloud SQL failover + FLUSHALL at +3 s, ×3 |

Local work makes R2-04 *ready*; it cannot *close* it. Exit authority sits with the lane, and the
handoff §5 is right that GW05b's lane is materially cheaper than GW05c's: one gateway VM, Cloud SQL
with a failover replica, Memorystore, two small rehydrator VMs, one loadgen — no L4 GPU, no 25k
estate.

Every local bound uses an **injected clock**: the faults are real (a stopped database, a paused
container, a held row lock) but the waiting is not. Claim no latency from local runs.

---

## 20. R2-03 dependency analysis

**The runbook does not separate them.** §0.4's card is titled *"GW05b — State freshness and HA
re-hydration (R2-03, R2-04)"*. One card, one owner, one set of acceptance tests. So there is no
boundary to negotiate — only a division of labour between what is already done and what is not.

| Behaviour | Owner | State |
|---|---|---|
| Signed freshness stamp; floor from the stamp, never zero | R2-03 | **done** (`state_sig.py`, `rehydrate.py._stamp`) |
| Trust only stamps from rounds that started after the process | R2-03 | **done** |
| Per-kind fail-closed past `FRESH_MS` | R2-03 | **done** (`state_stamp.py`, uncalled pending GW06) |
| Postgres ride-through up to `PG_GRACE_MS` | R2-03 | **done** (`_ride_through`, `_store_holds`) |
| Withhold the stamp unless every kind verified | R2-03 | **done** (`_claimable`) |
| Four session timeouts, effective | **R2-04** | **gap** — G1-G4 |
| Lock-free `REPEATABLE READ` publish snapshot | **R2-04** | **done** — G5 |
| Lock/statement timeout ≠ Postgres outage | **R2-04** | **gap** — G6 |
| Concurrent publish cannot regress | **R2-04** | **gap** — G7 |
| ≥ 2 rehydrators, ≥ 2 zones, idle-stop exempt | **R2-04** | **gap** — G8-G10 |
| `ok_publish_pending` alarm | **R2-04** | **gap** — G11 |
| Rehydrator process, env loader, metrics | **shared** | **gap** — R2-04 is the first consumer that cannot proceed without them |
| `/readyz` calling `state_ready`; posture rendering | R2-03 / **GW06** | handoff §1.1, §1.3 — out of scope here |

**Shared changes:** C7 (service), C8 (metrics), C10 (image). They are R2-04's because requirements
3-5 are untestable without them, and they incidentally make R2-03's dormant alerts evaluable.

**Does order matter?** No. R2-04 is **not** blocked on R2-03: `_stamp` works, `_ride_through`
works, and the lock drill passes. The one genuine coupling runs the other way — **C4 changes what
reaches R2-03's ride-through**, narrowing its input from "any database error" to "a real outage".
That is a correction of R2-03's input, not a change to its logic, and T-C4c pins the R2-03 path as
unchanged.

**What R2-04 must not touch:** `FRESH_MS`, `PG_GRACE_MS`, `_ride_through`, `_store_holds`,
`_claimable`'s withhold rule, the degraded-stamp semantics, `StampView`, or the posture codes.
Any change there is a silent change to R2-03 and is forbidden by the brief.

---

## 21. Implementation phases

Ordered so each phase is independently shippable and revertible, and so the highest-severity
defect lands first.

**Phase 0 — fault classification (C4).** Independent of everything else; the most serious defect;
~40 lines plus tests. Gate: T-C4a-d offline, full `state_control` suite, `mypy --strict`,
import-linter, ruff. **Ship this alone, first.**

**Phase 1 — connection hardening (C1, C2, C3, C14).** `SET LOCAL` authoritative, client-side
`tcp_user_timeout` + keepalives, the read-back assertion, the `ALTER ROLE` deployment step. Gate:
T-C1a-c, T-C2a-b, T-C3a-c, T-C14a, plus the **negative control** — live through PgBouncer with
`SET LOCAL` disabled, the read-back must FAIL. That negative control is the evidence G1-G4 were
real, so it is the artefact to keep.

**Phase 2 — lock-free snapshot completion (C6) and publish guard (C5).** Both small, both
behaviour-preserving in the healthy path. Gate: T-C5a-c, T-C6a-c, plus both existing lock drills
(:453 and :819) unchanged and still green.

**Phase 3 — the service (C7, C8, C10).** Entrypoint, env loader, metrics, image. Gate: T-C7a-d,
T-C8a-b, T-C10a-b; the gateway-v2 CI reproducibility check must still pass; `python -m state_control`
runs a round against the drill containers.

**Phase 4 — `ok_publish_pending` (C9).** Migration plus the exporter. Separate phase because it is
the only schema change and therefore the only one with a migration rollback. Gate: T-C9a-c; verify
`StatePublishPending` evaluates against a live scrape.

**Phase 5 — HA deployment (C11) and idle-stop protection (C12).** Two named services, labels,
healthchecks, the CI assertion. Gate: T-C11a-c, T-C12, R2-04-7, R2-04-8.

**Phase 6 — Django compliance (C13).** Deliberately last and deliberately separable: it is
compliance with the runbook's wording, not a fix for the defect, so it must be droppable without
touching anything above. Gate: `control/ai_mesh_control/tests/test_analytics_db_timeout.py`
extended.

**Phase 7 — full R2-04 acceptance suite.** R2-04-1 through R2-04-9 against the drill containers,
via an extended `scripts/gw05c_local_drills.sh` (or a sibling `scripts/r2_04_drills.sh`) writing an
evidence verdict in the established shape. Record what is local and what is lane-gated.

**Phase 8 — lane verification.** L05b-3, L05b-4, L05b-6 and G-14 on the cloud lane; compare against
the runbook's figures (0.54-0.79 s restore, 2.25 s writer fail-fast, ≈ 2.05 s flush bound) and
record deviations rather than restating the reference numbers as ours.

Documentation duties, per `AGENTS.md`: log the change set in `docs/pipeline/CHANGELOG.md`'s
equivalent for this lane and add a one-line pointer where the GW05b handoff records R2-04 status —
specifically, **correct handoff §4**, which currently reports all four timeouts as "done".

---

## 22. Rollback plan

Every phase is revertible independently. The ordering above exists so that the risky parts are last.

| Phase | Rollback | Risk of rolling back |
|---|---|---|
| 0 — classification | revert the commit; `_db_faults` returns to `(OSError, psycopg.Error)` | **Reintroduces the 16 s degraded ride-through on a lock timeout.** Bounded and silent — the worst property. Do not roll back without recording it. |
| 1 — hardening | revert; `options`-only returns | **Reintroduces unbounded waits in any PgBouncer topology.** This is the closest thing to reinstating H7 and must be flagged explicitly. The `ALTER ROLE` step is independently revertible with `ALTER ROLE … RESET …`. |
| 2 — snapshot + guard | revert | `counters()` returns to read-write (benign); `publish_kind` returns to `guard=False` — reintroduces the two-rehydrator regress window. Mitigate by running one rehydrator until re-applied. |
| 3 — service | scale both services to 0, or revert the compose change | Returns to today's state: no rehydrator, so `ok_publish_pending` writes are never carried and the fleet fails closed at `FRESH_MS`. **This is a global-availability rollback — never do it as a routine step.** Prefer rolling back to the previous *image* with the service still running. |
| 4 — pending age | revert the exporter; leave the column | The column is additive with a default, so it is safe to leave. Rolling the migration back is possible but unnecessary — prefer leaving it and reverting only code. `StatePublishPending` returns to dormant. |
| 5 — deployment | `docker compose up -d --scale state-rehydrator-b=0` for a single-instance fallback | **Reintroduces the single point of global non-enforcement** — the 09:06Z ledger incident's precondition. Acceptable briefly and with the alarm acknowledged; never silently. |
| 6 — Django | revert; it touches nothing R2-04 depends on | none |

**Configuration-only rollbacks**, available without a deploy and preferred as the first response:

- Timeout values: every one is an env knob after Phase 1, so a too-aggressive bound is corrected by
  restarting with a larger value — **not** by reverting the mechanism. If 800 ms proves too tight
  for the head read, raise it; do not disable `SET LOCAL`.
- Deep cadence: raise `AMF_REHYDRATE_DEEP_EVERY` if the deep pass is implicated.
- Break-glass: `AMF_STATE_FRESH_MS=0` disables fail-closed entirely. It **reinstates R2-03** (a
  process on a lagging replica can serve revoked or killed state), is loud at start-up via
  `StateKnobs.warnings`, and is visible as `amf_state_stamp_fresh`. Last resort, never a default,
  and the handoff carries an open owner question about removing it before v3 sign-off.

**The standing rule for this card:** no rollback may silently restore unbounded lock waiting.
Phases 0, 1 and 2 all carry that risk, and each revert must be accompanied by an explicit note that
R2-04's defect is live again.

---

## 23. Risks

**Introduced by this plan.**

- **An over-tight `statement_timeout` aborts legitimate work.** 800 ms is derived from the freshness
  arithmetic, but the deep pass is O(records) and the handoff records 2.40 s first-start catch-up at
  25k tenants. Mitigated by the separate `snapshot_timeout_ms` (§18) — if the implementer collapses
  these into one number, the deep round will fail on any large estate. This is the single most
  likely implementation mistake.
- ~~`SET LOCAL` as the first statement changes when the `REPEATABLE READ` snapshot is taken.~~
  **Retired — this was wrong.** `SET` takes no transaction snapshot, so the repeatable-read snapshot
  is still taken at the first reading statement. Verified by the 618-test live suite, whose existing
  snapshot-consistency assertions all hold unchanged. The residual constraint is only that the
  `SET LOCAL` must be in the same transaction.
- **`tcp_user_timeout` is Linux-only.** Passing it on another platform raises at connect time. Guard
  it if local macOS development is expected.
- **The read-back assertion will fail on first deployment.** That is the point — it is detecting a
  real defect — but it means Phase 1 cannot be deployed without also resolving the PgBouncer path.
  Sequence the rollout so the assertion lands with the `SET LOCAL` that satisfies it.
- **`allow_regress` default `False` could block a legitimate `STORE_AHEAD` repair** if the reason
  plumbing is wrong. T-C5b is the guard; without it a divergent store would never heal, which is
  worse than the regress window being fixed.
- **Two rehydrators double the Postgres connection churn** — eight short-lived connections per
  second against `PGBOUNCER_MAX_DB_CONNECTIONS=80` and `POSTGRES_MAX_CONNECTIONS=400`. Small, but
  `PostgresConnectionsHigh` already alarms at 80% and should be watched during Phase 5.

**Carried forward from R2-03/GW05b**, listed so this plan is not read as resolving them: NTP
coupling between the gateway's clock and the rehydrator's (`StateClockSkew` alarms on the gauge);
`PG_GRACE_MS` 16 s against a measured 15.5 s loaded Cloud SQL gap — 3% headroom; the gap between
`connect_timeout` succeeding and the first statement, which C2's keepalives narrow but do not close;
the "verified" predicate attesting versions, positions and counts rather than contents; unmeasured
stamp age at 50k records. All are documented in the handoff §6 and none is R2-04's to fix.

**Risks this plan does not address, by design.** Both rehydrators down is accepted (L05b-6: global
fail-closed at the bound). A Postgres primary-zone failure reduces to Scenario E. `fresh_ms=0`
remains an escape hatch.

---

## 24. Definition of done

**Code.**

- [ ] A lock timeout and a statement timeout are classified as bounded faults: `db_down` is not
      set, `_ride_through` is not reached, no degraded stamp is minted, the stamp is withheld
      (C4; T-C4a-d).
- [ ] A genuine I/O outage still reaches R2-03's ride-through unchanged (T-C4c).
- [ ] All four timeouts are applied by `SET LOCAL` and verified by a `pg_settings` read-back that
      fails start-up on mismatch (C1, C3).
- [ ] `tcp_user_timeout` is a client-side libpq parameter with keepalives configured (C2).
- [ ] The negative control is recorded: through PgBouncer with `SET LOCAL` disabled, the read-back
      FAILS and a `pg_sleep` beyond the deadline completes.
- [ ] The per-round head read takes no lock and performs no write (C6).
- [ ] `publish_kind` refuses a regress except on the `STORE_AHEAD` epoch repair, in both the Valkey
      implementation and the in-memory twin (C5).
- [ ] The publish snapshot remains `REPEATABLE READ, READ ONLY` with zero lock requests — verified,
      not assumed (G5).
- [ ] `state_control` carries no `FOR SHARE`, and `FOR UPDATE` only on the three writer call sites.

**Process and deployment.**

- [ ] `python -m state_control` runs a rehydrator with env-driven knobs and logs
      `StateKnobs.warnings` at start-up (C7).
- [ ] `state_control` is in the image, and the reproducibility check still passes (C10).
- [ ] Two named services in two declared zones, both healthy, both stamping, `verified_at`
      monotonic (C11).
- [ ] Killing one leaves the survivor stamping with no freshness lapse (R2-04-7).
- [ ] Both carry an identity-based idle-stop exemption, asserted by a CI test that fails when the
      label is removed (C12; R2-04-8).
- [ ] The `ALTER ROLE` idle-transaction default is applied and documented (C14).

**Observability.**

- [ ] `job="amf-state-rehydrator"` scrapes two instances, so
      `StateRehydratorSingleInstance` evaluates (C8).
- [ ] `amf_rehydration_lock_timeout_total` exists and increments on the C4 path — R2-04's own
      detector.
- [ ] `amf_state_publish_pending_oldest_seconds` has a producer, so `StatePublishPending`
      evaluates (C9).
- [ ] `amf_state_stamp_withheld_total` has a producer, so `StateStampWithheld` evaluates.
- [ ] No metric carries a tenant-derived label; `SERIES_COUNT` is a fixed expression (R2-10).

**Tests and gates.**

- [ ] R2-04-1 … R2-04-9 all pass locally, including the 60 s hold and the ≈ 2.25 s writer
      fail-fast assertion.
- [ ] The existing drill at `test_lgw05c_drills.py:819` still passes unchanged.
- [ ] Full `gateway_v2` + `state_control` suite green offline and with real Postgres + Valkey.
- [ ] `mypy --strict`, import-linter, ruff, and all five AST gates clean on both trees.
- [ ] Evidence written under `docs/plans/evidence/<date>-r2-04/` in the established verdict shape,
      stating explicitly which bounds use an injected clock and claiming no latency from local runs.

**Documentation.**

- [ ] `docs/plans/2026-10-08-gw05b-handoff.md` §4 **corrected**: the four timeouts are not "done"
      until they survive PgBouncer and are verified.
- [ ] This plan's change set logged per `AGENTS.md`, with a one-line pointer.
- [ ] `deploy/observability/gw05b-state-freshness-alerts.yml` moved into `rule_files` once the
      producers exist, or its dormancy note updated to say what remains.

**Lane (exit authority — cannot be closed locally).**

- [ ] L05b-3 on Cloud SQL: every kind restored within the declared flush bound while the lock is
      held; queued writer fails fast.
- [ ] L05b-4: Cloud SQL failover at 60% load, 0 × 503 for valid tenants.
- [ ] L05b-6: both rehydrators killed → global fail-closed at the bound; recovery ≤ 1 s after one
      returns.
- [ ] G-14: Cloud SQL failover + FLUSHALL at +3 s, ×3 → 0 violations.

**The twelve questions from the brief, answered.**

1. *Which transaction causes the stall?* `StateWriter._commit` / `_commit_many` /
   `repair_epoch` via `tx.counters(kind, lock=True)` → `pg.py:95` `FOR UPDATE` on
   `amf_state_counter`.
2. *Which rehydration query waits on it?* **None, today** — `snapshot()` takes no lock. RC2's
   `FOR SHARE` read is gone. The residual risks are the `INSERT…ON CONFLICT` head read (C6) and the
   misclassification of a lock timeout (C4).
3. *Where are connections created?* `PostgresControlDB._connect`, `pg.py:211-222`; one per
   operation, no pool. PgBouncer is the pool for every other plane.
4. *Where should the four timeouts be configured?* `pg.py` — `options` retained, `SET LOCAL`
   authoritative in `tx()` and `snapshot()`, `tcp_user_timeout` as a client connect parameter, all
   verified by a `pg_settings` read-back at start-up.
5. *How does the lock-free snapshot work?* §10 — `REPEATABLE READ, READ ONLY`, bare `SELECT` on the
   counter row, records and engaged keys in the same transaction, fully materialised in memory, then
   `ROLLBACK`, then sign and publish with the manifest last.
6. *How do concurrent rehydrators publish safely?* §12 — no leader election; `put_stamp`
   WATCH-guarded and monotonic in `verified_at`; manifest written last and guarded against regress,
   with `guard=False` scoped to the `STORE_AHEAD` repair.
7. *How do two rehydrators span two zones?* §13 — two named services, two single-zone MIGs in the
   Cloud SQL primary's region, `min = max = 1`, one scrape job.
8. *How are idle-stop policies prevented from removing them?* §14 — identity-based exemption labels
   plus `deletionProtection` plus `min = max = 1`, asserted in CI, never activity-based.
9. *What happens in each failure scenario?* §15, A through G.
10. *Which files change?* §17, C1 through C14.
11. *What proves R2-04 is fixed?* §19, R2-04-1 through R2-04-9, plus the lane tests in §19's
    mapping table.
12. *What is the order and rollback?* §21 (nine phases, Phase 0 first) and §22 (per-phase, with the
    standing rule that no rollback may silently restore unbounded lock waiting).
