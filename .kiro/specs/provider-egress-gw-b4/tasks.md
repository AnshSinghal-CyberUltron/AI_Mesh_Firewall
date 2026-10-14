# Implementation Plan: Provider egress client + dispatch routing (B4 / GW17-18)

## Overview

Convert the B4 design into a series of code-generation steps that land the **real streaming
HTTP/SSE `ProviderClient`** (generation egress) and the **C43 load-aware `GuardRouter`** (guard
routing selection) **bottom-up along the import-linter layer contract**
(`runtime → dispatch`, both below `edge`), the same shape as the shipped GW16/B3 cards (source
before its tests; property/unit/integration tests; a checkpoint; a final gate). B4 lives entirely
in the `dispatch` layer plus two derived accessors in `runtime/resources.py`; it drops the real
client in **behind** the shipped `@runtime_checkable` `ProviderClient` Protocol in
`gateway_v2/dispatch/provider.py` (today only `StubProviderClient` exists) and adds `GuardRouter` as
a NEW sibling of the pure `DispatchRouter` in `gateway_v2/dispatch/routing.py` (left unchanged).

B4 does **not** change the `ProviderClient` Protocol or the `UpstreamRequest` / `UpstreamEvent`
value types; it REUSES the shipped `Transform` codec (body shaping) and the shipped
`edge/wire/sse.py SSEDecoder` (SSE parsing, injected because `dispatch` may not import `edge`). The
real BYOK generation socket and the real GPU guard fleet socket stay **deferred live gates** named
as seams, not scaffolded; the local half proves the client code, the injection seam, and the
routing selection against a `Fake_Upstream` / `Fake_Guard`. Every value type and seam lands a
caller in a later step; the final steps wire `build_app(provider=...)` and run the full gate, so
there is no orphaned code.

Implementation language: **Python** (the design is grounded in concrete `gateway_v2/gateway_v2/*.py`
modules and the shipped house idiom). All code targets `mypy --strict` + `ruff` clean.

### Conventions enforced in EVERY coding task

- **Property tests**: a seeded `random.Random` driven for **≥10,000 iterations**, **NO hypothesis**;
  async lifecycle paths via `asyncio.run`. Each property test is tagged
  `# Feature: provider-egress-gw-b4, Property N: <text>` and `# Validates: Requirements X.Y`, with
  exactly one property ↔ one property-based test. Only four genuinely universal properties exist
  (Property 1 Transform byte-identity round-trip, Property 2 terminal final-flag, Property 3
  GuardRouter min-queue-wait selection, Property 4 GuardRouter determinism) — the design's PBT
  prework classifies the HTTP client / socket paths (connect, stream, abort-within-bound, resource
  release, `build_app` wiring) as INTEGRATION, not property, so no other property sub-tasks are
  added.
- **Value types** are frozen slotted dataclasses or `StrEnum`; the new `GuardLoad` / `Load_Snapshot`
  are `@dataclass(frozen=True, slots=True)`. No module-level mutable (`check_no_module_mutable`,
  `check_frozen_dataclasses`).
- **No capacity literal outside `runtime/resources.py`** — the `RV_OWNER_QUEUE_MS` owner-queue bound
  default and the GW19 guard-wait budget (`max(100, cap + 40)` ms, `cap` = CoDel hard cap 60 ms) are
  derived accessors in `resources.py`, the only module `check_capacity_literals` exempts; `dispatch`
  reads them, never inlines them. `GuardRouter.__init__` takes `owner_queue_bound_s: float` passed in
  at the `edge` wiring site; the `cancellation_bound_s()` used by `abort()` is read from the contract.
- **`dispatch` imports only `domain` and layers below it, never `plan`** (import-linter `layers`
  contract). `dispatch` may NOT import `edge`, so the `SSEDecoder` is INJECTED into
  `HttpProviderClient` as `decoder_factory` and `edge` wires the real `edge/wire/sse.py SSEDecoder`
  through `build_app` — the same inject-never-import-upward discipline the shipped `StreamPipeline`
  uses for the detector. B4 adds no new upward dispatch edge and **no egress→detect edge** (the guard
  *routing* is in `dispatch`; the detector *call* is GW07/GW08). `lint-imports` stays **2 contracts
  kept**.
