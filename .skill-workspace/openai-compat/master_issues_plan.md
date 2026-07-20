Paths confirmed. The synthesis below is my return value.

# OpenAI-Compatibility Master Plan

## 1. Executive Summary — Current vs Target, Top Gaps

**Current state.** ZeroShield ships 3 of ~12 OpenAI product surfaces through the FastAPI gateway (`gateway/ai_mesh_gateway/main.py`, 10,657 LOC): `POST /v1/chat/completions` (full firewall pipeline), `POST /v1/embeddings` (lightweight), `GET /v1/models`. Plus a portable `/v1/vector/*` and `/v1/rag/*` retrieval family. The chat path is the only one with mature enforcement: a 12-stage pipeline (auth → rate-limit → kill-switch → model-state → policy → Tier-1 scan → Tier-2 Bedrock guard → routing adjudicator → LLM → output-guard → redaction → telemetry), terminal SSE trace frame per M-51, and a client-safe `zeroshield` envelope. All 9 stored memory campaigns confirm this path is fundamentally **fail-closed**.

**Target.** Stock-OpenAI-SDK compatibility for the FULL Responses API plus the remaining surfaces, with **zero firewall forking** — every new surface inherits the existing enforcement primitives via format adapters, never reimplements them.

**Top gaps, ranked by leverage:**

| # | Gap | Why it's top | Effort |
|---|-----|-------------|--------|
| G1 | No `POST /v1/responses` (core data plane) | Blocks the entire Responses program; SDK `client.responses.create()` 404s | M (core) → XL (full) |
| G2 | No pipeline-reuse seam — `proxy_chat` is monolithic | Without it, every surface forks enforcement → fail-open drift (the exact class of bug the memory log shows recurring) | M |
| G3 | Error envelope is 3 incompatible shapes (flat string, flat+code, nested) | Stock SDK mis-parses → wrong exception class on **block/auth** paths; silently degrades enforcement signal to clients | M |
| G4 | No stored response state (`store`, `previous_response_id`, GET/DELETE) | Multi-turn + tool loops impossible; introduces **cross-tenant state leakage** risk if done naively | L |
| G5 | Chat param drops (`logprobs`, `max_completion_tokens`, `reasoning*`, `parallel_tool_calls`, `user`) | Cheap, pure passthrough, high SDK-compat value | S each |
| G6 | Responses/tool/run streaming event types absent (`response.created`/`.delta`/`.completed`) | Streaming clients can't parse; mid-stream guard must port to new event shape | L |
| G7 | Tool-call input/output **not scanned** (args + tool names bypass scanner/guard) | CRITICAL latent fail-open — jailbreak/PII in tool arguments today bypasses Tier-1/2 entirely even on chat path | M |
| G8 | Audio/`modalities` + `json_schema` structured output bypass text scanners | New exfil channels; must reject or transcribe-then-scan | L |
| G9 | Files / Vector Stores / Assistants / control-plane mgmt surfaces | Large but deferrable; agents depend on Responses+state first | L–XL |

The single highest-risk item is **G7 + G2 together**: tool-call content is already a partial fail-open on the *existing* chat path, and the Responses program multiplies tool surface. The seam (G2) is the prerequisite that makes G7 a one-place fix rather than N.

---

## 2. Dependency-Ordered Implementation Phases

Foundation first. Each gap lists: **files · approach (firewall reuse) · effort · risk + mitigation.**

### Phase 0 — Foundation: pipeline-reuse seam + error/ID hygiene (do before any new surface)

**0a. Extract enforcement seam from `proxy_chat`** *(the keystone — see §3)*
- Files: `gateway/ai_mesh_gateway/main.py` (refactor `proxy_chat` ~L3484–7139).
- Approach: factor the request-gate block (auth, rate-limit, kill-switch, model-state, threat-intel, blocked-keywords, policy-check, input-scan Tier-1/2) into `async def _enforce_request_gates(auth_ctx, scannable_text, body, org_config) -> GateResult(allowed, block_response|None, scan_verdict, policy_verdict, routing_ctx)`. Factor the response side into `_enforce_output(items_or_text, verdicts, ...) -> guard_verdict` and `_finalize(usage, verdicts, ...)` (telemetry + TPM reconciliation + circuit breaker). `proxy_chat` becomes a thin caller; `proxy_responses` calls the identical functions with format adapters.
- Effort: **M**.
- Risk: refactor regression on the proven chat path. Mitigation: behavior-preserving extraction guarded by the existing `test_openai_sdk_compat.py` + `test_stream_trace_frame.py` suites; no logic change in the same commit as extraction.

