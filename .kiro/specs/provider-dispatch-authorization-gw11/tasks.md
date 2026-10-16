# Implementation Plan: Provider Dispatch Authorization (GW11)

## Overview

Convert the GW11 design (Dispatch_Core: the authorization + verification + error-mapping + pool-bound layer wrapping the already-shipped GW12 egress transport) into a series of prompts for a code-generation LLM that will implement each step with incremental progress. Each prompt builds on the previous ones and ends with wiring things together — no orphaned code. Focus is ONLY on tasks that involve writing, modifying, or testing code.

The build is bottom-up along the import-linter layer graph `domain → resolve → dispatch → lint/CI`, then tests, then the exit gate. The sealed `DispatchAuthorization` + `_Witness`/`_WITNESS` + `_mint` land in `domain` first (C1); the single `authorize()` mint site in `resolve` (C2); the four independent dispatch-core modules — verify (C4), catalogue (C5), provider-errors (C6), pool (C7) — compose into the one `AuthorizedProviderEntry` (C3); the provenance AST gate (C8), the fourth import-linter contract and CI wiring (C9) land alongside; the behavioral suite, the gate's own forgery-matrix test, and the LGW11-1 mypy-negative harness follow their targets; the exit gate runs last.

House conventions are preserved throughout (R12.4): `from __future__ import annotations`; frozen `@dataclass(frozen=True, slots=True)` value types; tuple `__all__`; no module-level mutable; no capacity literal outside `runtime/resources.py` (the pool bound is injected); the layer order; `mypy --strict`, `ruff`, `lint-imports`, and the AST gates green against the existing full-suite baseline (1819 passed / 95 skipped / 1 xfailed).

Package layout: the gateway package root is `gateway_v2/gateway_v2/` (so `domain`, `resolve`, `dispatch` live under it); the `lint/` gates, `tests/`, `pyproject.toml`, and `.github/workflows/gateway-v2.yml` sit at `gateway_v2/`. Module paths below follow the design's `gateway_v2/<layer>/...` naming.

## Tasks

- [x] 1. Sealed `DispatchAuthorization` + mint seam in the domain layer
  - Create `gateway_v2/domain/dispatch_authorization.py` with the module-private `_Witness` (`__slots__ = ()`) and its single `_WITNESS` instance; the frozen+slotted `DispatchAuthorization` value type (`plan_version: str`, `disposition: str`, `_witness: _Witness`) whose `__post_init__` identity-checks `self._witness is _WITNESS` and raises `ValueError` otherwise (the sealed constructor); and the module-private `_mint(plan_version, disposition)` that stamps `_WITNESS`.
  - Use `from __future__ import annotations`, a tuple `__all__` exporting `DispatchAuthorization` (keep `_Witness`/`_WITNESS`/`_mint` module-private, not in `__all__`).
  - Export `DispatchAuthorization` from `gateway_v2/domain/__init__.py` so both `resolve/` (to mint) and `dispatch/` (to require) import it from the lowest layer.
  - Verify `check_no_module_mutable` accepts the `_WITNESS = _Witness()` module-level singleton: the gate flags only `list`/`dict`/`set` literals and `list()/dict()/set()` calls, so a custom-class instance singleton (like `START`/`ZERO` precedent) is permitted — confirm against the gate and flag in Notes if any handling is needed.
  - _Requirements: 1.1, 1.2, 1.4, 1.6_

- [x] 2. `authorize()` mint site in the resolve layer
  - Create `gateway_v2/resolve/authorize.py` importing ONLY `gateway_v2.domain` (`Decision`, `Disposition`, and `DispatchAuthorization` + `_mint` from `domain.dispatch_authorization`).
  - Implement `authorize(decision: Decision) -> DispatchAuthorization | None`: return `None` when `decision.disposition is Disposition.BLOCK` (terminal → no object exists); otherwise mint via `_mint(plan_version=decision.plan_version, disposition=str(decision.disposition))` for ALLOW / FLAG / REDACT (including REWRITE→REDACT).
  - Re-export `authorize` from `gateway_v2/resolve/__init__.py`. This is the single legal mint call site; the provenance gate's allow-set top package is `resolve`.
  - _Requirements: 1.5, 4.2_

