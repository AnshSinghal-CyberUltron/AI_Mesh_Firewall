# Requirements Document

## Introduction

This feature closes card **GW15** of the AI Mesh Firewall v2 backend rewrite. Chat completions, legacy completions, and embeddings already run on `gateway_v2`. The runbook's GW15 implementation line still names `/v1/responses`, and the 2026-10-22 correction records that route as the only remaining GW15 partial. Today `POST /v1/responses` is the registered placeholder in `gateway_v2/gateway_v2/edge/routes.py` and returns HTTP 501. `gateway_v2/gateway_v2/edge/openai_responses.py` is empty.

v1 already implements this surface as a format adapter over the chat pipeline (`proxy_responses` calls the chat handler, then translates the result). The stock SDK conformance tests in `gateway_v2/tests/openai_conformance/test_openai_sdk_compat.py` describe the contract: a non-streaming `responses.create`, the typed streaming event sequence, a content block the SDK raises as `BadRequestError`, and the store, retrieve, delete, input-items, and `previous_response_id` cells. This close-out brings that adapter onto the shipped v2 chat path. It does not build a second scanner, a second resolver, or a second plan.

The parent spec `.kiro/specs/chat-surface-gw15/` stays the record of the shipped chat surface. Its scope line that leaves `/v1/responses` as a placeholder is superseded for this route only. MCP, RAG, and vector stay HTTP 501 until GW17 and GW18.

### Scope boundaries

- **In scope.** `POST /v1/responses` (streaming and non-streaming), `GET /v1/responses/{id}`, `DELETE /v1/responses/{id}`, and `GET /v1/responses/{id}/input_items`. Translation of a Responses body into the chat canonical request, and translation of a chat result back into a Responses object or typed event stream. The org-scoped response store those four operations need.
- **Reuse, do not rebuild.** The shipped chat lifecycle: pre-checks, identity, GW19 admission, GW06 budget, input enforcement, the provider call, output enforcement, and the six-stage console projection. A Responses request that reaches a model is a chat request after translation.
- **Out of scope.** `/v1/chat/completions`, `/v1/completions`, embeddings, models, and moderations (already shipped, or owned by GW16). MCP, RAG, and vector (GW17 and GW18). A real provider socket, the Node SDK, real-TCP conformance, and the 10,000-request C2 replay. Those stay deferred live gates, the same standing rule as the parent GW15 spec. No change to v1 `gateway/ai_mesh_gateway/`.

### Deferred live gates

The operator has no cloud resources for this step. These are named and not scaffolded as local gates that cannot run:

- Real provider socket. Local proof uses the injected provider already used by chat.
- LGW15-1 real-TCP and Node SDK runs.
- LGW15-6, the 10,000 C2 chat replay, including Responses rows in that corpus.

## Glossary

- **Responses_Surface**: The `/v1/responses` handler that replaces the HTTP 501 placeholder, landing in `edge/openai_responses.py` and registered from `edge/routes.py`.
- **Responses_Request**: An OpenAI Responses API JSON body (`model`, `input`, optional `instructions`, `stream`, `store`, `previous_response_id`, `metadata`).
- **Chat_Surface**: The shipped `/v1/chat/completions` path. The Responses_Surface translates into it and back out of it.
- **Canonical_Request**: The internal chat request the shipped parser already accepts, including every scannable text channel.
- **Input_Channel**: Text the Responses_Request can carry that the chat pipeline must see: a string `input`, each text part of an input-item list, `instructions`, and function-call arguments and outputs.
- **Response_Object**: The non-streaming JSON object with `object` `response`, a `resp`-prefixed `id`, `status`, `output`, and `output_text`, which the stock OpenAI SDK parses as a Response.
- **Typed_Event**: One server-sent event in a Responses stream, named by its `type` (`response.created`, `response.output_item.added`, `response.content_part.added`, `response.output_text.delta`, `response.output_text.done`, `response.output_item.done`, `response.completed`, `response.failed`).
- **Response_Store**: The org-scoped record of a stored Response_Object, its replay messages, and its input items. A record is visible only to the org that created it.
- **Console_Object**: The shipped six-stage projection (`zeroshield` and `pipeline_trace`) the chat path already attaches. Stages are `auth`, `rate_limit`, `input_scan`, `model_routing`, `model_output`, and `output_guardrail`.
- **Error_Envelope**: The single `edge/errors.py` rendering of a value-code. The Responses_Surface uses it. It does not invent a second error shape.
- **FAIL_OPEN**: Forwarding raw bytes or admitting work when a guard or a decision cannot be computed. Forbidden. FAIL_OPEN stays 0.

## Requirements

### Requirement 1: Replace the placeholder with one adapter

**User Story:** As an OpenAI SDK caller, I want `client.responses.create` to reach the same firewall as chat, so that a Responses client is not a way around the gateway.

#### Acceptance Criteria

