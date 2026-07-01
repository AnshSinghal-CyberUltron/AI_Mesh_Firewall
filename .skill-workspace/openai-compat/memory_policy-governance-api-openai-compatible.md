# Policy-Governance-API: OpenAI-compatible policy configuration and enforcement controls accessible via gateway keys (`/v1/zeroshield/policies/*`), enabling governance as programmable APIs alongside inference

## Current
Control plane (`control/ai_mesh_control/`) exposes Django REST API endpoints:
  - `/api/policies/` (CRUD + analytics): policy/rule management via JWT auth (control/ai_mesh_control/policy/views.py:PolicyViewSet L79-320)
  - `/api/firewall/config/` (GET/PUT): singleton firewall settings via JWT (core/firewall_config_views.py:FirewallConfigView L17-85)
  - `/api/security/` (read-only): threat intelligence, OWASP/incident dashboards (policy/security_urls.py L39-76)
  - `/api/kill-switches/` (CRUD + activate/deactivate): emergency model isolation via JWT, admin-gated mutations (core/kill_switch_views.py:KillSwitchViewSet L38-150)
  - `/api/models/` (GET): LLM model state (GET/PATCH/DELETE per model)

Gateway (`gateway/ai_mesh_gateway/`) runs FastAPI OpenAI-compatible data plane:
  - `/v1/chat/completions` POST (stream-capable): proxy to LLMs, runs full firewall pipeline (pipeline_trace.py, output_guard.py, scanner.py) via auth_context from middleware (main.py:L3598)
  - `/v1/embeddings` POST: proxy to embedding providers (main.py:L7211)
  - `/v1/models` GET: lists org-scoped models, SOFT_AUTH only (main.py:L10431)
  - `/v1/vector/*` (query/upsert/delete/config): RAG vector ops with firewall enforcement (vector_routes.py L33)

Auth model (separate per plane):
  - Gateway: Bearer token (GatewayAPIKey) lookup in Redis (middleware.py:validate_api_key L104-200), AuthContext injected into request.state with org_id, allowed_models, rate_limit_tpm, roles
  - Control: JWT (JWTAuthenticationWithTermination, auth/authentication.py L32-55) scoped to org via UserProfile.organization FK

Policy enforcement: compiled bundles stored in Redis (`policies:compiled:{org_slug}`) synced from control plane, deserialized at request time by proxy_chat() into policy_engine (policy_engine.py) for zero-latency tenant-isolated checks. No gateway-facing policy CRUD endpoints exist; all mutations go through control plane only.

Design intent: Gateway = stateless data plane; Control = single source of truth for governance. Policy propagation via Redis Pub/Sub (`policy_updates` channel, policy_sync.py L37).

## OpenAI Spec
OpenAI Management APIs (governance/policy namespaces):
  - `/v1/organization/members` (GET/POST/DELETE): org member RBAC
  - `/v1/organization/projects` (GET/POST/PATCH): project management
  - `/v1/organization/projects/{id}/api_keys` (GET/POST/DELETE): API key CRUD (parallel to control `/api/gateways/keys/`)
  - `/v1/organization/audit_logs` (GET): HITL/compliance audit trail
  - `/v1/batch` (POST/GET): async batch job submission (not governance, but management API surface)

OpenAI Responses API (used in M-51 streaming trace design):
  - `POST /v1/responses`: create response with typed input/output items, streaming events (response.created, response.output_text.delta, response.completed), previous_response_id state, tool_calls (file_search, web_search, function_calling)

No built-in OpenAI "policies" endpoint; governance is organizational/project-level (members, keys, audit). But ZeroShield can define `/v1/zeroshield/*` policy namespace per spec extension (extra top-level field tolerance in SDK, as per ZeroShieldResponse.v1.md L9-30).

