# Implementation Plan: Deterministic Detectors + Byte-Verified Redaction (GW09)

## Overview

This plan fills the empty `gateway_v2/gateway_v2/detect/deterministic/` package with the pure, in-process deterministic detectors behind the shipped `gateway_v2.detect.base.Detector` protocol (GW08, DONE). Work is serialized in dependency order so one canonicalization, one matcher, and one byte-verified redactor are built cohesively: `canonicalize.py` and `patterns.py` first, `matcher.py` after the ruleset exists, `redactor.py`, then the `detectors.py` + `__init__.py` wiring that joins them. The §10.2.2 Ledger_Note is a markdown-only deliverable that can run in parallel. The entire test suite (8 property tests + LGW09 example tests + per-family scorer + pins) and its sibling fixture corpus are written by a SINGLE file-owning task — never split across parallel sub-agents (the B6/B7/B8/B9/GW07/GW08 single-test-file collision lesson). A gate task and a checkpoint close the workflow.

Every GW09 module declares `from __future__ import annotations` and imports only `gateway_v2.domain` and sibling `gateway_v2.detect.*` (R8.7). This workflow produces only planning artifacts and code tasks; it does not run the feature end-to-end.

## Tasks

- [x] 1. Implement the single canonicalization pass, span map, and bounded decode budget
  - Create `gateway_v2/gateway_v2/detect/deterministic/canonicalize.py` with `from __future__ import annotations` and `re`, importing only `gateway_v2.domain` (for `Span`)
  - Declare module-level `REDACTION_PLACEHOLDER: str = "[REDACTED]"` locally (detect may not import `egress`/`resolve`); pinned equal to egress by a test in Task 7
  - Implement frozen `SpanMap` (`@dataclass(frozen=True, slots=True)`) backed by an immutable tuple of `(canonical_index, original_start, original_end)` records, with `to_original(canonical_start, canonical_end) -> Span` collapsing a canonical range to the minimal original byte `Span`
  - Implement frozen `DecodeBudget` whose bound comes from a module-level named constant (NOT a dataclass capacity field, R8.4), exposing the cheap pre-check `within(text) -> bool` (length / expansion-ratio estimate / part counts)
  - Implement local `BudgetExceeded(Exception)` — caught inside the layer, never an HTTP error, never escaping detect (R8.6)
  - Implement `canonicalize(text, budget) -> tuple[str, SpanMap]`: evaluate `DecodeBudget.within` BEFORE any expansion-heavy decode (R2.6, the C26 fix); on exhaustion raise `BudgetExceeded` (R2.5, explicit reject, no silent truncation); then apply the ordered Decoder_Set once accumulating a `SpanMap` (R2.1, R2.3)
  - Implement the ordered Decoder_Set (R2.2): double %-encoding, HTML entities, base64 (bounded segments), fullwidth→ASCII fold, zero-width / Default_Ignorable / Mn strip (full category), `\u` unescape, Cyrillic/confusable homoglyph fold, RTL-reversed-key normalization, "at"/"dot" email de-obfuscation
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 8.2, 8.4, 8.6, 8.7_
  - _Design: `canonicalize.py` — single canonicalization pass + span map + decode budget_

- [x] 2. Implement the versioned, hashed deterministic ruleset
  - Create `gateway_v2/gateway_v2/detect/deterministic/patterns.py` with `from __future__ import annotations`, `re`, `hashlib`, `MappingProxyType`, importing only `gateway_v2.domain` (for `Category`)
  - Define `PatternKind(StrEnum)` with `REDACTABLE` and `SIGNAL`
  - Define frozen `PatternSpec` (`@dataclass(frozen=True, slots=True)`): `name, category: Category, kind: PatternKind, prefilter, precise, detector_name`
  - Build `RULESET: Mapping[str, PatternSpec]` as a `MappingProxyType` (R8.5, no module-level mutable) with the shipped families: PII (`PII`, REDACTABLE — email/phone/SSN-like), secret (`SECRET`, REDACTABLE — AWS AKIA/ASIA, generic API key, token), credential (`CREDENTIAL`, REDACTABLE — PEM/private-key header, card number), injection (`PROMPT_INJECTION`/`JAILBREAK`, SIGNAL — instruction-override phrases), command (`COMMAND`, SIGNAL — shell constructs, **NO bare backtick-pair pattern**)
  - Patterns are self-contained COPIES adapted from the evidence snapshot with a `# Reference` note citing `rvproto/detect/...` — never imports
  - Implement `ruleset_version() -> str`: sha256 over serialized `(name, category, kind, prefilter, precise)` tuples sorted by name (R8.3)
  - _Requirements: 3.4, 4.1, 4.2, 4.4, 6.1, 6.2, 8.3, 8.5, 8.7_
  - _Design: `patterns.py` — versioned, hashed ruleset; `PatternKind`/`PatternSpec`/`RULESET`/`ruleset_version()`_