- [x] 3. Dispatch-core supporting modules (four independent files)
  - [x] 3.1 `apply_and_verify` + verification value types
    - Create `gateway_v2/dispatch/verify.py` with the frozen+slotted `VerifiedBytes` (`request: GatewayRequest`, `log: str`), the terminal `VerificationFailure(Exception)`, and `apply_and_verify(decision, request) -> VerifiedBytes`.
    - Apply `Decision.transformations` to the request bytes, then verify byte-level that every `finding.spans` entry under a redacting disposition is covered by a transformation and carries no surviving raw bytes; fail-closed (raise `VerificationFailure`) on any uncovered/partial span or surviving raw span. For REDACT, send only sanitized bytes and emit the verification log entry (LGW11-3).
    - Reuse the shipped `Transformation`/`Span` from `domain`; introduce NO new redactor (deferred to GW15). Mirror `egress/output_guard.py::apply_decision` byte-boundary discipline on the request side.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 12.1_
  - [x] 3.2 Snapshot catalogue + two distinct errors
    - Create `gateway_v2/dispatch/catalogue.py` with frozen+slotted `ProviderCatalogue` (`org_id: str`, `entries: tuple[CatalogueEntry, ...]`) and `CatalogueEntry` (`provider: str`, `model: str`, `timeout_s: float`, `context_band: int`); the distinct exceptions `EmptyCatalogueError` and `ControlPlaneOutageError`; an injectable `CatalogueSource` `Protocol`; and `resolve_catalogue(source, org_id) -> ProviderCatalogue`.
    - A store/control-plane blip raises `ControlPlaneOutageError`; a resolved-but-genuinely-empty catalogue raises `EmptyCatalogueError`. Neither error object carries a tenant-facing "connect your provider" message (edge renders codes). The two must be distinct classes, neither in the other's MRO.
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
  - [x] 3.3 C27 provider error mapping / timeouts / size bound / payload scan / over-band
    - Create `gateway_v2/dispatch/provider_errors.py` with the frozen `PROVIDER_STATUS_MAP: Mapping[int, GatewayErrorClass]` (`MappingProxyType`), the `GatewayErrorClass`/`GatewayError` value types, and `OVER_BAND_CODE = "context_length_exceeded"`.
    - Implement `map_provider_error(status, headers, body)` (400→400 preserving provider code; 429→429 preserving `Retry-After` + rate-limit headers; never a blanket 502); `provider_timeout_s(plan, provider, model)` (plan-declared per provider/model, no fixed 120 s); `bound_nonstream_body(reader, max_bytes)` (fail-closed over the bound; `max_bytes` passed in, no capacity literal here); `scan_error_payload(payload, scan_seam)` (scan before forward via an injected `detect/guard` seam); `over_band_error(history, model_band)` (incremental history scan → SDK-compatible `context_length_exceeded`, not 413); and the malformed-body path reporting `stream_malformed_upstream` (reused from `domain.posture`) never success.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 9.4_
  - [x] 3.4 Bounded, instrumented provider pool
    - Create `gateway_v2/dispatch/pool.py` with `ProviderPool` (`__slots__ = ("_bound", "_sem", "_wait_metric")`), `__init__(self, *, bound: int, wait_metric: WaitMetric)`, and an async `lease()` context manager that applies bounded queueing with backpressure (no exception storm) and records a non-negative `pool_wait_time` sample on every acquisition.
    - The bound arrives as an injected `int` (resolved from `ResourceContract.pool_size(PoolKind.PROVIDER)` at the edge wiring site); this module holds NO `maxsize=`/`Semaphore(N)` capacity literal so the capacity-literal gate stays green.
    - _Requirements: 9.1, 9.2, 9.3, 9.5_

