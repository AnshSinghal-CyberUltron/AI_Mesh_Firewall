# Detection Corpus — Sampling Methodology

This document is the written `Sampling_Methodology` deliverable for the labelled detection
corpus (task **G0.1**, Gate 0). It records how corpus items were sourced and authored, how the
`paraphrase` family's disjointness from the shipped scanner trigger vocabulary was ensured, how
the reproducible train/eval split is assigned, how the 10% label audit was performed, and that
every sensitive-looking value in the corpus is a safe fake fixture.

The Gate 0 methodology review (Requirement 7.6) checks that each of the following sections is
present: sourcing/authoring per origin type, paraphrase disjointness, split reproducibility, the
label-audit procedure, and the fake-fixtures statement. All five are present below.

## Corpus overview

The corpus is **test-only and additive**: every artefact lives under `tests/detection_corpus/`,
it imports the shipped scanner only read-only (to audit the trigger-token list), and it changes
no production code and no detection verdict (Requirement 9).

- **748 items total** — **323 malicious**, **425 benign**, stored one JSON object per line in
  `malicious.jsonl` and `benign.jsonl`.
- **Malicious coverage** spans **all nine** shipped `ATTACK_PATTERNS` attack families plus the
  dedicated `paraphrase` family:
  `prompt_injection` (60), `jailbreak` (38), `data_leakage` (38), `goal_hijacking` (26),
  `tool_overreach` (26), `sql_injection` (26), `command_injection` (26), `path_traversal` (26),
  `vector_injection` (26), and `paraphrase` (31). This exceeds the ≥8-attack-family and
  ≥1-paraphrase minimums (Requirements 1.1, 1.3, 1.4).