- **`check_http_outside_edge_resolve` stays green with an `httpx` client in `dispatch`** — the gate
  forbids `HTTPException` / `JSONResponse` / `status_code=4xx` (HTTP *response* construction), none of
  which an outbound streaming client constructs. B4 renders no HTTP responses; a provider/guard
  failure surfaces as an `UpstreamEvent.error_frame` that `edge` (the only render layer) turns into
  the declared SSE `Error_Frame`.
- **`httpx` is lazy-imported** inside `open()` (not at module load), declared under the existing
  `conformance` extra (which already carries `httpx>=0.27`), with an `ImportError` hint
  `pip install 'gateway-v2[conformance]'` — byte-for-byte the pattern `edge/serve.py` uses for
  uvicorn under `[serve]`. So `import gateway_v2.dispatch.http_provider` on a tree WITHOUT the extra
  still succeeds; only constructing-and-opening a stream needs it.
- **`check_sizes`**: every function ≤ 120 lines and every module ≤ 800 lines; `dispatch/provider.py`
  and `dispatch/routing.py` stay untouched. **Fail-closed everywhere** (empty guard pool, malformed
  upstream SSE, abort-within-bound timeout). `unscanned_channels()` stays `()`.
- Each task references its requirement sub-clauses and the design section/property it satisfies
  (traceability), matching the GW16/B3 `tasks.md` style; every code task is followed by its test task
  and a note to run the gate.

## Tasks

- [x] 1. Derived capacity accessors — `runtime/resources.py` (MODIFY, bottom of the build order)
  - [x] 1.1 Add `owner_queue_bound_s()` and `guard_wait_budget_s()` to `ResourceContract`
    - Modify `gateway_v2/gateway_v2/runtime/resources.py`: add two derived capacity accessors in the
      existing `_DEFAULT_*` + `load_contract` env→value idiom (the only module
      `check_capacity_literals` exempts) — `owner_queue_bound_s()` returns the `RV_OWNER_QUEUE_MS`
      owner-queue bound (RC-measured default ≈ 20 ms, read from env with the measured default), and
      `guard_wait_budget_s()` returns the GW19/R2-07 guard-wait budget `max(100, cap + 40)` ms where
      `cap` is the CoDel hard cap (60 ms) — both operands capacity-derived, the derivation living
      here so `dispatch` holds no literal
    - Place the `RV_OWNER_QUEUE_MS` default constant alongside the module's existing `_DEFAULT_*`
      constants; accessors return seconds (float), consistent with `cancellation_bound_s()`; no
      module-level mutable, no new third-party surface
    - _Design: Components §C (Data Models — capacity bounds), Architecture (runtime accessors)_
    - _Requirements: 4.4, 9.3_

  - [x]* 1.2 Write unit/property test for the two accessors
    - `tests/runtime/test_b4_resources.py`: `owner_queue_bound_s()` all-unset env → the measured
      ≈ 20 ms default (in seconds); a valid `RV_OWNER_QUEUE_MS` override is read through; a
      present-but-unparseable value falls back to the default; `guard_wait_budget_s()` equals
      `max(0.100, cap + 0.040)` with `cap` = 0.060 s (= 0.100 s) and tracks `cap` when a larger cap is
      configured — a seeded `random.Random` ≥10,000-iteration check over random valid
      `RV_OWNER_QUEUE_MS` strings that the parsed value or its default is always returned and never
      raises (tagged `# Validates: Requirements 4.4, 9.3`)
    - _Design: Testing Strategy (unit/EXAMPLE — derived accessors), Requirements Coverage (4.4, 9.3)_
    - _Requirements: 4.4, 9.3_
  - Run the gate (`.venv/bin/python -m pytest`, `mypy --strict`, `ruff`, `lint-imports`, the 6 AST
    gates) over `runtime/resources.py` before building on it.

