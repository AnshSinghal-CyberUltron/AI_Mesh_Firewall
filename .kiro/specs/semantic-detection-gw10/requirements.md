# Requirements Document

## Introduction

GW10 implements **semantic injection detection, tokenizer-aware windowing, and the per-request false-positive-rate (FPR) budget** inside the gateway_v2 `detect/semantic/` layer. The objective, stated verbatim in the runbook card (§10.9, `## GW10 — Implement semantic detection, windowing and the per-request FPR budget`, ~line 5117, now the SOLE source of truth), is to *"Add semantic injection detection with a published, per-request false-positive rate and no silent truncation of long inputs."*

This exists because the semantic model's published operating point is **per window, not per request**, and FPR **compounds across windows**: a model quoted at ~1% FPR at 512 tokens sees ~1.99% at two windows and ~6.79% at seven windows (the independence formula `request_fpr ≈ 1 − (1 − per_window_fpr)^W`, confirmed near 1% per window), which at 1,000 RPS in an ENFORCE posture is roughly 20 false blocks per second. §10.2.2 requires the per-request FPR be published before any customer-facing claim.

### Critical scope split (local half vs deferred cloud/GPU half)

Per the card's agent-execution note (*"Agent-executable against local_onnx. GPU-specific throughput belongs to GW20; this card is about correctness and the FPR budget."*), this spec covers the **LOCAL half only**: the tokenizer-aware windowing algebra, the per-request FPR-budget derivation algebra, the per-language posture, confusable mapping, role scoping, separate multilingual reporting, and the semantic Detector that drives the shipped `GuardBackend` — all exercised against the shipped `local_onnx` CPU backend and a deterministic fake backend.

The following are **DEFERRED** and explicitly out of scope (named so the boundary is unambiguous):
- The real semantic guard **model** and its measured recall / FPR / AUC numbers at the chosen thresholds (the LGW10-2 measured FPR curve and the LGW10-4 real per-language recall/FPR numbers) → GPU fleet / GW20 / T01. The local half tests the **mechanism** (the FPR-budget derivation, the window count, the per-language posture) against a declared/configured per-window FPR input, not the model's measured curve.
- GPU-specific throughput and the 20 ms-profile throughput sizing → GW20.
- The signed 22M-vs-86M Indic recall-vs-latency trade (T01).
- The production tokenizer **bytes** (`tokenizer.json`): only the pinned tokenizer hash plus the measured chars-per-token model are local; the real tokenizer load is a declared seam.
- GW11 provider dispatch (the next card).

GW10 ships ONLY the contents of `detect/semantic/` (windowing + FPR-budget + language-posture + role-scoping + the semantic Detector over the GuardBackend) plus its tests.

### Grounding (verified against the tree, do NOT redefine or invent)

- **Target package empty.** `gateway_v2/gateway_v2/detect/semantic/` contains only `__init__.py`. GW10 fills it.
- **Depends on GW08 (DONE).** The semantic detector IMPLEMENTS the shipped `gateway_v2.detect.base.Detector` protocol and CALLS the shipped `gateway_v2.detect.guard.backend.GuardBackend` protocol. Verified shapes:
  - `Detector.detect(text: str, context: DetectorContext) -> tuple[Finding, ...]` is **synchronous**; it also declares `detector_version: str` and `required_capabilities: frozenset[str]` (`detect/base.py`).
  - `DetectorContext(phase: Phase, rules: tuple[Rule, ...], budget: Budget)` — it carries NO message-role field (`detect/base.py`).
  - `GuardBackend.classify(windows: Sequence[str], budget: Budget) -> Sequence[GuardResult]` is **async**; `readiness() -> Readiness` is async; `capacity_hint() -> CapacityHint` is sync (`detect/guard/backend.py`).
  - `GuardResult(window_index: int, category: str, score: float)`, `Readiness(ready, model_hash, last_ok_at, reason)`, `Budget(remaining_s: float)` (`detect/guard/backend.py`).
  - `local_onnx` ships a working CPU backend; a not-ready / over-budget call raises `BackendUnavailable(reason)` (`"stale_or_manifest_mismatch"` or `"over_budget"`) — it NEVER fabricates a clean result (`detect/guard/local_onnx.py`).
