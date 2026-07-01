# Audit/Observability API surface — programmatic real-time event streams and trace retrieval

## Current
**Gateway (FastAPI):** Current audit surface is fragmented across admin-gated HTTP endpoints:
- `/v1/admin/logs` (SSE stream, admin-only) — gateway logs via log_buffer.LOG_BUFFER subscribe()
- `/v1/admin/bedrock-logs` + `/v1/admin/bedrock-logs/stream` (SSE, admin-only) — Bedrock-specific telemetry from bedrock_logger.BEDROCK_LOG_RING
- `/metrics` (Prometheus text format, auth-optional) — gateway/ai_mesh_gateway/metrics.py counters (amf_gateway_requests_total, amf_gateway_policy_blocks_total, etc.)
- **No programmatic event list endpoint:** No GET /v1/audit/events or /v1/observability/events with filtering by request_id, org, timestamp, action
- **No request-specific trace retrieval:** No GET /v1/audit/trace/{request_id} to correlate gateway decision steps

**Control Plane (Django):** Audit data lives in models.EnforcementEvent + Mongo operational_events:
- `EnforcementEvent` model (policy/models.py:166) — Postgres, tenant-scoped: org, policy, rule, action (block/redact/monitor), user_id, agent FK, metadata (JSON), created_at, event_class (enum: enforcement, policy_hmac_failure, tier2_breaker_state_change, query_rewritten, query_blocked, query_downgraded, hallucination_detected)
- Index: (organization, event_class, -created_at) for SOC triage queries
- **Policy enforcement logging:** /api/security/* endpoints (policy/security_urls.py + security_views.py) expose ThreatFeedView, OwaspEventsView, SecurityIncidentListView, EnforcementActionStatsView — these are Django/REST, not OpenAI-compatible
- **No OpenAI-compatible event list:** No GET /v1/audit/events endpoint routable through FastAPI gateway
- **No cross-plane correlation:** Bedrock request_id + ZeroShield request_id (zs-<12 hex>) are not unified in an OpenAI-compatible trace contract

**Telemetry pipeline (gateway/ai_mesh_gateway/):**
- `telemetry_ops.py` (line 63) — emit_operational_event() async helper, fires-and-forgets to Mongo sink (telemetry_mongo.py) if GATEWAY_MONGO_TELEMETRY_ENABLED=true
- `telemetry_mongo.py` — optional Mongo collection (aiguardx_telemetry.enforcement_events); best-effort writes; Mongo index: (org_slug, event_class, ts DESC)
- **Pipeline trace:** pipeline_trace.py (L138) enrich_zeroshield_from_verdict() merges guard-model fields into client metadata, but this is embedded in response, not separately queryable
- **No persistent request-level trace store:** The 12-stage ZeroShield pipeline (scanner Tier-1 → Tier-2 → policy adjudicator → output guard → routing) leaves no queryable audit trail except the final action in EnforcementEvent.metadata

**Request ID correlation:**
- Gateway generates `f"zs-{uuid.hex[:12]}"` per request (main.py:530, 1289) — present in every response.zeroshield.request_id
- Bedrock client uses separate `request_id` (bedrock_logger.py:280, hex[:12] without prefix)
- **No unified trace log:** Query for "show me all stages for zs-abc123def456" does not map to anything in Postgres or Mongo

**Files involved:**
- gateway/ai_mesh_gateway/main.py: L10410 /v1/models (ModelViewSet anchor), L1264 _build_zeroshield_metadata, L530 request_id generation
- gateway/ai_mesh_gateway/metrics.py: Prometheus counter/histogram defs (L71–L100+)
- gateway/ai_mesh_gateway/telemetry_ops.py: emit_operational_event (L63), KNOWN_EVENT_CLASSES (L48)
- gateway/ai_mesh_gateway/telemetry_mongo.py: record_enforcement_event (L83), collection init + index (L37, L73)
- control/ai_mesh_control/policy/models.py: EnforcementEvent (L166), event_class field (L203)
- control/ai_mesh_control/policy/security_views.py: ThreatFeedView (L295), ThreatSourcesView (L1589) — Django-only, not OpenAI-compatible
- control/ai_mesh_control/policy/evaluation_urls.py: /api/policy/enforcement-events/ (EnforcementEventBatchView, agent-facing, ingest only)

## OpenAI Spec
**OpenAI Audit API (from https://platform.openai.com/docs/api-reference/audit-logs):**
- **GET /v1/audit_logs** — List audit log events, paginated. Query params: limit (default 20, max 100), after (cursor), resource_id, resource_type, action (GET, POST, DELETE, PATCH), category, created_at.start_date, created_at.end_date. Returns {object: "list", data: [{id, type, api_version, created_at, user, resource, action, category, status, metadata}], has_more, next_page}.
- Response: 200 {object: "list", data: [{...}], has_more: bool, first_id, last_id}

**OpenAI Admin API (from https://platform.openai.com/docs/api-reference/admin):**
- **GET /v1/organization/projects/{project_id}/audit_logs** — Scoped to project/org
- **POST /v1/organization/audit_logs/query** — Advanced filtering (Elasticsearch-like)

**OpenAI Realtime Events (Responses API, from https://platform.openai.com/docs/guides/realtime):**
- **GET /v1/responses/{response_id}/events** — Stream response.created / response.output_text.delta / response.output_item.added / response.completed / error events as SSE
- **GET /v1/responses** — List responses with status, user_id, resource_type, created_at range

**Proposed ZeroShield alignment:**
- Expose gateway-side audit trail (request_id + enforcement decision) via **GET /v1/audit/events** (OpenAI-compatible shape)
- Expose per-request trace (all pipeline stages) via **GET /v1/audit/trace/{request_id}** (new, custom to ZeroShield)
- Expose observability streams via **GET /v1/audit/stream** (SSE, OpenAI Realtime-inspired)
- Tenant-scoped: filter by org_id (from auth context)
- Response format: {object: "list", data: [{request_id, action, threat_type, created_at, source, metadata}], has_more, next_page}

## Reusable hooks
**Firewall Pipeline Enforcement Reuse:**
- `main.py::_extract_api_key_context` (L~450) — extract org_id from X-API-Key, reuse for tenant scoping all new endpoints
- `main.py::verify_client_request_authorization` (called throughout) — auth check for /v1/* endpoints
- `main.py::_org_audit_logging_enabled` (L2755) — check if org has audit logging enabled before persist; gate /v1/audit/events behind this flag
- `main.py::_audit_fire_and_forget` (L2692) — async telemetry emit wrapper; enhance to pass request_id through and record in Postgres EnforcementEvent
- `pipeline_trace.py::enrich_zeroshield_from_verdict` (L138) — already builds rich trace object; return trace breakdown (stages array) for persistence in new endpoints
- `pipeline_trace.py::_redact_for_client_response` (L472) — allowlist of fields safe for clients; reuse when filtering /v1/audit/events response to strip PII fields (redacted_prompt, original_prompt_hash, compliance_tags) if caller is not org admin

**Telemetry Pipeline Reuse:**
- `telemetry_ops.py::emit_operational_event` (L63) — existing async sink; enhance to include request_id and event_class in all calls; /v1/audit/events reads from this stream
- `telemetry_ops.py::KNOWN_EVENT_CLASSES` (L48) — extend with routing_decision, output_guard_block, mcp_audit, vector_retrieval_blocked
- `telemetry_mongo.py::record_enforcement_event` (L83) — already writes events to Mongo; ensure request_id field is present
- `bedrock_logger.py::new_request_id` (L280) — use zs-prefixed format as **primary** request_id across gateway (already done in main.py; ensure bedrock calls receive it)

**Control Plane Model & Query Reuse:**
- `control/policy/models.py::EnforcementEvent` (L166) — existing audit model; add optional request_id field (CharField, indexed) + trace_json field (JSONField) for full pipeline trace
- `control/policy/audit_utils.py` (L~10) — existing audit helpers; reuse for query filtering and compliance tag resolution
- `control/policy/compliance_service.py` — reuse compliance tag mapping (GDPR, SOC2, HIPAA) when returning event metadata
- Django ORM query pattern (already in evaluation_views.py:_enforcement_events_for_request ~L~300) — filter by org, created_at range, action, event_class; reuse for /v1/audit/events implementation

**Response Shape Reuse:**
- `docs/contracts/ZeroShieldResponse.v1.md` (L40–86) — response.zeroshield contract; align /v1/audit/events data shape to match field names (request_id, action, threat_type, detection_tier, risk_score, matched_patterns, confidence)
- `gateway/ai_mesh_gateway/vector_routes.py` — RAG endpoints return {object, project_id, collections} shape; adopt {object: 'list', data: [...], has_more} for consistency

## Gaps
[
  {
    "title": "Missing /v1/audit/events endpoint (OpenAI-compatible event list)",
    "severity": "Critical",
    "detail": "No programmatic way to query enforcement events via an OpenAI-compatible REST API. Currently, audit data exists in Postgres (control/policy/models.py:EnforcementEvent) and optional Mongo (telemetry_mongo), but there is no gateway-side endpoint exposing them with pagination, filtering, tenant-scoping, and stock OpenAI SDK compatibility. Client must call Django control plane APIs (/api/security/* routes) which are not OpenAI-compatible.",
    "files": "gateway/ai_mesh_gateway/main.py (no GET /v1/audit/events), control/ai_mesh_control/policy/models.py:166 (EnforcementEvent model), control/ai_mesh_control/policy/security_views.py (ThreatFeedView uses Django REST, not compatible)",
    "implementationApproach": "Add FastAPI GET /v1/audit/events endpoint in gateway/main.py (~L10450) that: (1) extracts tenant org_id from X-API-Key auth context (reuse _extract_api_key_context or verify_client_request_authorization pattern from main.py), (2) queries Postgres EnforcementEvent via Django ORM (import from control plane via shared SDK or async query helper), (3) filters by org_id, event_class (query param), created_at range (after/before), action, status (optional), (4) paginates with limit/offset or cursor (after param), (5) returns OpenAI-compatible shape: {object: 'list', data: [{request_id, action, threat_type, event_class, created_at, policy_code, rule_code, user_id, metadata}], has_more, next_page}. Reuse pipeline_trace.py label formatters for operator-facing field names. Inherit firewall policy enforcement (fail-closed tenant isolation via org_id binding) from _org_audit_logging_enabled pattern at main.py:2755.",
    "effort": "M",
    "firewallRisk": "Enforcement: must strictly scope results to authenticated tenant's org_id (not a client-supplied filter). Mitigation: bind org_id to auth token at gateway boundary (main.py._extract_api_key_context returns Organization FK); query only that org. Isolation: queries hit Postgres (separate from hot request path), so no latency/availability risk. Information disclosure: metadata field may contain PII (prompt snippet, user_id) \u2014 strip before returning unless explicitly caller-scoped (e.g. user_id filter, org admin role). Use audit_utils.py patterns or redact_all from patterns module (pipeline_trace.py:16)."
  },
  {
    "title": "Missing /v1/audit/trace/{request_id} endpoint (request-level trace retrieval)",
    "severity": "Critical",
    "detail": "No way to correlate all pipeline decision stages (scanner Tier-1 \u2192 Tier-2 \u2192 policy \u2192 output guard \u2192 routing \u2192 final action) for a single request by its zs-<request_id>. The 12-stage firewall pipeline (_audit_fire_and_forget pattern at main.py:2692) leaves only a final EnforcementEvent row, not a queryable breakdown of each stage's verdict. OpenAI Responses API has GET /v1/responses/{id}/events; ZeroShield should expose per-request pipeline breakdown.",
    "files": "gateway/ai_mesh_gateway/main.py (no GET /v1/audit/trace/{request_id}), gateway/ai_mesh_gateway/pipeline_trace.py:138 (enrich_zeroshield_from_verdict merges fields but doesn't persist trace), control/ai_mesh_control/policy/models.py:195 (metadata JSON field stores some trace, not searchable)",
    "implementationApproach": "Add FastAPI GET /v1/audit/trace/{request_id} endpoint in gateway/main.py (~L10450) that: (1) validates request_id format (zs-<12 hex>), (2) queries Postgres EnforcementEvent filtered by (org_id, metadata @> {\"request_id\": request_id}) to find the summary row, (3) extracts metadata.trace array (if persisted during _audit_fire_and_forget) or reconstructs from metadata fields (guard_action, detection_tier, matched_patterns, routing fields), (4) optionally queries Mongo enforcement_events collection for detailed stage-by-stage log (event_class=enforcement AND metadata.request_id=request_id), (5) returns {request_id, stages: [{name: 'scanner_tier_1', verdict, duration_ms, reason}, {name: 'scanner_tier_2', ...}, {...}], final_action, final_reason, created_at, policy_applied: {code, name}, matched_rules: [{code, name, action}], organization_id}. Stage names defined in pipeline_trace.py (GUARD_MODEL_LABEL, PATTERN_ENGINE_LABEL, OUTPUT_GUARD_LABEL, etc., L24\u201327).",
    "effort": "M",
    "firewallRisk": "Enforcement: request_id lookup must not leak cross-org data. Mitigation: always bind query to authenticated org_id; reject requests for request_id from a different org (fail-closed). Isolation: Postgres + Mongo queries are off-hot-path. Information disclosure: trace may reveal policy rule triggers, guard model scores, or matched patterns \u2014 only expose to org admins or role-based access control. Implement role check (e.g. IS_ORG_ADMIN or AUDITOR role) before returning detailed fields."
  },
  {
    "title": "Missing /v1/audit/stream endpoint (real-time SSE event stream, OpenAI Realtime-compatible)",
    "severity": "High",
    "detail": "Current SSE streams (/v1/admin/logs, /v1/admin/bedrock-logs/stream) are admin-only and not tenant-scoped. No OpenAI Realtime-compatible event stream for audit trails (analogous to Responses API response.created / response.output_text.delta / response.completed events). Operators cannot subscribe to policy violations in real-time per tenant/org.",
    "files": "gateway/ai_mesh_gateway/main.py (L9867 /v1/admin/logs, L9936 /v1/admin/bedrock-logs/stream \u2014 both admin-only), gateway/ai_mesh_gateway/log_buffer.py (LOG_BUFFER subscription mechanism), telemetry_ops.py (emit_operational_event async fire-and-forget)",
    "implementationApproach": "Add FastAPI GET /v1/audit/stream endpoint in gateway/main.py (~L10475) that: (1) authenticates via X-API-Key or Authorization header (reuse verify_client_request_authorization), (2) extracts org_id from auth context, (3) opens a Mongo change stream (motor AsyncIOMotorClient.watch) on enforcement_events collection filtered by {org_slug: org_slug}, (4) yields server-sent events with type: 'audit.event_created', data: {request_id, action, threat_type, event_class, created_at, metadata}, (5) supports optional query params: event_class filter, severity filter (warning/critical). Falls back to poll-based implementation if Mongo unavailable (tail Postgres EnforcementEvent with periodic SELECT WHERE created_at > last_known_ts). Emit trace via telemetry_ops.emit_operational_event when stream subscription starts (audit trail that org_id subscribed).",
    "effort": "M",
    "firewallRisk": "Enforcement: streams must be org-isolated. Mitigation: bind event filter to org_id at stream open time; all yielded events verified to have matching org_slug. Isolation: SSE stream is long-lived connection; implement timeout + heartbeat to prevent resource exhaustion (idle streams kill after 5min). Information disclosure: event metadata may contain user_id, agent_id, original_prompt_hash, redaction_fields \u2014 filter via _redact_for_client_response pattern (main.py:472); only org admins receive full metadata."
  },
  {
    "title": "Missing unified request ID correlation across gateway \u2192 Bedrock \u2192 control plane",
    "severity": "High",
    "detail": "Gateway generates zs-<12 hex> request_id per chat completion (main.py:530), Bedrock client generates separate 12-digit hex ID (bedrock_logger.py:280), and control plane EnforcementEvent has its own implicit ID (Django pk). No single trace thread ties all three together. Operators cannot follow a single request through input scan \u2192 Bedrock judgment \u2192 policy decision \u2192 control plane audit log.",
    "files": "gateway/ai_mesh_gateway/main.py (L530 zs-{uuid.hex[:12]}, L1289 request_id generation), gateway/ai_mesh_gateway/bedrock_logger.py (L280 new_request_id, hex[:12] no prefix), control/ai_mesh_control/policy/models.py (EnforcementEvent.metadata JSON, no explicit request_id field), stream_orchestration.py (L114 request_id ctx field, L355 passed in trace, L469\u2013470 conditionally included)",
    "implementationApproach": "Modify request ID generation to propagate consistently: (1) Keep zs-<12 hex> as the **primary request_id** (generate once at request entry, main.py ~L520). (2) Pass request_id through to bedrock_scanner.scan() + bedrock_client.invoke_model() calls (already done at bedrock_client.py with optional request_id param; ensure main.py passes it). (3) Embed request_id in EnforcementEvent.metadata at fire-and-forget site (main.py._audit_fire_and_forget already wraps telemetry_ops.emit_operational_event; pass request_id in event payload). (4) Modify telemetry_mongo.record_enforcement_event to ensure event dict has {request_id: zs-...} top-level field. (5) Update control/policy/models.py EnforcementEvent to add optional request_id CharField (indexed) for direct lookups. (6) Pipeline trace (pipeline_trace.py enrich_zeroshield_from_verdict) already includes request_id in zeroshield output; ensure bedrock_logger.py calls also log to bedrock_logger data dict (do not refactor Bedrock logs, log separately).",
    "effort": "M",
    "firewallRisk": "Enforcement: None \u2014 request_id is an internal correlation mechanism, not a security boundary. Isolation: Request ID is in responses + logs; ensure it is not an oracle (cannot reverse from request_id to infer org, model, user \u2014 check response envelope allows it as client-safe). Information disclosure: request_id is safe to expose (UUID-derived, no PII). No new attack surface if properly constrained."
  },
  {
    "title": "Missing compliance/audit event filtering by policy code and rule code",
    "severity": "High",
    "detail": "GET /v1/audit/events (when implemented) will need to filter by matched policy/rule. Currently, EnforcementEvent has FK to policy + rule (policy/models.py:183\u2013184), but no direct query-friendly way to ask 'show all violations of policy POL-001 in the last 24h'. Control plane has top-rules analytics (policy/analytics_views.py:TopRulesView), but not real-time rule-hit stream.",
    "files": "control/ai_mesh_control/policy/models.py (EnforcementEvent.policy FK:183, EnforcementEvent.rule FK:184), control/ai_mesh_control/policy/analytics_views.py (TopRulesView L188 for analytics, not real-time), control/ai_mesh_control/policy/security_urls.py (no /v1/audit/events/by-rule route)",
    "implementationApproach": "When implementing GET /v1/audit/events: (1) Add optional query params: policy_code (string), rule_code (string), category (string). (2) Resolve policy_code \u2192 policy.id via control-plane query or cached lookup (implement cache layer for policy code \u2192 id). (3) Filter EnforcementEvent by policy_id or rule_id if supplied. (4) If rule_code supplied, join to policy.Rule and filter by rule.code. (5) Return matched_policy_code and matched_rule_code in each event row (via SELECT policy.code, rule.code in ORM). Reuse enum ACTION_BLOCK, ACTION_REDACT, ACTION_MONITOR from policy/constants.py for action filter.",
    "effort": "S",
    "firewallRisk": "Enforcement: Policy/rule code lookups must be org-scoped. Mitigation: add WHERE clause policy.organization_id = authenticated_org_id when resolving code \u2192 id. Isolation: None. Information disclosure: None if rule.code is already visible in policy management UI."
  },
  {
    "title": "Missing Postgres persistence of full trace (not just final event)",
    "severity": "Medium",
    "detail": "Pipeline trace (all 12 stages) is built in-memory via pipeline_trace.py:enrich_zeroshield_from_verdict but only the final decision is persisted to Postgres EnforcementEvent. If operator queries 'trace' endpoint, we must reconstruct from embedded metadata JSON (error-prone), or query optional Mongo. High-value audit data (which stage blocked, confidence scores per stage, which rules matched at policy stage) is lost after response is sent.",
    "files": "gateway/ai_mesh_gateway/pipeline_trace.py (L138 enrich_zeroshield_from_verdict builds full trace in memory), gateway/ai_mesh_gateway/stream_orchestration.py (L417 build_stream_trace_frame yields final frame, not persisted), control/ai_mesh_control/policy/models.py (EnforcementEvent.metadata JSONField, does not have schema for trace array)",
    "implementationApproach": "Add optional trace_json JSONField to EnforcementEvent model (Django migration required). When calling telemetry_ops.emit_operational_event or creating EnforcementEvent row, pass trace_array = [{stage: 'scanner_tier_1', verdict: {...}, duration_ms: X}, {stage: 'policy_adjudicator', ...}] extracted from pipeline_trace.enrich_zeroshield_from_verdict result (add return of trace breakdown). Store in new field or extend metadata schema (metadata.trace_stages array). Ensure trace data is redacted per R17 (pipeline_trace._truncate already does PII redaction; apply before persist). Query endpoint (GET /v1/audit/trace/{request_id}) reads trace_json field directly. Mongo sink (telemetry_mongo) also receives full trace in event dict.",
    "effort": "M",
    "firewallRisk": "Enforcement: Trace contains matched patterns, risk scores, policy verdicts \u2014 only org admins should see. Mitigation: role-check in GET /v1/audit/trace/{request_id} (AUDITOR or ORG_ADMIN role required). Isolation: New field is read-only from client perspective (no query param injection). Information disclosure: Trace redacts PII at pipeline stage before persist (R17); ensure _truncate covers all text fields in trace_json."
  },
  {
    "title": "Missing event_class enum expansion for advanced observability (e.g. routing decisions, output guard, MCP audit)",
    "severity": "Medium",
    "detail": "telemetry_ops.py:KNOWN_EVENT_CLASSES (L48) has 9 values (enforcement, policy_hmac_failure, tier2_breaker_state_change, query_rewritten, query_blocked, query_downgraded, hallucination_detected). Missing: routing_decision, output_guard_block, mcp_tool_access_denied, mcp_response_redacted, vector_retrieval_blocked. Without fine-grained event classes, /v1/audit/events?event_class=routing cannot distinguish routing decisions from policy blocks.",
    "files": "gateway/ai_mesh_gateway/telemetry_ops.py (L36\u201359 KNOWN_EVENT_CLASSES), control/ai_mesh_control/policy/models.py (L203 event_class CharField, no validation vs KNOWN_EVENT_CLASSES)",
    "implementationApproach": "Extend KNOWN_EVENT_CLASSES frozenset in telemetry_ops.py to include: EVENT_CLASS_ROUTING_DECISION = 'routing_decision', EVENT_CLASS_OUTPUT_GUARD_BLOCK = 'output_guard_block', EVENT_CLASS_MCP_AUDIT = 'mcp_audit', EVENT_CLASS_VECTOR_BLOCKED = 'vector_retrieval_blocked'. Update comment block (L34\u201335) to document when each is emitted. Call emit_operational_event(..., event_class=EVENT_CLASS_ROUTING_DECISION, ...) at routing decision points (e.g. llm_router.py when selected_model != original_model). For output guard, call at scanner.py or output_guard.py verdict points. For MCP audit, call from mcp_proxy.py tool-access checks. For vector, call from vector_routes.py filter points. Update KNOWN_EVENT_CLASSES in control/policy/models.py comment (L201\u2013210) to keep in sync.",
    "effort": "M",
    "firewallRisk": "Enforcement: None \u2014 event_class is a tag, not a enforcement decision point. Isolation: None. Information disclosure: event_class is visible to org admins; does not leak sensitive data beyond the fact that a decision happened at a certain stage."
  },
  {
    "title": "Missing tenant scoping enforcement for admin/observability endpoints",
    "severity": "High",
    "detail": "/v1/admin/logs and /v1/admin/bedrock-logs (main.py:L9867, L9905) require admin role but do NOT scope to tenant org. A multi-tenant gateway where Admin User A belongs to Org-X can view logs from Org-Y (or global logs). This violates tenant isolation (no cross-org data leak).",
    "files": "gateway/ai_mesh_gateway/main.py (L9872 admin_logs, L9915 admin_bedrock_logs use _require_admin_role, no org filter; L2755 _org_audit_logging_enabled checks org config but doesn't restrict scope)",
    "implementationApproach": "Modify /v1/admin/logs and /v1/admin/bedrock-logs to add org-scoping: (1) Extract org_id from X-API-Key auth context (same as _extract_api_key_context). (2) Verify authenticated user is admin for that org (not global admin). (3) Filter log entries by log source (service name) + org_slug or request context (e.g. auth_context.organization_id in request.state). (4) For admin_logs (from log_buffer), add org field to buffered events (require LOG_BUFFER to tag each event with org_slug during emit). (5) For bedrock_logs (from bedrock_logger.BEDROCK_LOG_RING), add org field to Bedrock log entries (bedrock_logger.py log_bedrock_request must include org context). Alternatively, return HTTP 403 if user is not org admin for the queried org (assume query param org_id or derive from API key).",
    "effort": "M",
    "firewallRisk": "Enforcement: Tenant isolation \u2014 multi-tenant deployments must enforce strict scope. Mitigation: (1) Bind log queries to authenticated org_id, (2) Fail-closed: if org context missing or unclear, return 403 rather than defaulting to global scope, (3) Test with two separate organizations to ensure no cross-org log leakage. Isolation: Log stream is long-lived (SSE), so scope must be enforced at stream open, not per-event (do not yield events from other orgs). Information disclosure: Logs may contain request bodies, models, errors \u2014 already should be redacted, but org-scoping prevents wholesale access."
  },
  {
    "title": "Missing OpenAI-compatible response format for events (shape + field names)",
    "severity": "Medium",
    "detail": "When GET /v1/audit/events is implemented, response shape must be OpenAI-compatible. OpenAI Audit Log API returns {object: 'list', data: [...], has_more, first_id, last_id}. ZeroShield response should follow the same shape to be consumable by tools expecting OpenAI audit format.",
    "files": "control/ai_mesh_control/policy/security_views.py (ThreatFeedView returns Django REST framework Response, not OpenAI shape), gateway/ai_mesh_gateway/vector_routes.py (RAG endpoints return {project_id, collections, rag_available}, not OpenAI-compatible list shape)",
    "implementationApproach": "When implementing GET /v1/audit/events response: Return {object: 'list', data: [{request_id (string), action (string: 'allow'|'block'|'redact'|'flag'|'monitor'), threat_type (string), event_class (string), matched_patterns (string[]), matched_policy_code (string, nullable), matched_rule_code (string, nullable), user_id (integer, nullable), created_at (Unix timestamp integer), metadata: {risk_score: float, confidence: float, org_slug: string, ...}}], has_more (boolean), next_page (string, opaque cursor), first_id (string), last_id (string)}. Reuse OpenAI SDK pydantic model shape (from openai-python SDK or define locally). Field names: request_id (same as response.zeroshield.request_id), action (same as enforcement decision), threat_type (from detection), created_at (Unix timestamp, same as EnforcementEvent.created_at).",
    "effort": "S",
    "firewallRisk": "Enforcement: None \u2014 response shape is cosmetic. Isolation: None. Information disclosure: Ensure field names do not accidentally expose internal schema (e.g. don't return 'policy_id' (FK), only policy_code (stable slug))."
  },
  {
    "title": "Missing query audit / RAG retrieval audit trail integration with /v1/audit/events",
    "severity": "Medium",
    "detail": "telemetry_ops.py (L139) has emit_query_audit_event for D_G5 query rewrite/block/downgrade telemetry. This event type is stored in Mongo but not queryable via /v1/audit/events yet (endpoint doesn't exist). Additionally, RAG/vector retrieval blocks are logged via scanner or vector_routes.py, but no unified audit trail per collection or per document chunk retrieved.",
    "files": "gateway/ai_mesh_gateway/telemetry_ops.py (L139 emit_query_audit_event), gateway/ai_mesh_gateway/vector_routes.py (RAG endpoints, no explicit audit logging), gateway/ai_mesh_gateway/scanner.py (scans RAG context, emits enforcement events)",
    "implementationApproach": "When implementing GET /v1/audit/events: (1) Include query_rewritten, query_blocked, query_downgraded events in response (already in KNOWN_EVENT_CLASSES at telemetry_ops.py:42\u201344). (2) For RAG retrieval audit, add explicit emit_operational_event calls in vector_routes.py (e.g. after filtering collections by policy, or when a chunk is redacted/blocked). Create EVENT_CLASS_VECTOR_RETRIEVAL_BLOCKED and call emit_operational_event at filter points (e.g. vector_routes.py rag_retrieve endpoint ~L??). (3) Ensure event metadata includes rag_collection_id, document_id (if chunk-level blocking), or chunk_hash. (4) Query endpoint filters by event_class=query_rewritten | query_blocked | query_downgraded | vector_retrieval_blocked when rag_only param is set.",
    "effort": "S",
    "firewallRisk": "Enforcement: None \u2014 audit events are logging only. Isolation: RAG audit events should include collection_id to prevent cross-org collection leakage (e.g. Org-X user must not see blocks on Org-Y collections). Mitigation: Filter events by both org_id AND collection (if collection_id in event metadata), fail-closed if org doesn't own that collection. Information disclosure: Chunk-level blocking (e.g. which document snippet was blocked) is sensitive; only org admins should see."
  }
]