- [x] 3. Implement the single combined-alternation matcher
  - Create `gateway_v2/gateway_v2/detect/deterministic/matcher.py` with `from __future__ import annotations`, `re`, importing `gateway_v2.domain` and sibling `detect.deterministic.patterns` (RULESET)
  - Define frozen `RawMatch` (`@dataclass(frozen=True, slots=True)`): `pattern_name, canonical_start, canonical_end`
  - Compile ONE combined alternation `(?P<name1>pf1)|(?P<name2>pf2)|...` over the prefilter supersets once at module import (not per call)
  - Implement `scan(canonical_text) -> tuple[RawMatch, ...]`: a SINGLE `finditer` over the combined regex (R3.1, R3.2 — no per-pattern loop); identify the pattern via `match.lastgroup`; confirm each prefilter hit against that pattern's `precise` regex before emitting a `RawMatch` (R3.3)
  - Use the Python `re` engine as the local default (R3.4) — Hyperscan is deferred
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 8.2, 8.7_
  - _Design: `matcher.py` — single multi-pattern matcher (one combined-alternation `re` scan)_

- [x] 4. Implement the one byte-verified redactor and reusable verify
  - Create `gateway_v2/gateway_v2/detect/deterministic/redactor.py` with `from __future__ import annotations`, importing only `gateway_v2.domain` (for `Transformation`, `Span`)
  - Re-declare module-level `REDACTION_PLACEHOLDER: str = "[REDACTED]"` locally; pinned equal to egress by the Task 7 test
  - Define frozen `RedactResult` (`@dataclass(frozen=True, slots=True)`): `payload, verified, unmaskable_spans`
  - Implement `redact(text, transformations) -> RedactResult`: apply each `Transformation`'s span→replacement left-to-right with overlap detection (mirroring `egress.apply_decision`); byte-verify (R5.2); `verified=True` iff every targeted span's original bytes are absent from the result; record spans a safe transform could not cover in `unmaskable_spans` and set `verified=False` (fail-closed, R5.3/R5.4)
  - Implement `verify(payload, original, spans) -> bool`: True iff for every span `original[span.start:span.end]` no longer appears in `payload`; a no-op leaves bytes present → False (R5.5); this is the function GW11's dispatch re-verify will reuse
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 8.2, 8.7_
  - _Design: `redactor.py` — one byte-verified redactor + reusable `verify`_

- [x] 5. Implement the concrete Detector classes and package exports
  - Create `gateway_v2/gateway_v2/detect/deterministic/detectors.py` wiring `canonicalize → scan → Finding[]`, importing `gateway_v2.domain` (`Finding`, `FindingStatus`, `Category`, `Span`) and the GW08 `detect.base` Detector protocol + sibling GW09 modules (canonicalize, patterns, matcher)
  - Implement a shared `_DeterministicBase` (`@dataclass(frozen=True, slots=True)`, no capacity field, R8.4) with `required_capabilities: frozenset[str] = frozenset()` and a `detector_version` property returning `ruleset_version()` (R8.3)
  - `detect(text, context)`: one canonicalization pass; on `BudgetExceeded` return a single `Finding(status=UNAVAILABLE, confidence=None, spans=(), evidence=None)` (R1.6, R2.5 — never empty-as-clean); otherwise `scan` the canonical text, filter to the detector's family, map canonical ranges back to ORIGINAL byte `Span`s via `SpanMap`, and emit `status=EXECUTED` findings with declared `confidence=1.0` and `evidence=None`
  - Never mutate `text` (R1.4); run in-process with no `GuardBackend` (R1.5); each class structurally satisfies the `@runtime_checkable Detector` protocol (R1.2) exposing `detector_version` and `required_capabilities` (R1.3)
  - Implement concrete classes: `PiiDetector` (PII), `SecretDetector` (SECRET/CREDENTIAL), `InjectionSignalDetector` (PROMPT_INJECTION/JAILBREAK/COMMAND, SIGNAL only — findings only, never a terminal block, R6.1/R6.2/R6.3)
  - Replace the empty `gateway_v2/gateway_v2/detect/deterministic/__init__.py` with a tuple `__all__` exporting the detector classes, `canonicalize`, `scan`, `redact`, `verify`, `ruleset_version`, and the value types (`SpanMap`, `DecodeBudget`, `PatternSpec`, `PatternKind`, `RawMatch`, `RedactResult`, `BudgetExceeded`)
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 4.3, 6.1, 6.2, 6.3, 8.1, 8.7_
  - _Design: `detectors.py` — concrete Detector-protocol classes; `__init__.py` — exports_

