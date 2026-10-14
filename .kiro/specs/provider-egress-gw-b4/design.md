# Design Document — Card B4 (GW17 / GW18): Provider egress client + dispatch routing

## Overview

Card B4 builds two pieces of the `gateway_v2` dispatch layer that today ship only as a stub or a
placeholder:

1. **`Real_Provider_Client`** — a real streaming HTTP/SSE `ProviderClient` to the
   organization-selected generation provider, dropping in behind the shipped
   `@runtime_checkable` `ProviderClient` Protocol in `gateway_v2/dispatch/provider.py` (today only
   `StubProviderClient` exists, whose docstring says "the real-socket path is a DEFERRED live
   gate"). (R1, R2, R3)
2. **`GuardRouter`** — the C43 load-aware selector that picks one member of the self-hosted guard
   pool to minimize queue-wait, a NEW sibling to the shipped pure `DispatchRouter` in
   `gateway_v2/dispatch/routing.py`, which is left unchanged. (R4, R5)

This card is the **local half (Category 2)** per `docs/LOCAL_WORKLIST_v3.md` §B4: the client code,
the injection seam, and the guard-routing selection are all built and verified **locally against
fake upstreams**; the real sockets (real BYOK provider; real GPU guard fleet) are deferred live
gates. (R6, R8)

### Runbook grounding (the governing document for every decision)

Per the user's hard constraint, every decision below is grounded in
`docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` and the actual code. The relevant runbook
anchors:

- **§0.4** — "Each card follows the §10.9 format (why, implementation, live acceptance tests, exit,
  evidence, rollback)." This design is shaped so `tasks.md` can produce that evidence; the Testing
  Strategy (§8) maps directly onto §10.9's *live acceptance tests → exit → evidence*.
- **Constraint table (§0.2, Part 0)** — three rows settle the two-upstream architecture:
  - *Customer model*: "Organization-selected generation provider/BYOK remains allowed and is
    **excluded from firewall-infrastructure cost/latency accounting**." → the `Generation_Provider`
    is BYOK and off the firewall cost/latency books (R1.6, R8.4).
  - *SSE / SDK*: "OpenAI-compatible JSON and SSE must work through official Python and Node SDKs,
    including tool-call streaming, cancellation and `[DONE]`." → the client speaks OpenAI-compatible
    SSE and honors cancellation + `[DONE]` (R1, R2, R3).
  - *Platform AI*: "No Bedrock, Vertex/Gemini, hosted moderation, or other platform-owned external
    AI dependency for enforcement." → the guard tier is **self-hosted**, never an external AI
    (R5.3).
- **§0.2 C10 / C38 split topology** — "c4-highcpu-16 gateway + g2-standard-4 guard over TCP"
  (confirmed at runbook lines 62, 122). The guard is reached over TCP in a split topology.
- **§0.2 C43 load-aware guard routing** — the measured behaviour the `GuardRouter` reproduces:
  "Queue-wait ratio 0.16–0.20 with routing on vs 0.48–0.50 with it off (RC1, 7/7 runs)"; "3
  gateways + 4 guards reach 760 RPS … which is 190 per guard (RC1 with a 20 ms owner bound)"; the
  fleet table records "With `RV_OWNER_QUEUE_MS=20`: … 760 ×3 PASS (190 per guard)" (runbook lines
  65–69, 126). GW19/R2-07 supplies the guard-wait budget relation `max(100, cap + 40)` ms where
  `cap` is the CoDel hard cap (60 ms).
- **Deferred (named seams, not scaffolded)** — the real BYOK provider socket and the real GPU guard
  fleet socket are the live gate; fleet-scale C43 capacity is GW20/GW20b; real GPU guard capacity
  ops is GW25 (runbook R2-15 → GW25, R2-16 → GW20).

### Where B4 sits — and why no gate breaks

B4 lives entirely in the **`dispatch`** layer. Per the import-linter `layers` contract in
`gateway_v2/pyproject.toml`, the order is `edge > admit > plan > detect > resolve > dispatch >
egress > audit > runtime > contracts > domain`. `dispatch` sits **above** `egress` / `runtime` /
`contracts` / `domain` and **below** the `plan` compilation layer. So the new modules may import
`gateway_v2.domain`, `gateway_v2.egress`, `gateway_v2.runtime` and below, and must **never** import
`gateway_v2.plan` (R9.1). This matches what `dispatch/transform.py` already does (it imports
`egress.stream.DownstreamFrame`), so no new layer edge is introduced and **no new egress→detect
edge** is created (the guard *routing* is in `dispatch`; the detector *call* that would touch
`detect` is GW07/GW08, not B4).

