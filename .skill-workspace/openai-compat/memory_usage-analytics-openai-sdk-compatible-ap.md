# Usage Analytics — OpenAI SDK-Compatible API Surface

The usage analytics surface exposes token consumption, cost tracking, and quota management via OpenAI-compatible endpoints. This includes:
1. Per-response `usage` object (prompt_tokens, completion_tokens, total_tokens) on every chat/embedding/response
2. POST /v1/usage/tokens — retrieve usage for a specific request window (org/user/key scoped)
3. GET /v1/billing/usage — retrieve cost aggregates (per-model, per-user, time-windowed)
4. Usage tracking in the firewall pipeline (throughput, cost enforcement, alerts)
5. OpenAI Admin API parity for org/project-scoped usage (admin/usage, admin/cost)

## Current
**ZeroShield today (non-streaming + streaming):**

GATEWAY DATA PLANE (/v1/* endpoints):
- gateway/ai_mesh_gateway/main.py:
  * Line 3484: POST /v1/chat/completions — returns stock OpenAI response with `usage` object (prompt_tokens, completion_tokens, total_tokens)
  * Line 6851: LLM_ROUTER.extract_usage(llm_resp) extracts usage dict from upstream LLM response
  * Line 6868-6885: record_usage() & record_org_usage() reconcile per-key + per-org TPM buckets with delta (actual - estimated)
  * Line 7169: POST /v1/embeddings — returns stock OpenAI embedding response with `usage` object
  * Line 10411: GET /v1/models — no usage info
  * Lines 3520-3530: Example response shows "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}

- gateway/ai_mesh_gateway/stream_orchestration.py:
  * Lines 45-97: StreamRunMetrics dataclass tracks usage (dict[str, int] | None), usage_estimated (bool)
  * Lines 231-249: _extract_usage_from_sse_line() parses streaming SSE chunks for usage object
  * Lines 256-309: _track_stream_metrics() accumulates usage; on completion calls record_usage() + record_org_usage() (lines 309-322)
  * Lines 348-372: build_stream_trace_frame() includes usage in final zeroshield trace frame (line 352: "usage": metrics.usage or {})
  * Lines 370-395: Streaming responses include final chunk with usage when stream_options.include_usage=true

- gateway/ai_mesh_gateway/rate_limiter.py:
  * Lines 59-156: check_rate_limit() pre-charges estimated_tokens to TPM bucket (fixed-window, per-key + per-org)
  * Lines 283-323: record_org_usage() post-charges delta (actual - estimated) to org TPM bucket; floor-clamps to 0 (line 312)

GATEWAY TELEMETRY:
- gateway/ai_mesh_gateway/telemetry.py:
  * Lines 141-196: build_telemetry_event() records tokens_used (dict[str, int] | None) as metadata

CONTROL PLANE (/api/* endpoints):
- control/ai_mesh_control/core/dashboard_views.py:
  * Lines 409-473: GET /api/dashboard/model-usage/?days=30 — aggregates EnforcementEvent metadata by model; returns per-model total/allowed/blocked/risk (NO token counts, NO cost data)

- control/ai_mesh_control/policy/security_views.py:
  * Lines 2154-2185: GET /api/security/usage-patterns/?hours=24 — per-hour totalAttempts/blocked (NO token counts, NO costs)

- control/ai_mesh_control/policy/analytics_views.py:
  * Lines 117-262: GET /api/policies/analytics/ — enforcement effectiveness trend, violations by hour/day/week, category performance (NO token usage, NO costs)

MODELS:
- control/ai_mesh_control/policy/models.py:
  * Lines 166-240: EnforcementEvent (org, policy, rule, action, user_id, endpoint_id, metadata:JSONField, created_at, event_class, incident_status)
  * metadata JSONField can contain tokens_used but NOT schema-enforced; no dedicated usage/cost tracking table

DATABASE: NO dedicated UsageRecord or CostRecord table; telemetry is append-only EnforcementEvent + optional MongoDB (control/policy/telemetry_mongo.py, line 30: disabled by default)

GAPS:
- Per-response usage object IS present in chat/completions + embeddings (stock OpenAI format)
- No /v1/usage/* endpoints for OpenAI-compatible retrieval
- No usage aggregation API for the control plane (e.g., POST /api/usage/aggregate or GET /api/usage/org)
- Cost tracking missing (no model->cost mapping, no cost per response, no billing aggregates)
- Usage not schema-enforced in EnforcementEvent.metadata (ad-hoc dict)
- No support for OpenAI Admin API usage/cost endpoints (admin/usage, admin/cost)


## OpenAI Spec
**OpenAI API Usage/Cost Specification (v1 + Admin):**

RESPONSES API (includes usage on every response):
```json
POST /v1/responses HTTP/1.1
{
  "model": "gpt-4o",
  "input": [{"type": "text", "text": "Hello"}],
  "modalities": ["text"],
  ...
}
Response 200:
{
  "id": "res_...",
  "object": "response",
  "created": 1718000000,
  "model": "gpt-4o",
  "output": [...],
  "usage": {
    "input_tokens": 10,
    "output_tokens": 8,
    "total_tokens": 18
  }
}
```

USAGE API (retrieve per-request usage):
```
GET /v1/usage?request_id=chatcmpl-123 (Completions API usage tracking)
GET /v1/billing/usage — retrieves usage aggregates
```

ADMIN API (org/project scoped usage):
```
GET /admin/organization/usage (usage aggregates for org)
  ?start_date=2024-01-01&end_date=2024-01-31&limit=50
  Returns: {
    "object": "list",
    "data": [
      {
        "organization_id": "org-123",
        "model": "gpt-4o",
        "input_tokens": 1000000,
        "output_tokens": 50000,
        "total_tokens": 1050000
      }
    ]
  }

GET /admin/user/usage
GET /admin/project/usage
POST /admin/usage/export — batch export
```

BILLING API (cost tracking):
```
GET /v1/billing/costs
GET /admin/organization/cost
GET /admin/cost/summary
Returns: {
  "organization_id": "org-123",
  "period": "2024-01",
  "models": {
    "gpt-4o": {
      "input_tokens": 1000000,
      "output_tokens": 50000,
      "total_cost_usd": 50.00
    }
  },
  "total_cost_usd": 50.00
}
```

CHAT COMPLETIONS — usage field (v1):
```json
POST /v1/chat/completions
Response:
{
  ...,
  "usage": {
    "prompt_tokens": N,
    "completion_tokens": M,
    "total_tokens": N+M
  }
}
```

Streaming: final chunk includes usage when stream_options={include_usage: true}

USAGE TRACKING across all endpoints:
- input_tokens / prompt_tokens (consumed from user)
- output_tokens / completion_tokens (generated by model)
- total_tokens (sum)
- cost_usd (optional, model-dependent)


## Reusable hooks
**Existing FirewallPipeline entrypoints to reuse (no duplication):**

1. **AuthMiddleware** (gateway/ai_mesh_gateway/middleware.py):
   - Line ~2880: validate_api_key, extract auth_ctx (user_id, org_id, organization_id, key_hash, project_id, prefix)
   - Reuse for /v1/usage/* and /v1/billing/usage endpoints — same auth validation, no new auth layer

2. **Rate Limiter** (gateway/ai_mesh_gateway/rate_limiter.py):
   - Lines 59–156: check_rate_limit(key_hash, rate_limit_tpm, estimated_tokens) — atomic Redis Lua script
   - Lines 283–323: record_org_usage(org_slug, actual_tokens, estimated_tokens) — delta reconciliation, floor-clamping
   - Reuse for cost-based quota (Gap #5): extend to track cost buckets without duplicating Redis logic

3. **LLMRouter.extract_usage()** (gateway/ai_mesh_gateway/llm_router.py:1275):
   - Lines 1275–1284: extracts {prompt_tokens, completion_tokens, total_tokens} from response dict
   - Reuse for all models (completions, responses, embeddings) — model-agnostic

4. **TelemetryProducer + build_telemetry_event()** (gateway/ai_mesh_gateway/telemetry.py):
   - Lines 141–196: event builder; accepts tokens_used param
   - Lines 59–138: non-blocking Redis flush (TelemetryProducer.emit + _flush_to_redis)
   - Reuse for usage telemetry — add cost_usd to tokens_used dict, same producer

5. **EnforcementEvent Query Pattern** (control/ai_mesh_control/policy/analytics_views.py):
   - Lines 134–142, 229–241: EnforcementEvent.objects.filter(created_at__gte=since) with org-scoping
   - Lines 126, 176: get_request_organization(request) for tenant isolation
   - Reuse for usage aggregation endpoints — same ORM patterns, same scoping

6. **Streaming Finalization** (gateway/ai_mesh_gateway/stream_orchestration.py):
   - Lines 300–322: extract usage → record_usage() → record_org_usage()
   - Reuse for both non-streaming (main.py:6871) and streaming — identical reconciliation logic

7. **Response Building** (gateway/ai_mesh_gateway/main.py):
   - Lines 3520–3530, 7020–7050: construct llm_resp dict, append zeroshield metadata, build headers
   - Lines 6851–6885: usage extraction → reconciliation → telemetry
   - Reuse for cost_usd injection (append to usage object after line 6851) — no new construction logic

**Summary: Do NOT duplicate auth, rate-limiting, telemetry, or ORM queries. All usage features thread through existing holes:**
- Auth: AuthMiddleware (line 2880)
- TPM: RateLimiter (line 59, 283)
- Usage extraction: LLMRouter.extract_usage (line 1275)
- Telemetry: TelemetryProducer (line 59 in telemetry.py)
- DB queries: EnforcementEvent.objects.filter (line 134)
- Response building: main.py proxy_chat finalization (line 6851)

## Gaps
[
  {
    "title": "Missing OpenAI Usage Retrieval API (/v1/usage, /v1/billing/usage)",
    "severity": "High",
    "detail": "ZeroShield has no OpenAI-compatible endpoint to retrieve usage for a specific request or time window. The stock OpenAI SDK expects POST /v1/usage or GET /v1/billing/usage to fetch usage metrics by request_id, org, user, or date range. Current telemetry lives only in control plane dashboards (/api/dashboard/model-usage, /api/security/usage-patterns) which are NOT OpenAI-compatible.",
    "files": "gateway/ai_mesh_gateway/main.py (none), control/ai_mesh_control/policy/models.py:166 (EnforcementEvent lacks dedicated usage schema)",
    "implementationApproach": "Add two gateway endpoints reusing the firewall pipeline: (1) POST /v1/usage/tokens {request_id, start_date, end_date, org_slug, user_id, key_hash} \u2192 queries Redis + Postgres EnforcementEvent by request_id or time window; returns list of {request_id, model, prompt_tokens, completion_tokens, total_tokens, timestamp}. (2) GET /v1/billing/usage?start_date=X&end_date=Y&org_slug=Z \u2192 aggregates EnforcementEvent.metadata.tokens_used by model over time window; returns list of {model, total_input_tokens, total_output_tokens, total_cost_usd}. Both endpoints run auth_middleware (api_key validation) + org-scoped filtering (line 5100 pattern from proxy_chat). Backend query enriches from EnforcementEvent.metadata['tokens_used'] OR from optional MongoDB telemetry collection (telemetry_mongo.py:21). Caching: Redis cache for 24h aggregates (per org/date).",
    "effort": "M",
    "firewallRisk": "**Low enforcement risk**: Usage retrieval is read-only, post-request. No new enforcement point. BUT scope must be org-scoped (via auth_ctx.organization_id) to prevent inter-tenant data leakage. **Mitigation**: Reuse AuthMiddleware validation (line 2890 in main.py) + org-scoped filtering identical to dashboard_views (line 418 pattern: _dashboard_org_context). Add org_slug to request validation so unauthenticated clients cannot enumerate usage across orgs."
  },
  {
    "title": "Cost Tracking Missing (model \u2192 cost mapping + per-response cost)",
    "severity": "High",
    "detail": "ZeroShield tracks token counts but not cost per request or per model. OpenAI API returns cost_usd on usage objects; ZeroShield's /v1/chat/completions response includes only token counts (prompt_tokens, completion_tokens, total_tokens), no cost. Cost lookup requires model-specific pricing table (updated with each OpenAI price change). Current dashboard shows request counts, not costs.",
    "files": "gateway/ai_mesh_gateway/main.py:6851 (extract_usage), gateway/ai_mesh_gateway/rate_limiter.py:283 (record_org_usage), control/ai_mesh_control/core/dashboard_views.py:409 (ModelUsageView has NO cost)",
    "implementationApproach": "Create a cost_calculator module (parallel to rate_limiter.py): (1) Define pricing dict per model (gpt-4o: {input: $0.005/1k, output: $0.015/1k}, gpt-4o-mini: {...}, claude-3-opus: {...}, bedrock-claude: {...}). (2) Add method calculate_cost(model, prompt_tokens, completion_tokens) \u2192 returns cost_usd. (3) Inject into main.py::proxy_chat after line 6851 (extract_usage) \u2192 call cost_calculator.calculate_cost() \u2192 append to response usage object {prompt_tokens, completion_tokens, total_tokens, cost_usd}. (4) Store cost_usd in telemetry event (telemetry.py:154 add cost_usd param; line 185 add to tokens_used dict). (5) For control plane: update ModelUsageView (dashboard_views.py:409) to sum cost_usd from EnforcementEvent.metadata['cost_usd']; return per-model {total_cost_usd, cost_per_request_avg}. **Pricing sync**: Either hardcode with version notes (e.g., pricing as of Feb 2025) OR fetch from OpenAI /v1/models endpoint periodically (cache in Redis, fallback to hardcoded).",
    "effort": "M",
    "firewallRisk": "**No new enforcement surface**. Cost calculation is post-response, read-only. Reuse rate_limiter architecture (no new auth/isolation points). **Mitigation**: Cost calculation MUST handle unknown models gracefully (return null cost_usd, log warning). Never reject a request because cost lookup failed \u2014 fail open for cost, fail closed for auth/policy."
  },
  {
    "title": "OpenAI Admin API Parity \u2014 /admin/usage/* endpoints for multi-tenant governance",
    "severity": "High",
    "detail": "OpenAI Admin API provides /admin/organization/usage, /admin/user/usage, /admin/project/usage to fetch usage across orgs/users/projects. ZeroShield control plane has NO equivalent; all endpoints are single-tenant (scoped via auth context). Customers using multi-org deployments cannot retrieve cross-org usage via OpenAI-compatible API.",
    "files": "control/ai_mesh_control/main_app/urls.py (no /admin/* routes for usage), control/ai_mesh_control/core/admin_urls.py (admin routes exist but not usage-specific), control/ai_mesh_control/policy/models.py:166 (EnforcementEvent org_id FK)",
    "implementationApproach": "Add /admin/usage/* endpoints in control plane (admin-gated, superuser only): (1) GET /admin/usage/organizations?limit=50&offset=0 \u2014 lists organizations with total token counts (sum EnforcementEvent by organization over 30d default). (2) GET /admin/usage/organizations/{org_id}?start_date=X&end_date=Y \u2014 per-org usage detail (input_tokens, output_tokens, cost, by model). (3) GET /admin/usage/users/{org_id}?limit=50 \u2014 per-user usage within an org. (4) GET /admin/usage/projects/{org_id}?limit=50 \u2014 per-project usage (requires Project model FK in EnforcementEvent if not present). Backend: Query EnforcementEvent.objects.filter(organization_id=X) grouped by metadata['model'], aggregating tokens_used['prompt_tokens'] + ['completion_tokens']. Reuse auth pattern from security_views.py (is_admin check). Cache 24h aggregates in Redis (key: admin_usage_org_{org_id}_{date}).",
    "effort": "M",
    "firewallRisk": "**Critical isolation risk**: Multi-tenant usage exposure if org filtering is bypassed. **Mitigation**: All /admin/usage/* endpoints MUST (1) check request.user.is_superuser/is_staff (admin_urls.py pattern), (2) validate org_id FK on request matches user's allowed orgs (reuse get_request_organization from auth.utils, line 126 in analytics_views.py), (3) log all /admin/* accesses to audit trail (create AdminAuditLog model if not present), (4) never expose raw request metadata (strip PII/prompts), (5) add rate limiting to /admin/* (quota per superuser, e.g., 100 req/min)."
  },
  {
    "title": "Usage Schema Enforcement in EnforcementEvent (metadata JSONField is untyped)",
    "severity": "Medium",
    "detail": "EnforcementEvent.metadata is a JSONField(default=dict) with no schema validation. Token counts are stored ad-hoc in metadata['tokens_used'] = {prompt_tokens, completion_tokens, total_tokens} (telemetry.py:185), but without validation, missing fields silently propagate. Control plane dashboards assume structure but fail gracefully on missing keys. No pydantic model enforces shape.",
    "files": "control/ai_mesh_control/policy/models.py:195 (metadata = JSONField), gateway/ai_mesh_gateway/telemetry.py:185 (tokens_used dict append)",
    "implementationApproach": "Create UsageMetadata pydantic model in gateway (parallel to ZeroShieldMetadata in pipeline_trace.py): UsageMetadata(prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0, cost_usd: float = 0.0, estimated: bool = False). Validate in telemetry.py::build_telemetry_event() before appending. In control plane, add migration to backfill missing token counts (prompt_tokens=0 if absent) to stabilize queries. Optionally: create dedicated UsageRecord model (org FK, request_id, model, tokens_used, cost_usd, created_at) to avoid over-loading EnforcementEvent.metadata; keep EnforcementEvent.metadata for policy/threat context only.",
    "effort": "S",
    "firewallRisk": "**No direct risk**: Validation is at storage layer, not enforcement. **Mitigation**: Ensure pydantic validation never rejects a request (fail open \u2014 log validation error, use default values). Backfill must be idempotent (check if tokens_used already exists before writing)."
  },
  {
    "title": "Responses API Usage Tracking \u2014 /v1/responses support (structured input/output with usage)",
    "severity": "High",
    "detail": "OpenAI Responses API (POST /v1/responses) returns usage on every response. ZeroShield currently supports only /v1/chat/completions (messages format). If /v1/responses is added (Phase 2 goal), usage tracking must be identical: extract from response, reconcile TPM, include in response object, record to telemetry. Currently no code path exists for responses.",
    "files": "gateway/ai_mesh_gateway/main.py (no /v1/responses handler), gateway/ai_mesh_gateway/llm_router.py:1275 (extract_usage assumes dict response)",
    "implementationApproach": "When /v1/responses endpoint is added (separate gap): (1) Reuse LLMRouter.extract_usage() \u2014 assumes response is dict with 'usage' key, works for both completions + responses. (2) Reuse rate_limiter.check_rate_limit() + record_org_usage() calls (lines 4751, 6871) without modification \u2014 they operate on token counts only. (3) Include usage in response object: {input_tokens, output_tokens, total_tokens, cost_usd} (matches OpenAI contract). (4) Telemetry: reuse build_telemetry_event() (telemetry.py:141), append tokens_used dict. **Key assumption**: responses API returns usage dict shape identical to chat/completions (or compatible). If shape differs (e.g., cache_tokens added), extend extract_usage() to normalize.",
    "effort": "S",
    "firewallRisk": "**Inherits from chat/completions**: No new risk if reusing existing extraction/TPM/telemetry. **Mitigation**: Reuse rather than duplicate \u2014 avoid separate response-specific usage extraction logic."
  },
  {
    "title": "Cost Enforcement / Quota Limits (org cost cap, not just TPM)",
    "severity": "Medium",
    "detail": "Rate limiter enforces TPM (tokens-per-minute) quota. But OpenAI also supports cost-per-minute or cost-per-day limits (e.g., $10/day budget). ZeroShield has no cost-based quota enforcement, only token-based. Orgs with high-cost models (gpt-4o, claude-3-opus) can exceed budget by token count alone.",
    "files": "gateway/ai_mesh_gateway/rate_limiter.py:59 (check_rate_limit uses TPM only), control/ai_mesh_control/core/models.py (no CostQuota model)",
    "implementationApproach": "Add cost-quota enforcement to rate_limiter (parallel to TPM): (1) New method check_cost_limit(key_hash, cost_usd, org_slug, cost_limit_usd_per_day) \u2014 similar to check_rate_limit but uses Redis INCRBY on cost bucket (ratelimit:cost:{key_hash}:{day_bucket}). (2) Pre-charge estimated cost in eligibility_phase (main.py::proxy_chat line 4751 region). (3) Post-charge actual cost in finalization (line 6885 region, after extract_usage). (4) Add org_cost_limit_usd_per_day to org config (control plane). (5) Return 429 if cost exceeded (mirror TPM 429 response, line 4770). **Coupling**: Cost enforcement depends on cost_calculator module (Gap #2); blocked until cost tracking is implemented.",
    "effort": "M",
    "firewallRisk": "**Medium enforcement risk**: Adds new quota check (cost). If cost_calculator is buggy, can block legitimate requests or allow unlimited spending. **Mitigation**: (1) Cost limit is optional (org_cost_limit_usd_per_day=0 means disabled, not -1). (2) Fail open on cost_calculator error (log, skip cost check, allow request). (3) Add alert when org approaches limit (95%, 99%), not just block at 100%."
  },
  {
    "title": "Usage Aggregation Control Plane API \u2014 OpenAI-compatible /api/usage/aggregate endpoint",
    "severity": "Medium",
    "detail": "Control plane has no unified usage endpoint that returns OpenAI-shaped response (list of usage records grouped by model/user/date). Current endpoints (/api/dashboard/model-usage, /api/security/usage-patterns) are bespoke dashboards, not API-compatible. A stock OpenAI SDK client cannot call control plane endpoints directly.",
    "files": "control/ai_mesh_control/core/dashboard_views.py:409 (ModelUsageView returns dashboard shape), control/ai_mesh_control/policy/security_views.py:2154 (UsagePatternsView returns dashboard shape)",
    "implementationApproach": "Create new control plane endpoint POST /api/usage/aggregate {start_date, end_date, group_by: 'model'|'user'|'project', org_slug, limit} \u2192 returns list of {model, user_id, total_input_tokens, total_output_tokens, total_cost_usd}. Reuse EnforcementEvent query from analytics_views.py (line 134 pattern). Schema: define UsageAggregateRequest, UsageAggregateRow pydantic models. Cache results in Redis (key: usage_agg_{org_id}_{group_by}_{date}). This is complementary to /v1/usage/* (gateway) \u2014 it aggregates (sums) vs. retrieves individual requests.",
    "effort": "S",
    "firewallRisk": "**Low risk**: Aggregation loses row-level detail (already hashed/anonymized in metadata). Reuse org-scoping from analytics_views.py (line 126 get_request_organization). Mitigation: add rate limiting (10 req/min per org) to prevent bulk export abuse."
  },
  {
    "title": "Streaming Usage Accuracy \u2014 SSE include_usage contract validation",
    "severity": "Low",
    "detail": "Streaming responses include final usage chunk when stream_options={include_usage: true}. Current code (stream_orchestration.py:370) checks _wants_usage_chunk() and emits usage in trace frame. However, no validation that usage is always present on completion, nor test coverage for missing usage (upstream provider timeout, parse error).",
    "files": "gateway/ai_mesh_gateway/stream_orchestration.py:370 (wants_usage_chunk), lines 300-305 (fallback to estimated_tokens if no usage)",
    "implementationApproach": "Strengthen fallback (line 303-305): if metrics.usage is None after stream completes, set metrics.usage_estimated=true AND emit warning log with request_id. Add test: test_stream_no_usage_falls_back_to_estimated (stream without usage chunk, verify metrics.usage_estimated=true + usage filled with estimated count). Ensure telemetry always has usage (line 352: emit even if estimated). This is defensive; most providers (OpenAI, Anthropic, Bedrock) include usage, but edge cases (timeout, parse error) should not lose usage entirely.",
    "effort": "S",
    "firewallRisk": "**No risk**: Defensive fallback only, does not change enforcement logic. Mitigation: flag estimated usage in logs so SRE can investigate if it becomes frequent (indicator of upstream provider issue)."
  }
]