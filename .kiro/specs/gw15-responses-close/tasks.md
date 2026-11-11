# Implementation Plan: Close GW15 `/v1/responses`

## Overview

Turn the approved design into code-generation steps that replace the HTTP 501
`POST /v1/responses` placeholder with a format adapter over the shipped chat
lifecycle. Chat, completions, and embeddings stay as they are. MCP, RAG, and
vector stay HTTP 501.

Work is bottom-up. Each wave is gate-checkable before the next one builds on it.
The chat lifecycle is wired in, not rebuilt. No second scanner, resolver, plan
surface, or budget.

Repo `/home/suraj/Documents/zeroshield/AI_Mesh_Firewall`, branch `suraj-revamp`.
Python commands run from `gateway_v2/` with `.venv/bin/python`, `.venv/bin/mypy`,
and `.venv/bin/ruff`.

Do not commit unless the operator asks. Do not push. Do not edit
`gateway/ai_mesh_gateway/`. Do not write Redis key `{rv2}:dev:firewall-plan`.
Do not mark GW15 done in the runbook. This plan does not do that.

### Conventions enforced in every coding task

- Value types are frozen slotted dataclasses. Module-level tables use
  `MappingProxyType`. No module-level mutable.
- No capacity literal outside `runtime/resources.py`. The response-store TTL is
  the one new literal, and it lives there.
- `egress` does not import `detect`. The new modules live in `edge` and call
  `ChatSurface`. They do not take their own scanner.
- Fail closed. A translate refusal, a store miss, and a store write failure make
  zero provider calls. `fail_open_total` stays 0.
- A new module stays at or under 800 lines. A new function stays at or under 120
  lines. `routes.py` is 794 lines today. If an edit would pass 800, move the
  helper into `openai_responses.py` instead of shrinking comments to fit.
- `mypy --strict` and ruff (line length 100) clean on every product file the
  wave touches.
- Property tests use a seeded `random.Random` for at least 10,000 iterations.
  No hypothesis. Tag each one
  `# Feature: gw15-responses-close, Property N: <text>` and
  `# Validates: Requirements X.Y`.
- The v1 suite in `tests/openai_conformance/test_openai_sdk_compat.py` keeps
  driving the v1 app. Do not change its `content_filter` cell. The v2 proof is
  a new module.

### Import cycle

`openai_responses.py` may import `ChatRoute` from `routes.py`. `routes.py` must
not import `openai_responses` at module level. `build_routes` receives an
already-built `ResponsesRoute`. Annotate that parameter under `TYPE_CHECKING`.
`app.py` imports both and is the only module that constructs the route.

## Tasks

- [x] 1. Response-store TTL on the resource contract
  - [x] 1.1 Add `response_store_ttl_s` **[product-code]**
    - Modify `gateway_v2/gateway_v2/runtime/resources.py`
    - Add a named constant for the v1 retention of 30 days, written out as
      seconds in this file only
    - Add `ResourceContract.response_store_ttl_s(self) -> int` returning that
      constant. No env override in this card
    - Callers in `edge` use the method. They do not spell the number
    - _Design: Components §4_
    - _Requirements: 8.1, 9.4_

  - [x] 1.2 Check the runtime module **[product-code]**
    - `mypy --strict gateway_v2/runtime/resources.py` exits 0
    - `ruff check gateway_v2/runtime/resources.py` exits 0
    - The capacity-literal AST gate still exits 0, and the new number is only
      in this file
    - The module stays at or under 800 lines
    - _Requirements: 9.4_

