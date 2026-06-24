# OpenAI-SDK Exact-Compatibility Contract (FROZEN — Phase 1)

**Status:** frozen 2026-06-22 · **Program:** OpenAI-SDK exact compatibility
**Product requirement:** ZeroShield must be consumable with the **unmodified** stock
`openai` Python SDK — *base URL + API key swap only*, no custom SDK.

**Source of truth (executable):** `gateway/ai_mesh_gateway/tests/test_openai_sdk_compat.py`
mounts the **real** FastAPI app over `httpx.ASGITransport` and drives it with the stock
SDK; **only the upstream provider is stubbed**. This document is the human-readable
projection of that suite — every clause cites the exact implementing location, or
`MISSING + Dx`.

- **SDK pinned:** `openai==2.38.0` (`gateway/pyproject.toml` → `[project.optional-dependencies].dev`). Bumping the pin requires re-running the suite.
- **Validate:** `cd gateway && .venv/bin/python -m pytest ai_mesh_gateway/tests/test_openai_sdk_compat.py -q`
- **Phase-0 result:** 25 passed + 4 xpassed, 0 failed (suite co-owned with a parallel session; count converged 23→25). The four playbook-flagged defects D2/D3/D4/D5 are marked `xfail(strict=False)` and **all XPASS** → already fixed in-tree. **In-process backlog is empty.**
- **Runtime middleware order (verified):** `CORSMiddleware[0]` → `_openai_compat_shim[1]` (`main.py:237`) → `_prom_observe[2]` → `AuthMiddleware[3]`. The compat shim is **outside** auth, so it re-nests + stamps `x-request-id` on *every* `/v1/*` 4xx/5xx — including auth 401s. *(Confirmed by `app.user_middleware` dump AND an end-to-end unauthenticated probe — see Dim 1. An adversarial auditor claimed the opposite from registration-order theory; refuted empirically.)*

> ⚠️ All evidence below is **in-process** against the local tree. Phase 7 must re-prove
> against a **live deployed gateway** (prod may lag local). File paths are relative to
> `gateway/ai_mesh_gateway/` unless noted.

---

## The 8-Dimension Contract

### Dim 1 — Auth
**Clause:** `Authorization: Bearer <gateway-key>`, org-scoped. A missing/invalid/expired/
revoked key → **401**, body in the nested OpenAI envelope; SDK selects the exception
class from the HTTP status (401 → `AuthenticationError`).

| Implements | Location |
|---|---|
| `AuthMiddleware` (ASGI), bearer extraction, soft-auth set | `middleware.py:202`, `SOFT_AUTH_PATHS` `middleware.py:51`, dispatch `middleware.py:303` |
| `AuthContext` (carries `organization_id`/`org_slug`/`allowed_models`) | `middleware.py:90-93`, built `middleware.py:199` |
| 401 emitted (flat body) + `WWW-Authenticate: Bearer` | `middleware.py:332-341`, `middleware.py:393` |
| **Flat 401 → nested envelope + `x-request-id`** (the shim is outside auth) | `_openai_compat_shim` `main.py:237-296` via `coerce_chat_error_to_openai` `responses_adapters.py:88` |
| Redis-unavailable → 503 + `Retry-After` | `middleware.py:354-362` |

**Verified:** `test_invalid_api_key_raises_authentication_error` PASS (401 → `AuthenticationError`).
**Empirically confirmed in-process** (unauthenticated `/v1/chat/completions`): body is the
NESTED envelope `{"error":{type:"authentication_error", code:"unauthorized", message, param:null}, request_id}`
with an `x-request-id` header — i.e. the flat 401 from `AuthMiddleware` is re-nested + stamped
by the outer `_openai_compat_shim`. *(Adversarial-review note: an auditor claimed the shim
sits inside auth; refuted by the `app.user_middleware` dump AND this end-to-end probe.)*

### Dim 2 — Path map
**Clause:** mirror the OpenAI paths the stock SDK calls; unimplemented OpenAI surfaces →
**clean OpenAI 404** (never a 405/500/HTML). See the full coverage table below.

