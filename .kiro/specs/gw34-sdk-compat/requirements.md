# Requirements Document

## Introduction

This feature implements card **GW34 (SDK and framework compatibility)** of the AI Mesh Firewall v2 backend rewrite (`gateway_v2/`). Runbook v3.1 §GW34 objective: *prove the clients customers actually use work against v2 itself, not a stub server*. Contract phase; depends on GW01 + GW15 (both done); required by GW21 and GW23. Tests L34-1 (each client's own test of chat, stream, tools and embeddings) and L34-2 (`/v1/files` is 404 with no SDK retry).

### What is already shipped (verified in the tree, NOT redone here)

- **Routing misses (GW36 S0, V-15):** an unknown path is `404 endpoint_not_found`, a known path with another method is `405` with `allow`, registered placeholders stay `501 not_implemented` (`edge/errors.py`, `edge/app.py`). GW34 only adds a regression cell for L34-2.
- **Chat/completions + completions surface (GW15, GW36 S1):** `edge/routes.py::ChatRoute`, `edge/openai_chat.py::ChatSurface`, `edge/wire/openai.py` (parser + `OpenAIResponseRenderer`), `edge/wire/openai_params.py` (forwarding allow-list), `edge/wire/openai_fold.py` (input fold + `UpstreamTemplate`), `edge/upstream_body.py`, `dispatch/openai_upstream.py`, `edge/wire/sse.py` (`SSEEncoder`/`SSEDecoder`).
- **Embeddings / models / moderations (GW16)** and **Responses (GW15 close)** exist and are exercised in `tests/openai_conformance/`.
- **Cancellation machinery (GW12):** `edge/cancel.py` (`CancellationController`, `KillLatch`), `edge/stream_control.py::FirstByteLatch`.

### Gaps found by reading the code (the reason this card has implementation work)

1. `edge/wire/openai_params.py::forwarded_fields` refuses with a declared 400 every field outside `_FORWARDED`/`_ACCEPTED`: `tools`, `tool_choice`, `parallel_tool_calls`, `functions`, `function_call`, `response_format`, `logprobs`, `top_logprobs`, `audio`, `modalities`, `prediction`, etc. (`unsupported_parameter`); messages with role `tool`/`function` or carrying `tool_calls`/`function_call` (`unsupported_parameter`, param `messages`); non-`text` content parts (`unsupported_content_part`).
2. `openai_fold.py` already folds `messages[].tool_calls[].function.arguments` and `messages[].function_call.arguments` as scanned channels, but `upstream_body.py` forwards only `{role, content}` per message, so a forwarded tool-call turn is impossible today.
3. **Streaming tool calls are silently dropped.** `SSEDecoder._deltas_from_choices` keeps only string-valued delta keys; `delta.tool_calls` (a list) is discarded. `finish_reason`, upstream `id`/`model`/`created` are discarded as well. `SSEEncoder` emits a fixed `chatcmpl-gateway`-style identity, always `finish_reason: null`, and one `choices[]` entry per delta (index = position in the frame), so a frame with two deltas becomes two "choices".
4. **Non-streaming tool calls are not SDK-valid.** `OpenAIResponseRenderer._message_for` renders one `tool_calls[0]` with only `function.arguments` (no `id`, no `function.name`), which the stock SDK models reject; parallel calls are merged into one.
5. **Output tool-call text has no per-call identity.** Egress scans channel-keyed text streams; `output_tool_arguments` exists in `domain/channels.py` but nothing produces it, and arguments of parallel calls would be concatenated into one stream.
6. **A streamed refusal before the first byte is an SSE error frame after HTTP 200.** `ChatRoute._render_refusal` (`req.streaming` branch) and `ChatRoute._stream_response` (coalescer refusal) call `_start_sse` and then `errors.render_sse(code)`; and `_stream`'s `_sink` writes `encoder.error(...)` after `_start_sse` even when no content byte has been released (upstream failure, first-window output BLOCK, kill/timeout before the first byte). OpenAI returns a plain HTTP error in these cases, and SDKs only apply their status-based error classes (and `maxRetries` policy) to a non-200 response (recorded in `AI_MESH_RUNBOOK_v3.1_PROGRESS.md`, "S5 / GW34").
7. The Node matrix (`tests/openai_conformance/node/openai_matrix.mjs`) runs against `stub_server.py`, not v2; there is no v2-driven L34-1 matrix. `openai`/`httpx` are not installed in the host Python (the gateway-v2 image has the `[dev]`+conformance extras), and no `node_modules` exists in the worktree.

### Scope boundaries

- **In scope:** the L34-1 matrix as offline tests (stock official Python and Node OpenAI SDKs where available locally; framework clients only through the same wire contract, see R2); implementation of tools/function calling, `response_format`, tool-role messages and non-text content decisions on the chat surface with scanning of every new channel in both directions; the pre-first-byte streamed-refusal fix; L34-2 regression cell; `design.md` recording each decision.
- **Out of scope:** the live/Docker/staging gate (L34-1 "against a real v2 boot" and L34-2 live) — shipped as code plus the offline matrix, never claimed or run (category-2 deferred live gate); 404/405 routing (GW36 S0); an Anthropic-style `/v1/messages` surface (runbook: "not in scope today" — kept out, recorded as a decision); audio/vision/file understanding; `n > 1` multi-choice rendering changes beyond not regressing; Responses-API tool calling; Azure-specific URL rewriting; embeddings to a real upstream (S1b); new capacity values outside `runtime/resources.py`.

## Glossary

- **Gateway**: the `gateway_v2` serving process.
- **L34-1 matrix**: the client compatibility matrix of R1–R2: (client × cell) offline tests.
- **Stock SDK**: the official `openai` Python package and `openai` npm package used unmodified: only `base_url`/`baseURL` and the API key differ from a call to `api.openai.com` (the GW15 LGW15-2 stop rule: no monkeypatch, no custom transport logic, no request/response shim).
- **Tool_Call_Fragment**: one element of `choices[].delta.tool_calls[]` in a streamed chunk: `{index, id?, type?, function:{name?, arguments?}}`.
- **Tool_Call**: the accumulated call for one `index`: `{id, type:"function", function:{name, arguments}}`.
- **Declared_Tool_Set**: the set of function names declared in the request `tools[]` (and named by `tool_choice`).
- **Pre_Byte_Refusal**: any gateway-detected terminal failure of a streaming request before the first response byte of the stream body (R7).
- **Declared refusal**: a 400 with a declared value-code naming the offending field (`unsupported_parameter` / `unsupported_content_part`), rendered by `edge/errors.py`, with zero provider calls.
- **FAIL_OPEN**: forwarding or releasing text that was not scanned/decided. Forbidden (pinned 0).
- Other terms (Input_Detector, OutputResolver, Error_Envelope, ResourceContract, FirstByteLatch, KillLatch) are as defined in `.kiro/specs/chat-surface-gw15/` and `chat-surface-gw16-misc/`.

## Requirements

### Requirement 1: L34-1 client compatibility matrix — Python and Node stock SDKs against v2

**User Story:** As a customer engineer, I want the official OpenAI SDKs to work against the Gateway with only a base URL and key swap, so that adopting the Gateway needs no client change.

#### Acceptance Criteria

1. THE matrix SHALL drive the genuine `gateway_v2.edge.app.build_app` ASGI app (not `stub_server.py`) behind the shipped `StubProviderClient`-style scripted provider, with the stock Python `openai` SDK (sync and async clients) over `httpx.ASGITransport`, constructed in exactly one helper that changes nothing but `base_url`, `api_key`, and `http_client`.
2. THE matrix SHALL contain, for the Python SDK, at least these cells, each asserting typed SDK parsing (no `dict` fallbacks, no `model_extra` reliance): (a) non-stream `chat.completions.create` → `ChatCompletion`; (b) `create(stream=True)` chunks, ending iteration at `[DONE]`; (c) `client.chat.completions.stream(...)` / `.parse` helper accumulation where the installed SDK offers it; (d) streamed fragmented tool calls accumulated to the exact `Tool_Call` (R4); (e) non-stream tool call → `message.tool_calls[0].function.name/arguments/id`; (f) client cancellation mid-stream (`stream.close()` and async context exit) (R6); (g) embeddings `create`; (h) responses `create` (+ `stream=True`); (i) error classes: blocked input → `BadRequestError`, bad key → `AuthenticationError`, unknown path (`/v1/files`) → `NotFoundError`, wrong method → `APIStatusError` 405; (j) the Pre_Byte_Refusal of R7 raising the status-based error class from `create(stream=True)` itself.
3. THE matrix SHALL contain the equivalent cells (a)–(j) for the stock Node `openai` SDK (pinned by `tests/openai_conformance/node/package.json`) by serving the same `build_app` on a loopback ephemeral port from the test process (no external network, no Docker), invoked via `node` as a subprocess, with the stream cancel cell using `stream.controller.abort()`.
4. IF `openai` (Python) or `httpx` is not importable, or `node` or the Node `openai` module is not resolvable offline, THEN the corresponding cells SHALL `skip` with a reason naming the missing dependency (`pytest.importorskip` / explicit skip), never install anything from the network and never fail the offline suite; the runner SHALL record in its report which cells ran and which skipped, so a skipped cell is never reported as passed.
5. THE matrix SHALL set `maxRetries`/`max_retries` to 0 on error-class cells, and SHALL assert that for `/v1/files` (L34-2) the gateway observed exactly one request (no SDK retry) and returned `404` `endpoint_not_found`.
6. THE matrix SHALL assert `[DONE]` is the final SSE event exactly once on every successful stream, that no `data:` event follows an error frame or `[DONE]`, and that the non-content `amf.trace` frame never reaches the SDK's content iterator as a chunk (since V13, 4 Nov 2026, the frame is an SSE comment line, `: amf.trace {json}`, rather than a named event).
7. THE matrix SHALL assert SSE/JSON framing facts the SDKs rely on: `Content-Type: text/event-stream` (streams) / `application/json` (non-stream and every pre-byte error), `x-request-id` present on every response, and a stable `id`/`model` across all chunks of one stream (R5.3).

### Requirement 2: Framework clients (LangChain, LlamaIndex, Vercel AI SDK, LiteLLM, Azure OpenAI)

**User Story:** As a customer using a framework on top of the OpenAI wire protocol, I want to know exactly which wire behaviours those clients depend on and have them proven, so that we do not discover incompatibilities in production.

#### Acceptance Criteria

1. THE matrix SHALL NOT install or import LangChain, LlamaIndex, Vercel AI SDK, LiteLLM, or the Azure client (none is installed locally; the offline gate forbids network installs). Instead it SHALL include a **wire-profile suite** replaying the request shapes those clients are documented to emit against the Gateway, using only stock-SDK or raw-`httpx` calls: `stream_options: {"include_usage": true}`; `tools` with `tool_choice: "auto"|"required"|{"type":"function",...}`; `parallel_tool_calls: false`; assistant+tool message round trips with `tool_call_id`; array-of-text-parts content; `user`, `metadata`, `seed`, `max_completion_tokens`; and an `api-key` header / `?api-version=` query on an Azure-style path.
2. FOR each wire-profile case the Gateway SHALL either serve it per R3–R5 or return a declared 400 naming the field (never a 5xx, never a silent drop); the case table SHALL list the framework(s) that emit it and the expected outcome so the expected diffs are enumerated, not implied.
3. THE Azure deployment-path form (`/openai/deployments/{d}/chat/completions`) and `api-key` header auth SHALL be recorded in `design.md` as **not supported** (404 `endpoint_not_found` on the Azure path; 401 on `api-key`-only auth) and covered by one assertion each, as a deliberate documented decision; the runbook lists the Azure client but defines no Azure surface contract.
4. THE real framework-library runs SHALL be recorded in `design.md` as a deferred live gate (they need network installs and a real v2 boot), not claimed.

### Requirement 3: Tools / function calling — request side (forwarded and scanned)

**User Story:** As an agent developer, I want `tools`, `tool_choice` and tool-result messages to work through the Gateway, with every text in them scanned, so that agent frameworks function without opening an unscanned channel.

#### Acceptance Criteria

1. THE Gateway SHALL accept and forward `tools` (each `{type:"function", function:{name, description?, parameters?, strict?}}`), `tool_choice` (`"none"|"auto"|"required"|{type:"function",function:{name}}`) and `parallel_tool_calls` (bool), validated before forwarding: `type` must equal `"function"`; `name` must match `^[A-Za-z0-9_-]{1,64}$`; `tools` length ≤ a bound read from `ResourceContract` (no literal elsewhere); `parameters` must be a JSON object whose nesting depth and total node count are bounded by `ResourceContract`; a `tool_choice` function name must be in the Declared_Tool_Set. Any violation → declared 400 `malformed_wrong_shape` with `param` naming the field, zero provider calls.
2. THE Gateway SHALL scan, as new input channels registered in `domain/channels.py` (scope prefix `tools[]`, `scanned=True`), every free-text value of a tool definition: the function `description` and every `description`/`title`/`enum`/`const`/`default`/`examples` string value and every property-name key inside `parameters`. The fold SHALL be total over them, extending `openai_fold._assert_fold_total` (a channel with no fold path fails the build, C21).
3. THE Gateway SHALL accept messages with role `tool` carrying `tool_call_id` (string, `^[A-Za-z0-9_.:-]{1,64}$`) and string/text-part `content`, scanned through the existing `message_content` / `content_part_text` channels; and assistant messages carrying `tool_calls` (`id`, `type:"function"`, `function.name`, `function.arguments`) with `content` possibly `null`.
4. A `tool` message whose `tool_call_id` does not match an `id` of an earlier assistant `tool_calls[]` in the same request, and an assistant `tool_calls[]` entry whose `function.name` is not in the Declared_Tool_Set (when `tools` is present), SHALL be refused with declared 400 `malformed_wrong_shape` (`param: "messages"`), zero provider calls (fail closed: the Gateway never forwards a tool turn it cannot tie to a declaration).
5. `function.arguments` of assistant `tool_calls` SHALL be scanned on BOTH the raw string and its decoded form (JSON string values decoded, span-mapped back to the raw bytes, runbook C21), through the existing `tool_arguments` channel. IF `arguments` is not valid JSON, THEN the request SHALL be refused with declared 400 `malformed_wrong_shape` (`param: "messages"`), zero provider calls (see Ambiguity A5).
6. WHEN input enforcement redacts a value inside `tool_calls[].function.arguments` or a tool definition, THE forwarded text SHALL be the byte-verified masked delta (existing `stage_upstream` + `upstream_body` slot discipline); AND IF the masked `arguments` is no longer valid JSON, THEN the request SHALL be refused `input_unmaskable` (never forwarded as broken JSON, never forwarded raw).
7. WHEN a BLOCK finding exists in any new input channel, THE Gateway SHALL refuse with the existing `input_blocked` code and make zero provider calls (C22 most-restrictive; REDACT never cancels BLOCK).
8. `upstream_body.py` SHALL render every forwarded tool field from the validated parsed structure (an allow-list copy: `tools`, `tool_choice`, `parallel_tool_calls`, and per-message `tool_calls`/`tool_call_id`/`name` of the tool message), never by copying the client body through; all scanned text enters the rendered body only as a verified slot delta.
9. THE deprecated `functions`, `function_call` request fields and the `function` message role SHALL remain declared refusals (`unsupported_parameter`); the assistant `function_call` message field SHALL remain refused. Decision recorded (Ambiguity A3).
10. `name` on `system`/`user`/`assistant` messages SHALL remain accepted-but-not-forwarded (free text outside scan coverage), unchanged from GW36 S1.

### Requirement 4: Tools / function calling — response side, streaming and non-streaming

**User Story:** As an agent developer, I want streamed and non-streamed tool calls returned exactly as the model produced them, so that the SDK reconstructs the exact call, while the Gateway has scanned every byte of arguments.

#### Acceptance Criteria

1. `SSEDecoder` SHALL parse `choices[].delta.tool_calls[]` fragments and `finish_reason` instead of discarding them; a `tool_calls` value that is not a list of objects with an integer `index`, or a fragment with a non-string `id`/`name`/`arguments`, SHALL set `malformed()` (fail closed `stream_malformed_upstream`), never be silently dropped.
2. Egress SHALL scan tool-call arguments **per call**: each `index` has its own text stream (channel `output_tool_arguments` with a per-index scan key), so parallel calls are never joined into one scanned string, and a deletion/redaction decision for one call never alters another. The base channel name used for rule and disposition lookup SHALL remain `output_tool_arguments`.
3. Output scanning of arguments SHALL cover the raw text and the decoded-JSON-string form (R3.5 symmetric). For each call, the `id` SHALL match `^[A-Za-z0-9_.:-]{1,64}$` and the `name` SHALL be in the Declared_Tool_Set (when the request declared tools; a response tool call for a request with no `tools` is refused); a violation SHALL terminate the response `output_blocked` (HTTP 403 if no byte has been released, R7) with zero bytes of that call released.
4. WHEN an enforcing output rule applies to `output_tool_arguments`, arguments SHALL NOT be released incrementally: each call's arguments are withheld until that call's `arguments` is complete (next `index` begins, `finish_reason` arrives, or the stream ends), scanned (raw + decoded), then released as ONE `Tool_Call_Fragment` (REDACT applied, BLOCK terminates). WHEN no enforcing output rule applies, fragments SHALL be forwarded in the order and boundaries received. In both cases the concatenation of the released `arguments` fragments for one `index` SHALL equal the (possibly redacted) arguments. If redaction makes the arguments invalid JSON, the response SHALL be terminated `output_blocked` instead.
5. The released stream SHALL carry, per call, `index` and `id`/`type:"function"`/`function.name` on the first fragment of that index only; `finish_reason` `"tool_calls"` | `"stop"` | `"length"` | `"content_filter"` propagated from upstream on the final choice chunk (an unrecognised value → `stream_malformed_upstream`); `finish_reason` is `null` on every earlier chunk.
6. THE non-streaming renderer SHALL render `message.tool_calls[]` as an array of complete `Tool_Call` objects (`id`, `type`, `function.name`, `function.arguments`), one per upstream `index`, in `index` order, with `content: null` when the model emitted no content, `finish_reason` from upstream; the stock Python and Node SDK SHALL parse it to typed tool-call objects.
7. THE fragment-ordering property SHALL hold: for any partition of any arguments string into fragments and any interleaving of parallel calls' fragments, the SDK-accumulated `Tool_Call`s equal the originals (property test, R10).
8. A BLOCK/withhold of tool-call output SHALL never leave a partially-valid call visible: a call is either fully released or none of its arguments are (when R4.4 withholding applies), and `finish_reason` is never `"tool_calls"` on a terminated stream.
9. THE `tool_choice: "none"` request SHALL still scan any tool call the upstream returns (the Gateway does not trust the request to prevent output); `parallel_tool_calls: false` with more than one returned call SHALL be forwarded as returned (not enforced by the Gateway; recorded in `design.md`).

### Requirement 5: Chunk framing fidelity

**User Story:** As an SDK user, I want the streamed chunks to carry the stable identity and shape the SDK expects, so that SDK stream helpers, accumulators and framework adapters work.

#### Acceptance Criteria

1. `SSEEncoder` SHALL emit one `choices[]` entry per upstream choice `index` per frame, with all of that frame's deltas for that choice merged into ONE `delta` object (e.g. `{content, reasoning_content}` in a single object), instead of one entry per delta position.
2. Every chunk of one stream SHALL carry the same gateway-minted `id` (`chatcmpl-` + the per-request id) and the requested `model`, plus `created` read from the injected clock; `delta.role: "assistant"` SHALL appear on the first chunk only.
3. A final chunk SHALL carry `finish_reason` (R4.5; `"stop"` default for a clean text completion) and an empty `delta`, before `[DONE]`.
4. `stream_options.include_usage` SHALL remain accepted and not forwarded; no usage chunk is emitted (Ambiguity A6); a `usage: null` field SHALL NOT break the SDK.
5. THE unchanged pass-through channels (`content`, `reasoning_content`, `refusal`) SHALL keep working; `refusal` delta SHALL render as `delta.refusal` and be scanned on `output_refusal`.

### Requirement 6: Cancellation

**User Story:** As a client developer, I want closing a stream to stop the upstream call and release the Gateway's resources, so that abandoned streams do not cost money or capacity.

#### Acceptance Criteria

1. WHEN the Python SDK stream is closed (`stream.close()`, leaving an `async with` block, `break` + `close`) or the Node SDK aborts (`stream.controller.abort()` / `AbortController`), THEN within the contract `cancellation_bound` (from `ResourceContract`, no literal in the test or code) the provider iterator SHALL be closed, the admission slot and budget lease holds SHALL be released (`RequestHolds.release`), and an audit record with the cancelled outcome SHALL be emitted — asserted for both a stream blocked waiting for the next upstream event and a stream actively releasing frames.
2. AFTER a cancelled stream, a second request on the same app instance SHALL succeed (no leaked slot), asserted by the matrix.
3. CANCELLATION SHALL NOT produce an SSE error frame or a second terminal frame to the (departed) client; `FAIL_OPEN` SHALL stay 0.
4. A cancellation DURING a tool-call stream SHALL release no half-withheld call (R4.4/R4.8) and SHALL close the upstream.

### Requirement 7: A refusal before the first byte is a plain HTTP error, not an SSE error frame after 200

**User Story:** As an SDK user, I want a request the Gateway refuses before streaming starts to fail like any OpenAI error, so that the SDK's status-based errors and retry rules apply and `create(stream=True)` raises.

#### Acceptance Criteria

1. WHEN a streaming request (`stream: true`) is refused by any lifecycle phase after parse (admission shed, budget, plan, input scan BLOCK/unmaskable, tools validation, surface refusal), THEN the Gateway SHALL answer with the same plain HTTP response a non-streaming request gets for that code (same status, same JSON `error` body, `x-request-id`, `Retry-After` where the code defines it, console projection headers), `Content-Type: application/json`, and SHALL NOT send `200`/`text/event-stream`.
2. WHEN the coalescer refuses admission (`STREAM_BUFFER_UNAVAILABLE`) before any stream byte, THEN it SHALL be a plain HTTP error likewise (R7.1 response shape).
3. WHEN a gateway-detected terminal failure occurs on a proceed outcome before the first content byte is written — upstream non-2xx / unavailable / timeout / malformed first event (`gateway_error`), a provider `error_frame` before any content, an output BLOCK/scan failure on the first releasable window, a kill/timeout before the first byte, or a tool-call validation failure (R4.3) — AND the no-splice orchestration has exhausted its eligible attempts, THEN the Gateway SHALL answer with the plain HTTP error for that code (status from `edge/errors.py`), starting the HTTP response only when the first frame or the terminal failure is known.
4. The response start SHALL be deferred only up to the first stream frame; time-to-first-byte of a healthy stream SHALL NOT increase (the first byte is still the first released frame) and the deferral SHALL NOT be a timer.
5. AFTER the first content byte has been written (latch set), terminal failures SHALL remain declared SSE `Error_Frame`s followed by no further content (unchanged); a stream that ends cleanly with no content SHALL still be `200` + `[DONE]`.
6. THE `amf.trace` frame SHALL NOT be emitted on a Pre_Byte_Refusal; the console/trace information SHALL travel on the HTTP projection headers as for non-streaming refusals; the audit record SHALL carry the same `refused` code as today.
7. THE Responses stream (`openai_responses.py`, which reuses the chat stream) SHALL NOT change behaviour or regress its existing tests; if it shares the fixed code path, its pre-byte refusal becomes plain HTTP as well and its tests are updated accordingly (Ambiguity A7).
8. THE existing tests that assert the old SSE-after-200 behaviour for pre-byte refusals SHALL be updated to the new contract in the same change, each named in the task list.

### Requirement 8: `response_format`

**User Story:** As a developer using structured outputs, I want `response_format` to work, with the schema scanned and output redaction never silently breaking JSON validity.

#### Acceptance Criteria

1. THE Gateway SHALL accept and forward `response_format` of type `text`, `json_object` and `json_schema` (`json_schema:{name, schema, strict?, description?}`), validated: `type` in that set; `name` matches `^[A-Za-z0-9_-]{1,64}$`; `schema` a JSON object within the same depth/node bounds as R3.1. Any other type or shape → declared 400 `malformed_wrong_shape`, `param: "response_format"`.
2. THE schema free-text values (`description`, `title`, `enum`/`const`/`default`/`examples` strings, property names) and `json_schema.description` SHALL be scanned through the R3.2 tool-definition channel family (one new channel `response_format_schema` registered in `domain/channels.py`) and forwarded as verified deltas, same as R3.6.
3. WHEN the request has `json_object` or `json_schema` and output REDACT applies to the response `content`, THE released content SHALL still be valid JSON; WHERE the enforced redaction would make it invalid JSON, THE Gateway SHALL terminate/withhold with `output_blocked` (fail closed; no schema-aware repair). Content for these formats SHALL be withheld until complete (R4.4 semantics) when an enforcing output rule applies, otherwise streamed incrementally.
4. THE existing channel `structured_output` (`choices[].message.content json_object`) SHALL be the scan label for this content, so `unscanned_channels()` stays empty.

### Requirement 9: `logprobs`, `top_logprobs`, non-text content parts, and other still-refused fields

**User Story:** As a security engineer, I want every field the Gateway cannot scan to stay a declared refusal, so that no unscanned channel opens as a side effect of compatibility work.

#### Acceptance Criteria

1. `logprobs` and `top_logprobs` SHALL remain declared 400 `unsupported_parameter` (named field), because logprob tokens and bytes are a text channel that the output pipeline cannot redact consistently with `content` (runbook v2.1 C4/C21: "logprobs=true defeats output REDACT"). `channels.py` keeps the `logprobs` channel entry as the standing reason; a request with `logprobs: false` or `null` SHALL be accepted and not forwarded.
2. Content parts of type `image_url`, `input_audio`, `file`, and `refusal`, and request fields `audio`, `modalities`, `prediction`, `web_search_options`, and any other field outside the accepted set of R3/R8 and today's `_ACCEPTED`/`_FORWARDED`, SHALL remain declared 400 `unsupported_content_part` / `unsupported_parameter` naming the field (runbook C21: unscannable parts need an explicit plan posture; the chosen posture is **reject**, never allow-with-SKIPPED).
3. EVERY currently-refused field not listed in R3, R8 SHALL stay refused with unchanged codes; a table-driven test SHALL pin the full accepted set and the refused set so adding a field to the accepted set without scanning fails the build.
4. A refusal SHALL echo only a field name matching the existing `_ECHOABLE_PARAM` shape, never client text.

### Requirement 10: Verification gates and properties

**User Story:** As the card owner, I want the card to exit on the same offline gates as every shipped card, without claiming a live gate.

#### Acceptance Criteria

1. THE change SHALL pass, unchanged in configuration: the full offline `pytest` run (container-run style, as the other cards), `mypy --strict`, `ruff`, `import-linter` (bottom-up layering preserved; `edge/wire` stays pure and the only OpenAI-shape renderer; HTTP objects only in `edge`), and the AST gates `check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_tenant_scale`, `check_sizes` over `gateway_v2`, with no new baseline entries.
2. NO capacity-position literal SHALL be added outside `runtime/resources.py`; every new bound (tool count, schema depth/nodes, tool-call count per response, arguments bytes per call) SHALL be a derived `ResourceContract` method with a documented multiplier and a fail-closed refusal when unset.
3. NEW value types SHALL be `@dataclass(frozen=True, slots=True)`; NO module-level mutable state; new constants are `frozenset`/`MappingProxyType`/tuples.
4. Property tests, each at least 100 seeded iterations (`random.Random(seed)`, house idiom, no hypothesis) and tagged in-source with `# Feature: gw34-sdk-compat, Property N: ...`, SHALL cover: (P1) tool-call fragment partition/interleave accumulation identity (R4.7); (P2) fold totality: every `TEXT_CHANNELS` input channel appears in the fold and every folded channel in the table (R3.2); (P3) no unscanned byte reaches the provider: every string leaf of the rendered upstream body is a verified slot delta or a validated structural token (R3.8); (P4) redaction never yields invalid JSON arguments/`json_*` content that is released (R3.6, R4.4, R8.3); (P5) Pre_Byte_Refusal: for any refusal code × stream flag, the response is HTTP and never begins with `200`+`text/event-stream` before the first byte (R7); (P6) SSE encode→decode round trip including tool-call fragments and `finish_reason`; (P7) parallel-call isolation: redacting call A never changes call B's bytes (R4.2); (P8) the accepted/refused parameter partition is exhaustive over a generated field-name corpus (R9.3).
5. EVERY refused path (R3.4, R3.6, R3.7, R4.3, R9) SHALL assert zero provider calls, and every output termination asserts zero released bytes of the offending call.
6. THE change SHALL NOT claim L34-1 "passes against a real v2 boot" or L34-2 live: the card's exit note SHALL state the offline matrix result and that the live Docker/staging run, the framework-library runs and the Node-vs-real-TCP run remain deferred.

### Requirement 11: Observability and audit

#### Acceptance Criteria

1. NEW refusals and terminations SHALL use existing declared value-codes where they exist (`input_blocked`, `input_unmaskable`, `output_blocked`, `unsupported_parameter`, `unsupported_content_part`, `malformed_wrong_shape`, `stream_malformed_upstream`); any new code SHALL be added to `edge/errors.py` with a declared status/type/message and a test, and SHALL NOT echo client or model text.
2. THE audit record and `PerRequestExports` SHALL record tool-call presence as a count only (`tool_calls_released`, `tool_calls_withheld`), with NO label derived from tool name, id or arguments (no tenant/content-derived label, GW14d discipline); counts SHALL be seeded zero.
3. NO logging of tool arguments, tool definitions, or schema text at any level.

### Requirement 12: Documentation of decisions

#### Acceptance Criteria

1. `design.md` SHALL record each decision with the option chosen and why, at minimum: Anthropic `/v1/messages` (out of scope), Azure paths (not supported), deprecated `functions`/`function_call` (refused), `logprobs` (refused), non-text parts (reject), `stream_options.include_usage` (not forwarded), per-index tool scanning and withhold-then-release default, `tool_choice:none` handling, Node SDK harness (loopback), skip policy for absent SDKs, deferred live gates.

## Assumptions and open ambiguities (chosen defaults — conservative, fail-closed)

- **A1 — Which SDKs run locally.** Host Python has no `openai`; no Node `node_modules`. Default: matrix cells `importorskip`/skip with an explicit reason and run in the gateway-v2 container image or any env that has the pinned SDKs; the report distinguishes ran/skipped (R1.4). Never network-install.
- **A2 — Framework clients (LangChain, LlamaIndex, Vercel AI SDK, LiteLLM, Azure).** Default: wire-profile suite instead of library installs (R2); real library runs deferred as a live gate.
- **A3 — Deprecated `functions`/`function_call`/role `function`.** Runbook is silent. Default: keep the declared refusal (unscanned-path risk, deprecated by OpenAI); revisit only if a customer needs it.
- **A4 — `logprobs`.** Runbook requires scanning logprobs tokens+bytes if forwarded and records the REDACT bypass. Default: keep the declared refusal (R9.1).
- **A5 — Invalid-JSON `arguments` in assistant history.** Default: refuse `malformed_wrong_shape` (OpenAI itself rejects non-JSON arguments on replay).
- **A6 — `stream_options.include_usage`.** Default: accepted, not forwarded, no usage chunk (as shipped). Some frameworks show null token usage; recorded as a known diff.
- **A7 — Responses stream and the pre-byte fix.** Default: change the shared chat stream path; if Responses inherits plain-HTTP pre-byte errors that is accepted (it matches OpenAI) and its tests are updated.
- **A8 — Output tool-call withholding vs. fragment fidelity.** Runbook asks that fragment order is not corrupted; with an enforcing rule fragments are merged into one per call (concatenation preserved). Default: withhold-then-release only when an enforcing output rule applies, else pass-through with original boundaries.
- **A9 — `parallel_tool_calls:false` and multiple returned calls.** Default: forward as returned, not enforced.
- **A10 — `n > 1`.** Default: unchanged; tool calls and `finish_reason` only for choice 0 / the shipped single-choice rendering; no regression.
- **A11 — Anthropic-style `/v1/messages`.** Default: out of scope (runbook: "not today"); a request to it stays `404 endpoint_not_found`.
- **A12 — Declared-tool-name enforcement on output.** Default: a tool call whose name is not declared is treated as `output_blocked` (a hallucinated/injected tool name must not reach an agent that executes by name).
