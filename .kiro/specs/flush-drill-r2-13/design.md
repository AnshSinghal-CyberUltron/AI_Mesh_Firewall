# Design Document — B8 / R2-13 (partial): local flush chaos drill + declared store-failure behaviour doc

## Overview

B8 (R2-13, partial) delivers the **locally buildable half** of R2-13: a **flush chaos drill** that
runs against the already-shipped `gateway_v2/state_control/` in-memory twins, plus a short **declared
store-failure behaviour** doc section. The failure this reproduces is the C36 "store flush, Postgres
untouched" fault — the published copy is wiped and the durable truth is intact — and the drill
asserts that one re-hydrator round restores every kind from the durable truth with **0
Restore_Violations**, exactly as C36 measured live ("0 violations in all 15 runs").

**This card is a TEST plus a DOC — no new production module.** Every mechanism the drill exercises is
already shipped: `MemoryStore` (the in-memory StateStore + StatePublisher twin, with `flush()` and
the re-hydrator-only whole-kind `publish_kind`), `MemoryControlDB` (the transactional Postgres twin a
flush does **not** touch — the durable source of truth), `Rehydrator` (`round_once(deep=…) ->
RoundSummary`, `RepairEvent`, reasons `MISSING`/`STORE_AHEAD`/`STALE`/`VERSION`), `StateWriter` (the
real seed-propagation path), and `ALL_KINDS` / `StateKind`. The drill rebuilds **nothing**; it drives
these pieces and verifies the restore invariant.

**Generalize the shipped pattern.** `gateway_v2/tests/state_control/test_lgw05b_rehydrate_stamp.py`
already contains the exact lab in miniature: a `_lab()` helper building `MemoryControlDB` +
`MemoryStore` + `StateWriter` + `Rehydrator`, a `_seed(writer)` that does one write per kind, and the
shipped flush→restore assertion `_seed(writer); store.flush(); summary = rehydrator.round_once();
assert {event.kind for event in summary.repairs} == set(StateKind)`. B8 **generalizes** that single
example into a seeded, randomized, multi-kind property: many orgs/keys/killswitches/budgets, flush, a
round, then a **deep** verify that the restored store agrees with the durable truth across every
kind (0 violations), carried as a seeded `random.Random` ≥ 10,000-iteration property test, plus the
doc.

**Correctness, not a wall-clock figure.** The RC2 live flush-restore bound (≈ 2.05 s against a live
Valkey; FLUSHALL restore 0.456 / 1.249 / 0.610 s) is a cloud measurement and is **not** comparable to
an in-memory twin. The local `Declared_Flush_Bound` is therefore expressed deterministically in
**re-hydrator rounds** — restored within **one** round, confirmed by a second round that is a no-op —
and the ≈ 2.05 s wall-clock certification is named a `Deferred_Cloud_Gate` (G-14), not asserted here.

