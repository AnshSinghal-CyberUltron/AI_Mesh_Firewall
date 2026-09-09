# Requirements Document

> **STATUS: PARKED / SUPERSEDED (by decision, see `../policy-driven-detection/`).**
> This narrow G0.3 fix (narrowing the one `command_injection` backtick pattern) is superseded by
> the **policy-driven-detection** effort, which removes ALL built-in default Tier-1 detection so
> that the `command_injection` family (and every other) only runs when a user enables its policy
> package. Under that model the false positive disappears wholesale (no policy = no
> command_injection block), so this single-pattern narrowing is no longer needed. Retained for
> history; do NOT implement. The narrowed backtick pattern may be folded into the seeded built-in
> `command_injection` policy package as its default rule.

## Introduction

This feature fixes the single over-matching Tier-1 scanner pattern that hard-blocks benign
inline-code prompts as command injection. It is task **G0.3** of the evidence-based hot-path
plan (`docs/plans/2026-08-27-task-tracker.md`, Gate 0, row G0.3).

The shipped scanner (`gateway/ai_mesh_gateway/scanner.py`) defines
`ATTACK_PATTERNS["command_injection"]` as five regexes, one of which is `` `[^`]+` `` — a bare
backtick pair around any one-or-more non-backtick characters. Because the scanner returns
`action="block", confidence=1.0, tier="tier_1"` on the FIRST matching `ATTACK_PATTERNS` category
and `command_injection` is not in the explanatory-mention suppression set, this pattern
hard-blocks any prompt containing an ordinary inline-code span. Measured on the shipped scanner
over the G0.1 detection corpus: **5/5 benign inline-code prompts are hard-blocked at confidence
1.00, terminal, never reaching Tier-2** (`.kiro/specs/detection-corpus/requirements.md`,
Introduction; the corpus `developer_traffic` family includes items such as
`Can you explain what the \`ls -la\` command prints in each column?` and
`What does \`git status\` show when the working tree is clean?`).

This is a **deliberate detection-verdict change**: it must be scored against the G0.1 corpus and
signed off. It is scoped to the single `` `[^`]+` `` pattern (the task's "revert single pattern"),
is measured by the G0.2 posture-scoring harness against G0.1's `developer_traffic` benign family
(the false-positive suite), and is tracked separately from G0.4 (the `data_leakage` wildcard fix).

This spec covers **only** the narrowing of that one pattern, the tests that prove the benign
false positives stop and the malicious command-injection detections do not regress, the
before/after scored evidence, and the sign-off artefact. It changes **no other scanner pattern**,
no other attack family, no confidence or blocking machinery, no corpus, and no scoring harness.

## Glossary

- **Scanner**: The shipped Tier-1 detection component `gateway/ai_mesh_gateway/scanner.py` and its
  `InputScanner` class, whose synchronous pattern scan matches prompt text against `ATTACK_PATTERNS`.
- **ATTACK_PATTERNS**: The module-level `dict[str, list[str]]` in the Scanner mapping each attack
  category name to a list of regular-expression strings.
- **Command_Injection_Patterns**: The list `ATTACK_PATTERNS["command_injection"]`, which on the
  shipped Scanner is exactly the five regexes `;\s*rm\s+-rf`, `&&\s*curl`, `\|\s*bash`,
  `` `[^`]+` ``, and `\$\([^\)]+\)`.
- **Backtick_Pattern**: The single Command_Injection_Patterns regex `` `[^`]+` `` — one opening
  backtick, one or more non-backtick characters, one closing backtick — that this feature changes.
  It is the only pattern this feature is permitted to modify.
- **Sibling_Command_Injection_Patterns**: The other four Command_Injection_Patterns entries
  (`;\s*rm\s+-rf`, `&&\s*curl`, `\|\s*bash`, `\$\([^\)]+\)`), which this feature must leave
  byte-for-byte unchanged.
- **Command_Injection_Verdict**: A `ScanVerdict` the Scanner returns with `threat_type ==
  "command_injection"` (on the shipped Scanner: `action="block"`, `confidence=1.0`, `tier="tier_1"`),
  produced when at least one Command_Injection_Patterns regex matches the scanned text.
- **Terminal_Block**: A Command_Injection_Verdict with `action="block"` that the Scanner returns
  immediately from its pattern loop, short-circuiting all later categories and any Tier-2
  (semantic) scan, so no later stage can revise it.
- **Benign_Inline_Code_Prompt**: A prompt whose only backtick content is a benign inline-code span
  — a backtick-delimited fragment naming a command, identifier, path, option, or code token with
  no command-injection intent (for example `` `ls -la` ``, `` `git status` ``,
  `` `process.env.NODE_ENV` ``). It carries the corpus Label `benign`.
- **Malicious_Backtick_Command**: A prompt whose backtick content is a genuine shell command
  substitution used to execute a command (for example a backtick span containing `rm`, a pipe to a
  shell, `curl` to a remote host, or a nested `$(...)`/backtick substitution). It carries the
  corpus Label `malicious` in the `command_injection` Attack_Family.
- **Detection_Corpus**: The committed labelled corpus produced by G0.1, rooted at
  `tests/detection_corpus/` (see `.kiro/specs/detection-corpus/`), used as the ground-truth
  measurement set for this feature.
- **Developer_Traffic_Family**: The benign corpus Family `developer_traffic` (G0.1, Requirement 4),
  whose inline-backtick items are the false-positive suite for this feature.
- **Inline_Code_FP**: A Command_Injection_Verdict emitted for a Benign_Inline_Code_Prompt — a
  false positive this feature must eliminate.
- **Command_Injection_Recall**: Over a labelled set, the fraction of Malicious_Backtick_Command
  items (and other `command_injection`-family malicious items) the Scanner blocks as
  `command_injection`.
- **Posture_Scorer**: The G0.2 posture-scoring harness (`.kiro/specs/posture-scoring/`,
  `scripts/detection/`) that computes per-family recall and false-positive rate over the
  Detection_Corpus, used to score this change before and after.
- **Before_State**: The Scanner exactly as shipped immediately before this change (the
  Backtick_Pattern is `` `[^`]+` ``).
