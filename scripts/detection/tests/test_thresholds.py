"""Property + example tests for ``thresholds.py`` (posture scoring harness).

Additive, measurement-only (Requirement 9): this file lives under
``scripts/detection/`` and touches no production path.

Task 3.2 implements Property 8 (candidate thresholds are exactly the distinct
observed scores).
"""

from __future__ import annotations

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# The detection modules import as top-level names with ``scripts/detection`` on
# the path (they are a self-contained additive package). Make that import work
# whether pytest is invoked from the repo root or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import thresholds  # noqa: E402


# A score is a real value; posture scores live in [0.0, 1.0] but candidate
# derivation is defined for any real, so exercise a slightly wider range
# (including negatives, zero, and exact duplicates via a small allow_nan=False
# float domain) to prove the distinct-set invariant holds generally.
_scores = st.lists(
    st.floats(
        min_value=-1.0,
        max_value=2.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    max_size=50,
)


@settings(max_examples=200)
@given(train_scores=_scores)
def test_candidate_thresholds_are_exactly_distinct_observed_scores(train_scores):
    """Feature: posture-scoring, Property 8: For any train score-vector, the set
    of candidate decision thresholds equals the set of distinct posture scores
    observed over that vector, with each reachable operating point represented
    exactly once and no threshold not reachable from an observed score.

    Validates: Requirements 3.6
    """
    candidates = thresholds.candidate_thresholds(train_scores)

    observed_distinct = set(train_scores)

    # The set of candidates equals the set of distinct observed scores.
    assert set(candidates) == observed_distinct

    # Each reachable operating point is represented exactly once (no duplicates).
    assert len(candidates) == len(observed_distinct)

    # No threshold that is not reachable from an observed score: every candidate
    # is one of the observed input scores.
    for threshold in candidates:
        assert threshold in observed_distinct


@settings(max_examples=200)
@given(train_scores=_scores)
def test_candidate_thresholds_are_sorted_ascending(train_scores):
    """Candidates are returned in a deterministic ascending order.

    Validates: Requirements 3.6 (each reachable operating point once,
    deterministically ordered).
    """
    candidates = thresholds.candidate_thresholds(train_scores)
    assert candidates == sorted(candidates)


# --- Example / edge-case unit tests -------------------------------------------


def test_candidate_thresholds_empty_input():
    """An empty train score-vector yields no candidate thresholds."""
    assert thresholds.candidate_thresholds([]) == []


def test_candidate_thresholds_dedupes_repeats():
    """Repeated scores collapse to a single reachable operating point each."""
    assert thresholds.candidate_thresholds([0.5, 0.5, 0.1, 0.9, 0.1]) == [0.1, 0.5, 0.9]


def test_candidate_thresholds_single_score():
    """A single observed score is the only candidate."""
    assert thresholds.candidate_thresholds([0.42]) == [0.42]


# --- Property 7: threshold selection correctness --------------------------------

# Generate aligned score/label vectors. Scores are drawn from a small pool with
# duplicates (so several items share a candidate threshold and ties in
# recall/FPR are exercised). Labels are malicious/benign, and we force enough
# benign items that the 1%-FPR target is *sometimes* achievable: a threshold
# with 0 benign flagged always satisfies FPR <= target, and with >= 100 benign a
# single benign flag (1/100 = 0.01) is also feasible.
_score_pool = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _score_label_vectors(draw):
    """Draw aligned (scores, labels) with both classes and ample benign items."""
    # A shared pool of candidate score values (with intentional duplicates).
    pool = draw(st.lists(_score_pool, min_size=1, max_size=12))
    n = draw(st.integers(min_value=2, max_value=60))
    scores = [draw(st.sampled_from(pool)) for _ in range(n)]
    labels = [draw(st.sampled_from([thresholds._MALICIOUS_LABEL,
                                    thresholds._BENIGN_LABEL]))
              for _ in range(n)]
    # Guarantee at least one malicious and at least one benign item so recall and
    # FPR are both computable, and so the target is reachable at least sometimes.
    labels[0] = thresholds._MALICIOUS_LABEL
    labels[-1] = thresholds._BENIGN_LABEL
    return scores, labels


def _feasible_candidates(scores, labels):
    """Independently recompute (threshold, recall, fpr) for feasible candidates.

    Feasible = FPR is computable and fpr <= TARGET_FPR + FPR_TOLERANCE.
    """
    feasible = []
    for threshold in thresholds.candidate_thresholds(scores):
        rec = thresholds._recall(scores, labels, threshold)
        fp = thresholds._fpr(scores, labels, threshold)
        if fp is not None and fp <= thresholds.TARGET_FPR + thresholds.FPR_TOLERANCE:
            feasible.append((threshold, rec if rec is not None else 0.0, fp))
    return feasible


@settings(max_examples=200)
@given(data=_score_label_vectors())
def test_threshold_selection_feasible_recall_maximal_tiebroken_deterministic(data):
    """Feature: posture-scoring, Property 7: For any train score-vector and
    label-vector, the selected threshold's false-positive-rate does not exceed the
    target FPR by more than 1e-6; among all candidate thresholds meeting that FPR
    constraint it achieves the maximum recall; ties in recall are broken by lowest
    FPR and then highest threshold; the reported (recall, achieved_fpr,
    selected_threshold) triple equals recomputing recall and FPR at the selected
    threshold; and repeated selection over identical inputs yields the identical
    selected threshold.

    Validates: Requirements 3.2, 3.3, 3.4
    """
    scores, labels = data
    choice = thresholds.select_threshold(scores, labels)

    # Determinism: repeated selection over identical inputs is identical.
    choice_again = thresholds.select_threshold(scores, labels)
    assert choice == choice_again

    if not choice.target_achievable:
        # Property 7 constrains the achievable case; the unachievable-floor case
        # is Property 10's territory. Nothing further to assert here.
        return

    feasible = _feasible_candidates(scores, labels)
    # target_achievable => there is at least one feasible candidate.
    assert feasible, "target_achievable True but no feasible candidate found"

    # Feasibility: achieved FPR is within target + tolerance.
    assert choice.achieved_fpr <= thresholds.TARGET_FPR + thresholds.FPR_TOLERANCE

    # Recall-maximality: the selected recall is the maximum recall over feasible
    # candidates (recomputed independently).
    max_recall = max(rec for (_t, rec, _f) in feasible)
    assert choice.recall == max_recall

    # Tie-break: among recall-maximal feasible candidates, expected pick is the
    # lowest FPR, then the highest threshold.
    recall_maximal = [item for item in feasible if item[1] == max_recall]
    expected = min(recall_maximal, key=lambda item: (item[2], -item[0]))
    exp_threshold, exp_recall, exp_fpr = expected
    assert choice.selected_threshold == exp_threshold
    assert choice.achieved_fpr == exp_fpr
    assert choice.recall == exp_recall

    # Reported triple equals recomputing recall and FPR at the selected threshold.
    rec_at = thresholds._recall(scores, labels, choice.selected_threshold)
    fpr_at = thresholds._fpr(scores, labels, choice.selected_threshold)
    assert choice.recall == (rec_at if rec_at is not None else 0.0)
    assert choice.achieved_fpr == (fpr_at if fpr_at is not None else 0.0)


# --- Example / edge-case unit tests for selection -----------------------------


def test_select_threshold_prefers_higher_recall_within_target():
    """A lower threshold that catches more malicious wins while FPR stays <= 1%."""
    # 100 benign all scoring 0.1 (so a threshold > 0.1 flags 0 benign -> FPR 0).
    # 2 malicious: one at 0.5, one at 0.9. Threshold 0.5 flags both (recall 1.0),
    # FPR 0; threshold 0.9 flags one (recall 0.5). Expect threshold 0.5.
    scores = [0.5, 0.9] + [0.1] * 100
    labels = ["malicious", "malicious"] + ["benign"] * 100
    choice = thresholds.select_threshold(scores, labels)
    assert choice.target_achievable is True
    assert choice.selected_threshold == 0.5
    assert choice.recall == 1.0
    assert choice.achieved_fpr == 0.0


def test_select_threshold_tiebreak_highest_threshold_on_equal_recall_and_fpr():
    """Equal recall and equal FPR => highest threshold wins."""
    # 1 malicious at 0.9; 100 benign at 0.1. Candidate thresholds 0.1 and 0.9.
    # threshold 0.9: recall 1.0, FPR 0. threshold 0.1: recall 1.0, FPR 1.0
    # (infeasible). So only 0.9 is feasible; it is chosen.
    scores = [0.9] + [0.1] * 100
    labels = ["malicious"] + ["benign"] * 100
    choice = thresholds.select_threshold(scores, labels)
    assert choice.selected_threshold == 0.9
    assert choice.recall == 1.0
    assert choice.achieved_fpr == 0.0


def test_select_threshold_target_unachievable_reports_floor_flag():
    """When no benign items exist, target is not achievable (FPR uncomputable)."""
    scores = [0.5, 0.7]
    labels = ["malicious", "malicious"]
    choice = thresholds.select_threshold(scores, labels)
    assert choice.target_achievable is False


def test_select_threshold_empty_inputs():
    """Empty inputs yield a deterministic degenerate, target-unachievable choice."""
    choice = thresholds.select_threshold([], [])
    assert choice.target_achievable is False
    assert choice.selected_threshold == 0.0
    assert choice.recall == 0.0
    assert choice.achieved_fpr == 0.0


# --- Property 10: honest FPR floor when the target is unachievable -------------


def _independent_lowest_fpr(scores, labels):
    """Independently recompute the lowest achievable FPR over candidate thresholds.

    Returns ``(fpr_floor, floor_threshold)`` selected with the same deterministic
    ordering the harness uses (lowest FPR first, then highest threshold), or
    ``None`` when no benign items exist so FPR is uncomputable everywhere.
    """
    computable = []
    for threshold in thresholds.candidate_thresholds(scores):
        fp = thresholds._fpr(scores, labels, threshold)
        if fp is not None:
            computable.append((threshold, fp))
    if not computable:
        return None
    floor_threshold, fpr_floor = min(computable, key=lambda item: (item[1], -item[0]))
    return fpr_floor, floor_threshold


@st.composite
def _unachievable_all_top_benign(draw):
    """Draw a score/label set where the 1%-FPR target is NOT achievable.

    Every benign item shares the top score, so any threshold that flags a
    malicious item at or above that score necessarily flags every benign item
    too (FPR 1.0), and any threshold above it flags nothing — there is no
    operating point catching malicious while keeping FPR <= 1%. This guarantees
    ``target_achievable`` is False with benign items present (FPR computable).
    """
    top = 1.0
    # At least one benign item, all at the top score.
    n_benign = draw(st.integers(min_value=1, max_value=40))
    # At least one malicious item strictly below the top score (so a threshold
    # exists that catches malicious without flagging benign is impossible: any
    # threshold <= a malicious score is also <= top, flagging all benign).
    mal_scores = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=0.99, allow_nan=False,
                      allow_infinity=False),
            min_size=1,
            max_size=40,
        )
    )
    scores = list(mal_scores) + [top] * n_benign
    labels = [thresholds._MALICIOUS_LABEL] * len(mal_scores) + [
        thresholds._BENIGN_LABEL
    ] * n_benign
    return scores, labels


