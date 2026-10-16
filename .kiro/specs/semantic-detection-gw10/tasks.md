# Implementation Plan: Semantic Detection, Windowing and the Per-Request FPR Budget (GW10)

## Overview

This plan fills the empty `gateway_v2/gateway_v2/detect/semantic/` package with the LOCAL half of
semantic injection detection, then adds one test file. The production side is built bottom-up in
dependency order: the role-admission algebra, the language posture + confusable fold, the
tokenizer-aware input windowing, and the pure FPR-budget algebra are mutually independent domain/pure
modules (wave 0); the aggregation rule consumes the windowing `Span` map; the `SemanticDetector`
orchestrator composes all of them plus the shipped GW08 `GuardBackend` / `local_onnx` and the sync
`Detector` protocol shim. The complete test suite — the 9 property tests, the LGW10-1..6 mechanism
tests, the `FakeGuardBackend`, the `local_onnx` smoke, and the structural/unit tests — is written as
ONE file by a SINGLE task in one pass (the B6/B7/B8/B9/GW07/GW08/GW09 single-test-file collision
lesson). The implementation follows the design's module layout and the Resolution 1 (async surface +
`_run` sync shim) and Resolution 2 (GW10-local typed `RoleScopedSegment` role axis) decisions. The
plan closes on the GW10 exit gate and a final checkpoint.

Each module uses the house idiom: `from __future__ import annotations`, frozen
`@dataclass(frozen=True, slots=True)` value types, `MappingProxyType` immutable tables, tuple
`__all__`, imports confined to `gateway_v2.domain`, the GW08 `detect` siblings (`detect.base`,
`detect.guard.backend`, `detect.guard.local_onnx`), and `gateway_v2.runtime`. No capacity / threshold
/ FPR-ceiling / overlap / chars-per-token value is ever a dataclass-field literal — such values are
module constants, config, or `ResourceContract` reads.

## Tasks

- [x] 1. Implement `roles.py` — role axis and pure role admission
  - Create `gateway_v2/gateway_v2/detect/semantic/roles.py`.
  - Define `SegmentRole(StrEnum)` with `SYSTEM`/`USER`/`ASSISTANT`/`TOOL`.
  - Define frozen+slots `RoleScopedSegment(role: SegmentRole, text: str)` as the typed role input (Resolution 2).
  - Define the `RoleScope` admission set as `frozenset[SegmentRole]` with the `{USER}` default.
  - Implement `admit_segments(segments, role_scope)` as a pure filter returning only segments whose role is in the scope.
  - Add `from __future__ import annotations` and a tuple `__all__`; no module-level mutable binding.
  - _Requirements: 5.4, 7.2, 8.2, 8.5, 8.7_

- [x] 2. Implement `language.py` — language posture, confusable fold, separate multilingual reporting
  - Create `gateway_v2/gateway_v2/detect/semantic/language.py`.
  - Implement a lightweight local language-ID heuristic (script/codepoint-range classifier stub) and the `SUPPORTED_LANGUAGES` frozenset.
  - Define frozen+slots `LanguagePosture(language: str, supported: bool)`; an unsupported language yields a non-clean posture (never a silent clean EXECUTED pass).
  - Implement the confusable/homoglyph fold as a self-contained `MappingProxyType` table applied BEFORE the tokenizer; do NOT import GW09 `canonicalize.py` internals.
  - Define frozen+slots `MultilingualReport` for the separate per-language breakdown record.
  - Add `from __future__ import annotations` and a tuple `__all__`; no module-level mutable binding.
  - _Requirements: 5.1, 5.2, 5.3, 5.5, 8.2, 8.5, 8.7_

