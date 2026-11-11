# Design Document: GW34 SDK and framework compatibility

## Overview

GW34 proves that the clients customers use work against `gateway_v2` itself. Reading the code shows that today they cannot for any agent workload: `edge/wire/openai_params.py` refuses `tools`, `response_format`, tool-role messages and tool-call history; `SSEDecoder` drops `delta.tool_calls` and `finish_reason`; `SSEEncoder` emits a fixed identity and one `choices[]` entry per delta; the non-stream renderer emits a tool call with no `id` or `name`; and a refused or failed streaming request is answered `200 text/event-stream` plus an SSE error frame, so the SDK never raises its status-based error class and never applies its retry policy.

The design makes four changes and one proof:

1. **Request side.** `tools`, `tool_choice`, `parallel_tool_calls`, `response_format`, tool-role messages and assistant `tool_calls` history are validated, folded into the scanned input channels, and rendered upstream only from verified slot deltas. Everything the gateway cannot scan stays a declared refusal.
2. **Response side.** Streamed tool calls are decoded per `index`, each call's arguments get their own scan stream, an edge-side `ToolCallAssembler` validates and re-frames them, and `finish_reason` travels end to end. The non-stream renderer produces complete SDK-valid `tool_calls`.
3. **Chunk framing.** One stable `id`/`model`/`created` per stream, `delta.role` on the first chunk only, merged deltas per chunk, and a final `finish_reason` chunk.
4. **Pre-first-byte refusals** become plain HTTP errors (same bytes as the non-stream refusal), by deferring the SSE response start to the first frame.
5. **Proof.** An offline L34-1 matrix drives the genuine `build_app` with the stock Python and Node `openai` SDKs, plus an L34-2 cell and a wire-profile suite for framework clients.

The existing layering stays: `edge/wire` stays pure and is the only OpenAI-shape renderer; HTTP objects exist only in `edge`; `egress` never imports `detect`; bounds come from `ResourceContract`; value types are frozen and slotted; nothing is mutable at module level. No source is edited by this document; it is the plan for the implementation tasks.

### Technology stack (locked on approval)

Python 3.12 (as in `pyproject.toml`), stdlib `json`/`re`/`asyncio`/`dataclasses`, the existing `pytest` + seeded `random.Random` property idiom (no hypothesis), `mypy --strict`, `ruff`, `import-linter`. Tests use the stock `openai` Python SDK and `httpx` (only when importable), the pinned Node `openai` package (`tests/openai_conformance/node/package.json`) via `node` subprocess, and `uvicorn` (already a `[serve]` dependency) for loopback TCP cells. No new runtime dependency is added to `gateway_v2`.

## Decisions

Each decision names the option chosen and why. Ambiguities take the conservative fail-closed option and never become "block everything".

| # | Topic | DECISION | Reason |
|---|---|---|---|
| D1 | `tools` / function calling | **Forward**, after validation, with every free-text value scanned and rendered from verified deltas. `tool_choice` and `parallel_tool_calls` forwarded as validated structure. | Agent frameworks cannot work with a declared refusal. All text is scanned, so no unscanned channel opens. A refusal stays the fallback for any shape that cannot be classified (D2 below). |
| D1a | Unknown JSON Schema keyword inside `parameters` | **Refuse** `malformed_wrong_shape` (`param: tools`). Keywords come from the closed table in "Schema keyword table". | A key outside the known set is free text the gateway cannot classify as structural or scanned. OpenAI itself only honours a subset. |
| D1b | Deprecated `functions` / `function_call` / role `function` | **Stay refused** (`unsupported_parameter`). The same applies upstream: a legacy `finish_reason: "function_call"` or `delta.function_call` from an older provider is rejected as `stream_malformed_upstream`, deliberately. | Deprecated by OpenAI, no customer need recorded (A3). Keeps the scanned surface small. |
| D2 | `response_format` | **Forward** `text`, `json_object`, `json_schema`. Schema text scanned through one new channel. Output validity guard for JSON modes (D9). | Structured outputs are common in SDK use (`.parse`). |
| D3 | `logprobs`, `top_logprobs` | **Stay refused** `unsupported_parameter`; `false`/`null` accepted and not forwarded. | The logprob token and byte arrays are a text channel the output pipeline cannot redact consistently with `content` (runbook C4/C21: logprobs defeats output REDACT). |
| D4 | Tool-role messages and assistant `tool_calls` history | **Accept** and forward. `tool` content goes through `message_content`/`content_part_text`. Each `tool_call_id` must match an earlier assistant `tool_calls[].id` in the same request. Each assistant call `name` must be in the declared set when `tools` is present. Arguments must be valid JSON. | A tool turn that cannot be tied to a declaration is never forwarded. When `tools` is absent the name tie is skipped (R3.4 literal), the id tie still applies. |
| D4a | Assistant arguments scan | The **forwarded** text is the canonical decoded re-serialisation of the parsed arguments. The **raw** string is also scanned as a scan-only pair. | Scanning only the canonical form removes the need to map spans from decoded text back to raw bytes. Raw bytes are never forwarded, so no raw escape sequence can smuggle a value past the scan. See "Arguments canonicalisation". |
| D5 | Non-text content parts (`image_url`, `input_audio`, `file`, `refusal`) and `audio`, `modalities`, `prediction`, `web_search_options` | **Stay refused** (`unsupported_content_part` / `unsupported_parameter`). | Runbook C21: unscannable parts need an explicit posture. The posture is reject, never allow-with-SKIPPED. |
| D6 | Bidirectional tool-call argument scanning | **Request:** D4a. **Response:** a per-call scan stream `output_tool_arguments#<index>` in egress, then an edge assembler that (a) validates id/name/JSON, (b) scans the decoded form of the released text, (c) withholds each call until complete when an enforcing output rule applies. | See "Response side". Streaming deltas split across chunks are covered because the scan key is the call index, not the chunk. |
| D6a | Withhold release point | With an enforcing output rule, **all calls release only when `ToolCallAssembler.finish()` is called, in index order, one fragment each.** `finish()` is invoked by `ChatStream._attempt()` after `await pipeline.run(...)` has returned cleanly, i.e. after egress has flushed every held tail. `finish_reason` and "the next index begins" are NOT release points: `finish_reason` is a meta channel that reaches the sink before egress flushes held argument tails on `chunk.final`. | Releasing earlier would see truncated arguments (false `output_blocked`) or release a partial call followed by a late fragment. An explicit end-of-stream call is correct for sequential and interleaved streams. Cost: tool-call latency to end of stream only under an enforcing output rule. |
| D6b | Without an enforcing output rule | Fragments forwarded with the boundaries received. | Nothing can be redacted, so incremental release is safe. |
| D6c | `tool_choice: "none"` | Output tool calls are still scanned and validated. | The gateway never trusts the request to prevent output. |
| D6d | `parallel_tool_calls: false` with several returned calls | Forwarded as returned, not enforced. | Documented known diff (A9). |
| D7 | Pre-first-byte refusal/failure | **Plain HTTP error** with the exact non-stream bytes, via a deferred SSE start (`edge/stream_response.py`). "First byte" means the first frame carrying a non-empty content, reasoning, refusal or tool-call delta (D21); role-only, empty-text and meta-only frames are held. After the first content byte the behaviour is unchanged (SSE `Error_Frame`). | OpenAI behaviour. SDKs apply status classes and `maxRetries` only to non-200. |
| D7a | Responses stream | **Unchanged behaviour.** `ChatRoute._stream_response(..., plain_pre_byte=False)` keeps the eager SSE start for `openai_responses.py`. | Its `forward` wrapper starts its own SSE and translates chat bytes. Feeding it a JSON error body would corrupt the stream. A plain-HTTP Responses pre-byte error is backlogged (R7.7 allows either). |
| D8 | L34-1 SDK matrix | Python SDK (sync + async) and Node SDK against the genuine `build_app`. Non-streaming and error cells use `httpx.ASGITransport`. **Streaming and cancel cells use a loopback uvicorn server on an ephemeral port.** Node always uses loopback. Cells skip with a named reason when a dependency is absent, a report lists ran vs skipped per client, and the card does NOT close while the Python SDK or Node loopback cells have a ran count of 0 (see "SDK execution environment"). | `httpx.ASGITransport` runs the app to completion and returns a buffered body, so it cannot prove incremental SSE, time to first byte or mid-stream cancel. This deviates from R1.1's wording for those cells. A probe test pins the behaviour so the deviation is evidenced, not assumed. |
| D9 | Output validity for JSON modes | When the request has `json_object`/`json_schema` and an enforcing output rule applies, `content` is withheld to completion (bounded by `ToolBounds.max_withheld_bytes`, over it `output_blocked`) and must `json.loads` after redaction, else `output_blocked`. | Fail closed. No schema-aware repair. |
| D10 | `stream_options.include_usage` | Accepted, not forwarded, no usage chunk. | Shipped behaviour (A6). Known diff for frameworks that read usage. |
| D11 | Anthropic `/v1/messages` | Out of scope, stays `404 endpoint_not_found`. | Runbook: not in scope today. |
| D12 | Azure paths | `/openai/deployments/{d}/chat/completions` stays 404; `api-key`-only auth stays 401. One assertion each. | The runbook lists the Azure client but defines no Azure surface. |
| D13 | Framework clients | Not installed. A **wire-profile suite** replays the documented request shapes with raw `httpx`/stock SDK calls. Real library runs are a deferred live gate. | The test RUN is offline (no network at run time). The SDK image BUILD needs the network and is a separate, preflighted step (see "SDK execution environment"). |
| D14 | Cancellation | Add a **disconnect watcher** that cancels the **inner stream task** (never the handler task) when ASGI reports `http.disconnect` before the stream completes. Provider iterator, coalescer and holds are released by `finally` blocks. `CancellationController` stays a GW12 harness object. Watcher cancel versus server cancel is told apart by `inner.cancelled() and current_task().cancelling() == 0` (D22). | `ChatRoute.handle` never reads `receive` after the body, and uvicorn discards `send` after a disconnect, so today an abandoned stream keeps running. |
| D15 | Missing `created` clock | New `ChatRoute.wall_clock_s: Callable[[], float] = time.time`. | `ChatRoute.clock` is the monotonic stream clock, not epoch time. A separate injected wall clock keeps tests deterministic. |
| D16 | Upstream repeats `id`/`name` on every fragment | The decoder keeps the first value per index and drops an equal repeat. A differing repeat is `stream_malformed_upstream`. | The stock SDK concatenates string deltas. A repeated name would corrupt the call. |
| D17 | Tool-call `n > 1` | Unchanged. `max_completions()` forces `n = 1`, so there is exactly one choice. | A10. |
| D18 | Undeclared tool name in a response; any tool call when the request had no `tools` | `output_blocked`. | A12. A hallucinated or injected name must not reach an agent that executes by name. |
| D19 | Upstream structural bound breach (index out of range, too many calls, arguments over the byte bound) | `stream_malformed_upstream`. | It is a malformed upstream, not a policy decision. |
| D20 | C26 part-count pre-check with tools | `max_request_parts()` is UNCHANGED. The tools ceiling is `max_request_parts() + ToolBounds.parts_allowance`, computed in `_pre_checks` from the ROUTE-level resolved `self.tool_bounds` (no second contract method, review R5-4); it applies only when `self.tool_bounds` is not `None` and the raw body contains the bytes `tools`, `tool_calls` or `response_format`. A route with `tool_bounds is None` always uses `max_request_parts()`. The byte-level `_part_count` stays unchanged and still refuses a flood. | `_part_count` counts every `{` and `,` byte, so 10 to 20 tools with pydantic schemas exceed 256 and were refused before any new code ran. The allowance is derived from the schema-node bound already enforced later, so the total request still cannot exceed a contract-derived limit. |
| D21 | Definition of first byte | A frame is a first-byte frame only if it carries at least one non-empty `content`, `reasoning_content`, `refusal` or tool-call delta. | R7.3 says first content byte. OpenAI-style upstreams open with an empty-content role chunk. |
| D22 | Watcher cancel vs server cancel | Discriminator `inner.cancelled() and asyncio.current_task().cancelling() == 0`. | Python 3.12 is the stack. Both cancels surface as `CancelledError` at the same `await`. |
| D23 | Names and ids with a REDACT-class finding | Escalate to BLOCK through span-to-pair attribution (see "BLOCK-only attribution"). | A pooled `Decision` has no channel. |
| D24 | Scan-slot budget | New `ToolBounds.max_scanned_slots`, derived from the p99 budget. Folded tool/schema/argument slots above it are refused `malformed_wrong_shape`. | Each string leaf is a separate detector call. Bytes alone do not bound the cost. |
| D25 | Lone surrogates, `1e400` | Refused `malformed_wrong_shape`, `param: messages`. | `json.dumps` would emit `Infinity`; a surrogate fails later at UTF-8 encode as a 500. |
| D26 | Upstream choice with `index >= 1` | Ignored (not forwarded, not scanned) when the request forced `n = 1`; a choice whose `index` is not a non-bool int is `stream_malformed_upstream`. | The gateway only ever asks for one choice (D17); an extra choice is never client-visible. Ignoring is safe because nothing from it reaches the client. |
| D27 | Scan-only pairs and cost | Scan-only pairs (`forwarded=False`) are excluded from `prompt_chars` and the token estimate. | They are never sent upstream, so they must not inflate budget or billing estimates. |

