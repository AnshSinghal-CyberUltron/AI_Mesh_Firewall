# Requirements Document

## Introduction

This feature is the GW27 card — "Console settings parity" — of the AI Mesh Firewall v3.1
backend rewrite. Its objective, taken verbatim from the runbook card (Part 00, GW27):

> Every one of the 45 FirewallConfig fields `firewall_v2_map.py` marks unsupported is either
> built or retired by a signed decision naming the field.

Today the control plane's v2 plan compiler (`control/ai_mesh_control/core/firewall_v2_map.py`)
classifies each `FirewallConfig` field as one of `identity`, `mapped`, or `unsupported`. A
field marked `unsupported` has no rule, mode, or action the compiler can emit, so a changed
value for it is refused at save with a field-level message
(`"{field} is unsupported. The plan compiler has no rule, mode, or action for this field."`).
An unchanged `unsupported` value is tolerated (the console posts the whole row), but any new
value is rejected loudly — the v2 `PlanDocument` (`gateway_v2/gateway_v2/plan/document.py`) can
only represent `org_id`, `streaming_mode`, and a list of rule drafts
(`category / mode / action / threshold / priority / scope / on_unavailable / surfaces / condition`).
It has no representation for rate limits, token ceilings, model catalogues, routing weights,
retention, compliance frameworks, response-token caps, or threat-intel controls. Those are the
unsupported fields.

GW27 closes this by, for every one of the 45 unsupported fields, taking one of two signed
dispositions and never silently dropping a value:

- **BUILD** — teach the control-plane compiler (and, where needed, the gateway plan/admit/
  resolve layers) to represent and enforce the field on v2 traffic, so a console change
  takes effect; or
- **RETIRE-BY-NAME** — record a named, documented decision that the field is not enforced by
  GW27, and keep its save behaviour honest (a change that cannot be represented keeps failing
  loudly with the field-level message, per L14b-5). Retire-by-name includes fields whose
  enforcement surface is **owned by another card** (GW28 routing, GW29 model isolation,
  GW31 RAG, GW33 output parity/REWRITE, GW36 token budget): GW27 does not build those, it
  records the delegation by name.

This document is the requirements artifact only. It enumerates all 45 fields, fixes each one's
BUILD-vs-RETIRE disposition (conservative and fail-closed wherever the card leaves a choice),
and captures the structural hard-constraints of the v2 tree as acceptance criteria. The design
and implementation follow in later steps.

### Dependencies and ownership (from the card)

- **Depends on:** GW26 (policy publishing — blocked keywords ride the policy path), GW14b
  (pipeline-trace honesty).
- **Required by:** GW21, GW23.
- **Explicitly owned by other cards (not built here):** routing fields → GW28; model
  isolation → GW29; RAG fields → GW31; hallucination and grounding → GW33; per-org token
  budget → GW36 (already documented in `firewall_v2_map.py`).

### Source of the "45"

`firewall_v2_map.py` contains 46 entries flagged `{"kind": "unsupported"}`. One of those,
`org_token_budget`, is already documented in that file as GW36-owned — it is a signed
`StateKind.BUDGET` record read and spent by the gateway's per-org `BudgetLease`, not a
firewall-plan field, and a change to it is reported like `org_tpm_limit`. The card's "45
FirewallConfig fields `firewall_v2_map.py` marks unsupported" is therefore the 46 entries minus
`org_token_budget`. This document enumerates all 46 by name (so nothing is lost), dispositions
the 45 in GW27 scope, and records `org_token_budget` explicitly as a GW36 delegation so the
field is never silently dropped either.

## Glossary

- **FirewallConfig**: the per-organization Django model
  (`control/ai_mesh_control/core/models.py::FirewallConfig`) backing the console settings
  form; it is serialized and published to the gateway.
- **V2_Plan_Compiler**: `control/ai_mesh_control/core/firewall_v2_map.py` — the module whose
  `FIELDS` table classifies each FirewallConfig field and whose `selection_errors()` and
  `drafts_for()` decide what the v2 plan document contains and what a save refuses.
- **Plan_Document**: the signed per-org selection the gateway loads and compiles
  (`gateway_v2/gateway_v2/plan/document.py::PlanDocument`): `org_id`, `streaming_mode`, and a
  tuple of `RuleDraft`. The only shape the gateway's `compile_plan` accepts.
- **Rule_Draft**: one compiled rule in the Plan_Document (category, mode, action, threshold,
  priority, scope, on_unavailable, surfaces, condition).
