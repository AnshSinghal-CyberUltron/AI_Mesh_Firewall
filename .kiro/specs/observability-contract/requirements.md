# Requirements Document

## Introduction

GW14d (Observability contract v3) unifies the six already-shipped, label-free metric **producers**
in `gateway_v2` behind one **Publisher** that implements the `MetricsRegistry.exposition()`
contract the raw-ASGI `/metrics` endpoint already consumes but that nothing provides yet. It closes
two items from the v3 corrections register:

- **R2-10 (CRITICAL)** — metric registration ran on the request path and could raise. A 64 KiB name
  directory meant ≈40% of a new tenant's first requests returned HTTP 500 at roughly 480
  UUID-named tenants, and per-org series were silently truncated at 512 per worker. The correction
  is a request-path operation that NEVER raises: a size check before insert; overflow degraded to a
  counted, exported process-local counter; per-tenant label cardinality bounded by top-K + "other";
  the directory capacity derived from the `ResourceContract` slot counts rather than a literal.

- **R2-11 (HIGH)** — the server-side view could not certify the SLO. It under-read by 3.2–5.8 ms
  with lifetime quantiles; it emitted untyped exposition that blinded the GKE HPA; counter resets
  published negative rates; `rejected_503` merged distinct causes; `plan_unavailable` went
  uncounted; FAIL_CLOSED was counted as a policy BLOCK; and audit loss was invisible. The correction
  (GW14d) is: a windowed per-request add-on histogram carrying the C4 definition with native
  Prometheus buckets; a held-tokens histogram; a `# TYPE` line on every family; reset-safe rates (no
  baseline ⇒ omit, never a negative); a `rejected{status,reason}` family; posture counters kept
  separate from policy BLOCK; a rate-limited structured event log; and a `/readyz` that fails when
  the metrics directory is missing.

The operator-facing objective, from the GW14d runbook, is: *"The operator sees the SLO, the reasons
and the losses; monitoring never breaks serving."* The production SLO itself is certified by a
black-box synthetic client and provider canary — not by this card — so GW14d's job is to make the
server-side view TRACK the client view, not to replace it.

This is a backend-rewrite card in the `gateway_v2/` package (Python 3.12, raw ASGI, no HTTP
framework; runtime deps are `psycopg` + `redis` only). It unifies and extends existing code; it
does not rebuild the producers.

### Scope boundaries (decisions already taken)

- **`rc3-obs-v1` is a SPEC REFERENCE only.** The repo has no Prometheus client library. The typing,
  reset-safety and GPU-busy machinery are reimplemented in the gateway_v2 producer/publisher idiom;
  the runbook text is the contract, not the reference patch's code shape.
- **The windowed-busy / GPU gauge mechanism is in scope; a GPU data source is OUT of scope.** The
  windowed-histogram machinery is built generically so GW20b/GW25 can feed GPU samples later. No GPU
  producer is fabricated now (none exists locally).
- **"Metrics shared memory" is modelled as the registry's own in-memory cardinality directory.**
  `gateway_v2` is single-process-per-worker with in-memory producers, so `/readyz`'s "metrics shared
  memory present" and R2-10's bounded directory are the Publisher's backing cardinality directory
  being initialized and available — NOT a new mmap segment.
- **Local-only; no cloud.** Verification is the offline `pytest` suite, `mypy --strict`, `ruff`,
  `import-linter`, and the AST gates (capacity-literals, no-module-mutable, frozen-dataclasses,
  http-outside-edge-resolve, tenant-scale). The L14d-1..5 live acceptance tests are expressed as
  requirements but certified by their offline/injected-clock equivalents in this local half; the
  cloud halves (HPA resolution, 20,000-tenant live scale) are deferred.

### House style (inherited constraints — bake into every requirement)

- Producer-vs-Publisher split: producers stay label-free value emitters; the Publisher merges,
  types and exposes.
- Label-free series; real zeros, never absence; fail-closed everywhere.
- No capacity literal outside `runtime/resources.py` (the capacity-literals AST gate).
- No tenant-derived label on any producer series — a tenant-derived label IS the R2-10 defect. The
  bounded top-K/"other" directory is the ONLY place per-tenant visibility is restored, and it is
  bounded by construction.
