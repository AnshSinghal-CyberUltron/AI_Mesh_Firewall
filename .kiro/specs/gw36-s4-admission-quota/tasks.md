# Implementation Plan: GW36 Slice S4 — Admission, Rate, and Budget Wiring

## Overview

S4 wires the real admission controller, per-principal GCRA, per-org budget lease
registry, the BUDGET applier, the control budget field, projector BUDGET writes +
CAS pool seeding, and the composed `QuotaGate` into the `AMF_PROFILE=production`
boot. The machinery it composes already ships (GW19 `AdmissionController`, GW06
`QuotaComponent`/`LocalGCRA`/`BudgetLease`, `derive_bounds`/`derive_lease_cap_tokens`/
`derive_low_watermark`, `StateKind.BUDGET`, the budget key family in
`runtime/store_keys.py`), so most tasks construct and inject, they do not re-implement.

Implementation language is **Python** (the design's code is Python throughout).

Tasks are ordered bottom-up so the import-linter layer order holds:
1. New domain/value types and pure logic (`BudgetRecord`, `GcraCache`, `QuotaRegistry`, `QuotaGate`).
2. The state path (control budget field + `SourceView.budgets`, `StateWriter.budget_set`, projector `_budgets` phase + CAS seed, `budget_applier`).
3. The production boot wiring (`edge/production_profile.py`: `_resolve_q_safe`, `AdmissionController`, `QuotaGate`/`GcraCache`/registry injection, `READY_KINDS += BUDGET`, boot log).
4. The offline gate suite and the live acceptance run + evidence.

Source root is `gateway_v2/gateway_v2/`; tests live under `gateway_v2/tests/<area>/`.
Property tests use the repo's property-based testing library, run ≥ 100 iterations
each, and are tagged in-source `# Feature: gw36-s4-admission-quota, Property N: <text>`.

## Tasks

- [x] 1. Budget value type and BUDGET snapshot/applier scaffolding (domain layer)
  - [x] 1.1 Add the `BudgetRecord` value type
    - Create `gateway_v2/gateway_v2/admit/budget_state.py` with `@dataclass(frozen=True, slots=True) class BudgetRecord(org: str, pool_tokens: int, generation: int)` where `pool_tokens >= 0` and `generation == feed_seq`
    - Keep it a pure value type with no store or Django imports
    - _Requirements: 9.2_

  - [ ]* 1.2 Write unit tests for `BudgetRecord`
    - Construction and field invariants (non-negative `pool_tokens`, generation == feed_seq)
    - _Requirements: 9.2_

- [x] 2. Per-principal GCRA cache (domain layer)
  - [x] 2.1 Implement `GcraCache`
    - Create `gateway_v2/gateway_v2/admit/gcra_cache.py` with `GcraCache(worker_count, clock=time.monotonic)`, `for_principal(key_hash, principal) -> LocalGCRA | None`, and `invalidate(key_hashes)`
    - Key by key sha256 hash; build and cache on first use; reuse the cached `LocalGCRA` while the principal's `(rate_per_s, burst)` is unchanged; rebuild when they change
    - Set per-worker rate = `principal.rate_per_s / worker_count`; pass `burst` through
    - Return `None` when `principal.rate_per_s == 0` (no per-key limit); never module-level mutable state
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7_

  - [x]* 2.2 Write unit tests for `GcraCache`
    - Build-and-cache on first use; same instance for an unchanged principal; new instance after `invalidate`; `None` at `rate_per_s == 0`
    - File: `gateway_v2/tests/admit/test_gw36_s4_gcra_cache.py`
    - _Requirements: 3.2, 3.3, 3.5_

  - [x]* 2.3 Write property test — GCRA allowance preserved on reject
    - **Property 3: GCRA allowance preserved on reject**
    - **Validates: Requirements 3.1, 3.6**
    - First arrival admitted; a reject never advances the TAT; admits in any window ≤ `burst + rate_per_s * window`; a reject never starves the next legitimately-timed arrival
    - File: `gateway_v2/tests/admit/test_gw36_s4_gcra_cache_property.py`, ≥ 100 iterations, Feature/Property tag

  - [x]* 2.4 Write property test — per-worker rate split sums to the principal rate
    - **Property 4: Per-worker rate split sums to the principal rate**
    - **Validates: Requirements 3.4**
    - For `N ∈ [1, 64]` and `rate_per_s ∈ [0, 10_000]` (including 0 → `None`), the N per-worker rates sum to `rate_per_s` within float tolerance
    - File: `gateway_v2/tests/admit/test_gw36_s4_gcra_cache_property.py`, ≥ 100 iterations, Feature/Property tag

- [x] 3. Per-org budget lease registry (domain layer)
  - [x] 3.1 Implement `QuotaRegistry`
    - Add `QuotaRegistry(snapshot, *, make_component)` to `gateway_v2/gateway_v2/admit/quota_registry.py` with `component_for(org) -> QuotaComponent | None` and `on_generation_change(org)`
    - Create one `QuotaComponent`/`BudgetLease` per org on first use from the served `BudgetRecord` (pool tokens + generation) and cache it; reuse on later requests with no request-path record read
    - Return `None` when no `BudgetRecord` is served for the org; mark the org for re-acquire on `on_generation_change`
    - Build each `BudgetLease` with a stable per-worker id, `derive_lease_cap_tokens` chunk, `derive_low_watermark`, the injected bounded store client, and an off-path `spawn`; instance state only
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.6, 7.3_

  - [x]* 3.2 Write unit tests for `QuotaRegistry`
    - Create on first use from the served record; reuse without a request-path record read; `component_for` returns `None` for an org with no record; `on_generation_change` marks re-acquire
    - File: `gateway_v2/tests/admit/test_gw36_s4_quota_registry.py`
    - _Requirements: 4.1, 4.2, 4.3, 7.3_

- [x] 4. Composed quota gate (edge layer)
  - [x] 4.1 Implement `QuotaGate` and widen the `BudgetDecider` owner surface
    - Create `gateway_v2/gateway_v2/edge/quota_gate.py` with `QuotaGate.evaluate(cost, *, request_id, owner) -> BudgetVerdict | Admitted` and `refund(cost, *, request_id, owner)`
    - Compose GCRA (via `GcraCache`) then budget (via `QuotaRegistry`): `None` GCRA skips the rate check; a non-`None` GCRA `admit()==False` refuses with the declared 429, allowance unchanged, zero provider calls; an unknown/missing owner or `None` component refuses `budget_unavailable`, zero tokens; delegate to `component.evaluate`; on `Admitted` report `owner_id == owner`
    - `refund` resolves the same owner's component and returns exactly the spent cost to that org's lease, synchronous, no store I/O, fail-closed; wrap `evaluate` in a fail-closed catch-all
    - Extend the `BudgetDecider` protocol in `edge/chat_deps.py` with the `owner` keyword, update the single lifecycle call site to pass the resolved org, and make the fail-closed defaults accept and ignore `owner`
    - _Requirements: 4.5, 4.7, 5.1, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x]* 4.2 Write unit tests for `QuotaGate`
    - admission→GCRA→budget order with a shed example; GCRA-reject skips the spend; refund returns exactly the spent cost; unknown/missing owner fails closed to `budget_unavailable`; `Admitted.owner_id` equals the passed owner
    - File: `gateway_v2/tests/edge/test_gw36_s4_quota_gate.py`
    - _Requirements: 5.3, 5.4, 6.4, 6.5_

  - [x]* 4.3 Write property test — per-org budget isolation and owner attribution
    - **Property 1: Per-org budget isolation and owner attribution**
    - **Validates: Requirements 4.4, 4.6, 4.7, 6.1, 6.2, 6.3, 6.4**
    - File: `gateway_v2/tests/admit/test_gw36_s4_budget_isolation_property.py`, ≥ 100 iterations, Feature/Property tag

  - [x]* 4.4 Write property test — refund net-zero before a provider call
    - **Property 2: Refund net-zero before a provider call**
    - **Validates: Requirements 5.4**
    - File: `gateway_v2/tests/admit/test_gw36_s4_refund_netzero_property.py`, ≥ 100 iterations, Feature/Property tag

  - [x]* 4.5 Write property test — gate short-circuit order
    - **Property 8: Gate short-circuit order**
    - **Validates: Requirements 2.4, 2.6, 5.1, 5.2, 5.3, 5.6**
    - 8 verdict combinations with instrumented spies asserting invocation order and whether a spend occurred
    - File: `gateway_v2/tests/edge/test_gw36_s4_gate_order_property.py`, ≥ 100 iterations, Feature/Property tag

