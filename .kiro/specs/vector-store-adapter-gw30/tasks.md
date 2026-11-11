# Implementation Plan: Vector Store Adapter (GW30)

## Overview

Convert the GW30 design into a series of code-generation steps that build the one-adapter-
interface-for-every-vector-store layer **bottom-up along the import-linter layer contract**
(`domain → runtime → dispatch → edge/injection site`), the same shape as the shipped SSE
egress (GW12), budget-lease (R2-09) and admission-control (R2-07/08) cards. Each layer is
validated by its own gates before the next layer builds on it, so there is no orphaned code:
every value type, bound, posture code, and seam is wired into a caller in a later step, and
the final live-gate task wires the real per-store transports behind the already-built adapter.
GW30 follows the shipped `dispatch/provider.py` idiom (injected `ProviderClient`,
`StubProviderClient`, frozen slotted value types) directly — the `Vector_Adapter` +
`Stub_Vector_Connector` are its analogues, never a rebuild.

Implementation language: **Python** (the design is grounded in concrete `gateway_v2/*.py`
modules and the shipped house idiom). All code targets `mypy --strict` + `ruff` clean.

### Scope boundary (what GW30 is NOT — no implementation tasks here)

- **No RAG routes, no RAG pipeline.** `/v1/rag/*` and `/v1/vector/*` and the four-stage RAG
  pipeline are **GW31**. GW30 ships only the adapter layer GW31 will consume.
- **No real per-store SDK/HTTP clients in the offline build.** The 13 named connectors are thin
  classes over an **injected transport**; the real network clients are a **deferred live gate**
  exercised only by L30-1/L30-2/L30-3 (the final task). Offline unit + property tests inject the
  socket-free `Stub_Vector_Connector`.

### Conventions enforced in EVERY coding task

- **Property tests**: a seeded `random.Random` driven for **≥10,000 iterations**, **NO
  hypothesis**; async paths via `asyncio.run`. Each property test is tagged
  `# Feature: vector-store-adapter-gw30, Property N: <text>` and
  `# Validates: Requirements X.Y`, with exactly one property ↔ one property-based test. Files
  are named `tests/dispatch/test_lgw30_*.py` (property/unit), `tests/runtime/test_lgw30_*.py`,
  `tests/domain/test_lgw30_*.py`.
- **Value types** are frozen slotted dataclasses (`@dataclass(frozen=True, slots=True)`,
  `from __future__ import annotations`, tuple `__all__`). Collection fields are immutable tuples
  / `MappingProxyType`, never lists. No module-level mutable — the 13-connector table is a
  factory function, not a module-level dict.
- **No capacity literal outside `runtime/resources.py`** — all timeouts/size bounds derive from
  `ResourceContract` and fail closed (`CapacityUnavailable` / `CapacityUnset`).
- **`dispatch` stays the adapter home; the `Vector_Adapter` and `Credential_Source` are injected
  from `edge`/`dispatch` and NEVER imported by any layer below `dispatch`** (gated by
  `lint-imports`).
- **Fail-closed everywhere**; every failure is a declared `Vector_Posture_Code` on a frozen
  result value, never an unhandled exception to calling code, never an empty success.
- **Codes-only posture vocabulary** in `domain/posture.py`; `edge` owns any HTTP mapping (GW31).
- Each task references its requirement sub-clauses and the design section/property it satisfies.

## Tasks