- [x] 3. Implement `windowing.py` — tokenizer-aware input windowing (pure)
  - Create `gateway_v2/gateway_v2/detect/semantic/windowing.py`.
  - Carry the measured chars-per-token values (3.04 markdown, 6.28 prose) as MODULE CONSTANTS, not dataclass fields; read token band, overlap, and max window count from config / `ResourceContract` / module constants, never from a dataclass-field literal.
  - Define frozen+slots `TokenizerSeam(tokenizer_hash: str, ...)` carrying the chars-per-token model handle (production tokenizer bytes deferred — R11.4).
  - Implement the pure windowing function of `(text, token_band, overlap, chars_per_token)`: size the band in chars via `band_chars = token_band * chars_per_token`, place the declared token overlap between adjacent windows, DISABLE the shipped 512-token truncation/padding, and window the WHOLE input.
  - Build the `Span` offset map so each window carries `(start, end)` char offsets mapping back to a `domain.finding.Span`.
  - Pin and record the `Tokenizer_Hash` on the plan output.
  - Compute `W = ceil` over the band with overlap; if `W > max_window_count`, raise the local `OverLengthError(required_windows, max_windows)` — an actionable length reject, NEVER head-and-tail middle-drop, never an HTTP type.
  - Return frozen+slots `WindowPlan(windows: tuple[str, ...], spans: tuple[Span, ...], window_count: int, tokenizer_hash: str)`.
  - Keep the module distinct from the shipped byte-windowing `detect/windowing.py` (do not reuse it).
  - Add `from __future__ import annotations` and a tuple `__all__`; no module-level mutable binding.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 4.1, 4.2, 4.3, 4.4, 8.2, 8.4, 8.5, 8.6, 8.7_

- [x] 4. Implement `fpr_budget.py` — the per-request FPR-budget algebra (pure)
  - Create `gateway_v2/gateway_v2/detect/semantic/fpr_budget.py`.
  - Implement `request_fpr(per_window_fpr, W) = 1 − (1 − per_window_fpr)^W` and the per-window budget `1 − (1 − ceiling)^(1/W)`.
  - Implement `derive_threshold(ceiling, per_window_fpr, W) -> float` inverting the independence formula so the per-request FPR is `≤ ceiling` by construction for the declared per-window FPR; the derived per-window threshold is monotone (non-decreasing strictness) in `W`.
  - Return frozen+slots `FprBudget(derived_threshold: float, ceiling_exceeded: bool)`; set `ceiling_exceeded` when no per-window threshold can satisfy the budget (the declared-behaviour FLAG condition per C6 is recorded, never silently proceeded past).
  - Read the ceiling and per-window FPR from the plan / config, never from a dataclass-field literal.
  - Add `from __future__ import annotations` and a tuple `__all__`; no module-level mutable binding.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 8.2, 8.4, 8.5, 8.7, 11.5_

- [x] 5. Implement `aggregate.py` — the declared Aggregation_Rule (pure)
  - Create `gateway_v2/gateway_v2/detect/semantic/aggregate.py`.
  - Implement max-score-per-category aggregation over the per-window GW08 `GuardResult[]` for each semantic category.
  - Map the owning window index (the window producing the maximum) back to its `Span` using the `WindowPlan.spans` map from Task 3.
  - Restrict emitted semantic categories to `Category.PROMPT_INJECTION` and `Category.JAILBREAK`; reject non-member categories via `domain.category.parse_category`.
  - Keep the function pure; use the shipped GW08 `GuardResult` and shipped `domain` types without redefining them.
  - Add `from __future__ import annotations` and a tuple `__all__`; no module-level mutable binding.
  - _Requirements: 2.3, 2.5, 8.2, 8.5, 8.7_

