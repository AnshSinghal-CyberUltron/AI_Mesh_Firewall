# Requirements Document

## Introduction

This feature builds a **posture scoring harness** — a reproducible measurement tool and a
committed, human-readable report — that publishes what each detection posture of the shipped
scanner actually detects. It is task **G0.2** of the evidence-based hot-path plan
(`docs/plans/2026-08-27-task-tracker.md`, Gate 0, row G0.2).

Gate 0 exists because posture decisions in this project have been made without knowing what any
posture actually detects; the plan was about to certify a service-level objective (SLO) for a
posture whose detection was never measured. G0.2 closes that gap by scoring three named detection
postures of the shipped scanner (`gateway/ai_mesh_gateway/scanner.py`) over the labelled detection
corpus produced by G0.1 (committed at `tests/detection_corpus/`), and by publishing a table of
recall at a 1% false-positive-rate operating point plus a per-family breakdown. The three postures
are: **Tier-1 only** (the fast pattern/regex scanner alone), **Tier-1 + policy** (Tier-1 plus the
policy layer), and **Tier-1 + semantic** (Tier-1 plus the semantic/Tier-2 detector).

The published table is itself the deliverable and the gate: no posture may be recommended and no
SLO may be published until this table exists. This whole gate exists because unmeasured numbers
were about to be certified, so the harness is bound by strict honesty and reproducibility rules —
labels are read only from the corpus, no number is published that the harness did not compute over
the corpus, an unscorable posture is reported as "not scored" rather than fabricated, and the
`eval` split is never tuned on.

This spec is strictly **measurement-only and additive**: new code lives under `scripts/detection/`
and the report under `docs/perf/`. It changes no line in the shipped scanner, no production code
path, and no detection verdict. It does not fix the `command_injection` backtick over-match (G0.3),
does not fix the `data_leakage` tool-description over-match (G0.4), does not decide the explanatory
carve-out (G0.5), does not build or extend the corpus (G0.1, done), and does not itself recommend a
posture or certify an SLO (downstream, gated on this table).

## Glossary

- **Posture_Harness**: The reproducible measurement tool that scores detection postures of the
  shipped scanner over the Detection_Corpus and emits the Posture_Report, rooted at the repository
  path `scripts/detection/` (principally `scripts/detection/score_postures.py`).
- **Detection_Corpus**: The committed, labelled corpus produced by task G0.1, rooted at the
  repository path `tests/detection_corpus/`, containing Corpus_Items in `malicious.jsonl` and
  `benign.jsonl`, a `families.json` documenting family names, and a `corpus_lint.py` validator.
- **Corpus_Item**: A single labelled record of the Detection_Corpus, one JSON object per line of a
  JSONL file, with the fields `id`, `text`, `label`, `family`, `split`, `provenance`, and
  `fake_fixtures`.
- **Corpus_Label**: The ground-truth classification of a Corpus_Item, exactly one of `malicious`
  or `benign`, read only from the Corpus_Item's `label` field.
- **Family**: The named category of a Corpus_Item, read from its `family` field; an Attack_Family
  for a malicious item (for example `prompt_injection`, `paraphrase`) or a Benign_Family for a
  benign item (for example `developer_traffic`, `general_benign`).
- **Attack_Family**: A Family whose Corpus_Items all carry the Corpus_Label `malicious`.
- **Benign_Family**: A Family whose Corpus_Items all carry the Corpus_Label `benign`.
- **Split**: The partition assignment of a Corpus_Item, read from its `split` field, exactly one of
  `train` or `eval`.
- **Eval_Split**: The subset of Corpus_Items whose Split value is `eval`, reserved for the headline
  measurement.
- **Scored_Set**: The set of Corpus_Items over which a single Posture_Scoring_Run computes its
  metrics, either the Eval_Split or the full Detection_Corpus, chosen explicitly per run and
  recorded in the Posture_Report.