- [x] 1. Posture codes — `domain/posture.py` (bottom of the build order, codes only)
  - [x] 1.1 Add the GW30 Vector_Posture_Codes
    - Add module-level `str` constants, each with a docstring, consistent with the existing
      `STREAM_*`/`*_UNAVAILABLE` naming: `VECTOR_OK`, `UNSUPPORTED_OPERATION`,
      `PROVIDER_UNREACHABLE`, `CREDENTIAL_UNAVAILABLE`, `STORE_NOT_CONFIGURED`,
      `CREDENTIAL_SCOPE_MISMATCH`, `ORG_IDENTITY_INVALID`, `VECTOR_CAPACITY_EXCEEDED`,
      `VECTOR_CAPACITY_UNSET`, `CREDENTIAL_LEAK_WITHHELD`
    - Codes only — no HTTP object, no rendering here; add each to the module `__all__`
    - _Design: Components §`domain/posture.py` (new Vector_Posture_Codes)_
    - _Requirements: 5.1, 6.1, 7.2, 8.3, 9.6, 10.2, 10.3, 11.3, 11.6_

  - [x] 1.2 Write unit tests for code distinctness
    - `tests/domain/test_lgw30_posture.py`: assert each code is a unique non-empty string with
      no duplicate spelling; assert `UNSUPPORTED_OPERATION != VECTOR_OK` and that
      `UNSUPPORTED_OPERATION` is not equal to any other GW30 code (R5.1); assert the full set
      is pairwise distinct
    - _Design: Error Handling (codes-vs-render boundary)_
    - _Requirements: 5.1, 6.1_

- [x] 2. Capacity authority — `runtime/resources.py` derivations
  - [x] 2.1 Add the GW30 `ResourceContract` accessors
    - Add module-level literals `_VECTOR_OP_TIMEOUT_MULT`, `_VECTOR_MAX_RESULT_COUNT` /
      `_VECTOR_MAX_RESULT_COUNT_MULT`, `_VECTOR_MAX_RESULT_BYTES_SHARE` (the ONLY module
      permitted capacity literals, gated by `lint/check_capacity_literals.py`)
    - Add `vector_operation_timeout_s()` = `p99_s × _VECTOR_OP_TIMEOUT_MULT`, raising
      `CapacityUnavailable` on a non-positive result and `CapacityUnset` when no timeout value
      is supplied (matching `_guard_pool`); `vector_max_result_count()` derived from
      `connection_budget()` clamped to `[1, 10_000]`; `vector_max_result_bytes()` derived from
      `per_worker_rss` clamped to `[1, 10_485_760]`; both fail closed below one
    - If the module would exceed its size gate, split the GW30 derivations into a mixin and note
      the split
    - _Design: Components §`runtime/resources.py` (GW30 capacity accessors)_
    - _Requirements: 6.4, 6.5, 11.1, 11.2, 11.4, 11.6_

  - [x] 2.2 Write unit tests for the derivations and fail-closed paths
    - `tests/runtime/test_lgw30_resources.py`: assert `vector_operation_timeout_s()` equals
      `p99_s × mult`; a non-positive derivation raises `CapacityUnavailable`; an unsupplied
      bound raises `CapacityUnset`; count clamps into `[1, 10_000]` and bytes into
      `[1, 10_485_760]`; no literal bound asserted outside the contract (read via the methods)
    - _Design: Testing Strategy (supporting unit tests)_
    - _Requirements: 6.5, 11.2, 11.6_

- [x] 3. Checkpoint — lower layers (domain + runtime) green
  - Ensure all tests pass, ask the user if questions arise. Run `mypy --strict`, `ruff`,
    `lint-imports`, and `check_capacity_literals`/`check_no_module_mutable` over `domain` and
    `runtime`.

