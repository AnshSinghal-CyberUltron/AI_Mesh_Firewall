# Requirements Document

## Introduction

This feature implements card **GW15** of the AI Mesh Firewall v2 backend rewrite (`gateway_v2/`): the real OpenAI-compatible chat/completions surface. It replaces the thin wiring proof in `gateway_v2/gateway_v2/edge/routes.py` `ChatRoute.handle` with the full `/v1/chat/completions` and `/v1/completions` handler (streaming AND non-streaming), landing the chat-surface adapter in the currently-empty `gateway_v2/gateway_v2/edge/openai_chat.py` (`__all__ = ()`).

GW15 wires ALREADY-SHIPPED pipeline mechanisms into the real request path. The thin proof today drives `DispatchRouter` → injected `ProviderClient` → `Coalescer` (fail-closed admit) → the shipped `StreamPipeline` release loop → one `SSEEncoder`, under the `FirstByteLatch` no-splice gate, `InFlightControl`, and `StreamTimeouts`, with an offloaded mid-stream error-frame scan via `ScanExecutor`. That proof runs the pipeline in PASS-THROUGH mode (`enforcing_output=False`) and names a `_DEFAULT_DESTINATION` because no resolved `ExecutionPlan` exists yet. Every deferral in `routes.py` is explicitly marked "GW15 adds the signed fallback chain / real `for_plan` resolution / request parsing into a plan". GW15 REPLACES those deferrals with the real request parsing, plan resolution, enforcing-output decision, auth, admission, and budget-lease wiring.