| Surface | Location |
|---|---|
| `POST /v1/chat/completions` | decorator `main.py:3854`, handler `proxy_chat` `main.py:3968` |
| `POST /v1/responses` | `main.py:7937` (single route — D1) |
| `GET/DELETE /v1/responses/{id}` · `GET /v1/responses/{id}/input_items` | `main.py:8018` / `8030` / `8042` |
| `POST /v1/embeddings` | `proxy_embeddings` `main.py:8168` |
| `GET /v1/models` · `GET /v1/models/{id}` | `list_models` `main.py:11415` · `retrieve_model` `main.py:11471` (**D4**) |
| `POST /v1/moderations` | `create_moderations` `main.py:11487` (**D4**) |
| 404-shim for unimplemented OpenAI surfaces | list `main.py:11548-11555`, handler `_openai_surface_unimplemented` `main.py:11541`, registered `main.py:11557` |
| Catch-all: ANY `HTTPException` (incl. 404) → OpenAI-shaped | `_openai_shaped_http_exc` `main.py:180` |

**Verified:** `test_models_list_returns_real_ids` PASS, `test_models_retrieve_by_id_D4` **XPASS**, `test_only_one_post_v1_responses_route` PASS.

### Dim 3 — Success schema
**Clause:** `chat.completion` / `chat.completion.chunk` / `response` / embeddings objects
carry OpenAI-exact fields (`id` with `chatcmpl-`/`resp-` prefix, `object`, `created`,
`model` = **client-requested** string, `choices`/`output`, `usage`). **No** upstream id /
provider / cost / internal alias leak.

| Implements | Location |
|---|---|
| ZeroShield metadata field builder (additive, `extra="allow"`) | `_build_zeroshield_metadata` `main.py:1508` |
| OpenAI id generation (`chatcmpl-`/`resp-`) | `generate_openai_id` `responses_adapters.py:33` |
| chat→responses object shaping | `chat_completion_to_responses` `responses_adapters.py:231` |
| Embeddings: echo client model alias (not upstream id) | `main.py:8483-8484` |
| No-leak scrub (`routed_model_id`/`model_id`, `provider_specific_fields`, citations) | `_scrub_upstream_passthrough` `main.py:1645` (pops at `1659/1663/1668`), `main.py:730-738` |

**Verified:** `test_non_streaming_parses_as_chat_completion`, `test_chat_tool_calls_forwarded_and_parsed`, `test_chat_response_format_json_schema`, `test_chat_usage_block_present`, `test_embeddings_single/batch`, `test_responses_non_streaming_parses_as_response_object` — all PASS.

### Dim 4 — Streaming
**Clause:** SSE `data: {json}\n\n` terminated by `data: [DONE]`. Chat → `chat.completion.chunk`
frames; Responses → **typed events** `response.created → response.in_progress →
response.output_item.added → response.content_part.added → response.output_text.delta* →
response.output_text.done → response.output_item.done → response.completed`
(`response.failed` on error). Stock SDK iterator must parse and terminate cleanly.

| Implements | Location |
|---|---|
| Chat stream finalize + terminal ZeroShield trace frame | stream pipeline `main.py:2253-2433` (`_build_zeroshield_metadata` mirror `2262/2279`) |
| Responses typed-event translation (created `7831`, in_progress `7832`, output_item.added `7833`, content_part.added `7877`, delta `7884`, done `7912`/`7917`, completed `7933`) | `_translate_chat_stream_to_responses` `main.py:7812-7934` |
| Pre-stream block returns JSON (never a corrupt SSE body) | `proxy_responses` `main.py:7986` |

**Verified:** `test_streaming_chunks_trace_frame_and_done`, `test_responses_streaming_events_and_text` PASS; `test_responses_typed_stream_sequence_D5` **XPASS** (full typed sequence incl. `output_text.delta`).

### Dim 5 — Error envelope
**Clause:** NESTED `{"error": {message, type, param, code}}` so the SDK populates
`e.code`/`e.type`/`e.param`/`e.message` (a flat top-level string leaves them `None`).
ZeroShield diagnostics (request_id/category/blocked_by/pipeline_trace) mirrored at the
top level for ZS/demo consumers.