- [x] 4. Adapter value types — `dispatch/vector_adapter.py` (data models)
  - [x] 4.1 Implement the frozen value types
    - `dispatch/vector_adapter.py`: `VectorRecord(id, vector: tuple[float, ...], metadata via
      MappingProxyType)`; `AdapterResult(code, records=None, count=None, collections=None,
      status=None, detail_ref=None)` with the `ok` property (`code == posture.VECTOR_OK`);
      `Capabilities(filters, namespaces, hybrid_search)` — three defined booleans; the six
      request value types `QueryRequest`, `UpsertRequest`, `DeleteRequest`, `ListRequest`,
      `CreateRequest`, `HealthRequest` (each carrying `org_id`, `store_id`, collection where
      applicable, immutable-tuple payloads, NO credential material)
    - Encode the **absent (None) vs empty (())** invariant: on any non-OK code `records is None`;
      a zero-record success is `code == VECTOR_OK` with `records == ()`; `count`/`collections`/
      `status` present only on the relevant success, `None` otherwise; `detail_ref` only ever a
      credential reference id
    - All `@dataclass(frozen=True, slots=True)`, `from __future__ import annotations`, tuple
      `__all__`
    - _Design: Data Models (AdapterResult, VectorRecord, request value types, Capabilities)_
    - _Requirements: 1.3, 1.6, 1.7, 2.1, 3.1, 5.4, 6.6_

  - [x] 4.2 Write unit tests for the absent-vs-empty invariant
    - `tests/dispatch/test_lgw30_values.py`: `AdapterResult(VECTOR_OK, records=())` has
      `ok is True` and `records == ()` (present, empty); any non-OK code with `records is None`
      has `ok is False`; the two are not equal; `Capabilities` leaves no flag undefined; request
      values are frozen (assignment raises) and hashable
    - _Design: Data Models (absent-vs-empty invariant)_
    - _Requirements: 1.6, 1.7, 3.1, 5.4, 5.5, 6.6_

- [x] 5. Credential value types — `dispatch/vector_credentials.py` (data models)
  - [x] 5.1 Implement `OrgCredential` and `CredentialResolution`
    - `dispatch/vector_credentials.py`: `OrgCredential(org_id, verified: bool, _secret, ref_id)`
      with `__repr__`/`__str__` overridden to redact the secret (`secret=<redacted>`) and **no**
      `to_dict`/`to_json`/`__getstate__` serialization method (a credential is never put at
      rest); `CredentialResolution(code, credential=None)` with the `ok` property
      (`credential is not None`), credential present only on success
    - Both `@dataclass(frozen=True, slots=True)`
    - _Design: Data Models (OrgCredential, CredentialResolution)_
    - _Requirements: 9.4, 9.5_

  - [x] 5.2 Write unit tests for redaction and non-serialization
    - `tests/dispatch/test_lgw30_credential_value.py`: `repr`/`str` of an `OrgCredential` never
      contain the secret substring; the type exposes no `to_dict`/`to_json`/`__getstate__`;
      `CredentialResolution.ok` is true only when a credential is present and false on every
      failure code
    - _Design: Testing Strategy (supporting unit tests)_
    - _Requirements: 9.4, 9.5_

- [x] 6. Adapter protocol — `dispatch/vector_adapter.py`
  - [x] 6.1 Implement the `Vector_Adapter` `@runtime_checkable` protocol
    - `@runtime_checkable class Vector_Adapter(Protocol)` declaring exactly the six async
      operations `query`, `upsert`, `delete`, `list_collections`, `create_collection`, `health`
      plus the `capabilities() -> Capabilities` accessor; mirror `ProviderClient`; the protocol
      lives in `dispatch` and is injected from `edge`/`dispatch`, never imported downward
    - _Design: Components §`dispatch/vector_adapter.py` (Vector_Adapter protocol)_
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 6.2 Write protocol-conformance unit tests
    - `tests/dispatch/test_lgw30_protocol.py`: a class implementing all six ops + `capabilities`
      is `isinstance(conn, Vector_Adapter)` true; a class missing any one of the six ops is
      non-conforming (`isinstance` false) and is rejected by the registry with a declared code
    - _Design: Testing Strategy (supporting unit tests)_
    - _Requirements: 1.1, 1.2_

