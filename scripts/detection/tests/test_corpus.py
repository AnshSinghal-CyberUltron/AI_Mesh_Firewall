"""Property + unit tests for the read-only corpus loader (``corpus.py``).

Covers Property 5 (label source and partition invariant) for the posture
scoring harness (task 4.2).
"""

from __future__ import annotations

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# The detection modules import as top-level names with ``scripts/detection`` on
# the path (they are a self-contained additive package, matching the sibling
# test modules). Make that import work whether pytest is invoked from the repo
# root, ``gateway/``, or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from corpus import (  # noqa: E402
    BENIGN_LABEL,
    MALICIOUS_LABEL,
    Corpus_Item,
    partition,
)

# --- Hypothesis strategies -------------------------------------------------

# Label values drawn from a space that deliberately mixes the two recognized
# labels with unrecognized / absent-equivalent values so the generated corpus
# exercises the malicious / benign / excluded partition (Requirements 2.3, 2.7).
_LABEL_VALUES = st.sampled_from(
    [
        MALICIOUS_LABEL,          # exactly "malicious"
        BENIGN_LABEL,             # exactly "benign"
        "",                       # absent-equivalent (from_obj maps missing -> "")
        "Malicious",              # wrong case -> unrecognized
        "BENIGN",                 # wrong case -> unrecognized
        "malicious ",             # trailing space -> not exactly "malicious"
        " benign",                # leading space -> not exactly "benign"
        "unknown",                # arbitrary unrecognized
        "attack",                 # arbitrary unrecognized
        "safe",                   # arbitrary unrecognized
    ]
)

# A "posture score" attached alongside each generated item. The partition must
# be invariant to it (no label is ever derived from scanner output).
_SCORE_VALUES = st.floats(min_value=0.0, max_value=1.0)


@st.composite
def _corpus_item(draw) -> Corpus_Item:
    """Generate a Corpus_Item with a varied label and varied other fields.

    The non-label fields (``text``, ``family``, ``split``) are the fields a
    scanner would consult; varying them independently of the label lets the test
    confirm classification never depends on them.
    """
    label = draw(_LABEL_VALUES)
    return Corpus_Item(
        id=draw(st.text(max_size=16)),
        text=draw(st.text(max_size=64)),
        label=label,
        family=draw(st.text(max_size=16)),
        split=draw(st.sampled_from(["train", "eval", "other", ""])),
        provenance=draw(st.text(max_size=16)),
    )


@st.composite
def _scored_corpus(draw):
    """Generate a list of (item, posture_score) pairs.

    The score stands in for any scanner output; it is generated independently of
    the label so the test can prove the partition ignores it entirely.
    """
    n = draw(st.integers(min_value=0, max_value=40))
    pairs = []
    for _ in range(n):
        item = draw(_corpus_item())
        score = draw(_SCORE_VALUES)
        pairs.append((item, score))
    return pairs


# --- Property 5 ------------------------------------------------------------


@settings(max_examples=200)
@given(pairs=_scored_corpus())
def test_partition_label_source_and_invariant(pairs):
    """Feature: posture-scoring, Property 5.

    For any generated corpus, an item is classified malicious or benign solely
    from its ``label`` field; items whose label is absent or is not exactly
    ``malicious`` or ``benign`` are excluded from both the malicious and benign
    counts and added to the excluded-label count, and the recorded
    malicious + benign + excluded-label counts sum to the total item count. The
    classification is invariant to every posture score (no label is ever derived
    from scanner output).

    Validates: Requirements 2.3, 2.6, 2.7, 7.4
    """
    items = [item for item, _score in pairs]
    total = len(items)

    result = partition(items)

    # Classification is solely from the `label` field.
    for item in result.malicious:
        assert item.label == MALICIOUS_LABEL
    for item in result.benign:
        assert item.label == BENIGN_LABEL
    # Everything else (absent / unrecognized label) is excluded.
    for item in result.excluded:
        assert item.label != MALICIOUS_LABEL
        assert item.label != BENIGN_LABEL

    # Every input item lands in exactly one bucket (partition, no loss/dup).
    assert (
        result.malicious_count + result.benign_count + result.excluded_count == total
    )
    assert result.total == total

    # An item's bucket is decided purely by its label: no field a scanner would
    # read (text / family / split) can move it. Recompute the expected buckets
    # from the label alone and require an exact match.
    expected_mal = [i for i in items if i.label == MALICIOUS_LABEL]
    expected_ben = [i for i in items if i.label == BENIGN_LABEL]
    expected_exc = [
        i for i in items if i.label not in (MALICIOUS_LABEL, BENIGN_LABEL)
    ]
    assert list(result.malicious) == expected_mal
    assert list(result.benign) == expected_ben
    assert list(result.excluded) == expected_exc

    # Invariance to any posture score: partition consumes only the items, never
    # the accompanying scores, so re-partitioning the identical items (regardless
    # of the scores generated alongside them) yields byte-identical buckets.
    rerun = partition(items)
    assert list(rerun.malicious) == list(result.malicious)
    assert list(rerun.benign) == list(result.benign)
    assert list(rerun.excluded) == list(result.excluded)