- [x] 4. Single authorized provider entry + runtime assertion
  - Create `gateway_v2/dispatch/authorized.py` with `assert_authorized(auth)` (the Runtime_Assertion: `getattr(auth, "_witness", None) is not _WITNESS` → raise, before any provider byte) and the one `AuthorizedProviderEntry` class holding injected collaborators (`ProviderClient`, `Transform`, `DispatchRouter`, catalogue resolver, error mapper, `ProviderPool`, the `GuardBackend` scan/rewrite seam) in `__slots__` — no shared mutable request-path state.
  - Implement `async def dispatch(self, auth: DispatchAuthorization, *, decision, request, catalogue) -> AsyncIterator[UpstreamEvent]`: `auth` is a REQUIRED parameter (omission is a build-time `mypy --strict` error — LGW11-1 static half); call `assert_authorized(auth)` first; then `apply_and_verify` (terminal on failure); derive the destination via `DispatchRouter.select(plan)` (pure, no adjudicator call, no shared mutable state); transform to upstream; lease the bounded pool; drive the shipped `ProviderClient.open(...)` for ALLOW/FLAG/REDACT and route REWRITE through the injected local `GuardBackend` (never a provider generation call); map/scan each event via the C27 error map.
  - Front the shipped `@runtime_checkable` `ProviderClient` + `UpstreamRequest`/`UpstreamEvent` WITHOUT rewriting the GW12 transport; audit every started request on lease (including cancelled).
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 8.8, 10.6, 12.2, 12.3_

- [x] 5. Provenance AST gate
  - Create `gateway_v2/lint/check_dispatch_provenance.py` mirroring `gateway_v2/lint/check_http_outside_edge_resolve.py` (frozen violation dataclass, `scan_file`/`scan_tree`, `main(argv)` returning non-zero on any hit, `python -m lint.check_dispatch_provenance gateway_v2` runnable with `raise SystemExit(main())`).
  - Allowed top package: `resolve` (plus the gate's own test fixtures, excluded like the secret gate's allowlist). Flag every forgery form of `DispatchAuthorization` minting/forgery outside `resolve/`, tracking import aliases: direct construction, `_mint(...)` (incl. `import _mint as X`), `dataclasses.replace`, `object.__new__(DispatchAuthorization)`, `object.__setattr__`/attribute assign of `_witness`, `copy.copy`/`copy.deepcopy`, `type(auth)(...)`, and import-alias evasion (`import ... as X/Y`) — covering the prototype's 17-form class.
  - _Requirements: 10.1, 10.2, 10.3_

- [x] 6. Fourth import-linter contract + CI wiring
  - Edit `gateway_v2/pyproject.toml` to add a fourth `forbidden` import-linter contract "No HTTP framework outside edge": `source_modules` = every layer except `gateway_v2.edge`, `forbidden_modules = ["starlette", "fastapi"]` (trivially kept while both are absent from `[project.dependencies]`; the contract still asserts the intent per C11).
  - Edit `.github/workflows/gateway-v2.yml` to add `python -m lint.check_dispatch_provenance` to the structural-gates job loop over `gateway_v2` (and `state_control`, trivially clean).
  - _Requirements: 10.3, 10.4, 10.5_

