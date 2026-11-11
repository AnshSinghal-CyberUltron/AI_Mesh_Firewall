# Requirements Document

## Introduction

GW08 builds the **detector framework** and the **GuardBackend interface** for the gateway_v2 `detect` layer. The objective, stated verbatim in the runbook card (§10.9, `## GW08 — Build the detector framework and the GuardBackend interface`), is to *"Make detectors uniform, replaceable and incapable of deciding anything, and make the semantic backend a deployment choice."*

This removes two v1 defects. First, v1 detectors mutate the request body, return HTTP responses, short-circuit the pipeline, and disagree about the same concept's name. GW08 replaces them with a uniform `Detector` protocol that emits `Finding[]` and nothing else — it cannot import `edge`, cannot raise HTTP, cannot mutate its input, and cannot decide disposition. Second, v1 binds the semantic backend into scanner semantics, so changing accelerator topology means editing security code. GW08 unbinds the backend behind a `GuardBackend` protocol (§10.5.4) so that moving from one accelerator to forty is a configuration change plus a `capacity_hint` change, not a source edit; the detector cannot observe which backend is active.

GW08 ships ONLY the framework: the `Detector` protocol, the `GuardBackend` protocol and its frozen value types, the four backend slots (one working local CPU/fake implementation plus three declared seams), the registry, reply-based readiness with `UNAVAILABLE` safety, `capacity_hint()` feeding the ResourceContract, and the AST/layer gate. The deterministic detectors (GW09) and semantic detection/windowing/FPR budget (GW10) that USE this framework are out of scope.

### Grounding (verified against the tree, do NOT redefine)

- The `detect` layer imports ONLY `gateway_v2.domain`. Confirmed by the import-linter `layers` contract in `gateway_v2/pyproject.toml` (`gateway_v2.detect` sits above `gateway_v2.domain` and below `gateway_v2.edge`), and by `detect/windowing.py` which states the rule explicitly. A detector therefore cannot import `edge`.
- Shipped `Finding(detector, detector_version, category, status, confidence, spans, evidence)`, `FindingStatus(EXECUTED | SKIPPED | UNAVAILABLE)`, and `Span` live in `gateway_v2/gateway_v2/domain/finding.py`. GW08 builds on these; it does NOT redefine them.
- Shipped `ExecutionPlan.required_detectors: frozenset[str]` lives in `gateway_v2/gateway_v2/domain/plan.py` and is already derived at compile time by `plan/compiler.py`. The registry resolves these ids to `Detector` instances at snapshot load, NOT per request.
- `gateway_v2/gateway_v2/audit/record.py` already serializes `finding.detector_version` into the audit row (line ~111), so `model_hash` flows into findings and audit via `detector_version`.
- `runtime/resources.py` (GW03 `ResourceContract`) holds capacity literals and derives guard-dependent bounds; its `guard_capacity: CapacityHint | None` field is fed by `GuardBackend.capacity_hint()`.
- The HTTP AST gate `gateway_v2/lint/check_http_outside_edge_resolve.py` sets `ALLOWED_TOP = {"edge", "resolve"}`. `detect` is NOT in the allow-list, so an `HTTPException` / `JSONResponse` / `status_code=4xx` construct inside `detect/` makes the gate exit 1 — this is exactly what LGW08-5 tests.
- The detect modules GW08 fills are the current 3-line stubs: `detect/base.py`, `detect/guard/backend.py`, `detect/guard/local_onnx.py`, `detect/guard/local_trt.py`, `detect/guard/triton_grpc.py`, `detect/guard/remote_http.py`, `detect/guard/__init__.py`.

## Glossary