- Correctness properties are expressed as seeded ≥10,000-iteration property tests (the house idiom;
  no `hypothesis` library).

## Glossary

- **Publisher**: The new unifying registry component that implements `MetricsRegistry.exposition()`.
  It merges all six producers' `series()`/`snapshot()` readings, emits a `# TYPE` line on every
  family, renders native Prometheus histogram buckets, and owns the bounded cardinality directory.
- **Producer**: One of the six shipped label-free metric emitters — `admit.metrics`
  (`AdmissionMetrics`, `QuotaMetrics`), `audit.metrics` (`AuditMetrics`), `state_control.metrics`
  (`RehydrationMetrics`), `runtime.state_metrics` (`StateMetrics`), `runtime.stream_metrics`
  (`PerRequestExports`), `runtime.holdback_metrics` (`HoldbackMetrics`). Each has
  `series()`/`snapshot()` and the no-tenant-label + real-zeros discipline. GW14d does not rebuild
  any of them.
- **Cardinality_Directory**: The Publisher's in-memory, bounded registry of per-tenant label slots.
  This is the "metrics shared memory" model: a sized directory the request path consults, never a
  new mmap. Its capacity is derived from `ResourceContract` slot counts.
- **Request_Path_Observation**: Any metric operation reachable from a serving request (e.g.
  registering or incrementing a per-tenant counter). The R2-10 contract is that this operation never
  raises.
- **Overflow_Counter**: A single process-local counter that is incremented and exported whenever a
  per-tenant insert cannot be accommodated by the Cardinality_Directory. It is the counted,
  exported degraded path that replaces raising.
- **Top_K_Directory**: The bounded per-tenant label scheme: the K highest-magnitude tenants keep
  their own slot and all remaining tenants collapse into a single `other` bucket.
- **Windowed_Histogram**: A rolling-window integer histogram whose p50/p99 reflect only samples
  inside the current window, with native Prometheus bucket boundaries. Distinct from the shipped
  lifetime nearest-rank `Histogram`.
- **C4_Definition**: The per-request latency definition the client synthetic uses — the add-on
  (gateway-attributable) latency the server adds on top of the provider. The Windowed_Histogram
  measures exactly this quantity server-side so the two views can be compared.
- **Reset_Safe_Rate**: A rate derived from a counter such that if no prior baseline sample exists
  (first observation, or a counter that went backwards after a restart), the rate is OMITTED rather
  than published as zero or negative.
- **Posture_Code**: One of the single-spelled failure codes in `domain.posture` —
  `KILL_SWITCH_UNAVAILABLE`, `SHARED_STATE_UNAVAILABLE`, `PLAN_UNAVAILABLE`, `BUDGET_UNAVAILABLE`,
  `OVERLOAD_SHED`, plus the GW12 `STREAM_*` codes. GW14d reuses these spellings; it invents none.
- **Admission_Reason**: One of the reason-separated admission outcomes in `audit.record` — `SHED`,
  `REJECTED` (and the `shed`/`rejected` reasons an admission record must carry). GW14d reuses this
  vocabulary for the `rejected{status,reason}` family.
- **Policy_BLOCK**: A disposition produced by the detection/policy pipeline (`block`). A fail-closed
  posture refusal is NOT a Policy_BLOCK and must be counted separately.
- **Structured_Event_Log**: A rate-limited stream of structured log lines, one class of line per
  client error class, so every error class has both a counter and a log line.
- **Rate_Limited**: Bounded emission such that a burst of identical events cannot produce unbounded
  log volume; emission is throttled per event class.
- **SERIES_COUNT**: Each producer's fixed, by-construction count of the series it can ever emit (an
  expression, asserted equal to `len(series())`). The Publisher's exposition count is the sum of the
  producers' SERIES_COUNTs plus the bounded set of tenant/`other` series.
- **Layer_Contract**: The import-linter `layers` ordering: edge > admit > plan > detect > resolve >
  dispatch > egress > audit > runtime > contracts > domain. `state_control` and `audit_control` sit
  outside `root_packages=["gateway_v2"]`, as the shipped producers already do.
