# Implementation Plan: load harness v2.3 — open-loop Poisson load + measurement instrument (B6 / R2-21, local half)

## Overview

Convert the B6 / R2-21 design into a series of code-generation steps that stand up the **local half**
of the open-loop Poisson load-and-measurement instrument ("harness v2.3") as a new top-level package
`gateway_v2/load_harness/` (sibling to `state_control/` and `audit_control/`, outside the
`gateway_v2` import-linter serving layers), **bottom-up** so each piece is gate-checkable before the
next builds on it — the same shape as the shipped B1–B5 cards (packaging + skeleton first; pure value
types + generators before the async driver; source before its tests; property/unit/integration
tests; checkpoint Gates; a final full-gate checkpoint):

1. **Packaging + skeleton** — the `pyproject.toml` edits (add `load_harness` to setuptools include,
   ruff `src`, isort known-first-party, mypy `files`; `root_packages` UNCHANGED so the harness stays
   outside the layer contract) + the `load_harness/` package tree with `__init__.py`, so
   mypy/ruff/packaging pick it up from the first commit.
2. **Pure value types + config** (`config.py`) — the frozen+slots run-spec value types and the named
   operational constants (schedule-drop tolerance, ladder step cap, headroom margin, repeat count,
   strata tolerances), **no capacity-position literal** (pass bound read later from
   `ResourceContract.target_p99_ms`).
3. **Open-loop arrival generator** (`arrivals.py`) — Poisson (inverse-CDF exponential) + constant,
   from an injected clock + injected `random.Random`, computing the full schedule up front
   (offline-deterministic).
4. **The five strata** (`strata/keys.py`, `strata/prompts.py`, `strata/retry.py`,
   `strata/secret_output.py`, `strata/tenants.py`) + the `request_plan.py` composer — each a pure
   function of `(population/config, random.Random, arrival)`, offline-deterministic.
5. **The C4 collector** (`c4.py`) — worst inter-chunk latency per stream, 100% sampling, holdback
   excluded, errors `+∞`, **delegating** p99 / tail-exclusion / firewall-added-delta to the shipped
   `gateway_v2/runtime/latency_slo.py` (B5) rather than duplicating it.
6. **The ladder + report** (`ladder.py`, `report.py`) — ≤ 10% steps, 3 repeats,
   highest-repeatable-pass / first-repeatable-fail, and the GW20b per-run report fields
   (`GpuBusy.UNAVAILABLE` sentinel, never fabricated).
7. **The async driver** (`driver.py`) — fires each planned request at its scheduled time against the
   booted gateway over HTTP without waiting on prior responses, records schedule drops + loadgen
   headroom, feeds the C4 collector; plus the `__main__.py` entrypoint.
8. **The local dry-run** (`dryrun.py` + integration test) — boots B1's `build_app` via B3's
   `serve.py` (`importorskip('uvicorn')`) OR attaches via `httpx.ASGITransport`
   (`importorskip('httpx')`) with a scripted `StubProviderClient` + B4's fake guard, over the
   non-TLS path, running the full Poisson ladder and asserting arrival distribution / strata shares /
   C4 numbers against tolerances.
9. **Docs + deferred-gate recording** and a **FINAL gate** mapping evidence to the card format.

Implementation language: **Python** — the design is grounded in concrete `gateway_v2` modules
(`runtime/latency_slo.py`, `runtime/resources.py`, `edge/serve.py`, `build_app`) and the shipped
house idiom; the design uses Python throughout, so no implementation-language question is needed.

### Conventions enforced in EVERY coding task

- **Property tests** use the house idiom: a seeded `random.Random` driven for **≥ 10,000 iterations**,
  **NO hypothesis**; async properties driven through `asyncio.run`. Each property test is tagged
  `# Feature: load-harness-r2-21, Property N: <text>` and `# Validates: Requirements X.Y`, with
  exactly one design property ↔ one property-based test. The ten design properties (Properties 1–10)
  map onto ten specific property-test sub-tasks placed next to the code they validate.
- **Value types** are frozen slotted dataclasses — every harness value type is
  `@dataclass(frozen=True, slots=True)` (`check_frozen_dataclasses`); no module-level mutable
  (`check_no_module_mutable`), registries built inside functions/`__init__`.
- **No capacity-position literal outside `gateway_v2/runtime/resources.py`**
  (`check_capacity_literals`): the pass bound is read from `ResourceContract.target_p99_ms`, the
  lost-segment tail comes from `gateway_v2.runtime.latency_slo` (never re-declared). The harness's own
  operational constants (schedule-drop tolerance, ladder step cap, headroom margin, repeat count,
  strata tolerances) are **named module constants with a documented rationale**, the same category as
  `latency_slo._LOST_SEGMENT_TAIL_MS` and `serving_config`'s port/drain defaults.
