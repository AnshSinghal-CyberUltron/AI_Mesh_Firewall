# Requirements Document

## Introduction

Card B4 (GW17 / GW18) builds the **real provider egress client** and the **C43 load-aware
guard-routing selection** for the `gateway_v2` backend rewrite. Today
`gateway_v2/dispatch/provider.py` ships only a `StubProviderClient` — an in-process scripted
substitute whose docstring states "the real-socket path is a DEFERRED live gate" — and
`gateway_v2/dispatch/routing.py` ships only a pure plan→destination `DispatchRouter`. Neither the
real streaming HTTP/SSE client to the organization-selected generation provider nor the load-aware
selection across the self-hosted guard pool exists.

This card implements the **local half (Category 2)** per `docs/LOCAL_WORKLIST_v3.md` §B4: the real
`ProviderClient` code behind the shipped `@runtime_checkable` Protocol, its injection seam through
`build_app(provider=...)`, and the C43 `GuardRouter` selection — all verified **locally against
fake upstreams** (an asyncio/aiohttp stub server speaking the provider's OpenAI SSE shape, plus a
fake guard). The real sockets (real BYOK provider; real GPU guard fleet) are the **deferred live
gate**.

### Runbook-resolved architecture (two distinct upstreams)

The crux of B4 is that there are **two separate upstreams**, settled by
`docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` (so this spec does not re-ask the owner's open
question #1 "upstream identity"):

1. **Generation provider** — the organization-selected / BYOK OpenAI-compatible external LLM. The
   runbook constraint table "Customer model" row states the generation provider/BYOK "remains
   allowed and is **excluded from firewall-infrastructure cost/latency accounting**". The firewall
   does not own or host it; `/v1/chat/completions` forwards the scanned prompt there and streams the
   completion back. OpenAI-compatible JSON + SSE through the official Python/Node SDKs — including
   tool-call streaming, cancellation, and `[DONE]` — is required ("SSE / SDK" row).

2. **Guard model** — the **self-hosted** enforcement detector tier (TensorRT Prompt Guard on the
   L4/T4 GPU fleet), reached over TCP in the split topology ("c4-highcpu-16 gateway + g2-standard-4
   guard over TCP", §0.2 C10/C38). The runbook "Platform AI" row forbids any platform-owned external
   AI for enforcement, so the guard is in-house.

**Dispatch routing (GW18)** is the measured **C43 "load-aware guard routing"** (§0.2 C43): routing
across the guard pool to minimize queue-wait (queue-wait ratio 0.16–0.20 with routing on vs
0.48–0.50 off; "190 per guard"; 3 gateways + 4 guards → 760 RPS; owner-queue bound
`RV_OWNER_QUEUE_MS`). B4's dispatch routing is this guard-pool selection.

### Scope boundary (what B4 does and does not build)

B4 = **generation egress** (the real `ProviderClient`) + **C43 guard ROUTING selection** + the
**guard egress seam**. The actual detector model call to the guard (feeding findings into the
pipeline) is **GW07/GW08 territory**, not B4; the guard-routing selection is still fully testable
against a fake guard without that detector existing. This follows the worklist §B4 scope and the
owner's leaning noted in the task briefing.

## Glossary

- **ProviderClient**: The shipped `@runtime_checkable` Protocol in `gateway_v2/dispatch/provider.py`
  — `open(request) -> AsyncIterator[UpstreamEvent]` plus `async abort()`. B4 implements a real
  drop-in behind this exact protocol.
- **Real_Provider_Client**: The B4-built real `ProviderClient` that opens a streaming HTTP/SSE
  request to the Generation_Provider. The in-process `StubProviderClient` remains the default.
- **Generation_Provider**: The organization-selected / BYOK OpenAI-compatible external LLM.
  Excluded from firewall-infrastructure cost/latency accounting.
- **Guard_Pool**: The set of self-hosted guard (detector) endpoints reached over the split-topology
  TCP. The enforcement tier; never a platform-owned external AI.
- **Guard_Router**: The B4-built C43 load-aware selector that chooses one Guard_Pool member to
  minimize queue-wait, given an injected per-guard load snapshot.
- **DispatchRouter**: The shipped pure plan-identity→destination selector
  (`gateway_v2/dispatch/routing.py`). B4 leaves it unchanged.
- **Load_Snapshot**: An injected, point-in-time reading of per-guard load signal (e.g. queue depth /
  owner-queue wait) that Guard_Router selects from. It is injected state, not wall-clock or
  randomness.
- **RV_OWNER_QUEUE_MS**: The runbook owner-queue bound used by C43 routing (RC-measured defaults
  around 20 ms).
- **UpstreamRequest / UpstreamEvent**: The shipped frozen value types in
  `gateway_v2/dispatch/provider.py`. B4 does not change them.
- **Transform**: The shipped byte-verified dispatch codec
  (`gateway_v2/dispatch/transform.py`) converting between the OpenAI wire shape and the provider
  body. The Real_Provider_Client reuses it.
- **Cancellation_Bound**: The GW12 in-flight cancellation deadline that `abort()` must honor (the
  shipped `InFlightKill` / cancellation controller contract).
- **Error_Frame**: A provider mid-stream error surfaced as `UpstreamEvent.error_frame`, scanned
  before any byte is forwarded (the shipped R11 scan-before-forward).
- **Fake_Upstream**: A local asyncio/aiohttp stub server that speaks the Generation_Provider's
  OpenAI SSE shape, used for local verification. The Fake_Guard is its guard-tier counterpart.
- **build_app seam**: The shipped `gateway_v2/edge/app.py` `build_app(provider=...)` injection point.
  A deployment swaps `StubProviderClient` for the Real_Provider_Client with no gateway rewrite.
- **Live_Gate**: A deferred verification that requires real cloud resources (the real BYOK provider
  socket, the real GPU guard fleet socket, fleet-scale C43 capacity).

## Requirements

### Requirement 1: Real generation-egress ProviderClient

**User Story:** As the gateway data plane, I want a real `ProviderClient` that streams from the
organization-selected generation provider, so that a scanned prompt can be forwarded to the BYOK
LLM and its completion streamed back without changing the shipped egress protocol.

#### Acceptance Criteria

1. THE Real_Provider_Client SHALL implement the shipped `ProviderClient` Protocol
   (`open(request) -> AsyncIterator[UpstreamEvent]` and `async abort()`) without modifying the
   `ProviderClient` Protocol or the `UpstreamRequest` / `UpstreamEvent` value types.
2. WHEN the Real_Provider_Client opens a request, THE Real_Provider_Client SHALL open a streaming
   HTTP/SSE connection to the Generation_Provider named by `UpstreamRequest.destination`.
3. WHEN the Generation_Provider streams response chunks, THE Real_Provider_Client SHALL yield
   `UpstreamEvent` values whose `text_deltas` are byte-compatible with the shipped
   `egress.stream` transport shape (`tuple[tuple[str, str], ...]`).
4. WHEN the Generation_Provider stream reaches its terminal chunk, THE Real_Provider_Client SHALL
   yield an `UpstreamEvent` with `final` set to `True`.
5. THE Real_Provider_Client SHALL produce the request body for the Generation_Provider using the
   shipped `Transform`, reusing the existing codec rather than duplicating body shaping.
6. THE Generation_Provider SHALL be treated as organization-selected / BYOK and SHALL be excluded
   from firewall-infrastructure cost and latency accounting, consistent with the runbook "Customer
   model" constraint.

### Requirement 2: HTTP/SSE client choice and byte-verified parsing

**User Story:** As a gateway maintainer, I want the real egress to use a well-defined async HTTP/SSE
client and reuse the shipped SSE codec, so that the wire parsing stays byte-correct and the client
is injected the same way as every other dependency.

#### Acceptance Criteria

1. THE Real_Provider_Client SHALL use a single async HTTP/SSE client whose selection is recorded as
   a design decision (the runbook does not pin the library; `httpx` is already available via the
   `conformance` extra).
2. WHERE the selected HTTP client library is an optional dependency, THE gateway package SHALL
   declare that library under a dedicated optional extra (for example a `provider` or `serve`
   extra), so the base package remains framework-free and a tree without the extra still imports.
3. WHEN the Real_Provider_Client parses the Generation_Provider SSE stream, THE Real_Provider_Client
   SHALL reuse the shipped SSE codec / `Transform` round-trip rather than duplicating SSE parsing.
4. FOR ALL well-formed provider payloads, THE Real_Provider_Client SHALL preserve the shipped
   `Transform` byte-identity round-trip on the text content (the GW12 Property 8 half).

### Requirement 3: Abort cuts the in-flight upstream read

**User Story:** As the GW12 cancellation controller, I want `abort()` on the real client to actually
stop reading the upstream socket within the cancellation bound, so that a cancelled stream frees the
connection instead of draining to completion.

#### Acceptance Criteria

1. WHEN `abort()` is awaited on the Real_Provider_Client during an in-flight stream, THE
   Real_Provider_Client SHALL stop reading from the Generation_Provider connection.
2. WHEN `abort()` stops an in-flight read, THE Real_Provider_Client SHALL cease reading within the
   shipped Cancellation_Bound honored by the GW12 `InFlightKill` / cancellation controller.
3. WHEN the Real_Provider_Client stops an aborted read, THE Real_Provider_Client SHALL release the
   underlying connection resources for that request.
4. IF `abort()` is awaited more than once, THEN THE Real_Provider_Client SHALL treat each additional
   call as a no-op.

### Requirement 4: C43 load-aware guard routing (GW18)

**User Story:** As the gateway dispatch layer, I want a load-aware selector that picks a guard from
the guard pool to minimize queue-wait, so that the fleet reproduces the measured C43 behaviour
(queue-wait ratio 0.16–0.20 routing-on vs 0.48–0.50 off).

#### Acceptance Criteria

1. WHEN the Guard_Router selects a guard, THE Guard_Router SHALL choose a Guard_Pool member that
   minimizes queue-wait given the injected Load_Snapshot.
2. FOR ALL Load_Snapshots, given the same Load_Snapshot and the same candidate Guard_Pool, THE
   Guard_Router SHALL return the same selection (deterministic given its inputs).
3. THE Guard_Router SHALL derive its selection from the injected Load_Snapshot and SHALL NOT read a
   wall-clock or generate randomness, except for an injected tie-break source used only when two
   candidates are equally loaded.
4. THE Guard_Router SHALL apply `RV_OWNER_QUEUE_MS` as the owner-queue bound consistent with the
   runbook C43 configuration.
5. THE shipped pure plan-identity→destination `DispatchRouter` SHALL remain unchanged, and the
   load-aware guard selection SHALL be a distinct selection concern (a sibling selector rather than
   a modification of `DispatchRouter`).

### Requirement 5: Explicit generation-vs-guard split in the egress wiring

**User Story:** As a gateway operator, I want chat/embeddings egress and guard-tier egress to be
wired to their correct distinct upstreams, so that generation traffic goes to the BYOK provider and
enforcement traffic goes to the self-hosted guard pool.

#### Acceptance Criteria

1. WHEN a chat or embeddings request is dispatched, THE gateway SHALL route generation egress to the
   Generation_Provider.
2. WHEN guard-tier selection is required, THE gateway SHALL route to a Guard_Pool member reached
   over the split-topology TCP path.
3. THE Guard_Pool SHALL be a self-hosted enforcement tier and SHALL NOT be a platform-owned external
   AI dependency, consistent with the runbook "Platform AI" constraint.
4. WHERE the real detector call (GW07/GW08) is not yet built, THE Guard_Router selection SHALL
   remain exercisable against a Fake_Guard, so B4 verifies routing without the detector model.

### Requirement 6: Local verification against fake upstreams

**User Story:** As a gateway maintainer, I want the real client and guard routing verified locally
against fake upstreams, so that the socket-free local half proves correctness before any cloud
resource is lit up.

#### Acceptance Criteria

1. THE local verification SHALL drive the Real_Provider_Client against a Fake_Upstream that speaks
   the Generation_Provider's OpenAI SSE shape over asyncio/aiohttp.
2. THE local verification SHALL exercise the Guard_Router selection against a Fake_Guard.
3. THE local verification SHALL confirm the shipped `Transform` byte-identity round-trip on provider
   content (reusing the GW12 property).
4. THE local verification SHALL confirm that `abort()` stops an in-flight read against the
   Fake_Upstream.
5. WHERE a property is genuinely universal, THE local verification SHALL express it as a seeded
   property-based test using the house idiom (seeded `random.Random`, no hypothesis).

### Requirement 7: Honest boot interaction through the shipped seam

**User Story:** As a deployment engineer, I want the real client to be a drop-in swap through the
existing `build_app(provider=...)` seam, so that enabling real generation requires no gateway
rewrite and the stub stays the safe default.

#### Acceptance Criteria

1. THE Real_Provider_Client SHALL be injectable through the shipped `build_app(provider=...)` seam
   without changing the seam's signature.
2. WHILE no `provider` is explicitly injected, THE `build_app` seam SHALL continue to default to the
   in-process `StubProviderClient`.
3. WHEN the Real_Provider_Client is injected and a reachable Generation_Provider exists, THE gateway
   data plane SHALL produce real completions; and WHILE those conditions are not met, THE local
   verification SHALL use the Fake_Upstream instead.

### Requirement 8: Deferred live gates named as seams, not scaffolded

**User Story:** As the program owner, I want the deferred cloud work named explicitly as seams, so
that the local half does not scaffold cloud behaviour and the live gates stay clearly bounded.

#### Acceptance Criteria

1. THE spec SHALL name the real BYOK Generation_Provider socket as a deferred Live_Gate and SHALL
   NOT scaffold a real external provider connection in the local half.
2. THE spec SHALL name the real GPU Guard_Pool fleet socket as a deferred Live_Gate (GW25) and SHALL
   NOT scaffold a real guard fleet connection in the local half.
3. THE spec SHALL name fleet-scale C43 capacity measurement as a deferred Live_Gate (GW20 / GW20b).
4. THE spec SHALL document the firewall-cost exclusion of the Generation_Provider as an accounting
   boundary rather than an implemented metric.

### Requirement 9: House conventions and gate compliance

**User Story:** As a gateway maintainer, I want B4 to satisfy the repository's structural gates, so
that the new code lands inside the existing layering and size discipline.

#### Acceptance Criteria

1. THE Real_Provider_Client and Guard_Router SHALL reside in the `dispatch` layer and SHALL import
   only `domain` and layers below it, never the plan compilation layer (import-linter `layers`
   contract).
2. THE B4 code SHALL keep every file at or under 800 lines and every function at or under 120 lines
   (the `check_sizes` AST gate).
3. THE B4 code SHALL place no capacity literal outside `runtime/resources.py`, consistent with the
   capacity-literal AST gate.
4. THE B4 code SHALL keep the two import-linter contracts in the kept state and SHALL pass
   `mypy --strict`, `ruff`, `lint-imports`, and the six AST gates
   (`check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`,
   `check_http_outside_edge_resolve`, `check_sizes`, `check_tenant_scale`).
