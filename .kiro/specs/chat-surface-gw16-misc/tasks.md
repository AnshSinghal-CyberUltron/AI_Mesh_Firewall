# Implementation Plan: OpenAI Misc Surfaces (GW16)

## Overview

Convert the GW16 design into a series of code-generation steps that land the **real** three
misc OpenAI-compatible surfaces (`POST /v1/embeddings`, `GET /v1/models` + `GET /v1/models/{id}`,
`POST /v1/moderations`) **bottom-up along the import-linter layer contract**
(`domain → runtime → egress → dispatch → plan → admit → edge`), the same shape as the shipped
GW12/GW15 cards. GW16 does **not** build a new pipeline: it WIRES the same already-shipped seams
GW15 wired — the injected `InputDetector` + `OutputResolver` input-scan path, GW05b/c identity +
freshness (`IdentityResolver`), the GW19 `AdmissionController` (`AdmissionDecider`), the GW06
`QuotaComponent` budget lease (`BudgetDecider`), the injected `ProviderClient`
(`StubProviderClient` locally), the single `edge/errors` `Error_Envelope`, and the GW14d
`PerRequestExports` — into three new surfaces. It REUSES the GW15 shared OpenAI typing
(`MalformedRequest` + the `MALFORMED_*` codes, the fixed-key-order JSON idiom, the fold-totality
discipline) and ADDS the misc request/response typing in a SIBLING module
`edge/wire/openai_misc.py` (Decision 3 — `check_sizes` 800-line gate), keeping `edge/wire` as the
ONLY layer that renders OpenAI wire shapes.

The three misc surfaces fill the currently-empty `edge/openai_misc.py` (`__all__ = ()`) and
replace the three `_placeholder(...)` entries in `edge/routes.py::build_routes`; the
responses/mcp/rag/vector placeholders and the GW15 chat/completions routes are untouched. Each
layer is validated by its own gates before the next layer builds on it, so there is no orphaned
code: every value type and seam lands a caller in a later step, and the final steps boot the ASGI
app with the real misc routes live and run the full gate.

Implementation language: **Python** (the design is grounded in concrete
`gateway_v2/gateway_v2/*.py` modules and the shipped house idiom). All code targets `mypy --strict`
+ `ruff` clean.

### Conventions enforced in EVERY coding task

- **Property tests**: a seeded `random.Random` driven for **≥10,000 iterations**, **NO
  hypothesis**; async lifecycle paths via `asyncio.run`. Each property test is tagged
  `# Feature: chat-surface-gw16-misc, Property N: <text>` and `# Validates: Requirements X.Y`,
  with exactly one property ↔ one property-based test.
- **Value types** are frozen slotted dataclasses or `StrEnum`; module-level tables use
  `MappingProxyType`. No module-level mutable — both route tables are built function-local in
  `build_routes`; the moderation category map and the envelope entries are `MappingProxyType`.
- **No capacity literal outside `runtime/resources.py`** — the `400`/`404` statuses are
  response-status codes and the new value-code strings are codes, not capacity-position literals;
  `edge` embeds no capacity literal.
- **`egress` MUST NOT import `detect`** — the input detector, output resolver, `ProviderClient`,
  and `Catalog_Source` are injected into `MiscRoute` in `edge` (the top layer) and threaded down.
- **Fail-closed everywhere**; `PerRequestExports.fail_open_total` is pinned at zero and never
  incremented.
- **Channel totality (C21)**: `unscanned_channels()` must stay empty — a `TEXT_CHANNELS` entry
  with no folding path is a build-time gate failure. GW15's `_assert_fold_total()` (chat scope,
  `messages[]`) and GW16's `_assert_misc_fold_total()` (misc scope, `input`/`input[]`) are
  disjoint by scope and both pass.
- **`check_sizes`**: `edge/wire/openai.py` and `edge/wire/openai_misc.py` each stay under 800
  lines (`MAX_MODULE_LINES = 800`) — the whole reason the misc typing is a sibling (Decision 3).
- Each task references its requirement sub-clauses and the design section/property it satisfies
  (traceability), matching the GW15 `tasks.md` style.

## Tasks