## Reusable hooks
Existing infrastructure to extend (no re-implementation needed):
  1. **middleware.py AuthContext** (L61-102): bearer-key lookup, org scoping, permissions dict → reuse for policy endpoints (already carries org_id, allowed_models, roles)
  2. **policy_sync.py PolicySync** (L64-150): org-scoped Redis cache refresh via Pub/Sub → reuse _org_caches[org_slug] as data source for GET/LIST policy endpoints
  3. **output_guard.py OutputVerdict** + **pipeline_trace.py enrich_zeroshield** (L117+): policy enforcement verdict building → reuse for tool_call policy validation in Responses API
  4. **stream_orchestration.py** (L168, L348, L420): SSE frame building for streaming + finalize hook → reuse for `/v1/responses` streaming events
  5. **scanner.py InputScanner** + **policy_engine.py**: policy evaluation logic → reuse for policy test endpoint (forward to control plane's /api/policies/test, or import the compiled bundle eval directly)
  6. **core/models.py FirewallConfig, KillSwitch, Policy, Rule** + **signals.py**: post_save hooks that publish to Redis Pub/Sub → reuse for control-plane-to-gateway config sync (already implemented)
  7. **core/kill_switch_audit.py write_kill_switch_audit**: audit trail logging → reuse for kill-switch, firewall-config, and policy mutation audit trails
  8. **policy_signing.py verify_bundle, _get_signing_key**: HMAC validation of compiled bundles → reuse for validating any policy bundles synced to gateway
  9. **rag_collections.py coerce_org_id, list_collections_for_clients**: org scoping for vector operations → reuse pattern for policy tenant isolation
  10. **main.py _REQUEST_ID context var** (L216): request correlation → reuse for audit trail + policy trace linkage

## Gaps
[
  {
    "title": "Missing gateway-side policy CRUD endpoints (no `/v1/zeroshield/policies/*`)",
    "severity": "Critical",
    "detail": "Gateway has NO endpoints to create/read/update/delete policies; all policy mutation requires control-plane JWT roundtrip. OpenAI SDK users (bearer-key-only) cannot manage policies programmatically without separate control-plane credentials. This breaks the M-51 product requirement: 'ZeroShield consumable with stock OpenAI SDK \u2014 no custom SDK. Integration is a base-URL + API-key swap only' (ZeroShieldResponse.v1.md L7-9). Operators need to manage policies alongside inference requests using THE SAME API key.",
    "files": "gateway/ai_mesh_gateway/main.py (no policy endpoints), control/ai_mesh_control/policy/views.py (JWT-only, not gateway-accessible)",
    "implementationApproach": "Add FastAPI routers to gateway/ai_mesh_gateway/ (e.g., policy_management_routes.py) that expose `/v1/zeroshield/policies` (LIST/CREATE), `/v1/zeroshield/policies/{id}` (GET/PATCH/DELETE), `/v1/zeroshield/rules/{id}` (GET/PATCH/DELETE). Routes authenticate via auth_context (bearer key from middleware), extract org_id from auth_context.organization_id, then forward CREATE/PATCH/DELETE to control plane via internal HTTP client (e.g., httpx) using GATEWAY_INTERNAL_API_KEY header. Reads (LIST/GET) can be served from Redis cache (policies:compiled:{org_slug}) after deserializing the compiled bundle. This reuses the existing firewall pipeline infrastructure: policy validation, signing (policy_signing.py), and Pub/Sub sync (policy_sync.py) remain control-plane owned; gateway becomes a read-through cache + RPC forwarder.",
    "effort": "L",
    "firewallRisk": "Enforcement/isolation risk: Gateway endpoints must enforce tenant isolation (deny listing/mutation of policies outside the request's org). Reuse existing pattern: getattr(auth_context, 'organization_id') AND (auth_context.permissions.get('allowed_actions') includes 'policy_write' for mutations, 'policy_read' for reads) else 403. Mitigation: auth_context structure (middleware.py L61-102) already carries org_id + permissions dict; apply IsAuthenticated + org-scope filter at route handler (mirror kill-switch pattern, core/kill_switch_views.py L59-64 for queryset filtering, but in FastAPI via guard clause). No new privilege escalation surface if control-plane-only mutations are signed/verified before Redis sync."
  },
  {
    "title": "Auth model mismatch: gateway keys vs control JWT; no unified governance-api auth",
    "severity": "High",
    "detail": "Gateway uses bearer keys (GatewayAPIKey) cached in Redis; control plane uses JWT (JWTAuthenticationWithTermination). A bearer key cannot create JWT; SDK users hitting gateway for inference then hitting control-plane endpoints for policy management hit a 401. OpenAI's model: single key (api_key) works for `/v1/chat/completions` AND `/v1/organization/*` (both scoped by key's org/project). ZeroShield must make bearer keys valid against both gateway AND control-plane policy endpoints.",
    "files": "gateway/ai_mesh_gateway/middleware.py (L61-102 AuthContext), control/ai_mesh_control/auth/authentication.py (JWT only), control/ai_mesh_control/core/gateway_key_views.py (GatewayAPIKey management)",
    "implementationApproach": "Create a new Django REST permission class (`rest_framework.permissions.BasePermission` subclass) in control/ai_mesh_control/auth/ (e.g., GatewayBearerAuthentication) that extracts the bearer token from the request Authorization header, looks it up in Redis (reuse middleware.py:validate_api_key L104-200 or extract to a shared module), and populates request.user (or a synthetic auth object) with the GatewayAPIKey's user/org. Mount this as an additional authentication class on policy endpoint viewsets (policy/views.py@PolicyViewSet.authentication_classes). This allows either JWT OR bearer key to authenticate the same endpoint. Alternatively, for gateway-side reads of policy, inject a synthetic request-like object with org_id into the control-plane client call (httpx call with GATEWAY_INTERNAL_API_KEY header + X-Org-ID header passed through).",
    "effort": "M",
    "firewallRisk": "Authentication/isolation risk: bearer key lookup in Redis is network-dependent (same as gateway inference); if Redis is down, auth fails hard (middleware returns 503, fail-closed). Reuse same Redis client + backoff logic already in place. Privilege escalation: ensure bearer key's permissions dict (middleware.py L92 'permissions': dict) is checked on policy mutations \u2014 e.g., only keys with 'policy_write' in allowed_actions are authorized for PATCH/DELETE. Mitigation: model the permissions dict on OpenAI's API key scoping (chat, embedding, file, fine-tuning, organization.read, organization.write) and enforce at view layer (policy/views.py decorator or viewset.get_permissions())."
  },
  {
    "title": "No policy caching layer on gateway; reads require control-plane roundtrip",
    "severity": "High",
    "detail": "Gateway forwards all policy mutation requests to control plane (slow, adds latency). Policy READS (LIST/GET) should be served from gateway's in-memory cache (policies:compiled:{org_slug} in Redis, already synced by PolicySync L64-150, policy_sync.py). But currently NO gateway endpoints exist to READ policies, so the cache is invisible to SDK users. This violates latency expectations (OpenAI SDK users expect sub-10ms policy retrieval).",
    "files": "gateway/ai_mesh_gateway/policy_sync.py (L64-150, PolicySync class), policy_engine.py (policy evaluation, not CRUD)",
    "implementationApproach": "In policy_management_routes.py (new file in gateway/ai_mesh_gateway/), implement LIST and GET handlers that deserialize the org's Redis-cached compiled bundle (policy_sync.py.PolicySync._org_caches[org_slug]). The compiled bundle format is internal (gateway-only); so LIST/GET responses must re-inflate the bundle into a client-safe JSON schema matching control-plane's PolicySerializer (policy/serializers.py L153-178). Add a deserialize function to policy_sync.py that reconstructs Policy and Rule objects from the compiled Redis bundle for client consumption. This reuses the hot-path PolicySync subscriber \u2014 no new Redis infrastructure needed, just add GET/LIST route handlers that read from the already-populated _org_caches.",
    "effort": "M",
    "firewallRisk": "Enforcement/isolation risk: PolicySync._org_caches is org-scoped (keyed on org_slug), so reading the cache inherently isolates by org. Ensure the GET/LIST route extracts org_slug from auth_context.org_slug before accessing the cache (mirror pattern in main.py L3992, L7306) else raise 403. Mitigation: PolicySync is updated in-place by the Pub/Sub subscriber async task (policy_sync.py:_refresh_org L201+); reads are lock-free (Python GIL + dict read atomicity). No new race condition if read handlers are stateless."
  },
  {
    "title": "No firewall config endpoints on gateway; config changes require control-plane JWT",
    "severity": "High",
    "detail": "Firewall configuration (enable/disable modules, set thresholds, etc.) is managed at `/api/firewall/config/` on control plane (firewall_config_views.py L17-85, GET/PUT only, JWT-gated). SDK users cannot tune firewall behavior without separate control-plane access. OpenAI analogy: organization settings would be accessible via `/v1/organization/settings` (not a real OpenAI endpoint, but shows the pattern \u2014 org-level config alongside inference API keys).",
    "files": "control/ai_mesh_control/core/firewall_config_views.py (L17-85), core/models.py (FirewallConfig singleton), core/firewall_config_serializer.py",
    "implementationApproach": "Add `/v1/zeroshield/firewall/config` GET/PATCH endpoints to gateway (policy_management_routes.py). GET reads from the org-scoped FirewallConfig cached in Redis (CONFIG_SYNC in main.py broadcasts updates via Pub/Sub, like PolicySync). PATCH forwards to control plane (httpx call with GATEWAY_INTERNAL_API_KEY) and waits for the 'config_updates' Pub/Sub notification (poll Redis Pub/Sub or block-read from a future/event) to update the local cache. Returns the updated config in control-plane's FirewallConfigSerializer format. Reuse existing Redis Pub/Sub infra (main.py L3020+ startup subscribes to config_updates, policy_signing.py for HMAC validation).",
    "effort": "M",
    "firewallRisk": "Enforcement/isolation risk: FirewallConfig is currently a SINGLETON (not per-org). This is a critical gap: if a single row serves all orgs, a PATCH by org A can clobber org B's settings. Check models.py FirewallConfig definition (L1-50 in firewall_config_views.py read code shows org parameter) \u2014 if it's truly per-org (has organization FK), then GET/PATCH are safe when filtered by auth_context.organization_id. If singleton, this is a separate Critical bug requiring schema migration (split into per-org rows). Mitigation: assume per-org and mirror kill_switch_views.py pattern (filter by request's org); if control plane's FirewallConfig query uses org scoping, gateway's endpoints must too."
  },
  {
    "title": "Kill-switch API inaccessible to SDK users; only control-plane JWT",
    "severity": "High",
    "detail": "Emergency model disable/reroute managed at `/api/kill-switches/` (control-plane, JWT-gated). An SDK user detecting runtime instability (e.g., 99% failure rate) must call a separate API (no bearer key auth) to activate a kill-switch. This breaks immediate response capability in a real incident \u2014 the user hitting the gateway for inference should be able to toggle kill-switches without a separate auth roundtrip.",
    "files": "control/ai_mesh_control/core/kill_switch_views.py (L38-150), core/models.py (KillSwitch model)",
    "implementationApproach": "Add `/v1/zeroshield/kill-switches` and `/v1/zeroshield/kill-switches/{id}/activate` PATCH/POST endpoints to gateway (policy_management_routes.py). GET/LIST route reads org's kill-switches from Redis cache (KillSwitch rows are synced to Redis by control plane). POST creates a kill-switch (forward to control plane via internal HTTP client). PATCH/activate/deactivate do PATCH on control plane and subscribe to kill_switch_updates Pub/Sub channel for cache invalidation. Auth gate on org-scoped bearer key with 'kill_switch_write' permission (add permission to auth_context.permissions schema). Reuse kill_switch_audit.py (core/kill_switch_audit.py) to log mutations.",
    "effort": "L",
    "firewallRisk": "Enforcement/isolation risk: kill-switch mutations are org-scoped (core/kill_switch_views.py L63 filters by organization). Ensure gateway endpoints do the same: check auth_context.organization_id against the kill-switch's organization FK before allowing PATCH/activate/deactivate (else 404 or 403). Privilege escalation: currently mutations are admin-gated (L52-56 IsAdminOrSuperuser). Bearer keys may not have admin roles; add a new permission flag 'kill_switch_admin' or require it to be in auth_context.roles (already modeled, middleware.py L99). Mitigation: bearer key must be issued with explicit kill_switch_write + kill_switch_admin scopes, configured at key-creation time (control-plane GatewayAPIKey.permissions dict)."
  },
  {
    "title": "No `/v1/responses` (Responses API) for governance policies with typed output/tools",
    "severity": "High",
    "detail": "OpenAI's Responses API (M-51 design anchor) enables structured policy enforcement on model outputs with typed schema validation (input/output items, tool_calls). ZeroShield currently supports only streaming trace metadata in `/v1/chat/completions` (ZeroShieldResponse.v1.md) \u2014 a flat JSON blob, not a strongly-typed response with policy-enforced tool calls. For agentic workflows, this is a gap: an agent calling a tool (file_search, web_search, function_calling) cannot have the tool parameters validated against policies (e.g., 'prevent function calls to delete_file') without a Responses-like typed surface.",
    "files": "gateway/ai_mesh_gateway/main.py (chat/completions only, no responses endpoint), docs/contracts/ZeroShieldResponse.v1.md (describes trace, not typed response schema)",
    "implementationApproach": "Design (not implement in this phase): sketch `/v1/responses` POST endpoint that takes input_items (typed user/assistant messages with tool_use), previous_response_id (for multi-turn state), runs them through the pipeline trace (pipeline_trace.py), and returns output_items (assistant message + tool_calls) with a typed schema (JSONSchema or OpenAPI). Tool calls are validated by a new policy domain 'tool_call' (alongside 'pipeline' and 'rag'), with rules like 'block function_calling where tool_name matches delete_*' (similar to current regex-based rules). Responses are streamed as Server-Sent Events (like chat/completions) with events like response.created, response.output_text.delta, response.completed, response.tool_call.started, response.tool_call.completed. This unifies inference + governance: a single `/v1/responses` call enforces policies on both input AND output shape. Reuse stream_orchestration.py (SSE builder) and output_guard.py (response validation); add tool_call_policy_engine.py (new policy domain handler). Schema validation uses pydantic (already a gateway dep) or jsonschema.",
    "effort": "XL",
    "firewallRisk": "Enforcement/isolation risk: tool_call validation is new enforcement surface (block/redact actions on tool parameters); same tenant-scoping applies (auth_context.organization_id filters policies). New categories (file_search_location, function_name, etc.) added to threat_type enum (output_guard.py L45) \u2014 ensure redaction logic doesn't leak tool details. Mitigation: apply _redact_for_client_response (main.py L4333, L5477) to tool_call details on block/redact actions (e.g., 'Tool call blocked: policy violation' without echoing the function name). Privilege escalation: a user with 'tool_call_read' permission can observe which tools are available (via output_items); control this with a new scope 'tool_call_visibility'. No new surface for external attacks (tools are defined by the agent, not user input)."
  },
  {
    "title": "No org membership / RBAC management endpoints (no `/v1/zeroshield/members`, `/v1/zeroshield/projects`)",
    "severity": "Medium",
    "detail": "OpenAI SDK users managing multi-user organizations (e.g., team LLM platform) cannot add/remove members or create sub-projects without control-plane JWT access. ZeroShield organization model (control/ai_mesh_control/auth_api/models.py Organization FK) supports this, but no gateway-facing API exposes it. This breaks workflows like 'API key holder wants to onboard a teammate and grant policy read-only access'.",
    "files": "control/ai_mesh_control/auth_api/models.py (Organization, UserProfile, user roles), core/gateway_key_views.py (GatewayAPIKey CRUD, shows example of org-scoped bearer-key endpoints needed)",
    "implementationApproach": "Add `/v1/zeroshield/organization/members` (GET/POST/DELETE), `/v1/zeroshield/organization/projects` (GET/POST/PATCH), `/v1/zeroshield/organization/audit_logs` (GET) endpoints to gateway. All require bearer token + org scoping. GET endpoints read from control-plane via internal HTTP client (forward with GATEWAY_INTERNAL_API_KEY + X-Org-ID). POST/PATCH/DELETE forward to control plane and cache result locally (optional, for /members and /projects; audit_logs are read-only so no cache needed). Auth gate on 'organization_admin' or 'organization_member_write' permissions. Model after OpenAI's `/v1/organization/*` shape (flat namespace, not nested under /v1/organization/{org_id}/ because auth_context already carries the org).",
    "effort": "M",
    "firewallRisk": "Enforcement/isolation risk: auth_context.organization_id must match the organization being queried (e.g., a user in org A cannot POST /members to org B). Implement by checking: if auth_context.organization_id != payload.get('organization_id'), raise 403. Privilege escalation: member creation/role assignment must not allow a non-admin to promote themselves or add new admins. Reuse control-plane's role enum (admin, superadmin, org_admin, member) and enforce via permissions dict (e.g., only keys with 'organization_admin' can POST /members with role='admin'). Mitigation: mirror control-plane permission checks (auth/permissions.py if exists, or add to auth/authentication.py) and call them on gateway endpoints."
  },
  {
    "title": "No usage analytics / audit log query endpoints for governance visibility",
    "severity": "Medium",
    "detail": "Control plane exposes `/api/security/` (analytics, threat feeds, KPIs) via JWT, not bearer key. SDK users (bearer-key-only) cannot query policy enforcement events, usage patterns, or compliance audits. OpenAI SDK offers no usage API, but ZeroShield adds governance audit surface: 'what policies blocked my requests?' + 'who modified firewall config?' are critical for observability.",
    "files": "control/ai_mesh_control/policy/security_urls.py (L39-76, 30+ analytics endpoints), core/models.py (EnforcementEvent, SecurityIncident, audit tables)",
    "implementationApproach": "Add `/v1/zeroshield/usage` (GET with filters) and `/v1/zeroshield/audit_logs` (GET) gateway endpoints. Usage queries enforcement events (policy/models.py EnforcementEvent table, scoped to auth_context.organization_id, date range, policy/rule filters) and returns a simplified schema (timestamp, policy_code, rule_name, action, request_id, threat_type, confidence). Audit logs return config changes (who, when, what field) from a dedicated audit trail table (e.g., FirewallConfigAudit, KillSwitchAudit, PolicyAudit \u2014 may already exist as core/kill_switch_audit.py shows). Both endpoints are read-only, forward to control plane via internal HTTP client, cache optionally (usage is real-time so no caching; audit is append-only so cache is valid). Auth gate on 'usage_read' and 'audit_read' permissions.",
    "effort": "M",
    "firewallRisk": "Enforcement/isolation risk: usage/audit queries must be filtered by org (WHERE organization_id = auth_context.organization_id). Ensure control-plane responses are already org-scoped; if not, gateway must post-filter before returning to client. Mitigation: control-plane endpoint handlers already scope to org (policy/views.py@PolicyAnalyticsView uses get_request_organization), so reuse those endpoints and proxy them (no double-filtering needed). Privacy: audit log entries may contain policy code names (e.g., 'PII_DETECT') which are semi-public but should not expose PII content or rule details; use a safe allowlist (similar to _redact_for_client_response in main.py)."
  },
  {
    "title": "No policy version/conflict management for concurrent SDK updates",
    "severity": "Medium",
    "detail": "Policy mutations on control plane use optimistic locking (version field, core/firewall_config_views.py L50-62). But gateway forwarding doesn't expose conflict detection to SDK callers. If two API key holders PATCH the same policy concurrently, the second gets a 409 Conflict (or 200 + clobbered change). OpenAI SDK users have no way to retry with conflict resolution (no conflict response schema, no version bump hint for re-fetch).",
    "files": "policy/serializers.py (PolicySerializer, PolicyWriteSerializer \u2014 may use version field), core/firewall_config_views.py (L50-62 optimistic lock), policy/views.py (PolicyViewSet mutations)",
    "implementationApproach": "Ensure gateway's PATCH /v1/zeroshield/policies/{id} endpoint forwards the control-plane request as-is (including version field in body) and returns the full response (including updated version and 409 status if conflict). Document in the endpoint's response schema that a 409 Conflict response includes 'version' and 'current_version' fields so the client can re-read the policy and retry. This requires NO code changes on the gateway (forward + transparent proxy already works) \u2014 just API documentation clarifying the semantics.",
    "effort": "S",
    "firewallRisk": "No new risk; control plane already enforces optimistic locking. Gateway is stateless forwarder."
  },
  {
    "title": "No policy syntax validation or dry-run testing endpoints",
    "severity": "Medium",
    "detail": "Policy creation involves writing regex patterns (policy/serializers.py L78-118 validate_condition), and malformed patterns cause silent failures at eval time (or ReDoS attacks). Control plane now validates ReDoS at write time (FINDING-3, L22-75) but SDK users get a 400 response with no explanation. No way to test a policy rule against sample inputs before deploying.",
    "files": "policy/serializers.py (L78-150 validate_condition, validate_redaction_config), policy/urls.py (L16 PolicyTestView \u2014 exists on control plane)",
    "implementationApproach": "Expose the existing control-plane `/api/policies/test/` (policy/urls.py L16, policy/evaluation_views.py PolicyTestView) via gateway as `/v1/zeroshield/policies/test` POST endpoint. Body is { 'rule_id': int, 'input_text': str, 'input_type': 'prompt'|'response' }, response is { 'matched': bool, 'confidence': number, 'reason': str }. This allows SDK users to validate rules before deployment. Forward directly to control plane (test endpoint doesn't mutate, just evaluates).",
    "effort": "S",
    "firewallRisk": "No new risk; read-only endpoint. Ensure the test endpoint is org-scoped on the control plane (policy/evaluation_views.py PolicyTestView.get_permissions / .get_queryset)."
  }
]