# Multi-model routing + /v1/models endpoint: How model param selects org models (LLMModelConfig, routing payload, weighted_fastpath), model aliases, and how Responses model field routes. Per-request routing/override and OpenAI model-listing compatibility.

## Current
/v1/models (gateway/ai_mesh_gateway/main.py:10410–10474): Lists org-visible models from LLM_ROUTER.get_model_list(), org-filtered by CONFIG_SYNC.get_model_routing(), deduped by id. Auth-gated (SOFT_AUTH at endpoint, but 401 if invalid key).

/v1/chat/completions model routing (main.py:3598–4120): 
- Extracts model from body.get("model") or falls back to litellm_default_model (llm_router.py:408–409)
- _build_kwargs (llm_router.py:461–520): Applies allowlist check (M-03 fix, line 470–493), resolves to active model (fallback chain per _resolve_runtime_model), then org-qualifies to {org}::{model} for LiteLLM routing key (H7 isolation, line 506–511)
- Model alias normalization: _normalize_model_alias (llm_router.py:382–383) maps legacy bedrock-gpt-oss-120b → bedrock-gpt-oss-120b; _MODEL_ALIAS_MAP (line 75–77)
- Reserved models: is_platform_model_name (platform_models.py:51–67) blocks zeroshield-model, Bedrock foundation IDs, legacy 120b from org inference
- Per-request compliant_fallback_chain (llm_router.py:528–533, 566–589): Non-standard OpenAI extension; injected by firewall pipeline for model-state reroutes (kill-switch / isolated models); retried on 4xx/timeout
- Allowlist enforcement: body["_inference_allowlist"] (main.py:6157) populated from org routing identity set; enforced in _build_kwargs (line 470–493)
- Routing preferences (main.py:1478–1624): Extract from body.routing_preferences, metadata, or direct body keys: enable_routing, preferred_model (body.model or "auto"), compliance_requirements, data_sensitivity, latency_budget_ms, weights (risk/cost/latency/priority) — all client-controllable
- Smart model selection (llm_router.py:1300–1500): score_candidates (1304–1357) ranks by weighted (risk/cost/latency/priority) metrics; select_model (1360–1369) deterministic weighted; Bedrock adjudicator (1400–1500+) for multi-candidate/high-risk/compliance-constrained requests; returns ModelSelection (model_name, model_id, score, reason, fallback_chain, decision_source: "weighted" | "weighted_fastpath" | adjudicator_model_id)
- LiteLLM routing: LLMRouter.acompletion (llm_router.py:535–645) disables auto-failover (H2, line 553), retries on 4xx/5xx via compliant_chain only (not global fallback_map)
- Org-scoped model isolation: model_state.py tracks per-org per-model status (active/isolated/degraded) in Redis (model_state:{org_slug}:{model_name}); check_model_state (line 43–126) enforces isolation action (block/reroute/alert)

Control plane (core/models.py:1500–1700):
- LLMModelConfig: model_name (user-facing), model_id (LiteLLM), provider, data_sensitivity_level, compliance_tags, cost_per_1k_{input,output}_tokens, latency_sla_ms, risk_score [0–1], routing_priority, rate_limit_rpm, is_active
- build_litellm_entry (1660–1676): Assembles LiteLLM model_list entry (model_name, provider, litellm_params with encrypted/env-var API keys)
- build_routing_payload (1678–1699): Exports routing metadata (all the above + api_key_set, api_key_env_var) — pushed to Redis (llm:model_configs:{org_slug}) on save signal
- Org-only inference mode (llm_router.py:194–206): Router starts empty, loads from Redis post-Control-Plane-sync

/v1/embeddings (main.py:7169–7450): Mirror chat model selection — defaults to text-embedding-3-small (llm_router.py:82), org-filters by routing, enforces allowlist, uses _embedding_fallbacks_for (custom fallback map, not global chat fallback)

OpenAI compatibility anchors:
- ZeroShieldResponse.v1 (docs/contracts/ZeroShieldResponse.v1.md): /v1/chat/completions adds top-level "zeroshield" field (client-safe subset of 40+ internal fields); stock OpenAI SDK tolerates extra fields
- Streaming: terminal trace frame (SSE chunk with empty choices + zeroshield) before [DONE] (stream_orchestration.py::stream_with_finalize line 420+)
- X-ZeroShield-* headers on routing output (Action, Routed-Model, Rerouted, Routing-Reason, etc.)