- **Sync/async bridge is a real tension.** `Detector.detect` is synchronous but `GuardBackend.classify` is async. The semantic detector must bridge the two; the exact bridging mechanism is a design/implementation decision and is NOT invented here. This spec states the requirement that one `classify` call is made per request and defers the sync/async bridge mechanism to design.
- **Role scoping requires a role input that the context does not currently carry.** `DetectorContext` holds `phase`, `rules`, `budget` only, and `detect` receives a single `text: str`. The card requires role-scoped semantic rules (system prompts and assistant history are not user input). The role input therefore does NOT exist in the shipped context today; this spec flags that dependency and requires role-scoping behaviour without inventing where the role arrives — whether it is threaded via `DetectorContext`, via a structured `text` input, or otherwise is a design decision.
- **"One backend call" reconciled with GW08 C7.** The card body says *"Batch windows into one backend call; never loop serially."* GW08's C7 correction established that micro-batching is WORSE on L4 and the owner does batch-1 FIFO internally. These reconcile: "one backend call" means ONE `classify(windows)` invocation passing ALL windows (not a per-window serial loop in the detector); the backend owner decides its internal batching. GW10's detector MUST call `classify` exactly once with the full window sequence and MUST NOT loop `classify` per window.
- **Separate input windowing.** `detect/windowing.py` is BYTE-window helpers for GW12b OUTPUT holdback — NOT token-windowing for semantic input. GW10 needs its OWN tokenizer-aware input windowing and MUST NOT reuse `windowing.py` for this.
- **Shipped domain types used (not redefined):** `domain/finding.py` `Finding`/`FindingStatus(EXECUTED|SKIPPED|UNAVAILABLE)`/`Span`; `domain/category.py` `Category` (`PROMPT_INJECTION`, `JAILBREAK` are the semantic categories); `domain/plan.py` `Rule` (`threshold: float | None` is the per-rule confidence threshold the FPR budget derives, `action` default behaviour per C6 below); `runtime/resources.py` `ResourceContract` is the capacity authority.
- **C6 (register, re-cols GW10/T04):** thresholds calibrated on short prompts fail on long text; the shipped tokenizer truncates at 512; multilingual results must be published separately; the default semantic action is **FLAG** until a signed per-request FPR and recall floor are met. GW10 calibrates per window count, disables tokenizer truncation, adds language identification with an explicit posture for unsupported languages, and scopes semantic rules by message role.
- **C7 (register, GW08):** one owner process per GPU, batch-1 FIFO internally, micro-batching worse at every setting — the reconciliation above.
- **Capacity authority.** Thresholds, FPR ceilings, max window count, overlap, and chars-per-token are NOT dataclass-field literals; they derive from the plan / `Rule.threshold` / `ResourceContract` / config, enforced by the AST gate `lint/check_capacity_literals.py`.

## Glossary

