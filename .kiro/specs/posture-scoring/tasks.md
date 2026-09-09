# Implementation Plan: Posture Scoring Harness (G0.2)

## Overview

Convert the feature design into a series of prompts for a code-generation LLM that will implement
each step with incremental progress. Make sure that each prompt builds on the previous prompts, and
ends with wiring things together. There should be no hanging or orphaned code that isn't integrated
into a previous step. Focus ONLY on tasks that involve writing, modifying, or testing code.

The harness is **measurement-only and additive** (Requirement 9): every file created lives under
`scripts/detection/**` or `docs/perf/**`; no line of `gateway/ai_mesh_gateway/scanner.py` or any
other production path is changed, and the scanner is invoked read-only. Implementation language is
**Python** (the design uses `.py` modules, `decimal.Decimal`, `os.replace`, Hypothesis, and imports
`ai_mesh_gateway.scanner`).

Build order follows the design: pure deterministic cores first (`metrics.py`, `thresholds.py`,
corpus loader), then the posture adapters, then `report.py` + `attribution.py`, then the
`score_postures.py` CLI wiring, then the integration/gate tests, then a final gate checkpoint.

The documented gate command is
`cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py`; the gateway regression
gate is `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`.

## Tasks

- [x] 1. Scaffold the additive package skeleton under `scripts/detection/`
  - Create `scripts/detection/__init__.py` and `scripts/detection/tests/__init__.py`
  - Create empty module files `metrics.py`, `thresholds.py`, `corpus.py`, `postures.py`,
    `report.py`, `attribution.py`, `score_postures.py` with module docstrings only (no logic yet)
  - Confirm no file is created outside `scripts/detection/**` / `docs/perf/**`
  - _Requirements: 9.1, 9.6_

- [x] 2. Implement the pure metrics core (`metrics.py`)
  - [x] 2.1 Implement round-half-up rounding helper
    - Implement `round_metric(value)` using `decimal.Decimal` with `ROUND_HALF_UP` to 4 dp (never
      Python's banker's-rounding `round()`); idempotent on already-rounded values
    - _Requirements: 2.8, 4.2, 5.2_

  - [x] 2.2 Write property test for round-half-up rounding
    - **Feature: posture-scoring, Property 6: For any real metric value, `round_metric` produces a value with at most 4 decimal places equal to the round-half-up rounding of the input to 4 dp, and applying `round_metric` again yields the same value.**
    - **Validates: Requirements 2.8, 4.2, 5.2**
    - Hypothesis, `max_examples >= 100`

  - [x] 2.3 Implement `flagged`, `recall`, and `fpr`
    - `flagged(score, threshold)` = `score >= threshold`
    - `recall(scores, labels, threshold)` = flagged malicious / total malicious, in `[0.0, 1.0]`;
      return a `NotComputable` sentinel with reason "malicious count is 0" when no malicious items
    - `fpr(scores, labels, threshold)` = flagged benign / total benign, in `[0.0, 1.0]`; return
      `NotComputable` with reason "benign count is 0" when no benign items
    - _Requirements: 2.1, 2.2, 2.4, 2.5_

  - [x] 2.4 Write property test for recall definition and range
    - **Feature: posture-scoring, Property 3: For any score-vector, label-vector, and decision threshold with at least one malicious item, `recall` equals the count of malicious items whose score is at or above the threshold divided by the total count of malicious items, and lies in the inclusive range 0.0 to 1.0.**
    - **Validates: Requirements 2.1**
    - Hypothesis, `max_examples >= 100`

  - [x] 2.5 Write property test for false-positive-rate definition and range
    - **Feature: posture-scoring, Property 4: For any score-vector, label-vector, and decision threshold with at least one benign item, `fpr` equals the count of benign items whose score is at or above the threshold divided by the total count of benign items, and lies in the inclusive range 0.0 to 1.0.**
    - **Validates: Requirements 2.2**
    - Hypothesis, `max_examples >= 100`

  - [x] 2.6 Implement per-family metrics and paraphrase gap
    - `per_family_recall(scores, labels, families, threshold)` and
      `per_family_fpr(scores, labels, families, threshold)` returning
      `dict[str, float | NotComputable]`, values rounded to 4 dp, in `[0.0, 1.0]`
    - `paraphrase_gap(per_family_recall)` = mean non-paraphrase recall minus `paraphrase` recall,
      rounded to 4 dp, in `[-1.0, 1.0]`, when both are computable
    - _Requirements: 4.1, 4.2, 5.1, 5.2, 4.6_

  - [x] 2.7 Write property test for per-family recall
    - **Feature: posture-scoring, Property 11: For any corpus and selected threshold, for each attack family present, the per-family recall equals the count of that family's malicious items flagged divided by the total count of that family's malicious items, rounded to 4 dp, and lies in the inclusive range 0.0 to 1.0.**
    - **Validates: Requirements 4.1, 4.2**
    - Hypothesis, `max_examples >= 100`

  - [x] 2.8 Write property test for per-family FPR
    - **Feature: posture-scoring, Property 12: For any corpus and selected threshold, for each benign family present, the per-family FPR equals the count of that family's benign items flagged divided by the total count of that family's benign items, expressed as a proportion in 0.0 to 1.0 rounded to 4 dp.**
    - **Validates: Requirements 5.1, 5.2**
    - Hypothesis, `max_examples >= 100`

  - [x] 2.9 Write property test for paraphrase gap
    - **Feature: posture-scoring, Property 13: For any per-family recall map in which the `paraphrase` recall and the mean non-paraphrase recall are both computable, the reported paraphrase gap equals the mean non-paraphrase recall minus the `paraphrase` recall, rounded to 4 dp, and lies in the inclusive range -1.0 to 1.0.**
    - **Validates: Requirements 4.6**
    - Hypothesis, `max_examples >= 100`

  - [x] 2.10 Write unit tests for metrics edge cases
    - 0 malicious → recall not computable + reason; 0 benign → fpr not computable + reason;
      boundary scores equal to threshold flagged; values clamp within range
    - _Requirements: 2.4, 2.5_