- [x] 7. Stub connector — `dispatch/vector_connectors.py` (build early; later tests inject it)
  - [x] 7.1 Implement `Stub_Vector_Connector`
    - In-process, **socket-free** `Vector_Adapter` mirroring `StubProviderClient`: driven by a
      seeded `random.Random` **injected** at construction (never created internally) and a
      scripted result/error sequence returned strictly in script order; declares a
      `Capabilities` value from its script; opens no network socket, file handle, or other
      external I/O; `isinstance(stub, Vector_Adapter)` true
    - _Design: Components §`dispatch/vector_connectors.py` (Stub_Vector_Connector)_
    - _Requirements: 4.4, 4.5, 4.6_

  - [x] 7.2 Write the stub-determinism property test
    - `tests/dispatch/test_lgw30_stub.py` — **Property 6: Stub determinism**
    - **Validates: Requirements 4.5, 4.6**; seeded `random.Random`, ≥10,000 iters, no
      hypothesis: two stubs with the same seed + same script produce byte-identical outputs
      across all calls in script order; assert zero sockets / file handles opened
    - _Design: Correctness Properties → Property 6_

- [x] 8. Registry — `dispatch/vector_adapter.py` (exact-match, no silent fallback)
  - [x] 8.1 Implement `VectorRegistry`
    - Slotted, stateless (`__slots__`, no clock/no randomness) selector constructed with an
      **injected** `store_id -> Vector_Adapter` mapping (built in `edge`/`dispatch`; no
      module-level mutable table); `select(store_id)` returns the connector for that **exact**
      id or `store_not_configured` (routing to no connector, tenant state untouched); never
      selects by "any available"/load/round-robin — the v1 `_resolve_vector_client`
      anti-pattern is barred; a connector-returned `provider_unreachable`/`unsupported_operation`
      is returned **as-is**, no other connector invoked (at most one connector per operation)
    - _Design: Components §`dispatch/vector_adapter.py` (VectorRegistry)_
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 8.2 Write the no-silent-fallback property test
    - `tests/dispatch/test_lgw30_no_fallback.py` — **Property 1: No silent fallback**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 15.2**; ≥10,000 iters, seeded: a
      request against a specific `store_id` reaches only that connector and zero others; an
      unregistered id reaches zero connectors and returns `store_not_configured`; the failure
      returned by a connector is never retried against another connector
    - _Design: Correctness Properties → Property 1_

- [x] 9. Capability enforcement — `dispatch/vector_adapter.py` / connector op path
  - [x] 9.1 Implement capability-flag enforcement on operations
    - A `query` carrying a filter against a `capabilities().filters == False` connector returns
      `unsupported_operation` — it does NOT silently ignore the filter and returns NO results;
      the same unsupported path covers `namespaces` and `hybrid_search`; where the flag is true
      the filter/namespace/hybrid is applied and only matching results return; an operation the
      connector does not implement returns `unsupported_operation` naming the operation, with
      `records is None` (absent, never an empty success)
    - _Design: Components §`dispatch/vector_adapter.py` (Capabilities), Error Handling table_
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.6, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 9.2 Write the unsupported-operation-not-empty property test
    - `tests/dispatch/test_lgw30_unsupported.py` — **Property 3: Unsupported operation is not an
      empty success**
    - **Validates: Requirements 3.5, 5.2, 5.3, 5.4, 5.5, 15.5**; ≥10,000 iters, seeded: for every
      generated unsupported operation / unsupported-capability call the result `code ==
      unsupported_operation`, `records is None`, and the result is NOT equal to a `VECTOR_OK`
      result with zero records
    - _Design: Correctness Properties → Property 3_

