# Requirements Document

## Introduction

This spec covers card **B6 = R2-21 "load harness v2.3"** — the open-loop Poisson load and
measurement instrument for the `gateway_v2` serving path, built to run **locally** against a single
local gateway so that the eventual fleet-scale capacity run is a pure measurement rather than a
debugging exercise.

The authority for this card is the GW20 / GW20b amendment in
`docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`. GW20b (binding) states that fleet-scale knees
are signed **only** from 3/3 repeats with **Poisson arrivals** using **harness v2.3 or later** with
these strata: multi-key (≥ 2,000 active keys over ≥ 200 orgs), long prompts (the signed share of
4–7k-token prompts), SDK-retry clients, secret-shaped outputs (with GW12b), and tenant count ≥ 10k
(with GW05c). Constant-arrival limits are published only as upper bounds. GPU-busy (windowed), owner
queue wait, loop lag, and the reason mix are reported per run. R2-01 is the measured fact that forces
Poisson: with random arrivals one L4 behind a split gateway passes at 100 RPS (17.4 ms) and fails at
124 RPS — about half the 200 RPS constant-arrival limit — so capacity must be sized on Poisson, with
a ladder in steps of ≤ 10% and three repeats at the pass point. The R2-21 register row records that
the ready harness could not generate multi-key, long-prompt, retrying, TLS, or secret-shaped outputs;
harness v2.3 adds those strata (harness floors measured at 0.29–0.34 ms, direct-path p99 ≤ 0.38 ms,
0 drops), while the **TLS client and some output kinds are explicitly "not built"**.

