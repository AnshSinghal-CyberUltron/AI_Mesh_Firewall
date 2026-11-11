# Requirements Document

## Introduction

GW07 (local half) builds **the single pure resolver**: one function `resolve(findings, plan, phase) -> Decision` in the `gateway_v2.resolve` layer that concentrates every security outcome of the AI Mesh Firewall into one place, so that any second decision site becomes a build failure. This removes the §10.1.1 defect class of the v1 chat pipeline (283 terminal-decision sites across 25 modules; organisation intent applied as a severity maximum instead of a precedence; a degraded output scan making the emitted action independent of the verdict; MONITOR mode enforcing on output; adapters erasing a finding before the resolver sees it).

The source of truth is the runbook `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`: the GW07 card (§10.9), the resolver specification (§10.5.3 "The resolver — the single authority"), and the v2.1 CORRECTION C22 (CRITICAL, per-finding resolution) together with the C11/C22 correction on the GW07 card.

The domain data model is **already shipped and frozen** (`gateway_v2.domain.finding`, `gateway_v2.domain.decision`, `gateway_v2.domain.plan`). This feature builds the pure FUNCTION on those existing types; it MUST NOT redefine them. The resolve-layer stub modules `resolve/resolver.py`, `resolve/decision.py`, and `resolve/conflict.py` are 3-line placeholders today; this feature implements the pure function (and resolve-layer helper modules only if needed), re-using the domain types.

Scope boundary: this card ships ONLY the pure `resolve()` function, its resolve-layer helpers, and its property + example tests. See the "Out of Scope" requirement for everything that is explicitly deferred.

## Glossary

- **Resolver**: The pure function `resolve(findings, plan, phase) -> Decision` in the `gateway_v2.resolve` layer. The single security-decision authority.
- **Finding**: The shipped frozen `gateway_v2.domain.finding.Finding(detector, detector_version, category, status, confidence, spans, evidence)` — what a detector reports. Absence is a `status`, never `None`-means-clean.
- **FindingStatus**: The shipped `EXECUTED | SKIPPED | UNAVAILABLE` enum.
- **Disposition**: The shipped `gateway_v2.domain.decision.Disposition(ALLOW | FLAG | REDACT | BLOCK)` ordered `BLOCK > REDACT > FLAG > ALLOW`.
- **most_restrictive**: The shipped `gateway_v2.domain.decision.most_restrictive(tuple[Disposition, ...]) -> Disposition` that returns the highest-ranked disposition (empty tuple returns `ALLOW`).
- **Decision**: The shipped frozen `gateway_v2.domain.decision.Decision(disposition, per_finding, transformations, findings, plan_version, deciding_rules, unavailable_detectors)` whose `__post_init__` already enforces that `disposition == most_restrictive(per_finding dispositions)`.
- **ExecutionPlan**: The shipped frozen `gateway_v2.domain.plan.ExecutionPlan` with `.version`, `.rules`, `.required_detectors`.
- **Rule**: The shipped frozen `gateway_v2.domain.plan.Rule(rule_id, category, mode, action, threshold, priority, scope, on_unavailable, surfaces)`.
- **Mode**: The shipped `gateway_v2.domain.plan.Mode(OFF | MONITOR | ENFORCE)`.
- **Action**: The shipped `gateway_v2.domain.plan.Action(ALLOW | FLAG | REDACT | BLOCK | REWRITE)`.
- **FailurePosture**: The shipped `gateway_v2.domain.plan.FailurePosture(FAIL_OPEN | FAIL_CLOSED)`. The shipped enum has NO `DEGRADE_TO` member.
- **Phase**: The shipped `gateway_v2.domain.plan.Phase(INPUT | OUTPUT)`, the resolver's third argument.
- **Selecting rule**: A rule whose configuration causes a given detector's finding to be evaluated (the rule that selected the detector).
- **Candidate disposition**: The disposition a single finding contributes under one ENFORCE rule.
- **House idiom**: The gateway_v2 property-test convention — a seeded `random.Random` running at least 10,000 iterations, with NO Hypothesis dependency, as every shipped card B1–B9 did.

## Requirements

### Requirement 1: Build the single pure resolver on the shipped domain types