- **Posture**: A named, reproducible detection configuration of the shipped scanner. The three
  Postures scored by this feature are Tier1_Only, Tier1_Plus_Policy, and Tier1_Plus_Semantic.
- **Tier1_Only**: The Posture that applies the fast pattern/regex scanner (`ATTACK_PATTERNS`)
  alone.
- **Tier1_Plus_Policy**: The Posture that applies Tier-1 together with the policy layer.
- **Tier1_Plus_Semantic**: The Posture that applies Tier-1 together with the semantic (Tier-2)
  detector.
- **Posture_Score**: The numeric detection signal a Posture assigns to one Corpus_Item, a real
  value used to decide, at a chosen Decision_Threshold, whether the item is flagged as malicious.
- **Decision_Threshold**: The numeric cutoff applied to a Posture_Score above which (or at which) a
  Corpus_Item is counted as flagged malicious by a Posture.
- **Flagged_Malicious**: The condition in which a Posture, at a given Decision_Threshold, classifies
  a Corpus_Item as malicious.
- **Recall**: For a Posture over a Scored_Set at a given Decision_Threshold, the fraction of
  malicious Corpus_Items that are Flagged_Malicious, computed as the count of malicious items
  Flagged_Malicious divided by the total count of malicious items in the Scored_Set.
- **False_Positive_Rate**: For a Posture over a Scored_Set at a given Decision_Threshold, the
  fraction of benign Corpus_Items that are Flagged_Malicious, abbreviated FPR, computed as the
  count of benign items Flagged_Malicious divided by the total count of benign items in the
  Scored_Set.
- **Target_FPR**: The maximum benign false-positive rate defining the headline operating point,
  fixed at 0.01 (1%, equivalently 1e-2).
- **Recall_At_Target_FPR**: The headline metric for a Posture — the Recall achieved at the
  Selected_Threshold chosen so that the Posture's False_Positive_Rate over the Scored_Set is at
  most the Target_FPR.
- **Selected_Threshold**: The Decision_Threshold the Posture_Harness chooses to realise the
  Recall_At_Target_FPR operating point per the Threshold_Selection_Rule.
- **Threshold_Selection_Rule**: The documented, deterministic rule the Posture_Harness uses to pick
  the Selected_Threshold that maximises Recall subject to False_Positive_Rate ≤ Target_FPR.
- **FPR_Floor**: The lowest False_Positive_Rate a Posture can achieve over the Scored_Set across all
  candidate Decision_Thresholds; when the FPR_Floor exceeds the Target_FPR, no threshold achieves
  the Target_FPR.
- **Per_Family_Recall**: The Recall of a Posture computed separately for the malicious Corpus_Items
  of each Attack_Family in the Scored_Set, at the Selected_Threshold.
- **Per_Family_FPR**: The False_Positive_Rate of a Posture computed separately for the benign
  Corpus_Items of each Benign_Family in the Scored_Set, at the Selected_Threshold.
- **Posture_Scoring_Run**: One execution of the Posture_Harness that scores exactly one Posture over
  exactly one Scored_Set and produces that Posture's metrics.
- **Posture_Report**: The committed, human-readable report published under `docs/perf/` that
  presents, for each scored Posture, its Recall_At_Target_FPR, its Per_Family_Recall, its
  Per_Family_FPR, and the attribution metadata identifying the Scored_Set and corpus version.
- **Corpus_Version**: The identifier that pins the exact Detection_Corpus contents a
  Posture_Scoring_Run was computed over — the repository commit identifier of `tests/detection_corpus/`
  at scoring time, sufficient to attribute and reproduce the number.
- **Unavailable_Posture**: A Posture that the Posture_Harness cannot score in the current
  environment (for example Tier1_Plus_Semantic when the semantic/Tier-2 detector is not installed
  or not reachable).
- **Reproducible_Command**: The single documented command that runs the Posture_Harness such that
  the same Detection_Corpus, the same shipped scanner, and the same command produce an identical
  Posture_Report.