- [x] 2. C43 load-aware guard routing — `dispatch/guard_routing.py` (NEW)
  - [x] 2.1 Implement `GuardLoad` + `Load_Snapshot` value types and `GuardRouter.select`
    - Create `gateway_v2/gateway_v2/dispatch/guard_routing.py`: frozen+slotted
      `GuardLoad(destination: str, owner_queue_wait_s: float, queue_depth: int)` and
      `Load_Snapshot(guards: tuple[GuardLoad, ...])` (reused value types; nothing in
      `dispatch/provider.py` / `dispatch/routing.py` is modified — R4.5)
    - `GuardRouter(__slots__)` takes `owner_queue_bound_s: float` (passed in from
      `contract.owner_queue_bound_s()` at the `edge` wiring site — NO literal in `dispatch`) and an
      injected `tie_break: Callable[[tuple[str, ...]], str]`; `select(snapshot) -> str` returns the
      destination of the `GuardLoad` with the MINIMUM effective queue-wait across the injected
      candidates, preferring a guard inside the `owner_queue_bound_s` bound over one that exceeds it
      (reproducing C43 min-queue-wait; runbook queue-wait ratio 0.16–0.20 on vs 0.48–0.50 off — R4.1,
      R4.4)
    - Deterministic given inputs: `select` reads NO wall-clock and NO `random`; the injected
      `tie_break` is the ONLY source of choice-among-equals, used only when two candidates are equally
      loaded (R4.2, R4.3); an empty candidate pool fails closed with a `ValueError` (no silent default
      guard — R4.1, R5.3); honest typing, no `Any` leak; add a comment naming the real per-guard
      load signal (live GPU guard fleet) as the deferred seam (GW25)
    - _Design: Components §C4 (`GuardRouter`), Data Models (`GuardLoad` / `Load_Snapshot`), Guard routing (GW18 / C43)_
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.3_

  - [x]* 2.2 Write property test for min-queue-wait selection (PBT)
    - `tests/dispatch/test_b4_guard_routing.py` — **Property 3: GuardRouter selects a minimum-queue-wait
      guard (bound-aware)**
    - **Validates: Requirements 4.1, 4.4**; seeded `random.Random` ≥10,000 iters, no hypothesis: over
      random non-empty `Load_Snapshot`s the returned destination is that of a guard whose effective
      queue-wait equals the minimum across all candidates, with a guard inside `owner_queue_bound_s()`
      preferred over one that exceeds it; an empty pool raises `ValueError`
    - _Design: Correctness Properties → Property 3_

  - [x]* 2.3 Write property test for determinism given inputs (PBT)
    - `tests/dispatch/test_b4_guard_routing.py` — **Property 4: GuardRouter is deterministic given its
      inputs**
    - **Validates: Requirements 4.2, 4.3**; ≥10,000 iters, no hypothesis: for any `Load_Snapshot` and
      fixed injected tie-break, two successive `select` calls return the identical destination; the
      selection reads no wall-clock and no randomness, and the equally-loaded case is resolved only by
      the injected tie-break
    - _Design: Correctness Properties → Property 4_
  - Run the gate over `dispatch/guard_routing.py` (incl. `check_frozen_dataclasses`,
    `check_capacity_literals`, `check_sizes`, `lint-imports` 2 kept) before building on it.

