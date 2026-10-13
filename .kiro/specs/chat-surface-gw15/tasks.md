# Implementation Plan: OpenAI Chat Surface (GW15)

## Overview

Convert the GW15 design into a series of code-generation steps that land the **real**
OpenAI-compatible chat/completions surface **bottom-up along the import-linter layer contract**
(`domain → runtime → egress → dispatch → plan → admit → edge`), the same shape as the shipped
GW12 SSE-egress card. GW15 KEEPS the shipped `ChatRoute` streaming orchestration
(`_stream`/`_run_attempts`/`_finish_stream`/`_next_attempt`/`_as_chunks`) **verbatim** and only
replaces three deferrals — `_EMPTY_BODY` → `Transform.to_provider(...).body`,
`_DEFAULT_DESTINATION` → `DispatchRouter.select(plan)`, `enforcing_output=False` →
`StreamPipeline.for_plan(plan, ...)` — plus adds the §10.4 request lifecycle in front of the
stream. Every pipeline mechanism (GW19 admission, GW06 budget lease, GW12b holdback + output
guard, GW14d exports, GW05b/c identity + freshness) is **DONE** and is **wired into, never
rebuilt**. Each layer is validated by its own gates before the next layer builds on it, so there
is no orphaned code: every value type and seam lands a caller in a later step, and the final steps
boot the ASGI app with the real chat route live and run the full gate.

Implementation language: **Python** (the design is grounded in concrete `gateway_v2/gateway_v2/*.py`
modules and the shipped house idiom). All code targets `mypy --strict` + `ruff` clean.

### Conventions enforced in EVERY coding task

- **Property tests**: a seeded `random.Random` driven for **≥10,000 iterations**, **NO
  hypothesis**; async lifecycle paths via `asyncio.run`. Each property test is tagged
  `# Feature: chat-surface-gw15, Property N: <text>` and `# Validates: Requirements X.Y`, with
  exactly one property ↔ one property-based test.
- **Value types** are frozen slotted dataclasses or `StrEnum`; module-level tables use
  `MappingProxyType`. No module-level mutable (the route table is built in `build_routes`).
- **No capacity literal outside `runtime/resources.py`** — all pre-check bounds (size, expansion
  ratio, part count) derive from the injected `ResourceContract` or `HoldbackConfig`; `edge`
  embeds none.
- **`egress` MUST NOT import `detect`** — the input detector, output resolver, and
  `ProviderClient` are injected into `ChatRoute` in `edge` (the top layer) and threaded down.
- **Fail-closed everywhere**; `PerRequestExports.fail_open_total` is pinned at zero and never
  incremented.
- **Channel totality (C21)**: `unscanned_channels()` must stay empty — a `TEXT_CHANNELS` entry
  with no folding path is a build-time gate failure.
- Each task references its requirement sub-clauses and the design section/property it satisfies
  (traceability), matching the GW12 `tasks.md` style.

## Tasks