Model listing response (llm_router.py:1046–1067): Returns [{id, object, owned_by, model_id}] — model_id is NEW field NOT in OpenAI spec; id is user-facing model_name

## OpenAI Spec
OpenAI Chat Completions (chat/completions):
- Params: model (required, string), messages, temperature, top_p, max_tokens, stop, n, presence_penalty, frequency_penalty, tools, tool_choice, response_format, seed, stream, stream_options
- Response: ChatCompletion {id, object: "chat.completion", created, model, choices [{index, message {role, content}, finish_reason}], usage {prompt_tokens, completion_tokens, total_tokens}}

OpenAI Responses API (responses.create) — NOT IMPLEMENTED:
- POST /v1/responses, model (required), instructions (optional), input_text (optional), modalities (optional), tools (optional with file_search/web_search/function), response_format (optional, typed output schema), previous_response_id (optional, for continuation), metadata (optional)
- Streaming events: response.created, response.output_text.delta, response.completed (with usage) — typed event envelopes
- Response: Response {id, object: "response", created, model, instructions, input_text, tools, output_text, modalities, usage, previous_response_id (echo)}
- Status: OpenAI Responses is in beta (2024); typed input/output items are emerging standard

OpenAI Models API (models.list):
- GET /v1/models
- Response: ListModelsResponse {object: "list", data: [Model {id, object: "model", created, owned_by, permission: [...]}]}
- No "model_id" or routing metadata in stock OpenAI response

OpenAI Assistants/Agents API — NOT IMPLEMENTED:
- POST /v1/assistants (threads, runs, streaming), POST /v1/threads/{id}/runs, streaming SSE events
- Assistant {id, object: "assistant", created_at, name, model (required per run or per assistant), instructions, tools, metadata, temperature, top_p, max_prompt_tokens, max_completion_tokens}

OpenAI Usage API — PARTIAL (per-response tokens only):
- POST /v1/usage (querying historical usage); no cost/compliance/routing metadata
- Per-completion: usage {prompt_tokens, completion_tokens, total_tokens} only — no cost_usd, risk_score, or routing_decision_factors

Embeddings API (embeddings):
- POST /v1/embeddings, model (required), input (string or [strings]), encoding_format (optional), user (optional)
- Response: Embedding {object: "list", model, data: [{object: "embedding", embedding: [...], index}], usage {prompt_tokens, total_tokens}}
- No routing or model-selection parameters

## Reusable hooks
Firewall pipeline (stream_orchestration.py + main.py):
- enqueue_job (main.py:41): Telemetry emission hook — reuse for Responses/Assistants events
- _launch_chat_stream_response (main.py:2003): SSE orchestration — generalize to Responses/Assistants streaming
- stream_with_finalize (stream_orchestration.py:420): Terminal trace frame emission — reuse for all streaming endpoints
- _extract_chat_routing_preferences (main.py:1478): Routing param extraction — refactor to _extract_routing_preferences(*request_body) for reuse in Responses/Assistants
- smart_select_model (llm_router.py:1300): Model selection heuristic — reuse directly for Responses/Assistants (add response_format as routing constraint)
- _build_kwargs (llm_router.py:461): Request builder for LLM calls — extend to build_responses_kwargs, build_run_kwargs to handle Responses/Assistants params
- pipeline_trace.py + output_guard.py: Input/output scanning — reuse for Responses input_text, Assistants instructions, tool outputs
- check_model_state + record_risk_event (model_state.py): Real-time model isolation — reuse per-turn for Assistants runs
- CONFIG_SYNC.get_model_routing (config management): Org-scoped model allowlist — reuse for Responses/Assistants
- isolation_reroute context (main.py:1716): Kill-switch / model-state reroute — reuse as fallback mechanism for Responses/Assistants model selection failure