- [x] 3. Real generation-egress client — `dispatch/http_provider.py` (NEW)
  - [x] 3.1 Implement `HttpProviderClient.open` — streaming HTTP/SSE through the injected decoder
    - Create `gateway_v2/gateway_v2/dispatch/http_provider.py`: a class that structurally satisfies
      the shipped `ProviderClient` Protocol WITHOUT changing the Protocol or the `UpstreamRequest` /
      `UpstreamEvent` value types (R1.1); `__init__(*, client_factory, decoder_factory, clock)` takes
      the httpx client factory, the shipped `edge/wire/sse.py SSEDecoder` factory (INJECTED — dispatch
      may not import `edge`), and an injected clock
    - `open(request)` is a plain method returning `AsyncIterator[UpstreamEvent]` (an async generator,
      exactly like `StubProviderClient.open`): unpack `request.body` (the `Transform.to_provider` pack
      — `BODY_MODEL_KEY` / `BODY_DELTAS_KEY`) into the OpenAI-compatible JSON body REUSING the shipped
      `Transform` codec (R1.5, R2.3); lazy-import `httpx` with the `pip install 'gateway-v2[conformance]'`
      hint (R2.2); open a STREAMING POST to `request.destination` (R1.2); feed each received byte chunk
      into the injected `SSEDecoder.feed()` (buffers split UTF-8 / split JSON surrogate pairs); map each
      decoded `DownstreamFrame` back to an `UpstreamEvent` (inverse of `Transform.from_provider`) whose
      `text_deltas` are byte-compatible with the `egress.stream` shape (`tuple[tuple[str, str], ...]`)
      (R1.3); a frame with `error_code` → `UpstreamEvent(text_deltas=(), final=True, error_frame=code)`
      so R11 scan-before-forward sees it; the decoder's `malformed()` latch → fail-closed
      `STREAM_MALFORMED_UPSTREAM` (R2.3); yield `final=True` on the terminal chunk, `[DONE]` as clean
      termination (R1.4)
    - Honest typing (no `Any` leak, body unpack reuses the `Transform` key constants); add a comment
      naming the real BYOK provider socket as the deferred live gate and the firewall-cost exclusion of
      the `Generation_Provider` as an accounting boundary (R1.6, R8.1, R8.4)
    - _Design: Components §C1 (`HttpProviderClient`), §C2 (HTTP/SSE client decision), Flow of a generation request_
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3_

  - [x] 3.2 Implement `HttpProviderClient.abort` — cut the in-flight read within the bound, idempotent
    - In `gateway_v2/gateway_v2/dispatch/http_provider.py`: `async def abort()` holds a reference to the
      in-flight httpx streaming response + read task; it (1) flips a one-way `_aborted` latch
      (idempotent — a second `abort()` observes the set latch and is a no-op, no raise, no double-close,
      the same one-way semantics as the shipped stub + `KillLatch` — R3.4); (2) closes the stream
      (`await response.aclose()`) / cancels the read task so `aiter_bytes()` stops yielding — the real
      read ceases (R3.1); (3) releases the underlying connection resources back to the httpx pool (R3.3)
    - The close is prompt and bounded so `edge/cancel.py CancellationController._kill_within_bound` —
      which awaits `provider.abort()` under `wait_for(contract.cancellation_bound_s())` and
      force-releases on timeout — can cut within the Cancellation_Bound (= `p99 × 3`), read from the
      contract, never a literal (R3.2, R9.3); ties into the shipped GW12 `InFlightKill.CUT_NEXT_CHUNK`
      through that controller
    - _Design: Components §C3 (`abort()`), Error Handling (client disconnect / abort twice)_
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x]* 3.3 Write Transform byte-identity round-trip property test (PBT)
    - `tests/dispatch/test_b4_transform_roundtrip.py` — **Property 1: Transform byte-identity round-trip
      on provider content**
    - **Validates: Requirements 1.3, 2.4, 6.3**; seeded `random.Random` ≥10,000 iters, no hypothesis:
      generate well-formed `GatewayRequest` payloads (random channels + unicode text, including astral
      code points that force the decoder's split-surrogate path) and assert byte identity through
      `to_provider → echo → from_provider` AND through the reused SSE codec
      (`SSEDecoder.feed(SSEEncoder.content(from_provider(ev)))` yields a `DownstreamFrame` whose
      `text_deltas` are byte-identical to the content the real client produced) — reuses the GW12
      Property-8 transform half (no socket needed)
    - _Design: Correctness Properties → Property 1_
  - Run the gate over `dispatch/http_provider.py` (incl. base-tree import WITHOUT the `conformance`
    extra succeeding, `check_http_outside_edge_resolve` green, `check_sizes`, `lint-imports` 2 kept)
    before building on it.

- [x] 4. Checkpoint — resources accessors + guard routing + http client green
  - Ensure all tests pass, ask the user if questions arise. Run `.venv/bin/python -m pytest` (the B4
    subset so far), `mypy --strict`, `ruff check gateway_v2 state_control lint tests`, `lint-imports`
    (2 contracts kept, no new edge, no egress→detect), and the 6 AST gates (`check_capacity_literals`,
    `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`,
    `check_sizes`, `check_tenant_scale`) over `runtime/resources.py`, `dispatch/guard_routing.py`, and
    `dispatch/http_provider.py`; confirm `import gateway_v2.dispatch.http_provider` succeeds without the
    extra, `unscanned_channels()` is still `()`, and each module stays ≤ 800 lines / each function ≤ 120.

- [x] 5. Local fake upstreams + integration — `tests/dispatch/` (NEW)
  - [x] 5.1 Build the `Fake_Upstream` + `Fake_Guard` harness
    - Create a `Fake_Upstream` test helper (a local `localhost` socket server, or httpx
      `ASGITransport` where honest) that speaks the `Generation_Provider`'s OpenAI SSE shape —
      `data: {chat.completion.chunk json}\n\n` content frames + the terminal `data: [DONE]\n\n`
      marker — plus fault controls: a mid-stream `{"error": …}` frame, a slow/stalling stream, and an
      abrupt disconnect (mirroring the runbook staging-provider recorder, §10.9 item 14) (R6.1); and a
      `Fake_Guard` counterpart used to exercise the `GuardRouter` selection + the guard egress seam
      with NO real detector present (R6.2)
    - The harness is test-only (no scaffolded cloud behaviour); drive `HttpProviderClient` against the
      `Fake_Upstream` over a real local socket where honest
    - _Design: Components §C7 (local fake upstreams), Testing Strategy (dual approach)_
    - _Requirements: 6.1, 6.2_

  - [x]* 5.2 Write terminal final-flag property test against the Fake_Upstream (PBT)
    - `tests/dispatch/test_b4_http_integration.py` — **Property 2: Terminal final-flag is set exactly on
      the last event**
    - **Validates: Requirements 1.4**; seeded `random.Random` ≥10,000 iters, no hypothesis (async via
      `asyncio.run`): generate provider streams of random length `n ≥ 1` against the `Fake_Upstream`,
      assert `final` is `True` on and only on the last event and `False` on every earlier event, and a
      `[DONE]` marker produces no extra content event
    - _Design: Correctness Properties → Property 2_

  - [x]* 5.3 Write http-client integration tests against the Fake_Upstream (open/stream/final, R11, abort, malformed)
    - `tests/dispatch/test_b4_http_integration.py` (1–3 representative examples each, over a real local
      socket where honest): `open()` connects to `UpstreamRequest.destination` and streams OpenAI-SSE
      chunks, last event `final=True` (R1.2, R6.1); a mid-stream `{"error": …}` frame → a terminal
      `UpstreamEvent.error_frame` scanned before any byte is forwarded, nothing forwarded on BLOCK
      (R1.3, R11); malformed/undecodable upstream SSE → the reused `SSEDecoder.malformed()` latch →
      fail-closed `STREAM_MALFORMED_UPSTREAM`, never reported successful (R2.3); `abort()` with an
      injected clock stops an in-flight read within `contract.cancellation_bound_s()` and releases the
      connection, and a second `abort()` is a no-op (R3, R6.4); `isinstance(client, ProviderClient)`
      and value-type identity (R1.1)
    - _Design: Testing Strategy (integration tests), Error Handling table_
    - _Requirements: 1.1, 1.2, 1.3, 2.3, 3.1, 3.2, 3.3, 3.4, 6.1, 6.4_

  - [x]* 5.4 Write Fake_Guard routing integration test
    - `tests/dispatch/test_b4_guard_integration.py`: a `GuardRouter`-selected destination is dispatched
      to a `Fake_Guard` over the local path with NO real GW07/GW08 detector present, confirming the
      routing selection + guard egress seam is exercisable without the detector model (R5.4, R6.2)
    - _Design: Components §C5 (guard egress seam), Testing Strategy (integration)_
    - _Requirements: 5.4, 6.2_
  - Run the gate over the new `tests/dispatch/` modules before building on it.

- [x] 6. Generation-vs-guard split + `build_app(provider=...)` injection wiring — `edge` seam
  - [x] 6.1 Wire the real client behind the shipped seam and the GuardRouter guard-egress seam
    - Show `HttpProviderClient` is a drop-in through the shipped `gateway_v2/edge/app.py`
      `build_app(..., provider: ProviderClient, …)` seam WITHOUT changing the seam's signature (R7.1);
      `edge/serve.py build_boot_dependencies` keeps `provider=StubProviderClient(...)` as the
      honest-boot default (R7.2) and a deployment injects `HttpProviderClient` explicitly, with the
      shipped `edge/wire/sse.py SSEDecoder` passed as the `decoder_factory` (edge wires the decoder in,
      preserving the layer contract)
    - Wire the generation-vs-guard split: chat/embeddings egress → `Generation_Provider` via the
      injected `ProviderClient`, `UpstreamRequest.destination` from the shipped `DispatchRouter` (R5.1);
      the `GuardRouter.select(snapshot)`-named guard destination reached over the split-topology TCP
      path, the guard self-hosted and never a platform-owned external AI (R5.2, R5.3); the detector
      call stays GW07/GW08 — named, not built — with the `owner_queue_bound_s` passed from
      `contract.owner_queue_bound_s()` at this wiring site (R9.3); add NO signature change, no new
      upward dispatch edge, no egress→detect edge
    - _Design: Components §C5 (generation-vs-guard split), §C6 (`build_app` injection seam)_
    - _Requirements: 5.1, 5.2, 5.3, 7.1, 7.2_

  - [x]* 6.2 Write boot/wiring integration tests for the injection seam
    - `tests/edge/test_b4_wiring.py`: `build_app(provider=HttpProviderClient(...))` assembles and
      streams against the `Fake_Upstream` while the `serve` default stays `StubProviderClient` with no
      real socket built (R7.1, R7.2); real completions are produced only when the real client is
      injected AND a reachable provider exists, and the local verification uses the `Fake_Upstream`
      otherwise (R7.3); a `GuardRouter` is constructed with `owner_queue_bound_s` from the contract and
      selection is dispatched to the `Fake_Guard` (R5.1, R5.2, R5.4)
    - _Design: Testing Strategy (integration — build_app wiring), Components §C6_
    - _Requirements: 5.1, 5.2, 5.4, 7.1, 7.2, 7.3_
  - Run the gate over the touched `edge` wiring + `tests/edge/test_b4_wiring.py` before building on it.

- [x] 7. Packaging — `pyproject.toml` provider HTTP client extra (verify/decide)
  - [x] 7.1 Confirm `httpx` is available under the `conformance` extra (reuse, no duplicate pin)
    - Modify/confirm `gateway_v2/pyproject.toml`: the real client reuses the EXISTING `conformance`
      extra (which already carries `httpx>=0.27`), so NO new third-party surface and NO duplicate pin
      is introduced; `[project].dependencies` stays `psycopg` + `redis` ONLY (base package stays
      framework-free, and a tree without the extra still imports `dispatch/http_provider` because httpx
      is lazy-imported) (R2.1, R2.2). If the owner prefers an independent knob, a sibling `provider`
      extra pinned to `httpx>=0.27` may be split instead — the design leans reuse `conformance`
    - Note (recorded here, applied with task 5): if the `Fake_Upstream` integration test must be
      collected/skipped like the live tests, record the `[tool.pytest.ini_options] addopts` narrowing
      (an `--ignore` / env-gate on the real-socket integration module) the same way the live-TCP tests
      are gated; the in-process fake-upstream subset runs by default
    - _Design: Components §C2 (declared under an extra, lazy-imported), Design Decisions #1_
    - _Requirements: 2.1, 2.2_
  - Run the gate (`.venv/bin/python -m pytest`, `mypy --strict`, `ruff`, `lint-imports`) after the
    packaging change to confirm the base suite is unaffected.

- [x] 8. Deferred seams recorded as named comments — `dispatch/http_provider.py` + `dispatch/guard_routing.py` (doc/verification task)
  - [x] 8.1 Verify each Deferred_Seam is named next to the code it leaves unscaffolded
    - Confirm the new `dispatch` modules each carry a comment naming the owning card and leaving the
      shipped stub/fake in place (nothing scaffolded): real BYOK `Generation_Provider` HTTP/SSE socket
      → B4 live gate / GW17-18 live half (local half uses `Fake_Upstream`, 8.1); real GPU `Guard_Pool`
      fleet socket → GW25 (local half uses `Fake_Guard`, 8.2); fleet-scale C43 capacity measurement
      (760 RPS, 190/guard) → GW20 / GW20b (8.3); the firewall-cost exclusion of the `Generation_Provider`
      documented as an accounting boundary, NOT an implemented metric — assert (smoke/doc) that no
      firewall-cost/latency metric observes `Generation_Provider` time (8.4)
    - This is a doc/verification task — a `grep` for the owning-card comments + an assertion that each
      stub/fake is in place and no cost metric is added; NO scaffolding is added
    - _Design: Deferred live gates (named seams, not scaffolded), Accounting boundary (R8.4, R1.6)_
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [x] 9. Final verification gate
  - [x] 9.1 Run the full gate and map evidence to the §10.9 card format
    - From cwd `gateway_v2` with venv `gateway_v2/.venv`, run the full gate: `.venv/bin/python -m
      pytest` (full offline suite + the B4 property/integration/structural subset), `mypy --strict`,
      `ruff check gateway_v2 state_control lint tests`, `lint-imports` (2 contracts kept, no new upward
      dispatch edge, no egress→detect edge), and the 6 AST gates (`check_capacity_literals`,
      `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`,
      `check_sizes`, `check_tenant_scale`); confirm `unscanned_channels()` is still `()` and file sizes
      stay ≤ 800 / functions ≤ 120. Fix any gate failure before closing
    - Map the evidence to §10.9 (why / implementation / live acceptance tests / exit / evidence /
      rollback): why = real generation egress + C43 guard routing behind the shipped seams; implementation
      = `HttpProviderClient` + `GuardRouter` + the two `resources.py` accessors + the `build_app` wiring;
      live acceptance = the `Fake_Upstream` / `Fake_Guard` integration transcripts (open/stream/final,
      mid-stream error_frame → R11, abort-within-bound + connection release, malformed → fail-closed,
      Fake_Guard routing) + the four property tests; exit = full gate green; rollback = B4 is additive
      (`dispatch/http_provider.py`, `dispatch/guard_routing.py`, the two no-op-at-default `resources.py`
      accessors, the injection wiring), reverting restores the `StubProviderClient` default with no
      behavioural change to any shipped module. Record the deferred live gates (real BYOK provider socket
      = B4 live half, real GPU guard fleet = GW25, fleet-scale C43 = GW20/GW20b) as such — NOT closed here
    - _Design: Testing Strategy (Gate — exit evidence), Deferred live gates_
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 8.1, 8.2, 8.3_

- [x] 10. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks (property, unit, integration) and can be skipped
  for a faster MVP; core implementation sub-tasks are never optional. The agent MUST implement
  un-starred sub-tasks and MUST NOT implement starred ones.
- Each task references specific requirement sub-clauses and the design section/property it satisfies
  for traceability; collectively the tasks cover all 9 requirements per the design.
- Checkpoints (tasks 4, 10) ensure each layer's gates (`mypy --strict`, `ruff`, `lint-imports` 2
  contracts, the 6 AST gates incl. `check_sizes`, `check_http_outside_edge_resolve` green, no
  egress→detect edge, `unscanned_channels()` empty) are green before the next layer builds on it; each
  code task is also followed by a note to run the gate.