**Authority and source of truth.** The binding authority is the **R2-13** register row (§10.5.2 /
GW05 / T21), the **C36** measured-facts rows (§0.1, §0.2), the **L05b-3** acceptance test, and the
**G-14** deferred cloud-overlap gate in `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` (owning
card for R2-13's amendment: **T21**); the local framing is `docs/LOCAL_WORKLIST_v3.md` block "B8". The
9 approved requirements in `requirements.md` are the proximate contract and every section below
traces to them (see §Traceability).

**Scope posture.** B8 is a **MEDIUM**, narrow, low-value Category-1/2 card — the smallest card so far,
explicitly smaller than B6/B7. It depends on **nothing new**; the re-hydrator and the twins are
shipped. The L05b-3 held-`FOR UPDATE`-lock flavour and queued-writer fail-fast are honoured only
insofar as the in-memory twin supports them (it has no real lock), so the card does **not** fake a
lock — those parts are deferred to the live gate (Req 6; §Deferred).

---

## Architecture

### Deliverable shape — a test file and a doc file, nothing else (Req 8)

| Artifact | Path | Nature |
|---|---|---|
| The flush chaos drill | `gateway_v2/tests/state_control/test_flush_drill_r2_13.py` | A pytest module (property + example tests). Test files sit **outside** the import-linter `root_packages = ["gateway_v2"]` contract (Req 8.1). |
| The declared-behaviour doc | `gateway_v2/state_control/docs/declared-failure-behaviour.md` | A markdown doc section co-located with the component it describes (Req 7). |

**Why `test_flush_drill_r2_13.py` under `tests/state_control/`.** It is a sibling of the reference
`test_lgw05b_rehydrate_stamp.py` and `test_lgw05c_writer.py`, so it reuses their established import
surface directly: `SECRET` and the `_plan_body` helper from `tests.state_control.test_lgw05c_writer`,
and the lab/seed shape from `test_lgw05b_rehydrate_stamp`. Keeping it beside the reference keeps the
"generalize the shipped example" intent legible and avoids a new test package.

**Why the doc is co-located under `state_control/docs/`.** The decision is to place the
declared-behaviour doc **beside the component it documents** (`gateway_v2/state_control/`) rather than
in the repo-wide `docs/` tree. Rationale: the subject is the store-failure semantics of the shipped
`state_control` twins and re-hydrator specifically, the audience is the maintainer reading that
package, and co-location keeps the doc discoverable with the code whose behaviour it certifies. It is
a leaf markdown file (`.md`), so it is invisible to setuptools package discovery, to ruff/mypy `.py`
collection, and to the `.py`-only AST gates. (The alternative — a section appended under the top-level
`docs/` runbook tree — is viable but further from the code; co-location is preferred and the runbook
already carries the authoritative R2-13 row that this doc cites back to.)

**No production code, no pyproject edits.** This card introduces **no** new production store,
re-hydrator, or state module and makes **no** edit to any file under `gateway_v2/state_control/*.py`,
`gateway_v2/domain/`, or `gateway_v2/runtime/` (Req 8.2). It makes **no** `pyproject.toml`,
`[tool.importlinter]`, ruff, or mypy configuration change: a new test file and a new `.md` doc need
none. Any seeding helper the drill needs is a small test-local function living **inside the test
file** (Req 8.3) — never a production module.

### Runs against the shipped twins, deterministic and offline (Req 8.4)

The drill is entirely in-memory: `MemoryStore` + `MemoryControlDB` wired through the real
`StateWriter` and driven by the real `Rehydrator`, with a seeded `random.Random` and no server, no
Postgres, no Valkey. Determinism comes from the seed; every iteration is reproducible. The re-hydrator
is constructed with `stale_grace_s=0.0` and a fixed `clock=lambda: <const>` (as the reference `_lab`
does) so a round neither sleeps nor varies with wall-clock time.

### Known-finding non-impact note (Req 8.5)

The pre-existing R2-05 finding — `state_control/valkey.py:100` `check_tenant_scale` trips on the
deep-verify path — is on the **live Valkey** adapter, not the in-memory twin. The flush drill uses
`MemoryStore` / `MemoryControlDB` and the `Rehydrator`'s deep verify reads `MemoryStore.stored_index`,
so the drill is **unaffected**; it does not touch `valkey.py`. `mypy --strict`, `ruff`, the AST gates
(which parse `gateway_v2` `.py` only and never read a test or a `.md`), and `unscanned_channels() ==
()` all stay green and unchanged.

---

## Components and Interfaces

The drill is a single flow the test drives, bound to the real shipped APIs. It is expressed as a
pure, deterministic function over a seeded `random.Random`, so the property test can call it ≥ 10,000
times.

### The lab (step 1) — reuse the shipped builders (Req 8.2, 8.3)

```python
# test-local, in test_flush_drill_r2_13.py — mirrors _lab() in test_lgw05b_rehydrate_stamp.py
def _lab() -> tuple[StateWriter, MemoryControlDB, MemoryStore, Rehydrator]:
    db = MemoryControlDB()
    store = MemoryStore()
    writer = StateWriter(db, store, SECRET)                      # the real seed/propagation path
    rehydrator = Rehydrator(
        db, store, writer, SECRET,
        stale_grace_s=0.0, clock=lambda: 1_000.0,                # deterministic, no sleep
        kinds=ALL_KINDS,                                         # full coverage over all four kinds
    )
    return writer, db, store, rehydrator
```

`SECRET` and `_plan_body` are imported from `tests.state_control.test_lgw05c_writer` (no new secret,
no duplicate helper — Req 8.3).

### Seeding generator (step 2) — randomized multi-kind state through the real writer (Req 1, Req 3.3)

A test-local generator seeds **every** kind through `StateWriter`, never by fabricating store contents
directly, so the drill exercises the real commit-then-publish propagation contract (Req 1.1, 1.3):

```python
def _seed_random(writer: StateWriter, rng: random.Random) -> None:
    # tiny bounded counts keep 10k iterations fast; every kind gets >= 1 write
    for i in range(rng.randint(1, 4)):                          # StateKind.PLAN
        org = f"org-{i}"
        writer.plan_set(org, _plan_body(org))
    for i in range(rng.randint(1, 4)):                          # StateKind.KEY
        writer.key_add(f"hash-{i}", f"org-{i}", key_id=f"k-{i}",
                       rate_per_s=rng.uniform(1.0, 50.0), burst=rng.uniform(1.0, 50.0))
    for i in range(rng.randint(1, 3)):                          # StateKind.KS (engaged set varies)
        writer.killswitch(f"scope-{i}", on=rng.random() < 0.5)
    for i in range(rng.randint(1, 4)):                          # StateKind.BUDGET
        writer.put(StateKind.BUDGET, f"org-{i}", {"limit": rng.randint(1, 10_000)})
```

Bounds are deliberately small (a handful of records per kind) so a ≥ 10,000-iteration property run
stays fast while still varying structure, counts, keys, budgets, and the KS engaged set. The KS branch
toggling `on=` is what makes `on_count` / `engaged_count` a non-trivial part of the restore check.

### Pre-flush baseline (step 3) — store head agrees with durable counters (Req 1.2)

After seeding, the drill asserts the pre-flush state is a known-good baseline: for every kind,
`store.stored_feed_seq(kind) == db.counters(kind).feed_seq`, and the deep index agrees
(`_restored_matches_durable` below, applied pre-flush). This proves the writer propagated what it
committed before the flush is introduced.

### Flush (step 4) and the flushed-store assertion (step 5) — Req 2

```python
store.flush()                                                   # Req 2.1 — manifests/records/index/engaged/feed_seq/stamp cleared
for kind in ALL_KINDS:
    assert store.stored_feed_seq(kind) is None                  # Req 2.2 — no verifiable published position
    assert store.stored_head(kind).manifest_raw is None
assert all(db.counters(kind).feed_seq >= 0 for kind in ALL_KINDS)  # Req 2.3 — durable truth retained (counters unchanged by flush)
```

`MemoryStore.flush()` clears `_manifests`, `_records`, `_index`, `_engaged`, `_feed_seq`, `_stamp`,
`_stamped` (and `MemoryControlDB` is never touched by it — the durable truth survives, Req 2.3).

### The re-hydrator round (step 6) — every kind repaired with reason MISSING (Req 3)

```python
summary = rehydrator.round_once(deep=True)                      # one round, deep so the index is also verified
assert {event.kind for event in summary.repairs} == set(StateKind)   # Req 3.1 — every flushed kind repaired
assert all(event.reason == MISSING for event in summary.repairs)     # Req 3.1 — reason is MISSING (store head gone)
assert summary.errors == ()                                     # Req 3.3 — no per-kind errors; summary.ok is True
```

A flushed store has `manifest_raw is None` for every kind, so `Rehydrator._diagnose` returns `MISSING`
and `repair` restores each kind via the re-hydrator-only whole-kind `publish_kind` path (Req 3.2 —
confirmable via `store.whole_kind_publishes == len(ALL_KINDS)` after the round).

### Deep verify (step 7) — the exact "0 Restore_Violations" comparison (Req 4)

The success criterion is **0 Restore_Violations**: after the restoring round, the restored store
agrees with the durable truth across every kind. The comparison uses the real APIs, per kind:

```python
def _restored_matches_durable(store: MemoryStore, db: MemoryControlDB) -> list[str]:
    violations: list[str] = []
    for kind in ALL_KINDS:
        counters = db.counters(kind)                            # KindCounters: version, feed_seq, count, on_count
        head = store.stored_head(kind)                          # StoredHead: manifest_raw, index_count, index_top, engaged_count
        # (a) position: the store's published feed_seq equals Postgres's (Req 4.1)
        if store.stored_feed_seq(kind) != counters.feed_seq:
            violations.append(f"{kind}: feed_seq {store.stored_feed_seq(kind)} != {counters.feed_seq}")
        # (b) counts: index entry count and record count agree with the durable count (Req 4.1)
        if head.index_count != counters.count:
            violations.append(f"{kind}: index_count {head.index_count} != count {counters.count}")
        # (c) engaged set size matches on_count (KS kill-switch engaged scopes) (Req 4.1)
        if head.engaged_count != counters.on_count:
            violations.append(f"{kind}: engaged {head.engaged_count} != on_count {counters.on_count}")
        # (d) DEEP: the store's full per-key index equals the durable positions (Req 4.2)
        _c, records, _engaged = db.snapshot(kind)
        durable_index = {record.key: record.feed_seq for record in records}
        if dict(store.stored_index(kind)) != durable_index:
            violations.append(f"{kind}: index {dict(store.stored_index(kind))} != {durable_index}")
    return violations

assert _restored_matches_durable(store, db) == []               # Req 4.3 — 0 Restore_Violations
```

This is the precise contract of "0 violations", stated against shipped APIs: `stored_feed_seq` /
`stored_head` / `stored_index` on the Store_Twin versus `db.counters` / `db.snapshot` on the
Durable_Twin, covering position, counts, the KS engaged set, and the full per-key index (the one
class a shallow diagnose cannot see — a missing entry masked by an extra). A non-empty return list is
the Restore_Violation report (§Error Handling).

### Round bound (step 8) — one round, second round is a no-op (Req 5)

```python
second = rehydrator.round_once(deep=True)                       # immediately after the restore
assert second.repairs == ()                                     # Req 5.3 — no further repair for any kind
assert second.ok                                                # the restore completed within ONE round (Declared_Flush_Bound)
```

The `Declared_Flush_Bound` is asserted as a **deterministic round count** (one round restores; a
second round repairs nothing — Req 5.1, 5.2, 5.3), never as a wall-clock duration. The ≈ 2.05 s RC2
figure is documented as the deferred cloud figure (Req 5.4; §Deferred), not asserted locally.

### L05b-3 flavour, honest to the twin (Req 6)

An example test models in-flight durable writes **around** a flush (seed, do a few extra
`writer.plan_set` / `writer.put` durable writes, then `store.flush()`, then a round) and asserts the
restore is still clean with 0 Restore_Violations (Req 6.1). It explicitly does **not** fake a `FOR
UPDATE` lock or a queued-writer fail-fast — the in-memory twin has no such lock (Req 6.2); the test's
docstring records that the held-lock behaviour and the fail-fast outcome are deferred to the live
gate (Req 6.3; §Deferred).

### The declared-behaviour doc (Req 7)

`gateway_v2/state_control/docs/declared-failure-behaviour.md` publishes the measured + declared
store-failure semantics with these sections, each citing the C36 facts:

1. **Flush → re-hydrator restore.** A store flush is restored from the durable Postgres source of
   truth by the re-hydrator, with 0 violations — citing C36 (FLUSHALL restore 0.456 / 1.249 / 0.610 s,
   bound ≈ 2.05 s; store flush ×3 restored in 0.46–1.25 s; the 10 s re-hydrator gap yielded only
   declared 503s with **0 violations in all 15 runs**) (Req 7.1).
2. **Cloud SQL failover — no data-plane impact under C36** (0 × 503 to tenants, 0 acknowledged writes
   lost) (Req 7.2).
3. **Valkey planned-maintenance failover ≈ 140 declared 503s** (Req 7.3).
4. **Valkey has no manual failover**, so a data-loss failover drill requires **Redis Standard HA or a
   flush drill** (Req 7.4).
5. **Drill delivery table (Req 7.5)** — what is local vs deferred:

   | Drill | Status |
   |---|---|
   | Flush drill (store wiped, Postgres untouched → re-hydrator restore, 0 violations) | **Delivered locally** (`test_flush_drill_r2_13.py`) |
   | Partition drill | Deferred (G-13 — needs real stores) |
   | Redis forced-failover drill | Deferred (G-13 — needs real stores) |
   | Cloud SQL + store overlap | Deferred (G-14, owned by GW05b) |
   | ≈ 2.05 s wall-clock flush-restore certification | Deferred (live/cloud figure, not the local round-count bound) |

---

## Data Models

**No new production value types — the drill reuses the shipped `state_control` types.** The comparison
reads shipped frozen value types directly: `KindCounters` (`db.py`: version, feed_seq, count,
on_count), `StoredHead` (`publisher.py`: manifest_raw, index_count, index_top, engaged_count),
`RoundSummary` / `RepairEvent` (`rehydrate.py`), `SignedRecord` / `Manifest` / `StateKind` /
`Version` (`domain/state.py`), and `ALL_KINDS` (`rehydrate.py`).

The only test-local shapes are helper functions (`_lab`, `_seed_random`, `_restored_matches_durable`)
and the ephemeral `list[str]` of violation strings they return — all inside the test file, no module
state, no new dataclass. No capacity/SLO number is a field anywhere in this card.

## Correctness Properties

*A property is a characteristic that should hold true across all valid executions of the system — a
formal statement bridging the human-readable spec and machine-verifiable correctness.* The drill is a
**pure, deterministic, offline** function over a seeded `random.Random`, so these are exercised with
the house idiom: seeded `random.Random`, ≥ 10,000 iterations, no hypothesis (Req 4.4, Req 8.4).

### Property 1: Flush then a round restores the store to agree with the durable truth, 0 violations
*For any* seeded multi-kind state written through `StateWriter`, flushing the Store_Twin and running
one re-hydrator round restores the store so that, for **every** kind in `ALL_KINDS`, the restored
published head (position, record/index count, engaged-set size) and the full per-key index equal the
Durable_Twin's counters and positions — i.e. `_restored_matches_durable(store, db) == []` (0
Restore_Violations). **Validates: Requirements 4.1, 4.2, 4.3, 4.4**

