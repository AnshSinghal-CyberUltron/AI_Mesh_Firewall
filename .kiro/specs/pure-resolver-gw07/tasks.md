# Implementation Plan: Pure Resolver (GW07)

## Overview

Convert the GW07 design into a tight, serialized set of coding steps. GW07 is a
LOCAL pure-function card over the already-shipped, frozen `gateway_v2.domain`
types — no new domain type is invented. The deliverable is small and cohesive:

1. The resolve-layer production code (one pure function + a few pure helpers +
   one frozen adapter), filling the three 3-line stubs in `resolve/` and
   exporting the public surface from `resolve/__init__.py`. All of `resolve/`
   imports ONLY `gateway_v2.domain`.
2. One new single-file test module `gateway_v2/tests/resolve/test_resolver_gw07.py`
   (plus a `tests/resolve/__init__.py` package marker) containing the 6 property
   tests + the LGW07 example/edge tests.

Structural lesson carried from B6/B7/B8/B9: the entire test file is ONE file
written by a SINGLE coherent task (one owner, one write) — it is NOT split
across parallel sub-agents. The production code is likewise written in ONE pass
so the resolver, its helpers, and the adapter land together consistently. The
production code is written BEFORE the test file that depends on it.

## Tasks

- [x] 1. Implement the resolve-layer production code in one pass
  - Fill `gateway_v2/gateway_v2/resolve/conflict.py`, `resolve/decision.py`,
    `resolve/resolver.py`, and update `resolve/__init__.py` to export the public
    surface (`resolve`, `PlanResolver`). Every module imports ONLY
    `gateway_v2.domain` (Req 8.3; design Decision 6).
  - `conflict.py` — `_select_rules(plan, phase) -> tuple[Rule, ...]`: the
    phase-admitted, non-OFF rules (INPUT phase admits `RuleScope.INPUT`/`BOTH`,
    OUTPUT phase admits `RuleScope.OUTPUT`/`BOTH`; `Mode.OFF` never selects).
    _Requirements: 2.1; design Decision 1_
  - `conflict.py` — `_rules_for(finding, selected) -> tuple[Rule, ...]`: the
    category join (rule `category` == finding `category`).
    _Requirements: 2.1, 6.1; design Decision 1_
  - `conflict.py` — `_winning_action(finding, rules) -> Action | None`: among the
    finding's ENFORCE selecting rules, order by `priority` DESCENDING (highest
    wins); the equal-priority tie-break is the most-restrictive MAPPED action
    among the tied rules via the shipped `most_restrictive` — NAME-FREE, NEVER
    `rule_id` ordering; returns `None` when no ENFORCE rule selects the finding.
    MONITOR contributes nothing; OFF does not select.
    _Requirements: 4.1, 5.1, 6.2, 6.5; design Decision 3_
  - `decision.py` — `_action_to_disposition(action) -> Disposition`: the 5→4 map
    ALLOW→ALLOW, FLAG→FLAG, REDACT→REDACT, BLOCK→BLOCK, REWRITE→REDACT.
    _Requirements: 5.1; design Decision 2_
  - `decision.py` — `_transformations_for(finding, disposition) -> tuple[Transformation, ...]`:
    one `Transformation(kind="redact", span, replacement)` per span when the
    finding's disposition is REDACT (incl. REWRITE→REDACT), using a
    locally-declared `[REDACTED]` sentinel that MUST agree with the shipped
    `egress.output_guard.REDACTION_PLACEHOLDER` (re-declared locally so `resolve/`
    imports only domain; agreement pinned by a test).
    _Requirements: 8.3, 8.5; design Decision 2, Decision 4_
  - `decision.py` — `PlanResolver`: a frozen, slotted dataclass
    `(plan: ExecutionPlan, phase: Phase)` whose `decide(matches) -> Decision`
    calls `resolve(matches, self.plan, self.phase)`; STRUCTURALLY compatible with
    the shipped `@runtime_checkable egress.output_guard.OutputResolver` protocol
    WITHOUT importing `egress`.
    _Requirements: 8.1, 8.3; design Decision 5_
  - `resolver.py` — `resolve(findings, plan, phase) -> Decision`: steps 1–6 in
    strict order. Record ALL input findings in `Decision.findings`
    (order-preserving, incl. SKIPPED, UNAVAILABLE, no-rule). For each finding:
    SKIPPED → recorded only; UNAVAILABLE → route through the selecting rule's
    `on_unavailable` posture (FAIL_CLOSED → BLOCK candidate, FAIL_OPEN → no
    candidate) and record its `detector` in `unavailable_detectors`
    (order-preserving, de-duplicated); EXECUTED → `_winning_action` →
    `_action_to_disposition` candidate. Build at most one `FindingDisposition`
    per enforced finding → `per_finding`; `disposition = most_restrictive(per_finding
    dispositions)` (empty → ALLOW; BLOCK terminal); `transformations` from
    `_transformations_for`; `plan_version = plan.version`; `deciding_rules` in
    priority order. Pure + total: no I/O, no clock, no globals, no logging, no
    HTTP, raises nothing.
    _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.2, 5.2, 6.3, 6.4, 6.6, 7.1, 7.2, 8.1, 8.2, 8.4; design Decision 3, Decision 4, Decision 6_