- [x] 10. Credential source + scope guard — `dispatch/vector_credentials.py`
  - [x] 10.1 Implement the `Credential_Source` seam and the scope guard
    - `@runtime_checkable class Credential_Source(Protocol)` with `async resolve(org_id) ->
      CredentialResolution`, injected from `edge`/`dispatch`, never imported downward; a stub
      source for offline tests; `resolve` reads the per-org plan key `{rv2}:plan:{org}` within
      the 5000 ms bound and returns exactly one `OrgCredential` scoped to that `org_id`
    - Fail closed: missing/empty `org_id` → `credential_unavailable` (no store call, state
      unchanged); control plane unreachable within 5000 ms → `credential_unavailable`; resolved
      credential not verified → `credential_unavailable`; a missing/empty request `org_id` →
      `org_identity_invalid`; the scope guard refuses with `credential_scope_mismatch` **before
      any backing-store call** when the resolved credential's `org_id` ≠ the request's `org_id`;
      the connector embeds no static/process-wide-default credential and carries no credential
      state shared across different `org_id` values
    - _Design: Components §`dispatch/vector_credentials.py` (Credential_Source, scope guard)_
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 10.2 Write the org-credential-isolation property test
    - `tests/dispatch/test_lgw30_org_isolation.py` — **Property 4: Org-credential isolation**
    - **Validates: Requirements 8.2, 10.1, 10.2, 10.4, 10.6, 15.6**; ≥10,000 iters, seeded: for
      every distinct `org_id` pair an operation for one `org_id` uses only that `org_id`'s
      credential and zero credentials of any other `org_id`; a resolved-vs-request mismatch is
      refused with `credential_scope_mismatch` before any store call (assert zero store calls on
      the mismatch path)
    - _Design: Correctness Properties → Property 4_

- [x] 11. Pre-emission leak guard — `dispatch/vector_credentials.py` / emission boundary
  - [x] 11.1 Implement the credential leak guard
    - At the connector's single emission boundary (before a result is returned, a log line
      written, or an audit field contributed), scan the outgoing bytes for any **consecutive run
      of characters** of the active `OrgCredential` value (plaintext or encoded/partially-masked
      form reproducing one or more consecutive chars); on a hit **withhold the entire** result/
      log/audit value and emit `credential_leak_withheld`; diagnostic detail carries only a
      reference id, never secret material; no exported/retained record serializes a credential
    - _Design: Components §`dispatch/vector_credentials.py` (pre-emission leak guard)_
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 11.2 Write the no-credential-leak property test
    - `tests/dispatch/test_lgw30_no_leak.py` — **Property 2: No credential leak**
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.6, 15.3, 15.4**; ≥10,000 iters, seeded: for
      every generated operation AND declared error, zero credential values (plaintext or any
      encoding/partial-mask reproducing ≥1 consecutive char) appear in any result value, log
      line, or audit field; an error terminus carries only a reference id in its diagnostic
      detail
    - _Design: Correctness Properties → Property 2_

- [x] 12. Connector operation execution over the injected transport — `dispatch/vector_connectors.py`
  - [x] 12.1 Implement the bounded operation path
    - Bind `vector_operation_timeout_s()`, `vector_max_result_count()`, `vector_max_result_bytes()`
      from the `ResourceContract` (no literal in the connector module); map transport outcomes
      to `AdapterResult` codes: connection refused / connect-or-read timeout / transport error /
      upstream 5xx → `provider_unreachable` (never an empty success, no unhandled exception);
      no timeout supplied → refuse to start, return `provider_unreachable` (no unbounded wait);
      a bound exceeded mid-op → abort before any partial result, discard accumulated data,
      return `vector_capacity_exceeded`; a required bound unset at init → `vector_capacity_unset`,
      refuse to start; an op past the derived timeout is cancelled, resources released,
      `provider_unreachable` returned; validate the per-op record/id-set size bounds (R2) and
      return the specific declared failure code on each violation
    - _Design: Components §`dispatch/vector_connectors.py` + §`runtime/resources.py`, Error
      Handling table_
    - _Requirements: 2.2, 2.4, 2.6, 2.9, 2.10, 6.2, 6.3, 6.4, 6.5, 11.3, 11.5, 11.6_

  - [x] 12.2 Write the provider-unreachable-not-empty property test
    - `tests/dispatch/test_lgw30_unreachable.py` — **Property 5: Provider-unreachable is not an
      empty success**
    - **Validates: Requirements 6.2, 6.3, 6.4, 6.6**; ≥10,000 iters, seeded, async via
      `asyncio.run`: for every generated transport failure (refusal/timeout/transport/5xx) and
      every timeout past the derived bound the result `code == provider_unreachable`,
      `records is None`, is distinguishable by `code` alone from a zero-record success, and no
      unhandled exception reaches the caller
    - _Design: Correctness Properties → Property 5_