## Requirements

### Requirement 1: Three named postures scored independently

**User Story:** As a firewall engineer, I want each of the three named postures scored on its own over the corpus, so that the report shows what each detection configuration detects without one posture's result masking another.

#### Acceptance Criteria

1. THE Posture_Harness SHALL score each of the three Postures Tier1_Only, Tier1_Plus_Policy, and Tier1_Plus_Semantic independently over the Scored_Set, producing for each Posture a distinct set of metrics comprising its Recall_At_Target_FPR, its Per_Family_Recall for every Attack_Family, and its Per_Family_FPR for every Benign_Family.
2. THE Posture_Harness SHALL represent each Posture as a named, reproducible detection configuration identified by a stable Posture name that is a non-empty string of 1 to 128 characters and is unique across all configured Postures.
3. WHEN the Posture_Harness scores one Posture, THE Posture_Harness SHALL derive that Posture's metrics only from that Posture's own detection configuration, such that changing, adding, or removing any other Posture leaves this Posture's reported metric values unchanged.
4. THE Posture_Harness SHALL support the addition of a further Posture as a new named detection configuration without modification to the metric-computation, threshold-selection, or reporting logic of the harness.
5. IF two or more Postures are configured with the same Posture name, THEN THE Posture_Harness SHALL reject the configuration, produce an error indication identifying the duplicated Posture name, emit no Posture_Report, and leave any previously committed Posture_Report unchanged.
6. IF the Scored_Set contains 0 Corpus_Items, THEN THE Posture_Harness SHALL produce an error indication stating that the Scored_Set is empty and SHALL emit no Posture_Report.

### Requirement 2: Recall and false-positive-rate computation

**User Story:** As a measurement engineer, I want recall and FPR computed from corpus labels for each posture, so that every published number is a true-positive-rate on malicious items and a false-positive-rate on benign items.

#### Acceptance Criteria

1. WHEN the Posture_Harness scores a Posture over a Scored_Set at a Decision_Threshold, THE Posture_Harness SHALL compute Recall as the count of malicious Corpus_Items that are Flagged_Malicious divided by the total count of malicious Corpus_Items in the Scored_Set, yielding a value in the inclusive range 0.0 to 1.0.
2. WHEN the Posture_Harness scores a Posture over a Scored_Set at a Decision_Threshold, THE Posture_Harness SHALL compute False_Positive_Rate as the count of benign Corpus_Items that are Flagged_Malicious divided by the total count of benign Corpus_Items in the Scored_Set, yielding a value in the inclusive range 0.0 to 1.0.
3. THE Posture_Harness SHALL classify each Corpus_Item as malicious or benign solely from that Corpus_Item's `label` field in the Detection_Corpus, treating a Corpus_Item as neither malicious nor benign when its `label` field is absent or does not match a recognized malicious or benign label value.
4. IF the Scored_Set contains 0 malicious Corpus_Items, THEN THE Posture_Harness SHALL report Recall as not computable for that Scored_Set and produce an error indication stating that the malicious count is 0, rather than reporting a Recall value.
5. IF the Scored_Set contains 0 benign Corpus_Items, THEN THE Posture_Harness SHALL report False_Positive_Rate as not computable for that Scored_Set and produce an error indication stating that the benign count is 0, rather than reporting a False_Positive_Rate value.
6. THE Posture_Harness SHALL record, for each Posture_Scoring_Run, the total count of malicious Corpus_Items and the total count of benign Corpus_Items in the Scored_Set used to compute Recall and False_Positive_Rate.
7. IF the Scored_Set contains one or more Corpus_Items whose `label` field is absent or unrecognized, THEN THE Posture_Harness SHALL exclude those Corpus_Items from both the malicious and benign counts, record the count of excluded Corpus_Items for that Posture_Scoring_Run, and produce an error indication stating that one or more items had an absent or unrecognized label.
8. WHEN the Posture_Harness reports a computed Recall or False_Positive_Rate value, THE Posture_Harness SHALL round each value to 4 decimal places using round-half-up.