**`check_http_outside_edge_resolve` with an httpx client in dispatch — verified green.** I read the
gate (`gateway_v2/lint/check_http_outside_edge_resolve.py`). Its rule forbids exactly three things
outside `edge/` and `resolve/`: the names `HTTPException` and `JSONResponse`, and a keyword
`status_code=` whose constant value is a 4xx integer. It concerns **HTTP response construction**
(the C11/C12 "only `edge/` and `resolve/` render terminal outcomes" rule, runbook §10.4 and C11),
**not an outbound HTTP client**. An `httpx.AsyncClient` streaming *to* a provider constructs none of
those symbols, so the gate stays green with an httpx client in `dispatch`. This is confirmed by
reading the gate source, not assumed.

The two import-linter contracts ("Gateway v2 layers" and "Resolve imports only domain") remain in
the kept state because B4 adds no `resolve`-sourced import and no upward dispatch edge (R9.4).

---

## Architecture

```mermaid
flowchart TD
    subgraph edge["edge/ (top layer — injection + wire)"]
        BA["build_app(provider=...)"]
        SSEenc["edge/wire/sse.py SSEEncoder"]
        SSEdec["edge/wire/sse.py SSEDecoder (REUSED)"]
        CC["edge/cancel.py CancellationController + KillLatch"]
    end
    subgraph dispatch["dispatch/ (B4 lives here)"]
        PROTO["provider.py ProviderClient Protocol<br/>UpstreamRequest / UpstreamEvent (UNCHANGED)"]
        STUB["provider.py StubProviderClient (default, UNCHANGED)"]
        RPC["http_provider.py HttpProviderClient (NEW — B4)"]
        XFORM["transform.py Transform.to_provider / from_provider (REUSED)"]
        DR["routing.py DispatchRouter (pure, UNCHANGED)"]
        GR["guard_routing.py GuardRouter (NEW — B4, load-aware)"]
    end
    subgraph runtime["runtime/"]
        RES["resources.py ResourceContract<br/>owner_queue_bound_s() + guard_wait_budget_s() (NEW accessors)"]
    end

    BA -->|injects provider| RPC
    BA -->|default| STUB
    RPC -->|body via| XFORM
    RPC -->|parse SSE via| SSEdec
    RPC --> PROTO
    STUB --> PROTO
    GR -->|reads derived bound| RES
    CC -->|abort()| RPC

    RPC -. generation egress .-> GENP[("Generation_Provider<br/>(BYOK; cost-excluded)<br/>— real socket DEFERRED")]
    GR -. guard egress seam .-> GUARD[("Guard_Pool member<br/>(self-hosted; split-topology TCP)<br/>— real fleet DEFERRED (GW25)")]

    FU[["Fake_Upstream (local socket, OpenAI SSE)"]] -.drives.-> RPC
    FG[["Fake_Guard (local)"]] -.drives.-> GR
```

### Two distinct upstreams (the crux)

| Upstream | What it is | Reached how | Accounting | B4 builds |
|---|---|---|---|---|
| `Generation_Provider` | Org-selected / BYOK OpenAI-compatible LLM | Streaming HTTP/SSE | **Excluded** from firewall cost/latency (runbook *Customer model*) | The real `HttpProviderClient` (generation egress) + its injection seam |
| `Guard_Pool` | **Self-hosted** enforcement detector tier (TensorRT Prompt Guard, L4/T4) | Split-topology **TCP** (§0.2 C10/C38) | On the firewall books (it IS the firewall) | The C43 `GuardRouter` *selection* + the guard egress *seam* |

**Scope boundary (what B4 does and does not build).** B4 = generation egress (`HttpProviderClient`)
+ C43 guard **routing selection** (`GuardRouter`) + the guard egress **seam**. The actual detector
model call to the guard — feeding findings into the pipeline — is **GW07/GW08 territory**, named
not built. The routing selection is fully testable against a `Fake_Guard` without that detector
existing (R5.4).

### Flow of a generation request (local half)

1. `edge` resolves the plan and builds a `GatewayRequest`; `Transform.to_provider` packs it into a
   frozen `UpstreamRequest` (reused, not duplicated — R1.5, R2.3).
2. `edge` calls `provider.open(request)`; with the real client injected this opens a streaming
   HTTP/SSE connection to `UpstreamRequest.destination` (R1.2).