- [x] 3. Implement deterministic threshold selection (`thresholds.py`)
  - [x] 3.1 Implement candidate thresholds and selection rule
    - `TARGET_FPR = 0.01`, `FPR_TOLERANCE = 1e-6`
    - `candidate_thresholds(train_scores)` = the distinct train scores only, each reachable
      operating point once, no unreachable threshold
    - `select_threshold(train_scores, train_labels)` → `ThresholdChoice(selected_threshold, recall,
      achieved_fpr, target_achievable)`: max recall subject to `fpr <= TARGET_FPR + FPR_TOLERANCE`,
      tie-break lowest FPR then highest threshold; deterministic for identical inputs
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6_

  - [x] 3.2 Write property test for candidate thresholds
    - **Feature: posture-scoring, Property 8: For any train score-vector, the set of candidate decision thresholds equals the set of distinct posture scores observed over that vector, with each reachable operating point represented exactly once and no threshold not reachable from an observed score.**
    - **Validates: Requirements 3.6**
    - Hypothesis, `max_examples >= 100`

  - [x] 3.3 Write property test for threshold selection correctness
    - **Feature: posture-scoring, Property 7: For any train score-vector and label-vector, the selected threshold's false-positive-rate does not exceed the target FPR by more than 1e-6; among all candidate thresholds meeting that FPR constraint it achieves the maximum recall; ties in recall are broken by lowest FPR and then highest threshold; the reported (recall, achieved_fpr, selected_threshold) triple equals recomputing recall and FPR at the selected threshold; and repeated selection over identical inputs yields the identical selected threshold.**
    - **Validates: Requirements 3.2, 3.3, 3.4**
    - Hypothesis, `max_examples >= 100`

  - [x] 3.4 Implement FPR floor reporting
    - `fpr_floor(train_scores, train_labels)` → `FloorChoice` with the lowest achievable FPR and the
      recall at the threshold realising that floor; `select_threshold` sets `target_achievable =
      False` when no candidate reaches the target or there are no benign items
    - _Requirements: 3.5_

  - [x] 3.5 Write property test for honest FPR floor
    - **Feature: posture-scoring, Property 10: For any score/label set in which no candidate threshold achieves FPR at most the target (or which contains no benign items), the harness reports the target as not achievable, reports the FPR floor equal to the lowest achievable false-positive-rate over the candidates, and reports the recall at the threshold realising that floor, rather than a `Recall_At_Target_FPR` value.**
    - **Validates: Requirements 3.5**
    - Hypothesis, `max_examples >= 100`

  - [x] 3.6 Write property test for no evaluation leakage into selection
    - **Feature: posture-scoring, Property 9: For any train score/label set and any additional items whose split is `eval` or otherwise non-train, the selected threshold computed from the train items alone is identical to the selected threshold computed after adding those non-train items — no non-train item influences the selected threshold, and the headline threshold is applied to the eval split for measurement.**
    - **Validates: Requirements 3.7, 7.5, 7.6**
    - Hypothesis, `max_examples >= 100`