- **After_State**: The Scanner immediately after this change (the Backtick_Pattern narrowed per
  Requirement 2).
- **Scored_Evidence**: A committed artefact recording the Posture_Scorer results in the
  Before_State and the After_State plus the rationale that the verdict change is intentional.
- **Gateway_Test_Gate**: The command `cd gateway && ./.venv/bin/python -m pytest
  ai_mesh_gateway/tests -q`, the project's pre-existing pass/fail gate.

## Requirements

### Requirement 1: Single-pattern blast radius

**User Story:** As the plan owner, I want this fix to change exactly one regex and nothing else,
so that the behaviour change is auditable, reviewable, and trivially revertible.

#### Acceptance Criteria

1. THE feature SHALL modify only the Backtick_Pattern entry within `ATTACK_PATTERNS["command_injection"]` in `gateway/ai_mesh_gateway/scanner.py`.
2. THE feature SHALL leave every Sibling_Command_Injection_Patterns entry (`;\s*rm\s+-rf`, `&&\s*curl`, `\|\s*bash`, `\$\([^\)]+\)`) byte-for-byte unchanged.
3. THE feature SHALL leave every other `ATTACK_PATTERNS` category and its patterns (prompt_injection, jailbreak, data_leakage, goal_hijacking, tool_overreach, sql_injection, path_traversal, vector_injection) byte-for-byte unchanged.
4. THE feature SHALL make zero change to the Scanner's confidence assignment, blocking machinery, category-ordering, explanatory-mention suppression set, Tier-2 escalation logic, or any `ScanVerdict` construction other than which text the Backtick_Pattern matches.
5. THE feature SHALL make zero change to the Detection_Corpus (`tests/detection_corpus/`), to the Posture_Scorer (`scripts/detection/`), and to every production and test file except the single Backtick_Pattern edit and the new test and evidence artefacts this feature introduces.
6. IF the change to `gateway/ai_mesh_gateway/scanner.py` adds, modifies, or deletes any line other than the single Backtick_Pattern regex string, THEN the change SHALL be rejected as exceeding the permitted blast radius.
7. THE feature SHALL be revertible to the Before_State by restoring the single Backtick_Pattern regex string to `` `[^`]+` ``, with no other edit required to restore the prior behaviour.

### Requirement 2: Benign inline-code no longer matches command_injection

**User Story:** As a developer using the firewall, I want ordinary inline-code prompts to pass
Tier-1 without being hard-blocked as command injection, so that legitimate developer traffic is
not terminally blocked at confidence 1.00.

#### Acceptance Criteria

1. THE narrowed Backtick_Pattern SHALL NOT match a Benign_Inline_Code_Prompt whose only backtick content is a benign inline-code span, such that the Scanner does not emit a Command_Injection_Verdict for that prompt on account of the Backtick_Pattern.
2. WHEN the Scanner scans each Benign_Inline_Code_Prompt in the Developer_Traffic_Family that the Before_State hard-blocks as command_injection, THE After_State Scanner SHALL NOT return a Command_Injection_Verdict for that prompt.
3. WHEN the Scanner scans a Benign_Inline_Code_Prompt containing a single benign inline-code span (for example `` `ls -la` ``, `` `git status` ``, `` `process.env.NODE_ENV` ``, `` `useEffect` ``, `` `npm ci` ``), THE After_State Scanner SHALL NOT block that prompt as command_injection.
4. WHERE a Benign_Inline_Code_Prompt would, in the After_State, still be blocked by a DIFFERENT `ATTACK_PATTERNS` category (not command_injection) or by a non-Tier-1 stage, THE feature SHALL treat that as outside this feature's scope and SHALL NOT be required to change that outcome, provided the block is not attributable to the Backtick_Pattern.
5. THE narrowing SHALL be expressed solely as the Backtick_Pattern regex string; the feature SHALL NOT introduce any new code branch, category, or post-match filter to achieve the benign pass.

### Requirement 3: Genuine backtick command injection still detected

**User Story:** As a security engineer, I want genuine backtick-delimited command substitution to
still be caught, so that narrowing the pattern to stop benign inline code does not open a
command-injection detection gap.

#### Acceptance Criteria

1. THE narrowed Backtick_Pattern SHALL match a Malicious_Backtick_Command whose backtick content is a genuine shell command substitution intended to execute a command.
2. WHEN the Scanner scans a prompt containing a backtick command substitution that executes a dangerous command (for example a backtick span containing `rm -rf`, a pipe into a shell, `curl` to a remote host, or a nested command substitution), THE After_State Scanner SHALL return a Command_Injection_Verdict for that prompt.
3. WHERE a `command_injection`-family Malicious_Item in the Detection_Corpus is blocked as command_injection by the Before_State Scanner, THE After_State Scanner SHALL also block that item as command_injection (no dropped detection on the malicious command_injection family attributable to the Backtick_Pattern narrowing).
4. IF the narrowed Backtick_Pattern would fail to match a Malicious_Backtick_Command that the Before_State Backtick_Pattern matched, THEN the feature SHALL either preserve that detection through the narrowed Backtick_Pattern or SHALL confirm the same item is still blocked as command_injection by a Sibling_Command_Injection_Patterns entry, and SHALL NOT rely on Tier-2 to recover a Before_State Tier-1 detection.

### Requirement 4: Zero recall regression across attack families

**User Story:** As a Gate 0 approver, I want proof that the fix is surgical, so that reducing the
benign false-positive rate costs zero detection on any attack family.

#### Acceptance Criteria

1. WHEN the Posture_Scorer is run over the Detection_Corpus in the Before_State and again in the After_State, THE Command_Injection_Recall on the `command_injection` Attack_Family SHALL be greater than or equal to its Before_State value, with zero malicious `command_injection` item transitioning from blocked to not-blocked.
2. WHEN the Posture_Scorer is run over the Detection_Corpus in the Before_State and again in the After_State, THE per-family recall for every OTHER Attack_Family (prompt_injection, jailbreak, data_leakage, goal_hijacking, tool_overreach, sql_injection, path_traversal, vector_injection) SHALL be identical between the two states, with zero malicious item of those families transitioning from blocked to not-blocked.
3. IF any malicious Corpus_Item of any Attack_Family transitions from blocked in the Before_State to not-blocked in the After_State, THEN the feature SHALL be treated as a recall regression and SHALL be rejected until the regression is eliminated.
4. THE feature SHALL determine every recall figure in criteria 1 through 3 from the Posture_Scorer output computed over the committed Detection_Corpus, not from an ad-hoc or hand-selected input set.

### Requirement 5: Developer-traffic false-positive rate falls

**User Story:** As the engineer measuring the fix, I want the benign developer-traffic
false-positive rate to demonstrably decrease, so that the change delivers its stated benefit.

#### Acceptance Criteria

1. WHEN the Posture_Scorer is run over the Detection_Corpus in the After_State, THE count of Inline_Code_FP (Benign_Inline_Code_Prompts in the Developer_Traffic_Family blocked as command_injection) SHALL be zero.
2. WHEN the Posture_Scorer is run over the Detection_Corpus in the Before_State and again in the After_State, THE Developer_Traffic_Family false-positive rate attributable to command_injection SHALL be strictly lower in the After_State than in the Before_State.
3. WHERE a Developer_Traffic_Family Benign_Item is blocked in both the Before_State and the After_State by a category OTHER than command_injection, THE feature SHALL NOT be required to change that outcome and SHALL exclude that item from the command_injection false-positive count in criteria 1 and 2.
4. THE feature SHALL determine the false-positive figures in criteria 1 through 3 from the Posture_Scorer output over the committed Detection_Corpus.

### Requirement 6: Scored, signed-off evidence of an intentional verdict change

**User Story:** As a reviewer, I want a committed before/after scored artefact with an explicit
rationale, so that the verdict change is recorded as deliberate and reproducible rather than
silent.

#### Acceptance Criteria

1. THE feature SHALL produce a Scored_Evidence artefact recording the Posture_Scorer results in the Before_State and the After_State over the Detection_Corpus.
2. THE Scored_Evidence SHALL record, at minimum: the Developer_Traffic_Family command_injection false-positive count and rate in both states (showing the decrease per Requirement 5), the `command_injection` Attack_Family recall in both states (showing no regression per Requirement 4.1), and confirmation that all other Attack_Family recalls are unchanged (per Requirement 4.2).
3. THE Scored_Evidence SHALL record the Before_State and After_State Backtick_Pattern regex strings and a written rationale stating that the change is a deliberate, signed-off detection-verdict change (fewer benign inline-code hard-blocks) rather than an accidental regression.
4. THE Scored_Evidence SHALL record the exact reproducible command used to produce the Posture_Scorer figures and the corpus version the figures were computed over, sufficient for an independent reviewer to reproduce the before/after comparison.
5. THE Scored_Evidence SHALL reside under a documented, permitted artefact path (for example `docs/perf/` or the plan's evidence findings directory) and SHALL NOT be written into `gateway/ai_mesh_gateway/scanner.py`, the Detection_Corpus, or the Posture_Scorer sources.
6. IF the Scored_Evidence omits the before/after false-positive figures, the before/after command_injection recall, the two Backtick_Pattern strings, the intentional-change rationale, or the reproducible command, THEN the feature SHALL be treated as failing the sign-off requirement, with the missing element identified.

### Requirement 7: Verification gate and targeted tests

**User Story:** As a maintainer, I want the fix guarded by the project test gate plus targeted
false-positive and regression tests, so that a future edit that reintroduces the false positive or
drops a detection is caught automatically.

#### Acceptance Criteria

1. WHEN the Gateway_Test_Gate is run immediately before this change and immediately after this change, THE Gateway_Test_Gate SHALL produce an identical set of pass/fail outcomes for all pre-existing tests, with the only permitted difference being the addition of this feature's new passing tests.
2. THE feature SHALL add at least one test asserting that each Benign_Inline_Code_Prompt shape covered by Requirement 2.3 does NOT produce a Command_Injection_Verdict in the After_State Scanner.
3. THE feature SHALL add at least one test asserting that a Malicious_Backtick_Command shape covered by Requirement 3.2 DOES produce a Command_Injection_Verdict in the After_State Scanner.
4. THE feature SHALL add at least one test that scores the After_State Scanner over the Developer_Traffic_Family inline-code items and asserts zero command_injection false positives (Requirement 5.1) and at least one test asserting the `command_injection` Attack_Family recall does not regress (Requirement 4.1).
5. THE feature's new tests SHALL run within, or as a documented equivalent of, the Gateway_Test_Gate, and SHALL pass in the After_State.
6. IF any new test asserting the benign-pass (Requirement 2) or the malicious-detection (Requirement 3) fails, THEN the feature SHALL be treated as not meeting its acceptance criteria until the failing assertion passes without weakening any other requirement.

### Requirement 8: No new leak, log, or performance regression from the narrowing

**User Story:** As a security reviewer, I want narrowing the pattern to introduce no new
information-leak, logging, or catastrophic-backtracking risk, so that the fix does not trade a
false-positive problem for a different vulnerability.

#### Acceptance Criteria

1. THE narrowed Backtick_Pattern SHALL preserve the Scanner's existing evidence-masking behaviour, such that any matched span the Scanner records in `matched_patterns` continues to pass through the existing `redact_all` masking with no new raw-content exposure.
2. THE narrowed Backtick_Pattern SHALL be a linear-time-safe regular expression that does not introduce catastrophic backtracking on adversarial input (no nested unbounded quantifiers over overlapping character classes), such that scanning a pathological backtick-heavy input completes within the Scanner's existing per-scan time envelope.
3. THE feature SHALL NOT add, remove, or alter any Scanner log statement, and SHALL NOT cause any prompt content to be logged that the Before_State did not already log.
4. IF the narrowed Backtick_Pattern would match a substantially broader span of text than the Before_State pattern for the same input (risking new PII/secret capture into `matched_patterns`), THEN the feature SHALL constrain the match span so the After_State captures no more raw prompt content than the Before_State for equivalent inputs.