- [x] 1. New scannable input channels (C21 foundation) — `domain/channels.py` (MODIFY, bottom of the build order)
  - [x] 1.1 Add the `embeddings_input` and `moderations_input` scanned channel entries
    - Modify `gateway_v2/gateway_v2/domain/channels.py`: add two `TextChannel(scanned=True)`
      entries, `embeddings_input` and `moderations_input`, each with a DISJOINT `sdk_path`
      (`input` / `input[]`) that does NOT start with `messages[]` or `choices[]`
    - Keep GW15's `_input_channel_names()` (which filters on the `messages[]` prefix) unchanged so
      its chat-scoped totality check still passes untouched; the misc inputs are a NEW scope
    - Both entries are `scanned=True` so `unscanned_channels()` stays empty (R3.3)
    - _Design: Misc input-channel folding model (C21), Grounding (channel enumeration), Data Models (TextChannel)_
    - _Requirements: 3.1, 3.3_

  - [x]* 1.2 Write channel-enumeration smoke test
    - `tests/domain/test_gw16_channels.py`: assert `embeddings_input` + `moderations_input` are
      present in `TEXT_CHANNELS` with `scanned=True`, their `sdk_path` does NOT start with
      `messages[]`/`choices[]`, and `unscanned_channels()` is empty
    - _Design: Testing Strategy (SMOKE — TEXT_CHANNELS entries), Requirements Coverage (3.1, 3.3)_
    - _Requirements: 3.1, 3.3_