- [x] 13. The 13 named connectors — `dispatch/vector_connectors.py` (factory, no module mutable)
  - [x] 13.1 Implement the connector factory + capability table
    - A **factory function** (built and returned per call, NOT a module-level mutable dict)
      naming exactly the 13 stores and each one's declared capability flags per the design
      table: `pinecone`, `chroma`, `milvus`, `qdrant`, `weaviate`, `pgvector`, `elasticsearch`,
      `redis`, `mongodb_atlas`, `aws_opensearch_serverless`, `aws_bedrock_kb`,
      `gcp_vertex_vector`, `azure_ai_search`; each is a thin `Vector_Adapter` over the injected
      transport and `isinstance`-satisfies the protocol; a requested store id outside the 13 is
      rejected as unsupported and returns no connector instance; the real per-store transports
      are a DEFERRED live gate (not built here — stub-backed offline)
    - _Design: Components §`dispatch/vector_connectors.py` (13 named connectors), Scope boundary_
    - _Requirements: 4.1, 4.2, 4.3, 4.7_

  - [x] 13.2 Write unit tests for the connector table
    - `tests/dispatch/test_lgw30_connectors.py`: the factory returns exactly 13 named stores,
      none omitted and none duplicated (R4.1, R4.2); each `isinstance(conn, Vector_Adapter)`
      true (R4.3); each declares all three capability flags defined and matching the design
      table; a store id outside the 13 is rejected with no instance returned (R4.7); assert no
      module-level mutable table exists (the table is a factory)
    - _Design: Testing Strategy (supporting unit tests)_
    - _Requirements: 4.1, 4.2, 4.3, 4.7_

- [x] 14. Checkpoint — adapter layer green
  - Ensure all tests pass, ask the user if questions arise. Run `mypy --strict`, `ruff`,
    `lint-imports` (no `edge`/`dispatch`→downward connector/credential import edge; adapter stays
    in `dispatch`), and the AST gates `check_no_module_mutable`/`check_capacity_literals` over
    `dispatch`.

- [x] 15. Final offline verification gate
  - [x] 15.1 Run the full offline gate and reconcile evidence
    - Run the full `gateway_v2` pytest suite + the `lgw30` subset; `mypy --strict` on the touched
      modules; `ruff`; `lint-imports` (assert no `edge`/`dispatch`→downward connector/credential
      edge, adapter stays in `dispatch`, layer order intact); and ALL AST gates:
      `check_capacity_literals` (bounds only in `runtime/resources.py`),
      `check_no_module_mutable` (connector table is a factory, no module-level dict),
      `check_frozen_dataclasses`, `check_http_outside_edge_resolve`
    - Fix any gate failure before closing; record the recorded gate output as offline evidence
    - _Design: AST / lint gates, Testing Strategy_
    - _Requirements: 1.4, 4.1, 11.4_