- [x] 1. OpenAI JSON codec + channel folding — `edge/wire/openai.py` (NEW, bottom of the build order)
  - [x] 1.1 Implement `CanonicalRequest`, `MalformedRequest`, and the parser
    - Create `gateway_v2/gateway_v2/edge/wire/openai.py`
    - Frozen slotted `CanonicalRequest(model, streaming, input_channels, text_deltas, surface)`
      carrying every scannable channel + routing fields + the stream flag
    - `MalformedRequest(code)` exception carrying a declared value-code (no raw framework error)
    - `OpenAIRequestParser.parse(body: bytes, *, surface: Surface) -> CanonicalRequest`: pure over
      the request bytes, no I/O and no security decision; records the `stream` flag as the
      streaming disposition; a malformed / off-contract body raises `MalformedRequest(code)`
    - Keep all security decisions OUT of this module (R1.5 — wire logic only)
    - _Design: Components §1, Data Models (CanonicalRequest/MalformedRequest)_
    - _Requirements: 1.1, 1.3, 1.4, 1.5_

  - [x] 1.2 Implement `fold_input_channels` over `domain/channels.py::TEXT_CHANNELS`
    - `fold_input_channels(parsed) -> tuple[tuple[str, str], ...]` walks every input `TextChannel`
      occurrence (every `messages[].content`, content part `.text`, `tool_calls[].function.arguments`,
      `function_call.arguments`, `name`/nested field, array element) into `(channel_name, text)` pairs
    - The fold is TOTAL over the enumerated input channels: a `TEXT_CHANNELS` input entry with no
      extraction path is a build-time failure (`unscanned_channels()` stays empty), so a new SDK
      field cannot silently escape scanning (C21)
    - Wire the folded pairs into `CanonicalRequest.input_channels`
    - _Design: Input-channel folding model (C21), Components §1_
    - _Requirements: 1.2_

  - [x] 1.3 Implement `OpenAIResponseRenderer.non_stream_body`
    - `OpenAIResponseRenderer.non_stream_body(req, deltas) -> bytes`: render a `CanonicalRequest`
      plus enforced upstream text into ONE OpenAI-compatible JSON body; parse-then-render preserves
      channel content byte-for-byte
    - _Design: Components §1, Data Models_
    - _Requirements: 9.1_

  - [x] 1.4 Write property test for parse/render round-trip
    - `tests/edge/test_lgw15_openai_codec.py` — **Property 1: Parse/render round-trip preserves
      channel content and the stream flag**
    - **Validates: Requirements 1.1, 1.3**; seeded `random.Random` ≥10,000 iters, no hypothesis:
      parse then re-render preserves every channel's text byte-for-byte and the streaming
      disposition exactly as the body `stream` flag
    - _Design: Correctness Properties → Property 1_

  - [x] 1.5 Write property test for input-channel fold totality (C21)
    - `tests/edge/test_lgw15_fold.py` — **Property 2: Input-channel fold is total (C21)**
    - **Validates: Requirements 1.2**; ≥10,000 iters, canary iterated over EVERY input
      `TextChannel` entry: a canary placed in any channel appears in the folded `input_channels`
    - _Design: Correctness Properties → Property 2_

  - [x] 1.6 Write malformed-body unit tests
    - `tests/edge/test_lgw15_openai_codec.py`: malformed / off-contract body variants each raise
      `MalformedRequest` with a declared value-code (not a raw framework error)
    - _Design: Testing Strategy (edge cases), Error Handling table (malformed body)_
    - _Requirements: 1.4_

