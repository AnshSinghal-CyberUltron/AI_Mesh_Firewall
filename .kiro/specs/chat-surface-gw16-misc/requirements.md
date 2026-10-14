# Requirements Document

## Introduction

This feature implements card **B2 / GW16** of the AI Mesh Firewall v2 backend rewrite (`gateway_v2/`): the three misc OpenAI-compatible surfaces plus the misc request/response typing. It fills the currently-empty `gateway_v2/gateway_v2/edge/openai_misc.py` (`__all__ = ()`, "empty until GW16") with the real handlers for `POST /v1/embeddings`, `GET /v1/models` (list) + `GET /v1/models/{id}` (retrieve), and `POST /v1/moderations`, and replaces the three corresponding `_placeholder(...)` entries in `gateway_v2/gateway_v2/edge/routes.py` `build_routes` — which today return a declared `not_implemented` value-code — with those real handlers.

GW16 follows the just-completed GW15 card (`.kiro/specs/chat-surface-gw15/`), which built the real `/v1/chat/completions` + `/v1/completions` handler. GW16 WIRES the same already-shipped pipeline seams GW15 wired — the injected input detector + `OutputResolver` input-scan path, GW05b/c identity + freshness, the GW19 `AdmissionController`, the GW06 `QuotaComponent` budget lease, the injected `ProviderClient` (`StubProviderClient` locally), the single `edge/errors` `Error_Envelope`, and the GW14d `PerRequestExports` — into three new surfaces. It REUSES the shared OpenAI typing GW15 landed in `edge/wire/openai.py` (`CanonicalRequest`, `MalformedRequest` + the `MALFORMED_*` declared value-codes, `OpenAIRequestParser`, `OpenAIResponseRenderer`, `fold_input_channels`) and ADDS the misc request/response typing (embeddings / models / moderations parse + render) alongside it, keeping `edge/wire` as the ONLY layer that renders OpenAI wire shapes.

The three surfaces differ in which lifecycle phases they run:

- **Embeddings** is a billable upstream call: it runs the FULL chat lifecycle — identity → admission → budget → input scan (BLOCK short-circuits with zero provider calls) → provider — then renders the OpenAI embeddings response shape with batch index order preserved. The embedding VECTORS come from the injected provider (`StubProviderClient` locally); GW16 invents no real embedding model.
- **Moderations** is a scan-only surface: it runs identity → input scan and surfaces the firewall findings as OpenAI moderation categories. It NEVER contacts the upstream provider.
- **Models** is a catalog lookup: it resolves identity, then lists / retrieves from an injected org/plan-derived catalog source. It runs NO input scan (there is no user text) and NEVER contacts the provider.

### Why this card exists — the channel enumeration (C21)

The v2.1 prototype passed SDK acceptance but failed the channel tests, so channel totality is a first-class gate (`unscanned_channels()` must stay empty). GW16 introduces NEW scannable request text — the embeddings `input` and the moderations `input` — and MUST enumerate every such field into the `domain/channels.py` channel model so a canary in any new input field is scanned, exactly as GW15's input fold is total over the chat input channels. A new scannable field with no scanning path is a build-time gate failure.

### Scope boundaries (explicit cross-references)

- **Shipped mechanisms — DONE, OUT OF SCOPE to rebuild; GW16 WIRES them.** The GW15 input-scan path (injected `input_detector` + `OutputResolver`), GW05b/c identity + freshness, GW19 `AdmissionController`, GW06 `QuotaComponent`, the injected `ProviderClient` / `StubProviderClient`, the single `edge/errors` `Error_Envelope`, the `edge/wire/openai.py` typing (`CanonicalRequest`/`MalformedRequest`/`OpenAIRequestParser`/`OpenAIResponseRenderer`/`fold_input_channels`), and GW14d `PerRequestExports`. GW16 wires into these without changing them.
- **Three misc surfaces ONLY.** `POST /v1/embeddings`, `GET /v1/models`, `GET /v1/models/{id}`, `POST /v1/moderations`. `/v1/responses`, `/v1/mcp`, `/v1/rag/query`, `/v1/vector/query` STAY the declared `not_implemented` placeholders already registered in `build_routes`. The chat/completions routes GW15 built are untouched.
- **Existing app — wire ONTO it.** GW16 wires onto the existing raw-ASGI app (`edge/app.py` + `edge/routes.py` `build_routes`). There is NO new server-process / entrypoint card in scope (that is B3).