- [x] 5. Checkpoint — domain and edge logic
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Control budget source (control plane — Django)
  - [x] 6.1 Add the per-org token budget field and validators
    - Add an integer token-budget column to the org model with `MinValueValidator(0)`, `MaxValueValidator(1_000_000_000)`, default `100_000`, and a migration
    - Validate the write surface (console/API): on out-of-range or non-integer, reject the write, retain the previously stored value, and return a range error; the column holds a numeric token count only, never a credential
    - _Requirements: 8.1, 8.2, 8.3, 8.5_

  - [x]* 6.2 Write unit tests for the control budget field
    - Default 100,000 on org creation; value is a numeric token count with no credential; migration applies
    - _Requirements: 8.2, 8.5_

  - [x]* 6.3 Write property test — control budget range validation
    - **Property 9: Control budget range validation**
    - **Validates: Requirements 8.1, 8.3**
    - Integers below 0, within `[0, 1_000_000_000]`, above the ceiling, plus non-integers; out-of-range/non-integer rejected and prior value retained
    - File: alongside `gateway_v2/tests/state_control/` per the control test suite, ≥ 100 iterations, Feature/Property tag

  - [x] 6.4 Expose the budget in the projector read view
    - Add `budgets: tuple[SourceBudget, ...]` (org slug → token budget) to `SourceView` in `state_control/source.py`; populate it in `source_pg.py` (SQL read, no Django import) and the `MemorySource` twin
    - _Requirements: 8.4_

