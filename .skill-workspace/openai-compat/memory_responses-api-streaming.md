# responses-api-streaming

## Current
ZeroShield currently implements OpenAI-compatible chat/completions streaming (SSE) with:

1. **Stream orchestration** (stream_orchestration.py:2-729): Manages SSE lifecycle (TTFT tracking, usage chunk synthesis, terminal zeroshield trace frame). Single choke point at stream_with_finalize() ~L500 that injects one trace frame before [DONE].

2. **Output scanning** (secure_streaming.py:1-622): Buffers SSE chunks, detects content deltas at sentence boundaries, applies OutputGuard verdict (block/redact/flag), emits redacted SSE or error chunks. Streaming PII-race prevention via STREAM_LOOKAHEAD_BYTES.

3. **Output guard** (output_guard.py:1-916): Runs async inspect() on buffered text; returns OutputVerdict with action (block/redact/flag), threat_type, detail, matched_patterns, compliance_tags.

4. **Terminal trace frame** (stream_orchestration.py:L417-486, build_stream_trace_frame): Emits ChatCompletionChunk with choices=[] + zeroshield metadata object before [DONE]. Captures mid-stream guard_action severity tracking (L40-84, record_guard_action). M-51 contract: one frame per termination, exactly before [DONE].

5. **Non-streaming metadata** (main.py, pipeline_trace.py): Builds full zeroshield metadata dict with routing/guard/policy context, then redacts client-safe subset. pipeline_trace.py::enrich_zeroshield_from_verdict enriches with guard-model fields.