- [x] 2. Pure Responses-to-chat translation
  - [x] 2.1 Implement `translate_request` and `response_object` **[product-code]**
    - Create `gateway_v2/gateway_v2/edge/wire/responses.py`
    - Pure. No I/O, no scanner, no import of `detect` or `egress`
    - `translate_request` returns either a chat JSON object or a
      `TranslateRefusal(code, param)`
    - String `input` becomes one user message. An item list maps `input_text`,
      `output_text`, and `text` to message text. `function_call` becomes an
      assistant `tool_calls` entry. `function_call_output` becomes a tool
      message. `mcp_call`, `mcp_approval_request`, `mcp_approval_response`,
      `mcp_list_tools`, and `custom_tool_call_output` become tool messages so
      their text is scanned and not dropped. This is not the MCP route
    - `instructions` becomes the system message and is placed before the user
      turn
    - `max_output_tokens` becomes `max_tokens`. `max_completion_tokens` is
      copied under its own name
    - Pass through `temperature`, `top_p`, `tools`, `tool_choice`,
      `parallel_tool_calls`, `stop`, `seed`, `user`, `metadata`, `stream`,
      `response_format`, and `n`. Map `text.format` to `response_format` only
      when the body did not already set `response_format`
    - Replay messages passed in by the caller are prepended after the system
      message and before the new user turn. This function does not read the
      store
    - Refusal codes, as frozen string constants in this module:
      `responses_malformed_body` (not a JSON object),
      `responses_invalid_model` (`param` `model`) when `model` is missing or
      not a string, `responses_missing_input` (`param` `input`) when both
      `input` and `instructions` are absent, `unsupported_content_part`
      (`param` `input`) for any other content-part type, including
      `input_file`, `input_audio`, and `input_image`
    - Do not add `malformed_request_body` to the chat envelope. Chat's unmapped
      500 for that spelling stays
    - `response_object` builds `id` as `resp_` plus 24 hex bytes from
      `secrets.token_hex`, `object` `response`, `status` `completed`,
      `output_text` from the enforced assistant content, `function_call` output
      items from tool calls, and usage when the chat body has it
      (`prompt_tokens` to `input_tokens`, `completion_tokens` to
      `output_tokens`). The `model` field is the caller's model
    - Copy `zeroshield` and `pipeline_trace` from the chat body when present.
      Drop any key named `evidence` from that copy
    - Each function stays at or under 120 lines. Split helpers rather than
      growing one function
    - _Design: Components §1_
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 4.5, 7.5_

  - [x] 2.2 Property test: text channels survive translation **[test-file]**
    - Create `gateway_v2/tests/edge/test_responses_translate.py`
    - **Property 1: Every text channel is present in the chat messages**
    - **Validates: Requirements 2.1, 2.2, 2.3**
    - Seeded `random.Random`, at least 10,000 iterations. Place a canary, one
      field at a time, in string `input`, a text part, `instructions`,
      `function_call` arguments, and `function_call_output`. Assert the canary
      appears in the translated chat messages and the result is not a refusal
    - _Design: Components §1_

  - [x] 2.3 Property test: an unsupported part is never dropped **[test-file]**
    - In `tests/edge/test_responses_translate.py`
    - **Property 2: A part outside the translated set refuses**
    - **Validates: Requirements 3.4**
    - At least 10,000 iterations. A random content-part type outside the
      supported set, always including the fixed cases `input_file`,
      `input_audio`, and `input_image`, returns `unsupported_content_part`
      with `param` `input` and does not return a chat body
    - _Design: Components §1, Deliberate differences (`input_image`)_

  - [x] 2.4 Unit tests for the declared refusals and the response object **[test-file]**
    - In `tests/edge/test_responses_translate.py`
    - Not a JSON object, a non-string `model`, and a body with neither `input`
      nor `instructions` each return the code and `param` from task 2.1
    - A completion with redacted assistant text produces that same text in
      `output_text` and does not contain the raw canary
    - A chat body that carries `zeroshield` and `pipeline_trace` is copied.
      An `evidence` key in that object is absent from the result
    - `id` starts with `resp`
    - _Requirements: 3.1, 3.2, 3.3, 4.2, 4.4, 4.5, 7.1, 7.5_