- [x] 6. Write the §10.2.2 Ledger_Note (markdown deliverable, no code)
  - Create `gateway_v2/gateway_v2/detect/deterministic/docs/injection-demoted-to-signal.md`, co-located under the package (B8 declared-failure-behaviour doc convention)
  - Record the behaviour change: `command_injection`'s bare backtick-pair pattern is removed; injection and command patterns are demoted from terminal block to signal; the plan/resolver now decides the action; cite the C3 developer-traffic FPR delta (v1 blocked 5/5 → FPR 1.0; v2 target zero terminal blocks)
  - _Requirements: 6.4_
  - _Design: `docs/injection-demoted-to-signal.md` — the Ledger_Note_

- [x] 7. Write the single GW09 test file and sibling fixture corpus
  - This ONE task owns BOTH `gateway_v2/gateway_v2/tests/detect/fixtures_gw09.py` AND `gateway_v2/gateway_v2/tests/detect/test_deterministic_detectors_gw09.py`, written in one coherent pass by one owner so the corpus and the tests stay consistent — MUST NOT be dispatched to parallel sub-agents (B6/B7/B8/B9/GW07/GW08 single-test-file collision lesson)
  - Create `fixtures_gw09.py`: the labeled Fixture_Corpus with three families — benign Developer_Traffic_Family (inline code / backticks), positive PII/secret, and obfuscated/encoded fixtures (R7.1)
  - Create `test_deterministic_detectors_gw09.py`: the per-family recall/FPR scorer reading `RULESET` (never case-specific overrides, scores as outcomes, R7.2/R7.5) reporting the Developer_Traffic_Family FPR separately (R7.3/R7.4); protocol-conformance tests (`isinstance(detector, Detector)`, attribute shapes, R1.2/R1.3); the single-pass structural assertion and the `re`-engine assertion (R3.2/R3.4); the `[REDACTED]` pin test asserting both local re-declarations equal `egress.output_guard.REDACTION_PLACEHOLDER`
  - All property + example sub-tasks below are delivered by THIS task in the SAME two files; they are listed separately only to enumerate coverage and MUST NOT be split out to parallel sub-agents

  - [x]* 7.1 Property 1 — purity and determinism
    - Seeded `random.Random` loop ≥ 10,000 iters, no `hypothesis`; two `detect` invocations yield identical finding tuples and input text is byte-for-byte unchanged
    - `# Feature: deterministic-detectors-gw09, Property 1: Purity and determinism`
    - **Validates: Requirements 1.4, 8.1**

  - [x]* 7.2 Property 2 — span soundness end-to-end
    - Every EXECUTED finding's spans are in bounds and redacting those original spans removes the planted value from the original text
    - `# Feature: deterministic-detectors-gw09, Property 2: Span soundness end-to-end`
    - **Validates: Requirements 2.3, 4.3, 5.1**

  - [x]* 7.3 Property 3 — injection and command emit signal only
    - Any injection/command match (incl. backtick-pair / inline-code prompts) emits PROMPT_INJECTION/JAILBREAK/COMMAND as signal, never a disposition or block-forcing outcome
    - `# Feature: deterministic-detectors-gw09, Property 3: Injection and command emit signal only`
    - **Validates: Requirements 6.1, 6.2, 6.3**

  - [x]* 7.4 Property 4 — canonicalization never silently misses
    - For any planted value under any declared Decoder_Set obfuscation, the pipeline either detects it or explicitly rejects on budget — never a clean, silently-truncated result
    - `# Feature: deterministic-detectors-gw09, Property 4: Canonicalization never silently misses`
    - **Validates: Requirements 2.2, 9.3**

  - [x]* 7.5 Property 5 — decode budget is monotonic and precedes the heavy work
    - Any input whose cheap pre-check exceeds the bound raises `BudgetExceeded` before the expansion-heavy decode; no over-budget input is silently truncated
    - `# Feature: deterministic-detectors-gw09, Property 5: Decode budget is monotonic and precedes the heavy work`
    - **Validates: Requirements 2.4, 2.5, 2.6**

  - [x]* 7.6 Property 6 — single-pass matcher equals the per-family union
    - The combined-alternation matcher emits exactly the match set a per-family precise-pattern loop would, and a prefilter-only near-miss produces no finding
    - `# Feature: deterministic-detectors-gw09, Property 6: Single-pass matcher equals the per-family union, with prefilter confirmation`
    - **Validates: Requirements 3.1, 3.2, 3.3**

  - [x]* 7.7 Property 7 — detector_version is a deterministic hash of the ruleset
    - Same ruleset → same version; any changed pattern → changed version
    - `# Feature: deterministic-detectors-gw09, Property 7: detector_version is a deterministic hash of the ruleset`
    - **Validates: Requirements 8.3**

  - [x]* 7.8 Property 8 — redactor verify is sound, and a no-op fails closed
    - `verify` of the redacted payload is True; `verify` of a no-op is False, driving the Unmaskable_Path
    - `# Feature: deterministic-detectors-gw09, Property 8: Redactor verify is sound, and a no-op fails closed`
    - **Validates: Requirements 5.2, 5.3**

  - [x]* 7.9 LGW09-1 example — five benign inline-code prompts, zero terminal blocks
    - The five Developer_Traffic_Family prompts produce zero terminal blocks, signal only
    - **Validates: Requirements 9.1**

  - [x]* 7.10 LGW09-3 example — secret split across chunk + UTF-8 boundary
    - Emitted spans redact to no-leak and the payload stays valid text (streaming recorder deferred to GW15)
    - **Validates: Requirements 9.2**

  - [x]* 7.11 LGW09-4 example — obfuscated/encoded PII detected or explicitly rejected
    - Detected within the Decode_Budget or explicitly rejected on budget, never silently missed
    - **Validates: Requirements 9.3**

  - [x]* 7.12 LGW09-5 example — no-op transformation detected pre-dispatch
    - `verify` False, the Unmaskable_Path runs, nothing is dispatched
    - **Validates: Requirements 9.4**

  - [x]* 7.13 LGW09-6 example — C3 scoring run publishes per-family recall/FPR
    - Developer_Traffic_Family FPR materially below v1's bare-backtick behaviour (v1 1.0 → v2 zero terminal blocks)
    - **Validates: Requirements 9.5, 7.2, 7.3, 7.4**

