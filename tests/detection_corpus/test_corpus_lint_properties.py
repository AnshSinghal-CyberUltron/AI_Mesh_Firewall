"""Property-based tests for the detection-corpus lint (task 3.4).

These tests exercise the pure helpers in ``corpus_lint`` with Hypothesis. The
module is imported by PATH (the corpus lives at repo-root ``tests/detection_corpus/``
and is loaded by the gateway wrapper via ``sys.path`` insertion, per the design's
Testing Strategy), so this file inserts its own directory on ``sys.path`` before
importing ``corpus_lint`` — keeping it self-contained and runnable both standalone
(``python -m pytest tests/detection_corpus/test_corpus_lint_properties.py``) and
under the gateway suite.
"""
from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# corpus_lint is imported by path (see module docstring): put this file's own
# directory (the corpus root) on sys.path, then import the stdlib-only validator.
_CORPUS_DIR = Path(__file__).resolve().parent
if str(_CORPUS_DIR) not in sys.path:
    sys.path.insert(0, str(_CORPUS_DIR))

import corpus_lint  # noqa: E402  (import must follow the sys.path insertion above)

# Minimum iterations per property (design Testing Strategy: >= 100).
ITERATIONS = 200

# The allowed split values, per corpus_lint.VALID_SPLITS (Requirement 8.1).
_VALID_SPLITS = frozenset(corpus_lint.VALID_SPLITS)


# ── Property 5 ────────────────────────────────────────────────────────────────
# Feature: detection-corpus, Property 5: Split assignment is a deterministic pure function
#
# For any item id, assign_split(id) returns the same value on every call (and on
# every process — the mapping is a pure SHA-256 hash of seed+id, no RNG state), and
# it returns only one of the allowed split values "train"/"eval".
@settings(max_examples=ITERATIONS, deadline=None)
@given(item_id=st.text())
def test_property5_assign_split_is_deterministic(item_id: str) -> None:
    first = corpus_lint.assign_split(item_id)

    # 1) Only ever the two allowed splits.
    assert first in _VALID_SPLITS, (
        f"assign_split({item_id!r}) returned {first!r}, "
        f"not one of {sorted(_VALID_SPLITS)}"
    )

    # 2) Stable across repeated calls with the same id (idempotent / pure).
    for _ in range(5):
        again = corpus_lint.assign_split(item_id)
        assert again == first, (
            f"assign_split({item_id!r}) is not deterministic: "
            f"first={first!r} later={again!r}"
        )


# ── Property 5 (default-argument invariance) ──────────────────────────────────
# Feature: detection-corpus, Property 5: Split assignment is a deterministic pure function
#
# Passing the documented committed SPLIT_SEED / default eval_fraction explicitly
# yields the same assignment as relying on the defaults — the function has no hidden
# state, so its result depends only on its inputs.
@settings(max_examples=ITERATIONS, deadline=None)
@given(item_id=st.text())
def test_property5_assign_split_defaults_match_explicit(item_id: str) -> None:
    implicit = corpus_lint.assign_split(item_id)
    explicit = corpus_lint.assign_split(
        item_id, eval_fraction=0.2, seed=corpus_lint.SPLIT_SEED
    )
    assert implicit == explicit, (
        f"assign_split({item_id!r}) differs between default and explicit args: "
        f"implicit={implicit!r} explicit={explicit!r}"
    )


# ── Property 5 (check_split_reproducible flags drift, and only drift) ──────────
# Feature: detection-corpus, Property 5: Split assignment is a deterministic pure function
#
# The second half of Property 5: check_split_reproducible flags EXACTLY those
# items whose stored split differs from assign_split(id). An item whose stored
# split is the correct deterministic assignment produces no violation; an item
# whose stored split is the opposite value produces exactly one "split" violation
# naming that item. This is the drift-detection guarantee (Requirement 8.2) that
# lets the lint verify a stored split rather than trust a hand-edited one.


def _opposite_split(split: str) -> str:
    """The other member of VALID_SPLITS ("train" <-> "eval")."""
    return "eval" if split == "train" else "train"


def _loaded_item(item_id: str, split: str, *, line: int = 1) -> corpus_lint.LoadedItem:
    """Build a usable LoadedItem carrying an id, non-empty text, and a split.

    check_split_reproducible only needs a usable id/text (per _usable_id_and_text)
    and a present, valid ``split``; the other schema fields are irrelevant to this
    rule (they are owned by check_schema), so the minimal object suffices.
    """
    return corpus_lint.LoadedItem(
        line=line,
        source="test.jsonl",
        obj={"id": item_id, "text": "x", "split": split},
    )


# Distinct, non-empty ids so each item is usable and independently identifiable.
_UNIQUE_IDS = st.lists(
    st.text(min_size=1, max_size=32).filter(lambda s: s.strip() != ""),
    min_size=0,
    max_size=12,
    unique=True,
)


@settings(max_examples=ITERATIONS, deadline=None)
@given(item_ids=_UNIQUE_IDS)
def test_property5_correct_splits_yield_no_violations(item_ids: list[str]) -> None:
    # Every item stores exactly the deterministic assignment for its id.
    items = [_loaded_item(i, corpus_lint.assign_split(i)) for i in item_ids]
    violations = corpus_lint.check_split_reproducible(items)
    assert violations == [], (
        "check_split_reproducible flagged items whose stored split already "
        f"matches assign_split(id): {[v.detail for v in violations]}"
    )