### Deferred live gates (category-2 seam — standing operator instruction)

The operator has NO cloud resources. These are shipped as code cloud-complete + the seam, and are NOT scaffolded as un-runnable gates:
- **Real embedding provider socket / real model backend** — `StubProviderClient` stays the in-process substitute for the embedding vectors; the real socket is a DEFERRED live gate (B4 / GW17-18). The real org/plan-derived model catalog backend is a DEFERRED seam behind the injected catalog source.
- **Real serving entrypoint over a socket (B3)** — manual `curl` / live-SDK testing over a real socket is deferred; GW16's own manual verification is in-process (the stock SDK over `httpx.ASGITransport` against `build_app`).

## Glossary

- **Gateway**: The `gateway_v2` AI Mesh Firewall serving process that terminates client OpenAI-compatible requests and enforces input/output guards.
- **Misc_Surface**: The collective name for the three GW16 handlers (`Embeddings_Surface`, `Models_Surface`, `Moderations_Surface`) landing in `edge/openai_misc.py`, wired through `edge/routes.py` `build_routes`.
- **Embeddings_Surface**: The `POST /v1/embeddings` handler.
- **Models_Surface**: The `GET /v1/models` (list) and `GET /v1/models/{id}` (retrieve) handlers.
- **Moderations_Surface**: The `POST /v1/moderations` handler.
- **SDK_Request**: An OpenAI-compatible JSON request body sent by an official OpenAI SDK to a Misc_Surface.
- **Embeddings_Request**: An SDK_Request to the Embeddings_Surface with body `{model, input}` where `input` is a string or a list of strings.
- **Moderations_Request**: An SDK_Request to the Moderations_Surface with body `{input}` (optionally `model`).
- **Input_Channel**: Any field of an SDK_Request that can carry client text. For embeddings and moderations this is the `input` field (each element when `input` is a list).
- **Scannable_Text**: Client-supplied text extracted from an Input_Channel that MUST be run through the input scan.
- **Allowed_Model_Catalog**: The org/plan-derived set of models the Models_Surface lists and retrieves, obtained from an injected catalog source resolved against the request's Tenant_Identity.
- **Catalog_Source**: The injected dependency the Models_Surface reads the Allowed_Model_Catalog from, mirroring GW15's injected-dependency discipline; its real org/plan backend is a deferred seam.
- **Tenant_Identity**: The authenticated principal and org/tenant resolved for a request, from the shipped GW05b/c identity + freshness stamp.
- **AdmissionController**: The shipped GW19 CoDel owner-admission component (`admit/` package) returning a `ShedVerdict` or `Admitted`.
- **QuotaComponent**: The shipped GW06 budget-lease façade (`admit/quota.py`) that spends a locally-held org budget lease without a request-path shared-state read.
- **ProviderClient**: The injectable, abortable upstream client protocol (`dispatch/provider.py`) speaking the OpenAI wire protocol to a configurable `base_url`.
- **StubProviderClient**: The shipped in-process `ProviderClient` substitute (`dispatch/provider.py`) with NO real socket, injected in local tests; the source of embedding vectors locally.
- **Input_Detector**: The injected findings-producing scanner (`Callable[[str], Sequence[Finding]]`), injected through `edge` above `detect`, the same seam shape GW15 uses.
- **OutputResolver**: The shipped resolver (`egress/output_guard.py`) that decides ALLOW / REDACT / BLOCK per finding; GW16 uses it to resolve input findings (C22 most-restrictive).
- **Enforced_Finding**: A detection finding whose policy disposition is enforced (BLOCK, REDACT, or FLAG).
- **BLOCK**: The most-restrictive per-finding disposition (C22): a request carrying a BLOCK-enforced finding is refused; a REDACT / FLAG rule never cancels a BLOCK.
- **Moderation_Result**: The OpenAI moderation result object `{flagged, categories{...}, category_scores{...}}` the Moderations_Surface produces from firewall findings, with firewall-specific signals (e.g. `prompt_injection`) carried as category extras.
- **Error_Envelope**: The single `edge/errors.py` builder (`render_http`, `render_sse`, `render_not_implemented`, `render`) that maps a value-code to an HTTP body / SSE frame, honoring the codes-vs-render boundary.
- **MalformedRequest**: The shipped `edge/wire/openai.py` exception carrying a declared `MALFORMED_*` value-code, raised for an off-contract body so the handler renders it through the Error_Envelope rather than a raw framework error.
- **Frozen_Contract**: The OpenAI-compatible request/response contract an unmodified SDK depends on; any required client code change beyond swapping `base_url` and the API key breaks it.
- **ResourceContract**: The capacity authority (`runtime/resources.py`) — the ONLY module permitted capacity-position literals.
- **FAIL_OPEN**: Forwarding raw (unscanned / undecided) text, or admitting a request, when a guard, auth, or decision cannot be computed. Forbidden; the Gateway fails closed. Pinned at 0.
- **Per_Request_Exports**: The shipped GW14d per-request observability export surface (`runtime/stream_metrics.py::PerRequestExports`) each Misc_Surface populates.
- **Route_Table**: The exact `(method, path) -> handler` mapping built in `build_routes` and matched in `edge/app.py` by `self.routes.get((method, path))`; GW16 must route `GET /v1/models/{id}` (a path parameter) without breaking the no-module-mutable discipline or the exact-match table shape.
- **Conformance_Harness**: The in-process `gateway_v2/tests/openai_conformance/` suite — the unmodified stock `openai` SDK over `httpx.ASGITransport` against `build_app`, base_url + key swapped only, `StubProviderClient` behind it.