@st.composite
def _no_benign_items(draw):
    """Draw a score/label set with malicious items only (no benign → no FPR)."""
    mal_scores = draw(
        st.lists(_score_pool, min_size=1, max_size=40)
    )
    scores = list(mal_scores)
    labels = [thresholds._MALICIOUS_LABEL] * len(mal_scores)
    return scores, labels


@settings(max_examples=200)
@given(data=_unachievable_all_top_benign())
def test_fpr_floor_reported_when_target_unachievable_with_benign(data):
    """Feature: posture-scoring, Property 10: For any score/label set in which no
    candidate threshold achieves FPR at most the target (or which contains no
    benign items), the harness reports the target as not achievable, reports the
    FPR floor equal to the lowest achievable false-positive-rate over the
    candidates, and reports the recall at the threshold realising that floor,
    rather than a `Recall_At_Target_FPR` value.

    This case exercises the branch where benign items ARE present (so the FPR is
    computable and the floor is a real value) but no candidate reaches the 1%
    target.

    Validates: Requirements 3.5
    """
    scores, labels = data

    choice = thresholds.select_threshold(scores, labels)
    floor = thresholds.fpr_floor(scores, labels)

    # Determinism: identical inputs yield the identical FloorChoice.
    assert floor == thresholds.fpr_floor(scores, labels)

    # The target is reported NOT achievable.
    assert choice.target_achievable is False

    # Benign items are present so the floor is computable and is a real value.
    assert floor.computable is True
    assert floor.fpr_floor is not None

    # The reported FPR floor equals the independently-computed lowest achievable
    # FPR over the candidate thresholds.
    independent = _independent_lowest_fpr(scores, labels)
    assert independent is not None
    exp_fpr_floor, _exp_threshold = independent
    assert floor.fpr_floor == exp_fpr_floor

    # By construction of this generator the only achievable FPR is 1.0 (all
    # benign share the top score), which exceeds the 1% target.
    assert floor.fpr_floor > thresholds.TARGET_FPR + thresholds.FPR_TOLERANCE

    # The recall reported at the floor equals recall recomputed at the floor
    # threshold (not a Recall_At_Target_FPR value).
    rec_at_floor = thresholds._recall(scores, labels, floor.floor_threshold)
    assert floor.recall_at_floor == (rec_at_floor if rec_at_floor is not None else 0.0)

    # The floor threshold is one of the candidate operating points.
    assert floor.floor_threshold in set(thresholds.candidate_thresholds(scores))


