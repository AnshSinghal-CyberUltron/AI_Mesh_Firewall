# Responses API Statefulness: OpenAI-compatible storage of chat responses with conversation state chaining via `previous_response_id`, persistence with `store=true`, and retrieval/deletion operations (GET /v1/responses/{id}, DELETE /v1/responses/{id}, GET /v1/responses/{id}/input_items). Tenant-isolated state store with firewall governance of stored conversation items.

## Current
ZeroShield currently:
- Returns non-streaming chat completions with OpenAI-compatible response body + zeroshield metadata (main.py:3484-7136, line 7136 returns JSONResponse with llm_resp dict enriched with zeroshield object)
- Stores enforcement events in Django EnforcementEvent model (control/ai_mesh_control/policy/models.py:166) with org FK for multi-tenancy, metadata JSONB for rich telemetry
- Uses Redis for model state caching (gateway/ai_mesh_gateway/model_state.py:9-54) with org-namespaced keys: `model_state:{org_slug}:{model_name}` pattern, async aioredis client
- Tracks request context via _REQUEST_ID context var (main.py:3625-3629) for correlation
- Has TTL/retention patterns via Redis (model_state.py imports aioredis, connection pools in shared/ai_mesh_shared/redis_pool.py)
- Does NOT persist response bodies, previous_response_id chains, or conversation state after transmission
- Response object shape: {id, object, created, model, choices[], usage, zeroshield{}} (docs/contracts/ZeroShieldResponse.v1.md, validated by tests/test_openai_sdk_compat.py)
- Output guard scans responses post-generation (output_guard.py:1-100), applies redact/block/flag/rewrite actions BEFORE final assembly
- Pipeline trace emitted to telemetry layer but never persisted for replay (pipeline_trace.py:1-100)


