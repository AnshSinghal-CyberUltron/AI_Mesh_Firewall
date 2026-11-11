# Requirements Document

## Introduction

This feature adds an **admission-control component** to the v3 backend-rewrite tree
(`gateway_v2/gateway_v2/admit/`) as a fresh module in the existing `admit/` package (which today
holds `identity.py`, `quota.py`, `killswitch.py`, `grant.py`, but no admission controller). It
implements correction-register items R2-07, R2-08 and (folded in) R2-18, delivered by runbook card
GW19 ("Implement admission control, overload semantics and graceful drain").

The problem it solves: in v1 and the prototype, behaviour under overload is **emergent and
unbounded**. Queues grow, p99 collapses, memory climbs, and the system never recovers. A
gateway-side per-worker connection cap (GW03) breaks the SLO under overload and never recovers
(≈20 ms floor from event-loop stalls). An old 12 ms instantaneous latency bound sheds long prompts
first (prompt-size bias). A tenant-blind FIFO queue lets one tenant starve another. Sheds carry a
retry-after of 6–11 ms, which the OpenAI SDK honours and retries twice — amplifying load ≈2.6×.
And after a store partition heals, latency returns to the SLO only after 10–180 s.

The objective is to make behaviour under overload **explicit and bounded** rather than emergent:
owner-level CoDel admission per tenant, admitted work answered late and never abandoned, an
explicit HTTP 503 overload response with a jittered minimum Retry-After and `x-should-retry: false`,
bounded queues everywhere with exported depth and age, a graceful drain state machine on SIGTERM,
isolated-and-respawned worker crashes, backpressure for slow consumers, and bounded post-heal
latency recovery.

Capacity literals come only from the `ResourceContract`
(`gateway_v2/gateway_v2/runtime/resources.py`); the measured guard service rate `q_safe` is
**injected/configured, never hardcoded** (it is measured by card GW20 and fed back into this
component).

### Scope boundary (CRITICAL — no cloud resources)

The admission-control **component logic** is in scope and is fully locally verifiable: the CoDel
state machine, per-owner queue accounting and fairness, the shed decision and Retry-After /
`x-should-retry` semantics, bounded-queue invariants, the drain state machine, worker-crash
isolation logic, and backpressure. These are verified with unit tests, property-based tests, and a
**local in-process open-loop load harness** that drives the controller directly with an injected
clock and injected service rate (not a real HTTP fleet).

The following are **deferred cloud/scale gates**, out of local scope, represented locally where
possible and otherwise explicitly noted as deferred: the full live fleet 3×-q_safe run (LGW19-1 at
real scale), the GW20 live q_safe measurement that feeds this card, gate G-06 retry-amplification
against the real OpenAI SDK, and gate G-15 live post-heal recovery. Local equivalents: an in-process
SDK-retry-semantics simulation (for Retry-After behaviour) and an injected-clock post-heal recovery
test.

Deployment wiring (removing the inert `--worker-connections` flag and migrating off the deprecated
uvicorn worker class) is a **dependency on the serving-entrypoint card**, not part of this
component. This feature scopes the requirement to the admission-control component and its
**observable** concurrency cap; the serving entrypoint in the rewrite tree may be a stub.

## Glossary

- **CoDel_Controller**: The Controlled-Delay admission state machine applied per owner. It tracks
  the minimum sojourn time over a sliding Interval and sheds (drops the head of the queue at the
  door) when that minimum has stayed above Target for a full Interval, backing off more
  aggressively the longer the overload persists. The admission rule of this component.
- **Owner** / **Tenant**: The unit of fairness and isolation. Each owner has its own admission
  accounting; CoDel is applied per owner, never as one global FIFO. "Guard owner" denotes the
  owner that owns the shared guard queue, which is also admission-bounded.
- **Sojourn_Time**: The time a request has spent waiting in a queue before service — the quantity
  CoDel measures. Measured against the injected clock, in milliseconds.
- **Target**: The CoDel sojourn-time target below which no shedding occurs. Owner-signed value: 5 ms.
- **Interval**: The CoDel sliding window over which the minimum sojourn time is tracked.
  Owner-signed value: 100 ms.
