# Requirements — Routing isolation honesty

**Status:** DRAFT — awaiting operator approval before any product-code change.

**Cell:** GCP Mesh (`aimeshfirewall.zeroshield.ai` / `aimeshgateway.zeroshield.ai`). Not GuardX / AWS.

**Locked 2026-09-14** from the ten product questions. Implementation MUST NOT start until this spec (requirements + design + tasks) is approved.

## Introduction

On the public Attack Simulator (Module 1.1), with **Routing Governance → Dynamic Routing = Enabled** (Module 1.5), operators still see a **product lie** and/or a **dead isolation alias**. These are **two request shapes** (Devil’s Advocate: they are not always one HTTP body):

**Shape A — request pin (PIPELINE-0028 Attack Simulator default).** Module 1.1 sends `enable_routing: false`. Gateway sets `routing_enabled=false` while `org_routing_enabled=true`. Copy: *“Org routing is off — the gateway used {model} directly without running routing policy.”* Timeline may paint `model_routing` SKIPPED because `honestStageAction` treats `action=allow` + `latency_ms===0` as skip (`pipelineTrace.js`). This spec **reverses PIPELINE-0028 for Attack Simulator only**; SDK pin stays.

**Shape B — kill-switch / circuit-breaker isolation.** `isolation_reroute_locked` skips `select_model`. Envelope uses `decision_source=kill_switch` (not `routing_disabled`) when audit is present. Live `mistral-nemo-cheap → claude-haiku-cheap` then LiteLLM **provider HTTP 404** as `upstream_error` — not an isolation decision. Attack Simulator may also mislabel that 502/404 as missing Model Connections credentials.

Sensitive Data Leakage (already-masked PII) follows PIPELINE-0012 (`redact_noop`) then dies on Shape B’s dead alias instead of completing on a Callable model.

This is a **system product failure**: 1.5 Enabled does not mean 1.1 runs routing policy, and isolation can select a catalog **name** the provider cannot serve.

This spec covers the durable fix: honest routing when Dynamic Routing is Enabled, kill-switch as a **candidate constraint** (never a silent skip and never a silent provider 404), Attack Simulator treating the selected model as a **preference hint**, save-time rejection of uncallable reroute targets, live GCP kill-switch cleanup, already-masked PII still completing on a valid model, public E2E proof, and a **full MIG bounce** after tests.

It does **not** change JWT session handling, analytics 503 `analytics_busy`, OpenAI `gpt-5.2` provider 401 (Model Connections credentials), or PIPELINE-0012 (already smart-masked PII must not hard-block).

## Glossary

- **Dynamic Routing / Org routing:** Organization `routing_enabled` from Routing Governance (1.5). When true, the org intends deterministic weighted routing for `/v1/chat/completions`.
- **Request pin:** Client `routing_preferences.enable_routing: false`. Documented SDK escape hatch to pin `model`. Distinct from org routing being off.
- **Attack Simulator:** Module 1.1 live `/v1/chat/completions` UI (`AttackSimulatorPanel.jsx`), including single-shot, scan-only fallback, stream, and burst.
- **Preference hint:** `enable_routing: true` plus `preferred_model` = the dropdown model. Gateway MAY select a better eligible model.
- **Kill-switch (KS):** Redis `kill_switch:{org}:…` plus control `/api/kill-switches/` (1.6). Actions `disable` and `reroute` with `fallback_model`.
- **Isolation lock (today):** `isolation_reroute_locked` in `proxy_chat` after a KS/model-state reroute. Today this forces `routing_active = False` and **skips** `LLM_ROUTER.select_model`.
- **Callable model:** An org `LLMModelConfig` row that is `is_active`, has a non-empty LiteLLM `model_id`, has provider credentials configured (encrypted key or env var), is chat-capable, is not itself kill-switched or model-state isolated, and is not a platform guard model.
- **Provider 404/401:** LiteLLM/upstream status reused by `_sanitize_llm_error_response`. Must never be the operator-facing outcome of an isolation reroute to a dead alias.
- **Smart-mask / already-masked PII:** Prompts whose PII is already `***` masked (`email_smart_masked`, `ssn_smart_masked`, …). PIPELINE-0012: redact-forward / `redact_noop`, not terminal block.
- **Public E2E:** Login and Attack Simulator on `https://aimeshfirewall.zeroshield.ai/` against the bounced MIG, not only unit tests or this VM’s Vite `:8180`.