3. The client reads SSE bytes, feeds them through the **shipped** `edge/wire/sse.py SSEDecoder`
   (reused — R2.3), and re-shapes each decoded `DownstreamFrame` back into an `UpstreamEvent` (the
   inverse of `Transform.from_provider`) whose `text_deltas` are byte-compatible with the egress
   transport shape (R1.3); the last event carries `final=True` (R1.4); a mid-stream provider error
   becomes `UpstreamEvent.error_frame` so it is scanned before any byte is forwarded (R11
   scan-before-forward, already wired in `edge` via `ErrorFrameScanner`).
4. On client disconnect, `edge/cancel.py CancellationController` awaits `provider.abort()`, which
   stops the real socket read within `contract.cancellation_bound_s()` and releases the connection
   (R3).

### Guard routing (GW18 / C43)

`GuardRouter.select(snapshot)` chooses one `Guard_Pool` member that minimizes queue-wait given an
**injected** `Load_Snapshot`. The purity tension with the shipped `DispatchRouter` is resolved
explicitly: `DispatchRouter` is a pure plan-identity→destination function (no inputs but the plan);
C43 routing is **load-aware**, so it *must* read load state — but that state is **injected**, not
read from a wall-clock or an RNG. The result is still deterministic given its inputs (R4.2), with an
**injected tie-break source** used only when two candidates are equally loaded (R4.3).

---

## Components and Interfaces

### C1. `HttpProviderClient` — new file `gateway_v2/dispatch/http_provider.py` (R1, R2, R3)

A real `ProviderClient` that drops in behind the shipped Protocol **without changing the Protocol or
the value types** (R1.1). The file is kept well under 800 lines and every method under 120 lines
(R9.2).

```python
# gateway_v2/dispatch/http_provider.py  (sketch — not final code)
from collections.abc import AsyncIterator
from gateway_v2.dispatch.provider import ProviderClient, UpstreamEvent, UpstreamRequest

class HttpProviderClient:  # structurally satisfies the shipped ProviderClient Protocol
    def __init__(self, *, client_factory, decoder_factory, clock) -> None: ...
    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]: ...  # async generator
    async def abort(self) -> None: ...
```

- **`open(request)`** is a plain method returning an `AsyncIterator[UpstreamEvent]` (an async
  generator), exactly like `StubProviderClient.open`, so callers keep writing
  `async for event in provider.open(request): ...` (R1.1). It:
  1. unpacks `request.body` (the `Transform.to_provider` pack — `BODY_MODEL_KEY` /
     `BODY_DELTAS_KEY`) into the OpenAI-compatible JSON request body — reusing the shipped
     `Transform` codec, not re-shaping (R1.5, R2.3);
  2. opens a **streaming** POST to `request.destination` (R1.2);
  3. feeds each received byte chunk into the shipped `edge/wire/sse.py SSEDecoder.feed()` (reused
     decoder — R2.3), which buffers partial events (split UTF-8 / split JSON surrogate pairs) and
     yields complete `DownstreamFrame`s and sets its `malformed()` latch on an undecodable event
     (fail-closed → `STREAM_MALFORMED_UPSTREAM`);
  4. maps each `DownstreamFrame` back to an `UpstreamEvent` (the inverse of
     `Transform.from_provider`): a content frame → `UpstreamEvent(text_deltas=…, final=…)`; a frame
     with `error_code` → `UpstreamEvent(text_deltas=(), final=True, error_frame=code)` so R11
     scan-before-forward sees it (R1.3, R11);
  5. yields `final=True` on the terminal chunk; the decoder treats `[DONE]` as clean termination
     (R1.4, SSE/SDK `[DONE]` constraint).
- **`abort()`** — see C3.
- **Honest typing.** No `Any` leaks; the body unpack reuses the `Transform` key constants; `mypy
  --strict` clean (R9.4).

**Which `edge/wire` symbol dispatch may import.** `edge` is the TOP layer; `dispatch` may **not**
import `edge`. So the `SSEDecoder` is **injected** into `HttpProviderClient` as `decoder_factory`
(and `edge` wires the real `edge/wire/sse.py SSEDecoder` in through `build_app` the same way it
already injects the `scanner`). `edge/wire/sse.py` imports only `egress` + `domain`, so the decoder
*itself* respects the layering; B4 reuses it by injection, keeping the layer contract kept (R9.1).
This is the same discipline the shipped `StreamPipeline` uses for the detector (inject, never import
upward).

