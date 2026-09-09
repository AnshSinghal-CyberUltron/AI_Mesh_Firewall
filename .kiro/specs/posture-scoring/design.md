# Design Document

## Overview

The **posture scoring harness** (`Posture_Harness`) is a reproducible, measurement-only tool that
scores three named detection postures of the shipped scanner
(`gateway/ai_mesh_gateway/scanner.py`) over the labelled detection corpus produced by G0.1
(`tests/detection_corpus/`) and publishes a committed, human-readable report to `docs/perf/`. The
report presents, per posture, the headline **recall@1%FPR** metric plus per-attack-family recall
and per-benign-family false-positive-rate breakdowns, with attribution metadata (corpus version,
scored set, reproducible command, target FPR) sufficient to regenerate every number.

This is task **G0.2** of the evidence-based hot-path plan. Its entire deliverable is the published
table, which is itself the Gate 0 exit criterion: no posture may be recommended and no SLO
published until this table exists (Requirement 10).

The three postures are:

- **`Tier1_Only`** — the fast pattern/regex scanner (`ATTACK_PATTERNS`) alone.
- **`Tier1_Plus_Policy`** — Tier-1 together with the policy layer.
- **`Tier1_Plus_Semantic`** — Tier-1 together with the semantic (Tier-2 / Bedrock) detector.

### Scope

**In scope** (from the requirements):

- Score each of the three postures independently over a chosen scored set (Requirement 1).
- Compute recall and FPR from corpus labels only (Requirement 2).
- Deterministically select a decision threshold that maximises recall subject to FPR ≤ 1%,
  tuned on the `train` split only, measured on the `eval` split (Requirements 3, 7).
- Per-attack-family recall and per-benign-family FPR breakdowns (Requirements 4, 5).
- Emit one committed plain-text report under `docs/perf/` with full attribution (Requirement 6).
- Reproducibility via a single documented command; no evaluation leakage (Requirement 7).
- Honest handling of an unavailable posture — reported as "not scored" with a reason, never
  fabricated or silently omitted (Requirement 8).
- Zero blast radius: all new files under `scripts/detection/` and `docs/perf/`; no scanner or
  production code changed; scanner invoked read-only (Requirement 9).

**Explicitly out of scope** (Requirement 9.6): the `command_injection` backtick over-match fix
(G0.3), the `data_leakage` tool-description over-match fix (G0.4), the explanatory carve-out
decision (G0.5), any change to the detection corpus (G0.1), and any posture recommendation or SLO
certification (downstream, gated on this table).

### Key design decisions

1. **Posture-adapter abstraction (Requirement 1.4).** Each posture is a small adapter object
   implementing a common `PostureAdapter` interface: `name`, an availability probe, and a
   `score(text) -> float` (or "unavailable" signal). The metric, threshold-selection, and report
   layers operate only against `PostureAdapter` + the collected scores — they never reference a
   concrete posture. Adding a fourth posture is a new adapter registration; no metric/threshold/
   report code changes. This directly realises the "add a further posture without modifying
   metric-computation, threshold-selection, or reporting logic" constraint.

2. **Score, not verdict.** A posture assigns each corpus item a numeric `Posture_Score` (a real in
   `[0.0, 1.0]`), and the harness applies a `Decision_Threshold` itself. This is what makes
   recall@1%FPR a *tunable operating point* rather than a single fixed scanner verdict. The natural
   score source is the scanner's own confidence: `ScanVerdict.confidence` (Tier-1 pattern
   confidence; the Tier-2/semantic path adds the Bedrock confidence). The scanner is invoked
   read-only to obtain this score; the harness never mutates scanner state and never uses the
   scanner's `action` to *label* an item (labels come from the corpus only — Requirements 2.3, 7.4).

3. **Pure functions for metrics and threshold selection.** Recall, FPR, per-family breakdowns, and
   the threshold-selection rule are implemented as pure functions over `(scores, labels, families,
   splits)`. This isolates the deterministic, property-testable core from the I/O (corpus load,
   scanner invocation, report write), so reproducibility and determinism can be proven directly
   (Requirements 3.3, 7.2).

