# OpenAI-SDK Compat — Phase 3 Decisions + Fix Plan (LOCKED)

Three fix-approach decisions, settled by adversarial debate (`devils-advocate` steelmanned the
opposite of each, `design-arbiter` decided on compat/risk/DX, `enforcement-guardian` held a hard
veto). **All three guardian sign-offs granted — no veto.** Every position is code-grounded.

---

## D-a — Block status → **400 `content_filter`** (LOCKED)

**Decision:** INPUT content/PII/secret/injection blocks return **HTTP 400**,
`error.code="content_filter"`, `error.type="invalid_request_error"` → SDK `BadRequestError`
(was `403` / `permission_error` / `content_blocked` → `PermissionDeniedError`).

**Why it wins:** the openai SDK keys the exception class on **HTTP status only** (`_make_status_error`),
so `e.code` never changes the class — the customer's `except` forks on 400-vs-403. `400 + content_filter`
is the Azure/LiteLLM/LangChain guardrail convention (LiteLLM raises `ContentPolicyViolationError` on the
`content_filter` substring; `exception_mapping_utils.py:111-119`); at 403/`content_blocked` that tooling is
**blind** to our blocks. `403` also mis-signals an **AUTH** failure (pollutes auth alerting / key-rotation
runbooks). Retry table is a wash — **both 400 and 403 are non-retried** (`_should_retry` retries only 408/409/429/≥500).

**Guardrails (mandatory, from the debate):**
- **Keep `model_not_allowed` / auth at 403** — authorization ≠ content filter; only content/PII/secret/injection categories move to 400. *(guardian)*
- **Output mid-stream blocks stay `200` + `finish_reason="content_filter"`** — a started stream can't retroactively become a 400 (`responses_adapters.py:226`, OpenAI-proper). 400/content_filter is **input-block only**. *(arbiter)*
- **Dual-key the body:** set `error.code="content_filter"` AND keep the top-level ZS `category`/`code` mirror — the demo client keys on `exc.status_code` generically, so it survives. *(arbiter)*
- **Migration:** add `GATEWAY_BLOCK_STATUS` env knob (`400|403`, default `400`) for a customer migration window; CHANGELOG as a documented breaking change (existing `except PermissionDeniedError` catchers). *(arbiter)*
- **NEVER 409/429/≥500** — those are auto-retried → a blocked adversarial prompt would be silently re-sent. *(all)*

**Impl:** `_build_safe_block_response(status_code=…)` is the single funnel (`main.py:3095/682`); add
`content_filter→invalid_request_error` to `_ZS_CODE_TO_OPENAI_TYPE` (`responses_adapters.py:42`).
**Guardian: SIGN OFF** — status is orthogonal to block-before-inference + body-scrub + non-retry; no bypass/leak.

## D-b — **Single shim + fold SEAM-C in** (LOCKED; reject the ~40-site edit)

**Decision:** the existing `_openai_compat_shim` (`main.py:237`) remains the single error-envelope choke
point. Do **NOT** edit ~40 `JSONResponse` sites. **Fold the SEAM-C request_id fix INTO the shim.**

**Why it wins:** the shim is verified to cover **all** `/v1/*` error sites, is **idempotent** (no double-wrap;
`responses_adapters.py:94`), passes SSE through untouched (`main.py:264`), and correctly excludes non-`/v1`
paths. The 40-site edit re-introduces the per-site drift the shim eliminates and still misses the catch-alls
(`main.py:180`/`170`/`11541`). The shim *causes* SEAM-C only by an **unconditional header overwrite** — a one-place fix.

**SEAM-C fix (one place):** change the shim rid precedence to **prefer a handler-set id**:
`rid = response.headers.get("x-request-id") or parsed.get("request_id") or getattr(request.state,"gw_request_id","") or <gen>`;
stamp the header **from that same value** and only override when absent → **header == body**. Thread the canonical
`_REQUEST_ID` ContextVar so shim + body + audit log share one id. **Also** set `request.state.gw_request_id` in
`proxy_embeddings` / `proxy_responses` / `create_moderations` (SEAM-C breadth — Phase-2 C3–C6).
**Guardian: SIGN OFF** — shim is status-preserving (can't un-block), body-preserving, idempotent, operates only on
already-scrubbed bodies; request_id is a tracing field, not an enforcement control.

## D-c — **Clean 404 now; files/batches = P2** (LOCKED)

**Decision:** keep the clean OpenAI `404` for `/v1/files` + `/v1/batches` (status quo, `main.py:11548-11555`).
Defer to P2.

**Why it wins:** no synchronous chat/responses/embeddings flow (the v1 gate) needs them — **vision works inline
via `image_url`/`data:` URIs** (`responses_adapters.py:150-155`); batch is inherently async/bulk. A `404` →
`openai.NotFoundError` is the honest SDK contract. Building them = a large **unscanned-upload / async-ingestion**
attack surface for zero synchronous benefit.
**Guardian: SIGN OFF** — not implementing removes an ingestion channel that would bypass the request-time
input-scan hard-block; 404-now = zero ingestion surface; strictly attack-surface-reducing.
**Caveat:** responses `input_file` (Phase-2 B12) should be **documented-unsupported or return a clear 400 param
error**, not silently dropped.

---

## Consolidated Phase-5 Fix Plan (decisions × Phase-2 reconciled defects)
1. **SEAM-B** (B1–B11): widen **BOTH** `_RESP_DIRECT_PASSTHROUGH` **and** `OPENAI_TOP_LEVEL_KEYS`, plus add
   `text.format→response_format` & `truncation` translation in `responses_to_chat` (per `P2H-ALLOWLIST-FIX-2LAYER` —
   adapter-only fixes are end-to-end ineffective).
2. **SEAM-C** (C1–C6) [D-b]: shim id-precedence + set `gw_request_id` on all surfaces + `_build_zeroshield_metadata`
   derives from the canonical id.
3. **SEAM-A** (A1–A3): validate `max_completion_tokens` (ceiling/sign); don't inject `max_tokens` when the client
   didn't send it; map `mct` correctly on the responses path.
4. **S1 (HIGH):** accumulate `delta.tool_calls` in `_translate_chat_stream_to_responses` → emit
   `response.function_call_arguments.delta/done` + a `function_call` output item.
5. **D-a:** block status 400/`content_filter` for **content categories only** (keep auth 403); `GATEWAY_BLOCK_STATUS` knob.
6. **B12 / D-c caveat:** responses `input_file` → explicit 400, not silent drop.

**Reclassified/intentional (no fix):** `n`-clamp (surface optional, product decision). **Needs-live:** Phase 7
(store durability, audit-join). **Confirmed clean:** isolation, single route, header presence, core stream edges, D1–D5.
