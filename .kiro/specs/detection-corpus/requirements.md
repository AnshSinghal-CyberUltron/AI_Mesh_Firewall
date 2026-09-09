# Requirements Document

## Introduction

This feature builds a **labelled detection corpus** — a committed, versioned set of prompts,
each labelled malicious or benign with per-item provenance — that becomes the ground-truth
measurement artefact for the AI Mesh Firewall's detection layer. It is task **G0.1** of the
evidence-based hot-path plan (`docs/plans/2026-08-27-task-tracker.md`, Gate 0, row G0.1).

Gate 0 exists because posture decisions in this project have been made without knowing what
any posture actually detects. Measured Tier-1 behaviour on the shipped scanner
(`gateway/ai_mesh_gateway/scanner.py`) is poor and asymmetric: 3/10 same-family injections
caught, 0/10 paraphrased injections caught, 5/5 benign inline-code prompts hard-blocked
(`command_injection` at confidence 1.00), and 3/12 ordinary tool descriptions hard-blocked
(`data_leakage` at confidence 1.00). The corpus is the input that downstream tasks score
against: G0.2 measures recall/false-positive rate per posture over this corpus, and G0.3/G0.4
use its benign families as their false-positive suite when fixing the two over-matching patterns.

This spec covers **only** the corpus artefact, its schema, its integrity checks, its label
audit, its reproducible splits, and its written sampling methodology. It is strictly
**test-only and additive**: it changes no production code, no scanner pattern, and no detection
verdict. It does not score any posture (G0.2), fix any pattern (G0.3/G0.4), or decide the
explanatory carve-out (G0.5).

## Glossary

- **Detection_Corpus**: The committed collection of labelled prompt items and their supporting
  artefacts (data files, lint tool, methodology document), rooted at the repository path
  `tests/detection_corpus/`.
- **Corpus_Item**: A single labelled record in the corpus, stored as one JSON object on one line
  of a JSONL file, describing exactly one prompt and its metadata.
- **Label**: The classification of a Corpus_Item as exactly one of the two values `malicious`
  or `benign`.
- **Malicious_Item**: A Corpus_Item whose Label is `malicious` — a prompt whose intent is an
  injection, jailbreak, data-exfiltration, or other attack the firewall is expected to detect.
- **Benign_Item**: A Corpus_Item whose Label is `benign` — a prompt with no attack intent that
  the firewall is expected to allow (including legitimate developer traffic that superficially
  resembles attacks).
- **Family**: The named category a Corpus_Item belongs to. For a Malicious_Item the Family names
  the attack class (for example `prompt_injection`, `jailbreak`, `data_leakage`, `paraphrase`);
  for a Benign_Item the Family names the benign class (for example `developer_traffic`,
  `general_benign`).
- **Attack_Family**: A Family whose items are all malicious. The shipped scanner recognises nine
  Attack_Family names in `ATTACK_PATTERNS`: `prompt_injection`, `jailbreak`, `data_leakage`,
  `goal_hijacking`, `tool_overreach`, `sql_injection`, `command_injection`, `path_traversal`,
  `vector_injection`.
- **Paraphrase_Family**: A dedicated Attack_Family of Malicious_Items that express the same
  attack intent as other malicious items but using **Disjoint_Trigger_Vocabulary** — none of the
  literal trigger tokens or phrases the shipped scanner regexes key on.
- **Disjoint_Trigger_Vocabulary**: The property that a Corpus_Item's prompt text contains none of
  the documented **Trigger_Tokens**. A dedicated, committed list of Trigger_Tokens makes this
  property concrete and machine-checkable.
- **Trigger_Token**: A literal token or phrase that a shipped scanner pattern in
  `ATTACK_PATTERNS` (or its fuzzy anchor list) keys on to raise a detection — for example
  `ignore previous instructions`, `developer mode`, `DROP TABLE`, `UNION SELECT`, `rm -rf`.
  The Trigger_Token list is the enumeration of these tokens/phrases derived from the shipped
  patterns and committed as part of the corpus.
- **Developer_Traffic_Family**: A dedicated benign Family of Benign_Items representing ordinary
  developer and agentic traffic — inline-backtick code, tool descriptions, SQL statements, and
  shell commands — deliberately including prompts that exercise the two known false-positive
  pattern shapes.