- [x] 7. Projector BUDGET writes and pool seeding (state_control)
  - [x] 7.1 Add `StateWriter.budget_set` and the projector `_budgets` write phase
    - Add `StateWriter.budget_set(org_id, pool_tokens)` delegating to `put(StateKind.BUDGET, org_id, {...})` with `if_changed=True`
    - Add a `_budgets` phase to `state_control/projector.py` (after the safety heartbeat) that writes one signed BUDGET record per org with a stored control budget; unchanged budgets write nothing; a changed budget advances `feed_seq`; a first-time org gets an initial generation; an org with no stored budget gets no record and any prior record is left unchanged
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 7.2 Seed the live pool counter with generation CAS
    - After writing/finding each org's record, WATCH/MULTI compare-and-set `{rv2}:budget:{org}:remaining` and `{rv2}:budget:{org}:generation` conditioned on stored generation == record `feed_seq`: on a new generation set `remaining = pool_tokens` and `generation = feed_seq`; do not re-seed when already equal; on CAS failure leave the counter unchanged and retry on a later round, bounded by a small attempt count
    - Use the live-counter key family in `runtime/store_keys.py` (`budget_remaining`, `budget_generation`); not `StateWriter.put`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x]* 7.3 Write unit tests for projector budget writes + seed
    - `budget_set` with `if_changed=True` writes only on change; first record gets an initial generation; no stored budget writes nothing and leaves any prior record unchanged; seed sets `remaining`/`generation` on a new generation and re-seeds on advance; forced CAS failure leaves the counter unchanged and retries bounded
    - File: `gateway_v2/tests/state_control/test_gw36_s4_projector_budget.py`
    - _Requirements: 9.1, 9.3, 9.6, 10.1, 10.2, 10.4, 10.6_

  - [x]* 7.4 Write property test — budget generation monotonicity
    - **Property 6: Budget generation monotonicity**
    - **Validates: Requirements 9.4, 9.5**
    - File: `gateway_v2/tests/state_control/test_gw36_s4_projector_budget_property.py`, ≥ 100 iterations, Feature/Property tag

  - [x]* 7.5 Write property test — CAS seed safety
    - **Property 7: CAS seed safety**
    - **Validates: Requirements 10.3, 10.5**
    - Interleave `seed(gen, pool)` (matching and stale) with `decrement(n)`; counter reflects decrements and only advances on a matching-generation seed
    - File: `gateway_v2/tests/state_control/test_gw36_s4_projector_budget_property.py`, ≥ 100 iterations, Feature/Property tag

