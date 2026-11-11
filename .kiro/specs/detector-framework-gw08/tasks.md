# Implementation Plan: Detector Framework + GuardBackend Interface (GW08)

## Overview

This plan fills the `gateway_v2.detect` layer with the GW08 detector framework: the `Detector`
protocol, the `GuardBackend` protocol and its frozen value types, reply-based readiness, four
backend slots (one working `local_onnx` CPU default + three honest declared seams), the registry
that resolves `required_detectors` at snapshot load, and `capacity_hint()` feeding the shipped
`ResourceContract`. The design uses concrete Python with full type annotations, so no implementation
language choice is required.

The production code is serialized into cohesive, same-layer tasks that respect the build
dependency: `detect/guard/backend.py` (the contract + value types + `ReadinessTracker`) must exist
before the backends, the `Detector` protocol, and the registry that reference it. The entire test
file is written in ONE pass by a single file-owning task — its property/example sub-tasks are
serialized under that one task and MUST NOT be dispatched to parallel sub-agents (the B6/B7/B8/B9
single-test-file-collision lesson).

## Tasks

- [x] 1. Implement the GuardBackend contract, value types, and ReadinessTracker (`detect/guard/backend.py`)
  - Fill the current 3-line stub `gateway_v2/gateway_v2/detect/guard/backend.py`
  - Define the `GuardBackend` `@runtime_checkable Protocol`: `async classify(windows: Sequence[str], budget: Budget) -> Sequence[GuardResult]`, `async readiness() -> Readiness`, `capacity_hint() -> CapacityHint`
  - Define frozen value types as `@dataclass(frozen=True, slots=True)`: `Budget(remaining_s: float)` with `derive(*, deadline_s, now_s)` classmethod (remaining duration, NOT a timestamp — C30; `now_s` from an injected clock); `GuardResult(window_index, category, score)` carrying no CPU/GPU/batch/replica field; `Readiness(ready, model_hash, last_ok_at, reason)`; `BackendCapacity(windows_per_second, queue_depth, batch_profile)` with `to_capacity_hint() -> CapacityHint`
  - Import and reuse the SHIPPED `gateway_v2.runtime.kinds.CapacityHint` (lower layer — legal import); do NOT redefine it
  - Implement the pure `ReadinessTracker(staleness_bound_s, manifest_hash)` with `record_ok(now_s, last_ok_at)` and `is_ready(*, now_s, last_ok_at, loaded_hash)` → ready iff `loaded_hash == manifest_hash` AND `last_ok_at is not None` AND `now_s - last_ok_at <= staleness_bound_s` (injected clock only; "thread alive" is never an input)
  - Declare no capacity numeric literal as a dataclass field and no module-level mutable binding
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 5.3, 5.4, 5.6, 5.7, 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 2. Implement the Detector protocol, DetectorContext, and domain re-exports (`detect/base.py`)
  - Fill the current 3-line stub `gateway_v2/gateway_v2/detect/base.py`
  - Define the `Detector` `@runtime_checkable Protocol` with `detect(text: str, context: DetectorContext) -> tuple[Finding, ...]`, a `detector_version: str` attribute, and a `required_capabilities: frozenset[str]` attribute
  - Document the hard rules in the protocol docstring: emit `Finding[]` only, never mutate `text`, never decide disposition, never raise HTTP, never import a higher layer, and `UNAVAILABLE` is a returned first-class status (never coerced to clean or omitted)
  - Define `DetectorContext` as `@dataclass(frozen=True, slots=True)` with `phase: Phase`, `rules: tuple[Rule, ...]`, `budget: Budget` (the budget type from Task 1)
  - Re-export `Finding`, `FindingStatus`, `Span` from `gateway_v2.domain.finding` without redefining them; import `Phase`/`Rule` from `gateway_v2.domain.plan`
  - Introduce no HTTP construct and import no `gateway_v2.edge`/`admit`/`plan` module
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 7.1, 7.2, 7.6, 7.7_