- [x] 2. §10.4 lifecycle orchestration, pre-provider half — `edge/openai_chat.py` (NEW)
  - [x] 2.1 Implement `TenantIdentity`, `LifecycleOutcome`, and the lifecycle skeleton
    - Create `gateway_v2/gateway_v2/edge/openai_chat.py` (replace `__all__ = ()`)
    - Frozen slotted `TenantIdentity(org_id, principal)` and
      `LifecycleOutcome(plan, destination, body, refuse_code)`
    - `ChatSurface.__init__(*, deps: "ChatRoute")` and
      `ChatSurface.run_lifecycle(scope, body) -> tuple[CanonicalRequest | None, LifecycleOutcome]`
      ordering the ordered pipeline; each phase returns early with a `refuse_code` on failure (fail
      closed), BEFORE `provider.open`
    - _Design: Components §2, The §10.4 request lifecycle, Data Models_
    - _Requirements: 3.1, 7.1_

  - [x] 2.2 Implement the Pre_Identity_Clock cheap pre-checks (C26)
    - In `run_lifecycle`, BEFORE parse/canonicalization: compare request size, expansion ratio, and
      part counts against bounds read from the injected `ResourceContract`; a breach sets a declared
      pre-check `refuse_code` and stops before canonicalization
    - No capacity literal in `edge` — call `ResourceContract` accessors and compare
    - _Design: Components §2, Data Models (pre-check bounds), Architecture (lifecycle)_
    - _Requirements: 3.1, 3.2, 3.4_

  - [x] 2.3 Wire parse → identity → the ONE plan round trip
    - After pre-checks: parse via `deps.parser` (a `MalformedRequest` → declared `refuse_code`);
      resolve `TenantIdentity` via the thin `IdentityResolver` over `admit.principal_of` + the
      GW05b/c freshness stamp (`None`/stale/unavailable ⇒ auth `refuse_code`, fail closed);
      perform the single `lookup_plan(snapshot, org_id, surface)` on the served `ReplicaSnapshot`
      (exactly one `Shared_State_Round_Trip`); an unresolved plan ⇒ declared `refuse_code`, no forward
    - _Design: Components §2 & §5, The §10.4 request lifecycle_
    - _Requirements: 2.1, 3.3, 4.1, 4.2, 4.3_

  - [x] 2.4 Wire admission → budget → input scan with the BLOCK short-circuit
    - `AdmissionController.admit(owner_id, request_id, enqueued_at)`: a `ShedVerdict` ⇒
      `refuse_code = verdict.code` (with retry), `Admitted` ⇒ continue, undecidable ⇒ shipped
      controller fails closed to a shed; then `QuotaComponent.evaluate(cost, request_id)` on the
      local lease (no request-path store read): a `BudgetVerdict` ⇒ `refuse_code = BUDGET_UNAVAILABLE`
    - INPUT scan: run the injected detector (`Callable[[str], Sequence[Finding]]`) over every folded
      `Input_Channel`, resolve via the injected `OutputResolver.decide`; a BLOCK disposition
      (C22 `most_restrictive`) ⇒ `refuse_code = "input_blocked"` and return BEFORE `provider.open`
      (zero provider calls); a scan raising ⇒ `scan_failure` fail-closed withhold
    - `destination = DispatchRouter.select(plan)`; `body = Transform.to_provider(GatewayRequest(...)).body`
    - _Design: Components §2 & §5, Error Handling table_
    - _Requirements: 2.2, 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 2.5 Write property test for pre-identity pre-checks
    - `tests/edge/test_lgw15_lifecycle.py` — **Property 5: Pre-identity pre-checks reject before
      canonicalization**
    - **Validates: Requirements 3.1, 3.2**; ≥10,000 iters: a request over a `ResourceContract`-derived
      size/expansion/part-count bound renders a declared value-code and the parse step is never invoked
    - _Design: Correctness Properties → Property 5_

  - [x] 2.6 Write property test for the single shared-state round trip
    - `tests/edge/test_lgw15_lifecycle.py` — **Property 6: Exactly one shared-state round trip per
      request**
    - **Validates: Requirements 3.3, 6.3**; ≥10,000 iters over every flow (admitted/shed/budget-
      refused/input-blocked/completed), instrumented `snapshot_source`: the served plan snapshot is
      read exactly once and no store read is attributable to the budget spend
    - _Design: Correctness Properties → Property 6_

  - [x] 2.7 Write property test for identity fail-closed
    - `tests/edge/test_lgw15_lifecycle.py` — **Property 7: Unresolvable or stale identity fails closed**
    - **Validates: Requirements 4.2, 4.3**; ≥10,000 iters, instrumented `StubProviderClient`: an
      unresolvable identity or stale/unavailable freshness stamp renders a declared auth value-code
      and makes zero provider calls
    - _Design: Correctness Properties → Property 7_

  - [x] 2.8 Write property test for pre-provider refusal short-circuit
    - `tests/edge/test_lgw15_lifecycle.py` — **Property 8: A pre-provider refusal short-circuits the
      provider**
    - **Validates: Requirements 5.2, 6.2**; ≥10,000 iters: an admission `ShedVerdict` or a
      `BudgetVerdict` renders the declared value-code and makes zero provider calls (counted
      `StubProviderClient`)
    - _Design: Correctness Properties → Property 8_

  - [x] 2.9 Write property test for the BLOCK input channel test
    - `tests/edge/test_lgw15_input_block.py` — **Property 9: A BLOCK input-channel finding yields
      zero provider calls (input channel test)**
    - **Validates: Requirements 7.1, 7.2, 7.3**; ≥10,000 iters, canary iterated over EVERY input
      `TextChannel`: a BLOCK-resolving canary in any channel refuses the request, makes zero provider
      calls, and renders the declared block value-code
    - _Design: Correctness Properties → Property 9_

  - [x] 2.10 Write property test for C22 most-restrictive BLOCK
    - `tests/edge/test_lgw15_input_block.py` — **Property 10: BLOCK is the most-restrictive
      disposition (C22)**
    - **Validates: Requirements 7.4**; ≥10,000 iters over random finding sets containing ≥1 BLOCK:
      the resolved per-finding outcome is BLOCK regardless of any co-matching REDACT/FLAG rule
    - _Design: Correctness Properties → Property 10_

  - [x] 2.11 Write wiring/edge-case unit tests
    - `tests/edge/test_lgw15_lifecycle.py`: valid key → principal (4.1); admit called / `Admitted`
      continues (5.1/5.3); undecidable admission → shed (5.4); quota called (6.1)
    - _Design: Testing Strategy (unit / example tests)_
    - _Requirements: 4.1, 5.1, 5.3, 5.4, 6.1_

