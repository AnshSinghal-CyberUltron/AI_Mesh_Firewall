# GW05b — State freshness and HA re-hydration (R2-03, R2-04)

Investigation and implementation plan. **No product code was changed by this document.**

Date: 2026-10-07 · Branch: `suraj-revamp` · Card: GW05b · Runbook: `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` line 185

---

## 1. Scope and non-goals

GW05b closes R2-03 and the gateway-side half of R2-04. Its one objective, from the card: *no
process ever serves revoked, killed or stale-version state because of replica lag, a missing
publish or a stuck re-hydrator; and store or Postgres blips do not become global outages.*

In scope:

- A signed freshness stamp, written by the re-hydrator once per round.
- A per-kind version/cursor floor for every gateway process, derived from that stamp instead of
  from zero.
- Per-kind fail-closed behaviour when the newest stamp a process has seen is older than
  `FRESH_MS`.
- A bounded Postgres-outage grace so a Cloud SQL failover does not become a fleet-wide 503.
- Deployment rules for running two or more re-hydrators.

Explicitly **not** in scope:

- Rewriting the state system. GW05c already landed the propagation redesign; this card adds one
  key, one dataclass, one view object and three call sites.
- New infrastructure. No new datastore, no new service, no new network path. The stamp is one
  Valkey string in a key that is already declared.
- Schema changes. Section 23 explains why none are needed.
- Re-deciding `FRESH_MS` or `PG_GRACE_MS`. Both are owner-locked in
  `gateway_v2/gateway_v2/domain/locks.py`. This card consumes them.
- Any other R2 item. R2-09 (budget refills off the request path), R2-14 (store RTO) and R2-18
  (post-heal recovery) are GW06's; R2-10/R2-11 are GW14d's.

---

## 2. The defect, stated twice

**R2-03 / SP1 — a fresh process has no floor.** The per-process version floor lives in RAM and
starts at `ZERO`. A manifest is refused for being *older than a version this process already
applied* — so a process that has applied nothing refuses nothing. After a failover to a lagging
store replica, a newly started worker verifies the older, genuinely signed manifest as valid and
serves pre-write state: revoked keys admitted, killed orgs admitted. Measured in RC2: 359 of 365
admits in 30 s.

**R2-03 / SP2 — an unpublished committed write never ages.** The writer commits to Postgres and
then publishes to the store. If the publish fails, the write is `ok_publish_pending` and only the
re-hydrator will republish it. Gateways verify the store *against itself* — the older manifest
verifies, refreshes succeed, the kill switch reports `ok`, nothing ages. With no re-hydrator
running, the gap is unbounded. Measured in RC2: 60 s with no enforcement and no 503.

The common root is the same in both: **a gateway's notion of "fresh" is "I read the store
recently", not "the store has been compared with the durable truth recently".** The fix is to make
the second thing observable to a gateway, cheaply, and to fail closed on its absence.

---

## 3. Evidence inventory, and its limits

| Claim | Evidence | Strength |
|---|---|---|
| SP1 reproduces | `docs/plans/evidence/2026-09-24-runbook-v3-round2/evidence/reviewer-state-propagation/EV/r2pass1/repro_regressed_store_new_process.out` — existing process refused `ks manifest 1.2 is older than 1.3`; new process accepted 1.2, `org_K_killed=false`, key R ADMITTED | Reproduced, RC2 |
| SP2 reproduces | `.../EV/r2pass1/repro_publish_pending_no_rehydrator.out` — 6 refresh rounds, `ks_state=ok`, key R ADMITTED, store ks 1.2 / key 1.1 vs Postgres 1.3 / 1.2 | Reproduced, RC2 |
| 359/365 admits in 30 s | reviewer `r2-pass1.md` SP1 (line 16) | Measured, RC2 |
| 60 s unbounded gap | reviewer `r2-pass1.md` SP2 (line 62) | Measured, RC2 |
| Fix costs ≈ 7.4 s of global 503 on a 10 s Postgres freeze | `patches/rc3-state-p0-README.md` | Measured, reference patch |
| Both re-hydrators gone → global 503 in 2.04–2.97 s; recovery 0.27–0.43 s | `rc3-state-p0-README.md` | Measured, reference patch |
| Store frozen 3.8 s → ≈1.2 s of global 503 | `rc3-state-p0-README.md` | Measured, reference patch |
| New process adds ≤ 1 s to ready | `rc3-state-p0-README.md` | Measured, reference patch |
| Loaded Cloud SQL gap 15.5 s (the basis for `PG_GRACE_MS` 16 s) | runbook R2-03 row, §0.2 | Measured, round 2 |
| Kinds restored in 0.54–0.79 s under a held `FOR UPDATE`; queued writer failed in 2.25 s | `rc3-state-p0-README.md`, runbook L05b-3 | Measured, reference patch |
| Ledger incident: the idle watchdog stopped the VM hosting the single re-hydrator | round-2 ledger 09:06Z, `rv-r2state-cp-1` | Observed |

Limits worth stating plainly:

- Every "measured" number above is from RC2 or the rc3 reference patch, on round-2's estate. That
  estate was torn down on 2026-09-24. **Nothing in this card has been measured on `gateway_v2`.**
- SP3 regime (d) — in-flight streams never re-check the kill switch — is a *different* defect
  (C24/GW12). A freshness stamp does not touch it, and this plan does not claim it does.
- The reference patch's own README lists open risks it did not close: clock/NTP dependence; old
  processes on a lagging replica still bounded only by `FRESH_MS`; a Postgres *hang* not covered by
  `tcp_user_timeout`; stamp age at 50k records; and no pub/sub nudge for the stamp. Sections 15,
  18, 20 and 29 carry each of those forward rather than inheriting them silently.

---

## 4. SP1 mechanism: the exact RC2 sites

Floors are per-process RAM, initialised to `ZERO`:

| Site | What |
|---|---|
| `rvproto/admit/state_v2.py:38` | `VersionedIdentity.version = ZERO` |
| `rvproto/admit/state_v2.py:85` | `VersionedKillSwitch.version = ZERO` |
| `rvproto/plan/snapshot_v2.py:46` | the plan snapshot's applied version, same shape |

The regress refusal is one argument:

```
# rvproto/runtime/versioned.py:100-114
verify_meta(self.k, kind, meta_raw, not_before=self.version)
```

`not_before` is the *only* thing standing between a worker and an older manifest. A manifest that
is correctly signed, internally consistent, and simply **old** is indistinguishable from a current
one on its own. The floor is the whole mechanism — and it is per process.

So the sequence is:

1. Write lands: key R revoked, org K killed. Postgres and the store primary both at ks 1.3.
2. The store fails over to a replica that is behind, at ks 1.2.
3. An existing worker, floor at 1.3, refuses 1.2 → `StoreDataUnavailable` → fails closed. Correct.
4. A worker started at step 3 has floor `ZERO`. It verifies 1.2, applies it, and `org_K_killed`
   returns `false`. Key R is admitted.

Step 4 is the defect. Note that it needs nothing exotic: an autoscaler adding a worker, a crash
respawn, or a rolling deploy all produce a fresh process. Zone loss is the natural trigger for
step 2, which is exactly when the fleet is also most likely to be creating processes.

Exposure bound in RC2, **conditional on the re-hydrator being up**:

```
RV_REHYDRATE_PERIOD_MS  1000    rvproto/runtime/config_v2.py:62
stale grace               250    rvproto/control/rehydrate.py:90-92
one gateway refresh      ≤500    RV_KS_REFRESH_MS
                        ------
                        ≈1.75 s
```

With the re-hydrator down, the bound is unbounded — which is SP2.

---

## 5. SP2 mechanism: the exact RC2 sites

The writer's two-phase shape:

```
# rvproto/control/writer.py:93-97, 110-119
commit to Postgres          -> durable
publish to the store        -> may fail
on publish failure          -> status "ok_publish_pending"
```

`ok_publish_pending` is honest at the control plane. The problem is that nothing on the data plane
can see it. The gateway's refresh reads the store's manifest and compares it to the gateway's own
applied version:

```
# rvproto/admit/state_v2.py:103-121
meta = verify_meta(self.k, "ks", meta_raw, not_before=self.version)
...
self.last_ok_ns = time.perf_counter_ns()
```

The older manifest verifies (it is genuine and not below the floor), `last_ok_ns` advances, and
`state()` returns `ok`. The staleness ceiling never trips, because the ceiling measures *time since
this process last read the store*, not *time since the store was known to match Postgres*. From the
gateway's point of view the system is perfectly healthy.

The only component that republishes a pending write is the re-hydrator
(`rvproto/control/rehydrate.py:84-116`). Round-2 deployments ran **one**, and the 09:06Z ledger
incident is the whole argument for section 19: an idle watchdog stopped `rv-r2state-cp-1`, which
happened to host it. Running two is already safe in RC2 — publishes are WATCH-guarded, budget
repairs are compare-and-set, and F-C36-RACE ran with two — so this half is a deployment rule, not
code.

---

## 6. Which replica actually lags

**The lagging replica in SP1 is the Valkey store replica, not a PostgreSQL replica.** This matters
enough to state before any design, because the task framing ("lagging database replica",
"PostgreSQL read routing") points at the wrong tier.

Verified by grep on the current tree:

```
$ grep -rn 'psycopg\|postgres\|PostgresControlDB' gateway_v2/gateway_v2/
(no matches)
```

The gateway package has no Postgres dependency at all. Only `gateway_v2/state_control/` opens a
Postgres connection, and it does so through a single DSN. The data plane reads exactly one durable
surface: the Valkey store.

So the topology in play is:

```
Postgres (truth)  <--read--  state_control (writer + re-hydrator)   one DSN, primary only
                                    |
                                 publish
                                    v
Valkey primary  --replicate-->  Valkey replica     <-- THIS is what lags
     ^                               ^
     |  read                         |  read after failover
  gateway workers ------------------- '
```

SP1 was reproduced by modelling the store failover as a `DUMP`/`RESTORE` of the store at an older
generation — i.e. the Valkey tier. R2-13 records that Memorystore for Valkey cannot be failed over
manually, which is why the drill is a flush or a restore rather than a real failover, and why
GW05c's local drills already use `docker pause` and `FLUSHALL`.