**0b. Unified OpenAI error envelope**
- Files: `main.py:153–174, 506–578 (_build_safe_block_response), 1451–1475, 3654–3946`; `middleware.py:104–449`.
- Approach: add `_build_openai_error_response(status, message, error_type, param=None, code=None)` returning **nested** `{error:{message,type,param?,code?}}` always; keep ZeroShield fields (`request_id`, `category`, `blocked_by`, `detection_tier`, `pipeline_trace`) at top level. Map ZS codes → OpenAI `type` (block→`permission_error`, rate→`rate_limit_error`, auth→`authentication_error`/`invalid_request_error`). Refactor `_build_safe_block_response` and middleware auth errors to use it.
- Effort: **M**.
- Risk: structural only; HTTP status (the real enforcement signal) unchanged. Mitigation: dual-field transition (`code` + `zeroshield_code`) for one minor version; SDK error-class assertion tests.

**0c. ID prefixes + `x-request-id` header**
- Files: `main.py:530` (request_id gen), response header injection ~`5298–5440`.
- Approach: `_generate_openai_id(object_type)` with registry (`resp_`, `msg_`, `run_`, `thread_`, `chatcmpl-`, `file-`, `vs_`). Keep `zs-` as internal correlation id; add `x-request-id` to all 200/error paths.
- Effort: **S**. Risk: none (additive metadata).

**0d. Tool-call scanning on the EXISTING chat path (G7 — fix the live fail-open now)**
- Files: `main.py:993–1010` (extend `_extract_prompt_from_messages` ~L2854), `scanner.py`, `output_guard.py:1078 (_apply_output_guard_nonstream)`, `secure_streaming.py:508–565`.
- Approach: fold `tools[].function.{name,description}` + `tool_calls[].function.{name,arguments}` into the scannable text for Tier-1/2. Add `_scan_tool_arguments()` running guard per argument value. Extend output guard to inspect `tool_use`/`tool_calls` items (name + args), block on `tool_overreach`/credential, redact PII-in-args by dropping/blocking the call (never in-place — breaks JSON schema).
- Effort: **M**.
- Risk: false positives on legitimate structured args (URLs, SQL). Mitigation: Tier-1 only on tool name+description; Tier-2 only for injection/jailbreak; allowlist tool names per org; fail-open on Bedrock error per existing tier-2 policy.

### Phase 1 — Responses API core (non-streaming, the FIRST milestone — see §5)

**1a. `POST /v1/responses` handler + input/output adapters**
- Files: new `proxy_responses` in `main.py` (~after `proxy_embeddings`); `ai_mesh_shared/openai_request_normalizer.py` (add `normalize_responses_input`, `_normalize_responses_to_chat`, `_normalize_chat_to_responses`).
- Approach: parse `input` (string OR typed items), flatten typed items to scannable text (fold image_url/audio_url/document_id to placeholders), translate to chat `messages` (instructions→system), call `_enforce_request_gates` (0a), route via `LLM_ROUTER.acompletion`, translate completion→`{output[],output_text,usage,status:"completed"}`, run `_enforce_output` on output items, attach `zeroshield` via reused `_build_zeroshield_metadata`/`_redact_for_client_response`.
- Effort: **M**. Risk: typed-item text under-extraction → injection bypass. Mitigation: always fold URLs/items into scanned text; defer audio/doc to Phase 4.

**1b. Responses param mapping + chat param passthroughs (G5)**
- Files: `llm_router.py:62–72 (_PASSTHROUGH_PARAMS)`, `openai_request_normalizer.py:15–20 (OPENAI_TOP_LEVEL_KEYS)`.
- Approach: add `logprobs, max_completion_tokens, parallel_tool_calls, user, reasoning, reasoning_effort`. Normalize `max_completion_tokens→max_tokens` / `max_output_tokens→max_tokens` when absent. Pass through `cache_*_tokens`, `system_fingerprint` from upstream usage. Preserve `choice.logprobs` across output-guard redaction.
- Effort: **S** (bundle). Risk: none (pure passthrough); reasoning tokens not client-visible.

### Phase 2 — Responses state store (`store`, `previous_response_id`, GET/DELETE)