- [x] 2. Write the single resolver test file in one pass
  - Create the `tests/resolve/` package (`gateway_v2/tests/resolve/__init__.py`)
    and write the ONE file `gateway_v2/tests/resolve/test_resolver_gw07.py`
    containing every test below. All sub-tasks are delivered by this single
    file-owning task in one coherent write and MUST NOT be dispatched to parallel
    sub-agents (B6/B7/B8/B9 single-file lesson). Depends on Task 1.
  - [x]* 2.1 Write the seeded generator strategy + per-iteration structural-invariants bundle
    - Seeded `random.Random`, NO Hypothesis; build adversarial `(findings, plan,
      phase)` per the design generator strategy (random `Category`,
      `FindingStatus` roughly equal; EXECUTED confidence in `[0,1]` else `None`;
      0–3 ascending non-overlapping `Span`s; detectors from a small pool; rules
      spanning all `Mode`/`Action`/`RuleScope`/`FailurePosture` with a small
      priority range so ties are frequent; multiple rules per category).
    - Structural-invariants bundle asserted EVERY iteration: `disposition ==
      most_restrictive(per_finding dispositions)`; `Decision.findings` completeness
      (all inputs present, order-preserving); `unavailable_detectors` completeness;
      `plan_version == plan.version`; exactly one `FindingDisposition` per enforced
      finding; BLOCK dominance.
    - _Requirements: 1.4, 2.3, 3.5, 6.6, 9.7; design Correctness Properties / Generator strategy_
  - [x]* 2.2 Write Property 1 — no non-derivable disposition
    - `# Feature: pure-resolver-gw07, Property 1` / `# Validates: Requirements 3.3, 5.1, 5.2, 9.1`
    - Every `FindingDisposition` is the Action→Disposition map of a scope-admitted
      ENFORCE rule selecting that finding, or a FAIL_CLOSED-UNAVAILABLE BLOCK; the
      request disposition is `most_restrictive` of exactly those; ≥10,000 iters.
    - _Requirements: 3.3, 5.1, 5.2, 9.1; design Property 1_
  - [x]* 2.3 Write Property 2 — adding a MONITOR rule never changes the disposition
    - `# Feature: pure-resolver-gw07, Property 2` / `# Validates: Requirements 4.1, 4.2, 9.2`
    - Metamorphic: adding one `Mode.MONITOR` rule (any category/action/priority/
      scope/posture) leaves `disposition` unchanged; ≥10,000 iters.
    - _Requirements: 4.1, 4.2, 9.2; design Property 2_
  - [x]* 2.4 Write Property 3 — adding a SKIPPED finding never changes the disposition
    - `# Feature: pure-resolver-gw07, Property 3` / `# Validates: Requirements 3.1, 9.3`
    - Adding a `FindingStatus.SKIPPED` finding leaves `disposition` unchanged and
      the added finding still appears in `Decision.findings`; ≥10,000 iters.
    - _Requirements: 3.1, 9.3; design Property 3_
  - [x]* 2.5 Write Property 4 — determinism
    - `# Feature: pure-resolver-gw07, Property 4` / `# Validates: Requirements 1.6, 8.2, 9.4`
    - Two independent evaluations return `Decision` values equal in every field;
      ≥10,000 iters.
    - _Requirements: 1.6, 8.2, 9.4; design Property 4_
  - [x]* 2.6 Write Property 5 — adding a REDACT/FLAG/ALLOW rule never removes a BLOCK
    - `# Feature: pure-resolver-gw07, Property 5` / `# Validates: Requirements 6.4, 7.1, 7.2, 9.5`
    - For inputs already BLOCK, adding any ENFORCE rule mapping to REDACT/FLAG/
      ALLOW (any priority/category/scope) keeps `disposition` BLOCK; ≥10,000 iters.
    - _Requirements: 6.4, 7.1, 7.2, 9.5; design Property 5_
  - [x]* 2.7 Write Property 6 — renaming rules never changes the disposition
    - `# Feature: pure-resolver-gw07, Property 6` / `# Validates: Requirements 6.5, 9.6`
    - Consistently (injectively) renaming every `rule_id` leaves `disposition`
      unchanged — proves name-independence of the tie-break; ≥10,000 iters.
    - _Requirements: 6.5, 9.6; design Property 6_
  - [x]* 2.8 Write the LGW07 example tests
    - LGW07-2: ENFORCE FLAG → disposition FLAG (not BLOCK), finding retained.
    - LGW07-3: organisation ENFORCE ALLOW over a detector that recommends BLOCK →
      signed semantics, NO severity maximum.
    - LGW07-5: MONITOR high-confidence finding → disposition unchanged, finding
      recorded.
    - LGW07-6: PII REDACT + Credential BLOCK on one request → BLOCK, both findings
      retained.
    - LGW07-7 (reframed): `gateway_v2.resolve` imports only `gateway_v2.domain`
      (gates stay green); note `resolve/` is HTTP-permitted by
      `check_http_outside_edge_resolve` and status-code gating is a dispatch concern.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5; design Single test file table_
  - [x]* 2.9 Write the edge/identity example tests
    - Empty findings → ALLOW; REWRITE→REDACT mapping; the local `[REDACTED]`
      sentinel agrees with `egress.output_guard.REDACTION_PLACEHOLDER`; identity
      check that the resolve-exported `Decision`/`Disposition` ARE the `domain`
      types (`is`), confirming no redefinition.
    - _Requirements: 1.3, 8.5, 10.6; design Single test file / Decision 2, Decision 4_