| Implements | Location |
|---|---|
| Central block builder → nested envelope | `_build_safe_block_response` `main.py:599` via `build_openai_error` `responses_adapters.py:69` |
| Universal flat→nested coercion (single choke point, idempotent) | `_openai_compat_shim` `main.py:237`, `coerce_chat_error_to_openai` `responses_adapters.py:88` |
| Responses-path chat-error coercion | `main.py:8005` (`_coerce_chat_error`) |
| HTTPException → OpenAI-shaped | `_openai_shaped_http_exc` `main.py:180` |

**Verified:** `test_blocked_request_raises_api_status_error_with_zeroshield_body` PASS; `test_error_fields_populated_on_block_D2` **XPASS** (`e.code`/`e.type`/`e.message` populated); `test_responses_blocked_non_stream_raises_permission_error` PASS.

### Dim 6 — Headers
**Clause:** `x-request-id` on **every** response (→ SDK `response._request_id` /
`error.request_id`); `content-type` `application/json` | `text/event-stream`; CORS
`expose-headers` includes `x-request-id`.

| Implements | Location |
|---|---|
| `x-request-id` on every `/v1/*` response (header == body id) | `_openai_compat_shim` `main.py:259`, `main.py:277` |
| Block path sets the header directly | `main.py:681-684` |
| CORS `expose_headers` includes `x-request-id` | `main.py:348` |

**Verified:** `test_error_request_id_present_on_block_D3` **XPASS** (`e.request_id` non-empty); trace-frame `zeroshield.request_id` asserted in `test_streaming_chunks_trace_frame_and_done` PASS.

### Dim 7 — Retry
**Clause:** honor `Retry-After`; the SDK auto-retries 408/409/429/≥500. Map upstream
provider throttles to **429/503 (never 502)**; never leak provider/topology on a
retryable error.

| Implements | Location |
|---|---|
| Upstream non-200 passthrough to client (status preserved) | `main.py:6689` |
| 408/429 excluded from risk/auto-isolation (transient) | `main.py:6703` (`code not in (408, 429)`) |
| Sanitize LiteLLM error text (no provider/topology leak) | `_sanitize_llm_error_response` (embeddings `main.py:8476`; chat error path) |
| Auth-service 503 + `Retry-After: 5` | `middleware.py:354-362` |

**Verified:** `test_error_upstream_429_maps_to_rate_limit_error` PASS (upstream 429 → `RateLimitError`, not 502).

### Dim 8 — Model strings
**Clause:** the `model` field **echoes the client-requested string**. Unknown model →
clean OpenAI 404 (not 400/500). No upstream provider id (`anthropic/…`, Bedrock) or cost
in `model` or body.

| Implements | Location |
|---|---|
| `retrieve_model` unknown → 404 `model_not_found` | `main.py:11481-11484` |
| Disallowed model (in allowlist gate) → 403 `model_not_allowed` | `main.py:4447-4466` |
| Embeddings: model alias echo + unknown-to-org → 404 `model_not_configured` | `main.py:8483-8484`, `main.py:8384-8396` |
| Responses echoes `raw_body["model"]` | `main.py:8007` |
| No upstream-id leak (scrub) | `_scrub_upstream_passthrough` `main.py:1645` |

**Verified:** `test_error_model_not_allowed` PASS (typed 403/404), `test_embeddings_single` PASS (alias echoed).

---

## Path Coverage Table (vs official OpenAI SDK surface)

Legend: **IMPL** = implemented with OpenAI semantics · **404-shim** = explicit clean
OpenAI 404 (`main.py:11548-11555`) · **404-catchall** = unknown path → OpenAI-shaped 404
(`_openai_shaped_http_exc` `main.py:180`) · **OOS** = out-of-scope for a firewall proxy.