4. **Deterministic threshold selection (Requirement 3).** Candidate thresholds are exactly the
   distinct posture scores observed over the **train** split. The rule picks the threshold that
   maximises recall subject to FPR ≤ 0.01 (with a `1e-6` tolerance), tie-breaking on lowest FPR
   then highest threshold. This yields a single, reproducible `Selected_Threshold` for identical
   inputs. When no candidate reaches the target FPR, the harness reports the `FPR_Floor` and the
   recall at that floor rather than a `Recall_At_Target_FPR` (Requirement 3.5).

5. **No eval leakage (Requirement 7).** Threshold tuning consumes only `train` items; the selected
   threshold is then applied to the `eval` split for the headline measurement. Splits are read from
   the corpus `split` field; items with any other split value are excluded and counted.

6. **Fail-closed honesty (Requirements 8, 6.10).** An unavailable posture (for example
   `Tier1_Plus_Semantic` when Bedrock/Tier-2 is not reachable) is recorded as "not scored" with a
   non-empty reason and produces no metric values. Missing attribution (corpus version, command,
   target FPR) aborts with a non-success result and no report. If all three postures are
   unavailable, the harness errors and emits no report.

7. **Round-half-up to 4 decimal places (Requirements 2.8, 4.2, 5.2).** Every reported metric is
   rounded to 4 dp using round-half-up (via `decimal.Decimal` with `ROUND_HALF_UP`, not Python's
   banker's-rounding `round()`), applied uniformly so re-runs reproduce byte-identical values.

8. **Reproducibility over the gateway venv (Requirement 7.1).** The documented command runs in the
   gateway-importable context so `import ai_mesh_gateway.scanner` resolves — the standalone corpus
   lint showed importing `ai_mesh_gateway` from outside the gateway directory fails. The command
   style is `cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py`.

## Architecture

### Component / file layout

Every file this feature introduces lives under `scripts/detection/` or `docs/perf/`
(Requirement 9.1). No file outside these two prefixes is added, modified, or deleted
(Requirement 9.2).

```
scripts/detection/
  score_postures.py        # entrypoint + CLI; wires the pipeline; the Reproducible_Command target
  postures.py              # PostureAdapter protocol + the 3 concrete adapters + availability probes
  corpus.py                # read-only Detection_Corpus loader -> Corpus_Item records + split/label filters
  metrics.py               # pure recall / FPR / per-family functions + round-half-up helper
  thresholds.py            # pure Threshold_Selection_Rule (candidates, FPR<=target, tie-breaks, FPR_Floor)
  report.py                # PostureReport model + plain-text (Markdown) emitter + attribution
  attribution.py           # Corpus_Version (git), Reproducible_Command, Target_FPR resolution
  __init__.py
  tests/
    test_metrics.py                 # Hypothesis property + example tests for metrics.py
    test_thresholds.py              # Hypothesis property + example tests for thresholds.py
    test_report.py                  # report schema / attribution / not-scored rendering
    test_corpus.py                  # loader edge cases (empty, unrecognized label, bad split)
    test_no_blast_radius.py         # git-diff-scope gate + reproducibility gate

docs/perf/
  posture_scores.md        # the committed Posture_Report (the G0.2 deliverable / gate artefact)
```

`score_postures.py` is the only module that performs I/O side effects (reads the corpus, invokes
the scanner read-only, writes the report). `metrics.py`, `thresholds.py`, and the pure parts of
`corpus.py`/`report.py` are side-effect-free and fully property-testable.

### Data flow

```mermaid
flowchart TD
    A[CLI: score_postures.py<br/>--scored-set eval|full] --> B[Resolve attribution<br/>Corpus_Version, Reproducible_Command, Target_FPR]
    B -->|missing any| BX[[abort: non-success,<br/>no report — R6.10]]
    B --> C[corpus.load_corpus<br/>read-only tests/detection_corpus/]
    C -->|unreadable / 0 items| CX[[abort: failure exit,<br/>no report — R7.3, R1.6]]
    C --> D[Partition by split + label<br/>train / eval; malicious / benign;<br/>exclude unrecognized label + bad split, count them]
    D --> E{For each Posture}
    E --> F[Probe availability]
    F -->|unavailable| G[record status=not scored + reason<br/>no metrics — R8.1]
    F -->|available| H[score train items -> Posture_Scores<br/>scanner read-only]
    H --> I[thresholds.select_threshold<br/>candidates = distinct TRAIN scores<br/>max recall s.t. FPR<=Target_FPR+1e-6<br/>tie: low FPR, then high threshold]
    I -->|no threshold <= target| J[FPR_Floor + recall at floor — R3.5]
    I --> K[apply Selected_Threshold to EVAL scores]
    J --> K
    K --> L[metrics: Recall_At_Target_FPR,<br/>Per_Family_Recall, Per_Family_FPR<br/>round-half-up 4dp]
    G --> M[Assemble PostureReport<br/>exactly 3 posture entries — R8.3]
    L --> M
    M -->|all 3 unavailable| MX[[abort: error, no report — R8.5]]
    M --> N[report.emit -> docs/perf/posture_scores.md]
    N -->|write fails| NX[[abort: non-success,<br/>no partial file — R6.8]]
    N --> Z[success exit — R7.3]
```

### How the harness reaches the scanner in read-only mode

- **Import context.** The scanner module is imported as `from ai_mesh_gateway import scanner` /
  `ai_mesh_gateway.scanner.InputScanner`. This requires the gateway package to be importable, so
  the `Reproducible_Command` runs from within `gateway/` using its venv
  (`cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py`). This mirrors the
  documented gateway run style and the standalone-lint finding that importing `ai_mesh_gateway`
  from outside the gateway directory fails.
- **Read-only invocation.** Each posture adapter constructs one `InputScanner` and calls its
  scan entrypoint per corpus item to obtain a numeric score derived from `ScanVerdict.confidence`.
  - `Tier1_Only`: the Tier-1 pattern scan path (`ATTACK_PATTERNS` via the synchronous scan),
    scored by the Tier-1 confidence.
  - `Tier1_Plus_Policy`: Tier-1 plus the policy layer's contribution to the score.
  - `Tier1_Plus_Semantic`: Tier-1 plus the Tier-2/semantic (Bedrock) confidence
    (`scan_prompt_with_tier2`); availability depends on `ENABLE_TIER2` / Bedrock reachability.
- **No mutation.** The harness never writes scanner state, never edits `scanner.py`, and never
  derives a corpus label from the scanner's `action` (Requirements 9.2, 9.3, 2.3, 7.4). The scanner
  is treated as a pure `text -> score` oracle for scoring purposes; its verdict for any input is
  byte-for-byte identical before and after this feature exists (Requirement 9.3).

## Components and Interfaces

### `postures.py` — posture adapter abstraction (Requirement 1.4)

```python
class PostureAdapter(Protocol):
    name: str  # stable, unique, 1..128 chars (R1.2)

    def probe(self) -> AvailabilityResult:
        """Return available, or (unavailable, non-empty reason). No scoring side effects."""

    def score(self, text: str) -> float:
        """Read-only Posture_Score in [0.0, 1.0] for one Corpus_Item's text."""
```

- Concrete adapters: `Tier1OnlyAdapter`, `Tier1PlusPolicyAdapter`, `Tier1PlusSemanticAdapter`.
- A `build_postures()` factory returns the three adapters in a fixed order and **rejects duplicate
  names** (Requirement 1.5) before any scoring, emitting no report.
- `probe()` for the semantic adapter checks `ENABLE_TIER2` and Bedrock reachability; on failure it
  returns `(unavailable, reason)` so the posture is later recorded "not scored" (Requirement 8).
- The metric/threshold/report modules depend only on this protocol + collected score lists, so a
  4th adapter needs no change to them (Requirement 1.4, 1.3).

### `corpus.py` — read-only loader

```python
def load_corpus(root: Path) -> Corpus            # reads malicious.jsonl, benign.jsonl, families.json
def scored_set(corpus, which: Literal["eval","full"]) -> ScoredSet
def partition(items) -> LabelPartition            # malicious / benign / excluded(label)  (R2.3, 2.7)
def split_of(item) -> Literal["train","eval","other"]   # R7.7
```

- Labels are read **only** from the item `label` field; an absent/unrecognized label → the item is
  excluded from both malicious and benign counts and the excluded count is recorded (R2.3, 2.7).
- A split value other than `train`/`eval` → excluded from tuning and headline, counted (R7.7).
- Never derives a label from the scanner (R7.4). Never writes to `tests/detection_corpus/`.

### `metrics.py` — pure metric functions (Requirements 2, 4, 5)

```python
def flagged(score: float, threshold: float) -> bool          # score >= threshold
def recall(scores, labels, threshold) -> float | NotComputable   # R2.1, 2.4
def fpr(scores, labels, threshold) -> float | NotComputable      # R2.2, 2.5
def per_family_recall(scores, labels, families, threshold) -> dict[str, float | NotComputable]  # R4
def per_family_fpr(scores, labels, families, threshold) -> dict[str, float | NotComputable]     # R5
def round_metric(value: float) -> float          # Decimal ROUND_HALF_UP to 4 dp  (R2.8)
def paraphrase_gap(per_family_recall) -> float | NotComputable    # R4.6, in [-1.0, 1.0]
```

- `recall`/`fpr` return a `NotComputable` sentinel (with a reason) when the malicious/benign count
  is 0, rather than a number (R2.4, 2.5) — the caller turns that into an error indication.
- All returned metric values are in `[0.0, 1.0]`; `paraphrase_gap` in `[-1.0, 1.0]`.
- Rounding is applied at the reporting boundary via `round_metric` (R2.8, 4.2, 5.2).

### `thresholds.py` — deterministic threshold selection (Requirement 3)

```python
TARGET_FPR = 0.01           # R3.1
FPR_TOLERANCE = 1e-6        # R3.2

def candidate_thresholds(train_scores) -> list[float]     # distinct TRAIN scores only  (R3.6, 3.7)
def select_threshold(train_scores, train_labels) -> ThresholdChoice
    # max recall s.t. fpr <= TARGET_FPR + FPR_TOLERANCE;
    # tie-break: lowest FPR, then highest threshold  (R3.2, 3.3)
    # -> ThresholdChoice(selected_threshold, recall, achieved_fpr, target_achievable: bool)
def fpr_floor(train_scores, train_labels) -> FloorChoice   # lowest achievable FPR + recall there (R3.5)
```

- Candidates are exactly the **distinct** posture scores over the **train** split — every reachable
  operating point evaluated once, no unreachable threshold evaluated (R3.6), and no non-train item
  influences the choice (R3.7, 7.6).
- Deterministic tie-break makes `select_threshold` a pure function of the (train) score/label
  multiset (R3.3) — identical inputs → identical `Selected_Threshold`.
- When no candidate satisfies FPR ≤ target (or there are no benign items to compute FPR), returns
  `target_achievable = False`; the caller reports the `FPR_Floor` and the recall at the floor
  threshold instead of a `Recall_At_Target_FPR` (R3.5).

### `report.py` — report emitter (Requirements 6, 8, 10)

```python
def build_report(results, scored_set_meta, attribution) -> PostureReport
def emit(report: PostureReport, out_path: Path) -> None   # writes docs/perf/posture_scores.md
```

- The report always contains **exactly one entry per posture** (three total), each marked "scored"
  (with metric values) or "not scored" (with a non-empty reason), in a form distinguishable from a
  metric value (R8.3).
- Presents per posture: name, `Recall_At_Target_FPR` (or the FPR-floor form), the
  `Selected_Threshold` and achieved FPR (R3.4), `Per_Family_Recall` for every attack family
  present (including `paraphrase`) with its malicious count (R4.3, 4.4), `Per_Family_FPR` for every
  benign family present (including `developer_traffic`) with its benign count (R5.3, 5.4), and the
  paraphrase gap when computable (R4.6). A family in `families.json` with 0 items in the scored set
  is marked "not computable", never left empty (R4.5, 5.5, 10.1).
- States the scored set (eval vs full) with malicious/benign counts (R6.4), the corpus version
  (R6.5), the reproducible command + target FPR (R6.6), the threshold-selection rule and that
  tuning was train-only (R3.8), and any excluded-item counts (R2.7, R7.7).
- **Atomic write** to avoid a partial/empty file on failure (R6.8): write to a temporary file in
  `docs/perf/` then `os.replace` into place; on any write error, abort non-success and leave no
  partial report.

### `attribution.py`

```python
def corpus_version(root: Path) -> str        # git commit id pinning tests/detection_corpus/  (R6.5)
def reproducible_command() -> str            # the single documented command  (R6.6, 7.1)
def target_fpr() -> float                     # 0.01  (R3.1, 6.6)
```

- If any of these cannot be determined, the harness aborts with a non-success result and an error
  identifying the missing attribution value, and emits no report (R6.10).

### `score_postures.py` — CLI / entrypoint (Requirement 7.1)

- Single documented command; no interactive input; the only documented argument selects the scored
  set: `--scored-set {eval,full}` defaulting to `eval` (the headline is measured on the eval split,
  R6.7). The report records which scored set each number was computed over.
- Orchestrates: resolve attribution → load corpus (abort on unreadable/empty) → for each posture:
  probe, score train, select threshold on train, apply to the scored set, compute metrics → build
  report (abort if all three unavailable) → emit report → exit success/failure.
- Exit code: success on completion, failure on unreadable/empty corpus, all-unavailable, missing
  attribution, or report-write failure (R7.3, R1.6, R8.5, R6.8, R6.10).

## Data Models

### `PostureConfig`

| field | type | notes |
|---|---|---|
| `name` | str | 1..128 chars, unique across postures (R1.2, 1.5) |
| `kind` | enum | `tier1_only` / `tier1_plus_policy` / `tier1_plus_semantic` |

### `Corpus_Item` (read-only, from G0.1)

| field | type | notes |
|---|---|---|
| `id` | str | e.g. `mal-prompt_injection-0000` |
| `text` | str | scored input |
| `label` | str | `malicious` / `benign` — the ONLY label source (R2.3, 7.4) |
| `family` | str | attack family (malicious) or benign family |
| `split` | str | `train` / `eval`; other → excluded + counted (R7.7) |
| `provenance` | str | attribution passthrough |
| `fake_fixtures` | list | passthrough (not used for scoring) |

### `PostureResult`

| field | type | notes |
|---|---|---|
| `posture_name` | str | |
| `status` | enum | `scored` / `not_scored` (R8.3) |
| `not_scored_reason` | str \| null | non-empty when `not_scored` (R8.1) |
| `recall_at_target_fpr` | float \| null | in [0,1], 4dp; null when not scored / floor reported |
| `selected_threshold` | float \| null | R3.4 |
| `achieved_fpr` | float \| null | FPR at selected threshold, 4dp (R3.4) |
| `target_fpr_achievable` | bool | R3.5 |
| `fpr_floor` | float \| null | reported when target unachievable (R3.5) |
| `recall_at_floor` | float \| null | recall at the floor threshold (R3.5) |
| `per_family_recall` | dict[str, float \| "not_computable"] | attack families incl. `paraphrase` (R4) |
| `per_family_fpr` | dict[str, float \| "not_computable"] | benign families incl. `developer_traffic` (R5) |
| `family_counts` | dict[str, int] | per-family item counts (R4.4, 5.4) |
| `paraphrase_gap` | float \| null | in [-1,1], 4dp, when computable (R4.6) |

### `ScoredSetMeta`

| field | type | notes |
|---|---|---|
| `which` | enum | `eval` / `full` (R6.4, 6.7) |
| `malicious_count` | int | R2.6, 6.4 |
| `benign_count` | int | R2.6, 6.4 |
| `excluded_label_count` | int | absent/unrecognized label (R2.7) |
| `excluded_split_count` | int | split not train/eval (R7.7) |

### `PostureReport` (schema of `docs/perf/posture_scores.md`)

| field | type | notes |
|---|---|---|
| `postures` | list[PostureResult] | exactly 3 entries (R8.3) |
| `scored_set` | ScoredSetMeta | R6.4 |
| `corpus_version` | str | R6.5 |
| `reproducible_command` | str | R6.6, 7.1 |
| `target_fpr` | float | 0.01 (R6.6) |
| `threshold_rule` | str | rule text + "tuned on train split only" (R3.8) |

### Attribution / corpus-version

`corpus_version` is the git commit identifier pinning `tests/detection_corpus/` at scoring time —
sufficient to attribute and reproduce every number (R6.5, 10.3). It, the reproducible command, and
the target FPR are all mandatory; a run cannot emit a report with any of them missing (R6.10).

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a
system — essentially, a formal statement about what the system should do. Properties serve as the
bridge between human-readable specifications and machine-verifiable correctness guarantees.*

The properties below are derived from the acceptance criteria via the prework analysis. They target
the pure, deterministic core (`metrics.py`, `thresholds.py`, corpus partitioning, report
assembly). Report-emission side effects, exit codes, blast-radius, and the reproducibility gate are
covered by example / integration tests in the Testing Strategy, not as universally-quantified
properties.

### Property 1: Posture scoring is independent

*For any* labelled corpus and *any* set of posture score-vectors, the metrics computed for a given
posture depend only on that posture's own scores — changing, adding, or removing any other posture
leaves that posture's reported metric values unchanged.

**Validates: Requirements 1.1, 1.3**

### Property 2: Posture name validity and uniqueness

*For any* list of configured posture names, the harness accepts the configuration if and only if
every name is a non-empty string of 1 to 128 characters and all names are unique; a list containing
a duplicate is rejected with the duplicated name identified and no report emitted.

**Validates: Requirements 1.2, 1.5**

### Property 3: Recall definition and range

*For any* score-vector, label-vector, and decision threshold with at least one malicious item,
`recall` equals the count of malicious items whose score is at or above the threshold divided by the
total count of malicious items, and lies in the inclusive range 0.0 to 1.0.

**Validates: Requirements 2.1**

### Property 4: False-positive-rate definition and range

*For any* score-vector, label-vector, and decision threshold with at least one benign item, `fpr`
equals the count of benign items whose score is at or above the threshold divided by the total count
of benign items, and lies in the inclusive range 0.0 to 1.0.

**Validates: Requirements 2.2**

### Property 5: Labels come only from the corpus label field

*For any* generated corpus, an item is classified malicious or benign solely from its `label` field;
items whose `label` is absent or is not exactly `malicious` or `benign` are excluded from both the
malicious and benign counts and added to the excluded-label count, and the recorded
malicious + benign + excluded-label counts sum to the total item count. The classification is
invariant to every posture score (no label is ever derived from scanner output).

**Validates: Requirements 2.3, 2.6, 2.7, 7.4**

### Property 6: Round-half-up to 4 decimal places is correct and idempotent

*For any* real metric value, `round_metric` produces a value with at most 4 decimal places equal to
the round-half-up rounding of the input to 4 dp, and applying `round_metric` again yields the same
value.

**Validates: Requirements 2.8, 4.2, 5.2**

### Property 7: Threshold selection is feasible, recall-maximal, tie-broken, and deterministic

*For any* train score-vector and label-vector, the selected threshold's false-positive-rate does not
exceed the target FPR by more than 1e-6; among all candidate thresholds meeting that FPR constraint
it achieves the maximum recall; ties in recall are broken by lowest FPR and then highest threshold;
the reported `(recall, achieved_fpr, selected_threshold)` triple equals recomputing recall and FPR at
the selected threshold; and repeated selection over identical inputs yields the identical selected
threshold.

**Validates: Requirements 3.2, 3.3, 3.4**

### Property 8: Candidate thresholds are exactly the distinct observed scores

*For any* train score-vector, the set of candidate decision thresholds equals the set of distinct
posture scores observed over that vector, with each reachable operating point represented exactly
once and no threshold not reachable from an observed score.

**Validates: Requirements 3.6**

### Property 9: No evaluation leakage into threshold selection

*For any* train score/label set and *any* additional items whose split is `eval` or otherwise
non-train, the selected threshold computed from the train items alone is identical to the selected
threshold computed after adding those non-train items — no non-train item influences the selected
threshold, and the headline threshold is applied to the eval split for measurement.

**Validates: Requirements 3.7, 7.5, 7.6**

### Property 10: FPR floor is reported honestly when the target is unachievable

*For any* score/label set in which no candidate threshold achieves FPR at most the target (or which
contains no benign items), the harness reports the target as not achievable, reports the FPR floor
equal to the lowest achievable false-positive-rate over the candidates, and reports the recall at the
threshold realising that floor, rather than a `Recall_At_Target_FPR` value.

**Validates: Requirements 3.5**

### Property 11: Per-family recall definition and range

*For any* corpus and selected threshold, for each attack family present, the per-family recall equals
the count of that family's malicious items flagged divided by the total count of that family's
malicious items, rounded to 4 dp, and lies in the inclusive range 0.0 to 1.0.

**Validates: Requirements 4.1, 4.2**

### Property 12: Per-family FPR definition and range

*For any* corpus and selected threshold, for each benign family present, the per-family FPR equals the
count of that family's benign items flagged divided by the total count of that family's benign items,
expressed as a proportion in 0.0 to 1.0 rounded to 4 dp.

**Validates: Requirements 5.1, 5.2**

### Property 13: Paraphrase gap definition and range

*For any* per-family recall map in which the `paraphrase` recall and the mean non-paraphrase recall
are both computable, the reported paraphrase gap equals the mean non-paraphrase recall minus the
`paraphrase` recall, rounded to 4 dp, and lies in the inclusive range -1.0 to 1.0.

**Validates: Requirements 4.6**

### Property 14: Report presents every present family with no empty cell

*For any* scored posture over a scored set, the report presents, for every attack family present, a
per-family recall cell (a value in 0.0 to 1.0 or an explicit "not computable" marker) alongside that
family's malicious count; and for every benign family present, a per-family FPR cell (a value or
"not computable") alongside that family's benign count — no cell for a present family is left empty
or missing.