- **Detect_Framework**: The GW08 deliverable — the `Detector` protocol, the `GuardBackend` interface and value types, the backend slots, the registry, readiness, and `capacity_hint`, all living in the `gateway_v2.detect` layer.
- **Detector**: A protocol-conforming unit that takes text plus plan context and returns a `tuple[Finding, ...]`. It declares `required_capabilities` and a `detector_version` (string). It emits findings only — it cannot decide disposition, cannot import `edge`, cannot raise HTTP, and cannot mutate its input.
- **Finding**: The shipped frozen type `gateway_v2.domain.finding.Finding`. Carries `detector`, `detector_version`, `category`, `status`, `confidence`, `spans`, `evidence`.
- **FindingStatus**: The shipped `StrEnum` with members `EXECUTED`, `SKIPPED`, `UNAVAILABLE`. `UNAVAILABLE` is a first-class result and is never clean.
- **GuardBackend**: The protocol defined in §10.5.4: `async def classify(windows, budget) -> Sequence[GuardResult]`, `async def readiness() -> Readiness`, `def capacity_hint() -> CapacityHint`. The semantic detector calls it and knows nothing of CPU/GPU/TensorRT/Triton/batch size/replica count.
- **Budget**: A frozen value type introduced by GW08 representing the remaining time/credit a `classify` call may use. Frames carry the remaining budget, NOT a timestamp (C30).
- **GuardResult**: A frozen value type introduced by GW08 — the per-window classification that the semantic detector maps to `Finding`s.
- **Readiness**: A frozen value type introduced by GW08 reporting a ready/not-ready state and the `model_hash`. Readiness is reply-based: it requires recent successful guard replies (C35).
- **CapacityHint**: A frozen value type introduced by GW08 reporting the backend owner's measured service rate (windows/s), queue depth, and batch profile.
- **model_hash**: The identity of the model, computed from the bytes actually loaded and compared against a signed manifest. It appears in every `Finding` (via `detector_version`) and every audit record.
- **Registry**: The component that resolves `ExecutionPlan.required_detectors` ids to `Detector` instances at snapshot load, not per request.
- **local_onnx**: The CPU `GuardBackend` slot; the DEFAULT for development and CI. Ships a working local implementation.
- **local_trt / triton_grpc / remote_http**: The in-process TensorRT, node-local Triton, and off-box burst `GuardBackend` slots. Declared seams in GW08; real GPU/network implementations deferred.
- **ResourceContract**: The GW03 frozen type in `runtime/resources.py` whose `guard_capacity` field is derived from `capacity_hint()`.
- **HTTP_AST_Gate**: `gateway_v2/lint/check_http_outside_edge_resolve.py`, allow-list `{edge, resolve}`.
- **Layer_Contract**: The import-linter contracts in `gateway_v2/pyproject.toml` that keep `detect` importing only `domain`.
- **Gate**: The GW08 exit gate run from cwd `gateway_v2` under `.venv`: full pytest green (baseline 1730 passed / 95 skipped / 1 xfailed, plus new tests), `mypy --strict`, `ruff check gateway_v2 state_control audit_control load_harness lint tests`, `lint-imports`, the AST gates each exiting 0, and `unscanned_channels() == ()`.

## Requirements

### Requirement 1: Detector protocol

**User Story:** As a backend engineer, I want a uniform Detector protocol that emits findings only, so that detectors are replaceable and incapable of deciding, mutating, or short-circuiting the pipeline.

#### Acceptance Criteria

1. THE Detect_Framework SHALL define a Detector protocol in the `gateway_v2.detect` layer whose detection method accepts input text plus plan context and returns a `tuple[Finding, ...]`.
2. THE Detector protocol SHALL declare a `required_capabilities` attribute and a `detector_version` string attribute.
3. WHEN a Detector produces findings, THE Detector SHALL leave its input text unchanged.
4. THE Detector protocol SHALL be defined without importing the `gateway_v2.edge` layer.
5. THE Detector protocol SHALL be defined without any HTTP construct (`HTTPException`, `JSONResponse`, or `status_code` in the 400–499 range).
6. WHERE a Detector cannot execute, THE Detector SHALL emit a Finding with `status` `UNAVAILABLE` rather than omitting the finding or emitting a clean result.

### Requirement 2: GuardBackend protocol and value types

**User Story:** As a backend engineer, I want a GuardBackend protocol with frozen supporting value types, so that the semantic backend is a deployment choice that detectors cannot observe.

#### Acceptance Criteria

