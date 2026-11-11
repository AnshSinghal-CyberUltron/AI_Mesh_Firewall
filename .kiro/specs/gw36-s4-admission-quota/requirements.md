# Requirements Document

## Introduction

This feature is slice S4 of the GW36 "Production boot wiring and local guard model" card. Slices
S0–S3 are complete: the production profile (`AMF_PROFILE=production`) already resolves signed KEY
and PLAN state per worker, hashes identity, and splits 401/503 refusals. On that boot every request
still refuses at admission because the admission controller, the per-principal rate limiter, and the
per-org token budget are all wired to their fail-closed defaults (`fail_closed_admission`,
`fail_closed_budget` in `edge/chat_deps.py`). S4 replaces those defaults with the real components so
a valid key reaches the provider phase subject to a declared concurrency/queue bound, a per-principal
burst and sustained rate, and a per-org token budget.

The shipped machinery this slice wires already exists: the GW19 `AdmissionController`, the GW06
`QuotaComponent` (composing `LocalGCRA` + `BudgetLease`), the contract-derived bounds
(`admit/quota.py::derive_bounds`, `derive_lease_cap_tokens`, `derive_low_watermark`), the
`StateKind.BUDGET` record kind, and the budget-lease store key family
(`runtime/store_keys.py`). S4 supplies what is missing around them: a BUDGET applier in the gateway
`StateSynchroniser` appliers map, per-principal GCRA built from the resolved `Principal`, the
`AMF_Q_SAFE` refuse-to-start input, a per-org `BudgetLease` registry, a quota protocol `owner`, and
— because S2 recorded that control has no budget field — a budget source in control plus projector
BUDGET record writes and live pool seeding keyed on the BUDGET record's generation.

This document scopes requirements to S4 only. The overall GW36 design (plan
`docs/plans/2026-11-02-gw36-production-boot.md`, §5.4 and §9.4, review finding N4) is settled and is
not re-opened here.

## Glossary

- **Production_Boot**: the gateway wiring selected by `AMF_PROFILE=production` (built in S3). The
  subject of every requirement unless another system is named.
- **Admission_Controller**: the shipped GW19 `AdmissionController`, the concurrency/queue-depth
  admission gate. Its `admit(owner_id, request_id, enqueued_at)` returns `Admitted` or a
  `ShedVerdict`; `complete(request_id)` releases an admitted slot.
- **Quota_Component**: the shipped GW06 `QuotaComponent` (`admit/quota.py`) composing a per-worker
  `LocalGCRA` and a store-backed `BudgetLease` into one admit/refuse decision over the surface
  `evaluate(cost, *, request_id)` / `refund(cost, *, request_id)`.
- **Local_GCRA**: the per-worker Generic Cell Rate Algorithm (`admit/gcra.py`) enforcing a burst
  and sustained rate with no store round trip.
- **Budget_Lease**: the store-backed per-worker lease (`admit/lease.py`) on one org's shared token
  pool; a chunk is acquired from the store and spent locally.
- **Budget_Lease_Registry**: the new per-org collection of `Budget_Lease` / `Quota_Component`
  instances the Production_Boot builds, keyed by org id, created on first use for an org.
- **Principal**: the resolved per-key identity carrying `rate_per_s` and `burst`, produced by the
  S3 identity path from a signed KEY record.
- **Budget_Record**: a published `StateKind.BUDGET` record for one org, carrying the configured pool
  token count and a generation.
- **Budget_Generation**: the monotonic generation tag of a `Budget_Record`; equal to the record's
  published `feed_seq`. A lease carrying an older generation is never spent.
- **Budget_Pool_Counter**: the live remaining-token counter at the store key `{rv2}:budget:{org}:remaining`,
  distinct from the configured `Budget_Record`; the `Budget_Lease` decrements it.
- **Budget_Applier**: the new synchroniser applier for `StateKind.BUDGET`, added to the gateway
  `StateSynchroniser` appliers map alongside the KEY, PLAN, and KS appliers.
- **Q_Safe**: the measured guard service rate used to derive every admission bound, supplied through
  the `AMF_Q_SAFE` environment variable. DECLARED until GW20 measures it.
- **Projector**: the control-to-state projector service (`state_control/projector_service.py`,
  added in S2) that reads control's tables and writes signed KEY/PLAN/KS records.