- [x] 3. Checkpoint — codec + pre-provider lifecycle green
  - Ensure all tests pass, ask the user if questions arise. Run `mypy --strict`, `ruff`,
    `lint-imports`, and the AST gates over `edge/wire/openai.py` + `edge/openai_chat.py`;
    confirm `unscanned_channels()` is empty.

- [x] 4. Non-streaming path — `edge/openai_chat.py` (NEW)
  - [x] 4.1 Implement `ChatSurface.collect_and_render`
    - `collect_and_render(req, plan, destination, body, send)`: open the injected `ProviderClient`,
      drain events to the final event, concatenate per-channel `text_deltas`; apply the output
      decision via the shipped `apply_decision(released, decision, base_offset=0)`; `OutputBlocked`
      ⇒ withhold the body + render the block value-code; else render ONE OpenAI JSON via
      `OpenAIResponseRenderer.non_stream_body` emitted through the single `emit_response(send,
      HTTPResponse)` path so an unmapped error renders the declared `Error_Envelope` body
    - An output scan/decision that cannot be computed ⇒ withhold the bytes (fail closed),
      `fail_open_total` stays 0
    - _Design: Components §3, Error Handling table_
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3_

  - [x] 4.2 Write property test for the REDACT output channel test
    - `tests/edge/test_lgw15_output.py` — **Property 11: A REDACT output-channel finding reaches the
      client zero times (output channel test)**
    - **Validates: Requirements 8.1, 8.2**; ≥10,000 iters, canary iterated over EVERY output
      `TextChannel`, for streaming AND non-streaming: a REDACT-resolving canary is removed/masked so
      the client-visible bytes contain it zero times
    - _Design: Correctness Properties → Property 11_

  - [x] 4.3 Write property test for BLOCK output withhold
    - `tests/edge/test_lgw15_output.py` — **Property 12: A BLOCK output finding withholds the whole
      response**
    - **Validates: Requirements 8.3, 9.3**; ≥10,000 iters: a BLOCK output finding withholds the body
      (streaming: terminal error frame, no content after; non-streaming: no JSON body) and renders the
      declared block value-code
    - _Design: Correctness Properties → Property 12_

  - [x] 4.4 Write property test for fail-closed with FAIL_OPEN pinned zero
    - `tests/edge/test_lgw15_output.py` — **Property 13: Uncomputable scan or decision fails closed
      with FAIL_OPEN pinned at zero**
    - **Validates: Requirements 8.4**; ≥10,000 iters including every injected scan/decision fault: the
      affected bytes are withheld and `PerRequestExports.snapshot().fail_open_total` stays zero
    - _Design: Correctness Properties → Property 13_

  - [x] 4.5 Write property test for non-streaming single enforced JSON
    - `tests/edge/test_lgw15_nonstream.py` — **Property 14: Non-streaming returns one enforced
      OpenAI-compatible JSON**
    - **Validates: Requirements 9.1**; ≥10,000 iters: the full response is collected, the output
      decision applied, and exactly one OpenAI-compatible JSON body returned whose content reflects
      the enforced (possibly redacted) text
    - _Design: Correctness Properties → Property 14_

  - [x] 4.6 Write unmapped-code non-stream unit test
    - `tests/edge/test_lgw15_nonstream.py`: an unmapped error value-code on the non-stream path renders
      the declared generic `Error_Envelope` body, never a raw framework error (9.2, 12.2)
    - _Design: Testing Strategy (edge cases); Requirements Coverage (9.2, 12.2)_
    - _Requirements: 9.2, 12.2_