The reason this card exists is the channel tests. The v2.1 prototype PASSED SDK acceptance but FAILED the channel tests, so they were added as first-class checks: a canary in EVERY SDK request field under BLOCK must produce zero provider calls, and a canary in EVERY output field under REDACT must reach the client zero times. This maps to risk C21 (a clean rebuild silently drops v1's ~95 input-channel-folding / output-channel-scanning / encoding behaviours unless they are enumerated as requirements).

### Scope boundaries (explicit cross-references)

- **Shipped mechanisms — DONE, OUT OF SCOPE to rebuild; GW15 WIRES them.** GW19 CoDel owner admission (`admit/` package, `AdmissionController`), GW06 budget lease (`admit/lease.py`, `admit/quota.py` `QuotaComponent`), GW12b bounded holdback + output guard (`egress/stream.py` `StreamPipeline`, `egress/output_guard.py` `OutputResolver`/`apply_decision`, `detect/holdback.py`, `detect/windowing.py`), GW14d observability (`edge/` render/counters/rates/windowed/event_log/readiness + per-request exports), and GW05b/c state + identity + freshness stamp are shipped. Requirements here wire into them without changing them.
- **Chat/completions ONLY.** `/v1/chat/completions` and `/v1/completions`, streaming and non-streaming. `/v1/responses`, embeddings, MCP, RAG/vector STAY as the declared `not_implemented` placeholders already registered in `build_routes` (GW16/17/18). GW15 does not touch them.
- **Existing app — wire ONTO it.** GW15 wires onto the existing raw-ASGI app (`edge/app.py` + `edge/routes.py` `build_routes`/`ChatRoute`). There is NO new server-process / entrypoint card in scope.

### Deferred live gates (category-2 seam — standing operator instruction)

The operator has NO cloud resources. These are shipped as code cloud-complete + the seam, and are NOT scaffolded as un-runnable gates:
- **Real provider socket** — `StubProviderClient` stays the in-process test seam; the real HTTP/SSE socket is a DEFERRED live gate.
- **LGW15-1 real-TCP + Node SDK conformance** — the in-process conformance subset is buildable locally; real-TCP + Node runs are deferred.
- **LGW15-6 10,000 C2 chat replay** — deferred live gate.
- Any fleet / cloud measurement — deferred.

## Glossary

- **Gateway**: The `gateway_v2` AI Mesh Firewall serving process that terminates client OpenAI-compatible requests, forwards to an upstream provider, and enforces input/output guards.
- **Chat_Surface**: The `/v1/chat/completions` and `/v1/completions` handler landing in `edge/openai_chat.py`, wired through `edge/routes.py` `ChatRoute`.
- **SDK_Request**: An OpenAI-compatible JSON request body sent by an official OpenAI SDK (Python or Node) to the Chat_Surface.
- **Canonical_Request**: The internal value produced by parsing an SDK_Request, carrying every scannable text channel and the routing fields, consumed by plan resolution and detection.
- **Input_Channel**: Any field of an SDK_Request that can carry client text: `messages` content, `system` content, tool/function call arguments, `name` fields, split/array text parts, and nested content.
- **Output_Channel**: Any field of an upstream response that can carry provider text reaching the client: content deltas, tool/function call argument fragments, and any nested text part.
- **Execution_Plan**: The resolved per-request plan (tenant policy, routing destination, enforcing-output set) that replaces the pass-through flag and `_DEFAULT_DESTINATION`, produced by a real `for_plan` resolution.
- **Dispatch_Router**: The deterministic plan-derived upstream selector (`dispatch/routing.py` `DispatchRouter`) that selects the destination once an Execution_Plan exists.
- **ProviderClient**: The injectable, abortable upstream streaming client protocol (`dispatch/provider.py`) speaking the OpenAI HTTP/SSE wire protocol to a configurable `base_url`.
- **StubProviderClient**: The shipped in-process `ProviderClient` substitute (`dispatch/provider.py`) with NO real socket, injected in local tests.
- **StreamPipeline**: The shipped bounded-holdback release loop (`egress/stream.py`) that scans and releases upstream deltas; GW15 runs it in enforcing-output mode derived from the Execution_Plan.
- **OutputResolver**: The shipped output-guard resolver (`egress/output_guard.py` `OutputResolver` + `apply_decision`) that decides ALLOW/REDACT/BLOCK per finding over output text.
- **AdmissionController**: The shipped GW19 CoDel owner-admission component (`admit/` package) returning a `ShedVerdict` or `Admitted`.
- **QuotaComponent**: The shipped GW06 budget-lease façade (`admit/quota.py`) that spends a locally-held org budget lease (`admit/lease.py`) without a request-path store read.
- **Pre_Identity_Clock**: The §10.4 lifecycle phase that runs cheap pre-checks (size, expansion ratio, part counts) BEFORE auth-independent heavy canonicalization, per constraint C26.
- **Shared_State_Round_Trip**: The exactly-one shared-state access the §10.4 request lifecycle performs per request.
- **Tenant_Identity**: The authenticated principal and org/tenant resolved for a request, from the shipped GW05b/c identity + freshness stamp.
- **Enforced_Finding**: A detection finding whose policy disposition is enforced (BLOCK, REDACT, or FLAG).
- **BLOCK**: The most-restrictive per-finding disposition (C22): a request or output carrying a BLOCK-enforced finding is refused; a REDACT/FLAG rule never cancels a BLOCK.
- **REDACT**: The disposition under which matched bytes are removed/masked so the canary never reaches the client.
- **Channel_Test**: The acceptance that a canary placed in any Input_Channel under BLOCK yields zero provider calls, and a canary placed in any Output_Channel under REDACT reaches the client zero times.
- **Error_Envelope**: The single `edge/errors.py` builder rendering value-codes into HTTP/SSE responses (`render_http`, `render_sse`, `render_not_implemented`), honoring the codes-vs-render boundary.
- **Frozen_Contract**: The OpenAI-compatible request/response contract an unmodified SDK depends on; any required client code change breaks it (LGW15-2 STOP RULE).
- **ResourceContract**: The capacity authority (`runtime/resources.py`) — the ONLY module permitted capacity-position literals. All serving bounds derive from it or from the injected `HoldbackConfig`.
- **FAIL_OPEN**: Forwarding raw (unscanned/undecided) bytes or admitting a request when a guard, count, auth, or decision cannot be computed. This is forbidden; the Gateway fails closed. FAIL_OPEN is pinned at 0.
- **Per_Request_Exports**: The shipped GW14d per-request observability export surface the Chat_Surface populates.
- **Conformance_Harness**: The existing `gateway_v2/tests/openai_conformance/` suite (Python SDK compat, Node dir, live-TCP, uvicorn), currently excluded via pyproject `addopts`.

## Requirements

### Requirement 1: Parse SDK request into a canonical request

**User Story:** As an OpenAI SDK client developer, I want the Gateway to parse my request body into an internal canonical form, so that every scannable text channel and routing field is available to detection and plan resolution.

#### Acceptance Criteria

1. WHEN an SDK_Request is received at `/v1/chat/completions` or `/v1/completions`, THE Chat_Surface SHALL parse the request body into a Canonical_Request.
2. WHEN the Chat_Surface parses an SDK_Request, THE Chat_Surface SHALL extract every Input_Channel into the Canonical_Request, including `messages` content, `system` content, tool and function call arguments, `name` fields, split or array text parts, and nested content.
3. WHEN an SDK_Request carries the `stream` field set to true, THE Chat_Surface SHALL record a streaming disposition in the Canonical_Request.
4. IF an SDK_Request body is malformed or violates the OpenAI-compatible request shape, THEN THE Chat_Surface SHALL render a declared value-code through the Error_Envelope rather than a raw framework error.
5. THE Chat_Surface SHALL perform request parsing in `edge/wire/` route-specific logic and SHALL NOT place security decisions in `edge/`.

### Requirement 2: Resolve the execution plan

**User Story:** As a platform operator, I want each request resolved into a real execution plan, so that routing and output enforcement reflect the tenant policy instead of a pass-through default.

#### Acceptance Criteria

1. WHEN a Canonical_Request and Tenant_Identity are available, THE Chat_Surface SHALL resolve an Execution_Plan through a real `for_plan` resolution.
2. THE Chat_Surface SHALL derive the upstream destination from the Execution_Plan via the Dispatch_Router and SHALL NOT use the `_DEFAULT_DESTINATION` stand-in.
3. THE Chat_Surface SHALL run the StreamPipeline with the enforcing-output set derived from the Execution_Plan rather than the pass-through flag `enforcing_output=False`.
4. IF plan resolution cannot complete, THEN THE Chat_Surface SHALL render a declared value-code through the Error_Envelope and SHALL NOT forward the request to a provider.

### Requirement 3: Pre-identity clock and cheap pre-checks

**User Story:** As a platform operator, I want cheap pre-checks to run before heavy canonicalization and before authentication, so that oversized or abusive requests are rejected without consuming auth-dependent work.

#### Acceptance Criteria

1. WHEN a request is received, THE Chat_Surface SHALL run the Pre_Identity_Clock cheap pre-checks, including request size, expansion ratio, and part counts, before auth-independent heavy canonicalization, per constraint C26.
2. IF a Pre_Identity_Clock pre-check exceeds its bound derived from the ResourceContract, THEN THE Chat_Surface SHALL reject the request through the Error_Envelope before canonicalization.
3. THE Chat_Surface SHALL perform exactly one Shared_State_Round_Trip per request during the §10.4 request lifecycle.
4. THE Chat_Surface SHALL derive every Pre_Identity_Clock bound from the injected ResourceContract and SHALL NOT embed a capacity literal outside `runtime/resources.py`.

### Requirement 4: Authenticate and resolve tenant identity

**User Story:** As a platform operator, I want each request authenticated and attributed to a tenant, so that policy, routing, and audit are scoped to the correct org.

#### Acceptance Criteria

1. WHEN a request passes the Pre_Identity_Clock pre-checks, THE Chat_Surface SHALL resolve Tenant_Identity using the shipped GW05b/c identity and freshness stamp.
2. IF Tenant_Identity cannot be resolved, THEN THE Chat_Surface SHALL render a declared authentication value-code through the Error_Envelope and SHALL NOT forward the request to a provider.
3. IF the freshness stamp is stale or unavailable, THEN THE Chat_Surface SHALL fail closed and refuse the request rather than forward it with unresolved identity.

### Requirement 5: Admit the request through GW19 admission control

**User Story:** As a platform operator, I want each request admitted through CoDel owner admission, so that one tenant's overload cannot degrade another and shed requests return a correct retry signal.

#### Acceptance Criteria

1. WHEN a request has a resolved Tenant_Identity, THE Chat_Surface SHALL submit the request to the shipped AdmissionController.
2. IF the AdmissionController returns a `ShedVerdict`, THEN THE Chat_Surface SHALL render the verdict's value-code through the Error_Envelope and SHALL NOT forward the request to a provider.
3. WHEN the AdmissionController returns an `Admitted` grant, THE Chat_Surface SHALL continue the request lifecycle.
4. IF the AdmissionController cannot produce a decision, THEN THE Chat_Surface SHALL fail closed and shed the request.

### Requirement 6: Spend the GW06 budget lease

**User Story:** As a platform operator, I want each admitted request to spend a locally-held org budget lease, so that budget is enforced without a shared-state read on the hot path.

#### Acceptance Criteria

1. WHEN a request is admitted, THE Chat_Surface SHALL request a budget decision from the shipped QuotaComponent.
2. IF the QuotaComponent refuses the request for budget, THEN THE Chat_Surface SHALL render the declared budget value-code through the Error_Envelope and SHALL NOT forward the request to a provider.
3. THE Chat_Surface SHALL obtain the budget decision from the locally-held lease and SHALL NOT perform a shared-state read on the request path for the budget spend.

### Requirement 7: Block input-channel findings before any provider call

**User Story:** As a security engineer, I want a BLOCK-enforced finding in any request field to stop the request before the provider is contacted, so that no blocked content ever leaves the Gateway toward an upstream model.

#### Acceptance Criteria

1. WHEN a Canonical_Request is resolved, THE Chat_Surface SHALL scan every Input_Channel through the injected detection mechanism before opening a ProviderClient stream.
2. IF any Input_Channel carries an Enforced_Finding with a BLOCK disposition, THEN THE Chat_Surface SHALL refuse the request and SHALL make zero provider calls.
3. WHEN a request is refused for a BLOCK input finding, THE Chat_Surface SHALL render the declared block value-code through the Error_Envelope.
4. WHERE a REDACT or FLAG rule co-matches with a BLOCK rule on the same request, THE Chat_Surface SHALL enforce the BLOCK disposition as the final per-finding outcome, per constraint C22.
5. THE Chat_Surface SHALL receive the detection mechanism and resolver as injected dependencies through `edge` above `detect`, and `egress` SHALL NOT import `detect`.

### Requirement 8: Redact output-channel findings so a canary never reaches the client

**User Story:** As a security engineer, I want a REDACT-enforced finding in any output field removed before the client sees it, so that a canary placed in any output channel reaches the client zero times.

#### Acceptance Criteria

1. WHEN upstream content is released, THE Chat_Surface SHALL scan every Output_Channel through the OutputResolver before the bytes are sent downstream.
2. IF an Output_Channel carries an Enforced_Finding with a REDACT disposition, THEN THE Chat_Surface SHALL remove or mask the matched bytes so the matched content reaches the client zero times.
3. IF an Output_Channel carries an Enforced_Finding with a BLOCK disposition, THEN THE Chat_Surface SHALL withhold the output and render the declared value-code through the Error_Envelope.
4. IF an output scan or decision cannot be computed, THEN THE Chat_Surface SHALL fail closed and withhold the bytes, keeping FAIL_OPEN at zero.

### Requirement 9: Non-streaming chat completion

**User Story:** As an OpenAI SDK client developer, I want non-streaming chat and text completions to return a single enforced JSON response, so that non-streaming clients work unchanged.

#### Acceptance Criteria

1. WHERE a Canonical_Request records a non-streaming disposition, THE Chat_Surface SHALL collect the full upstream response, apply the output enforcement decision, and return one OpenAI-compatible JSON response.
2. WHEN a non-streaming response is returned, THE Chat_Surface SHALL render it through the single response path so an unmapped error value-code renders the declared Error_Envelope body rather than a raw framework error.
3. IF an Enforced_Finding with a BLOCK disposition is present in the collected response, THEN THE Chat_Surface SHALL withhold the response body and render the declared block value-code through the Error_Envelope.

### Requirement 10: Streaming chat completion with tool-call fragment reassembly

**User Story:** As an OpenAI SDK client developer, I want streamed tool calls with fragmented arguments to reconstruct exactly, so that the SDK rebuilds the exact call I expected.

#### Acceptance Criteria

1. WHERE a Canonical_Request records a streaming disposition, THE Chat_Surface SHALL stream the enforced response through the shipped StreamPipeline release loop and emit a terminal `[DONE]` marker on clean completion.
2. WHEN upstream delivers a tool-call with arguments split across multiple fragments, THE Chat_Surface SHALL forward the fragments so the official SDK reconstructs the identical tool call.
3. WHEN the FirstByteLatch is unset, THE Chat_Surface SHALL permit a signed fallback or retry attempt, and WHEN the first content byte is released, THE Chat_Surface SHALL set the latch and SHALL NOT splice a second upstream response.
4. IF an InFlightControl trigger fires — maximum stream duration, kill switch, key revocation, plan change, or stale snapshot — THEN THE Chat_Surface SHALL cut the stream and render the matching declared Error_Frame at the single terminal site.

### Requirement 11: OpenAI-compatible provider client over base_url

**User Story:** As a platform operator, I want the Gateway to forward requests to a configurable OpenAI-compatible upstream, so that the Gateway is a transparent proxy to any provider at a given base_url.

#### Acceptance Criteria

1. THE Chat_Surface SHALL forward resolved requests through the injected ProviderClient, which speaks the OpenAI HTTP/SSE wire protocol to a configurable `base_url`.
2. WHERE local tests run, THE Chat_Surface SHALL accept the shipped StubProviderClient as the injected ProviderClient with no real socket.
3. THE Chat_Surface SHALL treat the real provider socket as a deferred live gate and SHALL NOT implement a real HTTP client in this feature.

### Requirement 12: Error envelope and frozen-contract fidelity

**User Story:** As an OpenAI SDK client developer, I want errors rendered in the OpenAI-compatible shape, so that an unmodified SDK with only base_url and key swapped works unchanged.

#### Acceptance Criteria

1. THE Chat_Surface SHALL render every value-code through the single Error_Envelope, as an HTTP body for non-streaming requests and as a declared SSE Error_Frame for streaming requests.
2. IF a value-code is unmapped, THEN THE Chat_Surface SHALL render the declared generic error through the Error_Envelope rather than leaking raw internal detail.
3. WHEN an unmodified OpenAI SDK targets the Gateway with only `base_url` and API key swapped, THE Chat_Surface SHALL serve the request unchanged for both streaming and non-streaming, preserving the Frozen_Contract.
4. IF serving a request would require any client code change beyond swapping `base_url` and the API key, THEN THE Chat_Surface SHALL fail the acceptance as a broken Frozen_Contract, per the LGW15-2 stop rule.

### Requirement 13: Two-tenant isolation

**User Story:** As a platform operator, I want two tenants with opposite policies served concurrently without cross-contamination, so that one tenant's configuration, findings, request IDs, and audit never leak into another's.

#### Acceptance Criteria

1. WHILE two tenants with opposite policies are served concurrently, THE Chat_Surface SHALL apply each tenant's own Execution_Plan to that tenant's request.
2. WHILE two tenants are served concurrently, THE Chat_Surface SHALL confine each tenant's configuration, findings, request IDs, and audit records to that tenant.
3. WHEN a request completes, THE Chat_Surface SHALL attribute its findings, request ID, and audit record to the originating Tenant_Identity only.

### Requirement 14: Per-request observability exports

**User Story:** As a platform operator, I want each chat request to populate the per-request export surface, so that latency, enforcement, and admission signals are observable without leaking content.

#### Acceptance Criteria

1. WHEN a request completes or is refused, THE Chat_Surface SHALL populate the shipped GW14d Per_Request_Exports.
2. THE Chat_Surface SHALL record admission, budget, input-enforcement, and output-enforcement outcomes in the Per_Request_Exports.
3. THE Chat_Surface SHALL keep FAIL_OPEN pinned at zero in the Per_Request_Exports.
4. THE Chat_Surface SHALL NOT place raw request or response content in the Per_Request_Exports.

### Requirement 15: Conformance harness enablement with deferred live gates

**User Story:** As a maintainer, I want the in-process conformance subset to run and pass locally, so that OpenAI compatibility is verified without cloud resources while the live gates stay seams.

#### Acceptance Criteria

1. THE Conformance_Harness in-process subset SHALL be enabled locally and SHALL pass against the Chat_Surface for the §5 S01 through S07 scenarios.
2. WHEN the in-process conformance subset runs, THE Chat_Surface SHALL satisfy OpenAI-compatible C1 conformance for streaming and non-streaming requests.
3. THE real-TCP conformance run, the Node SDK run, and the 10,000 C2 chat replay SHALL remain deferred live gates and SHALL NOT be scaffolded as un-runnable local gates.
4. THE Chat_Surface SHALL preserve the import-linter layer contract, the capacity-literal and module-level-mutable AST gates, `mypy --strict`, ruff, and the house seeded-random property idiom of at least 10,000 iterations without hypothesis.