- [x] 8. BUDGET applier and snapshot (admit — gateway synchroniser)
  - [x] 8.1 Implement `BudgetSnapshot` and `budget_applier`
    - Add `BudgetSnapshot` (`record_for(org) -> BudgetRecord | None`, `fresh()` gated by the BUDGET `KindGate`) and `budget_applier(snapshot, registry_hook) -> Applier` to `gateway_v2/gateway_v2/admit/budget_state.py`
    - On a drained round replace the served record for each named org and leave unnamed orgs unchanged; decode/verify the signed record against the cursor+floor; raise `StoreDataUnavailable` on drift or signature/completeness failure so the cursor and served records stay put; on a generation change call `registry_hook(org)` (`QuotaRegistry.on_generation_change`)
    - _Requirements: 7.2, 7.3, 7.4_

  - [x]* 8.2 Write unit tests for `budget_applier`
    - Replace named orgs / leave unnamed unchanged; drift and signature-failure examples leave the cursor and served records unchanged; generation-change triggers the registry hook
    - File: `gateway_v2/tests/admit/test_gw36_s4_budget_applier.py`
    - _Requirements: 7.2, 7.3, 7.4_

- [x] 9. Checkpoint — state path
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Production boot wiring (edge/production_profile.py)
  - [x] 10.1 Add `_resolve_q_safe` refuse-to-start
    - Add `_resolve_q_safe(env)` run before any component is built: blank/whitespace → `ProfileError` naming `AMF_Q_SAFE` as missing; non-decimal → `ProfileError` naming the variable and reproducing the rejected value; `<= 0` → `ProfileError`; no default, no literal
    - _Requirements: 1.1, 1.2, 1.4, 2.2_

  - [x] 10.2 Construct and inject the real `AdmissionController`
    - Pass the resolved `q_safe` to `derive_bounds(contract, q_safe=..., audit_drain_rate_per_s=..., audit_bytes_per_record=...)`; propagate `CapacityUnset`/`CapacityUnavailable` as a refuse-to-start with no fallback to `fail_closed_admission`
    - Source audit sizing from `AuditSink.calibrate` when wired, else from the declared `AMF_AUDIT_DRAIN_RATE_PER_S`/`AMF_AUDIT_BYTES_PER_RECORD` env vars
    - Build `AdmissionController(bounds=..., params=..., rng=..., supervisor=..., metrics=..., drain_window_s=..., clock=...)` and inject it as `ChatRoute.admission`, replacing `fail_closed_admission`
    - _Requirements: 1.3, 1.6, 2.1, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 10.3 Wire `GcraCache`, `QuotaRegistry`, and `QuotaGate` into the boot and KEY-delta path
    - Build the `GcraCache` (`worker_count` from boot config / `AMF_WORKERS`), the `QuotaRegistry` over the `BudgetSnapshot`, and the `QuotaGate` composing them; inject the `QuotaGate` as `ChatRoute.quota`, replacing `fail_closed_budget`
    - Invalidate the `GcraCache` alongside the `IdentityCache` from the identity applier on a KEY delta; register `budget_applier` in `StateRuntime.sync` so the appliers map carries KEY/PLAN/KS/BUDGET
    - _Requirements: 3.3, 4.1, 5.1, 5.6, 7.1_

  - [x] 10.4 Gate `/readyz` on BUDGET and emit the boot log
    - Add `StateKind.BUDGET` to `StateRuntime.READY_KINDS` and gate `BudgetSnapshot` through `gates.gate(StateKind.BUDGET)` so `/readyz` returns a non-success response naming BUDGET when stale past the 5000 ms window
    - Grow the `_REPLACED` set to drop the S3 fail-closed `admission`/`quota` lines; emit one boot-log line per wired admission, rate, and budget dependency (q_safe DECLARED + GW20 ownership; per-worker rate split; rate 0 = no per-key limit DECLARED; budget_unavailable never shared_state_unavailable)
    - _Requirements: 1.5, 7.5, 2.1, 3.5_

  - [x]* 10.5 Write unit tests for q_safe resolution and boot log
    - Valid value passed to `derive_bounds`; boot log records DECLARED and GW20 ownership; `CapacityUnset`/`CapacityUnavailable` propagation; audit sizing from `calibrate` when wired, from env otherwise
    - File: `gateway_v2/tests/edge/test_gw36_s4_boot.py`
    - _Requirements: 1.3, 1.5, 1.6, 2.3, 2.7, 2.8_

  - [x]* 10.6 Write property test — fail closed on any undecidable input
    - **Property 5: Fail closed on any undecidable input**
    - **Validates: Requirements 1.1, 1.2, 1.4, 1.6, 2.2, 2.3, 4.5, 5.5, 6.5, 7.4**
    - Tagged union of undecidable inputs; common post-condition: a refusal, no admit, no provider call
    - File: `gateway_v2/tests/admit/test_gw36_s4_failclosed_property.py`, ≥ 100 iterations, Feature/Property tag

  - [x]* 10.7 Write integration tests for production boot wiring
    - After `apply_production`, `ChatRoute.admission` is an `AdmissionController` and `ChatRoute.quota` is the composed `QuotaGate`, and neither fail-closed default remains in `deps`; `/readyz` gates on BUDGET naming it when expired; a committed budget change is readable through `SourceView.budgets` within one projector round
    - _Requirements: 2.1, 2.2, 7.5, 8.4_