- [x] 6. Implement `detector.py` + `__init__.py` — the `SemanticDetector` orchestrator and exports
  - Create `gateway_v2/gateway_v2/detect/semantic/detector.py` composing Tasks 1–5 and the shipped GW08 `detect.base.Detector` protocol, `detect.guard.backend.GuardBackend`, and `detect.guard.local_onnx`.
  - Implement `async def classify_input(segments, context) -> tuple[Finding, ...]` as the primary surface in order: role-admit (Task 1) → confusable fold + language posture (Task 2) → windowing (Task 3) → ONE `await backend.classify(windows, budget)` call with ALL windows (never a serial per-window loop) → aggregate (Task 5) → apply the derived threshold (Task 4) → emit `Finding[]`.
  - On `BackendUnavailable`, mid-batch timeout (detected against the injected `Budget.remaining_s` / injected clock), or not-ready `readiness()`, emit a single `Finding(status=UNAVAILABLE, confidence=None)` carrying `Rule.on_unavailable`; never present a partial per-window result set as a complete clean classification.
  - On an unsupported `LanguagePosture`, emit the non-clean posture (UNAVAILABLE or an explicit unsupported-language finding), never a clean EXECUTED pass.
  - Expose the composite `detector_version` (`f"semantic:{model_hash}:{tokenizer_hash}:{ruleset_version}"`) and `required_capabilities` (e.g. `frozenset({"semantic"})`); record `W` and the tokenizer hash on every request.
  - Implement the thin sync `detect(text, context)` shim wrapping `classify_input` via the house idiom `def _run[T](coro) -> T: return asyncio.run(coro)`, used ONLY at the sync boundary (never on the async hot path).
  - Keep the detector topology-blind (drive the backend only through `classify` / `readiness` / `capacity_hint`; reference no CPU/GPU/TensorRT/Triton/batch/replica symbol); never mutate input; never decide disposition, raise HTTP, or short-circuit.
  - Create `gateway_v2/gateway_v2/detect/semantic/__init__.py` with a tuple `__all__` re-exporting the public surface.
  - _Requirements: 2.1, 2.2, 2.4, 5.2, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.5, 8.6, 8.7_

- [x] 7. Write the single GW10 test file
  - Create `gateway_v2/tests/detect/test_semantic_detection_gw10.py` as ONE cohesive file written in one pass. Every sub-task below is delivered by this single file-owning task and MUST NOT be dispatched to parallel sub-agents (single-test-file collision lesson).
  - Add the `FakeGuardBackend` (satisfies the `GuardBackend` protocol) with injectable `GuardResult[]`, injectable timeout / not-ready, and an invocation counter.
  - Drive the async surface from sync test bodies via the house idiom `_run(coro) = asyncio.run(coro)` (as in `tests/state_control/test_lgw05c_writer.py:49`).
  - All property tests use a seeded `random.Random` with ≥10,000 iterations and do NOT use hypothesis; each property test maps one-to-one to a design Property (1–9) and is tagged `# Feature: semantic-detection-gw10, Property N: <text>` and `# Validates: Requirements X.Y`.

  - [x]* 7.1 Property 1 — windowing covers the whole input, boundaries hide nothing
    - **Property 1: Windowing covers the whole input, boundaries hide nothing**
    - **Validates: Requirements 1.1, 1.2, 1.3, 4.1, 4.3**

  - [x]* 7.2 Property 2 — over-length inputs explicitly rejected, never silently truncated
    - **Property 2: Over-length inputs are explicitly rejected, never silently truncated**
    - **Validates: Requirements 4.2, 4.3, 1.3, 9.5**

  - [x]* 7.3 Property 3 — FPR-budget derivation within ceiling and monotone in W
    - **Property 3: The FPR-budget derivation keeps the per-request FPR within the ceiling and is monotone in W** (including `W ∈ {1, 2, 4, 7}`)
    - **Validates: Requirements 3.2, 3.4, 3.6, 9.2**

  - [x]* 7.4 Property 4 — exactly one classify call per request
    - **Property 4: Exactly one classify call per request** (counted by the fake backend; no serial loop)
    - **Validates: Requirements 2.1, 2.2, 9.3**

  - [x]* 7.5 Property 5 — unavailable/timed-out/not-ready backend yields UNAVAILABLE, never partial-as-complete
    - **Property 5: An unavailable, timed-out, or not-ready backend yields UNAVAILABLE, never partial-as-complete**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 9.6**

  - [x]* 7.6 Property 6 — unsupported language is never a silent clean pass
    - **Property 6: An unsupported language is never a silent clean pass**
    - **Validates: Requirements 5.1, 5.2, 9.4**

  - [x]* 7.7 Property 7 — role scoping, a user-scoped rule does not fire on system/assistant-only input
    - **Property 7: Role scoping — a user-scoped rule does not fire on system/assistant-only input**
    - **Validates: Requirements 5.4**

  - [x]* 7.8 Property 8 — pure algebra is deterministic and never mutates its input
    - **Property 8: The pure algebra is deterministic and never mutates its input**
    - **Validates: Requirements 1.6, 3.6, 7.2, 8.1**

  - [x]* 7.9 Property 9 — aggregation is max-score-per-category with a correct Span and only semantic categories
    - **Property 9: Aggregation is max-score-per-category with a correct Span and only semantic categories**
    - **Validates: Requirements 2.3, 2.5**

  - [x]* 7.10 Mechanism tests LGW10-1..6
    - LGW10-1: plant a high-score marker in the first, middle, and last window of a long input → detected in every position (or a length reject); a planted middle-window miss fails. _Requirements: 9.1_
    - LGW10-2: the FPR-budget algebra at `W ∈ {1, 2, 4, 7}` stays within the declared ceiling by construction (measured curve deferred). _Requirements: 9.2_
    - LGW10-3: exactly one `classify` call for N windows (fake counter == 1), no serial loop. _Requirements: 9.3_
    - LGW10-4: a multilingual corpus — unsupported language is non-clean, reported separately (real per-language numbers deferred). _Requirements: 9.4_
    - LGW10-5: an input exceeding the max window count → explicit actionable error. _Requirements: 9.5_
    - LGW10-6: a fake that times out / goes not-ready mid-batch → UNAVAILABLE with the rule posture, no partial-as-complete. _Requirements: 9.6_

  - [x]* 7.11 Real-backend smoke + structural/unit tests
    - `local_onnx` real-backend smoke (CI default): a `classify` returns results and the detector maps them to findings. _Requirements: 7.5_
    - `detector_version` composite carries model + tokenizer + ruleset; `W` and tokenizer hash recorded. _Requirements: 1.4, 2.4, 7.1_
    - `isinstance(detector, Detector)`; value types are frozen+slots. _Requirements: 7.1, 8.2_
    - the tokenizer-seam substitution yields identical `W`; the confusable fold runs before windowing; the ceiling-exceeded recorded condition. _Requirements: 1.5, 5.3, 3.5_

