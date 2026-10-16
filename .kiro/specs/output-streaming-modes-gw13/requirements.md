# Requirements Document

## Introduction

This feature implements the locally-buildable half of card **GW13** of the AI Mesh Firewall v3 backend rewrite (`gateway_v2/`), as defined in `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` §10.9 (header `## GW13 — Implement output enforcement and the two explicit streaming modes`) together with its **v2.1 CORRECTION (C4, C21, C25)**. The runbook is the sole source of truth; every requirement below is traceable to the GW13 card text, the C4/C21/C25 correction, or one of the live acceptance tests LGW13-1..6.

The GW13 objective, verbatim from the card, is: *"Make the output path use the same resolver as the input path, and make the two streaming modes honestly distinct at the wire and in the latency claim."*

A consolidation audit found GW13 **PARTIAL**: the output-enforcement glue is shipped under R2-06 / GW12b (`egress/stream.py::StreamPipeline`, `egress/output_guard.py`, the holdback engine), but GW13's distinct **STRICT_WITHHOLD** mode is unbuilt (`egress/strict.py` is an empty stub `"""Whole-response withhold mode — empty until GW13."""`, `__all__ = ()`), and the plan compiler rejects STRICT_WITHHOLD at compile time (`plan/compiler.py` `SUPPORTED_STREAMING = frozenset({StreamingMode.INCREMENTAL})`). This spec builds the two explicit streaming modes, wires both through the one resolver at `phase=OUTPUT`, lets a plan select STRICT_WITHHOLD, and makes the produced mode visible and separately latency-labelled.

### What is already shipped (reused, NOT rebuilt)

