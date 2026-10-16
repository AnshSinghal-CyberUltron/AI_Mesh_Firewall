# Design Document

## Overview

GW10 fills `gateway_v2/gateway_v2/detect/semantic/` with the **local half** of semantic
injection detection: tokenizer-aware input windowing, the per-request false-positive-rate
(FPR) budget algebra, language posture, confusable mapping, role-scoped classification, and
the `Semantic_Detector` that drives the shipped GW08 `GuardBackend` and emits
`Finding[]`. It is grounded verbatim in the runbook GW10 card (§10.9,
`## GW10 — Implement semantic detection, windowing and the per-request FPR budget`, ~L5117),
its v2.1 CORRECTION (C6, C7), §10.5.1 (the C21 role-scoped channel model), §10.4 step ④ (the
async request lifecycle), and the measured §0.2/PG2 facts. The runbook is the sole source of
truth; this design does exactly what it and the 11 requirements say and no more.

The feature exists because the semantic model's published operating point is **per window, not
per request**, and FPR compounds across windows: `request_fpr ≈ 1 − (1 − per_window_fpr)^W`.
A model at ~1% per-window FPR sees ~1.99% at two windows and ~6.79% at seven — roughly 20 false
blocks/second at 1,000 RPS in ENFORCE. §10.2.2 requires the per-request FPR be published before
any customer-facing claim. GW10 derives the confidence threshold from a declared ceiling and the
actual window count so the per-request FPR stays within the ceiling by construction.

### Scope: local half only

In scope (ships in `detect/semantic/` plus one test file): the windowing algebra, the FPR-budget
derivation algebra, the per-language posture, confusable mapping, role scoping, separate
multilingual reporting, and the `Semantic_Detector` over the shipped `local_onnx` backend and a
deterministic fake.

Deferred (named in §Deferred Scope, R11): the real guard **model** and its measured recall / FPR /
AUC numbers (→ GW20 / T01); GPU throughput and the 20 ms profile (→ GW20); the signed 22M-vs-86M
Indic trade (→ T01); the production tokenizer **bytes** (a declared seam — only the pinned hash and
the chars-per-token model are local); and the real request-message → role-segment lifecycle folding
(→ GW15). The local half tests the **mechanism** against a declared/configured per-window FPR input,
not the model's measured curve.

### Two resolved dependencies

The requirements flagged two dependencies. Both are **resolved from the runbook** and implemented
here as design decisions (not asked of the user).

**Resolution 1 — the sync/async bridge.** §10.5.1 defines `GuardBackend.classify` as `async` and
§10.4 step ④ runs detectors in the async request path, but the shipped `detect/base.Detector.detect`
is synchronous. **Decision: the semantic detector's real surface is async.** Its primary method is
`async def classify_input(...) -> tuple[Finding, ...]`; the §10.4 async lifecycle awaits it directly
and never blocks the event loop. For uniformity with the sync `Detector` protocol where a sync call
is genuinely unavoidable (and only at a true sync boundary, never inside an already-async caller),
a thin shim `detect(text, context)` wraps the async method via the shipped house idiom
`def _run[T](coro) -> T: return asyncio.run(coro)` — the exact idiom used in
`tests/state_control/test_lgw05c_writer.py:49` and the GW08 tests. The hot path (§10.4) awaits
`classify_input`; `asyncio.run` is used only in the sync shim and in tests, never on the request path.

**Resolution 2 — role scoping.** §10.5.1 (C21) is explicit: "role-scoped semantic rules (system
prompts and assistant history are not user input)" and detectors scan "the joined text of each
message's parts" with role awareness. The shipped `DetectorContext(phase, rules, budget)` carries
**no role field** (GW04 built it minimal; C21's full channel typing is a GW04/GW15 concern).
**Decision: GW10 defines its own typed role input.** The async surface takes a sequence of
role-tagged segments — a frozen `RoleScopedSegment(role: SegmentRole, text: str)` where `SegmentRole`
∈ `{SYSTEM, USER, ASSISTANT, TOOL}` — which the detector windows and classifies with role awareness,
applying each rule only to the roles the rule's scope admits (user input by default; system/assistant
history excluded unless the rule opts in). GW10 owns the role-aware windowing+classification
**mechanism** over this typed input; **GW15 wires the real message → segment folding** from the
OpenAI request (C21 is explicitly "before GW04 ... exercised by GW15"). `domain/plan.Rule` carries
`scope: RuleScope` (INPUT | OUTPUT | BOTH) but no message-role axis, so the role-admission set is a
GW10-local concept layered over the rule; a `role_scope` is resolved per rule from the plan/config
(see §Components).

