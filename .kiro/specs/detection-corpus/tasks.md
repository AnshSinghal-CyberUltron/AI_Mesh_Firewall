# Implementation Plan: Detection Corpus (G0.1)

## Overview

Test-only, additive artefact: a labelled detection corpus plus a pure `corpus_lint.py` validator
and its pytest wrapper. Everything lives under repo-root `tests/detection_corpus/` except one
pytest wrapper at `gateway/ai_mesh_gateway/tests/test_detection_corpus_lint.py`. No production
code changes and no scanner-verdict changes (R9).

Build order (dependency-driven): scaffolding → trigger tokens → lint core → individual lint rules
(each test-driven) → property tests → corpus data authoring → label audit → methodology doc →
pytest wrapper + gate. Test sub-tasks are marked optional with `*`. Language: Python (stdlib for
lint; Hypothesis for property tests).

## Tasks

- [x] 1. Scaffolding: package + family + split-seed foundations
  - [x] 1.1 Create the `tests/detection_corpus/` package and `families.json`
    - Create `tests/detection_corpus/__init__.py` (marks package importable by the wrapper)
    - Create `families.json` with `{"attack": [9 ATTACK_PATTERNS families + "paraphrase"], "benign": ["developer_traffic","general_benign"]}`
    - Define and commit the `SPLIT_SEED` constant location (in `corpus_lint.py`, documented for reuse)
    - _Requirements: 1.6, 1.7, 2.6, 9.1_

- [x] 2. Trigger tokens
  - [x] 2.1 Author `trigger_tokens.json` from the shipped scanner
    - Read-only import/inspection of `ATTACK_PATTERNS` + `FUZZY_ANCHOR_PHRASES` from `gateway/ai_mesh_gateway/scanner.py`
    - Enumerate literal tokens/phrases each pattern keys on, each entry `{token, source_pattern}` (e.g. `prompt_injection[0]`)
    - No mutation of scanner; derivation only
    - _Requirements: 3.1_

- [x] 3. Lint core: dataclasses + normalization + deterministic split
  - [x] 3.1 Create `corpus_lint.py` shell with data shapes and constants
    - `Violation` (frozen: `rule`, `item_id`, `detail`) and `LintReport` (frozen: `ok`, `violations`, `counts`) dataclasses
    - `VALID_LABELS`, `VALID_SPLITS`, `REQUIRED_FIELDS`, `SPLIT_SEED`; stdlib imports only (`json`, `re`, `unicodedata`, `hashlib`, `pathlib`, `dataclasses`)
    - _Requirements: 2.5, 2.7, 5.9_

  - [x] 3.2 Implement `normalize_text(text)`
    - lowercase → NFKC → collapse consecutive whitespace to single space → strip
    - _Requirements: 3.2_

  - [x] 3.3 Implement `assign_split(item_id, *, eval_fraction=0.2, seed=SPLIT_SEED)`
    - Pure: `int(sha256(seed+id).hexdigest()[:8],16)/2**32 < eval_fraction` → `eval`, else `train`
    - _Requirements: 8.1, 8.2_

  - [x] 3.4 Property test for `assign_split` determinism (Property 5)
    - **Feature: detection-corpus, Property 5: Split assignment is a deterministic pure function**
    - Hypothesis, ≥100 iterations: `assign_split(id)` stable across repeated calls
    - **Validates: Requirements 8.1, 8.2**

  - [x] 3.5 Unit tests for `normalize_text`
    - Case-fold, NFKC folding, whitespace collapse, strip edge cases
    - _Requirements: 3.2_