- [x] 3. Pure chat-SSE to Responses-event translator
  - [x] 3.1 Implement the translator **[product-code]**
    - Create `gateway_v2/gateway_v2/edge/wire/responses_events.py`
    - Pure over captured chat SSE bytes. No I/O
    - A clean text stream emits, in order, `response.created`,
      `response.output_item.added`, `response.content_part.added`, one
      `response.output_text.delta` per released content delta,
      `response.output_text.done`, `response.output_item.done`,
      `response.completed`
    - The last event is `response.completed`. Its `response.output_text` equals
      the joined deltas. Do not emit `response.in_progress`. Do not emit
      `data: [DONE]`
    - Frames are `event: <type>\ndata: <json>\n\n`
    - Copy `zeroshield` and `pipeline_trace` from a chat `amf.trace` frame onto
      the terminal response object. Do not emit `amf.trace` as its own event.
      Drop an `evidence` key
    - A chat frame whose error code is `output_blocked` emits `response.failed`
      and stops. That response has `status` `failed` and `output_text` `""`.
      `response.completed` is absent. Deltas that arrived before the block are
      still emitted. Do not buffer them
    - Split the state machine so each function stays at or under 120 lines
    - _Design: Components §2_
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.3, 7.1, 7.2, 7.3, 7.5_

  - [x] 3.2 Property test: event order and the failed terminal **[test-file]**
    - Create `gateway_v2/tests/edge/test_responses_events.py`
    - **Property 3: Typed events stay in order, and a withhold ends failed**
    - **Validates: Requirements 5.2, 5.3, 5.4, 6.3**
    - At least 10,000 iterations. For a random non-empty delta list, the seven
      required types occur in order, the last delta precedes
      `response.output_text.done`, and the completed `output_text` equals the
      concatenation. For a random prefix of those deltas followed by
      `output_blocked`, the last event is `response.failed`, `output_text` on
      that object is empty, and `response.completed` is absent
    - _Design: Components §2_

- [x] 4. Envelope rows for the new codes
  - [x] 4.1 Map the six codes **[product-code]**
    - Modify `gateway_v2/gateway_v2/edge/errors.py` `_ENVELOPE` and
      `gateway_v2/gateway_v2/edge/wire/sse.py` `error_frame_for`
    - Add only these spellings. Chat does not emit them
      - `responses_malformed_body` → 400 `invalid_request_error`
      - `responses_invalid_model` → 400 `invalid_request_error`
      - `responses_missing_input` → 400 `invalid_request_error`
      - `unsupported_content_part` → 400 `invalid_request_error`
      - `response_not_found` → 404 `not_found_error`
      - `response_store_unavailable` → 503 `service_unavailable`
    - HTTP `error.type` and the SSE frame message come from the same
      `error_frame_for` entry, matching the existing codes
    - Do not map `malformed_request_body`. The preservation test that pins it
      at HTTP 500 stays green
    - _Design: Components §6, Deliberate differences_
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 8.2, 8.4, 8.5_

  - [x] 4.2 Render tests for the six codes **[test-file]**
    - Extend `gateway_v2/tests/edge/test_lgw15_errors.py` or add
      `tests/edge/test_responses_errors.py` if that file is already at the size
      gate
    - `render_http` for each new code returns the status and `error.type` in
      task 4.1, and the JSON `code` is the spelling that was passed
    - `responses_invalid_model` echoes `param` `model`.
      `responses_missing_input` and `unsupported_content_part` echo `param`
      `input`
    - _Requirements: 3.2, 3.3, 3.4, 8.4_

- [x] 5. Org-scoped response store
  - [x] 5.1 Implement the protocol, the memory store, and the Valkey store **[product-code]**
    - Create `gateway_v2/gateway_v2/edge/responses_store.py`
    - Frozen slotted `StoredResponse` with the client response object, the
      replay messages, and the input items. No `Finding` field and no
      `evidence` field
    - `ResponseStore` protocol: `put(org_id, record) -> bool`,
      `get(org_id, response_id) -> StoredResponse | None`,
      `delete(org_id, response_id) -> bool`
    - `MemoryResponseStore` keys by `(org_id, response_id)`. This is what the
      in-process tests inject
    - `ValkeyResponseStore` uses the Redis URL the dev profile already has
      (`AMF_DEV_REDIS_URL`). Key `{rv2}:responses:{org_id}:{response_id}`.
      TTL from `contract.response_store_ttl_s()`. A client error makes `put`
      return false and `get` return `None`. Never read or write
      `{rv2}:dev:firewall-plan`
    - `put` returns false when the record has no `resp` id or the write fails.
      It does not swallow the failure into a success
    - _Design: Components §4_
    - _Requirements: 8.1, 8.6, 8.7, 9.4_

  - [x] 5.2 Property test: one org cannot read another **[test-file]**
    - Create `gateway_v2/tests/edge/test_responses_store.py`
    - **Property 4: Store keys do not cross orgs**
    - **Validates: Requirements 8.4, 8.7**
    - At least 10,000 iterations against `MemoryResponseStore`. Random org ids
      and response ids. After `put(org_a, record)`, `get(org_b, id)` is `None`
      when `org_b` differs, `get(org_a, id)` returns the record, and `delete`
      on `org_b` leaves `org_a`'s record in place
    - A separate unit cell: `put` returning false is observable and does not
      make a later `get` succeed
    - _Design: Components §4_

