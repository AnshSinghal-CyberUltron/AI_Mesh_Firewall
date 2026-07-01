# POST /v1/chat/completions — OpenAI Chat Completions (stock SDK compatible)

## Current
ZeroShield gateway proxies chat/completions through FastAPI (main.py:3598 proxy_chat) with:
- Request parsing: normalize_openai_chat_request (openai_request_normalizer.py) -> strict OpenAI allowlist in OPENAI_TOP_LEVEL_KEYS (line 15-20)
- Request validation: boundary checks on model/max_tokens/messages/n at lines 3665-3950 (type safety, floor/ceiling, count caps)
- Passthrough params: _PASSTHROUGH_PARAMS (llm_router.py:62-72) = temperature, top_p, max_tokens, stop, presence_penalty, frequency_penalty, tools, tool_choice, response_format, seed, n, stream_options
- Firewall pipeline: input scan (InputScanner), Tier-2 guard model (Bedrock), policy checks, output guard for streaming (SecureStreamingResponse)
- Streaming: SSE via stream_orchestration.py with terminal zeroshield trace frame (empty-choices ChatCompletionChunk + extra top-level zeroshield field per ZeroShieldResponse.v1.md §2)
- Error handling: JSONResponse with shape {"error": {"message": "...", "type": "...", "code": ...}} (mixed with custom codes like "blocked", "forbidden", "rate_limited")
- Responses: stock chat.completion JSON + extra top-level "zeroshield" field (redacted client-safe allowlist via _redact_for_client_response, line 4333/5477)
- Model routing: via LiteLLM Router with org-qualified keys ({org}::{model}), kill-switch checks, model-state isolation
- Usage object: stock shape with prompt_tokens, completion_tokens, total_tokens (stream_orchestration.py:246-248)

## OpenAI Spec
OpenAI Chat Completions API (latest as of Feb 2025) requires:
REQUEST PARAMS: model, messages, temperature, top_p, max_tokens, stop, presence_penalty, frequency_penalty, tools, tool_choice, response_format (with json_schema subtype), seed, n, stream, stream_options (include_usage, include_delta_usage), user
GPT-5.2+ PARAMS: reasoning, reasoning_effort, logprobs, max_completion_tokens, parallel_tool_calls, audio (audio_param), modalities (text, audio)
RESPONSE SHAPE: ChatCompletion object = {id, object, created, model, choices[{index, message{role, content, [tool_calls]}, [logprobs], finish_reason}], usage{prompt_tokens, completion_tokens, total_tokens, [cache_creation_input_tokens, cache_read_input_tokens]}, [system_fingerprint]}
STREAMING: ChatCompletionChunk = {id, object, created, model, choices[{index, delta{[role], [content], [tool_calls], [audio_delta]}, [logprobs], finish_reason}], [usage]}; final chunk: data: [DONE]
ERROR ENVELOPE (OpenAI spec): {"error": {"message": "...", "type": "error_type_string"}} where type is the error code string (not nested "code" field); HTTP 400 (invalid_request_error), 401 (authentication_error), 404 (model_not_found), 429 (rate_limit_error), 500 (server_error), 503 (service_unavailable_error)

## Reusable hooks
Existing firewall pipeline components to reuse for new parameter handling:
1. openai_request_normalizer.py::normalize_openai_chat_request — add new params to OPENAI_TOP_LEVEL_KEYS (logprobs, max_completion_tokens, reasoning, reasoning_effort, parallel_tool_calls, user, modalities)
2. llm_router.py::_PASSTHROUGH_PARAMS — extend tuple to include all above params; they are simple pass-through (no validation, forward to upstream)
3. main.py::proxy_chat boundary validation (~line 3700-3950) — reuse existing pattern for max_tokens to validate max_completion_tokens; could normalize max_completion_tokens -> max_tokens for upstream compat
4. scanner.py::InputScanner.scan_prompt / patterns.py — for audio modality, reuse text-scanning logic on transcribed audio (phase 2)
5. output_guard.py::OutputGuardManager._guard_output — reuse existing content-redaction logic; ensure choice.logprobs and audio chunks are preserved when content is redacted
6. stream_orchestration.py::build_stream_trace_frame — pass through cache_creation_input_tokens and system_fingerprint if present in LiteLLM response
7. pipeline_trace.py / main.py error handling — standardize error envelopes by mapping ZeroShield codes to OpenAI error.type strings; preserve custom code in a nested zeroshield field