**Validates: Requirements 4.3, 4.4, 5.3, 5.4, 10.1**

### Property 15: Unavailable-posture honesty

*For any* availability mask over the three postures with at least one available, the report contains
exactly three posture entries; each available posture is marked "scored" and carries its metric
values; each unavailable posture is marked "not scored" with a non-empty reason and carries no
`Recall_At_Target_FPR` value and no per-family metric value, in a form distinguishable from a metric
value.

**Validates: Requirements 6.9, 8.1, 8.2, 8.3, 8.4**

### Property 16: Deterministic reproducibility of the computed metrics

*For any* fixed corpus and fixed posture score functions, running the deterministic pipeline twice
produces metric values that are byte-for-byte identical across the two runs (excluding fields that
record wall-clock timestamps or elapsed run duration).

**Validates: Requirements 7.2, 6.6, 10.3**

## Error Handling

The harness fails **closed and honest**: on any unrecoverable input or attribution error it
terminates with a non-success result, emits an error identifying the cause, and writes **no** report
(never a partial or fabricated one).

| Condition | Handling | Requirement |
|---|---|---|
| Scored set has 0 items | Error "scored set is empty"; no report; failure exit | 1.6, 7.3 |
| Detection corpus unreadable | Error identifying the unreadable corpus; no report; failure exit | 7.3 |
| 0 malicious items in scored set | Recall reported not computable + error "malicious count is 0"; no fabricated recall | 2.4 |
| 0 benign items in scored set | FPR reported not computable + error "benign count is 0"; no fabricated FPR | 2.5 |
| Items with absent/unrecognized `label` | Excluded from both counts; excluded-label count recorded; error indication that one or more labels were absent/unrecognized | 2.3, 2.7 |
| Items with `split` not `train`/`eval` | Excluded from tuning and headline; excluded-split count recorded in the report | 7.7 |
| Target FPR unachievable (no candidate ≤ target, or no benign) | Report FPR floor + recall at floor; not a `Recall_At_Target_FPR` | 3.5 |
| Attack/benign family in `families.json` with 0 items in scored set | Marked "not computable" (never an empty cell) | 4.5, 5.5 |
| A posture is unavailable (e.g. Tier-2/semantic not installed/reachable) | Recorded "not scored" + non-empty reason; no metric values; report still emitted with the other postures | 8.1, 8.2, 8.4, 6.9 |
| All three postures unavailable | Error "no posture could be scored"; no report; no fabricated metrics | 8.5 |
| Report write to `docs/perf/` fails | Atomic write (temp + `os.replace`); on failure abort non-success, error identifies the write failure, no partial/empty file left | 6.8 |
| Corpus version / reproducible command / target FPR undeterminable | Abort non-success; error names the missing attribution value; no report omitting it | 6.10 |
| Attempted write/mutation outside `scripts/detection/` or `docs/perf/` (incl. scanner) | Terminate without applying the change; error identifies the out-of-scope path; files outside the two prefixes unchanged | 9.4 |