## Architecture and file plan

New and changed files, by layer (bottom up). Sizes matter: `lint/check_sizes.py` fails a module over 800 lines or a function over 120. Today `edge/routes.py` is 799 lines, `runtime/resources.py` 800, `edge/wire/sse.py` 772 and `edge/openai_chat.py` 712, so splitting is part of the work, not an option.

### `domain`

- `domain/channels.py`: add input channels and one constant.
  - `tool_description` (`tools[].function.description`), `tool_schema_text` (`tools[].function.parameters`), `response_format_schema` (`response_format.json_schema`), `tool_arguments_raw` (`messages[].tool_calls[].function.arguments raw`). All `scanned=True`.
  - `tool_name` (every tool/function name on the request side: `tools[].function.name`, assistant `tool_calls[].function.name`, `tool_choice.function.name`) and `tool_call_id` (assistant `tool_calls[].id`, tool-message `tool_call_id`), `scanned=True`, **BLOCK-only** (see "Tool names and ids").
  - `tool_schema_struct` (review R4-1): the per-tool and per-`response_format` joined run of every *structural* schema string (property names, `$defs`/`definitions` keys, `required` entries, `title`, `$ref`, `$anchor`, `$id`, `$schema`, `format`, `pattern`, `$comment`, and `enum`/`const` string members). `scanned=True`, **BLOCK-only**, scan-only (`forwarded=False`). Never redacted.
  - `output_tool_id`, `output_tool_name` (`choices[].message.tool_calls[].id` / `.function.name`), `scanned=True`.
  - `META_CHANNELS: frozenset[str] = frozenset({"finish_reason"})`. A meta channel is not text. Its value is checked by the decoder against a closed set before it enters the pipeline, so `unscanned_channels()` stays empty.
- `domain/posture.py`: no new code. All refusals reuse `malformed_wrong_shape`, `unsupported_parameter`, `unsupported_content_part`, `input_blocked`, `input_unmaskable`, `output_blocked`, `stream_malformed_upstream`, `stream_killed`.

### `runtime`

- `runtime/resources.py` is exactly 800 lines (the gate fails above 800). Budget: the new constants need about 8 documented constants x about 4 lines = 32 lines, plus the `max_request_parts()` edit (+3) and the mixin base-class entry (+1), so about 36 lines must be freed and the target is <= 790 after additions, which means freeing about 46 lines. Two moves, in this order, each followed by `check_sizes` before any behaviour task: (1) the Group-F accessors (`max_output_tokens`, `default_response_token_ceiling`, `max_output_tokens_boundary`, `max_completions`, about 25 lines) into `runtime/resources_budget.py` as `BudgetBoundsMixin`; (2) the stream-timeout accessors (`inter_chunk_timeout_s`, `max_stream_duration_s` and siblings, about 25 lines) into `runtime/resources_stream.py` as `StreamBoundsMixin`. Both follow `resources_vector.py` (constants stay in `resources.py`, the mixin reads them with function-local imports). If the sum is under 46 lines after the two moves, a third move (`mcp_bounds` plus `McpBounds`) is done before adding constants. The frozen slotted `ToolBounds` and `ToolBoundsMixin.tool_bounds()` live in `runtime/resources_tools.py`.
- `ToolBounds` (frozen, slotted): `max_tools`, `schema_depth`, `schema_nodes`, `max_tool_calls`, `arguments_bytes`, `max_scanned_slots`, `parts_allowance`, `max_withheld_bytes`. Values are in "ToolBounds values" below. Each is checked `>= 1`, else `CapacityUnavailable` (fail closed, same as every shipped method). No literal appears outside `runtime/resources.py`.

### `egress`

- `egress/stream.py` (after the `stream_meta.py` extraction, see size table): the enforcing and strict-withhold pipelines pass `META_CHANNELS` deltas through unscanned and unheld (`_run_passthrough` already forwards everything and needs no change) (a `_pass_meta` branch in `_run_enforcing`, a separate ordered list emitted last in `StrictWithholdPipeline._run`). Scan keys are otherwise opaque, so `output_tool_arguments#0` and `output_tool_arguments#1` get separate `_Text` streams with no other change. This is the property that makes per-call isolation (R4.2) hold in both pipelines.
- `egress` does not learn about tool calls. It sees channel names.

### `dispatch`

- `dispatch/openai_upstream.py`: `_drain` already forwards every non-`role` delta. No change except that `finish_reason` and the `#`-suffixed tool channels now pass through (they arrive as ordinary `text_deltas`). A test pins that `role` is still dropped.

### `edge/wire` (pure)

- `edge/wire/openai_tools.py` (new): validation and folding of `tools`, `tool_choice`, `parallel_tool_calls`, `response_format`, and tool-message ties. Exposes `fold_tools(builder, parsed, bounds) -> ToolsTemplate`, `validate_tool_messages(...)`, and `canonical_arguments(text, bounds) -> str`. Raises `MalformedRequest` with a declared code.
- `edge/wire/openai_fold.py`: `TemplateMessage` gains `tool_calls: tuple[TemplateToolCall, ...]` and `tool_call_id: str | None`; `UpstreamTemplate` gains `tools`, `tool_choice`, `parallel_tool_calls`, `response_format`; `FoldResult` gains a parallel `forwarded: tuple[bool, ...]` (False for scan-only pairs). `INPUT_SDK_PATH_PREFIXES` adds `tools[]` and `response_format`. `_FOLDED_INPUT_CHANNELS` adds the seven new input channels (the four above plus `tool_name`, `tool_call_id` and `tool_schema_struct`), and `_assert_fold_total()` keeps failing the build on any divergence (P2).
- `edge/wire/openai_params.py`: `tools`, `tool_choice`, `parallel_tool_calls`, `response_format` move from refused to handled; role `tool` joins `_ROLES`; the `tool_calls` key is checked structurally instead of refused; `function_call` and `functions` stay refused. The refused set is an explicit frozenset so P8 can partition it.
- `edge/wire/sse_chunks.py` (new): `SSEChoice`, `SSEChunk` and the new `ChunkEvent`/`ToolFragment` value types, moved out of `sse.py` and re-exported from it (keeps `sse.py` under the size gate).
- `edge/wire/sse_decode.py` (new): the `SSEDecoder`, moved out of `sse.py` and re-exported from it, with the tool-call changes below.
- `edge/wire/sse.py`: encoder changes below.
- `edge/wire/tool_stream.py` (new, pure): `ToolCallAssembler` and the channel-key grammar.
- `edge/wire/openai.py`: `OpenAIResponseRenderer._message_for` renders complete `tool_calls[]`.

### `edge`

- `edge/upstream_body.py` + new `edge/upstream_tools.py`: render tools/response_format/tool messages from the template.
- `edge/stream_response.py` (new): `StreamResponse`, the deferred-start writer (D7).
- `edge/chat_stream.py` (new): the streaming orchestration moved out of `routes.py` (`_stream`, `_run_attempts`, `_finish_stream`, `_emit_produced_mode`, `_next_attempt`, `_build_pipeline`, `_ProducedModeHolder`, about 280 lines) as a frozen slotted `ChatStream` that holds the route. `routes.py` keeps `handle`, `_render_refusal`, `_stream_response` (now a thin delegator) and re-exports `_ProducedModeHolder`. `ChatRoute` is imported under `TYPE_CHECKING` to avoid a cycle.
- `edge/disconnect.py` (new): the disconnect watcher and `StreamingDone` flag (D14).
- `edge/openai_nonstream.py`: skip `META_CHANNELS` in `_enforce_channels`; add `validate_tool_output`.
- `edge/asgi_sse.py`: `as_chunks` closes the provider iterator and releases the coalescer in `finally`.

## Request side

### Parsing and validation order

`OpenAIRequestParser.parse` keeps its order: decode, model, stream flag, messages shape, `forwarded_fields`, fold, budget fields. Tools validation runs inside `forwarded_fields`/`fold_tools`, before any scan, with zero provider calls on failure.

### `tools`

| Input | Rule | On failure |
|---|---|---|
| `tools` | optional; list; `len <= bounds.max_tools`; `null` treated as absent | `malformed_wrong_shape`, `param: tools` |
| `tools[].type` | must equal `"function"` | same |
| `tools[].function.name` | required string matching `^[A-Za-z0-9_-]{1,64}$`; unique within the request | same |
| `tools[].function.description` | optional string; scanned (`tool_description`) | wrong type: same |
| `tools[].function.strict` | optional bool; forwarded | same |
| `tools[].function.parameters` | optional JSON object; depth `<= schema_depth`; node count `<= schema_nodes`; keywords in the closed set (D1a) | same |
| `tool_choice` | `"none"`, `"auto"`, `"required"`, or `{type:"function", function:{name}}` with `name` in the declared set; a named choice with no `tools` is refused | `malformed_wrong_shape`, `param: tool_choice` |
| `parallel_tool_calls` | bool | `param: parallel_tool_calls` |

`parameters` walk: a recursive, iterative-bounded walk (explicit stack, not recursion, so depth bombs cannot raise `RecursionError`) classifies every string. Keywords are structural literals. `type` values must be in the closed JSON-Schema type set and are structural. Every other string falls into exactly one of two classes (review R4-1): **structural strings** (property names, `$defs` keys, `required` entries, `title`, `$ref` and the other identifier-like keywords listed in the keyword table) go into the per-tool joined run on BLOCK-only channel `tool_schema_struct`, scan-only and rendered from the validated template; **free-text strings** (`description`, `default`, `examples`) are forwarded redactable slots on `tool_schema_text`. Numbers, booleans and `null` are structural. Schema node counts include keys and leaves.

Keys are NEVER redacted slots: a property or `$defs` key is rendered verbatim from the template and only after the `tool_schema_struct` run returned ALLOW. Consequently key collisions after redaction and dangling `$ref` after redaction cannot occur and no `input_unmaskable` path exists for them. The one residual case is an object key inside a `default`/`examples` value (free JSON value): those keys are forwarded redactable slots on `tool_schema_text`, and if two keys in one such object become equal after redaction `with_openai_body` refuses `input_unmaskable` (kept as an assertion, no silent merge). The rendered `$ref` pointers are still checked to resolve inside the template schema at fold time (a validation, not a post-redaction repair).

### `response_format`

`type` in `{text, json_object, json_schema}`. `json_schema`: `name` matches `^[A-Za-z0-9_-]{1,64}$`; `schema` an object within the same bounds and the same walk as `parameters` (channel `response_format_schema`); `description` scanned on the same channel; `strict` bool. Anything else: `malformed_wrong_shape`, `param: response_format`.

### Tool messages and history

Messages may have role `tool` with `tool_call_id` matching `^[A-Za-z0-9_.:-]{1,64}$` and string or text-part `content`. Assistant messages may carry `tool_calls[]` (`id` matching the same pattern and unique in the request, `type: "function"`, `function.name`, `function.arguments` string) with `content` null. Ties checked in one pass in message order (the shape patterns below are necessary but not sufficient: ids and names are also scanned, see "Tool names and ids"):

- a `tool` message's `tool_call_id` must equal an `id` from an **earlier** assistant message in this request;
- an assistant call `name` must be in the declared set when `tools` is present.

Failure: `malformed_wrong_shape`, `param: messages`. Message `name` on `system`/`user`/`assistant` stays accepted and not forwarded. The `tool` message `name` is also not forwarded.

### Tool names and ids (scanned, BLOCK-only)

The patterns `^[A-Za-z0-9_-]{1,64}$` (names) and `^[A-Za-z0-9_.:-]{1,64}$` (ids) admit a 20-character `AKIA...` key, a 64-character hex secret or short injection text such as `ignore_previous_instructions`, and these go upstream byte for byte. They are therefore NOT treated as structural tokens. Every occurrence of a name (`tools[].function.name`, assistant call `name`, `tool_choice.function.name`) is folded as a scan-only pair on channel `tool_name`, and every id (assistant call `id`, tool message `tool_call_id`) on `tool_call_id`. The shape check runs first (cheap, no scan); the scan runs on the shape-valid value.