- [x] 16. Live acceptance gates — L30-1 / L30-2 / L30-3 (real Docker/staging, real wire bytes)
  - [x] 16.1 Wire the real per-store transports and capture live exit evidence
    - **THE CARD DOES NOT EXIT ON UNIT TESTS ALONE.** Per the runbook, exit evidence is live
      Docker/staging behaviour, real wire bytes, and controlled-load measurement. This task
      wires the real per-store SDK/HTTP transports (the deferred live gate) BEHIND the
      already-built adapter/registry/credential seam — the offline layers are unchanged — and
      runs the three live acceptance tests, persisting evidence:
    - **L30-1** — run the same `query`/`upsert`/`delete` suite against every named connector
      with byte-identical equivalent inputs; confirm identical result sets (matching record ids,
      field values, counts) and identical **declared-error categories**; record the real wire
      bytes and per-operation outcome per connector; do not substitute unit results
      (_Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_)
    - **L30-2** — make a connector's backing store unreachable in live staging; observe
      `provider_unreachable` within 30 s, never an empty result labelled a clean search; persist
      the code, request id, timestamp (_Requirements: 13.1, 13.2, 13.3, 13.4_)
    - **L30-3** — present Org A's credential for Org B's request; observe refusal **before** any
      backing-store connection, zero backing-store operations for that request, a refusal
      identifying a credential-to-org mismatch, and no Org B payload; persist each refusal
      outcome for the cutover review (_Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_)
    - This task depends on real infrastructure and is acceptable as the last task.
    - _Design: Testing Strategy (live acceptance gates), Open Questions (deferred live gates)_
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 13.1, 13.2, 13.3, 13.4, 14.1, 14.2, 14.3, 14.4, 14.5_

- [x] 17. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks (property, unit) and can be skipped for a
  faster MVP; core implementation sub-tasks are never optional. The agent MUST implement
  un-starred sub-tasks and MUST NOT implement starred ones.
- Each task references specific requirement sub-clauses and the design section/property it
  satisfies for traceability.
- Checkpoints (tasks 3, 14, 17) ensure each layer's gates (`mypy --strict`, `ruff`, import-linter,
  AST gates) are green before the next layer builds on it.
- Property tests use a seeded `random.Random` for ≥10,000 iterations, no hypothesis, async via
  `asyncio.run`; one property ↔ one test; tagged
  `# Feature: vector-store-adapter-gw30, Property N` and `# Validates: Requirements X.Y`. The six
  design properties map: P1 → 8.2, P2 → 11.2, P3 → 9.2, P4 → 10.2, P5 → 12.2, P6 → 7.2.
- The adapter stays in `dispatch`; the `Vector_Adapter` and `Credential_Source` are injected from
  `edge`/`dispatch` and never imported downward. All capacity literals live only in
  `runtime/resources.py`. Every failure is a declared `Vector_Posture_Code` on a frozen result,
  never an empty success and never an unhandled exception.
- Requirement 2 (per-operation uniform results) is satisfied across the data-model task (4.1, the
  uniform `AdapterResult` shape and the `query` success in 2.1) and the bounded-execution task
  (12.1, the declared-failure codes for each operation). Requirement 15 (property-test obligation)
  is satisfied collectively by tasks 7.2, 8.2, 9.2, 10.2, 11.2, 12.2 (15.1/15.7 — seeded
  `random.Random`, ≥10,000 iters, no hypothesis, seed-reproducible — are enforced in every
  property-test sub-task).
- The real per-store SDK/HTTP transports are a DEFERRED live gate with NO offline implementation
  task; task 16.1 wires them behind the adapter and captures L30-1/L30-2/L30-3 live exit evidence.
  This is a GW card, NOT an R2 item: do NOT create the four-memory Ruflo/Cursor mirrors.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "2.2", "4.1", "5.1"] },
    { "id": 2, "tasks": ["4.2", "5.2", "6.1", "7.1"] },
    { "id": 3, "tasks": ["6.2", "7.2", "8.1", "10.1"] },
    { "id": 4, "tasks": ["8.2", "9.1", "10.2", "11.1"] },
    { "id": 5, "tasks": ["9.2", "11.2", "12.1", "13.1"] },
    { "id": 6, "tasks": ["12.2", "13.2"] },
    { "id": 7, "tasks": ["15.1"] },
    { "id": 8, "tasks": ["16.1"] }
  ]
}
```