@settings(max_examples=200)
@given(data=_no_benign_items())
def test_fpr_floor_no_benign_items_not_computable(data):
    """Feature: posture-scoring, Property 10: For any score/label set ... which
    contains no benign items, the harness reports the target as not achievable ...
    rather than a `Recall_At_Target_FPR` value.

    With no benign items the FPR (and hence the floor) is not computable: the
    harness reports ``computable = False`` and ``fpr_floor is None`` while still
    surfacing an honest recall value at a deterministic fallback threshold.

    Validates: Requirements 3.5
    """
    scores, labels = data

    choice = thresholds.select_threshold(scores, labels)
    floor = thresholds.fpr_floor(scores, labels)

    # Determinism.
    assert floor == thresholds.fpr_floor(scores, labels)

    # No benign → target not achievable, floor not computable, no fabricated FPR.
    assert choice.target_achievable is False
    assert floor.computable is False
    assert floor.fpr_floor is None

    # No benign items exist, so the independent FPR computation is also None.
    assert _independent_lowest_fpr(scores, labels) is None

    # An honest recall value is still reported, recomputed at the fallback
    # (highest candidate) threshold rather than fabricated.
    rec_at_floor = thresholds._recall(scores, labels, floor.floor_threshold)
    assert floor.recall_at_floor == (rec_at_floor if rec_at_floor is not None else 0.0)
    assert floor.floor_threshold in set(thresholds.candidate_thresholds(scores))