- **Hard_Cap**: The absolute sojourn-time ceiling; a request whose sojourn time reaches the cap is
  shed regardless of CoDel state. Owner-signed value: 60 ms.
- **Guard_Wait_Budget**: The maximum time a request may wait for the guard, computed as
  `max(100, Hard_Cap + 40)` ms. With Hard_Cap = 60 ms this is 100 ms.
- **Q_Safe**: The measured safe concurrency / service rate of the guard, injected into the
  component (measured by GW20). The admission concurrency bound is derived from the ResourceContract
  and `Q_Safe`; `Q_Safe` is never hardcoded in this component.
- **ResourceContract**: The frozen capacity contract in
  `gateway_v2/gateway_v2/runtime/resources.py`, the only module permitted to hold
  capacity-position literals. Exposes `queue_depth(service_rate)`, `offered_service_rate()`,
  `target_p99_ms`, `utilization_cap`, `per_worker_rss`.
- **Admitted**: A request the CoDel_Controller allowed past the door. Admitted work is answered
  late if necessary but is never abandoned mid-flight.
- **Shed**: A request the CoDel_Controller rejected at the door before service, returned as an
  explicit overload response. A shed happens only at admission, never after admission.
- **Retry_After**: The retry delay carried by a shed response. Owner-proposed minimum ≥ 1 s,
  jittered.
- **Should_Retry_Policy**: The `x-should-retry: false` header (or an adequate Retry_After) emitted
  on a shed so a retrying SDK client does not amplify load.
- **Overload_Response**: The declared shed response: HTTP status 503, a Retry_After, a
  Should_Retry_Policy signal, and a request id, emitted before latency collapses.
- **Bounded_Queue**: A queue with a declared maximum depth whose current depth and oldest-item age
  are exported. Applies to request, guard, dispatch, egress, and audit queues.
- **Drain_Window**: The bounded, declared, measured, and published time window within which, after
  SIGTERM, in-flight streams are finished before the process exits.
- **Declared_Termination**: An explicit terminal frame sent to a stream that exceeds the
  Drain_Window, as opposed to a silently truncated frame.
- **Load_Harness**: The local in-process open-loop load generator that drives the CoDel_Controller
  with an injected clock and injected service rate, used for all local acceptance tests.
- **Injected_Clock**: A `Callable[[], float]` time source injected into the component so tests
  advance time deterministically (project convention, mirroring `KillSwitchSnapshot`).
- **FAIL_OPEN**: Any path where, under overload or error, a request is admitted or forwarded
  without an admission decision. The component forbids FAIL_OPEN; it fails closed to a shed.

## Requirements

### Requirement 1: Owner-level CoDel admission

**User Story:** As a platform operator, I want admission decided per owner by a CoDel controller, so
that behaviour under overload is explicit and bounded instead of a global FIFO that collapses.

#### Acceptance Criteria

1. THE CoDel_Controller SHALL apply admission decisions per Owner, maintaining separate sojourn-time
   state for each Owner.
2. WHILE the minimum Sojourn_Time over a full Interval stays at or below Target, THE
   CoDel_Controller SHALL admit every request for that Owner.
3. WHEN the minimum Sojourn_Time has stayed above Target for a full Interval, THE CoDel_Controller
   SHALL shed at the door, selecting the next shed point by the CoDel backoff schedule.
4. IF a request's Sojourn_Time reaches Hard_Cap, THEN THE CoDel_Controller SHALL shed that request
   regardless of CoDel backoff state.
5. THE CoDel_Controller SHALL use Target = 5 ms, Interval = 100 ms, and Hard_Cap = 60 ms as
   injected owner-signed parameters.
6. THE CoDel_Controller SHALL read the current time from an Injected_Clock supplied at construction.
7. THE CoDel_Controller SHALL bound the guard Owner's queue by the same admission rule applied to
   every other Owner.

### Requirement 2: Admitted work is answered late, never abandoned

**User Story:** As an API consumer, I want a request that was admitted to always receive an answer,
so that admission is a stable contract rather than a request that can vanish mid-flight.