### C2. HTTP/SSE client decision (R2.1, R2.2)

**Decision: `httpx` async (`httpx.AsyncClient`), declared under an optional extra.** Rationale:

- The runbook does **not** pin an HTTP client library (verified — no such line in §0.2 or §10.4),
  so this is an **in-spec design decision**, declared here as the governing document requires for a
  purely-local detail.
- `httpx` is **already a dependency** of the repo via the `conformance` extra
  (`gateway_v2/pyproject.toml`: `conformance = ["openai>=1.0", "httpx>=0.27", …]`), so no new
  third-party surface is introduced to the fleet.
- `httpx.AsyncClient.stream("POST", url)` gives a native **async streaming** response with
  `aiter_bytes()`, and `aclose()` on the stream context stops the read and releases the
  connection — exactly what `abort()` needs (C3).
- Alternatives considered: `aiohttp` (a second client stack the repo does not already carry — adds
  surface for no gain) and the stdlib (`http.client`/`asyncio` raw — no first-class async streaming
  client, would re-implement connection pooling). `httpx` wins on "already present + async-native +
  clean cancel."

**Declared under an extra, lazy-imported (R2.2).** The base package stays framework-free
(`[project].dependencies` is `psycopg` + `redis` only). `httpx` is declared under a dedicated extra
— **reuse the existing `conformance` extra** (which already carries `httpx>=0.27`), or add a sibling
`provider` extra pinned to `httpx>=0.27`; the design decision is to reuse `conformance` to avoid a
duplicate pin, and the tasks phase may split a `provider` extra if the owner prefers an independent
knob. `httpx` is imported **lazily inside `open()`** (not at module load), with an `ImportError`
hint `pip install 'gateway-v2[conformance]'` — byte-for-byte the pattern `edge/serve.py` uses for
uvicorn under `[serve]`. So `import gateway_v2.dispatch.http_provider` on a tree **without** the
extra still succeeds (R2.2); only constructing-and-opening a stream needs the extra.

### C3. `abort()` — cutting the real read within the Cancellation_Bound (R3)

`abort()` must stop the real socket read, not merely flip a latch (the stub flips a latch; the real
client must actually stop reading — R3.1). Design:

- The client holds a reference to the in-flight stream context (the `httpx` streaming response) and
  the read task. `abort()`:
  1. flips a one-way `_aborted` latch (idempotent — R3.4) and records it, matching
     `StubProviderClient`'s one-way-latch discipline;
  2. **closes the stream** (`await response.aclose()`) and/or cancels the read task, which stops
     `aiter_bytes()` from yielding further bytes — the read ceases (R3.1);
  3. **releases the underlying connection resources** for that request back to the `httpx` pool
     (R3.3).
- **Within the Cancellation_Bound (R3.2).** `edge/cancel.py CancellationController._kill_within_bound`
  already awaits `provider.abort()` under `asyncio.wait_for(timeout=contract.cancellation_bound_s())`
  and force-releases on timeout. `HttpProviderClient.abort()` is written so the stream close is
  prompt and bounded; it ties into the shipped **GW12 `InFlightKill.CUT_NEXT_CHUNK`** contract
  through that controller (the `KillLatch` cuts the downstream pipeline at the next chunk boundary;
  `abort()` cuts the upstream read). The derived bound is `contract.cancellation_bound_s()` (=
  `p99 × 3`), **read from the contract**, never a literal (R9.3).
- **Idempotent (R3.4).** A second `abort()` observes the already-set latch and is a no-op (no raise,
  no double-close) — the same one-way semantics as the shipped stub and `KillLatch`.

### C4. `GuardRouter` — new file `gateway_v2/dispatch/guard_routing.py` (R4)

A NEW sibling selector. `DispatchRouter` is **unchanged** (R4.5).

```python
# gateway_v2/dispatch/guard_routing.py  (sketch)
from collections.abc import Callable
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class GuardLoad:
    """One guard's injected load reading."""
    destination: str
    owner_queue_wait_s: float   # the measured owner-queue wait for this guard
    queue_depth: int

@dataclass(frozen=True, slots=True)
class Load_Snapshot:
    """A point-in-time, injected reading over all candidate guards."""
    guards: tuple[GuardLoad, ...]

class GuardRouter:
    __slots__ = ("_owner_queue_bound_s", "_tie_break")
    def __init__(self, *, owner_queue_bound_s: float, tie_break: Callable[[tuple[str, ...]], str]) -> None: ...
    def select(self, snapshot: Load_Snapshot) -> str: ...
```