- **ResourceContract**: `runtime/resources.py` — the only module permitted to hold capacity-position
  literals. Slot counts and directory size derive from it.

## Requirements

### Requirement 1: Publisher implements the exposition contract

**User Story:** As an operator, I want one endpoint that exposes every gateway metric in a single typed Prometheus text body, so that one scrape sees the whole gateway without the serving loop being blocked.

#### Acceptance Criteria

1. THE Publisher SHALL implement the `MetricsRegistry` protocol by providing an `exposition()` method that returns the Prometheus text exposition as a single string.
2. WHEN `exposition()` is called, THE Publisher SHALL read all six producers (admit.metrics AdmissionMetrics and QuotaMetrics, audit.metrics AuditMetrics, state_control.metrics RehydrationMetrics, runtime.state_metrics StateMetrics, runtime.stream_metrics PerRequestExports, runtime.holdback_metrics HoldbackMetrics) and concatenate their readings into one text body.
3. IF reading any one producer raises an error during `exposition()`, THEN THE Publisher SHALL emit the readings of all producers that read successfully, omit the family output of the failing producer, and return a complete text body (never raising to the caller).
4. THE Publisher SHALL emit exactly one `# TYPE` line immediately preceding the first series line of each distinct metric family name it exposes, where a histogram family's `_bucket`, `_sum`, and `_count` series together count as one family named by their shared base name.
5. WHERE a metric family is not a histogram and its name ends with `_total`, THE Publisher SHALL type that family as a counter; WHERE a non-histogram family name does not end with `_total`, THE Publisher SHALL type that family as a gauge; WHERE a family is a histogram, THE Publisher SHALL type that family as a histogram.
6. WHEN `exposition()` renders a histogram family, THE Publisher SHALL emit the `_bucket`, `_sum`, and `_count` series with explicit, finite bucket boundaries and SHALL include a terminal `+Inf` bucket whose value equals the family's `_count`.
7. IF two or more producers expose the same metric family name, THEN THE Publisher SHALL emit that family name at most once with a single `# TYPE` line so that the output contains no duplicate `# TYPE` declaration for any family name.
8. WHILE the Publisher is read through `/metrics`, THE edge serving skin SHALL compute the exposition off the serving loop via asyncio.to_thread so that the serving loop remains available to accept and forward other requests for the full duration of the exposition computation.
9. THE Publisher SHALL reside at a Layer_Contract position that permits it to read the `admit`, `audit`, `runtime`, and `state_control` producers without violating the import-linter layers contract (edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain).

### Requirement 2: Request-path metric operations never raise (R2-10 core)

**User Story:** As a new tenant, I want my first requests to succeed, so that metric registration can never turn a successful request into an HTTP 500.

#### Acceptance Criteria

1. WHEN a Request_Path_Observation registers or updates a per-tenant metric slot, THE Publisher SHALL complete the metric operation without propagating any exception to the serving request _(R2-10)_.
2. IF any error occurs during a Request_Path_Observation metric operation (including allocation failure, serialization failure, or an unexpected internal error), THEN THE Publisher SHALL catch the error, route the observation to the Overflow_Counter, and return control to the serving request without raising _(R2-10, L14d-1)_.
3. IF the Cardinality_Directory holds a number of per-tenant entries equal to its derived capacity and no free slot exists for a new per-tenant entry, THEN THE Publisher SHALL route the observation to the Overflow_Counter instead of allocating a slot _(R2-10)_.
4. WHEN an observation is routed to the Overflow_Counter, THE Publisher SHALL increment the Overflow_Counter by exactly one and SHALL NOT allocate or modify any per-tenant slot for that observation _(R2-10)_.
5. THE Publisher SHALL expose the Overflow_Counter as a single process-local `_total` counter series that is included in every metrics scrape response, with an initial exported value of zero before the first overflow _(R2-10, L14d-1)_.
6. WHEN the Publisher evaluates whether a new per-tenant slot can be allocated, THE Publisher SHALL complete the capacity size check and reach an allocate-or-overflow decision before performing any insert into the Cardinality_Directory _(R2-10)_.
7. THE Publisher SHALL derive the Cardinality_Directory capacity from `ResourceContract` slot counts and SHALL NOT hold a capacity-position literal in any module outside `runtime/resources.py` _(R2-10)_.