- [x] 6. `ResponsesRoute` over the shipped chat lifecycle
  - [x] 6.1 Implement create, retrieve, delete, and input items **[product-code]**
    - Replace the empty module `gateway_v2/gateway_v2/edge/openai_responses.py`
    - `ResponsesRoute` holds the booted `ChatRoute` and a `ResponseStore`. It
      does not hold a scanner
    - `POST` reads the body with `edge.asgi_sse.read_body`, the reader
      `ChatRoute.handle` already uses
    - Translate. On a `TranslateRefusal`, render it with `errors.render_http`
      and `emit_response`. Do not call `run_lifecycle`. Provider open count
      stays unchanged
    - When `previous_response_id` is set, call `chat.identity.resolve(scope)`
      before the lifecycle. `None` renders `shared_state_unavailable`, the
      same code `ChatSurface._identity` returns, and does not touch the store.
      A resolved identity and a store miss render `response_not_found` and do
      not call `run_lifecycle`
    - On a hit, prepend that record's replay messages and then call the
      lifecycle
    - Copy the caller scope, set `path` to `/v1/chat/completions`, and call
      `ChatSurface(deps=chat).run_lifecycle` with a fresh `RequestHolds`
    - On `refuse_code`, render JSON through `render_http` and attach
      `console_from_holds` when `holds` has an input decision. Do this for
      `stream: true` as well. Do not call `ChatRoute._render_refusal`
    - On proceed and `stream` false, call `ChatSurface.collect_and_render`
      with a capturing ASGI `send`, then `response_object`. If `store` is
      true, `put` the enforced object and the replay messages taken from the
      masked upstream body plus the enforced assistant text. A false `put`
      renders `response_store_unavailable` and does not write HTTP 200. If
      `store` is omitted or false, do not write
    - On proceed and `stream` true, call `ChatRoute._stream_response` with a
      capturing `send`, translate with the task 3 translator, and write those
      events. A `store: true` clean stream persists the object on
      `response.completed` before that event is written. A failed `put` emits
      `response.failed` instead. A stream that ends `response.failed` is not
      stored
    - `finally` calls `emit_chat_audit` and `holds.release` once, the same
      pair `ChatRoute.handle` uses. A path that returned before
      `run_lifecycle` still must not raise out of `finally`
    - Retrieve, delete, and input items share one identity check. Unresolved
      identity renders `shared_state_unavailable` and does not read the store.
      A miss renders `response_not_found`. Delete returns
      `{"id", "object": "response", "deleted": true}`. Input items returns
      `{"object": "list", "data": <stored items>}`
    - Replay and stored input items are the enforced text, not the raw body
      from before masking
    - If the lifecycle's recorded org on `holds` disagrees with the org used
      for a `previous_response_id` read, do not store and do not replay further.
      Render `response_store_unavailable`
    - Keep each method at or under 120 lines by splitting create into
      translate, continue, refuse, non-stream finish, and stream finish
    - The module stays at or under 800 lines
    - _Design: Components §3, Option 4, Request order_
    - _Requirements: 1.1, 1.2, 1.3, 5.5, 6.1, 6.2, 6.4, 6.5, 7.1, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 6.2 Handler unit tests that do not need the stock SDK **[test-file]**
    - Create `gateway_v2/tests/edge/test_responses_route.py`
    - Use the existing chat lifecycle fixtures
      (`tests/edge/_chat_lifecycle_fixtures.py`) and a counting
      `StubProviderClient`
    - An `input_file` body is HTTP 400 `unsupported_content_part` and the
      provider open count is 0
    - An input block, stream false and stream true, is JSON
      `input_blocked` at HTTP 400, not an event stream, and the provider open
      count is 0
    - A non-streaming allow returns `object` `response`, `status` `completed`,
      and `output_text` equal to the stub text. `pipeline_trace` has the six
      chat stage names and no `kill_switch`, `policy`, or `model_input`
    - A `previous_response_id` that is not stored is HTTP 404
      `response_not_found` and the provider open count is 0
    - `store` omitted, then retrieve, is HTTP 404 `response_not_found`
    - _Requirements: 1.2, 3.4, 3.5, 4.1, 4.2, 5.5, 6.1, 7.2, 7.3, 8.2, 8.5_