- [x] 2. Misc OpenAI codec — `edge/wire/openai_misc.py` (NEW, sibling under 800 lines — Decision 3)
  - [x] 2.1 Add the two new declared value-codes and the misc request types
    - Create `gateway_v2/gateway_v2/edge/wire/openai_misc.py`; import `MalformedRequest`,
      `MALFORMED_BODY`, `MALFORMED_NOT_AN_OBJECT`, `MALFORMED_WRONG_SHAPE` from
      `edge/wire/openai.py` (REUSE, never redefine — R2.6)
    - Declare `MISSING_REQUIRED_PARAMETER = "missing_required_parameter"` and
      `MODEL_NOT_FOUND = "model_not_found"` as plain module-level string constants (codes, not HTTP
      objects, not capacity literals — Decision 1)
    - Frozen+slotted `MiscEmbeddingsRequest(model: str, input_items: tuple[str, ...])` and
      `MiscModerationsRequest(model: str | None, input_items: tuple[str, ...])` and
      `ModerationResult(flagged, categories, category_scores)`
    - _Design: Components §1, Data Models, Decision 1_
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6, 7.3, 9.1_

  - [x] 2.2 Implement `MiscRequestParser.parse_embeddings` / `parse_moderations`
    - `MiscRequestParser` pure over the request bytes (no I/O, no security decision — R2.5):
      decode (reusing the GW15 decode discipline), validate the OpenAI-compatible shape, extract
      `model` + the ordered `input` items (string → one item; list → one item per element in
      request order)
    - A bad / off-contract body raises `MalformedRequest` with a declared `MALFORMED_*` code; a
      moderations body missing `input` raises `MalformedRequest(MISSING_REQUIRED_PARAMETER)` (R2.4);
      never a raw framework error (R2.3)
    - _Design: Components §1, Error Handling table (malformed body, missing input)_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 2.3 Implement `fold_misc_input` + import-time `_assert_misc_fold_total` (C21)
    - `fold_misc_input(items, channel) -> tuple[tuple[str, str], ...]` emits one `(channel, item)`
      Scannable_Text pair per input element, in request order (R3.2)
    - `_assert_misc_fold_total()` (same import-time pattern as GW15's `_assert_fold_total`) compares
      the misc fold's emittable channel names against the misc-scoped `TEXT_CHANNELS` names (those
      NOT prefixed `messages[]` and NOT prefixed `choices[]`); a misc input channel with no misc
      folding path — or a misc folding path with no `TEXT_CHANNELS` entry — raises `ChannelFoldError`
      at import, DISJOINT from GW15's chat-scoped check
    - _Design: Misc input-channel folding model (C21), Components §1_
    - _Requirements: 3.2, 4.1, 8.1_

  - [x] 2.4 Implement `MiscResponseRenderer` + the moderation category map
    - `MiscResponseRenderer` pure wire logic (R2.5), fixed key order + `ensure_ascii=True` +
      compact separators so renders are byte-stable and round-trip:
      `embeddings_body` → `{object:"list", data:[{index, embedding, object:"embedding"}], model, usage}`
      with one `data` entry per input item, each `index` matching the input position (R6.2/R6.3);
      `models_list_body` → `{object:"list", data:[{id, object:"model", ...}]}` in catalog order;
      `model_retrieve_body` → `{id, object:"model", ...}`;
      `moderation_body` → `{id:"modr-…", model, results:[{flagged, categories{…}, category_scores{…}}]}`
    - Add the firewall-`Category` → OpenAI-moderation-category map as a `MappingProxyType` (no
      module-level mutable): stock categories map directly; a firewall-specific `Category` with no
      stock counterpart rides as a category EXTRA named by the firewall `Category` spelling (R8.4)
    - _Design: Components §1 & §3 (moderation category mapping), Data Models (ModerationResult)_
    - _Requirements: 6.2, 6.3, 7.1, 7.2, 8.2, 8.3, 8.4_

  - [x]* 2.5 Write property test for misc parse/render round-trip
    - `tests/edge/test_gw16_codec.py` — **Property 2: Misc parse/render round-trip preserves input
      items and model**
    - **Validates: Requirements 2.1, 2.2**; seeded `random.Random` ≥10,000 iters, no hypothesis:
      parsing a well-formed embeddings/moderations body then rendering the response preserves every
      `input` item byte-for-byte in request order and preserves `model`
    - _Design: Correctness Properties → Property 2_

  - [x]* 2.6 Write property test for malformed misc body → declared code
    - `tests/edge/test_gw16_codec.py` — **Property 3: Malformed misc body yields a declared
      value-code**
    - **Validates: Requirements 2.3**; ≥10,000 iters over random malformed bodies (not JSON, not an
      object, wrong-typed `input`/`model`): the parser raises `MalformedRequest` with a declared
      value-code, never a raw framework error
    - _Design: Correctness Properties → Property 3_

  - [x]* 2.7 Write property test for misc input fold totality + order (C21)
    - `tests/edge/test_gw16_fold.py` — **Property 4: Misc input fold is total and ordered (C21)**
    - **Validates: Requirements 3.2, 4.1, 8.1**; ≥10,000 iters, canary iterated over EVERY input
      item position: a canary placed in any item appears as its own `(channel, text)` pair in the
      misc fold — one pair per element, in request order
    - _Design: Correctness Properties → Property 4_

  - [x]* 2.8 Write property test for embeddings batch index order
    - `tests/edge/test_gw16_codec.py` — **Property 11: Embeddings response preserves batch index
      order**
    - **Validates: Requirements 6.2, 6.3**; ≥10,000 iters over random input lists + provider
      results: the rendered body has exactly one `data` entry per input item, each `index` equals
      its input position, entries appear in request order (index-order round trip)
    - _Design: Correctness Properties → Property 11_

  - [x]* 2.9 Write property test for models-list rendering
    - `tests/edge/test_gw16_codec.py` — **Property 12: Models list renders every allowed model**
    - **Validates: Requirements 7.1**; ≥10,000 iters over random catalogs: the body has
      `object == "list"` and exactly one `data` entry per catalog model, each with `id` and
      `object == "model"`, in catalog order
    - _Design: Correctness Properties → Property 12_

  - [x]* 2.10 Write property test for moderation category fidelity
    - `tests/edge/test_gw16_codec.py` — **Property 14: Moderation findings map to categories with
      fidelity**
    - **Validates: Requirements 8.2, 8.3, 8.4**; ≥10,000 iters over random finding sets (varying
      `Category`/`status`/`confidence`): each EXECUTED finding sets its mapped category flagged with
      its score — a stock category when one exists, else a firewall-specific EXTRA — and overall
      `flagged` is true exactly when ≥1 category is flagged
    - _Design: Correctness Properties → Property 14_

  - [x]* 2.11 Write malformed-body and firewall-extra unit tests
    - `tests/edge/test_gw16_codec.py`: moderations `{}` → `MalformedRequest(MISSING_REQUIRED_PARAMETER)`
      (2.4); a non-stock firewall category rendered as a moderation category extra; byte-stable
      render idiom spot checks
    - _Design: Testing Strategy (unit/example — 2.4, firewall extra)_
    - _Requirements: 2.4, 8.4_

- [x] 3. Error envelope entries — `edge/errors.py` (MODIFY, Decision 1)
  - [x] 3.1 Add the two `_ENVELOPE` entries
    - Modify `gateway_v2/gateway_v2/edge/errors.py`: add `"missing_required_parameter": (400,
      "invalid_request_error")` and `"model_not_found": (404, "not_found_error")` to the
      `_ENVELOPE` `MappingProxyType` (the single render authority)
    - `render_http` already reads `_ENVELOPE.get(code)` + builds `{"error":{message,type,code}}`
      with the mapped status/type; the `400`/`404` are response statuses, not capacity literals;
      nothing else in `edge/errors.py` changes
    - _Design: Components §6 (envelope entries), Decision 1, Error Handling table_
    - _Requirements: 7.3, 9.1_