---

## 7. Postgres read routing and replica lag: why GW05b does not touch it

Taking the question on its own terms and answering it rather than deflecting:

- **Does the gateway read Postgres?** No. Section 6. There is therefore no gateway-side read
  routing to change, and no gateway-visible Postgres replica lag.
- **Does the control plane read a replica?** No. `state_control/pg.py` takes one DSN. The writer
  needs the primary (it writes), and the re-hydrator's `snapshot` must see the same truth the
  writer committed, so routing it to a replica would reintroduce SP1 one tier up: the re-hydrator
  would stamp "verified" against a view that is behind the writer, and gateways would trust it.
- **Should the re-hydrator ever read a replica?** No, and this should be written down as a
  prohibition rather than left as an omission. The stamp is an assertion about durable truth. It
  may only be issued from a connection that can see every committed write. Adding a read-replica
  DSN would be the one change in this area capable of *creating* the defect the card exists to
  close.

Concretely, the plan's position: **no change to Postgres read routing; add a comment in
`state_control/pg.py` recording that a replica DSN is forbidden for the re-hydrator, so a later
cost-optimisation does not silently introduce it.** That is a docstring, not a schema or
infrastructure change.

The one genuine Postgres concern in this card is *availability*, not *consistency*: a Cloud SQL
failover takes 11–16 s, during which the re-hydrator cannot read truth and therefore cannot honestly
stamp. Section 18 handles that with `PG_GRACE_MS`.

---

## 8. The defect as it exists in `gateway_v2` today

This is the finding that matters most for scheduling: **GW05c faithfully reproduced R2-03.** The
propagation redesign fixed the cost shape and left the freshness hole exactly where RC2 had it.

```python
# gateway_v2/gateway_v2/runtime/state_feed.py:88
START = Cursor(version=ZERO, feed_seq=0)
"""A reader with no stamp and nothing applied."""
```

```python
# gateway_v2/gateway_v2/runtime/state_task.py:100
self._cursors: dict[StateKind, Cursor] = {kind: START for kind in appliers}
```

Every process starts every kind at `(ZERO, 0)`. `FeedReader._read_head` then calls:

```python
decode_manifest(
    self._secret, kind, head.manifest_raw,
    not_before=cursor.version,
    feed_floor=cursor.feed_seq,
)
```

With `cursor == START`, `not_before=ZERO` and `feed_floor=0` — both checks are vacuous. A fresh
worker on a lagging replica accepts the older manifest, reads the older index, applies the older
records, and `KillSwitchSnapshot.state()` returns `OK`. That is step 4 of section 4, in the new
tree.

The seam is already named and already written:

```python
# gateway_v2/gateway_v2/runtime/state_task.py:105
def raise_floor(self, kind: StateKind, floor: Cursor) -> None:
    """Raise a cursor without applying anything. GW05b's freshness stamp calls this.

    A process must not start from zero on a lagging replica: the floor is the higher of what
    it has applied and what a stamp verified. Floors only rise.
    """
```

Verified callers of `raise_floor` in the whole repository:

```
tests/runtime/test_lgw05c_sync.py:378
tests/runtime/test_lgw05c_sync.py:381
tests/runtime/test_lgw05c_sync.py:391
tests/state_control/test_lgw05c_drills.py:226
```

**Nothing in product code calls it.** The function exists, is tested, and is dead. GW05b's job is
to give it a caller.

Likewise `StoreKeys.stamp` is declared and unused:

```python
# gateway_v2/gateway_v2/runtime/store_keys.py
@property
def stamp(self) -> str:
    """GW05b's signed freshness stamp. Raises a reader's cursor floor above zero."""
    return f"{self.namespace}:stamp"
```

Nothing writes `{rv2}:stamp` and nothing reads it. The only other references in the repository are
a key-layout assertion in `tests/runtime/test_lgw05c_feed.py:506,513` (`keys.stamp ==
"{rv2}:stamp"`, and its inclusion in the one-slot hash-tag check) — so the name and the slot are
already pinned by a test, which is convenient: GW05b cannot silently relocate the key.

And `state_control/rehydrate.py` writes no stamp: `Rehydrator.round_once` returns a `RoundSummary`
with `repairs` / `healthy` / `errors` and stops there. `RoundSummary.ok` already means *every kind
succeeded this round*, which is precisely the stamping condition — it is computed and then
discarded.

---

## 9. Seams GW05c already left for this card

GW05b is unusually cheap because the previous card was written with it in view. What already exists:

| Seam | Where | What GW05b does with it |
|---|---|---|
| `Cursor(version, feed_seq)` | `runtime/state_feed.py` | the stamp carries one of these per kind |
| `raise_floor(kind, floor)` | `runtime/state_task.py:105` | gets its first product caller |
| `decode_manifest(..., not_before, feed_floor)` | `runtime/state_sig.py:255` | already enforces both floor dimensions |
| `StoreKeys.stamp` → `{rv2}:stamp` | `runtime/store_keys.py` | the key, already in the hash-tagged namespace |
| `RoundSummary.ok` | `state_control/rehydrate.py` | the "every kind verified" predicate |
| `KindCounters(version, feed_seq, count, on_count)` | `state_control/db.py:28` | the per-kind versions the stamp attests |
| `_mac`, `canonical_body`, domain-tagged signatures | `runtime/state_sig.py` | the stamp signs the same way records and manifests do |
| `FRESH_MS = 5_000`, `PG_GRACE_MS = 16_000` | `domain/locks.py` | the two bounds, already owner-locked |
| `RoundReport` per kind | `runtime/state_task.py` | per-kind fail-closed decisions attach here |
| `StateMetricsRecorder`, fixed `SERIES_COUNT` | `runtime/state_metrics.py` | stamp gauges extend the fixed budget |
| `require_bounded_client(client, below_s=...)` | `runtime/store_valkey.py:69` | must finally be *called* (section 22) |

The honest summary: **GW05b is "write the stamp, read the stamp, raise the floor from it, fail
closed per kind" — four verbs against seams that already exist.** It is not a redesign, and the
plan should not be allowed to grow into one.

### Two existing drills this card inherits

GW05c left two drills in `tests/state_control/test_lgw05c_drills.py` that are about GW05b directly,
and they divide the work exactly along the I2/I3 line of section 11.

`test_drill_a_regressed_store_is_refused` (line 217) — docstring: *"The sp1 CLASS without a replica:
a store generation older than the applied floor."* It drains a worker to position 10, calls
`raise_floor` **by hand**, republishes the kind at `(1,4)`, and asserts the round is refused. So:

- It proves **I2** — a floor, once raised, is honoured. The refusal machinery works.
- It does **not** prove **I3**, and cannot, because it supplies the floor manually. The manual
  `raise_floor` call *is* the thing GW05b has to replace with a stamp.
- Consequence: phase 3's job is narrower than it looks. The refusal path is already proven; what is
  missing is only the *source* of the floor.

`test_an_unpublished_write_is_invisible_until_a_round` (line 245) — docstring: *"Today's behaviour,
pinned. GW05b's stamp is what will make this fail CLOSED (sp2)."* It writes through a
`BrokenPublisher`, asserts the worker's drain still reports `ok` and the plan is simply absent, then
runs a re-hydrator round and asserts the plan appears. `pending.durable is True and
pending.published is False`.

This test **asserts the defect**, deliberately and with a comment saying so. Phase 4 must invert it:
the drain should still succeed (the store is healthy; there is nothing to read), but the *plan
lookup* must refuse once the stamp ages out rather than returning "no such tenant". The test is
renamed and rewritten in phase 4, not deleted — the current assertions become the `fresh_ms=0`
negative control.

---

## 10. R2-04: the part that is already shipped

The runbook's R2-04 correction has five clauses. Three are already done, in GW05c.

| Clause | Status | Evidence |
|---|---|---|
| `lock_timeout` on every control-plane connection | **DONE** — 2000 ms | `state_control/pg.py:171, 184` |
| `statement_timeout` | **DONE** — 5000 ms | `state_control/pg.py:172, 185` |
| `idle_in_transaction_session_timeout` | **DONE** — 5000 ms | `state_control/pg.py:173, 186` |
| `tcp_user_timeout` | **DONE** — 5000 | `state_control/pg.py:174, 187` |
| lock-free `REPEATABLE READ` publish snapshot | **DONE** | `state_control/pg.py:8, 207` — `snapshot` reads `REPEATABLE READ, READ ONLY` and takes no row lock |
| ≥ 2 re-hydrators in ≥ 2 zones | **NOT DONE** — deployment | section 19 |
| never let an idle-stop policy touch them | **NOT DONE** — deployment | section 19 |
| alarm when an `ok_publish_pending` write is older than one period | **NOT DONE** | section 24 |

The lock drill is already green locally. `test_drill_rehydration_under_a_held_row_lock` in
`tests/state_control/test_lgw05c_drills.py` holds a `FOR UPDATE` on the counter row and asserts
every kind is restored in under 5 s — against RC2's 38 s. The reference patch's own figure is
0.54–0.79 s; our drill asserts a looser ceiling because it runs against a container on a laptop,
not Cloud SQL.

There is one `state_control/pg.py` caveat worth carrying: the connection is opened per operation
(`connect_timeout=5`), which is why GW05c measured write end-to-end p99 at 20.2 ms. That is not on
a serving path, but it *is* on the re-hydrator's round, and section 18's grace arithmetic has to
budget for it.

**Consequence for this card:** R2-04 needs no new Postgres code. It needs a deployment manifest and
one alarm. Do not re-implement the timeouts.

---

## 11. The invariant GW05b must establish

Stated so it can be tested rather than admired:

> **I1 (enforcement bound).** For any write committed to Postgres at time `t`, every gateway
> process either enforces that write by `t + FRESH_MS + ε`, or refuses the affected kind. `ε` is
> one re-hydrator period plus one gateway refresh period.

> **I2 (no floorless process).** A process may apply a manifest of kind `K` only if its version and
> position are at or above the higher of (what this process has applied) and (what a stamp this
> process trusts has verified).

