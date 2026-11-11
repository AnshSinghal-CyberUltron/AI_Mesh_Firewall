# Requirements Document

## Introduction

This feature implements correction-register item **R2-23** (**C43 push cost**) in the v3
backend-rewrite tree. It adds a **per-owner, publish-side nudge coalescer** to the control-plane
package `state_control/` (which sits OUTSIDE the import-linter `root_packages=["gateway_v2"]`,
exactly like `audit_control/`), sitting between the writer's committed write and the transport's
`PUBLISH`, so that a burst of writes to a single owner collapses to a **bounded number of nudges
per owner per window** rather than one `PUBLISH` per write. It also discharges a **verification
obligation** that the GW05c rewrite already structurally eliminated the original R2-23 defect,
and delivers the **microbenchmark** the register explicitly asks for.

### The register item (R2-23, verbatim)

> C43 push cost: 72 pushes per request at 36 workers, +2.3 gateway CPU-ms per request. Push
> coalescing was deferred from RC2; it exists only in the unreleased RC3 base (523ecb8) and was
> never measured.

Correction: *"GW08 / GW19 bound push rate per owner (coalesced), with a microbenchmark and a
targeted knee re-measure."* Impact **MEDIUM**. Fix before GW19.

### Critical framing — what is ALREADY structurally closed by the GW05c rewrite

The original "72 pushes per request" was a **v1/RC2** property: the control plane PUBLISHed a
nudge **per worker per request** on the serving path. In the v3 tree (`gateway_v2/`) this is GONE
by construction, and this feature MUST state that honestly rather than re-claim the per-request
defect as still open. Verified ground truth (reuse, do not reinvent):

- **Write side** — `state_control/valkey.py` (`ValkeyPublisher._guarded`): every
  `publish_record` emits exactly ONE `PUBLISH` per record write; `publish_batch` emits exactly
  ONE `PUBLISH` per batch (records in bounded chunks, then **the manifest LAST** under `WATCH`,
  **ONE nudge**); `publish_kind` (re-hydrator only) emits one. Never per-worker, never
  per-request.
- **Write side** — `state_control/writer.py` (`StateWriter`): the one write path commits to
  Postgres, then calls `publish_record` / `publish_batch` on the publisher. `put_many` is already
  "ONE transaction, ONE version bump, ONE manifest, ONE nudge". There is no serving-path publish.
- **Read side** — `gateway_v2/runtime/state_nudge.py` (`NudgeListener`): already COALESCES.
  `drain_max=64` messages per wakeup collapse into ONE nudge; `_collect` keeps the HIGHEST
  announced position per kind; a worker whose cursor is already at or past the announced
  `<kind>:<feed_seq>` SKIPS the round entirely, so "a nudge storm costs nothing". The nudge is a
  LATENCY hint, never load-bearing for correctness (pub/sub is lossy by design; the periodic
  O(1) round is the correctness backstop).

So there is NO per-request push on any serving path in `gateway_v2`. This feature records that as
"already closed by GW05c" plus a standing verification obligation (an assertion that the write
path emits one `PUBLISH` per write/batch and the serving/request path emits ZERO publishes).

### What is genuinely remaining (the real scope of this card)

The control-plane WRITER's domain operations (`plan_set` / `plan_offboard` / `key_add` /
`key_revoke` / `killswitch`, each keyed by `org_id` or a per-owner key) each call
`publish_record` / `publish_batch`, and EACH write emits its own nudge `PUBLISH`. A burst of
writes to a SINGLE owner therefore still emits one `PUBLISH` per write — unbounded in that
owner's write rate. R2-23's correction ("bound push rate per owner (coalesced)") asks that the
`PUBLISH` (push) rate be **bounded per owner and coalesced**: a burst of writes to one owner
within a window collapses to a bounded number of nudges (ideally one carrying the highest
`feed_seq`), rather than one `PUBLISH` each.

This is a **per-owner, publish-side coalescer / rate-limiter** that sits between the writer's
commit and the transport's `PUBLISH`. The hard constraint: it MUST NEVER coalesce the DURABLE
writes themselves (the record `SET`/`HSET` + index `ZADD` + manifest `SET`) — those are
correctness and are already O(1) per write (GW05c). It ONLY coalesces the NUDGE (the lossy
latency hint). Coalescing a lossy hint is always safe, because a dropped or merged nudge only
means a worker learns via the next periodic round, bounded by the freshness window.