- [x] 4. Catalog seam types — `edge/app.py` (MODIFY, catalog types + default)
  - [x] 4.1 Add `CatalogSource` Protocol, `CatalogModel`, and `empty_catalog_source`
    - Modify `gateway_v2/gateway_v2/edge/app.py`: add a `@runtime_checkable` `CatalogSource`
      Protocol (`catalog_for(identity: TenantIdentity) -> tuple[CatalogModel, ...]`), a frozen+slotted
      `CatalogModel(id: str, created: int, owned_by: str)`, and `empty_catalog_source` default
      returning `()` (a models list of none, every retrieve a `model_not_found`)
    - The real org/plan-derived catalog backend is a DEFERRED seam behind `CatalogSource` (R11.3) —
      do NOT scaffold a backend; the default is honest-empty, never a fabricated model
    - _Design: Components §7 (CatalogSource + boot wiring), Data Models (CatalogModel), File list_
    - _Requirements: 7.4_

- [x] 5. The three Surface handlers — `edge/openai_misc.py` (FILLED, replaces `__all__ = ()`)
  - [x] 5.1 Implement `MiscRoute` injected-deps value
    - Fill `gateway_v2/gateway_v2/edge/openai_misc.py`: frozen+slotted `MiscRoute` carrying
      `parser`, `renderer`, `identity` (`IdentityResolver`, fail-closed default), `admission`
      (`AdmissionDecider`, fail-closed), `quota` (`BudgetDecider`, fail-closed), `input_detector`
      (`InputDetector`), `resolver` (`OutputResolver`, C22), `provider` (`ProviderClient`),
      `router` (`DispatchRouter`), `transform` (`Transform`), `catalog` (`CatalogSource`, default
      `empty_catalog_source`), `clock`, `exports` (`PerRequestExports`)
    - Built once at boot; detector/resolver/provider/catalog injected HERE (edge, above detect) so
      `egress` never imports `detect` (R4.5); no module-level mutable, no capacity literal
    - _Design: Components §2 (MiscRoute), Data Models (MiscRoute), Critical layer constraint_
    - _Requirements: 1.5, 4.5_

  - [x] 5.2 Implement `EmbeddingsSurface.handle` — full billable lifecycle with the BLOCK gate
    - `EmbeddingsSurface(deps=MiscRoute)`: parse via `deps.parser.parse_embeddings` (a
      `MalformedRequest` → declared `refuse_code` via the envelope); then the ordered lifecycle
      identity → admission → budget → input scan → provider, each phase returning early with a
      declared `refuse_code` (fail closed) BEFORE `provider.open`
    - Identity via `deps.identity.resolve(scope)` (`None`/stale/unavailable ⇒
      `shared_state_unavailable`, zero provider calls — R5.1/R5.2); `deps.admission.admit(...)` (a
      `ShedVerdict` ⇒ `overload_shed` + retry, zero calls — R5.3); `deps.quota.evaluate(cost,
      request_id)` on the local lease, no request-path store read (a `BudgetVerdict` ⇒
      `budget_unavailable`, zero calls — R5.4/R5.5)
    - INPUT scan: `fold_misc_input` the `embeddings_input` items, run `deps.input_detector` over
      EACH item, resolve via `deps.resolver.decide` (C22 `most_restrictive`); a BLOCK ⇒
      `input_blocked` and return BEFORE `provider.open` (zero provider calls — R4.1-R4.4); a scan
      raising ⇒ `scan_failure` fail-closed withhold (R4.6)
    - On proceed: `destination = deps.router.select(plan)`, `body = deps.transform.to_provider(...).body`,
      open the injected `ProviderClient`, read vectors from the `UpstreamEvent` stream (never compute
      vectors — R6.1), render with index order preserved via `renderer.embeddings_body` (R6)
    - Populate `exports` content-free on every terminal outcome, `fail_open_total` pinned 0 (R10)
    - _Design: Components §2, Per-surface lifecycle (embeddings), Error Handling table_
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.4, 10.1, 10.2, 10.3, 10.4_

  - [x] 5.3 Implement `ModerationsSurface.handle` — scan-only, no provider
    - `ModerationsSurface(deps=MiscRoute)`: parse via `deps.parser.parse_moderations` (missing
      `input` ⇒ `MISSING_REQUIRED_PARAMETER` → 400); resolve identity (`None` ⇒
      `shared_state_unavailable`); `fold_misc_input` the `moderations_input` items; run
      `deps.input_detector` over EACH item; map findings → categories via the renderer's category
      map; render `moderation_body` (R8.1-R8.4)
    - Open NO provider (R8.5); an uncomputable scan ⇒ `scan_failure` fail-closed, NO result body
      (never a `flagged:false` unscanned result — R8.6); populate `exports` content-free,
      `fail_open_total` 0
    - _Design: Components §2 & §3, Per-surface lifecycle (moderations), Error Handling table_
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 10.1, 10.3, 10.4_

  - [x] 5.4 Implement `ModelsSurface.handle_list` / `handle_retrieve` — catalog lookup, no scan, no provider
    - `ModelsSurface(deps=MiscRoute)`: resolve identity (`None` ⇒ `shared_state_unavailable` fail
      closed — R7.6); read the `Allowed_Model_Catalog` from `deps.catalog.catalog_for(identity)`
      (R7.4); `handle_list` renders `models_list_body` (R7.1); `handle_retrieve` extracts `{id}`
      from `scope["path"]` (`path[len("/v1/models/"):]`, pure string work — Decision 2), renders
      `model_retrieve_body` for a present id (R7.2), else `model_not_found` → 404 (R7.3)
    - Run NO input scan (no client text — R7.5) and open NO provider (R7.4); populate `exports`
      content-free, `fail_open_total` 0
    - _Design: Components §2, Per-surface lifecycle (models), Decision 2_
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 10.1, 10.3, 10.4_

  - [x]* 5.5 Write property test for a BLOCK embeddings input → zero provider calls
    - `tests/edge/test_gw16_embeddings_block.py` — **Property 5: A BLOCK embeddings input finding
      yields zero provider calls**
    - **Validates: Requirements 4.2, 4.3**; ≥10,000 iters, canary iterated over EVERY input item,
      counted `StubProviderClient`: a BLOCK-resolving canary refuses the request, makes zero
      provider calls, and renders the declared block value-code
    - _Design: Correctness Properties → Property 5_

  - [x]* 5.6 Write property test for C22 most-restrictive BLOCK
    - `tests/edge/test_gw16_embeddings_block.py` — **Property 6: BLOCK is the most-restrictive
      disposition (C22)**
    - **Validates: Requirements 4.4**; ≥10,000 iters over random finding sets with ≥1 BLOCK and
      co-matching REDACT/FLAG: the resolved per-finding outcome is BLOCK and the Embeddings_Surface
      refuses
    - _Design: Correctness Properties → Property 6_

  - [x]* 5.7 Write property test for uncomputable scan/decision fail-closed, FAIL_OPEN zero
    - `tests/edge/test_gw16_failclosed.py` — **Property 7: Uncomputable scan or decision fails closed
      with FAIL_OPEN pinned at zero**
    - **Validates: Requirements 4.6, 8.6**; ≥10,000 iters over every injected scan/decision fault on
      embeddings + moderations: the surface refuses (no raw forward, no unscanned moderation result),
      makes zero provider calls, and `PerRequestExports.snapshot().fail_open_total` stays zero
    - _Design: Correctness Properties → Property 7_

  - [x]* 5.8 Write property test for identity fail-closed
    - `tests/edge/test_gw16_identity.py` — **Property 8: Unresolvable or stale identity fails closed**
    - **Validates: Requirements 5.2**; ≥10,000 iters over every misc surface, counted
      `StubProviderClient`: an unresolvable identity or stale/unavailable freshness stamp renders
      the declared auth value-code and makes zero provider calls
    - _Design: Correctness Properties → Property 8_

  - [x]* 5.9 Write property test for pre-provider refusal short-circuit
    - `tests/edge/test_gw16_embeddings_block.py` — **Property 9: A pre-provider refusal
      short-circuits the provider**
    - **Validates: Requirements 5.3, 5.4**; ≥10,000 iters: an admission `ShedVerdict` or a
      `BudgetVerdict` renders the declared value-code and makes zero provider calls (counted
      `StubProviderClient`)
    - _Design: Correctness Properties → Property 9_

  - [x]* 5.10 Write property test for the single shared-state access
    - `tests/edge/test_gw16_lifecycle.py` — **Property 10: Exactly one shared-state access, none for
      the budget spend**
    - **Validates: Requirements 5.5**; ≥10,000 iters over every embeddings flow (shed/budget-refused/
      input-blocked/completed), instrumented identity/store seam: identity/plan is served from
      RAM/local lease and no request-path store read is attributable to the budget spend
    - _Design: Correctness Properties → Property 10_

  - [x]* 5.11 Write property test for unknown model id → not-found, zero provider calls
    - `tests/edge/test_gw16_models.py` — **Property 13: Unknown model id yields not-found with zero
      provider calls**
    - **Validates: Requirements 7.3**; ≥10,000 iters over random catalogs + unknown ids, counted
      `StubProviderClient`: an absent id renders `model_not_found` (HTTP 404, `not_found_error`) and
      makes zero provider calls, so the stock SDK raises `openai.NotFoundError`
    - _Design: Correctness Properties → Property 13_

  - [x]* 5.12 Write property test for content-free exports with FAIL_OPEN zero
    - `tests/edge/test_gw16_exports.py` — **Property 16: Every terminal outcome populates
      content-free exports with FAIL_OPEN zero**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**; ≥10,000 iters over every terminal misc
      outcome (allowed/shed/budget-refused/input-blocked/not-found/moderated/errored):
      `PerRequestExports` carries the admission/budget/input dimensions, `fail_open_total` stays
      zero, and no raw request/response content (folded input text, provider vectors) appears in any
      export field
    - _Design: Correctness Properties → Property 16_

  - [x]* 5.13 Write per-surface wiring/edge-case unit tests
    - `tests/edge/test_gw16_lifecycle.py`: embeddings lifecycle order → every pre-provider refusal
      zero calls (5.6); rendered vectors equal the stub's scripted vectors (6.1); retrieve a known
      id → `object == "model"`, id matches (7.2); instrumented provider/detector record zero calls
      on a models request (7.4/7.5); zero provider calls on moderations (8.5)
    - _Design: Testing Strategy (unit/example tests), Requirements Coverage_
    - _Requirements: 5.6, 6.1, 7.2, 7.4, 7.5, 8.5_