- [x] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Main GW11 behavioral test suite (SINGLE FILE, ONE PASS)
  - [x] 8.1 Author the one behavioral suite `gateway_v2/tests/dispatch/test_provider_dispatch_authorization_gw11.py`
    - SINGLE-TEST-FILE: this file is written in one pass by this one task — do NOT split its cases across parallel sub-agents. All of the following live in this one file. Use the house idiom: seeded `random.Random` generators, ≥10,000 iterations for the genuine properties, NO `hypothesis`; mechanism/example tests for the rest. Tag each test `# Feature: provider-dispatch-authorization-gw11, Property N` + `# Validates: Requirements X.Y`.
    - Provide the recorder fake `ProviderClient` (satisfies the `@runtime_checkable` protocol like `StubProviderClient`, counts every provider call) and the injected-clock in-process concurrency harness used below.
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 2.3, 2.5, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 6.1, 6.4, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 9.2, 9.3, 9.4, 10.6, 11.1, 11.2, 11.3, 12.3_
  - [x]* 8.2 Properties 1–3: sealed authorization + mint-iff-non-terminal (same file, serialized)
    - **Property 1: Sealed constructor — no authorization constructible outside the mint** — **Validates: Requirements 1.1, 1.2**
    - **Property 2: `dataclasses.replace` on an authorization fails** — **Validates: Requirements 1.3**
    - **Property 3: Authorization minted iff decision non-terminal** — **Validates: Requirements 1.5, 4.2**
  - [x]* 8.3 Properties 4–5: runtime assertion + BLOCK zero-call via recorder (same file, serialized)
    - **Property 4: A forged authorization is rejected at the runtime assertion (recorder zero calls)** — **Validates: Requirements 1.6, 2.3, 2.5, 10.6**
    - **Property 5: BLOCK reaches no provider (recorder-proven, LGW11-2)** — **Validates: Requirements 4.1, 4.2, 4.3**
  - [x]* 8.4 Property 6: transform-and-verify against finding spans (same file, serialized)
    - **Property 6: Transformed bytes verify against finding spans, or nothing is sent (terminal on failure)** — **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
  - [x]* 8.5 Property 7: REWRITE via local GuardBackend, zero firewall calls (same file, serialized)
    - **Property 7: REWRITE runs on the local Guard_Backend, zero firewall-attributable provider calls (LGW11-4)** — **Validates: Requirements 5.1, 5.2, 5.3, 12.3**
  - [x]* 8.6 Property 8: deterministic plan routing (same file, serialized)
    - **Property 8: Routing is deterministic from plan identity, no shared mutable state** — **Validates: Requirements 6.1, 6.4**
  - [x]* 8.7 Property 9: distinct catalogue errors, LGW11-5 (same file, serialized)
    - **Property 9: Empty catalogue and control-plane outage are distinct and never confused** — **Validates: Requirements 7.2, 7.3, 7.4, 7.5**
  - [x]* 8.8 Properties 10–16: C27 error-map / timeout / size / scan / over-band / audit / malformed (same file, serialized)
    - **Property 10: Provider errors map to exact class with headers, never a blanket 502** — **Validates: Requirements 8.1, 8.2, 8.3**
    - **Property 11: Request timeout is the plan-declared per-provider/model value** — **Validates: Requirements 8.4, 9.5**
    - **Property 12: Non-streaming responses are size-bounded, fail-closed over the bound** — **Validates: Requirements 8.5**
    - **Property 13: Provider error payloads are scanned before forwarding** — **Validates: Requirements 8.6**
    - **Property 14: Over-band conversations return SDK-compatible context error, not 413** — **Validates: Requirements 8.7**
    - **Property 15: Every started request is audited, including cancelled ones** — **Validates: Requirements 8.8**
    - **Property 16: A malformed provider body is a protocol error, never success** — **Validates: Requirements 9.4**
  - [x]* 8.9 Property 17: bounded pool + exported wait time (same file, serialized)
    - **Property 17: The pool is bounded and exports its wait time** — **Validates: Requirements 9.2, 9.3**
  - [x]* 8.10 Property 19: LGW11-6 injected-clock concurrency (500 req × 20 orgs) (same file, serialized)
    - **Property 19: Concurrency produces no spurious provisioning errors and no shared-state corruption** — **Validates: Requirements 11.1, 11.2, 11.3**

- [x] 9. Provenance-gate forgery-matrix unit test
  - [x] 9.1 Author `gateway_v2/tests/gates/test_check_dispatch_provenance.py` (separate file, co-located with the other gate tests like the secret-headers gate test)
    - Unit-test the gate's `scan_tree` on a clean tree (zero hits) and one dirty fixture per enumerated forgery form outside `resolve/` (direct construction, `_mint` call, `dataclasses.replace`, `object.__new__`, `object.__setattr__`/attribute assign of `_witness`, `copy`/`deepcopy`, `type(auth)(...)`, import-alias `as X`/`as Y`, …) — asserting ≥1 hit per dirty fixture, achieving the 17/17 forgery-form coverage.
    - _Requirements: 10.2_
  - [x]* 9.2 Property 18: gate flags every forgery outside resolve/, passes a clean tree
    - **Property 18: The provenance gate flags every forgery form outside resolve/ and passes a clean tree** — **Validates: Requirements 10.1, 10.2**

- [x] 10. LGW11-1 static-type negative harness
  - Author `gateway_v2/tests/dispatch/test_gw11_mypy_negative.py` as its own small harness (distinct from the behavioral suite because the mypy-negative compile check is a distinct mechanism): a tiny fixture snippet that calls `AuthorizedProviderEntry.dispatch(...)` omitting the `auth` argument, run through `mypy --strict` in the test, asserting it fails type-checking (the static half of LGW11-1). Pick the cleanest form (invoke mypy on the snippet and assert a non-zero/`call-arg` result).
  - _Requirements: 2.2, 2.3_