## Requirements

### Requirement 1: Register the three misc surfaces as real handlers

**User Story:** As a platform operator, I want the three misc OpenAI routes served by real handlers instead of not-implemented placeholders, so that an OpenAI SDK can call embeddings, models, and moderations against the Gateway.

#### Acceptance Criteria

1. WHEN `build_routes` assembles the Route_Table, THE Gateway SHALL register `POST /v1/embeddings`, `GET /v1/models`, and `POST /v1/moderations` to the GW16 Misc_Surface handlers instead of the `not_implemented` placeholders.
2. WHEN `build_routes` assembles the Route_Table, THE Gateway SHALL route `GET /v1/models/{id}` to the Models_Surface retrieve handler.
3. THE Gateway SHALL keep `/v1/responses`, `/v1/mcp`, `/v1/rag/query`, and `/v1/vector/query` registered as the declared `not_implemented` placeholders.
4. THE Gateway SHALL build the Route_Table as a function-local value inside `build_routes` and SHALL NOT introduce a module-level mutable to route the `/v1/models/{id}` path parameter.
5. THE Misc_Surface handlers SHALL render every OpenAI wire shape through `edge/wire` and SHALL NOT place security decisions in bare `edge/` routing code.

### Requirement 2: Parse and type the misc request/response shapes

**User Story:** As an OpenAI SDK client developer, I want the Gateway to parse my embeddings and moderations request bodies into typed internal values and render typed responses, so that malformed bodies become declared errors and well-formed bodies round-trip through the OpenAI shapes.

#### Acceptance Criteria