- [x] 6. Checkpoint — channels + codec + envelope + catalog + handlers green
  - Ensure all tests pass, ask the user if questions arise. Run `mypy --strict`, `ruff`,
    `lint-imports` (both contracts), and the 6 AST gates over `domain/channels.py`,
    `edge/wire/openai_misc.py`, `edge/errors.py`, and `edge/openai_misc.py`; confirm `check_sizes`
    holds (each `edge/wire` module under 800 lines) and `unscanned_channels()` is empty.

- [x] 7. Route-table wiring — `edge/routes.py` (MODIFY)
  - [x] 7.1 Replace the three `_placeholder` entries and add the `prefix_routes` table
    - Modify `gateway_v2/gateway_v2/edge/routes.py`: change `build_routes(chat)` →
      `build_routes(chat: ChatRoute, misc: MiscRoute) -> tuple[RouteTable, RouteTable]`
    - In the function-local exact table, replace the three misc `_placeholder` entries with the real
      handlers: `("POST","/v1/embeddings") → EmbeddingsSurface(deps=misc).handle`,
      `("GET","/v1/models") → ModelsSurface(deps=misc).handle_list`,
      `("POST","/v1/moderations") → ModerationsSurface(deps=misc).handle`
    - Keep `/v1/responses`, `/v1/mcp`, `/v1/rag/query`, `/v1/vector/query` as `_placeholder`
      (`not_implemented`) UNCHANGED (R1.3); keep the GW15 chat/completions entries untouched
    - Add the second function-local `prefix` table binding
      `("GET","/v1/models/") → ModelsSurface(deps=misc).handle_retrieve` (Decision 2); return
      `(exact, prefix)`; `emit_response` reused verbatim; no module-level mutable (R1.4)
    - _Design: Components §4 (build_routes wiring), Decision 2, File list_
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x]* 7.2 Write route-table wiring unit tests
    - `tests/edge/test_gw16_routes.py`: the three misc exact keys bind the real handlers (NOT
      `_placeholder`) (1.1); the `prefix` table binds `("GET","/v1/models/")` → retrieve (1.2);
      responses/mcp/rag/vector still resolve to `not_implemented` (1.3); both tables are
      function-local values of `build_routes`
    - _Design: Testing Strategy (unit/example — route table), Requirements Coverage (1.1, 1.3)_
    - _Requirements: 1.1, 1.3_

