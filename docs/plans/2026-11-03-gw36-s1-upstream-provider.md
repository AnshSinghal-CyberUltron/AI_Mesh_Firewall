# GW36 slice S1 — production upstream provider (plan)

Status date: 3 November 2026, branch `suraj-revamp`. Parent plan:
`docs/plans/2026-11-02-gw36-production-boot.md` (finding N6, slice S1). Live-test upstream per the
runbook (§3 L1, T02): the synthetic OpenAI-compatible recorder `staging/t02/recorder.py`.

## 1. What is true today (verified)

| # | Finding | Evidence |
|---|---|---|
| F1 | The provider only ever sees `{model, text_deltas}`. `stage_upstream` → `verified_upstream` → `Transform.to_provider` packs the masked `(channel, text)` pairs; message roles, `tool_call_id`, content-part structure, `max_tokens`, `n` and every sampling field are gone before dispatch. | `edge/openai_chat.py:703-719`, `dispatch/transform.py:82-97` |
| F2 | The dev adapter rebuilds `messages` as one `user` message per delta (V-23) and sends only `{model, messages, stream}`. | `edge/dev_upstream.py:44,52-60,89-94` |
| F3 | The shipped `HttpProviderClient` posts the internal pack (`text_deltas`), not an OpenAI body; it never reads the HTTP status; its abort latch is one-way per instance, so a shared instance stops every later stream after one abort. | `dispatch/http_provider.py:207-231,339-351,281-293` |
| F4 | The dev adapter's `abort()` aborts every in-flight stream of the worker; it opens a new `httpx.AsyncClient` per request (no pooling). | `edge/dev_upstream.py:118-148` |
| F5 | **`/v1/completions` never scans `prompt`.** The fold reads `messages[]` only; live, a `prompt` carrying an SSN and an injection got `input_scan` allow and the echo provider received 0 chars; two different prompts got the same request id (the id hashes the folded channels). Today the model sees nothing; once S1 forwards the request structure it would be a raw leak. | `edge/wire/openai.py:442-473`, live probe 3 Nov |
| F6 | The SSE decoder keeps string-valued `delta` keys only (drops `tool_calls` lists and `finish_reason`). | `edge/wire/sse.py:706-735` |
| F7 | `/v1/responses` translates to a chat payload and passes `tools`, `tool_choice`, `parallel_tool_calls`, `response_format`, `user`, `metadata`, `n`, `stop`, `seed`, `temperature`, `top_p` through. | `edge/wire/responses.py:54-66` |
| F8 | v1 behaviour for the output budget: `max_tokens` unset → inject the org `max_response_tokens` (default 4096); set → clamp to it; only `max_completion_tokens` → clamp that and leave `max_tokens` absent (reasoning models reject both). | `gateway/ai_mesh_gateway/main.py:8700-8719` |
| F9 | `edge/wire/openai.py` is 799 lines and `edge/openai_chat.py` 795 (module limit 800). | `wc -l` |
| F10 | The T02 recorder answers only `POST /v1/chat/completions`; it records the joined prompt text, `contains_ssn`, `contains_akia`, model and stream, not roles or budget fields. | `staging/t02/recorder.py:48-86,222` |

## 2. Design

### 2.1 One traversal for scanning and for the upstream body (edge/wire)

Move the fold out of `edge/wire/openai.py` into `edge/wire/openai_fold.py` (re-exported, F9) and make
it a single walker that produces BOTH the folded `(channel, text)` pairs and an **upstream
template**: the forwardable request structure where every scanned text occurrence is a
`TextSlot(index, channel)` placeholder. Raw text never enters the template, and the template and the
fold cannot disagree because one loop builds both.

Messages admitted into the template:

- `role` ∈ {`system`, `developer`, `user`, `assistant`}; `name` only if it matches the OpenAI name
  pattern `^[A-Za-z0-9_-]{1,64}$`.
- `content`: a string (one slot) or a list of text parts (`type` absent or `text`, one slot each).
  Any other part type (image, audio, file) → 400 `unsupported_content_part` (param `messages`): it
  is not scanned.