1. WHEN an Embeddings_Request body is received, THE Embeddings_Surface SHALL parse it into a typed value carrying the `model` field and the `input` as either a single Scannable_Text or an ordered list of Scannable_Text items.
2. WHEN a Moderations_Request body is received, THE Moderations_Surface SHALL parse it into a typed value carrying the `input` as Scannable_Text.
3. IF an Embeddings_Request or Moderations_Request body is malformed or off the OpenAI-compatible shape, THEN THE Misc_Surface SHALL raise a declared value-code and render it through the Error_Envelope rather than a raw framework error.
4. IF a Moderations_Request omits the required `input` field, THEN THE Moderations_Surface SHALL render the declared `missing_required_parameter` value-code through the Error_Envelope.
5. THE misc request/response typing SHALL live in `edge/wire` and SHALL reuse the shipped `MalformedRequest` + `MALFORMED_*` value-code discipline rather than defining a parallel error mechanism.
6. THE misc request/response typing SHALL NOT redefine the GW15 shared types (`CanonicalRequest`, `OpenAIRequestParser`, `OpenAIResponseRenderer`, `fold_input_channels`) that already exist in `edge/wire/openai.py`.

### Requirement 3: Enumerate the new scannable input channels (C21)

**User Story:** As a security engineer, I want every new scannable request field GW16 introduces enumerated in the channel model, so that a canary placed in an embeddings or moderations input is always scanned and no field silently escapes scanning.

#### Acceptance Criteria

1. THE Gateway SHALL enumerate the embeddings `input` and the moderations `input` as scannable Input_Channels in the `domain/channels.py` channel model.
2. WHEN an Embeddings_Request or Moderations_Request `input` carries a list, THE Misc_Surface SHALL extract each list element into a separate Scannable_Text item for scanning.
3. THE Gateway SHALL keep `unscanned_channels()` empty, so a scannable Misc_Surface field with no scanning path is a build-time gate failure.

### Requirement 4: Scan embeddings input and block before any provider call

**User Story:** As a security engineer, I want a BLOCK-enforced finding in an embeddings input to stop the request before the provider is contacted, so that blocked content never leaves the Gateway toward an upstream embedding model.

#### Acceptance Criteria

1. WHEN an Embeddings_Request is parsed, THE Embeddings_Surface SHALL scan every Scannable_Text item of the `input` through the injected Input_Detector before opening a ProviderClient stream.
2. IF any embeddings Input_Channel carries an Enforced_Finding with a BLOCK disposition, THEN THE Embeddings_Surface SHALL refuse the request and SHALL make zero provider calls.
3. WHEN an embeddings request is refused for a BLOCK input finding, THE Embeddings_Surface SHALL render the declared block value-code through the Error_Envelope.
4. WHERE a REDACT or FLAG rule co-matches with a BLOCK rule on the same embeddings input, THE Embeddings_Surface SHALL enforce the BLOCK disposition as the final per-finding outcome, per constraint C22.
5. THE Embeddings_Surface SHALL receive the Input_Detector and OutputResolver as injected dependencies through `edge` above `detect`, and `egress` SHALL NOT import `detect`.
6. IF the input scan or its decision cannot be computed, THEN THE Embeddings_Surface SHALL fail closed and refuse the request, keeping FAIL_OPEN at zero.

### Requirement 5: Run the full billable lifecycle for embeddings

**User Story:** As a platform operator, I want embeddings — a billable upstream call — authenticated, admitted, and budget-metered like chat, so that embeddings traffic cannot bypass admission control or budget enforcement.

#### Acceptance Criteria