### Requirement 3: Bounded per-tenant label cardinality (R2-10 — top-K + other)

**User Story:** As an SRE, I want per-tenant visibility that cannot grow without bound, so that restoring "which tenant" never reintroduces the 512-slot overflow or the 50,003-series blowup.

#### Acceptance Criteria

1. THE Publisher SHALL restore per-tenant visibility only through the Top_K_Directory, where the Top_K_Directory maps each of the K highest-magnitude tenants to an individual series and all other tenants to a single `other` series.
2. THE Publisher SHALL NOT attach any tenant-derived label (tenant ID, org slug, or any value uniquely identifying an individual tenant) to any producer series.
3. THE Publisher SHALL retain at most K individual per-tenant slots, where K is a fixed value derived from the `ResourceContract` slot counts and does not change in response to the observed tenant count.
4. WHEN the number of observed tenants is less than or equal to K, THE Publisher SHALL emit one individual series per observed tenant and SHALL emit the `other` series with a zero value.
5. WHEN the number of observed tenants exceeds K, THE Publisher SHALL emit an individual series for each of the K tenants ranked highest by magnitude and SHALL collapse every tenant not in that top-K set into the single `other` series.
6. IF two or more tenants at the K-th rank boundary share an identical magnitude, THEN THE Publisher SHALL retain exactly K individual series by applying a deterministic tie-break that yields the same selection on repeated exposition of the same input set.
7. THE Publisher SHALL emit the `other` series as an explicit zero value from the first exposition onward, including when no tenant has overflowed, so that the state "no overflow tenants" (series present with value zero) is distinguishable from the state "series absent".
8. THE total count of series THE Publisher emits for per-tenant visibility SHALL be at most K plus one for every exposition, regardless of the observed tenant count.

### Requirement 4: Windowed per-request add-on histogram (R2-11 / L14d-1)

**User Story:** As an operator certifying the SLO, I want the server-side p99 to match the synthetic client's p99 over the same window, so that the server view can corroborate the SLO instead of under-reading it.

#### Acceptance Criteria

1. WHEN a request completes, THE Publisher SHALL record one C4_Definition per-request add-on latency sample, in milliseconds, into the Windowed_Histogram, where C4_Definition is the gateway-attributable latency the gateway adds on top of the provider.
2. THE Publisher SHALL apply a single configured window length identically to the server-side Windowed_Histogram and to the client C4 comparison window, with window boundaries driven by a deterministic injected clock.
3. THE Windowed_Histogram SHALL compute its p50 and p99 from only the samples inside the current window, and SHALL NOT compute, retain, or fall back to lifetime quantiles.
4. THE Windowed_Histogram SHALL expose native Prometheus histogram buckets with explicit, finite boundaries spanning from 0 milliseconds to at least 1000 milliseconds, plus a terminal overflow bucket, with no infinite boundary other than that terminal bucket.
5. WHEN the server-side Windowed_Histogram p99 is compared with a client C4 p99 computed over the same window using the same injected clock, THE Publisher SHALL produce a server p99 that is within 0.5 milliseconds of the client p99 and that does not under-read the client p99 by more than 0.5 milliseconds. _(L14d-1)_
6. WHEN a new window begins, THE Windowed_Histogram SHALL discard samples that fall outside the window before reporting a quantile.
7. IF the current window contains no samples, THEN THE Windowed_Histogram SHALL report a defined empty-window indication rather than a stale, lifetime, or zero quantile.

### Requirement 5: Reset-safe rates (R2-11)

**User Story:** As an operator, I want rates that are never negative, so that a worker restart or counter reset does not publish an impossible rate.

#### Acceptance Criteria

