# Requirements — GW35 Console data, admin routes and Scan Detail truth

**Status:** DRAFT for design. Requirements only; no product code is changed by this document.
**Card:** GW35 (Phase Control; depends on GW14b, GW29; required by GW21, GW23). Runbook `AI_MESH_MASTER_RUNBOOK_v3.1_CLEAN.docx` is authoritative; contract source is the GW14b section of `AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`.
**Branch / worktree:** `gw35-console-truth` at `.worktrees/gw35`, based on `suraj-revamp`.
**Scope boundary:** local half only. The live Docker/staging gate (L35-1, L35-2 on a real stack) and human browser sign-off are NOT run or claimed here. They stay for the human.

## Introduction

GW35 closes the gap between what the v2 gateway does and what the console says it did. Four things are wrong today, each confirmed in code on this branch:

1. `gateway_v2/gateway_v2/edge/console_projection.py::chat_console` writes `_stage("auth", "allow", True, "")` and `_stage("rate_limit", "allow", True, "")` as literals. Every trace shows both as ran-and-allowed, whatever happened.
2. `console_from_holds` returns `None` unless `holds.input_decision` is set. `RequestHolds.note_input` is called only after identity, plan, admission, budget and scan (`edge/openai_chat.py` ~L296). A refusal at identity (401/503), plan, admission (shed) or budget/rate limit therefore carries no `zeroshield`/`pipeline_trace`. The console then falls back to the nine-stage template because `liveGateway.js` requires `stages.length >= 6`.
3. `openai_nonstream._console_for` sets `output_action = "block"` when `refuse_code` is set and the provider was dispatched. An upstream failure (`upstream_error`, `upstream_rate_limited`, `stream_malformed_upstream`, timeout) is therefore painted `output_guardrail: block`. `audit/console_schema.py` has `_ACTIONS = {allow, flag, redact, block}` and `_STAGE_ACTIONS = _ACTIONS | {skip}`; there is no `error`. The streaming path has the same shape (`edge/routes.py` ~L601 sets `stats.output_action = "block"` on a non-allow kill).
4. `audit_emit._records` writes an `admission` record only when `holds.org_id` is set (`emit_chat_audit` returns early on `not holds.org_id`). `holds.note_tenant` is called only after identity resolves, so a failed credential (`invalid_api_key`, 401) produces no audit record and no console row. Progress file lists this as "Failed-credential audit records (GW35)".

A fifth class of defect is page-level: some pages read fields v2 does not produce, and six admin-ledger rows retire features (breaker, bedrock-test, db-test, MCP oauth/start) that the console still calls.

## Glossary

- **Stage set:** the ordered six stages v2 actually runs: `auth`, `rate_limit`, `input_scan`, `model_routing`, `model_output`, `output_guardrail`. Defined by `_STAGES` in `console_projection.py`.
- **Dropped stages:** the three v1 stages v2 does not run: `kill_switch`, `policy`, `model_input`. See Decision D-02 (the GW14b plan names `circuit_breaker` instead of `model_input`; that is the disagreement V-16 records).
- **Refusal:** any HTTP/SSE response where the gateway did not return provider output as success.
- **Pre-scan refusal:** a refusal that happens before `holds.note_input` (parse excluded, see D-06): pre-checks, identity, plan, admission, budget.
- **Upstream failure:** the provider was dispatched (`holds.dispatched`) and failed or returned something the gateway could not use. Not a policy decision.
- **Stage action:** value of `stages[].action`. **Final action:** `pipeline_trace.final_action` and `zeroshield.action`.
- **Console inventory:** `docs/plans/evidence/2026-10-26-gw14b/{frontend-verifier,cp-consumers,config-keys,admin-ledger}`, checked by `python -m lint.check_console_inventory` and `gateway_v2/tests/lint/test_console_inventory.py`.

## Requirements

### Requirement 1 — Auth and rate_limit report the real outcome (defect 1)