@settings(max_examples=100)
@given(
    label=_LABEL_VALUES,
    score_a=_SCORE_VALUES,
    score_b=_SCORE_VALUES,
    text_a=st.text(max_size=64),
    text_b=st.text(max_size=64),
)
def test_partition_ignores_score_and_scanner_fields(
    label, score_a, score_b, text_a, text_b
):
    """Two items with the same label but different text / attached scores always
    classify into the same bucket — proving no label is derived from scanner
    output (Requirement 7.4).
    """
    item_a = Corpus_Item(
        id="a", text=text_a, label=label, family="fam-a", split="train",
        provenance="p",
    )
    item_b = Corpus_Item(
        id="b", text=text_b, label=label, family="fam-b", split="eval",
        provenance="p",
    )

    part_a = partition([item_a])
    part_b = partition([item_b])

    # Same label -> same bucket membership counts, independent of score/text.
    assert part_a.malicious_count == part_b.malicious_count
    assert part_a.benign_count == part_b.benign_count
    assert part_a.excluded_count == part_b.excluded_count

    # The attached posture scores are never consulted (documented, not read).
    _ = (score_a, score_b)


# --- Unit tests: concrete partition examples -------------------------------


def test_partition_mixed_example():
    """A hand-built mixed corpus partitions exactly by label."""
    items = [
        Corpus_Item("m1", "t", MALICIOUS_LABEL, "prompt_injection", "eval", "p"),
        Corpus_Item("b1", "t", BENIGN_LABEL, "developer_traffic", "eval", "p"),
        Corpus_Item("x1", "t", "", "none", "eval", "p"),          # absent label
        Corpus_Item("x2", "t", "Malicious", "none", "eval", "p"),  # wrong case
        Corpus_Item("m2", "t", MALICIOUS_LABEL, "paraphrase", "train", "p"),
    ]

    result = partition(items)

    assert result.malicious_count == 2
    assert result.benign_count == 1
    assert result.excluded_count == 2
    assert result.total == len(items)


def test_partition_empty_is_all_zero():
    """An empty corpus partitions into three empty buckets summing to zero."""
    result = partition([])
    assert result.malicious_count == 0
    assert result.benign_count == 0
    assert result.excluded_count == 0
    assert result.total == 0


# --- Unit tests: loader edge cases (task 4.3) ------------------------------
#
# These plain example tests exercise the loader's edge cases directly, using
# small in-memory Corpus / Corpus_Item fixtures and tiny tmp_path JSONL files.
# They never read from or write to the committed tests/detection_corpus/.

import json
from pathlib import Path

from corpus import (  # noqa: E402
    EVAL_SPLIT,
    TRAIN_SPLIT,
    Corpus,
    load_corpus,
    scored_set,
    split_of,
)


def _mk_item(
    item_id: str,
    label: str,
    *,
    split: str = "eval",
    family: str = "none",
    text: str = "t",
) -> Corpus_Item:
    """Build an in-memory Corpus_Item for a loader edge-case fixture."""
    return Corpus_Item(
        id=item_id,
        text=text,
        label=label,
        family=family,
        split=split,
        provenance="test",
    )


def _mk_corpus(items) -> Corpus:
    """Wrap items in a Corpus with empty family declarations for selection tests."""
    return Corpus(
        items=tuple(items),
        attack_families=(),
        benign_families=(),
        root=Path("."),
    )


def test_partition_empty_corpus_zero_items():
    """An empty corpus (0 items) partitions into three empty buckets.

    Validates: Requirements 1.6, 2.7
    """
    result = partition([])

    assert result.malicious == ()
    assert result.benign == ()
    assert result.excluded == ()
    assert result.total == 0


def test_scored_set_empty_corpus_returns_no_items():
    """Selecting a Scored_Set from a 0-item corpus yields an empty set.

    Validates: Requirements 1.6, 2.7
    """
    empty = _mk_corpus([])

    eval_set = scored_set(empty, "eval")
    full_set = scored_set(empty, "full")

    assert eval_set.which == "eval"
    assert eval_set.items == ()
    assert full_set.which == "full"
    assert full_set.items == ()


def test_partition_unrecognized_label_excluded():
    """An item with an unrecognized label is excluded, not counted mal/benign.

    Validates: Requirements 2.3, 2.7
    """
    good_mal = _mk_item("m1", MALICIOUS_LABEL)
    good_ben = _mk_item("b1", BENIGN_LABEL)
    unknown = _mk_item("u1", "phishing")          # arbitrary unrecognized value
    wrong_case = _mk_item("u2", "Malicious")      # case-sensitive: unrecognized
    absent = _mk_item("u3", "")                   # absent-equivalent label

    result = partition([good_mal, good_ben, unknown, wrong_case, absent])

    assert list(result.malicious) == [good_mal]
    assert list(result.benign) == [good_ben]
    # The three unrecognized-label items are excluded from both counts.
    assert list(result.excluded) == [unknown, wrong_case, absent]
    assert result.excluded_count == 3
    assert result.malicious_count == 1
    assert result.benign_count == 1
    assert result.total == 5


