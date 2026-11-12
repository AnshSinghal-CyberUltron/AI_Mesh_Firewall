**Plan Reviewer B (security, scale & operability): REQUEST CHANGES**

Reviewed head `b284dc9efd7f0a152f6fd2f88336722870ea4ead` (plan rev 1) against parent §6 (`e2c3e2cf`) and D1–D5. Code claims were re-read at `25c33c86`. I re-ran and extended the prototypes on this host (2 CPUs, load 3.7–8.2, shared). Postgres work ran under `.ctl-db.lock` in scratch databases `rvb118_bench`, `rvb118_chunk5` and `rvb118_chunk5b`, all dropped afterwards. Valkey work used a scratch namespace `{rvb118}`, and its 1,502 keys were removed. The scripts and outputs are summarised in the evidence block at the end.

**What holds up.**
- Release 1 is sound and should ship first, as planned: the raw-map stamp, required kinds supplied by the reader, the held-stamp fix and per-kind `covered_at`. `stamp_compat.txt` matches HEAD's code.
- Per-row `except Exception` in derivation, the D4 omission and the ciphertext-only view (no plaintext enters MODEL state in F1a-2: `cred` is `none`, `pending` or `aead1` only) are right.
- The O(1) request path and the source-revision idea are also right.
- The author's HEAD finding is confirmed. `StateSynchroniser.round_once` catches only `StoreDataUnavailable` (`state_task.py:153-160`), `drain_all` runs every kind in one comprehension (`:232-234`), and `PlanDeltaApplier` catches only `CompileError` (`delta.py:107-112`).

The six blockers below are failure-isolation and scale defects in the design as written. Each comes with evidence and a concrete fix.

## BLOCKERS

### B1. One tenant's source rows, or one malformed control row, can stop the projector round fleet-wide
This is the #117 bug class, moved from validation to the source read.

**The design.** §4.2.6 isolates *derivation* per row. The changed-org read (§4.2.2) is one statement per 500 orgs, under the 800 ms `statement_timeout`. HEAD's policy is "a database or store fault propagates" (`projector.py:205`, `projector_service.py:277-281`). So a chunk that fails means no heartbeat, and 16 s later the fleet fails closed. The org's `rev` never reaches the memo, so every round retries it and fails again: the outage is **permanent** while the row exists.

**Per-org volume is tenant-controlled and unbounded, contrary to §6's "bounded per-org work":**
- the plan caps *active* models only ("65th active model refused"), so inactive rows are unlimited;
- `api_key` is a `CharField` with no `max_length` (`llm_model_serializer.py:30`), and control accepts a 1 MiB body (`settings.py:309`), so each row can carry a ~1.4 MB Fernet token.