### Requirement 3: Recall at 1% FPR threshold selection

**User Story:** As a Gate 0 approver, I want a precise rule for choosing the decision threshold that hits at most 1% FPR, so that the headline recall@1%FPR is defined, reproducible, and honest when no threshold can reach 1% FPR.

#### Acceptance Criteria

1. THE Posture_Harness SHALL define the Target_FPR as 0.01.
2. THE Posture_Harness SHALL select, per the Threshold_Selection_Rule, the Selected_Threshold as the Decision_Threshold that maximises Recall over the Scored_Set subject to the constraint that the Posture's False_Positive_Rate over the Scored_Set is at most the Target_FPR, where a False_Positive_Rate is treated as at most the Target_FPR when it does not exceed the Target_FPR by more than 0.000001.
3. WHEN more than one Decision_Threshold achieves the maximum Recall subject to False_Positive_Rate at most Target_FPR, THE Posture_Harness SHALL select the Decision_Threshold among them that yields the lowest False_Positive_Rate, and WHEN more than one such Decision_Threshold remains, THE Posture_Harness SHALL select the highest Decision_Threshold value among them, so that repeated runs over identical Posture_Scores choose the identical Selected_Threshold.
4. WHEN the Posture_Harness has chosen the Selected_Threshold, THE Posture_Harness SHALL report the Recall_At_Target_FPR as the Recall at the Selected_Threshold together with the achieved False_Positive_Rate at the Selected_Threshold and the Selected_Threshold value.
5. IF no Decision_Threshold achieves a False_Positive_Rate at most the Target_FPR over the Scored_Set, or the Scored_Set contains no benign Corpus_Items from which a False_Positive_Rate can be computed, THEN THE Posture_Harness SHALL report that the Target_FPR is not achievable, SHALL report the FPR_Floor as the lowest achievable False_Positive_Rate, and SHALL report the Recall at the Decision_Threshold that realises the FPR_Floor, rather than reporting a Recall_At_Target_FPR value.
6. THE Posture_Harness SHALL derive the set of candidate Decision_Thresholds solely from the distinct Posture_Scores the Posture assigns to the Corpus_Items in the Scored_Set, such that the Threshold_Selection_Rule evaluates each operating point reachable from the observed scores exactly once and evaluates no threshold not so reachable.
7. THE Posture_Harness SHALL derive the candidate Decision_Thresholds used for Threshold_Selection_Rule tuning only from Corpus_Items in the train split, such that no Corpus_Item outside the train split influences the Selected_Threshold.
8. THE Posture_Report SHALL state the Threshold_Selection_Rule, the Target_FPR value used, and that the Selected_Threshold was tuned on the train split only, such that a reviewer can reconstruct how each Recall_At_Target_FPR was chosen.

### Requirement 4: Per-family recall breakdown for attack families

**User Story:** As a reviewer signing off Gate 0, I want recall broken down by attack family, so that gaps like near-zero paraphrase recall and the same-family versus paraphrase difference are visible.

#### Acceptance Criteria