- [x] 8. Checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Run the GW09 exit gate
  - From cwd `gateway_v2` under `.venv`: run the full `pytest` suite green (baseline 1752 passed / 95 skipped / 1 xfailed plus the new GW09 tests) (R10.1, R10.2)
  - Run `mypy --strict` (R10.3)
  - Run `ruff check gateway_v2 state_control audit_control load_harness lint tests` (R10.4)
  - Run `lint-imports` and confirm the `detect` layer contract is kept (GW09 imports only `gateway_v2.domain` + `gateway_v2.detect.*`) (R10.5)
  - Run the 6 AST gates, each exiting 0: `check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_sizes`, `check_tenant_scale` (R10.6)
  - Confirm `unscanned_channels() == ()` (R10.7)
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_
  - _Design: Testing Strategy — The gate (R10)_

- [x] 10. Final checkpoint — ensure the deliverable is complete
  - Ensure all tests pass and the gate is green, ask the user if questions arise.

## Explicitly NOT tasks (deferred / out-of-scope, R11)

These are named so the plan does not absorb other cards' responsibilities. They are NOT work items in this plan:

- LGW09-2 streaming/recorder PII-redact assertion → GW15 (the local half still asserts the detector produces spans a redactor removes, covered by Task 7 / LGW09-3)
- Dispatch-layer re-verify of the transformed payload before the provider call → GW11 (GW09 ships the reusable `verify` function in Task 4, not the provider-call wiring)
- Hyperscan matcher engine → performance deferral; `re` is the local default (`auto`→`re` loses Unicode word boundaries)
- Full production C3 corpus and signed numbers → evidence; GW09 ships a representative local Fixture_Corpus + per-family recall/FPR
- GW02 parity-harness and expected-diff machinery → deferred; the Ledger_Note still ships (Task 6)
- Semantic detector (GW10) and the real guard model → excluded

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP; the core implementation tasks (1–6, 9) are never optional.
- Task 7's `*` sub-tasks (7.1–7.13) are all delivered by the SINGLE file-owning Task 7 in the two co-owned files; they are enumerated for coverage only and MUST NOT be dispatched to parallel sub-agents.
- Production is serialized in dependency order: canonicalize + patterns first, matcher after the ruleset, redactor, then detectors + exports after all four.
- Each task references specific requirement sub-clauses and the design element it implements.
- Property tests use seeded `random.Random` loops of ≥ 10,000 iterations (no `hypothesis`), one property ↔ one test, tagged per the design.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2", "6"] },
    { "id": 1, "tasks": ["3"] },
    { "id": 2, "tasks": ["4"] },
    { "id": 3, "tasks": ["5"] },
    { "id": 4, "tasks": ["7"] },
    { "id": 5, "tasks": ["9"] }
  ]
}
```