**User story:** As a console operator reading Scan Detail or the Attack Simulator, I want the `auth` and `rate_limit` stages to show what happened to my request, so that a refusal there is visible and an allow there is real.

**Acceptance criteria**

1.1 `chat_console` takes the auth outcome and the rate-limit outcome as inputs. No call site passes a literal `allow`. No string literal `"allow"` is the second argument of `_stage("auth", …)` or `_stage("rate_limit", …)` in `console_projection.py` (AST test).
1.2 Auth outcomes: identity resolved → `auth: allow, ran: true`. `invalid_api_key` (401: missing, unknown, revoked or expired key) → `auth: block, ran: true, deciding_rule: "invalid_api_key"`. Identity could not be decided (`shared_state_unavailable`, 503) → `auth: error, ran: true, deciding_rule: "shared_state_unavailable"`.
1.3 Rate-limit outcomes: GCRA and budget passed → `rate_limit: allow, ran: true`. `rate_limited` (429) → `rate_limit: block, ran: true, deciding_rule: "rate_limited"`. `overload_shed` (503, admission) → `rate_limit: block, ran: true, deciding_rule: "overload_shed"` (D-03). `budget_unavailable` (503) → `rate_limit: error, ran: true, deciding_rule: "budget_unavailable"`.
1.4 A stage after the refusing stage is `action: skip, ran: false`, with no `deciding_rule`. `final_action` is the refusing stage's action (`block` or `error`).
1.5 The stage is `ran: false, action: skip` only when the stage truly did not run. A stage that ran and allowed is never `skip` (L14b-2 honesty rule, PIPELINE-0035).
1.6 `ran` is derived from the request-lifecycle facts recorded on `RequestHolds` (new per-request fields; slotted, no module state). It is not derived from latency or from whether another stage has a decision.
1.7 Property test (≥ 100 iterations, tagged in source, e.g. `# Feature: gw35-console-truth, Property 1`): for every refusal code the lifecycle can emit before the provider, the generated trace has exactly the stage set of 1.4, the refusing stage is the first non-allow stage, and every later stage is `skip`/`ran:false`.

### Requirement 2 — Pre-scan refusals still produce a trace (defect 1, V-16)

**User story:** As an operator, I want a revoked key or a burst-limit refusal to appear on Scan Detail as a six-stage trace, so that the console does not fall back to the nine-stage template.

**Acceptance criteria**

2.1 Every refusal after body parse and before `note_input` returns a body (HTTP) or a trailing `amf.trace` frame (SSE) with `zeroshield` and `pipeline_trace`, built through the same `chat_console` function. Codes covered: `invalid_api_key`, `shared_state_unavailable`, `plan_unavailable`, `overload_shed`, `rate_limited`, `budget_unavailable`.
2.2 `console_from_holds` no longer returns `None` solely because `input_decision` is unset. It returns `None` only when no request facts at all were recorded (parse/pre-check refusals, D-06).
2.3 A pre-scan trace has `input_scan: skip, ran: false`. `plan_version` is omitted when no plan was pinned (identity refused). It is never fabricated (D-04). The schema accepts the omission only when `input_scan.ran` is false.
2.4 `plan_unavailable` (plan could not be resolved after identity) follows D-05: `auth: allow, ran: true`, `rate_limit: skip, ran: false`, `input_scan: error, ran: false, deciding_rule: "plan_unavailable"` (the scan could not start), later stages `skip`. `ran: false` with `action: error` is the one permitted combination of those two values, and the validator pins it.
2.5 The frontend renders a six-stage array of any mix of actions without falling to the nine-stage template. The `>= 6` threshold in `liveGateway.js` is not lowered (L14b-2 note).
2.6 HTTP status, error envelope and `x-request-id` for each refused code are byte-identical to today. Only `zeroshield` and `pipeline_trace` are added. A regression test pins this for each code.
2.7 SDK-visible behaviour is unchanged: a 401 stays plain HTTP even when `stream: true` (GW36 S3, N11). The trace for a streamed identity refusal is therefore carried in the HTTP error body, not an SSE frame.
2.8 `X-ZeroShield-Action` is set on pre-scan refusals from the final action.