**Measured** (`pg_chunk5b`: the plan's own chunk of 500 orgs × 5 models, three runs each):

| Chunk | Result |
|---|---|
| 500 orgs × 5 | ok, 25–67 ms |
| 499 orgs × 5 **+ one org with 192 rows × ~1.4 MB tokens** | ok 1,099 ms, then **cancelled** at 831 ms and at 837 ms |
| 499 orgs × 5 **+ one org with 250,000 inactive rows** | **cancelled** 3/3 (811–821 ms) |

**A malformed row needs no volume at all.**
- One `created_at = 'infinity'` (raw SQL, an import or a restore) fails the **whole statement**.
- The plan's `extract(epoch FROM created_at)::bigint` raises `FeatureNotSupported: cannot convert infinity to bigint`.
- Fetching the raw column instead raises psycopg `DataError: timestamp too large`.
- Both are driver or statement errors, so "type-check each driver value" (§4.2.6) never runs.

**The 500-org chunk is not density-independent.** At the plan's cap of 64 models per org it is cancelled **with no attacker** (886 ms, `pg_review_bench` §1-4). 100 orgs × 64 takes 144 ms.

**Per-org assembly failure keeps the previous record** (§4.2.6). The heartbeat then claims an org whose removals were never applied. That is fail-open for that org.

**Fix:**
- **Bound at the source.** Control caps total rows per org (active and inactive) and `api_key` length (F1a-3's 4 KiB, enforced now). Add a DB `CHECK (octet_length(encrypted_api_key) <= …)`.
- **Bound in SQL.**
  - A per-org `LATERAL … LIMIT 129`.
  - Do not ship the token at all: F1a-2 needs only its presence, length and shape. Server-side `md5` over 1.4 MB tokens was also cancelled, so the cap must come first.
  - Read `created_at` with `CASE WHEN isfinite(…)`.
- **Chunk by a row and byte budget**, not by an org count.
- **On any chunk error, bisect down to the org**, then write that org a fail-closed `unavailable` safety record. That needs no source rows. Count it and alarm. A source-read error never fails the round. Do the same on a per-org assembly failure, instead of "previous record stays".
- **Extend T2.5** with: a chunk cancelled by `statement_timeout`, a driver `DataError`, an infinity timestamp, and an oversized token.

### B2. A MODEL repair cannot complete at 100k orgs, and one failed repair withholds the stamp fleet-wide
**The read is not chunked.** §4.3 chunks the repair's *publish* only. The repair still *reads* the whole kind, bodies included, in one statement:

`Rehydrator.repair` → `self._snapshot(kind)` → `PostgresControlDB.snapshot` (`rehydrate.py:381-395`, `pg.py:592-618`).

**The plan's own evidence shows that read cancelled**: `pg_source_bench.txt`, "every MODEL state record … CANCELLED … after 842 ms", 221 MB at 100k × 5. At 64 models per org the bodies alone are ~2.6 GB (25.6 KB canonical each, measured), which is also re-hydrator memory.

**Any MODEL repair then fails on every round.** Repairs follow a Valkey restart or flush (`MISSING`), a lost publish (`STALE`), `COUNTS`, `INDEX` and `CONTENTS`. Each failure raises `ControlPlaneBoundExceeded`, and `_claimable` withholds the stamp for **every kind** (`rehydrate.py:604-641`). The fleet fails closed and cannot self-heal.

**A store writer gets amplification.** Tampering one index score makes deep verify (`CONTENTS`) republish the whole kind.

**Fix:**
- A keyset-paged repair read with a per-page budget, streamed into the chunked publish, manifest last under WATCH.
- A *targeted* republish of the differing keys for `CONTENTS`.
- T2.15 extended with a repair at 100k × 5 and at 10k × 64 under the 800 ms bound.

### B3. The heartbeat over-claims MODEL whenever MODEL projection is off or refused
This is a silent fail-open, past the 21 s bound of parent §6.10. In each case below the projector writes nothing for MODEL while `mark_projected` still heartbeats (`projector.py:224`).
- **The projector flag is turned off.** §8's R4 rollback says "records stay; nobody reads them". That is false once R5 or R6 is done.
- **The floor is lowered below 3** with `--allow-lower` (§4.2.1: "the projector writes nothing for it").
- **The revision table is missing because the control migration was rolled back** (§4.2.2: "a refusal of MODEL projection, logged").

The re-hydrator keeps stamping `floor.stamp_kinds`, model included, so gateways keep MODEL fresh. From then on, console deactivations, removals and (in F1a-3) rotations never reach a gateway. The only signal is a log line.

**Fix:**
- **A per-kind projector heartbeat**, e.g. `(kind, last_ok_at)`, advanced for MODEL only when every changed org's MODEL safety version is durable this round.
- **The re-hydrator covers a kind only when that kind's heartbeat is within `lag_ms`.** Projector refusal or flag-off then makes MODEL uncovered: fail closed, for MODEL only.
- **The floor CLI refuses to lower `min_generation`** below the generation of any kind in `stamp_kinds`.
- **Test:** after R6, apply each of the three cases and assert MODEL is unverified within `lag_ms + fresh_ms`.

### B4. MODEL work in the shared round is unbounded
A bulk change stops the fleet, and it delays the kill switch and key revocations.

**(a) Writes dominate the cost, and §5 leaves them out.**
- **Measured with the real writer path** (`StateWriter.put_many(require_new=True)`: real Postgres, real `ValkeyPublisher`, 100 records per chunk, the closest shape to `put_existing`):

  | Bodies | Median per chunk | Max per chunk |
  |---|---|---|
  | typical 5-model | 227 ms | 383 ms |
  | 64-model | 527 ms | 651 ms |

  That is 2.3–5.3 ms per org with a safety write, against 0.3–0.6 ms per org of reads.
- **The real limit is far lower than stated.** §5's "26k–53k changed orgs per `lag_ms`" counts reads only. With safety writes the budget is about 5k orgs at 5 models and about 2k at 64 models, before any discount for load.
- **The lag budget is shared with the previous round.** `last_ok_at` is the round's `as_of` (`pg.py:463-473`), so the lag also spans the previous round's post-heartbeat work.
- **10% churn at 100k orgs breaks it.** 10k orgs cost ~23 s of writes plus 3–6 s of reads. The heartbeat misses `lag_ms` and the fleet fails closed.

**(b) The initial projection (R4) breaks it at 64 models per org.** The add budget is 4 × 500 orgs per round, which costs ~10.5 s of writes plus ~2.9 s of 100-org chunk reads after the heartbeat. The next heartbeat is then past 16 s, so the base kinds' stamp is withheld fleet-wide during R4.

**(c) The trigger fires on every `UPDATE` of `core_llmmodelconfig`, including columns MODEL never projects.**
- **Measured:** a bulk `routing_priority` update bumped **10,000/10,000** orgs, with no MODEL change.
- **An existing command does exactly this.** `differentiate_routing_catalog --org all` calls `cfg.save(update_fields=[cost…, updated_at])` on every row (`management/commands/differentiate_routing_catalog.py:80`). Every org is then re-read before the heartbeat.

**(d) The kill switch and revocations wait.** While 3m runs, the *next* round's KS-on and KEY revokes wait behind it. The kill switch's one-round latency becomes "however long MODEL takes".

**(e) The plan breaks its own principle on the projector side.** §3.1 item 4 says a MODEL gap must never stale identity, plans or the kill switch. That holds on the stamp side; on the projector side a MODEL backlog stops the one shared heartbeat.

**Fix:**
- Decouple MODEL from the base heartbeat: its own time-boxed phase or loop, plus B3's per-kind heartbeat, so a MODEL backlog stales only MODEL.
- Time-box MODEL work per round, before and after the heartbeat.
- Make the add and liveness budget byte- or time-based.
- Use a column-filtered, statement-level trigger. **Measured:** with transition tables it bumped 0 orgs on a non-projected bulk update, and on a projected one it does one upsert per org.
- Replace T2.16's CPU-only budget on a twin with a wall-clock round test on real Postgres and Valkey at 100k × 5, with 1% and 10% churn, asserting base heartbeat age and KS-on latency.

### B5. The view LRU is bounded by count while sized for a typical org, so a worker blows its memory contract
Measured with the author's own parser (`view64.py`, tracemalloc):

| Org shape | Canonical body | View bytes per org | Full 6,788-org view |
|---|---|---|---|
| 5 models | 2,087 B | 6,139 B | 40 MiB |
| **64 models (plan cap)** | 25,579 B | **73,018 B** | **473 MiB** |
| 64 models + 64 omitted | 42,730 B | 87,853 B | 569 MiB |
| worst | 452,010 B | 549,162 B | **3,555 MiB** |

At the plan's own per-org cap, the 10% share holds 574 orgs, not 6,788, and a full view exceeds the whole 400 MiB worker RSS (`resources.py:25`). Parent §6.4 asks for a contract-derived bound.

**Fix:**
- A byte-weighted LRU: charge each org its parsed size, or canonical bytes × the measured ~2.9 factor, and cap by bytes.
- An admission ceiling per org: serve above it uncached, like the C36 rule.
- An `amf_model_view_bytes` gauge.
- T2.8 asserts bytes at the 5-model, 64-model and worst shapes.

### B6. The breaker cannot trip at the target scale
**The arithmetic.**
- §4.2.7 trips at `max(128, 5% of served entries)` per round.
- MODEL only derives changed orgs plus 1,000 reconcile orgs per round.
- At density `d` and `N` orgs, a systemic fault therefore surfaces at most about `1,000·d` newly-omitted entries per round, against a threshold of `0.05·N·d`.
- It never trips once `N > 20,000`. At 100k × 5 that is about 5,000 against 25,000.

**The consequence.** A bad projector release, or (in F1a-3) a wrong key, empties catalogues 1,000 orgs per round, all of them in about 100 s, with no trip. That is exactly what parent §6.6 requires the breaker to stop.

**The hold is per org, not per entry.** "Every org write that would make such a transition is held" also holds a console fact made in the same org (an operator deactivation), which contradicts "console facts are never held".

**Fix:**
- A ratio over entries *examined* this round, with a minimum sample (for example ≥ 128 entries across ≥ 3 orgs).
- A cumulative per-reason window across one reconcile pass.
- Hold per entry: keep `H`'s entry for the systemic transitions only.
- Extend T2.6 with the reconcile-drain case at 100k.

## SHOULD-FIX

**S1. Control migration and triggers** (§4.2.2, §7.3). Measured in `pg_review_bench` §5:
- **New deadlocks.**
  - Two console transactions touched *disjoint* rows of orgs 21 and 22, in opposite order.
  - Without the trigger both commit; with it, one gets `DeadlockDetected`.
  - Statement-level triggers that bump in `org_id` order remove the case within one statement. State the residual across statements.
- **Lock queue.**
  - `CREATE TRIGGER` waiting behind one open console write made an *unrelated* console write block until its 2 s timeout.
  - With `lock_timeout = 250ms` the DDL fails fast instead.
  - The migration needs `lock_timeout` plus a retry, and a non-atomic split from the backfill.
  - The `default_model` 64 → 128 widen is metadata-only (same filenode, verified), but it takes ACCESS EXCLUSIVE, so it needs the same `lock_timeout`.
- **Bulk cost.** Row triggers made a 640k-row bulk update 3.5× slower (147 s against 41 s).
- **Cascades.** One org delete bumped `rev` 64 times. The rev row also outlives the org.
- **Missed triggers.** `session_replication_role = replica` (logical-replication apply, `pg_restore --disable-triggers`) missed a full deactivation.
  - The projector should check `pg_trigger.tgenabled` every round and refuse (MODEL uncovered, per B3) when a trigger is off.
  - The runbook should name the replica-role case, which no catalogue check sees.
- **Rollback and re-apply.** That sequence resets `rev` to 1, so memo equality misses edits made in the gap. Add a source epoch, stored in the memo.

**S2. The cold-fetch replay bound applies per replay** (§4.4.3, §6). A store writer that keeps write access replays again after each repair, so "served for at most 60 s + 30 s" should read "per replay; unbounded for a persistent writer; detected and alarmed". In F1a-3 the replayed record can carry a rotated credential. Name the alarm (a repair with `kind=model reason=contents`, sweep evictions > 0) and the runbook action: treat it as a store compromise and rotate the state key.

**S3. Floor poisoning and operator error** (§3.2).
- The floor table has no CHECK constraints: `stamp_kinds ⊇ {plan,key,ks,budget}`, the kind-name grammar, a range for `min_generation`.
- Nothing defines what happens when the row is missing, malformed or unreadable, and how that interacts with the Postgres ride-through.
- The CLI does not check that any re-hydrator can stamp a newly named kind, or that every member is at the new `min_generation`. One `floor set` can therefore withhold the stamp fleet-wide in about 21 s. Have members report `(generation, capabilities, seen_at)` in Postgres, and make the CLI refuse unless they satisfy the change (`--force` logs at CRITICAL).
- A replay of an older signed `{ns}:floor` can make a below-floor gateway ready. That needs a store writer and an old image, so keep a high-water mark.

**S4. Slug reuse and renames.**
- **Classify a "new" org by whether its record key exists.**
  - A tombstoned key must go through `put_existing`.
  - Otherwise `put_many(require_new=True)` raises `RecordExists` after the heartbeat, every round, and stops step 6 (KEY adds) fleet-wide.
- **Bind MODEL to the immutable control org id.** Carry an `org_pk` in the KEY and MODEL bodies and check equality at pin.
  - Without it, a stale principal for a renamed org can resolve the new same-slug org's models during KEY/MODEL drain skew.
  - From F1a-3 that includes its credentials.

**S5. Filter semantics in `S`** (§4.2.4, §4.2.3). `allowed` must be the *intersection* of `H` and `D`, not "the more restrictive" list. An allowlist whose names are all dropped must become deny-all (`[]`), never `null`.

**S6. `H` for changed orgs is unspecified.** The safety computation needs `H`'s body, and MODEL is out of the snapshot. Bodies can reach 512 KiB. State where `H` is read, chunk that read by bytes, and measure it.

**S7. Isolate the reader as well as the applier.** §3.1 item 7 covers appliers only. `_envelope` catches only `ValueError`/`UnicodeDecodeError` (`state_sig.py:290-293`), so a deeply nested stored value raises `RecursionError` out of `poll` and aborts `drain_all` for every kind. Wrap `poll` and `poll_sparse` the same way: class name only, error scoped to the kind.

**S8. Logs.**
- The generic handlers log exception *text*: `projector_service.py:280` and `edge/state_runtime.py:438`. MODEL paths must log class names only, and L2.6 should inject failures through those handlers.
- B1's "do not select the token" also keeps Fernet tokens out of F1a-2's projector entirely.
- Specify the plaintext type of F1a-3's `CredentialSealer` hook as a redacting secret type.

**S9. Operability.**
- **Runbook.** There is none. Add one entry per failure class:
  - breaker open, and when `AMF_PROJECTOR_BREAKER_ALLOW` is appropriate;
  - reconcile fixes > 0 (check the triggers);
  - floor refusal;
  - MODEL repair failing;
  - view memory;
  - a spike of unavailable orgs;
  - sweep evictions > 0 (store compromise).
- **Alerts.** Enumerate them.
- **Metrics.** Labels are closed vocabularies with no org label, which is good. Pre-seed every label value at 0. Add:
  - MODEL phase duration and per-kind heartbeat age;
  - changed orgs per round;
  - chunk bisections and isolated orgs;
  - repair duration;
  - view bytes and fill hit ratio.
- **Rollback.**
  - R4's rollback is unsafe after R5 (B3).
  - Control-migration rollback is missing from §8.
  - `--allow-lower` below 3 must be refused while `stamp_kinds` names `model`.

**S10. Cold-start Valkey load.**
- Every worker warms its own view through fills (three ops plus a body each), so warming a fleet moves workers × view bytes out of one Valkey. That is 200 × 40 MiB ≈ 8 GB at 5 models, and far more at 64.
- State this load, cap fill concurrency and rate per worker, and add a fill-rate metric as the F1d baseline.

## NITs
- **N1.** In `S`, keep `H.default` while it is still served, instead of `null`; otherwise `auto` is briefly refused.
- **N2.** The index sweep's single `ZMSCORE` of up to 6,788 members blocks Valkey for the length of the call. Chunk it, at about 500 members.
- **N3.** Cold-fill parsing runs on the request path: 753 µs at 64 models and ~3 ms worst (measured). Offload it above a size threshold, as the delta path does.
- **N4.** The reconcile batch is fixed at 1,000 orgs, which at 64 models is 64k rows per round. Budget it in rows (see B1).
- **N5.** Add a test that a 100k-record MODEL burst drains within `fresh_ms`. The budget is `DeltaBudget(256)` × 64 rounds per cycle.

<details><summary>Evidence: commands and outputs (Reviewer B prototypes, not in the repo)</summary>

Run from `gateway_v2/` with `../../venv-ci/bin/python`, Postgres under `flock .ctl-db.lock nice -n 10`.

`view64.py` reuses the author's `model_record_bench.parse` and `body` unchanged:
```
| typical 5 models | 2,087 | 6,139 | 6,832 | 40 MiB | 75 |
| 64 typical models (plan cap, 51-byte keys) | 25,579 | 73,018 | 574 | 473 MiB | 753 |
| 64 typical models + 64 omitted | 42,730 | 87,853 | 477 | 569 MiB | 809 |
| worst (64 max-length, 4 KiB keys, 64 omitted) | 452,010 | 549,162 | 76 | 3,555 MiB | 3,049 |
```
Columns: body bytes | view bytes per org | orgs that fit the 40 MiB share | full 6,788-org LRU | decode+parse µs.

`pg_chunk5b.py`: 2,000 orgs × 5, the author's `CHANGED` SQL, RR + `statement_timeout=800`:
```
500 orgs x 5 models: ok 29 ms | ok 67 ms | ok 25 ms
499 x 5 + one org with 192 rows x ~1.4 MB token: ok 1,099 ms | CANCELLED 831 ms | CANCELLED 837 ms
499 x 5 + one org with 250,000 inactive rows: CANCELLED 821 | 811 | 813 ms
```

`pg_review_bench.py`: 10,000 orgs × 64 plus two tenant orgs, author's schema and trigger:
```
plan chunk: 500 orgs x 64 models: CANCELLED (QueryCanceled) after 886 ms
100 orgs x 64 models: ok: 6,400 rows, 144 ms
bulk UPDATE of NON-projected routing_priority, 640k rows, row trigger: 147.1 s; orgs bumped 10,000
same, trigger disabled: 41.4 s
statement trigger + transition tables + projected-column filter: non-projected 59.3 s, bumped 0; projected 48.9 s, bumped 10,000
session_replication_role=replica: org deactivated every model, rev 2 -> 2 (missed)
org delete cascades 64 model deletes: rev 2 -> 66; rev row outlives the org
default_model varchar(64)->(128): filenode unchanged (no rewrite)
deadlock, disjoint rows: trigger off T1=committed T2=committed; trigger on T1=DeadlockDetected T2=committed
CREATE TRIGGER queued behind one open write: unrelated write QueryCanceled after 2,001 ms; lock_timeout=250ms -> LockNotAvailable 251 ms
put_many(100 records, 5-model bodies): median 227 ms, max 383 ms per chunk (real StateWriter + PostgresControlDB + ValkeyPublisher)
put_many(100 records, 64-model bodies): median 527 ms, max 651 ms per chunk
```

psycopg on `'infinity'::timestamptz`: `DataError timestamp too large (after year 10K)`. `extract(epoch FROM 'infinity'::timestamptz)::bigint`: `FeatureNotSupported cannot convert infinity to bigint`.
</details>


---
_Generated by [Claude Code](https://claude.ai/code)_