### Property 2: Every flushed kind is repaired with reason MISSING
*For any* seeded multi-kind state, after `store.flush()` a re-hydrator round repairs exactly the set
`set(StateKind)`, each via a `RepairEvent` whose reason is `MISSING`, with no per-kind errors
(`summary.ok`). **Validates: Requirements 3.1, 3.2, 3.3**

### Property 3: The restore is a one-round bound and the second round is a no-op
*For any* seeded multi-kind state, the restore completes within the `Declared_Flush_Bound` of **one**
re-hydrator round, and a second round run immediately after performs no repair for any kind
(`second.repairs == ()`), confirming completeness as a deterministic round count rather than a
wall-clock figure. **Validates: Requirements 5.1, 5.2, 5.3**

### Property 4: A flush never touches the durable truth
*For any* seeded multi-kind state, `store.flush()` leaves the Durable_Twin unchanged: for every kind,
`db.counters(kind)` and `db.snapshot(kind)` are byte-for-byte identical before and after the flush, so
the restore always has the full durable state to recover from. **Validates: Requirements 2.1, 2.3**

### Property 5: In-flight durable writes around a flush still restore cleanly (L05b-3, honest)
*For any* seeded multi-kind state with additional durable writes committed around the flush, flush then
a re-hydrator round still restores every kind with 0 Restore_Violations — without modelling any `FOR
UPDATE` lock the in-memory twin does not have. **Validates: Requirements 6.1, 6.2**