**2a. Response store (Redis-first, org-scoped)**
- Files: new `gateway/ai_mesh_gateway/responses_store.py`; control-plane `Response` model (optional durable backup) in `control/.../core/models.py`; reuse `shared/ai_mesh_shared/redis_pool.py`.
- Approach: key `responses:{org_id}:{response_id}`, TTL config (`store_responses_ttl_days`). On `store=true`, persist redacted output_text + minimal items. `previous_response_id`: load → **assert org match** → reconstruct messages → prepend. HMAC the response_id with org secret to prevent forge/cross-org replay.
- Effort: **L**. Risk: **cross-tenant state leak** (highest in this phase). Mitigation: org_id binding on every read; HMAC validation fail-closed; re-scan parent output via output-guard before reuse (policies may have changed); store redacted-only.

**2b. `GET /v1/responses/{id}`, `DELETE`, `GET /v1/responses/{id}/input_items`, pagination**
- Files: `main.py` new routes; pagination helper `_paginate_list_response(items, limit, after)`.
- Approach: on GET, **re-scan stored items against current policy** (store `policies_hash_at_write`; mismatch→`review_required`/403 in block mode). Soft-delete (`is_deleted`) + audit event; keep EnforcementEvent FK with `SET_NULL`. Input_items: dual copy (operator-raw vs client-redacted).
- Effort: **M**. Risk: stale-policy bypass on replay. Mitigation: mandatory re-scan, no bypass.

### Phase 3 — Streaming (Responses typed events + mid-stream guard)

**3a. Responses streaming events**
- Files: extend `stream_orchestration.py:489 (stream_with_finalize)` with `stream_type` param; `secure_streaming.py` event parsing.
- Approach: emit `response.created` → `response.output_item.added`/`content_part.added` → `response.output_text.delta` → `response.output_text.done`/`output_item.done` → `response.completed`. SSE with `event:` prefix. Reuse `StreamRunMetrics`, finalize hooks, TPM reconciliation. Inject `response.zeroshield` (mirror M-51 terminal frame) before `response.completed`.
- Effort: **L**. Risk: PII token spanning deltas; partial output on block. Mitigation: reuse `STREAM_LOOKAHEAD_BYTES`; only emit `output_item.done` after final lookahead flush scanned; on block emit `response.error` not partial.

**3b. Tool-call streaming + buffered argument scan**
- Files: `secure_streaming.py`, `stream_orchestration.py`.
- Approach: buffer `function_call_arguments.delta` into full JSON object before deeming safe; `json.loads` then scan parsed form (handle escaped JSON). Don't stream tool_use atomically — buffer name+args, scan, then emit.
- Effort: **M**. Risk: malformed-JSON evasion. Mitigation: validate complete object on `finish_reason=tool_calls`; buffer-before-emit for tool items.

### Phase 4 — Tools, retrieval, file_search

**4a. Tool governance policy** — extend `policy/models.py` Rule with `allowed_tools`; compiler stage intersects request `tools[]`/`tool_choice` with org allowlist (block `tool_not_allowed`). Reuse MCP `target_tool` pattern. Effort **M**.
**4b. file_search → ZeroShield RAG** — map `tools[{file_search}]` to `RAGFirewallPipeline.execute` with `vector_store_id` as collection; reuse `ContextGuard` + ranker sanitization. web_search behind URL-allowlist + SSRF guard (`_url_guard`). Effort **M**.
**4c. Tool-call state cache** — `zs:response:{id}:{tool_call_id}` TTL 1h; validate `tool_result` against prior call (reject forged `tool_use_id`); cumulative token accounting across loop turns (extend rate-limiter with `response_id`). Effort **M**. Risk: tool-loop TPM DoS → fail-closed cumulative cap.
**4d. Audio/modalities + json_schema (G8)** — Phase-1 minimal: **reject** audio modality and `json_schema` (400) to prevent silent bypass; document. Phase-2: transcribe-then-scan / scan all JSON leaf values. Effort **L**.

### Phase 5 — Vector Stores + Files API
Models `VectorStore`/`File`/`VectorStoreFile`; multipart upload with mandatory `ContextGuard` content scan + mime allowlist + per-org byte/file quota (Redis-backed, fail-fast). Cascade delete; soft-delete TTL. Reuse `_resolve_org_from_token`, `RAGFirewallPipeline`. Effort **L**.