- `tool_calls` / `function_call` / role `tool` / `function` → 400 `unsupported_parameter` (param
  `messages`) until GW34 (decision §9.3: tools refused until tool-call deltas stream back).

Legacy completions `prompt` (F5): a new input channel `completion_prompt` (`sdk_path` `prompt`). A
string, or a list of exactly one string, is one slot; the upstream template is a chat request with
one `user` message. A list of several prompts, or token-id arrays, → 400 `unsupported_parameter`
(param `prompt`): batching is clamped to one completion and token ids cannot be scanned.

Top-level fields:

| Class | Fields |
|---|---|
| Forwarded as given | `temperature`, `top_p`, `presence_penalty`, `frequency_penalty`, `seed`, `stop`, `logit_bias`, `reasoning_effort` |
| Forwarded after v1's budget rule (F8) | `max_tokens`, `max_completion_tokens` (from the clamped request) |
| Accepted, not forwarded | `stream`, `stream_options`, `user`, `metadata`, `store`, `n` (clamped to 1), `service_tier` |
| Refused 400 `unsupported_parameter` with `param` | everything else, including `tools`, `tool_choice`, `functions`, `function_call`, `parallel_tool_calls`, `response_format`, `logprobs`, `top_logprobs`, `audio`, `modalities`, `prediction` |

`unsupported_parameter` → (400, `invalid_request_error`) in the envelope. The refusal happens at
parse, before identity, like every other malformed-body refusal.

### 2.2 Render after verification (edge)

`_proceed` already holds the verified, masked deltas (`stage_upstream`). A new
`edge/upstream_body.py::with_openai_body(upstream_body, template)` fills each `TextSlot(i, channel)`
with verified delta `i` — the channel must match and the slot count must equal the delta count —
and adds two keys to the upstream body: `openai_body` (the rendered OpenAI request, `stream: true`)
and `openai_endpoint` (`chat/completions`). A mismatch refuses with the lifecycle-incomplete code
and zero provider calls. The masked deltas stay in the body under `text_deltas` (the Responses
store reads them). `dispatch` (`GatewayRequest`, `verify`, `authorized`) is untouched.

### 2.3 Provider (dispatch)

New `dispatch/openai_upstream.py::OpenAIUpstreamProvider(base_url, api_key, timeout_s,
max_error_bytes, decoder_factory, client_factory)`:

- One pooled `httpx.AsyncClient` per provider instance (created on first use, closed by `aclose()`).
- `open(request)` posts `body["openai_body"]` to `base_url + "/" + body["openai_endpoint"]`. A body
  without those keys yields a terminal error event and posts nothing (the internal pack is never
  sent).
- Non-2xx: read at most `max_error_bytes`, map through the shipped `map_provider_error` (400 keeps
  the provider code, 429, unmapped status → `upstream_error`), yield a terminal error event. Never an
  empty success.
- SSE decode through the injected decoder; the `role` channel is dropped (metadata, not content).
- `abort()` cuts only the stream opened in the caller's context (a `ContextVar` handle), not every
  stream on the worker (F3, F4).

`edge/dev_upstream.py` is deleted; the dev profile builds `OpenAIUpstreamProvider` from
`AMF_DEV_PROVIDER_URL` (a base URL ending in `/v1`; a full `.../chat/completions` URL is accepted and
trimmed). The production boot (S5) builds the same class from `AMF_PROVIDER_*`.

### 2.4 Recorder

`staging/t02/recorder.py` also records `roles`, `body_keys`, `max_tokens`, `max_completion_tokens`
and `n` per call, so the live test can check structure, not only text.

## 3. Out of S1 (named, not hidden)

- `/v1/embeddings` to a real upstream (needs `/embeddings` + a JSON, non-SSE reply) → S1b; with the
  OpenAI provider it yields a declared error, never a chat call.