## Error Handling

- **What a Restore_Violation looks like.** `_restored_matches_durable` returns a non-empty `list[str]`
  naming the exact divergence per kind — a mismatched `feed_seq`, an `index_count` not equal to the
  durable `count`, an `engaged_count` not equal to `on_count`, or a per-key index that differs from
  the durable positions (a missing entry, an extra entry, or a wrong position). The drill asserts the
  list is empty; a failure prints the offending kind and both sides, so a regression in the shipped
  re-hydrator or twin is diagnosable from the assertion message alone.
- **A missing or errored repair surfaces as a failed round.** If a kind failed to restore, it would
  appear in `summary.errors` (and the repaired-kind set would not equal `set(StateKind)`); the drill
  asserts `summary.errors == ()` and the full repaired set, so a partial restore fails the test rather
  than passing silently.
- **The honest L05b-3 limit.** The in-memory twins have **no** real `FOR UPDATE` lock and **no**
  queued-writer fail-fast path, so the drill does not assert either — attempting to would be faking a
  mechanism the twin does not provide. The held-lock-while-flushed behaviour and the queued-writer
  fail-fast outcome of L05b-3 are deferred to the live gate and recorded as such in the doc and the
  test docstring (Req 6.3).
- **Determinism.** The seed fully determines each iteration; the re-hydrator runs with
  `stale_grace_s=0.0` and a fixed clock so no sleep or wall-clock dependence can flake a round.