### Requirement 3 — An honest `error` action end to end (defect 2)

**User story:** As an operator, I want an upstream failure to show as an error at the model stage, so that I do not read a provider outage as a firewall output block.

**Acceptance criteria**

3.1 **Gateway schema.** `console_schema._STAGE_ACTIONS` and the final-action set include `error`. `_ACTIONS` is split into two named sets: the four decision dispositions (`allow, flag, redact, block`) and the console vocabulary (dispositions plus `error`). `domain.decision.Disposition` is NOT extended: `error` is a console/outcome word, not a policy disposition (D-01).
3.2 **Upstream failure mapping (non-stream).** When the provider was dispatched and the refuse code is an upstream-failure code (`upstream_error`, `upstream_rate_limited`, `stream_malformed_upstream`, provider timeout, size-bound exceeded, and any code from `dispatch/provider_errors.py` mapped by `edge/errors.py`), the trace is: `model_routing: allow, ran`; `model_output: error, ran: true, deciding_rule: <code>`; `output_guardrail: skip, ran: false`; `final_action: error`. `output_guardrail` is `block` only when the output resolver actually returned BLOCK (`refuse_code == output_blocked` or `output_decision.disposition == block`).
3.3 **Streaming.** A stream that fails upstream before or after first byte, or is cut by timeout, shows the same stage mapping in the `amf.trace` frame. The `stats.output_action = "block"` assignment at `routes.py` ~L601 is replaced by an outcome that distinguishes kill-for-policy (`block`) from upstream/transport failure (`error`) (D-07). Kill reasons that are policy (`output_blocked`, key revoked, kill switch) stay `block`; `stream_malformed_upstream`, timeout, provider abort and disconnect-on-error are `error`. A client disconnect with no error is not an error and is not shown as `block`.
3.4 **Audit.** The output-phase audit record's `outcome` for an upstream failure is not `block`. The record is stamped with reason = the refuse code. The existing `AuditRecord` outcomes are checked; if none fits, the reason travels in `detail` and `outcome` takes the existing `rejected` value (D-08). No new audit outcome enum value is added without a schema version bump.
3.5 **Control drain.** `control/ai_mesh_control/core/v2_audit_adapter.py::_RANK` accepts `error`. `merge_row` ranks `error` equal to `block` for "stricter wins" only against `allow`/`flag`/`redact`. Between `error` and `block`, `block` wins (a policy refusal is the more informative truth). A record with an unknown action string is rejected as `RejectedV2("action")` rather than stored with an unranked action (today `_RANK.get(..., -1)` silently ranks it lowest).
3.6 **Control API/model.** `EnforcementEvent.action` is a free `CharField(128)`; no migration is needed. Any serializer, filter, SOC total or chart query that enumerates actions is reviewed and `error` is added where it enumerates. `error` events are excluded from "threats blocked" totals and counted separately (D-09). The `gateway_v2.total_threats` figure does not count `error`.
3.7 **Frontend.** `error` is already in `_STAGE_SEVERITY`/`_STAGE_SCORE` in `pipelineTrace.js`. Required: `STAGE_LABELS`/stage theme in `StageTimeline.jsx` and the action badge in `LogDetailPage.jsx`, the threat feed, the live gateway result mapper (`inferFinalAction`, `blockingStageName`) and the Attack Simulator result card each render `error` with their own colour and label ("Upstream error"), never as BLOCK, and never as "No threat detected" (PIPELINE-0032 rule retained). Tests for every function above.
3.8 **Final action.** `error` is never produced when a policy disposition was decided and the provider was not the cause. A request that both had an input BLOCK and no provider call is `block`.
3.9 Property test (≥ 100 iterations): for every (input_disposition, provider_outcome ∈ {ok, upstream_failure}, output_disposition) combination, `final_action == error` iff the provider failed and no stricter policy action was decided, and `output_guardrail` is never `block` unless the output resolver returned BLOCK.
3.10 HTTP status and envelope for upstream failures are unchanged.