1. WHEN the Publisher computes a Reset_Safe_Rate for a counter-derived rate series and no prior baseline sample exists for that counter, THE Publisher SHALL omit the rate series from the published output rather than publish any value, including zero.
2. IF a counter's current value is strictly less than its prior baseline value, THEN THE Publisher SHALL classify the counter as reset for the current publication interval and SHALL omit the counter-derived rate series for that interval rather than publish a value computed from the current-minus-baseline difference.
3. WHEN the Publisher classifies a counter as reset per criterion 2, THE Publisher SHALL adopt the counter's current value as the new baseline for the next publication interval such that the immediately following interval with a non-decreasing counter value publishes a non-negative rate.
4. THE Publisher SHALL ensure that every published counter-derived rate series value is greater than or equal to zero, and SHALL never emit a value less than zero for any counter-derived rate series.
5. WHILE a sentinel gauge whose value or absence is itself the signal (such as a freshness-age sentinel using a reserved not-yet-observed value) is being published, THE Publisher SHALL apply the no-baseline omission rule from criterion 1 and the reset omission rule from criterion 2 to counter-derived rate series ONLY, and SHALL continue to emit the sentinel gauge with its reserved value unchanged.

### Requirement 6: Reason-separated rejection family (R2-11 M2)

**User Story:** As an on-call engineer, I want each 503 cause to have its own counter, so that 9,241 kill-switch 503s are never indistinguishable from 8 store-outage 503s.

#### Acceptance Criteria

1. WHEN the Publisher exposes the `rejected` metric family, THE Publisher SHALL dimension every series by exactly two labels — a `status` label and a `reason` label — where `reason` is drawn solely from the closed Admission_Reason vocabulary defined in `audit.record` (the admission-rejection causes: kill switch, revoked key, unverifiable plan, and overload shed).
2. WHEN two admission rejections occur with distinct `reason` values, THE Publisher SHALL increment two distinct `rejected` series and SHALL NOT merge them into any single aggregated series such as `rejected_503`.
3. THE Publisher SHALL draw the complete set of emitted `reason` label values from a closed, finite set of at most the Admission_Reason vocabulary members, and THE Publisher SHALL NOT emit any `reason` label value derived from tenant-, org-, or request-supplied input.
4. WHEN the Publisher produces its first exposition after start-up and before any admission rejection has occurred, THE Publisher SHALL emit one series per `reason` bucket in the Admission_Reason vocabulary, each with the value exactly 0, so that "no rejections of this cause" (series present, value 0) is distinguishable from "cause not instrumented" (series absent).
5. IF an admission rejection carries a `reason` value that is not a member of the Admission_Reason vocabulary, THEN THE Publisher SHALL NOT fall back to a merged or default rejection series, SHALL preserve the per-reason counters, and SHALL surface an error indication that an out-of-vocabulary reason was supplied.

### Requirement 7: Posture counters separate from policy BLOCK (R2-11)

**User Story:** As a security operator, I want a fail-closed posture refusal counted separately from a policy block, so that FAIL_CLOSED is never read as a detection decision.

#### Acceptance Criteria

1. THE Publisher SHALL expose exactly one distinct counter series for each Posture_Code defined in `domain.posture` (KILL_SWITCH_UNAVAILABLE, SHARED_STATE_UNAVAILABLE, PLAN_UNAVAILABLE, BUDGET_UNAVAILABLE, OVERLOAD_SHED, and the GW12 STREAM_* codes), with no two Posture_Codes sharing a counter series.
2. WHEN a posture refusal with a given Posture_Code occurs, THE Publisher SHALL increment that code's own posture counter by exactly 1 and SHALL NOT increment any other Posture_Code counter.
3. WHEN a Posture_Code refusal occurs, THE Publisher SHALL leave every Policy_BLOCK series unchanged.
4. THE Publisher SHALL name each posture counter using the exact single-spelled code string from `domain.posture` and SHALL NOT expose any alternative-spelling series for a posture code.
5. IF the Publisher produces its first exposition before any posture refusal has occurred, THEN THE Publisher SHALL emit every Posture_Code counter as a present series with value exactly 0 rather than omit it.
6. WHEN a fail-closed posture refusal occurs, THE Publisher SHALL increment only the corresponding Posture_Code counter and SHALL NOT increment any Policy_BLOCK series for that event.

