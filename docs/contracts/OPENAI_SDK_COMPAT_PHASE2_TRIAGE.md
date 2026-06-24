# OpenAI-SDK Compat — Phase 2 Adversarial Triage (RECONCILED)

**Method:** claims are settled by a TEST, not opinion. Every surviving defect has a
FAILING `xfail(strict=True)` repro; every dropped/false-positive claim is a PASSING
challenge test (or removed). **Adversarial:** this pass independently re-ran + challenged
a prior 12-defect triage and a 5-agent mesh hunt — false-positives were caught, framings
corrected, and ~17 missed defects added.

## Repro corpus (`gateway/ai_mesh_gateway/tests/`)
- `test_openai_sdk_compat_phase2.py` — prior pass (12)
- `test_openai_sdk_compat_phase2_adversarial.py` — lead, SEAM-B extensions (5)
- `test_p2_false_positive_hunter.py`, `test_p2_hidden_failure_hunter.py`,
  `test_p2_state_config_agent.py`, `test_p2_runtime_evidence_agent.py`,
  `test_p2_edge_case_agent.py` — 5 mesh agents

**GATE run:** `pytest <corpus> -q -rxX` → **48 passed, 39 xfailed, 0 failed, 0 xpassed.**
(passed = false-positive challenges + clean-edge confirmations; xfailed = confirmed-defect
repros; 0 xpassed = no defect claim silently passes.)

> Scope: in-process (stock SDK over ASGITransport, upstream stubbed). 2 items need a LIVE
> gateway (Phase 7). SDK CORE remains clean (D1–D5, isolation, headers, core stream edges).

---

## SEAM-A — `max_completion_tokens` handling (4)
| id | sev | repro | note |
|---|---|---|---|
| A1 `P2-MCT-CHAT-injects-max_tokens` | **HIGH** | phase2 | client sends only `max_completion_tokens` → gateway also injects `max_tokens=4096` (main.py:5278) → dual-field 400 on o1/o3/gpt-5 |
| A2 `P2-MCT-CHAT-uncapped` | MED | phase2 | `mct` bypasses the ceiling/sign validation (main.py:4059-4126 keys only on `max_tokens`) |
| A2b `P2-MCT-CHAT-negative-unvalidated` | LOW | fp-hunter | negative `mct` → 200 (vs `max_tokens<0` → 400); sign-gap sub-case of A2 |
| A3 `P2H-RESP-MCT-RENAMED` | MED | hidden | responses path renames `mct`→`max_tokens` (responses_adapters.py:200-203) → reasoning models behind `/v1/responses` get the deprecated field |

## SEAM-B — responses→chat adapter param drops (13 + fix-guidance)
Root: `_RESP_DIRECT_PASSTHROUGH` allowlist (responses_adapters.py:111) copies only 11 keys.
**Fix-completeness (`P2H-RESP-ALLOWLIST-FIX-2LAYER`, MED):** the responses path re-enters
`proxy_chat`→`normalize_openai_chat_request(strip_unknown_top_level=True)` (main.py:4018),
so adding a param to the adapter allowlist alone is **end-to-end ineffective** — it must
ALSO be added to `OPENAI_TOP_LEVEL_KEYS` (shared/.../openai_request_normalizer.py). `text`
is the exception (adapter strips it first → needs an explicit `text.format`→`response_format`
translation in `responses_to_chat`).

| id | sev | repro | note |
|---|---|---|---|
| B1 `P2-RESP-drops-response_format` | **HIGH** | phase2 | chat-style structured output dropped |
| B2 `P2-RESP-drops-text-format` | **HIGH** | adversarial | **native Responses structured output** (`responses.parse()`) dropped — adapter doesn't handle `text` at all |
| B3 `P2-RESP-drops-frequency/presence_penalty` | MED | phase2 | |
| B4 `P2-RESP-drops-top_logprobs` | MED | phase2 | `logprobs` kept, companion dropped |
| B5 `P2-RESP-drops-logit_bias` | MED | adversarial | (2-layer: chat path drops too) |
| B6 `P2-RESP-drops-service_tier` | LOW-MED | adversarial | (2-layer) latency/billing tier ignored |
| B7 `P2-RESP-drops-modalities` | MED | fp-hunter | output-modality selection lost |
| B8 `P2-RESP-drops-safety_identifier` | MED | fp-hunter | **security:** abuse-attribution signal lost on responses |
| B9 `P2-RESP-drops-prediction` | LOW | adversarial | predicted-outputs latency opt lost |
| B10 `P2-RESP-drops-truncation` | LOW | adversarial | Responses-native; needs translation, long-context callers 400 |
| B11 `P2-RESP-drops-prompt_cache_key` | LOW | fp-hunter | cache-affinity hint lost |
| B12 `P2H-RESP-INPUT-PART-DROP` | MED | hidden | unknown input parts (input_file/input_audio/reasoning/refusal) silently dropped in `_input_item_to_message` |
| B13 `P2H-RESP-EMPTY-TOOLCALLID` | LOW | hidden | `function_call_output` w/o id → empty `tool_call_id` forwarded |

## SEAM-C — request_id correlation (6) — **broader than one root cause**
The "three independent ids per request" pathology recurs on **5 surfaces** via 3 mechanisms;
the prior plan (thread `_REQUEST_ID` into `_build_zeroshield_metadata`) fixes only chat-body.
Full fix also needs `request.state.gw_request_id` set in `proxy_embeddings`/`proxy_responses`/
`create_moderations` **and** the shim to PRESERVE a handler-set `x-request-id`.