6. **Models/endpoints exposed**: /v1/chat/completions (chat, stream:true), /v1/embeddings, /v1/rag/*, /v1/models, /v1/vector/* (all run same firewall pipeline).

**Current gaps**: No /v1/responses endpoint. No Responses API event types (response.created, response.in_progress, response.output_item.added, response.output_text.delta, response.output_text.done, response.completed). No streaming parameter translation (litellm chat chunks → typed Responses events).

## OpenAI Spec
OpenAI Responses API (/v1/responses, client.responses.create()):

**Event sequence** (server-sent events, application/x-ndjson or text/event-stream):
- event: response.created (response_id, response object with status=queued)
- event: response.in_progress (response status)
- [repeated] event: response.output_item.added (output_item with index, type, content)
- [repeated] event: response.content_part.added (within output text item)
- [repeated] event: response.output_text.delta (delta text chunk)
- event: response.output_text.done (final text, completion)
- event: response.output_item.done (item finalization)
- event: response.completed (final response object with usage, status=completed)
- [on error] event: response.error (error envelope)

**Typed output items**: ResponseOutputItem (union of ResponseOutputText, ResponseOutputAudio, ResponseReasoningItem, ResponseFunctionToolCall, ResponseCustomToolCall, etc.).

**Input**: ResponseCreateParams {response: ResponseCreateParamsResponse {...}, stream: bool, ...}. Supports typed inputs (content blocks w/ type, file_search/web_search/function tools, previous_response_id for continuation).

**State tracking**: response_id (uuid), status (queued|in_progress|completed|cancelled|failed), moderation flag, usage (prompt/completion/cache tokens).

**HTTP 200 streaming response**: application/x-ndjson or text/event-stream. Each event is a JSON line prefixed with "event: TYPE\n" + "data: {...JSON...}\n\n".

## Reusable hooks
1. **stream_with_finalize** (stream_orchestration.py:L489-662): Single choke point that wraps inner generator with instrumentation, injects terminal trace frame before [DONE], finalizes circuit/rate/telemetry. Reuse by passing ResponsesEventEmitter as inner generator instead of SecureStreamingResponse.

2. **Output guard invocation** (secure_streaming.py:L209-327, _flush_buffer): Buffers content, runs output_guard.inspect(full_text, context_chunks, org_config, org_slug), records_guard_action to StreamRunMetrics. Move this logic into ResponsesEventEmitter to scan Responses events (response.output_text.delta, response.output_item.added). Core verdict-to-action mapping (block/redact/flag) is reusable unchanged.

3. **StreamRunMetrics** (stream_orchestration.py:L45-97): Tracks TTFT, chunk count, usage, guard_action, guard_threat_type, matched_patterns. Extend with response_type field (default 'chat', can be 'response') and moderation flag. Reuse record_guard_action method unchanged. Same finalization hooks apply.

4. **build_stream_zeroshield_base** (main.py:~L1430): Builds client-safe zeroshield metadata dict from request context (route_selection, scan_verdict, redacted_prompt). Reuse unchanged for /v1/responses to build base trace. Call pipeline_trace.enrich_zeroshield_from_verdict identically.

5. **Input scanner (tier-1) and output guard (tier-2)** (INPUT_SCANNER, OUTPUT_GUARD globals in main.py): Already initialized and configured per-org. Responses endpoint reuses same scanner instances: input_scanner on request.response.input (text items), output_guard on streaming deltas (same async inspect contract).

6. **Rate limiter and circuit breaker** (RateLimiter, CircuitBreaker from rate_limiter.py, circuit_breaker.py): Initialized once and passed to finalize_stream via StreamFinalizeHooks. Reuse token-based TPM cap (input/output tokens sum to total_tokens) across both chat/completions and responses API (no separate rate-limit tracks needed).

7. **Telemetry producer** (TELEMETRY global, telemetry_ops.py): Emits events via emit_query_audit_event, build_telemetry_event. Extend event_type to include 'response_created', 'response_completed', 'response_error' (distinct from 'chat_complete'). Reuse same emit pipeline, just label response_type in metadata.

## Gaps
[
  {
    "title": "Missing /v1/responses endpoint and response-creation flow",
    "severity": "Critical",
    "detail": "No POST /v1/responses (client.responses.create) endpoint. Missing request validation, response_id generation (uuid4), initial response object creation, tri-state (queued/in_progress/completed). Must accept ResponseCreateParams: model, messages (or response input items), tools (with tool_choice typed), temperature, max_completion_tokens, moderation, stream boolean, previous_response_id (for continuation).",
    "files": "gateway/ai_mesh_gateway/main.py (needs new @app.post endpoint ~L3484 analogue)",
    "implementationApproach": "Reuse proxy_chat eligibility_phase (auth, model allowlist, org config) to validate request. Create UUID4 response_id. Emit response.created event (with response object, status=queued). Immediately transition to response.in_progress before streaming to LLM. Route via existing LLM_ROUTER (already supports tool-call structures from chat/completions). Non-streaming requests return final response object; streaming returns SSE with event-prefixed format (not just data: lines).",
    "effort": "L",
    "firewallRisk": "Response-creation endpoint opens new attack surface: oversized input_items, excessive tool definitions, chained previous_response_id loops (unlimited conversation depth), malicious tool schemas. Mitigation: reuse input_scanner tier-1 on request body + tool definitions; enforce response_id \u2192 previous_response_id DAG acyclicity; cap input_items count (analogue to messages array limit ~200); rate-limit per-response creation (prevent response-spam)."
  },
  {
    "title": "Missing Responses API streaming event types and translation layer",
    "severity": "Critical",
    "detail": "Current SSE emits OpenAI chat.completion.chunk format (choices[0].delta.content). Responses API requires typed semantic events: response.created, response.in_progress, response.output_item.added (with item.type/id), response.content_part.added, response.output_text.delta, response.output_text.done, response.completed. Must translate litellm chat acompletion_stream() deltas (string chunks with delta.content) into structured Responses output_items and sub-events.",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py, secure_streaming.py (extend to emit Responses event types)",
    "implementationApproach": "Create ResponsesEventEmitter class (analogue to SecureStreamingResponse.__aiter__). Wrap LLM_ROUTER.acompletion_stream output. For each chat delta chunk: (1) parse JSON, extract delta.content. (2) Emit response.output_item.added (once, when first content delta arrives; type='text', id=uuid). (3) Emit response.content_part.added (once per output item creation). (4) Emit response.output_text.delta for each content delta. On [DONE]: emit response.output_text.done, response.output_item.done, response.completed (with final usage). On error: emit response.error. Envelope each event as 'event: TYPE\\ndata: {JSON}\\n\\n' (NDJSON format per Responses API contract).",
    "effort": "M",
    "firewallRisk": "Event-type translation is lossy (typed Responses \u2192 untyped chat chunks). Output guard must inspect EVERY event before emission (already done at secure_streaming._flush_buffer level, but must adapt to Responses event structure). New risk: tool_calls (function_tool / custom_tool items) streamed as deltas \u2014 ensure all tool-call arguments are fully scanned before tool execution (mitigate function-injection attacks). Mitigation: output guard treats tool_calls deltas identically to content deltas; buffer full tool-call argument before deeming it safe."
  },
  {
    "title": "Missing output guard integration into Responses event stream",
    "severity": "High",
    "detail": "Responses events (response.output_text.delta, response.output_item.added) must flow through the same firewall pipeline as chat/completions (output_guard.inspect, buffer+scan+verdict). Scanning must complete BEFORE event is emitted to client. Terminal zeroshield trace frame must inject into Responses stream as final event before response.completed.",
    "files": "gateway/ai_mesh_gateway/secure_streaming.py, stream_orchestration.py",
    "implementationApproach": "Extend SecureStreamingResponse to handle Responses event format: (1) Parse 'event: TYPE' and 'data: {JSON}' lines separately. (2) For response.output_text.delta: extract text, buffer into _content_buffer (same lookahead logic). On flush boundary: run output_guard.inspect() \u2192 verdict (block/redact/flag). (3) Rebuild event with sanitized text if redact action. (4) For response.output_item.done: final scan of complete item (not buffered; item is complete). (5) Inject custom 'event: response.zeroshield' event (new, non-standard but mirrors M-51 terminal-frame contract) immediately before 'event: response.completed' to carry enforcement metadata (action, threat_type, detail, matched_patterns, processing_time_ms). Defensive: if output_guard unavailable, emit zeroshield event with scan_degraded=true.",
    "effort": "M",
    "firewallRisk": "Responses event stream is event-driven, not chunked. A response.output_item.done event signifies item completion; if a PII token spans from one delta to the next, the lookahead tail logic (STREAM_LOOKAHEAD_BYTES) must still work. Risk: an output_item.done event could be emitted while a trailing PII partial-token is still in the lookahead window. Mitigation: enforce that an output_item.done is only emitted AFTER a final flush of the full item (including lookahead tail) has been scanned. If a PII match is detected in the tail during that final flush, block the output_item.done event and emit response.error instead."
  },
  {
    "title": "Missing Responses moderation flag and usage tracking",
    "severity": "High",
    "detail": "Responses API response object carries moderation: boolean (whether output violated moderation policy) and usage: {prompt_tokens, completion_tokens, cache_creation_input_tokens?, cache_read_input_tokens?}. Terminal response.completed event includes final response object with these fields. Must track partial usage mid-stream and finalize at completion.",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py (StreamRunMetrics), main.py",
    "implementationApproach": "Extend StreamRunMetrics to capture moderation flag (boolean, set when output_guard action='block' or scan_degraded). In response.created event, moderation=false initially. In response.completed event, set moderation=true if output_blocked or guard_action='block'. Usage tracking: reuse existing stream_orchestration._extract_usage_from_sse_line (parses chat chunks for usage field). For Responses format, usage may be embedded in response.completed event or sent as separate response.usage event. Emit response.completed with final response object: {id, status='completed', usage: {prompt_tokens, completion_tokens, total_tokens}, moderation: bool, output: [items]}.",
    "effort": "S",
    "firewallRisk": "Moderation flag visibility could expose internal guard state (e.g., moderation=true before output.completed reveals policy violation before client reads all content). Mitigation: emit moderation=true in response.completed (final event after all content), not mid-stream; never expose internal guard threat_type in the public moderation flag (boolean only). Usage tracking allows token-level billing; ensure partial-usage in mid-stream events matches final usage in response.completed (internal consistency check on StreamRunMetrics.usage across the full stream)."
  },
  {
    "title": "Missing tool-call streaming (function_tool, custom_tool, file_search, web_search items)",
    "severity": "High",
    "detail": "Responses API supports typed tool calls (ResponseFunctionToolCall, ResponseCustomToolCall, ResponseFileSearchToolCall, ResponseWebSearchToolCall, etc.) as output_items. Tool-call arguments stream as deltas (response.function_call_arguments.delta events). Must parse tool definitions from request, match incoming tool-call streams to those definitions, validate argument structure, scan argument text for PII/injection before execution.",
    "files": "gateway/ai_mesh_gateway/main.py (tool validation), secure_streaming.py (argument scanning), stream_orchestration.py",
    "implementationApproach": "Reuse existing tool-call handling from chat/completions (main.py already parses 'tools' array and validates against org tool registry). For Responses: (1) Store tool definitions in StreamLaunchContext. (2) When response.function_call_arguments.delta arrives, accumulate argument text in _content_buffer (same as response.output_text.delta). (3) On flush or when tool-call item complete: run output_guard.inspect(full_arguments_json_str) \u2192 verdict. (4) Block/redact if tool argument contains PII/injection/jailbreak. (5) Tool execution (actual function invocation) happens server-side (outside streaming scope), but validate before execution begins. Scanning ensures malicious arguments never reach the tool executor.",
    "effort": "M",
    "firewallRisk": "Tool-call arguments are JSON strings, which are harder to scan than plain text (nested structures, escaped characters). Example: a function argument '{\"query\":\"SELECT * FROM users\"}' with embedded SQL injection is plain text once unescaped, but the scanner must handle both forms. Risk: output_guard.inspect(escaped_json) may miss injection inside a string literal. Mitigation: output_guard must parse tool arguments (json.loads if valid JSON, else treat as plain string); scan the unescaped/parsed form. For file_search and web_search items, the output is search results (URLs, snippets); scan those snippets for PII leakage (e.g., search result accidentally exposing internal IP)."
  },
  {
    "title": "Missing Responses API response_id continuation (previous_response_id parameter)",
    "severity": "Medium",
    "detail": "Responses API supports previous_response_id parameter to continue a prior response (e.g., user feedback loop where client provides previous response_id and system appends to it). Must validate response_id \u2192 previous_response_id chains exist, prevent cycles, enforce rate limits on chained requests to avoid abuse.",
    "files": "gateway/ai_mesh_gateway/main.py (response validation phase)",
    "implementationApproach": "In proxy_responses (new endpoint), check request.previous_response_id if present. Validate response_id exists in a short-lived cache (Redis, TTL ~1h). Verify response_id is owned by the same org/user (permission check). Enforce max-chain depth (e.g., max 5 continuations per original response) to prevent conversation-explosion DoS. Store response_id \u2192 creation_timestamp in cache at stream end; on continuation request, verify the original response is recent (<1h old). Rate-limit: count continuation chains per user/org in finalize_stream phase.",
    "effort": "S",
    "firewallRisk": "Malicious user chains previous_response_id to an old response from a DIFFERENT org or another user. Escalation: if permission check is missing, user-A can append to user-B's response. Mitigation: tie response_id to (org_slug, user_id) tuple; reject continuation if previous_response_id's org/user differs from current request's auth context. Also: enforce previous_response_id != response_id (prevent self-loops); audit continuation chains in telemetry (detect unusual chain depth patterns indicating abuse)."
  },
  {
    "title": "Missing Responses input_items alternative to messages (typed content blocks)",
    "severity": "Medium",
    "detail": "Responses API accepts input via 'response.input' parameter (alternative to messages). Input can carry multimodal content blocks (text, image, file, audio) with explicit types. Must validate input item structure, scan content (text/image OCR), enforce file size limits, validate file types (not arbitrary binary).",
    "files": "gateway/ai_mesh_gateway/main.py (request validation)",
    "implementationApproach": "Parse request.response.input (list of input items) if present (fallback to messages if absent, for backward compat). Validate each input_item: type in {text, image, image_url, audio, file, document} (check OpenAI schema), required fields present. For text items: scan via input_scanner (same as messages[].content). For image/audio/file items: enforce size limits (<100MB for files), validate MIME types (image: jpeg/png/webp/gif; audio: mp3/wav/m4a; file: pdf/txt). File items may carry raw base64 or file_id reference; if file_id, verify it's owned by the org (permission check). Multimodal scanning: for image input_items, pass to input_scanner if it supports vision (bedrock tier-2 guard does). For audio input_items, transcribe if available or pass byte-count as input length to rate limiter.",
    "effort": "M",
    "firewallRisk": "File uploads are a major attack vector: oversized files cause OOM, malicious PDFs execute code if a PDF parser is involved, ZIP bombs extract to GB. Risk: a user uploads a file_id that belongs to another org's sensitive document. Mitigation: (1) Enforce strict file-size limits per file type. (2) File ownership check: file_id must be in current org's file store (tie to org_id in policy). (3) Disable PDF parsing or sandbox it (never execute embedded scripts). (4) Scan file contents (ZIP integrity check, no embedded executables) before accepting. (5) Rate-limit by file count and cumulative size (prevent file-spam)."
  },
  {
    "title": "Missing event-stream format negotiation (NDJSON vs SSE with event: prefix)",
    "severity": "Medium",
    "detail": "OpenAI Responses API streams events in 'application/x-ndjson' or 'text/event-stream' (with 'event: TYPE' prefix on each). Current chat/completions uses 'text/event-stream' with 'data: {...}' lines and no event type. Responses requires explicit event type in the stream so clients can parse by event.type (response.created, response.completed, etc.).",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py (build_base_stream_headers, finalized stream format)",
    "implementationApproach": "For /v1/responses endpoint, return StreamingResponse with media_type='application/x-ndjson' (or 'text/event-stream'; both are valid). Emit events as: 'event: response.created\\ndata: {JSON}\\n\\n' (SSE format with event field) or as NDJSON (one JSON per line, with 'event' field inside). Use the SSE format (event: prefix) for consistency with standard SSE clients and parity with OpenAI docs. Emit response.zeroshield events the same way: 'event: response.zeroshield\\ndata: {JSON}\\n\\n' before 'event: response.completed'. Content-Type header: 'text/event-stream; charset=utf-8'.",
    "effort": "S",
    "firewallRisk": "Format mismatch breaks client parsers. Example: client expecting NDJSON (one JSON per line) receives SSE 'event:' prefixed lines \u2192 parse error. No security risk if format is consistent (all events in same format), but interoperability failure. Mitigation: document format choice clearly (SSE with event: prefix); include Content-Type header; test with OpenAI SDK (client.responses.create(..., stream=True))."
  },
  {
    "title": "Missing finalization hooks for Responses (telemetry, circuit breaker, rate limiting parity)",
    "severity": "Medium",
    "detail": "Current stream finalization (stream_orchestration.finalize_stream ~L277) records circuit breaker status, updates rate limits (TPM), emits telemetry. Must be adapted for Responses API so response-creation events are tracked separately from chat/completions events (distinct telemetry metrics: response_created_count, response_completed_count, response_error_count, response_cancelled_count).",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py (finalize_stream, StreamFinalizeHooks), metrics.py",
    "implementationApproach": "Extend StreamFinalizeHooks to include a response_type field (default 'chat', can be 'response'). In finalize_stream, emit telemetry event with response_type label. Reuse circuit_breaker.record_success/record_error but label by response_type. Rate limiter: token count (estimated_tokens) is same across both API types, so existing rate_limit_tpm logic works unchanged. Telemetry: emit distinct event types (event_type='response_created' for response.created, event_type='response_completed' for response.completed, event_type='response_cancelled' for cancellations). Dashboard queries can then filter by response_type to separate response API usage from chat/completions usage.",
    "effort": "S",
    "firewallRisk": "If finalization hooks are missing or misconfigured, rate limits may not be enforced (Responses requests exempt from TPM cap). Risk: malicious user floods /v1/responses endpoint without being rate-limited, causing quota burn. Mitigation: ensure rate_limiter.record_usage is called in finalize_stream for ALL streaming response types (chat and responses); verify via telemetry audit that Responses requests are appearing in rate-limit counters."
  },
  {
    "title": "Missing input and output token counting for Responses (separate from chat/completions)",
    "severity": "Low",
    "detail": "OpenAI offers /responses/input_tokens POST endpoint to count tokens in a Responses input request (without executing it). Must implement endpoint, accept ResponseInputTokenCountParams, run token counter on request body, return InputTokenCountResponse {input_tokens: int}.",
    "files": "gateway/ai_mesh_gateway/main.py (new @app.post /responses/input_tokens endpoint)",
    "implementationApproach": "Create /v1/responses/input_tokens endpoint (analogue to chat/completions, no streaming). Parse request.response.input or request.messages. Use existing token-counting logic from litellm or built-in counter (e.g., tiktoken for OpenAI models). Run through input_scanner (validate input) but do NOT execute/forward to LLM (dry run). Return {input_tokens: int}. Support both GET /responses/{response_id}/input_items (list) and POST /responses/input_tokens (count) per OpenAI spec.",
    "effort": "S",
    "firewallRisk": "Token-counting endpoint is read-only (no inference execution) but must still validate request format and size (prevent oversized input_items from crashing counter). Mitigation: same input-validation as /v1/responses creation (file size limits, item count caps, etc.). Token counter must not leak internal model info (e.g., token count \u2260 model ID) \u2014 return only token count, no model details."
  }
]