| OpenAI SDK call | Path | Status | Anchor / Disposition |
|---|---|---|---|
| `chat.completions.create` | `POST /v1/chat/completions` | **IMPL** | `main.py:3854/3968` |
| `responses.create` | `POST /v1/responses` | **IMPL** | `main.py:7937` |
| `responses.retrieve` / `delete` | `GET/DELETE /v1/responses/{id}` | **IMPL** | `main.py:8018/8030` |
| `responses.input_items.list` | `GET /v1/responses/{id}/input_items` | **IMPL** | `main.py:8042` |
| `embeddings.create` | `POST /v1/embeddings` | **IMPL** | `main.py:8168` |
| `models.list` | `GET /v1/models` | **IMPL** | `main.py:11415` |
| `models.retrieve` | `GET /v1/models/{id}` | **IMPL (D4)** | `main.py:11471` |
| `moderations.create` | `POST /v1/moderations` | **IMPL (D4)** | `main.py:11487` |
| `completions.create` (legacy) | `POST /v1/completions` | **404-shim · OOS** | `main.py:11548` |
| `files.*` | `/v1/files`, `/v1/files/{rest}` | **404-shim · OOS** | `main.py:11549-11550` |
| `batches.*` | `/v1/batches`, `/v1/batches/{rest}` | **404-shim · OOS** | `main.py:11551-11552` |
| `images.*` | `/v1/images/{rest}` | **404-shim · OOS** | `main.py:11553` |
| `audio.*` | `/v1/audio/{rest}` | **404-shim · OOS** | `main.py:11554` |
| `fine_tuning.*` | `/v1/fine_tuning/{rest}` | **404-shim · OOS** | `main.py:11555` |
| (client typo guard) | `POST /v1/chat-completions` | **404-shim** | registered shim |
| `responses.cancel` (background) | `POST /v1/responses/{id}/cancel` | **404-catchall · OOS** | `main.py:180` (background mode unsupported) |
| `models.delete` (fine-tuned) | `DELETE /v1/models/{id}` | **404-catchall · OOS** | `main.py:180` |
| `vector_stores.*` | `/v1/vector_stores/*` | **404-catchall · OOS** | ZS uses `/v1/rag/*` instead |
| `beta.assistants` / `threads` | `/v1/assistants`, `/v1/threads` | **404-catchall · OOS** | OpenAI-deprecated → Responses |
| `uploads.*` | `/v1/uploads/*` | **404-catchall · OOS** | `main.py:180` |

> **SDK client-side helpers (no separate HTTP path):** `responses.parse()` and
> `responses.stream()` are convenience wrappers in the SDK over `POST /v1/responses`
> (structured-output parsing / a stream manager) — covered by the `responses.create` row.
> `responses.compact()` is **not** a distinct OpenAI HTTP surface. *(Flagged "missing" by an
> auditor; verified false-positive — they are not separate endpoints.)*

**ZeroShield extensions (bearer-auth, NOT OpenAI surface, out of compat scope):**
`GET /v1/observability` (`8062`), `GET /v1/usage` (`8096`), `POST /v1/policy/check`,
`/v1/rag/*` (collections/documents/ingest/query), `/v1/admin/*`.

### Missing in-scope OpenAI surfaces: **NONE.**
Every OpenAI path the stock SDK's core inference/responses/embeddings/models/moderations
methods call is implemented. There are **zero `MISSING + Dx`** rows.

> **Runtime-probe corroboration (Phase-1 review, in-process).** The **404-catchall** rows
> above were asserted by code anchor (`_openai_shaped_http_exc` `main.py:180`); a direct
> probe confirms they are SDK-clean at runtime — there is **no raw `{"detail":...}`
> anywhere in `/v1`**:
> ```
> POST /v1/completions (404-shim) → 404 nested=True code=endpoint_not_found x-request-id=Y
> GET  /v1/assistants (catchall)  → 404 nested=True type=invalid_request_error x-request-id=Y
> POST /v1/threads (catchall)     → 404 nested=True type=invalid_request_error x-request-id=Y
> POST /v1/responses/x/cancel     → 404 nested=True type=invalid_request_error x-request-id=Y
> ```
> Likewise the **auth 401** was probed across all three failure modes (no header /
> malformed bearer / unknown key) → every one returns the nested envelope
> (`type:authentication_error, code:unauthorized`) **with `x-request-id`**, independently
> reproducing the `app.user_middleware`-dump finding. Probe scripts: `/tmp/probe_auth401.py`,
> `/tmp/probe_404.py` (reuse the harness fixture config). Evidence wins over the static
> "flat 401 / D1 missing" claim raised by an adversarial pass.

