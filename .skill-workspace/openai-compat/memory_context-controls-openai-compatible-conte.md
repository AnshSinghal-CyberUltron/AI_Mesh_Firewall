# context-controls: OpenAI-compatible context-window management, message truncation strategy, token limits, and conversation state lifecycle.

## Current
**Current ZeroShield context controls (gateway/ai_mesh_gateway/):**

1. **Per-key token budget** — `gateway_key_views.py` + `models.py:348-351` (GatewayAPIKey.max_context_tokens field; default 0=unlimited):
   - Stored in control plane Django model; cached in Redis as part of auth context (`middleware.py:96`)
   - Applied at gateway during chat/completions: `main.py:4795-4799` reads `auth_ctx.max_context_tokens`, falls back to CONFIG["default_max_context_tokens"]
   - If >0, invokes `context_assembler.prune_messages(messages, max_ctx)` (line 4799)

2. **Token estimation** — `context_assembler.py:72-83`:
   - `estimate_tokens()` uses tiktoken (cl100k_base) when available, else fast heuristic (len(text)//4)
   - Per-message: `_message_tokens()` sums role + content + 4-token overhead

3. **Pruning strategy** — `context_assembler.prune_messages()` (lines 97-178):
   - **Sliding-window invariant**: always keep first system message + latest user message; drop oldest non-system messages to fit budget
   - Logs truncation events; returns unmodified messages if max_tokens ≤ 0
   - **Warning logged** if system+latest user alone exceed budget — returns minimal context

4. **Hard message-count cap** — `main.py:1430, 3770-3778`:
   - `MAX_MESSAGES = 200` — rejects requests with >200 entries in the messages array (DoS/amplification gate)
   - Returns 400 `"too_many_messages"` error

5. **Max output tokens enforcement** — `main.py:1431, 3684-3743, 4880-4889`:
   - `MAX_OUTPUT_TOKENS_CEILING = 1_000_000` — absolute upper bound on max_tokens parameter
   - Org-level cap: `org_config["max_response_tokens"]` (default 4096; line 4880)
   - Clamps request max_tokens to min(requested, org_config) if >0; else uses org ceiling
   - Input validation: coerces float max_tokens to int, rejects <1 or >ceiling with 400

6. **RAG context guards** — `context_guard.py` (document-level threat scanning):
   - Scans retrieved documents for prompt injection, hidden instructions, toxicity, PII/secrets **BEFORE** truncation
   - **M-19 truncation-order invariant**: scans FULL document text; only truncates evidence snippet in verdict (`SNIPPET_MAX_CHARS=100`)
   - No token-budget enforcement at retrieval stage (left to client RAG assembly)

7. **Field-level redaction** — `context_assembler.redact_messages()` (lines 253-269):
   - Redacts sensitive JSON fields (SSN, credit card, email, etc.) from messages based on caller's sensitivity clearance
   - Runs before pruning; orthogonal to token-budget control

**Control plane exposure** — `control/ai_mesh_control/`:
- `core/models.py:348-351` — GatewayAPIKey.max_context_tokens (PositiveIntegerField, default=0)
- `core/gateway_serializers.py` — REST endpoint for CRUD (max_context_tokens in read/write schema)
- `core/gateway_key_views.py` — API views accept max_context_tokens in POST/PATCH body

**No Responses API / previous_response_id tracking, no truncation=auto|disabled parameter, no multi-turn context state lifecycle.**

## OpenAI Spec
**OpenAI API context-control surfaces:**

1. **chat/completions** (existing):
   - `max_tokens` (int): max completion tokens; OpenAI SDK applies model context window check client-side
   - `max_completion_tokens` (new in some models): alias for max_tokens; caps output tokens
   - No explicit input-context truncation parameter in OpenAI API (client controls via message pruning)

2. **Responses API** (POST /v1/responses) — typed input/output with state:
   - `input` (array of ResponseInputItem): typed content items (text, tool, file, …)
   - `output_item` (optional ResponseOutputItem): previous response item to continue from
   - `max_tokens` (int): output token budget
   - `truncation_strategy` (ResponseTruncationStrategy): controls how model handles input overflow:
     - `truncation_strategy.type` = "auto" (default) | "disabled"
     - **"auto"**: model truncates middle messages if input exceeds window; preserves system + latest turn
     - **"disabled"**: reject request if input exceeds window (fail-closed context)
   - `previous_response_id` (string, optional): continue a prior response.id; state chain for multi-turn
   - Streaming: typed response events (response.created, response.output_text.delta, response.completed)
   - Usage: `response.usage.input_tokens`, `response.usage.output_tokens`

3. **Assistants API** (agents):
   - Thread state management (messages, metadata, expires_at)
   - `truncation_strategy` on run creation: {type: "auto" | "last_messages"}
   - `max_prompt_tokens`, `max_completion_tokens` per run
   - `metadata` for conversation context tagging

4. **Token counting** (tokens API):
   - POST /v1/tokens/count: estimate input + output tokens (no client-side guessing)

5. **Vision & multimodal**:
   - image_url, input_audio content parts have their own token weight (detail=low|high for images)
   - No explicit truncation control per part, but included in message token count

6. **Model configuration** (contextual):
   - model spec includes context_window (max supported), max_tokens_supported
   - Client SDK validates request max_tokens ≤ model.context_window

**Key gap: ZeroShield has no truncation=auto|disabled knob, no previous_response_id state chain, no Responses API, no per-part truncation control, no token-count endpoint.**

## Reusable hooks
**Existing firewall pipeline entrypoints to reuse for context-controls:**

1. **Input validation gate** (`main.py::_validate_chat_completions_body`, lines 3661-3750):
   - Already validates max_tokens, messages array shape, content-part types
   - Hook point: extend to validate truncation_strategy + max_completion_tokens params; coerce both to max_tokens

2. **Input scanning + policy** (`main.py::proxy_chat`, lines 4800-4880):
   - Already calls scanner.scan_verdict (Tier-1) + policy_engine.check (policy)
   - Hook point: policy rules can specify per-org max_context_tokens (new field); input scan detects context overflow as early as policy stage

3. **Context assembly** (`context_assembler.prune_messages`, lines 97-178):
   - Already implements sliding-window pruning with system+latest-user preservation
   - Hook point: extend signature to accept truncation_strategy param; run same scanning logic pre-pruning (context_guard.scan_documents on message content before truncation) to ensure truncated messages don't hide threats

4. **Stream orchestration + finalization** (`stream_orchestration.py::stream_with_finalize`, lines 420-520):
   - Already accumulates StreamRunMetrics (output_blocked, guard_action, etc.)
   - Hook point: add metrics for context_truncation_applied, dropped_message_count; emit in terminal trace frame

5. **Output guard** (`output_guard.py`, enforcement on response content):
   - Already scans LLM output for threats before streaming/return
   - Hook point: track cumulative output tokens; if cumulative > max_completion_tokens, trigger output guard to stop stream (similar to existing output_blocked logic)

6. **Telemetry / pipeline trace** (`pipeline_trace.py::build_pipeline_trace`, ~line 50-120):
   - Already logs all enforcement decisions (tier-1, tier-2, policy, guard)
   - Hook point: add context_truncation_event field; log truncation_strategy + message_count_before/after + tokens_saved

7. **Rate limiter** (`rate_limiter.py`, tpm/rpm tracking):
   - Already accounts for estimated tokens before request
   - Hook point: context_assembler.estimate_tokens already used for TPM budget; extending to support multimodal weights auto-applies to rate-limit accounting

8. **LiteLLM router** (`llm_router.py::acompletion`, model routing):
   - Already selects model based on risk/cost/latency scoring
   - Hook point: model selection can consider available context window; re-route if requested max_tokens > model.context_window (new routing factor)

**No new enforcement gates needed — context-controls are orthogonal to existing Tier-1/Tier-2/policy/output-guard gates. Reuse message validation, scanning, and telemetry hooks.**

## Gaps
[
  {
    "title": "Responses API: typed input/output items + truncation_strategy parameter",
    "severity": "Critical",
    "detail": "OpenAI Responses API (POST /v1/responses) accepts typed input items (text, tool_result, file, \u2026) and output_item (to continue from prior response), with truncation_strategy.type = 'auto'|'disabled'. ZeroShield lacks /v1/responses endpoint, truncation_strategy parameter, and output_item state chain. Stock OpenAI SDK expects this surface for multi-turn typed workflows.",
    "files": "gateway/ai_mesh_gateway/main.py (routing), context_assembler.py (no truncation_strategy support), stream_orchestration.py (no output_item handling)",
    "implementationApproach": "1. Add /v1/responses POST endpoint in main.py (similar to chat/completions routing, ~line 3300). 2. Extend context_assembler.prune_messages() signature to accept truncation_strategy={type, disabled_message} (default auto). 3. When type='auto', use existing sliding-window logic; when type='disabled', reject if pruning would occur. 4. Add output_item parameter to RequestBody schema; track prior_response_id in audit trail (pipeline_trace.py). 5. Responses stream events: emit response.created, response.output_text.delta, response.completed (extend stream_orchestration.py build_stream_trace_frame to emit typed chunks). 6. Pass truncation_strategy + output_item through firewall pipeline (same gates as chat: input scan, policy, output guard). 7. Reuse existing enforcement: context_assembler._apply_to_input_items (new), context_guard.scan_documents (for file items), output_guard for output truncation.",
    "effort": "XL",
    "firewallRisk": "Medium \u2014 truncation_strategy='disabled' creates new fail-closed path (reject on overflow); must ensure context_assembler guards against client-controlled truncation logic bypass. Mitigation: truncation logic remains gateway-owned; client 'disabled' request simply triggers validation error if message count or context tokens exceed limits. No client-side truncation execution."
  },
  {
    "title": "previous_response_id state chain for multi-turn Responses API",
    "severity": "High",
    "detail": "Responses API supports previous_response_id to continue a prior response (multi-turn state), but ZeroShield has no response.id generation, no response state table, and no previous_response_id lookup. Stock SDK expects to pass response_id from prior turn to continue conversation.",
    "files": "gateway/ai_mesh_gateway/main.py (no response.id assignment), middleware.py (no session/state tracking), docs/contracts/ZeroShieldResponse.v1.md (no state lifecycle)",
    "implementationApproach": "1. Assign response.id = f'resp_<org>_<timestamp>_<hash>' in _build_zeroshield_metadata (main.py, line 968). 2. Store response metadata in Redis (key: response_id) with TTL=24h: {org_id, user_id, model, truncation_strategy, last_message_count, context_tokens_used}. 3. On Responses API request with previous_response_id: lookup Redis; validate org_id + user_id match; load prior truncation_strategy + message context. 4. Extend context_assembler to accept prior_context param; merge with new input items (maintains conversation thread). 5. Emit response.id in both non-streaming response (top-level) and streaming trace frame (field in zeroshield.response_id). 6. Reuse firewall pipeline: all prior turns already scanned; new turn enters same Tier-1/Tier-2 gates.",
    "effort": "L",
    "firewallRisk": "Medium \u2014 response.id becomes credential-like (client can replay or forge). Mitigation: response.id includes org_id + user_id hash; API validates previous_response_id lookup must match auth context. TTL prevents unbounded accumulation. Log response.id reuse attempts."
  },
  {
    "title": "truncation_strategy parameter with type='auto'|'disabled' semantics",
    "severity": "Critical",
    "detail": "OpenAI Responses API supports truncation_strategy with 'auto' (default: model truncates middle) and 'disabled' (fail-closed: reject if context overflows). ZeroShield has only implicit auto (prune_messages always runs); no 'disabled' option, no strategy parameter in request schema.",
    "files": "gateway/ai_mesh_gateway/main.py (body schema, ~line 3661-3750), context_assembler.py (prune_messages, ~line 97)",
    "implementationApproach": "1. Extend RequestBody schema validation (main.py, post line 3750) to accept optional truncation_strategy: {type: 'auto'|'disabled'}. 2. Modify context_assembler.prune_messages(messages, max_tokens, truncation_strategy) to: (a) if type='disabled', return early with error if len(messages) would trigger pruning OR total_tokens > max_tokens; (b) if type='auto', use existing sliding-window logic. 3. Default type='auto' for OpenAI compat (existing behavior). 4. Return 400 'context_overflow' error if type='disabled' and context exceeds budget; include overflow_tokens hint. 5. Log truncation strategy choice in pipeline_trace; emit in zeroshield metadata as truncation_applied: boolean. 6. No enforcement change \u2014 strategy is client-configurable; ZeroShield always has token budget (from key or org config) that acts as absolute ceiling.",
    "effort": "M",
    "firewallRisk": "Low \u2014 strategy is declarative request parameter, not enforcement logic. Mitigation: gateway always enforces max_context_tokens (from key) + org ceiling; client truncation_strategy only affects error message (auto allows, disabled blocks)."
  },
  {
    "title": "max_completion_tokens parameter (alias for max_tokens, newer models)",
    "severity": "Medium",
    "detail": "Recent OpenAI models accept max_completion_tokens as explicit output token cap (distinct from max_tokens in older APIs). ZeroShield accepts max_tokens but not max_completion_tokens; stock SDK sends whichever is supported by the model.",
    "files": "gateway/ai_mesh_gateway/main.py (RequestBody validation, lines 3684-3743)",
    "implementationApproach": "1. Extend input validation (main.py, post line 3743) to accept optional max_completion_tokens: int. 2. Coerce semantics: if both max_tokens and max_completion_tokens present, error (ambiguous). If only max_completion_tokens, treat as max_tokens. 3. Enforce same ceiling: min(max_completion_tokens, org_config['max_response_tokens']). 4. LiteLLM pass-through: if upstream model supports max_completion_tokens, rewrite body to use it; else use max_tokens. 5. Log which param was used in zeroshield.output_token_param for debugging.",
    "effort": "S",
    "firewallRisk": "Low \u2014 output token parameter only; rate limiter and token budgets already account for completion tokens via usage accounting (stream_orchestration.py)."
  },
  {
    "title": "Input context token estimation endpoint (/v1/tokens/count)",
    "severity": "High",
    "detail": "OpenAI tokens API (POST /v1/tokens/count) allows clients to estimate token cost before incurring charges. ZeroShield lacks this endpoint; clients must estimate locally or guess. Important for context-control UX: client needs to know if messages fit under truncation_strategy='disabled' before sending.",
    "files": "gateway/ai_mesh_gateway/main.py (routing, no /v1/tokens/* endpoints), context_assembler.py (has estimate_tokens, ~line 72)",
    "implementationApproach": "1. Add POST /v1/tokens/count route in main.py (before chat/completions route, ~line 2600). 2. Accept body: {messages: [{role, content}] | string, model: string, tools?: []}. 3. Call context_assembler._estimate_tokens_for_messages(messages, model) \u2192 {token_count: int}. 4. Return JSON {input_tokens: int}. 5. Apply same input validation as chat (content-part shapes, multimodal weights). 6. No firewall gates (pure computation, no side effects). 7. Optional: add tools token accounting (OpenAI counts tool definitions). 8. Reuse tiktoken encoder from context_assembler._ENCODER.",
    "effort": "M",
    "firewallRisk": "Low \u2014 read-only estimation, no enforcement applied. Mitigation: rate-limit /v1/tokens/count per key (same tpm bucket as chat to prevent probing attacks)."
  },
  {
    "title": "Multimodal content-part token weight (image detail, audio duration)",
    "severity": "Medium",
    "detail": "OpenAI chat/completions counts tokens per content-part type: image_url with detail=low|high (different weights), input_audio with encoding (different weights for raw vs. base64). ZeroShield's estimate_tokens treats all text-like parts equally; doesn't weight images or audio. Affects context-window accuracy for multimodal requests.",
    "files": "gateway/ai_mesh_gateway/context_assembler.py (estimate_tokens, _message_tokens, lines 72-94), main.py (multimodal content-part validation, ~line 3812)",
    "implementationApproach": "1. Extend context_assembler._message_tokens(msg) to detect content-part types: (a) for each part in msg.content if isinstance(content, list): (b) if part['type']=='text', add len(text)//4 (existing); (c) if type=='image_url', add 170 base + 130\u00d7(detail=='high') \u2014 use OpenAI token weights; (d) if type=='input_audio', add 128 base + duration_sec bonus. 2. Add helper estimate_image_tokens(detail) \u2192 int; estimate_audio_tokens(encoding, duration) \u2192 int. 3. Update _message_tokens to call these helpers when building per-message cost. 4. Log per-part token estimates in debug-level traces. 5. Reuse during chat/completions request estimation (line 1542).",
    "effort": "M",
    "firewallRisk": "Low \u2014 token estimation only; no changes to enforcement logic. Mitigation: weights derived from published OpenAI token counting; validate against actual model responses in tests."
  },
  {
    "title": "Context window model metadata + client-side validation hints",
    "severity": "Medium",
    "detail": "OpenAI SDK validates max_tokens \u2264 model.context_window client-side before sending. ZeroShield's /v1/models endpoint (main.py, ~line 10411) returns model metadata but lacks context_window field. Stock SDK expects model['context_window'] to exist for validation.",
    "files": "gateway/ai_mesh_gateway/main.py (proxy_models endpoint, ~line 10411-10450), llm_router.py (model registry)",
    "implementationApproach": "1. Extend _build_model_response (main.py) to include context_window field for each model: {id, object, created, owned_by, context_window: int, max_tokens_supported: int}. 2. Source context_window from llm_router.MODEL_REGISTRY or LiteLLM model spec. 3. For custom/fallback models, use safe default (e.g., 128k for GPT-4). 4. Include in both single-model (/v1/models/{model_id}) and list (/v1/models) responses. 5. Stock SDK will validate max_tokens \u2264 context_window before request; if exceeded, client raises error before sending to gateway.",
    "effort": "S",
    "firewallRisk": "Low \u2014 metadata only; gate remains server-side (main.py input validation)."
  },
  {
    "title": "Streaming context truncation events + per-chunk token tracking",
    "severity": "High",
    "detail": "OpenAI streaming (SSE) includes usage: {completion_tokens: int} in each chunk (or terminal chunk only in some models). ZeroShield's stream_orchestration.py emits usage in terminal trace frame but doesn't expose per-chunk token deltas. Clients can't track context consumption mid-stream. No event type for truncation_occurred (when auto-truncation drops messages).",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py (build_stream_trace_frame, lines 348-400), main.py (no per-chunk usage)",
    "implementationApproach": "1. Extend StreamRunMetrics to track per-chunk tokens: {chunks_processed, cumulative_output_tokens, truncation_count}. 2. Modify stream_orchestration to emit truncation event: before streaming starts, if context_assembler.prune_messages dropped messages, emit a chat.completion.chunk with choices=[] + zeroshield={action: 'redact', reason: 'Input context truncated due to max_context_tokens budget'}. 3. Include usage in this chunk: {prompt_tokens: <pruned_count>, completion_tokens: 0}. 4. For per-chunk usage: if upstream stream includes usage in chunks, pass-through in zeroshield.partial_usage or accumulate into terminal frame. 5. Emit terminal usage frame (existing) with final cumulative totals. 6. Log truncation reason in zeroshield detail (e.g., 'Dropped 3 messages to fit 4096-token budget').",
    "effort": "L",
    "firewallRisk": "Low \u2014 streaming metadata only; no enforcement change. Mitigation: truncation is already enforced pre-stream (context_assembler.py); event is informational."
  },
  {
    "title": "Conversation state expiry + context window lifecycle management",
    "severity": "Medium",
    "detail": "OpenAI Assistants API threads expire after 30 days of inactivity. ZeroShield has no conversation lifecycle: no message expiry, no state cleanup, no conversation.expires_at. For long-running chats with truncation_strategy='disabled', need way to explicitly close context (flush conversation) without losing audit trail.",
    "files": "middleware.py (auth context, no session tracking), main.py (no conversation endpoint)",
    "implementationApproach": "1. Add optional conversation_id param to chat/completions + Responses API (client-supplied or gateway-generated UUID). 2. Store conversation metadata in Redis: {org_id, user_id, conversation_id, created_at, last_activity_at, message_count, tokens_consumed, truncation_events}. Update last_activity_at on every request. 3. Add TTL policy: conversations expire after 30 days inactivity (config: GATEWAY_CONVERSATION_TTL=2592000 sec). 4. Optional DELETE /v1/conversations/{conversation_id} endpoint to explicitly close. 5. On expiry, emit telemetry event for audit (conversation_expired action). 6. Emit conversation_id in zeroshield metadata (allows clients to track context across requests). 7. Log message pruning counts per conversation (helps operators tune max_context_tokens).",
    "effort": "M",
    "firewallRisk": "Medium \u2014 conversation_id becomes auth credential-like. Mitigation: lookup validates org_id + user_id match; Redis TTL prevents unbounded accumulation. No access-control changes needed if conversation_id is opaque (not leaked client-side)."
  },
  {
    "title": "Request context window overflow behavior + client error codes",
    "severity": "Medium",
    "detail": "When truncation_strategy='disabled' and context overflows, ZeroShield should return a distinct HTTP 400 error code ('context_overflow' or 'context_window_exceeded'). Currently returns generic validation error. Clients need to distinguish overflow from malformed input.",
    "files": "gateway/ai_mesh_gateway/main.py (error responses, ~line 401-500, 1887-1950)",
    "implementationApproach": "1. Add error code 'context_overflow' to _build_safe_block_response / error responses. 2. When context_assembler.prune_messages detects overflow + truncation_strategy='disabled', raise ContextWindowExceededError(message_count=N, total_tokens=T, budget=B, overflow_tokens=T-B). 3. Catch in main.py proxy_chat and emit 400 response with code='context_overflow', detail showing overflow_tokens. 4. Distinguish from 'too_many_messages' (array length) vs 'context_overflow' (token budget). 5. Include remediation hint in error detail: 'To fit context, reduce messages by {overflow_tokens} tokens or increase max_context_tokens key limit.' 6. Log context_overflow events per org (metric for capacity planning).",
    "effort": "S",
    "firewallRisk": "Low \u2014 error codes only; no enforcement logic change."
  }
]