- [x] 5. Real `ChatRoute` — `edge/routes.py` (MODIFY)
  - [x] 5.1 Expand `ChatRoute` with the GW15 deps and replace the three deferrals
    - Modify `gateway_v2/gateway_v2/edge/routes.py`: keep the shipped frozen+slotted `ChatRoute`
      fields and ADD `parser`, `renderer`, `input_detector`, `identity` (`IdentityResolver`),
      `snapshot_source`, `admission`, `quota`, `exports`
    - Replace the three deferrals in the shipped orchestration ONLY: `_build_pipeline` →
      `StreamPipeline.for_plan(plan, ...)`; `_destination` → `DispatchRouter.select(plan)`;
      `_EMPTY_BODY` in `_next_attempt` → `Transform.to_provider(GatewayRequest(...)).body`
    - Keep `_stream`/`_run_attempts`/`_finish_stream`/`_next_attempt`/`_as_chunks`,
      `Coalescer`/`FirstByteLatch`/`InFlightControl`/`StreamTimeouts`/`ScanExecutor` UNCHANGED
    - The chat handler dispatches to `ChatSurface` (`run_lifecycle` → stream OR `collect_and_render`);
      `build_routes(chat)` keeps the same route table shape (R2.3 `not_implemented` placeholders untouched)
    - _Design: Components §4 & §6, Grounding table (streaming orchestration REUSED verbatim)_
    - _Requirements: 2.2, 2.3, 2.4, 10.1, 10.2, 10.3, 10.4, 11.1, 12.1, 13.1, 13.2, 13.3_

  - [x] 5.2 Write property test for DispatchRouter determinism
    - `tests/edge/test_lgw15_dispatch.py` — **Property 3: DispatchRouter is a deterministic function
      of plan identity**
    - **Validates: Requirements 2.2**; ≥10,000 iters: `select` returns the same destination exactly
      when two plans share identity (`org_id` + `epoch.sequence.content_hash`), and `_DEFAULT_DESTINATION`
      is never returned
    - _Design: Correctness Properties → Property 3_

  - [x] 5.3 Write property test for plan-driven enforcement + unresolved fail-closed
    - `tests/edge/test_lgw15_dispatch.py` — **Property 4: Plan drives enforcement, and an unresolved
      plan fails closed**
    - **Validates: Requirements 2.3, 2.4**; ≥10,000 iters: `StreamPipeline.for_plan` engages output
      enforcement iff `enforcing_output_rules(plan)` is non-empty; an unresolved marker raises
      `EnforcementUndetermined`, renders a declared value-code, and makes zero provider calls
    - _Design: Correctness Properties → Property 4_

  - [x] 5.4 Write property test for clean streaming terminal [DONE]
    - `tests/edge/test_lgw15_stream.py` — **Property 15: Clean streaming ends with exactly one
      terminal [DONE]**
    - **Validates: Requirements 10.1**; ≥10,000 iters, async via `asyncio.run`: a cleanly completing
      stream emits exactly one terminal `[DONE]` and no frame after it
    - _Design: Correctness Properties → Property 15_

  - [x] 5.5 Write property test for tool-call fragment reassembly
    - `tests/edge/test_lgw15_stream.py` — **Property 16: Tool-call fragment reassembly round-trip**
    - **Validates: Requirements 10.2**; ≥10,000 iters over any `arguments` string and any
      fragmentation: concatenation of forwarded fragments reconstructs the identical `arguments`
    - _Design: Correctness Properties → Property 16_

  - [x] 5.6 Write property test for the no-splice invariant
    - `tests/edge/test_lgw15_stream.py` — **Property 17: No-splice invariant after the first content
      byte**
    - **Validates: Requirements 10.3**; ≥10,000 iters: a fallback/retry is permitted only while the
      `FirstByteLatch` is unset; once the first content byte is released no second upstream response
      is spliced in
    - _Design: Correctness Properties → Property 17_

  - [x] 5.7 Write property test for single Error_Envelope rendering
    - `tests/edge/test_lgw15_errors.py` — **Property 18: Every value-code renders through the single
      Error_Envelope**
    - **Validates: Requirements 12.1**; ≥10,000 iters: every value-code renders through the one
      `edge/errors` envelope — HTTP body for non-streaming, declared SSE Error_Frame for streaming
    - _Design: Correctness Properties → Property 18_

  - [x] 5.8 Write property test for two-tenant isolation
    - `tests/edge/test_lgw15_isolation.py` — **Property 19: Two-tenant isolation**
    - **Validates: Requirements 13.1, 13.2, 13.3**; ≥10,000 iters, two concurrently-served tenants
      with opposite policies: each request is served under its own `ExecutionPlan` and no config,
      finding, request ID, or audit of one tenant appears in the other's outcome
    - _Design: Correctness Properties → Property 19_

  - [x] 5.9 Write per-trigger in-flight example tests
    - `tests/edge/test_lgw15_inflight.py`: one example per `InFlightControl` trigger (max duration,
      kill switch, key revocation, plan change, stale snapshot) asserts the matching declared code
      (`STREAM_MAX_DURATION`/`stream_killed`/`STREAM_KEY_REVOKED`/`STREAM_PLAN_CHANGED`/
      `STREAM_SNAPSHOT_STALE`) at `_finish_stream`'s single terminal site, reusing the shipped
      `InFlightControl`/`KillLatch` seams
    - _Design: Testing Strategy (per-trigger in-flight), Error Handling table_
    - _Requirements: 10.4_