#### Acceptance Criteria

1. WHEN the CoDel_Controller admits a request, THE Admission_Control_Component SHALL deliver a
   response for that request even if the response is late.
2. THE Admission_Control_Component SHALL perform shedding only at admission time, before service
   begins.
3. IF the system is under sustained overload, THEN THE Admission_Control_Component SHALL NOT drop
   any Admitted request after admission.

### Requirement 3: Remove the prompt-size-biased bound and the gateway-side per-worker cap

**User Story:** As a platform operator, I want the old 12 ms instantaneous bound and the GW03
per-worker connection cap removed, so that admission is not biased against long prompts and can
recover after overload.

#### Acceptance Criteria

1. THE Admission_Control_Component SHALL decide admission by Sojourn_Time and CoDel state only,
   independent of request prompt size.
2. THE Admission_Control_Component SHALL NOT apply a fixed 12 ms instantaneous latency bound as an
   admission rule.
3. THE Admission_Control_Component SHALL derive its concurrency bound from the ResourceContract and
   the injected Q_Safe rather than from a connection count.
4. WHERE the serving entrypoint still carries a per-worker connection cap, THE
   Admission_Control_Component SHALL treat removal of that cap as a declared dependency on the
   serving-entrypoint card and SHALL document the dependency.

### Requirement 4: Concurrency bound derived from the ResourceContract and injected Q_Safe

**User Story:** As a platform operator, I want the concurrency bound computed from measured capacity,
so that the cap is grounded in the signed contract and the measured guard rate, not a guessed number.

#### Acceptance Criteria

1. THE Admission_Control_Component SHALL compute its concurrency bound using the ResourceContract
   methods (`queue_depth`, `offered_service_rate`, `target_p99_ms`, `utilization_cap`) and the
   injected Q_Safe.
2. THE Admission_Control_Component SHALL receive Q_Safe through injection or configuration at
   construction time.
3. THE Admission_Control_Component SHALL NOT contain a hardcoded Q_Safe literal.
4. THE Admission_Control_Component SHALL hold capacity-position literals only by reference to the
   ResourceContract and SHALL NOT define its own capacity literals.
5. IF Q_Safe is not supplied, THEN THE Admission_Control_Component SHALL refuse to start and SHALL
   report that Q_Safe is unset.

### Requirement 5: Explicit overload response with status, Retry-After, and request id

**User Story:** As an API consumer, I want a shed to be a clear, declared response, so that my client
can react deterministically rather than observing a latency collapse.

#### Acceptance Criteria

1. WHEN the CoDel_Controller sheds a request, THE Admission_Control_Component SHALL emit an
   Overload_Response with HTTP status 503.
2. THE Overload_Response SHALL include a Retry_After value.
3. THE Overload_Response SHALL include a request id.
4. THE Admission_Control_Component SHALL emit the Overload_Response before admitted-request p99
   latency exceeds target.
5. THE Admission_Control_Component SHALL base the shed threshold on the measured Q_Safe.

### Requirement 6: Minimum jittered Retry-After and should-retry policy

**User Story:** As a platform operator, I want sheds to carry a long enough, jittered Retry-After and
a should-retry policy, so that a retrying SDK client does not amplify load.

#### Acceptance Criteria

1. WHEN a request is shed, THE Admission_Control_Component SHALL set Retry_After to a value greater
   than or equal to the configured minimum of 1 s.
2. THE Admission_Control_Component SHALL apply jitter to the Retry_After value.
3. WHEN a request is shed, THE Admission_Control_Component SHALL emit a Should_Retry_Policy of
   `x-should-retry: false` or a Retry_After adequate to prevent immediate client retry.
4. THE Admission_Control_Component SHALL NOT emit a Retry_After in the 6 ms–11 ms range.
5. WHERE a client retries a shed within the simulated SDK retry semantics, THE
   Admission_Control_Component SHALL keep the amplification factor below the G-06 gate bound in the
   local SDK-retry-semantics simulation.

### Requirement 7: Bounded queues everywhere with exported depth and age

**User Story:** As a platform operator, I want every shared queue bounded with observable depth and
age, so that memory stays bounded and overload is visible before it collapses the system.