- [x] 4. Implement the read-only corpus loader (`corpus.py`)
  - [x] 4.1 Implement loading, splitting, and label partitioning
    - `load_corpus(root)` reads `malicious.jsonl`, `benign.jsonl`, `families.json` read-only from
      `tests/detection_corpus/`; never writes to it and never derives a label from the scanner
    - `scored_set(corpus, which)` for `which` in `{"eval", "full"}`
    - `partition(items)` → malicious / benign / excluded(label): classify solely from the item
      `label` field; absent/unrecognized label → excluded, counted; malicious + benign + excluded =
      total
    - `split_of(item)` → `train` / `eval` / `other`; a split other than train/eval is excluded and
      counted
    - _Requirements: 2.3, 2.6, 2.7, 7.4, 7.7_

  - [x] 4.2 Write property test for label source and partition invariant
    - **Feature: posture-scoring, Property 5: For any generated corpus, an item is classified malicious or benign solely from its `label` field; items whose `label` is absent or is not exactly `malicious` or `benign` are excluded from both the malicious and benign counts and added to the excluded-label count, and the recorded malicious + benign + excluded-label counts sum to the total item count. The classification is invariant to every posture score (no label is ever derived from scanner output).**
    - **Validates: Requirements 2.3, 2.6, 2.7, 7.4**
    - Hypothesis, `max_examples >= 100`

  - [x] 4.3 Write unit tests for loader edge cases
    - Empty corpus / 0 items; item with unrecognized label; item with bad split value;
    - `--scored-set` selection returns eval subset vs full corpus
    - _Requirements: 1.6, 2.3, 2.7, 7.7_

- [x] 5. Implement the posture adapters (`postures.py`)
  - [x] 5.1 Implement the `PostureAdapter` protocol and factory
    - `PostureAdapter` protocol: `name` (unique, 1..128 chars), `probe() -> AvailabilityResult`,
      `score(text) -> float` in `[0.0, 1.0]`
    - `build_postures()` factory returns the three adapters in fixed order and **rejects duplicate
      names** before any scoring, emitting no report
    - _Requirements: 1.2, 1.4, 1.5_

  - [x] 5.2 Write property test for posture name validity and uniqueness
    - **Feature: posture-scoring, Property 2: For any list of configured posture names, the harness accepts the configuration if and only if every name is a non-empty string of 1 to 128 characters and all names are unique; a list containing a duplicate is rejected with the duplicated name identified and no report emitted.**
    - **Validates: Requirements 1.2, 1.5**
    - Hypothesis, `max_examples >= 100`

  - [x] 5.3 Implement the three concrete adapters (read-only scanner scoring)
    - `Tier1OnlyAdapter`: Tier-1 pattern scan path scored by `ScanVerdict.confidence`
    - `Tier1PlusPolicyAdapter`: Tier-1 plus the policy layer's contribution to the score
    - `Tier1PlusSemanticAdapter`: Tier-1 plus Tier-2/semantic (Bedrock) confidence; `probe()`
      checks `ENABLE_TIER2` and Bedrock reachability, returning `(unavailable, reason)` on failure
    - Construct one `InputScanner` per adapter; never mutate scanner state; never use the scanner
      `action` as a label
    - _Requirements: 1.1, 1.3, 8.1, 9.3_

  - [x] 5.4 Write property test for posture scoring independence
    - **Feature: posture-scoring, Property 1: For any labelled corpus and any set of posture score-vectors, the metrics computed for a given posture depend only on that posture's own scores — changing, adding, or removing any other posture leaves that posture's reported metric values unchanged.**
    - **Validates: Requirements 1.1, 1.3**
    - Hypothesis, `max_examples >= 100`

  - [x] 5.5 Write unit tests for adapter read-only + availability behaviour
    - Scanning the same input twice yields an identical verdict with no scanner-module mutation
      (scanner used purely as a `text -> score` oracle); semantic adapter unavailable path returns a
      non-empty reason and no metric values
    - _Requirements: 8.1, 9.3_