- [x] 6. Boot wiring — `edge/app.py` (MODIFY)
  - [x] 6.1 Build the single `ChatRoute` at boot
    - Modify `gateway_v2/gateway_v2/edge/app.py`: construct every GW15 dep once (bounds from the
      injected `ResourceContract`/`HoldbackConfig`), build the single `ChatRoute`, inject the real
      GW07/GW08 detector as `input_detector` (the stub in tests) and `StubProviderClient` as
      `provider` locally; no module-level mutable, no capacity literal
    - Wire `exports` (`PerRequestExports`): every terminal outcome (allowed/shed/budget-refused/
      input-blocked/output-redacted/output-blocked/errored) populates admission, budget,
      input-enforcement, and output-enforcement dimensions, keeps `fail_open_total` at zero, and
      places no raw request/response content
    - _Design: Components §6 & §7, File list_
    - _Requirements: 11.1, 11.2, 11.3, 14.1, 14.2, 14.3, 14.4_

  - [x] 6.2 Write property test for content-free exports with FAIL_OPEN zero
    - `tests/edge/test_lgw15_exports.py` — **Property 20: Every terminal outcome populates
      content-free exports with FAIL_OPEN zero**
    - **Validates: Requirements 14.1, 14.2, 14.3, 14.4**; ≥10,000 iters over every terminal outcome:
      `PerRequestExports` carries the admission/budget/input/output dimensions, `fail_open_total`
      stays zero, and no raw request/response content appears in any export field
    - _Design: Correctness Properties → Property 20_

  - [x] 6.3 Write boot-wiring and ProviderClient injection unit tests
    - `tests/edge/test_lgw15_app.py`: `build_app` builds one `ChatRoute` with every dep, accepts
      `StubProviderClient` as the injected `ProviderClient` with no real socket (11.2), and the
      chat/completions routes resolve while the GW16/17/18 placeholders stay `not_implemented`
    - _Design: Components §7, Scope boundaries_
    - _Requirements: 11.2_