@settings(max_examples=ITERATIONS, deadline=None)
@given(item_ids=_UNIQUE_IDS)
def test_property5_drifted_splits_are_flagged_exactly(item_ids: list[str]) -> None:
    # Every item stores the OPPOSITE of its deterministic assignment: each is drift.
    items = [
        _loaded_item(i, _opposite_split(corpus_lint.assign_split(i)))
        for i in item_ids
    ]
    violations = corpus_lint.check_split_reproducible(items)

    # One "split" violation per drifted item, naming exactly the drifted ids.
    assert len(violations) == len(item_ids), (
        f"expected {len(item_ids)} split violations, got {len(violations)}"
    )
    assert all(v.rule == "split" for v in violations), (
        f"expected every violation rule to be 'split', got "
        f"{sorted({v.rule for v in violations})}"
    )
    assert {v.item_id for v in violations} == set(item_ids), (
        "flagged item ids do not match the drifted ids: "
        f"flagged={sorted({v.item_id for v in violations})} "
        f"expected={sorted(set(item_ids))}"
    )


@settings(max_examples=ITERATIONS, deadline=None)
@given(item_ids=_UNIQUE_IDS)
def test_property5_flags_drift_iff_stored_differs(item_ids: list[str]) -> None:
    # Mix correct and drifted stored splits deterministically (alternate by index)
    # and assert the flagged set equals exactly the drifted set — the "if and only
    # if" of Property 5's drift-detection half.
    items: list[corpus_lint.LoadedItem] = []
    expected_flagged: set[str] = set()
    for idx, item_id in enumerate(item_ids):
        correct = corpus_lint.assign_split(item_id)
        if idx % 2 == 0:
            stored = correct  # matches -> no violation
        else:
            stored = _opposite_split(correct)  # drift -> violation
            expected_flagged.add(item_id)
        items.append(_loaded_item(item_id, stored))

    violations = corpus_lint.check_split_reproducible(items)
    assert {v.item_id for v in violations} == expected_flagged, (
        "check_split_reproducible did not flag exactly the drifted items: "
        f"flagged={sorted({v.item_id for v in violations})} "
        f"expected={sorted(expected_flagged)}"
    )


# ── check_split_reproducible skips items other rules own (Requirement 1.8) ─────
# Feature: detection-corpus, Property 5: Split assignment is a deterministic pure function
#
# Items already owned by check_schema are skipped so this rule never double-reports
# or crashes: a line that did not parse (obj is None), an item with a missing/
# non-string id, and an item whose stored split is missing or not a VALID_SPLITS
# member each produce zero split violations here.
def test_check_split_reproducible_skips_schema_owned_items() -> None:
    skipped = [
        # unparsed line
        corpus_lint.LoadedItem(line=1, source="test.jsonl", obj=None),
        # missing id
        corpus_lint.LoadedItem(
            line=2, source="test.jsonl", obj={"text": "x", "split": "train"}
        ),
        # empty id
        corpus_lint.LoadedItem(
            line=3, source="test.jsonl", obj={"id": "", "text": "x", "split": "train"}
        ),
        # missing split (schema owns it)
        corpus_lint.LoadedItem(
            line=4, source="test.jsonl", obj={"id": "no-split", "text": "x"}
        ),
        # invalid split value (schema owns it)
        corpus_lint.LoadedItem(
            line=5,
            source="test.jsonl",
            obj={"id": "bad-split", "text": "x", "split": "holdout"},
        ),
    ]
    assert corpus_lint.check_split_reproducible(skipped) == []


# ── Task 4.8: plain example-based rule test for check_split_reproducible ───────
# Requirement 8.2. A concrete, deterministic companion to the Property 5 tests
# above: it states the task's guarantee directly on fixed example items rather
# than over Hypothesis-generated inputs — "mutate a stored split -> Violation;
# matching split -> none". Kept example-based so a regression is reported against
# a stable, human-readable fixture (not a shrunk counterexample).
def test_check_split_reproducible_rule_matching_and_mutated() -> None:
    # Two concrete ids with their deterministic assignments.
    id_a, id_b = "corpus-item-alpha", "corpus-item-beta"
    correct_a = corpus_lint.assign_split(id_a)
    correct_b = corpus_lint.assign_split(id_b)

    # Matching stored splits -> no violation.
    matching = [_loaded_item(id_a, correct_a), _loaded_item(id_b, correct_b)]
    assert corpus_lint.check_split_reproducible(matching) == [], (
        "items whose stored split matches assign_split(id) must not be flagged"
    )

    # Mutate item A's stored split to the opposite value -> exactly one violation
    # naming item A; item B (still correct) is not flagged.
    mutated = [
        _loaded_item(id_a, _opposite_split(correct_a)),
        _loaded_item(id_b, correct_b),
    ]
    violations = corpus_lint.check_split_reproducible(mutated)
    assert len(violations) == 1, (
        f"expected exactly one split violation for the mutated item, got {violations}"
    )
    (only,) = violations
    assert only.rule == "split"
    assert only.item_id == id_a
    # The detail records both the stored (mutated) and expected (correct) split.
    assert _opposite_split(correct_a) in only.detail
    assert correct_a in only.detail