- Property tests use a seeded `random.Random` for ≥10,000 iterations, no hypothesis, async via
  `asyncio.run`; one property ↔ one test; tagged `# Feature: provider-egress-gw-b4, Property N` and
  `# Validates: Requirements X.Y`. Only four properties exist (Property 1 Transform byte-identity
  round-trip, Property 2 terminal final-flag, Property 3 GuardRouter min-queue-wait selection,
  Property 4 GuardRouter determinism) — the design's PBT prework classifies the HTTP client / socket
  paths (connect, stream, abort-within-bound, resource release, `build_app` wiring) as INTEGRATION, so
  no other property sub-tasks are added.
- B4 DROPS the real `HttpProviderClient` in behind the shipped `ProviderClient` Protocol and ADDS
  `GuardRouter` as a sibling of the pure `DispatchRouter` (unchanged). It REUSES the shipped `Transform`
  codec (body shaping) and the shipped `edge/wire/sse.py SSEDecoder` (SSE parsing, INJECTED because
  `dispatch` may not import `edge`); nothing in `dispatch/provider.py` / `dispatch/routing.py` /
  `dispatch/transform.py` is modified. The ONLY existing-code touch is the two derived accessors added
  to `runtime/resources.py` (task 1.1) and the `build_app` wiring site (task 6.1), neither changing a
  signature.