- **Worker_Count**: the number of gateway worker processes the Production_Boot is configured to run,
  used to divide a per-principal sustained rate across workers.
- **budget_unavailable**: the narrow refusal posture the Quota_Component returns when an org's token
  budget cannot admit a request; distinct from `shared_state_unavailable`.

## Requirements

### Requirement 1: Refuse to start without a declared guard rate

**User Story:** As an operator, I want the production boot to refuse to start when the guard service
rate is not declared, so that admission bounds are never computed from a guessed value.

#### Acceptance Criteria

1. WHERE `AMF_PROFILE` equals `production`, WHEN the `AMF_Q_SAFE` environment variable is absent, empty, or contains only whitespace, THE Production_Boot SHALL refuse to start and SHALL emit a boot-log entry naming `AMF_Q_SAFE` as the missing variable.
2. WHERE `AMF_PROFILE` equals `production`, IF `AMF_Q_SAFE` is present but does not parse as a decimal number strictly greater than 0, THEN THE Production_Boot SHALL refuse to start and SHALL emit a boot-log entry naming `AMF_Q_SAFE` and reproducing the rejected value.
3. WHERE `AMF_PROFILE` equals `production`, WHEN `AMF_Q_SAFE` parses as a decimal number strictly greater than 0, THE Production_Boot SHALL pass that value as `q_safe` to `admit/quota.py::derive_bounds` and SHALL use the returned admission bounds.
4. WHILE any refuse-to-start condition in criteria 1, 2, or 6 holds, THE Production_Boot SHALL apply no default and no literal value for Q_Safe and SHALL NOT continue startup.
5. WHEN the Production_Boot derives admission bounds under criterion 3, THE Production_Boot SHALL emit a boot-log entry recording that Q_Safe is DECLARED and that the measured Q_Safe value is owned by GW20.
6. WHERE `AMF_PROFILE` equals `production`, IF `admit/quota.py::derive_bounds` raises `CapacityUnset` for the provided `AMF_Q_SAFE` value, THEN THE Production_Boot SHALL refuse to start and SHALL emit a boot-log entry indicating that the admission capacity is unset.

### Requirement 2: Wire the real admission controller

**User Story:** As an operator, I want the production boot to use the real admission controller, so
that requests are admitted against a declared concurrency and queue-depth bound instead of being
shed by the fail-closed default.

#### Acceptance Criteria

1. WHERE `AMF_PROFILE` equals `production`, THE Production_Boot SHALL construct the GW19 Admission_Controller with its queue and concurrency bounds derived from the declared `q_safe` sourced from the `AMF_Q_SAFE` environment variable, and SHALL inject it as the chat route admission decider in place of `fail_closed_admission`.
2. IF `AMF_Q_SAFE` is unset or resolves to a value that is not greater than zero, THEN THE Production_Boot SHALL refuse to start, SHALL report that `q_safe` is unset, and SHALL NOT fall back to `fail_closed_admission`.
3. IF any Admission_Controller bound cannot be derived from the resource contract, THEN THE Production_Boot SHALL refuse to start and SHALL NOT fall back to `fail_closed_admission`.
4. WHEN a request is admitted by the Admission_Controller, THE Production_Boot SHALL proceed to the quota phase for that request.
5. WHEN the Admission_Controller sheds a request, THE Production_Boot SHALL refuse that request with the shed verdict's code, a retry-after value of at least 1 second, and the request id, and SHALL make zero provider calls for that request.
6. WHEN an admitted request completes, THE Production_Boot SHALL release exactly one admitted slot for that request through `complete(request_id)`.
7. WHERE the shipped `AuditSink.calibrate` is wired, THE Production_Boot SHALL source the audit sizing inputs from its measured result.
8. IF the shipped `AuditSink.calibrate` is not wired, THEN THE Production_Boot SHALL read the declared audit drain rate and bytes-per-record from the environment as the dev profile does.

### Requirement 3: Per-principal burst and sustained rate

**User Story:** As a platform operator, I want each API key limited to its configured burst and
sustained rate, so that one key cannot saturate a worker.

#### Acceptance Criteria