## Architecture

The detector is a thin orchestrator over pure algebra. Everything that can be a pure function of its
inputs (windowing, FPR-budget derivation, aggregation, language posture, confusable fold) is pure and
property-testable in isolation; the only I/O is the single `await backend.classify(...)` call.

```mermaid
flowchart TD
    IN["Role-tagged input<br/>tuple[RoleScopedSegment, ...]"] --> SCOPE["Role scoping<br/>admit segments per rule.role_scope<br/>(system/assistant excluded<br/>unless opted in)"]
    SCOPE --> CONF["Confusable mapping<br/>(homoglyph fold BEFORE tokenizer)"]
    CONF --> LANG["Language ID + posture<br/>supported? -> continue<br/>unsupported -> UNAVAILABLE/<br/>unsupported-language finding"]
    LANG -->|supported| WIN["Tokenizer-aware windowing<br/>W = ceil(len/band-chars), overlap,<br/>no 512 truncation, Span offset map"]
    WIN -->|W > max| REJ["Over-length REJECT<br/>actionable error (no middle-drop)"]
    WIN -->|W <= max| BUDGET["FPR-budget derivation<br/>derive_threshold(ceiling, per_window_fpr, W)<br/>via 1-(1-fpr)^W"]
    BUDGET --> ONE["ONE classify(windows, budget) call<br/>(all W windows, never a serial loop)"]
    ONE -->|BackendUnavailable / timeout / not-ready| UNAVAIL["Finding(status=UNAVAILABLE,<br/>confidence=None) + rule failure posture<br/>(never partial-as-complete)"]
    ONE -->|GuardResult[]| AGG["Aggregate: max-score-per-category<br/>across windows; map owning window -> Span"]
    AGG --> THR["Apply Derived_Threshold"]
    THR --> OUT["Finding[]<br/>PROMPT_INJECTION / JAILBREAK<br/>+ recorded W, tokenizer hash, per-language breakdown"]
    LANG -->|unsupported| UNAVAIL
```

### House idiom and layering

`detect/semantic/` mirrors the `detect/holdback.py` / `detect/deterministic/canonicalize.py` house
idiom: `from __future__ import annotations`; frozen `@dataclass(frozen=True, slots=True)` value
types; `MappingProxyType` immutable tables; tuple `__all__`; imports only `gateway_v2.domain`, the
GW08 `detect` siblings (`detect.base`, `detect.guard.backend`, `detect.guard.local_onnx`), and
`gateway_v2.runtime` (a lower layer — a legal import from `detect`). It never imports `edge`,
`admit`, or `plan`. No module-level mutable binding. No capacity/threshold/FPR-ceiling/overlap/
chars-per-token numeric literal lives in a dataclass **field** — such values come from the plan,
`Rule.threshold`, `ResourceContract`, or module/config constants, exactly as
`runtime/resources.py` and `detect/holdback.py` do (verified against `lint/check_capacity_literals.py`,
which flags capacity-named assignments/fields outside `runtime/resources.py`).

## Components and Interfaces

Module layout in `detect/semantic/`:

| Module | Responsibility |
|---|---|
| `roles.py` | `SegmentRole` enum, frozen `RoleScopedSegment(role, text)`, `RoleScope` admission set, and `admit_segments(segments, role_scope)` — pure role filtering. |
| `language.py` | Language ID (local heuristic/stub), the supported-language set, `Language_Posture`, confusable/homoglyph fold applied **before** the tokenizer, and the per-language reporting record. |
| `windowing.py` | Tokenizer-aware input windowing: `TokenizerSeam`, chars-per-token model, the token band + overlap, the `WindowPlan` (windows + `Span` offset map + recorded `W` + pinned tokenizer hash), over-length reject. **Pure.** Distinct from `detect/windowing.py` (byte-windowing for output holdback). |
| `fpr_budget.py` | The pure FPR-budget algebra: `derive_threshold(ceiling, per_window_fpr, W)` via `1 − (1 − fpr)^W`, the ceiling-exceeded recorded condition. **Pure.** |
| `aggregate.py` | The declared `Aggregation_Rule`: fold `GuardResult[]` → request-level per-category result, mapping the owning window index back to a `Span`. **Pure.** |
| `detector.py` | `SemanticDetector`: the async orchestrator (`classify_input`) and the thin sync `Detector`-protocol shim (`detect`). Composes the pure modules + the one `classify` call. |
| `__init__.py` | Tuple `__all__` re-exporting the public surface. |

### Role input (`roles.py`)

```python
class SegmentRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

@dataclass(frozen=True, slots=True)
class RoleScopedSegment:
    role: SegmentRole
    text: str
```

`role_scope` for a rule is a `frozenset[SegmentRole]`, resolved from the plan/config; the default is
`{USER}` (user input only). `admit_segments(segments, role_scope)` returns only the segments whose
role is in the scope — the pure core of Requirement 5.4 and Property P7. A rule scoped to user input
therefore never fires on a system/assistant-only input.

### Language posture + confusable (`language.py`)

Language ID is a lightweight **local** heuristic (e.g. a script/codepoint-range classifier) — the
real multilingual AUC is deferred (R11). The POSTURE is what is local and enforced: a
`SUPPORTED_LANGUAGES` frozenset; an unsupported language yields a **distinct posture** — either a
`Finding(status=UNAVAILABLE, confidence=None)` or an explicit `unsupported-language` finding — and
**never** a silent clean EXECUTED pass (Requirement 5.2, Property P6). Confusable/homoglyph mapping
runs **before** the guard tokenizer (Requirement 5.3); it is self-contained (a confusable table in
the module, in the spirit of the GW09 `canonicalize.py` homoglyph fold) and does not import GW09's
internals. Multilingual behaviour is reported separately via a per-language result record the
caller/audit reads (Requirement 5.5) — distinct from the aggregate English number.

### Tokenizer-aware windowing (`windowing.py`) — Requirement 1, 4

The windowing is a **pure function** of `(text, token_band, overlap, chars_per_token)`:

- **Chars-per-token model (measured, not 4.0).** The band is sized in characters as
  `band_chars = token_band * chars_per_token`, using the measured in-corpus values (3.04 markdown,
  6.28 prose) carried as **module constants** (not dataclass-field literals) — e.g. a 1,024-token
  band is ~3,158 chars of markdown, matching the card's worked figure. The band, overlap, and
  chars-per-token are read from config / `ResourceContract` / module constants, never from a
  dataclass field, so `check_capacity_literals.py` stays green (R1.6, R8.4).
- **Overlap.** Adjacent windows share a declared `overlap` of tokens (⇒ `overlap_chars`) so any
  contiguous match up to one band long appears whole in at least one window (R1.2, Property P1).
- **No 512 truncation.** The shipped tokenizer truncates/pads at 512; GW10 **disables** that and
  windows the WHOLE input (R1.3). Nothing is silently cut.
- **Span offset map.** Each window carries the `(start, end)` char offsets of its slice in the
  original text, so a window-level `GuardResult` maps back to a `domain.finding.Span` in the
  original input.
- **Pinned tokenizer hash.** A `Tokenizer_Hash` identifies the tokenizer version (window count varies
  with tokenizer version) and is recorded on every request (R1.4).
- **Tokenizer seam.** Where the production tokenizer bytes are not loaded, the algebra consumes a
  `TokenizerSeam` (the chars-per-token model, or an available tokenizer) and the windowing algebra is
  **identical** either way (R1.5, R11.4).
- **Over-length.** `W = ceil over the band with overlap`. If `W > max_window_count` (read from
  `ResourceContract`/config, R4.4), the detector **rejects on length with an actionable error** — it
  never applies head-and-tail truncation that drops the middle (R4.2, R4.3, Property P2). Within the
  max, **every** window is classified (R4.1, Property P1). This is the explicit replacement for v1's
  `_head_tail` silent hole (`bedrock_scanner.py:63-72`).