- [x] 8. ASGI matcher + boot wiring — `edge/app.py` (MODIFY, Decision 2)
  - [x] 8.1 Add the one prefix-match step in `_http` + `_match_prefix`, build `MiscRoute` at boot
    - Modify `gateway_v2/gateway_v2/edge/app.py`: add ONE declared step in `_http` AFTER the
      exact-match miss and BEFORE the `not_implemented` fallback — call `_match_prefix(method,
      path)`; on a hit, dispatch the prefixed handler (which reads `scope["path"]` for `{id}`);
      exact-match behaviour for every other route is unchanged
    - `_match_prefix(method, path)` — a small bounded method (under `check_sizes`) consulting the
      function-local `prefix_routes`; returns the longest `(method, prefix)` whose `prefix` is a
      proper prefix of `path` with a NON-EMPTY trailing segment, else `None` (so `/v1/models/`
      empty-suffix falls through to `not_implemented`); HTTP construction stays in `edge`
    - Build the single `MiscRoute` at boot the same way `ChatRoute` is built — inject the real
      detector/resolver/provider/catalog (or the stubs/`StubProviderClient`/`empty_catalog_source`
      in tests); update the `build_routes(chat, misc)` call site; hold both returned tables as
      `self.routes` + `self.prefix_routes`; no module-level mutable, no capacity literal
    - Keep the added method small — GW15 already split `app.py` once for `check_sizes`
    - _Design: Components §5 (_http prefix step) & §7 (boot wiring), Decision 2, File list_
    - _Requirements: 1.2, 1.4, 1.5_

  - [x]* 8.2 Write property test for `/v1/models/{id}` path-parameter matching
    - `tests/edge/test_gw16_app.py` — **Property 1: `GET /v1/models/{id}` path-parameter matching**
    - **Validates: Requirements 1.2**; ≥10,000 iters over random request paths: the matcher routes
      `/v1/models` to list, routes `/v1/models/<seg>` (non-empty `<seg>`) to retrieve with the
      extracted id equal to `<seg>`, and routes any other path away from the Models handlers (to its
      exact handler or `not_implemented`)
    - _Design: Correctness Properties → Property 1_

  - [x]* 8.3 Write property test for the single Error_Envelope rendering
    - `tests/edge/test_gw16_errors.py` — **Property 15: Every value-code renders through the single
      Error_Envelope**
    - **Validates: Requirements 9.1**; ≥10,000 iters: `missing_required_parameter` → (400,
      invalid_request_error), `model_not_found` → (404, not_found_error), every malformed-body code
      → its declared entry, and an unmapped code → the declared generic error with no raw internal
      detail
    - _Design: Correctness Properties → Property 15_

  - [x]* 8.4 Write matcher-edge-case and boot-wiring unit tests
    - `tests/edge/test_gw16_app.py`: the `_http` prefix step (exact code path); `/v1/models/`
      (empty suffix → `not_implemented`, not retrieve); an unmapped value-code → generic `500` body
      with no raw leak (R9.2); `build_app` builds one `MiscRoute` with every dep, accepts
      `StubProviderClient` + `empty_catalog_source` with no real socket/backend, and the misc routes
      resolve while responses/mcp/rag/vector stay `not_implemented`
    - _Design: Testing Strategy (edge cases), Scope boundaries (deferred seams)_
    - _Requirements: 9.2, 11.3_