- [x] 3. Implement the four backend slots and the guard package exports (`detect/guard/{local_onnx,local_trt,triton_grpc,remote_http}.py` + `detect/guard/__init__.py`)
  - [x] 3.1 Implement the working `local_onnx` CPU default (`detect/guard/local_onnx.py`)
    - Fill the current 3-line stub; ship a real backend that computes `model_hash` from the bytes it actually loads (a bytes-hashed backing blob / deterministic CPU seam is acceptable — the real ONNX load/inference is a declared bytes-hashed seam) and compares it to a signed manifest
    - Drive a `ReadinessTracker` on an injected clock; produce a clean `EXECUTED` `GuardResult`/`Finding` ONLY when genuinely ready; never fabricate clean results
    - Report a `BackendCapacity` from a measured window rate and expose `capacity_hint()` deriving `CapacityHint.tokens_per_second` from `windows_per_second`
    - _Requirements: 3.1, 3.2, 3.3, 5.1, 5.2, 6.1_
  - [x] 3.2 Implement the three declared seams (`detect/guard/local_trt.py`, `detect/guard/triton_grpc.py`, `detect/guard/remote_http.py`)
    - Fill each 3-line stub as an honest seam: `readiness()` returns `ready=False` with a `reason` naming the deferral (e.g. `"deferred_gpu_backend"`, `"deferred_network_backend"`); `classify()` raises a clear deferred-backend error
    - Never pretend to be ready and never return a clean `EXECUTED` result
    - _Requirements: 3.1, 3.4, 5.2, 9.3_
  - [x] 3.3 Wire the guard package exports (`detect/guard/__init__.py`)
    - Export `GuardBackend`, `Budget`, `GuardResult`, `Readiness`, `BackendCapacity`, `ReadinessTracker`, and the four backend factories as the single public surface for the registry and the semantic detector
    - _Requirements: 3.1, 2.1, 2.2, 2.3_

- [x] 4. Implement the registry, config-only backend selection, and the detect public surface (`detect/registry.py` + `detect/__init__.py`)
  - Create the NEW module `gateway_v2/gateway_v2/detect/registry.py`
  - Define `UnknownDetectorError(KeyError)` and the immutable `_REGISTRY` registration map (`MappingProxyType`, detector id → `Callable[[GuardBackend], Detector]` factory) — no module-level mutable binding
  - Implement `resolve_required_detectors(required: frozenset[str], *, backend: GuardBackend) -> tuple[Detector, ...]` resolving EVERY id at snapshot load (sorted, deterministic), raising `UnknownDetectorError` naming any unresolved id; resolution is total-or-fail, never per-request and never a silent drop
  - Implement `select_backend(config: Mapping[str, str]) -> GuardBackend`: config-only `AMF_GUARD_BACKEND` → a backend factory; unset → `local_onnx` (the CI default); no source edit swaps a backend, and a swap changes the model identity (hence `model_hash`)
  - Update `detect/__init__.py` to export the public surface: `Detector`, `DetectorContext`, `resolve_required_detectors`, `select_backend` (and the guard surface as needed)
  - _Requirements: 3.5, 3.6, 4.1, 4.2, 4.3, 5.10, 7.5_