- **`select(snapshot)` (R4.1)** returns the destination of the `Guard_Load` with the **minimum
  queue-wait** across the injected candidates. The `RV_OWNER_QUEUE_MS` owner-queue bound
  (`owner_queue_bound_s`) gates eligibility: a guard whose `owner_queue_wait_s` exceeds the bound is
  deprioritized relative to one within the bound (reproducing C43's "route to minimize queue-wait";
  runbook queue-wait ratio 0.16–0.20 on vs 0.48–0.50 off). This is where the measured C43 behaviour
  lives (R4.4).
- **Deterministic given inputs (R4.2).** `select` is a pure function of `snapshot` +
  `owner_queue_bound_s` + the injected `tie_break`. No wall-clock, no `random` (R4.3).
- **Injected tie-break (R4.3).** When two candidates are equally loaded, the injected
  `tie_break(candidate_destinations) -> destination` chooses deterministically (a test injects a
  fixed-order tie-break; production may inject a stable hash). This is the ONLY source of
  "choice among equals," and it is injected — so determinism-given-inputs holds.
- **Where the `Load_Snapshot` comes from.** It is **injected** (R4-glossary: "injected state, not
  wall-clock or randomness"). The real per-guard queue-depth / owner-queue-wait signal — read from
  the live guard fleet — is the **deferred live half** (the real GPU guard socket, GW25). Locally
  the snapshot is a fabricated reading the test or the `Fake_Guard` harness supplies.
- **`DispatchRouter` unchanged (R4.5).** `GuardRouter` is a distinct class in a distinct file; the
  shipped `DispatchRouter.select(plan)` purity property still holds untouched.

### C5. Generation-vs-guard split in the egress wiring (R5)

- **Chat / embeddings → `Generation_Provider`.** The existing `edge` chat/embeddings path dispatches
  generation egress to the injected `ProviderClient` — in production the `HttpProviderClient`; the
  `UpstreamRequest.destination` is the plan-derived generation endpoint from the shipped
  `DispatchRouter` (R5.1).
- **Guard tier → `GuardRouter`-selected `Guard_Pool` member over split-topology TCP.** When
  guard-tier selection is required, `GuardRouter.select(snapshot)` names the guard destination,
  reached over the split-topology TCP path (§0.2 C10/C38). The guard is **self-hosted**, never a
  platform-owned external AI (R5.2, R5.3 — runbook *Platform AI* row).
- **Guard egress SEAM, not the detector call.** B4 provides the *routing selection* + the *egress
  seam*; the actual detector model call that produces findings is **GW07/GW08** (named, not built).
  The `GuardRouter` selection is exercisable against a `Fake_Guard` with no real detector present
  (R5.4).

### C6. `build_app(provider=...)` injection seam (R7)

- **Drop-in swap (R7.1).** `gateway_v2/edge/app.py build_app(..., provider: ProviderClient, …)`
  already takes the provider as a keyword parameter; the real `HttpProviderClient` is injected
  through this **same seam with no signature change**. `edge/serve.py build_boot_dependencies`
  wires it the same way it wires every dependency.
- **Stub stays the default (R7.2).** `edge/serve.py` currently wires
  `provider = StubProviderClient(...)` (an empty, socket-free stub) as the honest-boot default. B4
  does not change that default; a deployment that wants real generation injects
  `HttpProviderClient` explicitly, exactly as B3 documents the "real provider HTTP/SSE upstream
  socket → B4 / GW17-18" deferred seam.
- **Real completions vs fake (R7.3).** When the real client is injected AND a reachable
  `Generation_Provider` exists, the data plane produces real completions; while that is not met, the
  local verification uses the `Fake_Upstream` instead (the reachable-real-provider half is the
  deferred live gate).

### C7. Local fake upstreams (R6)

- **`Fake_Upstream`** — a local server (an `asyncio`/`aiohttp` stub, or `httpx`'s in-process
  `ASGITransport`, or a real `localhost` socket) that speaks the `Generation_Provider`'s **OpenAI
  SSE shape**: `data: {chat.completion.chunk json}\n\n` content frames and the terminal
  `data: [DONE]\n\n` marker, plus fault controls (mid-stream `{"error": …}` frame, a slow/stalling
  stream, an abrupt disconnect) mirroring the runbook's staging provider recorder (§10.9 item 14).
  The `Fake_Upstream` is driven as the real client's upstream so the `HttpProviderClient` exercises
  the real HTTP/SSE path over a real local socket where honest (R6.1).
- **`Fake_Guard`** — a local guard-tier counterpart used to exercise the `GuardRouter` selection +
  the guard egress seam without the GW07/GW08 detector (R6.2).
- The byte-identity `Transform` round-trip (the GW12 Property 8 half) is reused as the correctness
  anchor on provider content (R6.3); `abort()` is verified to stop an in-flight read against the
  `Fake_Upstream` (R6.4); genuinely-universal properties use the house seeded-`random.Random` idiom
  (R6.5, see §8).

---

## Data Models

B4 **reuses** the shipped frozen value types and adds a small, frozen, immutable set for the guard
router. Nothing in `dispatch/provider.py` or `dispatch/transform.py` is modified (R1.1).

| Type | Owner | B4 change |
|---|---|---|
| `UpstreamRequest(destination: str, body: Mapping[str, object])` | `dispatch/provider.py` | **unchanged** (consumed) |
| `UpstreamEvent(text_deltas: tuple[tuple[str,str],...], final: bool, error_frame: str \| None)` | `dispatch/provider.py` | **unchanged** (produced by the real client) |
| `ProviderClient` Protocol (`open`, `abort`) | `dispatch/provider.py` | **unchanged** (implemented) |
| `GatewayRequest`, `Transform`, `BODY_MODEL_KEY`, `BODY_DELTAS_KEY` | `dispatch/transform.py` | **unchanged** (reused) |
| `DownstreamFrame`, `SSEDecoder` | `egress/stream.py`, `edge/wire/sse.py` | **unchanged** (reused by injection) |
| `GuardLoad(destination, owner_queue_wait_s, queue_depth)` | `dispatch/guard_routing.py` (NEW) | frozen, slotted |
| `Load_Snapshot(guards: tuple[GuardLoad, ...])` | `dispatch/guard_routing.py` (NEW) | frozen, slotted |

**Capacity bounds live in `runtime/resources.py` (R9.3).** Two new accessors are added to
`ResourceContract`, derived from existing capacity authority — **no literal in `dispatch`**:

- `owner_queue_bound_s()` → the `RV_OWNER_QUEUE_MS` owner-queue bound (RC-measured default ≈ 20 ms,
  runbook C43). It is read from the environment (`RV_OWNER_QUEUE_MS`) with the measured default, in
  the same `_DEFAULT_*` + `load_contract` pattern the module already uses — the default constant
  lives in `resources.py`, the only module `check_capacity_literals` exempts.
- `guard_wait_budget_s()` → the GW19/R2-07 guard-wait budget `max(100, cap + 40)` ms where `cap` is
  the CoDel hard cap (60 ms). Both operands are capacity-derived; the derivation lives in
  `resources.py` and `GuardRouter` reads it, never inlining a literal.

`GuardRouter.__init__` takes `owner_queue_bound_s: float` (passed in from
`contract.owner_queue_bound_s()` at the `edge` wiring site), so `dispatch` holds no capacity literal.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a
system — essentially, a formal statement about what the system should do. Properties serve as the
bridge between human-readable specifications and machine-verifiable correctness guarantees.*

**PBT applicability — assessed honestly.** The genuinely-universal, pure logic in B4 is (a) the
`Transform` byte-identity round-trip on provider content, (b) the terminal-flag discipline over
streams, and (c) the `GuardRouter` selection and determinism over injected load snapshots. These are
pure functions of their inputs and have a large input space → PBT applies. The **HTTP client and
socket paths** (connect, stream, abort-within-bound, resource release, build_app wiring) are
**INTEGRATION**, not property — they test wiring to an external endpoint and timing against a derived
bound, where 100 random iterations find nothing a handful of representative examples do not; they are
covered by integration tests against the `Fake_Upstream`/`Fake_Guard` in §8, not by properties.

The following properties survive the prework property-reflection (consolidating the duplicate
round-trip criteria 1.3/2.4/6.3 into one, folding purity 4.3 into determinism 4.2, and folding the
`RV_OWNER_QUEUE_MS` bound 4.4 into the selection property).

### Property 1: Transform byte-identity round-trip on provider content

*For any* well-formed `GatewayRequest` payload `x`, shaping it to the provider and reading it back
preserves the text content byte-for-byte: `from_provider(echo(to_provider(x)))` reproduces `x`'s
`text_deltas` exactly, and the equivalent round-trip through the reused SSE codec
(`SSEDecoder.feed(SSEEncoder.content(from_provider(ev)))`) yields a `DownstreamFrame` whose
`text_deltas` are byte-identical to the content the real client produced.

**Validates: Requirements 1.3, 2.4, 6.3**

### Property 2: Terminal final-flag is set exactly on the last event

*For any* generated provider stream of length `n ≥ 1`, iterating the `HttpProviderClient` against the
`Fake_Upstream` yields events where `final` is `True` on and only on the last event and `False` on
every earlier event, and a `[DONE]` marker produces no extra content event.

**Validates: Requirements 1.4**

### Property 3: GuardRouter selects a minimum-queue-wait guard (bound-aware)

*For any* `Load_Snapshot` over a non-empty candidate `Guard_Pool`, the destination returned by
`GuardRouter.select` is that of a guard whose effective queue-wait equals the minimum across all
candidates, with a guard inside the `owner_queue_bound_s()` bound preferred over one that exceeds it.

**Validates: Requirements 4.1, 4.4**

### Property 4: GuardRouter is deterministic given its inputs

*For any* `Load_Snapshot` and fixed injected tie-break, two successive calls to
`GuardRouter.select` return the identical destination; the selection reads no wall-clock and no
randomness, so repeated evaluation on equal inputs is always equal (including the equally-loaded case
resolved only by the injected tie-break).

**Validates: Requirements 4.2, 4.3**

---

## Error Handling

| Condition | Handling | Requirement |
|---|---|---|
| Provider mid-stream error frame (`{"error": …}`) | Decoder maps it to a terminal `DownstreamFrame.error_code`; the client surfaces it as `UpstreamEvent.error_frame` so `edge` scans it **before any byte is forwarded** (R11 scan-before-forward); nothing is forwarded on a BLOCK | R1.3, R11 |
| Undecodable / malformed upstream SSE | Reused `SSEDecoder.malformed()` latch → fail-closed `STREAM_MALFORMED_UPSTREAM`; the stream is never reported successful | R2.3 |
| Client disconnect / in-flight cancel | `CancellationController` awaits `abort()` under `wait_for(contract.cancellation_bound_s())`; the real read stops, the connection is released; on timeout the controller force-releases (fail-closed) and the `KillLatch` has already cut the downstream pipeline | R3.1–R3.3 |
| `abort()` called twice | One-way latch → the second call is a no-op (no raise, no double-close) | R3.4 |
| `httpx` extra not installed | Lazy import inside `open()` raises a clear `ImportError` naming `pip install 'gateway-v2[conformance]'`; module import itself never fails | R2.2 |
| Empty candidate guard pool | `GuardRouter.select` fails closed with a `ValueError` (no silent default guard) rather than dialing an unverified endpoint | R4.1, R5.3 |
| Unreachable real provider (deferred) | Not scaffolded locally; the local half uses the `Fake_Upstream`. Real-provider connection errors are a live-gate concern | R7.3, R8.1 |

B4 renders **no HTTP responses** — a provider/guard failure surfaces as an `UpstreamEvent.error_frame`
/ a terminal `DownstreamFrame.error_code` that `edge` (the only layer that renders) turns into the
declared SSE `Error_Frame`. This keeps the C11/C12 "only `edge/`+`resolve/` render terminal outcomes"
rule intact and `check_http_outside_edge_resolve` green.

---

## Testing Strategy

The strategy maps directly onto the runbook **§10.9** card shape so `tasks.md` can emit *live
acceptance tests → exit → evidence*.

### Dual approach

- **Property tests** (house idiom: seeded `random.Random`, **no hypothesis**, ≥ 100 iterations each)
  — the four properties above. Each test is tagged
  `# Feature: provider-egress-gw-b4, Property N: <property text>` and references its design property.
  - Property 1 — generate well-formed `GatewayRequest` payloads (random channels + unicode text,
    including astral code points that force the decoder's split-surrogate path), assert byte identity
    through `to_provider → (fake stream / echo) → from_provider` and through the reused SSE codec.
  - Property 2 — generate streams of random length against the `Fake_Upstream`, assert the final-flag
    discipline.
  - Property 3 / 4 — generate random `Load_Snapshot`s over random pools, assert min-queue-wait
    selection (bound-aware) and determinism-given-inputs with a fixed injected tie-break.
- **Integration tests** (1–3 representative examples each, against the local fakes over a real
  localhost socket where honest):
  - `open()` connects to `UpstreamRequest.destination` and streams OpenAI-SSE chunks (R1.2, 6.1).
  - `abort()` stops an in-flight read within `contract.cancellation_bound_s()` (injected clock) and
    releases the connection; a second `abort()` is a no-op (R3, 6.4).
  - `GuardRouter`-selected destination is dispatched to a `Fake_Guard` with no real detector (R5.4,
    6.2).
  - `build_app(provider=HttpProviderClient(...))` assembles and streams; the `serve` default stays
    `StubProviderClient` (R7.1, R7.2).
- **Smoke / structural checks** — `isinstance(client, ProviderClient)` and value-type identity (R1.1);
  base-tree import without the extra (R2.2); the documented accounting-exclusion boundary (R1.6, R8.4).

### Gate (the exit evidence)

`tasks.md` must show all green, as the §10.9 *exit* and *evidence*:

- `pytest` — the property + integration + structural suite above.
- `mypy --strict` — clean over the new `dispatch` files + the `resources.py` accessors.
- `ruff` — clean.
- `lint-imports` (import-linter) — both contracts kept; no new upward dispatch edge, no
  egress→detect edge (R9.1, R9.4).
- The **six AST gates**: `check_capacity_literals` (the `RV_OWNER_QUEUE_MS` default +
  guard-wait budget live in `resources.py`, R9.3), `check_no_module_mutable`,
  `check_frozen_dataclasses` (the new `GuardLoad`/`Load_Snapshot` are frozen+slotted),
  `check_http_outside_edge_resolve` (verified green with an httpx client in `dispatch` — the gate
  forbids `HTTPException`/`JSONResponse`/`status_code=4xx`, none of which an outbound client
  constructs), `check_sizes` (files ≤ 800 / functions ≤ 120, R9.2), `check_tenant_scale`.

### Deferred live gates (named seams, not scaffolded) — R8

| Deferred gate | Owning card | Not done in B4 |
|---|---|---|
| Real BYOK `Generation_Provider` HTTP/SSE socket | **B4 live gate / GW17-18 live half** | No real external provider connection; local half uses `Fake_Upstream` (R8.1) |
| Real GPU `Guard_Pool` fleet socket | **GW25** (GPU capacity ops; runbook R2-15) | No real guard fleet connection; local half uses `Fake_Guard` (R8.2) |
| Fleet-scale C43 capacity measurement (760 RPS, 190/guard) | **GW20 / GW20b** (runbook R2-16, R2-21) | No fleet capacity run in the local half (R8.3) |

**Accounting boundary (R8.4, R1.6).** The `Generation_Provider` is BYOK and is **excluded from
firewall-infrastructure cost/latency accounting** (runbook *Customer model* row; §1.1 "Real BYOK
provider capacity is reported separately … is not AI Mesh gateway capacity"). This is a documented
**accounting boundary**, not an implemented metric: no firewall-cost/latency metric observes
`Generation_Provider` time, and the design asserts (as a smoke/doc check) that none is added.

---

## Design Decisions and Rationale (summary)

1. **`httpx` async under an optional extra, lazy-imported** — the runbook does not pin a client, so
   this is an in-spec decision; `httpx` is already present via `conformance`, is async-native, and
   `aclose()` gives clean cancellation. Lazy import keeps the base package framework-free (parity
   with uvicorn under `[serve]`). (R2.1, R2.2)
2. **Reuse the shipped `Transform` + `SSEDecoder` by injection** — no duplicated body shaping or SSE
   parsing; `edge` injects the decoder (dispatch may not import `edge`), preserving the layer
   contract. (R1.5, R2.3, R9.1)
3. **`GuardRouter` is a new sibling, not a `DispatchRouter` change** — C43 is load-aware so it needs
   injected load state; `DispatchRouter` stays pure plan-identity→destination. The purity tension is
   resolved by injecting the `Load_Snapshot` and the tie-break (no clock/rng). (R4)
4. **Capacity bounds in `resources.py` only** — `RV_OWNER_QUEUE_MS` default + the GW19 guard-wait
   budget are added as derived accessors; `dispatch` reads them, never inlines them. (R9.3)
5. **Live sockets stay deferred seams** — the real BYOK provider and real GPU guard fleet are named
   with owning cards and are not scaffolded; the local half proves the client code, the injection
   seam, and the routing selection against fakes. (R8)