- [x] 9. Checkpoint — routes + matcher + boot wired, misc surfaces live
  - Ensure all tests pass, ask the user if questions arise. Run `mypy --strict`, `ruff`,
    `lint-imports` (both contracts), and the 6 AST gates over `edge/routes.py` + `edge/app.py`;
    confirm `check_sizes` holds for the modified `app.py` method and `unscanned_channels()` is empty.

- [x] 10. Conformance harness extension (in-process subset; live gates deferred)
  - [x] 10.1 Extend the in-process conformance subset for the misc scenarios
    - Extend `gateway_v2/tests/openai_conformance/` for the misc scenarios against the handlers over
      `httpx.ASGITransport` against `build_app` with only `base_url` + API key swapped,
      `StubProviderClient` + a scripted/empty `CatalogSource` behind it: embeddings single + batch
      (index order preserved), models list + retrieve + unknown-id raising `openai.NotFoundError`,
      moderations benign + flagged (a firewall finding surfaced) + missing-input error, and a BLOCK
      on an embeddings input yielding zero provider calls (R11.1/R11.2)
    - Apply the LGW15-2 stop rule: any required client code change beyond `base_url` + key fails the
      acceptance (R9.3/R9.4). If a `conformance` extra / `pyproject` `addopts` narrowing is needed
      to enable the misc subset (as GW15 did), include that change here
    - Keep the real embedding provider socket, the real model catalog backend, and any real-TCP /
      live-SDK run (B3) as DEFERRED named seams — NOT scaffolded as un-runnable gates (R11.3);
      `StubProviderClient` + `empty_catalog_source` stay the in-process substitutes
    - _Design: Testing Strategy (Conformance), Scope boundaries (deferred live gates)_
    - _Requirements: 6.4, 9.3, 9.4, 11.1, 11.2, 11.3_