- [x] 3. Run the GW07 verification gate
  - From the `gateway_v2` working directory under the `.venv` virtual
    environment, confirm: the new resolver tests pass; full pytest green
    (baseline 1713 passed / 95 skipped / 1 xfailed, plus the new tests);
    `mypy --strict` clean; `ruff check gateway_v2 state_control audit_control
    load_harness lint tests` clean; `lint-imports` with ALL contracts kept,
    ESPECIALLY "Resolve imports only domain" and "Gateway v2 layers"; the 6 AST
    gates each exit 0 (`check_capacity_literals`, `check_no_module_mutable`,
    `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_sizes`,
    `check_tenant_scale`); `unscanned_channels() == ()`.
  - Fix any failure in the Task 1 production code or Task 2 test file (no source
    changes outside `resolve/` and `tests/resolve/`), then re-run until green.
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

- [x] 4. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Deferred / out of scope (explicitly NOT tasks)

Per Requirement 12 and the GW07 card, the following are NOT implemented by this
plan and MUST NOT be pulled in:

- **LGW07-1 and LGW07-4 → GW15** (need a served request or a live semantic
  detector). _Req 12.1_
- **`DispatchAuthorization` provider-gate → GW11/dispatch** (type absent from the
  tree; "BLOCK unreachable by a provider by type" is not owned here). _Req 12.2_
- **GW05 plan-compiler rejection** of plans the resolver cannot represent → GW05.
  _Req 12.3_
- **`DEGRADE_TO(action)` posture → GW05** (absent from the shipped
  `FailurePosture` enum). _Req 12.4, 3.6_
- **Detector implementations → GW08/GW09/GW10.** _Req 12.5_
- **The `OutputResolver` injection wiring → GW08/GW15** (this card ships the
  structurally-compatible `PlanResolver`; it does NOT move the protocol or edit
  `egress`/`edge`). _Design Decision 5_

## Notes

- Sub-tasks marked with `*` are the test sub-tasks of Task 2; they are optional
  for a faster MVP and are ALL delivered by the single file-owning Task 2 in one
  coherent write — they MUST NOT be dispatched to parallel sub-agents.
- Each task references specific requirement sub-clauses and the design element it
  implements for traceability.
- Task 3 is the quality gate; Task 4 is the checkpoint.
- The production code is written BEFORE the test file that depends on it; the
  test file is one coherent file, not split.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9"] },
    { "id": 2, "tasks": ["3"] }
  ]
}
```