- [x] 5. Checkpoint - Ensure production modules import cleanly and types resolve
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Write the single GW08 test file (`gateway_v2/tests/detect/test_detector_framework_gw08.py`)
  - Confirm `gateway_v2/tests/detect/__init__.py` already exists (it does — no new package file)
  - Create ONE test file written in a single coherent pass; use the house async idiom (`def _run[T](coro: Awaitable[T]) -> T: return asyncio.run(coro)`), seeded `random.Random` (≥ 10,000 iterations, no `hypothesis`), and tag each property test `# Feature: detector-framework-gw08, Property N` / `# Validates: Requirements X.Y`, one property ↔ one test
  - **All sub-tasks below are delivered by this single file-owning task and MUST NOT be dispatched to parallel sub-agents — the whole file is written by one owner in one pass to avoid the single-test-file collision.**
  - _Requirements: 8.1, 8.7_
  - [x]* 6.1 Property test — not-ready backend yields UNAVAILABLE, never clean
    - **Property 1: A not-ready backend always yields UNAVAILABLE, never clean**
    - not-ready `FakeGuardBackend` + random text → all findings `UNAVAILABLE`, tuple non-empty, none `EXECUTED`
    - **Validates: Requirements 1.6, 5.1, 5.2**
  - [x]* 6.2 Property test — reply-based readiness under the injected clock
    - **Property 2: Readiness is reply-based and monotone-correct under the injected clock**
    - random reply/query schedules → `is_ready` True iff hash matches AND reply within bound; include the "alive but stale" case
    - **Validates: Requirements 5.3, 5.4**
  - [x]* 6.3 Property test — over-budget classify resolves to UNAVAILABLE within the bound
    - **Property 3: A classify that exceeds its Budget resolves to UNAVAILABLE within the bound**
    - random budgets + injected elapsed > budget → `UNAVAILABLE` within the bound, distinct from `EXECUTED`
    - **Validates: Requirements 5.5, 5.8**
  - [x]* 6.4 Property test — manifest mismatch always fails closed
    - **Property 4: A manifest mismatch always fails closed**
    - random byte blobs + mismatched manifest → not-ready / refuse, no substitute hash; matching → ready-eligible
    - **Validates: Requirements 5.6, 5.7**
  - [x]* 6.5 Property test — model identity is deterministic, distinguishing, cache-invalidating
    - **Property 5: Model identity is deterministic, distinguishing, and cache-invalidating**
    - random byte blobs → same bytes same hash, different bytes different hash; a hash change misses the old-keyed cache entry
    - **Validates: Requirements 3.3, 3.6, 5.10**
  - [x]* 6.6 Property test — capacity_hint reports a measured rate, not a constant
    - **Property 6: capacity_hint reports a measured rate, not a constant**
    - random measured `BackendCapacity` → `capacity_hint().tokens_per_second` tracks `windows_per_second` (monotone), never a fixed literal; wire into `ResourceContract._guard_pool`
    - **Validates: Requirements 6.1, 6.3**
  - [x]* 6.7 Property test — registry resolves totally or fails the load
    - **Property 7: The registry resolves totally or fails the load**
    - random id subsets resolve to exactly one `Detector` per id; random unknown ids raise at snapshot load naming the id (no silent drop, no per-request resolution)
    - **Validates: Requirements 4.1, 4.3**
  - [x]* 6.8 Property test — model_hash appears in every Finding and audit row
    - **Property 8: model_hash appears in every produced Finding and audit row**
    - random classify runs → every `Finding.detector_version == model_hash`, and the audit `finding_detail` row carries the same string
    - **Validates: Requirements 5.9**
  - [x]* 6.9 Property test — a detector leaves its input text unchanged
    - **Property 9: A detector leaves its input text unchanged**
    - random text → input byte-identical after `detect(text, context)`
    - **Validates: Requirements 1.3**
  - [x]* 6.10 Property test — value types round-trip
    - **Property 10: Value types round-trip**
    - random instances of `Budget`/`GuardResult`/`Readiness`/`CapacityHint` → `decode(encode(v)) == v`
    - **Validates: Requirements 8.7**
  - [x]* 6.11 Example/conformance tests — protocols, value types, factories, swap, registry reuse, topology-blindness
    - `Detector`/`GuardBackend` are `runtime_checkable Protocol`s and a conforming fake `isinstance`-passes; `Budget.derive` subtracts injected `now` and exposes no absolute-time field; `BackendCapacity.to_capacity_hint()` returns a `runtime.kinds.CapacityHint`
    - Four factories register under `local_onnx`/`local_trt`/`triton_grpc`/`remote_http`; unset config → `local_onnx` ready+serving; each seam → not-ready with a deferral reason + a deferred-error `classify`
    - LGW08-2 config-only swap: varying `AMF_GUARD_BACKEND` selects a different factory → different `model_hash` on findings and audit, no source edit
    - LGW08-3/4: a `FakeGuardBackend` drives not-ready→UNAVAILABLE and manifest-mismatch→not-ready; the registry resolves once per snapshot (counting factory) and reuses the tuple per request
    - The semantic-detector surface references none of CPU/GPU/TensorRT/Triton/batch/replica and imports only the `GuardBackend` protocol; `capacity_hint` feeds a `ResourceContract`
    - _Requirements: 1.1, 1.2, 2.1, 2.3, 2.5, 2.6, 2.7, 2.8, 3.1, 3.2, 3.4, 3.5, 3.6, 4.2, 7.2_
  - [x]* 6.12 Gate/AST integration tests
    - LGW08-5: the HTTP AST gate catches a PLANTED violation written to a temp `detect`-named module tree (never committed) and reports the real `detect/` clean
    - `check_frozen_dataclasses`, `check_no_module_mutable`, `check_capacity_literals`, `check_http_outside_edge_resolve` each exit 0 over the package; `detect` modules import no `gateway_v2.edge`/`admit`/`plan` and the legal `runtime.kinds` import is present; `unscanned_channels() == ()`
    - _Requirements: 7.4, 7.5, 7.6, 7.7, 7.8, 8.6_