### Requirement 4 — Failed-credential audit records (defect 3)

**User story:** As a security operator, I want a rejected key to leave an audit row, so that probing and revoked-key use are visible.

**Acceptance criteria**

4.1 An `invalid_api_key` refusal emits one admission-phase audit record, outcome `rejected`, reason `invalid_api_key`, status 401, with the console projection of R1/R2.
4.2 The raw credential is never written, logged or put in a metric label. The record carries a fingerprint: the first 12 hex characters of the existing `hashed_bearer_key` SHA-256 (the hash identity already computes). A test asserts the raw bearer token string is absent from every serialised record, log line and metric exposition for a failed request.
4.3 Attribution (D-10): if the identity resolver knows the org that owned a revoked or expired key, that org's id is used. If it does not (unknown key, missing header), the record is written to the reserved unattributed stream `{rv2}:audit:_unauthenticated`. No tenant is ever guessed.
4.4 The control drain reads `_unauthenticated` only when `AMF_V2_AUDIT_ORG_MAP` maps `_unauthenticated=<slug>`. Unmapped remains "not written" (existing behaviour). The dev profile mapping used for L35-2 is documented.
4.5 `shared_state_unavailable` (503, identity doubt) is also recorded (reason `shared_state_unavailable`, `auth: error`), because the key may be valid. It is attributed only if an org is known, otherwise to `_unauthenticated`.
4.6 **Flood bound.** Unauthenticated records are bounded by a per-process rate derived from `ResourceContract` (`event_log_rate` family, a new derivation in `runtime/resources.py` if none fits). No capacity literal appears anywhere else. A suppressed record increments a counted drop metric (reason `unauthenticated_rate_limited`). The 401 response is never delayed or changed by audit saturation.
4.7 `emit_chat_audit` and its observer still never raise and never alter the response (existing contract). A broken sink callback does not change the 401.
4.8 A request that fails at pre-check or parse (R2 D-06) is out of this requirement.
4.9 `/metrics` counts failed-credential records and drops with label-free series (GW14d rule: no tenant-derived label).

### Requirement 5 — No invented stages (defect 4)

**Acceptance criteria**

5.1 `_STAGES` stays exactly the six names. `kill_switch`, `policy` and `model_input` never appear in a gateway trace, in the schema `_STAGE_KEYS` values, or in any frontend path that fabricates them from v2 data.
5.2 `validate_console` rejects a stage name outside the six (today `_validate_stage` only checks non-empty). Unit test: a stage named `kill_switch` fails validation.
5.3 The frontend never synthesises a kill-switch, policy or model-input stage when the trace carries `source: gateway_v2` (or six stages). Existing nine-stage rendering for v1 events is unchanged and is selected only by `source != gateway_v2`, not by stage count alone.
5.4 Record kept: a policy BLOCK on input still shows its stage (`input_scan`) and `deciding_rule`. (Runbook V-16: "A policy block still shows its stage and deciding rule".)
5.5 The six-stage set is written down once (D-02). The GW14b plan text and the runbook name the same dropped stages: `kill_switch`, `policy`, `model_input`. A doc test greps the repo docs listed in D-02 for the stale `circuit_breaker` dropped-stage wording.
5.6 The written decision is marked "proposed, awaiting owner signature" (runbook §00.7 decision 12). This card does not sign it on the owner's behalf.

### Requirement 6 — Pages read only fields v2 produces (defect 5)

**Acceptance criteria**