**User Story:** As a security engineer, I want one pure resolver function built on the already-shipped domain model, so that every security outcome is produced in exactly one place without redefining the frozen vocabulary.

#### Acceptance Criteria

1. THE Resolver SHALL expose a function with the signature `resolve(findings, plan, phase) -> Decision`, where `findings` is a sequence of `gateway_v2.domain.finding.Finding`, `plan` is a `gateway_v2.domain.plan.ExecutionPlan`, and `phase` is a `gateway_v2.domain.plan.Phase`.
2. THE Resolver SHALL return a `gateway_v2.domain.decision.Decision` constructed from the shipped frozen type.
3. THE Resolver SHALL re-use the shipped domain types `Finding`, `FindingStatus`, `Disposition`, `most_restrictive`, `FindingDisposition`, `Transformation`, `Decision`, `Rule`, `Mode`, `Action`, `FailurePosture`, `Phase`, and `RuleScope` without redefining them.
4. THE Resolver SHALL produce a `Decision` whose request `disposition` equals `most_restrictive` of the dispositions in `per_finding`, satisfying the shipped `Decision.__post_init__` invariant.
5. THE Resolver SHALL set `Decision.plan_version` to the plan's `.version` value.
6. THE Resolver SHALL be total: FOR ALL inputs of the declared types, the Resolver SHALL return a `Decision` and SHALL NOT raise.

### Requirement 2: Resolution step 1 — partition findings by selecting rule

**User Story:** As a security engineer, I want every finding partitioned by the rule that selected its detector, so that a finding with no governing rule cannot silently influence the outcome.

#### Acceptance Criteria

1. THE Resolver SHALL partition findings by the rule that selected the finding's detector.
2. IF a finding has no selecting rule, THEN THE Resolver SHALL record that finding in `Decision.findings` and SHALL give that finding no effect on the request disposition.
3. THE Resolver SHALL include ALL findings in `Decision.findings`, including SKIPPED findings and findings with no selecting rule.

### Requirement 3: Resolution step 2 — drop SKIPPED, route UNAVAILABLE through the rule's posture

**User Story:** As a security engineer, I want SKIPPED findings dropped and UNAVAILABLE findings routed through the owning rule's failure posture, so that there is no generic degraded branch that computes an action independent of the verdict.

#### Acceptance Criteria

1. THE Resolver SHALL drop SKIPPED findings from disposition contribution while still recording them in `Decision.findings`.
2. WHEN a finding has status UNAVAILABLE, THE Resolver SHALL route that finding through its selecting rule's `on_unavailable` FailurePosture, and SHALL NOT route it through any generic degraded branch.
3. WHERE a selecting rule's `on_unavailable` posture is FAIL_CLOSED, THE Resolver SHALL treat the UNAVAILABLE finding as a BLOCK candidate disposition.
4. WHERE a selecting rule's `on_unavailable` posture is FAIL_OPEN, THE Resolver SHALL have the UNAVAILABLE finding contribute nothing (equivalently an ALLOW candidate) to the request disposition.
5. WHEN a finding has status UNAVAILABLE, THE Resolver SHALL record that finding's detector in `Decision.unavailable_detectors`.
6. THE Resolver SHALL honor only the shipped FailurePosture members FAIL_OPEN and FAIL_CLOSED, and SHALL treat any `DEGRADE_TO(action)` posture as outside this feature (a GW05 plan-compiler concern), because the shipped `FailurePosture` enum has no `DEGRADE_TO` member.

### Requirement 4: Resolution step 3 — MONITOR mode is structurally non-enforcing

**User Story:** As a security engineer, I want MONITOR-mode rules to record findings without contributing to disposition, so that the P7 defect (MONITOR enforcing on output) is removed by construction.

#### Acceptance Criteria

1. WHEN a finding is selected by a MONITOR-mode rule, THE Resolver SHALL record that finding and SHALL contribute nothing from that rule to the request disposition.
2. WHEN a MONITOR-mode rule is present in the plan, THE Resolver SHALL produce a request disposition identical to the disposition it would produce if that MONITOR-mode rule were absent.

### Requirement 5: Resolution step 4 — ENFORCE mode uses the rule's configured action

**User Story:** As a security engineer, I want ENFORCE-mode rules to contribute their configured action as the candidate disposition, so that the emitted outcome is derived only from the findings and the plan.