1. WHEN a Principal is resolved for a request, THE Production_Boot SHALL enforce that request against a Local_GCRA built from that Principal's `rate_per_s` and `burst`, where an arrival is admitted only while it is within the `burst` tolerance of the sustained `rate_per_s` and the first arrival for a newly built Local_GCRA is always admitted.
2. WHEN the first request for a given key is resolved, THE Production_Boot SHALL cache that key's Local_GCRA instance, and WHEN any subsequent request for that same key is resolved while its resolved Principal is unchanged, THE Production_Boot SHALL reuse the single cached Local_GCRA instance rather than constructing a new one.
3. WHEN a KEY delta changes a key's resolved Principal's `rate_per_s` or `burst`, THE Production_Boot SHALL replace that key's cached Local_GCRA with a new instance built from the updated `rate_per_s` and `burst`, such that the next request for that key is evaluated against the updated values.
4. WHILE Worker_Count is 1 or greater, THE Production_Boot SHALL set each worker's Local_GCRA sustained rate to the Principal's `rate_per_s` divided by Worker_Count.
5. WHERE a Principal's `rate_per_s` is 0, THE Production_Boot SHALL apply no per-key request rate limit for that key, SHALL admit every request for that key with respect to this limit, and SHALL record that absence in the boot log marked DECLARED.
6. IF a request exceeds its key's `burst` or per-worker sustained rate, THEN THE Production_Boot SHALL refuse that request with the declared 429 rate-limited response, SHALL make zero provider calls for that request, and SHALL leave the key's Local_GCRA allowance unchanged so that the next legitimately timed arrival is still admitted.
7. THE Production_Boot SHALL enforce no per-key request-count limit beyond the `burst` and sustained rate, because per-key request-count limits are owned by GW27 and out of scope for this slice.

### Requirement 4: Per-org token budget with a lease registry

**User Story:** As a platform operator, I want each org's token budget enforced independently, so
that one org exhausting its budget does not affect another org.

#### Acceptance Criteria

1. THE Production_Boot SHALL maintain a Budget_Lease_Registry keyed by org id.
2. WHEN a request is the first for its org, THE Budget_Lease_Registry SHALL create that org's Budget_Lease from the org's Budget_Record pool tokens and tag it with the org's Budget_Generation.
3. WHEN a later request arrives for an org already present in the Budget_Lease_Registry, THE Budget_Lease_Registry SHALL reuse that org's existing Budget_Lease without re-reading the org's Budget_Record on the request path.
4. WHEN a request's token cost is drawn, THE Quota_Component SHALL spend it against the requesting org's Budget_Lease only and SHALL leave every other org's Budget_Lease remaining balance unchanged.
5. IF an org has no Budget_Record, THEN THE Quota_Component SHALL refuse the request with `budget_unavailable`, SHALL spend zero tokens, and SHALL make zero provider calls for that request.
6. WHILE one org's Budget_Lease is exhausted, THE Quota_Component SHALL admit a request for a different org whose Budget_Lease still holds remaining tokens sufficient for that request's token cost.
7. IF the requesting org's Budget_Lease cannot cover the request's token cost, THEN THE Quota_Component SHALL refuse the request with `budget_unavailable`, SHALL NOT return `shared_state_unavailable`, SHALL spend zero tokens, and SHALL make zero provider calls for that request.

### Requirement 5: Compose admission, rate, and budget in order

**User Story:** As a developer, I want admission, rate, and budget enforced in a fixed order, so
that a request is refused at the cheapest gate that rejects it.

#### Acceptance Criteria

1. WHEN a request is processed, THE Production_Boot SHALL evaluate the Admission_Controller first, then the Local_GCRA, then the Budget_Lease, in that fixed order, and SHALL stop at the first gate that refuses the request.
2. IF the Admission_Controller sheds a request, THEN THE Production_Boot SHALL refuse it without evaluating the Local_GCRA or the Budget_Lease.
3. IF the Local_GCRA refuses a request, THEN THE Production_Boot SHALL refuse it without spending the Budget_Lease.
4. WHEN a request spends budget and is then refused before any provider call, THE Quota_Component SHALL refund the spent cost through `refund(cost, *, request_id)` such that the net budget change for that request is zero.
5. IF any admission, rate, or budget decision is undecidable, THEN THE Production_Boot SHALL refuse the request, SHALL NOT issue any provider call, and SHALL leave the Budget_Lease net balance unchanged for that request.
6. WHEN the Admission_Controller admits, the Local_GCRA allows, and the Budget_Lease is successfully spent, THE Production_Boot SHALL proceed to the provider call for that request.