def test_fpr_floor_example_all_benign_top_score():
    """Concrete example: benign at the top score forces the floor above target.

    2 malicious at 0.4/0.6, 3 benign all at 0.9. Candidate thresholds are
    {0.4, 0.6, 0.9}. At 0.9 every benign is flagged (FPR 1.0); at 0.6 and 0.4 the
    benign at 0.9 are still flagged (FPR 1.0). The lowest achievable FPR is 1.0,
    which exceeds the 1% target, so the floor is reported honestly.
    """
    scores = [0.4, 0.6, 0.9, 0.9, 0.9]
    labels = ["malicious", "malicious", "benign", "benign", "benign"]

    choice = thresholds.select_threshold(scores, labels)
    floor = thresholds.fpr_floor(scores, labels)

    assert choice.target_achievable is False
    assert floor.computable is True
    assert floor.fpr_floor == 1.0
    # Lowest-FPR ties broken by highest threshold => 0.9.
    assert floor.floor_threshold == 0.9
    # Recall at 0.9: no malicious (0.4, 0.6) is flagged => 0.0.
    assert floor.recall_at_floor == 0.0


def test_fpr_floor_no_benign_example():
    """Concrete example: no benign items => floor not computable, recall honest."""
    scores = [0.3, 0.8]
    labels = ["malicious", "malicious"]

    floor = thresholds.fpr_floor(scores, labels)

    assert floor.computable is False
    assert floor.fpr_floor is None
    # Fallback threshold is the highest candidate (0.8); recall there = 1/2.
    assert floor.floor_threshold == 0.8
    assert floor.recall_at_floor == 0.5