- [x] 7. Register the route and retire the responses 501 cells
  - [x] 7.1 Wire boot and the route table **[product-code]**
    - Modify `gateway_v2/gateway_v2/edge/app.py` and
      `gateway_v2/gateway_v2/edge/routes.py`
    - `app.py` builds one store. Use `ValkeyResponseStore` when
      `AMF_DEV_REDIS_URL` is set, otherwise `MemoryResponseStore`. Build one
      `ResponsesRoute(chat, store)` and pass it to `build_routes`
    - `build_routes(chat, misc, responses)` replaces
      `("POST", "/v1/responses"): _placeholder("responses")` with
      `responses.handle`
    - Add prefix `("GET", "/v1/responses/")` and
      `("DELETE", "/v1/responses/")`. The GET handler splits
      `scope["path"]` into retrieve (`/v1/responses/{id}`) and input items
      (`/v1/responses/{id}/input_items`). Anything else on that prefix is
      `response_not_found`
    - The exact table still has one POST entry for `/v1/responses`.
      `("POST", "/v1/mcp")`, `("POST", "/v1/rag/query")`, and
      `("POST", "/v1/vector/query")` stay on `_placeholder`
    - Follow the import-cycle rule in the overview. If `routes.py` or
      `app.py` would pass 800 lines, move the prefix closures and the store
      constructor into `openai_responses.py` and leave a one-line call behind
    - _Design: Components §5, Import cycle_
    - _Requirements: 1.1, 1.4, 1.5, 9.4_

  - [x] 7.2 Update the placeholder assertions **[test-file]**
    - Remove `POST /v1/responses` from the 501 sets and rewrite the cell that
      asserts the responses placeholder, in:
      - `tests/edge/test_lgw15_app.py`
      - `tests/edge/test_lgw12_app.py`
      - `tests/edge/test_gw16_app.py`
      - `tests/edge/test_gw16_routes.py`
      - `tests/edge/test_gw16_matcher_edges.py`
    - The replacement assertion is: the exact table contains one
      `("POST", "/v1/responses")`, and MCP, RAG, and vector still render the
      declared 501 `not_implemented` envelope
    - Do not weaken those three 501 assertions
    - _Requirements: 1.4, 1.5_

