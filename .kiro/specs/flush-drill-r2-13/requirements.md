# Requirements Document

## Introduction

This spec covers card **B8 = R2-13 (partial) "the local flush chaos drill + declared store-failure
behaviour doc"** — the locally buildable half of R2-13. The authority is the R2-13 register row
(§10.5.2 / GW05 / T21), the **C36** measured-facts rows (§0.2 and §0.1), the **L05b-3** acceptance
test, and the **G-14** deferred cloud overlap gate in
`docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`; the owning card for R2-13's amendment is **T21**.
The local-half framing is `docs/LOCAL_WORKLIST_v3.md`, block "B8".

R2-13 asks to **publish the declared store-failure behaviour** and run **chaos drills (flush,
partition, Redis forced failover)**. The register row records the measured semantics: Cloud SQL
failover has no data-plane impact with RC2's C36; Valkey planned maintenance costs ≈ 140 declared
503s; and because Valkey cannot be failed over manually, a data-loss failover drill needs Redis
Standard HA **or a flush drill**. The C36 facts certify the live behaviour: FLUSHALL restore
0.456 / 1.249 / 0.610 s (bound ≈ 2.05 s), store flush ×3 restored in 0.46–1.25 s, and the
re-hydrator gap of 10 s yielded only declared 503s with **0 violations in all 15 runs**. L05b-3
further requires that, with an idle `FOR UPDATE` held for 60 s and then a FLUSHALL, every kind is
restored within the declared flush bound while the lock is still held and a queued writer fails fast.

This card deliberately delivers **only** what is locally buildable, and it is a **drill plus a doc**,
not a new store or a new re-hydrator:

1. **A flush chaos drill TEST** (local, offline, deterministic) against the already-shipped
   in-memory twins in `gateway_v2/state_control/`. It seeds state across kinds into the
   Store_Twin (`MemoryStore`) and the Durable_Twin (`MemoryControlDB`), **flushes the store**
   (`MemoryStore.flush()` — the published copy is wiped; the Durable_Twin is untouched), runs a
   **re-hydrator round** (`Rehydrator.round_once`), and asserts **every kind is restored** from the
   durable truth with **0 Restore_Violations**, via the whole-kind `publish_kind` restore path that
   the re-hydrator alone may call.

2. **A short "declared store-failure behaviour" doc section** (markdown) that publishes, as the
   runbook's correction asks, the measured + declared failure behaviour for the store, citing the
   C36 facts and naming what is local versus deferred.

The pieces the drill runs against are **shipped**, so this card builds a **test and a doc, not a new
production module**: `MemoryStore` (the in-memory StateStore + StatePublisher twin with `flush()` and
the re-hydrator-only `publish_kind`), `MemoryControlDB` (the transactional Postgres twin a flush does
**not** touch — the durable source of truth), `Rehydrator` (`round_once(deep: bool = False) ->
RoundSummary`, per-kind `RoundSummary` / `RepairEvent`, repair reasons incl. `MISSING` /
`STORE_AHEAD` / `STALE` / `VERSION`), and `ALL_KINDS` / `StateKind` (the kinds seeded and restored).

**The local drill asserts correctness, not a wall-clock figure.** The RC2 live flush-restore bound
of ≈ 2.05 s is a wall-clock measurement against a live Valkey and is **not** comparable to the
in-memory twin. The local Declared_Flush_Bound is therefore expressed deterministically in
**re-hydrator rounds** (restored within one round, or a small bounded number of rounds), and the
≈ 2.05 s wall-clock certification is a Deferred_Cloud_Gate (G-14).

The **L05b-3 flavour** (a flush while a writer/lock condition holds) is honoured only insofar as the
twin supports it: the in-memory twins have no real `FOR UPDATE` lock, so this card does **not** fake
one. The core requirement is the flush → restore → 0-violations correctness; the held-lock
fail-fast behaviour and the ≈ 2.05 s wall-clock figure are deferred to the live L05b-3 / G-14 gate.