- **Unsupported_Field**: a FirewallConfig field the V2_Plan_Compiler marks
  `{"kind": "unsupported"}` — a new value for it is refused at save unless the field is on the
  signed retire list and the retire decision changes that behaviour.
- **BUILD**: the disposition in which GW27 adds real v2 representation and enforcement for a
  field so a console change takes effect on v2 traffic.
- **RETIRE-BY-NAME**: the disposition in which GW27 records a named, documented decision not to
  enforce the field, and the save path stays honest (fail-loudly on a value the compiler cannot
  represent).
- **Delegated_Field**: a RETIRE-BY-NAME field whose enforcement is owned by another named card
  (GW28/GW29/GW31/GW33/GW36); GW27 records the owner and does not build it.
- **Honest_Save**: the save-path contract (L14b-5) — a save the compiler cannot represent keeps
  failing with a field-level message naming the field; it is never accepted and then ignored.
- **Offline_Gate_Suite**: the v2 tree's offline verification bar — full `pytest` offline suite,
  `mypy --strict`, `ruff`, import-linter (both contracts kept), and the five AST gates
  (capacity-literals, no-module-mutable, frozen-dataclasses, http-outside-edge-resolve,
  tenant-scale) each exiting 0, over both the `gateway_v2` tree and the control tree touched.

## Field enumeration and disposition

All 46 `unsupported`-flagged FirewallConfig fields, each with its GW27 disposition. The 45 in
scope plus `org_token_budget` (GW36 delegation, enumerated for completeness).

### Group A — Rate limiting and token ceiling (BUILD)

The card names these explicitly: "request rate limit, burst limit and org TPM (admission
exists, per-org configuration does not)".

| # | Field | Disposition | Decision |
|---|---|---|---|
| 1 | `rate_limit_enabled` | **BUILD** | Master switch for the per-org request-rate control published into the plan; off = no rate rule. |
| 2 | `requests_per_minute` | **BUILD** | Per-org sustained request rate published so the admission/GCRA path enforces it per org. |
| 3 | `burst_limit` | **BUILD** | Per-org burst allowance; drives the L27-2 "6th request in a second → declared 429 with Retry-After" behaviour. |
| 4 | `org_tpm_limit` | **BUILD** | Org-wide tokens-per-minute ceiling across all keys (0 = disabled); published for the gateway org-wide TPM check. |

### Group B — Content filtering: blocked keywords (BUILD via GW26)

| # | Field | Disposition | Decision |
|---|---|---|---|
| 5 | `blocked_keywords` | **BUILD (via GW26)** | Comma-separated keywords become a keyword-condition rule through the GW26 policy-publishing path (dependency), not a new compiler primitive. |

### Group C — Response guardrails and limits (BUILD)

The card names "max response tokens", "audit logging and retention", "compliance frameworks"
and "REWRITE actions (now refused at save)".

| # | Field | Disposition | Decision |
|---|---|---|---|
| 6 | `max_response_tokens` | **BUILD** | Per-org response-token cap represented in the plan and enforced on the output/egress path. |
| 7 | `output_policy_enabled` | **BUILD** | Output policy-engine enforcement master switch; emitted as an output-scope rule enable. |
| 8 | `output_policy_action` (`rewrite` rejected) | **BUILD enable + RETIRE `rewrite`** | `allow/flag/redact/block` BUILD; `rewrite` stays refused-at-save (delegated to GW33). |
| 9 | `output_incident_logging_enabled` | **BUILD** | Output-guard incident logging toggle published so operator choice takes effect. |
| 10 | `output_hallucination_action` (`rewrite` rejected) | **RETIRE-BY-NAME → GW33** | Hallucination/grounding output action is GW33's surface; `rewrite` stays refused-at-save. |

### Group D — Audit and compliance (BUILD)

| # | Field | Disposition | Decision |
|---|---|---|---|
| 11 | `audit_logging_enabled` | **BUILD** | Per-org audit/telemetry enable represented in the plan and honoured by the gateway audit path. |
| 12 | `retention_days` | **BUILD** | Per-org audit retention window published; bounds the durable sink's per-org retention. |
| 13 | `compliance_frameworks` | **BUILD** | Active compliance frameworks published so compliance-driven enforcement reflects operator selection. |

### Group E — Threat intelligence (BUILD)

The card names "threat intel and auto-block".