- **No HTTP type outside the driver boundary** (`check_http_outside_edge_resolve`): the harness speaks
  HTTP via a client (`httpx`) at the `driver.py` boundary only, never a gateway HTTP object; it reaches
  the gateway over a socket / ASGI transport, not by importing `edge` internals.
- **Modules ≤ 800 lines, functions ≤ 120 lines** (`check_sizes`); `check_tenant_scale` /
  `check_secret_headers` untouched; `unscanned_channels() == ()` unchanged (the harness adds no channel).
- **One allowed import edge**: the harness imports *downward* into `gateway_v2.runtime.latency_slo`
  and `gateway_v2.runtime.resources.ResourceContract` only (as `state_control` imports
  `gateway_v2.domain.state_knobs`); `gateway_v2` never imports `load_harness`, so `lint-imports` stays
  **contracts kept** with no new serving-path edge.
- Each task references its requirement sub-clauses and the design component/section/property it
  satisfies (traceability), matching the B1–B5 `tasks.md` style; every code task is followed by its
  test/gate note.

### The gate (run from cwd `gateway_v2` using the `gateway_v2/.venv` venv)

All gate commands run from cwd `gateway_v2` with the `gateway_v2/.venv` venv (system python lacks
redis). The full gate is:

- `.venv/bin/python -m pytest`
- `.venv/bin/mypy --strict`
- `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`
- `.venv/bin/lint-imports`
- the 6 AST gates: `.venv/bin/python -m lint.check_capacity_literals gateway_v2`,
  `.venv/bin/python -m lint.check_no_module_mutable gateway_v2`,
  `.venv/bin/python -m lint.check_frozen_dataclasses gateway_v2`,
  `.venv/bin/python -m lint.check_http_outside_edge_resolve gateway_v2`,
  `.venv/bin/python -m lint.check_sizes gateway_v2`,
  `.venv/bin/python -m lint.check_tenant_scale gateway_v2`
- the unscanned-channels check (`unscanned_channels() == ()`)

(The AST gates run over `gateway_v2` only, but the harness keeps the same discipline; mypy/ruff cover
`load_harness` via the `pyproject.toml` edits in Task 1.)

## Tasks

- [x] 1. Packaging + `load_harness/` package skeleton (built first so mypy/ruff/packaging see it)
  - [x] 1.1 Edit `pyproject.toml` + create the package skeleton
    - Edit `gateway_v2/pyproject.toml`: add `"load_harness*"` to `[tool.setuptools.packages.find].include`,
      add `"load_harness"` to `[tool.ruff].src`, add `"load_harness"` to
      `[tool.ruff.lint.isort].known-first-party`, add `"load_harness"` to `[tool.mypy].files`; leave
      `[tool.importlinter].root_packages = ["gateway_v2"]` UNCHANGED so the harness sits outside the
      layer contract
    - Create the package tree `gateway_v2/load_harness/__init__.py` (+ `gateway_v2/load_harness/strata/__init__.py`)
      with a module docstring naming the card (B6 / R2-21) and the one allowed import edge
      (`gateway_v2.runtime.latency_slo` + `ResourceContract` downward only); no module-level mutable,
      no re-exported capacity literal
    - _Design: Architecture → Placement, "Exact pyproject.toml / tooling edits", Gate posture_
    - _Requirements: 14.1, 14.2, 14.4_

- [x] 2. Pure run-spec config + named operational constants — `config.py`
  - [x] 2.1 Implement the frozen run-spec value types + operational constants
    - Create `gateway_v2/load_harness/config.py` with frozen+slots `RunSpec`, `StrataConfig`,
      `LadderConfig`, `Tolerances` carrying offered rate, arrival mode (`POISSON` | `CONSTANT`),
      enabled strata + their configured shares, tolerances, and the injected `random.Random` seed; a
      parse/validate from args/env (warn-and-default idiom, mirroring `serving_config.py`)
    - Declare the harness operational constants as NAMED module constants with docstrings citing their
      rationale — `_SCHEDULE_DROP_TOLERANCE_S`, `_MAX_LADDER_STEP_FRACTION` (≤ 0.10),
      `_MIN_HEADROOM_MARGIN`, `_LADDER_REPEAT_COUNT` (3), `_MAX_INFRA_ERROR_SHARE`, and the strata
      tolerances — classified operational/statistical, NOT capacity-position literals; the pass bound
      is NOT held here (read later from `ResourceContract.target_p99_ms`)
    - No module-level mutable; all value types frozen+slots; module ≤ 800 lines / functions ≤ 120
    - _Design: Module map (config.py), Data Models, Gate posture (capacity-literal handling)_
    - _Requirements: 1.6, 10.1, 14.2, 14.3_

