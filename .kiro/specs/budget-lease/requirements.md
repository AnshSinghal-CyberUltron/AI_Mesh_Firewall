# Requirements Document

## Introduction

This feature adds the **org-level budget lease plus local GCRA** quota mechanism to the v3
backend-rewrite tree (`gateway_v2/gateway_v2/`), extending the `admit/` package's `quota.py`
module (which today holds only `derive_bounds`, the GW19 bounds derivation) with the budget-lease
and GCRA logic the card reserves it for ("Local GCRA + shared lease"), and confirming/porting the
store-connection boundary in `runtime/` (`store_valkey.py` and `state_nudge.py`).

It implements correction-register item **R2-09** (budget-lease refills off the request path) and
folds in **R2-14** (retry one idempotent store read on TCP RTO timeout) and **R2-19**
(store-connection boundary D1/D2 fixes plus the 45 s partition regression test), all delivered by
the **v3 AMENDMENT to card GW06** ("Build the admission layer with bounded shared state and one
round trip"), together with the C12/C24/C26/C29/C36 v2.1 corrections that bear on
budget/lease/store-connection.

The problems it solves:

- **R2-09 (HIGH):** Budget-lease refills ran **on the request path**, so a store outage failed
  large prompts from +0.15 s instead of after the declared 5 s RAM window. Measured on the RC2
  fleet during a 45 s store partition: 211 × HTTP 503 `shared_state_unavailable` from +0.15 s,
  **inside** the declared 5 s RAM window — because a lease refill needs the store and was being
  done synchronously on the hot path.
- **R2-14 (MEDIUM):** Roughly 1e-4 of cross-zone store round trips hit the 200 ms minimum TCP RTO,
  over the 25 ms request-path timeout.
- **R2-19 (HIGH, already fixed in the prototype):** A redis-py 8 keepalive `ETIMEDOUT` was read as
  "no message", so `listen()` spun to out-of-memory (defect D2); a dead pooled socket answered 500
  (defect D1). This is a **port + regression-test** task into `gateway_v2`.

The objective is to make the org-level token budget a **shared lease spent locally** rather than a
synchronous store read on the hot path: a Low_Watermark Async_Refill keeps the lease topped up off
the request path; during a store outage identity, kill switch, and plan are served from RAM for the
declared window while budget spends the Remaining_Lease and then refuses only quota with a distinct
`budget_unavailable` reason; leases are generation-tagged and revoked on budget-config change,
TTL-reclaimed on worker crash, and never multiplied by worker or replica count.

Capacity literals come only from the `ResourceContract`
(`gateway_v2/gateway_v2/runtime/resources.py`); the Lease_Chunk size is **derived from the
contract**, never a constant, and measured inputs (q_safe-style) are **injected, never hardcoded**.
The store client is **injected** (as the existing adapters are), so the lease logic is testable with
fakeredis.

### Reused ground truth (verified — reuse, do not reinvent)

- **`domain/posture.py`** already defines `BUDGET_UNAVAILABLE = "budget_unavailable"` (with an
  R2-09 docstring stating the budget posture is narrower — spend the remaining lease, then refuse
  only quota), plus `MIN_RETRY_AFTER_S = 1.0` and `gap_retry_after_s(...)`. These MUST be reused and
  MUST NOT be redefined.
- **`admit/quota.py`** currently holds only `derive_bounds`; the budget lease + GCRA is NEW code
  added to this same module.
- **`runtime/resources.py`** `ResourceContract` is the only module permitted to hold
  capacity-position literals; it exposes `q_safe`-fed `offered_service_rate()`, `target_p99_ms`,
  `utilization_cap`, `queue_depth(...)`. The Lease_Chunk derives from it.
- **`runtime/store_valkey.py`** holds the reader store boundary with `require_bounded_client` and
  `bounded_timeout_s` (per-operation timeout bounded below the refresh period).
- **`runtime/state_nudge.py`** holds the push/nudge listener with the D1/D2 reconnect/backoff logic
  (keepalive-error-not-silence, dead-socket-reconnect) and its three dead-connection signatures
  (`error` / `no_wait` / `silent`).
- **Layer contract:** `admit` imports only `runtime` + `domain`. The store client is injected.
- **Conventions:** injectable `Callable[[], float]` clock, frozen slotted dataclasses, `StrEnum`,
  fail-closed, producer-only label-free metrics (follow `admit/metrics.py`).

### Scope boundary (CRITICAL — no cloud resources)

The following **component logic** is in scope and fully locally verifiable with unit tests,
property-based tests, and an in-process harness driving the lease directly with an injected clock
and fakeredis:

- the Budget_Lease state machine (acquire / spend / return-unspent / TTL-reclaim),
- the Local_GCRA (burst and rate, per-worker, local, no store round trip),
- the Budget_Generation tag plus revoke-on-change (C29),
- the no-Replica_Multiplication invariant (C24/C29),
- the Low_Watermark Async_Refill off the request path (R2-09),
- the per-path outage posture (`budget_unavailable` vs `shared_state_unavailable`),
- the retry-once-on-idempotent-read-timeout (R2-14),
- the Store_Connection_Boundary D1/D2 behaviour (R2-19) with the 45 s partition regression test.

Local equivalents stand in for the live gates: LGW06-3 (simulate N in-process replicas sharing one
fakeredis lease, assert aggregate ≤ limit + declared overshoot), LGW06-5 (injected store latency →
bounded timeout + declared posture), the R2-09 partition behaviour (fakeredis partition → budget
spends the remaining lease then `budget_unavailable`; refill proven off the request path), and the
R2-19 45 s partition regression (fakeredis plus a keepalive-`ETIMEDOUT` / dead-socket simulation).

The following are **deferred cloud/scale gates**, out of local scope, represented by the local
equivalents above and explicitly noted as deferred: the full live four-replica fleet quota run
(full LGW06-3), the live store failover (**LGW06-6**), and real cross-zone RTO measurement. The
zone placement for R2-14 (gateways placed in the store primary's zone) is a **deployment
dependency, not code** — the retry-once-on-timeout is the code requirement.

## Glossary

- **Budget_Lease**: A shared, store-backed reservation of a chunk of an Org's token budget that a
  worker acquires and then spends locally, so the org-level budget is enforced across all workers
  and replicas without a store round trip per request.
- **Lease_Chunk**: The size of one acquired slice of budget. Derived from the ResourceContract
  (from `q_safe` / `offered_service_rate` / `target_p99_ms` / `utilization_cap`), never a hardcoded
  constant.
- **Remaining_Lease**: The portion of a worker's currently held Lease_Chunk that has not yet been
  spent locally. During a store outage, budget admission is served from the Remaining_Lease only.
- **Low_Watermark**: The remaining-lease threshold at or below which an Async_Refill is triggered,
  so a worker refills before the Remaining_Lease is exhausted rather than on the request that would
  exhaust it.
- **Async_Refill**: A background acquisition of a new Lease_Chunk from the store, triggered at the
  Low_Watermark and run **off the request path**, so no request ever performs a synchronous store
  read to refill the lease.
- **Budget_Generation**: A monotonic tag stamped on a Budget_Lease identifying the Org budget
  configuration generation under which the lease was acquired. A change of the Org's budget config
  advances the generation and invalidates older leases (C29).
- **GCRA** / **Local_GCRA**: The Generic Cell Rate Algorithm used **per worker, locally, with no
  store round trip** to enforce the Org's burst and rate limits (the Local_Burst_Limit and the
  sustained rate). The lease governs the org-level token budget; GCRA governs burst and rate.
- **Local_Burst_Limit**: The per-worker burst allowance enforced by the Local_GCRA.
- **Lease_TTL**: A time-to-live on the Budget_Lease record, kept on the **store's clock**, so a
  crashed worker's unspent lease is reclaimed by the store after the TTL expires rather than lost.
- **Replica_Multiplication**: The defect where N workers or replicas each enforce the per-org limit
  locally, admitting up to N× the intended limit. The shared Budget_Lease exists to prevent this.
- **Budget_Unavailable**: The posture code `budget_unavailable` (already defined in
  `domain/posture.py`) returned when, during a store outage, the Remaining_Lease is exhausted; it is
  deliberately narrower than `shared_state_unavailable` — it refuses only quota, not the whole
  request path.
- **Shared_State_Unavailable**: The global posture code `shared_state_unavailable` (already defined
  in `domain/posture.py`) for an identity/state outage. Budget admission MUST NOT return this code;
  budget failures use `Budget_Unavailable`.
- **Store_Connection_Boundary**: The reader-side store adapter boundary in
  `runtime/store_valkey.py` (bounded per-operation timeout, dead-socket reconnect) and the push
  listener in `runtime/state_nudge.py` (keepalive-error-not-silence, reconnect/backoff), carrying
  the R2-19 D1/D2 fixes.
- **Idempotent_Read**: A store read with no side effects (e.g. reading a lease or budget record)
  that may be safely retried; R2-14 permits exactly one retry on a timeout.
- **ResourceContract**: The frozen capacity contract in `gateway_v2/gateway_v2/runtime/resources.py`,
  the only module permitted to hold capacity-position literals; the Lease_Chunk is derived from it.
- **Injected_Clock**: A `Callable[[], float]` time source injected at construction so tests advance
  time deterministically (project convention).
- **Store_Client**: The redis/valkey client, injected into the lease logic (as the existing
  adapters inject it), so the component is testable with fakeredis.
- **FAIL_OPEN**: Any path where a budget or quota decision is skipped and a request is admitted
  without a decision. The component forbids FAIL_OPEN; it fails closed.

## Requirements

### Requirement 1: Local GCRA for burst and rate, shared lease for the org token budget

**User Story:** As a platform operator, I want burst and rate enforced locally by GCRA and the
org-level token budget enforced by a shared lease, so that per-request quota needs no store round
trip while the org budget is still enforced across all workers.

#### Acceptance Criteria

1. THE Quota_Component SHALL enforce the Org burst allowance and sustained rate using a Local_GCRA
   that performs no store round trip per request.
2. THE Quota_Component SHALL enforce the Org-level token budget using a shared Budget_Lease spent
   locally.
3. WHEN a request is evaluated for admission, THE Quota_Component SHALL apply the Local_GCRA burst
   and rate check and the Budget_Lease spend without issuing a synchronous store read on the
   request path.
4. THE Quota_Component SHALL read the current time from an Injected_Clock supplied at construction.
5. THE Quota_Component SHALL receive the Store_Client by injection and SHALL NOT construct a store
   client of its own.

### Requirement 2: Lease chunk derived from the ResourceContract

**User Story:** As a platform operator, I want the Lease_Chunk size computed from the signed
capacity contract, so that the chunk is grounded in measured capacity and not a guessed constant.

#### Acceptance Criteria

1. THE Quota_Component SHALL derive the Lease_Chunk size from the ResourceContract using its
   measured inputs (`offered_service_rate`, `target_p99_ms`, `utilization_cap`, and the injected
   `q_safe`-style rate).
2. THE Quota_Component SHALL NOT contain a hardcoded Lease_Chunk capacity literal.
3. THE Quota_Component SHALL hold capacity-position literals only by reference to the
   ResourceContract.
4. IF the Lease_Chunk size cannot be computed from the ResourceContract, THEN THE Quota_Component
   SHALL refuse to acquire a lease rather than acquire with an unbounded or guessed chunk.
5. THE Quota_Component SHALL receive the measured rate input through injection or configuration at
   construction time rather than as a literal.

### Requirement 3: Lease acquire, spend, and return of unspent budget

**User Story:** As an API consumer, I want unspent lease budget returned when a worker stops, so
that I am not refused with budget remaining because a worker lost its lease.

#### Acceptance Criteria

1. WHEN a worker needs budget, THE Quota_Component SHALL acquire a Lease_Chunk from the store and
   spend it locally against subsequent requests.
2. WHILE a worker holds a Lease_Chunk with Remaining_Lease above zero, THE Quota_Component SHALL
   admit budget-eligible requests by decrementing the Remaining_Lease locally.
3. WHEN a worker shuts down with Remaining_Lease above zero, THE Quota_Component SHALL return the
   unspent Remaining_Lease to the store.
4. THE Quota_Component SHALL set a Lease_TTL on each Budget_Lease record using the store's clock so
   that a crashed worker's lease is reclaimed after the Lease_TTL expires.
5. IF a worker crashes without returning its lease, THEN THE store SHALL reclaim the unspent lease
   by Lease_TTL expiry so the budget is not permanently lost.

### Requirement 4: Low-watermark asynchronous refill off the request path

**User Story:** As a platform operator, I want lease refills to happen in the background before the
lease runs out, so that a store outage never fails a request on a synchronous refill read.

#### Acceptance Criteria

1. WHEN the Remaining_Lease drops to or below the Low_Watermark, THE Quota_Component SHALL trigger
   an Async_Refill to acquire a new Lease_Chunk.
2. THE Quota_Component SHALL perform the Async_Refill off the request path and SHALL NOT issue a
   store read to refill the lease during request admission.
3. WHILE an Async_Refill is in progress, THE Quota_Component SHALL continue admitting
   budget-eligible requests from the Remaining_Lease.
4. IF an Async_Refill fails, THEN THE Quota_Component SHALL continue serving from the Remaining_Lease
   and SHALL retry the Async_Refill off the request path.
5. THE Quota_Component SHALL compute the Low_Watermark from the Lease_Chunk size rather than from a
   hardcoded literal.

### Requirement 5: Per-path outage posture — budget_unavailable, not shared_state_unavailable

**User Story:** As an API consumer, I want a store outage to fail only my quota with a distinct
reason after my lease is spent, so that a budget-counter problem is not reported as a global outage.

#### Acceptance Criteria

1. WHILE the store is unavailable and the Remaining_Lease is above zero, THE Quota_Component SHALL
   admit budget-eligible requests from the Remaining_Lease.
2. WHEN the store is unavailable and the Remaining_Lease is exhausted, THE Quota_Component SHALL
   refuse budget-eligible requests with the posture code `budget_unavailable`.
3. THE Quota_Component SHALL NOT return `shared_state_unavailable` for a budget-only failure.
4. WHILE the store is unavailable, THE Quota_Component SHALL allow identity, kill switch, and plan
   to be served from RAM for the declared window independently of the budget posture.
5. THE Quota_Component SHALL reuse the `BUDGET_UNAVAILABLE` code and the `gap_retry_after_s` helper
   from `domain/posture.py` and SHALL NOT define a second budget-outage code.
6. WHEN a request is refused with `budget_unavailable`, THE Quota_Component SHALL carry a
   Retry-After value of at least `MIN_RETRY_AFTER_S` computed via `gap_retry_after_s`.

### Requirement 6: Budget generation tag and revoke-on-change

**User Story:** As a platform operator, I want a lease invalidated when the org budget config
changes, so that stale budget is never spent after a config change.

#### Acceptance Criteria

1. WHEN a Budget_Lease is acquired, THE Quota_Component SHALL tag it with the current
   Budget_Generation of the Org.
2. WHEN the Org budget configuration changes, THE Quota_Component SHALL advance the
   Budget_Generation.
3. IF a held Budget_Lease carries a Budget_Generation older than the current generation, THEN THE
   Quota_Component SHALL treat that lease as invalid and SHALL NOT spend it.
4. WHEN a held lease is found to carry a stale Budget_Generation, THE Quota_Component SHALL acquire
   a new lease under the current generation before admitting further budget-eligible requests.

### Requirement 7: No replica multiplication of the per-org limit

**User Story:** As a platform operator, I want N replicas to admit at most the org limit in
aggregate, so that scaling out does not multiply a tenant's effective rate.

#### Acceptance Criteria

1. THE Quota_Component SHALL enforce the per-Org token budget through the shared Budget_Lease so
   that aggregate admission across all workers and replicas does not exceed the Org limit plus the
   declared lease overshoot.
2. THE Quota_Component SHALL NOT multiply the per-Org limit by the worker count or the replica count.
3. WHEN multiple workers share one Org's budget, THE Quota_Component SHALL draw all admissions from
   the one shared Budget_Lease pool rather than from independent per-worker counters.
4. THE Quota_Component SHALL publish the declared lease overshoot so the aggregate bound
   (limit + overshoot) is observable.

### Requirement 8: Retry one idempotent store read on timeout

**User Story:** As an API consumer, I want a transient cross-zone timeout retried once, so that the
rare TCP RTO spike does not fail a store read that would have succeeded.

#### Acceptance Criteria

1. IF an Idempotent_Read of the store times out, THEN THE Quota_Component SHALL retry that read at
   most once.
2. THE Quota_Component SHALL NOT retry a non-idempotent store operation on timeout.
3. IF the single retry of an Idempotent_Read also times out, THEN THE Quota_Component SHALL surface
   the timeout rather than retry again.
4. THE Quota_Component SHALL treat gateway placement in the store primary's zone as a deployment
   dependency and SHALL document that the retry-once-on-timeout is the code-side requirement.

### Requirement 9: Store-connection boundary D1/D2 fixes (keepalive error, dead-socket reconnect)

**User Story:** As a platform operator, I want a keepalive timeout surfaced as an error and dead
sockets reconnected, so that the push listener does not spin to out-of-memory and dead pooled
sockets do not answer with a 500.

#### Acceptance Criteria

1. IF a keepalive read returns `ETIMEDOUT`, THEN THE Store_Connection_Boundary SHALL surface it as
   an error and SHALL NOT treat it as "no message".
2. IF a pooled store socket is dead, THEN THE Store_Connection_Boundary SHALL reconnect rather than
   answer with a 500.
3. THE Store_Connection_Boundary SHALL retain the `require_bounded_client` and `bounded_timeout_s`
   contract so a store operation timeout stays below the refresh period.
4. THE Store_Connection_Boundary SHALL retain the push-listener dead-connection signatures
   (`error`, `no_wait`, `silent`) and the reconnect-after-backoff behaviour so the listener never
   spins without yielding.

### Requirement 10: 45-second partition regression test

**User Story:** As a developer, I want a reproducible partition regression test, so that the R2-19
out-of-memory spin and the R2-09 inside-window failures cannot silently return.

#### Acceptance Criteria

1. THE Partition_Regression_Test SHALL simulate a 45 s store partition using fakeredis and a
   keepalive-`ETIMEDOUT` / dead-socket simulation.
2. WHEN the simulated partition is active, THE Partition_Regression_Test SHALL assert the push
   listener surfaces the keepalive timeout as an error and does not spin to unbounded memory.
3. WHEN the simulated partition is active, THE Partition_Regression_Test SHALL assert budget
   admission spends only the Remaining_Lease and then returns `budget_unavailable`, never
   `shared_state_unavailable`.
4. WHEN the simulated partition is active, THE Partition_Regression_Test SHALL assert no store read
   is issued on the request path to refill the lease.

### Requirement 11: Local acceptance equivalents for the deferred cloud gates

**User Story:** As a developer, I want the GW06 live acceptance tests reproduced locally, so that
lease correctness is proven without a cloud fleet.

#### Acceptance Criteria

1. THE Local_Harness SHALL simulate N in-process replicas sharing one fakeredis Budget_Lease and
   SHALL assert aggregate admission does not exceed the Org limit plus the declared lease overshoot
   (local equivalent of LGW06-3).
2. WHEN store latency of 200 ms is injected, THE Local_Harness SHALL assert the store operation
   stays within the bounded timeout, the declared posture holds, and the connection pool does not
   grow unbounded (local equivalent of LGW06-5).
3. WHEN a fakeredis partition is injected, THE Local_Harness SHALL assert budget spends the
   Remaining_Lease and then returns `budget_unavailable`, and that the refill was never on the
   request path (R2-09 behaviour).
4. THE Quota_Component SHALL treat the full live four-replica fleet quota run, the live store
   failover (LGW06-6), and real cross-zone RTO measurement as deferred cloud/scale gates covered
   locally by the equivalents in this requirement.

### Requirement 12: Label-free quota metrics (producer only)

**User Story:** As a platform operator, I want lease and quota metrics as a fixed, label-free
series set, so that observability never creates a tenant-derived series that breaks exposition.

#### Acceptance Criteria

1. THE Quota_Component SHALL emit lease and quota metrics as a producer only, following the
   `admit/metrics.py` producer-vs-publisher split.
2. THE Quota_Component SHALL NOT attach a tenant-derived label (owner / org) to any metric series.
3. THE Quota_Component SHALL seed every counter to zero at construction so "no refills / no sheds"
   is distinguishable from "series absent".
4. THE Quota_Component SHALL export the declared lease overshoot, the Async_Refill count, and the
   `budget_unavailable` refusal count as fixed, label-free series.

### Requirement 13: Fail-closed budget and quota decisions

**User Story:** As a security operator, I want budget and quota to fail closed, so that an outage or
internal error never admits a request without a budget or quota decision.

#### Acceptance Criteria

1. IF the Quota_Component cannot reach a budget or quota decision, THEN THE Quota_Component SHALL
   refuse the request rather than admit without a decision.
2. THE Quota_Component SHALL record zero FAIL_OPEN events across the local partition and
   overload tests.
3. IF the Lease_Chunk or Low_Watermark cannot be computed, THEN THE Quota_Component SHALL refuse to
   acquire a lease rather than acquire with an unbounded value.
4. THE Quota_Component SHALL treat a lease carrying a stale Budget_Generation as unspendable rather
   than spending it on a best-effort basis.

## Correctness Properties (for property-based testing)

These properties express the invariants the local property-based tests MUST assert. Each is tagged
with the correctness-pattern category it belongs to.

1. **No replica multiplication (invariant).** For any number of in-process replicas sharing one
   Budget_Lease pool and any arrival pattern, aggregate admission never exceeds the Org limit plus
   the declared lease overshoot. The shared lease, not a local counter, bounds the aggregate.
2. **Unspent budget is always returned (round-trip / invariant).** Across any sequence of
   acquire / spend / shutdown and crash events, no budget is permanently lost: a cleanly stopped
   worker returns its Remaining_Lease, and a crashed worker's lease is reclaimed by Lease_TTL. The
   sum of spent-plus-returned-plus-reclaimed budget equals the acquired budget.
3. **Stale generation is never spent (invariant).** A Budget_Lease carrying a Budget_Generation
   older than the current generation is never spent; after a generation change, admissions draw
   only from a lease acquired under the current generation.
4. **Budget outage is narrow (invariant / metamorphic).** During a store outage, budget admission
   spends only the Remaining_Lease and then returns `budget_unavailable`, never
   `shared_state_unavailable`; identity, kill switch, and plan stay RAM-served for the declared
   window regardless of the budget posture.
5. **Refill is never on the request path (invariant).** No request-path admission ever issues a
   store call to refill the lease; the Async_Refill is triggered only at the Low_Watermark and runs
   off the request path. The count of request-path refill store calls is always zero.
6. **Idempotent read retries at most once (invariant).** An Idempotent_Read retries at most once on
   timeout, and a non-idempotent operation is never retried on timeout.
7. **Keepalive timeout is an error, never silence (error condition).** A keepalive `ETIMEDOUT` is
   surfaced as an error and never silently as "no message", so the push listener cannot spin to
   unbounded memory.
8. **GCRA bounds burst and rate (invariant / metamorphic).** Local admissions over any window never
   exceed the Local_Burst_Limit plus the sustained rate the Local_GCRA enforces, independent of the
   lease state.
9. **Lease chunk is contract-derived (invariant).** For any ResourceContract, the Lease_Chunk and
   the Low_Watermark are functions of the contract's measured inputs only; no capacity literal
   appears in the quota module.
10. **Fail-closed under error (error condition).** A missing contract-derived chunk, an undecidable
    budget decision, and a stale-generation lease all fail closed (refuse), never fail open.

## Measured acceptance targets (from the GW06 v3 amendment)

Used as the targets the local equivalents must reproduce in shape:

- **R2-09 baseline (the defect this feature removes):** during a 45 s store partition the RC2 fleet
  returned 211 × HTTP 503 `shared_state_unavailable` from +0.15 s, inside the declared 5 s RAM
  window, because the lease refill was synchronous on the request path.
- **R2-09 fixed behaviour:** during a store partition, budget spends the Remaining_Lease and then
  returns `budget_unavailable` (not `shared_state_unavailable`), identity/kill-switch/plan stay
  RAM-served for the declared window, and no refill store call is issued on the request path.
- **R2-14:** roughly 1e-4 of cross-zone round trips hit the 200 ms minimum TCP RTO over the 25 ms
  request-path timeout; the code fix is one retry of an idempotent read on timeout.
- **R2-19 (prototype defect):** 39 of 120 requests got 429 with budget remaining because a crashed
  worker's lease was lost; the fix is the Lease_TTL reclaim. The keepalive-`ETIMEDOUT`-as-no-message
  spin to out-of-memory is removed by surfacing it as an error.
- **LGW06-3 (local equivalent):** four replicas against one org quota → aggregate admission ≤ limit
  plus declared lease overshoot; overshoot published; quota does not multiply by replica count.
- **LGW06-5 (local equivalent):** inject 200 ms store latency → bounded timeout, declared posture,
  no unbounded pool growth.
- **LGW06-6 (deferred cloud gate):** live store failover → recovery within the declared window,
  posture holds. Out of local scope; represented by the fakeredis partition equivalents above.