## Gaps
[
  {
    "title": "Responses API (/v1/responses) not exposed",
    "severity": "High",
    "detail": "OpenAI Responses API (POST /v1/responses, client.responses.create) is missing entirely. This API supports typed input/output items, previous_response_id state for continuations, file_search/web_search/function tools, typed streaming events (response.created, response.output_text.delta, response.completed), and streaming response format selection \u2014 all critical for agentic and retrieval workflows. The API is in OpenAI beta (2024) and represents the direction for structured multi-turn conversations with tools.",
    "files": "gateway/ai_mesh_gateway/main.py (no endpoint), control/ai_mesh_control/core/models.py (no response_format storage), shared/ai_mesh_shared/openai_request_normalizer.py (no Responses normalization)",
    "implementationApproach": "Create POST /v1/responses endpoint that reuses the firewall pipeline (input_scan \u2192 policy \u2192 tier1/tier2 \u2192 LLMRouter). (1) Normalize Responses request body (instructions, input_text, modalities, tools, response_format, previous_response_id, metadata). (2) Inject _inference_allowlist, _compliant_fallback_chain, routing_preferences from request into kwargs like chat/completions does (main.py:4006\u20136157). (3) Route model selection via smart_select_model (llm_router.py:1300+) with response_format as a routing constraint (e.g., prefer models that support JSON schema in response_format). (4) Call litellm.aresponses(**kwargs) or llm.responses.create if litellm supports it (verify litellm version). (5) Emit streaming events (response.created, output_text.delta, response.completed) via SSE with zeroshield trace frame. (6) Store response_format schema in LLMModelConfig if org-scoped Responses support is desired. Firewall policy engine may need new rules for input_text validation (like messages in chat) and output schema enforcement (like output_guard).",
    "effort": "L",
    "firewallRisk": "ENFORCEMENT: Responses API input_text and tool definitions bypass existing message content scanning (input_scan targets messages[*].content only). Mitigation: extend PII scanner + threat_intel gate to Responses input_text; validate tool definitions (URLs, function schemas) pre-routing. ISOLATION: previous_response_id continuation may leak model state across requests if not org-scoped in Redis; mitigation: key as response_id:{org_slug}:{response_id} in continuation cache. AUDIT: new event type 'response_created' and 'response_completed' must feed audit trail (StreamRunMetrics.record_guard_action equivalent)."
  },
  {
    "title": "Model override in request body not OpenAI-compatible",
    "severity": "High",
    "detail": "Stock OpenAI SDK does not support per-request model selection hints beyond the 'model' param. ZeroShield accepts non-standard fields in request body (routing_preferences.enable_routing, routing_preferences.model_risk_score, body.data_sensitivity, body.latency_budget_ms, body.compliance_requirements, body.enable_routing) that are not in OpenAI spec. A request from stock SDK with model='gpt-4o' is always routed deterministically; ZeroShield's adjudicator + weighted scoring is not discoverable by SDK users. Additionally, body._inference_allowlist and body._compliant_fallback_chain are injected by the gateway itself (not client), breaking the contract: clients cannot override fallback behavior.",
    "files": "gateway/ai_mesh_gateway/main.py:1478\u20131624 (_extract_chat_routing_preferences), llm_router.py:522\u2013527 (_pop_inference_allowlist), llm_router.py:528\u2013533 (_pop_compliant_fallback_chain)",
    "implementationApproach": "Standardize on OpenAI's 'model' field as the routing selector: (1) Do NOT accept non-standard routing_preferences in request body from clients; strip and ignore if present (for backward compat, log a deprecation warning). (2) Support per-request model override via 'model' param alone (not 'auto', not a sentinel value \u2014 just the model_name string). (3) Offer opt-in per-request routing override via X-ZeroShield-Routing or similar header (NOT body), carrying explicit model preferences or routing weights in base64-encoded JSON. (4) Remove _inference_allowlist/_compliant_fallback_chain injection from main.py; instead use auth_context.allowed_models (gateway API key allowlist) as the hard enforcement gate, and org-scoped kill-switch/model-state isolation as the fallback mechanism (both already enforced downstream). (5) Document in OpenAI-compat contract that 'model' is required and treated as user-facing model_name; the gateway routes internally to the underlying LiteLLM deployment via org-qualification, but the response.model echoes the requested model (not the internal routed-to model).",
    "effort": "M",
    "firewallRisk": "ISOLATION: Clients currently cannot specify which fallback model to use, so reroute chains are operator-controlled only (safe); removing _compliant_fallback_chain injection would preserve this. AUDIT: routing decision must remain in zeroshield trace even without per-request override (decision_source: 'user_requested_model' vs 'weighted' vs 'kill_switch'). ENFORCEMENT: auth_context.allowed_models is already enforced (M-03 fix line 470\u2013493); no regression."
  },
  {
    "title": "Model listing response includes non-standard 'model_id' field",
    "severity": "Medium",
    "detail": "GET /v1/models returns [{id, object, owned_by, model_id}]. The 'model_id' field (LiteLLM internal identifier like 'openai/gpt-4o-mini') is not in the OpenAI ListModelsResponse schema. Stock OpenAI SDK models.list() filters/accesses the 'id' field only; clients that iterate 'data' and access .model_id will get unexpected behavior if ZeroShield adds or removes this field.",
    "files": "gateway/ai_mesh_gateway/llm_router.py:1046\u20131067 (get_model_list returns model_id), gateway/ai_mesh_gateway/main.py:10410\u201310474 (_returns from LLM_ROUTER.get_model_list())",
    "implementationApproach": "(1) Remove 'model_id' from get_model_list() response (line 1058). (2) If internal tooling (operator UI, metrics) needs model_id, expose it via a separate admin endpoint (e.g., /api/models/ in control plane, not /v1/models). (3) If OpenAI SDK users ask for model metadata (cost, latency, risk score), define a ZeroShield-specific extension endpoint (/v1/models/details or /v1/zeroshield/models/routing) that includes model_id, routing metadata, and cost/risk/compliance info. (4) Document in ZeroShieldResponse.v1 that /v1/models conforms to OpenAI spec; any extensions are in separate response bodies.",
    "effort": "S",
    "firewallRisk": "None \u2014 this is a schema-compatibility issue, not a security/isolation surface."
  },
  {
    "title": "Model alias resolution not OpenAI-documented",
    "severity": "Medium",
    "detail": "bedrock-gpt-oss-120b is aliased to bedrock-gpt-oss-120b via _MODEL_ALIAS_MAP (llm_router.py:75\u201377) \u2014 but this is a legacy compatibility shim with no OpenAI parallel. Clients cannot know which aliases are valid for an org without hitting a 422 (model_not_configured). Additionally, _is_routing_sentinel_model (referenced in main.py:4113\u20134118) treats 'auto' as a special model name that triggers routing heuristics \u2014 not standard OpenAI behavior.",
    "files": "gateway/ai_mesh_gateway/llm_router.py:75\u201377 (_MODEL_ALIAS_MAP), llm_router.py:382\u2013383 (_normalize_model_alias), gateway/ai_mesh_gateway/main.py:4113\u20134118 (routing sentinel logic)",
    "implementationApproach": "(1) Deprecate _MODEL_ALIAS_MAP: instead, store aliases in LLMModelConfig.model_name (e.g., create a row with model_name='bedrock-gpt-oss-120b-legacy-alias' pointing to the same model_id as the canonical entry). (2) Remove routing sentinel 'auto' from OpenAI surface: clients that want automatic routing must use a concrete model_name. If ZeroShield wants to support optional auto-routing, expose it as a custom header (X-ZeroShield-Auto-Route: true) or a separate /v1/zeroshield/completions endpoint (not /v1/chat/completions). (3) Ensure /v1/models lists all valid model_name options (including aliases) so clients can discover them without trial-and-error.",
    "effort": "M",
    "firewallRisk": "ISOLATION: Aliases are per-gateway config (no cross-org leak), already validated at deployment time. ENFORCEMENT: aliased models are routed through the same firwall pipeline as their canonical form, so no regression."
  },
  {
    "title": "Per-request routing weights and parameters not OpenAI-spec",
    "severity": "Medium",
    "detail": "Clients can control routing via body.routing_preferences.{enable_routing, risk_weight, cost_weight, latency_weight, priority_weight, data_sensitivity, latency_budget_ms, compliance_requirements, model_risk_score} or equivalent metadata/body keys. This is ZeroShield-specific and not in OpenAI chat/completions spec. A client using stock OpenAI SDK cannot pass these params; they are silently dropped by normalize_openai_chat_request. Additionally, the weighted_fastpath decision source skips the Bedrock adjudicator for performance but is not discoverable by clients \u2014 they cannot opt into full adjudication for a specific request.",
    "files": "gateway/ai_mesh_gateway/main.py:1478\u20131624 (_extract_chat_routing_preferences), gateway/ai_mesh_shared/openai_request_normalizer.py (strips unknown fields before llm_router sees them)",
    "implementationApproach": "(1) Define a ZeroShield-specific routing extension that maps to OpenAI's metadata param (if any). For now, document that routing_preferences are not part of stock OpenAI SDK and require manual JSON body construction or a ZeroShield SDK wrapper. (2) Expose routing weights as org config only (FirewallConfig.routing_*_weight), not per-request; if per-request override is needed, add a custom header (X-ZeroShield-Routing-Weights: {base64-json}). (3) For adjudicator opt-in, use the same header mechanism or add to org config. (4) Ensure that stock SDK users always get deterministic weighted routing (or single-candidate shortcut) without surprises; the Bedrock adjudicator is a value-add for operators, not a user-facing choice.",
    "effort": "M",
    "firewallRisk": "ENFORCEMENT: Weighted scoring and adjudicator selection are internal heuristics; neither bypasses policy (both run after input_scan + policy + tier2 gates). Per-request weights are client-controlled, so operators must trust clients (or run in monitor-only mode for high-risk orgs). AUDIT: routing decision_source and decision_factors are recorded in zeroshield trace and telemetry, enabling operators to tune weights if a particular strategy is abused."
  },
  {
    "title": "Model state (isolated/degraded) reroute not in OpenAI spec",
    "severity": "Medium",
    "detail": "When a model is isolated or degraded in ModelState (model_state.py), the gateway reroutes via model_state isolation verdict (isolation_action: block/reroute/alert) and fallback_model, triggered by risk_score crossing a threshold. This is a ZeroShield-specific governance layer with no OpenAI parallel. Stock SDK users cannot know a model is isolated until they get a 503 or a response with a different routed_model in the zeroshield trace. Additionally, kill-switch (from KillSwitchPolicy in the control plane) also triggers reroute but is enforced separately, creating two independent reroute paths (model_state + kill_switch) that could conflict or create confusion.",
    "files": "gateway/ai_mesh_gateway/model_state.py, gateway/ai_mesh_gateway/main.py:1675\u20131713 (_build_isolation_reroute_metadata)",
    "implementationApproach": "(1) Document model isolation in ZeroShieldResponse.v1 as a governance feature; stress that stock SDK users should check zeroshield.rerouted and zeroshield.decision_source ('model_state' vs 'kill_switch') to detect forced reroute. (2) Consolidate kill-switch + model-state checks into a single 'model_isolation' service layer (not two separate gates); deduplicate audit/telemetry logic. (3) Expose model isolation status via a ZeroShield-specific admin endpoint (/api/models/{model_name}/status) so operators can query why a model was isolated. (4) In zeroshield trace, always emit isolation_source ('kill_switch' vs 'model_state_risk' vs 'model_state_manual') so the root cause is clear.",
    "effort": "M",
    "firewallRisk": "ISOLATION: Model isolation is org-scoped in Redis (model_state:{org_slug}:{model_name}), so no cross-org leak. Kill-switch is global but gated by org (KillSwitchPolicy.organization), preventing a global kill-switch on an org's model without the org's data. ENFORCEMENT: Both isolation paths re-check the fallback model (via _isolation_reroute_context -> fallback_chains), so a fallback model cannot be isolated + auto-routed again (no infinite loop). AUDIT: reroute audit is logged separately (isolation_reroute_audit in main.py), enabling operators to track which models were force-rerouted and why."
  },
  {
    "title": "Assistants/Agents API not implemented",
    "severity": "High",
    "detail": "OpenAI Assistants API (POST /v1/assistants, /v1/threads, /v1/threads/{id}/runs, streaming) is not implemented. This API is foundational for multi-turn agentic workflows with tool use, file search, code execution, and long-lived state. ZeroShield currently handles single-turn chat/completions + Responses (not yet) only. Assistants require thread management, run state tracking, streaming run events, and per-run tool execution context \u2014 all complex governance surfaces.",
    "files": "gateway/ai_mesh_gateway/main.py (no /v1/assistants, /v1/threads endpoints), control/ai_mesh_control/ (no thread/run models)",
    "implementationApproach": "Create Assistants API surface with policy enforcement: (1) POST /v1/assistants: store org-scoped Assistant {id, model (required), instructions, tools, metadata, temperature, top_p, max_*_tokens}. Enforce model routing + allowlist (reuse chat/completions logic). (2) POST /v1/threads: create thread, attach to org + user. (3) POST /v1/threads/{id}/runs: execute run with assistant + thread context. Firewall gates: (a) input_scan each user message in thread history + new message; (b) policy engine checks assistant instructions (system-level PII/jailbreak); (c) tool definitions validated (URL allowlist, function schema); (d) each tool invocation (file_search, code_execution, function call) scanned + policy-gated. (4) Streaming /v1/threads/{id}/runs?stream=true: emit run.created, run.step.created, run.step.delta, run.completed events (akin to Responses streaming). (5) Tool outputs: when a tool returns, run state updates with output_text; output_guard must scan tool outputs for PII/jailbreak before continuing. (6) Model routing: for multi-turn assistant runs, allow per-turn model override (not just per-assistant model), reusing smart_select_model heuristics.",
    "effort": "XL",
    "firewallRisk": "ENFORCEMENT: Tool definitions (URLs, function schemas) are new attack surface \u2014 code injection in function descriptions or URL parameters could trigger assistant to call unintended APIs. Mitigation: validate tool definitions at assistant creation time (URL allowlist, function name/param schema validation). Tool outputs (from code_execution, file_search, function calls) are opaque to the gateway unless they are text; must scan text outputs with PII scanner. ISOLATION: Thread state is org+user scoped; a thread cannot be accessed cross-org. Run context must be isolated per-org (run_id:{org_slug}:{run_id} in Redis). AUDIT: Each tool invocation is a separate event that must be logged (tool_name, tool_input, tool_output, model_used), enabling compliance audits of assistant behavior. CONTINUITY: Long-running multi-step runs may exceed gateway timeout; implement async run polling (/v1/threads/{id}/runs/{run_id}) to allow clients to check status without blocking."
  },
  {
    "title": "Weighted routing and smart selection not discoverable by clients",
    "severity": "Medium",
    "detail": "The full routing heuristic (weighted scoring by risk/cost/latency/priority, Bedrock adjudicator, weighted_fastpath optimization) is internal to ZeroShield. Clients using stock OpenAI SDK have no way to learn what routing weights their org is using, whether adjudicator will run for a request, or how the selected model was chosen (decision_source). The decision_factors and policy_summary fields are in the zeroshield trace only after the request completes, blocking clients from optimizing requests proactively.",
    "files": "gateway/ai_mesh_gateway/llm_router.py:1300\u20131500 (smart_select_model, adjudicator logic), gateway/ai_mesh_gateway/main.py:1478\u20131624 (routing preferences extraction), docs/contracts/ZeroShieldResponse.v1.md (only documents POST-request trace, not pre-request discovery)",
    "implementationApproach": "(1) Expose org routing config via a ZeroShield-specific endpoint (GET /api/orgs/{org_slug}/routing-config or equivalent in control plane) that lists routing weights, adjudicator settings, preferred models, model costs/risks/latencies. Clients can call this once per session to optimize. (2) Document in ZeroShieldResponse.v1 that decision_source, decision_factors, routing_reason, and policy_summary are available in the zeroshield trace for post-hoc analysis. (3) For advanced clients, offer an optional '/v1/zeroshield/routing/preview' endpoint (POST) that accepts a partial request (model, compliance_requirements, data_sensitivity) and returns the predicted selected_model + reasoning WITHOUT executing the request \u2014 a dry-run for client optimization. (4) In org UI / API docs, highlight that routing is automatic and transparent; document the default weights and adjudicator behavior so operators understand what to expect.",
    "effort": "M",
    "firewallRisk": "POLICY LEAK: Exposing routing weights + model metadata could allow attackers to reverse-engineer which model is cheapest/fastest and spam that model to exhaust budget. Mitigation: gate routing-config endpoint behind high-privilege auth (org admin only), and do NOT expose cost/latency in client-visible zeroshield trace (only in internal telemetry). OPTIMIZATION: Offering a dry-run /preview endpoint adds a new query surface; validate it with the same policy engine (input_scan + policy gates) to prevent abuse (e.g., repeatedly calling /preview to enumerate models)."
  },
  {
    "title": "Embeddings model routing not fully aligned with chat routing",
    "severity": "Low",
    "detail": "Embeddings use a separate routing path (_embedding_fallbacks_for in llm_router.py:~2270) and default to 'text-embedding-3-small' if no model is specified (llm_router.py:82). Chat/completions falls back to litellm_default_model (llm_router.py:408). If an org configures a different set of embedding models vs. chat models, clients cannot discover which embeddings models are available (no /v1/models filtering for modality). Additionally, embeddings skip the smart_select_model heuristic entirely (no compliance routing, no adjudicator), treating all embeddings requests as low-risk + no governance.",
    "files": "gateway/ai_mesh_gateway/llm_router.py:2270+ (_embedding_fallbacks_for), main.py:7387\u20137391 (embeddings routing), main.py:7310\u20137313 (embedding model default)",
    "implementationApproach": "(1) Extend LLMModelConfig to support an 'modality' field (e.g., 'chat', 'embedding', 'image', 'audio') so /v1/models can filter by modality (GET /v1/models?modality=embedding). (2) For embeddings, enable optional smart routing (gated by org config embedding_routing_enabled, default off for perf) that applies the same weighted heuristic for data_sensitivity/compliance constraints. (3) Unify embeddings fallback with chat fallback: use the global fallback_map for both, or split explicitly (fallback_map_chat vs. fallback_map_embedding) so operators can tune them separately. (4) In zeroshield trace for embeddings, include routing metadata (original_model, routed_model, reason) for operators to audit (currently missing).",
    "effort": "M",
    "firewallRisk": "ENFORCEMENT: Embeddings are currently assumed low-risk and skip tier2 scanning/adjudicator, so redacted_response field is always empty (no output guard for embeddings). This is by design (embeddings are deterministic vectors, not text). However, if an org marks embeddings as 'confidential' data_sensitivity and the gateway routes to a 'public' embedding model, there is no governance check. Mitigation: extend policy engine to validate embedding model sensitivity level matches request data_sensitivity before routing (same compliance check as chat)."
  },
  {
    "title": "Model cost and risk metadata not exposed for billing/governance",
    "severity": "Medium",
    "detail": "LLMModelConfig stores cost_per_1k_{input,output}_tokens and risk_score [0\u20131] (main.py:1579\u20131595), and these feed the routing heuristic. However, they are NOT returned in the zeroshield trace (neither response.zeroshield nor response headers), and they are NOT available to clients for cost prediction / request budgeting. Stock OpenAI SDK users cannot know upfront whether a request will route to an expensive or risky model. Additionally, 'owned_by' in /v1/models is hardcoded per-provider (e.g., 'openai') but does not distinguish between org-managed and platform-managed deployments.",
    "files": "gateway/ai_mesh_gateway/llm_router.py:1046\u20131067 (get_model_list does not include cost/risk), gateway/ai_mesh_gateway/main.py:968\u20131050+ (_build_zeroshield_metadata), docs/contracts/ZeroShieldResponse.v1.md (no cost/risk fields listed)",
    "implementationApproach": "(1) Add cost_usd and risk_score to the zeroshield trace in response (non-streaming) and in the terminal streaming frame. Format: cost_usd is float (actual cost of tokens used, from usage + model cost_per_1k_*), risk_score is [0\u20131] (model's baseline risk_score). (2) For /v1/models, add optional 'cost_per_1k_tokens' object to each model entry (opt-in via X-ZeroShield-Include-Costs header) so clients can query cost_per_1k_input + cost_per_1k_output. (3) In zeroshield trace, rename 'selected_model' to 'routed_model' (already done per docs) and add 'routed_model_cost_per_1k_input', 'routed_model_cost_per_1k_output' so clients can post-hoc compute cost. (4) For predictions, offer a /v1/zeroshield/estimate-cost endpoint (POST) that accepts {model, messages} and returns predicted_tokens + predicted_cost_usd (reusing estimate_prompt_tokens logic).",
    "effort": "M",
    "firewallRisk": "AUDIT: Cost and risk metadata are operator-facing (not user secrets); exposing them enables cost management and risk assessment. No isolation risk. ENFORCEMENT: Cost info should not include operator cost thresholds or budget limits (those are in KillSwitchPolicy, not model config); only expose the raw per-token cost so clients can compute their own budgets."
  }
]