1. WHEN a client calls `POST /v1/responses`, THE Responses_Surface SHALL handle the request and SHALL NOT return HTTP 501.
2. THE Responses_Surface SHALL be a format adapter over the shipped Chat_Surface. It SHALL translate a Responses_Request into a Canonical_Request, run that request through the shipped chat lifecycle, and translate the chat result back.
3. THE Responses_Surface SHALL NOT implement a second detector, a second resolver, or a second plan. Input and output dispositions SHALL be the dispositions the chat lifecycle already returns.
4. WHILE `POST /v1/responses` is registered, THE route table SHALL contain exactly one full match for that method and path.
5. THE Responses_Surface SHALL leave `POST /v1/mcp`, `POST /v1/rag/query`, and `POST /v1/vector/query` as the existing HTTP 501 placeholders.

### Requirement 2: Translate every text channel into the chat request

**User Story:** As a security engineer, I want every text field of a Responses request to be visible to the chat scanner, so that a secret placed in instructions or a tool argument is not skipped.

#### Acceptance Criteria

1. WHEN a Responses_Request carries a string `input`, THE Responses_Surface SHALL place that string in the Canonical_Request as user text.
2. WHEN a Responses_Request carries an input-item list, THE Responses_Surface SHALL place each text part, and each function-call argument and function-call output, into an Input_Channel of the Canonical_Request.
3. WHEN a Responses_Request carries `instructions`, THE Responses_Surface SHALL place that text in the Canonical_Request where the chat scanner reads system text.
4. WHEN `previous_response_id` names a stored response of the same org, THE Responses_Surface SHALL prepend that response's replay messages to the Canonical_Request before enforcement runs.
5. THE Responses_Surface SHALL perform the translation in the Responses adapter. Security decisions SHALL remain in the shipped chat lifecycle.

### Requirement 3: Reject a request the adapter cannot translate

**User Story:** As an SDK caller, I want a bad Responses body to come back as a declared client error, so that a missing field or an unsupported part does not become a 500 or a silently dropped channel.

#### Acceptance Criteria

1. IF the body is not a JSON object, THEN THE Responses_Surface SHALL return HTTP 400 through the Error_Envelope.
2. IF `model` is missing or is not a string, THEN THE Responses_Surface SHALL return HTTP 400 through the Error_Envelope with `param` `model`.
3. IF `input` is absent and `instructions` is absent, THEN THE Responses_Surface SHALL return HTTP 400 through the Error_Envelope with `param` `input`.
4. IF an input content part has a type the chat pipeline cannot translate, including `input_file` and `input_audio`, THEN THE Responses_Surface SHALL return HTTP 400 through the Error_Envelope with `param` `input` and SHALL NOT drop that part and continue.
5. WHEN a request is rejected under this requirement, THE Responses_Surface SHALL make zero provider calls.

### Requirement 4: Non-streaming create returns a Response object

**User Story:** As an OpenAI SDK caller, I want a non-streaming `responses.create` to parse as a Response, so that an unmodified SDK works with only the base URL and the key changed.

#### Acceptance Criteria

1. WHERE `stream` is absent or false and the chat lifecycle allows the request, THE Responses_Surface SHALL return HTTP 200 and a Response_Object.
2. THE Response_Object SHALL set `object` to `response`, `status` to `completed`, and `output_text` to the enforced assistant text.
3. THE Response_Object SHALL include a usage block when the chat result includes usage.
4. THE Response_Object `id` SHALL be prefixed `resp`.
5. WHEN the chat lifecycle redacts output, THE Response_Object SHALL carry the redacted text and SHALL NOT carry the raw matched bytes.

### Requirement 5: Streaming create emits the typed event sequence

**User Story:** As an OpenAI SDK caller, I want a streaming `responses.create` to yield the typed Responses events in order, so that the SDK assembles the text without a parse error.

#### Acceptance Criteria

1. WHERE `stream` is true and the chat lifecycle opens a stream, THE Responses_Surface SHALL respond with `text/event-stream` and SHALL emit Typed_Events rather than raw chat completion chunks.
2. THE Typed_Event sequence SHALL include, in this order, `response.created`, `response.output_item.added`, `response.content_part.added`, `response.output_text.delta`, `response.output_text.done`, `response.output_item.done`, and `response.completed`.
3. WHEN more than one `response.output_text.delta` is emitted, THE last delta SHALL precede `response.output_text.done`.
4. THE last Typed_Event of a clean stream SHALL be `response.completed`, and that event's response `output_text` SHALL equal the assembled enforced text.
5. IF the chat lifecycle refuses the request before any stream byte, THEN THE Responses_Surface SHALL return the error as JSON and SHALL NOT open an event stream.

### Requirement 6: Enforcement matches chat

**User Story:** As a security engineer, I want a Responses request to block, redact, and flag under the same plan as chat, so that the surface cannot weaken a tenant rule.

#### Acceptance Criteria