- Any finding in these two channels is `input_blocked`. Redaction is never applied: a redacted name or id would break the declared-set and id ties and change the call identity, so a REDACT-class finding is escalated to BLOCK for these channels (conservative fail-closed choice, documented). The forwarded string is the validated original, which is only reachable when the scan returned ALLOW.
- Because the pairs are scan-only (`forwarded=False`), no slot is filled and `with_openai_body` renders the original validated value from the template.
- Equal values (a name declared once and referenced in `tool_choice` and in history) are folded once per occurrence, not deduplicated, so the fold stays total and the scan covers each occurrence.

#### BLOCK-only attribution (D23)

`scan_input` pools findings in request-content coordinates and `Finding` carries `spans` but no channel, so attribution is done by span in `edge/input_enforcement.py`, next to `_base_offsets`:

1. `domain/channels.py` adds `BLOCK_ONLY_CHANNELS: frozenset[str] = frozenset({"tool_name", "tool_call_id", "tool_schema_struct"})`.
2. After the pooled detector run and before `resolver.decide` on the pooled set, `input_enforcement` computes, from `_base_offsets(req.text_deltas)` and each delta's length, the half-open range of every delta whose channel is in `BLOCK_ONLY_CHANNELS`.
3. For each finding with a span overlapping one of those ranges, it calls `resolver.decide([finding])` alone. If that decision is anything other than ALLOW, the whole request is `input_blocked` (zero provider calls). This is independent of the pooled decision, so a REDACT-class value (email, card, key) in a name or id cannot resolve to a pooled REDACT whose redacted pair is then discarded.
4. A finding whose span straddles a BLOCK-only range and a forwarded range counts as a hit (conservative).
5. Tests (P3 extension): an email in a tool name, an email in a `tool_choice` name, a card number in an assistant call id and in a `tool_call_id` each give `input_blocked` and zero provider calls; the same value in a user message still gives the normal REDACT.

### Schema keyword table

The walk over `parameters` and `response_format.json_schema.schema` classifies every key and value from this closed table. Anything not in it is refused (`malformed_wrong_shape`, `param: tools` or `response_format`).

| Class | Keywords | Treatment |
|---|---|---|
| Type | `type` (string or list from `string number integer boolean object array null`) | structural; value outside the set is refused |
| Subschema | `items`, `additionalProperties` (bool or subschema), `additionalItems`, `not`, `if`, `then`, `else`, `contains`, `propertyNames`, `unevaluatedProperties`, `unevaluatedItems` | recurse |
| Subschema list | `anyOf`, `oneOf`, `allOf`, `prefixItems` | recurse into each |
| Named subschema map | `properties`, `$defs`, `definitions`, `patternProperties`, `dependentSchemas` | each key goes to the `tool_schema_struct` run (BLOCK-only, scan-only); each value recurses |
| Required-name list | `required`, `dependentRequired` values | each string goes to the `tool_schema_struct` run |
| Structural string | `title`, `pattern`, `format`, `$comment`, `$anchor`, `$id` | `tool_schema_struct` run |
| Free-text string | `description` | forwarded redactable slot on `tool_schema_text` |
| `$schema` | `$schema` | `tool_schema_struct` run; forwarded as is from the template |
| `$ref` | `$ref` | must match `^#(/[^/]*)*$` (local pointer only; a remote ref is refused). Goes to the `tool_schema_struct` run. Every `$ref` must resolve inside the template schema at fold time, else `malformed_wrong_shape` |
| Free JSON value, free-text | `default`, `examples` | every string leaf and every object key is a forwarded redactable slot on `tool_schema_text`; numbers, bools and null structural |
| Free JSON value, closed | `const`, `enum` | every string member goes to the `tool_schema_struct` run (BLOCK-only: redacting an enum member would change the contract); object keys inside them likewise; numbers, bools and null structural |
| Number or bool | `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`, `minLength`, `maxLength`, `minItems`, `maxItems`, `minProperties`, `maxProperties`, `uniqueItems`, `nullable`, `readOnly`, `writeOnly`, `deprecated` | structural; wrong JSON type refused |
| OpenAI structured outputs | `strict` (at the function / json_schema level, not inside a schema) | bool, structural |

`$defs`, `$ref`, `anyOf`, `additionalProperties`, `items`, `required`, `title`, `default` (the set pydantic, LangChain and the SDK `.parse` helper emit) are all in the table. A wire-profile case replays a pydantic-v2 style schema with `$defs`/`$ref`/`anyOf`/`additionalProperties: false` and asserts `200` and an upstream body whose `$ref` resolves. The table is a module-level `MappingProxyType` in `edge/wire/openai_tools.py`.

### ToolBounds values

All in `runtime/resources.py` as named constants with a one-line rationale each; `ToolBounds` reads them. `max_withheld_bytes` and `parts_allowance` are derivations only (existing methods and `_PARTS_PER_SCHEMA_NODE`). **Gate allowlist (review R5-2):** GW34 adds NO new file to `check_capacity_literals.ALLOWED_REL`. `resources_tools.py`, `resources_budget.py` and `resources_stream.py` hold no numeric capacity literal, only references and derivations from `resources.py`. GW27 D-13 proposes a second allowed file `runtime/resources_org.py`; GW34 neither depends on it nor adds one. If GW27 lands first, GW34 is unaffected; if both would need a further file, that is recorded as a cross-card decision in the exit note, not decided here.

| Field | Derivation | Value / rationale |
|---|---|---|
| `max_tools` | `_MAX_TOOLS` | 128, the OpenAI per-request function limit |
| `max_tool_calls` | `_MAX_TOOL_CALLS` | 128, same limit applied to calls per message and per response |
| `schema_depth` | `_SCHEMA_DEPTH` | 32; deeper than any generated schema, shallow enough that the iterative walk is cheap |
| `schema_nodes` | `max_request_bytes() // _SCHEMA_NODE_BYTES` | `_SCHEMA_NODE_BYTES = 64`: a node (key plus leaf) needs at least 4 bytes of JSON, 64 allows realistic average node size without letting node count exceed byte capacity |
| `arguments_bytes` | `max_request_bytes() // _TOOL_ARGUMENTS_DIVISOR` | `_TOOL_ARGUMENTS_DIVISOR = 4`: one call's arguments may use at most a quarter of the request |
| `max_scanned_slots` | `floor(target_p99_ms * 1000 * _SCAN_BUDGET_FRACTION / _SCAN_SLOT_COST_US)` | `_SCAN_BUDGET_FRACTION = 0.5` of the p99 target may go to detector invocations; `_SCAN_SLOT_COST_US = 20` microseconds per slot (initial; see below). At p99 20 ms this is 500 slots |
| `parts_allowance` | `_PARTS_PER_SCHEMA_NODE * schema_nodes` | `_PARTS_PER_SCHEMA_NODE = 2`: a node contributes at most one `{` and one `,` to the byte count (D20). Added to `max_request_parts()` by `_pre_checks` (review R5-4) |
| `max_withheld_bytes` | `stream_buffer_bytes(active_streams())` (existing contract methods) | Total bytes the assembler may withhold per stream across ALL tool calls plus JSON-mode `content` (review R5-2). NO new constant and NO literal: it reuses the exact derivation the shipped `Coalescer` ceiling uses. The effective per-call bound is `min(arguments_bytes, max_withheld_bytes)` |

`max_scanned_slots` counts every detector invocation made on folded string slots from tools, schema, `response_format` and assistant arguments (forwarded and scan-only). Above it the request is refused `malformed_wrong_shape` before any scan.

**Slot arithmetic and the intended minimum (review R3-5, R4-2).** Counting one detector call per string leaf, a pydantic tool costs name 1, description 1, 5 property keys, 5 `title`s, 5 parameter `description`s, up to 5 `required` entries = about 22 to 25 slots if each string had its own call. The fold therefore uses **subtree runs** (precedent: `join_runs`): inside one tool all structural strings go into ONE scan run on `tool_schema_struct` (BLOCK-only, never redacted), and only free text (`description`, `default`, `examples`) keeps its own slots. A pydantic tool then costs name 1, description 1, structural run 1, parameter descriptions 5 = 8 slots. Arithmetic, stated once:

| Request | Tools | History | `response_format` | Total slots | Share of 500-slot cap |
|---|---|---|---|---|---|
| 20 tools, 5 params, 20-message history | 20 x 8 = 160 | 60 | 8 | 228 | 46% |
| 40 tools, 5 params, 20-message history | 40 x 8 = 320 | 60 | 8 | 388 | 78% |

**Chosen target (one rule, no contradiction).** (a) REQUIRED: the 20-tool request is admitted at the reference `target_p99_ms` with `slots_used <= 0.7 * max_scanned_slots` (228 <= 350). (b) STRETCH, reported: the 40-tool request is admitted with `slots_used <= 0.8 * max_scanned_slots` (388 <= 400) provided the measured `_SCAN_SLOT_COST_US` is <= 20. If the benchmark measures a cost above 20 us, the cap falls below 485 and the 40-tool case no longer fits; that is recorded in the evidence directory and in the card exit note as a REPORTED, CARD-BLOCKING-FOR-THE-STRETCH finding (the exit note must say "40-tool target not met at cost X us"), and the required 20-tool target still must pass against the measured cap (at 25 us the cap is 400, 0.7 of it is 280, and 228 <= 280 passes). If even 20 tools fail the 0.7 bound, the card is not closed; widening subtree joining (parameter descriptions shorter than a stated byte cap in one more run per tool) is the permitted mitigation, never silently lowering the target. Refusing the request is the last resort.

**Measurement at task start (evidence, not assumption).** The first implementation task, before any fold code, runs a benchmark over the shipped detector (`detect` entry point used by `input_enforcement`) on 1000 short worst-case slots and on a 25-slot and an 8-slot realistic tool, records p50 and p99 microseconds per call in the evidence directory, and sets `_SCAN_SLOT_COST_US` to the measured p99 per call rounded up. `_SCAN_SLOT_COST_US` is therefore derived from the benchmark, not asserted, and `target_p99_ms` (read from the contract, 20 ms at the reference) is a named input of the derivation `floor(target_p99_ms * 1000 * _SCAN_BUDGET_FRACTION / _SCAN_SLOT_COST_US)`, recorded beside the cost in the evidence file. The calibration test scans `max_scanned_slots` worst-case short slots and asserts the total is under `target_p99_ms * _SCAN_BUDGET_FRACTION`; if it fails the task raises `_SCAN_SLOT_COST_US` (lowering the bound), never loosens the test. Tests: a request at the bound and one above it; a 20-tool pydantic request asserts `slots_used <= 0.7 * max_scanned_slots` (required); a 40-tool request asserts `slots_used <= 0.8 * max_scanned_slots` and is marked as the reported stretch (an `xfail(strict=False)` with the measured cost in the reason is NOT used; the test fails loudly and the exit note records it if the cost exceeds 20 us). The C26 acceptance test for D20 uses the 20-tool request and asserts the slots-used headroom. A P3 case (review R4-1): an email in a property name, in a `$defs` key, in a `required` entry and in an `enum` string each give `input_blocked` with zero provider calls (REDACT-class values on `tool_schema_struct` are escalated to BLOCK), while the same email in a parameter `description` is redacted normally.

**Input-side false-positive measurement (review R3-8).** A detector test feeds realistic input identifiers and names through the BLOCK-only channels (`tool_name`, `tool_call_id`, the structural run) and asserts ALLOW: UUID v4 ids (LangChain style), `call_` + 24 alphanumerics, `toolu_` + 22 alphanumerics, `chatcmpl-tool-` + hex ids, snake_case and camelCase names such as `get_current_weather`, `searchWeb`, and a pydantic structural run. A failure means a high-entropy rule would block ordinary agent history and the task must report it rather than weaken the BLOCK-only rule.