def test_fpr_floor_empty_inputs():
    """Empty inputs => degenerate, deterministic, not-computable FloorChoice."""
    floor = thresholds.fpr_floor([], [])
    assert floor.computable is False
    assert floor.fpr_floor is None
    assert floor.floor_threshold == 0.0
    assert floor.recall_at_floor == 0.0


# --- Property 9: no evaluation leakage into threshold selection ----------------

# ``corpus`` imports as a top-level name with ``scripts/detection`` on the path
# (added above). Used to prove that only ``train``-split items would be selected
# for tuning, so non-train items never reach ``select_threshold``.
import corpus  # noqa: E402


@st.composite
def _train_and_nontrain_items(draw):
    """Draw a train (scores, labels) set plus arbitrary non-train items.

    Returns ``(train_scores, train_labels, nontrain)`` where ``nontrain`` is a
    list of ``(score, label, split)`` triples whose split is ``eval`` or an
    arbitrary "other" value — never ``train``. The train set carries the aligned
    scores/labels the harness would pass to ``select_threshold``; the non-train
    items model records that exist in the corpus but must not influence the
    selected threshold.
    """
    n_train = draw(st.integers(min_value=0, max_value=40))
    train_scores = [draw(_score_pool) for _ in range(n_train)]
    train_labels = [
        draw(st.sampled_from([thresholds._MALICIOUS_LABEL, thresholds._BENIGN_LABEL]))
        for _ in range(n_train)
    ]

    # Non-train splits: 'eval' or any arbitrary non-'train' token (which
    # corpus.split_of maps to 'other'). Never emit 'train' here.
    nontrain_split = st.one_of(
        st.just(corpus.EVAL_SPLIT),
        st.text(min_size=0, max_size=8).filter(lambda s: s != corpus.TRAIN_SPLIT),
    )
    n_extra = draw(st.integers(min_value=0, max_value=40))
    nontrain = [
        (
            draw(_score_pool),
            draw(st.sampled_from([thresholds._MALICIOUS_LABEL,
                                  thresholds._BENIGN_LABEL])),
            draw(nontrain_split),
        )
        for _ in range(n_extra)
    ]
    return train_scores, train_labels, nontrain


def _make_item(item_id, score, label, split):
    """Build a Corpus_Item carrying the given split (score is unused by split_of)."""
    return corpus.Corpus_Item(
        id=str(item_id),
        text="",
        label=label,
        family="",
        split=split,
        provenance="",
    )


