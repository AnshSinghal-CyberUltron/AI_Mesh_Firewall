# Requirements Document

## Introduction

GW09 fills the empty `gateway_v2/gateway_v2/detect/deterministic/` package with **fast, deterministic PII/secret/credential/transport detectors whose redaction is verified on the bytes that actually leave, and whose false positives are scored rather than assumed** (the runbook card Objective, §10.9, `## GW09 — Implement deterministic detectors with byte-verified redaction`, ~line 5058, plus its v2.1 CORRECTION C21/C26 and the referenced register corrections C21/C26 in §0-A.3).

GW09 removes two v1 defects named in the card's "Why this task exists":

1. **The decision was made against one redactor while the wire was produced by a different, weaker one.** v1 decided REDACT against `INPUT_SCANNER.redact_pii` on a flattened prompt while the wire was produced by `llm_router._apply_redaction → patterns.redact_all` (its own docstring calls it "by construction WEAKER"), so the system could decide REDACT and emit something the decision never inspected. v2 has **exactly one redactor**: the bytes the resolver inspected are the bytes dispatch sends, and a redaction that did not remove what it claimed is a **failure (fail-closed), not a pass** — byte-verified against the finding spans.
2. **`ATTACK_PATTERNS["command_injection"]` matched any backtick pair** (`` `[^`]+` ``), which hard-blocked 5 of 5 benign inline-code prompts terminally. v2 **demotes injection and command patterns from terminal block to a signal**: they contribute a Finding only; the plan/resolver decides the action. This is a §10.2.2 behaviour change requiring a ledger entry and a C3 score.

GW09 is the **clean-room backend, local half**. It depends on GW08 (DONE): the detectors implement the shipped `gateway_v2.detect.base.Detector` protocol and are IN-PROCESS (no `GuardBackend` — that is the semantic/GW10 path). The resolver already turns detector findings into the one `Decision` (GW07, DONE). GW09 owns producing the finding spans, a single canonicalization, and a single redactor that transforms exactly those spans and verifies the transformed bytes removed what the finding claimed. The dispatch-layer RE-verify before the provider call is a GW11 touchpoint, scoped OUT of GW09 (GW09 provides the verifiable spans plus the verify function, not the provider-call wiring).

### Grounding (verified against the tree, do NOT redefine)

- The target package `gateway_v2/gateway_v2/detect/deterministic/` is EMPTY (only `__init__.py`). GW09 fills it.
- `gateway_v2/gateway_v2/detect/base.py` ships the `Detector` protocol: `detect(self, text: str, context: DetectorContext) -> tuple[Finding, ...]`, attributes `detector_version: str` and `required_capabilities: frozenset[str]`. The protocol's hard rules: emit `Finding[]` only; MUST NOT mutate `text`; MUST NOT decide disposition; MUST NOT raise HTTP; MUST NOT import a higher layer (`edge`/`admit`/`plan`). GW09 detectors conform to this and build on it, not redefine it.
- `gateway_v2/gateway_v2/domain/finding.py` ships frozen `Finding(detector, detector_version, category, status, confidence, spans, evidence)`, `FindingStatus(EXECUTED | SKIPPED | UNAVAILABLE)`, and `Span(start, end)`. GW09 emits these with BYTE-OFFSET spans; it does NOT redefine them. `evidence` is capped at 256 chars by `__post_init__`.
- `gateway_v2/gateway_v2/domain/category.py` ships `Category` with members `PROMPT_INJECTION`, `JAILBREAK`, `PII`, `SECRET`, `CREDENTIAL`, `COMMAND`, `TOXICITY`, `EXFIL`, `DOS`. GW09 emits the shipped members (PII, SECRET, CREDENTIAL, COMMAND, PROMPT_INJECTION, JAILBREAK); it does NOT add members.
- `gateway_v2/gateway_v2/domain/decision.py` ships frozen `Transformation(kind, span, replacement)`. The GW07 resolver (`resolve/decision.py`) maps a REDACT disposition to `Transformation`s; the `[REDACTED]` placeholder convention is shipped in `egress/output_guard.py` (`REDACTION_PLACEHOLDER = "[REDACTED]"`) and re-declared locally in `resolve/decision.py`. GW09 produces the verifiable spans the resolver consumes.
- `gateway_v2/gateway_v2/audit/record.py` serializes finding SPANS (offsets) and `detector_version`, never the raw matched value.
- The HTTP AST gate `gateway_v2/lint/check_http_outside_edge_resolve.py` allow-lists `{edge, resolve}`; `detect` is excluded, so an HTTP construct inside `detect/` fails the gate. `raise` is banned in `detect/` by a static gate (§10.2.3).
- `unscanned_channels()` lives in `gateway_v2/gateway_v2/domain/channels.py` and must stay `()`.

## Glossary

- **Deterministic_Detectors**: The GW09 deliverable — the PII, secret/credential, and injection/command-signal detectors in `gateway_v2.detect.deterministic`, each conforming to the shipped `Detector` protocol and each a pure function over text.
- **Detector**: The shipped `gateway_v2.detect.base.Detector` protocol. A GW09 detector takes text plus `DetectorContext` and returns `tuple[Finding, ...]`; it emits findings only — it cannot decide disposition, raise HTTP, import a higher layer, or mutate its input.
- **Finding**: The shipped frozen `gateway_v2.domain.finding.Finding`, carrying `detector`, `detector_version`, `category`, `status`, `confidence`, `spans`, `evidence`.
- **Span**: The shipped frozen `gateway_v2.domain.finding.Span(start, end)`. GW09 spans are BYTE OFFSETS into the text the decision inspects.
- **Category**: The shipped `gateway_v2.domain.category.Category`. GW09 emits PII, SECRET, CREDENTIAL, COMMAND, PROMPT_INJECTION, and JAILBREAK.
- **Transformation**: The shipped frozen `gateway_v2.domain.decision.Transformation(kind, span, replacement)` the resolver produces from a REDACT disposition. GW09 produces the verifiable spans a transformation targets.
- **Canonicalization**: The single pass that decodes a text into a canonical form before matching, carrying a span map so a match in canonical space maps back to a byte span in the original text.
- **Decode_Budget**: A declared bound on canonicalization cost (decode depth/expansion/work). When exhausted, canonicalization EXPLICITLY REJECTS rather than silently truncating.
- **Decoder_Set**: The declared set of decoders the canonicalization covers: percent-encoding including double %-encoding, HTML entities, base64, fullwidth, zero-width / Default_Ignorable / Mn control characters, `\u` escapes, Cyrillic/confusable homoglyphs, RTL-reversed keys, and "at/dot" email obfuscation.
- **Multi_Pattern_Matcher**: The single matcher pass over the canonicalized text. There is NO per-pattern loop over the text.
- **Redactor**: The single component that transforms exactly the finding spans and byte-verifies the transformed payload removed what the finding claimed. There is EXACTLY ONE redactor.
- **Byte_Verify**: The check that the transformed bytes no longer contain what a finding's span claimed. A redaction that did not remove what it claimed is a FAILURE (fail-closed), not a pass.
- **Unmaskable_Path**: The fail-closed path taken when a transformation cannot be applied safely: the payload is NEVER dispatched unverified.
- **Signal**: A Finding that contributes to the decision WITHOUT being a terminal block in the detector. Injection and command patterns emit a signal; the plan/resolver decides the action.
- **Ledger_Note**: The short §10.2.2 behaviour-change documentation artifact recording that injection/command were demoted from terminal block to signal.
- **Fixture_Corpus**: A representative LABELED local corpus — benign developer-code family, positive PII/secret fixtures, and obfuscated/encoded fixtures — from which per-family recall and FPR are computed.
- **Developer_Traffic_Family**: The benign developer-code fixture family (inline code, backticks) whose FPR is reported SEPARATELY and must be materially below v1's bare-backtick behaviour.
- **Recall / FPR**: Per-family detection recall and false-positive rate (the card's C3 scoring), computed from the Fixture_Corpus.
- **Detector_Version**: The `detector_version` string the GW08 protocol requires — a hash of the versioned ruleset. Rulesets are versioned and hashed (card rollback rule).
- **Gate**: The GW09 exit gate run from cwd `gateway_v2` under `.venv`: full pytest green (baseline 1752 passed / 95 skipped / 1 xfailed, plus new tests), `mypy --strict`, `ruff check gateway_v2 state_control audit_control load_harness lint tests`, `lint-imports`, the 6 AST gates each exiting 0, and `unscanned_channels() == ()`.

## Requirements

### Requirement 1: Detector-protocol-conforming deterministic detectors

**User Story:** As a security engineer, I want the deterministic detectors to conform to the shipped Detector protocol and run in-process, so that they are uniform, replaceable, and incapable of deciding anything.

#### Acceptance Criteria

1. THE Deterministic_Detectors SHALL be defined in the `gateway_v2.detect.deterministic` package.
2. THE Deterministic_Detectors SHALL implement the shipped `gateway_v2.detect.base.Detector` protocol, exposing a `detect` method that accepts input text plus a `DetectorContext` and returns a `tuple[Finding, ...]`.
3. THE Deterministic_Detectors SHALL expose a `detector_version` string attribute and a `required_capabilities` `frozenset[str]` attribute.
4. WHEN a Deterministic_Detector produces findings, THE Deterministic_Detector SHALL leave its input text unchanged.
5. THE Deterministic_Detectors SHALL run in-process without invoking a GuardBackend.
6. WHERE a Deterministic_Detector cannot execute, THE Deterministic_Detector SHALL emit a Finding with `status` `UNAVAILABLE` rather than omitting the finding or presenting an empty result as clean.

### Requirement 2: Single canonicalization with a declared decoder set and bounded decode budget

**User Story:** As a security engineer, I want one canonicalization pass with a declared decoder set and a bounded decode budget, so that obfuscated input is normalized once and never stalls the event loop or is silently truncated.

#### Acceptance Criteria

1. THE Canonicalization SHALL normalize input text in a single pass before matching.
2. THE Canonicalization SHALL cover the declared Decoder_Set: percent-encoding including double percent-encoding, HTML entities, base64, fullwidth characters, zero-width / Default_Ignorable / Mn control characters, `\u` escapes, Cyrillic/confusable homoglyphs, RTL-reversed keys, and "at/dot" email obfuscation.
3. THE Canonicalization SHALL carry a span map so a match in canonical form maps back to a byte-offset Span in the original text.
4. THE Canonicalization SHALL enforce a declared Decode_Budget bounding decode cost.
5. IF the Decode_Budget is exhausted, THEN THE Canonicalization SHALL explicitly reject the input rather than silently truncating it.
6. THE Canonicalization SHALL evaluate the Decode_Budget bound before performing the expansion-heavy canonicalization work.

### Requirement 3: Single multi-pattern matcher with no per-pattern loop

**User Story:** As a security engineer, I want one multi-pattern matcher pass over the canonicalized text, so that detection cost is a single pass rather than a loop over the text per pattern.

#### Acceptance Criteria

1. THE Multi_Pattern_Matcher SHALL match all deterministic patterns against the canonicalized text in a single pass.
2. THE Multi_Pattern_Matcher SHALL NOT iterate over the text once per pattern.
3. WHERE a pattern acts as a superset prefilter, THE Multi_Pattern_Matcher SHALL confirm the match with the precise matcher before a Finding is emitted.
4. THE Multi_Pattern_Matcher SHALL use the Python `re` engine as the local default rather than Hyperscan.

### Requirement 4: PII, secret, and credential detector families

**User Story:** As a security engineer, I want detector families for PII, secrets, and credentials, so that sensitive data is detected with byte-offset spans and the correct shipped Category.

#### Acceptance Criteria

1. THE Deterministic_Detectors SHALL detect PII (including email addresses, phone numbers, and SSN-like values) and emit findings with `category` `PII`.
2. THE Deterministic_Detectors SHALL detect secrets and credentials (including API keys, AWS keys, tokens, PEM/private-key headers, and card numbers) and emit findings with `category` `SECRET` or `CREDENTIAL`.
3. WHEN a detector family matches sensitive content, THE Deterministic_Detectors SHALL emit each Finding with byte-offset Spans locating the match in the inspected text.
4. THE Deterministic_Detectors SHALL emit findings whose `category` is a shipped `gateway_v2.domain.category.Category` member.

### Requirement 5: One byte-verified redactor with unmaskable fail-closed semantics

**User Story:** As a security engineer, I want exactly one redactor whose output is byte-verified against the finding spans, so that the bytes the resolver inspected are the bytes that leave and nothing is dispatched unverified.

#### Acceptance Criteria

1. THE Redactor SHALL be the single redactor through which the inspected bytes are transformed, so that the bytes the resolver inspected are the bytes dispatch sends.
2. WHEN the Redactor transforms a payload, THE Redactor SHALL byte-verify that the transformed payload no longer contains what each targeted Finding span claimed.
3. IF the transformed payload still contains what a Finding span claimed, THEN THE Redactor SHALL treat the result as a failure and fail closed rather than passing it.
4. IF a transformation cannot be applied safely, THEN THE Redactor SHALL run the rule's Unmaskable_Path and SHALL NOT dispatch the payload unverified.
5. THE Redactor SHALL expose a verify function that checks a transformed payload against the Finding spans, so that the dispatch-layer re-verify (GW11) can reuse it.

### Requirement 6: Injection and command patterns demoted to signal, with a ledger note

**User Story:** As a security engineer, I want injection and command patterns to emit a signal instead of a terminal block, so that benign inline code is not hard-blocked and the plan decides the action.

#### Acceptance Criteria

1. WHEN an injection or command pattern matches, THE Deterministic_Detectors SHALL emit a Finding as a signal and SHALL NOT produce a terminal block.
2. THE Deterministic_Detectors SHALL emit injection and command findings with `category` `PROMPT_INJECTION`, `JAILBREAK`, or `COMMAND`.
3. WHEN a benign inline-code prompt containing a backtick pair is scanned, THE Deterministic_Detectors SHALL NOT terminally block it.
4. THE GW09 deliverable SHALL include a Ledger_Note recording the §10.2.2 behaviour change that injection and command patterns are demoted from terminal block to signal.

### Requirement 7: Per-family recall and FPR scoring on a local fixture corpus

**User Story:** As a security engineer, I want per-family recall and false-positive rate computed from a labeled local fixture corpus, so that the backtick fix is proven rather than asserted.

#### Acceptance Criteria

1. THE GW09 deliverable SHALL include a representative labeled Fixture_Corpus containing a benign Developer_Traffic_Family, positive PII/secret fixtures, and obfuscated/encoded fixtures.
2. THE GW09 deliverable SHALL compute and publish recall and FPR per detector family from the Fixture_Corpus.
3. THE GW09 deliverable SHALL report the Developer_Traffic_Family FPR separately from the other families.
4. THE Developer_Traffic_Family FPR SHALL be materially below v1's bare-backtick terminal-block behaviour.
5. THE GW09 deliverable SHALL compute Fixture_Corpus scores as outcomes without tuning a pattern to make an individual corpus case pass.

### Requirement 8: Purity, determinism, versioned-hashed ruleset, layer and AST constraints

**User Story:** As a backend engineer, I want the detectors to be pure, deterministic, versioned, and layer-clean, so that detection is reproducible, auditable, and structurally correct.

#### Acceptance Criteria

1. THE Deterministic_Detectors SHALL be pure functions over text: the same input text SHALL yield the same findings.
2. THE Deterministic_Detectors SHALL declare any value type as a frozen `@dataclass(frozen=True, slots=True)`.
3. THE Deterministic_Detectors SHALL derive `detector_version` from a hash of the versioned ruleset.
4. THE Deterministic_Detectors SHALL NOT express a capacity value as a dataclass field.
5. THE Deterministic_Detectors SHALL NOT introduce module-level mutable state.
6. THE Deterministic_Detectors SHALL NOT introduce a new HTTP type and SHALL NOT raise an HTTP response within the `detect` layer.
7. THE Deterministic_Detectors SHALL import no higher layer (`edge`, `admit`, or `plan`), importing only `gateway_v2.domain` and the GW08 `detect` siblings.

### Requirement 9: Local acceptance tests

**User Story:** As a security engineer, I want the local half of the LGW09 acceptance tests to pass, so that the defects are proven fixed locally while the live-only tests are explicitly deferred.

#### Acceptance Criteria

1. WHEN five benign inline-code prompts from the Developer_Traffic_Family are scanned (LGW09-1), THE Deterministic_Detectors SHALL produce zero terminal blocks and SHALL present the findings as signal only.
2. WHEN a secret is split across a chunk and a UTF-8 boundary (LGW09-3), THE Deterministic_Detectors SHALL produce spans whose redaction removes the secret with no leak and a valid protocol frame.
3. WHEN an obfuscated or encoded PII fixture is scanned (LGW09-4), THE Deterministic_Detectors SHALL detect it within the declared Decode_Budget or explicitly reject it on budget, never silently missing it.
4. WHEN a transformation is deliberately made a no-op (LGW09-5), THE Redactor SHALL detect the no-op pre-dispatch, run the Unmaskable_Path, and SHALL NOT dispatch the payload.
5. WHEN the C3 scoring run executes (LGW09-6), THE GW09 deliverable SHALL publish per-family recall and FPR from the Fixture_Corpus with the Developer_Traffic_Family FPR materially below v1's.

### Requirement 10: Gate

**User Story:** As a backend engineer, I want the GW09 exit gate to pass, so that the deliverable meets the clean-room backend quality bar.

#### Acceptance Criteria

1. THE Gate SHALL run from cwd `gateway_v2` under the `.venv` environment.
2. THE Gate SHALL pass the full pytest suite green at the baseline of 1752 passed, 95 skipped, 1 xfailed, plus the new GW09 tests.
3. THE Gate SHALL pass `mypy --strict`.
4. THE Gate SHALL pass `ruff check gateway_v2 state_control audit_control load_harness lint tests`.
5. THE Gate SHALL pass `lint-imports`.
6. THE Gate SHALL exit 0 on each of the 6 AST gates.
7. THE Gate SHALL confirm `unscanned_channels()` returns an empty tuple.

### Requirement 11: Deferred and out-of-scope boundaries

**User Story:** As a security engineer, I want the deferred and out-of-scope work named explicitly, so that GW09 delivers only the local half and does not absorb other cards' responsibilities.

#### Acceptance Criteria

1. THE GW09 deliverable SHALL defer LGW09-2 (the PII-redact streaming/recorder assertion) to GW15, while the local half still asserts the detector produces spans a redactor removes.
2. THE GW09 deliverable SHALL defer the dispatch-layer re-verify of the transformed payload before the provider call to GW11, providing only the verifiable spans and the verify function rather than the provider-call wiring.
3. THE GW09 deliverable SHALL defer the Hyperscan matcher engine as a performance optimization, using the Python `re` engine as the local default.
4. THE GW09 deliverable SHALL defer the full production C3 corpus and signed numbers, building a representative local Fixture_Corpus and computing per-family recall/FPR from it.
5. THE GW09 deliverable SHALL defer the GW02 parity-harness and expected-diff machinery, while still producing the Ledger_Note for the injection-demoted-to-signal behaviour change.
6. THE GW09 deliverable SHALL exclude the semantic detector (GW10) and the real guard model.