| id | sev | repro | note |
|---|---|---|---|
| C1 `P2-XRID-block-403` (+ `…-SECURITY_BLOCK-log`) | MED | phase2 + runtime | 403 header ≠ body ≠ `[SECURITY_BLOCK]` log id → `e.request_id` un-joinable to the incident log |
| C2 `P2-XRID-success-header-ne-body` | LOW-obs | phase2 | 200 header ≠ `body.zeroshield.request_id`; SDK reads header fine → **observability-only**, not an SDK break |
| C3 `P2-XRID-RESP-header-ne-body-id` | MED | fp-hunter + runtime | shim overwrites the responses handler's `resp_…` header with `zs_…` → `r._request_id != r.id` |
| C4 `P2-XRID-RESP-block-header-ne-body` | MED | runtime | responses 403 header ≠ body `request_id` |
| C5 `P2-XRID-MOD-id-ne-header` | MED | runtime | moderations `modr-…` body id uncorrelated to header |
| C6 `P2-XRID-EMB-log-ne-header` | MED | runtime | embeddings logs under `zs-emb-…`, header is a 3rd id, success body has no `request_id` |

## Dim-4 streaming (3)
| id | sev | repro | note |
|---|---|---|---|
| S1 `EDGE-RESP-STREAM-DROPS-TOOLCALLS` | **HIGH** | edge | `/v1/responses` streaming silently drops tool/function calls (`_translate_chat_stream_to_responses` main.py:7870-7886 ignores `delta.tool_calls`); works non-stream + chat-stream |
| S2 `EDGE-USAGE-ORDER` | LOW | edge | `stream_options.include_usage`: usage chunk isn't the last frame before `[DONE]` (trace frame appended after) |
| S3 `P2-STREAM-upstream429-no-retryafter` | LOW | phase2/edge | non-stream upstream 429 passthrough omits `Retry-After` (main.py:6744); correctly scoped to non-stream |

## State (1)
| id | sev | repro | note |
|---|---|---|---|
| ST1 `P2-STORE-org-typecollision` | LOW | state | `ResponseStore` namespaces by `str()`-coerced `org_id`; latent int/str collision. **Not live today** (control plane emits int PKs consistently — proven); defense-in-depth: apply `coerce_org_id` like the vector path |

## Cosmetic / parity (2)
| id | sev | repro | note |
|---|---|---|---|
| X1 `P2-Dx-eparam-not-populated-chat-validation` | LOW | phase2 | chat 400s leave `e.param=None`; OpenAI allows null → SDK compliant; param-enrichment gap only |
| X2 `P2-CONTENT-part-validation-gap` | LOW | phase2 | file/audio/video parts admitted unvalidated; in-process harm unproven → defense-in-depth |

---

## DROPPED / RECLASSIFIED (not compat defects)
- `P2-RESP-N-dropped` → **FALSE POSITIVE.** Dropping `n` on Responses is *correct* (single-output API). Challenge test PASSES. **Dropped.**
- `P2-N-CHAT-clamp` + `EDGE-STREAM-N-EMPTYCHOICES` → **INTENTIONAL** single-choice output-guard security policy (choices[1..] would ship unscanned). SDK parses a valid ChatCompletion. Not a compat defect; surfacing the clamp is a **product decision**.

## NEEDS-LIVE (Phase 7)
- `REDIS_CLIENT=None` → `store=true` is a silent no-op (worker-restart store durability needs real redis).
- End-to-end `request_id` join across log→telemetry→audit (TELEMETRY stubbed in-process).

## CONFIRMED CLEAN (adversarially, with passing tests)
Cross-tenant `ResponseStore` isolation (4/4: retrieve/prev_id/delete/input_items all 404, no
cross-tenant delete-DoS) · single `POST /v1/responses` route (D1) · store durability across a
fresh `ResponseStore` over the same redis · **no `x-request-id` header drops on any of 12
surfaces** (Dim 6 holds everywhere) · stream edges: blocked-stream→JSON-403, terminal
`[DONE]` ordering, chat `delta.tool_calls` survive, mid-stream error→`APIError` · D1–D5 (Phase 0/1).

---

## Fix leverage (for Phase 5)
1. **SEAM-B (one widening, two layers):** add the dropped params to BOTH `_RESP_DIRECT_PASSTHROUGH` *and* `OPENAI_TOP_LEVEL_KEYS`; add explicit `text.format`→`response_format` + `truncation` translation in `responses_to_chat`. Fixes B1–B11.
2. **SEAM-C (canonical id):** set `request.state.gw_request_id` from the handler's `_REQUEST_ID` in proxy_chat/embeddings/responses/moderations, make `_build_zeroshield_metadata` derive from it, and have `_openai_compat_shim` PREFER a handler-set `x-request-id`. Fixes C1–C6 + the audit-join.
3. **SEAM-A:** validate `max_completion_tokens` with the same ceiling/sign rules and don't inject `max_tokens` when the client didn't send it; map `mct` correctly on the responses path. Fixes A1–A3.
4. **S1 (HIGH):** accumulate `delta.tool_calls` in `_translate_chat_stream_to_responses` and emit `response.function_call_arguments.delta/done` + a `function_call` output item.