- [x] 3. Open-loop arrival generator — `arrivals.py`
  - [x] 3.1 Implement the Poisson + constant arrival schedule under injected clock + rng
    - Create `gateway_v2/load_harness/arrivals.py` with frozen+slots `Arrival(index, scheduled_s)`
      and `ArrivalSchedule(mode, offered_rate_per_s, arrivals)`; an `ArrivalMode` enum
      (`POISSON` | `CONSTANT`)
    - Poisson inter-arrivals via inverse-CDF `-ln(1 - rng.random()) / offered_rate_per_s`, accumulated;
      constant inter-arrivals `1 / offered_rate_per_s`; the whole schedule computed UP FRONT from
      `(rng, clock)` alone with no network/server dependency so it is generated and verified offline
    - Threads the injected `Clock = Callable[[], float]` and injected `random.Random` (never
      module-global `random.*`)
    - _Design: Determinism spine, Open-loop scheduler contract (Poisson/constant draws, up-front schedule)_
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 13.1, 13.3_

  - [x]* 3.2 Property test — Poisson arrivals match the configured process (PBT)
    - Add `gateway_v2/tests/load_harness/test_arrivals.py`: seeded `random.Random` ≥ 10,000 iters, no
      hypothesis — **Property 1: Poisson arrivals match the configured process within tolerance**:
      for any seed and offered rate, the realized mean inter-arrival time of a Poisson
      `ArrivalSchedule` is within the declared tolerance of `1/offered_rate` over a large sample
    - Tag `# Feature: load-harness-r2-21, Property 1` / `# Validates: Requirements 1.2, 12.3`
    - _Design: Correctness Properties → Property 1_
    - _Requirements: 1.2, 12.3_

- [x] 4. The five strata generators + request-plan composer
  - [x] 4.1 Multi-key stratum — `strata/keys.py`
    - Create `gateway_v2/load_harness/strata/keys.py` with frozen+slots `KeyPopulation`
      (≥ 2,000 keys over ≥ 200 orgs, configurable up to 50,000) and `KeyAssignment`; a pure
      per-request key draw from the configured distribution keyed by the injected `rng`; expose the
      distinct-keys + distinct-orgs actually exercised for the report
    - _Design: Strata interfaces (Multi-key), Module map (strata/keys.py)_
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 4.2 Long-prompt corpus stratum — `strata/prompts.py`
    - Create `gateway_v2/load_harness/strata/prompts.py` with frozen+slots `PromptCorpus` /
      `PromptPick`: mix 4,000–7,000-token prompts at a configured share of 1% / 5% / 25% against
      ≤ 1,024-token prompts; realized share within the declared tolerance; expose the realized share +
      a long/short tag so the C4 collector can split long-prompt vs ≤ 1,024-token C4 statistics
    - _Design: Strata interfaces (Long-prompt), Module map (strata/prompts.py)_
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 4.3 SDK-retry client stratum — `strata/retry.py`
    - Create `gateway_v2/load_harness/strata/retry.py` with frozen+slots `RetryPolicy` /
      `RetryOutcome`: model a configured base retry share + an aggressive-retry share following SDK
      retry semantics; a client that receives a shed response carrying `x-should-retry: false` NEVER
      re-issues it (honoring R2-08); expose the total/original offered ratio (retry amplification)
    - _Design: Strata interfaces (Retry), Module map (strata/retry.py)_
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 4.4 Secret-shaped-output stratum — `strata/secret_output.py`
    - Create `gateway_v2/load_harness/strata/secret_output.py` with frozen+slots `SecretOutputPlan`:
      mark a configured share of requests to drive the gateway's GW12b holdback path by SCRIPTING the
      injected stub's output bytes to match the holdback shape classes (the shape-matched scripted-stub
      approach — the harness arranges the emitted bytes, it does NOT prove the GW07/GW08 detector
      classifies them); record the realized share of streams that produced secret-shaped output; a
      docstring records the honest holdback-mechanism-not-detector limit (detector-driven classification
      is a deferred seam)
    - _Design: Strata interfaces (Secret-shaped output), Dry-run topology (secret-shaped-output stratum, honest limit)_
    - _Requirements: 6.1, 6.2_

  - [x] 4.5 Tenant-scale stratum — `strata/tenants.py`
    - Create `gateway_v2/load_harness/strata/tenants.py` with frozen+slots `TenantPopulation` /
      `TenantAssignment`: distribute offered requests across a configured population of ≥ 10,000
      tenants; a pure per-request tenant draw from the configured distribution keyed by the injected
      `rng`; expose the distinct tenants actually exercised for the report
    - _Design: Strata interfaces (Tenant-scale), Module map (strata/tenants.py)_
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 4.6 Request-plan composer — `request_plan.py`
    - Create `gateway_v2/load_harness/request_plan.py` with frozen+slots `PlannedRequest`: layer all
      ENABLED strata (key, tenant, prompt, retry policy, secret-output plan) onto one `Arrival`,
      deterministically given the seed; a pure function of `(RunSpec, rng, Arrival)` with no
      network/server dependency so it is offline-verifiable
    - _Design: Module map (request_plan.py), Determinism spine_
    - _Requirements: 13.1, 13.2, 13.3_

  - [x]* 4.7 Property test — strata shares converge to configured shares (PBT)
    - Add `gateway_v2/tests/load_harness/test_strata_shares.py`: seeded `random.Random` ≥ 10,000 iters,
      no hypothesis — **Property 4: Strata shares converge to configured shares**: for any seed and
      configured strata shares (long-prompt 1/5/25%, retry, secret-shaped), the realized shares over a
      run converge within the declared tolerance
    - Tag `# Feature: load-harness-r2-21, Property 4` / `# Validates: Requirements 4.2, 5.3, 6.2, 12.4`
    - _Design: Correctness Properties → Property 4_
    - _Requirements: 4.2, 5.3, 6.2, 12.4_

  - [x]* 4.8 Property test — key/org/tenant populations honor their floors (PBT)
    - Add `gateway_v2/tests/load_harness/test_strata_population.py`: seeded `random.Random` ≥ 10,000
      iters, no hypothesis — **Property 5: Key/org/tenant populations honor their floors**: for any
      enabled multi-key/tenant config, assignments draw only from the configured population and the
      distinct-count report never exceeds the population nor falls below what the distribution admits
      (≥ 2k keys / ≥ 200 orgs; ≥ 10k tenants when the offered count allows)
    - Tag `# Feature: load-harness-r2-21, Property 5` / `# Validates: Requirements 3.1, 3.3, 3.4, 7.1, 7.3`
    - _Design: Correctness Properties → Property 5_
    - _Requirements: 3.1, 3.3, 3.4, 7.1, 7.3_

  - [x]* 4.9 Property test — retry honors `x-should-retry: false` and reports amplification (PBT)
    - Add `gateway_v2/tests/load_harness/test_retry.py`: seeded `random.Random` ≥ 10,000 iters, no
      hypothesis — **Property 9: Retry honors `x-should-retry: false` and reports amplification**: for
      any configured retry/aggressive shares and shed responses, a response carrying
      `x-should-retry: false` is never re-issued and the reported total/original offered ratio equals
      the realized amplification
    - Tag `# Feature: load-harness-r2-21, Property 9` / `# Validates: Requirements 5.1, 5.2, 5.4`
    - _Design: Correctness Properties → Property 9_
    - _Requirements: 5.1, 5.2, 5.4_

  - [x]* 4.10 Property test — determinism: same seed + clock ⇒ identical schedule + strata (PBT)
    - Add `gateway_v2/tests/load_harness/test_determinism.py`: seeded `random.Random` ≥ 10,000 iters,
      no hypothesis — **Property 10: Determinism**: for any seed and injected clock, two independent
      runs of the generator produce an identical `ArrivalSchedule` and identical `PlannedRequest`
      strata assignments
    - Tag `# Feature: load-harness-r2-21, Property 10` / `# Validates: Requirements 13.1, 13.2, 13.3`
    - _Design: Correctness Properties → Property 10, Determinism spine_
    - _Requirements: 13.1, 13.2, 13.3_