A **microbenchmark** is explicitly required: a local, deterministic, in-process measurement
(injected clock) that shows push count per owner per window is bounded, and reports the
CPU / push-count delta (the "+2.3 CPU-ms / 72 pushes" shape) between the un-coalesced and
coalesced paths. This is a shipped, runnable-now deliverable.

### Scope boundary (CRITICAL — category-1 clean, one deferred cloud gate)

The coalescer is production code that runs identically local and cloud. Everything below is
LOCAL and shipped now, verifiable with unit tests, seeded property-based tests, and an in-process
harness driving the publisher with an injected clock and the in-memory `MemoryStore` /
`fakeredis`:

- the per-owner Nudge_Coalescer (window-bounded `PUBLISH` count per owner),
- the Durability_Never_Coalesced invariant (durable writes are untouched),
- the Highest_Feed_Seq nudge contract (a coalesced nudge announces the latest position),
- the Never_Behind_Beyond_Freshness invariant (a merged/dropped nudge costs only latency),
- the Fail_Safe guarantee (a coalescer failure never affects durability and never leaves a
  worker behind beyond the freshness window),
- the per-owner push-rate bound derived from a declared authority (not a literal),
- the already-closed per-request / per-worker push verification obligation,
- the Microbenchmark deliverable.

The ONLY deferred piece is the register's **"targeted knee re-measure"** at fleet scale (36
workers / real Valkey / real request load) — a DEFERRED CLOUD GATE, declared OPEN, exactly like
prior cards' live gates. It is represented locally by the Microbenchmark.

### Reused ground truth (verified — reuse, do not reinvent)

- `state_control/valkey.py` `ValkeyPublisher._guarded` is where the single `PUBLISH` of
  `"<kind>:<feed_seq>"` is emitted today; the coalescer wraps / intercepts this emission.
- `state_control/publisher.py` defines the `StatePublisher` protocol and the `MemoryStore` twin,
  which records every nudge in `MemoryStore.nudges` and is both a publisher and a reader store —
  the end-to-end local harness.
- `state_control/writer.py` `StateWriter` is the one caller; the coalescer must not change its
  four honest outcomes (`ok` / `ok_publish_pending` / `unknown` / `error`).
- `gateway_v2/runtime/state_nudge.py` `NudgeListener` / `parse_nudge` define the nudge wire
  contract `"<kind>:<feed_seq>"` and the read-side coalescing / skip-if-at-or-past behaviour that
  makes a merged nudge safe. The coalescer MUST preserve this payload contract.
- `gateway_v2/runtime/resources.py` `ResourceContract` is the only module permitted to hold
  capacity-position literals (the "no capacity literal outside `runtime/resources.py`"
  discipline); it already derives bounds as multiples of `target_p99_ms` / worker count.
- `gateway_v2/domain/state_knobs.py` `StateKnobs` holds the control-plane periods
  (`rehydrate_period_ms`, `refresh_period_ms`, `fresh_ms`, `enforcement_bound_s`) with
  fail-at-start validation; it is the natural home for a control-plane-side knob since
  `state_control` is outside `root_packages`.
- `gateway_v2/runtime/state_nudge.py` `ListenerCounters` is the label-free counter pattern GW14d
  wires into a metric family; any coalescer metric MUST follow it (bounded, label-free, no
  per-owner label on a series).
- Conventions: injectable `Callable[[], float]` clock, frozen slotted dataclasses, fail-closed /
  fail-safe, producer-only label-free metrics, property-based correctness as seeded
  >= 10,000-iteration tests (house idiom, no hypothesis).

## Glossary

- **Nudge**: The pub/sub message `"<kind>:<feed_seq>"` emitted on the updates channel after a
  committed write. A LATENCY hint only; never load-bearing for correctness because pub/sub is
  lossy by construction.
- **Push**: One `PUBLISH` of a Nudge on the transport. "Push cost" / "push rate" in R2-23 refers
  to the count of these `PUBLISH` operations.
- **Owner**: The per-tenant identity a write is keyed by — `org_id` for a plan, the per-key owner
  for a key record, the scope for a kill switch. The unit the push rate is bounded per.