- [x] 11. Structural / smoke / integration guard tests
  - [x]* 11.1 Smoke + integration guards (may live in the behavioral suite or a small sibling, implementer's choice)
    - Transport fronting (R2.4): inject `StubProviderClient`, assert `isinstance(client, ProviderClient)` and the entry drives the shipped transport unchanged.
    - No-adjudicator / no-shared-state (R6.2, R6.3): a fake adjudicator that fails if called; assert `DispatchRouter` carries no instance/module mutable state.
    - No-new-redactor / no-socket-rebuild (R12.1, R12.2): assert `apply_and_verify` imports `Transformation`/`Span` from `domain` and adds no redaction algorithm; assert the entry wraps the shipped transport.
    - Import-linter fourth contract (R10.4, R10.5): assert the contract exists in `pyproject.toml`.
    - Gate wiring (R10.3): assert `check_dispatch_provenance` exposes `main()`/`SystemExit` and is present in the structural-gates job of `.github/workflows/gateway-v2.yml`.
    - _Requirements: 2.4, 6.2, 6.3, 10.3, 10.4, 10.5, 12.1, 12.2_

- [x] 12. Exit gate — full verification sweep
  - Run the full offline suite and confirm it is ≥ baseline (1819 passed / 95 skipped / 1 xfailed) plus the new GW11 tests, 0 failed.
  - Run `mypy --strict` over the new modules; `ruff check gateway_v2 state_control lint tests`; `lint-imports` (now 4 contracts, all kept).
  - Run every AST gate over the real tree exiting 0, INCLUDING the new `python -m lint.check_dispatch_provenance gateway_v2`, plus `check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_sizes`, `check_http_outside_edge_resolve`.
  - Confirm `unscanned_channels() == ()`.
  - _Requirements: 8, 9, 10, 12.4_

- [x] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- **SINGLE-TEST-FILE collision lesson (every prior card).** The main GW11 behavioral suite (Task 8) is ONE Python file written in a single pass by that one task. Its enumerated `*` sub-items (Properties 1–17, 19; the recorder fake; the LGW11-2/4/5/6 mechanism tests; the C27 example matrix; the forgery-form behavioral cases) are serialized same-file work owned by Task 8 — do NOT split them across parallel sub-agents. The provenance-gate's own forgery-matrix test (Task 9, `tests/gates/`) and the LGW11-1 mypy-negative harness (Task 10) are legitimately separate files: the mypy-negative compile check is a distinct harness, and the gate's AST unit test co-locates with the other gate tests like the secret-headers gate test.
- **`_WITNESS` module-mutable note.** The `_WITNESS = _Witness()` module-level singleton is an immutable `__slots__ = ()` instance. `check_no_module_mutable` flags only `list`/`dict`/`set` literals and `list()/dict()/set()` calls (confirmed by reading the gate), so this singleton is permitted exactly as domain singletons like `START`/`ZERO` are. If a future change to the gate flagged it, resolve per the gate's existing singleton precedent — do not weaken the singleton.
- **Capacity literal.** The pool bound is injected as an `int` from `ResourceContract.pool_size(PoolKind.PROVIDER)` resolved at the edge wiring site; `dispatch/pool.py` and `dispatch/provider_errors.py` hold no capacity literal so `check_capacity_literals` stays green.
- **Honest C11 residual.** The sealed constructor closes naive construction only; the provenance gate (static) + runtime witness-identity assertion (dynamic) + recorder zero-call proof + the fourth forbidden contract are the enforceable stack. A determined `object.__new__` + `object.__setattr__` forgery inside an unscanned module that also obtains the private `_WITNESS` is the stated, not-provably-closed residual — matches C11's position.
- **Requirement 12 deferred boundaries (do NOT build here).** No real redactor (GW15 owns LGW11-3's real-redactor part; GW11 does verification-against-spans + sanitized dispatch only). No real provider-socket rebuild (GW12 `http_provider.py` transport exists; GW11 adds only the auth/verify/error-map/pool layer; live-socket conformance is a deferred live gate). REWRITE is local-only through `GuardBackend`, never a provider generation call.
- Tasks marked with `*` are optional (tests) and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirement sub-clauses for traceability; checkpoints ensure incremental validation.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2"] },
    { "id": 2, "tasks": ["3.1", "3.2", "3.3", "3.4", "5"] },
    { "id": 3, "tasks": ["4", "6"] },
    { "id": 4, "tasks": ["8.1", "9.1", "10", "11.1"] },
    { "id": 5, "tasks": ["8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "8.9", "8.10", "9.2"] }
  ]
}
```
