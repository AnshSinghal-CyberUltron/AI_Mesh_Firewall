# responses-api-tools: OpenAI Responses API tool calling (function_call + file_search + web_search) with enforcement

## Current
ZeroShield currently supports OpenAI-compatible chat/completions (/v1/chat/completions, main.py:3483-3594) and embeddings (/v1/embeddings, main.py:7168-7210). Tool-call structures (tool_calls, tool_choice, function_call) are tolerated in chat/completions requests but not actively enforced or governed:

- Chat endpoint normalizes and accepts tools/tool_choice in request body (no validation), passes them to LiteLLM (llm_router.py:65 includes tool_choice in passthrough fields)
- Tool-call output channels (tool_calls function name/arguments) are scanned as secondary text in input security (main.py:993-1010 extracts tool function text for prompt injection detection)
- Tool-call outputs are similarly scanned during streaming (secure_streaming.py:508-565 buffers tool_calls deltas)
- Tool-call outputs are included in output-guard scans (secure_streaming.py:551-565 aggregates tool_calls text for PII/hallucination detection)
- RAG (Retrieval-Augmented Generation) is a separate surface: /v1/vector/query and /v1/rag/* endpoints (vector_routes.py) with their own input/output scanning pipeline (rag_orchestrator.py, rag_pipeline/*.py)
- Policy model has tool-response field redaction (policy/models.py:58-66 redaction_fields array for MCP tool responses)
- MCP connector has tool governance via target_tool field (policy/models.py:149-154) per MCP-domain policies

Missing: no /v1/responses endpoint exists; no tool invocation loop (submit tool output, re-enter for next completion); no tool-choice policy enforcement (whitelist/blacklist tools); no file_search or web_search built-in tools; no previous_response_id state tracking; no typed input/output items (OpenAI Responses API schema); no tool-result submission endpoint.

## OpenAI Spec
OpenAI Responses API (2025-02-01 spec) exposes:
- POST /v1/responses: create(model, modalities=[...], input_items=[{type: "text"|"image"|"audio", [text|image|audio]: ...}, {type: "tool_result", tool_use_id: "call_...", content: ...}], tools=[{type: "function", function: {name, description, parameters}}], tool_choice="auto"|"required"|"none"|specific function name, previous_response_id=id, instruction=..., metadata={...}) → {id, created, status: "in_progress"|"succeeded"|"incomplete", output: {items: [{type: "text", text: "..."}, {type: "tool_call", tool_call: {id: "call_xyz", type: "function", function: {name, arguments}}}], finish_reason: "stop"|"tool_calls"|...}, usage: {input_tokens, output_tokens}}
- Built-in tools: type="function" functions={name: "file_search"|"web_search", description, parameters} (file_search={index_ids: [...], query_string}, web_search={query})
- Streaming: Server-sent events (response.created, response.output_text.delta, response.output_text.done, response.output_item_added, response.tool_call_created, response.tool_call_delta, response.completed)
- Tool-call loop: client gets {type: "tool_call", id, function: {name, arguments}} in output.items, then submits input_items with {type: "tool_result", tool_use_id: id, content: "..."} in next request, optionally with previous_response_id to continue same conversation thread
- Tool governance: tools[] array on the request gates which functions are available (client-specified allowlist; OpenAI SDK does not enforce server-side gating per se, but API-key context applies)
- Modalities: text, image, audio (multimodal inputs/outputs, separate from traditional message/content arrays)

## Reusable hooks
ZeroShield can inherit tool enforcement from the existing firewall pipeline by reusing these integration points:

1. **Input scanning pipeline** (gateway/ai_mesh_gateway/scanner.py:InputScanner, pipeline_trace.py): Responses API request body parsing → input_items extraction → fold text/audio/image/tool_result into single scannable text → run through existing Tier-1 (regex) + Tier-2 (Bedrock guard model) security scans. Tool function names and arguments are scanned the same way as chat tool_calls (main.py:993-1010).

2. **Policy evaluation** (policy/compiler.py PolicyEvaluator): For tool governance, reuse existing rule-evaluation stack: org/user context lookup → enabled_policies filter → rule matching on {tool_name, tool_use_type: 'file_search'|'custom'} → action verdict (allow/block/redact). Extend Rule model with allowed_tools field (already supports target_tool for MCP, same pattern).

3. **Output guarding** (gateway/ai_mesh_gateway/output_guard.py OutputGuard): When tool_call outputs are received (tool_result.content), run the same output-guard logic: PII/secret detection, credential exposure, IP leakage. Verdicts (action='block'|'redact') apply to tool result text before it re-enters the LLM context. Extend output_guard.OutputVerdict to include tool_use_id and tool_output_index.

4. **Streaming orchestration** (stream_orchestration.py StreamRunMetrics, stream_with_finalize): For /v1/responses streaming, wrap the LLM SSE generator in the same pipeline: eligibility_phase (policy preflight) → stream_phase (LiteLLM SSE with tool_call buffering) → finalization_phase (circuit breaker, TPM reconciliation). SecureStreamingResponse buffers tool_call argument deltas and flushes at tool_call boundaries (reuse FlushReason.BOUNDARY logic).

5. **Telemetry/audit** (pipeline_trace.py build_pipeline_trace, telemetry.py EnforcementEvent): Emit EnforcementEvent for each tool invocation with fields: {tool_name, tool_use_id, tool_argument_text_sample, tool_result_size, matched_policies, action}. Build _pipeline_trace with tool_call index and tool_result verdict. Reuse existing event-class taxonomy ('tool_invocation').

6. **Rate limiting** (rate_limiter.py RateLimiter): Extend TPM budget to account for tool invocation token cost (tool arguments + tool result consume tokens; policy can set tool-specific budgets per org).

7. **Redaction fields** (policy/models.py Policy.redaction_fields): Reuse existing redaction_fields for tool_result.content: redact sensitive keys recursively from tool output JSON before the LLM sees it. Same NFKC-normalized case-insensitive matching (output_guard.py apply_redaction).

8. **Context guard** (context_guard.py): For file_search built-in tool, map index_ids to RAG collection context; reuse context_guard to validate document retrieval against org/user scopes (field-level PII redaction per policy).

## Gaps
[
  {
    "title": "No /v1/responses endpoint (core Responses API)",
    "severity": "Critical",
    "detail": "ZeroShield lacks the POST /v1/responses entry point required for stock OpenAI SDK client.responses.create(...) calls. Without this, Responses API clients receive 404 instead of firewall-gated responses. The endpoint must parse modalities (text, image, audio), input_items (typed array including tool_result items), tools[], tool_choice, previous_response_id, and instruction fields, then dispatch to LLM and return the structured response envelope with output.items (text + tool_calls) and typed streaming events.",
    "files": "gateway/ai_mesh_gateway/main.py (no /v1/responses route); gateway/ai_mesh_gateway/llm_router.py (no responses-api dispatch); shared/ai_mesh_shared/litellm_byok.py (may need Responses API routing config)",
    "implementationApproach": "Add @app.post('/v1/responses') handler in main.py (~3600 LOC pattern: auth \u2192 type-validation \u2192 prompt-assembly \u2192 security-scan \u2192 model-selection \u2192 llm-call \u2192 output-guard \u2192 response-assembly). Reuse existing: normalize_openai_chat_request (openai_request_normalizer.py) generalized to handle input_items + tools + modalities; policy_adjudicator for routing decision; input_scanner for prompt-injection on text/image inputs. Output phase: call output_guard on text output, tool function names, and tool arguments (separate audit trail per tool call). Build response envelope with typed output.items and usage. Support both non-streaming (JSON) and streaming (SSE with response.created/delta/tool_call_* events).",
    "effort": "XL",
    "firewallRisk": "High risk: tool invocation introduces new inference surface outside traditional LLM request/response. Mitigations: (1) gate tools[] array by policy before forwarding to LLM (whitelist/blacklist per org/user), (2) scan tool function names and argument schemas for injection/data-exfiltration patterns at parse time, (3) audit each tool_call output as a separate event for forensics, (4) track tool use in policy decision_factors (already done for chat tools, extend to Responses API)."
  },
  {
    "title": "Tool invocation loop not implemented (tool_result submission + state tracking)",
    "severity": "Critical",
    "detail": "OpenAI Responses API requires a multi-turn loop: client receives tool_call output, invokes the tool (externally), then submits the tool_result back via input_items in the next request. ZeroShield has no mechanism to accept {type: 'tool_result', tool_use_id, content} inputs, validate them against the prior tool_call, or handle previous_response_id state linking. This breaks the agentic pattern where an agent submits multiple tool invocations and integrates their outputs into a single reasoning thread.",
    "files": "gateway/ai_mesh_gateway/main.py (no tool_result parsing); gateway/ai_mesh_gateway/pipeline_trace.py (no tool_call/tool_result audit trail); control/ai_mesh_control/core/ (no tool_result enforcement event model)",
    "implementationApproach": "In the /v1/responses handler, parse input_items to detect {type: 'tool_result'} entries. For each tool_result: (1) validate tool_use_id exists in a prior /v1/responses call (use Redis cache: zs:response:{id}:{tool_call_id} \u2192 {function_name, arguments}), (2) scan tool_result.content through output_guard to prevent tool-output injection (e.g., tool returns malicious JSON intended to confuse the LLM), (3) fold tool_result text into the internal message history for LLM context assembly, (4) emit EnforcementEvent(action='tool_call_accepted', tool_result_size=len(content)) for audit. Optional: track token budget per response thread (previous_response_id linkage) to prevent DoS loops.",
    "effort": "L",
    "firewallRisk": "High risk: tool_result content can exfiltrate data from external systems back into the model context. Mitigation: (1) apply same PII/secret redaction logic used for LLM outputs (output_guard.py) to tool_result.content before feeding it to the model, (2) record matched_patterns on redacted tool results in the audit trail, (3) enforce per-tool redaction policy (policy.redaction_fields already supports this for MCP tools; extend to Responses API tools), (4) rate-limit tool invocations per user/response to prevent infinite loops."
  },
  {
    "title": "No file_search built-in tool (map to ZeroShield RAG /v1/rag)",
    "severity": "High",
    "detail": "OpenAI Responses API includes type='function' tools with special names file_search and web_search. file_search takes {index_ids: [string], query_string: string} as parameters and retrieves documents from OpenAI's file-retrieval index. ZeroShield has no built-in file_search tool; clients who specify tools=[{type: 'function', function: {name: 'file_search', ...}}] receive 404 or 'tool not found' from LLM. This prevents RAG workflows using the Responses API surface. Similarly, web_search is absent.",
    "files": "gateway/ai_mesh_gateway/main.py (no tool handler registry); gateway/ai_mesh_gateway/vector_routes.py (existing /v1/rag/query but not linked to Responses API tool invocations); control/ai_mesh_control/policy/models.py (policy domain 'mcp' exists, need 'builtin_tools' or 'responses_api' domain)",
    "implementationApproach": "In /v1/responses handler, when parsing tools[], check for {function: {name: 'file_search'}} or {function: {name: 'web_search'}}. For file_search: map index_ids to ZeroShield RAG collections (stored in Redis mapping org\u2192{collection_id\u2192index_id}), extract query_string, invoke rag_pipeline.RAGFirewallPipeline.execute_query(...) with the firewall enforcement (input_scanner on query_string, output_guard on retrieved documents per policy). Return tool_call output with {type: 'text', text: JSON-serialized documents}. For web_search: optionally integrate with external web search API (e.g., Tavily, Google Custom Search) with the same guardrail wrap (scan query for doxing/scraping intent, redact URLs pointing to sensitive domains). Policy: add allowed_tools list to governance (whitelist file_search/web_search per org/user, similar to target_tool in MCP policies).",
    "effort": "M",
    "firewallRisk": "Medium risk: file_search/web_search can leak query context or retrieve sensitive documents. Mitigations: (1) same RAG firewall applies to file_search (query is scanned for injection, documents are ranked/filtered per policy), (2) web_search firewall must block queries targeting sensitive domains (HR systems, financial sites, competitors) via policy allowlist, (3) audit each tool invocation with document_count, matched_policies in EnforcementEvent."
  },
  {
    "title": "No tool-choice policy enforcement (whitelist/blacklist per organization/user)",
    "severity": "High",
    "detail": "Chat completions accept tools[] and tool_choice, which are forwarded to LiteLLM without validation against org-level or user-level policy. A user could request use of tools=['web_search', 'file_search', 'custom_function_that_exfiltrates_data'], and ZeroShield would allow it. No policy surface exists to restrict which tools a given user/org can invoke. The Policy model has target_tool for MCP but not for Responses API tools or built-in tools.",
    "files": "control/ai_mesh_control/policy/models.py (Rule model; needs allowed_tools field); control/ai_mesh_control/policy/ (policy evaluation for chat/completions does not check tools array)",
    "implementationApproach": "Extend policy/models.py Rule to include allowed_tools (ArrayField of tool function names or '*' for all). In policy compiler (policy/compiler.py), add a rule-evaluation stage for tool governance: when /v1/responses receives tools[], evaluate enabled policies against the org/user context and extract the allowlist (union of all matching rules' allowed_tools). Intersect the request tools[] with the allowlist; if any tool is not in the allowlist, emit a policy block verdict (code='tool_not_allowed'). Similar check for tool_choice (if restricted to a specific tool name, validate that name is in the allowlist). Persist the allowed_tools_evaluated fact in the response metadata (pipeline_trace.py).",
    "effort": "M",
    "firewallRisk": "Low to Medium: policy enforcement prevents unintended tool access. Mitigation: (1) default policy allows all tools (backward-compat), (2) orgs opt-in to tool allowlisting, (3) audit each tool_choice verdict in EnforcementEvent, (4) support dynamic tool discovery (orgs can query /api/policies/available-tools to list tools allowed per user)."
  },
  {
    "title": "No tool-output audit trail and response state tracking (Redis/database)",
    "severity": "High",
    "detail": "When a tool_call is emitted (output.items includes {type: 'tool_call', id: 'call_xyz', function: {name, arguments}}), ZeroShield must store the call metadata (tool_call id \u2192 function_name, arguments, timestamp, user_id, org_id) for later validation when a tool_result is submitted. Without Redis caching, a malicious actor could submit a tool_result with a forged tool_use_id and trick the model. Additionally, previous_response_id linkage requires a way to retrieve prior response state (prior tool_calls, consumed tokens, finish_reason) to validate continuity. Currently, no /v1/responses/{response_id} endpoint exists to fetch prior state.",
    "files": "gateway/ai_mesh_gateway/main.py (no response cache/persistence); gateway/ai_mesh_gateway/secure_streaming.py (no response envelope persistence); control/ai_mesh_control/core/models.py (no EnforcementEvent schema for tool calls)",
    "implementationApproach": "On tool_call emission (before returning to client), store in Redis: zs:response:{response_id}:{tool_call_id} \u2192 {function_name, arguments, timestamp_ms, user_id, org_id} with TTL 3600s (1 hour, long enough for multi-turn but expires stale calls). When tool_result arrives, look up the tool_call_id to validate it came from a prior call (reject if missing \u2192 400 'tool_use_id_not_found'). Optionally add GET /v1/responses/{response_id} endpoint to return {output, finish_reason, usage, zeroshield: {...}} for clients (useful for debugging, requires storing full response in Redis). Extend EnforcementEvent model with tool_call_id, tool_call_index, tool_function_name, tool_result_size fields; emit an event for each tool_call and each tool_result.",
    "effort": "M",
    "firewallRisk": "Medium risk: tool_call/tool_result tracking is essential for fraud prevention. Mitigations: (1) Redis cache is ephemeral (no persistent leak risk), (2) tool_call_id is cryptographically unique (generated by OpenAI SDK, not user-controlled), (3) validate all tool_results come from authenticated API key (already gated by main.py auth middleware), (4) rate-limit tool_result submissions (max N results per response per second) to prevent DoS."
  },
  {
    "title": "No input_items type validation and multimodal scanning (image, audio)",
    "severity": "High",
    "detail": "Responses API input_items is a typed array: each item is {type: 'text'|'image'|'audio'|'tool_result', [text|image|audio|...]: ...}. Currently, ZeroShield normalizes chat.completions (messages array) but does not support structured input_items. Image input (base64 or URL) and audio input (base64 or URL) need to be scanned separately from text: image for OCR-extractable PII, audio for speech-to-text PII. Tool_result items need separate schema validation.",
    "files": "shared/ai_mesh_shared/openai_request_normalizer.py (only normalizes message-based requests); gateway/ai_mesh_gateway/scanner.py (no image/audio scanning, Tier-2 Bedrock can handle multimodal but not currently hooked)",
    "implementationApproach": "In /v1/responses handler, parse input_items and classify each by type. For type='text': existing scanner path (InputScanner tier-1 regex + Tier-2 Bedrock if enabled). For type='image': if image is base64-encoded or URL, download/decode, optionally run OCR or Bedrock's multimodal vision scan to extract text, then scan the extracted text through the same pipeline. For type='audio': if audio is provided, optionally transcribe via speech-to-text (AWS Transcribe or local Whisper), then scan the transcript. For type='tool_result': validate schema (tool_use_id exists, content field is string or structured), scan content as secondary text (tool output content may contain sensitive data). Update pipeline_trace.py to record which modality items were scanned and their verdicts (per input_items[i] index).",
    "effort": "L",
    "firewallRisk": "Medium risk: image/audio inputs can bypass text-only scanning. Mitigation: (1) OCR/transcription is lossy (not 100% accurate), so org can choose tier (tag-only, redact, or block), (2) multimodal scanning is resource-intensive; gate behind org policy flag (default off for cost), (3) audit each image/audio scan in EnforcementEvent."
  },
  {
    "title": "No tool-specific streaming events (response.tool_call_created, response.tool_call_delta)",
    "severity": "Medium",
    "detail": "When streaming=true on Responses API, OpenAI emits typed SSE events: response.tool_call_created (tool_call id, function name, index), response.tool_call_delta (tool_call id, arguments delta), response.output_text.delta (text content delta), etc. ZeroShield's secure_streaming.py buffers and scans chat.completion deltas (content, reasoning_content, tool_calls) but does not emit typed Responses API streaming events. Clients using stream_options.include_usage expect response.completed event with final usage; clients handling tool_calls expect response.tool_call_created before arguments arrive.",
    "files": "gateway/ai_mesh_gateway/secure_streaming.py (buffers SSE but emits chat.completion.chunk shape); gateway/ai_mesh_gateway/stream_orchestration.py (build_stream_trace_frame returns chat.completion.chunk); tests/test_stream_trace_frame.py (validates chunk shape)",
    "implementationApproach": "Add /v1/responses streaming path: when request includes stream=true, launch async SSE generator from LiteLLM that yields OpenAI Responses API events ({type: 'response.created'}, {type: 'response.output_text.delta', delta: {text: '...'}}, {type: 'response.tool_call_created', tool_call: {id, function: {name}}}, {type: 'response.output_item_added', item: {type, index}}, {type: 'response.completed', response: {output, usage}}). Wrap in SecureStreamingResponse to scan tool_call arguments as they arrive (same as chat tool_calls streaming logic), redact PII from output_text.delta chunks, and emit final response.completed with guard action (allow/redact/flag/block). Support response.output_item_index to match deltas to specific output.items[i].",
    "effort": "M",
    "firewallRisk": "Low risk: streaming events are structured SSE and do not introduce new enforcement gaps. Mitigation: (1) same output_guard logic applies to tool_call argument assembly (scan as they accumulate), (2) final response.completed event includes zeroshield metadata (parallel to chat.completion trace frame)."
  },
  {
    "title": "Tool governance policy not exposed in control plane API",
    "severity": "Medium",
    "detail": "Control plane (Django) lacks API endpoints to CRUD tool governance policies. Admins cannot currently configure allowed_tools per org/user via /api/policies/. The MCP connector has policy hooks (policy_domain='mcp', target_tool), but no equivalent for Responses API tools (file_search, web_search, custom functions). Dashboard has no tool auditing section (which tools are being invoked, by whom, with what parameters).",
    "files": "control/ai_mesh_control/policy/urls.py (no tool governance routes); control/ai_mesh_control/policy/views.py (no ToolGovernanceView); control/ai_mesh_control/core/dashboard_urls.py (no tool_audit_view)",
    "implementationApproach": "Add to policy/urls.py: GET /api/policies/tools/ (list available tools + policy allowlists), POST /api/policies/tools/ (create tool allowlist rule), PATCH /api/policies/tools/{rule_id}/ (update), DELETE /api/policies/tools/{rule_id}/. Serializer: ToolGovernanceRuleSerializer with fields {org_id, allowed_user_ids, allowed_tools: ['file_search', 'web_search', ...], action: 'block'|'allow', priority}. Add to dashboard_urls.py: GET /api/dashboard/tool_audit/ (recent tool invocations, grouped by tool name / user / org, with invocation count, success rate, redaction rate). Store tool audit events in EnforcementEvent with event_class='tool_invocation'.",
    "effort": "M",
    "firewallRisk": "Low risk: control plane API is administrative. Mitigation: (1) require org-admin JWT for policy changes (existing auth check), (2) audit policy create/update/delete in control-plane audit log."
  },
  {
    "title": "Tool arguments schema validation not hooked into policy",
    "severity": "Medium",
    "detail": "OpenAI Responses API allows tools[] with function.parameters (JSON Schema), e.g. {name: 'web_search', parameters: {type: 'object', properties: {query: {type: 'string'}}, required: ['query']}}. ZeroShield does not validate LLM-generated tool arguments against the schema before scanning them. A malformed argument (e.g., query is an object instead of string) could slip through or cause downstream errors. Additionally, policy could restrict argument patterns (e.g., query must not contain certain keywords).",
    "files": "gateway/ai_mesh_gateway/main.py (no tool schema registry); gateway/ai_mesh_gateway/scanner.py (no schema validation)",
    "implementationApproach": "Add a tool schema registry in the /v1/responses handler: when tools[] is received, extract and validate all function.parameters against JSON Schema draft (validate format, bounds, enum values). Store in request context for later use. When a tool_call is emitted, validate its arguments JSON against the stored schema (reject malformed arguments before scanning). Extend policy to include tool_parameter_restrictions: {tool_name: {parameter_name: {blocked_values: [...], blocked_patterns: [regex]}}}. Scan tool arguments through pattern matching in addition to text scanning.",
    "effort": "S",
    "firewallRisk": "Low risk: schema validation hardens the tool invocation boundary. Mitigation: (1) fail-open on unknown schema (allow the tool call if schema is missing or invalid), (2) audit schema violations in EnforcementEvent."
  }
]