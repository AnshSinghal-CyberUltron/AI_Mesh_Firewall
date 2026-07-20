# auth-sdk-compat: OpenAI SDK authentication & error shape compatibility

## Current
ZeroShield currently implements:

1. AUTH (middleware.py:104-449):
   - Bearer token extraction (middleware.py:305-310) from Authorization header
   - SHA-256 key hashing + Redis lookup (middleware.py:125-129)
   - Returns error dict with {status_code, error, message} (middleware.py:119-197)
   - Error envelopes as flat JSONResponse with error/message fields (middleware.py:332-402)
   - No "type", "param", or nested "code" fields in auth errors

2. ERROR RESPONSES (main.py:153-174, 506-578, 1451-1475):
   - Exception handlers return {"error": "string", "message": "..."} (main.py:160-162, 171-173)
   - Invalid input validation → 400 with flat {"error": "invalid_request", "message", "code"} (main.py:3654-3657)
   - LLM upstream errors → nested {"error": {"message", "type", "code"}} (main.py:1472) 
   - Policy blocks → flat {"error": "blocked", "message", "code", "request_id", "category", "blocked_by"} (main.py:562-571)
   - Inconsistent: auth errors use flat shape, some input validation uses flat {"error": string, "code": string}, upstream errors use nested {"error": {nested object}} (main.py:1472, 5710, 6269)

3. LIST RESPONSES (main.py:10421-10474, 7189, 10448):
   - /v1/models: {"object": "list", "data": [model_objects]} (main.py:10421)
   - /v1/embeddings: {"object": "list", "data": [embedding_objects]} (line 7189)
   - No "has_more", "first_id", "last_id" pagination fields
   - No limit/offset query parameter support for pagination

4. ID FORMATS (main.py:530, test_openai_sdk_compat.py:79):
   - Chat completions: "chatcmpl-<uuid>" (test_openai_sdk_compat.py:79)
   - ZeroShield request_id: "zs-<12 hex>" (main.py:530, docs/contracts/ZeroShieldResponse.v1.md:41)
   - No resp_*, msg_*, run_*, thread_* prefixes implemented

5. HEADERS:
   - X-ZeroShield-* response headers (stream_orchestration.py:168, docs/contracts:164-187)
   - WWW-Authenticate header on 401 (middleware.py:339) per RFC 9728
   - No standard OpenAI headers like x-request-id

## OpenAI Spec
OpenAI API v1 Specification (Python SDK):

AUTH:
- HTTP Authorization: Bearer {api_key} header (standard)
- 401 Unauthorized: missing/invalid key → openai.AuthenticationError
- 403 Forbidden: forbidden/disabled key → openai.PermissionDeniedError  
- 503 Service Unavailable: auth service down, Retry-After header

ERROR BODY SHAPE (required for SDK parsing):
{
  "error": {
    "message": "Human-readable description",
    "type": "error_type" (e.g. "invalid_request_error", "authentication_error"),
    "param": "field_name" (optional: which field caused 400),
    "code": "machine_code" (optional: e.g. "invalid_api_key", "model_not_found")
  }
}
OR legacy flat format (deprecated but still parsed):
{
  "error": "code_string",
  "message": "..."
}

HTTP Status → SDK Exception Mapping:
- 400 Bad Request → openai.BadRequestError (invalid_request_error type)
- 401 Unauthorized → openai.AuthenticationError
- 403 Forbidden → openai.PermissionDeniedError  
- 408 Request Timeout → openai.APITimeoutError
- 429 Too Many Requests → openai.RateLimitError
- 5xx Internal Server Error → openai.APIError
- Other 4xx → openai.APIStatusError

LIST RESPONSES (for pagination compliance):
{
  "object": "list",
  "data": [items...],
  "has_more": boolean,
  "first_id": "string",
  "last_id": "string"
}
Query params: limit, after, before (for cursor pagination)

ID PREFIXES (standard OpenAI):
- resp_* : Response (Responses API)
- msg_* : Message (Assistants)
- run_* : Run (Assistants)
- thread_* : Thread (Assistants)
- chatcmpl_* : Chat Completion

REQUIRED HEADERS:
- x-request-id: correlation ID for support
- x-processing-ms: gateway processing latency (optional)