## Testing Strategy

**This card is a test and a doc only** — it ships no production code and no property/implementation
module beyond the test file.

### Property-based testing applies here

The flush→round→verify drill is pure logic over a large input space (randomized multi-kind seed
states), the archetype PBT fits. House idiom: seeded `random.Random`, ≥ 10,000 iterations, **no
hypothesis** (Req 4.4, Req 8.4). Properties 1–5 in §Correctness Properties each map to one property
test, tagged **Feature: flush-drill-r2-13, Property N: {property text}**. Source states are kept tiny
(a handful of records per kind) so the 10k-iteration run stays fast.

### Example / integration tests (specific scenarios)

- **MISSING-reason-per-kind**: a representative seed, flush, round → assert every repair's reason is
  `MISSING` and `whole_kind_publishes == len(ALL_KINDS)` (the re-hydrator-only restore path, Req 3.2).
- **One-round bound + second-round no-op**: the explicit Req-5 assertion (`second.repairs == ()`).
- **L05b-3 honest flavour**: a flush with durable writes around it still restores cleanly, with an
  explicit comment that no `FOR UPDATE` lock is modelled (Req 6).
- **Pre-flush baseline**: after seeding, the store head agrees with the durable counters (Req 1.2),
  guarding that the drill starts from a known-good state before the flush is introduced.