- [x] 11. Final verification gate
  - [x] 11.1 Run the full gate and reconcile
    - Run the full `gateway_v2` pytest suite + the `gw16` subset + the in-process conformance
      subset; `mypy --strict`; `ruff`; `lint-imports` (both contracts — the layer order holds,
      `egress` never imports `detect`); ALL 6 AST gates (`check_capacity_literals`,
      `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`,
      `check_tenant_scale`, `check_sizes`); and confirm `unscanned_channels()` is empty
    - Fix any gate failure before closing; the gate must be green
    - _Design: CI gates (R11.4), Testing Strategy_
    - _Requirements: 11.4_

- [x] 12. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks (property, unit, example) and can be skipped for
  a faster MVP; core implementation sub-tasks are never optional. The agent MUST implement un-starred
  sub-tasks and MUST NOT implement starred ones.
- Each task references specific requirement sub-clauses and the design section/property it satisfies
  for traceability; collectively the tasks cover all 11 requirements per the design's Requirements
  Coverage table.
- Checkpoints (tasks 6, 9, 12) ensure each layer's gates (`mypy --strict`, `ruff`, import-linter,
  the 6 AST gates incl. `check_sizes`, `unscanned_channels()` empty) are green before the next layer
  builds on it.
- Property tests use a seeded `random.Random` for ≥10,000 iterations, no hypothesis, async via
  `asyncio.run`; one property ↔ one test; tagged `# Feature: chat-surface-gw16-misc, Property N` and
  `# Validates: Requirements X.Y`. The channel/BLOCK properties (4, 5) iterate the canary over every
  input item position so a dropped misc-fold path fails the gate (C21).
- GW16 WIRES the shipped mechanisms (GW19 admission, GW06 budget lease, the input-scan path +
  `OutputResolver` C22, GW14d exports, GW05b/c identity + freshness, the injected `ProviderClient`)
  and REUSES the GW15 `edge/wire/openai.py` typing; it rebuilds none of them. `egress` never imports
  `detect`; the detector, resolver, provider, and catalog source are injected through `MiscRoute` in
  `edge`.
- Capacity literals live only in `runtime/resources.py`; the `400`/`404` statuses are
  response-status codes and the new value-code strings are codes, not capacity-position literals.
  The misc typing is a SIBLING module (`edge/wire/openai_misc.py`, Decision 3) so each `edge/wire`
  module stays under the `check_sizes` 800-line bound; the pipeline fails closed everywhere and
  `fail_open_total` stays zero.
- Deferred live gates — the real embedding provider socket, the real org/plan-derived model catalog
  backend, and the real serving entrypoint (B3) with its live-socket/`curl` testing — have NO
  implementation tasks; they stay named seams (`ProviderClient` / `CatalogSource` injected through
  `MiscRoute`), with `StubProviderClient` + `empty_catalog_source` as the in-process substitutes
  (task 10.1).
- Shared-file coordination (same as GW15): several optional test sub-tasks within a single wave
  write the same new test file — `test_gw16_codec.py` (2.5, 2.6, 2.8, 2.9, 2.10, 2.11),
  `test_gw16_embeddings_block.py` (5.5, 5.6, 5.9), `test_gw16_lifecycle.py` (5.10, 5.13), and
  `test_gw16_app.py` (8.2, 8.4). Implementation sub-tasks that write the SAME production module
  (`edge/wire/openai_misc.py`: 2.1→2.2→2.3→2.4; `edge/openai_misc.py`: 5.1→5.2→5.3→5.4) are
  serialized into distinct waves to avoid write conflicts; the co-waved test sub-tasks that append to
  one shared test file must coordinate on that file (append-only), exactly as GW15's concurrently
  written `test_lgw15_*` files did.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "3.1", "4.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3"] },
    { "id": 4, "tasks": ["2.4", "5.1"] },
    { "id": 5, "tasks": ["2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "5.2"] },
    { "id": 6, "tasks": ["5.3"] },
    { "id": 7, "tasks": ["5.4"] },
    { "id": 8, "tasks": ["5.5", "5.6", "5.7", "5.8", "5.9", "5.10", "5.11", "5.12", "5.13", "7.1"] },
    { "id": 9, "tasks": ["7.2", "8.1"] },
    { "id": 10, "tasks": ["8.2", "8.3", "8.4", "10.1"] },
    { "id": 11, "tasks": ["11.1"] }
  ]
}
```