1. IF the chat lifecycle returns an input block, THEN THE Responses_Surface SHALL return that block's HTTP status and Error_Envelope, and SHALL make zero provider calls.
2. IF the chat lifecycle returns an output block on a non-streaming request, THEN THE Responses_Surface SHALL withhold the assistant text and return that block's Error_Envelope.
3. IF the chat lifecycle withholds a stream after it has started, THEN THE Responses_Surface SHALL end the Typed_Event sequence with `response.failed`, SHALL set that response `status` to `failed`, SHALL NOT emit `response.completed`, and SHALL leave `output_text` on the failed response empty.
4. WHERE the same plan and the same text are submitted to chat and to Responses, THE Responses_Surface SHALL produce the same disposition the chat lifecycle produced.
5. IF enforcement cannot be computed, THEN THE Responses_Surface SHALL fail closed and SHALL keep FAIL_OPEN at zero.

### Requirement 7: The console object is the chat projection

**User Story:** As a console operator, I want a Responses call to carry the same six-stage trace as chat, so that Scan Detail does not invent stages for this surface.

#### Acceptance Criteria

1. WHEN a Responses request completes or is refused after the chat lifecycle has built a Console_Object, THE Responses_Surface SHALL attach that same `zeroshield` object and `pipeline_trace` to the Response_Object or the error body.
2. THE `pipeline_trace` SHALL contain only the six stages the chat projection already emits.
3. THE Responses_Surface SHALL NOT add `kill_switch`, `policy`, or `model_input` to the trace.
4. WHEN a stage did not run, THE stage SHALL keep `ran` false and `action` `skip`, as the chat projection already records it.
5. THE Responses_Surface SHALL NOT write `Finding.evidence` into the Response_Object, the error body, or the Response_Store.

### Requirement 8: Store, retrieve, delete, and continue a response inside one org

**User Story:** As an SDK caller, I want `store`, `retrieve`, `delete`, `input_items`, and `previous_response_id` to work inside my org and nowhere else, so that a stored response is not a cross-tenant read.

#### Acceptance Criteria

1. WHERE `store` is true and the create completed, THE Responses_Surface SHALL persist the Response_Object, its replay messages, and its input items under the caller's org.
2. WHERE `store` is omitted or false, THE Responses_Surface SHALL NOT persist the response, and a later retrieve or input-items read of that id SHALL return HTTP 404 with code `response_not_found`.
3. WHEN the caller retrieves or deletes a stored id owned by the same org, THE Responses_Surface SHALL return the stored object or the deleted-object acknowledgement.
4. IF the id is unknown, belongs to another org, or the caller is not authenticated, THEN THE Responses_Surface SHALL NOT return the stored object. An unauthenticated caller SHALL receive the same authentication error the chat surface already returns. A missing or foreign id for an authenticated caller SHALL return HTTP 404 with code `response_not_found`.
5. WHEN `previous_response_id` names a stored response of the same org, THE Responses_Surface SHALL continue that conversation. WHEN it does not, THE Responses_Surface SHALL return HTTP 404 with code `response_not_found` and SHALL make zero provider calls.
6. THE Response_Store SHALL persist the enforced text the client was allowed to receive. It SHALL NOT persist a raw copy of bytes the chat lifecycle masked.
7. THE Response_Store SHALL key records by org so two orgs cannot read, delete, or chain each other's ids.

### Requirement 9: Local proof, and the gates that stay deferred

**User Story:** As a maintainer, I want an in-process SDK proof that this surface matches the Responses cells, so that the card can be reviewed before any cloud run.

#### Acceptance Criteria

1. THE in-process conformance tests SHALL cover non-streaming create, the typed streaming sequence, an input block with zero provider calls, a streamed output withhold ending in `response.failed`, and the store, retrieve, delete, input-items, and `previous_response_id` round trip.
2. THE shipped chat, completions, and embeddings tests SHALL still pass after this surface is added.
3. THE real-TCP run, the Node SDK run, and the 10,000-request C2 replay SHALL remain deferred live gates and SHALL NOT be stubbed as local passes.
4. THE Responses_Surface SHALL keep the import-linter layer contract, the capacity-literal and module-mutable AST gates, `mypy --strict`, and ruff. A new module SHALL stay at or under 800 lines, and a new function SHALL stay at or under 120 lines.
5. THE card SHALL NOT be marked done in the runbook until a human reviews the local proof. This requirements document does not mark it done.

## Requirement trace

| Runbook item | Where it is required |
|---|---|
| GW15 implementation work: `/v1/responses` as a thin adapter over the shared pipeline | Requirements 1, 2, 6 |
| LGW15-2 base-URL swap, no client change | Requirements 4, 5 |
| LGW15-5 class of SDK reconstruction, applied to the Responses event sequence | Requirement 5 |
| Channel rule: a blocked input makes zero provider calls | Requirements 3, 6 |
| Channel rule: redacted output bytes do not reach the client | Requirements 4, 6, 8 |
| GW14b six-stage console object, no filled-in stages | Requirement 7 |
| LGW15-1 real TCP and Node, LGW15-6 corpus replay | Requirement 9, deferred |
| GW17 and GW18 placeholders stay 501 | Requirement 1 |