def test_split_of_bad_split_value_returns_other():
    """A split value other than train/eval maps to 'other'.

    Validates: Requirement 7.7
    """
    assert split_of(_mk_item("t1", MALICIOUS_LABEL, split=TRAIN_SPLIT)) == TRAIN_SPLIT
    assert split_of(_mk_item("e1", MALICIOUS_LABEL, split=EVAL_SPLIT)) == EVAL_SPLIT
    # Anything else -> "other".
    assert split_of(_mk_item("o1", MALICIOUS_LABEL, split="validation")) == "other"
    assert split_of(_mk_item("o2", MALICIOUS_LABEL, split="TRAIN")) == "other"
    assert split_of(_mk_item("o3", MALICIOUS_LABEL, split="")) == "other"
    assert split_of(_mk_item("o4", MALICIOUS_LABEL, split="test")) == "other"


def test_scored_set_eval_selects_eval_subset_vs_full():
    """scored_set('eval') returns only eval items; 'full' returns all items.

    Validates: Requirements 6.7, 7.7
    """
    eval_a = _mk_item("e1", MALICIOUS_LABEL, split=EVAL_SPLIT)
    eval_b = _mk_item("e2", BENIGN_LABEL, split=EVAL_SPLIT)
    train_a = _mk_item("t1", MALICIOUS_LABEL, split=TRAIN_SPLIT)
    other_a = _mk_item("o1", BENIGN_LABEL, split="validation")  # bad split value
    corpus = _mk_corpus([eval_a, train_a, eval_b, other_a])

    eval_set = scored_set(corpus, "eval")
    full_set = scored_set(corpus, "full")

    # eval scored set = only the eval-split items, in corpus order.
    assert list(eval_set.items) == [eval_a, eval_b]
    # full scored set = every item including train and bad-split items.
    assert list(full_set.items) == [eval_a, train_a, eval_b, other_a]
    # The eval subset is a strict subset of the full set here.
    assert set(eval_set.items) < set(full_set.items)


def test_scored_set_rejects_unknown_selector():
    """An unknown Scored_Set selector is rejected with a ValueError."""
    corpus = _mk_corpus([_mk_item("e1", MALICIOUS_LABEL)])
    try:
        scored_set(corpus, "train")  # type: ignore[arg-type]
    except ValueError as exc:
        assert "train" in str(exc)
    else:  # pragma: no cover - selection must reject anything but eval/full
        raise AssertionError("scored_set should reject an unknown selector")


def test_load_corpus_reads_jsonl_and_families(tmp_path):
    """load_corpus reads malicious/benign JSONL + families.json read-only.

    Uses a tiny tmp_path corpus; never touches tests/detection_corpus/.
    Validates: Requirements 2.3, 7.7
    """
    root = tmp_path / "detection_corpus"
    root.mkdir()
    (root / "malicious.jsonl").write_text(
        json.dumps(
            {"id": "m1", "text": "attack", "label": "malicious",
             "family": "prompt_injection", "split": "eval", "provenance": "p"}
        )
        + "\n"
        # blank line is skipped by the reader
        + "\n"
        + json.dumps(
            {"id": "m2", "text": "attack2", "label": "malicious",
             "family": "paraphrase", "split": "train", "provenance": "p"}
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "benign.jsonl").write_text(
        json.dumps(
            {"id": "b1", "text": "hello", "label": "benign",
             "family": "developer_traffic", "split": "eval", "provenance": "p"}
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "families.json").write_text(
        json.dumps(
            {"attack": ["prompt_injection", "paraphrase"],
             "benign": ["developer_traffic"]}
        ),
        encoding="utf-8",
    )

    corpus = load_corpus(root)

    # malicious items load first, then benign; blank line ignored -> 3 items.
    assert len(corpus.items) == 3
    assert [i.id for i in corpus.items] == ["m1", "m2", "b1"]
    assert corpus.attack_families == ("prompt_injection", "paraphrase")
    assert corpus.benign_families == ("developer_traffic",)

    # Partition + selection round-trip on the freshly loaded corpus.
    part = partition(corpus.items)
    assert part.malicious_count == 2
    assert part.benign_count == 1
    assert part.excluded_count == 0

    eval_items = scored_set(corpus, "eval").items
    assert [i.id for i in eval_items] == ["m1", "b1"]


def test_load_corpus_empty_files_yield_zero_items(tmp_path):
    """An empty corpus on disk (0 JSONL lines) loads as 0 items.

    Validates: Requirement 1.6
    """
    root = tmp_path / "detection_corpus"
    root.mkdir()
    (root / "malicious.jsonl").write_text("", encoding="utf-8")
    (root / "benign.jsonl").write_text("\n\n", encoding="utf-8")  # only blanks
    (root / "families.json").write_text(
        json.dumps({"attack": [], "benign": []}), encoding="utf-8"
    )

    corpus = load_corpus(root)

    assert corpus.items == ()
    assert scored_set(corpus, "eval").items == ()
    assert scored_set(corpus, "full").items == ()
    assert partition(corpus.items).total == 0