- [x] 6. Checkpoint - pure cores and adapters verified
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement attribution resolution (`attribution.py`)
  - [x] 7.1 Implement attribution helpers
    - `corpus_version(root)` = git commit id pinning `tests/detection_corpus/`;
      `reproducible_command()` = the single documented command; `target_fpr()` = `0.01`
    - If any cannot be determined, signal a non-success result identifying the missing value so the
      caller emits no report
    - _Requirements: 6.5, 6.6, 6.10, 3.1_

  - [x] 7.2 Write unit tests for missing-attribution abort
    - Each of corpus version / reproducible command / target FPR undeterminable → non-success,
      error names the missing value, no report
    - _Requirements: 6.10_

- [x] 8. Implement the report emitter (`report.py`)
  - [x] 8.1 Implement `build_report` assembly
    - `build_report(results, scored_set_meta, attribution)` → `PostureReport` with **exactly one
      entry per posture (three total)**, each marked "scored" (with metric values) or "not scored"
      (with a non-empty reason) distinguishable from a metric value
    - Include per posture: `Recall_At_Target_FPR` (or FPR-floor form), `Selected_Threshold` +
      achieved FPR, per-family recall + malicious counts (incl. `paraphrase`), per-family FPR +
      benign counts (incl. `developer_traffic`), paraphrase gap when computable; a family present in
      `families.json` with 0 items marked "not computable" (never empty)
    - Record scored set + counts, corpus version, reproducible command + target FPR, the
      threshold-selection rule + "tuned on train split only", and excluded-item counts
    - _Requirements: 6.2, 6.3, 6.4, 6.5, 6.6, 6.9, 8.2, 8.3, 8.4, 3.8, 4.3, 4.4, 5.3, 5.4, 10.1_

  - [x] 8.2 Write property test for report family-completeness
    - **Feature: posture-scoring, Property 14: For any scored posture over a scored set, the report presents, for every attack family present, a per-family recall cell (a value in 0.0 to 1.0 or an explicit "not computable" marker) alongside that family's malicious count; and for every benign family present, a per-family FPR cell (a value or "not computable") alongside that family's benign count — no cell for a present family is left empty or missing.**
    - **Validates: Requirements 4.3, 4.4, 5.3, 5.4, 10.1**
    - Hypothesis, `max_examples >= 100`

  - [x] 8.3 Write property test for unavailable-posture honesty
    - **Feature: posture-scoring, Property 15: For any availability mask over the three postures with at least one available, the report contains exactly three posture entries; each available posture is marked "scored" and carries its metric values; each unavailable posture is marked "not scored" with a non-empty reason and carries no `Recall_At_Target_FPR` value and no per-family metric value, in a form distinguishable from a metric value.**
    - **Validates: Requirements 6.9, 8.1, 8.2, 8.3, 8.4**
    - Hypothesis, `max_examples >= 100`

  - [x] 8.4 Implement atomic `emit` to `docs/perf/posture_scores.md`
    - Write to a temporary file in `docs/perf/` then `os.replace` into place; on any write error,
      abort non-success and leave no partial/empty report
    - _Requirements: 6.1, 6.8_

  - [x] 8.5 Write unit tests for report content and emission
    - Report states threshold rule + 0.01 + "tuned on train split only" (3.8); states scored set +
      counts (6.4); states corpus version (6.5); reproducible command + target FPR (6.6); every
      rendered number traces to a computed field, no foreign constants (7.8); exactly one report
      file under `docs/perf/` after a run (6.1); forced write failure → non-success, no partial file
    - _Requirements: 3.8, 6.1, 6.4, 6.5, 6.6, 6.8, 7.8_