> **I3 (start gate).** A newly started process trusts only a stamp from a re-hydrator round that
> *started after the process did*. A stamp written before the process existed says nothing about the
> store the process is now reading.

> **I4 (honest silence).** A re-hydrator round that could not verify every kind writes no stamp.
> Freshness lapses; it is never claimed.

> **I5 (monotonicity).** Floors only rise, and `verified_at` only moves forward. A stamp from a
> lagging replica or a slower second re-hydrator never lowers either.

I3 is the subtle one and is what actually closes SP1. I2 alone is not enough: after a store
failover, the stamp sitting on the *lagging replica* is itself old, so a fresh process reading it
would adopt an old floor and happily serve old state. I3 forces the process to wait until a
re-hydrator has compared *that* store — the one it is now reading — with Postgres. The cost is up to
one re-hydrator round of extra start-up, which the reference patch measured at ≤ 1 s.

I4 is what makes the whole scheme fail safely rather than confidently. The temptation when a round
partly fails is to stamp the kinds that succeeded. Doing so would let a permanently broken kind sit
behind a fresh-looking stamp forever.

---

## 12. Stamp contents, signature and key

### Contents

```python
# gateway_v2/gateway_v2/domain/state.py  (design sketch, not applied)

@dataclass(frozen=True, slots=True)
class Stamp:
    """One re-hydrator round's assertion about the store it just compared with Postgres."""

    verified_at: float
    """Wall clock at the round's START. Everything committed before this is covered."""

    cursors: Mapping[StateKind, Cursor]
    """Per kind: the (version, feed_seq) Postgres held and the store was found to match."""

    by: str
    """Which re-hydrator (REHYDRATOR_ID, default hostname:pid). Gateways log it."""

    signature: str
```

Two deliberate differences from the rc3 reference, both forced by GW05c:

1. **`cursors`, not `versions`.** rc3's stamp carried `{kind: Version}` because RC2's floor was a
   single `Version`. GW05c's floor is a `Cursor(version, feed_seq)`, and `decode_manifest` enforces
   both dimensions. Carrying only the version would leave `feed_floor` at 0, so a fresh process
   would still accept a manifest at an older *position* under an equal version — which is exactly
   the shape a partial restore produces. The stamp must carry both numbers.
2. **`verified_at` is the round's start, not its end.** Same as rc3, and the reason is worth
   repeating because it reads backwards: the round reads the store, *then* Postgres (section 13), so
   the Postgres version it compares covers every commit that happened before the round began. Using
   the end time would over-claim.

### Signature

The stamp signs exactly as records and manifests do, with its own domain tag:

```python
# gateway_v2/gateway_v2/runtime/state_sig.py  (design sketch)
STAMP_DOMAIN = "stamp2"   # alongside RECORD_DOMAIN = "rec2", MANIFEST_DOMAIN = "meta2"

def stamp_signature(secret: bytes, verified_at_ms: int,
                    cursors: Mapping[StateKind, Cursor], by: str) -> str: ...
def make_stamp(...) -> Stamp: ...
def encode_stamp(stamp: Stamp) -> bytes: ...
def decode_stamp(secret: bytes, raw: bytes | str | None) -> Stamp: ...
```

The domain tag is not decoration. Without it, a manifest envelope and a stamp envelope that happen
to share a field layout could be substituted for one another under the same HMAC key. `rec2` /
`meta2` already establish the pattern; `stamp2` follows it.

`decode_stamp` raises `StoreDataUnavailable` on a missing, malformed or forged stamp — the same
exception every other integrity failure in this subsystem raises, so the posture table needs no new
branch.

### Key

`StoreKeys.stamp` → `{rv2}:stamp`. Already declared. One key, one namespace hash tag, so it can sit
in the same `MULTI` as a manifest read on a cluster-mode store. There is **one** stamp for the whole
namespace, not one per kind: the per-kind detail lives inside it, and a single key is what lets a
gateway pick it up as one extra command on a read it was already making (section 20).

---

## 13. The "verified" condition on an O(1) re-hydrator

This is the one place where the reference implementation **cannot be copied**, and the plan would be
wrong to pretend otherwise.

rc3 decided a kind was verified like this:

```python
# rc3-state-p0.patch, rvproto/control/rehydrate.py
if int(meta["count"]) != len(recs) or meta["digest"] != digest(recs):
    return "digest", pg_v
```

`digest(recs)` is the whole-set content digest over every record of the kind — read from Postgres
via `self.db.snapshot(kind)`, every round, for all four kinds. That is finding F6/S3, and GW05c
deleted it on purpose. There is no `digest` and no `verify_set` anywhere in `gateway_v2`; the
tenant-scale gate (`lint/check_tenant_scale.py`) forbids reintroducing the commands that would
rebuild it.

GW05c's re-hydrator diagnoses in O(1) instead:

```python
# state_control/rehydrate.py  (shipped)
counters = self._db.counters(kind)          # one indexed row
head = self._publisher.stored_head(kind)    # GET + ZCARD + ZREVRANGE + SCARD
...
if manifest.feed_seq > counters.feed_seq:  return STORE_AHEAD
if manifest.feed_seq < counters.feed_seq:  return STALE
if manifest.version != counters.version:   return VERSION
if manifest.count != counters.count or manifest.on_count != counters.on_count:
                                           return COUNTS
if head.index_count < manifest.count or head.index_top < manifest.feed_seq:
                                           return INDEX
if kind is StateKind.KS and head.engaged_count != counters.on_count:
                                           return ENGAGED
return None
```

plus a separate, occasional deep `verify(kind)` that compares the store's whole `key -> feed_seq`
index against Postgres. `verify` is what closes GW05c's A10 residual (one missing index entry masked
by one extra, counts agreeing while contents do not).

**So what may a GW05c stamp honestly assert?**

> `diagnose(kind) is None` proves that the store's manifest for `kind` is at exactly the version and
> position Postgres holds, that it attests the same record and engaged counts, and that the index is
> not behind it. It does **not** prove the record *contents* match.

That is strictly weaker than rc3's digest comparison, and it is still sufficient for the invariants
in section 11, because I1/I2/I3 are statements about **versions and positions**, not contents. A
content divergence under matching counts and positions is a different failure class — it is what
`verify` exists for, and it is covered by GW05c's record-level signatures on the read path
(`record_matches_index`, `record.version <= manifest.version`).

Design decision, stated so it is reviewable:

- **The stamp is issued on the O(1) condition.** `RoundSummary.ok and not summary.repairs_pending`
  — every kind either diagnosed clean or was repaired to clean this round.
- **The stamp records which condition it was issued on.** Add a `deep: bool` field. A round that ran
  `verify` sets it; a shallow round does not. Gateways do not treat them differently (that would
  reintroduce a fleet-wide dependency on an O(records) round), but the operator-visible metric
  `amf_state_stamp_deep_age_seconds` makes a long gap between deep rounds alarmable.
- **A repaired kind counts as verified.** After `repair`, the store has been rewritten from Postgres
  wholesale, so it matches by construction. rc3 took the same position
  (`verified[kind] = pg_v` after a successful publish).

Why not stamp only on a deep round? Because the deep round is O(records), and the card's own
constraint is that the re-hydrator is now on the data plane's availability path — a round slower than
`FRESH_MS − period` produces stamps that are born stale and the whole fleet fails closed. At 50k
records per kind the rc3 README already flagged "P1's O(changes) rounds are needed". Tying freshness
to the deep round would make tenant count determine global availability, which is the §2.1 rule (1)
violation this whole programme exists to remove.

---

## 14. Raising a reader's floor from the stamp

### Where the floor lives

`StateSynchroniser` owns the cursors and is already the sole owner of cursor advancement. The stamp
view must therefore feed `StateSynchroniser.raise_floor`, and nothing else may write cursors.

```python
# gateway_v2/gateway_v2/runtime/state_stamp.py   (new module, design sketch)

class StampView:
    """The newest stamp this process trusts. One per worker, shared by every kind's reader."""

    def __init__(self, secret: bytes, *, fresh_ms: int = FRESH_MS,
                 pg_grace_ms: int = PG_GRACE_MS,
                 started_at: float | None = None,
                 clock: Callable[[], float] = time.time) -> None: ...

    def observe(self, raw: bytes | None) -> bool:
        """Adopt a stamp. False when absent, malformed or forged. Floors only rise."""

    def floor(self, kind: StateKind) -> Cursor:
        """The lowest (version, feed_seq) of `kind` this process may apply."""

    def fresh(self, now: float | None = None) -> bool: ...
    def age_seconds(self, now: float | None = None) -> float | None: ...
    def unverified(self) -> str:
        """Why state is refused, for the 503 detail and the structured log."""
```

### The wiring

One call site, in the synchroniser's round:

```
round begins
  store.head(kind)  ->  also GET {rv2}:stamp  in the SAME round trip
  view.observe(stamp_raw)
  sync.raise_floor(kind, view.floor(kind))     for every registered kind
  poll(kind, cursor) with the raised cursor
```

`raise_floor` already refuses to lower anything:

```python
current = self.cursor(kind)
if floor.feed_seq < current.feed_seq or floor.version < current.version:
    return
self._cursors[kind] = floor
```

That satisfies I5 for free. One correctness note on the existing guard: it refuses when *either*
dimension is lower, which is right — a stamp whose version is higher but whose position is lower is
incoherent and must not be adopted piecemeal.

### Why the floor must be raised before `poll`, not after

`FeedReader._read_head` passes `cursor.version` as `not_before` and `cursor.feed_seq` as
`feed_floor` into `decode_manifest`. If the floor were raised after the round, the round that
mattered — the first one a fresh process runs — would already have accepted the stale manifest. The
order is not an optimisation; it is the fix.

### The one case `raise_floor` does not cover

A floor raised to `Cursor(v, s)` makes the process *refuse* anything below `(v, s)`. It does not make
the process *hold* records it never read. So after the floor is raised on a lagging replica, the
round's `poll` raises `StoreDataUnavailable` ("manifest older than the floor"), the `RoundReport`
carries an error, the cursor stays put, and the kind has no applied state at all. That is the correct
outcome — and it is exactly why section 16 has to define what serving does with a kind in that
condition, rather than leaving it to whatever the component's default happens to be.