- **Nudge_Coalescer**: The per-owner, publish-side component that sits between the writer's
  committed write and the transport's `PUBLISH`, collapsing a burst of a single owner's nudges
  within a window into a bounded number of pushes.
- **Durable_Write**: The record `SET`/`HSET`, the index `ZADD`, the engaged-set `SADD`/`SREM`,
  and the manifest `SET` — the correctness-bearing part of a write. NEVER coalesced.
- **Per_Owner_Push_Bound**: The maximum number of pushes the Nudge_Coalescer emits for one owner
  within one Coalesce_Window. Derived from a declared authority (ResourceContract or a
  StateKnobs control-plane knob), never a hardcoded literal.
- **Coalesce_Window**: The time window within which a single owner's nudges are collapsed,
  bounded by the Per_Owner_Push_Bound. Measured on an Injected_Clock.
- **Highest_Feed_Seq**: The maximum `feed_seq` among the nudges a coalesced push stands in for;
  the single rule for which position a coalesced nudge announces.
- **Freshness_Window**: The bound within which a worker learns of a committed write even if every
  nudge for it is dropped or merged — the periodic O(1) round plus the staleness bound, expressed
  by `StateKnobs.enforcement_bound_s` / `fresh_ms`. The correctness backstop.
- **Fail_Safe**: The stance the Nudge_Coalescer takes when it cannot run (error, shutdown): it
  never affects a Durable_Write and never leaves a worker behind beyond the Freshness_Window —
  either by publishing the nudge directly or by relying on the periodic round.
- **Microbenchmark**: A local, deterministic, in-process measurement on an Injected_Clock that
  reports the push count per owner per window and the CPU / push-count delta between the
  un-coalesced and coalesced paths (the "+2.3 CPU-ms / 72 pushes" shape).
- **Knee_Re_Measure**: The deferred fleet-scale re-measurement (36 workers / real Valkey / real
  request load) of the push cost at the capacity knee; a DEFERRED CLOUD GATE.
- **ResourceContract**: The frozen capacity contract in `gateway_v2/runtime/resources.py`, the
  only module permitted to hold capacity-position literals.
- **StateKnobs**: The control-plane period/relationship value type in
  `gateway_v2/domain/state_knobs.py`, validated fail-at-start; candidate home for a control-plane
  Per_Owner_Push_Bound knob.
- **Injected_Clock**: A `Callable[[], float]` time source injected at construction so tests and
  the Microbenchmark advance time deterministically (project convention).
- **StatePublisher**: The publisher protocol in `state_control/publisher.py`
  (`publish_record` / `publish_batch` / `publish_kind`) the writer and re-hydrator depend on.

## Requirements

### Requirement 1: The per-request / per-worker push defect is already closed (verification obligation)

**User Story:** As a platform operator, I want proof that the control plane no longer pushes per worker per request, so that the original R2-23 "72 pushes per request" defect cannot silently return.

#### Acceptance Criteria

1. WHEN `publish_record` completes a single record write, THE Push_Verification SHALL assert that exactly 1 `PUBLISH` operation was emitted for that write.
2. WHEN `publish_batch` completes with a batch containing between 1 and the maximum supported batch size (inclusive) of records, THE Push_Verification SHALL assert that exactly 1 `PUBLISH` operation was emitted for the batch, independent of the record count in the batch.
3. WHEN a serving / request path operation completes, THE Push_Verification SHALL assert that the count of `PUBLISH` operations emitted by that path equals 0.
4. WHEN a sequence of W writes across O distinct owners is executed under a configuration of N workers serving R requests, THE Push_Verification SHALL assert that the total emitted `PUBLISH` count is determined solely by W and O (one push per record write plus one manifest-last nudge per owner), and SHALL assert that the total emitted `PUBLISH` count does not change when N varies and does not change when R varies, all other inputs held constant.
5. IF the Nudge_Coalescer emits any `PUBLISH` from the serving / request path, OR emits a `PUBLISH` count that varies with worker count, THEN THE Push_Verification SHALL report a verification failure identifying the offending push source and the observed versus expected push count.

### Requirement 2: Per-owner coalesced push-rate bound