- **Semantic_Detector**: The GW10 deliverable — the Detector-protocol class living in `gateway_v2.detect.semantic` that performs tokenizer-aware windowing, calls the `GuardBackend` once per request, aggregates results, applies the FPR budget, applies language and role posture, and emits `Finding[]`.
- **Window**: A tokenizer-aware slice of the input, bounded by a declared token band with a declared overlap between adjacent windows.
- **Window_Count (W)**: The number of windows a given input is split into; a function of the input length and the measured characters-per-token, recorded on every request.
- **Overlap**: The declared number of tokens shared between adjacent windows so a match straddling a boundary is not lost.
- **Chars_Per_Token**: The measured characters-per-token ratio for actual traffic (measured in-corpus: 3.04 for markdown, 6.28 for prose), used to size a token band in characters — NOT the 4.0 rule of thumb.
- **Tokenizer_Seam**: The declared tokenizer boundary. The windowing algebra consumes a chars-per-token model (or a real tokenizer where available); the production tokenizer bytes are a deferred seam, identified by a pinned Tokenizer_Hash.
- **Tokenizer_Hash**: The pinned, recorded hash identifying the tokenizer version; the window count varies with tokenizer version, so the hash is recorded on every request.
- **Per_Window_FPR**: The configured/declared per-window false-positive rate input (near 1% per window per the card). The real measured number is deferred (GW20/T01); the local half uses the declared value.
- **Per_Request_FPR_Ceiling**: The per-request FPR ceiling the plan declares.
- **Derived_Threshold**: The resolver's confidence threshold, derived from the Per_Request_FPR_Ceiling and the actual Window_Count via the independence formula `request_fpr ≈ 1 − (1 − per_window_fpr)^W`.
- **Aggregation_Rule**: The declared rule by which the per-window `GuardResult[]` is combined into the request-level semantic finding(s).
- **Over_Length_Input**: An input whose required window count exceeds the declared maximum.
- **Language_Posture**: The explicit posture assigned per detected language. An unsupported language is a distinct posture (e.g. UNAVAILABLE or an explicit unsupported-language finding) and NEVER a silent clean EXECUTED pass.
- **Confusable_Mapping**: The homoglyph/confusable normalization applied BEFORE the guard tokenizer.
- **Role_Scoping**: Distinguishing message roles so that system prompts and assistant history are not treated as user input for semantic rules.
- **GuardBackend / classify / GuardResult / Readiness / Budget / BackendUnavailable**: The shipped GW08 types in `detect/guard/`; used, not redefined.
- **Finding / FindingStatus / Span / Category / Rule / ResourceContract**: The shipped domain/runtime types; used, not redefined.
- **Capacity_AST_Gate**: `gateway_v2/lint/check_capacity_literals.py`, which forbids capacity/threshold numeric literals in dataclass fields and capacity positions.
- **Layer_Contract**: The import-linter contracts keeping `detect` importing only `domain`, the GW08 `detect` siblings (`detect/guard`, `detect/base`), and `runtime` — never a higher layer (`edge` / `admit` / `plan`).
- **Gate**: The GW10 exit gate run from cwd `gateway_v2` under `.venv`: full pytest green (baseline 1767 passed / 100 skipped / 1 xfailed, plus new tests), `mypy --strict`, `ruff check gateway_v2 state_control audit_control load_harness lint tests`, `lint-imports`, the 6 AST gates each exiting 0, and `unscanned_channels() == ()`.

## Requirements

### Requirement 1: Tokenizer-aware windowing with declared overlap, pinned tokenizer hash, and no 512 truncation

**User Story:** As a security engineer, I want long inputs split into tokenizer-aware overlapping windows sized by measured characters-per-token, so that no part of a long input is silently cut and window boundaries do not hide a match.

#### Acceptance Criteria

1. WHEN the Semantic_Detector receives an input, THE Semantic_Detector SHALL split the input into windows sized by a declared token band using the measured Chars_Per_Token for the input rather than a fixed 4.0 characters-per-token assumption.
2. THE Semantic_Detector SHALL place a declared Overlap of tokens between adjacent windows so that a match straddling a window boundary appears whole in at least one window.
3. THE Semantic_Detector SHALL disable the shipped tokenizer's 512-token truncation and padding so that an input longer than 512 tokens is not silently cut.
4. THE Semantic_Detector SHALL pin the Tokenizer_Hash and SHALL record the Tokenizer_Hash on every request.
5. WHERE the production tokenizer bytes are not loaded, THE Semantic_Detector SHALL consume the Tokenizer_Seam (a measured chars-per-token model or an available tokenizer) while keeping the windowing algebra identical.
6. THE Semantic_Detector SHALL compute the windowing algebra as pure functions of the input, the token band, the Overlap, and the Chars_Per_Token, taking the band, overlap, and chars-per-token from configuration or the ResourceContract rather than from dataclass-field numeric literals.