### Phase 6 — Assistants/Threads/Runs (agents)
Control-plane `Assistant/Thread/Message/Run/RunStep` models; gateway proxies; **background run executor** (Celery `workers/.../agent_runs.py`) reusing the SAME seam (0a) per turn. Tool bridge → MCP. `RunExecutionContext` extends `StreamLaunchContext`. Effort **XL**. Risk: prompt-injection via thread history, tool loops → `max_iterations` cap + per-turn re-scan. **Recommend deferring** in favor of Responses+tools (simpler typed model, no background jobs).

### Phase 7 — Control-plane surfaces (governance/usage/audit/tenant via bearer key)
- Usage/cost: `/v1/usage/tokens`, `/v1/billing/usage`, `cost_calculator` (model pricing table), `cost_usd` in usage object. Effort **M**.
- Audit/observability: `/v1/audit/events` (OpenAI-shaped list), `/v1/audit/trace/{request_id}` (per-stage), org-scoped SSE. Add `request_id` field + `trace_json` to `EnforcementEvent`. Effort **M**.
- Tenant mgmt: `/v1/organization/{projects,users,api_keys}` reusing `GatewayAPIKey`, signals, `IsGatewayKeyOwner`. Add `rotate()` (atomic). Effort **M**.
- Gateway-side policy CRUD `/v1/zeroshield/policies/*` (read from `PolicySync._org_caches`, forward mutations to control plane). Effort **L**.
- Dual-auth: bearer key valid against control-plane policy endpoints (new DRF auth class reusing `validate_api_key`). Effort **M**. Risk: privilege escalation → scope checks (`policy_write`, `kill_switch_admin`) per key permission.

---

## 3. The Single Most Important Architectural Decision

**`/v1/responses` MUST consume the same enforcement functions as `proxy_chat` through a request→chat→response adapter sandwich — never a parallel pipeline.**

Concretely: extract the enforcement core out of `proxy_chat` (Phase 0a) into three format-agnostic functions and have *both* endpoints call them:

```
INPUT ADAPTER          SHARED ENFORCEMENT CORE (single source of truth)        OUTPUT ADAPTER
─────────────          ──────────────────────────────────────────────        ──────────────
responses.input  ─┐                                                        ┌─→ responses.output[]
(string|items)    ├─► _normalize_*_to_chat() ─► _enforce_request_gates()  ─┤
chat.messages    ─┘    (messages[])              (auth,rate,kill,model,    ├─→ chat.choices[]
                                                  policy,Tier1,Tier2)       │
                       LLM_ROUTER.acompletion ──► _enforce_output() ───────┤
                       (org-qualified key)        (guard,redact)            │
                                                  _finalize() ─────────────┘
                                                  (telemetry,TPM,CB,zeroshield envelope)
```

Why this is non-negotiable, grounded in the codebase's own history: the memory log records **repeated fail-open regressions** introduced precisely when an enforcement decision was conditioned on a path-specific flag (`heavy-regression-r5-compliance-failopen`: "security gates never conditioned on `needs_inference`"; `xtenant-model-invocation`: cross-tenant invocation from gating removal). A forked Responses pipeline would re-introduce that entire class of bug at scale, because Tier-2 fail-open behavior, redaction attribution, skip-after-block invariants, and the M-51 single-trace-frame contract all live in subtle conditionals inside `proxy_chat`. Forking copies the easy 80% and silently drops the hard 20% (the security 20%).

The adapter sandwich makes format the *only* thing that differs: `input` polymorphism and `output[]` shape are pure serialization at the edges; auth, scanning, policy, guard, redaction, telemetry, routing, and the zeroshield envelope are **called, not copied**. New surfaces (Responses, Assistants runs, tool loops) become "build the adapter, reuse the core." This also means firewall hardening lands in one place and protects every surface at once — which is exactly the property the existing campaigns rely on.

---

## 4. Risk Register

