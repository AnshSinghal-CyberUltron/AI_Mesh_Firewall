# OpenAI Responses API (/v1/responses + tool_choice/tools/typed_streaming/previous_response_id state + tool execution loops)

## Current
**Chat/Completions (proxy_chat, /v1/chat/completions @ L3484-7139):**
- Auth check (L3992, L4047-4102): GatewayAPIKey → org_slug, allowed_models, rate_limit_tpm, risk_score
- Rate limiting (L4597-4683): burst + rpm Redis checks, org TPM ceiling (L4706-4747), per-key TPM (L4749-4793)
- Kill-switch (L4241-4405): Redis check + reroute/block
- Model state (L4406-4596): Redis isolation + reroute/block
- Threat intel (L4195-4239): risk_score thresholds
- Blocked keywords (L4824-4877): config-driven filter
- Policy check (L4985-5192): _policy_check_cached deterministic rules (block/redact/rewrite/downgrade/model_downgrade, L5020-5192)
- Input scanning (L5194-5505): Tier-1 (regex) via INPUT_SCANNER.scan_prompt (L5264), force_sync_tier2 logic (L5249-5258), Tier-2 Bedrock (L5250), verdict action (block/redact/flag)
- Output guard nonstream (L5690-5705): _apply_output_guard_nonstream (L1078) → credential/PII/hallucination/IP checks → block/redact/flag
- Model routing (L5721-5895): LLM_ROUTER.adjudicate_model_selection (L5757) based on compliance/sensitivity/risk_score/latency
- Streaming response (L5516-5555): _launch_chat_stream_response (L2003) → stream_orchestration.stream_with_finalize
- Non-streaming LLM call (L5557): LLM_ROUTER.acompletion (body, redacted_prompt) → code 200/error
- Telemetry (L5522-5580, L5566-5580): _emit_telemetry + stage_metrics (auth_ms, policy_ms, tier1_ms, tier2_ms, upstream_ms)

**Stream orchestration (stream_orchestration.py, L1-500+):**
- stream_phase (L206+): LLM_ROUTER async streaming to SecureStreamingResponse (L2062-2180)
- Streaming output guard (secure_streaming.py): token-by-token buffer + _flush_buffer (on content/PII/credential) → redact/block decision
- Terminal trace frame: stream_orchestration.build_stream_trace_frame (L348) → empty choices + zeroshield metadata + usage
- M-51 guard action recording: StreamRunMetrics.record_guard_action (L65-83)

**Control plane contracts:**
- /api/policies/, /api/security/, /api/firewall/{config,models}, /api/models, /api/kill-switches, /api/gateways, /api/dashboard (in control/ai_mesh_control/main_app/urls.py)

**Entry points for pipeline reuse:**
- LLM_ROUTER.acompletion(body, redacted_prompt) @ L5557: takes OpenAI dict + optional redacted prompt text
- INPUT_SCANNER.scan_prompt(text, is_rag, toxicity_threshold) @ L5264
- INPUT_SCANNER.scan_prompt_with_tier2(...) @ L5250
- OUTPUT_GUARD.evaluate(response_text, ...) implicit via _apply_output_guard_nonstream @ L1078
- _policy_check_cached(prompt, "", user_id, ..., org_slug, ...) @ L4987
- LLM_ROUTER.adjudicate_model_selection(...routing_models, request_messages, ...) @ L5757

## OpenAI Spec
**OpenAI Responses API (relevant to M-51 contract):**
- POST /v1/responses: request body = {model, instructions?, messages?, tools?, response_format?, metadata?, previous_response_id?, ...}
- Response body: {id, object="response", created, model, choices: [{index, type, response: {id, type, items: [{type, text|tool_use: {id, name, arguments}}]}, stop_reason}], ...}
- Tool call loop: client sends previous_response_id + response.items (with tool results as messages) → gateway re-invokes model with context
- Typed streaming events: response.created, response.output_text.delta, response.output_tool_use.delta, response.completed, response.output_text.done, response.output_tool_use.done
- Usage tracking: cumulative tokens across tool loop turns (streaming include_usage on final event)