### C26 part-count allowance (D20)
`ResourceContract.max_request_parts()` is unchanged (`workers() * _MAX_REQUEST_PARTS_MULT`), so tool-free traffic keeps the full strength of the C26 flood guard. There is no second contract method (review R5-4). `ToolBounds` carries `parts_allowance = _PARTS_PER_SCHEMA_NODE * schema_nodes`, and `edge/openai_chat.py::_pre_checks` picks the ceiling from the route-level `self.tool_bounds` (resolved once at `build_app`, see Decoder): if it is `None` (a contract without `tool_bounds`, e.g. the standalone `test_lgw15_*` fakes) the ceiling is always `contract.max_request_parts()`, and the tools bytes check is not even evaluated; otherwise the larger ceiling `contract.max_request_parts() + bounds.parts_allowance` is used only when `body.find(b"tools") >= 0 or body.find(b"tool_calls") >= 0 or body.find(b"response_format") >= 0` (a cheap `bytes.find` on the already-read body, before parse). `_pre_checks` therefore never calls a contract attribute the fakes lack. A false positive (the word in ordinary text) raises the ceiling for that one request only, which is still contract-bounded and still followed by the fold's own bounds. `_part_count` is unchanged. Tests: a 20-tool pydantic-style request with `workers() == 1` is admitted and reaches the fold; a tool-free body of `max_request_parts() + 1` `,` bytes (257 with one worker) is still refused `REFUSE_PRE_CHECK`; a body with `tools` bytes and `max_request_parts() + parts_allowance + 1` commas is refused; **fake-contract test (R5-4):** on an app built with a `test_lgw15_*`-style fake contract (no `tool_bounds`, no `max_request_parts_with_tools`), a body containing the bytes `tools` yields the declared `unsupported_parameter` refusal (not an `AttributeError`/500) and a tool-free body with `max_request_parts() + 1` commas is refused by `REFUSE_PRE_CHECK` with the fake's own `max_request_parts`.

### Arguments canonicalisation (D4a)

For each assistant `tool_calls[].function.arguments`:

1. Reject if longer than `bounds.arguments_bytes`. 1b. If the string is empty or whitespace-only, treat it as `{}` and record the known diff (review R4-4); the raw empty string is still folded as the scan-only `tool_arguments_raw` pair.
2. `json.loads` with `parse_constant` raising (NaN/Infinity refused) and catching `ValueError`/`RecursionError`. Failure is `malformed_wrong_shape`, `param: messages` (A5). A float that overflows (`1e400` parses to `inf` without calling `parse_constant`) is caught by step 4 (`allow_nan=False`). Any string (value or key) containing a surrogate code point (U+D800 to U+DFFF) is refused the same way, checked in the iterative walk of step 3 (D25).
3. Check depth and node bounds with an iterative walk.
4. Re-serialise with `json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))`, catching `ValueError` (refused as above). This is the **forwarded slot** on channel `tool_arguments`. Escapes are decoded, so `AK\u0049A...` is scanned as `AKIA...`.
5. Also fold the **raw string** as a scan-only pair on `tool_arguments_raw` (`forwarded=False`, no template slot). A BLOCK on it blocks the request. A REDACT on it is not applied because the raw bytes are never forwarded.

Consequences, all recorded as known diffs: duplicate JSON keys collapse to the last, whitespace is normalised, and float spelling may change. None changes what the model sees semantically.

### Cost accounting (D27)

`prompt_chars` and the token estimate are computed from forwarded pairs only (`FoldResult.forwarded`). Raw-argument, name and id scan-only pairs are excluded.

### Redaction and the byte-verified path

The existing `stage_upstream`/`verified_upstream` discipline is unchanged: the verified deltas replace pair text. `with_openai_body` is extended:

- it checks `len(texts) == template.pair_count` (all pairs) and fills only pairs whose `forwarded` flag is true;
- a slot flagged as JSON (arguments) is checked with `json.loads` after filling. Invalid means `input_unmaskable`;
- it returns a `FillRefused(code)` value (frozen, slotted) instead of `None` for these two cases, so the caller refuses with the right code. A plain slot mismatch still returns `None` and the existing refusal.

### Rendering (R3.8)

`edge/upstream_tools.py` builds the upstream `tools`, `tool_choice`, `parallel_tool_calls`, `response_format` and per-message `tool_calls`/`tool_call_id` from the **template only**, never from the client body. Every string leaf of the rendered body is either a verified slot delta, or a template value (name, id, enum literal, schema keyword) that was shape-validated AND (for names and ids) scanned ALLOW in the input phase (P3). The only unscanned strings are closed-set literals (`type: "function"`, schema keywords, `tool_choice` literals).

### BLOCK precedence

Unchanged input phase: a BLOCK finding in any new channel is `input_blocked` before staging (C22), zero provider calls.

## Response side

### Channel grammar

`edge/wire/tool_stream.py` owns the grammar. Keys are `output_tool_id#<n>`, `output_tool_name#<n>`, `output_tool_arguments#<n>`, with `n` a decimal index below `bounds.max_tool_calls`. `base_channel(key)` returns the part before `#`, which is the name registered in `domain/channels.py`. `base_channel` is used only by the renderer and the assembler to map a key back to its domain channel; egress and `_enforce_channels` resolve from findings over text and key by the full string, so they need no lookup. The legacy bare `output_tool_arguments` is accepted by the renderer as index 0 so existing tests keep passing.

### Decoder (`SSEDecoder`)

`SSEDecoder.__init__` takes `max_tool_calls` and keeps small per-stream state: the first `id` and `name` seen per index. **Resolved once, lazily (review R3-1).** `ChatRoute` gets a field `tool_bounds: ToolBounds | None`, filled once in `build_app` by `_resolve_tool_bounds(contract)`, which does `getattr(contract, "tool_bounds", None)` and calls it if present; per request, nothing calls `contract.tool_bounds()`. A contract without `tool_bounds` (the standalone fakes in `tests/edge/test_lgw15_*`, which are `@staticmethod` classes, not `ResourceContract` subclasses) leaves the field `None`: such a route is a tools-disabled route, so a request carrying `tools`, `tool_calls`, a `tool` role or `response_format` is refused with the declared `unsupported_parameter` code (the GW36 S1 refusal behaviour), and the decoder is built with `max_tool_calls=0`, so any upstream `tool_calls` delta is `stream_malformed_upstream`. The real `ResourceContract` always provides it, so production has no such branch active. This is the conservative fail-closed choice; the fakes keep working unchanged and no fake needs editing. A test asserts both: a fake contract builds an app and serves a plain chat/stream request, and a `tools` request on it is refused, never a 500.

For choice index 0 (D17; a choice with `index >= 1` is ignored, D26), `_deltas_from_choices`:

- keeps the current string-valued key behaviour for `content`, `reasoning_content`, `refusal` and any other string key except the reserved ones below. **Reserved delta keys (review R3-2):** a `delta` key that is in `META_CHANNELS`, equals `role`, contains `#`, or starts with `output_tool_` sets `malformed()` (`stream_malformed_upstream`); it is never turned into a channel. This closes the unscanned META text path (`delta.finish_reason`) and stops an upstream-supplied `output_tool_arguments#99999` from skipping the index bound, the D16 first-value rule and the shape checks. Only the decoder mints `#`-suffixed channels. The assembler and egress treat any `#`-suffixed key they did not expect (unknown base, index not previously introduced by an id/name/arguments channel within `max_tool_calls`) as `stream_malformed_upstream`, never as a new call. Decoder tests: one per case (`delta.finish_reason`, `delta.role` as a string key, `delta.output_tool_name#0` with no `tool_calls`, `delta.output_tool_arguments#99999`, a key containing `#`);
- parses `delta.tool_calls` as a list of objects. Each needs `index` as a non-bool int in range, optional string `id`, optional `type == "function"`, optional `function` object with optional string `name` and `arguments`. Anything else sets `malformed()` (never a silent drop). Emits `output_tool_id#n` and `output_tool_name#n` once per index (D16) and `output_tool_arguments#n` for each non-empty `arguments`. Unknown extra keys on a fragment are ignored and never forwarded;
- treats `delta.function_call` other than `None` as malformed;
- reads `finish_reason`: `None` is nothing; one of `stop`, `length`, `tool_calls`, `content_filter` becomes a `("finish_reason", value)` delta; any other value sets `malformed()` (R4.5).

### Egress

The enforcing pipeline and the strict-withhold pipeline treat `finish_reason` as a meta channel (see `egress` above). `_run_passthrough` already forwards every delta unchanged and needs no meta branch (review R4-6); there is no third branch to write. Everything else is scanned per channel key as today.

### Tool-call assembler (sink side)

`ToolCallAssembler` is a per-request mutable slotted class (precedent: `_TerminalLatch`, `FirstByteLatch`; the module-mutable gate only covers module-level state). It receives each released `DownstreamFrame` from the route's `_sink`, and returns zero or more `ChunkEvent`s for the encoder. It is constructed with: `declared: frozenset[str]`, `tools_declared: bool`, `enforcing: bool`, `json_mode: bool`, `bounds: ToolBounds`, and an injected `decoded_clean: Callable[[str], Awaitable[bool]]`.

Behaviour:

- **Content channels** pass through unchanged, except when `json_mode and enforcing`: `content` is buffered (charged against the total withheld bound below) and released once at the end after `json.loads` succeeds (D9), else the sink terminates `output_blocked`.
- **Tool channels**, per index, accumulate `id`, `name` and `arguments` text. Because id and name are scanned streams that may be held by the scanner, arguments can arrive before the name. The assembler buffers arguments (bounded per call by `arguments_bytes` and in total by `max_withheld_bytes`; overflow is `stream_malformed_upstream`, D19) until id and name are both known. **Total withheld bound (review R5-2).** The assembler keeps ONE running total of every byte it holds: arguments, ids, names, and JSON-mode `content` (D9). It is constructed with `charge: Callable[[int], bool]` and `refund: Callable[[int], None]`, bound by `chat_stream.py` to the request's own `Coalescer.offer(n)` and `Coalescer.release(n)` (`egress/backpressure.py`), so withheld bytes are charged to the same `stream_buffer_bytes(active_streams())` budget as egress holds and the per-stream ceiling is not doubled. A `False` from `charge`, or a total above `ToolBounds.max_withheld_bytes`, is `Terminate(stream_malformed_upstream)` for tool-call bytes and `Terminate(output_blocked)` for JSON-mode `content` (the latter is a policy-shaped refusal: the response cannot be validated within budget). Bytes are refunded as `finish()` emits them or when the request ends. Test: 128 calls at `arguments_bytes` each (32x `max_request_bytes()` if unbounded) yield a terminal code with a measured peak withheld total `<= max_withheld_bytes`, and an oversized JSON-mode body yields `output_blocked` with a bounded peak.
- **Validation** once id and name are known: `id` matches `^[A-Za-z0-9_.:-]{1,64}$`; `name` is in `declared` (and `tools_declared` is true). A violation terminates `output_blocked` with zero bytes of that call released (D18). Because `output_tool_id#n` and `output_tool_name#n` are scanned with the redacting holdback, a REDACT on either would change the identity and fail the shape or declared-set check anyway; the shape and declared-set checks therefore turn a redaction that changes the identity into terminal `output_blocked`, not a repair. No distinct audit detail is produced (the assembler sees only released text and cannot tell redaction from the original; the plain `output_blocked` code is what the audit records). A test also covers a redaction that deletes characters but still passes the id regex (`call_AKIA...` becoming `call_`): the id is then a valid but different id, which is harmless because the id is only echoed back to the same client, and the call's name must still be declared. A test feeds realistic OpenAI-style ids (`call_` + 24 alphanumerics) and realistic names through the shipped detectors and asserts ALLOW, so the false-positive risk is measured, not assumed.
- **Not enforcing:** the first fragment of an index is emitted as `{index, id, type:"function", function:{name, arguments:<buffered or "">}}`; later arguments are emitted as `{index, function:{arguments:<delta>}}` with the received boundaries.
- **Enforcing:** nothing is emitted for any call until `finish()` is called (D6a). Then, for each index in order: parse arguments as JSON (invalid after redaction means `output_blocked`), run `decoded_clean` over the decoded string leaves and keys (any non-ALLOW residual finding means `output_blocked`; this catches an escape-obfuscated value the raw-text scan missed, and needs no span mapping because the text has already passed the raw scan), then emit ONE fragment carrying id, type, name and the full arguments.
- **Incomplete calls at end of stream (review R4-3), both modes.** `finish()` walks every index seen (including indexes that only ever received `arguments`). For each it requires a shape-valid `id`, a declared `name` and an `arguments` value (an absent `arguments` is treated as the empty string, see normalisation below). Gaps: arguments without name, arguments without id, name without id, or id without name give `Terminate(stream_malformed_upstream)` (structural gap); an undeclared or `tools_declared == False` name gives `Terminate(output_blocked)`. `finish_reason: "tool_calls"` with zero calls seen gives `Terminate(stream_malformed_upstream)`. Buffered arguments are therefore never silently dropped in non-enforcing mode. Tests, one per gap: arguments without name, arguments without id, name without id, id without name, `finish_reason: "tool_calls"` with no call, plus the same four in enforcing mode.
- **Empty arguments (review R4-4, D4a).** Empty or whitespace-only `arguments` for a call (some OpenAI-compatible providers send `""` where OpenAI sends `"{}"` for a no-parameter tool) is normalised to `{}` on both sides: in history (`canonical arguments`, step 1b) and on output in `finish()` and `validate_tool_output` before the JSON parse. This is recorded as a known wire-profile diff. A non-empty string that is not valid JSON remains `malformed_wrong_shape` (history) or `output_blocked` (output, enforcing). In non-enforcing streaming mode the empty value is forwarded as the received boundaries (empty or absent arguments are emitted as `""`), unchanged.
- **Finish:** `finish_reason` is only recorded when it arrives (it is never a release trigger). It is emitted on the final chunk by the stream after `finish()`. `finish_reason: "tool_calls"` is never emitted on a terminated stream (R4.8). If upstream gave none for a clean stream, the final chunk uses `"stop"` (`"tool_calls"` if any call was released).