### Gates (Req 8.5)

`mypy --strict`, `ruff`, the `.py`-only AST gates, `lint-imports` (the test stays outside
`root_packages`), and `unscanned_channels() == ()` all stay green and **unchanged** — the card adds a
test file and a `.md` doc and edits no production source, no config. The known `valkey.py:100`
`check_tenant_scale` finding is on the live Valkey path and does not touch the in-memory drill.

## Deferred

Named seams only — nothing below is built or scaffolded by this card (Req 9).

| Deferred item | Owner | Status |
|---|---|---|
| **Partition drill** (needs real stores) — `Deferred_Cloud_Gate` | G-13 / fleet | out of scope; not delivered here (Req 9.1, 9.4) |
| **Redis forced-failover drill** (needs real stores) — `Deferred_Cloud_Gate` | G-13 / fleet | out of scope; not delivered here (Req 9.1, 9.4) |
| **Cloud SQL + store overlap drill** — `Deferred_Cloud_Gate` | **GW05b** (G-14) | named; not delivered here (Req 9.2) |
| **Live ≈ 2.05 s wall-clock flush-restore certification** — `Deferred_Cloud_Gate` | T21 / fleet | the cloud figure; the local drill asserts a round-count bound instead (Req 9.3) |
| **L05b-3 held-`FOR UPDATE` lock + queued-writer fail-fast** | live L05b-3 / G-14 | the in-memory twin has no lock; deferred (Req 6.3) |

## Traceability

| Design section | Requirements |
|---|---|
| Overview (test+doc split, generalize-the-shipped-pattern, round-not-wall-clock) | intro of Req 1–9; Req 5.4 |
| Architecture → Deliverable shape (test + doc paths, no production/pyproject change) | Req 8.1, 8.2, 8.3 |
| Architecture → Runs against shipped twins, deterministic/offline | Req 8.4 |
| Architecture → Known-finding non-impact | Req 8.5 |
| Components → Lab + seeding generator | Req 1.1, 1.3, 8.2, 8.3 |
| Components → Pre-flush baseline | Req 1.2 |
| Components → Flush + flushed-store assertion | Req 2.1, 2.2, 2.3 |
| Components → Re-hydrator round | Req 3.1, 3.2, 3.3 |
| Components → Deep verify (0 Restore_Violations) | Req 4.1, 4.2, 4.3 |
| Components → Round bound | Req 5.1, 5.2, 5.3 |
| Components → L05b-3 honest flavour | Req 6.1, 6.2, 6.3 |
| Components → Declared-behaviour doc | Req 7.1, 7.2, 7.3, 7.4, 7.5 |
| Data Models | Req 8.2, 8.3 |
| Correctness Properties | Req 2, 3, 4, 5, 6 (Req 4.4) |
| Error Handling | Req 4.3, 6.2, 6.3 |
| Testing Strategy | Req 4.4, 8.4, 8.5 |
| Deferred | Req 6.3, 9.1, 9.2, 9.3, 9.4 |