Note: a known pre-existing finding (R2-05 changelog) is that `state_control/valkey.py:100`
`check_tenant_scale` trips on the deep-verify path — but that is the **live Valkey** path, not the
in-memory twin. The local flush drill uses the `MemoryStore` / `MemoryControlDB` twins and is
unaffected.

This card honours the same **house conventions** as B1–B7. The drill TEST lives in
`gateway_v2/tests/` (test files sit outside the import-linter `root_packages = ["gateway_v2"]`
contract); any new helper code is minimal; where a property fits, it uses the house test idiom
(a seeded `random.Random`, ≥ 10,000-iteration property test, no hypothesis); it is deterministic and
offline (the twins are in-memory, no server); and it keeps `mypy --strict`, `ruff`, the AST gates,
and `unscanned_channels() == ()` green.

This is a **MEDIUM**, narrow, **low-value Category-1/2** card (a drill test plus a doc), explicitly
**smaller** than B6/B7. It depends on **nothing new** — the re-hydrator and the twins are shipped.

## Glossary

- **Flush_Drill**: the local, offline, deterministic chaos drill that seeds multi-kind state, flushes
  the store twin, runs a re-hydrator round, and asserts the store is restored from the durable truth
  with 0 Restore_Violations.
- **Store_Twin / MemoryStore**: the shipped in-memory StateStore + StatePublisher twin
  (`state_control/publisher.py`), with a `flush()` that wipes the published copy and the
  re-hydrator-only whole-kind `publish_kind` restore path.
- **Durable_Twin / MemoryControlDB**: the shipped transactional in-memory Postgres twin
  (`state_control/db.py`) that a flush does **not** touch — the durable source of truth the restore
  reads from.
- **Rehydrator_Round**: one invocation of `Rehydrator.round_once(deep=...)`, which compares the
  published copy against the durable truth and republishes every lost kind via `publish_kind`,
  returning a per-kind `RoundSummary` with `RepairEvent`s.
- **Restore_Violation**: any divergence, after the drill, between the restored Store_Twin and the
  Durable_Twin — a kind missing, stale, store-ahead, version-mismatched, or contents-mismatched. The
  drill's success criterion is **0 Restore_Violations** (the C36 "0 violations" result, locally).
- **Declared_Flush_Bound**: the deterministic, local bound within which the restore must complete,
  expressed in **re-hydrator rounds** (not a wall-clock figure). The RC2 live ≈ 2.05 s wall-clock
  bound is the cloud figure and is a Deferred_Cloud_Gate.
- **Declared_Failure_Behaviour**: the doc section publishing the measured + declared store-failure
  semantics (flush → re-hydrator restore from durable Postgres; Cloud SQL failover = no data-plane
  impact per C36; Valkey planned maintenance ≈ 140 declared 503s; Valkey has no manual failover, so a
  data-loss drill is a flush drill or Redis Standard HA).
- **Deferred_Cloud_Gate**: a capability out of scope here because it needs real stores / cloud
  resources — the **partition** drill and the **Redis forced-failover** drill (G-13), the **Cloud SQL
  + store overlap** (G-14, owned by GW05b), and the live ≈ 2.05 s wall-clock flush-restore
  certification.

## Requirements

### Requirement 1: Seed multi-kind state into both twins

**User Story:** As a reliability engineer, I want the drill to seed state across kinds into both the
store twin and the durable twin, so that a flush has a non-trivial, known good state to be restored
to.

#### Acceptance Criteria

1. WHEN the Flush_Drill begins, THE Flush_Drill SHALL seed state for every kind in `ALL_KINDS` into
   the Durable_Twin and publish it to the Store_Twin through the shipped writer path.
2. WHEN seeding completes, THE Flush_Drill SHALL confirm that, for every seeded kind, the Store_Twin's
   published head agrees with the Durable_Twin's counters so the pre-flush state is a known good
   baseline.