The returned `WindowPlan(windows: tuple[str, ...], spans: tuple[Span, ...], window_count: int,
tokenizer_hash: str)` is frozen+slotted.

### One classify call + aggregation (`aggregate.py`) — Requirement 2

The detector builds the full window sequence and calls `await backend.classify(windows, budget)`
**exactly once** with ALL windows (R2.1); it never loops `classify` per window (R2.2, Property P4).
This reconciles the card body ("one backend call") with GW08 C7 (batch-1 FIFO is the owner's
internal choice): "one backend call" means one `classify` invocation from the detector, not a serial
loop; the backend decides its internal batching.

**Declared `Aggregation_Rule`: max-score-per-category across windows.** For each semantic category
(`PROMPT_INJECTION`, `JAILBREAK`), the request-level score is the maximum `GuardResult.score` over all
windows for that category, and the emitted `Span` is the offset of the window that produced that
maximum. Rationale: an injection present in any single window must surface at the request level
(a mean/min would let a single hostile window be diluted by benign ones — the opposite of the card's
"middle-window miss is a failure" stance). `W` is recorded on every request (R2.4). Emitted semantic
findings carry only `Category.PROMPT_INJECTION` or `Category.JAILBREAK` (R2.5).

### FPR-budget derivation (`fpr_budget.py`) — Requirement 3

The pure algebra, a function of `(ceiling, per_window_fpr, W)`:

```
request_fpr(per_window_fpr, W) = 1 − (1 − per_window_fpr)^W
```

To hold the per-request FPR at or below the ceiling across `W` independent windows, the per-window
operating point must have a per-window FPR of at most:

```
per_window_fpr_budget(ceiling, W) = 1 − (1 − ceiling)^(1/W)
```

`derive_threshold(ceiling, per_window_fpr, W) -> float` returns the per-window confidence threshold
that corresponds to that per-window FPR budget for the declared model operating point. The declared
`Per_Window_FPR` input (the model operating point, ~1% per the card) is a **configured/declared local
value**; the **real measured number is deferred** (GW20/T01) — the local half proves the *mechanism*,
not the curve. The threshold is monotone in `W`: more windows ⇒ a stricter per-window threshold
(Property P3). Because the derivation inverts the independence formula, the resulting per-request FPR
is `≤ ceiling` **by construction** for the declared per-window FPR (R3.4, Property P3).