## Evidence (current behavior)

| ID | Fact | Proof |
|---|---|---|
| E1 | Attack Simulator always sends `enable_routing: false` for a named model. | `AttackSimulatorPanel.jsx` uses `pinnedModelRoutingPreferences` at single-shot, scan-only retry, and burst. Isolation simulator already uses `simulatorRoutingPreferences`. |
| E2 | Request pin sets `routing_enabled=false` even when `org_routing_enabled=true`. | `main.py` `_extract_chat_routing_preferences`: `routing_enabled = routing_override if set else org_routing_enabled`. |
| E3 | KS reroute skips weighted routing even if the client did not pin. | Second `routing_active` assignment: `and not isolation_reroute_locked`. |
| E4 | Pin skip envelope says `routing_disabled`. KS-with-audit uses `_build_isolation_reroute_metadata` (`kill_switch`), **not** `routing_disabled`. Do not compose E4 with E3 as one stamp. | `main.py` ~9862 vs ~9867. |
| E5 | UI copy attributes **pin** to **org** routing being off. | `routingExplain.js` for `routing_disabled`. |
| E9 | UI paints SKIPPED when gateway `action=allow` and latency rounds to 0. | `honestStageAction` in `pipelineTrace.js`. |
| E10 | Circuit breaker writes Redis `kill_switch:*` with ModelState fallback, no serializer Callable check, same `isolation_reroute_locked`. | `circuit_breaker._activate_kill_switch_trip`. |
| E6 | KS save only requires an **active catalog name**, not a working LiteLLM id. | `KillSwitchCreateSerializer` filters `LLMModelConfig.is_active`; live `claude-haiku-cheap` 404’d. |
| E7 | `ModelStateUpdateSerializer` does not require fallback to be an active connected model. | `control/.../serializers.py` validate() self-loop only. |
| E8 | Already-masked PII is allowed to the model (`redact_noop`); then inference used the dead KS alias. | Live `zs-8ed6fa2aa9e1`; PIPELINE-0012 tests. |

## Conflict resolution (locked)

Two answers looked contradictory. This spec **locks the synthesis**:

1. **Kill-switch target wins if callable** — isolation **removes** the isolated model from the candidate set. If the operator set `fallback_model` and it is Callable, the remaining set is constrained to that target (hard filter). If it is not Callable, **fail closed** (`503`, stable code, no LiteLLM call).
2. **`model_routing` is not skip when Org Dynamic Routing is Enabled and the client did not SDK-pin** — the adjudicator still **runs** on the remaining set. If isolation hard-filters to one Callable fallback, `candidate_count` MAY be 1; that is still not `routing_disabled` and not UI SKIPPED via 0ms. Isolation is a constraint, not org-off copy.
3. **SDK pin remains** — a **non-simulator** client may send `enable_routing: false`. Attack Simulator MUST NOT. Copy MUST NOT say org routing is off when `org_routing_enabled` is true.
4. **PII** — primary “don’t forward raw PII” plus specific path: already-masked traffic is redacted (including honest `redact_noop`) **then inference completes on a Callable model**. Do not regress PIPELINE-0012 into a hard block on smart-masks.

## Requirements

### Requirement 1: Attack Simulator honours Dynamic Routing

**User Story:** As an operator with Dynamic Routing Enabled, when I run Attack Simulator I want the gateway to run routing policy, using my dropdown model only as a preference.

#### Acceptance Criteria