#### Acceptance Criteria

1. THE Admission_Control_Component SHALL enforce a declared maximum depth on each of the request,
   guard, dispatch, egress, and audit queues.
2. IF enqueueing an item would exceed a queue's declared maximum depth, THEN THE
   Admission_Control_Component SHALL shed at the door rather than exceed the bound.
3. THE Admission_Control_Component SHALL export the current depth of each Bounded_Queue.
4. THE Admission_Control_Component SHALL export the age of the oldest item in each Bounded_Queue.
5. THE Admission_Control_Component SHALL derive each queue's maximum depth from the ResourceContract.

### Requirement 8: Graceful drain on SIGTERM

**User Story:** As a platform operator, I want a bounded, declared drain on shutdown, so that
in-flight streams finish cleanly, audit is flushed, and clients get a declared terminal frame rather
than truncated output.

#### Acceptance Criteria

1. WHEN SIGTERM is received, THE Admission_Control_Component SHALL stop accepting new requests.
2. WHILE draining, THE Admission_Control_Component SHALL finish in-flight streams within the
   declared Drain_Window.
3. IF an in-flight stream has not finished when the Drain_Window elapses, THEN THE
   Admission_Control_Component SHALL send a Declared_Termination terminal frame for that stream
   rather than a truncated frame.
4. WHEN draining completes or the Drain_Window elapses, THE Admission_Control_Component SHALL flush
   the audit queue before exit.
5. THE Admission_Control_Component SHALL publish the measured drain duration.
6. THE Admission_Control_Component SHALL read drain timing from the Injected_Clock so the drain
   state machine is deterministically testable.

### Requirement 9: Worker-crash isolation and respawn

**User Story:** As a platform operator, I want a crashing worker isolated and respawned, so that one
worker's failure does not take the whole serving unit down as it did in the prototype.

#### Acceptance Criteria

1. IF a worker crashes, THEN THE Admission_Control_Component SHALL isolate the crash to that worker.
2. WHEN a worker has crashed, THE Admission_Control_Component SHALL respawn a replacement worker.
3. WHILE one worker is crashing or respawning, THE Admission_Control_Component SHALL continue
   admitting and serving requests on the remaining workers.

### Requirement 10: Backpressure for slow consumers

**User Story:** As an API consumer with a fast connection, I want slow consumers isolated by
backpressure, so that my streams are unaffected and total memory stays bounded.

#### Acceptance Criteria

1. WHILE a consumer reads a stream slower than it is produced, THE Admission_Control_Component SHALL
   apply backpressure to that stream so its buffered memory stays within its declared bound.
2. THE Admission_Control_Component SHALL keep total streaming memory bounded regardless of the
   number of slow consumers.
3. WHILE some consumers are slow, THE Admission_Control_Component SHALL serve fast consumers without
   degradation attributable to the slow consumers.

### Requirement 11: Bounded post-heal latency recovery

**User Story:** As a platform operator, I want latency to return to the SLO quickly after a store
partition heals, so that recovery is bounded rather than taking up to three minutes.

#### Acceptance Criteria

1. WHEN a store partition heals, THE Admission_Control_Component SHALL return admitted-request
   latency to within the target SLO within 10 s, measured against the Injected_Clock in the local
   post-heal recovery test.
2. THE Admission_Control_Component SHALL treat the full live post-heal certification (gate G-15) as
   a deferred cloud/scale gate and SHALL cover it locally with an injected-clock recovery test.

### Requirement 12: Local open-loop load harness and scaled-down acceptance

**User Story:** As a developer, I want the overload behaviours verifiable locally, so that admission
correctness is proven without a cloud fleet.

#### Acceptance Criteria

1. THE Load_Harness SHALL drive the CoDel_Controller in-process using an Injected_Clock and an
   injected service rate.
2. WHEN offered load reaches 3× the injected Q_Safe, THE Load_Harness SHALL observe explicit
   shedding, admitted p99 within budget, and no unbounded memory growth (local equivalent of
   LGW19-1).