## Reusable hooks
1. UNIFIED ERROR BUILDER (create new):
   - _build_openai_error_response(status_code: int, message: str, error_type: str, param: str = None, code: str = None) → JSONResponse
   - Called by: middleware.py::validate_api_key (auth errors), main.py::_bad_input_handler (400s), main.py::_build_safe_block_response (403s), main.py input validation loops (invalid_request_error)
   - Returns NESTED {error: {message, type, param?, code?}} always
   - Reuses FastAPI JSONResponse for headers support

2. EXISTING _build_safe_block_response (reuse, wrap):
   - Currently at main.py:506-578
   - Refactor to call _build_openai_error_response for the error field
   - Preserves ZeroShield metadata (request_id, category, blocked_by, detection_tier, pipeline_trace) at top level
   - No logic change, structure-only refactor

3. EXISTING stream_orchestration.py header injection (extend):
   - stream_orchestration.py::enrich_stream_headers (line 168) already injects X-ZeroShield-* headers
   - Add x-request-id alongside X-ZeroShield-* batch
   - Reuse request_id from StreamRunMetrics

4. EXISTING list filtering (reuse for pagination):
   - main.py::_filter_inference_eligible_models (routes inference filtering)
   - Wrap with _paginate_list_response helper
   - No new dependencies, applies limit/after to existing model lists

5. PIPELINE TRACE SANITIZATION (existing):
   - main.py::_scrub_trace_for_client (line 577) already strips sensitive fields
   - No change needed; works with nested error shape