**Stock OpenAI SDK shape:**
- client.responses.create(model, instructions, messages, tools, ...) → Response object
- Tool choice handling: tools=[{type, function: {name, description, parameters}}], tool_choice={type, function: {name}} or "auto"/"required"
- Response format: {"type": "json_object"} for structured output
- previous_response_id: correlation field for tool loops

## Reusable hooks
1. **Auth layer** (L3992): already org-aware, extract from request.state.auth_context
2. **Rate limiting** (L4597, L4706, L4749): existing Redis TPM/RPM enforcers; responses API tool loops must consume tokens per-turn
3. **Policy check** (_policy_check_cached @ L4987): deterministic rule engine; reuse for tool input (instructions/tool descriptions) + tool result content
4. **Input scanner** (INPUT_SCANNER @ L5194-5505): Tier-1/Tier-2; reuse for messages, tool arguments, instructions text
5. **Model routing** (LLM_ROUTER.adjudicate_model_selection @ L5757): selection logic already tool-agnostic (just checks messages + compliance); tool loops call same selector
6. **Output guard** (_apply_output_guard_nonstream @ L1078): credential/PII/hallucination detector; extend to scan tool-use items (tool name + arguments + results)
7. **Kill-switch + model state** (L4241, L4406): already model-aware; responses API tool loops re-check selected model on each turn
8. **Telemetry** (L5522, _emit_telemetry): already event_type-agnostic; set event_type="tool_call" | "response_streaming" | "response_complete" for new surfaces
9. **Streaming trace** (stream_orchestration.build_stream_trace_frame @ L348): extend to cover tool-use delta events (set detection_tier/threat_type from tool-argument scanner verdict)
10. **Context assembler** (context_assembler.minimize_context @ L4799): already handles message history; reuse for tool loop context minimization