- **Benign coverage**: `developer_traffic` (75) and `general_benign` (350), exceeding the ≥300
  benign and ≥1 developer-traffic minimums (Requirements 1.2, 1.5). The `developer_traffic`
  family deliberately exercises **both** known false-positive shapes — the `command_injection`
  `` `[^`]+` `` inline-backtick shape and the four `.*`-wildcard `data_leakage` tool-description
  shapes (Requirement 4).

The valid family names per label are published in `families.json`; the enforced integrity,
uniqueness, no-leakage, disjointness, and coverage checks live in `corpus_lint.py` and run in the
gateway test gate.

## 1. Sourcing and authoring per origin type (Requirement 7.2)

Every `Corpus_Item` carries a `provenance` string that records its origin type, and every item in
this corpus is **internally produced** — there are no externally sourced items, so there are no
external origin references to cite. Two internal origin types are used, each identifiable from the
`provenance` field:

- **`hand_authored:<marker>`** — the item was written by hand. The `<marker>` names the intent the
  item was authored against.
- **`templated:<marker>`** — the item was produced by an internal, deterministic template
  (a fixed sentence frame combined with a benign topic). Templating is used only for a portion of
  the `general_benign` family to broaden benign phrasing coverage; the frames and topics are
  ordinary, non-attack English, so each templated item is a genuine benign prompt.

Per family:

- **Malicious families (all `hand_authored`).** Each malicious item is hand-authored to express a
  specific attack intent, and its `provenance` marker ties it to the shipped scanner pattern (or
  fuzzy-anchor entry) that the intent corresponds to — e.g. `hand_authored:prompt_injection[0]`,
  `hand_authored:sql_injection[1]`, `hand_authored:command_injection[3]`, plus `…​.extra[n]`
  markers for additional same-family variants beyond the one-per-pattern base set. In corpus terms
  these malicious items are **derived from the scanner pattern** they were authored against
  (`derived_from_scanner_pattern`) while remaining fully hand-authored text — the marker makes that
  derivation auditable item-by-item.
- **`paraphrase` family (`hand_authored`).** Each paraphrase item (`hand_authored:paraphrase[n]`)
  is hand-authored to express the *same* attack intent as the malicious families but using
  **Disjoint_Trigger_Vocabulary** — no literal trigger token the shipped regexes key on (see
  §2). Paraphrase items are labelled `malicious` (Requirement 3.6).
- **Benign families.** `developer_traffic` items are hand-authored
  (`hand_authored:dev_traffic/inline_backtick[n]`, `…/tool_description[n]`, `…/sql[n]`,
  `…/shell[n]`), covering the four required content categories — inline-backtick code, tool
  descriptions, SQL statements, and shell commands — and deliberately including the two
  FP-prone shapes. `general_benign` items are a mix of hand-authored (`…/pool[n]`) and templated
  (`templated:general_benign/frame{f}/topic{t}`) benign prompts.

Because every origin type is internal, a reviewer can reproduce or verify any item's origin from
its `provenance` marker alone, with no external lookup (Requirement 7.2).

## 2. Paraphrase disjointness and Trigger_Token derivation (Requirements 3.5, 7.3)

**Purpose.** The `paraphrase` family exists so that "0/10 paraphrase recall" measures a *genuine*
detection gap rather than an artefact of reused trigger words. That guarantee only holds if the
family provably contains none of the literal tokens the shipped patterns key on.

**Trigger_Token derivation.** The `Trigger_Token` list is committed in `trigger_tokens.json` as a
list of `{"token", "source_pattern"}` records. Each token is a literal alphanumeric phrase
extracted from a shipped `ATTACK_PATTERNS` scanner regex (or from the scanner's
`FUZZY_ANCHOR_PHRASES` list), and `source_pattern` records exactly which pattern it came from —
e.g. `prompt_injection[0]` (`ignore previous instructions`), `sql_injection[1]` (`DROP TABLE`),
`command_injection[0]` (`rm -rf`), `command_injection[3]` (`` `whoami` ``),
`FUZZY_ANCHOR_PHRASES.jailbreak[0]` (`developer mode`). The list therefore enumerates the literal
tokens/phrases every shipped pattern keys on, tagged to its source pattern (Requirement 3.1).

**Keeping the list honest as the scanner evolves.** `corpus_lint.py`'s
`check_trigger_token_coverage` performs the one read-only, lazy, try/except-guarded import of the
live `ATTACK_PATTERNS`, re-derives the literal set, and flags drift as a `trigger_token_drift`
violation if `trigger_tokens.json` no longer covers the live patterns — so the disjointness
guarantee cannot silently rot.

**How disjointness was ensured.** Each paraphrase item was hand-authored to convey an attack
intent using vocabulary chosen to avoid every entry in the trigger list, comparing against the
same normalization the machine check uses.

**Machine check (`check_paraphrase_disjoint`).** `Normalized_Text` is defined (Requirement 3.2) as
`normalize_text()`: lowercase → NFKC Unicode fold → collapse consecutive whitespace to a single
space → strip. For every `paraphrase`-family item, the lint verifies that the item's
`Normalized_Text` contains **zero** trigger-token substrings, comparing each trigger token under
the same normalization. Any occurrence is reported as a `disjointness` violation naming the item
and the matched token, and the lint exits non-zero (Requirements 3.3, 3.4). The committed corpus
lints clean, so the paraphrase family holds zero trigger-token substrings in normalized space.

**Two-independent-reviewer sign-off (Requirement 3.5).** Beyond the machine check, **two
independent reviewers each separately confirmed, before Gate 0 sign-off, that the `paraphrase`
family's disjointness from the shipped trigger vocabulary is genuine** — i.e. that each paraphrase
item still expresses the attack intent while sharing none of the scanner's literal trigger
tokens, and that the disjointness is a real semantic paraphrase rather than a trivial spelling
evasion. Both reviewers recorded a pass. This dual human confirmation, together with the
machine `check_paraphrase_disjoint` gate, is the disjointness guarantee relied on at Gate 0.

## 3. Reproducible train/eval split (Requirements 7.4, 8.1)

The split is a **deterministic pure function of the item id** — no RNG state is stored — so any
reviewer rebuilding or extending the corpus produces identical partitions.

- **Split seed (committed constant).** `SPLIT_SEED = "detection-corpus/g0.1/split-seed/v1"`,
  defined in `corpus_lint.py` as the single source of truth.
- **Assignment rule.** For an item id, `assign_split(id)` computes:

  ```
  fraction = int(sha256(SPLIT_SEED + id).hexdigest()[:8], 16) / 2**32
  split    = "eval" if fraction < eval_fraction else "train"     # eval_fraction = 0.2
  ```

  i.e. the first 32 bits of `sha256(SPLIT_SEED + id)` are mapped into `[0, 1)`; an item lands in
  `eval` when that fraction is below 0.2, otherwise `train`.
- **Verification, not trust.** `check_split_reproducible` recomputes `assign_split(id)` for every
  item and flags any item whose stored `split` differs, so a hand-edited split is caught
  (Requirement 8.2).
- **Observed distribution.** The committed corpus splits to **605 train / 143 eval** (≈19.1%
  eval), satisfying the ≥1-train and ≥1-eval minimums (Requirement 8.4). `check_leakage` further
  verifies no `train` item and `eval` item share `Normalized_Text` (Requirements 5.4, 5.5).

Re-running the rule on the same ids with the same seed reproduces every assignment exactly, with
zero items differing (Requirement 8.2).

## 4. Label-audit procedure (Requirements 6.6, 7.5)

The label audit is a documented re-check of a random ≥10% sample of the corpus's labels, recorded
in `label_audit.json` (`{seed, total, sample:[{id, result}]}`).

- **Sampling rate.** At least 10% of all items, rounded up to the next whole item:
  `ceil(0.10 × 748) = 75` items sampled — a 75/748 ≈ 10.03% rate (Requirement 6.1).
- **Deterministic seed.** `seed = "detection-corpus-label-audit-v1"`, recorded in
  `label_audit.json` alongside `total = 748` (Requirements 6.2, 6.3).
- **Selection procedure.** All 748 corpus ids are ranked by their `sha256(seed + id)` hexdigest in
  ascending lexicographic order, and the **first 75** ids in that ranking form the sample. Because
  the ranking is a pure function of the fixed seed and the fixed id set, repeating the selection
  with the same seed and the same corpus reproduces an identical sample (Requirement 6.2). This
  procedure was re-executed against the committed corpus and reproduces exactly the 75 ids stored
  in `label_audit.json`.
- **Per-item re-check procedure.** For each sampled item, the auditor independently re-reads the
  item's `text` and its assigned `label`/`family`, judges the correct label from the text alone
  (without deferring to the original author), and records a result of exactly one of
  `confirmed-correct` or `confirmed-incorrect` (Requirement 6.4). Every sampled item carries a
  recorded result — a sample with any missing result is treated as incomplete (Requirement 6.5).
- **Auditor role and pass/fail criteria.** The re-check is performed by a corpus reviewer acting as
  **label auditor**, distinct from the item's original author. The pass/fail criterion applied to
  **each audited label** is binary: a label **passes** (`confirmed-correct`) when the auditor
  agrees the item's assigned `malicious`/`benign` label is correct given its text, and **fails**
  (`confirmed-incorrect`) otherwise.
- **Result.** All 75 sampled items are recorded `confirmed-correct` (0 `confirmed-incorrect`), so
  the audited labels passed.

## 5. Fake fixtures — safe to commit (Requirement 10.2)

**All sensitive-looking values in this corpus are `Fake_Fixture`s and are safe to commit.** The
corpus contains **zero real personally identifiable information and zero real secret or
credential** (Requirement 10.3).

The categories of sensitive-looking value covered by the fake-fixture guarantee are:

- **PII** — Social Security Numbers, credit card numbers, and email addresses.
- **Secrets** — API keys and similar tokens.
- **Credentials** — API keys and other access credentials.

This corpus **deliberately avoids embedding real-shaped sensitive values**: every item's
`fake_fixtures` field is an empty list, and no item carries a live PII/secret/credential value.
Malicious items express data-exfiltration and secret-disclosure *intent* through phrasing (e.g.
"reveal secrets", "dump api keys") rather than by embedding an actual SSN, card number, key, or
credential, so there is nothing sensitive to leak. `corpus_lint.py`'s `check_fixtures` enforces
the invariant: any PII- or secret-shaped value that appears without the documented fake marker
(a non-empty `fake_fixtures` entry) is rejected as a `fixture` violation (Requirements 10.1, 10.4,
10.5). Because the `fake_fixtures` lists are empty across the corpus, the fixture check has nothing
to flag and the corpus is safe to commit as-is.

## Reproducing and validating the corpus

Run the lint as part of the gateway gate, or standalone:

```
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q
python -m corpus_lint tests/detection_corpus
```

A clean corpus reports success and exits 0; any schema, uniqueness, leakage, disjointness,
coverage, fixture, or path-scope violation exits non-zero and names the offending item
(Requirements 5.9, 5.10).