- [x] 7. Conformance harness enablement (in-process subset; live gates deferred)
  - [x] 7.1 Enable the in-process conformance subset against the Chat_Surface
    - Enable the in-process subset of `gateway_v2/tests/openai_conformance/` (currently excluded via
      pyproject `addopts`) for scenarios S01–S07, streaming and non-streaming; the unmodified Python
      OpenAI SDK targets the handler with only `base_url` + API key swapped, `StubProviderClient`
      behind it; the LGW15-2 stop rule fails the acceptance if any client code change beyond the
      swap is required
    - Keep the real-TCP + Node SDK runs and the LGW15-6 10,000 C2 chat replay as DEFERRED live gates:
      the real `ProviderClient` socket stays a documented named seam (NOT scaffolded as an un-runnable
      gate); `StubProviderClient` stays the in-process substitute
    - _Design: Testing Strategy (Conformance), Scope boundaries (deferred live gates)_
    - _Requirements: 11.3, 12.3, 12.4, 15.1, 15.2, 15.3_

- [x] 8. Final verification gate
  - [x] 8.1 Run the full gate and reconcile
    - Run the full `gateway_v2` pytest suite + the `lgw15` subset + the in-process conformance
      subset; `mypy --strict`; `ruff`; `lint-imports` (both contracts — layer order holds, `egress`
      never imports `detect`); ALL 5 AST gates (`check_capacity_literals`, `check_no_module_mutable`,
      `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_tenant_scale`); and
      confirm `unscanned_channels()` is empty
    - Fix any gate failure before closing; the gate must be green
    - _Design: CI gates (R15.4), Testing Strategy_
    - _Requirements: 7.5, 15.4_

- [x] 9. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks (property, unit, example) and can be skipped for
  a faster MVP; core implementation sub-tasks are never optional. The agent MUST implement un-starred
  sub-tasks and MUST NOT implement starred ones.
- Each task references specific requirement sub-clauses and the design section/property it satisfies
  for traceability; collectively the tasks cover all 15 requirements per the design's Requirements
  Coverage table.
- Checkpoints (tasks 3, 9) ensure each layer's gates (`mypy --strict`, `ruff`, import-linter, AST
  gates, `unscanned_channels()` empty) are green before the next layer builds on it.
- Property tests use a seeded `random.Random` for ≥10,000 iterations, no hypothesis, async via
  `asyncio.run`; one property ↔ one test; tagged `# Feature: chat-surface-gw15, Property N` and
  `# Validates: Requirements X.Y`. The channel-test properties (2, 9, 11) iterate the canary over
  every `TEXT_CHANNELS` entry so a dropped folding path fails the gate (C21).
- GW15 WIRES the shipped mechanisms (GW19 admission, GW06 budget lease, GW12b holdback + output
  guard, GW14d exports, GW05b/c identity + freshness) and KEEPS the shipped `ChatRoute` streaming
  orchestration verbatim; it replaces only the three deferrals (`_EMPTY_BODY`,
  `_DEFAULT_DESTINATION`, `enforcing_output=False`). `egress` never imports `detect`; the detector,
  resolver, and `ProviderClient` are injected through `ChatRoute` in `edge`.
- Capacity literals live only in `runtime/resources.py`; the §10.4 pre-check bounds derive from the
  injected `ResourceContract`. The pipeline fails closed everywhere and `fail_open_total` stays zero.
- The deferred live gates — the real HTTP/SSE provider socket, the real-TCP + Node SDK conformance
  runs, and the LGW15-6 10,000 C2 chat replay — have NO implementation tasks; they stay named seams
  (task 7.1), with `StubProviderClient` as the in-process substitute.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["1.4", "1.5", "1.6", "2.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "2.4"] },
    { "id": 4, "tasks": ["2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "4.1"] },
    { "id": 5, "tasks": ["4.2", "4.3", "4.4", "4.5", "4.6", "5.1"] },
    { "id": 6, "tasks": ["5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "5.9", "6.1"] },
    { "id": 7, "tasks": ["6.2", "6.3", "7.1"] },
    { "id": 8, "tasks": ["8.1"] }
  ]
}
```