- [x] 5. Gate — pure core (packaging + config + arrivals + strata + request-plan) green
  - Ensure all tests pass, ask the user if questions arise. From cwd `gateway_v2` with the
    `gateway_v2/.venv` venv run the full gate over the pure core (`config.py`, `arrivals.py`,
    `strata/*`, `request_plan.py` + their tests): `.venv/bin/python -m pytest`,
    `.venv/bin/mypy --strict`,
    `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`,
    `.venv/bin/lint-imports` (contracts kept, no new edge), the 6 AST gates
    (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
    and the unscanned-channels check (`unscanned_channels() == ()`). Fix any gate failure before building on it.

- [x] 6. Client-side C4 collector — `c4.py` (reuses `runtime/latency_slo`)
  - [x] 6.1 Implement the C4 collector delegating p99 / tail-exclusion / delta to `latency_slo`
    - Create `gateway_v2/load_harness/c4.py` with frozen+slots `ChunkArrival`, `StreamC4`
      (`worst_chunk_ms`, `errored`), and `C4Summary` (`c4_p99_ms`, `long_prompt: PathSamples`,
      `short_prompt: PathSamples`); per stream sample 100% of chunks, record the MAX non-holdback
      inter-chunk client-side latency as the stream's C4, SUBTRACT holdback-attributed latency, and
      record `+∞` for an errored stream
    - Reconcile with B5: finite per-stream C4 values become a `gateway_v2.runtime.latency_slo.PathSamples`
      and the firewall-added delta vs a Direct_Path baseline is computed by
      `latency_slo.firewall_added_delta(...)` (same `exclude_lost_segment_tails` + nearest-rank p99,
      NO duplicate computation); the `+∞` entries are a harness-level overlay included in the C4 p99
      over ALL offered streams; the pass bound is read from `ResourceContract.target_p99_ms` (harness
      holds no SLO/capacity literal); `DirectBaselineMissing` propagates
    - _Design: C4 collector + latency_slo reconciliation, Data Models (c4.py), Error Handling (DirectBaselineMissing propagates)_
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 9.1, 9.2, 9.3_

  - [x]* 6.2 Property test — C4 is the worst chunk, holdback-excluded, errors `+∞` (PBT)
    - Add `gateway_v2/tests/load_harness/test_c4.py`: seeded `random.Random` ≥ 10,000 iters, no
      hypothesis — **Property 6: C4 is the worst chunk, holdback-excluded, errors `+∞`**: for any
      stream of per-chunk arrivals with injected holdback segments, `StreamC4.worst_chunk_ms` equals
      the maximum non-holdback inter-chunk latency; for any errored stream the C4 is `+∞`; and the C4
      p99 over all offered streams includes the `+∞` entries
    - Tag `# Feature: load-harness-r2-21, Property 6` / `# Validates: Requirements 8.1, 8.3, 8.4, 8.5, 12.5`
    - _Design: Correctness Properties → Property 6_
    - _Requirements: 8.1, 8.3, 8.4, 8.5, 12.5_

  - [x]* 6.3 Property test — C4 reconciliation matches `latency_slo` (PBT)
    - Add `gateway_v2/tests/load_harness/test_c4.py` (append): seeded `random.Random` ≥ 10,000 iters,
      no hypothesis — **Property 7: C4 reconciliation matches `latency_slo`**: for any set of finite
      per-stream C4 samples and a Direct_Path baseline, the harness's firewall-added delta equals
      `latency_slo.firewall_added_delta` of the same `PathSamples` (same p99, same lost-segment-tail
      exclusion) — never a duplicate computation
    - Tag `# Feature: load-harness-r2-21, Property 7` / `# Validates: Requirements 8.2, 9.1, 9.2`
    - _Design: Correctness Properties → Property 7_
    - _Requirements: 8.2, 9.1, 9.2_

- [x] 7. Rate ladder + per-run report
  - [x] 7.1 Implement the rate ladder — `ladder.py`
    - Create `gateway_v2/load_harness/ladder.py` with frozen+slots `LadderStep` and `LadderResult`
      (`highest_repeatable_pass_rate`, `first_repeatable_fail_rate`, `steps`); advance offered rate in
      steps of at most `_MAX_LADDER_STEP_FRACTION` (≤ 0.10); run 3 repeats at a candidate pass rate; a
      single run passes iff C4 p99 < `target_p99_ms` AND infra-error share ≤ `_MAX_INFRA_ERROR_SHARE`
      AND `Schedule_Drop` count == 0; generator-bound runs are excluded (never pass or fail)
    - `Highest_Repeatable_Pass` = greatest rate passing 3/3; `First_Repeatable_Fail` = lowest rate
      failing 3/3 and NEVER a rate that passed at least once — enforced by construction
    - _Design: Rate ladder, Data Models (ladder.py)_
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [x] 7.2 Implement the per-run report — `report.py`
    - Create `gateway_v2/load_harness/report.py` with frozen+slots `RunReport`, `ReasonMix`, and
      `GpuBusy`; `RunReport` carries `c4_p99_ms` + pass/fail vs the SLO_Bound (11.1), `owner_queue_wait`
      (11.2), `event_loop_lag` (11.3), the `ReasonMix` over offered requests (11.4), the Schedule_Drop
      count (1.4, 2.4), Loadgen_Headroom (2.3), the realized strata shares + distinct key/org/tenant
      counts, and a windowed GPU-busy field (11.5)
    - `GpuBusy` is an explicit sum type — `GpuBusy.available(value)` vs `GpuBusy.UNAVAILABLE` — so with
      no configured GPU source the field is reported UNAVAILABLE rather than a fabricated number (11.6,
      15.2); owner queue wait, loop lag, and reason mix are read from the gateway's exposed telemetry at
      the client boundary, never fabricated; constant-arrival limits labeled as upper bounds (1.6)
    - _Design: Per-run report, Data Models (report.py), Error Handling (GPU-busy never fabricated)_
    - _Requirements: 1.6, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 15.2_

  - [x]* 7.3 Property test — ladder highest-pass / first-fail invariants (PBT)
    - Add `gateway_v2/tests/load_harness/test_ladder.py`: seeded `random.Random` ≥ 10,000 iters, no
      hypothesis — **Property 8: Ladder highest-pass / first-fail invariants**: for any assignment of
      per-rate pass/fail results, `highest_repeatable_pass_rate` is the greatest rate passing 3/3,
      `first_repeatable_fail_rate` is the lowest rate failing 3/3, the reported failing rate is never a
      rate that passed at least once, and steps advance by ≤ 10%
    - Tag `# Feature: load-harness-r2-21, Property 8` / `# Validates: Requirements 10.1, 10.2, 10.4, 10.5`
    - _Design: Correctness Properties → Property 8_
    - _Requirements: 10.1, 10.2, 10.4, 10.5_

  - [x]* 7.4 Unit tests — report + ladder edge cases
    - Add `gateway_v2/tests/load_harness/test_report.py`: `GpuBusy.UNAVAILABLE` when no source is
      configured, constant-arrival "upper bound" labeling, an all-errored run (`c4_p99_ms == +∞`),
      offered rate at the ladder floor, a headroom-below-margin run excluded from knee determination,
      and a non-zero Schedule_Drop count failing the run's pass test; representative examples (not PBT)
    - _Design: Testing Strategy (unit + integration — edge cases/error conditions), Error Handling_
    - _Requirements: 1.4, 2.2, 2.4, 8.5, 10.3, 11.6_

- [x] 8. Async open-loop driver + entrypoint
  - [x] 8.1 Implement the async open-loop dispatcher — `driver.py`
    - Create `gateway_v2/load_harness/driver.py` with frozen+slots `DispatchRecord` and `OfferedRun`;
      an async dispatcher that fires each `PlannedRequest` at its `scheduled_s` against the booted
      gateway over HTTP (via `httpx` at this driver boundary only — the sole HTTP site, keeping
      `check_http_outside_edge_resolve` clean) WITHOUT awaiting any prior request's response, so the
      offered rate is never reduced by server latency
    - Record a `Schedule_Drop` when an actual dispatch start exceeds `scheduled_s` by more than
      `_SCHEDULE_DROP_TOLERANCE_S`; measure `Loadgen_Headroom` as the margin between the rate the
      generator can issue (dry-fire calibration / saturation throughput) and the configured offered
      rate, marking a run below `_MIN_HEADROOM_MARGIN` as generator-bound and excluded from knee
      determination; feed each stream's chunk arrivals to the C4 collector
    - _Design: Open-loop scheduler contract (dispatch, schedule drop), Generator headroom self-proof, Module map (driver.py)_
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4_

  - [x] 8.2 Implement the `python -m load_harness` entrypoint — `__main__.py`
    - Create `gateway_v2/load_harness/__main__.py` mirroring `state_control`/`audit_control`
      `__main__`: parse a `RunSpec` from args/env, construct the injected clock + seeded
      `random.Random`, build the arrival schedule + planned requests, and invoke the driver/ladder;
      no module-level mutable, honest typing (no `Any` leak)
    - _Design: Module map (__main__.py)_
    - _Requirements: 13.1_

  - [x]* 8.3 Property test — open-loop independence from server speed (PBT, async)
    - Add `gateway_v2/tests/load_harness/test_driver.py`: seeded `random.Random` ≥ 10,000 iters, no
      hypothesis, driven via `asyncio.run` — **Property 2: Open-loop independence from server speed**:
      for any schedule and any sequence of (arbitrary, even increasing) simulated response latencies,
      the dispatched start times equal the pre-computed scheduled times (within the drop tolerance) —
      offered rate never shrinks because responses slowed (uses an injected fake transport, no real server)
    - Tag `# Feature: load-harness-r2-21, Property 2` / `# Validates: Requirements 1.1, 1.5`
    - _Design: Correctness Properties → Property 2_
    - _Requirements: 1.1, 1.5_

  - [x]* 8.4 Property test — schedule-drop accounting is exact (PBT, async)
    - Add `gateway_v2/tests/load_harness/test_driver.py` (append): seeded `random.Random` ≥ 10,000
      iters, no hypothesis, driven via `asyncio.run` — **Property 3: Schedule-drop accounting is
      exact**: for any schedule and injected dispatch delays, a request is recorded as a
      `Schedule_Drop` iff its actual start exceeds its scheduled start by more than the tolerance, and
      the reported count equals the number of such requests
    - Tag `# Feature: load-harness-r2-21, Property 3` / `# Validates: Requirements 1.3, 1.4`
    - _Design: Correctness Properties → Property 3_
    - _Requirements: 1.3, 1.4_

- [x] 9. Gate — collector + ladder + report + driver green
  - Ensure all tests pass, ask the user if questions arise. From cwd `gateway_v2` with the
    `gateway_v2/.venv` venv run the full gate over `c4.py`, `ladder.py`, `report.py`, `driver.py`,
    `__main__.py` + their tests: `.venv/bin/python -m pytest`, `.venv/bin/mypy --strict`,
    `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`,
    `.venv/bin/lint-imports`, the 6 AST gates
    (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
    and the unscanned-channels check. Confirm the C4 reconciliation adds no `latency_slo` duplication
    and the driver is the only HTTP site. Fix any gate failure before the dry-run.

- [x] 10. Local dry-run against B1 + B3 + B4 fake guard — `dryrun.py` + integration test
  - [x] 10.1 Implement the dry-run orchestration — `dryrun.py`
    - Create `gateway_v2/load_harness/dryrun.py` with frozen+slots `DryRunResult`: boot/attach one
      local gateway built by B1's `build_app` and hosted by B3's `serve.py` entrypoint with a scripted
      `StubProviderClient` (socket-free, deterministic; scripted to emit secret-shaped bytes for the
      configured share) + B4's `FakeGuard`, over the non-TLS path; run the full Poisson Rate_Ladder end
      to end; collect C4; assert the realized arrival distribution matches Poisson within tolerance, the
      realized strata shares match config within tolerance, and the C4 numbers match the Req 8 definition
    - _Design: Dry-run topology (option a — scripted StubProviderClient, non-TLS), Module map (dryrun.py), Error Handling (honest-boot gateway)_
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 15.3_

  - [x]* 10.2 Integration test — full Poisson ladder against the booted local gateway
    - Add `gateway_v2/tests/load_harness/test_dryrun.py` that `importorskip`s `uvicorn` (real loopback
      socket via `serve.py`) OR `httpx` (`httpx.ASGITransport` against `build_app(provider=scripted_stub, ...)`)
      so a framework-free `.[dev]` tree SKIPS cleanly; drive the booted gateway with the scripted
      `StubProviderClient` + `FakeGuard`, run the full Poisson ladder, and assert: realized arrivals
      match Poisson within tolerance (12.3), realized strata shares match config within tolerance (12.4),
      and C4 numbers are consistent with the Req 8 client-side definition (12.5); the secret-shaped share
      flows through the real GW12b holdback path and the honest holdback-mechanism-not-detector limit is
      recorded in the result/report
    - _Design: Testing Strategy (integration test — local dry-run), Dry-run topology (importorskip extras, in-process ASGI variant)_
    - _Requirements: 6.1, 6.2, 12.1, 12.2, 12.3, 12.4, 12.5_

- [x] 11. Gate — dry-run green (local integration de-risks the cloud one-shot)
  - Ensure all tests pass, ask the user if questions arise. From cwd `gateway_v2` with the
    `gateway_v2/.venv` venv run the full gate including the dry-run integration test (confirm it SKIPS
    cleanly when uvicorn/httpx are absent and PASSES where present): `.venv/bin/python -m pytest`,
    `.venv/bin/mypy --strict`,
    `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`,
    `.venv/bin/lint-imports`, the 6 AST gates
    (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
    and the unscanned-channels check.

- [x] 12. Docs + deferred-cloud-gate recording — `load_harness/README.md`
  - [x] 12.1 Write the harness README + name every Deferred_Cloud_Gate
    - Create `gateway_v2/load_harness/README.md`: how to run `python -m load_harness` (the dry-run
      against the local B1+B3+B4 gateway, non-TLS), the determinism contract (same seed + clock ⇒
      identical schedule + strata), the GW20b per-run report fields, and the honest
      holdback-mechanism-not-detector limit of the secret-shaped stratum
    - Name each Deferred_Cloud_Gate with its owning card/gate, NOTHING scaffolded: R2-01 fleet-scale
      knee numbers, G-01 autoscaling, G-06 fleet-scale retry amplification, G-09 integrated re-measure
      (15.1); the real GPU-busy data source — field defined + positioned, reported UNAVAILABLE (15.2);
      the TLS client / G-10 edge-TLS gate — not built, dry-run on the non-TLS path (15.3); the GW07/GW08
      secret detector + the socket-level fake-generation upstream (option b) as later seams
    - _Design: Deferred Cloud Gates, Dry-run topology (TLS non-build, option-b defer)_
    - _Requirements: 15.1, 15.2, 15.3_

- [x] 13. Final verification gate
  - [x] 13.1 Run the full gate and map evidence to the card format
    - From cwd `gateway_v2` with the `gateway_v2/.venv` venv, run the FULL gate over the whole
      `load_harness/` package + `gateway_v2/tests/load_harness/`: `.venv/bin/python -m pytest` (full
      offline suite + the harness property/unit/integration subset), `.venv/bin/mypy --strict`,
      `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`,
      `.venv/bin/lint-imports` (contracts kept, no new serving-path edge), the 6 AST gates
      (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
      and the unscanned-channels check (`unscanned_channels() == ()`); confirm modules stay
      ≤ 800 lines / functions ≤ 120. Fix any gate failure before closing
    - Map the evidence to the card format (why / implementation / local-acceptance / exit / evidence /
      rollback): why = build the local half of harness v2.3 so the cloud knee run is a measurement, not
      a debugging exercise; implementation = the `load_harness/` package (config/arrivals/strata/
      request_plan/c4/ladder/report/driver/dryrun/__main__) + the `pyproject.toml` edits + the
      `tests/load_harness/` suite; local-acceptance = the ten property tests (Properties 1–10), the
      unit edge-case tests, and the dry-run integration test (where uvicorn/httpx present); exit = full
      gate green + the dry-run passing locally; evidence = the gate transcripts; rollback = B6 is
      additive (the new `load_harness/` tree + its tests + the additive `pyproject.toml` list entries,
      `root_packages` unchanged), reverting removes the harness with no change to any shipped
      `gateway_v2` module. Record the deferred cloud gates (R2-01, G-01, G-06, G-09, real GPU source,
      TLS/G-10, GW07/GW08 detector, option-b upstream) as such — NOT closed here
    - _Design: Testing Strategy (gates that apply), Deferred Cloud Gates, Traceability_
    - _Requirements: 14.4, 14.5, 15.1, 15.2, 15.3_

- [x] 14. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are the test sub-tasks (property, unit, integration) and are visually
  distinguished; this is a Run-All-Tasks card, so the leaf tasks are schedulable and all are intended
  to be implemented. Core implementation sub-tasks are never optional.
- Each task references specific requirement sub-clauses and the design component/section/property it
  satisfies for traceability; collectively the tasks cover all 15 requirements per the design's
  Traceability table.
- The ten design properties map one-to-one onto ten property-test sub-tasks: P1 (§3.2 arrivals), P2
  (§8.3 open-loop independence), P3 (§8.4 schedule-drop accounting), P4 (§4.7 strata shares), P5 (§4.8
  populations), P6 (§6.2 C4 worst-chunk/∞), P7 (§6.3 C4 reconciliation), P8 (§7.3 ladder invariants),
  P9 (§4.9 retry), P10 (§4.10 determinism). All property tests use a seeded `random.Random` for
  ≥ 10,000 iterations, no hypothesis; async properties via `asyncio.run`; one property ↔ one test;
  tagged `# Feature: load-harness-r2-21, Property N` / `# Validates: Requirements X.Y`.
- Gate checkpoints (tasks 5, 9, 11, 13/14) run the full gate from cwd `gateway_v2` using the
  `gateway_v2/.venv` venv (system python lacks redis): `.venv/bin/python -m pytest`,
  `.venv/bin/mypy --strict`, `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness
  lint tests`, `.venv/bin/lint-imports`, the 6 AST gates
  (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
  and the unscanned-channels check (`unscanned_channels() == ()`). Gate 5 is after the pure core,
  Gate 9 after the collector/ladder/driver, Gate 11 after the dry-run, Gate 13 the FINAL gate.
- Capacity-position literals live only in `gateway_v2/runtime/resources.py`: the pass bound is read via
  `ResourceContract.target_p99_ms`, the lost-segment tail comes from `runtime/latency_slo.py`. The
  harness's schedule-drop tolerance, ladder step cap, headroom margin, repeat count, and strata
  tolerances are named operational/statistical constants (the `serving_config.py` / `latency_slo`
  category), NOT capacity literals. All harness value types are frozen+slots with no module-level
  mutable; HTTP lives only at the `driver.py` boundary; `root_packages` stays `["gateway_v2"]` so the
  harness sits outside the layer contract and adds no serving-path import edge.
- Shared-file coordination: §6.2 + §6.3 both write `tests/load_harness/test_c4.py` (append-only), so
  they are serialized into distinct waves; §8.3 + §8.4 both write `tests/load_harness/test_driver.py`
  (append-only), serialized into distinct waves. `config.py` (§2.1) precedes `arrivals.py` (§3.1) and
  the strata (§4.x) that read its constants; `request_plan.py` (§4.6) follows the five strata;
  `c4.py` (§6.1) precedes its tests; `ladder.py`/`report.py` (§7.x) precede the driver (§8.1) that
  emits runs into them; `dryrun.py` (§10.1) follows the driver + ladder it orchestrates.

## Deferred cloud gates — explicitly NOT tasks (out of scope, local half only)

Per Requirement 15 and the design's Deferred Cloud Gates section, the following are named seams with
**no** implementation task here; `tasks.md` delivers only the local half:

- **R2-01 fleet-scale knee numbers** — the actual Poisson knee RPS across the real split-gateway fleet.
- **G-01 autoscaling step tests**, **G-06 fleet-scale retry amplification**, **G-09 integrated
  re-measure** — fleet-resource gates.
- **Real GPU-busy data source** — the harness defines/positions the windowed GPU-busy field but reports
  it `UNAVAILABLE` with no configured source.
- **TLS client / G-10 edge-TLS gate** — not built; the dry-run uses the non-TLS path.
- **GW07/GW08 secret detector + the socket-level fake-generation upstream (option b)** — detector-driven
  secret classification and real-upstream secret generation; the local dry-run drives the GW12b holdback
  **mechanism** with shape-matched scripted stub output only.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["3.1", "4.1", "4.2", "4.3", "4.4", "4.5"] },
    { "id": 3, "tasks": ["3.2", "4.6", "4.7", "4.8", "4.9"] },
    { "id": 4, "tasks": ["4.10", "6.1", "7.1", "7.2"] },
    { "id": 5, "tasks": ["6.2", "7.3", "7.4", "8.1"] },
    { "id": 6, "tasks": ["6.3", "8.2", "8.3"] },
    { "id": 7, "tasks": ["8.4", "10.1"] },
    { "id": 8, "tasks": ["10.2", "12.1"] },
    { "id": 9, "tasks": ["13.1"] }
  ]
}
```