**User Story:** As a platform operator, I want a burst of writes to one owner to collapse to a bounded number of pushes, so that a single owner's write rate cannot drive an unbounded push rate.

#### Acceptance Criteria

1. WHEN a burst of writes to a single Owner occurs within one Coalesce_Window, THE Nudge_Coalescer SHALL emit at most the Per_Owner_Push_Bound pushes for that Owner in that window.
2. THE Nudge_Coalescer SHALL maintain the push budget independently per Owner so that one Owner's burst does not reduce another Owner's available pushes within the same window.
3. WHEN writes to a single Owner continue across multiple Coalesce_Windows, THE Nudge_Coalescer SHALL apply the Per_Owner_Push_Bound within each window independently rather than across the whole stream.
4. WHILE no new writes are pending for an Owner, THE Nudge_Coalescer SHALL emit no further pushes for that Owner.
5. THE Nudge_Coalescer SHALL receive the Injected_Clock at construction and SHALL measure every Coalesce_Window boundary on that clock.
6. WHERE an Owner is identified, THE Nudge_Coalescer SHALL treat the Owner as the per-tenant write identity (the `org_id` for a plan, the per-key owner for a key record, or the scope for a kill switch) and SHALL bound pushes per that identity.

### Requirement 3: Durability is never coalesced

**User Story:** As a security operator, I want every committed write to remain fully durable regardless of coalescing, so that coalescing a latency hint can never drop state.

#### Acceptance Criteria

1. THE Nudge_Coalescer SHALL coalesce only the Nudge (the `PUBLISH`) and SHALL NOT coalesce, batch, drop, or delay any Durable_Write.
2. WHEN N writes are issued to one Owner in any interleaving with writes to other Owners, THE Durable_Write count for that Owner SHALL equal N regardless of how many pushes the Nudge_Coalescer emits for that Owner.
3. THE Nudge_Coalescer SHALL preserve the manifest-last ordering so that a reader observes either the prior complete manifest or the new complete generation, and never a partially written generation.
4. THE Nudge_Coalescer SHALL leave each write's reported outcome equal to the outcome the writer would report with push coalescing disabled, mapping one-to-one onto `ok`, `ok_publish_pending`, `unknown`, or `error`.
5. THE Nudge_Coalescer SHALL leave the record `SET`/`HSET`, the index `ZADD`, the engaged-set `SADD`/`SREM`, and the manifest `SET` of each write byte-identical to the un-coalesced path.
6. IF the Nudge_Coalescer encounters an error while coalescing or emitting a push, THEN THE Durable_Write SHALL remain committed and its reported outcome SHALL be unchanged.

### Requirement 4: Highest-feed_seq-preserving nudge contract

**User Story:** As a developer on the gateway read side, I want a coalesced nudge to announce the latest committed position, so that a reader at or past it still correctly skips the round.

#### Acceptance Criteria

1. WHEN the Nudge_Coalescer emits a coalesced push standing in for a set of two or more of an Owner's writes for a single kind, THE Nudge_Coalescer SHALL set the announced feed_seq equal to the maximum feed_seq value among all writes in that set.
2. WHEN the Nudge_Coalescer emits a coalesced push standing in for exactly one write, THE Nudge_Coalescer SHALL set the announced feed_seq equal to that write's feed_seq.
3. THE Nudge_Coalescer SHALL emit the nudge payload as a single string in the wire format `"<kind>:<feed_seq>"`, where `<kind>` contains no colon character and `<feed_seq>` is a base-10 integer, such that `parse_nudge` returns the identical (kind, feed_seq) pair that was emitted.
4. THE Nudge_Coalescer SHALL emit each coalesced push for exactly one kind, such that a single push announces a feed_seq for one and only one kind, matching the read-side `_collect` per-kind coalescing.
5. IF the Nudge_Coalescer would emit a push for an (Owner, kind) pair with a feed_seq strictly less than the highest feed_seq it has previously announced for that same (Owner, kind) pair, THEN THE Nudge_Coalescer SHALL suppress that push and SHALL retain the previously announced highest feed_seq as the announced position, with no lower position ever placed on the wire.
6. WHEN a reader's cursor feed_seq for a kind is greater than or equal to a received coalesced nudge's announced feed_seq for that same kind, THE read side SHALL skip the round and SHALL perform no backend read for that round.