1. WHEN org `routing_enabled` is true, THE Attack Simulator (single-shot, stream, scan-only retry, and burst) SHALL send `routing_preferences.enable_routing: true` and MAY send `preferred_model` equal to the selected connected model.
2. WHEN org `routing_enabled` is false, THE Attack Simulator SHALL pin (`enable_routing: false`) so SDK-off and UI-off match.
3. THE Attack Simulator SHALL obtain org routing from `useFirewallConfig.jsx` (`routing_enabled`). WHILE that config is loading or the GET failed, THE panel SHALL omit `routing_preferences.enable_routing` (gateway uses org RAM) **or** wait; it SHALL NOT default `?? true` and SHALL NOT guess org-off.
4. THE Attack Simulator SHALL NOT call `pinnedModelRoutingPreferences` for those chat paths. This **reverses PIPELINE-0028 for Module 1.1 only**. Cheapest-eligible overwrite of the dropdown is accepted (preference hint).
5. Isolation Ops SHALL keep `simulatorRoutingPreferences` (no regression).
6. Unit tests SHALL cover org-on → `{ enable_routing: true, preferred_model }` and org-off → pin.
7. `gatewayChatFetch.rewriteChatBodyModelAuto` SHALL NOT retry around an isolation / kill-switch failure (including 404 `model_not_configured` after KS).

### Requirement 2: Org-on means `model_routing` runs

**User Story:** As an operator, I never want `model_routing` skipped solely because a kill-switch fired, if Dynamic Routing is Enabled.

#### Acceptance Criteria

1. WHEN `org_routing_enabled` is true AND the request is not an explicit SDK pin (`enable_routing` is not `false`), THE gateway SHALL set routing active and SHALL call `LLM_ROUTER.select_model` (or equivalent) on the **remaining** eligible chat models.
2. THE gateway SHALL NOT use `isolation_reroute_locked` (kill-switch, **model-state, or circuit-breaker**) to skip the adjudicator when Requirement 2.1 holds.
3. WHEN routing runs, THE gateway `model_routing` stage SHALL have `action` other than `skip` (typically `allow` / `reroute` / `confirm`). THE frontend SHALL NOT map `allow` + `latency_ms === 0` to skip for `model_routing` (fix `honestStageAction` or never round that stage to 0.0).
3b. Isolation SHALL NOT call `resolve_compliant_fallback` `chain_next` / `catalog_scan` to pick a **different** model than the operator fallback. Uncallable fallback → 503, not silent substitute.
3c. `_drop_isolated_or_killed_candidates` SHALL fail closed for the isolated name on Redis/check error (not keep the killed candidate).
3d. WHEN `model` is `auto`/empty and org routing is on, THE gateway SHALL run `select_model` after dropping isolated models. Empty remaining → Req 2.5. `simulatorRoutingPreferences("auto")` returning null is OK (omit prefs; org RAM applies).
3e. WHEN `fallback_model` is `auto` or a routing sentinel, control SHALL 400; runtime SHALL treat it as uncallable (no catalog-scan chain).
4. WHEN a KS/model-state reroute constrained the set, THE stage and `zeroshield.routing` SHALL set `decision_source` to `kill_switch` or `model_state` (not `routing_disabled`), SHALL include `candidate_count` of the remaining set, and SHALL name `requested_model` vs `routed_model` honestly.
5. IF remaining callable models is empty, THEN THE gateway SHALL return **503** with a stable code (`kill_switch_active` or a new `isolation_target_uncallable`) **before** LiteLLM, with `pipeline_trace` present. It SHALL NOT return provider 404/401 as the product error.

### Requirement 3: Kill-switch target wins only if Callable

**User Story:** As an operator, isolation must land on a model that can actually complete, or refuse.

#### Acceptance Criteria

