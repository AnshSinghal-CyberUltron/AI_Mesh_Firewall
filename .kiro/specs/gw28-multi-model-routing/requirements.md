# Requirements Document

## Introduction

Card **GW28 — Multi-model routing** extends the shipped `gateway_v2` dispatch layer to route a
single resolved request across **multiple generation models / providers** deterministically, without
double generation, policy bypass, or corrupt streams. It also closes a recorded-not-fixed defect
that a sibling card depends on: today a **stalled upstream holds a non-streaming request up to the
300 s read timeout** (recorded in `docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md` under GW36 Slice S1 as
"S5 / GW28"), which GW36 S5 cannot land on top of. GW28 bounds that stall.

### Where this sits in the shipped tree

The dispatch layer already ships the pieces GW28 builds on:

- `gateway_v2/dispatch/routing.py` — `DispatchRouter.select(plan)`: a **pure, deterministic**
  plan-identity → destination string selector (`__slots__ = ()`, no clock, no `random`, no I/O, no
  module-level mutable state). The destination is a total function of the plan identity
  (`org_id`, `epoch`, `sequence`, `content_hash`); `compiled_at` and `feed_seq` are deliberately
  excluded. This is the determinism contract GW28 must preserve and extend to a multi-destination
  world.
- `gateway_v2/dispatch/provider.py` — the `ProviderClient` `@runtime_checkable` Protocol
  (`open(request) -> AsyncIterator[UpstreamEvent]`, `async abort()`), the frozen slotted value types
  `UpstreamRequest(destination, body)` and `UpstreamEvent(text_deltas, final, error_frame,
  gateway_error)`, and the in-process `StubProviderClient`.
- `gateway_v2/dispatch/openai_upstream.py` — `OpenAIUpstreamProvider` (B4/GW36 S1): one pooled
  `httpx.AsyncClient` per worker built by `httpx_client_factory(api_key, timeout_s, connections)`.
  Today it maps `httpx.TimeoutException -> upstream_timeout`, non-2xx to typed codes, and a
  malformed stream to `STREAM_MALFORMED_UPSTREAM`. The **single `timeout_s`** on that client is the
  only stall guard, and it is a coarse whole-request bound (the 300 s symptom).
- `gateway_v2/dispatch/transform.py` — the byte-verified codec (`Transform`) GW28 reuses unchanged.
- `gateway_v2/runtime/resources.py` — the **single capacity authority**: every streaming
  timeout/duration/bound (`inter_chunk_timeout_s`, `idle_timeout_s`, `max_stream_duration_s`,
  `cancellation_bound_s`, …) is derived as a multiple of `target_p99_ms`. This is the only module
  `lint/check_capacity_literals.py` exempts. GW28 adds its new bound **here and nowhere else**.

### Hard constraints this card inherits (strict codebase)

These are not negotiable and every requirement below is written to respect them:

1. **Bottom-up import-linter layer order.** `dispatch` sits above `egress`/`runtime`/`contracts`/
   `domain` and **below** `plan`. `dispatch` imports `domain.plan` (the plan vocabulary), never the
   `plan` compilation layer, and never `edge`/`admit`/`detect`. No new forbidden edge may appear.
2. **No new capacity literal outside `runtime/resources.py`.** Every limit, timeout, destination
   count, or bound GW28 introduces is a derivation inside `ResourceContract`, read through an
   accessor. `lint/check_capacity_literals.py` must still exit 0.
3. **No module-level mutable state.** Routing tables, per-provider counters, and selection state are
   carried by value or per-instance; `lint/check_no_module_mutable.py` must still exit 0.
4. **Frozen / slotted dataclasses for value types.** Any new value type (a routing decision, a
   provider descriptor, a multi-destination plan view) is `@dataclass(frozen=True, slots=True)` or a
   `__slots__` class; `lint/check_frozen_dataclasses.py` must still exit 0.
5. **Fail-closed everywhere.** Ambiguity, missing configuration, an unknown model, an unreachable
   provider, a selection that cannot be made, or an exception in routing resolves to a **declared
   typed refusal (block / error)** — never to an arbitrary provider, never to a silent fallback that
   bypasses enforcement, never `FAIL_OPEN`.
6. **`edge` / `resolve` gain no forbidden imports.** The HTTP rendering of a GW28 refusal stays in
   `edge` via the existing `Error_Envelope`; `dispatch` returns value-codes only, no HTTP object.

### Documented conservative assumptions (runbook genuinely ambiguous — recorded, not blocking)