1. THE Detect_Framework SHALL define a GuardBackend protocol with an async `classify` method accepting a window sequence and a Budget and returning a `GuardResult` sequence.
2. THE GuardBackend protocol SHALL define an async `readiness` method returning a Readiness that includes `model_hash`.
3. THE GuardBackend protocol SHALL define a synchronous `capacity_hint` method returning a CapacityHint.
4. THE Detect_Framework SHALL introduce frozen value types Budget, GuardResult, Readiness, and CapacityHint as `@dataclass(frozen=True, slots=True)` types.
5. THE Budget value type SHALL represent the remaining time or credit a `classify` call may use and SHALL carry remaining budget rather than a timestamp.
6. THE CapacityHint value type SHALL report measured windows per second, queue depth, and batch profile.
7. THE semantic detector module SHALL call the GuardBackend protocol without referencing CPU, GPU, TensorRT, Triton, batch size, or replica count.
8. WHILE the Detect_Framework is active, THE Detect_Framework SHALL expose no mechanism by which a Detector can observe which GuardBackend implementation is active.

### Requirement 3: The four backend slots and the local_onnx CI default

**User Story:** As a platform operator, I want four backend implementations behind one interface selected by configuration, so that changing accelerator topology is a config change rather than a security-code edit.

#### Acceptance Criteria

1. THE Detect_Framework SHALL provide four GuardBackend slots named `local_onnx`, `local_trt`, `triton_grpc`, and `remote_http`.
2. THE `local_onnx` backend SHALL ship a working local implementation that serves as the default for development and continuous integration.
3. THE `local_onnx` backend SHALL produce real Readiness, `model_hash`, and CapacityHint behaviour rather than fabricated clean results.
4. THE `local_trt`, `triton_grpc`, and `remote_http` backends SHALL be provided as declared seams whose real GPU, Triton, and network implementations are deferred.
5. WHEN a deployment selects a GuardBackend, THE Detect_Framework SHALL perform the selection through deployment configuration without a source edit.
6. WHEN the selected GuardBackend changes by configuration, THE Detect_Framework SHALL cause the `model_hash` reported in findings and audit records to change accordingly.

### Requirement 4: The detector registry

**User Story:** As a backend engineer, I want required detectors resolved to instances at snapshot load, so that detector resolution is not repeated per request.

#### Acceptance Criteria

1. WHEN a snapshot is loaded, THE Registry SHALL resolve each id in `ExecutionPlan.required_detectors` to a Detector instance.
2. THE Registry SHALL resolve `required_detectors` ids to Detector instances at snapshot load and SHALL NOT resolve them per request.
3. IF a `required_detectors` id cannot be resolved to a Detector instance, THEN THE Registry SHALL signal the unresolved id as an error at snapshot load.

### Requirement 5: Reply-based readiness and the UNAVAILABLE safety invariant

**User Story:** As a security engineer, I want an unavailable backend to make its detectors UNAVAILABLE and never clean, so that an outage or a hung backend can never fabricate a benign result.

#### Acceptance Criteria

1. WHILE a GuardBackend reports not-ready, THE Detect_Framework SHALL cause that backend's detectors to produce findings with `status` `UNAVAILABLE`.
2. WHILE a GuardBackend reports not-ready, THE Detect_Framework SHALL NOT cause that backend's detectors to produce a clean or benign EXECUTED result.
3. THE Readiness state SHALL be derived from recent successful guard replies.
4. IF a GuardBackend inference thread is hung while no recent successful reply has been received, THEN THE Readiness state SHALL report not-ready.
5. WHEN a GuardBackend owner is dead or wedged, THE Detect_Framework SHALL cause pending and new `classify` calls to resolve to `UNAVAILABLE` within a bounded time, measured against an injected clock.
6. WHEN the bytes actually loaded by a GuardBackend do not match the signed manifest, THE GuardBackend SHALL report not-ready and SHALL refuse to serve its detectors.
7. IF a GuardBackend engine cache is corrupt, THEN THE GuardBackend SHALL report not-ready and SHALL NOT present a silent substitute model as the selected model.
8. WHEN a GuardBackend is slow and a `classify` call exceeds its Budget, THE Detect_Framework SHALL produce a finding with `status` `UNAVAILABLE` distinct from a clean pass.
9. THE `model_hash` SHALL appear in every Finding via `detector_version` and in every audit record.
10. WHEN a GuardBackend `model_hash` changes under a running process, THE Detect_Framework SHALL treat the new hash as a new version and SHALL invalidate caches keyed by the previous hash.

### Requirement 6: capacity_hint feeds the ResourceContract

**User Story:** As a platform operator, I want guard-dependent bounds derived from the backend's measured service rate, so that capacity is read from the environment rather than hardcoded.

#### Acceptance Criteria