6.1 Every frontend read of a gateway-produced field is either (a) in the GW14b v2 contract (`zeroshield.{action,request_id}`, `pipeline_trace.{final_action,plan_version,stages[].{name,action,ran,deciding_rule}}`, headers, the fields listed in `console_schema.py`), (b) guarded by a `source !== "gateway_v2"` branch, or (c) listed in the console-inventory ledger as v1-only.
6.2 Pages that show a field v2 does not produce show "not reported by gateway v2" (or omit the control) for `gateway_v2` rows. They do not show `0`, `—`-as-zero, "No threat detected", or a default. Examples to audit: latency, TTFT and Duration (`pipeline_trace.total_latency_ms`), `routing` decision card, `guard_summary`, `scan_outcome`, token counts and risk score. A v2 row has no risk score (the adapter does not write one); the UI shows none.
6.3 The audit list in the design is produced by running the inventory generator and diffing against the contract, not by reading by hand. The output is attached to the design.
6.4 `python -m lint.check_console_inventory --write` regenerates `frontend-verifier/fields`, `cp-consumers`, `config-keys` and `admin-ledger/routes.json`. The regenerated diff is reviewed: every added/removed read is explained in the design. `test_console_inventory.py` is green with the new ledgers. A diff that silently adds a v1-only read as allowed is a defect.
6.5 The `cp-consumers` ledger records the new control translation of `error` (R3.5) and the unattributed stream (R4.3) as consumers.
6.6 The inventory lint gains a check that the frontend vocabulary of stage actions equals the gateway's console vocabulary (`allow, flag, redact, block, error, skip`). The check fails when either side adds a value alone.

### Requirement 7 — Admin routes the console still calls (runbook GW35 work item, V-06)

**Acceptance criteria**

7.1 Of the 26 `admin-ledger/routes.json` rows, the console callers of `retire` rows are enumerated by the lint (breaker reset/state/trigger via `CircuitBreakerSimulator`/`IsolationOpsSimulator`, `BedrockTestPanel` bedrock-test, db-test, MCP discover/call/oauth start).
7.2 For each retired row with a console caller, the control proxy no longer forwards to the v1 gateway when the target is gateway v2. It returns an explicit, declared "retired on gateway v2" response (HTTP 410 with a stable code `retired_on_gateway_v2`), never a 200 and never a success stub (GW14b rule).
7.3 Each caller component renders the retired state as a labelled "Retired on gateway v2" panel with the ledger reason. It does not render a broken control, an empty chart or a spinner. `bedrock-test` is retired by runbook §1 with a console change; this requirement is that change.
7.4 `/v1/usage`, `/v1/observability` and `/v1/policy/check` (used by scripts and the demo, not by the console; runbook V-06) are decided and recorded: D-11 (keep as not-served, record as ledger rows, no behaviour change). A ledger row is added for each.
7.5 The threat feed continues to list `gateway_v2` rows (existing behaviour kept) and is tested with an `error` row and a pre-scan refusal row.
7.6 Ledger rows needing a signed owner decision carry `signed: null` and `decision_ref` until the owner signs. The lint reports them as "pending signature" and exits 0 locally. A `--require-signed` flag exits non-zero, for use by the human cutover gate (GW21/GW23). This card does not forge a signature.

### Requirement 8 — Gates and non-functional constraints