## Gaps
[
  {
    "title": "Missing parameter passthrough: logprobs",
    "severity": "High",
    "detail": "OpenAI's logprobs parameter (and GPT-5.2's log_prob_top_tokens sub-field) allows clients to request log probabilities for returned tokens. The normalizer includes logprobs in OPENAI_TOP_LEVEL_KEYS (line 19) but llm_router.py _PASSTHROUGH_PARAMS (line 62-72) does NOT include it, so the parameter is silently dropped before reaching the upstream provider.",
    "files": "gateway/ai_mesh_gateway/llm_router.py:62-72 (_PASSTHROUGH_PARAMS)",
    "implementationApproach": "Add 'logprobs' to _PASSTHROUGH_PARAMS tuple (line 65, after 'tool_choice'). No firewall validation needed (pass-through only). LiteLLM will forward to the provider. Response logprobs in choice.logprobs are already handled by stock OpenAI SDK parsing.",
    "effort": "S",
    "firewallRisk": "None \u2014 logprobs is transparent metadata about token likelihoods, not content. No injection/PII/output-guard implications. Firewall pipeline already runs pre-LLM, unaffected by log probability data flowing through."
  },
  {
    "title": "Missing parameter passthrough: max_completion_tokens (GPT-5.2 alias for max_tokens)",
    "severity": "High",
    "detail": "OpenAI's newer models accept max_completion_tokens as an alias for max_tokens. The normalizer allowlists both (line 16, 19) but _PASSTHROUGH_PARAMS only includes 'max_tokens' (line 63). When a client sends max_completion_tokens instead (or in addition), it's silently stripped, forcing the provider to use its default max output.",
    "files": "gateway/ai_mesh_gateway/llm_router.py:62-72, openai_request_normalizer.py:15-20",
    "implementationApproach": "Add 'max_completion_tokens' to _PASSTHROUGH_PARAMS. At boundary (main.py ~3720), normalize max_completion_tokens -> max_tokens if max_tokens is absent (OpenAI SDK sends max_completion_tokens for newer models). For backward-compat, honor whichever key the client sent and let LiteLLM/provider pick the right name.",
    "effort": "S",
    "firewallRisk": "None \u2014 max_completion_tokens is a duplicate of max_tokens (same semantic, different name). Firewall output-guard already caps total_output_tokens per user/org (output_guard.py). No new exposure."
  },
  {
    "title": "Missing parameter passthrough: parallel_tool_calls (enables concurrent tool execution)",
    "severity": "High",
    "detail": "OpenAI's parallel_tool_calls parameter (boolean, default True in some models) controls whether the model can call multiple tools in parallel in a single response. Not in _PASSTHROUGH_PARAMS, so function-calling flows that rely on parallel execution are degraded to sequential only.",
    "files": "gateway/ai_mesh_gateway/llm_router.py:62-72",
    "implementationApproach": "Add 'parallel_tool_calls' to _PASSTHROUGH_PARAMS (after 'tool_choice'). No firewall validation needed. Tool-call pattern matching in scanner.py (tool_overreach detection) already works on tool_calls regardless of parallelism \u2014 parallel calls just cause more tool_calls in a single response, which the scanner handles.",
    "effort": "S",
    "firewallRisk": "Mitigation: Tool-call overreach detection (tool_overreach threat_type in scanner.py) and tool-call injection via prompt are NOT bypassed by parallelism. The firewall sees the final tool_calls array and validates it the same way whether calls were serial or parallel. Output-guard monitors tool_calls for compliance post-LLM. No new surface."
  },
  {
    "title": "Missing parameter passthrough: user (OpenAI usage tracking identifier)",
    "severity": "Medium",
    "detail": "OpenAI's user parameter allows clients to tag requests for usage tracking and abuse detection. The normalizer doesn't explicitly block it, but _PASSTHROUGH_PARAMS doesn't include it (line 62-72), so it's dropped by normalize_openai_chat_request (strip_unknown_top_level=True at line 3643).",
    "files": "gateway/ai_mesh_gateway/llm_router.py:62-72, main.py:3643",
    "implementationApproach": "Add 'user' to both normalizer.py OPENAI_TOP_LEVEL_KEYS and _PASSTHROUGH_PARAMS. Already per-org-scoped via auth_context.user_id in telemetry, but upstream provider (OpenAI, etc.) expects the client's own user tag for their abuse detection. Forward as-is.",
    "effort": "S",
    "firewallRisk": "None \u2014 user is a string identifier, not executable code or sensitive data (client assigns their own). Telemetry already tracks user_id from auth_context, so forwarding the client's user tag adds context without creating a new surface."
  },
  {
    "title": "Missing parameter passthrough: reasoning & reasoning_effort (GPT-5.2 chain-of-thought)",
    "severity": "High",
    "detail": "GPT-5.2+ models support reasoning parameter (enables internal chain-of-thought) and reasoning_effort ('low', 'medium', 'high'). Normalizer includes both (line 19) but _PASSTHROUGH_PARAMS doesn't, so reasoning requests fall back to standard generation.",
    "files": "gateway/ai_mesh_gateway/llm_router.py:62-72, openai_request_normalizer.py:19",
    "implementationApproach": "Add 'reasoning' and 'reasoning_effort' to _PASSTHROUGH_PARAMS. No firewall changes needed. Scanner patterns (prompt_injection, jailbreak) remain effective on the prompt itself; internal reasoning tokens are not user-visible in the response and don't exit the firewall.",
    "effort": "S",
    "firewallRisk": "Mitigation: Internal reasoning is opaque and not returned to caller in a way that bypasses output-guard. Output-guard monitors the final message.content, not internal thinking. Reasoning adds latency but no new attack surface. Prompt-injection and jailbreak detection still applies to the user's input prompt (pre-reasoning)."
  },
  {
    "title": "Error envelope type field mismatch with OpenAI spec (uses 'code' instead of 'type' for the error code)",
    "severity": "Medium",
    "detail": "ZeroShield returns errors as {\"error\": {\"message\": \"...\", \"type\": \"...\", \"code\": ...}} but OpenAI spec uses {\"error\": {\"message\": \"...\", \"type\": \"error_code_string\"}} where type IS the error code (e.g., type='invalid_request_error'). The extra 'code' field (non-standard) is added at line 1474, and 'type' is set to strings like 'upstream_error', 'invalid_request', 'blocked' which don't match OpenAI's standard type names (invalid_request_error, authentication_error, rate_limit_error, server_error).",
    "files": "gateway/ai_mesh_gateway/main.py:1470-1475, main.py:3654-3946 (many error responses with custom 'error' strings)",
    "implementationApproach": "Harmonize error responses: (1) map ZeroShield's custom codes (blocked, forbidden, rate_limited, etc.) to OpenAI error type names (content_policy_violation, authentication_error, rate_limit_error); (2) use 'type' field for the error code string (not 'code'); (3) keep a parallel custom-code in a sub-field (e.g., error.param or error.code) for ZeroShield-specific telemetry if needed, but don't mix it into the OpenAI-standard envelope. Example: {\"error\": {\"message\": \"Request blocked by policy.\", \"type\": \"content_policy_violation\", \"param\": \"content\", \"zeroshield_code\": \"blocked\", \"zeroshield_detail\": {...}}}",
    "effort": "M",
    "firewallRisk": "Mitigation: Error envelope compatibility is purely structural. Enforcement (block/allow decisions) is made in the pipeline and returned at the HTTP status level (403, 429, etc.) before the response body. Changing the error JSON shape does NOT affect policy enforcement, only client parsing. Stock SDK's APIStatusError already extracts the HTTP status; the body is secondary. Backward-compat risk: existing clients parsing error.code will break; must bump version or maintain dual-field for transition."
  },
  {
    "title": "Missing streaming response fields: cache_creation_input_tokens, cache_read_input_tokens (OpenAI Prompt Caching usage)",
    "severity": "Medium",
    "detail": "OpenAI's Prompt Caching feature adds cache_creation_input_tokens and cache_read_input_tokens to usage objects (chat.completion and streaming chunks). ZeroShield's usage object (stream_orchestration.py:246-248, main.py:3526-3530) only includes prompt_tokens, completion_tokens, total_tokens. When a provider returns cache usage, it's either lost or stored under a different key.",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py:240-250, main.py:3526-3530",
    "implementationApproach": "Check LiteLLM's response.usage object for cache_creation_input_tokens and cache_read_input_tokens (if provider fills them). Pass through to client's usage object. In streaming chunks, include them in the usage sub-object when present. This is transparent pass-through; no firewall logic involved. Telemetry tracking of cache hits is beneficial for cost/latency analysis but not required for OpenAI compat.",
    "effort": "S",
    "firewallRisk": "None \u2014 cache token counts are metadata about which tokens came from cache vs fresh compute. No security implication. Output-guard monitors total_tokens which is unchanged; cache origin doesn't bypass content safety."
  },
  {
    "title": "Missing streaming response field: system_fingerprint (OpenAI determinism tracking)",
    "severity": "Low",
    "detail": "OpenAI's chat.completion and chat.completion.chunk responses include an optional system_fingerprint field (a hash of the model version and settings that affect output generation, used to detect when the model behavior has changed). Not returned in ZeroShield responses.",
    "files": "gateway/ai_mesh_gateway/main.py (response building), stream_orchestration.py (chunk building)",
    "implementationApproach": "If LiteLLM/provider returns system_fingerprint in the response, pass it through to the client's chat.completion and chunk objects. This is transparent; no firewall logic. If the upstream provider doesn't return it, omit it (OpenAI SDK allows it to be absent).",
    "effort": "S",
    "firewallRisk": "None \u2014 system_fingerprint is read-only metadata for change detection. No injection or execution risk."
  },
  {
    "title": "Missing parameter handling: modalities (audio input/output in GPT-5.2+)",
    "severity": "High",
    "detail": "GPT-5.2+ models support audio input and output (modalities=['text', 'audio']). The normalizer doesn't include 'modalities' in OPENAI_TOP_LEVEL_KEYS, and audio handling (audio_param, audio_delta in responses) is not integrated into the firewall pipeline. Audio content bypasses input/output scanning (no pattern matching for audio streams).",
    "files": "gateway/ai_mesh_shared/openai_request_normalizer.py:15-20, gateway/ai_mesh_gateway/scanner.py (pattern-based, text-only), gateway/ai_mesh_gateway/output_guard.py (text-only)",
    "implementationApproach": "Phase 1 (minimal): Add 'modalities' to normalizer OPENAI_TOP_LEVEL_KEYS. Do NOT pass audio content to scanner/output-guard (out of scope for MVP, same as multimodal images today). Add a firewall warning log when audio modality is requested but scanning is enabled (explain the limitation). Phase 2 (full): Integrate audio transcription/captioning into the scanner pipeline (e.g., transcribe audio to text, scan the transcript, redact/block audio if transcript is blocked). For output, intercept audio chunks from the streaming response and scan them similarly.",
    "effort": "L",
    "firewallRisk": "High without mitigation: Audio modality is a NEW SURFACE that completely bypasses pattern matching and guard models (they work on text). A user can ask the model to output sensitive data in audio form, circumventing output-guard. Mitigation: (1) Explicitly disable audio modality (strip modalities from request or reject with 400 if audio is requested). (2) Add audio transcription into the scanner/guard pipeline (complex, requires speech-to-text library). (3) Document that audio is not scanned and orgs must manually control which users/keys can use audio. Recommend option 1 (reject) for MVP to prevent silent bypass."
  },
  {
    "title": "Output response object missing logprobs field on choices (GPT-5.2 feature)",
    "severity": "Medium",
    "detail": "When client sends logprobs parameter, OpenAI returns choice.logprobs containing token log probabilities (logprobs.content[] array with token/logprob/top_logprobs). ZeroShield's response building (main.py ~3520, response.model_dump()) relies on LiteLLM's pass-through, but there's no explicit validation that choice.logprobs is preserved and not stripped by output-guard redaction.",
    "files": "gateway/ai_mesh_gateway/main.py (~3520 response.model_dump()), output_guard.py (_extract_response_from_completion, _set_completion_response_text)",
    "implementationApproach": "When output-guard redacts choice[0].message.content, it must PRESERVE choice.logprobs (the log probabilities reference the original tokens, not the redacted content). Verify that _set_completion_response_text (which rewrites message.content) does not accidentally drop logprobs. Add a unit test: send logprobs=true, get a response, verify response.choices[0].logprobs is present and matches the redacted content's token length.",
    "effort": "S",
    "firewallRisk": "Mitigation: Log probabilities are metadata about token likelihoods; redacting the content but dropping logprobs would be a silent data loss (client thinks they have probabilities for the redacted tokens, but the structure is incomplete). Output-guard already redacts content safely; this is just defensive preservation of the logprobs structure (no new injection risk)."
  },
  {
    "title": "Missing response_format json_schema validation integration with firewall",
    "severity": "Medium",
    "detail": "OpenAI's response_format.type='json_schema' allows clients to specify a structured JSON schema for the response. ZeroShield passes response_format through (line 64 in _PASSTHROUGH_PARAMS) but the output-guard does NOT validate the response against the schema \u2014 it only redacts the content text. A malicious schema could be used to force the model to output structured PII in a way that bypasses pattern matching (e.g., 'email': <field>, 'value': <captured_email>).",
    "files": "gateway/ai_mesh_gateway/llm_router.py:65, output_guard.py (~secure_streaming.py _flush_buffer, output_guard_manager)",
    "implementationApproach": "When response_format.type='json_schema' is in the request, parse the response as JSON and validate it against the provided schema. If PII/secret patterns are detected in any schema field value (not just message.content), trigger redaction/blocking. Alternatively (simpler for MVP): warn in logs when json_schema is used ('structured output requested; schema-aware scanning not yet supported, only content text is scanned'). Phase 2: integrate schema-aware pattern matching.",
    "effort": "M",
    "firewallRisk": "High without mitigation: Structured output (json_schema) is a new channel for bypassing text-based pattern matching. A schema like {\"fields\": {\"secret\": \"apikey_value\"}} could force the model to output plaintext secrets in a way that the regex scanner misses. Mitigation: (1) Disable json_schema (strip response_format if type='json_schema'). (2) Post-process parsed JSON and scan all leaf values (not just the top-level message.content). (3) Require approval for json_schema via a firewall policy rule."
  },
  {
    "title": "Missing validation: n > 1 is silently clamped without client warning (output-guard single-choice limitation)",
    "severity": "Low",
    "detail": "At line 3922, ZeroShield clamps n to 1 ('C3: output guard is single-choice by design'). This is the correct behavior (output-guard can only scan choice[0]), but clients are not warned that n > 1 is being ignored. The response still arrives with choices[0] only, so the client may assume n was honored when it wasn't.",
    "files": "gateway/ai_mesh_gateway/main.py:3906-3922",
    "implementationApproach": "Option 1: Emit a warning header (X-Warning: 'n > 1 clamped to 1 due to output scanning') so the client is aware. Option 2: Document in the API that n > 1 is unsupported when output_scan_enabled=true. Option 3: Return an early 400 error if client sends n > 1 with a clear message ('Multiple completions (n > 1) are not supported when output scanning is enabled; n clamped to 1'). Currently line 3922 silently rewrites n=1, which is safe but lacks transparency.",
    "effort": "S",
    "firewallRisk": "None \u2014 this is UX transparency, not a security gap. Clamping to 1 is the right enforcement; just add a client-facing warning so they know why they got fewer results than requested."
  }
]