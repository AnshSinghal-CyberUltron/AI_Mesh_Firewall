# OpenAI Assistants/Agents API (`/v1/assistants`, `/v1/threads`, `/v1/messages`, `/v1/runs`, `/v1/run_steps`) — thread-scoped agentic context with tool execution, stateful message history, and long-running background runs with polling/event streaming.

## Current
**ZeroShield Today:**

**Gateway Data Plane** (`/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/`):
  - **Exposed endpoints** (main.py lines 3483–10410):
    - POST /v1/chat/completions (line 3483) — OpenAI-compatible streaming + non-streaming
    - POST /v1/embeddings (line 7168) — embedding API
    - POST /v1/rag/* (lines 7757–8342) — RAG/vector retrieval through firewall
    - GET /v1/models (line 10410) — model list
    - POST /v1/policy/check (line 9715) — policy evaluation
    - **MCP/Agent surfaces**: org_gateway_router (mcp_proxy.py lines 1242–1914) exposes **only** JSON-RPC MCP protocol (`/{org_slug}/mcp/{server_slug}/*`) for tool/resource discovery + execution, NOT OpenAI Agents API.
  - **Auth**: middleware.py (lines 104–150) validates Bearer token against Redis; AuthContext carries org_slug, project_id, allowed_models, rate limits, roles (G8 per-agent filtering).
  - **Firewall pipeline** (reusable for agents): stream_orchestration.py → scanner.py → output_guard.py → pipeline_trace.py:
    - Input scan (Tier-1 regex + Tier-2 Bedrock guard model).
    - Policy evaluation (control plane /api/policies/).
    - Output redaction/flagging.
    - Zeroshield metadata envelope (docs/contracts/ZeroShieldResponse.v1.md) with `action`, `detection_tier`, `threat_type`, `matched_patterns`, `risk_score`.
  - **Streaming contract**: Every SSE stream ends with a trace frame (stream_orchestration.py line 348) carrying full zeroshield verdict.

**Control Plane** (`/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/`):
  - **Models**: 
    - core/models.py (line 107): `Agent` (agent_type ∈ {browser, desktop, ide, mobile, api, gateway, mcp, agentic}; tracks agent registration).
    - core/models.py (line 187): `OrganizationAgentKey` (org-scoped agent API key provisioning).
    - mcp_connector/models.py (line 10): `MCPServerRegistration` (MCP tool catalog + enable/disable per-tool/per-org + OAuth 2.1 support for outbound auth to upstream MCP servers).
  - **URLs** (main_app/urls.py line 37): `/api/mcp-connector/` (mcp_connector/urls.py lines 7–33):
    - POST /api/mcp-connector/servers/ — register MCP server
    - POST /api/mcp-connector/tools/call/ — call MCP tool (synchronous)
    - GET /api/mcp-connector/tools/ — list available tools
    - No assistant/thread/message/run management endpoints exist.
  - **Policy scoping** (policy/models.py lines 73–90): Per-user (`allowed_user_ids`), per-agent (`allowed_agent_ids`), per-role (`allowed_roles`) filtering; MCP policies can bind to specific servers (line 40–46).

**Agent Execution Today** (No Assistants API, only MCP):
  - org_gateway_router (mcp_proxy.py lines 1242+) accepts JSON-RPC `{"method": "tools/call", "params": {...}}` and routes to the upstream MCP server (streamable-http, SSE, stdio, websocket transport).
  - Tool list/call go through mcp_scan_orchestrator.py (firewall scan + enable/disable filtering).
  - No conversation history, no run state, no background job scheduling.
  - MCP OAuth (mcp_oauth.py, mcp_oauth_proxy.py) handles 3-legged auth to upstream MCP servers; creds stored encrypted in MCPServerRegistration (phase C).


## OpenAI Spec
**OpenAI Assistants/Agents API 2025-06 Spec:**

**Core entities:**
  1. **Assistants** (POST /v1/assistants, GET /v1/assistants/{id}, PATCH /v1/assistants/{id}, DELETE /v1/assistants/{id}):
     - `id`, `name`, `description`, `model` (required), `instructions` (system prompt), `tools` (array: {type ∈ [code_interpreter, retrieval, function], ...}), `file_ids`, `metadata`, `temperature`, `top_p`, `response_format`.
  2. **Threads** (POST /v1/threads, GET /v1/threads/{id}, PATCH /v1/threads/{id}, DELETE /v1/threads/{id}):
     - `id`, `created_at`, `metadata`.
     - Thread messages are append-only (create via POST /v1/threads/{id}/messages).
  3. **Messages** (POST /v1/threads/{id}/messages, GET /v1/threads/{id}/messages, GET /v1/threads/{id}/messages/{id}):
     - `id`, `role` ∈ [user, assistant], `content` (array: {type ∈ [text, image_file, image_url], ...}), `file_ids`, `created_at`, `metadata`.
  4. **Runs** (POST /v1/threads/{id}/runs, GET /v1/threads/{id}/runs/{id}, PATCH /v1/threads/{id}/runs/{id}, POST /v1/threads/{id}/runs/{id}/cancel):
     - `id`, `assistant_id`, `thread_id`, `status` ∈ [queued, in_progress, requires_action, completed, failed, cancelled, expired], `required_action`, `last_error`, `created_at`, `expires_at`, `started_at`, `completed_at`, `cancelled_at`, `failed_at`.
     - `required_action` (when status=requires_action): {type: "submit_tool_outputs", submit_tool_outputs: {tool_calls: [{id, type, function: {name, arguments}}]}}.
  5. **RunSteps** (GET /v1/threads/{id}/runs/{id}/steps, GET /v1/threads/{id}/runs/{id}/steps/{id}):
     - `id`, `run_id`, `type` ∈ [message_creation, tool_calls], `status` ∈ [in_progress, completed, failed, cancelled], `step_details` (message or tool_call details), `created_at`, `completed_at`, `last_error`.

**Streaming (beta):**
  - POST /v1/threads/{id}/runs?stream=true returns Server-Sent Events with typed events: `thread.created`, `message.created`, `message.delta`, `run.created`, `run.in_progress`, `run.requires_action`, `run.completed`, `run.failed`.

**Tool submission:**
  - POST /v1/threads/{id}/runs/{id}/submit_tool_outputs: `{"tool_outputs": [{"tool_call_id": "...", "output": "..."}]}` — unblock a requires_action run, continue execution.

**File handling (Retrieval):**
  - POST /v1/files, GET /v1/files/{id}, DELETE /v1/files/{id}.
  - files.create(..., purpose="assistants") stores files for thread use.
  - Assistant `file_ids` or message `file_ids` references files for code_interpreter/retrieval tools.


## Reusable hooks
**Existing firewall pipeline** (reuse for agent execution):

1. **Input scan gate** (scanner.py, pipeline_trace.py):
   - `_security_scan(prompt)` (main.py line 869) → Tier-1 (regex patterns) + Tier-2 (Bedrock Guard Model).
   - Returns `ScanResult` {action: [allow, flag, redact, block], threat_type, confidence, matched_patterns, detail, risk_score}.
   - **Reuse**: Call on assistant instructions + thread history + user messages before LLM forward.

2. **Policy evaluation** (eligibility_phase in main.py ~line 2300):
   - Query policy_sync (control plane /api/policy/check) for org_slug + user_id/agent_id + model + prompt (optional).
   - Returns `PolicyEvaluation` {action, matched_policy_names, decision_source, routing_reason}.
   - **Reuse**: Call on run creation (before enqueuing); on tool-call arguments (treat as pseudo-prompt).

3. **Output scan + redaction** (output_guard.py, secure_streaming.py):
   - `scan_output(response_text, threat_types=[pii, secret, ...])` → Tier-2 Guard Model.
   - Returns `OutputGuardVerdict` {action, threat_type, redaction_map, detail}.
   - **Reuse**: Call on run completions (final LLM response); on tool outputs (before storing in RunStep).

4. **Trace envelope** (pipeline_trace.py, stream_orchestration.py):
   - `_build_zeroshield_metadata(...)` (main.py line 968) → internal full metadata object.
   - `_redact_for_client_response(zeroshield_dict)` (main.py line 581) → client-safe allowlist.
   - `build_stream_trace_frame(...)` (stream_orchestration.py line 348) → SSE chunk + zeroshield object.
   - **Reuse**: Call on run step completion; attach verdicts to RunStep.zeroshield_metadata (new DB field).

5. **Auth context threading** (middleware.py AuthContext, stream_orchestration.py StreamLaunchContext):
   - `AuthContext` carries org_slug, user_id, project_id, allowed_models, roles, rate_limit_tpm.
   - `StreamLaunchContext` carries all of the above + route_selection, scan_verdict, scan_mode.
   - **Reuse**: Pass identical context to run executor task (workers/tasks/agent_runs.py); use to filter policy, models, scans.

6. **MCP proxy + tool execution** (mcp_proxy.py, mcp_scan_orchestrator.py):
   - `scan_mcp_payload(...)` (mcp_scan_orchestrator.py) → Tier-1/Tier-2 scan on tool arguments/response.
   - `_get_enabled_tools(org_slug, server_slug)` (mcp_proxy.py line 1321) → Query control plane /api/mcp-connector/internal/enabled-tools/.
   - **Reuse**: Call in run executor when invoking tool (inside agent_runs.py task).

7. **Rate limiting + circuit breaker** (rate_limiter.py, circuit_breaker.py):
   - `rate_limiter.check(key=api_key_hash, tokens=estimated_tokens, rate_limit_tpm)` → Check quota.
   - `circuit_breaker.is_open()` → Check upstream health.
   - **Reuse**: Call before run LLM forward; apply cumulative tokens across all steps in run.

**Insertion points in main.py** (same as chat/completions, apply to agent runs):
- **After auth** (line 3632): Extract org_slug, user_id, project_id from request.state.auth_context.
- **Before policy check** (line ~2500): Run eligibility_phase (model allowlist, org rate limits).
- **Before inference** (line ~2700): Run input scan (Tier-1 + Tier-2).
- **After inference** (line ~3100): Run output scan (Tier-2 Guard Model) + redaction.
- **Finalize** (line ~3300): Emit telemetry, circuit breaker, TPM reconciliation.

All five insertion points already exist for chat/completions; run executor task will call the same functions via shared imports (scanner.py, pipeline_trace.py, output_guard.py, stream_orchestration.py, rate_limiter.py, circuit_breaker.py).

## Gaps
[
  {
    "title": "Missing /v1/assistants CRUD surface",
    "severity": "High",
    "detail": "No GET /v1/assistants, POST /v1/assistants, GET /v1/assistants/{id}, PATCH /v1/assistants/{id}, DELETE /v1/assistants/{id} endpoints. Assistants are agentic workload definitions (model + system prompt + tools + files) stored on the control plane and referenced from runs; they enable reusable agent templates. Currently, ZeroShield has only MCP server registrations (mcp_connector/models.py MCPServerRegistration), not OpenAI Assistants.",
    "files": "/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/main.py (lines 3483\u201310410, no assistants endpoint); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/main_app/urls.py (no /api/assistants/ route); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/core/models.py (Agent model is generic agent telemetry, not an Assistants workload definition).",
    "implementationApproach": "**Control Plane** (Django): Add `Assistant` model (core/models.py or new app). Fields: `id` (UUID), `name`, `description`, `model`, `instructions`, `tools` (JSON array: {type, function: {name, description, parameters (JSON schema)}, ...}), `file_ids` (FK to FileUpload), `metadata`, `temperature`, `top_p`, `response_format`, `organization` (FK), `created_at`, `updated_at`. Add ViewSet + serializers + DRF endpoints (/api/assistants/ CRUD). Scope to org via `organization` FK; policy filtering via `allowed_user_ids` / `allowed_agent_ids` / `allowed_roles` (reuse Policy model). **Gateway** (FastAPI): Add POST /v1/assistants, GET /v1/assistants, GET /v1/assistants/{id}, PATCH /v1/assistants/{id}, DELETE /v1/assistants/{id} (proxy to control plane /api/assistants/ via org_slug + auth context). Response wrapping: Apply zeroshield envelope (pipeline_trace.py _build_zeroshield_metadata) for CRUD operations (log assistant creation/modification as security events for audit).",
    "effort": "M",
    "firewallRisk": "**Enforcement surface**: CRUD operations on assistants do NOT execute LLM inference (no prompt/response scan). Policy/audit scope: Log assistant creation/updates in AuditLog; enforce `allowed_user_ids` / `allowed_agent_ids` scoping via PolicyEvaluation (treat assistant creation as a security event, similar to model allowlist checks). **Mitigation**: Store assistants in org-scoped DB; auth middleware validates org_slug from request.state.auth_context; assistant detail endpoints return 404 if org_slug does not match the authenticated request's org."
  },
  {
    "title": "Missing /v1/threads CRUD surface",
    "severity": "High",
    "detail": "No GET /v1/threads, POST /v1/threads, GET /v1/threads/{id}, PATCH /v1/threads/{id}, DELETE /v1/threads/{id} endpoints. Threads are stateful conversation containers (append-only message list, metadata). Currently, ZeroShield routes MCP tool calls synchronously with no conversation history.",
    "files": "/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/main.py (no thread endpoints); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/main_app/urls.py (no /api/threads/ route); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/core/models.py (no Thread model).",
    "implementationApproach": "**Control Plane** (Django): Add `Thread` model (new app or core/models.py): `id` (UUID), `organization` (FK), `metadata` (JSON), `created_at`, `updated_at`. Thread messages are added via POST /v1/threads/{id}/messages (see messages surface). Add ViewSet (/api/threads/ CRUD). **Gateway** (FastAPI): POST /v1/threads (create, call control plane POST /api/threads/, return Thread JSON), GET /v1/threads (list org-scoped threads, paginated), GET /v1/threads/{id}, PATCH /v1/threads/{id} (metadata only), DELETE /v1/threads/{id}. Middleware enforces org_slug isolation. No policy evaluation needed for thread creation (threads are stateless containers); log creation as AuditLog for observability.",
    "effort": "M",
    "firewallRisk": "**Isolation**: Thread creation is org-scoped; no inference/scanning at creation time. **Risk**: If thread metadata is user-controllable (e.g., metadata.context_prompt), could be used to inject instructions later. **Mitigation**: Thread metadata is read-only after creation (PATCH only allows metadata field update, which is benign JSON storage). Thread detail endpoints enforce org_slug + user ID scoping (if threads should be per-user, add user_id FK and filter in list/get)."
  },
  {
    "title": "Missing /v1/threads/{id}/messages CRUD + streaming",
    "severity": "High",
    "detail": "No POST /v1/threads/{id}/messages (append user message), GET /v1/threads/{id}/messages (list messages in thread), GET /v1/threads/{id}/messages/{id}. Messages are append-only conversation elements (user/assistant/tool roles, text/image/file content). Currently, chat/completions endpoint is stateless (each call is independent); no persistent message history.",
    "files": "/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/main.py (no messages endpoints); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/core/models.py (no Message model).",
    "implementationApproach": "**Control Plane** (Django): Add `Message` model: `id` (UUID), `thread` (FK to Thread), `role` \u2208 [user, assistant, tool], `content` (JSON array of {type \u2208 [text, image_file, image_url, image_source], value/url/source_type}), `assistant_id`, `run_id`, `file_ids` (JSON or M2M to FileUpload), `created_at`. Index on (thread, created_at) for efficient list. Add ViewSet (/api/threads/{id}/messages/). **Gateway** (FastAPI): POST /v1/threads/{id}/messages (add user message, validate content schema, call control POST /api/threads/{id}/messages/), GET /v1/threads/{id}/messages (paginated, ordered by created_at), GET /v1/threads/{id}/messages/{id}. **Firewall integration**: User-submitted messages go through full input scan (stream_orchestration.py pipeline \u2014 Tier-1 regex + Tier-2 guard model + policy evaluation). If message is blocked, return 403 with zeroshield envelope (block reason, threat type, matched patterns). If redaction applies, store redacted version in Message.content.value.",
    "effort": "L",
    "firewallRisk": "**Critical scan point**: User message creation is the input-scan gate (same as chat/completions prompt scan). **Enforcement**: Call _security_scan (main.py line 869) on message.content[].value for all user-role messages; apply policy check (eligibility_phase from chat/completions). **Blocking**: If message is blocked, return 403 immediately (do not create message). **Redaction**: If redaction applies, create message with redacted text. **Output**: Assistant messages (created by runs) skip input scan (they are LLM-generated, scanned at completion time). **Mitigation**: Reuse pipeline_trace.py + scanner.py + output_guard.py logic; wrap message creation in the same enforcement decorator used by chat/completions proxy_chat function."
  },
  {
    "title": "Missing /v1/threads/{id}/runs \u2014 background job execution + polling",
    "severity": "Critical",
    "detail": "No POST /v1/threads/{id}/runs (create async run on assistant + thread), GET /v1/threads/{id}/runs (list runs), GET /v1/threads/{id}/runs/{id} (poll run status), PATCH /v1/threads/{id}/runs/{id} (metadata), POST /v1/threads/{id}/runs/{id}/cancel (cancel run), POST /v1/threads/{id}/runs/{id}/submit_tool_outputs (unblock tool-calling run). Runs are long-lived workloads (status transitions: queued \u2192 in_progress \u2192 requires_action/completed/failed). OpenAI Runs automatically call tools and fetch outputs; ZeroShield must orchestrate this via a background job queue while enforcing policies on each tool call.",
    "files": "/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/main.py (no run endpoints); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/core/models.py (no Run model); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/stream_orchestration.py (streaming pipeline for chat/completions, NOT for multi-step runs).",
    "implementationApproach": "**Control Plane** (Django): Add `Run` model: `id` (UUID), `assistant` (FK to Assistant), `thread` (FK to Thread), `status` \u2208 [queued, in_progress, requires_action, completed, failed, cancelled, expired], `required_action` (JSON: {type, submit_tool_outputs: {tool_calls: [{id, type, function}]}}), `last_error` (JSON), `created_at`, `started_at`, `completed_at`, `cancelled_at`, `expired_at`. Add `RunStep` model: `id` (UUID), `run` (FK to Run), `type` \u2208 [message_creation, tool_calls], `status`, `step_details` (JSON), `created_at`, `completed_at`, `last_error`. **Background job queue** (workers/ai_mesh_workers/tasks/mcp.py or new tasks/agent_runs.py): Celery task to execute run: (1) poll run.status; (2) if queued, transition to in_progress; (3) forward thread messages to assistant.model via /v1/chat/completions (with tools=[assistant.tools]); (4) if response contains tool_calls, transition to requires_action, store in run.required_action, yield RunSteps with type=tool_calls; (5) await POST /v1/threads/{id}/runs/{id}/submit_tool_outputs with tool outputs; (6) iterate until status=completed/failed/cancelled. **Gateway** (FastAPI): POST /v1/threads/{id}/runs (enqueue job, return Run{status: queued, ...}), GET /v1/threads/{id}/runs/{id} (poll status from DB, lazy-update from job if running), POST /v1/threads/{id}/runs/{id}/submit_tool_outputs (update tool outputs, resume job). **Streaming (beta)**: POST /v1/threads/{id}/runs?stream=true returns SSE with `thread.run.in_progress`, `thread.run.requires_action`, etc. events. **Firewall integration**: Each tool call goes through MCP proxy (mcp_scan_orchestrator.py scan + enable/disable filtering); each LLM forward (step 3) goes through chat/completions pipeline (input scan on assistant instructions + thread history + new prompt; output scan on completion).",
    "effort": "XL",
    "firewallRisk": "**Multi-stage enforcement**: (a) Tool discovery (assistant.tools) \u2014 validate against org's allowed tools + MCP server enable/disable. (b) LLM forwarding \u2014 full input/output scan pipeline (scanner.py + output_guard.py + pipeline_trace.py). (c) Tool execution \u2014 MCP proxy scan + enable/disable (mcp_scan_orchestrator.py). (d) Tool output ingestion \u2014 optional output scan (defender's choice via policy). **Attack surfaces**: (1) **Prompt injection via thread history**: Attacker adds messages to thread, then run re-injects them into LLM prompt \u2192 can jailbreak assistant instructions. **Mitigation**: Store run.redacted_prompt (redacted version of full prompt sent to LLM for audit); enforce output scan on all run completions (same as chat/completions). (2) **Tool looping**: Malicious/confused assistant calls same tool repeatedly, exhausting quota. **Mitigation**: Run max_iterations limit (default 10, configurable per assistant); circuit breaker on tool-call count. (3) **PII leakage in tool outputs**: Tool returns PII; assistant uses it in response. **Mitigation**: Scan run.last_message.content for PII after tool output ingestion; apply redaction/flagging. **Reuse**: Call stream_orchestration.stream_with_finalize logic (line 420) or extract its scanning pipeline into a shared async_scan_and_enforce(prompt, response, ...) helper used by both chat/completions AND runs."
  },
  {
    "title": "Missing /v1/threads/{id}/runs/{id}/steps \u2014 runstep enumeration + observability",
    "severity": "High",
    "detail": "No GET /v1/threads/{id}/runs/{id}/steps (list steps in a run), GET /v1/threads/{id}/runs/{id}/steps/{id} (fetch step detail). RunSteps are the DAG of execution: each step is either a message creation or a tool_calls activity. OpenAI surfaces them for observability (client can see which tools were called, what responses they gave). ZeroShield must log these as audit trail + pipeline trace.",
    "files": "/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/core/models.py (no RunStep model); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/mcp_connector/models.py (has MCPEventLog for MCP tool discovery, not for run steps).",
    "implementationApproach": "**Control Plane** (Django): RunStep model (see runs gap). **Gateway** (FastAPI): GET /v1/threads/{id}/runs/{id}/steps (paginated), GET /v1/threads/{id}/runs/{id}/steps/{id}. Both proxy to /api/threads/{id}/runs/{id}/steps/ on control plane. **Audit trail**: Each RunStep creation also logs an AuditLog entry (action='run_step_created', resource=run_id, details={step_details}). **Pipeline trace**: If step is tool_calls, attach pipeline_trace (scanner outcome, enable/disable status, PII redaction, etc.). If step is message_creation, attach zeroshield verdict (full envelope from stream_orchestration.build_stream_trace_frame).",
    "effort": "M",
    "firewallRisk": "**Observability**: RunSteps expose tool-call history + outputs. If outputs contain PII, they are visible to clients querying GET /v1/threads/{id}/runs/{id}/steps/{id}. **Mitigation**: (a) Redact PII in RunStep.step_details.tool_calls[].output before returning to client (same logic as _redact_for_client_response, main.py line 581). (b) Honor X-User-ID context: only return step details if requester matches run creator or has org admin role (RBAC via allowed_roles in policy/core/models.py)."
  },
  {
    "title": "Missing /v1/files \u2014 file uploads for Retrieval tool + code_interpreter",
    "severity": "Medium",
    "detail": "No POST /v1/files (upload file for assistants), GET /v1/files (list), GET /v1/files/{id}, DELETE /v1/files/{id}. Files are used by retrieval (embedding + RAG) and code_interpreter (document context) tools; assistants and messages reference file_ids.",
    "files": "/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/main.py (no file endpoints); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/core/models.py (no FileUpload model).",
    "implementationApproach": "**Control Plane** (Django): Add `FileUpload` model: `id` (UUID), `organization` (FK), `filename`, `file_size`, `mime_type`, `storage_key` (encrypted path to S3/disk), `purpose` \u2208 [assistants, batch, fine-tune], `created_at`, `expires_at`. **Gateway** (FastAPI): POST /v1/files (multipart, store in S3 with org prefix, create FileUpload record, return {id, filename, created_at}), GET /v1/files, GET /v1/files/{id}, DELETE /v1/files/{id}. File storage MUST be org-scoped (S3 key = s3://{org_slug}/{file_id}/{filename}); auth middleware enforces org_slug from request. **Scan**: File upload is untrusted input; scan file for malware/suspicious content (bedrock_scanner.py or add file-type validation). **Retrieval integration**: When assistant.file_ids is set, RAG pipeline (rag_pipeline/pipeline.py) loads files and uses them as context sources.",
    "effort": "M",
    "firewallRisk": "**Malware**: Uploaded files must be scanned for malicious content before storage. **Mitigation**: Run bedrock_scanner.py scan on file content (or offload to ClamAV for binary files). **PII**: File may contain PII; if used by code_interpreter/retrieval, PII can leak. **Mitigation**: Scan file for PII patterns (Presidio/regex Tier-1); redact or block if needed. **Quota**: Unlimited file upload can exhaust storage. **Mitigation**: Per-org file storage quota (e.g., 1GB default); enforce in POST /v1/files with 429 Too Many Requests if exceeded."
  },
  {
    "title": "Missing /v1/runs (batch runs) \u2014 legacy runs.create without thread",
    "severity": "Low",
    "detail": "OpenAI supports /v1/runs (deprecated batch endpoint for runs without explicit thread context); ThreadedRuns (/v1/threads/{id}/runs) are the modern replacement. ZeroShield should NOT implement legacy /v1/runs; recommend clients use threads + runs instead.",
    "files": "N/A (intentionally absent; recommendation only).",
    "implementationApproach": "Skip this endpoint. Document in API reference: 'Batch /v1/runs is deprecated; use /v1/threads/{id}/runs instead.' If a client attempts POST /v1/runs, return 405 Method Not Allowed with message 'Use POST /v1/threads/{id}/runs to create threaded runs.'",
    "effort": "S",
    "firewallRisk": "N/A (not implementing)."
  },
  {
    "title": "Tool execution model mismatch: OpenAI Agents vs. ZeroShield MCP",
    "severity": "High",
    "detail": "OpenAI Assistants call tools defined in `assistant.tools` (function definitions with JSON schema parameters). ZeroShield's current tool execution is MCP-only (org_gateway_router routes to /gateway/{org_slug}/mcp/{server_slug}/* JSON-RPC). Assistant.tools must map to MCP server tools OR be Lambda/HTTP webhooks. **Gap**: No mapping layer from Assistant.tools to MCP tools; no function-call response formatting (OpenAI expects {tool_call_id, output (string/JSON)} submission, MCP expects {result, error}).",
    "files": "/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/mcp_proxy.py (lines 1242+, JSON-RPC tool/list + tool/call); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/control/ai_mesh_control/mcp_connector/models.py (MCPServerRegistration, no bridge to Assistant.tools); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/docs/contracts/ZeroShieldResponse.v1.md (only covers chat/completions, not Agents).",
    "implementationApproach": "**Design choice** (recommend Responses API instead): OpenAI Assistants are stateful, require long-running job orchestration, and tool execution is opaque to the client. **Modern alternative**: Responses API (POST /v1/responses, gap #13 below) gives the client direct typed tool-input/output + function calling + explicit state (previous_response_id) without background jobs. If persisting on Agents: **Tool bridge** \u2014 Assistant.tools (OpenAI JSON schema) \u2192 MCP server tools (JSON-RPC). (1) At assistant creation, validate each tool: if type='function', extract {name, description, parameters}; resolve matching MCP server tool by name (query control plane /api/mcp-connector/tools/ for org + server + tool_name). (2) At run execution, when LLM returns tool_call {id, type='function', function: {name, arguments}}, forward to MCP server via mcp_proxy.py (call JSON-RPC tools/call). (3) Format response: {tool_call_id, output: JSON.stringify(mcp_result)} and POST to submit_tool_outputs. **Scan integration**: Tool call arguments go through input scan (prompt_injection detection on arguments); tool outputs go through output scan (PII, secrets redaction). **Alternative** (recommended): Implement Responses API instead; clients prefer the simpler typed request/response model over background jobs.",
    "effort": "XL",
    "firewallRisk": "**Tool argument injection**: LLM-generated arguments could contain injection payloads. **Mitigation**: Scan tool call arguments with Tier-1 regex + Tier-2 guard (scanner.py). **Tool output PII**: Tool returns sensitive data. **Mitigation**: Scan tool output with output_guard.py; redact PII before storing in RunStep. **Execution control**: MCP tools can have side effects; run should enforce rate limits + timeout. **Mitigation**: Circuit breaker + timeout on mcp_proxy.py calls (already in stream_orchestration.py, extend to run task executor)."
  },
  {
    "title": "Missing model context for streaming + run orchestration",
    "severity": "Medium",
    "detail": "Chat/completions streaming (stream_orchestration.py) and future run orchestration both need to maintain context: redacted_prompt, route_selection, policy verdict, scan_mode, org_slug, user_id, etc. **Current**: StreamLaunchContext (stream_orchestration.py lines 100\u2013120) captures this. **Gap**: No equivalent context object for run execution; run job will need identical context (plus run_id, assistant_id, thread_id). Must design context passing to avoid duplication.",
    "files": "/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/stream_orchestration.py (lines 100\u2013120, StreamLaunchContext); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/workers/ai_mesh_workers/tasks/mcp.py (existing background job, no agent run task yet).",
    "implementationApproach": "Create `RunExecutionContext` in gateway (or shared library ai_mesh_shared/). Inherit from StreamLaunchContext; add {run_id, assistant_id, thread_id, max_iterations, tool_timeout_s}. Use same context for (a) stream_orchestration.stream_with_finalize (existing), (b) future workers/tasks/agent_runs.py task (new). Ensures both paths apply identical policies, scans, audit logging.",
    "effort": "S",
    "firewallRisk": "If context is not properly threaded, run orchestration may bypass policy evaluation or scan. **Mitigation**: Enforce context passing in task executor; assert org_slug/policy_verdict are present before LLM call."
  },
  {
    "title": "Missing org_gateway_router scope for Assistants API (OAuth + multi-tenant isolation)",
    "severity": "High",
    "detail": "Current org_gateway_router (mcp_proxy.py line 36) provides external `/gateway/{org_slug}/mcp/{server_slug}/*` endpoints for MCP access. If Assistants API is exposed on the gateway (POST /v1/assistants, etc.), it MUST be org-scoped but the current routing assumes org_slug is in the URL path. **Gap**: /v1/assistants, /v1/threads endpoints do NOT have org_slug in the path; org context comes from auth header + Redis lookup. Assistants created by org A must not be visible to org B. **Current mitigations**: middleware.py (lines 104\u2013150) extracts org_slug from auth_context (AuthContext.org_slug set from Redis key). But if org_slug lookup fails, /v1/assistants requests must still fail safely.",
    "files": "/Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/middleware.py (lines 104\u2013150, validate_api_key sets AuthContext.org_slug); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/mcp_proxy.py (org_gateway_router line 36, enforces org_slug path parameter); /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway/ai_mesh_gateway/main.py (lines 3483\u20133594, proxy_chat extracts org_slug from request.state.auth).",
    "implementationApproach": "**Middleware**: proxy_chat (main.py line 3598) already extracts auth_context.org_slug; ensure same pattern for Assistants endpoints. (1) All /v1/assistants* endpoints MUST call `_validate_org_scope(request, org_slug)` (used by org_gateway_router, mcp_proxy.py line 1253) or equivalent. (2) When forwarding to control plane (/api/assistants/), add X-Org-Slug header (mcp_proxy.py line 62 pattern). (3) Control plane ViewSet filters QuerySet by request.org (tenant isolation). **OAuth for external clients**: If org_gateway_router is extended to support /gateway/{org_slug}/assistants/*, add OAuth 2.1 token validation (mcp_oauth.py, mcp_oauth_proxy.py) as fallback when Bearer token is absent. **Isolation enforcement**: Return 403 Forbidden if auth_context.org_slug != requested_assistant.organization.slug.",
    "effort": "M",
    "firewallRisk": "**Cross-tenant access**: If org isolation is not enforced, org A assistant can be accessed/deleted by org B auth key. **Mitigation**: Middleware validates org_slug; ViewSet filters by org; endpoint handler asserts org_slug match."
  }
]