The authoritative GW28 card prose lives in `AI_MESH_MASTER_RUNBOOK_v3.1_CLEAN.docx`, which is **not
present in this worktree** (only the v2/v2.1/v3 `*_BACKEND_REWRITE` docx and the v3 markdown are
here, and §0-A.5 stops at GW24). The requirement set is therefore reconstructed from: the PROGRESS
file's GW28 references, the shipped dispatch/routing/provider code, the §1 latency contract, and the
existing routing-transparency behaviour recorded in `AGENTS.md` (PIPELINE-0021/0031: `model_routing`
stage, `route_destination`, `routing_reason`, `decision_source`, adjudicator, `remapped_from`). This
runs overnight unattended, so every genuine gap is resolved with a **conservative, fail-closed**
decision and recorded here as an assumption rather than blocking:

- **A1 — Routing selection is plan-derived and deterministic, not model-adaptive.** GW28 extends the
  shipped pure `DispatchRouter` to a **set** of plan-pinned destinations and selects among them by a
  pure function of plan identity plus injected, deterministic selection inputs (no clock, no
  `random`, no live load probe on the request path). Any "smart"/adaptive routing signal is treated
  as an **injected snapshot value**, exactly as B4's `Load_Snapshot` and GW06's injected clock are.
- **A2 — The candidate set comes from the pinned plan.** The models/providers a request may route to
  are fixed by the resolved, signed `ExecutionPlan` for the org.
  **A2 (amended, round 4; owner sign-off pending, conservative default applied):** the pinned plan
  contributes the *identity* (org, epoch, sequence, content_hash) that keys the HRW ranking. The
  `Candidate_Set` itself comes from a boot-time, org-keyed `RoutingTable` (design D-1), because the
  shipped `ExecutionPlan` carries no candidate list. A plan epoch change re-keys the ranking but does
  not change the table. An empty or unresolvable set still fails closed. GW28 does **not** read a live
  provider registry on the request path; an empty or unresolvable candidate set fails closed.
- **A3 — "No double generation" is an invariant across retry/fallback/remap.** At most one upstream
  generation stream per request may deliver content to the client. A safe retry or fallback to a
  second model is permitted **only after** the first attempt has produced **zero forwarded content
  bytes** and has been terminated; once any content byte is forwarded, no re-generation occurs.
- **A4 — The stalled-upstream bound applies to both non-streaming and streaming, derived from the
  SLO authority.** The bound is added to `runtime/resources.py` as a multiple of `target_p99_ms`
  (the existing `_stream_timeout_s` idiom), not as a literal. For non-streaming it bounds time-to-
  first-byte and whole-response; for streaming it reuses / aligns with the shipped
  `inter_chunk_timeout_s` and `max_stream_duration_s`. The explicit numeric bound is stated in
  Requirement 5.
- **A5 — Enforcement is unchanged by routing choice.** Which model is selected may **never** change
  whether input/output enforcement runs. The same resolved plan's enforcing rules apply to every
  candidate; routing cannot select a "guard-off" path. This is the no-policy-bypass invariant.
- **A6 — Local verification only; real multi-provider sockets are a deferred live gate.** Like B4,
  the real BYOK sockets and fleet-scale capacity are `Live_Gate`s. GW28 is verified against injected
  stubs / fake upstreams in-process.

## Glossary

- **ExecutionPlan**: The shipped frozen resolved plan (`domain.plan`) carrying `org_id`, `epoch`,
  `sequence`, `content_hash`, `version`. It carries no `model`/`provider`/`destination` field — routing
  is derived, not stored.
- **DispatchRouter**: The shipped pure plan-identity → single-destination selector. GW28 extends the
  routing surface without weakening its purity contract.
- **Candidate_Set**: The ordered set of generation destinations (HRW-keyed by the pinned plan identity; sourced from the boot-time RoutingTable, see A2 amended) a request may route to.
  Derived from the resolved `ExecutionPlan`; carried by value (frozen/slotted); never a module-level
  mutable table.
- **Routing_Decision**: A frozen, slotted value type recording the chosen destination, the requested
  vs selected model, the `routing_reason`, the `decision_source`, and (on a remap) `remapped_from`.
  A value only — it carries no HTTP object and is rendered for the trace by `edge`.
- **Selection_Inputs**: The injected, deterministic inputs routing may consult (a `Load_Snapshot`,
  injected weights, injected clock if ever needed). Never wall-clock or `random` read on the path.