### Requirement 2: One backend call, declared aggregation, and recorded window count

**User Story:** As a backend engineer, I want all windows passed to the backend in a single classify call and aggregated by a declared rule, so that there is no serial per-window loop on the hot path and the window count is always recorded.

#### Acceptance Criteria

1. WHEN the Semantic_Detector classifies an input, THE Semantic_Detector SHALL call `classify` exactly once with the full sequence of windows for that request.
2. THE Semantic_Detector SHALL NOT invoke `classify` in a per-window serial loop.
3. WHEN the Semantic_Detector receives the per-window `GuardResult` sequence, THE Semantic_Detector SHALL combine the results into the request-level finding(s) using the declared Aggregation_Rule.
4. THE Semantic_Detector SHALL record the Window_Count on every request.
5. WHEN the Semantic_Detector emits a semantic finding, THE Semantic_Detector SHALL carry only `Category.PROMPT_INJECTION` or `Category.JAILBREAK` as the semantic category.

### Requirement 3: The per-request FPR budget — derivation and ceiling behaviour

**User Story:** As a platform operator, I want the confidence threshold derived from a declared per-request FPR ceiling and the actual window count, so that the per-request false-positive rate stays within the published ceiling instead of compounding silently across windows.

#### Acceptance Criteria

1. THE Semantic_Detector SHALL read the Per_Request_FPR_Ceiling from the plan rather than from a dataclass-field numeric literal.
2. WHEN the Semantic_Detector resolves the confidence threshold for a request, THE Semantic_Detector SHALL derive the Derived_Threshold from the Per_Request_FPR_Ceiling and the actual Window_Count using the independence formula `request_fpr ≈ 1 − (1 − per_window_fpr)^W`.
3. THE Semantic_Detector SHALL compute the Per_Window_FPR input from configuration or the plan rather than from a dataclass-field numeric literal.
4. WHILE the Derived_Threshold holds, THE Semantic_Detector SHALL keep the resulting per-request FPR at or below the Per_Request_FPR_Ceiling by construction for the declared Per_Window_FPR.
5. IF a request's derivation cannot keep the per-request FPR at or below the Per_Request_FPR_Ceiling, THEN THE Semantic_Detector SHALL record the ceiling-exceeded condition and SHALL apply the declared behaviour rather than silently proceeding.
6. THE Semantic_Detector SHALL compute the FPR-budget derivation as pure functions of the ceiling, the Per_Window_FPR, and the Window_Count.

### Requirement 4: Over-length inputs — explicit reject, no silent middle-drop

**User Story:** As a security engineer, I want over-length inputs either fully covered or explicitly rejected, so that head-and-tail truncation can never silently drop the middle of an input.

#### Acceptance Criteria

1. WHEN the Semantic_Detector processes an input within the maximum window count, THE Semantic_Detector SHALL ensure every window of the input is classified.
2. IF an Over_Length_Input exceeds the declared maximum window count, THEN THE Semantic_Detector SHALL reject the request on length with an actionable error.
3. THE Semantic_Detector SHALL NOT apply head-and-tail truncation that drops the middle of an input.
4. THE Semantic_Detector SHALL read the maximum window count from the ResourceContract or configuration rather than from a dataclass-field numeric literal.

### Requirement 5: Language identification posture, confusable mapping, role scoping, and separate multilingual reporting

**User Story:** As a security engineer deploying in a multilingual region, I want unsupported languages surfaced as a distinct posture, homoglyphs normalized before tokenization, semantic rules scoped by message role, and multilingual behaviour reported separately, so that a non-English input is never silently treated as a clean pass.

#### Acceptance Criteria