| # | Field | Disposition | Decision |
|---|---|---|---|
| 14 | `threat_intel_enabled` | **BUILD** | Per-org threat-intel enable represented in the plan. |
| 15 | `auto_block_threats` | **BUILD** | Auto-block-on-threat enable represented in the plan. |
| 16 | `threat_score_threshold` | **BUILD** | Minimum score (0–100) that triggers blocking, published as the threat threshold. |

### Group F — Logging detail (RETIRE-BY-NAME)

| # | Field | Disposition | Decision |
|---|---|---|---|
| 17 | `log_level` | **RETIRE-BY-NAME** | v2 observability is the typed GW14d exposition with bounded cardinality; there is no per-org "log detail" knob and the card does not name one to build. Not an enforcement selection. Save stays honest. |

### Group G — Model governance / isolation (RETIRE-BY-NAME → GW29)

Model isolation is explicitly owned by GW29.

| # | Field | Disposition | Decision |
|---|---|---|---|
| 18 | `model_isolation_enabled` | **RETIRE-BY-NAME → GW29** | Isolation is plan state built by GW29 (KillSwitchSnapshot / isolation). |
| 19 | `allowed_models` | **RETIRE-BY-NAME → GW28** | Allowed-model governance is the GW28 per-org model catalogue. |
| 20 | `default_model` | **RETIRE-BY-NAME → GW28** | Default/fallback model selection is GW28 routing. |

### Group H — Tier-2 execution and MCP Tier-2 (RETIRE-BY-NAME, conservative)

These fields configure the v1 Tier-2 (Bedrock LLM scan) and MCP posture. v2's detection is
policy-driven (GW07–GW10) and its semantic detector is GW10; there is no v2 "Tier-2 execution
mode" plan primitive and no card names one for GW27 to build. Disposition is RETIRE-BY-NAME with
a conservative, fail-closed rationale: the absence of an enforcement surface means a changed
value cannot take effect, so the save stays honest rather than accepting a value that would be
ignored.

| # | Field | Disposition | Decision |
|---|---|---|---|
| 21 | `semantic_analysis_enabled` | **RETIRE-BY-NAME → GW10** | Semantic detection enable is governed by the GW10 semantic detector / policy set, not a firewall-plan field. |
| 22 | `tier2_execution_mode` | **RETIRE-BY-NAME** | No v2 plan primitive for sync/async Tier-2 execution; not named for GW27 build. |
| 23 | `tier2_stream_hold_enabled` | **RETIRE-BY-NAME → GW13** | Stream-hold is the GW13 output-streaming-mode surface (strict/incremental), not a firewall-plan field. |
| 24 | `tier2_stream_hold_timeout_ms` | **RETIRE-BY-NAME → GW13** | Same surface as 23. |
| 25 | `tier2_enabled` | **RETIRE-BY-NAME → GW10** | Per-org Tier-2 override is a detector/policy concern; no firewall-plan primitive. |
| 26 | `mcp_tier2_enabled` | **RETIRE-BY-NAME → GW17/GW10** | MCP Tier-2 gate is the MCP-surface card's concern. |
| 27 | `mcp_ext_scan_action` | **RETIRE-BY-NAME → GW17/GW32** | External MCP proxy posture is the MCP-surface card's concern. |
| 28 | `tier2_strict` | **RETIRE-BY-NAME** | Tier-2-unavailable posture; v2 fail-closed posture is `on_unavailable` on each rule, which the compiler already emits. No separate plan field to build. |
| 29 | `mcp_policy_only_enforcement` | **RETIRE-BY-NAME → GW17** | MCP Phase-3 cutover flag is the MCP-surface card's concern. |

### Group I — Factuality / hallucination grounding (RETIRE-BY-NAME → GW33)

Hallucination and grounding are explicitly owned by GW33.

| # | Field | Disposition | Decision |
|---|---|---|---|
| 30 | `factuality_check_enabled` | **RETIRE-BY-NAME → GW33** | Hallucination-check enable is GW33 output parity. |
| 31 | `hallucination_grounding_mode` | **RETIRE-BY-NAME → GW33** | Grounding scoring mode is GW33. |
| 32 | `hallucination_grounding_threshold` | **RETIRE-BY-NAME → GW33** | Grounding threshold is GW33. |
| 33 | `hallucination_grounding_model` | **RETIRE-BY-NAME → GW33** | Grounding model override is GW33. |

### Group J — RAG security (RETIRE-BY-NAME → GW31)

RAG fields are explicitly owned by GW31.