1. WHEN the Posture_Harness scores a Posture, THE Posture_Harness SHALL compute Per_Family_Recall separately for each Attack_Family present in the Scored_Set, at the Posture's Selected_Threshold, yielding a value in the inclusive range 0.0 to 1.0.
2. THE Posture_Harness SHALL compute the Per_Family_Recall of an Attack_Family as the count of that Attack_Family's malicious Corpus_Items that are Flagged_Malicious divided by the total count of that Attack_Family's malicious Corpus_Items in the Scored_Set, rounded to 4 decimal places.
3. THE Posture_Report SHALL present, for each scored Posture, the Per_Family_Recall of every Attack_Family present in the Scored_Set, including the `paraphrase` Attack_Family.
4. THE Posture_Report SHALL present, for each Attack_Family row, the count of that Attack_Family's malicious Corpus_Items in the Scored_Set alongside the Per_Family_Recall value.
5. IF an Attack_Family named in the Detection_Corpus `families.json` has 0 Corpus_Items in the Scored_Set, THEN THE Posture_Report SHALL mark that Attack_Family's Per_Family_Recall as not computable for that Scored_Set rather than presenting a Per_Family_Recall value.
6. WHERE both the `paraphrase` Attack_Family Per_Family_Recall and the mean Per_Family_Recall across the non-paraphrase Attack_Families are computable, THE Posture_Report SHALL present the difference between the mean non-paraphrase Per_Family_Recall and the `paraphrase` Per_Family_Recall as a value in the inclusive range -1.0 to 1.0 rounded to 4 decimal places.

### Requirement 5: Per-family FPR breakdown for benign families

**User Story:** As the engineer scoping the false-positive fixes, I want FPR broken down by benign family, so that the developer_traffic false-positive rate is visible per category.

#### Acceptance Criteria

1. WHEN the Posture_Harness scores a Posture, THE Posture_Harness SHALL compute Per_Family_FPR separately for each Benign_Family present in the Scored_Set, at the Posture's Selected_Threshold.
2. THE Posture_Harness SHALL compute the Per_Family_FPR of a Benign_Family as the count of that Benign_Family's benign Corpus_Items that are Flagged_Malicious divided by the total count of that Benign_Family's benign Corpus_Items in the Scored_Set, expressed as a proportion in the range 0.0 to 1.0 rounded to 4 decimal places.
3. THE Posture_Report SHALL present, for each scored Posture, the Per_Family_FPR of every Benign_Family present in the Scored_Set, including the `developer_traffic` Benign_Family.
4. THE Posture_Report SHALL present, for each Benign_Family row, the count of that Benign_Family's benign Corpus_Items in the Scored_Set alongside the Per_Family_FPR value expressed as a proportion in the range 0.0 to 1.0 rounded to 4 decimal places.
5. IF a Benign_Family named in the Detection_Corpus `families.json` has 0 Corpus_Items in the Scored_Set, THEN THE Posture_Report SHALL mark that Benign_Family's Per_Family_FPR as not computable for that Scored_Set rather than presenting a Per_Family_FPR value.

### Requirement 6: Committed human-readable report with attribution

**User Story:** As a Gate 0 approver, I want a committed, readable report I can open to make the posture decision, so that the per-posture numbers are visible, reproducible, and attributable to a specific corpus and split.

#### Acceptance Criteria

1. WHEN the Posture_Harness completes a scoring run, THE Posture_Harness SHALL emit exactly one Posture_Report as a committed plain-text file under the repository path `docs/perf/`, where "committed" means the file is present in the working tree after the run completes.
2. THE Posture_Report SHALL present, for each scored Posture, the Posture name and that Posture's Recall_At_Target_FPR.
3. THE Posture_Report SHALL present, for each scored Posture, the Per_Family_Recall for every Attack_Family and the Per_Family_FPR for every Benign_Family present in the Scored_Set.
4. THE Posture_Report SHALL state the Scored_Set used, identifying whether it is the Eval_Split or the full Detection_Corpus, together with the count of malicious Corpus_Items and the count of benign Corpus_Items it contains.
5. THE Posture_Report SHALL state the Corpus_Version identifying the exact Detection_Corpus contents the metrics were computed over.
6. THE Posture_Report SHALL state the Reproducible_Command and the Target_FPR (0.01) used to produce the report, such that re-running the stated Reproducible_Command against the same Corpus_Version reproduces every Recall_At_Target_FPR, Per_Family_Recall, and Per_Family_FPR value in the report identically.
7. THE Posture_Harness SHALL score the Eval_Split as the Scored_Set for the headline Recall_At_Target_FPR measurement, and WHERE scoring the full Detection_Corpus is explicitly requested, THE Posture_Harness SHALL score the full Detection_Corpus as the Scored_Set, recording in the Posture_Report which Scored_Set each reported number was computed over.
8. IF the Posture_Harness cannot write the Posture_Report to `docs/perf/`, THEN THE Posture_Harness SHALL terminate with a non-success result and an error indication identifying the write failure, and SHALL NOT leave a partial or empty Posture_Report file.
9. IF one or more Postures cannot be scored during a run, THEN THE Posture_Report SHALL record each unscored Posture together with an indication that it was not scored, and SHALL still present the metrics for every Posture that was scored.
10. IF the Corpus_Version, the Reproducible_Command, or the Target_FPR cannot be determined for a run, THEN THE Posture_Harness SHALL terminate with a non-success result and an error indication identifying the missing attribution value, and SHALL NOT emit a Posture_Report omitting that value.