### Requirement 5: Never leave a worker behind beyond the freshness window

**User Story:** As a platform operator, I want a merged or dropped nudge to cost only latency, so that coalescing never leaves a worker permanently behind the latest committed state.

#### Acceptance Criteria

1. THE Nudge_Coalescer SHALL treat the Nudge as a non-correctness latency hint, such that merging or dropping a Nudge changes only the latency with which a worker learns a committed write and never changes which committed `feed_seq` the worker eventually reaches.
2. THE Nudge_Coalescer SHALL rely on the periodic O(1) round as the correctness backstop for any merged or dropped Nudge, where the Freshness_Window within which that round must deliver the committed write equals the periodic round period plus the staleness bound expressed by `StateKnobs.enforcement_bound_s` and `fresh_ms`.
3. WHEN a Nudge for an Owner is merged into a coalesced push or dropped, THE affected worker SHALL learn of the committed write within the Freshness_Window via the periodic round, measured on the Injected_Clock from the moment the write is committed.
4. WHEN the Freshness_Window elapses after a committed write whose Nudge was merged or dropped, THE affected worker's applied `feed_seq` SHALL be at or above that committed `feed_seq` and SHALL NOT be below it.
5. THE Nudge_Coalescer SHALL NOT merge or drop a Nudge in a way that leaves a worker below the latest committed `feed_seq` for longer than the Freshness_Window.
6. THE Nudge_Coalescer SHALL NOT increase the worst-case propagation bound from a committed write to enforcement-or-refusal above the existing `StateKnobs.enforcement_bound_s` guarantee.

### Requirement 6: Fail-safe on coalescer failure or shutdown

**User Story:** As a platform operator, I want a coalescer failure to never cost durability or leave a worker behind, so that adding a latency optimisation cannot create a correctness regression.

#### Acceptance Criteria

1. IF the Nudge_Coalescer cannot run due to an internal error or a transport fault on the push, THEN THE write path SHALL complete the Durable_Write and SHALL report a write outcome identical to the outcome it would report when push coalescing is disabled.
2. IF the Nudge_Coalescer fails to emit a coalesced push for an Owner, THEN THE system SHALL either publish that Owner's nudge directly or defer to the next periodic round, and SHALL cause each affected Owner to be refreshed within the Freshness_Window measured from the Durable_Write commit.
3. WHEN the Nudge_Coalescer is shut down with pending coalesced nudges, THE Nudge_Coalescer SHALL, for each affected Owner, flush the pending Highest_Feed_Seq push or defer that Owner to the next periodic round, and SHALL NOT discard a pending Highest_Feed_Seq position without the affected Owner being refreshed within the Freshness_Window.
4. THE Nudge_Coalescer SHALL NOT cause a Durable_Write to fail and SHALL NOT alter a reported write outcome when a coalesced push cannot be emitted.
5. THE write path SHALL commit the Durable_Write and determine its reported write outcome before any coalesced push is attempted.

### Requirement 7: Per-owner push bound derived from a declared authority

**User Story:** As a platform operator, I want the per-owner push bound grounded in a declared capacity authority, so that the bound is not a guessed constant scattered in the code.

#### Acceptance Criteria

1. THE Nudge_Coalescer SHALL derive the Per_Owner_Push_Bound and the Coalesce_Window from exactly one declared authority, either the ResourceContract or a declared StateKnobs control-plane knob, and SHALL NOT derive either value from a hardcoded capacity-position literal.
2. THE Nudge_Coalescer SHALL NOT contain a capacity-position literal for the Per_Owner_Push_Bound or the Coalesce_Window outside the single declared authority.
3. WHERE the Per_Owner_Push_Bound or the Coalesce_Window is declared as a StateKnobs control-plane knob, THE Nudge_Coalescer SHALL validate the knob at start and SHALL refuse to start if the knob value is absent or fails validation, acknowledging that `state_control` is outside `root_packages`.
4. IF the Per_Owner_Push_Bound or the Coalesce_Window cannot be resolved from the single declared authority at start, THEN THE Nudge_Coalescer SHALL refuse to start, SHALL emit a start-up error indicating which value could not be resolved, and SHALL NOT begin serving or coalescing push nudges.
5. WHILE validating configuration at start, IF the resolved Coalesce_Window is strictly greater than the Freshness_Window, THEN THE Nudge_Coalescer SHALL reject the configuration and refuse to start with a start-up error indicating the Coalesce_Window exceeds the Freshness_Window, and a Coalesce_Window less than or equal to the Freshness_Window SHALL be accepted.

