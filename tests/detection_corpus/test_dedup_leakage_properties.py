"""Property-based tests for ``check_dedup`` and ``check_leakage`` (task 4.4).

These tests exercise the two pure rule functions with Hypothesis, proving the
design's Properties 2 and 3 over synthetic ``LoadedItem`` corpora:

* Property 2 — a duplicate ``Violation`` is emitted for two items **iff** they
  share ``normalize_text(text)`` (symmetric: every id in a colliding group is
  named; complete: all-distinct normalized text yields none).
* Property 3 — a leakage ``Violation`` is emitted **iff** some ``train`` item and
  some ``eval`` item share ``normalize_text(text)``; otherwise none.

The module is imported by PATH (the corpus lives at repo-root
``tests/detection_corpus/`` and is loaded by the gateway wrapper via ``sys.path``
insertion, per the design's Testing Strategy), so this file inserts its own
directory on ``sys.path`` before importing ``corpus_lint`` — mirroring
``test_corpus_lint_properties.py`` and ``test_check_coverage.py`` so it runs both
standalone and under the gateway suite.
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


# --- test item builder ---------------------------------------------------------


def _item(
    item_id: str, text: str, *, split: str = "train", line: int = 1
) -> corpus_lint.LoadedItem:
    """Build a usable LoadedItem carrying an id, text, and split.

    ``check_dedup`` needs only a usable id/text (per ``_usable_id_and_text``);
    ``check_leakage`` additionally reads ``split``. The remaining schema fields
    are owned by ``check_schema`` and irrelevant here, so the minimal object
    suffices.
    """
    return corpus_lint.LoadedItem(
        line=line,
        source="test.jsonl",
        obj={"id": item_id, "text": text, "split": split},
    )


# Distinct, non-empty ids so each synthetic item is usable and independently
# identifiable (an empty/whitespace-only id is skipped by both rules).
_UNIQUE_IDS = st.lists(
    st.text(min_size=1, max_size=24).filter(lambda s: s.strip() != ""),
    min_size=0,
    max_size=10,
    unique=True,
)

# Text values that normalize to something distinct per index (a plain integer
# string is invariant under normalize_text), so a freshly-built corpus has, by
# construction, no accidental normalized-text collisions until we force one.
def _distinct_text(idx: int) -> str:
    return f"prompt number {idx}"


# ── Property 2 ────────────────────────────────────────────────────────────────
# Feature: detection-corpus, Property 2: Normalized-text uniqueness is symmetric and complete
#
# For any corpus of all-distinct normalized text, check_dedup yields zero
# duplicate violations (completeness: it does not invent collisions).
@settings(max_examples=ITERATIONS, deadline=None)
@given(item_ids=_UNIQUE_IDS)
def test_property2_all_distinct_text_yields_no_duplicates(
    item_ids: list[str],
) -> None:
    items = [_item(i, _distinct_text(idx)) for idx, i in enumerate(item_ids)]
    violations = corpus_lint.check_dedup(items)
    assert violations == [], (
        "check_dedup flagged duplicates in a corpus whose normalized texts are "
        f"all distinct: {[v.detail for v in violations]}"
    )


# ── Property 2 ────────────────────────────────────────────────────────────────
# Feature: detection-corpus, Property 2: Normalized-text uniqueness is symmetric and complete
#
# For any corpus, IFF two items share normalize_text(text) is a duplicate
# Violation emitted naming BOTH ids. We build a base corpus of distinct texts,
# then force a random subset to all share one normalized text: the flagged set is
# EXACTLY that shared subset, every violation carries rule="duplicate", and each
# violation's detail names the other colliding ids (symmetry).
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    item_ids=st.lists(
        st.text(min_size=1, max_size=24).filter(lambda s: s.strip() != ""),
        min_size=2,
        max_size=10,
        unique=True,
    ),
    data=st.data(),
)
def test_property2_shared_text_flags_exactly_that_group(
    item_ids: list[str], data: st.DataObject
) -> None:
    # Choose >= 2 of the ids to collapse onto one shared normalized text; the
    # rest keep distinct texts. The shared group is the exact expected flagged set.
    n = len(item_ids)
    shared_count = data.draw(st.integers(min_value=2, max_value=n))
    shared_ids = set(item_ids[:shared_count])

    items: list[corpus_lint.LoadedItem] = []
    for idx, item_id in enumerate(item_ids):
        if item_id in shared_ids:
            # Same normalized text for every shared id (case/whitespace vary but
            # normalize away, exercising normalize_text rather than raw ==).
            text = "  SHARED   collision  TEXT " if idx % 2 else "shared collision text"
        else:
            text = _distinct_text(idx)
        items.append(_item(item_id, text))

    violations = corpus_lint.check_dedup(items)

    # Every finding is a duplicate violation.
    assert all(v.rule == "duplicate" for v in violations), (
        f"expected every rule to be 'duplicate', got "
        f"{sorted({v.rule for v in violations})}"
    )
    # Completeness + symmetry: exactly the shared ids are flagged, each once.
    assert {v.item_id for v in violations} == shared_ids, (
        "flagged ids != shared-text group: "
        f"flagged={sorted({v.item_id for v in violations})} "
        f"expected={sorted(shared_ids)}"
    )
    assert len(violations) == len(shared_ids), (
        f"expected one violation per shared id ({len(shared_ids)}), "
        f"got {len(violations)}"
    )
    # Symmetry of the detail: each violation names the OTHER colliding ids.
    for v in violations:
        others = shared_ids - {v.item_id}
        for other in others:
            assert other in v.detail, (
                f"duplicate violation for {v.item_id!r} does not name "
                f"co-colliding id {other!r}: {v.detail!r}"
            )


# ── Property 2 (biconditional over duplicated-or-not) ─────────────────────────
# Feature: detection-corpus, Property 2: Normalized-text uniqueness is symmetric and complete
#
# The full "iff": for a randomly grouped corpus, an item is flagged as a
# duplicate EXACTLY when at least one OTHER item shares its normalized text. We
# assign each id a group label; ids sharing a label share text. The expected
# flagged set is every id in a group of size >= 2.
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    item_ids=st.lists(
        st.text(min_size=1, max_size=24).filter(lambda s: s.strip() != ""),
        min_size=0,
        max_size=10,
        unique=True,
    ),
    data=st.data(),
)
def test_property2_flagged_iff_shares_normalized_text(
    item_ids: list[str], data: st.DataObject
) -> None:
    # Assign each id to a group index; ids in the same group get the same text.
    group_of: dict[str, int] = {}
    for item_id in item_ids:
        group_of[item_id] = data.draw(
            st.integers(min_value=0, max_value=max(0, len(item_ids) - 1)),
            label=f"group-{item_id}",
        )

    items = [
        _item(item_id, f"group text {group_of[item_id]}") for item_id in item_ids
    ]

    # Expected: an id is flagged iff another id shares its group (normalized text).
    group_members: dict[int, list[str]] = {}
    for item_id in item_ids:
        group_members.setdefault(group_of[item_id], []).append(item_id)
    expected_flagged = {
        item_id
        for item_id in item_ids
        if len(group_members[group_of[item_id]]) >= 2
    }

    violations = corpus_lint.check_dedup(items)
    assert {v.item_id for v in violations} == expected_flagged, (
        "duplicate flagged set != items sharing normalized text: "
        f"flagged={sorted({v.item_id for v in violations})} "
        f"expected={sorted(expected_flagged)}"
    )


# ── Property 3 ────────────────────────────────────────────────────────────────
# Feature: detection-corpus, Property 3: No train/eval leakage passes iff splits are clean
#
# For any corpus with NO train/eval normalized-text collision, check_leakage
# yields zero leakage violations — even when duplicates exist WITHIN a single
# split (that is a dedup concern, not leakage).
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    train_ids=_UNIQUE_IDS,
    eval_ids=_UNIQUE_IDS,
)
def test_property3_disjoint_splits_yield_no_leakage(
    train_ids: list[str], eval_ids: list[str]
) -> None:
    # train and eval texts drawn from disjoint namespaces -> no cross-split share.
    items = [_item(i, f"train text {i}", split="train") for i in train_ids]
    items += [_item(i, f"eval text {i}", split="eval") for i in eval_ids]
    violations = corpus_lint.check_leakage(items)
    assert violations == [], (
        "check_leakage flagged leakage with no shared train/eval text: "
        f"{[v.detail for v in violations]}"
    )


# ── Property 3 ────────────────────────────────────────────────────────────────
# Feature: detection-corpus, Property 3: No train/eval leakage passes iff splits are clean
#
# For any corpus, a leakage Violation exists EXACTLY when some train item and some
# eval item share normalized text. We build disjoint train/eval texts, then force
# a chosen train id and eval id to share one normalized text: leakage is flagged
# for that pair, and only that pair.
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    train_ids=st.lists(
        st.text(min_size=1, max_size=24).filter(lambda s: s.strip() != ""),
        min_size=1,
        max_size=6,
        unique=True,
    ),
    eval_ids=st.lists(
        st.text(min_size=1, max_size=24).filter(lambda s: s.strip() != ""),
        min_size=1,
        max_size=6,
        unique=True,
    ),
    data=st.data(),
)
def test_property3_forced_collision_flags_exact_pairs(
    train_ids: list[str], eval_ids: list[str], data: st.DataObject
) -> None:
    # Ensure the two id namespaces are disjoint so a shared text is the ONLY
    # cross-split collision (a shared id would be an id-dup owned by check_schema).
    eval_ids = [i for i in eval_ids if i not in set(train_ids)]
    if not eval_ids:
        return  # nothing to collide against; vacuous, skip this example

    # Pick one train id and a subset of eval ids to collapse onto a shared text.
    colliding_train = data.draw(st.sampled_from(train_ids))
    eval_share_count = data.draw(st.integers(min_value=1, max_value=len(eval_ids)))
    colliding_evals = set(eval_ids[:eval_share_count])

    shared = "  Cross  SPLIT   Collision "  # normalizes to a single canonical form

    items: list[corpus_lint.LoadedItem] = []
    for i in train_ids:
        text = shared if i == colliding_train else f"train text {i}"
        items.append(_item(i, text, split="train"))
    for i in eval_ids:
        text = shared if i in colliding_evals else f"eval text {i}"
        items.append(_item(i, text, split="eval"))

    violations = corpus_lint.check_leakage(items)

    assert all(v.rule == "leakage" for v in violations), (
        f"expected every rule to be 'leakage', got "
        f"{sorted({v.rule for v in violations})}"
    )
    # Exactly one leakage violation per (colliding train id, colliding eval id)
    # pair; item_id is the train id and the detail names the eval id.
    assert len(violations) == len(colliding_evals), (
        f"expected {len(colliding_evals)} leakage pairs, got {len(violations)}"
    )
    assert all(v.item_id == colliding_train for v in violations), (
        "every leakage violation should name the colliding train id "
        f"{colliding_train!r}: {[v.item_id for v in violations]}"
    )
    # The detail names the eval id via repr ({eval_id!r}), so match on repr to be
    # robust to ids containing characters that repr escapes (e.g. control chars).
    for eval_id in colliding_evals:
        assert any(repr(eval_id) in v.detail for v in violations), (
            f"no leakage violation names colliding eval id {eval_id!r}: "
            f"{[v.detail for v in violations]}"
        )


# ── Property 3 (biconditional) ────────────────────────────────────────────────
# Feature: detection-corpus, Property 3: No train/eval leakage passes iff splits are clean
#
# The full "iff": for a randomly grouped two-split corpus, leakage is flagged
# EXACTLY when a group holds both a train id and an eval id. Same-split-only
# groups (a within-split duplicate) are NOT leakage.
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    entries=st.lists(
        st.tuples(
            st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != ""),
            st.sampled_from(("train", "eval")),
            st.integers(min_value=0, max_value=5),  # group index -> shared text
        ),
        min_size=0,
        max_size=12,
        unique_by=lambda t: t[0],  # ids unique
    ),
)
def test_property3_leakage_iff_group_spans_both_splits(
    entries: list[tuple[str, str, int]],
) -> None:
    items = [
        _item(item_id, f"group text {group}", split=split)
        for item_id, split, group in entries
    ]

    # Expected leakage exists iff at least one group index carries both a train
    # id and an eval id (normalized text is fixed per group).
    trains_by_group: dict[int, set[str]] = {}
    evals_by_group: dict[int, set[str]] = {}
    for item_id, split, group in entries:
        (trains_by_group if split == "train" else evals_by_group).setdefault(
            group, set()
        ).add(item_id)

    expected_pairs: set[tuple[str, str]] = set()
    for group, trains in trains_by_group.items():
        for train_id in trains:
            for eval_id in evals_by_group.get(group, set()):
                expected_pairs.add((train_id, eval_id))

    violations = corpus_lint.check_leakage(items)

    # Every leakage violation has item_id == some train id and names an eval id
    # via repr in its detail. Reconstruct the flagged (train, eval) pairs by
    # matching each known eval id's repr against the detail (repr-based to be
    # robust to ids containing characters that repr escapes, e.g. control chars).
    all_eval_ids = {
        eval_id for grp in evals_by_group.values() for eval_id in grp
    }
    got_pairs: set[tuple[str, str]] = set()
    for v in violations:
        assert v.rule == "leakage"
        for eval_id in all_eval_ids:
            if repr(eval_id) in v.detail:
                got_pairs.add((v.item_id, eval_id))

    # The set of flagged (train, eval) pairs must equal the cross-split groups.
    assert got_pairs == expected_pairs, (
        "leakage pairs != groups spanning both splits: "
        f"flagged={sorted(got_pairs)} expected={sorted(expected_pairs)}"
    )


# --- both rules skip items other rules own (Requirement 1.8) -------------------
# Feature: detection-corpus, Property 2: Normalized-text uniqueness is symmetric and complete
# Feature: detection-corpus, Property 3: No train/eval leakage passes iff splits are clean
#
# Items already owned by check_schema are skipped so these rules never
# double-report or crash: an unparsed line (obj is None), a missing/empty/non-string
# id, and a non-string text each contribute nothing. For leakage, an item with a
# missing or invalid split is also ignored (leakage is specifically train-vs-eval).
def test_dedup_and_leakage_skip_schema_owned_items() -> None:
    skipped = [
        corpus_lint.LoadedItem(line=1, source="test.jsonl", obj=None),
        corpus_lint.LoadedItem(
            line=2, source="test.jsonl", obj={"text": "x", "split": "train"}
        ),  # missing id
        corpus_lint.LoadedItem(
            line=3,
            source="test.jsonl",
            obj={"id": "", "text": "x", "split": "train"},
        ),  # empty id
        corpus_lint.LoadedItem(
            line=4,
            source="test.jsonl",
            obj={"id": "no-text", "split": "train"},
        ),  # missing text
        corpus_lint.LoadedItem(
            line=5,
            source="test.jsonl",
            obj={"id": "bad-text", "text": 123, "split": "train"},
        ),  # non-string text
    ]
    assert corpus_lint.check_dedup(skipped) == []
    assert corpus_lint.check_leakage(skipped) == []


def test_leakage_ignores_items_without_valid_split() -> None:
    # Two items with identical normalized text but neither carries a valid split:
    # a duplicate (dedup's concern) but NOT leakage.
    items = [
        corpus_lint.LoadedItem(
            line=1, source="test.jsonl", obj={"id": "a", "text": "same text"}
        ),  # missing split
        corpus_lint.LoadedItem(
            line=2,
            source="test.jsonl",
            obj={"id": "b", "text": "same text", "split": "holdout"},
        ),  # invalid split
    ]
    assert corpus_lint.check_leakage(items) == []
    # dedup still sees them as a normalized-text collision.
    assert {v.item_id for v in corpus_lint.check_dedup(items)} == {"a", "b"}