- [x] 4. Individual lint rules (each with its rule-level test)
  - [x] 4.1 Implement `check_schema(items)`
    - Valid-JSON-per-line, required fields present, types/lengths (id 1–128 unique, text 1–1048576 with ≥1 non-ws, provenance 1–512), label/split enums, family valid-for-label
    - Malformed line → one schema Violation with line number; excluded item not counted
    - _Requirements: 2.2, 2.3, 2.4, 2.8, 2.9, 1.8_

  - [x] 4.2 Rule + property test for `check_schema` (Property 1)
    - **Feature: detection-corpus, Property 1: Schema completeness is decidable per line**
    - Synthetic dirty fixtures: bad label, missing field, oversized field, malformed JSON line → exactly one schema Violation each; clean lines → none
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9**

  - [x] 4.3 Implement `check_dedup(items)` and `check_leakage(items)`
    - `check_dedup`: no two items share `normalize_text(text)` → duplicate Violation naming both ids
    - `check_leakage`: no `train` item shares normalized text with an `eval` item → leakage Violation naming both ids
    - _Requirements: 5.2, 5.3, 5.4, 5.5_

  - [x] 4.4 Property tests for dedup and leakage (Properties 2, 3)
    - **Feature: detection-corpus, Property 2: Normalized-text uniqueness is symmetric and complete**
    - **Feature: detection-corpus, Property 3: No train/eval leakage passes iff splits are clean**
    - Hypothesis ≥100 iters: duplicate Violation ⇔ shared normalized text; leakage Violation ⇔ train/eval collision
    - **Validates: Requirements 5.2, 5.3, 5.4, 5.5**

  - [x] 4.5 Implement `check_paraphrase_disjoint(items, tokens)`
    - Paraphrase-family items' `normalize_text` must contain none of the normalized Trigger_Tokens; each match → disjointness Violation naming item + token
    - _Requirements: 3.3, 3.4_

  - [x] 4.6 Property test for paraphrase disjointness (Property 4)
    - **Feature: detection-corpus, Property 4: Paraphrase disjointness is exactly trigger-token absence**
    - Hypothesis ≥100 iters: paraphrase item with/without injected token → disjointness Violation ⇔ token present
    - **Validates: Requirements 3.2, 3.3**

  - [x] 4.7 Implement `check_split_reproducible(items)`
    - Each item's stored `split` must equal `assign_split(id)`; drift → Violation
    - _Requirements: 8.2_

  - [x] 4.8 Rule test for `check_split_reproducible`
    - Mutate a stored split → Violation; matching split → none
    - _Requirements: 8.2_

  - [x] 4.9 Implement `check_coverage(items)`
    - ≥300 malicious, ≥300 benign, ≥8 distinct attack families, ≥1 paraphrase, ≥1 developer_traffic, ≥1 train & ≥1 eval; each unmet minimum → coverage Violation naming threshold vs observed
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 5.6, 5.7, 8.4, 8.5_

  - [x] 4.10 Rule test for `check_coverage`
    - Undersized synthetic corpus → coverage Violations naming each unmet minimum
    - _Requirements: 5.6, 5.7_

  - [x] 4.11 Implement `check_dev_traffic_shapes(items)`
    - developer_traffic family must include an inline-backtick `` `[^`]+` `` item AND a `data_leakage` `.*`-wildcard tool-description item; missing shape → Violation
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 4.12 Rule test for `check_dev_traffic_shapes`
    - Dev-traffic missing backtick or wildcard shape → shape/coverage Violation
    - _Requirements: 4.2, 4.3_

  - [x] 4.13 Implement `check_fixtures(items)`
    - Any PII/secret-shaped value must be listed in `fake_fixtures` (documented marker); unmarked real-shaped value → fixture Violation
    - _Requirements: 10.1, 10.4, 10.5_

  - [x] 4.14 Rule test for `check_fixtures`
    - Unmarked PII-shaped value → fixture Violation; marked fixture → none
    - _Requirements: 10.4_

  - [x] 4.15 Implement `check_trigger_token_coverage(tokens)` and `check_path_scope(root)`
    - `check_trigger_token_coverage`: guarded read-only import of `ATTACK_PATTERNS`; re-derive live literals; committed list missing a live literal → `trigger_token_drift` Violation; import failure → Violation, never crash
    - `check_path_scope`: every corpus file path resolves under `tests/detection_corpus/`; escape → `path_scope` Violation
    - _Requirements: 3.1, 9.1, 9.2_

  - [x] 4.16 Rule tests for trigger-token coverage and path scope
    - `trigger_tokens.json` missing a live literal → `trigger_token_drift` Violation; scanner-import-guard path returns Violation not crash; out-of-scope path → `path_scope` Violation
    - _Requirements: 3.1, 9.2_

- [x] 5. Lint aggregation + CLI
  - [x] 5.1 Implement `lint_corpus(root)` and `main(argv)`
    - `lint_corpus`: load `malicious.jsonl` + `benign.jsonl`, run every rule, aggregate `LintReport`; never raise on data problems; raise `FileNotFoundError` on missing root/required file
    - `main`: `python -m corpus_lint [root]` CLI; print all violations; return 0 (ok) / 1..255 (violations)
    - _Requirements: 5.1, 5.8, 5.9, 5.10_

  - [x] 5.2 Property test for exit-code contract (Property 6)
    - **Feature: detection-corpus, Property 6: The report exit-code contract is exact**
    - Hypothesis ≥100 iters: `ok == (violations == ())`; `main` returns 0 ⇔ ok, non-zero (1–255) otherwise
    - **Validates: Requirements 5.8, 5.9, 5.10**

  - [x] 5.3 Edge tests for loader error handling
    - Missing `benign.jsonl` → `FileNotFoundError` (not false-clean); malformed JSONL line → one schema Violation with line number
    - _Requirements: 2.9, 5.9_

- [x] 6. Checkpoint - lint tool complete and self-verified
  - Ensure all lint-rule and property tests pass against synthetic fixtures, ask the user if questions arise.

- [x] 7. Corpus data authoring
  - [x] 7.1 Author `malicious.jsonl` (malicious items + paraphrase family)
    - ≥300 malicious items across ≥8 of the 9 attack families
    - Paraphrase family with DISJOINT trigger vocabulary (label `malicious`); verify against `check_paraphrase_disjoint`
    - Each item: unique `id` (1–128), non-ws `text`, `label`, valid `family`, `split == assign_split(id)`, `provenance` (1–512), `fake_fixtures` (fakes marked, no real PII/secrets)
    - _Requirements: 1.1, 1.3, 1.4, 1.6, 2.2, 2.4, 2.6, 2.7, 2.8, 3.6, 8.2, 10.1, 10.3, 10.5_

  - [x] 7.2 Author `benign.jsonl` (benign items + developer_traffic family)
    - ≥300 benign items; developer_traffic family incl. an inline-backtick `` `[^`]+` `` item AND a `data_leakage` `.*`-wildcard tool-description item; plus SQL and shell-command categories
    - Each item: unique `id`, non-ws `text`, `label` `benign`, valid benign `family`, `split == assign_split(id)`, `provenance`, marked `fake_fixtures`
    - Ensure no train/eval leakage or normalized-text dupes across both files
    - _Requirements: 1.2, 1.5, 1.7, 2.2, 2.4, 2.6, 2.7, 2.8, 4.1, 4.2, 4.3, 4.4, 8.4, 10.1, 10.3, 10.5_

- [x] 8. Label audit
  - [x] 8.1 Author `label_audit.json`
    - Deterministic-seed sample of ≥10% of all items (ceil, ≥1); record `seed` and `total`
    - Per-item `result` of `confirmed-correct` | `confirmed-incorrect`; every sampled item has a recorded result
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 8.2 Unit test for audit-sample reproducibility
    - Same seed + same items → identical sampled id set
    - _Requirements: 6.2_

- [x] 9. Methodology document
  - [x] 9.1 Author `SAMPLING_METHODOLOGY.md`
    - Sourcing/authoring per origin type; paraphrase disjointness + trigger-token derivation incl. two-independent-reviewer disjointness sign-off record; split seed/rule; label-audit procedure (rate, seed, per-item re-check)
    - Dedicated section: all sensitive-looking values are Fake_Fixtures safe to commit, enumerating covered categories (PII, secrets, credentials)
    - _Requirements: 3.5, 6.6, 7.1, 7.2, 7.3, 7.4, 7.5, 10.2_

- [x] 10. Pytest wrapper + gate
  - [x] 10.1 Create `gateway/ai_mesh_gateway/tests/test_detection_corpus_lint.py`
    - Resolve root via `Path(__file__).resolve().parents[2]/"tests"/"detection_corpus"`, `sys.path`-insert, import `corpus_lint`
    - `assert lint_corpus(root).ok` with violations joined into the assert message
    - _Requirements: 5.1, 9.5_

  - [x] 10.2 Add rule-level dirty-fixture tests to the wrapper
    - Tiny synthetic dirty fixtures proving each rule fires (bad label, dup, train/eval leak, paraphrase-carrying-token, missing dev-traffic shape, unmarked PII)
    - _Requirements: 2.9, 4.3, 5.3, 5.5, 10.4_

- [x] 11. Final checkpoint - full gate green
  - Run `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q` (zero failures/errors) and `python -m corpus_lint tests/detection_corpus` (returns 0). Ask the user if questions arise.
  - _Requirements: 5.1, 9.5_

## Notes

- Tasks marked with `*` are optional (test sub-tasks) and can be skipped for a faster MVP.
- Each task references specific requirement sub-clauses for traceability.
- Property tests (`*`) map one-to-one to design Properties 1–6, tagged per the design Testing Strategy.
- The corpus-data authoring (task 7) is large and split into malicious-authoring (7.1) and
  benign-authoring (7.2) sub-tasks; both are validated by the lint built in tasks 3–5.
- Change set stays narrow and additive: stage only `tests/detection_corpus/**` and the one gateway
  wrapper test; never `git add -A` (R9 no-blast-radius).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["3.1"] },
    { "id": 2, "tasks": ["3.2", "3.3"] },
    { "id": 3, "tasks": ["3.4", "3.5", "4.1", "4.3", "4.5", "4.7", "4.9", "4.11", "4.13", "4.15"] },
    { "id": 4, "tasks": ["4.2", "4.4", "4.6", "4.8", "4.10", "4.12", "4.14", "4.16", "5.1"] },
    { "id": 5, "tasks": ["5.2", "5.3", "7.1"] },
    { "id": 6, "tasks": ["7.2"] },
    { "id": 7, "tasks": ["8.1", "9.1"] },
    { "id": 8, "tasks": ["8.2", "10.1"] },
    { "id": 9, "tasks": ["10.2"] }
  ]
}
```