3. WHEN the Load_Harness ramps offered arrival rate open-loop until admitted p99 exceeds budget,
   THE Load_Harness SHALL record the highest passing arrival rate and the first failing arrival rate
   (LGW19-2).
4. WHEN the injected guard service rate is halved mid-run, THE Admission_Control_Component SHALL
   respond by shedding and SHALL keep queue age bounded (LGW19-5).
5. WHILE slow consumers are present on 50% of streams, THE Load_Harness SHALL confirm backpressure,
   bounded memory, and unaffected fast consumers (LGW19-6).
6. THE Load_Harness SHALL verify that the concurrency cap binds and is observable (LGW19-4), not
   inert as in v1.
7. THE Admission_Control_Component SHALL treat the full live fleet run, the GW20 live Q_Safe
   measurement, gate G-06 against the real OpenAI SDK, and gate G-15 as deferred cloud/scale gates,
   covered locally by the equivalents in this requirement where possible.

### Requirement 13: Fail-closed under overload and error

**User Story:** As a security operator, I want admission to fail closed, so that overload or an
internal error never silently forwards a request without an admission decision.

#### Acceptance Criteria

1. IF the CoDel_Controller cannot reach an admission decision, THEN THE Admission_Control_Component
   SHALL shed the request rather than admit without a decision.
2. THE Admission_Control_Component SHALL record zero FAIL_OPEN events across the local overload-burst
   test.
3. IF a Bounded_Queue bound cannot be computed, THEN THE Admission_Control_Component SHALL refuse to
   admit rather than enqueue without a bound.

## Correctness Properties (for property-based testing)

These properties express the invariants the local property-based tests MUST assert. They are listed
with the correctness-pattern category each belongs to.

1. **No abandonment (invariant).** For every request the CoDel_Controller admits, a response is
   eventually produced; no Admitted request is dropped. Admitted responses may be late but never
   absent.
2. **Shedding under sustained overload (metamorphic).** Under sustained offered load above the
   admission bound, the shed set is non-empty AND admitted p99 stays within Target budget. More
   overload does not push admitted p99 above budget.
3. **Per-tenant fairness (invariant).** One Owner saturating its share cannot push another Owner's
   shed rate above that other Owner's own fair share. Isolating per-owner CoDel preserves fairness
   regardless of the saturating Owner's arrival pattern.
4. **Queue-depth bound (invariant).** For every Bounded_Queue at every step, current depth ≤ the
   queue's declared maximum depth.
5. **Retry-After floor and should-retry (invariant).** Every shed response carries
   Retry_After ≥ the configured minimum (1 s) AND a Should_Retry_Policy of `x-should-retry: false`
   (or an adequate Retry_After). No shed carries a 6–11 ms Retry_After.
6. **CoDel quiescence (invariant).** The CoDel_Controller never sheds while the minimum Sojourn_Time
   has stayed at or below Target for a full Interval.
7. **Drain terminal guarantee (invariant).** On drain, every in-flight stream is terminated within
   the Drain_Window with a Declared_Termination terminal frame; no stream ends in a truncated frame.
8. **Retry-amplification bound (model-based).** Under the local SDK-retry-semantics simulation, the
   total load produced by shed-then-retry stays below the G-06 amplification bound — contrasted with
   the 6–11 ms Retry_After baseline that triples load.
9. **Post-heal recovery bound (metamorphic).** After an injected store heal, admitted latency
   returns to within the SLO within 10 s on the Injected_Clock.
10. **Error conditions (error).** Missing Q_Safe, an uncomputable queue bound, and an undecidable
    admission all fail closed (refuse-to-start or shed), never fail open.

## Measured acceptance targets (from runbook §0.2)

Used as the targets the local equivalents must reproduce in shape:

- Under a 4.3× overload burst (140 → 600 → 140 RPS over 120 s): CoDel-admitted requests hold
  p99 ≈ 17.8 ms; every shed is a declared 503 with a Retry_After; 0 FAIL_OPEN; recovery in ≈5 s.
- With the GW03 per-worker cap on (C10 off): admitted p99 ≈ 23.52 ms, sheds even at base load, and
  never recovers — the behaviour this component removes.