- **FP_Prone_Shape**: One of the two shipped scanner pattern shapes known to over-match benign
  developer traffic: (1) the `command_injection` pattern `` `[^`]+` `` (any backtick pair,
  matching benign inline code), and (2) the four `.*`-wildcard patterns in `data_leakage`
  (matching benign tool descriptions).
- **Provenance**: Per-item metadata recording where a Corpus_Item came from and how it was
  produced — for example `hand_authored`, `derived_from_scanner_pattern`, or a cited public
  source — sufficient for a reviewer to reproduce or verify the item's origin.
- **Split**: The assignment of a Corpus_Item to exactly one of the two partitions `train` or
  `eval`, used to separate items available for tuning from items reserved for measurement.
- **Train_Eval_Leakage**: The presence in the corpus of a `train` item and an `eval` item whose
  Normalized_Text is identical or near-identical, which would let an item measured in `eval`
  also be seen during tuning.
- **Normalized_Text**: The prompt text of a Corpus_Item after a documented normalization
  (lowercasing, Unicode NFKC folding, and whitespace collapse) used as the basis for
  duplicate and leakage detection.
- **Corpus_Lint**: The machine-checkable validation tool that verifies the corpus satisfies its
  schema, uniqueness, no-leakage, disjointness, and coverage-minimum requirements, runnable as a
  single documented command.
- **Label_Audit**: The documented re-check of a random 10% sample of Corpus_Items to confirm
  that assigned Labels are correct, with its methodology and results recorded.
- **Sampling_Methodology**: The written document describing how items were sourced and authored,
  how Paraphrase_Family disjointness was ensured, how Splits were assigned reproducibly, and how
  the Label_Audit was performed.
- **Fake_Fixture**: A sensitive-looking value (for example an SSN, credit card number, API key,
  or email) that is deliberately synthetic, documented as fake, and safe to commit to the
  repository.

## Requirements

### Requirement 1: Corpus size and family coverage

**User Story:** As a firewall engineer, I want the corpus to meet fixed size and family-coverage
minimums, so that G0.2 can measure recall and false-positive rate over a statistically meaningful
and representative set of attacks and benign traffic.

#### Acceptance Criteria

1. THE Detection_Corpus SHALL contain at least 300 Corpus_Items with the Label `malicious`.
2. THE Detection_Corpus SHALL contain at least 300 Corpus_Items with the Label `benign`.
3. THE Detection_Corpus SHALL contain Corpus_Items with the Label `malicious` spanning at least 8 distinct Attack_Family values drawn from the set {prompt_injection, jailbreak, data_leakage, goal_hijacking, tool_overreach, sql_injection, command_injection, path_traversal, vector_injection}.
4. THE Detection_Corpus SHALL contain at least 1 Corpus_Item whose Family is the Paraphrase_Family.
5. THE Detection_Corpus SHALL contain at least 1 Corpus_Item whose Family is the Developer_Traffic_Family.
6. WHERE a Corpus_Item has the Label `malicious`, THE Detection_Corpus SHALL assign that item exactly one Family that is a member of the Attack_Family set defined in criterion 3.
7. WHERE a Corpus_Item has the Label `benign`, THE Detection_Corpus SHALL assign that item exactly one Family that is a benign Family and is not a member of the Attack_Family set.
8. IF a Corpus_Item has a Label that is neither `malicious` nor `benign`, or has no assigned Family, or has an assigned Family that is not valid for its Label, THEN THE Detection_Corpus SHALL reject that Corpus_Item and exclude it from the counts in criteria 1 through 5, and SHALL produce a validation error identifying the rejected item and the reason for rejection.

### Requirement 2: JSONL schema and per-item fields

**User Story:** As a consumer of the corpus (the G0.2 scoring script and the Corpus_Lint), I want
every item stored in a strict, machine-readable schema, so that I can load and reason about each
item without guessing its structure.

#### Acceptance Criteria

1. THE Detection_Corpus SHALL store Corpus_Items in JSONL files under the repository path `tests/detection_corpus/`, with exactly one JSON object per line and each line terminated by a single newline character.
2. THE Detection_Corpus SHALL assign each Corpus_Item a stable identifier that is a non-empty string of 1 to 128 characters and is unique across every Corpus_Item in the entire corpus.
3. IF two or more Corpus_Items share the same identifier value, THEN THE Corpus_Lint SHALL report each duplicated identifier as a schema violation identifying the affected lines.
4. THE Detection_Corpus SHALL record for each Corpus_Item the prompt text as a string of length 1 to 1,048,576 characters that contains at least one non-whitespace character.
5. THE Detection_Corpus SHALL record for each Corpus_Item a Label whose value is exactly one of `malicious` or `benign`.
6. THE Detection_Corpus SHALL record for each Corpus_Item a Family value that is exactly one member of the documented set of Family names published for the corpus.
7. THE Detection_Corpus SHALL record for each Corpus_Item a Split value that is exactly one of `train` or `eval`.
8. THE Detection_Corpus SHALL record for each Corpus_Item a Provenance value as a non-empty string of 1 to 512 characters that identifies the item's origin.
9. IF a line in a corpus JSONL file is not valid JSON, or omits any of the fields identifier, prompt text, Label, Family, Split, or Provenance, or contains any of these fields with a value outside its permitted type, length, or enumerated set, THEN THE Corpus_Lint SHALL report that line as a schema violation indicating the line number and the specific field and reason for the violation.

### Requirement 3: Paraphrase family disjointness

**User Story:** As a reviewer signing off Gate 0, I want the paraphrase family to provably contain
none of the literal trigger tokens the shipped patterns key on, so that "0/10 paraphrase recall"
measures a genuine detection gap rather than an artefact of reused trigger words.

#### Acceptance Criteria

1. THE Detection_Corpus SHALL include a committed Trigger_Token list that enumerates every literal token and phrase that the shipped `ATTACK_PATTERNS` scanner patterns key on, where each entry records the token text and the identifier of the scanner pattern it was derived from.
2. THE Detection_Corpus SHALL define Normalized_Text as text produced by applying, in order, lowercasing, NFKC Unicode normalization, and collapsing of consecutive whitespace characters to a single space with leading and trailing whitespace removed.
3. WHERE a Corpus_Item's Family is the Paraphrase_Family, WHEN Corpus_Lint processes that item, THE Corpus_Lint SHALL verify that the item's Normalized_Text contains none of the Trigger_Tokens compared using the same Normalized_Text transformation.
4. IF a Paraphrase_Family Corpus_Item's Normalized_Text contains one or more Trigger_Tokens, THEN THE Corpus_Lint SHALL report each occurrence as a disjointness violation that identifies the offending Corpus_Item and each matched Trigger_Token, and SHALL complete with a non-zero failure outcome.
5. THE Sampling_Methodology SHALL document how Paraphrase_Family disjointness was ensured and how the Trigger_Token list was derived from the shipped scanner patterns, including a record that two independent reviewers each confirmed the disjointness is genuine before Gate 0 sign-off.
6. WHERE a Corpus_Item's Family is the Paraphrase_Family, THE Detection_Corpus SHALL assign that item the Label `malicious`.

### Requirement 4: Developer-traffic family exercises the known false-positive shapes

**User Story:** As the engineer fixing the two over-matching patterns (G0.3/G0.4), I want a benign
family that deliberately triggers the two known false-positive shapes, so that I have a
false-positive suite to score my fix against.

#### Acceptance Criteria

1. THE Developer_Traffic_Family SHALL contain at least 1 Benign_Item for each of the following four content categories, for a minimum of 4 Benign_Items total: inline-backtick code, tool descriptions, SQL statements, and shell commands.
2. THE Developer_Traffic_Family SHALL contain at least 1 Benign_Item whose prompt text contains a backtick-delimited inline-code span that matches the command_injection FP_Prone_Shape `` `[^`]+` `` (one opening backtick, at least 1 non-backtick character, one closing backtick).
3. THE Developer_Traffic_Family SHALL contain at least 1 Benign_Item whose prompt text is a tool description that matches at least 1 of the four `.*`-wildcard `data_leakage` FP_Prone_Shapes.
4. WHERE a Corpus_Item's Family is the Developer_Traffic_Family, THE Detection_Corpus SHALL assign that item the Label `benign`.
5. IF a Benign_Item in the Developer_Traffic_Family is assigned a Label other than `benign`, THEN THE Detection_Corpus SHALL reject the item and produce an error indication identifying the item and its invalid Label, and SHALL retain the corpus contents unchanged.

### Requirement 5: Corpus lint enforces integrity and coverage

**User Story:** As a maintainer, I want a single command that mechanically verifies the corpus is
well-formed, deduplicated, leak-free, and meets coverage minimums, so that a broken corpus never
lands in the repository.

#### Acceptance Criteria

1. THE Corpus_Lint SHALL be runnable as a single documented command, either within the gateway test gate (`cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`) or as an equivalent documented standalone command.
2. WHEN the Corpus_Lint runs, THE Corpus_Lint SHALL validate every Corpus_Item against the schema and SHALL verify that no two Corpus_Items share an identical Normalized_Text value.
3. IF two or more Corpus_Items share an identical Normalized_Text value, THEN THE Corpus_Lint SHALL report a duplicate violation that identifies each of the conflicting items by their unique identifiers and SHALL retain all reported items unmodified.
4. WHEN the Corpus_Lint runs, THE Corpus_Lint SHALL verify that no `train` item and `eval` item exhibit Train_Eval_Leakage.
5. IF a `train` item and an `eval` item exhibit Train_Eval_Leakage, THEN THE Corpus_Lint SHALL report a leakage violation that identifies both items by their unique identifiers.
6. WHEN the Corpus_Lint runs, THE Corpus_Lint SHALL verify all of the following coverage minimums: at least 300 Malicious_Items, at least 300 Benign_Items, at least 8 distinct Attack_Family values, at least one item of Paraphrase_Family, and at least one item of Developer_Traffic_Family.
7. IF any coverage minimum defined in criterion 6 is not met, THEN THE Corpus_Lint SHALL report a coverage violation that names each unmet minimum and states the required threshold and the observed count.
8. IF any Corpus_Item fails schema validation, THEN THE Corpus_Lint SHALL report a schema violation that identifies the failing item by its unique identifier and the failed constraint.
9. WHEN the corpus satisfies schema validity, Normalized_Text uniqueness, absence of Train_Eval_Leakage, train/eval disjointness, and all coverage minimums defined in criterion 6, THE Corpus_Lint SHALL report success and SHALL terminate with a success exit status of 0.
10. IF at least one schema, uniqueness, leakage, disjointness, or coverage violation is present, THEN THE Corpus_Lint SHALL terminate with a non-success exit status that is a non-zero value between 1 and 255.

### Requirement 6: 10% label audit

**User Story:** As a reviewer, I want a documented re-check of a random 10% sample of the corpus's
labels, so that I have evidence the labels are correct before trusting any measurement built on them.

#### Acceptance Criteria

1. THE Detection_Corpus SHALL include a Label_Audit that re-checks a random sample of at least 10% (rounded up to the next whole item) of all Corpus_Items, with a minimum sample of 1 item when the corpus contains at least 1 Corpus_Item.
2. THE Label_Audit SHALL select its sample using a documented deterministic seed such that repeating the selection with the same seed and the same set of Corpus_Items produces an identical set of sampled items.
3. WHEN the Label_Audit selects its sample, THE Label_Audit SHALL record the seed value used and the total count of Corpus_Items from which the sample was drawn.
4. THE Label_Audit SHALL record, for each sampled item, a re-check result of exactly one of confirmed-correct or confirmed-incorrect indicating whether the reviewer confirms the assigned Label is correct.
5. IF any sampled item has no recorded re-check result, THEN THE Label_Audit SHALL be treated as incomplete and SHALL produce an indication identifying the count of sampled items missing a re-check result.
6. THE Sampling_Methodology SHALL document the sampling rate (at least 10%), the deterministic seed, the selection procedure used to draw the Label_Audit sample, and the procedure used to perform each per-item re-check.

### Requirement 7: Written sampling methodology

**User Story:** As a Gate 0 approver, I want a written methodology document, so that the corpus's
sourcing, disjointness guarantee, splits, and audit are reproducible and reviewable.

#### Acceptance Criteria

1. THE Detection_Corpus SHALL include exactly one Sampling_Methodology document committed under `tests/detection_corpus/`.
2. THE Sampling_Methodology SHALL describe, for each origin type, how Corpus_Items were sourced and authored, identifying for every item its origin type (externally sourced or internally authored) and, for externally sourced items, the origin reference.
3. THE Sampling_Methodology SHALL describe how Paraphrase_Family Disjoint_Trigger_Vocabulary was ensured, including how the Trigger_Token list was derived from the shipped scanner patterns.
4. THE Sampling_Methodology SHALL describe how Splits were assigned reproducibly, including the documented split seed value or the deterministic assignment rule sufficient for an independent reviewer to reproduce identical Split assignments for all Corpus_Items.
5. THE Sampling_Methodology SHALL describe how the Label_Audit was performed, including the number of Corpus_Items audited, the auditor role, and the pass or fail criteria applied to each audited label.
6. IF the Sampling_Methodology omits any of the sourcing description, Disjoint_Trigger_Vocabulary description, Split reproducibility description, or Label_Audit description, THEN THE Detection_Corpus SHALL be treated as failing the Gate 0 methodology review, with the missing section identified.

### Requirement 8: Reproducible splits

**User Story:** As a measurement engineer, I want the train/eval split to be reproducible from a
documented rule, so that anyone rebuilding or extending the corpus produces the same partitions.

#### Acceptance Criteria

1. THE Detection_Corpus SHALL assign each Corpus_Item to exactly one Split, where the allowed Split values are `train` and `eval`, using a deterministic rule or seed that is recorded in documentation accessible to any party rebuilding the corpus.
2. WHEN the split assignment is re-run with the same documented seed or rule on the same set of Corpus_Items, THE Detection_Corpus SHALL assign every Corpus_Item to the same Split value it was assigned on the prior run, with zero items differing.
3. IF a Corpus_Item cannot be assigned to a Split by the documented rule or seed, THEN THE Detection_Corpus SHALL reject the split operation and produce an error indication identifying the unassignable Corpus_Item, leaving all prior Split assignments unchanged.
4. THE Detection_Corpus SHALL assign at least 1 Corpus_Item to the `train` Split and at least 1 Corpus_Item to the `eval` Split.
5. IF the split assignment produces 0 Corpus_Items in the `train` Split or 0 Corpus_Items in the `eval` Split, THEN THE Detection_Corpus SHALL reject the split operation and produce an error indication stating which Split is empty.

### Requirement 9: Test-only, additive, no verdict change

**User Story:** As the plan owner, I want the corpus to have zero blast radius, so that adding it
cannot change any firewall verdict or affect any production path.

#### Acceptance Criteria

1. THE Detection_Corpus SHALL reside entirely under the repository path `tests/detection_corpus/`, such that every file introduced by the corpus has a path prefixed by `tests/detection_corpus/`.
2. IF any file introduced or modified by the corpus resolves to a path outside `tests/detection_corpus/`, THEN THE Corpus_Lint SHALL fail the check and report an error indicating the out-of-scope path.
3. THE Detection_Corpus SHALL introduce zero added, modified, or deleted lines in `gateway/ai_mesh_gateway/scanner.py` and in every other production code path outside `tests/detection_corpus/`.
4. THE Detection_Corpus SHALL produce zero change to any detection verdict emitted by the shipped scanner, such that for every input the scanner's verdict before and after adding the corpus is identical.
5. WHEN the gateway test gate (`cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`) is run immediately before adding the corpus and immediately after adding the corpus, THE gateway test gate SHALL produce an identical set of pass/fail outcomes for all pre-existing tests, with the only permitted difference being the addition of the Corpus_Lint's own passing checks.

### Requirement 10: Safe, fake fixtures

**User Story:** As a repository maintainer, I want every sensitive-looking value in the corpus to
be obviously fake and documented as such, so that committing the corpus leaks no real PII or secret.

#### Acceptance Criteria

1. WHERE a Corpus_Item's prompt text contains a value resembling PII (including but not limited to Social Security Number, credit card number, or email address) or a secret (including but not limited to API key or credential), THE Detection_Corpus SHALL use a Fake_Fixture drawn from a documented reserved-for-testing or example range rather than a real value.
2. THE Sampling_Methodology SHALL document, in a dedicated section, that all sensitive-looking values in the corpus are Fake_Fixtures and are safe to commit, and SHALL enumerate the categories of sensitive-looking values covered (PII, secrets, credentials).
3. THE Detection_Corpus SHALL contain zero real personally identifiable information and zero real secret or credential across all Corpus_Items.
4. IF a value resembling PII or a secret is detected in a Corpus_Item that is not a documented Fake_Fixture, THEN THE Detection_Corpus SHALL reject the Corpus_Item and produce an error indication identifying the offending Corpus_Item, and SHALL exclude the Corpus_Item from the committed corpus.
5. WHERE a Fake_Fixture is used in a Corpus_Item, THE Detection_Corpus SHALL associate the value with a documented marker indicating the value is fake, such that a reviewer can distinguish every Fake_Fixture from a real value without external lookup.