1. THE GuardBackend `capacity_hint` method SHALL report the backend owner's measured service rate rather than a constant.
2. WHEN the ResourceContract derives a guard-dependent bound, THE ResourceContract SHALL derive it from the CapacityHint rather than from a numeric literal in a capacity position.
3. WHEN a GuardBackend's measured service rate changes, THE `capacity_hint` method SHALL report the changed CapacityHint.

### Requirement 7: Purity, layer contract, and the HTTP AST gate

**User Story:** As a security engineer, I want the house idiom and layer rules enforced by gates, so that a detector cannot regain the ability to raise HTTP, decide, or import edge.

#### Acceptance Criteria

1. THE Detect_Framework value types SHALL be declared as `@dataclass(frozen=True, slots=True)` types.
2. THE Detect_Framework SHALL express the Detector and GuardBackend contracts as `Protocol` types.
3. WHERE readiness or timeout logic computes elapsed time, THE Detect_Framework SHALL read time from an injected clock rather than a wall-clock call in pure logic.
4. THE Detect_Framework SHALL declare no capacity numeric literal as a dataclass field, keeping capacity numbers in `runtime/resources.py`.
5. THE Detect_Framework SHALL declare no module-level mutable binding.
6. THE Detect_Framework SHALL introduce no new HTTP type.
7. THE `gateway_v2.detect` layer SHALL import only the `gateway_v2.domain` layer, as enforced by the Layer_Contract.
8. IF an HTTP construct (`HTTPException`, `JSONResponse`, or a `status_code` in the 400–499 range) is added inside a module under `detect/`, THEN THE HTTP_AST_Gate SHALL exit with a non-zero status.

### Requirement 8: The exit gate

**User Story:** As a backend engineer, I want a single reproducible exit gate, so that the GW08 framework is verified green before merge.

#### Acceptance Criteria

1. WHEN the Gate runs the full pytest suite from cwd `gateway_v2` under `.venv`, THE Gate SHALL report the baseline of 1730 passed, 95 skipped, and 1 xfailed, plus the new GW08 tests, with zero failures.
2. WHEN the Gate runs `mypy --strict`, THE Gate SHALL report success.
3. WHEN the Gate runs `ruff check gateway_v2 state_control audit_control load_harness lint tests`, THE Gate SHALL report success.
4. WHEN the Gate runs `lint-imports`, THE Gate SHALL report the import contracts kept, including the detect-imports-only-domain rule.
5. WHEN the Gate runs each AST gate, THE Gate SHALL observe each AST gate exit with status 0.
6. WHEN the Gate evaluates `unscanned_channels()`, THE Gate SHALL observe an empty tuple result.
7. FOR ALL frozen Detect_Framework value types that are serialized or round-tripped (Budget, GuardResult, Readiness, CapacityHint), decoding an encoded value SHALL produce an equivalent value (round-trip property).

### Requirement 9: Deferred scope and declared boundaries

**User Story:** As a reviewer, I want the deferred items named explicitly, so that GW08 is not expected to deliver GPU, cloud, or dependent-card work.

#### Acceptance Criteria

1. THE Detect_Framework SHALL treat LGW08-1 (identical detection suite on all four backends) as deferred to GW15.
2. THE Detect_Framework SHALL treat LGW08-6 (backend degrades to half throughput, admission bounds respond) as deferred to GW19.
3. THE Detect_Framework SHALL treat the real GPU implementations of `local_trt` and `triton_grpc` and the real off-box transport of `remote_http` as declared seams whose implementations are deferred to the GPU fleet (GW25).
4. THE Detect_Framework SHALL treat the real semantic guard model and the engine/TensorRT caches as deferred cloud/GPU work.
5. THE Detect_Framework SHALL treat the C43 push-coalescing owner-CPU microbenchmark and the G-09 knee re-measure as deferred cloud evidence of the GW08 v3 amendment, noting that R2-23's local push-coalescing is already complete elsewhere.
6. THE Detect_Framework SHALL exclude the deterministic detectors (GW09) and the semantic detection, windowing, and per-request FPR budget (GW10) that use this framework.
7. WHEN GW08 exits, THE Detect_Framework SHALL have delivered only the framework, the GuardBackend interface, the local CPU and fake backends, the registry, readiness, `capacity_hint`, and the AST gate.