1. WHEN the Semantic_Detector receives an input, THE Semantic_Detector SHALL identify the input language and assign a Language_Posture.
2. IF the identified language is unsupported, THEN THE Semantic_Detector SHALL surface a distinct posture (UNAVAILABLE or an explicit unsupported-language finding) rather than a clean EXECUTED pass.
3. THE Semantic_Detector SHALL apply Confusable_Mapping before the guard tokenizer runs.
4. WHERE a semantic rule is role-scoped, THE Semantic_Detector SHALL distinguish message roles so that system prompts and assistant history are not treated as user input.
5. THE Semantic_Detector SHALL report multilingual behaviour separately from English behaviour.

### Requirement 6: Backend unavailability and mid-batch timeout — UNAVAILABLE, never partial-as-complete

**User Story:** As a security engineer, I want a backend timeout or outage mid-batch to resolve to UNAVAILABLE with the rule's posture applied, so that a partial classification is never presented as a complete clean result.

#### Acceptance Criteria

1. IF the GuardBackend is unavailable or times out mid-batch, THEN THE Semantic_Detector SHALL emit a Finding with `status` `UNAVAILABLE` and SHALL apply the rule's failure posture.
2. WHEN the GuardBackend raises `BackendUnavailable`, THE Semantic_Detector SHALL map that signal to an `UNAVAILABLE` finding rather than a clean or benign EXECUTED result.
3. THE Semantic_Detector SHALL NOT present a partial set of per-window results as a complete classification.
4. WHILE the GuardBackend reports not-ready, THE Semantic_Detector SHALL NOT produce a clean EXECUTED semantic result.

### Requirement 7: The semantic Detector-protocol class over the GuardBackend

**User Story:** As a backend engineer, I want the semantic detector to be a Detector-protocol class that drives the GuardBackend, so that it integrates uniformly with the shipped detect framework and observes no backend topology.

#### Acceptance Criteria

1. THE Semantic_Detector SHALL implement the shipped `Detector` protocol, exposing a `detector_version` string and a `required_capabilities` set and returning a `tuple[Finding, ...]`.
2. WHEN the Semantic_Detector runs, THE Semantic_Detector SHALL leave its input text unchanged.
3. THE Semantic_Detector SHALL NOT decide request disposition, SHALL NOT raise an HTTP response, and SHALL NOT short-circuit the pipeline.
4. THE Semantic_Detector SHALL drive the GuardBackend through the `classify`, `readiness`, and `capacity_hint` contract without referencing CPU, GPU, TensorRT, Triton, batch size, or replica count.
5. WHEN the Semantic_Detector drives the GuardBackend, THE Semantic_Detector SHALL use the shipped `local_onnx` backend as the continuous-integration default and SHALL be drivable by a deterministic fake GuardBackend for tests.

### Requirement 8: Purity, house idiom, layer contract, and AST gates

**User Story:** As a security engineer, I want the house idiom, layer rules, and AST gates enforced, so that the semantic detector cannot regain the ability to raise HTTP, hardcode capacity, or import a higher layer.

#### Acceptance Criteria

1. THE Semantic_Detector SHALL compute the windowing algebra and the FPR-budget derivation as pure functions, performing backend I/O only through the async `classify` call via the shipped idiom.
2. THE Semantic_Detector value types SHALL be declared as `@dataclass(frozen=True, slots=True)` types.
3. WHERE timeout or elapsed-time logic is computed, THE Semantic_Detector SHALL read time from an injected clock rather than a wall-clock call in pure logic.
4. THE Semantic_Detector SHALL declare no capacity, threshold, FPR-ceiling, overlap, or characters-per-token value as a dataclass-field numeric literal, keeping such values in the plan, `Rule.threshold`, the ResourceContract, or configuration.
5. THE Semantic_Detector SHALL declare no module-level mutable binding.
6. THE Semantic_Detector SHALL introduce no new HTTP type.
7. THE `gateway_v2.detect.semantic` module SHALL import only `gateway_v2.domain`, the GW08 `detect` siblings, and `gateway_v2.runtime`, and SHALL NOT import a higher layer (`edge`, `admit`, or `plan`), as enforced by the Layer_Contract.
8. WHEN a property test exercises the windowing algebra or the FPR-budget derivation, THE property test SHALL use a seeded `random.Random` with at least 10,000 iterations and SHALL NOT use the hypothesis library.

