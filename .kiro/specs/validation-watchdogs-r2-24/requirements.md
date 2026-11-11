# Requirements Document

## Introduction

This spec covers the **local half of B9 / R2-24**: four standalone, unit-testable
validation-environment watchdog/alarm **utilities** built in the `gateway_v2` tree.

Source of truth is the runbook `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`
(R2-24 register row in §7/program, and the v3 AMENDMENT in §3). Round 2 measured
test-environment waste: ≈ $120 of idle burn after agents stopped, idle reserved GPU
slots billing, and an audit backlog that nearly took down the shared stores. The
runbook correction requires every validation environment to run an idle watchdog, a
burn-rate monitor, a store-memory alarm and an audit guard, plus an owner-visible
teardown rule that survives agent or session loss.

The R2-24 status is **LOW / PARTIAL**: the watchdog and alarm **utilities** are buildable
locally; the owner-visible teardown rule is operations/deployer policy. This spec
therefore ships ONLY the four pure evaluator utilities and nothing else. Each utility is a
frozen-dataclass evaluator with an injected clock that takes readings as plain values and
returns an alarm verdict. It performs no I/O, does not sleep or reach the network, and does
not import a higher architectural layer. The caller (the edge/observability layer, already
above both `runtime` and `audit`) decides what to do with each verdict.

Everything else named in the runbook correction — the owner-visible teardown rule, idle
reserved GPU slot billing, and all real cloud/store/metrics/billing wiring — is explicitly
deferred and out of scope for this local half.

## Glossary

- **Validation_Environment**: a cloud environment used to run and validate the fleet during a round of work; the thing round 2 left idle and over-billed.
- **Idle_Watchdog**: the pure evaluator that detects a validation environment with no activity for longer than a configured idle bound.
- **Burn_Rate_Monitor**: the pure evaluator that compares observed or projected spend against a configured budget-per-window.
- **Store_Memory_Alarm**: the pure evaluator that raises on the shipped store-memory posture (used ratio, audit budget exceeded, unsafe/evicting policy).
- **Audit_Guard**: the pure evaluator that raises on an audit backlog or completeness breach from the shipped audit readings.
- **Alarm_Verdict**: a frozen result value returned by every utility, carrying at least an alarm level and a reason; it is advisory, and the caller decides the action.
- **Injected_Clock**: a `Callable[[], float]` passed into a utility (house idiom, defaulting to `time.monotonic`), so time is a deterministic input rather than a side effect.
- **StoreMemory**: the shipped frozen reading `gateway_v2.runtime.storemem.StoreMemory(used, maxmemory, policy, evicted_keys)` with `.used_ratio` and `.policy_unsafe`.
- **BudgetDecision**: the shipped result of `gateway_v2.runtime.storemem.audit_budget(knobs, mem)`, carrying `budget_bytes`, `source`, `warnings`.
- **Audit_Reading**: the shipped audit metrics surface `gateway_v2.audit.metrics` (`store_used_ratio`, `store_evicted_keys`, `store_maxmemory_bytes`, `completeness_ratio` against the DURABLE high-water mark, acknowledged-vs-durable high-water marks, queue depth).
- **Layer_Contract**: the import-linter order (low to high): domain, contracts, runtime, audit, egress, dispatch, resolve, detect, plan, admit, edge.
- **Gate**: the local acceptance checks run from cwd `gateway_v2` with the `gateway_v2/.venv` venv (pytest, `mypy --strict`, `ruff`, `lint-imports`, the 6 AST gates, and `unscanned_channels() == ()`).

## Requirements

### Requirement 1: Idle watchdog utility

**User Story:** As a validation-environment owner, I want a pure idle-watchdog evaluator, so that a validation environment left running with no activity is flagged before it burns ≈ $120 of idle time.

#### Acceptance Criteria