3. THE Flush_Drill SHALL seed through the shipped `StateWriter` / publisher path rather than fabricate
   store contents directly, so the drill exercises the real propagation contract.

### Requirement 2: Flush wipes the store but not the durable truth

**User Story:** As a reliability engineer, I want the flush to model real data loss on the store while
leaving the durable source of truth intact, so that the drill reproduces the C36 "store flush,
Postgres untouched" fault.

#### Acceptance Criteria

1. WHEN the Flush_Drill flushes the Store_Twin, THE Flush_Drill SHALL invoke `MemoryStore.flush()` so
   the published manifests, records, index, engaged sets and stamp are all cleared.
2. WHILE the Store_Twin is flushed and before a re-hydrator round runs, THE Store_Twin SHALL report no
   verifiable published position for any seeded kind.
3. WHEN the Store_Twin is flushed, THE Durable_Twin SHALL retain the full seeded state unchanged, so
   the durable truth is available to restore from.

### Requirement 3: A re-hydrator round restores every kind from the durable truth

**User Story:** As a reliability engineer, I want one re-hydrator round after a flush to restore every
kind from the durable truth, so that the store is brought back to the durable state exactly as C36
describes.

#### Acceptance Criteria

1. WHEN a Rehydrator_Round runs after a flush, THE Rehydrator SHALL repair every flushed kind and
   report each repair with a `RepairEvent` whose reason is `MISSING`.
2. WHEN the Rehydrator repairs a flushed kind, THE Rehydrator SHALL restore it via the whole-kind
   `publish_kind` path, which is the re-hydrator-only restore path.
3. WHEN the restoring Rehydrator_Round completes, THE RoundSummary SHALL report no per-kind errors so
   the round is reported as successful.

### Requirement 4: The restored store matches the durable truth with zero violations

**User Story:** As a reliability engineer, I want the restored store to match the durable truth across
every kind, so that the drill reproduces C36's "0 violations" result locally.

#### Acceptance Criteria

1. WHEN the restoring Rehydrator_Round completes, THE Flush_Drill SHALL assert that, for every kind in
   `ALL_KINDS`, the Store_Twin's restored published head agrees with the Durable_Twin's counters.
2. WHEN the Flush_Drill performs a deep verification of the restored store against the durable truth,
   THE Flush_Drill SHALL find the Store_Twin's full per-key index equal to the Durable_Twin's
   positions for every kind.
3. WHEN the Flush_Drill evaluates the drill outcome, THE Flush_Drill SHALL report **0**
   Restore_Violations.
4. WHERE a property-based test fits, THE Flush_Drill SHALL assert, for any seeded multi-kind state,
   that flush then a re-hydrator round restores the Store_Twin to agree with the Durable_Twin across
   every kind, using the house test idiom (a seeded `random.Random`, ≥ 10,000-iteration property
   test, no hypothesis).

### Requirement 5: Restore completes within the declared, deterministic round bound

**User Story:** As a reliability engineer, I want the restore to complete within a declared,
deterministic bound expressed in re-hydrator rounds, so that the local drill certifies the correctness
and promptness of the restore without claiming a wall-clock figure the in-memory twin cannot support.

#### Acceptance Criteria

1. WHEN a flush is followed by Rehydrator_Rounds, THE Flush_Drill SHALL restore every kind within the
   Declared_Flush_Bound expressed as a bounded number of re-hydrator rounds.
2. THE Flush_Drill SHALL assert the Declared_Flush_Bound as a deterministic round count rather than a
   wall-clock duration.
3. WHEN a second Rehydrator_Round runs immediately after the restore, THE Rehydrator SHALL perform no
   further repair for any kind, so the restore is confirmed complete within the bound.
4. THE Flush_Drill SHALL document that the RC2 ≈ 2.05 s wall-clock flush-restore bound is the
   live/cloud figure (a Deferred_Cloud_Gate) and is not asserted by the local drill.

### Requirement 6: L05b-3 held-lock flavour, honest to the twin