1. WHEN an Embeddings_Request is received, THE Embeddings_Surface SHALL resolve Tenant_Identity using the shipped GW05b/c identity and freshness stamp before contacting the provider.
2. IF Tenant_Identity cannot be resolved, or the freshness stamp is stale or unavailable, THEN THE Embeddings_Surface SHALL fail closed, render a declared authentication value-code through the Error_Envelope, and make zero provider calls.
3. WHEN an Embeddings_Request has a resolved Tenant_Identity, THE Embeddings_Surface SHALL submit the request to the shipped AdmissionController, and IF the AdmissionController returns a `ShedVerdict` THEN THE Embeddings_Surface SHALL render the verdict's value-code through the Error_Envelope and make zero provider calls.
4. WHEN an Embeddings_Request is admitted, THE Embeddings_Surface SHALL request a budget decision from the shipped QuotaComponent, and IF the QuotaComponent refuses for budget THEN THE Embeddings_Surface SHALL render the declared budget value-code through the Error_Envelope and make zero provider calls.
5. THE Embeddings_Surface SHALL obtain the budget decision from the locally-held lease and SHALL NOT perform a shared-state read on the request path for the budget spend.
6. THE Embeddings_Surface SHALL run the lifecycle phases in the order identity → admission → budget → input scan → provider, so that every refusal before the provider is opened makes zero provider calls.

### Requirement 6: Return the OpenAI embeddings response with preserved order

**User Story:** As an OpenAI SDK client developer, I want a batch embeddings request to return one embedding per input item in request order, so that the SDK maps each embedding back to its input.

#### Acceptance Criteria

1. WHEN an Embeddings_Request passes the input scan, THE Embeddings_Surface SHALL forward the resolved request through the injected ProviderClient to obtain the embedding vectors and SHALL NOT compute embedding vectors itself.
2. WHEN the Embeddings_Surface renders a response, THE Embeddings_Surface SHALL produce the OpenAI embeddings shape `{object:"list", data:[{index, embedding, object:"embedding"}], model, usage}`.
3. WHEN an Embeddings_Request carries a list `input`, THE Embeddings_Surface SHALL emit one `data` entry per input item with each entry's `index` matching the position of its input item, preserving request order.
4. WHERE local tests run, THE Embeddings_Surface SHALL accept the shipped StubProviderClient as the injected ProviderClient with no real socket, and THE real embedding provider socket SHALL remain a deferred live gate.

### Requirement 7: List and retrieve models from the injected catalog

**User Story:** As an OpenAI SDK client developer, I want to list available models and retrieve a model by id, so that the SDK's `models.list()` and `models.retrieve(id)` work against the Gateway.

#### Acceptance Criteria

1. WHEN a `GET /v1/models` request is received, THE Models_Surface SHALL resolve Tenant_Identity and return the Allowed_Model_Catalog from the injected Catalog_Source in the OpenAI shape `{object:"list", data:[{id, object:"model", ...}]}`.
2. WHEN a `GET /v1/models/{id}` request is received for an id present in the Allowed_Model_Catalog, THE Models_Surface SHALL return that model in the OpenAI shape `{id, object:"model", ...}`.
3. IF a `GET /v1/models/{id}` request names an id absent from the Allowed_Model_Catalog, THEN THE Models_Surface SHALL render a declared not-found value-code through the single Error_Envelope so the stock SDK raises `openai.NotFoundError`.
4. THE Models_Surface SHALL obtain the Allowed_Model_Catalog from the injected Catalog_Source resolved against the request's Tenant_Identity and SHALL NOT contact the ProviderClient.
5. THE Models_Surface SHALL run no input scan, because a models request carries no client Scannable_Text.
6. IF Tenant_Identity cannot be resolved for a models request, THEN THE Models_Surface SHALL fail closed and render a declared authentication value-code through the Error_Envelope.

### Requirement 8: Moderate input and surface firewall findings as categories

**User Story:** As an OpenAI SDK client developer, I want a moderations request to classify my input, so that the SDK's `moderations.create(input=...)` returns flagged categories reflecting the firewall's findings.

#### Acceptance Criteria