1. THE Idle_Watchdog SHALL be a frozen dataclass evaluator that accepts an Injected_Clock and a "last activity at" timestamp as plain value inputs.
2. WHEN the elapsed time between the Injected_Clock reading and the last activity timestamp exceeds the configured idle bound, THE Idle_Watchdog SHALL return an Alarm_Verdict carrying an idle alarm level and a reason.
3. WHILE the elapsed time between the Injected_Clock reading and the last activity timestamp is within the configured idle bound, THE Idle_Watchdog SHALL return an Alarm_Verdict carrying a no-alarm level.
4. THE Idle_Watchdog SHALL be deterministic for a given set of inputs and Injected_Clock reading, performing no I/O, no sleeping, and no network access.

### Requirement 2: Burn-rate monitor utility

**User Story:** As a validation-environment owner, I want a pure burn-rate evaluator, so that spend exceeding the configured budget-per-window is flagged while the environment is still running.

#### Acceptance Criteria

1. THE Burn_Rate_Monitor SHALL be a frozen dataclass evaluator that accepts an Injected_Clock, an observed-spend input, and a configured budget-per-window as plain value inputs.
2. WHEN the observed or projected burn rate exceeds the configured budget-per-window bound, THE Burn_Rate_Monitor SHALL return an Alarm_Verdict carrying a burn-rate alarm level and a reason.
3. WHILE the observed or projected burn rate is within the configured budget-per-window bound, THE Burn_Rate_Monitor SHALL return an Alarm_Verdict carrying a no-alarm level.
4. THE Burn_Rate_Monitor SHALL be deterministic for a given set of inputs and Injected_Clock reading, performing no I/O, no sleeping, and no network access.

### Requirement 3: Store-memory alarm utility

**User Story:** As a validation-environment owner, I want a pure store-memory alarm built on the shipped GW14c readings, so that a store approaching capacity or evicting is flagged before an audit flood takes down the shared stores.

#### Acceptance Criteria

1. THE Store_Memory_Alarm SHALL be a frozen dataclass evaluator that reads a shipped StoreMemory value and a shipped BudgetDecision value passed in by the caller as plain values.
2. THE Store_Memory_Alarm SHALL consume the shipped `gateway_v2.runtime.storemem` readings (`StoreMemory.used_ratio`, `StoreMemory.policy_unsafe`, and `audit_budget` output) and SHALL NOT re-implement store-memory posture computation.
3. WHEN the StoreMemory used ratio crosses the configured threshold, THE Store_Memory_Alarm SHALL return an Alarm_Verdict carrying a store-memory alarm level and a reason.
4. WHEN the audit budget is exceeded by the StoreMemory used bytes, THE Store_Memory_Alarm SHALL return an Alarm_Verdict carrying a store-memory alarm level and a reason.
5. IF the StoreMemory maxmemory policy is unsafe or evicting, THEN THE Store_Memory_Alarm SHALL return an Alarm_Verdict carrying a store-memory alarm level and a reason.
6. WHILE the used ratio is below threshold, the audit budget is not exceeded, and the policy is not unsafe, THE Store_Memory_Alarm SHALL return an Alarm_Verdict carrying a no-alarm level.

### Requirement 4: Audit guard utility

**User Story:** As a validation-environment owner, I want a pure audit-guard evaluator built on the shipped durable audit readings, so that an audit backlog or completeness breach is flagged before it nearly takes down the shared stores (R2-05/R2-11).

#### Acceptance Criteria

1. THE Audit_Guard SHALL be a frozen dataclass evaluator that reads a shipped Audit_Reading value passed in by the caller as plain values.
2. THE Audit_Guard SHALL measure completeness against the DURABLE high-water mark using the shipped `completeness_ratio`, and SHALL NOT re-derive completeness from the acknowledged high-water mark.
3. WHEN the shipped `completeness_ratio` falls below the configured completeness bound, THE Audit_Guard SHALL return an Alarm_Verdict carrying an audit alarm level and a reason.
4. WHEN the audit queue depth exceeds the configured backlog bound, THE Audit_Guard SHALL return an Alarm_Verdict carrying an audit alarm level and a reason.
5. WHILE completeness is at or above the configured bound and queue depth is within the configured backlog bound, THE Audit_Guard SHALL return an Alarm_Verdict carrying a no-alarm level.
6. THE Audit_Guard SHALL be deterministic for a given Audit_Reading, performing no I/O, no sleeping, and no network access.