### Requirement 8: Held-tokens histogram exposition (R2-11)

**User Story:** As an operator, I want the holdback held-tokens distribution exposed as a typed histogram, so that the bounded-holdback behaviour is observable fleet-wide.

#### Acceptance Criteria

1. THE Publisher SHALL expose the `HoldbackMetrics` held-tokens distribution as a Prometheus histogram family preceded by exactly one `# TYPE` line declaring the histogram type.
2. THE Publisher SHALL render the held-tokens histogram with native Prometheus `_bucket`, `_sum`, and `_count` series and explicit, finite bucket boundaries plus a terminal `+Inf` bucket whose value equals the family `_count`.
3. THE Publisher SHALL expose the held-tokens histogram with no tenant-derived label.

### Requirement 9: Rate-limited structured event log (R2-11 / L14d-5)

**User Story:** As an on-call engineer, I want every client error class to produce both a counter and a log line, so that a reason code can be traced to a concrete event without flooding the logs.

#### Acceptance Criteria

1. THE Publisher SHALL expose exactly one counter series for each client error class, such that the set of counter series is in one-to-one correspondence with the set of client error classes.
2. WHEN a client error of a given class occurs and that class has emitted fewer than its rate limit within the current window, THE Publisher SHALL emit exactly one Structured_Event_Log line for that error class. _(L14d-5)_
3. WHILE the same client error class occurs repeatedly, THE Publisher SHALL Rate_Limit the Structured_Event_Log to at most a configured maximum number of lines per fixed time window per error class, so that emitted line volume per class per window never exceeds that configured maximum regardless of event rate.
4. IF a client error occurs for a class that has reached its rate limit within the current window, THEN THE Publisher SHALL increment that class's counter series and suppress emission of an additional Structured_Event_Log line for that event.
5. THE Structured_Event_Log line SHALL carry the originating error class identifier and either the Admission_Reason or the Posture_Code for that error.
6. THE Structured_Event_Log line SHALL contain no raw request content and no secret value, such that no byte sequence from the originating request body or any secret appears in the emitted line.

### Requirement 10: /readyz fails when the metrics directory is missing (R2-11)

**User Story:** As a deployment, I want readiness to fail if metrics are not initialized, so that a worker serving blind to its own metrics is never routed traffic.

#### Acceptance Criteria

1. IF the Cardinality_Directory has not been initialized or is not available, THEN THE readiness probe SHALL report not ready and SHALL return a reason value that names the metrics directory as the unavailable dependency.
2. WHILE the Cardinality_Directory is available AND every other required state dependency reported by `state_ready(view)` returns ready, THE readiness probe SHALL report ready with no unavailable-dependency reason.
3. WHEN the readiness probe reports not ready because the metrics directory is unavailable, THE edge serving skin SHALL respond to the `/readyz` request with HTTP status 503 and a response body that names the metrics directory as the unavailable dependency.
4. IF both the metrics directory and at least one `state_ready(view)` state dependency are unavailable, THEN THE readiness probe SHALL report not ready and SHALL return a single reason value naming exactly one unavailable dependency, evaluating the existing `state_ready(view)` state dependency gate before the metrics directory gate.
5. THE readiness probe SHALL evaluate both the existing `state_ready(view)` state dependency gate and the metrics directory gate on every readiness evaluation, extending rather than replacing `state_ready(view)`, and SHALL report ready only when both gates return ready.

### Requirement 11: Native typed exposition for the autoscaler (R2-11 M4 / L14d-4)

**User Story:** As the GKE HPA, I want gauge metrics typed correctly, so that I can resolve a scaling metric instead of receiving an untyped `unknown`.

#### Acceptance Criteria