- **Generation_Provider / Model**: An organization-selected / BYOK OpenAI-compatible external LLM
  destination. Excluded from firewall-infrastructure cost/latency accounting (runbook "Customer
  model").
- **ProviderClient / UpstreamRequest / UpstreamEvent**: The shipped dispatch Protocol and frozen
  value types. GW28 does not change them.
- **Stall_Bound**: The GW28 upstream stall/read bound added to `ResourceContract`, derived from
  `target_p99_ms`. Replaces the coarse 300 s whole-request `timeout_s` as the effective stall guard.
- **No_Double_Generation**: The invariant that at most one upstream generation stream forwards
  content to the client per request (A3).
- **No_Policy_Bypass**: The invariant that the resolved plan's enforcement applies identically to
  every candidate, so routing choice cannot disable or skip a guard (A5).
- **Stream_Integrity**: The invariant that a routed, retried, or aborted stream stays a well-formed
  SSE / OpenAI stream — one terminal frame, no content after an error frame, no interleaving of two
  upstreams' bytes.
- **Live_Gate**: A deferred verification needing real cloud resources (real BYOK sockets, fleet
  capacity).

## Requirements

### Requirement 1: Deterministic multi-destination routing

**User Story:** As the gateway data plane, I want routing across multiple candidate models/providers
to be a pure, deterministic function of the pinned plan, so that the same request on the same plan
always lands on the same upstream and a safe retry re-derives the identical choice.

#### Acceptance Criteria

1. THE routing surface SHALL preserve the shipped `DispatchRouter` purity contract: no clock, no
   `random`, no I/O, and no module-level mutable state on the selection path.
2. WHEN a request resolves to an `ExecutionPlan` with a `Candidate_Set`, THE router SHALL select a
   destination as a total, deterministic function of the plan identity (`org_id`, `epoch`,
   `sequence`, `content_hash`) together with the injected `Selection_Inputs`.
3. WHEN the same `ExecutionPlan` and the same `Selection_Inputs` are presented twice, THE router
   SHALL return the identical `Routing_Decision` both times (property-testable without a harness).
4. THE router SHALL NOT consult `compiled_at` or `feed_seq` for selection, consistent with the
   shipped identity contract (time and another tenant's cursor excluded).
5. THE `Candidate_Set` and the `Routing_Decision` SHALL be frozen/slotted value types carried by
   value, with no module-level mutable routing table.
6. WHERE routing consults a load/weight signal, THE signal SHALL be an injected `Selection_Inputs`
   snapshot value, never a wall-clock read or a live probe issued on the request path.

### Requirement 2: No double generation

**User Story:** As a tenant paying per generated token, I want a routed request to generate from at
most one model at a time and never re-generate once output has started, so that routing, retry, and
fallback cannot cause duplicate billing or duplicate content.

#### Acceptance Criteria

1. FOR ANY single request, THE gateway SHALL open at most one generation stream that forwards
   content to the client at a time.
2. WHEN a candidate attempt is selected, THE gateway SHALL NOT open a second candidate's generation
   stream while the first attempt is still live.
3. WHEN a safe retry or fallback to a second candidate occurs, THE gateway SHALL permit it ONLY IF
   the first attempt forwarded zero content bytes to the client and has been terminated and its
   upstream connection released (`abort()` honored).
4. IF the first attempt has already forwarded any content byte to the client, THEN THE gateway SHALL
   NOT re-generate on another candidate and SHALL surface the original stream's terminal outcome.
5. THE retry/fallback decision SHALL be deterministic and re-derivable (Requirement 1), so a replay
   lands on the same next candidate.
6. THE number of attempts per request SHALL be bounded by a value derived in `runtime/resources.py`
   (no literal elsewhere), and exhausting it SHALL fail closed with a declared typed refusal.

### Requirement 3: No policy bypass across routing choices

**User Story:** As the security owner, I want every candidate model to be subject to the identical
resolved enforcement, so that no routing choice can become a path that skips input or output guards.

#### Acceptance Criteria

1. THE enforcement applied to a request SHALL be a function of the resolved `ExecutionPlan` only, and
   SHALL be identical regardless of which candidate destination is selected.
2. THE router SHALL NOT be able to select a destination that disables, skips, or downgrades any
   enabled input or output control of the pinned plan.
3. WHEN a retry or fallback selects a second candidate, THE gateway SHALL apply the same resolved
   input enforcement (already-scanned/masked request body) and the same output enforcement to the
   second attempt — the scanned prompt is reused, never re-admitted raw.
4. IF routing cannot guarantee that the pinned plan's enforcement applies to the selected
   destination, THEN THE gateway SHALL fail closed with a declared typed refusal rather than dispatch.
5. THE routing layer SHALL NOT import `edge`, `admit`, or `detect`, nor otherwise create a path that
   reaches a provider without passing the shipped enforcement seam.

### Requirement 4: Stream integrity under routing, retry, and abort

**User Story:** As an OpenAI-SDK client, I want a routed or retried stream to remain a single
well-formed SSE / OpenAI stream, so that routing never produces interleaved, duplicated, or
truncated-after-content output.

#### Acceptance Criteria

1. FOR ANY request, THE downstream stream SHALL carry the bytes of exactly one upstream generation
   attempt's content; two candidates' content SHALL NEVER be interleaved on one client stream.
2. WHEN a retry/fallback occurs under the zero-forwarded-content condition (Requirement 2.3), THE
   downstream stream SHALL emit no content from the abandoned attempt.
3. THE routed stream SHALL preserve the shipped GW12 stream state machine: exactly one terminal
   frame, and no content frame after an error frame.
4. WHEN a candidate attempt is aborted (retry, cancellation, kill), THE gateway SHALL stop reading
   that upstream within the shipped `cancellation_bound_s` and release its connection before any
   second attempt begins.
5. THE routed path SHALL reuse the shipped `Transform` / SSE codec and preserve its byte-identity
   round-trip on text content (GW12 Property 8); GW28 SHALL NOT duplicate SSE parsing.
6. A mid-stream provider `error_frame` SHALL continue to be scanned before any byte is forwarded
   (shipped R11), unchanged by routing.

### Requirement 5: Bounded stalled-upstream read (the GW36 S5 dependency)

**User Story:** As a client whose chosen model has stalled, I want my non-streaming request to fail
in a bounded, declared way instead of hanging to the 300 s read timeout, so that a stalled upstream
cannot pin a worker or a client connection.

#### Acceptance Criteria

1. THE gateway SHALL bound the time a non-streaming request waits on a stalled upstream by a
   `Stall_Bound` that is **strictly less than the current 300 s whole-request read timeout** and is
   derived in `runtime/resources.py` as a multiple of `target_p99_ms` (no literal outside that
   module), using the shipped `_stream_timeout_s` fail-closed idiom.
2. WHEN an upstream produces no first byte within the `Stall_Bound`, THE gateway SHALL terminate the
   attempt and surface a declared typed code (`upstream_timeout`, reusing the shipped
   `openai_upstream` vocabulary) — NOT a 200, NOT an empty success, NOT a hang.
3. WHEN an upstream stalls mid-response (streaming or non-streaming) beyond the inter-chunk / idle
   bound, THE gateway SHALL terminate and release the connection within the shipped streaming
   timeout contract, rather than waiting for the coarse whole-request timeout.
4. THE `Stall_Bound` termination SHALL release the pooled connection for the request and SHALL NOT
   leak a half-open socket back into the pool.
5. WHEN the `Stall_Bound` fires on a candidate during a routed request, THE gateway MAY apply the
   Requirement 2 retry/fallback rules (zero-forwarded-content only); otherwise it SHALL surface the
   `upstream_timeout` refusal.
6. THE `Stall_Bound` value and its derivation SHALL be observable (logged as a resource-contract
   default at start-up, same as the other derived bounds) so GW36 S5 can assert the bound it depends
   on.

### Requirement 6: Routing transparency and typed refusals

**User Story:** As an operator reading a trace, I want to see which model was requested, which was
selected, why, and — on a refusal — a declared reason, so that routing is auditable and never a
silent black box.

#### Acceptance Criteria

1. THE gateway SHALL expose a `Routing_Decision` carrying at least: requested model, selected model
   / destination, `routing_reason`, `decision_source`, and (on a remap/fallback) `remapped_from` and
   the attempt index — consistent with the shipped `model_routing` trace stage (PIPELINE-0021).
2. THE `Routing_Decision` SHALL be a frozen/slotted value returned from `dispatch`; its rendering
   into the trace / HTTP response SHALL happen in `edge` via the existing `Error_Envelope` / trace
   projection, with no HTTP object constructed in `dispatch`.
3. WHEN routing fails closed (empty `Candidate_Set`, unknown model, attempts exhausted,
   unreachable-all, enforcement-guarantee gap), THE gateway SHALL emit a declared typed value-code
   rendered through the shipped error envelope, never an arbitrary-provider fallback.
4. THE routing decision and any refusal SHALL be recorded in the audit/trace path **without**
   placing raw request content or secrets at rest (consistent with the shipped redaction-safe trace
   fields).

### Requirement 7: Layer, capacity, and purity gates stay green

**User Story:** As a maintainer, I want GW28 to land under the same strict gates as every shipped
card, so that the multi-model routing does not smuggle in a forbidden import, a stray literal, or
mutable state.

#### Acceptance Criteria

1. THE implementation SHALL keep `import-linter` passing with no new forbidden edge (`dispatch`
   imports only `domain` and below; never `plan`, `edge`, `admit`, or `detect`).
2. THE implementation SHALL keep `lint/check_capacity_literals.py` at exit 0: every new bound (attempt
   cap, `Stall_Bound`, candidate limits) lives only in `runtime/resources.py`.
3. THE implementation SHALL keep `lint/check_no_module_mutable.py` and
   `lint/check_frozen_dataclasses.py` at exit 0: new value types are frozen/slotted and no
   module-level mutable routing state is introduced.
4. THE implementation SHALL keep `mypy --strict` and `ruff` clean, consistent with the shipped tree.
5. THE core routing determinism, no-double-generation, and no-policy-bypass invariants SHALL be
   expressed as seeded property tests (house idiom, no `hypothesis`) under
   `gateway_v2/tests/dispatch/`, and the stalled-upstream bound SHALL be exercised against an
   injected fake upstream that never sends a first byte.

## Non-Functional Requirements

1. **Determinism is a correctness property, not a convenience.** Routing, retry selection, and
   fallback order are reproducible from declared, injected inputs; no path reads wall-clock or
   `random` for a selection decision.
2. **Latency.** Routing selection is pure, in-process work and SHALL stay inside the §1 request-path
   SLO (`target_p99_ms`, default 20 ms); it adds no I/O on the request path. The `Stall_Bound` is a
   failure bound, not a success-path cost.
3. **Local verifiability.** Every invariant is testable in-process against injected stubs / fake
   upstreams; real multi-provider sockets and fleet capacity are deferred `Live_Gate`s.

## Acceptance Criteria (card-level, numbered and testable)

1. Given one `ExecutionPlan` with a two-candidate `Candidate_Set` and fixed `Selection_Inputs`,
   repeated selection returns the identical `Routing_Decision` every time (Req 1).
2. Given a request whose first candidate forwards zero content bytes then fails, a fallback to the
   second candidate occurs and exactly one content stream reaches the client (Req 2, Req 4).
3. Given a request whose first candidate has already forwarded a content byte and then errors, no
   second generation is attempted and the original terminal outcome is surfaced (Req 2.4).
4. Given any candidate selection, the resolved plan's enforcing input/output controls run identically
   (no candidate yields a guard-off path) (Req 3).
5. Given an injected fake upstream that never sends a first byte, a non-streaming request terminates
   with `upstream_timeout` at the derived `Stall_Bound` (≪ 300 s), the pooled connection is released,
   and no 200/empty-success is returned (Req 5).
6. Given a mid-stream stall beyond the inter-chunk/idle bound, the stream terminates and releases the
   connection within the shipped streaming timeout contract (Req 5.3).
7. A `Routing_Decision` (requested vs selected, reason, source, remap/attempt) is present on the
   trace, and every fail-closed path emits a declared typed code with no raw content at rest (Req 6).
8. `import-linter`, `check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`,
   `mypy --strict`, and `ruff` all pass with GW28's changes (Req 7).

## Out of Scope

- The real BYOK provider sockets and the fleet-scale multi-provider capacity run — deferred
  `Live_Gate`s (A6), mirrored on B4.
- Adaptive / learned routing policy (cost-vs-risk scoring, live load balancing) beyond deterministic
  selection over an injected snapshot. GW28 provides the deterministic selection seam; a smarter
  policy that still feeds it an injected snapshot is additive and out of this card.
- The guard-pool (detector) C43 load-aware routing — that is B4/GW18 territory and already specified
  in `provider-egress-gw-b4`. GW28 routes **generation** models, not the enforcement guard tier.
- The console `error` action schema for an upstream failure (shows `output_guardrail: block` today)
  — recorded against GW35, not GW28.
- The pre-first-byte streamed-refusal-after-200 SSE-error-frame behaviour — recorded against
  GW34 / GW36 S5, not GW28, except where Requirement 4.3 already forbids content after an error frame.
- Changes to `UpstreamRequest` / `UpstreamEvent` / `ProviderClient` value types or the `Transform`
  codec — reused unchanged.
```