**User Story:** As a reliability engineer, I want the L05b-3 flush-under-write-pressure flavour
represented only insofar as the in-memory twin supports it, so that the drill stays honest and does
not fake a lock the twin does not have.

#### Acceptance Criteria

1. WHERE the Flush_Drill models in-flight writes to the durable truth around a flush, THE Flush_Drill
   SHALL still restore every kind from the durable truth with 0 Restore_Violations.
2. THE Flush_Drill SHALL NOT model a real `FOR UPDATE` lock or a queued-writer fail-fast outcome,
   because the in-memory twin provides no such lock.
3. THE Flush_Drill SHALL document that the held-`FOR UPDATE`-lock behaviour and the queued-writer
   fail-fast outcome of L05b-3 are deferred to the live gate.

### Requirement 7: Declared store-failure behaviour doc section

**User Story:** As an owner, I want the measured and declared store-failure behaviour published as the
runbook's correction asks, so that the organization has one authoritative statement of what happens
when the store fails and which drills certify it.

#### Acceptance Criteria

1. THE Declared_Failure_Behaviour doc SHALL state that a store flush is restored from the durable
   Postgres source of truth by the re-hydrator, with 0 violations, citing the C36 measured facts.
2. THE Declared_Failure_Behaviour doc SHALL state that a Cloud SQL failover has no data-plane impact
   under C36 (0 × 503 to tenants, 0 acknowledged writes lost), citing the C36 measured facts.
3. THE Declared_Failure_Behaviour doc SHALL state that a Valkey planned-maintenance failover costs
   ≈ 140 declared 503s, citing the C36 measured facts.
4. THE Declared_Failure_Behaviour doc SHALL state that Valkey cannot be failed over manually, so a
   data-loss failover drill requires Redis Standard HA or a flush drill.
5. THE Declared_Failure_Behaviour doc SHALL name which drills are delivered locally (the flush drill)
   and which are Deferred_Cloud_Gates (partition, Redis forced failover, and the Cloud SQL + store
   overlap).

### Requirement 8: Deliverable shape and house conventions

**User Story:** As a maintainer, I want this card to deliver only a test and a doc under the gateway_v2
house rules, so that it adds no production module and does not weaken the import, typing, or capacity
invariants.

#### Acceptance Criteria

1. THE Flush_Drill SHALL be delivered as a test under `gateway_v2/tests/`, which sits outside the
   import-linter `root_packages = ["gateway_v2"]` contract.
2. THE card SHALL NOT introduce a new production store, re-hydrator, or state module, and SHALL run
   the drill against the already-shipped `MemoryStore`, `MemoryControlDB`, and `Rehydrator`.
3. WHERE the drill needs helper code, THE helper code SHALL be minimal and SHALL reconcile with the
   shipped `state_control` twins rather than duplicate them.
4. THE Flush_Drill SHALL be deterministic and offline, using the in-memory twins with no server.
5. THE card SHALL keep `mypy --strict`, `ruff`, the AST gates, and `unscanned_channels() == ()`
   passing.

### Requirement 9: Explicit out-of-scope / deferred boundary

**User Story:** As an owner, I want the cloud drills and the live wall-clock certification named as not
delivered here, so that the delivered scope is unambiguous and the deferred work has a clear owner.

#### Acceptance Criteria

1. THE card SHALL document that the **partition** drill and the **Redis forced-failover** drill
   (G-13, needing real stores) are Deferred_Cloud_Gates and are not delivered here.
2. THE card SHALL document that the **Cloud SQL + store overlap** drill (G-14, owned by GW05b) is a
   Deferred_Cloud_Gate and is not delivered here.
3. THE card SHALL document that the live ≈ 2.05 s wall-clock flush-restore certification is a
   Deferred_Cloud_Gate and is not asserted by the local drill.
4. THE card SHALL NOT introduce any real-store, partition, or forced-failover behaviour as part of
   this card.