### Requirement 8: Microbenchmark deliverable

**User Story:** As a developer, I want a local, deterministic microbenchmark of the push cost, so that the coalesced bound and the push-count delta are measurable now without a cloud fleet.

#### Acceptance Criteria

1. THE Microbenchmark SHALL execute entirely in a single local process using an Injected_Clock and a MemoryStore (fakeredis) twin, making zero network connections to any real Valkey instance.
2. WHEN the Microbenchmark is executed two or more times with identical input workload and identical Injected_Clock seed, THE Microbenchmark SHALL produce byte-identical reported output values across all runs.
3. THE Microbenchmark SHALL report the observed push count per Owner per Coalesce_Window and SHALL assert that each reported per-Owner-per-window push count is less than or equal to the Per_Owner_Push_Bound.
4. IF any reported per-Owner-per-window push count exceeds the Per_Owner_Push_Bound, THEN THE Microbenchmark SHALL terminate with a failing result that identifies the offending Owner, the Coalesce_Window, the observed count, and the configured Per_Owner_Push_Bound.
5. THE Microbenchmark SHALL execute the same write workload through both the un-coalesced path and the coalesced path, and SHALL report the total push count for each path and the push-count delta computed as the un-coalesced total push count minus the coalesced total push count.
6. THE Microbenchmark SHALL report a per-request and per-push cost shape for the coalesced path expressed in the same units as the R2-23 baseline (CPU-milliseconds and total push count, baseline reference "+2.3 CPU-ms / 72 pushes"), including the measured CPU-millisecond cost and the measured total push count.
7. THE Microbenchmark SHALL report the Durable_Write count observed on the un-coalesced path and the Durable_Write count observed on the coalesced path for the same write workload, and SHALL assert that the two Durable_Write counts are equal.
8. IF the Durable_Write count on the coalesced path is not equal to the Durable_Write count on the un-coalesced path for the same workload, THEN THE Microbenchmark SHALL terminate with a failing result that reports both Durable_Write counts and the workload identifier.

### Requirement 9: Deferred fleet knee re-measure (open cloud gate)

**User Story:** As a platform operator, I want the fleet-scale knee re-measurement declared as an open deferred gate, so that the local microbenchmark is not mistaken for the live measurement.

#### Acceptance Criteria

1. THE Feature SHALL declare the Knee_Re_Measure as a DEFERRED CLOUD GATE whose status is OPEN and whose scope is marked out-of-local-scope, where the Knee_Re_Measure is defined as a targeted re-measurement of push cost at the capacity knee executed at fleet scale with exactly 36 workers, a real (non-mocked) Valkey instance, and real request load.
2. THE Feature SHALL record a declaration stating that the Microbenchmark is the local representative of the Knee_Re_Measure and does not substitute for the live fleet-scale measurement.
3. IF any local test asserts, reports, or otherwise marks the Knee_Re_Measure as satisfied, passed, or closed, THEN THE Feature SHALL treat that assertion as invalid and SHALL keep the Knee_Re_Measure status as OPEN.
4. WHILE the Knee_Re_Measure status is OPEN, THE Feature SHALL present its status as OPEN in every location that reports gate status, with no location reporting it as satisfied, passed, or closed.

### Requirement 10: Label-free coalescer metric (producer only)

**User Story:** As a platform operator, I want any coalescer metric to be a fixed, label-free series set, so that observability never creates a per-owner series that breaks exposition.

#### Acceptance Criteria