1. WHEN the Publisher exposes a metric family, THE Publisher SHALL emit exactly one `# TYPE` line for that family whose declared type is `counter` for every family whose name ends in the `_total` suffix and `gauge` for every non-histogram family whose name does not end in `_total`, such that no family is exposed with the type `unknown` or with the `# TYPE` line absent.
2. WHEN an autoscaler requests a gauge family that the Publisher exposes, THE Publisher SHALL return that family with a `# TYPE` line declaring type `gauge`, such that the autoscaler resolves the family without receiving an untyped or `unknown` response. _(L14d-4)_
3. THE Publisher SHALL expose the windowed-busy gauge family, built as a generic mechanism accepting externally supplied samples for later GPU use, with a `# TYPE` line declaring type `gauge`. _(windowed-busy mechanism in scope; GPU producer out of scope)_
4. IF no sample has been supplied to the windowed-busy gauge family, THEN THE Publisher SHALL still emit that family's `# TYPE gauge` line without fabricating a GPU data source or emitting a synthesized sample value. _(windowed-busy mechanism in scope; GPU producer out of scope)_

### Requirement 12: Tenant ids at scale (R2-10 / L14d-2)

**User Story:** As a platform operator, I want 2,000 and 20,000 UUID-named tenants served without a single metrics-induced 500, so that tenant growth never degrades availability.

#### Acceptance Criteria

1. WHILE exactly 2,000 UUID-named tenants are observed at the default slot capacity under the local injected-clock in-process verification harness, THE Publisher SHALL return zero HTTP 500 responses attributable to metric registration across all tenant requests and all `/metrics` scrapes, measured as 0 such responses out of the full observation run. _(L14d-2)_
2. WHILE exactly 20,000 UUID-named tenants are observed at a raised slot capacity under the local injected-clock in-process verification harness, THE Publisher SHALL return zero HTTP 500 responses attributable to metric registration across all tenant requests and all `/metrics` scrapes, measured as 0 such responses out of the full observation run. _(L14d-2)_
3. WHEN the count of distinct observed tenants exceeds the Cardinality_Directory slot capacity, THE Publisher SHALL serve the request without an HTTP 500 and SHALL increment the Overflow_Counter by exactly one for each dropped per-tenant observation. _(L14d-2)_
4. IF a per-tenant observation is dropped due to Cardinality_Directory capacity exhaustion, THEN THE Publisher SHALL exclude that observation from all published per-tenant series while retaining all previously registered tenant series unchanged. _(L14d-2)_

### Requirement 13: Restart safety (R2-11 / L14d-3)

**User Story:** As an operator, I want no negative or empty window after a restart, so that an owner or worker SIGKILL does not corrupt the SLO view.

#### Acceptance Criteria

1. WHEN an owner or worker process is terminated by SIGKILL and subsequently restarted, THE Publisher SHALL publish a value greater than or equal to zero for every windowed series and every rate series, and SHALL NOT publish any value less than zero. _(L14d-3)_
2. IF a counter's observed value after a restart is less than its value before the restart, THEN THE Publisher SHALL treat the decrease as a reset per Requirement 5 and SHALL omit the affected rate for that interval. _(L14d-3)_
3. IF no prior baseline exists for a counter after a restart, THEN THE Publisher SHALL omit the affected rate for that interval per Requirement 5 rather than publish a computed rate. _(L14d-3)_
4. WHEN the Windowed_Histogram contains zero samples in the current window, THE Publisher SHALL report each of its quantiles as the defined empty-window value and SHALL NOT publish any quantile value less than zero. _(L14d-3)_

### Requirement 14: Reason and class coverage (L14d-5)

**User Story:** As an on-call engineer, I want every client error class to map to both a counter and a log line, so that no reason code is silent.

#### Acceptance Criteria

1. THE Publisher SHALL provide, for every member of the Admission_Reason vocabulary and every Posture_Code, both a counter series and a Structured_Event_Log line class, in one-to-one correspondence. _(L14d-5)_
2. THE Publisher SHALL expose the counter for each Admission_Reason and each Posture_Code as a series distinct from, and never conflated with, the Policy_BLOCK series. _(L14d-5)_
3. IF a client error occurs whose class has no mapped counter and log-line, THEN THE Publisher SHALL surface a coverage-gap error indication rather than silently drop the error. _(L14d-5)_

### Requirement 15: Exposition count is bounded by construction (R2-10)

**User Story:** As a reviewer, I want the exposed series count to be a fixed expression, so that a tenant-derived series fails a gate instead of shipping.

#### Acceptance Criteria