- [x] 11. Checkpoint — production boot wiring
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Offline gate suite
  - [x] 12.1 Run the full offline gate suite and fix failures
    - `mypy --strict`, `ruff`, `import-linter` contracts (Django never imported by `gateway_v2`; projector is the only control→signed-state hop), and the AST gates — the capacity-literal gate (no capacity number outside a `ResourceContract` call; `AMF_Q_SAFE` is an injected measurement; control range `[0, 1e9]`/default `100_000` are Django policy values) and the module-mutable gate (`QuotaRegistry`/`GcraCache` hold instance state only)
    - Run in the repo's container-run offline suite as S1–S3 did; fix any failures
    - _Requirements: 2.1, 3.4, 4.1, 7.1_

- [ ] 13. Live acceptance run and evidence (Req 11)
  - [x] 13.1 Compose the service and run the live acceptance scenarios
    - Against the live stack / T02-style setup, capture wire evidence: a burst for one key beyond its burst/sustained rate returns HTTP 429 with the declared rate-limited indicator (11.1); an org whose budget is exhausted gets `budget_unavailable` on the wire with its budget state unchanged (11.2); a second non-exhausted org still succeeds with no rate-limited/`budget_unavailable` indicator (11.3); startup emits a single boot-log line naming each wired admission, rate, and budget dependency (11.4)
    - Write the evidence to a `SUMMARY.md` following the S1–S3 evidence conventions
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

- [ ] 14. Final checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional (test sub-tasks) and can be skipped for a faster MVP; core implementation and the live acceptance run (13.1) and control Django field (6.1) are REQUIRED and never marked optional.
- Each task references the specific requirement sub-clauses it satisfies for traceability.
- The nine property tests (Properties 1–9) are mapped one property per file as the design's Correctness Properties section prescribes, at ≥ 100 iterations with the `# Feature: gw36-s4-admission-quota, Property N: <text>` tag.
- Checkpoints (5, 9, 11, 14) ensure incremental validation at each layer boundary so the import-linter layer order is respected as the slice grows.
- S4 wires shipped machinery (GW19/GW06/`derive_bounds`/`StateKind.BUDGET`); it does not re-implement it. No capacity literal is introduced outside a `ResourceContract` call.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "6.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "6.2", "6.3", "6.4"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "3.1", "7.1"] },
    { "id": 3, "tasks": ["3.2", "4.1", "7.2", "8.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "4.4", "4.5", "7.3", "7.4", "7.5", "8.2"] },
    { "id": 5, "tasks": ["10.1", "10.2"] },
    { "id": 6, "tasks": ["10.3"] },
    { "id": 7, "tasks": ["10.4"] },
    { "id": 8, "tasks": ["10.5", "10.6", "10.7"] },
    { "id": 9, "tasks": ["12.1"] },
    { "id": 10, "tasks": ["13.1"] }
  ]
}
```