The local worklist (`docs/LOCAL_WORKLIST_v3.md`, block "B6") frames the **local half**: build the
harness code, the arrival generator, the strata generators, the client-side C4 collector, the ladder
runner, and the report format; then run the **full Poisson ladder** against one local gateway (B1's
`build_app`, hosted by B3's uvicorn entrypoint) plus B4's fake guard / fake upstream, proving the
harness produces correct arrival distributions, correct strata, and correct C4 numbers. The
fleet-scale knee **numbers** themselves (R2-01), autoscaling (G-01), retry amplification at fleet
scale (G-06), the integrated re-measure (G-09), the real GPU-busy data source, and the TLS client
(the G-10 edge-TLS gate) are **deferred cloud gates** and are explicitly out of scope for this spec.

The harness must honor the same house conventions as B1–B5: it sits **outside** the `gateway_v2`
import-linter serving layers (the design must resolve its placement as its own top-level package, the
way `state_control` and `audit_control` already sit outside `root_packages=["gateway_v2"]`), carries
**no capacity-position literals** outside `gateway_v2/runtime/resources.py`, uses frozen + slots value
types, and keeps `mypy --strict`, `ruff`, the six AST gates, and `unscanned_channels() == ()` green.
It is **open-loop and deterministic** under an injected clock and an injected `random.Random` seed so
the arrival distribution and the strata are reproducible and testable offline without a running
server, using the house test idiom (seeded `random.Random`, ≥ 10,000-iteration property tests, no
hypothesis; `asyncio.run` for async).

## Glossary

- **Load_Harness**: the open-loop load generation and measurement instrument built by this card
  (harness v2.3), runnable locally against one gateway.
- **Open_Loop**: an arrival model in which request start times are scheduled in advance from the
  arrival process and are NOT delayed by how fast the server responds; the generator never slows its
  offered rate because the server slowed.
- **Closed_Loop**: the forbidden alternative in which the client waits for a response before issuing
  the next request, so a slowing server hides its own overload by throttling the offered rate.
- **Poisson_Arrivals**: inter-arrival times drawn from an exponential distribution, producing random
  (bursty) arrivals; the core arrival mode for capacity sizing (R2-01).
- **Constant_Arrivals**: fixed inter-arrival times; a secondary mode whose published limits are only
  upper bounds.
- **Schedule_Drop**: a scheduled request whose actual dispatch slipped past its scheduled start time
  by more than a declared tolerance because the generator could not keep up — evidence the generator,
  not the server, is the bottleneck.
- **Loadgen_Headroom**: the proven margin by which the generator can out-pace the offered rate, used
  to certify the generator is not the bottleneck (gate LGW20-5).
- **Stratum / Strata**: a dimension of realistic traffic shape the harness can mix in — multi-key,
  long-prompt corpora, retry clients, secret-shaped outputs, tenant scale.
- **C4**: the firewall-added client-side latency metric — the worst (maximum) inter-chunk latency per
  stream, measured client-side, with 100% per-chunk sampling, **holdback excluded**, and errored
  streams counted as `+∞`.
- **Firewall_Added_Delta**: the client-side C4 p99 difference between a through-firewall path and a
  Direct_Path baseline, as defined by the shipped `gateway_v2/runtime/latency_slo.py` (B5); the
  harness C4 collector must be consistent with this definition rather than duplicate it.
- **SLO_Bound**: the request-path p99 bound read from the one capacity authority,
  `ResourceContract.target_p99_ms`; a run passes only if C4 p99 is below it.
- **Rate_Ladder**: an ascending sequence of offered rates in steps of ≤ 10%, used to find the knee.
- **Highest_Repeatable_Pass**: the greatest offered rate that passes on 3/3 repeats.
- **First_Repeatable_Fail**: the lowest offered rate that fails on 3/3 repeats; the reported failing
  rate is never a rate that passed even once.
- **Reason_Mix**: the per-run distribution of outcome reasons (admitted / each shed-or-error reason
  code) for the offered requests.
- **Deferred_Cloud_Gate**: a measurement or capability that is explicitly out of scope here because it
  requires cloud fleet resources (R2-01 numbers, G-01, G-06, G-09, the real GPU source, the TLS
  client / G-10).

## Requirements

### Requirement 1: Open-loop Poisson arrival generation with scheduled-time accounting

**User Story:** As a capacity engineer, I want the harness to generate open-loop Poisson arrivals with
scheduled-time accounting, so that offered load is independent of server speed and a slowing server
cannot hide overload by throttling my offered rate.

#### Acceptance Criteria

1. THE Load_Harness SHALL generate request start times open-loop, scheduling each arrival from the
   arrival process independently of when prior requests complete.
2. WHERE the arrival mode is Poisson, THE Load_Harness SHALL draw inter-arrival times from an
   exponential distribution parameterized by the configured offered rate.
3. WHEN a scheduled request is dispatched later than its scheduled start time by more than a declared
   tolerance, THE Load_Harness SHALL record the request as a Schedule_Drop.
4. THE Load_Harness SHALL report the Schedule_Drop count for every run.
5. THE Load_Harness SHALL dispatch each scheduled request without waiting for any prior request's
   response to complete, so the offered rate is never reduced by server response latency.
6. WHERE the arrival mode is Constant_Arrivals, THE Load_Harness SHALL generate arrivals at fixed
   inter-arrival times and label the resulting limits as upper bounds in the run report.

### Requirement 2: Generator headroom self-proof

**User Story:** As a capacity engineer, I want the harness to prove it is not the bottleneck, so that a
measured knee reflects the server and not the generator.

#### Acceptance Criteria

1. THE Load_Harness SHALL measure Loadgen_Headroom as the margin between the rate the generator can
   issue and the configured offered rate.
2. WHEN Loadgen_Headroom falls below a declared minimum margin for a run, THE Load_Harness SHALL mark
   that run's result as generator-bound and exclude it from pass/fail knee determination.
3. THE Load_Harness SHALL report Loadgen_Headroom for every run as part of the generator self-proof
   (gate LGW20-5).
4. IF a run records any Schedule_Drop, THEN THE Load_Harness SHALL treat the run as not a clean
   measurement of the server and surface the Schedule_Drop count in the run report.

### Requirement 3: Multi-key stratum

**User Story:** As a capacity engineer, I want the harness to drive many distinct API keys across many
orgs, so that capacity is measured under realistic key cardinality rather than a single hot key.

#### Acceptance Criteria

1. WHERE the multi-key stratum is enabled, THE Load_Harness SHALL distribute offered requests across a
   configured population of at least 2,000 active keys spanning at least 200 orgs.
2. THE Load_Harness SHALL support a configured key population of up to 50,000 keys.
3. WHEN the multi-key stratum generates a request, THE Load_Harness SHALL assign it a key drawn from
   the configured population according to the configured distribution.
4. THE Load_Harness SHALL report the count of distinct keys and distinct orgs actually exercised in a
   run.

### Requirement 4: Long-prompt corpus stratum

**User Story:** As a capacity engineer, I want the harness to mix in long prompts at configured shares,
so that capacity is measured against the long/short prompt bias that drives the knee.

#### Acceptance Criteria

1. WHERE the long-prompt stratum is enabled, THE Load_Harness SHALL mix 4,000–7,000-token prompts into
   the offered traffic at a configured share of 1%, 5%, or 25%.
2. WHEN the long-prompt share is configured, THE Load_Harness SHALL produce an actual long-prompt share
   that matches the configured share within a declared tolerance over a run.
3. THE Load_Harness SHALL report, per run, the realized long-prompt share and separate C4 statistics
   for long prompts versus prompts of at most 1,024 tokens.

### Requirement 5: SDK-retry client stratum

**User Story:** As a capacity engineer, I want the harness to model SDK retry behavior including an
aggressive-retry mode, so that retry amplification under overload is represented in offered load.

#### Acceptance Criteria

1. WHERE the retry stratum is enabled, THE Load_Harness SHALL model a configured share of clients that
   retry failed requests following SDK retry semantics.
2. WHEN a modeled client receives a shed response carrying `x-should-retry: false`, THE Load_Harness
   SHALL NOT re-issue that request (honoring R2-08 x-should-retry semantics).
3. WHERE aggressive-retry mode is enabled, THE Load_Harness SHALL apply the configured aggressive-retry
   share in addition to the base retry share.
4. THE Load_Harness SHALL report the ratio of total offered requests to original offered requests so
   retry amplification is visible per run.

### Requirement 6: Secret-shaped output stratum

**User Story:** As a capacity engineer, I want the harness to request outputs shaped like secrets, so
that the GW12b holdback path is exercised during a capacity run.

#### Acceptance Criteria

1. WHERE the secret-shaped-output stratum is enabled, THE Load_Harness SHALL cause the driven endpoint
   to emit outputs whose shapes trigger the GW12b holdback path at a configured share.
2. THE Load_Harness SHALL report the realized share of streams that produced secret-shaped output in a
   run.

### Requirement 7: Tenant-scale stratum

**User Story:** As a capacity engineer, I want the harness to spread traffic over a large tenant
population, so that capacity is measured under the tenant cardinality GW05c targets.

#### Acceptance Criteria

1. WHERE the tenant-scale stratum is enabled, THE Load_Harness SHALL distribute offered requests across
   a configured tenant population of at least 10,000 tenants.
2. WHEN the tenant-scale stratum generates a request, THE Load_Harness SHALL assign it a tenant drawn
   from the configured population according to the configured distribution.
3. THE Load_Harness SHALL report the count of distinct tenants actually exercised in a run.

### Requirement 8: Client-side C4 collector

**User Story:** As a capacity engineer, I want a client-side C4 collector that captures the worst chunk
per stream with holdback excluded and errors as `+∞`, so that the firewall-added latency is measured
the way GW20 certifies it.

#### Acceptance Criteria

1. WHEN a stream completes, THE Load_Harness SHALL record its C4 as the maximum per-chunk client-side
   latency observed for that stream.
2. THE Load_Harness SHALL sample 100% of chunks per stream when computing C4.
3. THE Load_Harness SHALL exclude holdback-attributed latency from each stream's C4.
4. IF a stream errors, THEN THE Load_Harness SHALL record that stream's C4 as positive infinity.
5. THE Load_Harness SHALL compute the C4 p99 over all offered requests, including requests recorded as
   positive infinity.

### Requirement 9: Reconciliation with the shipped Firewall_Added_Delta definition

**User Story:** As a maintainer, I want the harness C4 definition reconciled with the shipped
`runtime/latency_slo.py`, so that the harness and B5 agree on what the firewall-added latency is
rather than defining it twice.

#### Acceptance Criteria

1. WHERE a Direct_Path baseline is measured, THE Load_Harness SHALL compute the Firewall_Added_Delta
   using the shipped `gateway_v2/runtime/latency_slo.py` definition rather than a duplicate
   computation.
2. THE Load_Harness SHALL apply the same lost-segment-tail exclusion semantics as
   `gateway_v2/runtime/latency_slo.py` when reconciling its C4 samples with the Firewall_Added_Delta.
3. THE Load_Harness SHALL read the pass bound from `ResourceContract.target_p99_ms` and SHALL hold no
   SLO or capacity-position literal of its own.

### Requirement 10: Rate ladder runner

**User Story:** As a capacity engineer, I want a ladder runner that climbs offered rate in small steps
with repeats at the pass point, so that the reported knee is repeatable.

#### Acceptance Criteria

1. THE Load_Harness SHALL advance the offered rate along a Rate_Ladder in steps of at most 10%.
2. WHEN the ladder reaches a candidate pass rate, THE Load_Harness SHALL run that rate three times.
3. THE Load_Harness SHALL classify a single run as passing only when its C4 p99 is below the SLO_Bound,
   its infrastructure-error share is within the declared low bound, and its Schedule_Drop count is
   zero.
4. THE Load_Harness SHALL report the Highest_Repeatable_Pass as the greatest offered rate that passes
   on three of three repeats.
5. THE Load_Harness SHALL report the First_Repeatable_Fail as the lowest offered rate that fails on
   three of three repeats, and SHALL NOT report as a failing rate any rate that passed on at least one
   repeat.

### Requirement 11: Per-run report fields

**User Story:** As a capacity engineer, I want each run's report to carry the diagnostic fields GW20b
names, so that a knee can be interpreted and signed.

#### Acceptance Criteria

1. THE Load_Harness SHALL report, per run, the C4 p99 and the pass/fail outcome against the SLO_Bound.
2. THE Load_Harness SHALL report, per run, the owner queue wait.
3. THE Load_Harness SHALL report, per run, the event-loop lag.
4. THE Load_Harness SHALL report, per run, the Reason_Mix of outcomes over the offered requests.
5. THE Load_Harness SHALL report, per run, a windowed GPU-busy field whose value SHALL be populated
   from a configured source.
6. WHERE no GPU-busy data source is configured, THE Load_Harness SHALL report the windowed GPU-busy
   field as unavailable rather than fabricating a value.

### Requirement 12: Local dry-run against B1 + B3 + B4 fake guard

**User Story:** As a capacity engineer, I want to run the full Poisson ladder against one local gateway
with a fake guard, so that I de-risk the cloud one-shot by measuring correct distributions, strata, and
C4 numbers locally.

#### Acceptance Criteria

1. THE Load_Harness SHALL drive a single local gateway built by B1's `build_app` and hosted by B3's
   uvicorn entrypoint, backed by B4's fake guard and fake upstream.
2. THE Load_Harness SHALL run the full Poisson Rate_Ladder end to end against that local gateway.
3. WHEN the local dry-run executes, THE Load_Harness SHALL verify that the realized arrival
   distribution matches the configured Poisson process within a declared tolerance.
4. WHEN the local dry-run executes, THE Load_Harness SHALL verify that the realized strata shares match
   their configured shares within declared tolerances.
5. WHEN the local dry-run executes, THE Load_Harness SHALL produce C4 numbers consistent with the
   client-side C4 definition in Requirement 8.

### Requirement 13: Determinism under injected clock and seed

**User Story:** As a maintainer, I want the arrival process and strata to be deterministic under an
injected clock and `random.Random` seed, so that distributions and strata are reproducible and testable
offline without a running server.

#### Acceptance Criteria

1. THE Load_Harness SHALL accept an injected clock and an injected `random.Random` seed for arrival
   generation and strata generation.
2. WHEN the harness is run twice with the same injected clock and the same seed, THE Load_Harness SHALL
   produce an identical arrival schedule and identical strata assignments.
3. THE Load_Harness SHALL generate the arrival schedule and strata assignments without requiring a
   running server, so they can be verified offline.

### Requirement 14: House conventions and placement

**User Story:** As a maintainer, I want the harness to honor the gateway_v2 house conventions, so that
it does not weaken the serving tree's guarantees.

#### Acceptance Criteria

1. THE Load_Harness SHALL be placed outside the `gateway_v2` import-linter serving layers, and the
   design SHALL resolve its exact placement (its own top-level package, as `state_control` and
   `audit_control` sit outside `root_packages`) without violating the layer contract or the AST gates.
2. THE Load_Harness SHALL hold no capacity-position literal outside `gateway_v2/runtime/resources.py`.
3. THE Load_Harness SHALL define its value types as frozen dataclasses with slots.
4. THE Load_Harness SHALL keep `mypy --strict`, `ruff`, the six AST gates, and
   `unscanned_channels() == ()` green.
5. THE Load_Harness SHALL be covered by property tests using a seeded `random.Random` with at least
   10,000 iterations and no hypothesis, driving async code through `asyncio.run`.

### Requirement 15: Deferred cloud gates (out of scope)

**User Story:** As the orchestrator, I want the cloud-only measurements named as out of scope, so that
the local half is not blocked on fleet resources and the category boundary stays clean.

#### Acceptance Criteria

1. THE Load_Harness SHALL treat the fleet-scale knee numbers (R2-01), autoscaling step tests (G-01),
   retry amplification at fleet scale (G-06), and the integrated re-measure (G-09) as deferred cloud
   gates that this spec does not deliver.
2. THE Load_Harness SHALL define and position the windowed GPU-busy field while treating the real
   GPU-busy data source as a deferred cloud gate.
3. THE Load_Harness SHALL defer the TLS client (the G-10 edge-TLS gate) and SHALL drive the local
   gateway over the non-TLS path, consistent with the R2-21 register row that the TLS client is not
   built.