- [x] 8. In-process SDK proof
  - [x] 8.1 Add the v2 Responses conformance module **[test-file]**
    - Create `gateway_v2/tests/openai_conformance/test_gw15_responses_v2.py`
    - Drive `gateway_v2.edge.app.build_app` the way
      `test_gw15_chat_surface_v2.py` does: stock `openai` SDK over
      `httpx.ASGITransport`, `StubProviderClient`, and
      `tests.edge._chat_lifecycle_fixtures`
    - `pytest.importorskip` `openai` and `httpx`
    - Cells:
      - non-streaming create parses as a Response, `output_text` equals the
        stub text, `usage` is present when the stub sends it, and
        `zeroshield.action` is on `model_extra`
      - streaming create yields the seven types in order and the last event is
        `response.completed` with the assembled text
      - an input block, stream false and stream true, raises
        `openai.BadRequestError` at HTTP 400 with code `input_blocked`, and
        the provider was not opened. Do not assert `content_filter`
      - a streamed output withhold iterates without an SDK decode error, the
        last event is `response.failed`, `status` is `failed`, `output_text`
        is empty, and `response.completed` is absent
      - `store` true, then retrieve, input items (`len(data) >= 1`),
        `previous_response_id`, delete, then retrieve raises
        `openai.NotFoundError`
      - `store` omitted, then retrieve, raises `NotFoundError` with code
        `response_not_found`
      - a second org's client retrieving the first org's id raises
        `NotFoundError`
    - Leave `test_openai_sdk_compat.py` on the v1 app
    - _Design: Local proof, Deliberate differences_
    - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.5, 6.1, 6.3, 7.1, 8.1, 8.2, 8.3, 8.4, 8.5, 9.1_

- [x] 9. Gate, and the checks that stay deferred
  - [x] 9.1 Run the local gate **[product-code]**
    - Result (2026-10-27): ruff clean; `mypy --strict` 231 files; import-linter 3/3 kept; AST
      gates capacity-literals, module-mutable, frozen-dataclasses, http-outside-edge,
      tenant-scale, sizes, dispatch-provenance, secret-headers exit 0. Full suite 2512 passed,
      95 skipped, 1 xfailed, 1 failed. The failure is
      `tests/lint/test_console_inventory.py::test_checked_in_inventory_matches_the_tree`: the
      GW14b `cp-consumers/endpoints.json` and `frontend-verifier/fields.json` ledgers already
      fail on a clean HEAD (`2d002e5f`). The working tree adds one row to that drift, the
      `action` read via `pipeline_trace` in `frontend/src/utils/liveGateway.js`, from the
      uncommitted streamed-trace fix, not from this card. Not regenerated with `--write`.
    - From `gateway_v2/`:
      - `ruff check` on the new and modified product modules and their tests
      - `mypy --strict` on the new and modified product modules
      - `lint-imports` still 3/3, with no `egress` to `detect` edge and no
        control-plane import of `gateway_v2`
      - the AST gates the repo already runs (capacity literals, module
        mutable, frozen dataclasses, HTTP outside edge, size) exit 0
      - `pytest` on `tests/edge/test_responses_translate.py`,
        `test_responses_events.py`, `test_responses_store.py`,
        `test_responses_route.py`, `test_responses_errors.py` if it exists,
        the updated app/route tests from task 7.2, and
        `tests/openai_conformance/test_gw15_responses_v2.py`
      - the existing chat, completions, and embeddings tests that already
        pass still pass. Do not treat a pre-existing environment failure as a
        regression from this card. Record it if one shows up
    - _Requirements: 9.2, 9.4_

  - [x] 9.2 Confirm the deferred scope was not stubbed **[test-file]**
    - No new test is marked passed for real TCP, the Node SDK, or the
      10,000-request C2 replay
    - No change under `gateway/ai_mesh_gateway/`
    - The runbook GW15 row is still not marked done
    - `{rv2}:dev:firewall-plan` was not written
    - _Requirements: 9.3, 9.5_

## Task dependency graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "3.1"] },
    { "id": 3, "tasks": ["3.2", "4.1"] },
    { "id": 4, "tasks": ["4.2", "5.1"] },
    { "id": 5, "tasks": ["5.2"] },
    { "id": 6, "tasks": ["6.1"] },
    { "id": 7, "tasks": ["6.2", "7.1"] },
    { "id": 8, "tasks": ["7.2", "8.1"] },
    { "id": 9, "tasks": ["9.1", "9.2"] }
  ]
}
```

Wave 0 is the TTL, because the store calls it. Wave 1 is the pure translator,
because the handler and the envelope tests name its codes. The stream
translator (3.1) does not need the envelope and can land beside the translation
tests. The handler (6.1) waits until the translator, the events, the envelope,
and the store exist. Registration (7.1) and the route unit tests (6.2) can
land together once the handler exists. The SDK module (8.1) waits until the
route is registered. The gate is last.