## Gaps
[
  {
    "title": "No /v1/responses endpoint (OpenAI Responses API missing)",
    "severity": "Critical",
    "detail": "Gateway only exposes /v1/chat/completions (L3484). OpenAI Responses API (POST /v1/responses) with request body {model, instructions?, messages?, tools?, response_format?, previous_response_id?, ...} is absent. Client cannot create responses with typed tool_use items or structured output. Tool loop iterations (client sends tool results as next request) require previous_response_id correlation, which is not supported.",
    "files": "gateway/ai_mesh_gateway/main.py (missing @app.post route); gateway/ai_mesh_gateway/stream_orchestration.py (missing response-specific streaming logic for typed items)",
    "implementationApproach": "Create proxy_responses async handler (async def proxy_responses(request: Request, ...)) @ main.py that: (1) extracts auth_context, org_slug, inference_models like proxy_chat L3992-4009; (2) runs identical auth/rate-limit/kill-switch/policy gates (reuse code by factoring common phase into _enforce_gateway_policy_checks(auth_ctx, body, org_config, ...) returning (allowed, response_or_none)); (3) on tool-equipped requests (body.get('tools')), forward to LLM_ROUTER.acompletion with model + messages + tools + response_format; (4) on tool-use in response.choices[0].response.items, scan tool arguments + tool name against INPUT_SCANNER Tier-1/2 (inject tool usage as pseudo-message: {'role': 'assistant', 'tool_calls': [{'function': {'name': tool_name, 'arguments': args_json}}]}); (5) when client sends previous_response_id + new messages with tool results, correlate via response_map cache (Redis key: 'response:{org_slug}:{request_id}' \u2192 {model, policy_verdict, routing_selection}); (6) re-run policy check on tool-result content before forwarding; (7) stream typed response.output_text.delta + response.output_tool_use.delta events via stream_orchestration; (8) emit telemetry event_type='tool_response' | 'response_streaming' + metadata {tool_calls_count, tool_choice_used}.",
    "effort": "XL",
    "firewallRisk": "Tool arguments (untrusted structured JSON from client) can carry prompt-injection payloads or data-exfiltration in 'arguments' fields. Mitigation: apply INPUT_SCANNER to tool-use items as if they were message content (Tier-1 regex + Tier-2 Bedrock scan of '{\"name\": \"...\", \"arguments\": {...}}' serialization). Tool results sent by the client (in subsequent requests with previous_response_id) must re-validate policy rules and re-scan for PII/secrets before forwarding to the model (fail-closed if policy unavailable). Store response_id \u2192 {org_slug, model, routing} in transient Redis (TTL 1h) to prevent response_id spoofing across orgs."
  },
  {
    "title": "No tool-use input scanning (tool_calls + function arguments bypasses gateway scanning)",
    "severity": "Critical",
    "detail": "proxy_chat scans messages[].content (L4802, scan_text = effective_prompt), but tool definitions (body.tools[].function.{name, description, parameters}) and client-submitted tool arguments (in message.tool_calls[].function.arguments) are NOT scanned. A tool_choice='required' request with a jailbreak payload hidden in tool arguments (e.g., {\"name\": \"execute_code\", \"arguments\": \"{\\\"code\\\": \\\"ignore_instructions()\\\"}\") bypasses Tier-1/2 entirely. Tests pass because the test suite does not supply tool_use requests with malicious tool arguments.",
    "files": "gateway/ai_mesh_gateway/main.py (L4802 _extract_prompt_from_messages, L5232 scan_text assembly); gateway/ai_mesh_gateway/scanner.py (no tool-aware scanning)",
    "implementationApproach": "Extend _extract_prompt_from_messages (L2854) to fold tool_calls[].function.arguments + tool_calls[].function.name into the prompt text for scanning (e.g., append '\\nTools used: ' + serialize tool_calls). Modify scan_text assembly (L5232-5247) to also include body.tools[] function descriptions + parameters schema (as JSON). Create _scan_tool_arguments(tool_calls: list[dict]) \u2192 list[tuple(tool_idx, argument_key, threat_verdict)] function that runs the INPUT_SCANNER on each tool argument value independently (e.g., tool_calls[0].function.arguments['api_key'] is scanned separately from the full arguments string). On Tier-2 block/redact of tool argument, either (a) block the entire request (tool-use is optional, caller can retry without tools), or (b) redact the matching argument value in-place (replace with '<redacted>'), or (c) strip that tool from body.tools and re-run model selection (requires fallback logic). Prefer (a) block for tool_overreach / injection, (b) redact for PII/secrets in arguments.",
    "effort": "M",
    "firewallRisk": "Tool arguments are structured (JSON) payloads from the client. Unlike freeform message content, they have schema (parameters object). Scanning them as-is with regex (Tier-1) will have high false-positive rate (parameter values like URLs, SQL snippets, code blocks are legitimate). Mitigation: (1) Tier-1 scan ONLY the tool function NAME and DESCRIPTION (parameters schema is metadata, not user data); (2) Tier-2 Bedrock scan the full arguments JSON for injection/jailbreak only (not PII, which is expected in API-call arguments); (3) fail-closed if Bedrock unavailable and tool_choice='required' (client must retry without tools). Block criteria: tool_name in ('execute_code', 'eval', 'run_bash', 'system_call') + injection pattern detected \u2192 immediate 403. Redact criteria: PII detected in argument value + org allows \u2192 redact in-place."
  },
  {
    "title": "No previous_response_id state tracking (tool loop responses API requests not correlated)",
    "severity": "High",
    "detail": "Responses API requires previous_response_id correlation: client calls /v1/responses with previous_response_id + new messages containing tool_results, and gateway must correlate to the earlier response to retain org/model/routing context. Gateway has no response storage or correlation mechanism. Each request starting with previous_response_id is treated as a fresh /v1/chat/completions call, losing: (a) the original selected model (may re-route to different model on retry); (b) the original policy verdict (policy checks run fresh, may have different outcome if config changed); (c) token accounting (TPM limit tracking restarts per request, no cumulative tool-loop cost).",
    "files": "gateway/ai_mesh_gateway/main.py (no previous_response_id field parsing or correlation); gateway/ai_mesh_gateway/llm_router.py (no response_id generation or storage)",
    "implementationApproach": "Extend proxy_responses (or proxy_chat with tool-loop awareness) to: (1) generate unique response_id on initial /v1/responses call (uuid4 or sha256(model + org_slug + request_id)); (2) store response_id \u2192 {model, org_slug, routing_selection, policy_verdict, cumulative_prompt_tokens, cumulative_completion_tokens, tool_calls} in Redis with TTL 3600s (1 hour, max reasonable tool-loop duration); (3) on subsequent request with previous_response_id, load stored context and: (a) lock model to the original selection (ignore routing on retry), (b) apply same policy verdict (skip re-evaluation if policy is deterministic), (c) carry cumulative token counts forward for TPM accounting; (4) validate previous_response_id belongs to the same (org_slug, user_id) to prevent cross-org response hijacking; (5) emit telemetry event_type='response_tool_loop_turn' with metadata {response_id, turn_number, tool_name, token_delta}; (6) on final completion (no more tool_calls), delete the response_id key.",
    "effort": "M",
    "firewallRisk": "previous_response_id is client-supplied. Attacker can forge a response_id to: (1) inherit a victim's org's model selection + policy context (carry forward a weaker policy verdict from an earlier request), (2) accumulate TPM tokens on behalf of the victim to exhaust their quota. Mitigation: (1) store response_id in Redis ONLY (not in client-facing response), (2) append HMAC(response_id, org_secret_key) to response_id before sending to client; (3) on retrieval, re-compute HMAC and verify before trusting org_slug from the payload; (4) validate request.state.auth_context.org_slug matches the stored org_slug (auth layer runs first, so org is verified). Fail-closed: if previous_response_id HMAC verification fails, return 403 with 'invalid_response_id'."
  },
  {
    "title": "No tool-use output scanning (tool-call results in response.items bypass output guard)",
    "severity": "High",
    "detail": "proxy_chat runs OUTPUT_GUARD on completion response text (L5690-5705 _apply_output_guard_nonstream). Responses API returns tool_use items in response.choices[0].response.items, where each item is {type, tool_use: {id, name, arguments}} or {type, text: '...'}. Tool-use items are NOT scanned: the tool name (e.g., 'execute_code') and arguments (e.g., bash command) bypass the output guard entirely. Text items are scanned. Attacker model (if compromised, or if client manually crafted response via API) can return a tool_use item with 'arguments' containing credentials/PII/IP addresses that bypasses redaction.",
    "files": "gateway/ai_mesh_gateway/output_guard.py (no tool_use scanning); gateway/ai_mesh_gateway/main.py (L1078 _apply_output_guard_nonstream only scans response text, not tool_use items)",
    "implementationApproach": "Extend OutputGuard.evaluate (called by _apply_output_guard_nonstream @ L1078) to accept optional tool_use_items parameter (list[{type, tool_use: {id, name, arguments}}]). For each tool_use item: (1) scan tool_use.name against suspicious patterns (code_execution, shell_command, system_call keywords); (2) scan tool_use.arguments (serialized JSON string) for PII/secrets/IP; (3) on Tier-2 block (credential in tool arguments), set output_verdict.action='block' with threat_type='credential_in_tool_arguments'; (4) on redact, replace matching argument value with '<redacted>' in-place. Emit telemetry event_type='output_guard' with metadata {tool_name, threat_type, redacted_arguments}. Non-streaming (L5690) path calls _apply_output_guard_nonstream with resp, which must unpack resp.choices[0].response.items if present. Streaming path (secure_streaming.py) must buffer tool_use items separately and run output guard on accumulated items at stream end (before emit finalize trace frame).",
    "effort": "M",
    "firewallRisk": "Tool-use items represent function calls the model wants to make. Scanning must NOT block legitimate tool calls (e.g., 'call_api' with normal arguments). False positives lead to dropped tool-calls and broken workflows. Mitigation: (1) allowlist tool names per org (org_config.get('allowed_tool_names', [])). Scan only tool names NOT in allowlist; (2) Tier-1 patterns for tool_overreach / code_injection only (not PII, which may legitimately appear in function arguments); (3) Tier-2 Bedrock scan only when org has tool_use_output_scan enabled (default false to avoid cost); (4) fail-open on Bedrock error (tool-call ships, operator reviews); (5) never redact tool arguments in-place (arguments are parsed JSON, redaction breaks schema)\u2014instead, block or drop the tool_call."
  },
  {
    "title": "No Responses API streaming (typed events response.created/output_text.delta/output_tool_use.delta missing)",
    "severity": "High",
    "detail": "proxy_chat streaming (L5516-5555) uses stream_orchestration.stream_with_finalize to emit SSE chunks of type 'chat.completion.chunk' with choices[0].delta field. Responses API streaming requires SSE events of type 'response.created' (once on open), 'response.output_text.delta' (per text token), 'response.output_tool_use.delta' (per tool JSON token), 'response.completed' (final). Gateway has no dispatcher for these typed events. Tool-use streaming requires incremental JSON assembly (accumulate 'arguments' chunks until complete object), which secure_streaming.py does not support.",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py (only emits chat.completion.chunk type); gateway/ai_mesh_gateway/secure_streaming.py (no tool_use buffering logic)",
    "implementationApproach": "Extend stream_orchestration to accept stream_type parameter ('chat.completion' | 'response'). For response-type streams: (1) emit {'type': 'response.created', 'response': {id, created}} immediately after headers; (2) on each LLM token, inspect if content (text) or tool_use (function call in progress): emit response.output_text.delta or response.output_tool_use.delta with the partial token; (3) accumulate tool_use.arguments as JSON tokens arrive (use json.JSONDecoder with partial decode support or simpler: buffer tokens + periodically emit delta of new tokens); (4) on stream completion (finish_reason='tool_calls' or 'stop'), emit response.completed with final response object shape; (5) emit terminal zeroshield trace frame before final [DONE]. Secure_streaming.py must extend its buffer to track tool_use items separately: buffer tool_name + arguments-so-far, check on each flush if complete JSON object formed, then run output guard on that object. Streaming output guard verdict (e.g., block tool_use on credential detection) must terminate the stream with an error event before the finalized response is sent.",
    "effort": "L",
    "firewallRisk": "Streaming tool_use.arguments as JSON requires incremental parsing. Attacker can send malformed/incomplete JSON (dangling quotes, unclosed braces) to confuse the accumulator and pass a redacted argument in the next chunk before it's recognized as part of the same argument object. Mitigation: (1) validate each complete tool_use object on finish (when 'finish_reason' indicates tool_calls done); (2) buffer entire tool_use object (name + arguments) before emitting to client (break streaming atomicity for tool_use, only stream text). Recommend: rewrite to use WebSocket instead of SSE for Responses API (allows bidirectional tool-result submission without multiple requests), but OpenAI SDK defaults to SSE, so stick with SSE + previous_response_id correlation for tool loops."
  },
  {
    "title": "Tool loop token accounting incomplete (TPM limits not cumulative across tool turns)",
    "severity": "High",
    "detail": "proxy_chat enforces per-key TPM limit @ L4750-4793: rate_limiter.check_rate_limit(auth_ctx.key_hash, rate_limit_tpm, estimated_request_tokens). Responses API tool loop is multiple requests (initial /v1/responses + N follow-ups with previous_response_id + tool_results). Each request independently checks TPM, resetting the counter if the loop consumes multiple tool turns. If limit is 100k TPM per day and tool loop makes 10 turns with 5k tokens each, each turn passes the 100k check individually (5k < 100k), but cumulative 50k should succeed\u2014however, it may exceed limit if there are concurrent requests. More critically, TPM resets on turn start, so a 10-turn tool loop can collectively exhaust 10x the intended quota.",
    "files": "gateway/ai_mesh_gateway/main.py (L4750-4793 rate_limiter.check_rate_limit); gateway/ai_mesh_gateway/rate_limiter.py (per-key TPM tracking, no cumulative tool-loop awareness)",
    "implementationApproach": "Extend rate_limiter to accept optional response_id parameter. When previous_response_id is provided: (1) retrieve stored response_id \u2192 {cumulative_tokens_so_far} from Redis; (2) add this request's estimated tokens to the cumulative total; (3) check against rate_limit_tpm (cumulative total, not per-turn); (4) on success, update stored response_id entry with new cumulative_tokens and TTL remaining; (5) fail with 429 'tool_loop_token_limit_exceeded' if cumulative exceeds limit. Emit telemetry with event_type='rate_limit_check' and metadata {response_id, turn_number, cumulative_tokens, limit_tpm, action: 'allow' | 'block'}. Org-level TPM ceiling (L4706-4747) must also be aware of tool loops: org_tpm_limit check must sum tokens across concurrent tool loops for that org (requires Redis set of active response_ids per org, increment on new response, decrement on completion).",
    "effort": "M",
    "firewallRisk": "Tool loop DoS: attacker can exhaust org/key TPM quota by initiating a tool loop and deliberately submitting tool results that trigger more tool calls (e.g., tool 'search' returns a list, next turn loops over list with a second tool). Cumulative token limit mitigates this but must be enforced strictly. Fail-closed: if cumulative token accounting is uncertain (Redis unavailable, response_id expired), deny the tool-loop request (require caller to start fresh, lose tool context). Never allow a tool turn to proceed if cumulative tokens ~= limit (enforce margin, e.g., cumulative >= limit - 1000 tokens \u2192 429)."
  },
  {
    "title": "No response_format support (structured output / JSON mode for Responses API)",
    "severity": "Medium",
    "detail": "Responses API supports response_format: {type: 'json_object' | 'text'} (or provider-specific schema). proxy_chat L64 filters passthrough params (temperature, top_p, max_tokens, stop, tools, tool_choice, response_format, seed, ...), so response_format is forwarded to LiteLLM. However, Responses API response envelope must validate the response structure matches the specified format (or provider enforces it upstream). If response_format.type='json_object' and the model returns invalid JSON in response.choices[0].response.text, gateway must detect and redact/block or emit warning. Current output guard (L1078) does not validate response structure against requested format.",
    "files": "gateway/ai_mesh_gateway/main.py (L64 _PASSTHROUGH_PARAMS includes response_format); gateway/ai_mesh_gateway/output_guard.py (no schema validation)",
    "implementationApproach": "Extract response_format from request body (L3-1, before LLM call). If response_format.type='json_object': (1) on 200 response, parse response.choices[0].message.content (or response.text in Responses API) as JSON; (2) if parse fails, emit warning (not block, model may have tried) with threat_type='structured_output_parse_error' and action='flag'; (3) if response_format.schema is provided, validate parsed JSON against schema (jsonschema library). Integrate into _apply_output_guard_nonstream (L1078) via a new check _validate_response_structure(response, response_format) \u2192 verdict. Fail-open on validation (ship the malformed output, flag for review).",
    "effort": "S",
    "firewallRisk": "Low. JSON schema validation is deterministic (no model involved). Malformed JSON is a model error, not a security threat. Flag but allow. If schema validation is expensive (large schema tree), cache compiled validators by schema hash."
  },
  {
    "title": "No factual grounding for tool-loop context (hallucination scoring ignores tool results)",
    "severity": "Medium",
    "detail": "Output guard (output_guard.py L87-89) scores hallucination risk using _RAG_GROUNDED_HALLUCINATION_THRESHOLD (0.2) when RAG context is present, else _NO_CONTEXT_HALLUCINATION_THRESHOLD (0.45). Tool-loop requests where the model has access to tool results (previous tool outputs fed back as context in messages) should lower hallucination threshold (tool results = external grounding, like RAG). Currently, _detect_rag_request (L4822) checks for 'rag_metadata' / 'context' fields in body, not for tool result presence. A tool loop with 10 search results returned as context should use the 0.2 threshold, but gateway will use 0.45 (ungrounded).",
    "files": "gateway/ai_mesh_gateway/main.py (L4822 _detect_rag_request); gateway/ai_mesh_gateway/output_guard.py (L87-89 hallucination thresholds)",
    "implementationApproach": "Extend _detect_rag_request to also check: if messages contain tool_result or function_result role with substantive content (len > 100 chars), consider it grounded. Pass is_rag=True to INPUT_SCANNER.scan_prompt and OUTPUT_GUARD.evaluate (if not already grounded by RAG). Minor code change, low risk.",
    "effort": "S",
    "firewallRisk": "None. Lowering hallucination threshold for grounded tool contexts is more permissive, not more restrictive. False negatives (miss a hallucination) are possible but acceptable (operator can flag post-hoc)."
  }
]