Error indications are surfaced on standard error and reflected in the process exit code (success on
completion, non-zero on any of the failure conditions above — Requirement 7.3).

## Testing Strategy

This feature has a clear pure-function core (metric computation, threshold selection, corpus
partitioning, report assembly) whose behaviour varies meaningfully with input and for which universal
"for all inputs" statements hold — so **property-based testing is appropriate** for that core.
The side-effecting shell (report emission, exit codes, scanner invocation, git-diff blast-radius,
gateway-suite regression) is not PBT-suited and is covered by example and integration tests.

### Dual approach

- **Property tests** verify the universal properties above over generated inputs.
- **Unit/example tests** verify specific scenarios, boundaries, error conditions, report content,
  and the operational contract.
- **Integration/gate tests** verify blast-radius and reproducibility against the real repo.

### Property-based tests (Hypothesis)

- Library: **Hypothesis** (the repo's Python PBT library, already used in
  `tests/detection_corpus/`), run against the pure functions in `metrics.py`, `thresholds.py`, and
  the pure corpus/report helpers.
- Each property test runs a **minimum of 100 iterations** (Hypothesis `max_examples >= 100`).
- Each property test is tagged with a comment referencing its design property, in the form:
  **Feature: posture-scoring, Property {number}: {property text}**.
- Each of Properties 1–16 is implemented by a **single** property-based test:
  - P1 posture independence, P2 name validity/uniqueness, P3 recall, P4 FPR, P5 label source,
    P6 rounding, P7 threshold selection (feasible/max-recall/tie-break/deterministic/consistent),
    P8 candidate set, P9 no-eval-leakage, P10 FPR floor, P11 per-family recall, P12 per-family FPR,
    P13 paraphrase gap, P14 report family-completeness, P15 unavailable-posture honesty,
    P16 deterministic reproducibility.