- Capacity literals live only in `runtime/resources.py`: the `RV_OWNER_QUEUE_MS` owner-queue bound
  default and the GW19 guard-wait budget (`max(100, cap + 40)` ms, `cap` = CoDel hard cap 60 ms) are
  derived accessors there; `GuardRouter` takes `owner_queue_bound_s` passed in from
  `contract.owner_queue_bound_s()`, and `abort()` reads `contract.cancellation_bound_s()` — `dispatch`
  inlines no literal. The new `GuardLoad` / `Load_Snapshot` are frozen+slotted with no module-level
  mutable; `httpx` reuses the existing `conformance` extra (lazy-imported in `open()`) and the base
  deps stay `psycopg` + `redis` only.
- `check_http_outside_edge_resolve` stays green with an `httpx` client in `dispatch` — the gate forbids
  `HTTPException` / `JSONResponse` / `status_code=4xx`, none of which an outbound streaming client
  constructs; B4 renders no HTTP responses (a provider/guard failure surfaces as an
  `UpstreamEvent.error_frame` that `edge` turns into the declared SSE `Error_Frame`).
- Deferred seams (task 8) — real BYOK provider socket (B4 live half / GW17-18), real GPU guard fleet
  socket (GW25), fleet-scale C43 capacity (GW20 / GW20b), and the firewall-cost accounting boundary —
  have NO implementation tasks; they stay named comments next to the shipped stub/fake, nothing
  scaffolded.
- Shared-file coordination: §2.2 + §2.3 both append to `tests/dispatch/test_b4_guard_routing.py`, and
  §5.2 + §5.3 both append to `tests/dispatch/test_b4_http_integration.py` — co-waved test sub-tasks that
  append to one shared test file must coordinate on that file (append-only). Implementation sub-tasks
  that write the SAME new source file are serialized into distinct waves to avoid write conflicts:
  `dispatch/http_provider.py` (3.1 `open` → 3.2 `abort`) and the guard-routing source (2.1) before its
  tests (2.2/2.3).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.1"] },
    { "id": 3, "tasks": ["3.2"] },
    { "id": 4, "tasks": ["3.3", "5.1"] },
    { "id": 5, "tasks": ["5.2", "5.3", "5.4"] },
    { "id": 6, "tasks": ["6.1"] },
    { "id": 7, "tasks": ["6.2", "7.1"] },
    { "id": 8, "tasks": ["8.1"] },
    { "id": 9, "tasks": ["9.1"] }
  ]
}
```