### Requirement 6: Quota evaluate and refund carry an owner

**User Story:** As a developer, I want the quota evaluate and refund surface to carry the owner, so
that a spend and its refund are attributed to the org whose lease was charged (review finding N4).

#### Acceptance Criteria

1. WHEN the Quota_Component evaluates a request carrying an owner, THE Quota_Component SHALL spend the request's cost against that owner's Budget_Lease only and SHALL decrement no other owner's Budget_Lease.
2. WHEN the Quota_Component refunds a request carrying an owner, THE Quota_Component SHALL credit the refund to that owner's Budget_Lease only.
3. WHEN a refund is requested for an owner, THE Quota_Component SHALL return exactly the spent cost to that owner's Budget_Lease and SHALL NOT credit any other org's Budget_Lease.
4. WHEN the Quota_Component admits a request, THE Quota_Component SHALL report on the `Admitted` outcome an owner equal to the owner passed to that `evaluate` call.
5. IF the owner on an `evaluate` or `refund` call is unknown or missing, THEN THE Quota_Component SHALL refuse with `budget_unavailable`, SHALL make zero provider calls, and SHALL leave every Budget_Lease unchanged.

### Requirement 7: BUDGET applier in the gateway synchroniser

**User Story:** As a developer, I want the gateway to apply published BUDGET records, so that budget
configuration changes reach the lease registry through the same signed-state path as keys and plans.

#### Acceptance Criteria

1. THE Production_Boot SHALL register exactly one Budget_Applier for `StateKind.BUDGET` in the gateway `StateSynchroniser` appliers map, alongside the KEY, PLAN, and KS appliers, such that the map contains an applier entry for each of the four kinds.
2. WHEN a BUDGET delta round is drained, THE Budget_Applier SHALL replace the served Budget_Record for each org named in that round's records with the record carried by the delta, and SHALL leave the served Budget_Record of every org not named in the round unchanged.
3. WHEN a drained BUDGET delta changes an org's Budget_Generation to a value other than the generation currently served for that org, THE Budget_Applier SHALL mark that org's Budget_Lease for re-acquisition under the new generation, and THE gateway SHALL refuse to admit further requests for that org, with an indication that budget state is re-acquiring, until a Budget_Lease under the new generation has been acquired for that org.
4. IF the Budget_Applier is handed a BUDGET delta that is a drift relative to the kind's current cursor or whose signature or completeness cannot be verified, THEN THE Budget_Applier SHALL refuse the delta, SHALL leave the BUDGET kind's cursor unchanged so the next round retries from the same position, SHALL leave every served Budget_Record unchanged, and SHALL report the refusal with a reason indicating the delta was drift or unverifiable.
5. IF the BUDGET kind has not been verified within the 5000 ms state-freshness window, THEN THE Production_Boot SHALL cause `/readyz` to return a non-success readiness response naming BUDGET as the unavailable dependency, consistent with the gating applied to the KEY, PLAN, and KS kinds.

### Requirement 8: Control budget source

**User Story:** As a platform operator, I want each org's token budget configurable in control, so
that the projector has a budget value to publish (S2 deferred this because control has no budget
field).

#### Acceptance Criteria

1. THE Control_Plane SHALL store a per-org token budget as a non-negative integer value between 0 and 1,000,000,000 tokens inclusive.
2. WHEN an org is created without an explicit token budget, THE Control_Plane SHALL store a declared default token budget of 100,000 tokens for that org.
3. IF a request sets an org's token budget to a value that is non-integer, negative, or greater than 1,000,000,000, THEN THE Control_Plane SHALL reject the request, retain the previously stored budget value unchanged, and return an error indicating the budget value is out of the permitted range.
4. WHEN an org's token budget value changes, THE Control_Plane SHALL make the new value readable by the Projector within 5 seconds of the change being committed.
5. THE Control_Plane SHALL expose the budget value as a numeric token count only and SHALL NOT include any raw credential or secret in the budget value.

### Requirement 9: Projector writes BUDGET records

**User Story:** As a developer, I want the projector to publish a signed BUDGET record for every
org, so that the gateway budget lease always has a record to read.