- Generators produce score-vectors (reals in `[0,1]`), label-vectors
  (`malicious`/`benign`/unrecognized/absent), family and split assignments (`train`/`eval`/other),
  and posture-availability masks — covering the edge cases below by construction.

### Example / unit tests

- **Boundaries (edge cases):** empty scored set (1.6); 0 malicious (2.4); 0 benign (2.5);
  a family with 0 items → not computable (4.5, 5.5); all-three-unavailable (8.5); each missing
  attribution field (6.10); Target_FPR constant is 0.01 (3.1).
- **Report content:** report states the threshold rule + 0.01 + "tuned on train split only" (3.8);
  states scored set + counts (6.4); states corpus version (6.5); states reproducible command +
  target FPR (6.6); every rendered number traces to a computed field, no foreign constants (7.8);
  a fully-populated report satisfies the exit-criterion checklist (10.4).
- **Extensibility (1.4):** register a synthetic 4th `PostureAdapter` and assert it flows through
  the unchanged metric/threshold/report modules and appears in the report.
- **Emission + operability:** exactly one report file under `docs/perf/` after a run (6.1); default
  scored set is `eval`, `--scored-set full` selects the full corpus, and the choice is recorded
  (6.7); the command runs headless with no interactive input (7.1); success → exit 0, empty/
  unreadable corpus → non-zero + no report (7.3); forced write failure → non-success, no partial
  file (6.8); simulated out-of-scope write → abort, files unchanged (9.4).