- **The INCREMENTAL release loop** — `egress/stream.py::StreamPipeline` (holdback scan, force-release trade-off, in-flight kill, boundary safety, separated latency / held-token metrics). GW13 adds STRICT_WITHHOLD **alongside** this; it does not rewrite the INCREMENTAL pipeline.
- **The one resolver** — `resolve/resolver.py::resolve(findings, plan, phase) -> Decision` is pure, total, and already phase-aware: `_select_rules(plan, phase)` selects phase-admitted rules, UNAVAILABLE findings route through each ENFORCE rule's `on_unavailable` posture (never a blanket redact), and MONITOR rules contribute nothing to disposition. The output-side enforcement semantics of LGW13-3 and LGW13-4 are therefore **already correct** in the shipped resolver; GW13's job is to ROUTE output through it from both modes.
- **The one-resolver seam** — `egress/output_guard.py`: the `OutputResolver` protocol (GW08's `resolve` satisfies it), `apply_decision(released, decision, base_offset)` (fail-closed byte discipline, raises `OutputBlocked` on a BLOCK disposition, fails closed on a partially-covered redaction span), and `enforcing_output_rules(plan)`.
- **The plan vocabulary** — `domain/plan.py`: `StreamingMode(INCREMENTAL, STRICT_WITHHOLD)` already exists, `ExecutionPlan.streaming_mode` carries it, and `Phase(INPUT, OUTPUT)` already exists.
- **The C21 output-field coverage** — `domain/channels.py`: every text-bearing OUTPUT field (`output_content`, `output_refusal`, `output_reasoning`, `output_tool_arguments`, `logprobs`, `structured_output`) is `scanned=True`, so `unscanned_channels() == ()`. The multi-field output-channel scanning the C21 correction names is already shipped under GW12b / the channels model; GW13-local scope is only that BOTH modes route output through the one resolver over the shipped channels and that neither mode creates a field the scanner skips.
- **The C4 split + holdback bound** — `runtime/holdback_config.py`, `runtime/holdback_metrics.py` (the separate `T_release_processing` vs `T_holdback_wait` series) and the B5 `runtime/latency_slo`. GW13-local reuses these; the signed / measured numbers are the deferred GW20 gate.
- **The bounded O(1) holdback (C25)** — shipped in GW12b. STRICT_WITHHOLD is a **distinct mode**, not a holdback variant; GW13 does not rebuild the holdback engine.

### Local-only / deferred constraint (standing)

The operator has no cloud resources and GW13's measured-latency gate moves to GW20. Everything below MUST be verifiable locally. The deferred items are enumerated in Requirement 10.

## Glossary

- **Gateway**: The `gateway_v2` AI Mesh Firewall serving process that terminates client requests, forwards to an upstream provider, and enforces output guards.
- **Resolver**: The single pure decision authority `resolve/resolver.py::resolve(findings, plan, phase) -> Decision`. It is the only disposition-producing site for both phases.
- **Phase**: The `domain.plan.Phase` enum member — `INPUT` or `OUTPUT` — passed to the Resolver so it selects phase-admitted rules.
- **Output_Resolution**: Resolution of output findings via `resolve(findings, plan, Phase.OUTPUT)`. There is no separate `enforce_output` function.
- **Streaming_Mode**: The `domain.plan.StreamingMode` enum member carried on `ExecutionPlan.streaming_mode` — `INCREMENTAL` or `STRICT_WITHHOLD`.
- **INCREMENTAL**: The streaming mode in which detectors run on a content-defined schedule and content is released as it is cleared, implemented by the shipped `egress/stream.py::StreamPipeline`.
- **STRICT_WITHHOLD**: The streaming mode in which no content byte is released downstream until the whole response has been inspected and the Resolver has decided, implemented in `egress/strict.py`.
- **First_Content_Byte**: The first assistant content byte that would be released downstream for a response.
- **Truthful_Block**: A BLOCK disposition that withholds every prohibited assistant byte. In INCREMENTAL a Truthful_Block is expressible ONLY before the First_Content_Byte. STRICT_WITHHOLD can always express a Truthful_Block because it withholds everything until the whole response is inspected.
- **Terminate_With_Declared_Error**: Ending an INCREMENTAL stream, after the First_Content_Byte, with a declared terminal error frame — NOT a silent truncation and NOT a claim that an already-delivered prefix was blocked.
- **Redact_Not_Yet_Released**: Redacting only content that has not yet been released downstream — one of the two honest INCREMENTAL outcomes after the First_Content_Byte.
- **Truncation_As_Block**: Presenting a mid-stream cut of an already-delivered prefix as if it were a BLOCK. This is forbidden (the v1 `secure_streaming.py` defect the card exists to remove).
- **Produced_Mode**: The Streaming_Mode that actually produced a given response, carried on the response as a backend field / header so the mode is visibly distinct.
- **Degenerate_To_Strict**: The condition where a provider emits the whole response in one chunk, so an INCREMENTAL stream had no opportunity to release before the whole response was present and the per-response behaviour matches STRICT_WITHHOLD; the trace records that this occurred.
- **Chunk_Boundary**: The point between two upstream deltas of a text channel. A secret or sensitive match may span a Chunk_Boundary.
- **on_unavailable**: The per-rule `FailurePosture` (`FAIL_OPEN` / `FAIL_CLOSED`) the Resolver applies to an UNAVAILABLE finding (`domain.plan.Rule.on_unavailable`).
- **MONITOR**: The `domain.plan.Mode.MONITOR` rule mode — the finding is recorded but contributes nothing to disposition (never enforces).
- **ResourceContract**: The capacity authority (`runtime/resources.py`) — the only module permitted capacity-position literals. Buffer / high-water bounds for STRICT_WITHHOLD derive from here or from `HoldbackConfig`; GW13 introduces no literal high-water mark.
- **Compiler**: `plan/compiler.py::compile_plan` and its `SUPPORTED_STREAMING` set, which gate whether a plan may select a Streaming_Mode.
- **T_release_processing**: The in-SLO latency series (gateway compute to release a byte) from the shipped `runtime/holdback_metrics.py`.
- **T_holdback_wait**: The separately-published latency series (time a released byte waited on disambiguating upstream bytes) from the shipped C4 split.

## Requirements

### Requirement 1: One resolver for both phases on the output path

**User Story:** As a security engineer, I want output enforcement to use the same resolver as input enforcement, so that a degraded output scan can never make the action independent of the verdict (the v1 P3/P6/P7 defects).

#### Acceptance Criteria

1. THE Gateway SHALL resolve output findings through `resolve(findings, plan, Phase.OUTPUT)` as the single decision authority.
2. THE Gateway SHALL NOT define or call a separate `enforce_output` output-enforcement function with its own downgrade rules.
3. WHERE the Streaming_Mode is INCREMENTAL, THE Gateway SHALL route released output bytes through the same Resolver.
4. WHERE the Streaming_Mode is STRICT_WITHHOLD, THE Gateway SHALL route the inspected output through the same Resolver.
5. WHEN output findings are resolved, THE Gateway SHALL apply the resulting `Decision` through the shipped `egress/output_guard.py::apply_decision`, which withholds bytes on a BLOCK disposition and fails closed on a partially-covered redaction span.

### Requirement 2: INCREMENTAL mode — truthful BLOCK only before the first content byte

**User Story:** As a security engineer, I want INCREMENTAL streaming to be honest about BLOCK, so that an operator who selected BLOCK is never served a truncated prefix presented as a block.

#### Acceptance Criteria

1. THE INCREMENTAL mode SHALL run detectors on a content-defined schedule and release cleared content as the shipped `StreamPipeline` already does.
2. WHILE no First_Content_Byte has been released, THE INCREMENTAL mode SHALL express a BLOCK disposition as a Truthful_Block that releases zero prohibited assistant bytes.
3. WHEN a BLOCK disposition arises after the First_Content_Byte has been released, THE INCREMENTAL mode SHALL end the stream with Terminate_With_Declared_Error.
4. WHEN sensitive content that has not yet been released is detected after the First_Content_Byte, THE INCREMENTAL mode SHALL apply Redact_Not_Yet_Released rather than retracting delivered bytes.
5. THE INCREMENTAL mode SHALL NOT present a mid-stream cut of an already-delivered prefix as a BLOCK (no Truncation_As_Block).
6. WHEN ending a stream after the First_Content_Byte, THE INCREMENTAL mode SHALL NOT claim that an already-delivered prefix was blocked.
7. IF a Truthful_Block cannot be expressed in INCREMENTAL mode for a given response, THEN THE INCREMENTAL mode SHALL declare BLOCK unsupported for that case rather than faking a truncation, and that limitation SHALL be documented.

### Requirement 3: STRICT_WITHHOLD mode — whole-response inspection before any release

**User Story:** As a tenant with strict output requirements, I want a mode that withholds all content until the whole response is inspected, so that a BLOCK on any part of the response is always a truthful block.

#### Acceptance Criteria

1. THE STRICT_WITHHOLD mode SHALL be implemented in `egress/strict.py` (currently an empty stub).
2. WHILE the whole response has not been inspected, THE STRICT_WITHHOLD mode SHALL release zero content bytes downstream.
3. WHEN the whole response has been inspected, THE STRICT_WITHHOLD mode SHALL resolve the response through `resolve(findings, plan, Phase.OUTPUT)` and apply the resulting `Decision` through `apply_decision`.
4. WHEN the resolved output `Decision` is a BLOCK disposition, THE STRICT_WITHHOLD mode SHALL release zero prohibited assistant bytes before the terminal result (LGW13-1).
5. THE STRICT_WITHHOLD mode SHALL reuse the shipped `OutputResolver` and `apply_decision` seam rather than defining a second output decision site.
6. THE STRICT_WITHHOLD mode SHALL be a distinct mode and SHALL NOT be implemented as a variant of the shipped bounded holdback engine (C25).

### Requirement 4: Compiler accepts STRICT_WITHHOLD

**User Story:** As a tenant administrator, I want to select STRICT_WITHHOLD for my plan, so that the compiler produces a plan carrying that mode instead of rejecting it.

#### Acceptance Criteria

1. THE Compiler SHALL include STRICT_WITHHOLD in `SUPPORTED_STREAMING` so a plan may select it.
2. WHEN a tenant selection names `streaming_mode = STRICT_WITHHOLD`, THE Compiler SHALL compile a plan whose `ExecutionPlan.streaming_mode` is STRICT_WITHHOLD.
3. WHEN a tenant selection names `streaming_mode = INCREMENTAL`, THE Compiler SHALL continue to compile a plan whose `ExecutionPlan.streaming_mode` is INCREMENTAL.
4. IF a tenant selection names a Streaming_Mode outside `SUPPORTED_STREAMING`, THEN THE Compiler SHALL raise `CompileError` and the previous plan SHALL keep serving.

### Requirement 5: The produced mode is visible on the response

**User Story:** As an operator, I want each response to carry which streaming mode produced it, so that the two modes are visibly distinct at the wire.

#### Acceptance Criteria

1. WHEN a response is produced, THE Gateway SHALL stamp the Produced_Mode on the response as a backend field or header.
2. WHERE the Streaming_Mode selected by the plan is INCREMENTAL, THE Gateway SHALL stamp the Produced_Mode consistent with how the response was actually produced.
3. WHERE the Streaming_Mode selected by the plan is STRICT_WITHHOLD, THE Gateway SHALL stamp the Produced_Mode as STRICT_WITHHOLD.
4. THE Gateway SHALL make the Produced_Mode available to callers of the backend response path.

### Requirement 6: Provider single-chunk response degenerates INCREMENTAL to strict

**User Story:** As an operator, I want the trace to tell me when an INCREMENTAL response could not stream incrementally, so that a single-chunk provider response is not misreported as incremental delivery.

#### Acceptance Criteria

1. WHEN the Streaming_Mode is INCREMENTAL and the provider emits the whole response in one chunk, THE Gateway SHALL inspect the whole response before releasing content, matching STRICT_WITHHOLD behaviour for that response (Degenerate_To_Strict).
2. WHEN a response Degenerates_To_Strict, THE Gateway SHALL record in the trace that the INCREMENTAL stream degenerated to strict for that response.

### Requirement 7: Boundary safety in both modes

**User Story:** As a security engineer, I want a secret spanning a chunk boundary to be caught in either mode, so that chunking never leaks a sensitive match (LGW13-6).

#### Acceptance Criteria

1. WHEN a sensitive match spans a Chunk_Boundary and the Streaming_Mode is INCREMENTAL, THE Gateway SHALL NOT release any prohibited byte of that match.
2. WHEN a sensitive match spans a Chunk_Boundary and the Streaming_Mode is STRICT_WITHHOLD, THE Gateway SHALL NOT release any prohibited byte of that match.
3. THE Gateway SHALL preserve the shipped INCREMENTAL boundary-safety behaviour (`StreamPipeline` holds the minimal matchable suffix and re-scans the whole accumulated channel text) without modification.

### Requirement 8: Resolver-owned UNAVAILABLE and MONITOR semantics hold for output

**User Story:** As a security engineer, I want an output BLOCK with the detector UNAVAILABLE to follow the rule's posture and a MONITOR output rule to not enforce, so that the v1 P3/P6/P7 defects do not reappear on the output path.

#### Acceptance Criteria

1. WHEN an output finding is UNAVAILABLE, THE Resolver SHALL apply the rule's `on_unavailable` posture and SHALL NOT apply a blanket redact (LGW13-3).
2. IF every ENFORCE rule selecting an UNAVAILABLE output finding has `on_unavailable = FAIL_CLOSED`, THEN THE Resolver SHALL yield a BLOCK candidate for that finding.
3. WHERE an output rule is in MONITOR mode, THE Gateway SHALL deliver the content and record the finding, and THE MONITOR rule SHALL NOT enforce (LGW13-4).
4. THE Gateway SHALL exercise the output-phase UNAVAILABLE posture (LGW13-3) and MONITOR non-enforcement (LGW13-4) through both Streaming_Modes to confirm the shipped resolver behaviour holds on the output path.

### Requirement 9: Separate latency labelling for the two modes

**User Story:** As an operator, I want each streaming mode's latency qualified separately, so that STRICT_WITHHOLD is never quoted with the INCREMENTAL time-to-first-token claim.

#### Acceptance Criteria

1. THE Gateway SHALL label the latency of INCREMENTAL and STRICT_WITHHOLD as two separate profiles.
2. THE STRICT_WITHHOLD mode SHALL NOT inherit the INCREMENTAL time-to-first-token claim.
3. THE INCREMENTAL mode SHALL reuse the shipped C4 split (`T_release_processing` in the SLO and `T_holdback_wait` published separately) for its latency qualification without redefining it.
4. THE STRICT_WITHHOLD mode SHALL be qualified by its completion overhead, labelled distinctly from the INCREMENTAL release-lag and first-content-delay profile.
5. THE Gateway SHALL NOT introduce a literal high-water or buffer bound; STRICT_WITHHOLD buffer bounds SHALL derive from `ResourceContract` or `HoldbackConfig`.

### Requirement 10: Non-goals and deferred boundaries

**User Story:** As a reviewer, I want the GW13-local scope boundaries named, so that the deferred cloud / UI / shipped-elsewhere work is explicit and not re-implemented here.

#### Acceptance Criteria

1. THE feature SHALL treat the measured latency numbers and the two signed latency profiles (LGW13-5) as DEFERRED to GW20; GW13-local SHALL prove only that the modes are mechanically distinct and separately labelled.
2. THE feature SHALL treat the console mode-visibility UI (what the tenant sees and what it costs) as DEFERRED to a UI card; GW13-local stamps the Produced_Mode on the backend response only.
3. THE feature SHALL treat the bounded O(1) holdback engine, force-release, and trade-off (C25 / R2-06 / GW12b) as shipped and SHALL NOT rebuild them.
4. THE feature SHALL treat the full text-bearing output-field scanning (logprobs tokens and bytes, annotations, tool/function arguments, structured outputs) as shipped where `domain/channels.py::unscanned_channels()` returns empty, and SHALL scope GW13 to the modes plus one-resolver wiring; a genuinely unscanned field SHALL be flagged rather than silently expanded into GW13.
5. THE feature SHALL treat the real provider socket and live wire captures as a DEFERRED live gate.
6. THE feature SHALL preserve the repository gates — `mypy --strict`, `ruff`, `lint-imports` (the layer order `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`, with `egress` forbidden from importing `detect`), and the 7 AST gates — and SHALL keep the offline suite green against its baseline.

## Traceability to the GW13 card

- **Objective (one resolver + honestly distinct modes)** → Requirements 1, 5, 9.
- **INCREMENTAL truthful-BLOCK-before-first-byte + terminate / redact-not-yet-released + stop-rule** → Requirement 2 (card "Implementation work", "Rollback / stop rule", "Agent execution notes").
- **STRICT_WITHHOLD built here, selected per plan, qualified separately** → Requirements 3, 4, 9.
- **Mode visible on the response** → Requirement 5 (console part deferred, Requirement 10.2).
- **Single-chunk degenerates to strict and the trace says so** → Requirement 6 (card "Failure scenarios").
- **C4 split + signed holdback bound reused; LGW13-5 → GW20** → Requirements 9, 10.1.
- **C21 scan every text-bearing output field** → Requirement 10.4 (shipped where `unscanned_channels() == ()`).
- **C25 bounded O(1) holdback not rebuilt; STRICT distinct** → Requirements 3.6, 10.3.
- **LGW13-1** (unsafe whole response, STRICT_WITHHOLD → zero prohibited bytes) → Requirement 3.4.
- **LGW13-2** (unsafe mid-stream, INCREMENTAL → declared termination, no truncation-as-block) → Requirements 2.3, 2.5, 2.6.
- **LGW13-3** (output BLOCK, detector UNAVAILABLE → on_unavailable posture, never blanket redact) → Requirements 8.1, 8.2, 8.4.
- **LGW13-4** (output rule MONITOR → delivered, recorded, non-enforcing) → Requirements 8.3, 8.4.
- **LGW13-5** (two separate latency profiles; strict never quoted as incremental) → Requirements 9.1, 9.2 (measured numbers deferred to GW20, Requirement 10.1).
- **LGW13-6** (secret spanning a chunk boundary in both modes → no leak) → Requirement 7.
- **Exit criteria** (one resolver for both phases; two modes implemented, selectable, visibly distinct; separate latency qualification; no truncation-as-block; boundary safety in both) → Requirements 1–9 collectively.