#### Acceptance Criteria

1. WHEN a finding is selected by an ENFORCE-mode rule, THE Resolver SHALL use that rule's configured `action` as the candidate disposition for that finding.
2. FOR ALL request dispositions the Resolver produces, THE Resolver SHALL ensure the disposition is derivable from an ENFORCE-mode rule, with the sole exception of a FAIL_CLOSED UNAVAILABLE contribution defined in Requirement 3.

### Requirement 6: Resolution step 5 (C22) — per-finding resolution then most-restrictive request disposition

**User Story:** As a security engineer, I want each finding resolved under the rules that select its own detector with priority ordering only same-finding rules, and the request disposition taken as the most restrictive per-finding disposition, so that an organisation's ALLOW for one category can never cancel a BLOCK for another and tie-breaks never depend on rule names (the P1 fix).

#### Acceptance Criteria

1. THE Resolver SHALL resolve each finding under the rules that select that finding's detector.
2. THE Resolver SHALL order candidate rules by `priority` only among rules that match the SAME finding, so that organisation intent overrides a recommendation for that finding.
3. THE Resolver SHALL compute the request disposition as the most restrictive per-finding disposition using the shipped `most_restrictive` ranking BLOCK > REDACT > FLAG > ALLOW.
4. THE Resolver SHALL NOT apply any severity maximum across different findings; an organisation ALLOW for category X SHALL NOT cancel a BLOCK for category Y.
5. WHEN two rules selecting the same finding have equal `priority`, THE Resolver SHALL apply a documented deterministic tie-break that does not depend on rule names, and SHALL record the deciding rule identifiers in `Decision.deciding_rules`.
6. THE Resolver SHALL populate `Decision.per_finding` with one `FindingDisposition` per enforced finding so that `most_restrictive(per_finding)` equals the request disposition.

### Requirement 7: Resolution step 6 — BLOCK is terminal

**User Story:** As a security engineer, I want BLOCK to be terminal, so that no later step can downgrade a block.

#### Acceptance Criteria

1. WHEN the per-finding resolution yields at least one BLOCK candidate disposition, THE Resolver SHALL produce a request disposition of BLOCK.
2. THE Resolver SHALL NOT allow any later resolution step, including REDACT or FLAG contributions, to alter a BLOCK request disposition.

### Requirement 8: Purity and layer-contract enforcement

**User Story:** As a security engineer, I want the resolver to be pure and structurally confined to importing only the domain layer, so that the single-authority guarantee is enforced by the build rather than by convention.

#### Acceptance Criteria

1. THE Resolver SHALL be pure: FOR ALL inputs it SHALL perform no I/O, read no clock, read or write no globals, produce no logging side effects, and reference no HTTP types.
2. FOR ALL identical `(findings, plan, phase)` inputs, THE Resolver SHALL return equal `Decision` values (same inputs produce the same Decision, always).
3. THE `gateway_v2.resolve` package SHALL import only `gateway_v2.domain`, keeping the shipped import-linter "Resolve imports only domain" forbidden contract green.
4. THE Resolver SHALL raise nothing and SHALL contain no HTTP constructs, so that the AST gate `check_http_outside_edge_resolve` (which permits HTTP in `edge/` and `resolve/`) stays green while the resolver itself remains HTTP-free.
5. THE resolve-layer modules `resolve/resolver.py`, and `resolve/decision.py` / `resolve/conflict.py` where helper logic is needed, SHALL re-use the domain `Decision` and `Disposition` types rather than redefining them.

### Requirement 9: Correctness properties (LGW07-8, house idiom, >= 10,000 generated cases)

**User Story:** As a security engineer, I want the resolver's universal properties proven by a seeded property suite, so that the removed defect classes cannot silently return.

#### Acceptance Criteria