8.1 Layering holds: new code obeys the import-linter contract (edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain). `console_projection` stays in `edge`. The control app does not import `gateway_v2`.
8.2 No capacity literal outside `runtime/resources.py` (`check_capacity_literals`). No module-level mutable state (`check_no_module_mutable`). New value types are `@dataclass(frozen=True, slots=True)` (`check_frozen_dataclasses`). `check_http_outside_edge_resolve` and `check_tenant_scale` stay clean.
8.3 Fail-closed: any exception while building the console object leaves the HTTP status/body of the underlying refusal unchanged and logs once at WARNING. It never turns a refusal into a success and never raises to the client. (The projection is additive.)
8.4 `Finding.evidence` and the raw prompt, output and credentials stay out of every new field (`_reject_evidence` extended to check the new fingerprint is not a full hash).
8.5 Offline gates, all green on the final commit: the full offline pytest run (container-run style as in the progress file), `mypy --strict`, `ruff`, `import-linter`, the five AST gates over the trees they cover, `python -m lint.check_console_inventory`, and for the frontend `npm run lint`, `npm run test:unit`, `npm run build` (node:22-alpine container if the host lacks node). Each gate's result is recorded in the evidence file with counts. A pre-existing failure is recorded as such with its baseline, not hidden.
8.6 Property tests have ≥ 100 iterations (house idiom: seeded `random.Random`, no hypothesis), are tagged in source `# Feature: gw35-console-truth, Property N: <text>`, and each requirement above that says "property test" has one.
8.7 Negative controls: for R1, R3 and R4 a test reverts the fix in a scratch copy of the module or monkeypatches the old behaviour and shows the test fails (as the progress file does for N18/N19).
8.8 Backward compatibility: v1 events (no `source`) and the v1 drain `_build_enforcement_metadata` in `core/tasks.py` are untouched. Existing v1 frontend tests pass unchanged.
8.9 The stream-time cost of the projection is bounded: building the trace is O(stages) with no extra I/O on the request path. An existing latency test or micro-benchmark is run before/after and the delta recorded (SLO-sensitive hot path).

## Live checks (card L-checks)

The runbook card lists two checks plus the cross-card browser sweep. They are NOT run in this card. This table is what the human runs, plus the local analogue that this card must supply.

| ID | Runbook procedure | PASS (runbook) | Local analogue required by this card |
|---|---|---|---|
| L35-1 | Playwright on the production bundle, every page (also L14b-3). | Works or is retired with sign-off. | Frontend unit tests for each page's v2 data path; production `npm run build` succeeds; ledger lint green. Browser run left to the human. |
| L35-2 | Revoked key, then burst limit. | Scan Detail shows the refusal at auth, then at rate_limit. | In-process ASGI tests: revoked key → 401, trace `auth: block`, audit record present; burst past GCRA → 429, trace `rate_limit: block`, `auth: allow`. Control adapter test turns each record into the right EnforcementEvent row. |
| L14b-2 (re-check) | Trace has the six stages that ran; dropped stages are not drawn as empty allows. | Six stages, none invented. | R1, R2, R5 tests. |
| L14b-3 (re-check) | Browser walk of every page. | No page breaks on v2 data. | R6, R7 tests. Browser run left to the human. |

## Defect coverage matrix

| # | Defect from the brief | Requirements |
|---|---|---|
| 1 | Auth and Rate Limit hard-coded ALLOW | R1, R2 |
| 2 | No `error` action; upstream failure shows `output_guardrail: block` | R3 |
| 3 | Failed-credential audit records | R4 |
| 4 | Do not invent kill-switch, policy or model-input stages | R5 |
| 5 | Pages read fields v2 does not produce; reconcile to GW14b; regenerate ledgers | R6, R7 |

## Recorded conservative decisions