1. THE Publisher SHALL expose exactly the sum of the six producers' SERIES_COUNT values plus the bounded Top_K_Directory series set (at most K plus one) plus the Overflow_Counter.
2. THE Publisher SHALL define its total series count as a fixed expression and SHALL assert that the fixed expression equals the number of families actually rendered by `exposition()`.
3. WHEN the observed tenant count changes, THE Publisher SHALL render the same fixed number of families, such that the rendered family count is invariant to the tenant count.
4. THE Publisher SHALL pass the tenant-scale AST gate so that no exposition path performs work proportional to the tenant count.

## Correctness Properties (for property-based testing)

Each property is a seeded ≥10,000-iteration property test in the house idiom (no `hypothesis`).

1. **No request-path raise on overflow (R2-10 / Req 2).** For any sequence of per-tenant
   observations of any length against a directory of any derived capacity, no Request_Path_Observation
   raises; every observation beyond capacity increments the Overflow_Counter by exactly one and the
   directory size never exceeds its derived capacity.

2. **Exposition is bounded and fully typed (R2-10 / Req 1, 3, 15).** For any observed tenant set,
   `exposition()` emits exactly `SERIES_COUNT_SUM + bounded_tenant_series + 1` families and emits a
   `# TYPE` line for every family; the per-tenant plus `other` series count never exceeds K + 1.

3. **Top-K preserves the largest magnitudes (Req 3).** For any multiset of per-tenant magnitudes,
   the K individual series retained are exactly the K largest magnitudes and the `other` series equals
   the sum of all remaining magnitudes (conservation: sum of exposed per-tenant values plus `other`
   equals the total observed).

4. **Windowed p99 tracks the injected window (R2-11 / Req 4).** For any injected sample window, the
   Windowed_Histogram p99 is within tolerance of the quantile computed directly over the same window's
   samples, and samples outside the window never influence the reported quantile.

5. **Reset-safe rate omits rather than publishes a negative (R2-11 / Req 5).** For any pair of
   counter samples, the Publisher either emits a non-negative rate or omits the series; it never emits
   a negative rate, and when the later sample is smaller than the earlier it omits.

6. **Every posture code and admission reason has its own counter distinct from policy BLOCK
   (R2-11 / Req 6, 7, 14).** For any sequence of posture refusals, admission rejections and policy
   blocks, each Posture_Code and each Admission_Reason has its own counter whose value equals the count
   of its events, and the Policy_BLOCK series equals the policy-block count and is incremented by no
   posture or admission event.

7. **Real zeros never absent (Req 3, 6, 7, 8).** For a freshly constructed Publisher with no
   observations, every bounded series (the `other` bucket, every `reason` bucket, every Posture_Code
   counter, the held-tokens histogram count) is present and equal to zero in the first exposition.

8. **Round-trip of the exposition text (parser safety).** For any Publisher state, parsing the
   rendered exposition back into `(family, type, value)` tuples and re-rendering produces byte-identical
   output, and every `# TYPE` line's declared type matches the suffix rule (`_total` ⇒ counter, else
   gauge). _(A pretty-printer/parser round-trip, included because the exposition format is a serializer.)_

9. **Directory capacity derives from the contract, never a literal (Req 2, 15).** For any
   `ResourceContract`, the derived K and directory capacity are a function of the contract's slot counts
   alone; no capacity-position literal appears outside `runtime/resources.py` (asserted by the
   capacity-literals AST gate).

## Verification

- Offline `pytest` suite (the local half), including the ≥10,000-iteration property tests above.
- `mypy --strict`, `ruff`, `import-linter` (both contracts kept; the Publisher respects the
  Layer_Contract and reads `admit`/`audit`/`runtime`/`state_control` producers from a permitted
  position — likely `edge` or a dedicated top layer).
- AST gates: capacity-literals, no-module-mutable, frozen-dataclasses, http-outside-edge-resolve,
  tenant-scale.
- L14d-1..5 are certified by their offline / injected-clock equivalents in this local half; the
  cloud halves (live HPA ScalingActive, 20,000-tenant live scale, real synthetic-client/provider
  canary) are deferred and declared OPEN.