### Requirement 7: Reproducibility and no evaluation leakage

**User Story:** As the plan owner, I want the score to be reproducible and free of evaluation leakage, so that the published table is trustworthy and can be regenerated on demand.

#### Acceptance Criteria

1. THE Posture_Harness SHALL be runnable through a single documented Reproducible_Command aligned with the repository gateway venv command style (for example `cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py`), where the command requires no interactive input and no arguments beyond those documented alongside it.
2. WHEN the Reproducible_Command is run twice over the same Detection_Corpus with the same shipped scanner, THE Posture_Harness SHALL produce a Posture_Report in which every reported metric value is byte-for-byte identical across the two runs, excluding fields that record wall-clock timestamps or total elapsed run duration.
3. IF the Reproducible_Command completes successfully, THEN THE Posture_Harness SHALL terminate with a success exit indication; and IF the Detection_Corpus cannot be read or contains no Corpus_Item, THEN THE Posture_Harness SHALL terminate with a failure exit indication and emit an error message identifying the unreadable or empty corpus, without writing a partial Posture_Report.
4. THE Posture_Harness SHALL read every Corpus_Label only from the Detection_Corpus item label field and SHALL derive no Corpus_Label from the shipped scanner's output.
5. THE Posture_Harness SHALL compute the headline Recall_At_Target_FPR over the Eval_Split without using any Eval_Split Corpus_Item to select the Selected_Threshold in a manner that consumes `eval` labels for tuning, such that the Eval_Split is measured and not tuned on.
6. IF the Threshold_Selection_Rule requires threshold tuning that would consume labels, THEN THE Posture_Harness SHALL perform that tuning using only Corpus_Items whose Split is `train`, and SHALL apply the resulting Selected_Threshold to the Eval_Split for the headline measurement.
7. IF any Corpus_Item carries a Split value other than `train` or `eval`, THEN THE Posture_Harness SHALL exclude that Corpus_Item from both threshold tuning and the headline measurement, and SHALL record the count of excluded Corpus_Items in the Posture_Report.
8. THE Posture_Report SHALL contain only metric values computed by the Posture_Harness over the Detection_Corpus during a single Posture_Scoring_Run, such that no reported number originates from a source other than that Posture_Scoring_Run.

### Requirement 8: Honest handling of an unavailable posture

**User Story:** As a reviewer, I want an unscorable posture reported as explicitly not scored, so that a missing detector never appears as a fabricated or silently omitted row.

#### Acceptance Criteria