1. FOR ALL generated `(findings, plan)` pairs, THE Resolver SHALL produce no disposition that is not derivable from an ENFORCE rule (allowing for the FAIL_CLOSED UNAVAILABLE contribution of Requirement 3).
2. FOR ALL generated `(findings, plan)` pairs, adding a MONITOR-mode rule SHALL NOT change the request disposition.
3. FOR ALL generated `(findings, plan)` pairs, adding a SKIPPED finding SHALL NOT change the request disposition.
4. FOR ALL generated `(findings, plan, phase)` inputs, the Resolver SHALL be deterministic: repeated evaluation of the same inputs SHALL produce equal Decisions.
5. FOR ALL generated `(findings, plan)` pairs, adding a REDACT or FLAG rule SHALL NOT remove a BLOCK request disposition.
6. FOR ALL generated `(findings, plan)` pairs, renaming rules SHALL NOT change the request disposition.
7. THE property suite SHALL be implemented in the gateway_v2 house idiom: a seeded `random.Random` running at least 10,000 iterations per property, with no Hypothesis dependency.

### Requirement 10: Local acceptance tests (LGW07 local half)

**User Story:** As a security engineer, I want the locally buildable LGW07 acceptance tests to be implemented, so that the resolver's action matrix across disposition, mode, and availability is proven without a served request or a live detector.

#### Acceptance Criteria

1. WHEN an ENFORCE rule configures an organisation action of FLAG for a finding (LGW07-2), THE Resolver SHALL retain the finding in `Decision.findings` and produce a request disposition of FLAG rather than BLOCK.
2. WHEN an organisation ENFORCE rule configures ALLOW for a finding whose detector recommends BLOCK (LGW07-3), THE Resolver SHALL resolve per the signed rule semantics and SHALL NOT apply a severity maximum.
3. WHEN a MONITOR-mode rule selects a high-confidence finding (LGW07-5), THE Resolver SHALL leave the request disposition unchanged and SHALL record the finding.
4. WHEN one prompt yields a PII finding under a REDACT rule and a Credentials finding under a BLOCK rule (LGW07-6), THE Resolver SHALL produce a request disposition of BLOCK by the most-restrictive resolution and SHALL retain both findings in `Decision.findings`.
5. THE test suite (LGW07-7, reframed) SHALL assert that the AST and import gates stay green and that the `gateway_v2.resolve` package imports only `gateway_v2.domain`, noting that `resolve/` is permitted HTTP by `check_http_outside_edge_resolve` and that the dispatch-layer status-code gating is a dispatch concern.
6. THE test suite SHALL implement the property suite of Requirement 9 as the LGW07-8 acceptance test with at least 10,000 generated cases per property.

### Requirement 11: Verification gate stays green

**User Story:** As a security engineer, I want the full gateway_v2 quality gate to remain green with the new resolver, so that the highest-scrutiny card in the track introduces no regression.

#### Acceptance Criteria

1. WHEN the gate runs from the `gateway_v2` working directory under the `.venv` virtual environment, THE full pytest suite SHALL stay green against the current baseline (1713 passed / 95 skipped / 1 xfailed) plus the new resolver tests.
2. THE codebase SHALL pass `mypy --strict`.
3. THE codebase SHALL pass `ruff check gateway_v2 state_control audit_control load_harness lint tests`.
4. THE codebase SHALL pass `lint-imports` with all contracts kept, especially "Resolve imports only domain".
5. THE AST gates SHALL each exit 0.
6. THE check `unscanned_channels()` SHALL equal `()`.

### Requirement 12: Out of scope and deferred items

**User Story:** As a security engineer, I want the explicit boundaries of this card recorded, so that deferred work is not accidentally pulled into the pure-resolver implementation.

#### Acceptance Criteria

1. THE feature SHALL defer LGW07-1 and LGW07-4 to GW15, because they require a served request or a live semantic detector.
2. THE feature SHALL treat the `DispatchAuthorization` provider-gate type as out of scope (a GW11/dispatch concern), because that type does not exist in the tree today; the "BLOCK unreachable by a provider by type" mechanism is not owned by this card.
3. THE feature SHALL treat the GW05 plan-compiler rejection of plans whose semantics the resolver cannot represent as out of scope (a GW05 concern).
4. THE feature SHALL treat the `DEGRADE_TO(action)` posture as out of scope (a GW05 concern), because it is absent from the shipped `FailurePosture` enum.
5. THE feature SHALL treat detector implementations as out of scope (GW08/GW09/GW10 concerns).
6. THE feature SHALL own only the pure `resolve()` function, its resolve-layer helper modules (`decision.py` / `conflict.py`) where needed, and its property and example tests.