| # | Field | Disposition | Decision |
|---|---|---|---|
| 34 | `rag_enabled` | **RETIRE-BY-NAME → GW31** | GW31 "six RAG settings". |
| 35 | `vector_db_isolation` | **RETIRE-BY-NAME → GW31** | GW31 / GW30 vector isolation. |
| 36 | `rag_max_documents` | **RETIRE-BY-NAME → GW31** | GW31. |
| 37 | `rag_relevance_threshold` | **RETIRE-BY-NAME → GW31** | GW31. |
| 38 | `rag_redaction_enabled` | **RETIRE-BY-NAME → GW31** | GW31. |
| 39 | `rag_tier2_enabled` | **RETIRE-BY-NAME → GW31** | GW31. |

### Group K — Routing governance (RETIRE-BY-NAME → GW28)

Routing fields are explicitly owned by GW28.

| # | Field | Disposition | Decision |
|---|---|---|---|
| 40 | `routing_risk_weight` | **RETIRE-BY-NAME → GW28** | GW28 weighted routing. |
| 41 | `routing_cost_weight` | **RETIRE-BY-NAME → GW28** | GW28. |
| 42 | `routing_latency_weight` | **RETIRE-BY-NAME → GW28** | GW28. |
| 43 | `routing_priority_weight` | **RETIRE-BY-NAME → GW28** | GW28. |
| 44 | `default_data_sensitivity` | **RETIRE-BY-NAME → GW28** | Sensitivity gating is GW28's pre-dispatch selection. |
| 45 | `routing_enabled` | **RETIRE-BY-NAME → GW28** | GW28 deterministic routing enable. |

### Delegated (enumerated, GW36-owned; not in the 45)

| # | Field | Disposition | Decision |
|---|---|---|---|
| — | `org_token_budget` | **RETIRE-BY-NAME → GW36** | Signed `StateKind.BUDGET` record spent by the per-org `BudgetLease`; already documented in `firewall_v2_map.py`. Change reported like `org_tpm_limit`; never silently dropped. |

### REWRITE actions (cross-cutting, named by the card)