- [x] 7. Run the GW08 exit gate
  - From cwd `gateway_v2` under `.venv`: run the full `pytest` suite — baseline 1730 passed / 95 skipped / 1 xfailed plus the new GW08 tests, zero failures
  - Run `mypy --strict` (success), `ruff check gateway_v2 state_control audit_control load_harness lint tests` (success), and `lint-imports` (all contracts kept — `detect` MAY import `runtime`; only the resolve-only extra contract constrains `resolve`)
  - Run each of the 6 AST gates and confirm each exits 0; evaluate `unscanned_channels()` and confirm it equals `()`
  - Fix any failures surfaced by the gate before completing
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and are serialized under their parent task.
- **The entire test file (Task 6 and all its `*` sub-tasks) is written by ONE file-owning task in a single pass and MUST NOT be split across parallel sub-agents** (the B6/B7/B8/B9 single-test-file-collision lesson). The property sub-tasks are same-file and serialized, never parallel.
- Build order is dependency-driven: `detect/guard/backend.py` (Task 1) ships the value types + protocol + `ReadinessTracker` first; `detect/base.py` (Task 2, `DetectorContext` carries `Budget`) and the backends (Task 3) follow; the registry (Task 4) resolves last; the test file (Task 6) follows all production; the gate (Task 7) is last.
- The corrected layer rule applies: `detect` imports no HIGHER layer (`edge`/`admit`/`plan`); importing `runtime.kinds.CapacityHint` is legal and intentional. Each task references specific requirement sub-clauses for traceability.

### Explicitly NOT tasks (deferred / out of scope — do NOT implement)

- **LGW08-1** (identical detection suite on all four backends) → deferred to GW15 (R9.1).
- **LGW08-6** (backend degrades to half throughput; admission bounds respond) → deferred to GW19 (R9.2).
- Real GPU `local_trt` / `triton_grpc`, real off-box `remote_http` transport, the real semantic guard model, and the engine/TensorRT caches → deferred to the GPU fleet / GW25 / cloud (R9.3, R9.4). The slots ship as honest declared seams only.
- **C43** push-coalescing owner-CPU microbenchmark and the **G-09** knee re-measure → deferred cloud evidence (R9.5); R2-23's local push-coalescing is already complete elsewhere.
- **GW09** deterministic detectors and **GW10** semantic detection / windowing / per-request FPR budget — the cards that USE this framework — are excluded (R9.6).

## Task Dependency Graph

Tasks 1, 2, and 4 are single-unit coding tasks (no decimal children), so they appear in the graph as their own leaf work. The Task 6 test sub-tasks all write the SAME single file, so they are serialized into one wave each (never parallel), reflecting that one owner writes the whole file.

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2", "3.1", "3.2"] },
    { "id": 2, "tasks": ["3.3"] },
    { "id": 3, "tasks": ["4"] },
    { "id": 4, "tasks": ["6.1"] },
    { "id": 5, "tasks": ["6.2"] },
    { "id": 6, "tasks": ["6.3"] },
    { "id": 7, "tasks": ["6.4"] },
    { "id": 8, "tasks": ["6.5"] },
    { "id": 9, "tasks": ["6.6"] },
    { "id": 10, "tasks": ["6.7"] },
    { "id": 11, "tasks": ["6.8"] },
    { "id": 12, "tasks": ["6.9"] },
    { "id": 13, "tasks": ["6.10"] },
    { "id": 14, "tasks": ["6.11"] },
    { "id": 15, "tasks": ["6.12"] }
  ]
}
```