| Risk | Surface | Failure mode | Mitigation | Owner gate |
|------|---------|-------------|-----------|-----------|
| **R1 Fail-open via forked pipeline** | All new surfaces | Responses/tools skip Tier-2/policy/redaction because logic was copied not called | §3 shared-core seam; CI assertion that `proxy_responses` invokes `_enforce_request_gates`/`_enforce_output`/`_finalize`; no security branch keyed on endpoint/`needs_inference` | Phase 0 |
| **R2 Tool-arg fail-open (live today)** | chat + responses | Jailbreak/PII/credential in `tool_calls.arguments` bypasses scanner/guard | Phase 0d: fold tool name+args into Tier-1/2 + output-guard; buffer-then-scan in stream | Phase 0 |
| **R3 Cross-tenant stored-state leak** | Responses store, threads, file_id | Org A reads/continues Org B's `previous_response_id`/file | org_id on every Redis key; HMAC response_id; assert `parent.org_id==auth.org_id`; audit on mismatch; store redacted-only | Phase 2 gate |
| **R4 Stale-policy replay bypass** | GET responses / input_items | Benign-at-write content retrieved after policy tightens, unscanned | Mandatory re-scan on retrieval; `policies_hash_at_write` compare → `review_required`/403 | Phase 2 |
| **R5 SDK error-shape breakage** | All | Flat/nested mismatch → SDK raises wrong/no exception on block/auth; client treats block as success-ish | Nested envelope everywhere (0b); ZS-type→OpenAI-type map; SDK exception-class tests for block/401/429 | Phase 0 |
| **R6 Streaming-trace contract violation** | Responses/tool streaming | >1 trace frame, or partial output reaches client on block, or PII spans deltas | Reuse `stream_with_finalize` single choke point; `response.error` not partial on block; lookahead before `output_item.done` | Phase 3 |
| **R7 New-modality scan bypass** | audio, json_schema, image | Model emits sensitive data as audio/structured JSON, evading text guard | Reject audio+json_schema in MVP (400); Phase-2 transcribe/leaf-scan | Phase 1/4 |
| **R8 Tool-loop / response-spam DoS** | tool loops | Cumulative TPM not enforced across turns; infinite tool calls | Cumulative token accounting keyed on response_id; `max_iterations`; fail-closed if accounting uncertain | Phase 4 |
| **R9 Privilege escalation via bearer key on control plane** | Phase 7 | Inference key mutates policy/kill-switch | Per-key scopes (`policy_write`,`kill_switch_admin`); org-match assert; audit | Phase 7 |

---

## 5. Recommended FIRST Milestone

**Goal:** `client.responses.create(model=..., input="...")` works **non-streaming, end-to-end, through the full firewall** — block, redact, and allow all behave exactly as on `/v1/chat/completions`.

This deliberately includes Phase 0a (seam) because it is what guarantees inherited enforcement; without it the milestone would be a fork.

**Exact files + functions:**

1. **`gateway/ai_mesh_gateway/main.py`**
   - Extract `async def _enforce_request_gates(auth_ctx, scannable_text, body, org_config) -> GateResult` from `proxy_chat` (~L3484–5505): auth/rate/kill-switch/model-state/threat-intel/blocked-keywords/`_policy_check_cached`/`INPUT_SCANNER.scan_prompt(+_with_tier2)`.
   - Extract `async def _enforce_output(text_or_items, verdicts, org_config) -> guard_verdict` from `_apply_output_guard_nonstream` (~L1078).
   - Extract `_finalize(...)` (telemetry `_emit_telemetry`, TPM `record_usage`/`record_org_usage`, circuit breaker).
   - Repoint `proxy_chat` to call all three (behavior-preserving).
   - Add `async def proxy_responses(request)`: validate → adapt → `_enforce_request_gates` → `LLM_ROUTER.acompletion` → `_enforce_output` → adapt out → `_build_zeroshield_metadata` + `_redact_for_client_response` → return `{id:resp_*, object:"response", output:[{type:"text",text}], output_text, usage, status:"completed", zeroshield:{...}}`.
   - Add `_build_openai_error_response(...)`; route `proxy_responses` errors through it.
   - `_generate_openai_id("response")` for `resp_*`; add `x-request-id`.

2. **`gateway/ai_mesh_shared/openai_request_normalizer.py`**
   - `normalize_responses_input(input)` → `(scannable_text, original_items)`.
   - `_normalize_responses_to_chat(body)` → chat body (`instructions`→system, `input`→user, `max_output_tokens`→`max_tokens`).
   - `_normalize_chat_to_responses(completion)` → responses object.
   - Add `user, max_completion_tokens, reasoning, reasoning_effort, parallel_tool_calls, logprobs` to `OPENAI_TOP_LEVEL_KEYS`.

3. **`gateway/ai_mesh_gateway/llm_router.py`** — add those six to `_PASSTHROUGH_PARAMS` (L62–72).