1. WHERE the Nudge_Coalescer exposes a metric, THE metric SHALL be an immutable value (a frozen counter/snapshot) that a publisher reads and wires into a family, and SHALL NOT import or reference a metrics registry or self-register a series.
2. THE Nudge_Coalescer SHALL expose exactly two metric series — the coalesced-push count and the pushes-saved count — and the total number of series SHALL remain two regardless of the Owner, tenant, or subscription count.
3. THE Nudge_Coalescer SHALL NOT attach a per-Owner, tenant-derived, or otherwise unbounded-cardinality label to any metric series.
4. WHEN a Nudge_Coalescer metric value is first created and before any coalescing has occurred, THE two series SHALL each be present with the value zero, so that "no coalescing happened" (present, zero) is distinguishable from "series absent".
5. WHILE the Nudge_Coalescer accumulates metric observations, THE coalesced-push count and the pushes-saved count SHALL each be monotonically non-decreasing.
6. WHEN a publisher reads a Nudge_Coalescer metric value, THE read SHALL NOT raise and SHALL NOT mutate the value.

## Correctness Properties (for property-based testing)

These properties express the invariants the local property-based tests MUST assert as seeded
>= 10,000-iteration tests (house idiom, no hypothesis). Each is tagged with its correctness
pattern.

1. **Per-owner push bound (invariant).** For any burst of N writes to one Owner within one
   Coalesce_Window, the number of pushes emitted is at most the Per_Owner_Push_Bound, and the last
   push for that Owner and kind carries the maximum `feed_seq` among the coalesced writes.
2. **Durable writes equal N (invariant).** For any workload of N writes to any mix of Owners, the
   count of Durable_Writes equals N regardless of coalescing, and each write's record, index entry,
   and manifest are byte-identical to the un-coalesced path.
3. **Reader never ends below the latest committed feed_seq (invariant / round-trip).** For any
   sequence of writes to one Owner, a reader applying only the coalesced nudges plus the periodic
   round ends at or above the latest committed `feed_seq` — never below it.
4. **Monotonic announced position (invariant).** The sequence of `feed_seq` values the
   Nudge_Coalescer announces for one Owner and kind is non-decreasing; a coalesced push never
   announces a position lower than one already announced.
5. **Per-owner isolation (invariant).** For any interleaving of writes across multiple Owners,
   each Owner's pushes are bounded independently; one Owner's burst never reduces another Owner's
   push budget and never reorders another Owner's announced positions.
6. **Coalescing is idempotent on a quiesced owner (idempotence).** Once an Owner's latest write
   has been announced and no further writes arrive, re-running the coalescer flush emits no
   additional push for that Owner.
7. **Fail-safe never costs durability or freshness (error condition).** For any injected push
   failure or shutdown with pending nudges, every Durable_Write still completes, the write outcome
   is unchanged, and no reader is left below the latest committed `feed_seq` beyond the
   Freshness_Window.
8. **Window bound derives from the authority (invariant).** For any ResourceContract or StateKnobs
   configuration, the Per_Owner_Push_Bound and the Coalesce_Window are functions of the declared
   authority only; no capacity literal appears in the coalescer, and a Coalesce_Window longer than
   the Freshness_Window is refused at start.
9. **Confluence of write ordering (confluence).** For any two orderings of the same set of
   per-Owner writes within a window, the set of Durable_Writes is identical and the final
   announced `feed_seq` per Owner and kind is identical, independent of the order the coalescer
   observed them.
10. **Push count is independent of worker and request count (metamorphic).** For a fixed set of
    writes and Owners, the total push count is unchanged when the simulated worker count or request
    count varies, confirming the per-request / per-worker push defect stays closed.

## Measured acceptance targets (from R2-23)

Used as the shape the local Microbenchmark must reproduce:

- **R2-23 baseline (the defect this feature bounds):** 72 pushes per request at 36 workers,
  +2.3 gateway CPU-ms per request — a v1/RC2 per-worker-per-request push property, already removed
  structurally by GW05c (Requirement 1 verifies this).
- **Remaining defect this feature closes:** a burst of writes to a single Owner still emits one
  `PUBLISH` per write (unbounded in that Owner's write rate); the coalescer bounds it to the
  Per_Owner_Push_Bound per Coalesce_Window.
- **Microbenchmark (local):** push count per Owner per window bounded by the Per_Owner_Push_Bound;
  reported push-count delta between the un-coalesced and coalesced paths; Durable_Write count
  identical on both paths.
- **Knee_Re_Measure (deferred cloud gate):** fleet-scale push cost at 36 workers / real Valkey /
  real request load — OPEN, out of local scope, represented by the Microbenchmark.