- Tool calls, `response_format`, logprobs → GW34 (refused with a declared 400 meanwhile).
- `Retry-After` / rate-limit headers from a provider 429 do not reach the client: `UpstreamEvent`
  carries no headers (records the gap; C27 follow-up).
- `finish_reason` and usage from upstream are not carried (F6).
- Charging: the cost estimate still uses the client `max_tokens` (0 when unset) while the upstream
  receives the injected ceiling; the under-charge is the pre-existing v2-enforcement-leaks finding 4.

## 4. Tests

Offline (house idiom, seeded `random.Random`, ≥ 10,000 iterations for properties):

- P1 fold/template alignment: for random message lists, the template's slots in order equal the
  folded pairs' channels, and rendering with the original texts reproduces every forwarded text.
- P2 no raw text in the template: no string from the request content appears in the template.
- Refusals: each refused field / role / part type → 400 with the right `param`, zero provider calls.
- `prompt` is folded and scanned (an SSN in `prompt` is redacted; an injection is blocked).
- Budget rule F8 (unset → ceiling; set → clamp; only `max_completion_tokens` → clamp, no `max_tokens`).
- Provider: body posted, status mapping (400/429/500), body-missing guard, per-context abort isolation
  (two concurrent streams, abort one), pooled client reused and closed.

Live, against the T02 recorder through a second dev gateway container on :8302 (the user's :8301
stays on echo):

- system + user + assistant roles arrive in order; SSN masked in the recorded prompt
  (`contains_ssn` false); `max_tokens` 4096 injected when unset, 999999 → 400 boundary / clamp;
  `tools` → 400 with 0 recorder calls; image part → 400 with 0 calls; `prompt` SSN → masked user
  message; injection → 400 with 0 calls; stream returns the recorder's content; recorder faults
  `http_error 429` / `malformed` / `disconnect` → declared errors, never an empty 200.

## 4a. Review outcome (three contradictory reviewers; each point re-checked in code)

Accepted and folded into S1:

| # | Finding | Change |
|---|---|---|
| R1 | `stop` strings would be forwarded unscanned; `name` matches the name pattern yet can carry an AKIA key or an SSN; `model` and the scalar sampling fields were unchecked text | `stop` becomes a scanned input channel (`stop_sequence`); `name` is **not forwarded** (registered difference); `model` must match `^[A-Za-z0-9._:/-]{1,128}$`; scalars are type- and range-checked (`reasoning_effort` a closed set, `logit_bias` int-string keys and values in [-100, 100]); unknown keys inside messages and parts are never copied |
| R2 | The chat and misc fold totality checks key on `sdk_path` prefixes; a `prompt` channel would raise `ChannelFoldError` at import | The chat-input scope is the prefix set (`messages[]`, `prompt`, `stop`); the misc scope excludes it; the tests' copies follow |
| R3 | The scan joins a message's text parts with no separator; a provider joining with a space or newline could reassemble a split value differently | A multi-part text message is rendered as ONE string joined with `""`, exactly the scanned bytes |
| R4 | A body carrying both `messages` and `prompt` cannot be told apart (both routes map to `Surface.CHAT`) | Refused: 400 `unsupported_parameter` (param `prompt`) |
| R5 | **A provider error event is forwarded as content**: `asgi_sse.py:258-265` yields `error_frame` as the final chunk, `openai_nonstream.py:194-195` appends it to `output_content` — a provider 429 reaches the client as HTTP 200 with the error text as the answer | New typed terminal `UpstreamEvent.gateway_error` (a declared code). Non-streaming: refuse with the envelope status. Streaming: flip the kill latch with the code, so the declared `Error_Frame` is sent. `error_frame` (the provider's own mid-stream error payload) keeps its GW12 R11 scan-and-forward behaviour |
| R6 | `map_provider_error` codes are not in the envelope (would render generic 500); a provider 401/403 passed through would tell the SDK its own key is bad | Envelope + `ERROR_SHAPES` entries for every mapped code; provider 401/403 → 502 `upstream_auth_error` |
| R7 | A 200 non-SSE body, residual bytes, or a stream with no `[DONE]` ends as an empty success; a transport error before the first byte is swallowed into an empty 200 | 2xx must be `text/event-stream`; `SSEDecoder.finished()` (saw `[DONE]`, no residual) is required at end of body; transport errors → `gateway_error` `upstream_unavailable` (502) |
| R8 | The `ContextVar` abort has no caller and would not reach a task created earlier | `abort()` is a documented no-op; a request's stream is cut by closing its iterator (cancel / `aclose`), and the generator's `finally` closes the response. Test: a cancel mid-read closes the response |
| R9 | Pool size, error-body cap and timeouts need contract authority; `httpx` is only in the `conformance` extra | `pool_size(PROVIDER)` sizes the pool; new `ResourceContract.max_provider_error_bytes()`; `httpx` added to the `serve` extra; the pooled client is created lazily in the running loop and closed on lifespan shutdown |
| R10 | Tests that will break: `test_lgw15_fold.py`, `test_lgw15_input_block.py`, `test_lgw15_openai_codec.py`, `test_v2_leaks_f_properties.py` (tool-bearing bodies now refused at parse), `test_dev_profile_components.py` (imports `dev_upstream`) | Updated to the new contract; fold-level coverage of tool channels kept |
| R11 | Every "masked at the provider" test reads `text_deltas`, not the bytes the provider is now sent | New tests and the key Group A test assert on `openai_body` |

Recorded, not fixed in S1:

- Streaming refusals before the first byte still render as an SSE `Error_Frame` after the 200 (the SSE start precedes `provider.open`); a real HTTP 429/400 on a stream needs the SSE start deferred → S5 / GW34.
- The edge `inter_chunk_timeout_s` (1.0 s at p99 20 ms) also bounds the FIRST token; a real model slower than 1 s to first token is cut → contract first-token timeout in S5.
- The request id is an unsalted hash of the raw input text (`openai_chat.py:737-744`); a low-entropy secret could be brute-forced from it → S3 (identity and request id).
- Embeddings on the OpenAI provider get a declared error, but `mark_dispatched()` runs first, so the spend is not refunded → S1b.
- `AuthorizedProviderEntry` (unused in production) rebuilds the body without `openai_body`.
- A comment-only SSE keep-alive (`: ping`) trips the decoder's malformed latch.
- `/v1/responses` requests with tools, function-call items or `text.format` now get a 400 naming the chat field (`messages`, `response_format`).

## 4b. Result (3 November)

Implemented as §2 + §4a with three deviations: (1) a request with no template (built by hand, not
parsed) passes through unchanged and the OpenAI provider refuses it (`upstream_request_unbuildable`)
— refusing in the lifecycle broke every stub-parser test and added nothing, since the provider
already fails closed; (2) the provider does not read a non-2xx body at all (nothing is echoed), so
no error-body cap is needed; (3) the pooled client is closed by the dev profile's lifespan wrapper;
the production boot's lifespan (S5) does the same.

Found while verifying and fixed: a stream cut by an upstream failure (or a withheld error frame)
reported `final_action: allow`; `ChatRoute._finish_stream` now records `block` for any cut except a
kill switch. The T02 recorder's `disconnect` fault behaved as a stall.

Also recorded: a stalled upstream holds a non-streaming request until the 300 s read timeout (S5 /
GW28 plan-declared timeouts); an upstream failure is shown as `output_guardrail: block` until the
console schema has an `error` action (GW35).

Evidence: `docs/plans/evidence/2026-11-03-gw36-s1/SUMMARY.md` — 2845 offline passed (the one
container failure needs `git` and passes on the host), 15/15 live checks against the T02 recorder.

## 5. Risks

- Strict refusal of unknown fields may break a client that sends a field we have not listed. That is
  surfaced as a declared 400 naming the field, not a silent change; GW34 runs the client matrix.
- Existing tests that send `tools` through `/v1/responses` will now see 400 and must be updated.
- Moving the fold is a large diff in a security-critical module; the alignment property is the guard.