#### Assembler API, end of stream, and termination mechanism

The assembler does no I/O except the injected `decoded_clean` scan callable, and exposes two methods:

- `feed(frame: DownstreamFrame) -> Sequence[ChunkEvent] | Terminate`
- `async finish() -> Sequence[ChunkEvent] | Terminate` (async because it awaits `decoded_clean`; `feed` stays synchronous because it never scans)

`Terminate` is a frozen slotted value carrying a `posture` code (`output_blocked`, `stream_malformed_upstream` or `scan_failure`). One path handles it, identical for all three pipelines (enforcing, strict-withhold, pass-through), because `StreamPipeline._run_passthrough` never checks `killed()` and so the kill latch alone cannot stop it:

1. `ChatStream._sink` calls `feed`. On `Terminate(code)` it calls `kill_latch.kill(code)`, stores `code` in the per-request pending-error holder, and raises a private `_AssemblerTerminated`.
2. `_attempt()` catches `_AssemblerTerminated` around `await pipeline.run(...)`. It is not counted as `stats.incomplete`; the stored code is what `_finish_stream` uses (pre-byte: plain HTTP error; after byte: SSE `Error_Frame`).
3. **Guarded finish (review R5-1).** `egress/stream.py` does not raise on a terminal failure: `_terminate(...)` (`output_blocked`, `scan_failure`, `holdback_overflow`, `killed`) sends a `DownstreamFrame(error_code=...)` and `run` RETURNS `StreamStats(incomplete=True)`. "Returns cleanly" therefore does not mean success. `_attempt()` calls `finish()` only under an explicit guard:
   ```python
   produced.stats = await pipeline.run(chunks, sink, kill_latch)
   if (not produced.stats.incomplete
           and kill_latch.reason() is None
           and pending_error.code is None
           and not sent_terminal.is_set()):
       await assembler.finish()   # may itself Terminate
   ```
   In every other case `finish()` is NOT called: the withheld calls are dropped with the request, zero tool bytes are released, no `finish_reason: "tool_calls"` is emitted, and the stored/egress code is what `_finish_stream` uses. A terminal `error_code` frame reaching `_sink` sets `sent_terminal` in BOTH states (pending: it records the code, writes nothing, and sets the flag; started: it writes the `Error_Frame` and sets the flag), so the guard does not depend on the shipped `_sink` dropping frames after the terminal. When the guard passes, the events `finish()` returns are written through the same sink path (so a `Terminate` from `finish()` takes steps 1 and 2 with no raise needed), and only then `note_stream_output` and `_finish_stream` run. Tests (one per egress terminate code: `output_blocked` on a content channel, `scan_failure`, `holdback_overflow`, `killed`), each with a withheld tool call outstanding under an enforcing rule: zero tool bytes released, no `finish_reason: "tool_calls"`, `finish()` not called (spy), and in the pending state a plain HTTP error rather than `200 text/event-stream`.
4. Non-enforcing mode: the first fragment of an index is emitted only after id and name are both validated, so an undeclared name (D18) is still `output_blocked` before any byte of the stream and remains a pre-byte plain HTTP error when no content has been released.

Tests cover the termination path in all three pipelines, a held final tail (egress holds the last argument fragment; the released arguments must equal the full arguments), and `finish()` being the only release point (a `finish_reason` frame followed by a late argument fragment yields one complete call).

The non-stream path uses a separate synchronous `decoded_clean_sync` (same detectors, called directly) and a synchronous `fold`-style helper, so the async assembler is used by the stream path only. `decoded_clean` is built in `edge/chat_stream.py` from `deps.error_scanner` and the `Phase.OUTPUT` resolver, run through the existing `ScanExecutor` offload (same pattern as `scan_error_frame_offloaded`). A scan error is fail closed: `scan_failure`.

Counts `tool_calls_released` and `tool_calls_withheld` are recorded on `RequestHolds` (`note_tool_calls`), exported as label-free counters seeded zero through the existing `OutcomeObserver`, and written to the audit `detail` as plain digit strings. No tool name, id or argument text reaches a label, a log line or an audit field.

### Non-streaming

`_collect_output_channels` already concatenates per channel key, so per-index isolation holds. `_enforce_channels` skips `META_CHANNELS`. A new `validate_tool_output(enforced, declared, tools_declared, bounds, decoded_clean_sync)` runs after enforcement and applies the same checks as the assembler (id shape, declared name, JSON validity, decoded residual scan, both-id-and-name present for every index). A failure returns `output_blocked`. `OpenAIResponseRenderer._message_for` builds `message.tool_calls` as a list of `{id, type:"function", function:{name, arguments}}` in index order, `content: null` when no content was emitted, and `finish_reason` from the meta delta (default `"stop"`, or `"tool_calls"` when calls exist). For a JSON-mode request where enforcement changed `content`, `json.loads` must succeed, else `output_blocked`.

## Chunk framing (R5)

`SSEEncoder` is constructed per stream by the chat stream with `stream_id = "chatcmpl-" + request_id`, `model = req.model`, `created = int(route.wall_clock_s())`. A new `StreamIdentity` frozen value carries the three; when it is absent the legacy defaults apply so existing callers compile.

- `encoder.chunk(event: ChunkEvent)` builds exactly one `choices[0]` entry per frame (n is forced to 1) with all deltas merged into one `delta` object. Channel to delta key is a closed table: `content`, `reasoning_content`, `refusal`, plus the tool and finish handling above. A channel outside the table is carried as its own name only for string deltas, as today.
- `delta.role: "assistant"` is added to the first chunk only.
- `encoder.finish(reason)` emits the final chunk with `delta: {}` and `finish_reason`, before `amf.trace` and `[DONE]`. It is not emitted when the stream terminated with an error.
- `finish_reason` is `null` on every earlier chunk.
- `encoder.content(frame)` stays for callers that still use it.
- `usage` is never emitted (D10).

## Pre-first-byte refusals (R7)

### Deferred start

`StreamResponse` (new, `edge/stream_response.py`, mutable slotted per request, three states: pending, started, closed) wraps ASGI `send`:

- `write(frame)`: a frame with a non-empty content, reasoning, refusal or tool-call delta is a first-byte frame (D21). On the first such frame, send `http.response.start` (200, `text/event-stream`), then every held frame in order, then this frame; later calls only the body. Sets the first-byte latch through the caller.
- `hold(frame)`: a role-only, empty-text or meta-only (`finish_reason`) frame arriving while pending is held. The held list is two NAMED slots, `_ROLE_SLOT` and `_FINISH_SLOT` (review R5-6): a role frame fills `_ROLE_SLOT`, a finish frame fills `_FINISH_SLOT`, and no integer bound exists anywhere in the module. A frame that finds its slot occupied is dropped. Further empty or role-only frames (provider keep-alives) are dropped, since they carry nothing the SDK needs once the first role chunk is held; overflow never starts the response, so the HTTP status stays undecided, memory is bounded, and the latch/state stay consistent: the `FirstByteLatch` is set only by `write`, and a later upstream failure is still a plain pre-byte HTTP error. A test sends 1000 empty keep-alive deltas then a failure and expects a plain HTTP error. The `_sink` latch for `FirstByteLatch` is set only by `write`, not `hold`.
- `fail_pre_byte(code, ...)`: allowed only while pending; emits the plain HTTP error through `_emit_error_code` with `request_id` and the console projection headers (`console_from_holds(holds, output_action=..., provider_called=holds.dispatched)`), then closes.
- `start_if_pending()`: used by the clean-empty-stream path; flushes held frames then the finish chunk, trace and `[DONE]`.

A test feeds `{"role":"assistant","content":""}` then an upstream timeout and expects a plain HTTP 504-class error (not `200 text/event-stream`); another feeds a role chunk, then a first-window output BLOCK, and expects the plain `output_blocked` error.

The deferral is not a timer: the start happens at the first released frame or at the terminal outcome, whichever is first, so time to first byte of a healthy stream is unchanged.

### Where it plugs in

1. `ChatRoute._render_refusal`: remove the `req.streaming` SSE branch. Every post-parse refusal (admission shed, budget, plan, input BLOCK/unmaskable, tools validation, surface refusal) renders through `_emit_error_code`, the same call a non-stream request uses. The identity special case (`refusal_request_id`) stays.
2. `ChatRoute._stream_response`: a coalescer refusal (`STREAM_BUFFER_UNAVAILABLE`) becomes `_emit_error_code` before any byte.
3. `ChatStream._sink`: a frame with `error_code` while the response is pending records the code in a per-request holder and writes nothing; while started it writes the `Error_Frame` as today.
4. `ChatStream._finish_stream`:
   - pending and a failure code exists (recorded sink code, else `kill_latch.reason()`): plain HTTP error for that code, status from `edge/errors.py`, no `amf.trace`;
   - pending and no failure (a clean stream that produced no content): start SSE, `encoder.finish`, trace, `[DONE]` (200);
   - started: unchanged terminal handling plus `encoder.finish` on a clean end.
5. Cases covered by 3 and 4: upstream non-2xx/unavailable/timeout/malformed first event (`kill_latch` carries the code from `as_chunks`), provider `error_frame` before content, first-window output BLOCK or scan failure, kill/timeout before the first byte, and tool-call validation failure (the assembler terminates before releasing anything).
6. The audit record carries the same `refused` code as today.

`_run_attempts` and `run_with_no_splice` are untouched, so "no-splice orchestration exhausted its attempts" is exactly the existing flow: the single eligible attempt ended byte-less.

### Responses

The Responses stream shares the new chunk framing (role once, finish chunk, stable identity) and the assembler. `ResponsesEventTranslator` reads only `delta.content`, so framing changes do not affect it; the Responses route constructs the assembler with `tools_declared=False`, so any upstream tool call there is `output_blocked`. One assertion in the Responses suite pins both facts. `ChatRoute._stream_response(..., plain_pre_byte: bool = True)`. `openai_responses.py` passes `False`; `StreamResponse` then starts eagerly, reproducing today's bytes (D7a). Chat-only tests update; Responses tests do not.

## Cancellation (R6)

`edge/disconnect.py::watch_disconnect(receive, stream_task: asyncio.Task, done: StreamingDone)` awaits `receive()` until it returns `http.disconnect`, then cancels `stream_task` only if `done` is unset. Uvicorn's `receive()` and httpx `ASGITransport`'s `receive()` both return `http.disconnect` once the response is complete, not only when the client leaves, so the watcher must never cancel the handler task itself.

- `ChatRoute.handle` runs the stream body (`ChatStream._stream`) as an inner `asyncio.Task` and awaits it. The watcher is started only for a streaming proceed outcome and holds a reference to the inner task, never to the ASGI app task.
- `StreamingDone` is a tiny per-request slotted flag. `handle` sets it before the terminal `[DONE]`/error write, so a disconnect reported after completion is a no-op.
- `handle` awaits the inner task inside `try/except asyncio.CancelledError`. The discriminator is `inner.cancelled() and asyncio.current_task().cancelling() == 0` (D22, Python 3.12). If true, the inner task was cancelled by the watcher: record `cancelled=True` and continue normally; the `finally` (`holds.release(admission, quota)` and `emit_chat_audit`) runs exactly once. Otherwise the server cancelled the handler task: record the same outcome, cancel and await the inner task, and re-raise after `finally`, so a server cancel is never swallowed and the inner task is never leaked. `handle` cancels the watcher in its `finally`. Tests: one for each branch (watcher cancel continues and returns; outer cancel re-raises and leaves no running inner task).

On cancel of the inner task, `CancelledError` unwinds through `ChatStream._stream`:

- `as_chunks` has `try/finally` that `aclose()`s the provider iterator (httpx stream context exits, socket closes) and releases any coalescer bytes;
- the audit and observer record `refused = posture.STREAM_KILLED` for a cancelled outcome. Nothing is written to the departed client, so no error frame and no second terminal frame (R6.3);
- the assembler's buffered, unreleased calls are dropped with the request (R6.4).