## OpenAI Spec
OpenAI Responses API (https://platform.openai.com/docs/api-reference/responses):
- POST /v1/responses: create response with {model, instructions, modalities, input_items[], store(bool), previous_response_id(string)}
- GET /v1/responses: list responses with pagination (limit, offset, order, ...)
- GET /v1/responses/{id}: retrieve response object with {id, object:"response", created, model, status, instructions, input_items[], output_items[], tokens, store, previous_response_id}
- GET /v1/responses/{id}/input_items: list input items {id, type, text/audio/image, ...}
- DELETE /v1/responses/{id}: soft delete (cleanup stored state)
- Response lifecycle: submitted → in_progress → completed → succeeded/failed
- Streamed responses via /v1/responses with stream=true emit typed SSE events: response.created, response.input_item.created, response.output_item.created, response.output_text.delta, response.completed
- Item state re-scanned on retrieval (implicit contract: GET {id}/input_items re-validates policy)
- store=true triggers write to provider's vector store for continuity; previous_response_id creates parent link for conversation chaining


## Reusable hooks
- **EnforcementEvent telemetry + audit loop** (control/policy/models.py:166-240): ResponsePersistence mirrors org FK isolation + incident_status tracking. Reuse _emit_telemetry() calls from main.py for response lifecycle events (store_enabled, previous_response_id validated, response_deleted).
- **Output guard scanning pipeline** (output_guard.py:1-100): Reuse scan_output(), redact_all(), get_compliance_tags() for item-level re-validation on GET /v1/responses/{id}. Integrate into ResponsesStore.fetch() as post-query validation gate.
- **Redis model-state pattern** (model_state.py:9-54, redis_pool.py): Use identical async aioredis + org-namespaced key pattern for response storage. connection_pool_kwargs() for connection setup.
- **Policy check cached** (main.py:6677-6689, _policy_check_cached): Reuse for input/output policy validation in response retrieval path. Call on each item in input_items[] before returning.
- **Request ID + telemetry context** (main.py:3625-3629, _REQUEST_ID context var): Thread through response store operations for full traceability (store write logs include request_id, retrieval logs include retriever_request_id).
- **Org isolation gate** (main.py:4850-4900, _validate_org_inference_model): Mirror org_id check from auth_ctx for all /v1/responses/* endpoints. Enforce via (org_config = CONFIG_SYNC.get_config(auth_ctx.org_slug)).
- **Streaming finalization** (stream_orchestration.py:420-500, stream_with_finalize): Reuse TPM reconciliation, circuit breaker integration, prometheus metrics recording for response streaming path.
- **Redaction for client** (_redact_for_client_response at main.py:4333, 5477): Reuse client-safe field allowlist (action, threat_type, confidence, etc.) when building GET /v1/responses response payload. Add response-specific fields (status, previous_response_id, stored_at).
- **Django ORM query indexing** (policy/models.py Meta.indexes for EnforcementEvent): Mirror for ResponsePersistence: (org_id, -created_at) for list endpoint pagination, (org_id, expires_at) for retention cleanup.

## Gaps
[
  {
    "title": "Response Store Missing: No persistent storage for response bodies or state",
    "severity": "Critical",
    "detail": "OpenAI Responses API requires a persistent store keyed by org+response_id. ZeroShield returns responses but discards them after transmission. Cannot satisfy GET /v1/responses/{id} or DELETE /v1/responses/{id} without a backing store. No previous_response_id chaining supported in input.",
    "files": "gateway/ai_mesh_gateway/main.py:7136 (response return point); no responses storage module exists",
    "implementationApproach": "Create gateway/ai_mesh_gateway/responses_store.py with org-namespaced Redis hash keys: `responses:{org_id}:{response_id}` storing {request_id, created, model, input_items[], output_items[], action, threat_type, compliance_tags, status, ttl_seconds}. Use the existing Redis pool from shared/ai_mesh_shared/redis_pool.py (connection_pool_kwargs pattern). At response finalization (main.py:7136 before JSONResponse), call ResponsesStore.persist(org_id, response_obj, ttl=config.response_ttl_days*86400). Add Django model ResponsePersistence (control/ai_mesh_control/policy/models.py post-EnforcementEvent) with org FK, response_id UUID PK, stored_at timestamp, metadata JSONB for audit.",
    "effort": "M",
    "firewallRisk": "ISOLATION: store must enforce org-scoped key namespacing (no org-A query of org-B responses). Mitigation: Redis hash keys include org_id hash, check auth_ctx.organization_id on retrieval, mirror EnforcementEvent's org FK isolation pattern (policy/models.py:175-182 reference model)."
  },
  {
    "title": "No previous_response_id Request Parameter Support",
    "severity": "Critical",
    "detail": "OpenAI SDK passes previous_response_id in POST /v1/responses to create a conversation chain. ZeroShield chat/completions endpoint does not parse or validate this field. Cannot verify that previous response was authored by same org or apply continuous conversation policies.",
    "files": "gateway/ai_mesh_gateway/main.py:3598-3683 (body parsing section); ai_mesh_shared/openai_request_normalizer.py (normalizer)",
    "implementationApproach": "In normalize_openai_chat_request (openai_request_normalizer.py), accept previous_response_id as optional string field (do not strip; add to normalizer's allowlist). In proxy_chat (main.py), after auth passes, call ResponsesStore.fetch_parent(org_id, previous_response_id) and validate: (a) parent exists, (b) parent.org_id matches current request org, (c) parent.status='completed'. If validation fails, return 400 with code='invalid_previous_response'. If valid, load parent output_items as prepended context (system message or conversation history) so model sees prior turn. Log parent_id in telemetry for audit trail (via stage_metrics dict, current pattern at main.py:6910).",
    "effort": "M",
    "firewallRisk": "CONTEXT_POISONING: parent response output_items must be re-scanned for policy violations (cannot trust stored content was clean; policies may have changed). Reuse output_guard.py::scan_output on parent output before prepending. REPLAY: enforce parent.action was 'allow'/'flag' (not 'block'/'redact') or reject chain continuation."
  },
  {
    "title": "No store=true Persistence Control",
    "severity": "High",
    "detail": "OpenAI Responses API accepts store=true parameter to write response into long-term store; store=false prevents persistence. ZeroShield /v1/chat/completions ignores this parameter. Cannot satisfy clients who need transient vs persistent response handling or GDPR-compliant retention control.",
    "files": "gateway/ai_mesh_gateway/main.py:3598-3683, 7136 (no store param parsing or conditional persist)",
    "implementationApproach": "Add store boolean field to normalized body (openai_request_normalizer.py, default=true for compatibility). At finalization (main.py:7136), check body.get('store', True). If False, skip ResponsesStore.persist() call. If True, persist with default org TTL from config (org_config.get('response_store_ttl_days', 30)). Emit telemetry event store_enabled={store} for audit.",
    "effort": "S",
    "firewallRisk": "RETENTION_POLICY: store=false transactions must still appear in EnforcementEvent log (audit trail is separate from user-facing store). Document: ResponsePersistence.stored=False (not deleted, just not fetchable via API GET)."
  },
  {
    "title": "GET /v1/responses/{id} Endpoint Missing",
    "severity": "Critical",
    "detail": "No endpoint to retrieve a previously stored response. Stock OpenAI SDK calls client.responses.get(response_id) which maps to GET /v1/responses/{response_id}. ZeroShield returns 404 or unimplemented error.",
    "files": "gateway/ai_mesh_gateway/main.py (no @app.get('/v1/responses/{response_id}') route)",
    "implementationApproach": "Add FastAPI route in main.py: @app.get('/v1/responses/{response_id}', tags=['Responses']) -> async def get_response(response_id: str, request: Request). (1) Extract org_id from auth_ctx (validate_api_key middleware already injected into request.state.auth_context). (2) Call ResponsesStore.fetch(org_id, response_id). (3) If not found, return JSONResponse(404, {error: 'not_found', code: 'response_not_found'}). (4) If found, return full response object: {id, object: 'response', created, model, status: 'completed', input_items[], output_items[], tokens{prompt_tokens, completion_tokens}, store, previous_response_id}. (5) Re-validate policy on output_items in-flight (call output_guard.py::scan_output on each output text item) and apply current org policies\u2014do NOT return blocked/redacted items without user knowing (set response.review_required=true if policy mismatch). (6) Redact operator-only fields before returning (mirror _redact_for_client_response pattern).",
    "effort": "M",
    "firewallRisk": "REPLAY_SCANNING: responses fetched via GET must be re-scanned against current policies. Stored output_items may violate new policies added after original completion. Mitigation: store response.original_action, response.original_policies_hash (SHA256 of matched policy ids at write time). On GET, recompute hash and if mismatch, flag response.review_required=true + return HTTP 403 with code='policy_changed' if enforcement_mode='block'. Otherwise flag for manual review."
  },
  {
    "title": "DELETE /v1/responses/{id} Endpoint Missing",
    "severity": "High",
    "detail": "No way to delete a stored response. OpenAI SDK calls client.responses.delete(response_id) -> DELETE /v1/responses/{response_id}. Cannot satisfy GDPR right-to-be-forgotten or customer cleanup requests.",
    "files": "gateway/ai_mesh_gateway/main.py (no @app.delete route)",
    "implementationApproach": "Add @app.delete('/v1/responses/{response_id}', tags=['Responses']) -> async def delete_response(response_id: str, request: Request). (1) Validate org_id from auth context. (2) Call ResponsesStore.delete(org_id, response_id). (3) If not found, return 404. (4) If found, delete Redis key and mark ResponsePersistence.deleted_at = now(). (5) Emit DeleteResponseEvent telemetry with org_id, response_id, deletion_reason='user_request', timestamp. (6) Return JSONResponse(200, {id: response_id, deleted: true, object: 'response.deleted'}).",
    "effort": "S",
    "firewallRisk": "AUDIT_TRAIL: soft delete only (never physically remove, keep encrypted backup for 90 days per SOC2 requirement). Add ResponsePersistence.is_deleted=False field, set True on delete. EnforcementEvent must retain reference even if response is deleted (foreign key on_delete=SET_NULL, not CASCADE)."
  },
  {
    "title": "GET /v1/responses/{id}/input_items Endpoint Missing",
    "severity": "High",
    "detail": "OpenAI Responses API includes GET /v1/responses/{id}/input_items to list input items (messages). No such endpoint in ZeroShield. Clients cannot inspect or replay conversation inputs.",
    "files": "gateway/ai_mesh_gateway/main.py (no sub-resource endpoint)",
    "implementationApproach": "Add @app.get('/v1/responses/{response_id}/input_items', tags=['Responses']) -> async def list_response_input_items(response_id: str, request: Request). (1) Fetch response from store. (2) Return {object: 'list', data: response.input_items[], response_id}. Input items are the original `messages` array from the request, stored as {id, type: 'text', text: (redacted from client; operator gets raw), role, created}. (3) Redact PII from text before returning to client (apply _redact_trace_text pattern from main.py:7113). (4) Return raw text to operator (if auth_ctx.is_admin). (5) Emit read event for audit log.",
    "effort": "S",
    "firewallRisk": "PII_LEAKAGE: input_items contain prompts which may have been redacted. ResponseStore must maintain two copies: original (operator-visible) and redacted (client-visible). On retrieval, check if response.action='redact' and user is not admin, return redacted copy."
  },
  {
    "title": "No Response Status Tracking (submitted/in_progress/completed)",
    "severity": "Medium",
    "detail": "OpenAI Responses API response objects have status field showing lifecycle (submitted, in_progress, completed). ZeroShield responses are synchronous (complete immediately) so status is implicitly 'completed'. But no status field returned, and no support for asynchronous response handling or in-progress polling.",
    "files": "gateway/ai_mesh_gateway/main.py:7136 (response payload construction); docs/contracts/ZeroShieldResponse.v1.md (contract does not mention status field)",
    "implementationApproach": "Add optional status field to response envelope. For non-streaming: status='completed' always (ZeroShield is sync-only at gateway layer; async handling deferred to client apps). For streaming: emit response.created event with status='submitted' at SSE start, then response.output_text.delta events, finally response.completed event with status='completed' (leverage stream_orchestration.py::build_stream_trace_frame pattern). Store status in ResponsePersistence.status enum field (draft='submitted', processing='in_progress', succeeded='completed', failed='error'). Clients can poll GET /v1/responses/{id} and see status progression (useful for long-running inference or multi-turn scenarios).",
    "effort": "M",
    "firewallRisk": "NONE (additive field, backward compatible). Existing non-streaming clients ignore status field."
  },
  {
    "title": "No Streaming Event Types for Responses API (response.created, response.output_text.delta, response.completed)",
    "severity": "High",
    "detail": "OpenAI Responses API streaming emits typed SSE events: response.created (start), response.input_item.created, response.output_item.created, response.output_text.delta (per token), response.completed (end). ZeroShield /v1/chat/completions streaming emits generic chat.completion.chunk with zeroshield trace frame. Cannot interop with clients expecting Responses API stream shape.",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py:1-420 (stream_with_finalize, build_stream_trace_frame, no response-specific event builders)",
    "implementationApproach": "Add new conditional streaming path in main.py (parallel to existing /v1/chat/completions stream): POST /v1/responses with stream=true triggers new response-streaming orchestrator. Create gateway/ai_mesh_gateway/responses_stream_orchestrator.py (mirror stream_orchestration.py). Emit typed events: (1) {event: 'response.created', data: {id, object: 'response.created', created, model}} at start. (2) For each LLM token, emit {event: 'response.output_text.delta', data: {id, delta: {text: token}, index: 0}} (reuse SecureStreamingResponse._flush_buffer pattern for output guard scanning). (3) At finish, emit {event: 'response.completed', data: {id, status: 'completed', output_items: [{type: 'text', text, ...}]}}. Apply same finalization_phase (TPM reconciliation, circuit breaker, telemetry) as chat stream. Modify stream_orchestration.py::stream_with_finalize to branch: if request from /v1/responses -> use response event shape, else use chat.completion.chunk shape.",
    "effort": "L",
    "firewallRisk": "SAME_AS_CHAT_STREAMING: output guard applies per-token (SecureStreamingResponse pattern). Response events must include zeroshield trace (new event type: response.security_checkpoint with action, threat_type, matched_patterns) emitted at finish before response.completed."
  },
  {
    "title": "No Conversation State/History Indexing (message roles, turn sequence)",
    "severity": "Medium",
    "detail": "Responses API stores input_items (conversation history) in a predictable order. Clients rely on iteration order to reconstruct conversation. ZeroShield stores messages as-is but does not index by role or turn number, so retrieval does not guarantee order or context coherence on replay.",
    "files": "gateway/ai_mesh_gateway/responses_store.py (hypothetical, not yet created); ResponsePersistence model",
    "implementationApproach": "In ResponsePersistence JSONB metadata, store messages as array with explicit schema: [{turn: 1, role: 'user'|'assistant', content, created_at, message_id}, ...]. When persisting response (main.py finalization), extract from body.messages[], assign turn numbers (0-indexed), set created_at from request timestamp. On GET /v1/responses/{id}/input_items, return in turn order with pagination (limit, offset) per OpenAI spec. Add database index on ResponsePersistence.metadata->'turn' for efficient replay.",
    "effort": "S",
    "firewallRisk": "CONVERSATION_INJECTION: if previous_response_id chain allows arbitrary message insertion (e.g., attacker prepends system prompt mid-conversation), policy must validate message sequence integrity. Mitigation: store original message hash (SHA256 of serialized message) and reject any replay with tampered turn order or modified content."
  },
  {
    "title": "No Tokens/Usage Tracking Per Response",
    "severity": "Medium",
    "detail": "Responses API response object includes tokens{prompt_tokens, completion_tokens} for cost tracking. ZeroShield does return usage but does not associate usage with stored response for billing/analytics queries.",
    "files": "gateway/ai_mesh_gateway/main.py:7136 (usage included in response); ResponsePersistence model (no usage field)",
    "implementationApproach": "Add usage JSONB field to ResponsePersistence: {prompt_tokens: int, completion_tokens: int, total_tokens: int, model_id: str}. At persist time (main.py), copy llm_resp.usage into ResponsePersistence.usage. Add control-plane view /api/responses/analytics/?org_id=X&date_range=Y to aggregate {total_tokens_used, cost_by_model, response_count} from ResponsePersistence table. Reuse DjangoORM aggregation (policy/models.py pattern for compliance violations).",
    "effort": "S",
    "firewallRisk": "COST_ABUSE: per-org token limits (rate limiting) must account for stored response *creation* time, not retrieval time, to prevent budget exhaustion via repeated GET {id} calls. Enforce token budget at creation (main.py rate limiter, current pattern in auth middleware)."
  },
  {
    "title": "No Compliance/Retention Policy Enforcement on Stored Responses",
    "severity": "High",
    "detail": "ZeroShield has ComplianceViolation model (control/policy/models.py:294) for framework tracking (GDPR, HIPAA) but does not auto-delete responses on retention expiry or apply encryption-at-rest per compliance framework.",
    "files": "control/ai_mesh_control/policy/models.py:294 (ComplianceViolation exists); gateway/ai_mesh_gateway/responses_store.py (hypothetical; no retention logic)",
    "implementationApproach": "Add ResponsePersistence.compliance_frameworks (ArrayField of choices: GDPR, HIPAA, SOC2, PCI_DSS). At persist time, inherit from org config org.compliance_frameworks. Add ResponsePersistence.expires_at = now() + ORG_RETENTION_DAYS[framework]. Background task (Django management command) daily: DELETE ResponsePersistence WHERE expires_at <= today AND is_deleted=False. Log deletions as ComplianceEvent (new model). Add control-plane endpoint PATCH /api/responses/compliance/?org_id=X to bulk-set retention policy and trigger immediate deletion of expired responses. Encrypt response JSONB at rest using django-encrypted-model-fields library (keys from KMS per org).",
    "effort": "L",
    "firewallRisk": "RETENTION_ESCAPE: if background cleanup fails silently, responses older than policy allows remain queryable. Mitigation: daily audit task cross-checks ResponsePersistence.expires_at vs actual retention, alerts if stale responses found. Add ResponsePersistence.audit_checked_at timestamp."
  },
  {
    "title": "No input_items/output_items Item-Level Policy Enforcement on Retrieval",
    "severity": "High",
    "detail": "When client fetches GET /v1/responses/{id}/input_items, ZeroShield must validate each item against current policies (policies may have changed since storage). OpenAI SDK expects items to be filtered/flagged if violated. ZeroShield does not re-scan stored items or apply current output_guard rules.",
    "files": "gateway/ai_mesh_gateway/output_guard.py:1-100 (scanning exists for live responses, not stored replay); (hypothetical) /v1/responses/{id}/input_items endpoint",
    "implementationApproach": "On GET /v1/responses/{id}/input_items, before returning each item, call output_guard.py::scan_output(item.text, org_config) and check result against current org policies. If scan verdict action='block', omit item from response and set response.review_required=true + include warning in response headers. If action='redact', apply redaction and include redacted item in response (with metadata [{redacted_by: 'policy', original_hash: SHA256, reason}]). If action='flag', include item but set item.flagged=true + reason. Emit PolicyReplayEvent telemetry {response_id, org_id, item_id, action, matched_policies}. This ensures stored items are *dynamically* evaluated against evolving policy.",
    "effort": "M",
    "firewallRisk": "POLICY_BYPASS: if on-retrieval scanning is skipped and items are returned unchanged, attacker could store benign response at time T, policies tighten at T+1 hour, then retrieve same response and get unscanned content. Mitigation: make scanning mandatory (no bypass). Add ResponsePersistence.policies_hash_at_write (SHA256 of org policy code at creation), compare to current policies_hash; if mismatch, set review_required=true."
  }
]