1. WHEN KS action is `reroute` and `fallback_model` is Callable and remains after dropping the isolated model, THE gateway SHALL serve that fallback (hard filter of the remaining set to that model) and SHALL still emit a real `model_routing` stage (Requirement 2).
2. WHEN fallback is missing, not in catalog, inactive, missing `model_id`, missing credentials, embedding-only, guard, itself killed/isolated, or LiteLLM would be invoked on an identity the gateway already knows is not in the org catalog, THE gateway SHALL fail closed (no upstream call).
3. WHEN LiteLLM/provider returns 404 or 401 for an **isolation-selected** model (KS, model-state, or circuit-breaker intent — not only leftover `isolation_reroute_locked`), THE gateway SHALL NOT surface `upstream_error` with the provider status as the primary product outcome. THE gateway SHALL fail closed with 503 and a stable isolation/target code, `pipeline_trace` present, `inferBlockedStage` mapping any new code (e.g. `isolation_target_uncallable`) to kill-switch/isolation — not `model_output`. JSON **and** pre-first-token stream (do not start SSE then 404). THE Attack Simulator SHALL NOT map that sanitized upstream error to “missing credentials.” Non-isolation `gpt-5.2` 401 remains out of scope (Req 9). No unscoped LiteLLM `_default_fallback_model` retry onto a killed model.
4. Existing fail-closed tests (fallback itself killed, model-state isolated, outside allowlist, embedding-only) SHALL remain green.

### Requirement 4: 1.6 / Model State reject uncallable reroute targets at save

**User Story:** As an operator, I cannot save a reroute to a label that is not an active connected chat model.

#### Acceptance Criteria

1. WHEN creating or updating a KillSwitch with `action=reroute`, THE control plane SHALL reject (HTTP 400) unless `fallback_model` matches an org `LLMModelConfig` with `is_active=true`, non-empty `model_id`, chat-capable, non-guard, and provider credentials configured (encrypted key or non-empty `api_key_env_var`).
2. THE same rule SHALL apply to `ModelIsolateSerializer` (already partially present) **and** `ModelStateUpdateSerializer` (gap today).
3. Self-loop, global scope, and guard-model rejections SHALL remain.
4. A catalog row that is active but has empty `model_id` or no credentials SHALL be rejected as not connected.
5. THE 1.6 UI SHALL show the serializer error (no silent save). A live provider probe at save time is **not** required; runtime Requirement 3.3 covers residual 404s.
6. Tests SHALL include: active+credentialed accepted; active+empty `model_id` rejected; inactive rejected; ModelState PATCH fallback-only rejected when uncallable.

### Requirement 5: Honest UI copy

**User Story:** As an operator with Dynamic Routing Enabled, I must never be told org routing is off.

#### Acceptance Criteria

1. WHEN `org_routing_enabled` is true, `summarizeRoutingDecision` SHALL NOT emit “Org routing is off”. `resolveRoutingDecision`, StageTimeline, Routing Audit, and Attack Simulator SHALL pass `org_routing_enabled` into the summarizer (not optional).
2. WHEN `org_routing_enabled` is true and the **request** pinned (`enable_routing: false` / `decision_source=routing_disabled`), THE copy SHALL state that **this request pinned** the model, and that org Dynamic Routing is Enabled.
3. WHEN `decision_source` is `kill_switch`, `model_state`, or `circuit_breaker`, THE copy SHALL describe isolation redirection. IF `candidate_count == 1` because of a hard filter, THE copy SHALL say the set was constrained to the isolation target, not “best of N weighted.”
4. Gateway skip-after-block on a **terminal 503 before routing** may still mark later stages skip; THE summarizer SHALL NOT say “routing policy selected” on empty `decision_source` skip stages.
5. StageTimeline detail / `skipDetail` SHALL not contradict the summarizer (no “governance disabled” next to “this request pinned”).

### Requirement 6: Already-masked PII still completes on a Callable model

**User Story:** As an operator running Sensitive Data Leakage (already-masked), I want redaction honesty and a completed inference, not a provider 404.

#### Acceptance Criteria