#### Acceptance Criteria

1. WHEN the Projector completes a round, THE Projector SHALL have written one signed `StateKind.BUDGET` record for every org that has a stored control token budget in that round's snapshot.
2. THE Projector SHALL write each Budget_Record carrying the org's configured pool token count as a non-negative integer and a Budget_Generation value.
3. WHEN an org has no prior Budget_Record, THE Projector SHALL write that org's first Budget_Record with an initial Budget_Generation.
4. WHEN an org's control token budget value changes between two consecutive rounds, THE Projector SHALL write an updated Budget_Record whose Budget_Generation is strictly greater than that org's previously written Budget_Generation.
5. WHEN an org's control token budget value is unchanged across consecutive rounds, THE Projector SHALL leave that org's Budget_Record and Budget_Generation unchanged (no new record written and the Budget_Generation not advanced).
6. IF an org has no stored control token budget in a round's snapshot, THEN THE Projector SHALL NOT write a Budget_Record for that org and SHALL leave any previously written Budget_Record for that org unchanged.

### Requirement 10: Projector seeds the live pool counter

**User Story:** As a developer, I want the projector to seed the live budget pool counter from the
BUDGET record, so that the lease has tokens to decrement and the counter tracks the current
generation (review finding N4).

#### Acceptance Criteria

1. WHEN the Projector writes a Budget_Record for an org under a new Budget_Generation, THE Projector SHALL seed that org's Budget_Pool_Counter at the store key `{rv2}:budget:{org}:remaining` to the Budget_Record's configured pool token count.
2. THE Projector SHALL set the Budget_Generation store counter equal to the Budget_Record's `feed_seq`.
3. WHEN the Projector seeds the Budget_Pool_Counter, THE Projector SHALL apply a compare-and-set conditioned on the stored generation equal to the Budget_Record's `feed_seq`, so a concurrent writer under a different generation does not overwrite the pool.
4. WHEN an org's Budget_Generation advances, THE Projector SHALL re-seed the Budget_Pool_Counter under the new generation.
5. WHEN the Projector processes an org whose Budget_Generation is unchanged from the generation already seeded, THE Projector SHALL NOT re-seed the Budget_Pool_Counter, so live lease decrements are not clobbered.
6. IF the compare-and-set on the generation fails, THEN THE Projector SHALL leave the Budget_Pool_Counter unchanged and SHALL retry on a later round, up to a bounded number of attempts.

### Requirement 11: Live acceptance evidence

**User Story:** As the card owner, I want S4 proven on the live stack, so that L36-1's admission half
is demonstrated with real wire bytes.

#### Acceptance Criteria

1. WHEN a burst of requests for one key exceeds that key's configured burst limit or sustained rate limit on the live Production_Boot, THE Production_Boot SHALL return an HTTP 429 response, observable on the wire, carrying the declared rate-limited indicator for that key.
2. WHEN an org's token budget is exhausted on the live Production_Boot, THE Production_Boot SHALL reject that org's next request with a response, observable on the wire, carrying a `budget_unavailable` indicator, and SHALL leave that org's budget state unchanged.
3. WHILE one org's token budget is exhausted on the live Production_Boot, THE Production_Boot SHALL admit a request for a second org whose budget is not exhausted, returning a success response observable on the wire without any rate-limited or `budget_unavailable` indicator.
4. WHEN the Production_Boot completes startup on the live stack, THE Production_Boot SHALL emit a single boot-log line that names each wired admission, rate, and budget dependency.

## Out of scope for S4

The following are named so they are not read as part of this slice. They are owned by other cards
or later slices of GW36.

- Per-key request-count limits (GW27). S4 enforces only burst and sustained rate per key; a token
  budget is enforced per org via the lease.
- Multi-model routing and per-org BYOK provider catalogue (GW28).
- Kill-switch enforcement on the request path (GW29). S4 does not add request-path kill-switch
  behaviour.
- The measured Q_Safe value (GW20). S4 consumes `AMF_Q_SAFE` as DECLARED.
- The real ONNX guard and the async semantic stage (GW36 slice S6).
- The production provider, catalogue, and the full `AMF_PROFILE=production` completion path proving
  L36-1 end to end (GW36 slice S5); S4 wires admission, rate, and budget only.
