"""Unit tests for ``corpus_lint.check_coverage`` (task 4.9).

``check_coverage`` verifies the corpus coverage minimums (Requirements 1.1-1.5,
5.6/5.7, 8.4/8.5): >=300 malicious, >=300 benign, >=8 distinct attack families,
>=1 paraphrase, >=1 developer_traffic, and >=1 item in each of the train/eval
splits. Each unmet minimum must yield exactly one coverage ``Violation`` naming
the required threshold vs the observed count; only schema-valid items are counted
(Requirement 1.8) so a malformed item cannot mask a shortfall.

Imported by PATH (the corpus lives at repo-root ``tests/detection_corpus/`` and
is loaded via ``sys.path`` insertion), mirroring
``test_corpus_lint_properties.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# corpus_lint is imported by path: put this file's own directory (the corpus
# root) on sys.path, then import the stdlib-only validator.
_CORPUS_DIR = Path(__file__).resolve().parent
if str(_CORPUS_DIR) not in sys.path:
    sys.path.insert(0, str(_CORPUS_DIR))

import corpus_lint  # noqa: E402  (import must follow the sys.path insertion above)

LoadedItem = corpus_lint.LoadedItem

# The nine attack families the 8-distinct minimum is counted over.
_ATTACK_FAMILIES = sorted(corpus_lint.ATTACK_PATTERN_FAMILIES)


# --- test item builders --------------------------------------------------------


def _item(
    idx: int,
    *,
    label: str,
    family: str,
    split: str,
    source: str = "malicious.jsonl",
) -> LoadedItem:
    """Build one schema-valid LoadedItem carrying the fields coverage reads."""
    return LoadedItem(
        line=idx + 1,
        source=source,
        obj={
            "id": f"{label}-{family}-{idx:05d}",
            "text": f"prompt text {label} {family} {idx}",
            "label": label,
            "family": family,
            "split": split,
            "provenance": "hand_authored",
        },
    )


def _malicious_block(
    count: int,
    *,
    families: list[str],
    split: str = "train",
    start: int = 0,
) -> list[LoadedItem]:
    """Build ``count`` malicious items spread across ``families``."""
    return [
        _item(
            start + i,
            label="malicious",
            family=families[i % len(families)],
            split=split,
        )
        for i in range(count)
    ]


def _benign_block(
    count: int,
    *,
    family: str = "general_benign",
    split: str = "train",
    start: int = 0,
) -> list[LoadedItem]:
    return [
        _item(
            start + i,
            label="benign",
            family=family,
            split=split,
            source="benign.jsonl",
        )
        for i in range(count)
    ]


def _clean_corpus() -> list[LoadedItem]:
    """A corpus that satisfies EVERY coverage minimum.

    300 malicious across all 9 attack families + 1 paraphrase malicious item,
    300 benign of which 1 is developer_traffic, and at least one item in each of
    train and eval.
    """
    items: list[LoadedItem] = []
    # 300 malicious, cycling all 9 attack families (>= 8 distinct), all train.
    items += _malicious_block(300, families=_ATTACK_FAMILIES, split="train")
    # 1 paraphrase malicious item (own dedicated family), placed in eval so both
    # splits are populated (Requirements 8.4/8.5).
    items.append(
        _item(1000, label="malicious", family="paraphrase", split="eval")
    )
    # 300 benign, all general_benign except one developer_traffic.
    items += _benign_block(299, family="general_benign", split="train", start=0)
    items.append(
        _item(
            2000,
            label="benign",
            family="developer_traffic",
            split="train",
            source="benign.jsonl",
        )
    )
    return items


def _coverage_details(items: list[LoadedItem]) -> list[str]:
    violations = corpus_lint.check_coverage(items)
    # Every violation this rule emits is corpus-level (item_id == "").
    assert all(v.rule == "coverage" for v in violations)
    assert all(v.item_id == "" for v in violations)
    return [v.detail for v in violations]


# --- the happy path ------------------------------------------------------------


def test_clean_corpus_has_no_coverage_violations() -> None:
    assert corpus_lint.check_coverage(_clean_corpus()) == []


# --- each unmet minimum fires exactly one named violation ----------------------


def test_too_few_malicious_fires_named_violation() -> None:
    # 298 malicious block (spans all 9 families) + 1 paraphrase malicious in eval
    # = 299 malicious total, one short of 300. Every OTHER minimum is met, so the
    # ONLY coverage violation is the malicious-count shortfall.
    items = _malicious_block(298, families=_ATTACK_FAMILIES, split="train")
    items.append(
        _item(1000, label="malicious", family="paraphrase", split="eval")
    )
    items += _benign_block(299, family="general_benign", split="train")
    items.append(
        _item(2000, label="benign", family="developer_traffic", split="train")
    )
    details = _coverage_details(items)
    assert any(
        "malicious items: required >= 300, observed 299" in d for d in details
    ), details


def test_too_few_benign_fires_named_violation() -> None:
    # 298 general_benign + 1 developer_traffic = 299 benign, one short of 300,
    # with developer_traffic still present so ONLY the benign-count minimum fails.
    items = _malicious_block(300, families=_ATTACK_FAMILIES, split="train")
    items.append(
        _item(1000, label="malicious", family="paraphrase", split="eval")
    )
    items += _benign_block(298, family="general_benign", split="train")
    items.append(
        _item(2000, label="benign", family="developer_traffic", split="train")
    )
    details = _coverage_details(items)
    assert any(
        "benign items: required >= 300, observed 299" in d for d in details
    ), details


def test_too_few_attack_families_fires_named_violation() -> None:
    # 300 malicious spanning only 7 distinct attack families -> below the 8 min.
    seven = _ATTACK_FAMILIES[:7]
    items = _malicious_block(300, families=seven, split="train")
    items.append(
        _item(1000, label="malicious", family="paraphrase", split="eval")
    )
    items += _benign_block(299, split="train")
    items.append(
        _item(2000, label="benign", family="developer_traffic", split="train")
    )
    details = _coverage_details(items)
    assert any(
        "distinct attack families: required >= 8, observed 7" in d
        for d in details
    ), details


def test_missing_paraphrase_fires_named_violation() -> None:
    items = [i for i in _clean_corpus() if i.obj["family"] != "paraphrase"]
    details = _coverage_details(items)
    assert any(
        "paraphrase-family items: required >= 1, observed 0" in d
        for d in details
    ), details


def test_missing_developer_traffic_fires_named_violation() -> None:
    items = [
        i for i in _clean_corpus() if i.obj["family"] != "developer_traffic"
    ]
    details = _coverage_details(items)
    assert any(
        "developer_traffic-family items: required >= 1, observed 0" in d
        for d in details
    ), details


def test_empty_train_split_fires_named_violation() -> None:
    # Everything in eval -> 0 train items (Requirement 8.4/8.5).
    items: list[LoadedItem] = []
    items += _malicious_block(300, families=_ATTACK_FAMILIES, split="eval")
    items.append(
        _item(1000, label="malicious", family="paraphrase", split="eval")
    )
    items += _benign_block(299, split="eval")
    items.append(
        _item(2000, label="benign", family="developer_traffic", split="eval")
    )
    details = _coverage_details(items)
    assert any(
        "train-split items: required >= 1, observed 0" in d for d in details
    ), details


def test_empty_eval_split_fires_named_violation() -> None:
    # Everything in train -> 0 eval items.
    items: list[LoadedItem] = []
    items += _malicious_block(300, families=_ATTACK_FAMILIES, split="train")
    items.append(
        _item(1000, label="malicious", family="paraphrase", split="train")
    )
    items += _benign_block(299, split="train")
    items.append(
        _item(2000, label="benign", family="developer_traffic", split="train")
    )
    details = _coverage_details(items)
    assert any(
        "eval-split items: required >= 1, observed 0" in d for d in details
    ), details


# --- Requirement 1.8: malformed items are excluded from coverage counts --------


def test_malformed_items_excluded_from_counts() -> None:
    # A clean corpus plus junk lines (unparseable + missing id) must not have the
    # junk counted toward any minimum; the clean corpus already meets them, so
    # zero coverage violations even with the junk present.
    items = _clean_corpus()
    items.append(LoadedItem(line=999, source="malicious.jsonl", obj=None,
                            parse_error="Expecting value"))
    items.append(
        LoadedItem(
            line=998,
            source="malicious.jsonl",
            obj={"label": "malicious", "family": "jailbreak"},  # no id
        )
    )
    assert corpus_lint.check_coverage(items) == []


def test_malformed_malicious_does_not_mask_shortfall() -> None:
    # 299 valid malicious (spanning all attack families) + 1 malformed (no id)
    # that would be the 300th "malicious" line if wrongly counted. The malformed
    # item is excluded (Requirement 1.8), so the malicious count stays 299 and
    # the shortfall STILL fires with observed 299 (not 300).
    items = _malicious_block(299, families=_ATTACK_FAMILIES, split="train")
    items.append(
        LoadedItem(
            line=5000,
            source="malicious.jsonl",
            obj={"label": "malicious", "family": "jailbreak", "split": "train"},
        )
    )
    items += _benign_block(300, split="train")
    details = _coverage_details(items)
    # No paraphrase item was added, so paraphrase minimum is unmet AND the
    # malicious count is exactly 299 (the malformed line excluded). Assert both.
    assert any(
        "malicious items: required >= 300, observed 299" in d for d in details
    ), details
    assert any(
        "paraphrase-family items: required >= 1, observed 0" in d
        for d in details
    ), details


# --- multiple simultaneous shortfalls each get their own violation -------------


def test_multiple_unmet_minimums_each_reported() -> None:
    # Empty corpus: every minimum is unmet at once.
    details = _coverage_details([])
    assert any("malicious items: required >= 300, observed 0" in d
               for d in details)
    assert any("benign items: required >= 300, observed 0" in d
               for d in details)
    assert any("distinct attack families: required >= 8, observed 0" in d
               for d in details)
    assert any("paraphrase-family items: required >= 1, observed 0" in d
               for d in details)
    assert any("developer_traffic-family items: required >= 1, observed 0" in d
               for d in details)
    assert any("train-split items: required >= 1, observed 0" in d
               for d in details)
    assert any("eval-split items: required >= 1, observed 0" in d
               for d in details)


# --- task 4.10: an undersized (non-empty) corpus names EACH unmet minimum ------


def test_undersized_corpus_names_each_unmet_minimum() -> None:
    """R5.6/5.7: a genuinely undersized synthetic corpus (not fully empty)
    fires one coverage Violation for EVERY minimum it fails, each naming the
    required threshold vs the observed count.

    This corpus is populated but small: 10 malicious spanning only 3 attack
    families, 5 benign, no paraphrase, no developer_traffic, everything in
    train (so eval is empty). Six of the seven minimums are unmet at once; the
    train-split minimum is the only one satisfied. Each unmet minimum must be
    named exactly once with its observed count.
    """
    three = _ATTACK_FAMILIES[:3]
    items = _malicious_block(10, families=three, split="train")
    items += _benign_block(5, family="general_benign", split="train")

    details = _coverage_details(items)

    # Every unmet minimum is named with required >= threshold + observed count.
    assert any(
        "malicious items: required >= 300, observed 10" in d for d in details
    ), details
    assert any(
        "benign items: required >= 300, observed 5" in d for d in details
    ), details
    assert any(
        "distinct attack families: required >= 8, observed 3" in d
        for d in details
    ), details
    assert any(
        "paraphrase-family items: required >= 1, observed 0" in d
        for d in details
    ), details
    assert any(
        "developer_traffic-family items: required >= 1, observed 0" in d
        for d in details
    ), details
    assert any(
        "eval-split items: required >= 1, observed 0" in d for d in details
    ), details

    # The one satisfied minimum (train-split has 15 items) must NOT be named.
    assert not any("train-split items" in d for d in details), details

    # Exactly one violation per unmet minimum (six here), no duplicates.
    assert len(details) == 6, details
