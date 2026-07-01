# OpenAI Responses API (/v1/responses) — Core Gateway Data Plane Surface

## Current
ZeroShield currently implements 3 of 12 OpenAI-compatible product surfaces:

1. **POST /v1/chat/completions** (main.py:3484-5721)
   - Input: `{model, messages[], temperature, max_tokens, tools, ...}`
   - Output: `{id, object: "chat.completion", choices[], usage, zeroshield: {...}}`
   - Pipeline stages: auth → policy-check → input-scan (Tier-1/2) → LLM-route → output-guard → redaction → telemetry
   - Streaming via SSE (stream_orchestration.py:stream_with_finalize), terminal trace frame with empty choices

2. **POST /v1/embeddings** (main.py:7168-7760)
   - Input: `{model, input: string|string[], encoding_format}`
   - Output: `{object: "list", data: [{object: "embedding", embedding[], index}], usage}`
   - Lightweight: auth → model-check → LLM-route → response-passthrough (no guardrails)

3. **GET /v1/models** (main.py:10410-10474)
   - Output: `{object: "list", data: [{id, object: "model", owned_by}]}`
   - Auth-gated, org-filtered model enumeration

**NOT IMPLEMENTED:**
- Responses API (client.responses.create) — no POST /v1/responses route
- Assistants/Threads/Runs API — no POST /v1/assistants/* routes
- Audio (speech-to-text, text-to-speech) — no /v1/audio/* routes
- Vision (image analysis) — embedded in chat multimodal content, not dedicated surface
- Fine-tuning — no /v1/fine_tuning/* routes
- Files/Batch — no /v1/files/* or /v1/batches/* routes
- Usage/billing API — no /v1/usage/* routes
- Admin/Org/Project APIs — control-plane only (Django: api/admin/, api/auth/), not gateway

**Enforcement Pipeline (reusable for Responses API):**
- Input scanning: `scanner.py::InputScanner.scan_prompt()` / `scan_prompt_with_tier2()` (Tier-1: pattern/regex; Tier-2: Bedrock guard model)
  - Verdict shape: `{action, threat_type, confidence, detail, matched_patterns, reason_code, tier}` (scanner.py:Verdict dataclass)
- Policy evaluation: `_policy_check_cached()` (main.py:2740) → org config + policy rules
- Output guard: `output_guard.py::OutputGuard.inspect_response()` → `{action, threat_type, confidence, detail, matched_patterns, matched_values, scan_degraded}`
- Redaction: `patterns.py::redact_all()` (deterministic, PII-safe)
- Telemetry: `_emit_telemetry()` / `enqueue_job()` (pipeline_trace.py + EnforcementEvent DB)
- Streaming orchestration: `stream_orchestration.py::stream_with_finalize()` → SSE + terminal trace frame + finalization (circuit-breaker, TPM reconciliation)

## OpenAI Spec
**OpenAI Responses API Contract** (as of OpenAI API docs, Jan 2025):

```
POST /v1/responses
Authorization: Bearer sk-...
Content-Type: application/json

Request body:
{
  "model": "gpt-4o",                           # required, string
  "input": "string" | [{...}],                 # required; string OR typed input items (text/image/audio/doc)
  "instructions": "string",                    # optional, system instructions
  "modalities": ["text", "audio"],             # optional, output format(s)
  "store": true | false,                       # optional, persist response
  "metadata": {...},                           # optional, client metadata
  "max_output_tokens": 4096,                   # optional, int
  "temperature": 1.0,                          # optional, float 0-2
  "reasoning": "string" | {...},               # optional, structured reasoning input
  "previous_response_id": "resp_...",          # optional, reference prior response for context/correction
  "stream": true | false                       # optional, SSE streaming
}

Response (200 OK):
{
  "id": "resp_001abc...",
  "object": "response",
  "created": 1700000000,
  "model": "gpt-4o",
  "status": "completed" | "in_progress" | "failed",
  "output": [
    {
      "type": "text",
      "text": "..."
    },
    {
      "type": "audio",
      "audio": "base64_or_url",
      "transcript": "..."
    },
    {
      "type": "document",
      "document_id": "doc_..."
    }
  ],
  "output_text": "concatenated text from output items",  # convenience field
  "usage": {
    "input_tokens": 100,
    "output_tokens": 50,
    "total_tokens": 150
  },
  "input_tokens": 100,           # deprecated alias for usage.input_tokens
  "output_tokens": 50,           # deprecated alias for usage.output_tokens
  "metadata": {...},             # echo back client metadata
  "stored": true                 # true if store=true and persisted
}

Streaming (stream=true):
event: response.created
data: {"id":"resp_...","object":"response",...}

event: response.output_text.delta
data: {"delta":"text chunk"}

event: response.completed
data: {"id":"resp_...","object":"response",...,"status":"completed",...}

Error bodies (4xx/5xx, same as chat.completions):
{
  "error": {
    "message": "...",
    "type": "invalid_request_error" | "authentication_error" | "rate_limit_error" | ...,
    "code": "invalid_model_id" | ...
  }
}
```

**Key differences from chat.completions:**
1. **Input polymorphism**: `input` can be a plain string OR an array of typed input items `{type, text|image_url|audio_url|document_id}`
2. **Output polymorphism**: response is an array of `{type, ...}` items (text, audio, document, etc.), not choices with message.content
3. **Statefulness**: `previous_response_id` allows follow-ups and multi-turn context without re-specifying prior history
4. **Modalities**: client declares which modalities are acceptable (`modalities: ["text", "audio"]`)
5. **Streaming events**: separate `response.created`, `response.output_text.delta`, `response.completed` (not chunk-based like SSE)
6. **Storage**: `store=true` → response persisted server-side with an id for later retrieval

## Reusable hooks
**Existing Pipeline Functions for Responses API Reuse:**

1. **Authentication & Request Context**
   - `middleware.py::validate_api_key()` (path: /v1/responses auto-matched)
   - `request.state.auth_context` populated by middleware (org_id, user_id, project_id, allowed_models, rate_limit_tpm)

2. **Input Validation & Normalization**
   - `openai_request_normalizer.py::normalize_openai_chat_request()` (extend for Responses input)
   - `main.py::_extract_prompt_from_messages()` (adapt to extract text from Responses input items)
   - Boundary validators: type checks on body/model/input (similar to chat at lines 3665-3890)

3. **Input Security Pipeline**
   - `scanner.py::InputScanner.scan_prompt()` (Tier-1 pattern scan, Responses: scan extracted input text)
   - `scanner.py::InputScanner.scan_prompt_with_tier2()` (Tier-2 Bedrock guard model, async post-LLM option)
   - `pipeline_trace.py::build_pipeline_trace()` (input decision attribution + tracing)
   - Verdict folding: `_apply_policy_action_to_verdict()` (main.py:~426)

4. **Policy Evaluation**
   - `main.py::_policy_check_cached()` (cached org policy lookup + rule evaluation)
   - Policy sync from control plane: `CONFIG_SYNC.evaluate(...)` (org-scoped rules)

5. **LLM Routing**
   - `llm_router.py::LLMRouter.acompletion()` (Responses normalizes to chat.completions before calling)
   - `llm_router.py::LLMRouter.acompletion_stream()` (for streaming Responses, output formatted as Responses events post-emission)
   - Model allowlist check: `_is_model_allowed()` (main.py:~3458)

6. **Output Security Pipeline**
   - `output_guard.py::OutputGuard.inspect_response()` (extend for Responses output items)
   - PII/secret detection: `patterns.py::detect_pii()`, `detect_credential_exposure()` (applied to all text output)
   - Redaction: `patterns.py::redact_all()` (deterministic, used in output-guard + client response)
   - Hallucination scoring: `output_guard.py::_score_hallucination_risk()` (inspect output text)

7. **Streaming Orchestration**
   - `stream_orchestration.py::StreamRunMetrics` (track TTFT, duration, guard actions — reuse for Responses events)
   - `stream_orchestration.py::stream_with_finalize()` (finalization loop, circuit-breaker, TPM reconciliation — adapt event emission)
   - `secure_streaming.py::SecureStreamingResponse` (output-guard mid-stream, Responses streaming reuses this)

8. **Telemetry & Audit**
   - `_emit_telemetry()` (main.py: event emit to control plane EnforcementEvent table)
   - `_audit_fire_and_forget()` (decision logging for analytics)
   - `enqueue_job()` (async tasks: Tier-2 post-scan, response storage, audit)
   - Trace building: `pipeline_trace.py::build_pipeline_trace()`, `enrich_zeroshield_from_verdict()` (reusable for Responses zeroshield metadata)

9. **Rate Limiting & Circuit Breaking**
   - `rate_limiter.py::RateLimiter.check_and_deduct()` (TPM enforcement, reuse with Responses usage object)
   - `circuit_breaker.py::CircuitBreaker.check()` (upstream health, model-level circuit, reuse)

10. **Response Building & Redaction**
    - `main.py::_build_zeroshield_metadata()` (comprehensive metadata dict from all verdicts)
    - `main.py::_redact_for_client_response()` (client-safe allowlist filtering, applies to zeroshield payload)
    - `main.py::_scrub_trace_for_client()` (evidence stripping from pipeline_trace)
    - `pipeline_trace.py::_mask_value_for_detail()` (PII masking in detail strings)

**Integration Points (no code duplication, composition via function calls):**
- proxy_responses calls INPUT_SCANNER (shared instance, Tier-1/2 verdicts reused)
- proxy_responses calls LLM_ROUTER.acompletion (normalized Responses → chat.completions body)
- proxy_responses calls OUTPUT_GUARD.inspect_response (shared instance, verdict reused)
- proxy_responses calls _emit_telemetry (shared telemetry sink, same event schema)
- proxy_responses calls stream_orchestration.stream_with_finalize for streaming (shared finalization, format → Responses events)
- Auth/rate-limit applied by existing middleware (no per-route code needed)

**No Duplication Pattern:**
All enforcement decisions (scan verdict, policy verdict, guard verdict, routing selection) flow through a single pipeline; Responses API consumes the same pipeline with format adapters at input (string/items → messages) and output (choices → output items). The zeroshield metadata, telemetry, redaction, and error handling are format-agnostic and reuse existing functions.

## Gaps
[
  {
    "title": "Missing POST /v1/responses route definition",
    "severity": "Critical",
    "detail": "No endpoint handler exists. Callers hitting POST /v1/responses receive a 404. The route must parse the request body (input as string or typed items), validate model/inputs, apply auth, and thread through the enforcement pipeline.",
    "files": "gateway/ai_mesh_gateway/main.py (new function ~11000+); ai_mesh_shared/openai_request_normalizer.py (add responses normalization)",
    "implementationApproach": "Add async def proxy_responses(request: Request, x_user_id, x_endpoint_id, x_agent_data) handler after proxy_embeddings (~line 7800). Mirror proxy_chat structure: (1) parse & validate request JSON (2) normalize input string->messages or extract text from typed items (3) call _policy_check_cached() (4) call INPUT_SCANNER.scan_prompt() on extracted text (5) route to LLM_ROUTER.aresponse() or acompletion() (6) apply OUTPUT_GUARD.inspect_response() to output items (7) build ZeroShield response metadata (8) return {id, object: \"response\", output[], output_text, usage, zeroshield: {...}}. Reuse auth + rate-limit middleware (request.state.auth_context).",
    "effort": "M",
    "firewallRisk": "Responses API accepts structured input items (image_url, audio_url, document_id) that chat.completions does not; scanner currently only scans text. Audio/image URLs and doc IDs must be scanned for injection/policy violations. Mitigation: (a) scan input string AND all text within typed items (b) scan URL schemes & hostnames against policy (c) defer audio/document scanning to downstream (LLM decides whether to fetch/process). Output items (text/audio/document) may contain PII/secrets \u2014 output_guard must inspect all text within response items, not just choices[0].message.content. Redaction must patch text within items, not replace the whole response. Streaming events (response.output_text.delta) need mid-stream guard inspection parity with chat.completions SSE."
  },
  {
    "title": "Input normalization: string vs. typed input items",
    "severity": "High",
    "detail": "Responses API accepts `input: string OR [{type: \"text\", text: \"...\"} | {type: \"image_url\", image_url: {...}} | {type: \"audio_url\", audio_url: {...}} | {type: \"document_id\", document_id: \"...\"} | ...]`. Normalizer must flatten typed items to a scannable prompt string (similar to _extract_prompt_from_messages) while preserving the typed structure for LLM forwarding.",
    "files": "ai_mesh_shared/openai_request_normalizer.py (add normalize_responses_input); gateway/ai_mesh_gateway/main.py (use in proxy_responses input validation)",
    "implementationApproach": "Add helper function normalize_responses_input(input_value) that: (1) if input is string, return (string_value, [{type: \"text\", text: input_value}]) (2) if input is list, iterate and extract text: for item in input if item[\"type\"] == \"text\" append item[\"text\"]; else if type == \"image_url\" append \"[image_url: ...]\"; else if type == \"audio_url\" append \"[audio_url: ...]\"; else append \"[document_id: ...]\". Return (concatenated_text, original_items). In proxy_responses, call this after input validation, use concatenated_text for scanner, pass original_items to LLM_ROUTER.",
    "effort": "S",
    "firewallRisk": "Incomplete extraction of text from typed items \u2192 injection bypasses. If audio/image URLs are not normalized to placeholder strings, a malicious {type: \"image_url\", image_url: \"<prompt-injection-in-query-params>\"} bypasses scanner. Mitigation: always fold URLs into the prompt text for Tier-1/2 scanning; Tier-2 guard model can contextualize URL semantic meaning."
  },
  {
    "title": "Output items inspection: text, audio, document types",
    "severity": "High",
    "detail": "output_guard.py::OutputGuard.inspect_response() currently accepts a completion dict with choices[0].message.content (chat-shaped). Responses API output is {output: [{type, text|audio|...}], output_text}. Guard must inspect all text within output items and handle non-text items (audio, document) gracefully.",
    "files": "gateway/ai_mesh_gateway/output_guard.py (add method inspect_response_items); gateway/ai_mesh_gateway/main.py (call it in proxy_responses output path)",
    "implementationApproach": "Add method inspect_response_items(output_items: list[dict]) -> OutputVerdict that: (1) extracts all text from items where type==\"text\" (2) scans for PII/secrets/hallucination via existing _check_pii_and_secrets, _score_hallucination, etc. (3) for type==\"audio\" skip text inspection but log presence (defer to LLM) (4) for type==\"document\" skip (assume server-side storage). Return highest-severity OutputVerdict across all text items. For redaction, add method redact_response_items(items, verdict) that patches text within matching items (e.g. masking PII in place), returns redacted items + new output_text. Hook into proxy_responses after LLM_ROUTER call, before response serialization.",
    "effort": "M",
    "firewallRisk": "Unscanned audio/document outputs bypass guard. Mitigation: document in trace that audio/document were intentionally deferred (output_guard reports \"audio_deferred_scan\" action); telemetry alerts on deferred output. Audio may contain speech-form PII (redaction harder); recommend monitoring flag rather than block for audio responses."
  },
  {
    "title": "Response-to-messages translation for routing context",
    "severity": "High",
    "detail": "Responses API is stateless per-call (no persistent threads like Assistants); however, `previous_response_id` allows referencing a prior response for context/correction. When previous_response_id is set, gateway must load the prior response and fold it into the messages array passed to LLM_ROUTER, mimicking OpenAI's internal context-binding. Current code has no prior-response cache or retrieval.",
    "files": "gateway/ai_mesh_gateway/main.py (add _load_previous_response_context helper); Redis or in-memory cache; database model in control plane",
    "implementationApproach": "Before calling LLM_ROUTER in proxy_responses, check if body[\"previous_response_id\"] is present. If so, (1) query a cache/DB for the prior response (store responses in Redis keyed by org_id + response_id, with TTL ~7 days) (2) reconstruct messages from prior response: [{role: \"user\", content: prior_request.input}, {role: \"assistant\", content: prior_response.output_text}] (3) prepend to the new messages before LLM call. This ensures the LLM has conversational context without explicit API message arrays (Responses API design). Fallback: if prior response not found or expired, emit a warning and proceed with current input only (don't block).",
    "effort": "M",
    "firewallRisk": "Prior response history is stored server-side; multi-tenant isolation risk: org A could potentially reference org B's response_id and leak its context. Mitigation: enforce org_id matching when loading prior response (assert prior_response.org_id == current_auth_ctx.org_id); emit audit event if mismatch. All prior responses in Redis must be org-scoped."
  },
  {
    "title": "Streaming response format (response.created, response.output_text.delta, response.completed events)",
    "severity": "High",
    "detail": "Chat.completions streaming uses Server-Sent Events with JSON chunks (data: {\"choices\":[...]}). Responses API streaming uses distinct event types: \"response.created\", \"response.output_text.delta\", \"response.completed\" with separate event envelopes. stream_orchestration.py must emit these event types, not chat.completion.chunk shapes.",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py (add stream_responses_phase, enrich for Responses events); gateway/ai_mesh_gateway/main.py (add streaming path in proxy_responses)",
    "implementationApproach": "Extend stream_orchestration.py with a responses-specific flow: (1) on preflight, send `event: response.created` with {id, object: \"response\", model, status: \"in_progress\", ...} (2) as LLM_ROUTER yields output chunks, map them to output items + deltas, send `event: response.output_text.delta` with {delta: \"text chunk\"} for each token (3) on completion or error, send `event: response.completed` with full response object {id, object, output[], output_text, status, usage, zeroshield: {...}} (4) on error mid-stream, send error envelope + `response.completed` with status: \"failed\". Reuse StreamRunMetrics for tracking TTFT/duration. Hook guard verdict into response.completed payload (zeroshield action/threat_type).",
    "effort": "L",
    "firewallRisk": "SSE event multiplexing: if guard blocks mid-stream, guard must emit error event + completed frame before closing connection. Ensure no partial/truncated output reaches client on block. Telemetry must capture all three event types separately so dashboard visibility is clear. Verify client SDK (openai.OpenAI.responses.create(stream=True)) can parse event types (not just raw SSE chunks)."
  },
  {
    "title": "Backwards translation: Responses request \u2192 internal chat.completions format for LLM routing",
    "severity": "High",
    "detail": "LLM_ROUTER.acompletion() expects a chat.completions body {model, messages[], ...}. Responses API input {input, instructions, modalities, ...} must be converted to {model, messages: [{role: \"system\", content: instructions}, {role: \"user\", content: input_text}], ...} + preserved params (max_output_tokens, temperature, etc.).",
    "files": "gateway/ai_mesh_gateway/main.py (add _normalize_responses_to_chat helper); ai_mesh_shared/openai_request_normalizer.py",
    "implementationApproach": "Add function _normalize_responses_to_chat(body: dict) -> dict that: (1) extracts input string (or concatenated text from typed items) (2) extracts instructions (if present, use as system message) (3) builds messages: []; if instructions, append {role: \"system\", content: instructions}; append {role: \"user\", content: input_text} (4) copy passthrough fields: model, max_output_tokens \u2192 max_tokens (rename), temperature, stop, seed, etc. (5) if modalities specified, add to body (some LLMs may ignore, but preserve for providers that support) (6) return normalized body. In proxy_responses, after input normalization, call this to get a chat-compatible dict for routing.",
    "effort": "S",
    "firewallRisk": "Incomplete field mapping \u2192 parameter loss (e.g., modalities not forwarded, max_output_tokens lost). Verify all Responses fields have corresponding chat equivalents or are intentionally dropped. Document which Responses fields are unsupported per provider (e.g., audio input on text-only models)."
  },
  {
    "title": "Forwards translation: LLM response \u2192 Responses API format",
    "severity": "High",
    "detail": "LLM_ROUTER returns a chat.completion {id, choices[0].message.content, usage, ...}. Must be converted to Responses format {id, object: \"response\", output: [{type: \"text\", text: ...}], output_text, usage, status: \"completed\"}.",
    "files": "gateway/ai_mesh_gateway/main.py (add _normalize_chat_to_responses helper)",
    "implementationApproach": "Add function _normalize_chat_to_responses(completion: dict) -> dict that: (1) extracts text from choices[0].message.content (or handles delta for streaming) (2) builds output items: [{type: \"text\", text: extracted_text}] (3) copies usage verbatim (4) builds response object: {id: completion[\"id\"], object: \"response\", created: completion[\"created\"], model: completion[\"model\"], output: items, output_text: extracted_text, usage: usage, status: \"completed\", metadata: {}} (5) appends zeroshield metadata from pipeline_trace (reuse _build_zeroshield_metadata). Return response object. In proxy_responses, call this after LLM_ROUTER.acompletion and before client response.",
    "effort": "S",
    "firewallRisk": "None \u2014 purely format translation, same redaction/guard enforcement applied before this step."
  },
  {
    "title": "Responses metadata storage (optional, for previous_response_id retrieval)",
    "severity": "Medium",
    "detail": "Responses API supports `previous_response_id` to reference prior responses. If responses are stored (store=true or default), they must be persisted (Redis + optional DB backup) so subsequent requests can load them. Current code has no responses table/cache.",
    "files": "control/ai_mesh_control (models for Response, ResponseItem); gateway cache layer; Redis schema",
    "implementationApproach": "Add Django model in control plane: class Response(models.Model) with fields: org_id, project_id, response_id (pk), input_text, output_text, output_items (JSON), request_body (JSON), status, created, metadata, ttl. After proxy_responses returns successfully (status: \"completed\"), enqueue a background job (via enqueue_job) to persist response to control DB. In gateway, query Redis first (keyed by f\"response:{org_id}:{response_id}\", TTL 7d), fallback to control DB. Alternatively, store only in Redis with TTL and skip DB (simpler, less durable). For now, implement Redis-only (opt-in via config toggle `store_responses_enabled`).",
    "effort": "M",
    "firewallRisk": "Multi-tenant isolation: responses must be org-scoped. If a leaked response_id from another org is queried, deny with org mismatch (emit audit event). Consider PII in stored responses \u2014 redacted version should be stored, or store only output_text (not full input/output items which may contain original PII before redaction)."
  },
  {
    "title": "ZeroShield response metadata integration",
    "severity": "Medium",
    "detail": "Responses API response must include zeroshield metadata (action, threat_type, confidence, etc.) at the top level, matching chat.completions contract (ZeroShieldResponse.v1.md). Current metadata builder (_build_zeroshield_metadata) is chat-centric.",
    "files": "gateway/ai_mesh_gateway/pipeline_trace.py; gateway/ai_mesh_gateway/main.py (_build_zeroshield_metadata, _redact_for_client_response)",
    "implementationApproach": "Reuse _build_zeroshield_metadata and _redact_for_client_response without modification (they work on input_scan verdict + output_guard verdict, not response shape). Call them in proxy_responses with same args. Metadata appends to response object at top level: {id, object, output[], zeroshield: {...}}. Stock OpenAI SDK tolerates extra fields, so this integrates cleanly.",
    "effort": "S",
    "firewallRisk": "Consistency: verify zeroshield schema (fields, allowlist) is identical for Responses as for chat.completions (should be \u2014 same enforcement pipeline). Document that Responses zeroshield.detection_tier may include \"output_guard\" (when output items are scanned), mirroring chat behavior."
  },
  {
    "title": "Auth + rate-limiting for /v1/responses",
    "severity": "Medium",
    "detail": "POST /v1/responses must enforce the same auth/rate-limit gates as chat.completions. Current middleware (validate_api_key in middleware.py) covers all POST routes to /v1/* by default; verify rate-limit TPM accounting includes Responses calls.",
    "files": "gateway/ai_mesh_gateway/middleware.py; gateway/ai_mesh_gateway/rate_limiter.py; gateway/ai_mesh_gateway/circuit_breaker.py",
    "implementationApproach": "No code change needed \u2014 auth + rate-limit middleware automatically applies to all /v1/* routes (Responses included). Verify in rate_limiter.py that input_tokens + output_tokens are correctly attributed from the Responses usage object (same shape as chat.completions usage). Test end-to-end with mock Responses request to confirm TPM limit enforcement.",
    "effort": "S",
    "firewallRisk": "Ambiguous: if Responses API allows audio/document input, token estimation may be inaccurate (audio measured in frames, not tokens). For audio input, LLM_ROUTER may not have a token estimate; recommend conservative fallback (e.g., assume 1 token per 10ms audio, or use audio byte length). Rate limit should not block on estimation uncertainty \u2014 fall back to allow + warn."
  },
  {
    "title": "Test coverage for Responses API against live app (parity with test_openai_sdk_compat.py)",
    "severity": "Medium",
    "detail": "test_openai_sdk_compat.py validates that stock OpenAI SDK works against chat.completions. Must add analogous tests for client.responses.create() (openai>=1.44 supports beta.responses), with non-streaming + streaming paths, blocked request scenarios, and metadata access.",
    "files": "gateway/ai_mesh_gateway/tests/test_responses_sdk_compat.py (new); gateway/ai_mesh_gateway/tests/test_stream_responses.py (new)",
    "implementationApproach": "Mirror test_openai_sdk_compat.py structure: mount real app via httpx.ASGITransport, create client = AsyncOpenAI(base_url=..., api_key=...). Test cases: (1) non-streaming Responses with string input, verify response.output[] shape + response.zeroshield access (2) streaming responses, verify event types (response.created, response.output_text.delta, response.completed) (3) block scenario (e.g., injection in input string), verify openai.APIStatusError with code field (4) Responses with previous_response_id (mock prior response in Redis), verify context passed to upstream (5) with typed input items (text + image_url), verify items normalized for scan.",
    "effort": "M",
    "firewallRisk": "SDK version: openai SDK added Responses API in ~1.44 (Jan 2025). If version too old, client.responses is unavailable. Recommendation: bump openai version in test requirements and document minimum version in README."
  }
]