### Requirement 5: Layer-contract placement

**User Story:** As a maintainer, I want the utilities placed so they never violate the import-linter Layer_Contract, so that `lint-imports` stays green and no lower layer imports a higher one.

#### Acceptance Criteria

1. THE validation-watchdog utilities SHALL be placed in the `gateway_v2` tree so that they comply with the Layer_Contract order (domain, contracts, runtime, audit, egress, dispatch, resolve, detect, plan, admit, edge).
2. WHERE a utility is placed in the `runtime` layer, THE utility SHALL NOT import `gateway_v2.audit`, because `audit` sits above `runtime` in the Layer_Contract.
3. THE Store_Memory_Alarm and THE Audit_Guard SHALL receive their readings as plain values or through a small caller-supplied protocol, so that no utility reaches down from a lower layer into `gateway_v2.audit`.
4. WHEN `lint-imports` is run, THE import-linter contracts SHALL report 2/2 kept.

### Requirement 6: House idiom and purity

**User Story:** As a maintainer, I want the utilities to follow the established gateway_v2 house idiom, so that they pass the AST gates and match the shipped evaluator style.

#### Acceptance Criteria

1. THE validation-watchdog utilities SHALL be defined as frozen dataclasses using `@dataclass(frozen=True, slots=True)`.
2. THE validation-watchdog utilities SHALL take time through an Injected_Clock `Callable[[], float]` following the inline-lambda idiom used by the shipped `Rehydrator` and `ReplicaSnapshot`, and SHALL NOT depend on `runtime/clock.py`.
3. THE validation-watchdog utilities SHALL NOT declare any capacity or SLO literal as a dataclass field, keeping capacity numbers in `runtime/resources.py` per the `check_capacity_literals` AST gate.
4. THE validation-watchdog utilities SHALL NOT define any module-level mutable, and SHALL NOT introduce any new HTTP type.
5. THE Alarm_Verdict returned by every utility SHALL be a frozen value carrying at least an alarm level and a reason, and the caller SHALL decide the resulting action.
6. WHERE property tests are written, THE tests SHALL use a seeded `random.Random` with at least 10,000 iterations and SHALL NOT use hypothesis.

### Requirement 7: Local acceptance gate

**User Story:** As a maintainer, I want the full local gate to stay green after the utilities land, so that the card is verifiably complete without regressing the baseline.

#### Acceptance Criteria

1. WHEN the pytest suite is run from cwd `gateway_v2` with the `gateway_v2/.venv` venv, THE suite SHALL stay green at the current baseline (1697 passed / 95 skipped / 1 xfailed) plus the new watchdog tests.
2. WHEN `mypy --strict` is run over the `gateway_v2` tree, THE type check SHALL pass.
3. WHEN `ruff check gateway_v2 state_control audit_control load_harness lint tests` is run, THE lint SHALL pass.
4. WHEN each of the 6 AST gates (`check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_sizes`, `check_tenant_scale`) is run, THE gate SHALL exit 0.
5. WHEN `unscanned_channels()` is evaluated, THE result SHALL equal `()`.

### Requirement 8: Deferred and out-of-scope items

**User Story:** As a validation-environment owner, I want the deferred items named explicitly, so that it is clear what this local half does NOT build.

#### Acceptance Criteria

1. THE local half SHALL NOT implement the owner-visible teardown rule, which is operations/deployer policy owned by T20.
2. THE local half SHALL NOT implement idle reserved GPU slot billing, which is a cloud/GPU-capacity concern owned by GW25.
3. THE local half SHALL NOT implement any real cloud environment wiring, real store or metrics I/O, or real GCP billing API access.
4. THE local half SHALL ship only the four pure, unit-tested evaluator utilities and their tests.