1. WHEN a Moderations_Request is parsed, THE Moderations_Surface SHALL resolve Tenant_Identity and scan every Scannable_Text item of the `input` through the injected Input_Detector.
2. WHEN the Moderations_Surface renders a response, THE Moderations_Surface SHALL produce the OpenAI moderation shape `{id:"modr-…", model, results:[{flagged, categories{...}, category_scores{...}}]}`.
3. WHEN the Input_Detector produces a finding over a moderations input, THE Moderations_Surface SHALL set the matching category to flagged and SHALL set the overall `flagged` field true when at least one category is flagged.
4. WHERE the Input_Detector produces a firewall-specific signal that has no stock OpenAI category, THE Moderations_Surface SHALL surface that signal as a category extra in the Moderation_Result.
5. THE Moderations_Surface SHALL NOT contact the ProviderClient, because moderations is a scan-only surface.
6. IF the input scan cannot be computed, THEN THE Moderations_Surface SHALL fail closed and render a declared value-code through the Error_Envelope rather than returning an unscanned result, keeping FAIL_OPEN at zero.

### Requirement 9: Error envelope and frozen-contract fidelity

**User Story:** As an OpenAI SDK client developer, I want misc-surface errors rendered in the OpenAI-compatible shape, so that an unmodified SDK with only base_url and key swapped works unchanged.

#### Acceptance Criteria

1. THE Misc_Surface SHALL render every value-code through the single Error_Envelope as an HTTP body, including the models not-found code, the moderations `missing_required_parameter` code, and every malformed-body code.
2. IF a value-code is unmapped, THEN THE Misc_Surface SHALL render the declared generic error through the Error_Envelope rather than leaking raw internal detail.
3. WHEN an unmodified OpenAI SDK targets a Misc_Surface with only `base_url` and API key swapped, THE Misc_Surface SHALL serve the request unchanged, preserving the Frozen_Contract for embeddings (single and batch), models (list, retrieve, and unknown-id), and moderations (benign, flagged, and missing-input).
4. IF serving a misc request would require any client code change beyond swapping `base_url` and the API key, THEN THE Misc_Surface SHALL fail the acceptance as a broken Frozen_Contract.

### Requirement 10: Per-request observability exports

**User Story:** As a platform operator, I want each misc request to populate the per-request export surface, so that admission, budget, and enforcement signals are observable without leaking content.

#### Acceptance Criteria

1. WHEN a misc request completes or is refused, THE Misc_Surface SHALL populate the shipped GW14d Per_Request_Exports.
2. THE Embeddings_Surface SHALL record admission, budget, and input-enforcement outcomes in the Per_Request_Exports.
3. THE Misc_Surface SHALL keep FAIL_OPEN pinned at zero in the Per_Request_Exports.
4. THE Misc_Surface SHALL NOT place raw request or response content in the Per_Request_Exports.

### Requirement 11: Conformance harness coverage with deferred live gates

**User Story:** As a maintainer, I want the in-process conformance subset to exercise the misc surfaces and pass locally, so that OpenAI compatibility is verified without cloud resources while the live gates stay seams.

#### Acceptance Criteria

1. THE Conformance_Harness in-process subset SHALL exercise the Misc_Surface with the unmodified stock OpenAI SDK over `httpx.ASGITransport` against `build_app`, with only `base_url` and the API key swapped and `StubProviderClient` behind it.
2. THE Conformance_Harness SHALL cover embeddings single and batch with index order preserved, models list and retrieve and an unknown id raising `openai.NotFoundError`, moderations benign and flagged with a firewall finding surfaced and a missing-input error, and a BLOCK on an embeddings input yielding zero provider calls.
3. THE real embedding provider socket, the real model catalog backend, and any real-TCP or live-SDK run SHALL remain deferred live gates and SHALL NOT be scaffolded as un-runnable local gates.
4. THE Misc_Surface SHALL preserve the import-linter layer contract (both contracts, including `egress` not importing `detect`), the `check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_tenant_scale`, and `check_sizes` AST gates, `mypy --strict`, ruff, and the house seeded-random property idiom of at least 10,000 iterations without hypothesis, and SHALL keep `unscanned_channels()` empty.