1. IF a Posture is an Unavailable_Posture, THEN THE Posture_Harness SHALL record that Posture's status as "not scored" together with a non-empty reason identifying why the Posture could not be scored, and SHALL NOT compute or store any Recall_At_Target_FPR value or per-family metric value for that Posture.
2. WHERE a Posture is an Unavailable_Posture, THE Posture_Report SHALL present that Posture with an explicit "not scored" status and the recorded non-empty reason, and SHALL NOT present a Recall_At_Target_FPR value or any per-family metric value for that Posture.
3. THE Posture_Report SHALL contain exactly one entry for each of the three Postures, such that no Posture is omitted, and SHALL mark each entry as either "scored" with metric values or "not scored" with a reason in a form distinguishable from a metric value.
4. WHEN at least one of the three Postures can be scored, THE Posture_Harness SHALL score every available Posture, emit the Posture_Report containing all three Postures, and mark each Unavailable_Posture as "not scored" with its recorded reason.
5. IF all three Postures are Unavailable_Postures, THEN THE Posture_Harness SHALL produce an error indication stating that no Posture could be scored, SHALL NOT emit any metric value, and SHALL NOT emit a Posture_Report containing fabricated metric values.

### Requirement 9: Measurement-only with zero blast radius

**User Story:** As the plan owner, I want G0.2 to be measurement-only, so that publishing the table cannot change any scanner verdict or touch any production path.

#### Acceptance Criteria

1. THE Posture_Harness SHALL reside entirely under the repository path `scripts/detection/`, and THE Posture_Report SHALL reside entirely under the repository path `docs/perf/`, such that every file introduced by this feature has a path prefixed by `scripts/detection/` or `docs/perf/`.
2. THE Posture_Harness SHALL introduce zero added, modified, or deleted lines in `gateway/ai_mesh_gateway/scanner.py` and in every other production code path outside `scripts/detection/` and `docs/perf/`, such that a line-level diff of all files outside those two path prefixes shows zero changed lines.
3. THE Posture_Harness SHALL invoke the shipped scanner in read-only mode, such that for every input in the Detection_Corpus the detection verdict emitted by the shipped scanner is byte-for-byte identical before and after this feature is added.
4. IF the Posture_Harness attempts any write, mutation, or state change to the shipped scanner or to any file outside `scripts/detection/` and `docs/perf/`, THEN THE Posture_Harness SHALL terminate without applying the change and SHALL emit an error indication identifying the attempted out-of-scope modification, and SHALL leave all files outside those two path prefixes unchanged.
5. WHEN the gateway test gate (`cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`) is run immediately before adding this feature and immediately after adding this feature, THE gateway test gate SHALL produce an identical set of pass/fail outcomes for all pre-existing tests, such that the count of passing tests and the count of failing tests are each unchanged and no pre-existing test transitions between pass and fail.
6. THE Posture_Harness SHALL exclude from its own scope the `command_injection` backtick over-match fix, the `data_leakage` tool-description over-match fix, the explanatory carve-out decision, any change to the Detection_Corpus, and any posture recommendation or SLO certification.

### Requirement 10: The published table is the gate

**User Story:** As the Gate 0 owner, I want the published table to be the exit criterion, so that no posture recommendation and no SLO can proceed until the measured table exists.

#### Acceptance Criteria

1. THE Posture_Report SHALL exist as a committed artefact under `docs/perf/` presenting, for each scored Posture, the Recall_At_Target_FPR as a value in the inclusive range 0.0 to 1.0, the Per_Family_Recall for every Attack_Family present in the Scored_Set, and the Per_Family_FPR for every Benign_Family present in the Scored_Set, with no per-family cell left empty or missing for a Family present in the Scored_Set.
2. WHILE the Posture_Report does not yet exist as a committed artefact under `docs/perf/`, THE posture-scoring feature SHALL be treated as not meeting its exit criterion, such that no posture recommendation and no SLO is published.
3. THE Posture_Report SHALL carry the attribution metadata required by Requirement 6, such that each published number is reproducible by re-running the stated Reproducible_Command against the stated Corpus_Version and Scored_Set and obtaining identical metric values.
4. WHEN the Posture_Report exists as a committed artefact under `docs/perf/` and presents the metrics required by criterion 1 with the attribution required by criterion 3, THE posture-scoring feature SHALL be treated as meeting the G0.2 exit criterion.