- [x] 9. Wire the CLI entrypoint (`score_postures.py`)
  - [x] 9.1 Implement pipeline orchestration and CLI
    - `--scored-set {eval,full}` defaulting to `eval`; headless, no interactive input
    - Orchestrate: resolve attribution → `load_corpus` (abort on unreadable/empty) → for each
      posture: probe, score train items, `select_threshold` on train, apply to the scored set,
      compute metrics → `build_report` (abort if all three unavailable) → `emit` report
    - _Requirements: 1.4, 6.7, 7.1, 8.4, 8.5_

  - [x] 9.2 Implement exit-code and fail-closed handling
    - Success on completion; failure exit on unreadable/empty corpus, all-postures-unavailable,
      missing attribution, or report-write failure; never emit a partial or fabricated report
    - _Requirements: 1.6, 7.3, 8.5, 6.8, 6.10_

  - [x] 9.3 Write unit tests for CLI operability and exit codes
    - Default scored set is `eval`, `--scored-set full` selects the full corpus and the choice is
      recorded (6.7); success → exit 0; empty/unreadable corpus → non-zero + no report (7.3);
      all-three-unavailable → error "no posture could be scored", no report (8.5); extensibility: a
      synthetic 4th `PostureAdapter` flows through unchanged metric/threshold/report modules and
      appears in the report (1.4)
    - _Requirements: 1.4, 6.7, 7.3, 8.5_

- [x] 10. Implement integration / gate tests (`tests/test_no_blast_radius.py`)
  - [x] 10.1 Write path-prefix and blast-radius gate tests
    - Path-prefix: every file introduced by the feature has a path prefixed by
      `scripts/detection/` or `docs/perf/` (9.1); blast-radius: a `git diff` restricted to files
      **outside** those two prefixes shows zero changed lines (also enforces exclusion of
      G0.3/G0.4/G0.5 and any corpus change)
    - _Requirements: 9.1, 9.2, 9.6_

  - [x] 10.2 Write reproducibility property test
    - **Feature: posture-scoring, Property 16: For any fixed corpus and fixed posture score functions, running the deterministic pipeline twice produces metric values that are byte-for-byte identical across the two runs (excluding fields that record wall-clock timestamps or elapsed run duration).**
    - **Validates: Requirements 7.2, 6.6, 10.3**
    - Hypothesis, `max_examples >= 100`

  - [x] 10.3 Write gateway-suite regression parity test
    - Assert the gateway test gate (`cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests
      -q`) produces an identical set of pass/fail outcomes for all pre-existing tests before and
      after adding the feature (passing count and failing count each unchanged, no transition)
    - _Requirements: 9.5_

- [x] 11. Final checkpoint - run the gate and confirm zero blast radius
  - Run the reproducible command twice and confirm byte-identical metric values (excluding
    timestamp/duration fields):
    `cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py`
  - Run the gateway regression gate and confirm identical pre-existing pass/fail outcomes:
    `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`
  - Confirm zero blast radius: a `git diff` outside `scripts/detection/**` and `docs/perf/**` shows
    zero changed lines, and `docs/perf/posture_scores.md` exists with three posture entries
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 6.1, 7.2, 7.3, 9.1, 9.2, 9.5, 10.1, 10.4_

## Notes

- Tasks marked with `*` are optional (test sub-tasks) and can be skipped for a faster core, but each
  property test maps one-to-one to a design Property (P1–P16) and carries its
  `Feature: posture-scoring, Property N: ...` tag plus a `Validates: Requirements ...` line.
- Each task references specific requirement sub-clauses for traceability.
- The change set is strictly additive: only `scripts/detection/**` and `docs/perf/**` (R9); no task
  touches `scanner.py` or any production path.
- Property tests validate the pure deterministic core; unit/example tests cover boundaries, report
  content, emission, exit codes, and read-only scanner use; integration/gate tests cover
  blast-radius, path-prefix, gateway regression parity, and reproducibility.
- Checkpoints ensure incremental validation before wiring proceeds.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "7.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2", "3.4", "4.2", "4.3", "7.2"] },
    { "id": 3, "tasks": ["2.4", "2.5", "2.6", "3.3", "3.5", "3.6", "5.1"] },
    { "id": 4, "tasks": ["2.7", "2.8", "2.9", "2.10", "5.2", "5.3", "8.1"] },
    { "id": 5, "tasks": ["5.4", "5.5", "8.2", "8.3", "8.4"] },
    { "id": 6, "tasks": ["8.5", "9.1"] },
    { "id": 7, "tasks": ["9.2", "10.1"] },
    { "id": 8, "tasks": ["9.3", "10.2", "10.3"] }
  ]
}
```