- **Ceiling source.** `Per_Request_FPR_Ceiling` comes from the plan, not a field literal (R3.1).
  Mapping to the shipped type: `domain.plan.Rule.threshold` is the per-rule confidence threshold the
  derivation *produces/compares against*; the FPR **ceiling** is a plan-level/config FPR input the
  derivation consumes. GW10 resolves the ceiling from the plan/config (threaded through config or a
  plan FPR field), derives the per-window `Derived_Threshold`, and compares each window's score
  against it. Where `Rule.threshold` is set, it is treated as a floor/override on the derived
  threshold (the operator's explicit confidence bar), never overwritten silently.
- **Ceiling-exceeded condition.** If a derivation cannot keep the per-request FPR at or below the
  ceiling (e.g. a degenerate `W`/`per_window_fpr` combination where no per-window threshold satisfies
  the budget), the detector **records the ceiling-exceeded condition** and applies the **declared
  behaviour** — default action **FLAG** per C6 ("the default semantic action is FLAG until a signed
  per-request FPR and recall floor are met") — never silently proceeding (R3.5). Where the per-request
  FPR at the deployed window count cannot be published at all, the detector surfaces the
  non-publishable condition (R11.5) rather than silently resolving the FPR-vs-recall trade toward the
  latency headline; this shares the recorded-condition mechanism.

### Backend unavailability (`detector.py`) — Requirement 6

`backend.classify` that raises `BackendUnavailable` (reason `stale_or_manifest_mismatch` or
`over_budget`), times out mid-batch (detected against the injected `Budget`/clock), or a
`readiness()` that reports not-ready ⇒ the detector emits a **single**
`Finding(status=FindingStatus.UNAVAILABLE, confidence=None)` carrying the rule's failure posture
(`Rule.on_unavailable`), and **never** presents a partial per-window result set as a complete clean
classification (R6.1–R6.4, Property P5). This builds directly on GW08's invariant: `local_onnx`
raises `BackendUnavailable` and never fabricates a clean result. `Finding.__post_init__` enforces
`confidence is None` for a non-EXECUTED status, so an UNAVAILABLE finding is structurally incapable of
carrying a fabricated score.

### The `Semantic_Detector` surface (`detector.py`) — Requirement 7, Resolution 1

```python
class SemanticDetector:  # satisfies the runtime_checkable Detector protocol
    detector_version: str          # composite provenance (below)
    required_capabilities: frozenset[str]  # e.g. {"semantic"}

    async def classify_input(
        self, segments: tuple[RoleScopedSegment, ...], context: DetectorContext
    ) -> tuple[Finding, ...]: ...   # PRIMARY surface; the §10.4 lifecycle awaits this

    def detect(self, text: str, context: DetectorContext) -> tuple[Finding, ...]:
        # thin sync shim for the sync Detector protocol; wraps classify_input via _run
        return _run(self.classify_input((RoleScopedSegment(SegmentRole.USER, text),), context))
```

- `detector_version` is a **composite** so a finding's provenance covers model + tokenizer + rules:
  `model_hash` (from `readiness()`), the pinned `Tokenizer_Hash`, and a GW10 ruleset version — e.g.
  `f"semantic:{model_hash}:{tokenizer_hash}:{ruleset_version}"`.
- `required_capabilities` names the semantic capability (e.g. `frozenset({"semantic"})`).
- The detector **never mutates** its input (R7.2, Property P8), **never decides disposition / raises
  HTTP / short-circuits** (R7.3), and is **backend-topology-blind** — it drives the backend only
  through `classify` / `readiness` / `capacity_hint` and references no CPU/GPU/TensorRT/Triton/batch/
  replica symbol (R7.4). `local_onnx` is the CI default; a deterministic `FakeGuardBackend`
  (injectable results, timeout, not-ready, invocation counter) drives tests (R7.5).

## Data Models

All frozen `@dataclass(frozen=True, slots=True)`; shipped domain/GW08 types are **used, not
redefined**.

| Type | Module | Fields / shape | Notes |
|---|---|---|---|
| `SegmentRole` | `roles.py` | `StrEnum` SYSTEM/USER/ASSISTANT/TOOL | GW10-local role axis (domain has none). |
| `RoleScopedSegment` | `roles.py` | `role: SegmentRole`, `text: str` | The typed role input (Resolution 2). |
| `TokenizerSeam` | `windowing.py` | `tokenizer_hash: str`, chars-per-token model handle | Declared seam; prod bytes deferred (R11.4). |
| `WindowPlan` | `windowing.py` | `windows: tuple[str, ...]`, `spans: tuple[Span, ...]`, `window_count: int`, `tokenizer_hash: str` | Pure windowing output; `W` recorded. |
| `OverLengthError` | `windowing.py` | `required_windows: int`, `max_windows: int` | Actionable length-reject signal (local, not HTTP). |
| `FprBudget` | `fpr_budget.py` | derived values from `(ceiling, per_window_fpr, W)` | Pure; carries `derived_threshold`, `ceiling_exceeded: bool`. |
| `LanguagePosture` | `language.py` | `language: str`, `supported: bool` | Unsupported ⇒ non-clean posture (R5.2). |
| `MultilingualReport` | `language.py` | per-language breakdown | Separate reporting (R5.5). |
| `GuardBackend` / `GuardResult` / `Readiness` / `Budget` / `BackendUnavailable` | `detect/guard/*` | shipped GW08 | Used, not redefined. |
| `Finding` / `FindingStatus` / `Span` / `Category` | `domain/*` | shipped | Used, not redefined. |
| `Rule` / `RuleScope` / `FailurePosture` / `Phase` | `domain/plan.py` | shipped | Used; `role_scope` layered over `Rule`. |
| `DetectorContext` | `detect/base.py` | shipped `(phase, rules, budget)` | Used; role input is a separate typed arg. |

Capacity-position values (`token_band`, `overlap`, `chars_per_token`, `max_window_count`,
`per_window_fpr`, `fpr_ceiling`) are **module constants / config / `ResourceContract`**, never
dataclass fields — verified against `lint/check_capacity_literals.py`.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a
system — essentially, a formal statement about what the system should do. Properties serve as the
bridge between human-readable specifications and machine-verifiable correctness guarantees.*

The pure modules (`windowing.py`, `fpr_budget.py`, `aggregate.py`, `roles.py`, `language.py` fold)
are property-testable in isolation; the detector's I/O boundary is exercised with a deterministic
`FakeGuardBackend`. All property tests use a seeded `random.Random` with ≥10,000 iterations and do
NOT use hypothesis (R8.8).

### Property 1: Windowing covers the whole input, boundaries hide nothing

*For any* input text, token band, overlap, and chars-per-token (within the maximum window count), the
union of the window spans covers every byte of the input with no gap, and any contiguous substring up
to one band long is wholly contained in at least one window — no middle is ever dropped.

**Validates: Requirements 1.1, 1.2, 1.3, 4.1, 4.3**

### Property 2: Over-length inputs are explicitly rejected, never silently truncated

*For any* input whose required window count exceeds the declared maximum, the windowing raises the
explicit over-length reject with an actionable error, and for no input does it return a window set
that omits a middle region of the input.

**Validates: Requirements 4.2, 4.3, 1.3, 9.5**

### Property 3: The FPR-budget derivation keeps the per-request FPR within the ceiling and is monotone in W

*For any* ceiling in `(0, 1)`, declared per-window FPR in `(0, 1)`, and window count `W ≥ 1`, the
per-request FPR implied by the derived per-window threshold is `≤ ceiling`, and the derived per-window
threshold is non-decreasing (strictness non-decreasing) as `W` increases, including at `W ∈ {1, 2, 4, 7}`.

**Validates: Requirements 3.2, 3.4, 3.6, 9.2**

### Property 4: Exactly one classify call per request

*For any* input yielding `N` windows, the detector invokes `backend.classify` exactly once (counted
by a spy/fake backend) with all `N` windows, and never in a per-window serial loop.

**Validates: Requirements 2.1, 2.2, 9.3**

### Property 5: An unavailable, timed-out, or not-ready backend yields UNAVAILABLE, never partial-as-complete

*For any* backend state that raises `BackendUnavailable`, times out mid-batch, or reports not-ready,
the detector emits a single `Finding` with `status == UNAVAILABLE` and `confidence is None` carrying
the rule's failure posture, and never emits a clean EXECUTED result or a partial per-window result set
presented as complete.

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 9.6**

### Property 6: An unsupported language is never a silent clean pass

*For any* input whose identified language is outside the supported set, the detector assigns a
non-clean `Language_Posture` (UNAVAILABLE or an explicit unsupported-language finding) and never emits
a clean EXECUTED semantic result for it.

**Validates: Requirements 5.1, 5.2, 9.4**

### Property 7: Role scoping — a user-scoped rule does not fire on system/assistant-only input

*For any* set of role-tagged segments containing no USER (or otherwise rule-admitted) segment, a
semantic rule whose role scope is user input produces no firing on the system/assistant/tool segments,
while a rule that opts a role in does fire on that role's segments.

**Validates: Requirements 5.4**

### Property 8: The pure algebra is deterministic and never mutates its input

*For any* input, repeated evaluation of the windowing, FPR-budget derivation, aggregation, and
confusable fold on identical arguments yields identical results, and the detector leaves its input
text/segments object unchanged.

**Validates: Requirements 1.6, 3.6, 7.2, 8.1**

### Property 9: Aggregation is max-score-per-category with a correct Span and only semantic categories

*For any* sequence of per-window `GuardResult`s, the aggregated per-category score equals the maximum
window score for that category, the emitted `Span` is the offset of the window that produced that
maximum, and every emitted semantic finding's category is `PROMPT_INJECTION` or `JAILBREAK`.

**Validates: Requirements 2.3, 2.5**

## Error Handling

- **Over-length input** → `OverLengthError` (a local value/exception carrying `required_windows` and
  `max_windows`), surfaced as an explicit, actionable length reject — never a silent middle-drop
  (R4.2). It is never an HTTP type and never escapes the detect layer, mirroring `canonicalize.py`'s
  local `BudgetExceeded`.
- **`BackendUnavailable` / timeout / not-ready** → a single `Finding(status=UNAVAILABLE,
  confidence=None)` with `Rule.on_unavailable` applied (R6). Mid-batch timeout is detected against the
  injected `Budget.remaining_s` and the injected clock (R8.3) — pure logic reads no wall clock.
- **Ceiling-exceeded / non-publishable FPR** → a recorded condition with the declared behaviour
  (FLAG per C6), never silent (R3.5, R11.5).
- **Unsupported language** → a non-clean posture, never a clean EXECUTED pass (R5.2).
- **Unknown category from the backend** → `domain.category.parse_category` rejects non-members;
  semantic findings carry only `PROMPT_INJECTION`/`JAILBREAK` (R2.5).

The detector raises no HTTP, decides no disposition, and short-circuits nothing (R7.3) — it only emits
`Finding[]`; the pure resolver at lifecycle step ⑤ consumes them.

## Testing Strategy

Property-based testing IS appropriate here: the windowing, FPR-budget, aggregation, role-admission,
and confusable fold are **pure functions with large input spaces** and clear universal properties
(coverage, monotonicity, determinism). The detector's I/O boundary (one `classify` call, UNAVAILABLE
mapping) is exercised with a deterministic fake. This is YOUR code's logic, not external-service
behaviour, so 10,000+ iterations find real edge cases cheaply. The **measured model numbers**
(recall/FPR/AUC curve, per-language recall) are explicitly deferred to GW20/T01 and are NOT asserted
here — the local half tests the mechanism against a declared per-window FPR input.

**Dual approach, one file.** All GW10 tests live in one new file
`gateway_v2/tests/detect/test_semantic_detection_gw10.py`:

- **Property tests** (seeded `random.Random`, ≥10,000 iterations, no hypothesis): Properties 1–9,
  each tagged `# Feature: semantic-detection-gw10, Property N: <text>`. The house async idiom
  `_run(coro) = asyncio.run(coro)` (from `tests/state_control/test_lgw05c_writer.py:49`) drives the
  async surface from the sync test body.
- **`FakeGuardBackend`**: injectable `GuardResult[]`, injectable timeout / not-ready, and an
  invocation counter (for P4). Satisfies the `GuardBackend` protocol.
- **Mechanism / example tests (LGW10-1..6):**
  - LGW10-1: plant a high-score marker in the first, middle, and last window of a long input →
    detected in every position, or a length reject; a planted middle-window miss fails.
  - LGW10-2 (local half): the FPR-budget algebra at `W ∈ {1, 2, 4, 7}` stays within the declared
    ceiling by construction; the measured FPR curve is deferred.
  - LGW10-3: exactly one `classify` call for N windows (fake counter == 1), no serial loop.
  - LGW10-4 (local half): a multilingual corpus — unsupported language is non-clean, reported
    separately; real per-language numbers deferred.
  - LGW10-5: an input exceeding the max window count → explicit actionable error.
  - LGW10-6: a fake that times out / goes not-ready mid-batch → UNAVAILABLE with the rule posture, no
    partial-as-complete.
  - A **real-backend smoke** against the shipped `local_onnx` (CI default): a classify returns results
    and the detector maps them to findings.
- **Unit tests** (specific examples / structural): `detector_version` composite carries
  model+tokenizer+ruleset; `W` and tokenizer hash recorded; `isinstance(detector, Detector)`; value
  types are frozen+slots; the tokenizer-seam substitution yields identical `W`; the confusable fold
  runs before windowing; the ceiling-exceeded recorded condition.

**Property test configuration:** minimum 100 iterations (GW10 uses ≥10,000 per the house idiom);
each property test references its design property by number in the tag.

### The exit gate (Requirement 10)

Run from cwd `gateway_v2` under `.venv`:

1. Full `pytest` green at the baseline **1767 passed / 100 skipped / 1 xfailed** plus the new GW10
   tests.
2. `mypy --strict`.
3. `ruff check gateway_v2 state_control audit_control load_harness lint tests`.
4. `lint-imports` (the Layer_Contract: `detect/semantic` imports only `domain`, GW08 `detect`
   siblings, and `runtime`).
5. The 6 AST gates each exit 0 — including `lint/check_capacity_literals.py` over `detect/semantic`
   (no capacity/threshold/overlap/chars-per-token field literal), the no-module-mutable gate, the
   frozen-dataclass gate, and the http-outside-edge gate.
6. `unscanned_channels() == ()`.

## Deferred Scope (Requirement 11)

Explicitly out of scope and NOT silently resolved by the local half:

1. **Measured model recall / FPR / AUC** at the chosen thresholds (the LGW10-2 measured FPR curve, the
   LGW10-4 per-language recall/FPR) → GPU fleet / GW20 / T01. The local half uses a declared/configured
   per-window FPR input, not the model's measured curve (R11.1).
2. **GPU-specific throughput and the 20 ms-profile sizing** → GW20 (R11.2).
3. **The signed 22M-vs-86M Indic recall-vs-latency trade** → T01 (R11.3).
4. **The production tokenizer bytes** (`tokenizer.json`) → a declared seam; only the pinned
   `Tokenizer_Hash` and the chars-per-token measurement model are local (R11.4).
5. **The real request-message → role-segment lifecycle folding** (building `RoleScopedSegment[]` from
   the OpenAI request's messages) → GW15 (C21: "before GW04 ... exercised by GW15"). GW10 owns the
   role-aware mechanism over the typed input.
6. **GW11 provider dispatch** → the next card.
7. If the per-request FPR at the deployed window count **cannot be published**, the detector stops /
   records the condition rather than silently resolving the FPR-ceiling-versus-recall trade in favour
   of the latency headline (R11.5).

## Requirements Traceability

| Requirement | Design element |
|---|---|
| **R1** Tokenizer-aware windowing, overlap, pinned hash, no 512 truncation | `windowing.py` (`WindowPlan`, chars-per-token module constants, overlap, disabled 512 truncation, `Tokenizer_Hash`, `TokenizerSeam`, Span offset map); Properties P1, P8 |
| **R2** One classify call, declared aggregation, recorded W | `detector.py` single `await classify`; `aggregate.py` max-score-per-category; recorded `W`; `PROMPT_INJECTION`/`JAILBREAK` only; Properties P4, P-AGG |
| **R3** Per-request FPR budget derivation + ceiling behaviour | `fpr_budget.py` `derive_threshold` via `1−(1−fpr)^W`; ceiling from plan/config; FLAG-on-ceiling-exceeded recorded condition; Property P3 |
| **R4** Over-length explicit reject, no middle-drop | `windowing.py` `OverLengthError`, max from `ResourceContract`/config, every-window coverage; Properties P1, P2 |
| **R5** Language posture, confusable, role scoping, separate multilingual reporting | `language.py` (posture, confusable-before-tokenizer, `MultilingualReport`); `roles.py` (`admit_segments`); Properties P6, P7 |
| **R6** Backend unavailability / mid-batch timeout → UNAVAILABLE | `detector.py` maps `BackendUnavailable`/timeout/not-ready → single UNAVAILABLE finding + failure posture; Property P5 |
| **R7** Semantic Detector-protocol class over GuardBackend | `detector.py` `SemanticDetector` (async `classify_input` + sync `detect` shim, Resolution 1); composite `detector_version`; `required_capabilities`; topology-blind; `local_onnx` + fake |
| **R8** Purity, house idiom, layer contract, AST gates | Pure `windowing`/`fpr_budget`/`aggregate`; frozen+slots; injected clock; capacity values as constants/config; no module mutable; no HTTP type; `detect`-only imports; seeded `random.Random` ≥10,000 iters; Property P8 |
| **R9** LGW10 mechanism tests | `tests/detect/test_semantic_detection_gw10.py` LGW10-1..6 + `local_onnx` smoke; deferred measurements named |
| **R10** Exit gate | §Testing Strategy → The exit gate (pytest baseline, mypy --strict, ruff, lint-imports, 6 AST gates, `unscanned_channels()==()`) |
| **R11** Deferred scope explicit | §Deferred Scope (1–7) |