---

## Per-Surface Execution Trace (line anchors)

Common outer pipeline (all `/v1/*`):
```
SDK → CORS[0] → _openai_compat_shim[1] (main.py:237) → _prom_observe[2] → AuthMiddleware[3] (middleware.py:202)
      AuthMiddleware: bearer→validate_api_key (middleware.py:365) → request.state.auth_context
      ... handler ...
      ← _openai_compat_shim stamps x-request-id (main.py:259) + (on 4xx/5xx) flat→nested coercion (main.py:289)
```

**chat.completions** `proxy_chat` `main.py:3968`
```
body validate (messages list main.py:4132) → allowed-model gate (4447) → input scan
(INPUT_SCANNER, block→_build_safe_block_response main.py:599; enforce log main.py:645/5791)
→ policy/compliance (POLICY_SYNC main.py:924/951; _routing_compliance_required 2796)
→ LLM_ROUTER.acompletion (6688) │ non-200 passthrough (6689), 408/429 risk-exclude (6703)
→ output guard OUTPUT_GUARD.inspect (6793, gated by secure_output_scan 6685)
→ + _build_zeroshield_metadata (1508) → JSONResponse  | stream: _fake/real stream → [DONE]
```

**responses** `proxy_responses` `main.py:7937`
```
validate model/input (7951-7959) → previous_response_id replay (_ResponseStore, 7967-7974)
→ responses_to_chat (responses_adapters.py:178) → _dispatch_chat_internally (7790) [reuses proxy_chat pipeline]
→ stream: _translate_chat_stream_to_responses (7812) typed events
   non-stream: chat_completion_to_responses (responses_adapters.py:231); error→_coerce_chat_error (8005)
→ store.save if store=true (8010) → JSONResponse + x-request-id (8015)
```

**embeddings** `proxy_embeddings` `main.py:8168`
```
body/input validation (8190-8261) → allowed-model (8276) → platform-reserved (8311)
→ org-ownership gate (8343, _filter_embedding_eligible_models) → TPM/burst (8417/8431)
→ kill-switch/model-state (8445/8457) → LLM_ROUTER.aembedding (8471) → alias echo (8483) → JSONResponse
```

**models** `list_models` `main.py:11415` → `_resolve_models_for_request` `11434` (org-scoped, dedup) ·
`retrieve_model` `11471` → 404 `model_not_found` `11481`.

**moderations** `create_moderations` `main.py:11487` → INPUT_SCANNER detectors → OpenAI moderation schema.

**errors (all paths):** handler JSONResponse → `_openai_compat_shim` (`main.py:237`) coerces flat→nested
(`coerce_chat_error_to_openai` `responses_adapters.py:88`) + `x-request-id`; uncaught `HTTPException` →
`_openai_shaped_http_exc` (`main.py:180`).

---

## GATE — Reconciliation with Phase-0 xfail set

| Phase-0 xfail cell | Contract dim | Coverage row | Reconciles? |
|---|---|---|---|
| D2 (chat error fields) → **XPASS** | Dim 5 | error-envelope IMPL (`main.py:599/237`) | ✅ implemented, not missing |
| D3 (request-id) → **XPASS** | Dim 6 | `x-request-id` IMPL (`main.py:259/684/348`) | ✅ |
| D4 (`models.retrieve`/moderations) → **XPASS** | Dim 2 | IMPL (`main.py:11471/11487`) | ✅ |
| D5 (responses typed-stream) → **XPASS** | Dim 4 | IMPL (`main.py:7812`) | ✅ |

The coverage table's **MISSING set is empty**, which equals the Phase-0 **xfail backlog
(empty — all four XPASSed)**. **No disagreement.** The remaining program risk is **prod
drift** (local tree vs deployed gateway), to be settled by the Phase-7 live run — not
in-process correctness.