The card lists "REWRITE actions (now refused at save)" among the build items. Four fields carry
a REWRITE option the compiler currently rejects at save: `output_pii_action`,
`output_credential_action`, `output_ip_leakage_action` (currently `mapped`, so **not** in the 45
unsupported fields), and `output_policy_action` / `output_hallucination_action` (in the 45).
The actual REWRITE transformation is owned by GW33 (output parity: "IP leak, exfil channels,
grounding, human review, REWRITE, canary"). GW27's conservative, fail-closed disposition:
**keep `rewrite` refused-at-save loudly on every action field** and record GW33 as the owner of
the REWRITE build. GW27 does not implement REWRITE; it guarantees the refusal is honest and
named, so no org believes a `rewrite` selection took effect.

### Disposition tally

- **BUILD (real v2 enforcement): 15 fields** — items 1–9 and 11–16:
  `rate_limit_enabled`, `requests_per_minute`, `burst_limit`, `org_tpm_limit`,
  `blocked_keywords` (via GW26), `max_response_tokens`, `output_policy_enabled`,
  `output_policy_action` (enable only; `rewrite` retired to GW33),
  `output_incident_logging_enabled`, `audit_logging_enabled`, `retention_days`,
  `compliance_frameworks`, `threat_intel_enabled`, `auto_block_threats`,
  `threat_score_threshold`. (Item 10, `output_hallucination_action`, sits in Group C but is a
  GW33 delegation, so it is counted under RETIRE-BY-NAME, not here.)

- **RETIRE-BY-NAME: 30 fields** — item 10 (`output_hallucination_action` → GW33), item 17
  (`log_level`), Group G (18–20 → GW29/GW28), Group H (21–29, conservative/delegated), Group I
  (30–33 → GW33), Group J (34–39 → GW31), Group K (40–45 → GW28).

- **Total in GW27 scope: 45** (15 BUILD + 30 RETIRE-BY-NAME). Plus `org_token_budget`
  enumerated as a GW36 delegation (the 46th `unsupported` entry, outside the 45).

Every RETIRE-BY-NAME disposition is fail-closed: because no GW27 enforcement surface exists for
the field, the compiler cannot represent a new value, so the save keeps failing loudly rather
than accepting a value that would be ignored. Delegations name the owning card so the field is
tracked, not dropped.

## Requirements

### Requirement 1: Every unsupported field has one signed disposition

**User Story:** As a backend owner, I want each of the 45 unsupported FirewallConfig fields to
carry exactly one disposition — BUILD or RETIRE-BY-NAME — named in code and documentation, so
that no field is silently ignored and the card's objective is auditable.

#### Acceptance Criteria

1. THE V2_Plan_Compiler `FIELDS` table SHALL carry, for every one of the 45 Unsupported_Fields, an explicit disposition marker distinguishing BUILD from RETIRE-BY-NAME, with no field left as a bare `{"kind": "unsupported"}` lacking a disposition.
2. WHERE a field is dispositioned RETIRE-BY-NAME, THE field's entry SHALL record the named reason or the owning card (GW28/GW29/GW31/GW33/GW36, or a standalone documented reason for `log_level` and the Tier-2/MCP group).
3. THE requirements and the compiler SHALL agree on the tally: 15 BUILD fields and 30 RETIRE-BY-NAME fields in GW27 scope, plus `org_token_budget` recorded as a GW36 delegation.
4. IF a FirewallConfig field exists that is neither `identity`, `mapped`, BUILD, nor RETIRE-BY-NAME, THEN a verification test SHALL fail, so a newly added field cannot slip through undispositioned.

### Requirement 2: Saves stay honest (L14b-5)

**User Story:** As an operator, I want a save the v2 compiler cannot represent to fail with a
field-level message naming the field, so that I never believe a setting took effect when it did
not.

#### Acceptance Criteria

1. WHEN a save changes a RETIRE-BY-NAME field to a value the V2_Plan_Compiler cannot represent, THE control plane SHALL refuse the save with a field-keyed message naming the field, and SHALL NOT store-and-ignore the value.
2. WHEN a save sets any output action field (`output_policy_action`, `output_hallucination_action`, `output_pii_action`, `output_credential_action`, `output_ip_leakage_action`) to `rewrite`, THE control plane SHALL refuse the save with a field-level message, because REWRITE is owned by GW33 and is not enforced by GW27.
3. WHILE a RETIRE-BY-NAME field retains its stored value unchanged, THE control plane SHALL accept the surrounding save (an unchanged unsupported value is not an error), consistent with the console posting the whole row.
4. WHEN a save changes a BUILD field to a value within that field's validator range, once the fleet plan-schema gate has passed and controls are enabled for the org (design D-10; before that, a change to a controls-backed field is refused loudly with a field-keyed message, which is a conforming outcome), THE control plane SHALL accept the save and SHALL include the field's effect in the published Plan_Document.
5. THE refusal message for an unrepresentable value SHALL be field-keyed (one message per offending field), so the console can attach it to the correct form control.

### Requirement 3: Rate limit, burst and org TPM take effect per org (BUILD)

**User Story:** As an operator, I want per-org request-rate, burst, and token-per-minute
ceilings I set in the console to take effect on v2 traffic, so that abuse protection is
configured per organization rather than only globally.

#### Acceptance Criteria

1. WHEN `rate_limit_enabled` is true and `requests_per_minute` / `burst_limit` are set, THE published Plan_Document SHALL carry the per-org rate and burst so the gateway admission path enforces them for that org on the billable OpenAI-compatible surfaces (chat, responses, embeddings). The limits are per gateway replica; MCP and moderations are named exclusions (owners GW17/GW32 and none respectively).
2. WHEN `rate_limit_enabled` is false, THE published Plan_Document SHALL carry no active per-org rate rule, and the gateway SHALL not rate-limit that org on this control.
3. WHEN `org_tpm_limit` is a positive integer, THE published Plan_Document SHALL carry the org-wide tokens-per-minute ceiling; WHEN it is 0, the ceiling SHALL be disabled (backward-safe), matching the model's documented semantics.
4. WHEN the burst limit is 5 and the requests-per-minute value is fixed so the per-worker emission interval exceeds the request spacing, THE gateway SHALL admit 5 back-to-back requests and return a declared 429 with a `Retry-After` header for the 6th (card test L27-2). Because org state is per worker process (no request-path store I/O), the guarantee is exact for a single-worker gateway; for a replica with N workers the aggregate burst admitted at one instant is between `burst` and `N * burst_worker`, where `burst_worker` is the even share plus a named number of binomial standard deviations of spread slack (exactly `burst` when N is 1), so compliant tenants are not falsely refused under uneven worker accept distribution. `org_tpm_limit > 0` is enforced independently of `rate_limit_enabled`; the switch gates only the request-rate and burst limit. All three limits (rate, burst, TPM) are per gateway replica, not fleet-wide; no fleet-wide bound is claimed (design D-4).
5. IF any rate/burst/TPM bound would require a capacity value, THEN that value SHALL be derived through `runtime/resources.py` (ResourceContract), never written as a literal outside that module.

### Requirement 4: Blocked keywords enforce through the policy path (BUILD via GW26)

**User Story:** As an operator, I want keywords I enter in `blocked_keywords` to block matching
traffic on v2, so that the console's keyword list is enforced.

#### Acceptance Criteria

1. WHEN `blocked_keywords` holds a non-empty comma-separated list, THE control plane SHALL publish those keywords as a keyword-condition rule through the GW26 policy-publishing path.
2. WHEN `blocked_keywords` is empty, THE published Plan_Document SHALL carry no keyword rule from this field.
3. THE keyword rule SHALL use the existing Rule_Draft condition shape (`kind="keywords"`), introducing no new compiler primitive, so GW26's policy publishing remains the single authority for keyword rules.
4. WHERE GW26 is not yet present on the tree, THE requirement SHALL be recorded as dependent on GW26 and SHALL NOT be implemented by duplicating keyword-rule compilation inside `firewall_v2_map.py`.

### Requirement 5: Response-token cap, output policy, audit, retention, compliance and threat-intel take effect (BUILD)

**User Story:** As an operator, I want the max-response-token cap, output-policy enforcement,
audit logging and retention, compliance frameworks, and threat-intel controls I set to take
effect on v2 traffic, so that these governance settings are not stored-and-ignored.

#### Acceptance Criteria

1. WHEN `max_response_tokens` is set within its validator range, THE published Plan_Document SHALL carry the per-org response-token cap and the gateway SHALL enforce it as a request-side clamp on the upstream `max_tokens`/`max_completion_tokens`; response-side token counting is owned by GW33 (design D-6).
2. WHEN `output_policy_enabled` is true, THE published Plan_Document SHALL carry an output-scope policy enable; WHEN false, no such enable.
3. WHEN `output_policy_action` is `allow`, `flag`, `redact`, or `block`, THE published Plan_Document SHALL carry that output action; WHEN it is `rewrite`, the save SHALL be refused per Requirement 2.2.
4. WHEN `output_incident_logging_enabled` is set, THE published Plan_Document SHALL carry the operator's choice for output-guard incident logging.
5. WHEN `audit_logging_enabled` is set, THE published Plan_Document SHALL carry the per-org audit/telemetry enable and the gateway audit path SHALL honour it.
6. WHEN `retention_days` is set within its validator range, THE published Plan_Document SHALL carry the per-org audit retention window.
7. WHEN `compliance_frameworks` is set, THE published Plan_Document SHALL carry the active frameworks.
8. WHEN `threat_intel_enabled`, `auto_block_threats`, and `threat_score_threshold` are set, THE published Plan_Document SHALL carry the threat-intel enable, auto-block enable, and the 0–100 score threshold respectively.

### Requirement 6: Delegated fields are named, not built, and stay honest

**User Story:** As a backend owner, I want fields whose enforcement is owned by another card to
be recorded against that card and to keep failing loudly on an unrepresentable change, so that
GW27 neither double-builds another card's surface nor silently drops the field.

#### Acceptance Criteria

1. THE routing fields (`routing_risk_weight`, `routing_cost_weight`, `routing_latency_weight`, `routing_priority_weight`, `default_data_sensitivity`, `routing_enabled`, `allowed_models`, `default_model`) SHALL be recorded as owned by GW28 and SHALL NOT be enforced by GW27.
2. THE `model_isolation_enabled` field SHALL be recorded as owned by GW29.
3. THE RAG fields (`rag_enabled`, `vector_db_isolation`, `rag_max_documents`, `rag_relevance_threshold`, `rag_redaction_enabled`, `rag_tier2_enabled`) SHALL be recorded as owned by GW31.
4. THE hallucination/grounding fields (`factuality_check_enabled`, `hallucination_grounding_mode`, `hallucination_grounding_threshold`, `hallucination_grounding_model`, `output_hallucination_action`) SHALL be recorded as owned by GW33.
5. THE `org_token_budget` field SHALL remain recorded as owned by GW36 (signed `StateKind.BUDGET`), unchanged by GW27.
6. WHEN any delegated field is changed to a value the compiler cannot represent, THE save SHALL be refused with a field-level message (Honest_Save), exactly as for any other RETIRE-BY-NAME field.

### Requirement 7: Conservatively retired fields have a documented reason

**User Story:** As a reviewer, I want each field retired without a delegating card to carry a
standalone documented reason, so that "retire" is a decision and not an omission.

#### Acceptance Criteria

1. THE `log_level` field SHALL be retired with the documented reason that v2 observability is the typed GW14d exposition and there is no per-org log-detail enforcement knob to build.
2. THE Tier-2 and MCP-posture fields (`semantic_analysis_enabled`, `tier2_execution_mode`, `tier2_stream_hold_enabled`, `tier2_stream_hold_timeout_ms`, `tier2_enabled`, `mcp_tier2_enabled`, `mcp_ext_scan_action`, `tier2_strict`, `mcp_policy_only_enforcement`) SHALL each be retired with a documented reason naming either the owning surface card (GW10/GW13/GW17/GW32) or the v2 mechanism that subsumes it (per-rule `on_unavailable` for `tier2_strict`).
3. WHERE a retire decision is ambiguous, THE decision SHALL be the conservative, fail-closed one (do not build an unenforced surface; keep the save honest) and the ambiguity SHALL be recorded in the field's documented reason.

## Non-Functional Requirements

### Structural constraints (v2 tree doctrine — hard acceptance criteria)

1. **Layer order (import-linter).** Any new gateway-side code SHALL respect the bottom-up layer contract (`edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`); a lower layer SHALL never import a higher one, and both import-linter contracts SHALL remain kept. Control-plane code (`control/…`) SHALL NOT import `gateway_v2`.
2. **No new capacity literal outside `runtime/resources.py`.** Every capacity/bound value (rate, burst, TPM, token cap, retention bound, queue/pool size) SHALL derive from the ResourceContract in `gateway_v2/gateway_v2/runtime/resources.py`; the AST capacity-literal gate SHALL exit 0. The gate's allowlist is exactly `runtime/resources.py` plus the sibling `runtime/resources_org.py` (design D-13; `resources.py` is at the 800-line cap), enforced by a gate test. Control-tree validation constants (keyword limits, `RETENTION_FLOOR_DAYS`, serializer ranges) are exempt; the AST capacity gate scans `gateway_v2` only.
3. **No module-level mutable state touched per request.** New modules SHALL hold no module-level mutable state on the request path; the AST no-module-mutable gate SHALL exit 0.
4. **Frozen/slotted dataclasses for value types.** Any new value type (e.g. a plan-document extension carrying rate/budget/retention selections) SHALL be a `@dataclass(frozen=True, slots=True)`; the AST frozen-dataclasses gate SHALL exit 0.
5. **Fail-closed everywhere.** Every new path SHALL fail closed: an unrepresentable or ambiguous value refuses (loud save failure) rather than passing through; a degraded enforcement surface SHALL not silently disable a configured control.
6. **HTTP only in edge/resolve.** No new `HTTPException`, `JSONResponse`, or `status_code=403/429` SHALL appear outside `edge/` and `resolve/` in the gateway tree; the http-outside-edge-resolve gate SHALL exit 0. The control plane's own save-refusal path is unchanged (it already returns field-keyed errors to the console, not through the gateway layers).
7. **Offline gate suite green.** The full Offline_Gate_Suite (pytest offline, `mypy --strict`, `ruff`, import-linter 2/2, five AST gates over both touched trees) SHALL pass with no new regressions.

## Acceptance Criteria (card-level, numbered and testable)

1. **AC-1 (enumeration).** A test enumerates all 46 `unsupported`-flagged fields in `firewall_v2_map.py` and asserts each carries a GW27 disposition; the count of BUILD = 15, RETIRE-BY-NAME = 30 (in scope), plus `org_token_budget` delegated to GW36. (Requirement 1)
2. **AC-2 (honest save, L14b-5).** Changing a RETIRE-BY-NAME field to an unrepresentable value is refused with a field-keyed message; an unchanged unsupported value is accepted alongside other edits. (Requirement 2)
3. **AC-3 (rewrite refused).** Setting any output action field to `rewrite` is refused at save with a field-level message naming the field. (Requirement 2.2)
4. **AC-4 (rate/burst/TPM take effect).** Once the fleet plan-schema gate has passed and controls are enabled for the org, a console change to `rate_limit_enabled` / `requests_per_minute` / `burst_limit` / `org_tpm_limit` is published into the Plan_Document and takes effect, as a per-gateway-replica limit, on chat, responses and embeddings; before that, the change is refused with a field-keyed message. (Requirement 3)
5. **AC-5 (L27-2 live).** With burst 5 and requests-per-minute fixed so the per-worker emission interval exceeds the request spacing, 5 back-to-back requests are admitted and the 6th gets a declared 429 with `Retry-After` on a single-worker gateway; at the production worker count the admitted count is at most `workers() * burst_worker(5, workers())` and the next request is refused with 429. (Requirement 3.4, design D-4)
6. **AC-6 (L27-1 live).** With the fleet gate passed and controls enabled for the test org, changing each of the 60 console settings either takes effect on v2 traffic or is rejected with a field-level message for a field on the signed retire list (or, before enablement, for a controls-backed field). (Requirements 2, 3, 4, 5)
7. **AC-7 (blocked keywords via GW26).** A non-empty `blocked_keywords` list is enforced as a keyword rule published through the GW26 path, with no keyword-rule compilation duplicated inside `firewall_v2_map.py`. (Requirement 4)
8. **AC-8 (response/audit/compliance/threat-intel BUILD).** `max_response_tokens` (request-side clamp), output-policy enable/action (non-rewrite), output incident logging, `audit_logging_enabled`, `retention_days`, `compliance_frameworks`, and the three threat-intel fields each appear in the published Plan_Document and take effect, subject to the AC-4 precondition for controls-backed fields and to the keyword/threat rollout mode (design D-8). (Requirement 5)
9. **AC-9 (delegations named).** Every delegated field is recorded against its owning card (GW28/GW29/GW31/GW33/GW36) and is not enforced by GW27; a changed-but-unrepresentable value still fails the save loudly. (Requirement 6)
10. **AC-10 (conservative retires documented).** `log_level` and each Tier-2/MCP-posture field carries a standalone documented reason; ambiguous cases resolve to the fail-closed decision. (Requirement 7)
11. **AC-11 (structural gates).** import-linter 2/2 kept, no new capacity literal outside `runtime/resources.py`, no module-level mutable state, frozen/slotted value types, HTTP only in edge/resolve, and the full offline gate suite green. (Non-Functional Requirements 1–7)

## Out of Scope

- Implementing REWRITE transformations, hallucination/grounding enforcement, or output-parity
  features — owned by **GW33**.
- Weighted model routing, allowed/default model governance, sensitivity/compliance pre-dispatch
  gating — owned by **GW28**.
- Model isolation, kill-switch, reroute, auto-isolation — owned by **GW29**.
- RAG pipeline, vector isolation, RAG settings enforcement — owned by **GW31** (adapter is
  **GW30**).
- The per-org token-budget lease mechanism — owned by **GW36** (`org_token_budget` is already
  documented and is only recorded, not changed, here).
- MCP surface, external-proxy posture, MCP Tier-2 gating, Phase-3 cutover — owned by **GW17 /
  GW32**.
- Any change to the console/frontend forms beyond consuming the existing field-level save-error
  messages — frontend parity is tracked under **GW35**.
- Serving wiring for the org controls (rate/burst/TPM, response clamp, audit gating) on chat, responses and embeddings IS in scope. MCP audit gating and compliance stamp ARE in scope (MCP resolves a plan and emits audit). Out of scope: MCP and moderations org rate/burst/TPM limiting (GW17/GW32), and the per-org token budget lease (GW36).


## Assumptions

- The "45" equals the 46 `{"kind": "unsupported"}` entries in `firewall_v2_map.py` minus
  `org_token_budget` (documented GW36-owned). This is the only reading consistent with both the
  card text ("the 45 FirewallConfig fields `firewall_v2_map.py` marks unsupported") and the
  file's own GW36 note. If the owner intends the 45 to be counted differently, the enumeration
  above still names every field, so no field is lost under either reading.
- `output_pii_action`, `output_credential_action`, and `output_ip_leakage_action` are currently
  `mapped` (not among the 45 unsupported fields), so GW27 does not build them; it only keeps
  their `rewrite` option refused-at-save, consistent with the card's "REWRITE actions (now
  refused at save)" line and GW33 ownership.
- BUILD fields are published into the gateway through an extension of the signed Plan_Document /
  policy path (GW26), not through a side channel; the exact representation (new plan-body fields
  vs. policy rules) is a design decision for the next step, constrained by the structural
  acceptance criteria above.