- [x] 8. Run the GW10 exit gate
  - From cwd `gateway_v2` under `.venv`, run the full `pytest` suite green at the baseline 1767 passed / 100 skipped / 1 xfailed plus the new GW10 tests.
  - Run `mypy --strict`.
  - Run `ruff check gateway_v2 state_control audit_control load_harness lint tests`.
  - Run `lint-imports` and confirm the `detect/semantic` layer imports only `domain`, the GW08 `detect` siblings, and `runtime` (`detect` may import `runtime`).
  - Run the 6 AST gates and confirm each exits 0: `check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_sizes`, `check_tenant_scale`.
  - Confirm `unscanned_channels() == ()`.
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 8.4, 8.5, 8.6, 8.7_

- [x] 9. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP. All `*` sub-tasks under Task 7
  are delivered by the SINGLE test-file-owning task in one pass and MUST NOT be dispatched to parallel
  sub-agents (the single-test-file collision lesson). They are serialized, same-file work.
- Each task references specific requirement sub-clauses and the design element it implements, and
  focuses only on coding activities.
- Property tests validate the design's universal correctness properties (seeded `random.Random`,
  ≥10,000 iterations, no hypothesis); unit and mechanism tests validate specific examples, edge cases,
  and the I/O boundary against the `FakeGuardBackend` and `local_onnx`.

### Explicitly NOT tasks (Deferred / out of scope — R11)

The following are named so the boundary is unambiguous and are NOT implemented by this plan:

- Measured model recall / FPR / AUC numbers at the chosen thresholds (the LGW10-2 measured FPR curve,
  the LGW10-4 per-language recall/FPR) → GPU fleet / GW20 / T01 (R11.1).
- GPU-specific throughput and the 20 ms-profile sizing → GW20 (R11.2).
- The signed 22M-vs-86M Indic recall-vs-latency trade → T01 (R11.3).
- The production tokenizer bytes (`tokenizer.json`) → a declared seam; only the pinned `Tokenizer_Hash`
  and the chars-per-token measurement model are local (R11.4).
- The real request-message → role-segment lifecycle folding (building `RoleScopedSegment[]` from the
  OpenAI request messages) → GW15.
- GW11 provider dispatch → the next card.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2", "3", "4"] },
    { "id": 1, "tasks": ["5"] },
    { "id": 2, "tasks": ["6"] },
    { "id": 3, "tasks": ["7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11"] },
    { "id": 4, "tasks": ["8"] }
  ]
}
```