Tests: a normally completed stream records exactly one audit record with its real outcome and one release (the watcher race), a cancel test per blocked-on-upstream and actively-releasing state, and a test where `receive()` returns `http.disconnect` immediately after the final send.

**Shipped test harnesses (review R5-3).** Many shipped ASGI harnesses use a `receive` stub that returns `{"type": "http.disconnect"}` as soon as the request-body list is exhausted. Under the watcher that is a client leaving before the first frame, so a streaming test using one would be cancelled. One rule, three parts:

1. **Decision for an immediate disconnect.** A `receive` that returns `http.disconnect` before any `send` of the stream is a REAL early cancel: the watcher cancels the inner task, nothing is written, the audit/observer outcome is `stream_killed`. A new test pins exactly this (stub `receive` disconnecting at once; asserts `stream_killed`, one release, zero bytes sent).
2. **Shared helper.** New `tests/edge/asgi_receive.py::receive_until_sent(body_events, sent: asyncio.Event)`: yields the body events, then blocks until the harness's `send` has seen the final body frame (or `http.response.start` for a non-stream), then returns `http.disconnect`. This matches uvicorn and httpx behaviour. The harness `send` sets `sent` after the last `more_body: False`.
3. **Conversion list.** The implementation task re-runs `grep -rn "http.disconnect" gateway_v2/tests` and converts every harness that reaches a streaming chat route (non-stream and non-chat routes are unaffected because the watcher starts only for a streaming proceed). Known set, by name: `tests/edge/test_lgw12_app.py:190`, `tests/edge/test_lgw12_asgi_conformance.py:279`, `tests/edge/test_v2_leaks_d_lifecycle.py:480`, `tests/edge/test_v2_leaks_f_properties.py:439`, `tests/edge/test_v2_leaks_f_output_budget_properties.py:547`, `tests/edge/test_v2_leaks_a_integration.py:504`, `tests/edge/test_v2_leaks_a_failclosed_properties.py:428`, `tests/edge/test_tenant_scale_harness.py:78`, `tests/edge/test_b3_serve.py:183`, `tests/edge/test_gw16_app.py:324`, `tests/edge/test_gw16_lifecycle.py:196`. These join the R6/R7.8 task list and the "Existing tests to update" list. Any harness that is not converted and fails because of an early disconnect is a task failure, never a reason to weaken the watcher.

The bound is `contract.cancellation_bound_s()`; tests read it from the contract and use no literal. Both a stream blocked on the next upstream event and a stream actively releasing frames are covered, because the cancel is delivered at whichever `await` the task is parked on.

## Error handling

| Operation | Failure | Recoverable | Caller receives | Logged |
|---|---|---|---|---|
| Parse `tools`/`tool_choice`/`parallel_tool_calls`/`response_format` | wrong type, bound exceeded, unknown schema keyword, undeclared choice | fatal, request-level | 400 `malformed_wrong_shape`, `param` = field | no (counter only) |
| Refused field (`functions`, `logprobs`, `audio`, ...) | present and truthy | fatal | 400 `unsupported_parameter` / `unsupported_content_part` | no |
| Tool message tie | id not from earlier assistant call; name undeclared | fatal | 400 `malformed_wrong_shape`, `param: messages` | no |
| Assistant arguments not JSON, NaN, too deep, too large | | fatal | 400 `malformed_wrong_shape`, `param: messages` | no |
| BLOCK in a new channel | | fatal | 400 `input_blocked` | existing audit |
| Redacted arguments no longer JSON; key collision; unverifiable slot | | fatal | 400 `input_unmaskable` | existing audit |
| `ToolBounds` unset or below 1 | `CapacityUnavailable` at boot or first use | fatal at start | boot refuses; at request time 503 via existing mapping | error, no content |
| Decoder: bad `tool_calls` shape, bad `finish_reason`, bad `function_call` | | fatal stream | pre-byte: HTTP 502 `stream_malformed_upstream`; after byte: SSE error frame | warn, code only |
| Index out of range, too many calls, arguments over bound | | fatal stream | same, `stream_malformed_upstream` | warn, code only |
| Undeclared name, bad id, calls with no `tools`, invalid JSON after redaction, decoded-form residual finding | | fatal stream | pre-byte: HTTP 403 `output_blocked`; after byte: SSE error frame; zero bytes of that call released | existing audit |
| Scanner error in `decoded_clean` | | fatal stream | `scan_failure` through the same path | error, no content |
| Pre-byte provider failure | | fatal after single attempt | plain HTTP error for the code (non-stream parity) | existing |
| Client disconnect before completion | | n/a | nothing written | audit `stream_killed` outcome |
| Disconnect event after the stream completed | | n/a | ignored (`StreamingDone` set) | none |
| Assembler `Terminate` | | fatal stream | via `_AssemblerTerminated` and the pending-error code | per code above |
| Plain-HTTP emit fails (peer gone) | | n/a | inner stream task cancelled by D14 | none |

All logging is code-only. Tool arguments, definitions and schema text are never logged at any level (R11.3). FAIL_OPEN stays pinned at 0: every path that cannot decide terminates or refuses.

## Validation summary for external inputs

| Input | Required | Type | Limits | On failure |
|---|---|---|---|---|
| `tools` | no | list | `max_tools`; name pattern + scanned (BLOCK-only); depth/nodes | 400 `malformed_wrong_shape` / `input_blocked` |
| `tool_choice` | no | str or object | closed set; declared name | 400 `malformed_wrong_shape` |
| `parallel_tool_calls` | no | bool | | 400 |
| `response_format` | no | object | closed type set; name pattern; schema bounds | 400 |
| `messages[].tool_call_id` | with role `tool` | str | pattern; tied to earlier call | 400 |
| `messages[].tool_calls[]` | no | list | `max_tool_calls`; id pattern; unique | 400 |
| `function.arguments` | with a call | JSON string | `arguments_bytes`; depth/nodes; no NaN | 400 |
| `logprobs` | no | bool or null | only `false`/`null` | 400 `unsupported_parameter` otherwise |
| Content part type | | `text` only | | 400 `unsupported_content_part` |
| Upstream `tool_calls` fragment | | per decoder | index range, string fields | `stream_malformed_upstream` |
| Upstream `finish_reason` | | closed set | | `stream_malformed_upstream` |

A refusal echoes only a field name matching `_ECHOABLE_PARAM`, never client text.

## Invariant ownership

| Invariant | Layer that enforces it | Why there |
|---|---|---|
| Every text byte sent upstream is a verified delta, a scanned-ALLOW name/id, or a closed-set literal | `edge` (`upstream_body`, `upstream_tools`) | It is the last step before `dispatch`, so nothing later can add bytes; P3 asserts it. |
| Input fold is total over input channels | `edge/wire/openai_fold.py` at import | Same place the existing C21 check lives; the build fails on divergence. |
| Per-call output isolation | `egress` (channel key per index) | The scanner already scans per key; no new mechanism. |
| Declared-name, id shape, JSON validity, decoded residual | `edge/wire/tool_stream.py` | Needs the request's declared set, which only `edge` holds. |
| No bytes before the response is known | `edge/stream_response.py` | HTTP objects exist only in `edge`. |
| Bounds | `runtime/resources.py` | The one capacity-literal home (gate). |
| No raw leak on cancel | `edge` `finally` plus `egress` discard | Unreleased held bytes die with the task. |
| Refusals make zero provider calls | `edge/openai_chat.py` lifecycle ordering | Unchanged; all new checks run in parse or staging. |

## Layering, gates and size

- `import-linter`: the new `edge/*` modules import only `edge` siblings and lower layers. `edge/wire/*` imports `domain`, `runtime` and the `egress.stream` value types only (`DownstreamFrame`, already imported by the shipped `edge/wire/sse.py`; no duplicate frame type is invented); no `detect`, no `dispatch`, no HTTP object. The rule that `egress` never imports `detect` is unchanged. `egress` gains only a `domain.channels` import. `ChatRoute` in `chat_stream.py` is `TYPE_CHECKING` only.
- `check_capacity_literals`: the new constants live in `resources.py`; the mixin modules hold references only.
- `check_no_module_mutable`: new module-level values are `frozenset`, tuples and `MappingProxyType`. Per-request mutables are instance state.
- `check_frozen_dataclasses` and the `slots` rule: `ToolBounds`, `StreamIdentity`, `ChunkEvent`, `ToolFragment`, `ToolsTemplate`, `TemplateToolCall`, `FillRefused` are `@dataclass(frozen=True, slots=True)`.
- `check_http_outside_edge_resolve`: no HTTP object outside `edge`.
- `check_sizes`: the splits come first and `check_sizes` is re-run after each split and each behaviour task (acceptance criterion). Per-module line budget (limit 800 lines per module, 120 per function), measured from today's sizes:

| Module | Today | Plan | Target |
|---|---|---|---|
| `egress/stream.py` | 800 | Extract the held-tail helpers and the new `_pass_meta` branch's support into `egress/stream_meta.py` FIRST (at least 40 lines moved), then add the branch | <= 780 |
| `edge/wire/sse.py` | 772 | Move `SSEChoice`/`SSEChunk`/`ChunkEvent`/`ToolFragment` to `sse_chunks.py` AND the whole `SSEDecoder` to `edge/wire/sse_decode.py` (re-exported from `sse.py`); encoder additions stay in `sse.py` | `sse.py` <= 600, `sse_decode.py` <= 500 |
| `runtime/resources.py` | 800 | Two mixin moves (Group-F accessors, stream-timeout accessors, about 50 lines), third move (`mcp_bounds`) if needed; then add ONLY the constants in "ToolBounds values"; `ToolBounds` and `tool_bounds()` live in `resources_tools.py` | <= 790 |
| `edge/routes.py` | 799 | Move streaming orchestration to `chat_stream.py` (about 280 lines) | <= 560 |
| `edge/openai_chat.py` | 712 | Tool rendering in `upstream_tools.py`; only call sites added | <= 760 |
| `edge/openai_nonstream.py` | check at task start | `validate_tool_output` goes to `edge/wire/tool_stream.py` if it would pass 750 | <= 780 |

If any target cannot be met, the task stops and splits further before adding behaviour; it never raises the gate.

## Testing

### Unit (pure, offline)

`openai_tools` validation tables; canonicalisation; fold totality; decoder cases (split fragments, repeated id, bad shapes, `finish_reason`); assembler state machine with a fake `decoded_clean`; encoder framing; renderer output; `StreamResponse` state machine with a fake `send`; `ToolBounds` derivation and fail-closed; the accepted/refused parameter partition table.

### Property tests

Seeded `random.Random`, at least 100 iterations, tagged `# Feature: gw34-sdk-compat, Property N: ...`.

- P1 partition/interleave accumulation identity, using a reference accumulator that mirrors the SDK's `accumulate_delta`; when the SDK is importable the real one runs too.
- P2 fold totality both ways.
- P3 no unscanned byte upstream, including a secret placed in a tool name, a `tool_choice` name, an assistant call id and a `tool_call_id` (each must be `input_blocked` with zero provider calls).
- P4 redaction never releases invalid JSON arguments or JSON-mode content.
- P5 for every refusal code and stream flag the response is plain HTTP, never `200 text/event-stream`, before the first byte.
- P6 SSE encode then decode round trip with tool fragments and `finish_reason`.
- P7 redacting call A leaves call B byte-identical.
- P8 the accepted and refused field sets partition a generated field-name corpus.

Every refused path asserts zero provider calls. Every output termination asserts zero released bytes of the offending call.

### Existing tests to update (R7.8)

Generated list (from `grep -rlE "render_sse|_stream_response|_finish_stream|_render_refusal|text/event-stream" tests`; the implementation task re-runs the grep and treats its output as the authority): `tests/dispatch/test_gw36_s1_openai_upstream.py`, `tests/edge/test_dev_profile_components.py`, `tests/edge/test_edge_live_l16b.py`, `tests/edge/test_lgw12_app.py`, `tests/edge/test_lgw12_asgi_conformance.py`, `tests/edge/test_lgw12_errors.py`, `tests/edge/test_lgw15_errors.py`, `tests/edge/test_lgw15_inflight.py`, `tests/edge/test_lgw15_stream.py`, `tests/edge/test_responses_route.py` (must pass unchanged), `tests/edge/test_stream_output_trace.py`, `tests/edge/test_v2_leaks_enforcement_preservation.py`, `tests/edge/test_v2_leaks_f_output_budget_properties.py`, `tests/openai_conformance/test_malformed_sse_frame.py`, `tests/openai_conformance/test_openai_sdk_compat.py`, `tests/openai_conformance/test_openai_sdk_compat_live_uvicorn.py` (the last three are on the pyproject ignore/legacy list; confirm they are not collected before editing). Fake contracts need NO edit (the lazy `tool_bounds` resolution above); a test in the unchanged six `test_lgw15_*` files continues to pass as-is. Also the disconnect-stub harnesses listed in Cancellation (R5-3), converted to `receive_until_sent`. Also grep-driven additions for files that reference the same symbols without the SSE literal: `tests/edge/test_lgw15_output.py`, `tests/edge/test_lgw15_dispatch.py`, `tests/edge/test_v2_leaks_d_lifecycle.py`, `tests/edge/test_lgw12_inflight_control.py`, `tests/runtime/test_lgw12_timeouts.py`, `tests/openai_conformance/test_gw15_chat_surface_v2.py`. Narrative of the known ones:

`tests/edge/test_lgw15_stream.py` (calls `_stream_response`), `tests/edge/test_lgw15_inflight.py` (calls `ChatRoute._finish_stream` and imports `_ProducedModeHolder`), `tests/edge/test_lgw15_input_block.py` and `tests/edge/test_lgw15_refusal.py` (streaming refusal as SSE after 200), `tests/edge/test_lgw12_asgi_conformance.py` and `tests/edge/test_lgw12_app.py` where they assert an SSE-wrapped pre-byte error, `tests/edge/test_lgw15_fold.py` and `tests/edge/test_lgw15_openai_codec.py` (refused `tools`/`tool_calls` now accepted), `tests/edge/test_gw36_s1_upstream_body.py`. `tests/edge/test_responses_route.py` must pass unchanged. The implementation tasks list each by name.

### L34-1 matrix (`tests/openai_conformance/sdk_matrix/`)

- `harness.py`: builds the genuine `build_app` with a `ScriptedProvider` (review R5-5), NEW test code and not the shipped `StubProviderClient` (which replays one fixed script for every `open()` and has a one-way `_aborted` latch, so one shared instance could not select by marker and would yield nothing after the first abort, breaking R6.2). `ScriptedProvider.open(request)` selects a script from `request.body["messages"]` (marker in the last user message) and creates fresh per-call state; it records per-call `closed` and `aborted` counters, which R6.1 asserts (iterator closed after a client cancel) and R6.2 relies on (a second request after a cancel succeeds). One app serves every cell. One helper builds each SDK client changing only `base_url`, `api_key`, `http_client`. A counting ASGI wrapper records requests for L34-2.
- Second harness mode, `wire` (finding 5): the app is built with the real `OpenAIUpstreamClient` over an `httpx.MockTransport` (ASGI cells) or a loopback fake upstream (`fake_upstream.py`, uvicorn on `127.0.0.1:0`, used by loopback and Node cells) that emits OpenAI-format SSE bytes. The fake upstream splits tool-call fragments at arbitrary byte offsets (mid-JSON-string, mid-escape, mid-UTF-8 sequence, mid-SSE-line) from a seeded `random.Random`. Cells (d) streaming tool-call deltas, (e) `[DONE]` and (j) the combined tool-call-with-split cell run through `wire` mode for the Python (sync and async) and Node SDKs, so the real `SSEDecoder` tool parsing, `_drain` one-frame-behind `final`, and the edge assembler are exercised through a stock SDK. The scripted mode remains for the rest.
- `serve_scripted.py`: runs the same app under uvicorn on `127.0.0.1:0`, prints the port; used by streaming and cancel cells and by the Node cells.
- `test_py_sdk_matrix.py`: cells (a) to (j) of R1.2 for the sync and async clients. `importorskip("openai")` and `importorskip("httpx")`.
- `node/gw34_matrix.mjs` + `test_node_sdk_matrix.py`: cells (a) to (j) for Node; abort via `stream.controller.abort()`. Skip with a reason when `node` or the `openai` module is not resolvable offline; never installs.
- `test_asgi_transport_probe.py`: proves `ASGITransport` buffers, which is the evidence for D8.
- `test_l34_2_files_404.py`: `/v1/files` returns `404 endpoint_not_found`, `max_retries=0`, exactly one request seen.
- `test_wire_profiles.py` + `wire_profiles.py`: a frozen table of cases (client family, request shape, expected outcome). Covers `stream_options.include_usage`, `tool_choice` variants, `parallel_tool_calls: false`, assistant+tool round trips, text-part arrays, `user`/`metadata`/`seed`/`max_completion_tokens`, an Azure path (404) and `api-key` header (401).
- `conftest.py`: `pytest_terminal_summary` prints and writes a ran/skipped report per client and cell, so a skip is never read as a pass.
- Assertions common to all cells: `[DONE]` is last and exactly once; nothing follows an error frame or `[DONE]`; `amf.trace` never surfaces as a chunk; content types; `x-request-id` on every response; stable `id`/`model` across chunks; typed SDK objects only (no `model_extra`).

### SDK execution environment

The host has `node` and `docker` but no `openai` Python module, no `uvicorn` and no `node_modules`, and the shipped gateway-v2 image is a Python image with no Node. A card that closes with every SDK cell skipped would leave the core claim (stock SDKs work) unproven, so the recipe is defined here:

- New `tests/openai_conformance/Dockerfile.sdk-matrix` (test tooling, not a runtime image): base `python:3.12-slim`, `pip install -e .[dev,serve,conformance]` (the `conformance` extra carries `pytest-asyncio` for the async cells) plus an exact-pinned `openai` Python package given by `ARG OPENAI_VERSION` (no constraints file exists; `pyproject.toml` only has `openai>=1.0`, so the pin lives in the Dockerfile and is echoed into the evidence report), a Node 22 stage, and `npm ci` from the committed `tests/openai_conformance/node/package-lock.json` (node `openai` 4.104.0). It runs with `--network host` so the loopback uvicorn server and the clients share `127.0.0.1`. **Build versus run (review R4-5):** `docker build` needs the network (`pip`, `npm ci`); the test RUN does not and is executed with no outbound access. Everywhere this document says "offline" it means the test run, not the image build.
- **Preflight task (first task of the card, before any code).** Run `docker build -f tests/openai_conformance/Dockerfile.sdk-matrix` and record success or the network failure (with the error text) in `docs/plans/evidence/<date>-gw34/sdk-image-build.json`. On build failure the card closes as "offline suites only, SDK cells open": pure, property and wire-profile tests run on the host, the exit note does not claim L34-1, and the ran/skipped report shows `ran == 0` for the SDK cells. A build failure never costs a test run.
- `make sdk-matrix` (or the documented `docker run` line in the file header) runs `pytest tests/openai_conformance/sdk_matrix -rs` inside it.
- `conftest.py` writes the report to `<repo root>/docs/plans/evidence/<date>-gw34/sdk-matrix-report.json`; the repo root is resolved with `Path(__file__).resolve().parents[N]` to the directory containing `docs/` (tests run with `gateway_v2/` as the working directory, so a relative path would be wrong), and the path is created if missing with, per client (python-sync, python-async, node) and cell (a) to (j): `ran`, `passed`, `skipped(reason)`, plus the environment (image digest or `host`, Python, `openai` versions, Node version).
- Closing rule: the card exit note states which environment produced the evidence and the ran/skipped counts per client. If the Python SDK or Node loopback cells have `ran == 0`, the card stays open and the exit note says so. On the bare host the pure and property tests still run, but they do not substitute for the SDK cells.

## Deferred live gates (not claimed)

L34-1 against a real v2 boot (Docker or staging), L34-2 live, the real framework-library runs (LangChain, LlamaIndex, Vercel AI SDK, LiteLLM, Azure client), and Node against real TCP beyond the loopback harness. The card's exit note states the offline matrix result and these four deferrals.

## Backlog

- Responses stream pre-byte refusals as plain HTTP (D7a).
- Usage chunk for `include_usage` (D10).
- Azure deployment-path support (D12).
- Release of tool calls before end of stream under an enforcing rule, if a measured latency need appears (D6a).
- `strict: true` schema validation of model output.

## Requirements traceability

R1, R2: L34-1 matrix and wire profiles. R3: request side. R4: response side and decoder. R5: chunk framing. R6: cancellation. R7: pre-first-byte. R8: `response_format` and D9. R9: D3, D5, partition table and P8. R10: gates, `ToolBounds`, P1 to P8, zero-call assertions, deferred note. R11: existing codes only, counts only, no logging of content. R12: the Decisions table.

Assumption notes: A1 to A12 are adopted as written in the requirements, with these refinements: A5 refuse invalid-JSON history (D4); A8 withhold only under an enforcing rule, released at end of stream (D6a); A7 Responses keeps its behaviour (D7a). The R1.1 wording about `ASGITransport` is narrowed by D8 for streaming and cancel cells only.

## Responses to design review findings

| # | Severity | Disposition | Where addressed |
|---|---|---|---|
| 1 | HIGH | Addressed. `finish_reason` is only recorded. New `ToolCallAssembler.finish()` is called by `_attempt()` after `pipeline.run(...)` returns cleanly and before `note_stream_output`. "Next index begins" and `finish_reason` are explicitly not release points. A held-final-tail test is required. | D6a; Response side "Enforcing", "Finish", "Assembler API, end of stream, and termination mechanism" |
| 2 | HIGH | Addressed. New scanned channels `tool_name` and `tool_call_id`, BLOCK-only (REDACT escalated to BLOCK), covering every name/id occurrence including `tool_choice.function.name`. Fold-totality list extended; P3 extended with a secret in a name and in an id. | Domain channels; "Tool names and ids"; Rendering; P3; invariant table |
| 3 | HIGH | Addressed. The stream body runs as an inner task; the watcher cancels only that task and only while `StreamingDone` is unset, which `handle` sets before the terminal write. `handle`'s `finally` always runs. Tests for normal completion (one audit record) and cancel. | D14; Cancellation (R6) |
| 4 | MEDIUM | Addressed. One path: assembler returns `Terminate(code)`; `_sink` kills the latch, records the code and raises private `_AssemblerTerminated`, caught in `_attempt()` and not counted as `incomplete`. Tests in all three pipelines. Non-enforcing first-fragment output_blocked is stated to be pre-byte. | Assembler API subsection; error table |
| 5 | MEDIUM | Addressed. Per-module line budget table; `stream_meta.py` extracted before `stream.py` grows; `SSEDecoder` moved to `sse_decode.py`; `ToolBounds` in `resources_tools.py`; `check_sizes` after each split is an acceptance criterion. `openai_nonstream.py` size is flagged for measurement at task start. | Layering, gates and size; file plan |
| 6 | MEDIUM | Addressed. Defined test image (`Dockerfile.sdk-matrix`: gateway-v2 `[dev,serve]`, pinned `openai`, Node 22, `npm ci` from lockfile, host networking), a per-client ran/skipped report, and a closing rule requiring non-zero ran for Python SDK and Node loopback cells. | D8; "SDK execution environment" |
| 7 | LOW | Addressed. Realistic-id/name detector test required; any REDACT on `output_tool_id`/`output_tool_name` is terminal with distinct audit detail `tool_identity_redacted`. | Assembler Validation bullet |
| 8 | LOW | Addressed. Stated that Responses shares framing and the assembler, constructed with `tools_declared=False`; one assertion added to the Responses suite. | D7a; Responses |
| 9 | NIT | Addressed. Legacy `function_call` finish reason and delta rejection documented. | D1b |

## Responses to design review findings (round 2)

Findings from `design-review.json`/`design-review.md` (13 findings). The earlier table above answers round 1.