4. **`gateway/ai_mesh_gateway/tests/test_responses_sdk_compat.py`** (new) — mount real app via `httpx.ASGITransport` + `AsyncOpenAI`: (a) string input → `output[]`/`output_text`/`response.zeroshield` present; (b) injection in input → `openai.PermissionDeniedError` with nested `error.type`; (c) PII in output → redacted text; (d) allow path parity with chat. Plus a refactor-guard run of existing `test_openai_sdk_compat.py` + `test_stream_trace_frame.py` (must stay green — proves 0a was behavior-preserving).

**Explicitly OUT of this milestone:** streaming, `store`/`previous_response_id`, tools/file_search, audio, GET/DELETE. Those are Phases 2–4.

---

## 6. What Is Realistically Deferrable

- **Assistants/Threads/Runs (Phase 6, XL):** background-job orchestration, thread state, run-step DAG. Defer in favor of Responses + tools + `previous_response_id`, which cover agentic/tool workflows with a simpler typed model and no async run executor. Revisit only on explicit customer demand.
- **Audio modalities & multimodal scanning (G8 full):** ship the **reject-with-400** guard in MVP (prevents silent bypass); defer transcribe-then-scan and image OCR scanning to a later phase.
- **`json_schema` structured-output scanning:** reject in MVP; defer leaf-value scanning.
- **Vector Stores + Files API (Phase 5):** `file_search` can map to existing `/v1/rag/*` first; full Files/VectorStore CRUD, batch upload, quotas, expiry are later.
- **Cost enforcement / billing quotas, usage export, cache-token & `system_fingerprint` passthrough, `logprobs` preservation tests:** all S/M niceties — implement opportunistically, none block the Responses program.
- **Pagination cursors, `model_id` removal from `/v1/models`, model-alias deprecation, context `truncation_strategy`/`/v1/tokens/count`, conversation-lifecycle TTL:** schema-hygiene and UX; batch into a later "spec-conformance" cleanup.
- **Legacy `/v1/runs`:** intentionally never implement → 405 with redirect message.
- **Control-plane bearer-key dual-auth + gateway policy CRUD (Phase 7):** valuable for SDK-only operators but orthogonal to inference compatibility; ship after the data-plane Responses surface is solid.

**Not deferrable (must land in Phase 0, regardless of scope cuts):** the §3 seam, the nested error envelope, and tool-call scanning on the existing chat path (G7/R2) — these are correctness/security foundations, not features.
---

## 7. Step 2D — Live Runtime Confirmation (local stack, org 3, gemma-free)

| Gap | Probe | Result | Verdict |
|-----|-------|--------|---------|
| G1 — Responses API missing | `POST /v1/responses` (valid key) | `404 {"detail":"Not Found"}` | CONFIRMED |
| G3 — error envelope non-OpenAI | 404/422 bodies | `{"detail":...}` / flat `{"error":"model_not_configured"}` (not nested `{error:{message,type}}`) | CONFIRMED |
| G7 — tool-arg/desc fail-open (LIVE security bug) | injection+PII in (A) message content vs (B) `tool_calls.arguments` vs (C) `tools[].function.description` | A→**403 blocked**; B,C→**passed firewall, reached upstream (429)** | CONFIRMED — tool-call content bypasses Tier-1/2 today |

Static analysis matched runtime. No false positives among the headline gaps. G7 is a live fail-open on the **existing** chat path (not just a Responses concern) and is the highest-priority security item.

---

## 8. Phase 6 — Verification Sign-Off (local, live)

```
Fix: OpenAI Responses API (/v1/responses) + G7 tool-scan + SDK passthroughs
──────────────────────────────────────────────────────────────────────────
Services running locally:   [x] YES (gateway restarted, healthy, no import errs)
Stock openai SDK works:     [x] YES (2.38.0: client.responses.create -> 200)
Firewall inherited:         [x] YES (injection -> openai.PermissionDeniedError)
OpenAI error envelope:      [x] YES (nested {error:{message,type}}; 400/403/404)
Streaming typed events:     [x] YES (response.created..delta..completed + [DONE])
State (previous_response_id):[x] YES (recalled context; GET/DELETE; cross-tenant 404)
G7 fail-open closed:        [x] YES (tool-arg & tool-def injection -> 403)
Chat regression:            [x] NONE (normal 200, injection 403)
Tests:                      [x] 50 pass (26 existing + 9 adapter + 15 streaming)
──────────────────────────────────────────────────────────────────────────
Status: VERIFIED (local). Local branch feat/openai-responses-api @ 0ea7d9f. NOT deployed.
```