---

## 15. Freshness evaluation: the start gate and the clock

```python
def fresh(self, now: float | None = None) -> bool:
    if self._fresh_s <= 0:
        return True                      # freshness not enforced (explicit opt-out only)
    if self._verified_at is None:
        return False                     # no stamp seen yet
    if self._verified_at < self._started_at:
        return False                     # I3: a round that predates this process
    return abs(self._age(now)) <= self._fresh_s
```

Four things to notice, each of which is a decision:

**The start gate (`verified_at < started_at`).** This is I3. Note the comparison is against the
*process* start, not the worker's first successful refresh. rc3 rounds `started_at` down to the
millisecond so a round that started within the process's start millisecond counts as after it; keep
that, it removes a flaky boundary in tests.

**`abs(...)`.** A stamp dated further in the future than `FRESH_MS` is not fresh. Without the
absolute value, a re-hydrator with a badly skewed clock could mint a stamp years ahead and pin the
fleet permanently "fresh". This is the cheapest available defence against the clock risk and it
costs nothing.

**`fresh_ms <= 0` disables enforcement.** Needed for migration (section 28 phase 1 ships the writer
before the reader enforces) and for a break-glass rollback. It must be an explicit configuration
value, never a default, and start-up must log loudly when it is set.

**The clock is wall clock, and that is a real dependency.** `verified_at` comes from the
re-hydrator's `time.time()`; the age comes from the gateway's. The comparison is only as good as NTP
across both tiers. Consequences, stated rather than buried:

- Skew of more than `FRESH_MS` (5 s) in the *gateway-behind* direction makes stamps look old → the
  fleet fails closed. Loud, safe, diagnosable via `amf_state_stamp_age_seconds` going negative or
  large.
- Skew in the *gateway-ahead* direction extends the exposure window by the skew. Unsafe, and the
  `abs()` check only catches gross cases.
- Monotonic clocks cannot be used: the two numbers come from different processes on different hosts.

Mitigations this card can take, none of which fully closes it: require NTP on both tiers as a
deployment precondition; export `amf_state_stamp_age_seconds` and alarm on it going negative; keep
`FRESH_MS` (5 s) comfortably above expected skew (ms, on GCE). Carried to section 29 as an accepted
risk with an owner question, because the alternative — a logical clock or a round-trip challenge —
is a much larger design and the reference implementation did not attempt it either.

### The existing staleness check is not replaced

`KillSwitchSnapshot.state()` already returns `STALE` when its own last refresh is older than
`FRESH_MS`:

```python
# gateway_v2/gateway_v2/admit/killswitch.py:108-115
verified = self._verified_at
if verified is None or moment - verified > self._stale_s:
    return KillSwitchState.STALE
```

That measures *"did I read the store recently"*. The stamp measures *"was the store compared with
Postgres recently"*. Both are required, and they fail in different scenarios:

| Scenario | local staleness | stamp freshness |
|---|---|---|
| Store unreachable from this worker | trips | trips (no new stamp either) |
| Store healthy, re-hydrator dead, publish pending (SP2) | **does not trip** | trips |
| Store failed over to a lagging replica, fresh process (SP1) | does not trip | trips |
| Re-hydrator healthy, worker's refresh task wedged | trips | does not trip (stamp is fresh in the store, unread) |

The last row is why the stamp read must happen on the *refresh* path, not on a separate timer: a
wedged refresh must look stale, and it does, because a wedged refresh also stops observing stamps.

`state()` therefore gains one clause, not a rewrite:

```python
if self._stamp is not None and not self._stamp.fresh(moment):
    return KillSwitchState.STALE
```

---

## 16. Per-kind fail-closed behaviour and reason codes

The card says "fail closed **per kind**". GW05c's `drain_all` already reports every kind's outcome
independently and raises for none, so the per-kind isolation exists; what is missing is the mapping
from "kind unavailable" to a response.

RC2's posture table (`rvproto/admit/posture.py`) is the vocabulary to reuse. `gateway_v2` has no
equivalent yet — `edge/` is stubs — so this card defines the mapping and the next card renders it.

| Kind | Condition | Outcome | Reason code | Status |
|---|---|---|---|---|
| `ks` | stamp not fresh | every request refused | `kill_switch_unavailable` | 503 + `Retry-After` |
| `ks` | `/v1/models` and other non-chat | refused | `shared_state_unavailable` | 503 + `Retry-After` |
| `key` | stamp not fresh | **no cached principal is trusted**; resolve refuses | `shared_state_unavailable` | 503 + `Retry-After` |
| `plan` | stamp not fresh | plan lookup refuses | `plan_unavailable` | 503 + `Retry-After` |
| `budget` | stamp not fresh | remaining lease is spent, then refused | `budget_unavailable` | 503 | 

The `key` row is the subtle one and rc3 got it right: a cached principal must not be served while
state is unverified, because **the revocation may be precisely the unpublished part.** Concretely:

```python
# gateway_v2/gateway_v2/admit/identity.py  (design sketch)
def cached(self, key_hash: str) -> Principal | None:
    if self._stamp is not None and not self._stamp.fresh():
        return None        # a revocation may be what we have not verified
    return self._held.get(key_hash)
```

This is the difference between "fail closed" and "fail closed except for the fast path", and the
fast path is where the traffic is. Without it the fix does nothing for an already-warm worker.

`Retry-After` reuses RC2's arithmetic, which is already correct:

```
retry_after_s = max(1.0, (rehydrate_period_ms + refresh_ms) / 1000.0)
```

With period 1000 ms and refresh 500 ms that is 1.5 s → clamped to 1.5 s. The floor of 1 s matters
for R2-08 (sheds carrying 6–11 ms retry-after cause SDK retry amplification).

Two rules that are easy to get wrong:

1. **`budget` must not gate the other kinds.** A budget-kind failure is a distinct, narrower
   outcome (R2-09). Treating it as global would turn a counter problem into an outage.
2. **A kind whose stamp cursor is unavailable but whose RAM snapshot is still inside its *local*
   freshness bound still fails closed.** The local bound is not a second chance; it is a different
   test that happens to be passing. I1 is about the stamp.

---

## 17. Readiness, and why `/readyz` cannot ship in this card

The runbook's GW05b requires `/readyz` to be 503 while state is unverified, and L05b-6 measures
"global fail-closed at the declared bound". RC2 implements this in `rvproto/edge/ops.py`:

```python
def worker_ready_now(st: State) -> bool:
    return guard_ok and st.plans.fresh() and st.ks.state() != "stale" and st.draining_since is None
```

In `gateway_v2`, `gateway_v2/edge/` is entirely stubs — every module is three to five lines with
`__all__: tuple[str, ...] = ()`. There is no HTTP surface, no `/readyz`, no start-up path and no
serving loop. Verified by reading the directory.

**This is a hard dependency on GW06, and the plan must say so rather than inventing an edge layer.**
What this card can and should do:

- Define the predicate as a pure function in `runtime`, with no HTTP in it:
  ```python
  # gateway_v2/gateway_v2/runtime/state_stamp.py
  def state_ready(view: StampView, reports: Sequence[RoundReport]) -> tuple[bool, str | None]: ...
  ```
  Unit-testable now, with no edge layer. GW06 calls it from `/readyz` and maps the reason.
- Define the reason codes (section 16) as constants in a module GW06 can import, so the two cards
  cannot drift into two vocabularies — which is C37's failure mode one level down.
- Record in the GW06 card's dependency list that `/readyz` must include `state_ready`, and that
  L05b-6 cannot be run until it does.

What this card must **not** do: build a minimal `edge/ops.py` to satisfy the gate. That would create
a second HTTP surface for GW06 to reconcile, and the structural gate already forbids
`HTTPException` / `JSONResponse` outside `edge`/`resolve`.

---

## 18. Postgres outage: the grace window

The requirement, verbatim from the card: *during a Postgres outage, keep stamping while no kind in
the store is behind the last verified versions, for at most `RV_STATE_PG_GRACE_MS`. Size it to at
least the measured loaded Cloud SQL gap (15.5 s), plus margin.*

`PG_GRACE_MS = 16_000` in `domain/locks.py`, commented "Postgres ride-through". Locked; consume it.