- **Read-only scanner (9.3):** scanning the same input twice yields an identical verdict and no
  scanner-module mutation, confirming the scanner is used purely as a `text -> score` oracle.

### Integration / gate tests

- **Blast-radius gate (9.2, 9.6):** a `git diff` restricted to files **outside**
  `scripts/detection/` and `docs/perf/` shows zero changed lines (this also enforces the exclusion
  of the G0.3/G0.4/G0.5 fixes and any corpus change).
- **Path-prefix gate (9.1):** every file introduced by the feature has a path prefixed by
  `scripts/detection/` or `docs/perf/`.
- **Gateway regression gate (9.5):** the gateway test gate
  (`cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`) produces an identical set
  of pass/fail outcomes for all pre-existing tests immediately before and immediately after adding
  the feature.
- **Reproducibility gate (7.2, 6.6):** running the `Reproducible_Command` twice over the same corpus
  version yields a report whose metric values are byte-identical (excluding timestamp/duration
  fields).

### Notes on what is deliberately not property-tested

- Scanner behaviour itself is not property-tested here — the scanner is upstream and unchanged
  (Requirement 9); the harness only reads its score.
- The gateway-suite regression and git-diff scope are infrastructure checks (external state,
  behaviour does not vary with generated input), so they are integration tests, not PBT.