## Gaps
[
  {
    "title": "Error envelope shape inconsistency (3x patterns)",
    "severity": "High",
    "detail": "ZeroShield uses 3 incompatible error formats simultaneously: (1) flat {error: string, message} (auth, main.py:162,173), (2) flat {error, message, code} with string code (input validation, main.py:3656), (3) nested {error: {message, type, code}} (upstream errors, main.py:1472). Stock OpenAI SDK expects NESTED {error: {message, type, param?, code?}} for ALL errors. When SDK receives flat format, it fails to parse error.error.message (AttributeError) or treats error as string instead of object.",
    "files": "gateway/ai_mesh_gateway/main.py:153-174, 506-578, 1451-1475, 3654-3657; gateway/ai_mesh_gateway/middleware.py:332-402",
    "implementationApproach": "Create a unified _build_openai_error_response(status_code, message, error_type, param=None, code=None) helper that ALWAYS returns {error: {message, type, param?, code?}} nested structure. Routes: (1) Auth failures (middleware.py) \u2192 call helper with error_type='invalid_api_key'|'invalid_request_error', code='unauthorized'|'forbidden' (2) Input validation (main.py:3654+) \u2192 error_type='invalid_request_error', param='field_name', code='invalid_X' (3) Policy blocks \u2192 map to 403 with error_type='permission_error', code='content_blocked' (4) Upstream errors (main.py:1472) \u2192 error_type='api_error', code from upstream. Reuse the firewall pipeline's _build_safe_block_response by normalizing its output through the helper.",
    "effort": "M",
    "firewallRisk": "MEDIUM \u2014 error handling doesn't enforce policy, only informs SDK/client. Wrapping in a standard envelope preserves all firewall decision logic (blocked_by, detection_tier in pipeline_trace). The helper is a serialization-only shim, no policy mutation."
  },
  {
    "title": "Auth errors missing nested structure",
    "severity": "High",
    "detail": "middleware.py::validate_api_key returns {status_code, error: string, message: string} but OpenAI SDK's AuthenticationError.__init__ expects response.json()['error'] to be a dict with .get('type'), .get('message'), .get('code'). Stock SDK code: `error_dict = error_dict_or_str if isinstance(error_dict_or_str, dict) else {'message': error_dict_or_str}` \u2014 it CONVERTS string errors to dicts, but loses type/code context, causing the SDK's error.type='undefined' and code parsing to fail. Manifest: invalid key returns 401 as openai.AuthenticationError but `.error.type` is None instead of 'invalid_api_key'.",
    "files": "gateway/ai_mesh_gateway/middleware.py:104-200, 332-402",
    "implementationApproach": "Modify validate_api_key to return nested error dict: {status_code, error: {message, type, code}, message_legacy} \u2014 or refactor AuthMiddleware to use _build_openai_error_response helper. Status-based type mapping: 401 \u2192 {type: 'invalid_api_key', code: 'unauthorized'}, 403 \u2192 {type: 'permission_error', code: 'api_key_disabled'|'api_key_expired'}, 503 \u2192 {type: 'server_error', code: 'service_unavailable'}. JSONResponse now wraps only the nested error: {...error dict...} not the status_code.",
    "effort": "M",
    "firewallRisk": "LOW \u2014 auth middleware is pre-firewall. Changes only affect error shape in 401/403/503 paths. Firewall decisions (policy engine) still run for 200 paths."
  },
  {
    "title": "List responses missing pagination fields",
    "severity": "Medium",
    "detail": "main.py:10421-10474 (/v1/models) and main.py:7189 (/v1/embeddings) return {object: 'list', data: [items]} with NO has_more, first_id, last_id, or cursor pagination support. Stock OpenAI SDK's List models parse .has_more to determine if next page exists; absence implies false, so works. But Assistants/Responses APIs REQUIRE full pagination fields + limit/after query params for compliance. Current implementation has no offset/limit query params, so cannot support Responses API list endpoints (msg list, run list, thread list) when implemented.",
    "files": "gateway/ai_mesh_gateway/main.py:10431-10474 (/v1/models route); 10411-10430 (OpenAPI example)",
    "implementationApproach": "Add cursor/offset pagination helper: _paginate_list_response(items: list, limit: int=20, after: str=None) \u2192 {object: 'list', data: limited_items, has_more: bool, first_id: str, last_id: str}. Extract limit/after from query params with defaults. For /v1/models: apply pagination if >100 models configured. For Responses API endpoints (future): enforce pagination on all list routes (messages, runs, threads). Query param schema: ?limit=20&after=resp_xyz (cursor-based, not offset-based, to match OpenAI). Reuse the list-building logic from LLM_ROUTER.get_model_list() and RAG collections.",
    "effort": "M",
    "firewallRisk": "LOW \u2014 pagination is UI/UX only, doesn't affect firewall enforcement. Cursor validation must be defensive (invalid cursor \u2192 400, not 500) to avoid DoS on list routes."
  },
  {
    "title": "Missing 'param' field for input validation errors",
    "severity": "Medium",
    "detail": "Input validation errors (main.py:3654-3785: invalid_request, invalid_model, invalid_max_tokens, invalid_messages) return {error: 'invalid_request', message, code} but OpenAI SDK expects {error: {message, type: 'invalid_request_error', param: 'field_name', code}}. The param field tells the SDK (and the client) WHICH request field is malformed, enabling targeted UI error messages. Current implementation has code but no param, losing diagnostic context.",
    "files": "gateway/ai_mesh_gateway/main.py:3654-3785 (validation loop for messages, max_tokens, n, model, etc.)",
    "implementationApproach": "Audit all input validation returns and add param field: return JSONResponse(status_code=400, content=_build_openai_error_response(message='...', error_type='invalid_request_error', param='max_tokens', code='invalid_max_tokens')). Param mapping: 'model' \u2192 invalid_model, 'max_tokens' \u2192 invalid_max_tokens, 'messages' \u2192 invalid_messages, 'n' \u2192 invalid_n, 'temperature' \u2192 invalid_temperature, etc. Replaces hardcoded JSONResponse calls with the centralized helper, inheriting nested structure + param support.",
    "effort": "M",
    "firewallRisk": "NONE \u2014 validation errors don't execute policy, only reject malformed requests before firewall. Adding param is pure metadata."
  },
  {
    "title": "Policy block error uses flat shape incompatible with SDK",
    "severity": "High",
    "detail": "main.py::_build_safe_block_response (line 506) returns {error: 'blocked', message, code, request_id, category, blocked_by, ...} \u2014 flat top-level error field. OpenAI SDK's PermissionDeniedError.__init__ expects response.json() to have error as dict {message, type, code}. Current shape causes SDK parsing to fail: error.type \u2192 AttributeError (string has no .type). Manifest: blocked request raises openai.APIStatusError instead of openai.PermissionDeniedError, or error.body is raw string instead of parsed dict.",
    "files": "gateway/ai_mesh_gateway/main.py:506-578 (_build_safe_block_response)",
    "implementationApproach": "Refactor _build_safe_block_response to use nested error: {error: {message, type: 'permission_error', code: 'content_blocked'}, request_id, category, blocked_by, detection_tier, pipeline_stage, ...}. Keep firewall-specific fields (request_id, category, blocked_by, detection_tier, pipeline_trace) at top level for operator consumption; move ONLY message/type/code into error object for SDK compliance. Non-breaking: SDK parses response.json()['error']['message'], operators still access body.get('request_id'). Reuse _build_openai_error_response as the nested error builder, then wrap with ZeroShield metadata.",
    "effort": "M",
    "firewallRisk": "MEDIUM \u2014 _build_safe_block_response is the firewall's main enforcement response. Changes must preserve all policy signals (blocked_by, detection_tier, pipeline_trace) in response body, only wrapping the error field. No logic changes, structure-only."
  },
  {
    "title": "Missing standard x-request-id response header",
    "severity": "Low",
    "detail": "OpenAI SDK and API standards expect x-request-id header on all responses for correlation/debugging. ZeroShield emits X-ZeroShield-* headers (stream_orchestration.py:168, main.py:5298-5440) but NOT x-request-id. Stock clients/proxies log x-request-id by convention; absence breaks trace correlation with external observability tools (DataDog, New Relic).",
    "files": "gateway/ai_mesh_gateway/main.py:5298-5440 (response header injection for 200s)",
    "implementationApproach": "Add x-request-id header to all response paths: (1) extract/generate request_id from ZeroShield metadata or generate UUID, (2) inject into response headers on success paths (200), (3) include in error responses via JSONResponse headers parameter. Reuse existing request_id generation in _build_safe_block_response (uuid.uuid4().hex[:12]), propagate through the response object. Middleware (middleware.py) could inject a trace_id at the HTTP level for all requests.",
    "effort": "S",
    "firewallRisk": "NONE \u2014 response header addition is metadata-only, doesn't affect enforcement."
  },
  {
    "title": "Response/Assistants/Threads IDs not using OpenAI prefixes (resp_*, msg_*, run_*, thread_*)",
    "severity": "Medium",
    "detail": "Current ID formats: chat.completion='chatcmpl-...', ZeroShield trace='zs-...'. When Responses API (resp_*, msg_*), Assistants (run_*, thread_*), and Threads API are implemented, IDs MUST use OpenAI-standard prefixes. Using 'zs-' or 'chatcmpl-' for response IDs will cause SDK deserialization to fail (SDK checks id.startswith('resp_') to route responses). No implementation yet, but surfaced as future gap.",
    "files": "gateway/ai_mesh_gateway/main.py:530 (zs- prefix for request_id); test_openai_sdk_compat.py:79 (chatcmpl- for completions). No resp_*, msg_*, run_*, thread_* implementations yet.",
    "implementationApproach": "When Responses API routes are added: use resp_<uuid> for response IDs, msg_<uuid> for message IDs, thread_<uuid> for thread IDs, run_<uuid> for run IDs. Create ID helper function: _generate_openai_id(object_type: str) \u2192 f'{TYPE_PREFIX[object_type]}_<random>'. Central registry of all OpenAI object types + their prefixes. Currently affects only future Responses/Assistants implementation; no urgent change for chat/embeddings/models.",
    "effort": "S",
    "firewallRisk": "NONE \u2014 applies only to future API surfaces; current chat/embeddings are compliant."
  },
  {
    "title": "Error type/code mapping incomplete for policy decisions",
    "severity": "Medium",
    "detail": "Blocked requests (main.py:562-571) use code='content_blocked' with category='prompt_injection'|'jailbreak'|'pii' etc., but OpenAI SDK's error.code is machine-parseable (should be per OpenAI's error code taxonomy: 'invalid_request_error', 'authentication_error', 'permission_error', 'server_error', etc.). Current codes ('content_blocked', 'tier2_degraded', 'kill_switch_active') are custom ZeroShield codes, not OpenAI-compatible. SDK code parsing may fail for conditional logic like `if error.code == 'model_not_found'` \u2014 ZeroShield returns 'model_not_configured' instead.",
    "files": "gateway/ai_mesh_gateway/main.py:562-571 (block response), 3654-3785 (input validation codes)",
    "implementationApproach": "Establish error code taxonomy mapping: ZeroShield code \u2192 OpenAI error_type + OpenAI code. E.g., 'content_blocked' \u2192 error_type='permission_error', code='content_policy_violation' (or reuse OpenAI's closest: 'permission_error'). Create _map_to_openai_error_code(zs_code: str) helper. Maintain both: error.code (OpenAI-standard for SDK) and a custom x-zeroshield-code header (operator dashboard). Codes affecting SDK: model_not_configured \u2192 'model_not_found', rate_limited \u2192 'rate_limit_exceeded', policy_unavailable \u2192 'service_unavailable'.",
    "effort": "M",
    "firewallRisk": "LOW \u2014 code field is informational. Changes don't affect policy enforcement, only error classification for client/operator consumption."
  }
]