Without this clause the fix is a regression: the reference patch measured a 10 s Postgres freeze
producing ≈ 7.4 s of **global 503**, because no Postgres means no verification means no stamp means
the fleet fails closed. A Cloud SQL failover is a routine, expected, 11–16 s event. Turning it into a
fleet outage trades one CRITICAL defect for another, and L05b-4 ("Cloud SQL failover at 60% load →
0 × 503 for valid tenants") would fail.

The grace condition, precisely:

> When Postgres is unreachable, a round may still stamp **iff** for every kind the store's manifest
> is at or above the cursor recorded in the last stamp this re-hydrator wrote, and the elapsed time
> since that stamp's `verified_at` is at most `PG_GRACE_MS`.

Why that is safe: the store is not behind what was already verified, so no previously-enforced write
has been lost. Why it is bounded: a write committed *during* the Postgres outage cannot be published
(the writer also needs Postgres), so the grace window is an explicit, declared window of potential
non-enforcement — 16 s — not an open one.

Implementation shape:

```python
# state_control/rehydrate.py  (design sketch)
def round_once(self, *, deep: bool = False) -> RoundSummary:
    ...
    # on a Postgres error for a kind, fall back to the grace check instead of skipping the stamp
    if summary.db_unavailable and self._within_pg_grace(now) and self._store_not_behind_last_stamp():
        self._write_stamp(verified_at=round_t0, cursors=self._last_stamp.cursors, degraded=True)
```

Three properties to get right:

1. **`verified_at` still advances** during the grace, otherwise the gateways age out anyway and the
   grace achieves nothing. The *cursors* stay at the last verified values; only the timestamp moves.
2. **The stamp is marked `degraded=True`** so operators can tell a verified round from a
   ride-through round, and so `amf_state_stamp_degraded` can alarm. Gateways do not change behaviour
   on it — a degraded stamp is still a valid floor and still counts as fresh — because changing
   behaviour would reintroduce the outage.
3. **The grace is measured from the last *verified* stamp, not the last written one.** Otherwise
   each degraded stamp would reset the clock and the ride-through would never end.

Arithmetic check against the locked values: `PG_GRACE_MS` 16 s ≥ the 15.5 s measured loaded gap, with
0.5 s of margin. That margin is thin. It is also the owner's number, so the plan records the concern
and does not change it: if L05b-4 fails on margin, that is an owner escalation with evidence, not a
unilateral retune.

One residual the reference patch names and this card inherits: `tcp_user_timeout` does not cover a
Postgres connection that *hangs* rather than dropping. `statement_timeout` 5000 ms covers a hung
query; a hang during connect is covered by `connect_timeout=5`. A hang between connect and the first
statement is the remaining sliver. Carried to section 29.

---

## 19. Two re-hydrators in two zones

Code already supports concurrency; the gap is deployment and one new guard.

Already safe in RC2 and preserved in GW05c:

- Publishes are WATCH-guarded and skip when the store holds a newer verified manifest
  (`state_control/valkey.py:_guarded`, `_is_ahead`).
- Budget repairs are compare-and-set (the `BUDGET_CAS` script).
- F-C36-RACE ran with two re-hydrators: 0/360 mislabelled manifests.
- GW05c's drill `test_drill_concurrent_writers_leave_no_gap`
  (`tests/state_control/test_lgw05c_drills.py:361`) already exercises contention on the counter row
  with the shipped `lock_timeout`.

New in this card: **a stamp must never be replaced by an older one.** Two re-hydrators in two zones
will have slightly different round starts, and a slower one must not overwrite a faster one's newer
stamp. rc3's `_put_stamp` is the right shape and should be ported:

```python
# state_control/valkey.py  (design sketch)
def put_stamp(self, stamp: Stamp) -> bool:
    """WATCH-guarded. False when another re-hydrator verified later than this round started."""
    # WATCH {rv2}:stamp
    #   current = GET; if decode(current).verified_at >= stamp.verified_at: return False
    #   MULTI; SET {rv2}:stamp encode(stamp); EXEC
    # retry on WatchError, bounded (5 attempts), then raise
```

A malformed or forged current stamp is overwritten rather than respected — otherwise an attacker who
can write the key once could freeze freshness forever by planting an un-decodable value with no
readable timestamp.

Deployment rules, to be written into the lane manifest and the GW05b card's exit criteria:

1. **≥ 2 re-hydrators in ≥ 2 zones.** One is a single point of global non-enforcement.
2. **`REHYDRATOR_ID` defaults to `hostname:pid`** and is carried in the stamp's `by` field, so the
   gateway log line names which re-hydrator last verified.
3. **No idle-stop policy may target a re-hydrator VM.** This is the 09:06Z ledger incident written
   as a rule. R2-24 mandates an idle watchdog on every validation environment; the watchdog's
   exclusion list must name the re-hydrator instances explicitly.
4. **An alarm fires when an `ok_publish_pending` write is older than one re-hydrator period.** This
   is the direct SP2 detector and it belongs to the control plane, not the gateway.
5. **Re-hydrator maintenance windows must not overlap each other, and neither may overlap Cloud SQL
   or Memorystore maintenance** (R2-13, E2-08).

Cost note for the owner: two small control-plane VMs. The re-hydrator's round is now O(1) per kind
(GW05c), so these do not need to be large; the deep `verify` cadence is the only O(records) work and
it runs off the critical path.

---

## 20. Stamp propagation latency and the recheck cadence

The reference patch lists "no pub/sub nudge for the stamp" as an open risk. GW05c shipped a nudge
listener (`runtime/state_nudge.py`, channel `{rv2}:updates`, payload `<kind>:<feed_seq>`), so the
question is whether the stamp should use it.

**Decision: no stamp nudge in this card.** Reasons:

1. The stamp is read as **one extra command on a read the worker already makes** — `GET
   {rv2}:stamp` alongside `head(kind)` in the same round trip, exactly as rc3 appended it to the
   kill-switch `MULTI`. Marginal cost is one command per refresh, not one round trip.
2. A nudge would reduce stamp *adoption* latency by at most one refresh period (≤ 500 ms) in the
   common case, against `FRESH_MS` of 5 s. It buys 10% of the budget for a new publish on every
   re-hydrator round from every re-hydrator — i.e. a constant pub/sub rate proportional to
   re-hydrator count times workers, which is R2-23's cost class.
3. The one case where latency genuinely matters is **start-up**: a fresh process must wait for a
   round that started after it did. rc3 handles that with a faster recheck rather than a nudge, and
   that is the right lever because it only costs anything while the process is not yet ready.

So: borrow rc3's `recheck_s`.

```python
self._recheck_s = min(self._refresh_s, 0.1)
...
# after a round
waiting = round_ok and not view.fresh()      # store reachable, stamp not yet usable
await asyncio.sleep(self._recheck_s if waiting else self._refresh_s)
```

While the store is reachable but the stamp is not yet trustworthy, poll every 100 ms. This bounds a
new process's extra start-up at one re-hydrator period plus 100 ms, which is how the reference patch
reached "+≤ 1 s to ready". Once fresh, the cadence returns to the normal refresh period, so steady
state is unchanged and §2.1 rule (1) is untouched.

Worth noting what this does *not* add: no new task, no new connection, no new timer. The recheck is
the existing refresh loop choosing a shorter sleep.

---

## 21. Naming and vocabulary corrections

Three collisions that will cause review confusion or, worse, a wrong fix later.

**`_verified_at` already means something else.** `KillSwitchSnapshot._verified_at` (and
`ReplicaSnapshot`'s per-tenant stamps) hold a **monotonic** timestamp of this process's last
successful store read. The stamp's `verified_at` is a **wall-clock** timestamp of a re-hydrator's
comparison against Postgres. Same name, different clock, different meaning, and the existing one is
the misleading one — nothing was "verified", it was merely read.

Proposed rename, as part of this card: `KillSwitchSnapshot._verified_at` → `_refreshed_at`, and
`age_seconds` → `refresh_age_seconds`, with `stamp_age_seconds` added for the other quantity. This is
a private field plus one public method on one class; `semantic_rename` handles it and the tests that
reference it are ours.

**"fresh" is overloaded.** `ReplicaSnapshot(fresh_ms=FRESH_MS)` means "last-known-good is still
usable". `StampView.fresh()` means "state has been verified against truth recently". Keep both words
but qualify at every call site: `snapshot.within_freshness_bound()` vs `stamp.fresh()`. Cheap, and it
prevents a future reader from deleting one as a duplicate of the other.

**`FRESH_MS` is used for two bounds today.** It is the default for `KillSwitchSnapshot.stale_ms`,
`ReplicaSnapshot.fresh_ms`, and now `StampView.fresh_ms`. The runbook explicitly sets
`RV_STATE_FRESH_MS` default = `RV_KS_STALE_MS` = 5 s, so sharing the constant is correct and
intentional. Record it in the docstring so nobody "fixes" it by splitting them.

Note also: the rc3 patch used `RV_STATE_FRESH_MS` default **3000**, while `domain/locks.py` locks
**5000**. The locked value wins (and matches the runbook's R2-03 row: "tune the bound to at least
the RAM-serving window (5 s)"). The reference patch's measured figures — 2.04–2.97 s to global 503,
≈ 7.4 s on a 10 s Postgres freeze — were taken at 3000, so expect our equivalents to be roughly 2 s
longer. Do not report the patch's numbers as ours.

---

## 22. Configuration and start-up validation

New configuration, all with defaults that come from `domain/locks.py`:

| Name | Default | Meaning |
|---|---|---|
| `AMF_STATE_FRESH_MS` | `FRESH_MS` (5000) | refuse state not verified within this long; `0` = not enforced |
| `AMF_STATE_PG_GRACE_MS` | `PG_GRACE_MS` (16000) | ride-through window for a Postgres outage |
| `AMF_REHYDRATE_PERIOD_MS` | 1000 | re-hydrator round period |
| `AMF_REHYDRATE_DEEP_EVERY` | 60 | run the deep `verify` every N rounds |
| `AMF_REHYDRATOR_ID` | `hostname:pid` | the stamp's `by` field |

Start-up validation, borrowed from rc3 and extended:

```
FRESH_MS != 0 and FRESH_MS < 2 * REHYDRATE_PERIOD_MS
    -> reject: a stamp is up to one period plus one round old
REHYDRATE_PERIOD_MS <= 0
    -> reject
PG_GRACE_MS != 0 and PG_GRACE_MS < FRESH_MS
    -> reject: a grace shorter than the freshness bound cannot ride anything through
refresh period > FRESH_MS / 2
    -> reject (this rule already exists for RV_KS_REFRESH_MS vs RV_KS_STALE_MS)
FRESH_MS == 0
    -> accept, log at WARNING: "state freshness NOT enforced (R2-03 exposure)"
```

These are fail-at-start checks, not runtime branches. The C36 lesson that cost RC2 a fleet-wide
fail-closed was a background store timeout equal to the staleness ceiling — a config relationship
nobody validated. Every relationship above is of that class.

### `require_bounded_client` must finally be called

GW05c's drills (A24) exposed that nothing enforced a per-operation store-client timeout, and added:

```python
# gateway_v2/gateway_v2/runtime/store_valkey.py:69
def require_bounded_client(client: object, *, below_s: float) -> float:
    """Raise UnboundedStoreClient unless the client's per-operation timeout is below `below_s`."""
```

It has no callers. GW05b is the right card to wire it, because the stamp makes the relationship
binding: if a store operation can block longer than `FRESH_MS`, the worker cannot evaluate freshness
within the bound it promises. The call belongs on the start-up path with
`below_s = FRESH_MS / 2 / 1000`.

Caveat for honesty: the start-up path lives in `edge/`, which is stubs. So this card can export the
check and unit-test it; the actual call site lands with GW06. Flag it in the GW06 dependency list
alongside `/readyz` rather than leaving a second uncalled guard behind.

---

## 23. Schema and key-layout changes

**No database schema change is required, and the plan should resist adding one.**

GW05c shipped three tables: `amf_state_counter`, `amf_state_record`, `amf_state_log`. The stamp
attests per-kind `(version, feed_seq)`, and `amf_state_counter` already holds exactly
`(version, feed_seq, count, on_count)` per kind, read in O(1) by `ControlDB.counters(kind)`. The
re-hydrator needs nothing it cannot already read.

Considered and rejected:

| Candidate | Why rejected |
|---|---|
| An `amf_state_stamp` table (durable stamp history) | The stamp is current state, not a log. Its whole purpose is to be read from the store by workers who never touch Postgres. Persisting it adds a write per round per re-hydrator for no reader. |
| A `verified_at` column on `amf_state_counter` | Would be written once per round per kind by two re-hydrators — write amplification on the row the writer locks. Directly against R2-04. |
| A `publish_pending_since` column to drive the SP2 alarm | Tempting, and the alarm (section 19 rule 4) does need to find old pending writes. But `amf_state_log` already records write outcomes; the alarm can query it. Revisit only if the query proves too slow, with measurement. |

Key-layout change: **one key, already declared.** `StoreKeys.stamp` → `{rv2}:stamp`. No new
namespace, no new hash tag, no change to `HASH_KINDS` or `ENGAGED_KINDS`.

Store memory impact: one string of roughly 250 bytes (four kinds × two integers plus a MAC and an
id). Against R2-05's 10.4 GiB instance this is not worth a line in the budget, but it is worth
noting that the key is **not** TTL'd on purpose — a TTL'd stamp would disappear during a store
outage and be indistinguishable from a dead re-hydrator, which is fine for safety but loses the
`by` and `verified_at` detail the operator needs. Staleness is computed from the content, not from
key expiry.

---

## 24. Metrics and cardinality

`runtime/state_metrics.py` exports a **fixed** series count:

```python
SERIES_COUNT = 8 * len(StateKind) + 6 + 3 + 6
```

R2-10 is the reason this is fixed rather than per-tenant: a full metrics directory made 40% of new
tenants' first requests fail with HTTP 500. Every series added here must be tenant-independent.

New series, all cardinality-1 or per-kind:

| Series | Type | Why |
|---|---|---|
| `amf_state_stamp_fresh` | gauge 0/1 | the single most important operational signal in this card |
| `amf_state_stamp_age_seconds` | gauge | alarm on approach to `FRESH_MS`; **also alarm on negative** (clock skew, section 15) |
| `amf_state_stamp_verified_at_seconds` | gauge | absolute, for cross-host correlation |
| `amf_state_stamp_missing_total` | counter | no stamp in the store — is a re-hydrator running? |
| `amf_state_stamp_invalid_total` | counter | malformed or forged; non-zero is a security signal |
| `amf_state_stamp_degraded` | gauge 0/1 | a Postgres ride-through stamp (section 18) |
| `amf_state_stamp_deep_age_seconds` | gauge | time since the last deep `verify` round (section 13) |
| `amf_state_unverified_transitions_total` | counter | fresh → not-fresh lapses, excluding a new process's first wait |
| `amf_state_floor{kind}` | gauge, per kind | the stamp-derived floor, for proving I2 in a live run |

That is 8 + `len(StateKind)` = 12 new series. `SERIES_COUNT` becomes
`9 * len(StateKind) + 6 + 3 + 6 + 8`. The gate test in `tests/runtime/test_lgw05c_metrics.py` asserts
the exported dict length matches `SERIES_COUNT`, so this is a one-line update plus the assertion.

Control-plane side (not Prometheus-exported from the gateway; the re-hydrator's own surface):

| Signal | Why |
|---|---|
| `stamp_written_total{degraded}` | distinguishes verified from ride-through rounds |
| `stamp_withheld_total` | I4 firing — a round could not verify every kind |
| `stamp_race_lost_total` | the other re-hydrator was newer; healthy in a 2-node deployment |
| `publish_pending_oldest_seconds` | the direct SP2 alarm (section 19 rule 4) |

Structured log lines, on **transitions only** (not per round — that is C31's "instrumentation on the
serving loop" class):

- gateway: `state_verified` / `state_unverified` with `age_s`, `by`, and the `unverified()` detail.
- re-hydrator: `stamp_first`, `stamp_resumed`, `stamp_withheld` with the failing kinds.

---

## 25. Structural-gate and architecture compliance

Checked against the gates GW05c runs, before writing any code.

| Gate | Effect on this design |
|---|---|
| ≤ 800 lines per module | `state_stamp.py` is new and small. `state_sig.py` gains ~60 lines (currently well under). `rehydrate.py` gains the stamp + grace logic; check its size, and if it approaches the limit move the grace predicate into its own module rather than trimming comments. |
| ≤ 120 lines per function | `Rehydrator.round_once` already delegates. The grace check must be its own function, not an inline branch. |
| no module-level mutable literals | `Stamp.cursors` is a `Mapping`, constructed per instance. `StampView._floor` is instance state. Fine. |
| frozen + slots dataclasses in `plan`/`detect`/`resolve` | `Stamp` lives in `domain/state.py`; make it frozen + slots anyway, for consistency with `Manifest` and `SignedRecord`. |
| no `HTTPException` / `JSONResponse` outside `edge`/`resolve` | section 17: the reason codes are constants, the predicate is a pure function. No HTTP in `runtime` or `admit`. |
| capacity literals only in `runtime/resources.py` | `recheck_s = 0.1` and the 5-attempt WATCH retry bound are not capacity literals (not `max_connections`, `maxsize`, `pool_size`, `queue_depth`, `workers`), but the retry bound should still be a named module constant. |
| `lint/check_tenant_scale.py` | **the important one.** The stamp path must not introduce any of the 15 forbidden commands. `GET` on one key is fine. The deep `verify` already exists and is already exempted as off-serving-path; do not extend its exemption to anything new. |
| import-linter layers | see below |

Layer check — `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`:

- `domain/state.py` gains `Stamp`. Imports nothing. Fine.
- `runtime/state_stamp.py` imports `domain.state` and `runtime.state_feed`. Fine.
- `runtime/state_sig.py` gains stamp signing; imports `domain.state`. Fine.
- `admit/killswitch.py` and `admit/identity.py` import `runtime.state_stamp` — `admit` is **above**
  `runtime`, so this is allowed.
- `plan/snapshot.py` imports `runtime.state_stamp` — `plan` is above `runtime`. Allowed.
- **Watch out:** `runtime.state_stamp` must not import from `admit` or `plan`. GW05c already hit this
  wall once (A23) and solved it with local read-only Protocols in `state_metrics.py`. If
  `state_ready()` needs to see component health, it takes a Protocol, not a concrete type.
- `state_control` is unchecked by import-linter (`root_packages = ["gateway_v2"]`) but may import
  only `gateway_v2.domain.state` and `gateway_v2.runtime.state_sig`, so that one signing
  implementation exists. The stamp signing must therefore live in `state_sig.py`, not in
  `state_control`.

§2.1 rule (1) check — *no work proportional to the number of tenants, keys or records on a serving
event loop*: the stamp adds one `GET` per refresh round and one `max()` per kind. Constant. The deep
`verify` is O(records) and runs in the control plane on a 60-round cadence, off every serving loop.

---

## 26. Test plan

Naming follows the existing convention: `tests/<layer>/test_lgw05b_<topic>.py`.

### Unit — signing (`tests/runtime/test_lgw05b_stamp_sig.py`)

1. round trip: `encode_stamp` → `decode_stamp` preserves `verified_at`, every cursor, `by`, `deep`,
   `degraded`.
2. a flipped bit anywhere in the envelope → `StoreDataUnavailable`.
3. a stamp signed under a different secret → `StoreDataUnavailable`.
4. **domain separation:** a manifest envelope fed to `decode_stamp` is refused, and vice versa, even
   under the same secret.
5. missing field, wrong type, non-integer version → `StoreDataUnavailable`, never a crash.
6. `None` raw → refused, counted as *missing* not *invalid* (the two have different operational
   meanings).

### Unit — `StampView` (`tests/runtime/test_lgw05b_stamp_view.py`)

7. no stamp observed → `fresh()` is False, `unverified()` names the missing stamp.
8. a stamp from a round that started **before** the process → observed (floors rise) but `fresh()`
   stays False. **This is I3 and it is the SP1 test.**
9. a stamp from a round that started after the process → `fresh()` True.
10. a stamp exactly at the process's start millisecond → counts as after (boundary).
11. age beyond `FRESH_MS` → False; recovery on the next good stamp → True.
12. a stamp dated `FRESH_MS + 1` in the **future** → False (the `abs()` clause).
13. floors only rise: observe `(2,50)` then `(1,10)` → floor stays `(2,50)`.
14. `verified_at` only moves forward: an older stamp does not lower it.
15. an invalid stamp changes nothing and increments the invalid counter.
16. `fresh_ms=0` → `fresh()` always True, **floors still apply** (the opt-out disables the 503, not
    I2).

### Unit — floor wiring (`tests/runtime/test_lgw05b_floor.py`)

17. a fresh `StateSynchroniser` with a stamp at `(3,120)` refuses a manifest at `(2,80)` —
    `RoundReport.error` set, cursor unmoved, **no records read** (command-counting guard, the same
    technique GW05c used for the negative control).
18. the same synchroniser accepts `(3,120)` and applies normally.
19. `raise_floor` is called **before** `poll` in the round: assert ordering via a recording store, so
    a refactor cannot silently reverse it.
20. a stamp with a higher version but a lower `feed_seq` is not adopted piecemeal.
21. per-kind isolation: a stamp that floors `ks` out of range does not stop the `plan` kind's round.

### Unit — fail-closed behaviour (`tests/admit/test_lgw05b_failclosed.py`)

22. `KillSwitchSnapshot.state()` → `STALE` when the stamp is not fresh, even though the local
    refresh age is well inside the bound. **This is the SP2 test.**
23. `IdentityCache.cached()` returns `None` while not fresh, and the held entry is still there
    afterwards (we suppress, not evict).
24. `IdentityCache.resolve()` refuses rather than fetching while not fresh.
25. plan lookup returns the unavailable state while not fresh, even for a tenant whose
    last-known-good is inside `fresh_ms`.
26. budget's failure is narrower: the remaining lease is spendable, then refused — and a budget
    failure alone does not make `ks` stale.
27. `state_ready()` returns `(False, reason)` while not fresh and `(True, None)` once fresh.
28. the reason strings are exactly the constants GW06 will render (one assertion per code).

### Unit — re-hydrator (`tests/state_control/test_lgw05b_rehydrate_stamp.py`)

29. a round where every kind diagnoses clean writes a stamp with the Postgres cursors and
    `verified_at == round start`.
30. a round that repaired a kind still stamps that kind (repaired == verified).
31. **I4:** a round where one kind raises writes **no** stamp, and logs `stamp_withheld` once, not
    once per round.
32. the per-kind error does not stop the other kinds (H7, already true; assert it still holds with
    stamping added).
33. a deep round sets `deep=True`; a shallow round does not.
34. `verified_at` uses the round's start, proven by a clock that advances during the round.

### Unit — Postgres grace (`tests/state_control/test_lgw05b_pg_grace.py`)

35. Postgres unreachable + store not behind the last stamp + inside `PG_GRACE_MS` → a stamp is
    written, `degraded=True`, cursors unchanged, `verified_at` advanced.
36. Postgres unreachable + store **behind** the last stamp → no stamp (the ride-through is void if
    the store lost something).
37. Postgres unreachable beyond `PG_GRACE_MS` → no stamp; the fleet is allowed to fail closed.
38. the grace is measured from the last **verified** stamp, so successive degraded stamps do not
    extend it indefinitely.
39. Postgres returns → the next round writes a non-degraded stamp with fresh cursors.

### Unit — concurrent re-hydrators (`tests/state_control/test_lgw05b_two_rehydrators.py`)

40. `put_stamp` with a newer current stamp present → returns False, the newer stamp survives.
41. `put_stamp` with an older current stamp → replaces it.
42. `put_stamp` with a malformed current value → overwrites it.
43. a `WatchError` storm → bounded retries, then a clean raise (no infinite loop).

### Live stack (real Valkey + real Postgres, `tests/state_control/test_lgw05b_live_stack.py`)

44. end to end: write → publish → re-hydrator round → a gateway worker reads the stamp from real
    Valkey, raises its floor, and applies.
45. **SP1 reproduction and fix, live:** publish at `(3,120)`; roll the store back to `(2,80)` by
    restoring a dump; start a *new* `StateSynchroniser`; assert it refuses and `ks.state()` is
    `STALE`. Run the same scenario with the stamp read disabled (`fresh_ms=0`, floors off) and assert
    it *admits* — the negative control that proves the test is testing something.
46. **SP2 reproduction and fix, live:** commit to Postgres with the publish blocked; no re-hydrator;
    assert the worker goes `STALE` within `FRESH_MS + ε` rather than reporting `ok`. Then start the
    re-hydrator and assert enforcement within one period plus one refresh.
47. two `Rehydrator` instances against one Valkey for 100 rounds: the stamp's `verified_at` is
    monotonic throughout.
48. one asyncio event loop for the whole module — **A21 applies here**: a real `redis.asyncio`
    client binds its transport to one loop, so the harness must not call `asyncio.run()` per helper.

### Drills (`scripts/gw05b_local_drills.sh`, extending the GW05c script)

49. `FLUSHALL` → every kind restored, a stamp resumes, the fleet returns to fresh. Measure the gap.
50. `docker pause` the store for 4 s → measure time to not-fresh and time to recovery (the local
    analogue of L05b-5).
51. `docker stop` Postgres for 10 s → assert the ride-through holds the fleet fresh for up to
    `PG_GRACE_MS` (the local analogue of L05b-4; this is the drill that would have caught the
    reference patch's 7.4 s regression).
52. kill both re-hydrators → measure time to global not-fresh; restart one → measure recovery (the
    local analogue of L05b-6).
53. hold `FOR UPDATE` for 60 s, then `FLUSHALL` → every kind restored while the lock is held
    (L05b-3; GW05c's existing drill already covers the lock, this adds the flush on top).
54. clock skew: run the re-hydrator with a `clock` injected 10 s ahead → assert the fleet fails
    closed via the `abs()` clause rather than being pinned fresh.

Negative controls are non-optional. GW05c's experience (the `for fs in []` fabricated oracle in
CHG-0009, the leak-blind oracle in CHG-0029) is that a state test which cannot fail is worse than no
test. Every one of 45, 46, 51 and 52 ships with its stamp-disabled counterpart.

---

## 27. Live acceptance tests, and what is locally provable

The card's six live tests, with an honest provability column.

| Test | Procedure | Pass | Locally provable? |
|---|---|---|---|
| L05b-1 | Lagging-replica failover; start a fresh gateway at +0.5 s; probe the revoked key and killed org at ≥ 24/s | 0 admits, with and without a re-hydrator | **Partly.** Test 45 proves the mechanism on one machine against real Valkey. The ≥ 24/s probe against a serving HTTP surface needs `edge/` (GW06) and a lane. |
| L05b-2 | Writer store path blocked → `ok_publish_pending`; re-hydrator stopped | fail closed within the stamp bound; enforcement ≤ 1 s after a re-hydrator returns | **Partly.** Test 46 proves it at the synchroniser level. "Enforcement" as a 503 needs `edge/`. |
| L05b-3 | Idle `FOR UPDATE` 60 s, then `FLUSHALL` | every kind restored within the flush bound while the lock is held; a queued writer fails fast | **Yes.** Drill 53. GW05c already proves the lock half (<5 s vs RC2's 38 s). |
| L05b-4 | Cloud SQL failover (11–16 s) at 60% load | 0 × 503 for valid tenants | **No.** Drill 51 proves the ride-through logic; "0 × 503 at 60% load" needs Cloud SQL and a loadgen. |
| L05b-5 | Valkey maintenance blip (3.8 s) | 0 × 503 beyond the RC2 baseline | **No.** Drill 50 approximates with `docker pause`; Memorystore maintenance cannot be triggered (R2-13). |
| L05b-6 | Both re-hydrators killed | global fail-closed at the declared bound; recovery ≤ 1 s after one returns | **Partly.** Drill 52 measures it per process. "Global" needs a fleet. |

What this means for scheduling: **GW05b's code and its invariants are fully testable now; four of
six live gates are not.** That is the same position GW05c ended in, and it should be stated in the
card's exit criteria rather than discovered at sign-off.

The lane GW05b needs is **smaller than GW05c's**: no L4 guard, no GPU quota, no 25k-tenant estate.
It needs one gateway VM with a few workers, Cloud SQL with a failover replica, Memorystore Valkey,
two small re-hydrator VMs and one loadgen. If the owner approves a single lane, GW05b's gates are
cheaper to clear than GW05c's and should go first.

Blocking dependencies, explicitly:

- L05b-1, L05b-2, L05b-4, L05b-5 need a serving surface → **GW06** (`edge/`).
- L05b-4 needs Cloud SQL with a failover replica → **lane provisioning + owner spend approval**.
- L05b-5 needs Memorystore Valkey → same, and R2-13 limits the drill to a flush or forced failover.
- L05b-6 needs ≥ 2 re-hydrator instances → **deployment manifest** (section 19).

---

## 28. Implementation phases

One commit per phase, on `suraj-revamp`, same convention as GW05c
(`type(scope): imperative summary` ≤ 70 chars). Every phase ends green on the full gate set.

**Phase 1 — the stamp as data.** `Stamp` in `domain/state.py`; `STAMP_DOMAIN`, `stamp_signature`,
`make_stamp`, `encode_stamp`, `decode_stamp` in `runtime/state_sig.py`. Tests 1–6. No behaviour
change anywhere; nothing reads or writes the key yet.
*Commit:* `feat(gw05b): sign a freshness stamp the way records are signed`

**Phase 2 — the re-hydrator writes it.** `put_stamp` on the publisher Protocol, `MemoryStore` and
`ValkeyPublisher` (WATCH-guarded, section 19). `Rehydrator` stamps on an all-kinds-verified round,
withholds otherwise, marks `deep`. Tests 29–34, 40–43. Gateways still ignore the key, so this phase
is safe to deploy alone.
*Commit:* `feat(gw05b): stamp every round that verified every kind`

**Phase 3 — the gateway reads it and raises floors.** `runtime/state_stamp.py` with `StampView` and
`state_ready`. `StateStore.stamp()` on the Protocol and the Valkey adapter. `StateSynchroniser`
observes the stamp and calls `raise_floor` before `poll`. Tests 7–21. **This is the phase that closes
SP1.** Ship with `fresh_ms` honoured for floors but the 503 behaviour still off, so floors land
before refusals. Narrower than it looks: the refusal path is already proven by
`test_drill_a_regressed_store_is_refused`, so this phase only replaces that drill's manual
`raise_floor` with a stamp-derived one (section 9).
*Commit:* `feat(gw05b): start a process from a verified floor, never from zero`

**Phase 4 — fail closed per kind.** The `state()` clause on `KillSwitchSnapshot`; `cached()`
suppression on `IdentityCache`; plan lookup; budget's narrower outcome; the reason-code constants.
Tests 22–28. **This is the phase that closes SP2.** Also the `_verified_at` → `_refreshed_at` rename
(section 21), and the inversion of `test_an_unpublished_write_is_invisible_until_a_round` — the drill
that currently *asserts* SP2 (section 9).
*Commit:* `feat(gw05b): refuse each kind whose state was not verified in time`

**Phase 5 — the Postgres ride-through.** The grace predicate and the degraded stamp. Tests 35–39.
This phase exists to stop phase 4 from turning a Cloud SQL failover into an outage, so **it must not
be deferred past phase 4 in a deployment**.
*Commit:* `feat(gw05b): ride out a Postgres outage instead of failing the fleet`

**Phase 6 — cadence, metrics and config.** `recheck_s`; the 12 new series and `SERIES_COUNT`;
transition-only log lines; the configuration table and its start-up validation; `require_bounded_client`
exported for GW06.
*Commit:* `feat(gw05b): see freshness, and bound the knobs that control it`

**Phase 7 — live stack and drills.** Tests 44–48 against real Valkey and real Postgres;
`scripts/gw05b_local_drills.sh` with drills 49–54; evidence under
`docs/plans/evidence/2026-10-07-gw05b/`.
*Commit:* `test(gw05b): drill freshness against real servers`

**Phase 8 — handoff.** The GW06 dependency list (`/readyz` must call `state_ready`;
`require_bounded_client` on start-up; the reason codes); the deployment manifest for two
re-hydrators in two zones with the idle-watchdog exclusion; the `ok_publish_pending` alarm; the
`state_control/pg.py` docstring forbidding a replica DSN (section 7).
*Commit:* `docs(gw05b): hand the serving-surface half to GW06`

Ordering constraint worth stating: **phases 3, 4 and 5 are one deployable unit.** Phase 3 alone is
safe. Phase 4 without phase 5 is a regression (a 16 s Postgres blip becomes a fleet outage). Do not
let the per-phase commit discipline become a per-phase deploy.

---

## 29. Risks and open questions

**R1 — Clock dependence (accepted, with an owner question).** Freshness compares the gateway's wall
clock to the re-hydrator's. NTP skew beyond `FRESH_MS` in one direction fails the fleet closed; skew
in the other extends the exposure window. Mitigations: the `abs()` clause, the negative-age alarm,
NTP as a deployment precondition, and `FRESH_MS` of 5 s against millisecond-scale expected skew on
GCE. *Owner question: is NTP-dependent freshness acceptable as the v3 mechanism, or should a later
card add a round-trip challenge?* The reference implementation accepted it.

**R2 — Old processes on a lagging replica are bounded only by `FRESH_MS`.** I3's start gate protects
*new* processes. An existing process that already applied `(3,120)` will refuse the replica's
`(2,80)` by I2, so it fails closed — correct. But an existing process whose floor happens to equal
the replica's version keeps serving until its stamp ages out, i.e. up to `FRESH_MS`. That is the
declared bound, and it is what I1 promises. Named here because the reference patch named it and it
should not look like an oversight.

**R3 — `PG_GRACE_MS` margin is thin.** 16 s against a measured 15.5 s loaded Cloud SQL gap is 3%
headroom. If L05b-4 fails, the honest response is evidence to the owner, not a quiet retune of a
locked constant.

**R4 — A Postgres hang between connect and first statement.** `connect_timeout=5` covers connect;
`statement_timeout=5000` covers a hung query; `tcp_user_timeout=5000` covers a dead peer. A hang in
between is the remaining sliver. Inherited from the reference patch, which also did not close it.
Low probability, bounded consequence (the round fails, the stamp is withheld, the grace applies).

**R5 — Stamp age at scale.** The rc3 README flagged stamp age at 50k records as untested. GW05c's
O(1) diagnose removes the main driver (the round no longer reads records), but the deep `verify`
still reads every record of a kind, and GW05c's own bench found first-start catch-up *does* scale
(0.09 / 0.93 / 2.40 s at 1k / 10k / 25k — A25). The deep round must therefore never gate stamping
(section 13), and `AMF_REHYDRATE_DEEP_EVERY` must be tunable. Unmeasured at 50k; flag it.

**R6 — The weaker "verified" predicate.** Section 13: a GW05c stamp attests versions, positions and
counts, not contents. A content divergence under matching counts and positions is covered only by the
occasional deep `verify` and by record-level signatures on the read path. This is a deliberate
weakening relative to rc3 and should be reviewed as such, not slipped through.

**R7 — Four of six live gates are blocked.** Section 27. GW05b will reach "code complete, invariants
proven locally, live gate pending a lane", which is exactly where GW05c sits. Two cards in that state
is a programme risk, not a technical one, and the owner should see it as such.

**R8 — The `fresh_ms=0` escape hatch.** A break-glass that disables the 503 behaviour is also a
break-glass that reinstates R2-03. It must be loud at start-up, exported as a metric, and never a
default. *Open question: should it be removable before v3 sign-off?*

**R9 — `edge/` does not exist.** Two guards from this card (`state_ready`, `require_bounded_client`)
will ship uncalled, pending GW06. GW05c already left one uncalled guard behind
(`require_bounded_client`). Three uncalled guards is a pattern that starts looking like dead code to
a reviewer who does not know the sequencing — hence phase 8's explicit handoff document.

---

## 30. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| **Fetch the floor from the control plane at process start** (one of the reviewer's two suggested directions) | Puts a control-plane RPC on every gateway's start-up path, so a control-plane outage stops the fleet from scaling out — and it solves only SP1. The stamp solves SP1 *and* SP2 with no new network dependency, because the store is already a dependency. |
| **Have gateways read Postgres directly to compare** | Breaks control/data separation (§2.1), puts Cloud SQL on the data plane, and would need a connection pool per worker. The whole point of C36's design is that the data plane reads one surface. |
| **Durable per-process floor on local disk** | A disposable replica (§2.1 "stateless serving replicas") must not have durable state. Also wrong: the floor would persist across a *legitimate* signed rollback. |
| **A stamp per kind (four keys)** | Four `GET`s instead of one, four WATCH-guarded writes per round, and the all-kinds-verified predicate (I4) would have to be reconstructed by the reader from four independent timestamps. One key, per-kind payload, is strictly simpler. |
| **TTL the stamp key so absence means stale** | Loses `by` and `verified_at` exactly when the operator needs them, and makes a store eviction indistinguishable from a dead re-hydrator. Staleness is computed from content. |
| **Tie the stamp to the deep `verify` round** | Makes global availability a function of record count — the §2.1 rule (1) violation the programme exists to remove. Section 13. |
| **Stamp only the kinds that verified** | Lets a permanently broken kind hide behind a fresh-looking stamp. I4 exists to prevent exactly this. |
| **A Redis Stream for stamp history** | The stamp is current state, not a log. Same reasoning that made GW05c choose a ZSET index over a stream: no retention to tune, no trim gap to detect. |
| **Add a pub/sub nudge for the stamp** | Section 20. Buys ≤ 500 ms against a 5 s budget, at a constant pub/sub cost proportional to re-hydrators × workers (R2-23's cost class). The 100 ms recheck gets the start-up win for free. |
| **Monotonic clocks instead of wall clock** | Impossible: the two timestamps come from different processes on different hosts. |
| **Reuse `RV_*` environment names** | The v3 tree uses `AMF_*`. Carrying `RV_*` would imply RC2 compatibility that does not exist (different key layout, different signature domains). |
| **Defer the `_verified_at` rename** | It is the single most confusing name in the subsystem and this card adds the thing it was wrongly named after. Renaming later costs more. |

---

## 31. Ready for implementation?

**Yes, with two caveats and one owner decision.**

What makes it ready:

- Both defects are reproduced, with evidence files, and both are **confirmed present in the current
  `gateway_v2` tree** (section 8), not merely in RC2.
- The reference implementation exists, has been read in full, and the two places it cannot be copied
  are identified and resolved (section 13's O(1) predicate, section 12's `Cursor`-shaped floor).
- Every seam the design needs already exists and is already tested (section 9). `raise_floor` is
  written and waiting for a caller.
- The two bounds are owner-locked, so there is nothing to argue about (`FRESH_MS` 5000,
  `PG_GRACE_MS` 16000).
- R2-04's code half is already shipped and drilled (section 10), so this card is smaller than the
  runbook row suggests.
- No schema change, no new infrastructure, one new store key that is already declared (section 23).

Caveat 1 — **two guards will ship uncalled.** `state_ready` and `require_bounded_client` need a
start-up path and an HTTP surface that `gateway_v2/edge/` does not have. Phase 8 hands them to GW06
in writing. Accepted.

Caveat 2 — **four of six live gates cannot run.** Sections 27 and 29/R7. The card will exit as "code
complete, invariants proven locally, live gate pending a lane". Same position as GW05c.

Owner decision needed before phase 7: **approve a GW05b lane**, or accept the local-only evidence for
now. The lane is materially cheaper than GW05c's — one gateway VM, Cloud SQL with a failover replica,
Memorystore Valkey, two small re-hydrator VMs, one loadgen, and **no L4 GPU and no 25k-tenant
estate**. If only one lane gets funded, this card's gates should go first: they are cheaper and they
cover a CRITICAL correctness defect rather than a performance one.

### The exact first step

```
Phase 1, one commit: add `Stamp` to gateway_v2/gateway_v2/domain/state.py as a frozen+slots
dataclass carrying (verified_at, cursors: Mapping[StateKind, Cursor], by, deep, degraded,
signature); add STAMP_DOMAIN = "stamp2" plus stamp_signature / make_stamp / encode_stamp /
decode_stamp to gateway_v2/gateway_v2/runtime/state_sig.py, reusing the existing _mac and
canonical_body; write tests/runtime/test_lgw05b_stamp_sig.py covering tests 1-6 of section 26,
including the domain-separation case in both directions.
```

Nothing reads or writes `{rv2}:stamp` after phase 1, so it is a pure addition with no behaviour
change and no deployment ordering concern. Gate it with the usual set before moving on:

```bash
cd gateway_v2
for tree in gateway_v2 state_control; do for g in check_sizes check_http_outside_edge_resolve \
  check_no_module_mutable check_capacity_literals check_frozen_dataclasses; do
  ./.venv/bin/python -m lint.$g "$tree"; done; done
./.venv/bin/python -m lint.check_tenant_scale gateway_v2
./.venv/bin/lint-imports && ./.venv/bin/ruff check gateway_v2 state_control lint tests
./.venv/bin/mypy --strict && ./.venv/bin/python -m pytest -q
```

Baseline to beat: **330 passed / 45 skipped** offline, **371 passed / 4 skipped** with real Postgres
and Valkey, mypy --strict clean on 108 files, import-linter 2 kept / 0 broken.