1. THE gateway SHALL preserve PIPELINE-0012: already smart-masked PII is redact-forward / `redact_noop`, not a false obfuscated-PII **block**.
2. AFTER input_scan, THE request SHALL continue to a Callable model chosen under Requirements 2–3 (not a dead isolation alias).
3. Public proof SHALL use a fixture that is PIPELINE-0012-eligible (already smart-masked) **and** does not trip a live CISO/block rule. HTTP 200 with completed inference is required **only** when no terminal block matches. A policy **403** with matched rule recorded is success vs isolation-404. The default LLM02 raw-PII preset is **not** that fixture unless it is proven not to 403. Scan-only / `max_tokens=0` / default burst-without-inference SHALL NOT count as Req 6 or routing-on proof.
4. Raw unmasked PII SHALL continue to redact or block per existing data-protection floor (PIPELINE-0032); this requirement does not weaken that floor.

### Requirement 7: Live GCP kill-switch cleanup

**User Story:** As the operator of live Mesh, the stale “Vendor incident” reroute must not keep breaking demos.

#### Acceptance Criteria

1. THE implementation work SHALL deactivate or delete the live zeroshield kill-switch that reroutes `mistral-nemo-cheap` → `claude-haiku-cheap` (incident over), via 1.6 API or admin, **before** public E2E of the happy routing path.
2. Evidence SHALL include GET list of active KS, Redis GET of `kill_switch:zeroshield:model:mistral-nemo-cheap` empty (or inactive), and ModelState fallback for that model not pointing at `claude-haiku-cheap`. Circuit-breaker Redis twins SHALL be inspected; do **not** blindly `--prune-orphans` CB-owned keys.
3. Cleanup SHALL NOT be “leave it and hope routing skips it.” Credential-scoped keys with the same pair SHALL be listed.

### Requirement 8: Tests, public E2E, and deploy

**User Story:** As the product owner, I only accept the fix with public proof and a full MIG bounce.

#### Acceptance Criteria

1. Gateway pytest covering: org-on + no pin → adjudicator runs; org-on + KS callable fallback → stage not skip, `decision_source=kill_switch`, `candidate_count>=1`, upstream model is fallback; org-on + KS uncallable fallback → 503, LiteLLM not called; org-on + request pin → pin honored, copy not “org off” (UI test).
2. Control tests for Requirement 4.
3. Frontend unit tests for Attack Simulator prefs + `routingExplain`.
4. **Mandatory handover proof:** public `https://aimeshfirewall.zeroshield.ai/` Attack Simulator **after Task 9 bounce** (Vite `:8180` is a dry run only). Logged in as demo admin, Dynamic Routing Enabled, inference-on completion whose response JSON shows weighted routing **or** honest isolation copy; gateway `model_routing` not skip; UI not SKIPPED via 0ms heuristic; HTTP not provider 404 to a dead alias; already-masked PII path per Req 6.3; **screenshots + response JSON** under `mcp-parallel/findings/routing-isolation-honesty/` (redact secrets/keys). Include one stream completion if the panel stream toggle is used.
5. Deploy constraint: **full MIG bounce** of the serving MIG (`mesh-firewall-mig-nws9`) after images are in GAR — not nginx-only. Record compose/image tags.
6. Pipeline changelog protocol IF gateway chat-pipeline files change: Ruflo `pipeline/changes`, `AGENTS.md` pointer, `.cursor/rules/pipeline-changelog.mdc`, `docs/pipeline/CHANGELOG.md` in the **same** commit when the user asks to commit.
7. Do not mark complete on unit tests alone.

### Requirement 9: Non-goals (must not regress)

1. Explicit SDK `enable_routing: false` SHALL still pin for non-simulator API clients (existing `test_s9_enable_routing_false_*`).
2. `kill_switch_enabled=false` SHALL still make KS inert.
3. Auth JWT / simulator key wipe fixes, analytics 503, and OpenAI key 401 are out of scope.
4. No change to MCP tool-result scanning.
5. Do not commit or print gateway API keys.

## Success (operator acceptance)

You will accept this as fixed when, on **public** Attack Simulator after the MIG bounce:

- 1.5 shows Dynamic Routing **Enabled**.
- A completion shows `model_routing` **not** skip, and copy does **not** say org routing is off.
- No provider 404 to `claude-haiku-cheap` (KS gone or fail-closed).
- Already-masked PII redacts then completes on a connected model.
- Evidence pack attached (JSON + screenshots).