### Requirement 9: Local acceptance tests (LGW10 mechanism), with deferred measurements named

**User Story:** As a reviewer, I want the LGW10 acceptance tests implemented as local mechanism tests against a fake and the local_onnx backend, so that correctness is proven locally while measured model numbers are explicitly deferred.

#### Acceptance Criteria

1. WHEN an attack is planted in the first, middle, or last window of a long input (LGW10-1), THE Semantic_Detector SHALL detect it in every one of those positions, or SHALL reject the input on length, with a planted middle-window miss counted as a failure.
2. WHEN the FPR-budget algebra is tested at window counts of 1, 2, 4, and 7 (LGW10-2 local half), THE Semantic_Detector SHALL derive the threshold from the declared Per_Window_FPR at each window count and keep the per-request FPR within the declared ceiling by construction, while the measured FPR curve on the real model is DEFERRED to GW20/T01.
3. WHEN the detector classifies N windows (LGW10-3), THE Semantic_Detector SHALL make exactly one `classify` call for the request as counted by a spy or fake backend, with no serial per-window loop.
4. WHEN a multilingual corpus is processed (LGW10-4 local half), THE Semantic_Detector SHALL assign a Language_Posture in which an unsupported language is not clean and SHALL report multilingual behaviour separately, while the real per-language recall/FPR numbers are DEFERRED to GW20/T01.
5. WHEN an input exceeds the maximum window count (LGW10-5), THE Semantic_Detector SHALL reject it with an explicit, actionable error.
6. WHEN a fake GuardBackend times out or goes not-ready mid-batch (LGW10-6), THE Semantic_Detector SHALL resolve to `UNAVAILABLE` with the rule posture applied and SHALL NOT present partial results as complete.

### Requirement 10: The exit gate

**User Story:** As a backend engineer, I want a single reproducible exit gate, so that the GW10 local half is verified green before merge.

#### Acceptance Criteria

1. WHEN the Gate is run from cwd `gateway_v2` under `.venv`, THE Gate SHALL run the full pytest suite green at the baseline of 1767 passed / 100 skipped / 1 xfailed plus the new GW10 tests.
2. THE Gate SHALL pass `mypy --strict`.
3. THE Gate SHALL pass `ruff check gateway_v2 state_control audit_control load_harness lint tests`.
4. THE Gate SHALL pass `lint-imports`.
5. THE Gate SHALL cause each of the 6 AST gates to exit 0, including the Capacity_AST_Gate over `detect/semantic`.
6. THE Gate SHALL confirm `unscanned_channels() == ()`.

### Requirement 11: Deferred scope is explicit and not silently resolved

**User Story:** As a reviewer, I want the deferred cloud/GPU work named in the spec, so that no measured model number is quietly treated as satisfied by the local half.

#### Acceptance Criteria

1. THE Semantic_Detector SHALL NOT present a measured recall, FPR, or AUC number for the real semantic guard model, which is DEFERRED to the GPU fleet / GW20 / T01.
2. THE Semantic_Detector SHALL NOT present GPU-specific throughput or 20 ms-profile sizing, which is DEFERRED to GW20.
3. THE Semantic_Detector SHALL NOT resolve the 22M-vs-86M Indic recall-vs-latency trade, which is DEFERRED to be signed in T01.
4. THE Semantic_Detector SHALL treat the production tokenizer bytes as a declared seam, keeping only the pinned Tokenizer_Hash and the chars-per-token measurement model local.
5. IF the per-request FPR at the deployed window count cannot be published, THEN THE Semantic_Detector SHALL stop rather than silently resolving the FPR-ceiling-versus-recall trade in favour of the latency headline.