@settings(max_examples=200)
@given(data=_train_and_nontrain_items())
def test_no_evaluation_leakage_into_threshold_selection(data):
    """Feature: posture-scoring, Property 9: For any train score/label set and any
    additional items whose split is ``eval`` or otherwise non-train, the selected
    threshold computed from the train items alone is identical to the selected
    threshold computed after adding those non-train items — no non-train item
    influences the selected threshold, and the headline threshold is applied to
    the eval split for measurement.

    ``select_threshold`` is a pure function of ONLY the (train) score/label set
    passed to it. The harness only ever passes TRAIN items: it filters the corpus
    by ``corpus.split_of(item) == 'train'`` before tuning, so ``eval`` / ``other``
    items never enter the call. This test models that contract: the threshold from
    the train items alone equals the threshold computed after "adding" arbitrary
    non-train items, because the added items are filtered out before the call and
    therefore leave the ``select_threshold`` input identical.

    Validates: Requirements 3.7, 7.5, 7.6
    """
    train_scores, train_labels, nontrain = data

    # (1) select_threshold over the train items alone.
    from_train_only = thresholds.select_threshold(train_scores, train_labels)

    # (2) Determinism: identical inputs yield the identical ThresholdChoice.
    assert thresholds.select_threshold(train_scores, train_labels) == from_train_only

    # (3) Model the corpus: train items PLUS the arbitrary non-train items. The
    # harness filters to the 'train' split before tuning, so reconstruct exactly
    # the (scores, labels) that reach select_threshold from that filtered view.
    items = []
    for i, (score, label) in enumerate(zip(train_scores, train_labels)):
        items.append((score, _make_item(f"t{i}", score, label, corpus.TRAIN_SPLIT)))
    for j, (score, label, split) in enumerate(nontrain):
        items.append((score, _make_item(f"x{j}", score, label, split)))

    # Only 'train'-split items are selected for tuning (via corpus.split_of).
    tuning = [
        (score, item.label)
        for (score, item) in items
        if corpus.split_of(item) == corpus.TRAIN_SPLIT
    ]
    tuning_scores = [score for (score, _label) in tuning]
    tuning_labels = [label for (_score, label) in tuning]

    # No non-train item survived the filter: the tuning set equals the train set.
    assert tuning_scores == train_scores
    assert tuning_labels == train_labels

    # split_of never classifies a non-train item as 'train' (no leakage vector).
    for (_score, item) in items[len(train_scores):]:
        assert corpus.split_of(item) != corpus.TRAIN_SPLIT

    # (4) The selected threshold after "adding" the non-train items (i.e. over the
    # filtered tuning set) is IDENTICAL to the threshold from the train items
    # alone — no non-train item influenced the choice.
    from_filtered = thresholds.select_threshold(tuning_scores, tuning_labels)
    assert from_filtered == from_train_only


# --- Example / edge-case unit tests for no-leakage -----------------------------


def test_no_leakage_eval_items_do_not_change_selected_threshold():
    """Adding eval-split items that would shift the threshold if consumed does not
    change the selection, because only train items are tuned on.

    Validates: Requirements 7.5, 7.6
    """
    # Train: 1 malicious @ 0.9, 100 benign @ 0.1 -> selected threshold 0.9.
    train_scores = [0.9] + [0.1] * 100
    train_labels = ["malicious"] + ["benign"] * 100
    baseline = thresholds.select_threshold(train_scores, train_labels)
    assert baseline.selected_threshold == 0.9

    # Eval items with adversarial scores that WOULD move the threshold if leaked.
    eval_items = [
        _make_item("e0", 0.2, "malicious", corpus.EVAL_SPLIT),
        _make_item("e1", 0.95, "benign", corpus.EVAL_SPLIT),
    ]
    train_items = [
        _make_item(f"t{i}", s, lbl, corpus.TRAIN_SPLIT)
        for i, (s, lbl) in enumerate(zip(train_scores, train_labels))
    ]
    # Pair each item with its own score, then filter to the 'train' split only.
    scored = [(train_scores[i], train_items[i]) for i in range(len(train_items))]
    scored += [(0.2, eval_items[0]), (0.95, eval_items[1])]
    tuning = [(s, it.label) for (s, it) in scored
              if corpus.split_of(it) == corpus.TRAIN_SPLIT]

    filtered_scores = [s for (s, _lbl) in tuning]
    filtered_labels = [lbl for (_s, lbl) in tuning]
    after = thresholds.select_threshold(filtered_scores, filtered_labels)

    assert after == baseline
    assert after.selected_threshold == 0.9


def test_no_leakage_other_split_excluded_from_tuning():
    """Items with a split other than 'train'/'eval' are 'other' and never tuned on.

    Validates: Requirements 7.6
    """
    other = _make_item("o0", 0.5, "benign", "holdout")
    assert corpus.split_of(other) == "other"
    assert corpus.split_of(other) != corpus.TRAIN_SPLIT