| # | Severity | Disposition | Where addressed |
|---|---|---|---|
| 1 | HIGH | Addressed. C26 `_part_count` would refuse realistic tools requests. `max_request_parts()` gains a schema-node allowance derived from `ToolBounds`; byte counter unchanged; tests: 20-tool one-worker request admitted, part flood still refused. | D20; "C26 part-count allowance"; "ToolBounds values" |
| 2 | MEDIUM | Addressed. Span-to-pair attribution in `input_enforcement` using `_base_offsets`; a non-ALLOW per-finding decision on a `BLOCK_ONLY_CHANNELS` range blocks the request; P3 cases with REDACT-class values in a name, `tool_choice` name and ids. | D23; "BLOCK-only attribution" |
| 3 | MEDIUM | Addressed. First byte is the first non-empty content/reasoning/refusal/tool delta; role-only, empty and meta-only frames are held in `StreamResponse` and flushed with the first real frame or at a clean finish; tests with an empty role chunk then failure. | D21; "Deferred start" |
| 4 | MEDIUM | Addressed. Discriminator `inner.cancelled() and current_task().cancelling() == 0`; other branch cancels/awaits the inner task and re-raises; a test per branch. | D22; Cancellation |
| 5 | MEDIUM | Addressed. Second harness mode `wire` with the real `OpenAIUpstreamClient` over `MockTransport`/loopback fake upstream and seeded arbitrary byte splits; cells (d), (e), (j) for Python sync/async and Node. | L34-1 matrix; D8 |
| 6 | MEDIUM | Addressed. Full keyword table with per-keyword class, `$ref` rule (local pointer, must resolve after fill), pydantic wire-profile case. | "Schema keyword table"; D1a |
| 7 | MEDIUM | Addressed. Values and rationale for every `ToolBounds` field; new `max_scanned_slots` from p99 (500 at 20 ms), refused above it; calibration benchmark and boundary tests. `_SCAN_SLOT_COST_US = 20` is an initial value that the benchmark can only raise. | D24; "ToolBounds values" |
| 8 | LOW | Addressed. `allow_nan=False` plus `ValueError` catch; surrogate code points refused in the iterative walk. | D25; "Arguments canonicalisation" |
| 9 | LOW | Addressed. Quantified (about 46 lines to free), two named moves with `check_sizes` after each, third move conditional. | File plan `runtime`; size table |
| 10 | LOW | Addressed. `ARG OPENAI_VERSION` exact pin (no constraints file exists), `.[dev,serve,conformance]`, evidence path resolved from the repo root. | "SDK execution environment" |
| 11 | LOW | Addressed. Generated grep list recorded; the task re-runs the grep as the authority. Some files are on the pyproject legacy ignore list and must be confirmed uncollected before editing. | "Existing tests to update" |
| 12 | LOW | Addressed by dropping the distinct `tool_identity_redacted` detail (no detection mechanism in the assembler); shape/declared-set checks still terminate `output_blocked`; a char-deleting redaction case is documented as harmless and tested. | Assembler Validation bullet |
| 13 | NIT | Addressed. Lookup claim reworded (`base_channel` only for renderer/assembler); choice `index >= 1` ignored, non-int index malformed; scan-only pairs excluded from cost. | D26, D27; Channel grammar |

Assumption risk carried forward: SDK cells need network at image-build time; the closing rule keeps the card open if Python SDK or Node loopback `ran == 0`.

## Responses to design review findings (round 3)
Findings from `design-review.json`/`design-review.md` (8 findings). Rounds 1 and 2 are answered above.
| # | Severity | Disposition | Where addressed |
|---|---|---|---|
| 1 | MEDIUM | Addressed. `tool_bounds` is resolved once at `build_app` via `getattr` into a `ChatRoute` field; per request nothing calls `contract.tool_bounds()`. A contract without it (the standalone `test_lgw15_*` fakes) yields a tools-disabled route: tool-bearing requests are refused `unsupported_parameter` (fail closed), plain traffic works, decoder `max_tool_calls=0`. No fake edit needed; a test covers both. | Decoder; "Existing tests to update" |
| 2 | MEDIUM | Addressed. Delta keys in `META_CHANNELS`, `role`, containing `#`, or starting `output_tool_` are `stream_malformed_upstream`; assembler/egress treat unexpected `#` keys as malformed, never a new call; one decoder test per case. | Decoder |
| 3 | MEDIUM | Addressed. `finish()` is `async` and awaited in `_attempt`; `feed` stays sync; "no I/O" reworded to "except the injected scan callable"; non-stream uses `decoded_clean_sync`. | Assembler API |
| 4 | MEDIUM | Addressed. `max_request_parts()` unchanged; `max_request_parts_with_tools()` used only when the body contains `tools`, `tool_calls` or `response_format` bytes; tests keep 257 commas refused for tool-free bodies. D20 row updated. | D20; "C26 part-count allowance" |
| 5 | MEDIUM | Addressed. Worked slot arithmetic for 20 and 40 tools; target "40-tool pydantic request admitted with headroom"; structural strings joined into one BLOCK-only run per tool (about 8 slots/tool); detector measured at task start and recorded; fallback is widening joins before refusing; tests assert headroom (<= 70% of cap), not equality. | "ToolBounds values" |
| 6 | LOW | Addressed. Layering text now allows the `egress.stream` value types in `edge/wire`. | Layering, gates and size |
| 7 | LOW | Addressed. Overflow never starts the response: at most one role and one finish frame are held, extra empty frames are dropped; constant `_HELD_FRAME_SLOTS = 2` named; latch only set by `write`; keep-alive flood test. | Deferred start |
| 8 | NIT | Addressed. Input-side ALLOW measurement test for UUID, `call_`, `toolu_` ids and snake/camel names on BLOCK-only channels. | "ToolBounds values" |

## Responses to design review findings (round 4)
Findings from `design-review.json`/`design-review.md` (6 findings). Rounds 1 to 3 are answered above.
| # | Severity | Disposition | Where addressed |
|---|---|---|---|
| 1 | MEDIUM | Addressed. New BLOCK-only scan-only channel `tool_schema_struct` (seven new input channels, in `BLOCK_ONLY_CHANNELS` and `_FOLDED_INPUT_CHANNELS`). Property names, `$defs` keys, `required`, `title`, `$ref`, `$anchor`, `$id`, `$schema`, `format`, `pattern`, `enum`/`const` members move to it; only `description`, `default`, `examples` stay forwarded redactable slots. Key-collision and dangling-`$ref` `input_unmaskable` paths removed except an assertion for object keys inside `default`/`examples`. P3 cases for an email in a property name, `$defs` key, `required` entry and `enum` member. | "domain", "edge/wire", "`tools`", "Schema keyword table", "ToolBounds values" |
| 2 | MEDIUM | Addressed. One rule: REQUIRED 20 tools plus history <= 70% of cap (228 of 500, 46%); STRETCH 40 tools <= 80% (388 of 500, 78%) valid only when the measured cost is <= 20 us, otherwise a reported finding in the exit note. Test numbers match the arithmetic. `_SCAN_SLOT_COST_US` is derived from the benchmark p99; `target_p99_ms` is a named input. | "Slot arithmetic and the intended minimum", "Measurement at task start" |
| 3 | MEDIUM | Addressed. `finish()` requires shape-valid id, declared name and arguments for every index seen, in both modes; gaps give `stream_malformed_upstream`, undeclared name `output_blocked`, `finish_reason: "tool_calls"` with no calls `stream_malformed_upstream`. One test per gap. | "Tool-call assembler" |
| 4 | LOW | Addressed. Empty or whitespace-only arguments normalised to `{}` on both sides, recorded as a known diff. | "Arguments canonicalisation", "Tool-call assembler" |
| 5 | LOW | Addressed. Build versus run distinguished in D13 and the environment section; first task builds the matrix image and records the result; on build failure the card closes as offline suites only, SDK cells open, L34-1 not claimed. | D13, "SDK execution environment" |
| 6 | NIT | Addressed. Pass-through needs no meta branch; stated in "Egress" and the file plan. | "Egress" |

## Responses to design review findings (round 5)
Findings from `design-review.md` (6 findings: 4 MEDIUM, 2 LOW). Rounds 1 to 4 are answered above. The design is approved by the orchestrator on condition that these are applied; no further review loop is opened.
| # | Severity | Disposition | Where addressed |
|---|---|---|---|
| 1 | MEDIUM | Addressed. `finish()` is called only under an explicit guard: `not stats.incomplete`, `kill_latch.reason() is None`, no pending error code, `sent_terminal` unset. A terminal `error_code` frame sets `sent_terminal` in both the pending and started states, so correctness does not rely on the shipped `_sink` dropping frames. On any egress terminate (`output_blocked`, `scan_failure`, `holdback_overflow`, `killed`) the withheld calls are dropped: zero tool bytes, no `finish_reason: "tool_calls"`, plain HTTP error when pending. One test per code with a withheld call outstanding. | "Assembler API, end of stream, and termination mechanism", step 3 |
| 2 | MEDIUM | Addressed. New `ToolBounds.max_withheld_bytes = stream_buffer_bytes(active_streams())` (existing methods, no new constant, no literal). The assembler keeps one running total over arguments, ids, names and JSON-mode `content` and charges it to the request's own `Coalescer` (`offer`/`release`), so the per-stream ceiling is not doubled. Overflow is `stream_malformed_upstream` for tool bytes, `output_blocked` for JSON-mode content. Effective per-call bound is `min(arguments_bytes, max_withheld_bytes)`. GW34 adds NO file to `check_capacity_literals.ALLOWED_REL`; the mixin modules hold derivations only; GW27 D-13 (`runtime/resources_org.py`) is neither relied on nor duplicated, and any cross-card need for a further file is recorded in the exit note. Test: 128 calls at `arguments_bytes` gives a terminal code with bounded peak. | "ToolBounds values", D9, "Tool-call assembler" |
| 3 | MEDIUM | Addressed. Rule: `http.disconnect` before any send is a real early cancel (`stream_killed`, pinned by a new test). Shared helper `tests/edge/asgi_receive.py::receive_until_sent` blocks until the response completes. The 11 shipped harnesses that reach a streaming chat route are named and converted; the task re-runs `grep -rn "http.disconnect"` as the authority. They are added to the R6/R7.8 list. | "Cancellation (R6)", "Existing tests to update" |
| 4 | MEDIUM | Addressed. No `max_request_parts_with_tools()`. `ToolBounds.parts_allowance` carries the node allowance and `_pre_checks` picks the ceiling from the route-level `self.tool_bounds`; `None` always uses `contract.max_request_parts()`. D20 reworded. The fake-contract test now sends a body containing `tools` and asserts the declared `unsupported_parameter` refusal, not a 500. | D20, "C26 part-count allowance", "ToolBounds values" |
| 5 | LOW | Addressed. The harness uses a new `ScriptedProvider` (test code, not the shipped `StubProviderClient`): `open(request)` selects the script from `request.body["messages"]`, fresh per-call state, per-call `closed` and `aborted` counters for R6.1 and R6.2. | "L34-1 matrix", `harness.py` |
| 6 | LOW | Addressed. `_HELD_FRAME_SLOTS = 2` removed; the held list is two named slots `_ROLE_SLOT` and `_FINISH_SLOT`, so no integer bound exists. | "Deferred start" |

## Deviations

Recorded during implementation (full table with rationale: `.agents/tasks/gw34/deviations.md`).

- V1 Joined runs: each tool channel is ONE fold pair per request (separator-joined, split back after the decision) instead of one pair per string, because the measured detector cost is ~7x the assumed 20 us. This is the "widening subtree joining" mitigation. Supersedes the slot arithmetic tables above; the 20-tool request now uses 8 of 50 slots.
- V2 `JOIN_SEPARATOR` is U+E000 (ASCII `\x1c`-`\x1f` match `\s` and created cross-item false positives).
- V3 `_SCAN_SLOT_COST_US = 200` (measured p99 152.1 us), `max_scanned_slots = 50` at 20 ms.
- V4 `ToolBounds.max_withheld_bytes = stream_buffer_bytes(connection_budget())` (the contract has no `active_streams()`).
- V5 The assembler re-numbers output call indexes dense and ascending in first-emission order: the stock SDK's `accumulate_delta` merges by list position.
- V6 `delta.role` is not a reserved/malformed key (providers send it); `finish_reason`, `#`-keys and the `output_tool_` prefix are.
- V7 BLOCK-only escalation covers REDACT and BLOCK; FLAG (monitor) stays FLAG.
- V8 `StreamResponse` holds no frames: the encoder adds `role` to the first written chunk and meta/empty frames never become chunks.
- V9 Tool-call counts are in the audit `detail` (digits) and on `RequestHolds`; no new Publisher series (pinned `SERIES_COUNT`).
- V10 All Python SDK cells use the loopback server (sync `httpx.Client` cannot use `ASGITransport`).
- V11 `x-request-id` is echoed on the 200 SSE start. V12 `SSEEncoder.content(frame)` emits one merged choice.
- V13 RESOLVED (owner decision, 4 Nov 2026): `amf.trace` could not be hidden from the stock SDK iterators while it was a named event (Python yielded a bare chunk with no choices; Node's `chat.completions.stream()` helper threw "chunk.choices is not iterable"). The owner chose to reframe it as an SSE COMMENT line, `: amf.trace {compact json}\n\n`, same payload, same position (after the finish chunk, before `[DONE]`). Every compliant SSE parser skips a comment, so R1.6 now holds and both SDK matrices assert it (Python `_content_chunks`, Node `contentChunks`; Node cell `c` is a normal passing test, the strict xfail is gone). `SSEDecoder.trace` exposes the comment to trace consumers while content decoding skips it; the Responses translator, the frontend `consumeSSEStream` and the tests read the new framing. Consumer list: `.agents/tasks/gw34/trace-comment-gate.md` and the exit note.