- **D-01 `error` is a console word, not a `Disposition`.** Adding it to `domain.decision.Disposition` would put "error" into `most_restrictive`, policy tables and every resolver. It is added only to the console vocabulary and the audit adapter. Ordering: `block` > `error` > `redact` > `flag` > `allow` for the "stricter wins" merge.
- **D-02 The dropped stages are `kill_switch`, `policy`, `model_input`.** This is the v3.1 runbook wording (card GW35, V-16). The GW14b plan says `circuit_breaker`; that wording is corrected in the repo docs. The correction is marked "proposed, awaiting owner signature", not signed.
- **D-03 Overload shed is shown on `rate_limit`.** Admission (CoDel shed) and quota (GCRA, budget) are one pre-model gate to the operator. A new stage name would break the six-stage contract. The `deciding_rule` (`overload_shed` vs `rate_limited`) distinguishes them.
- **D-04 Omit, never fabricate.** `plan_version` is omitted when no plan was pinned. `deciding_rule` is omitted for allow/skip. Matches the adapter rule "a v2 record that lacks a fact omits the fact".
- **D-05 `plan_unavailable` maps to `input_scan: error, ran: false`.** The scan could not start. `ran: false` + `error` is the single allowed pair; the validator pins it.
- **D-06 Parse and pre-check refusals (`request_pre_check_exceeded`, malformed body, boundary refusals) keep no trace.** Identity is not yet resolved, no tenant exists to attribute, and the runbook calls out "a refusal before the input scan" only for the scan-path. Recorded as a known gap, listed under Out of scope. (Conservative: attributing an unparsed body is riskier than leaving it to `/metrics` counters.)
- **D-07 Stream kill reasons split by cause.** Policy kills stay `block`; transport/upstream failures are `error`; a client disconnect with no error is neither.
- **D-08 No new audit `outcome` value.** Reuse `rejected` with the reason in `detail`. Avoids a `schema_version` bump and a control re-ingest of old rows.
- **D-09 `error` rows are not threats.** They appear in the feed with their own badge and are excluded from "threats blocked" and `gateway_v2.total_threats`.
- **D-10 Unattributed failed credentials go to a reserved stream, drained only when mapped.** No tenant guess; existing "unmapped org is not written" stays the default.
- **D-11 `/v1/usage`, `/v1/observability`, `/v1/policy/check`.** Recorded as ledger rows with decision `retire` (not served by v2), owner `gw35`, signature pending. No new route is built.
- **D-12 Retired-route response is HTTP 410 `retired_on_gateway_v2`.** Not 501 (the SDK retries 501 per V-15; these are console routes, but a declared non-retryable code is the safe choice) and not 404 (ambiguous with a typo).
- **D-13 Fingerprint length 12 hex.** Enough to group repeat probes, too short to be a usable credential hash. Chosen without a measurement; the design may adjust it.
- **D-14 The `ran` flag is set from lifecycle facts, not time.** Matches PIPELINE-0035.

## Out of scope

- Running or claiming the live Docker/staging gate, the Playwright production-bundle sweep (L35-1, L14b-3 browser parts) and human sign-off. Owner signatures on the six-stage set and the retired-route ledger rows.
- Building v2 equivalents for breaker, bedrock-test, db-test or MCP OAuth; wiring kill-switch, policy or model-input stages into v2; GW17b OAuth server work; GW28 routing catalogue; GW18 RAG/vector (stay HTTP 501).
- Traces for parse, pre-check and boundary refusals (D-06); a streamed refusal before first byte remaining an SSE error frame (GW34 / S5).
- Changing HTTP statuses, error envelopes, Retry-After, SDK-visible behaviour or the OpenAI wire contract.
- Control-plane v1 pipeline, v1 `telemetry:events` list, the v1 drain defaults for v1 events.
- Cutover-time shadow/side-by-side display (GW21), real OS supervision, capacity numbers, `Disposition` changes, new policy rules.
- Known and untouched: `state_control/valkey.py:100` `check_tenant_scale` finding, "coroutine `BudgetLease._refill` was never awaited" warning.

## Assumptions

- The frontend lives in `frontend/` (React + Vite, `npm run test:unit`, `npm run lint`, `npm run build`).
- `httpStatus` and `refuse_code` are available to `console_projection` at all pre-scan refusal sites through `RequestHolds`; the design chooses the exact fields.
- `EnforcementEvent.action` is unconstrained at the DB layer (`CharField(max_length=128)`), so no migration is needed. Design confirms by reading the serializers.
- The Django test suite may not be runnable on the host venv (progress file notes no Django); control tests run in the container, and the final evidence states which ran.
