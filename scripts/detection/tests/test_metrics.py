"""Property + example tests for ``metrics.py`` (posture scoring harness).

Additive, measurement-only (Requirement 9): this file lives under
``scripts/detection/`` and touches no production path.

Task 2.2 implements Property 6 (round-half-up to 4 decimal places is correct
and idempotent).
"""

from __future__ import annotations

import os
import sys
from decimal import ROUND_HALF_UP, Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

# The detection modules import as top-level names with ``scripts/detection`` on
# the path (they are a self-contained additive package). Make that import work
# whether pytest is invoked from the repo root or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metrics  # noqa: E402

_QUANTUM = Decimal("0.0001")


def _independent_round_half_up_4dp(value: float) -> Decimal:
    """Reference round-half-up to 4 dp, computed independently of ``metrics``.

    Mirrors the exact-decimal path ``round_metric`` uses (quantize the decimal
    the caller wrote, via ``str``, so the half-up decision is deterministic at
    the 4th place) — but written here as an independent oracle so the test does
    not merely restate the implementation.
    """
    return Decimal(str(value)).quantize(_QUANTUM, rounding=ROUND_HALF_UP)


# Real metric values. Posture metrics live in [0.0, 1.0] / [-1.0, 1.0], but
# ``round_metric`` is defined for any real, so exercise a wider finite range
# (including negatives and zero, no NaN/inf) to prove the property holds
# generally.
_reals = st.floats(
    min_value=-1000.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)


@settings(max_examples=200)
@given(value=_reals)
def test_round_metric_correct_and_idempotent(value):
    """Feature: posture-scoring, Property 6: For any real metric value,
    ``round_metric`` produces a value with at most 4 decimal places equal to the
    round-half-up rounding of the input to 4 dp, and applying ``round_metric``
    again yields the same value.

    Validates: Requirements 2.8, 4.2, 5.2
    """
    result = metrics.round_metric(value)

    # (a) At most 4 decimal places: the float, taken as the exact decimal it
    # represents, must be an integer multiple of 0.0001. Comparing against the
    # quantized-to-4dp Decimal of the result proves no 5th+ decimal place.
    result_decimal = Decimal(str(result))
    assert result_decimal == result_decimal.quantize(_QUANTUM, rounding=ROUND_HALF_UP)

    # (b) Equals the independent round-half-up of the input to 4 dp.
    expected = _independent_round_half_up_4dp(value)
    assert Decimal(str(result)) == expected

    # (c) Idempotent: rounding an already-rounded value yields the same value.
    assert metrics.round_metric(result) == result


# --- Example / edge-case unit tests -------------------------------------------


def test_round_metric_half_up_at_fourth_place():
    """A ``.5`` case at the 5th place rounds away from zero (not banker's)."""
    # Python's round(0.12345, 4) == 0.1234 (round-half-to-even); round_metric
    # must round half UP to 0.1235.
    assert metrics.round_metric(0.12345) == 0.1235
    assert metrics.round_metric(0.12355) == 0.1236


def test_round_metric_negative_half_up():
    """Negative ``.5`` cases round away from zero (round-half-up magnitude)."""
    assert metrics.round_metric(-0.12345) == -0.1235


def test_round_metric_already_four_places_unchanged():
    """A value already at 4 dp is returned unchanged (idempotent base case)."""
    assert metrics.round_metric(0.1234) == 0.1234
    assert metrics.round_metric(1.0) == 1.0
    assert metrics.round_metric(0.0) == 0.0


def test_round_metric_truncates_below_half():
    """Digits below the half boundary at the 5th place round down."""
    assert metrics.round_metric(0.123449) == 0.1234


def test_round_metric_idempotent_examples():
    """Applying round_metric twice equals applying it once (idempotence)."""
    for v in (0.12345, 0.999949, -0.55555, 0.00005, 0.75):
        once = metrics.round_metric(v)
        assert metrics.round_metric(once) == once


# --- Task 2.5: Property 4 — false-positive-rate definition and range ----------


def _independent_fpr(scores, labels, threshold):
    """Reference FPR, computed independently of ``metrics.fpr``.

    Count benign items (label exactly ``"benign"``) whose score is at or above
    the threshold, divided by the total count of benign items. Written as an
    independent oracle so the test does not merely restate the implementation.
    Caller guarantees at least one benign item.
    """
    benign_total = 0
    benign_flagged = 0
    for score, label in zip(scores, labels):
        if label == "benign":
            benign_total += 1
            if score >= threshold:
                benign_flagged += 1
    return benign_flagged / benign_total


# A label drawn from benign / malicious / an unrecognized-or-absent value, so
# the property holds when non-benign labels (which must never contribute to the
# FPR denominator) are interleaved with the benign items.
_fpr_labels = st.sampled_from(["benign", "malicious", "other", "", "BENIGN"])

_fpr_score = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

_fpr_threshold = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _aligned_scores_labels_with_benign(draw):
    """Aligned (scores, labels) vectors guaranteed to contain >= 1 benign item."""
    n = draw(st.integers(min_value=0, max_value=40))
    scores = draw(st.lists(_fpr_score, min_size=n, max_size=n))
    labels = draw(st.lists(_fpr_labels, min_size=n, max_size=n))

    # Guarantee at least one benign item by injecting a benign pair at a random
    # position (Property 4 is quantified over sets with >= 1 benign item).
    inject_score = draw(_fpr_score)
    pos = draw(st.integers(min_value=0, max_value=len(scores)))
    scores.insert(pos, inject_score)
    labels.insert(pos, "benign")
    return scores, labels


@settings(max_examples=200)
@given(data=_aligned_scores_labels_with_benign(), threshold=_fpr_threshold)
def test_fpr_definition_and_range(data, threshold):
    """Feature: posture-scoring, Property 4: For any score-vector, label-vector,
    and decision threshold with at least one benign item, ``fpr`` equals the
    count of benign items whose score is at or above the threshold divided by the
    total count of benign items, and lies in the inclusive range 0.0 to 1.0.

    Validates: Requirements 2.2
    """
    scores, labels = data
    result = metrics.fpr(scores, labels, threshold)

    # With >= 1 benign item the metric is computable (never NotComputable).
    assert isinstance(result, float)

    # (a) Equals the independent count(benign flagged) / count(benign).
    expected = _independent_fpr(scores, labels, threshold)
    assert result == expected

    # (b) Lies in the inclusive range 0.0 to 1.0.
    assert 0.0 <= result <= 1.0


# --- Task 2.4: recall definition and range (Property 3) ----------------------

# Label pool for aligned score/label vectors: the two recognized ground-truth
# values plus an unrecognized value that must NOT count toward the malicious
# population (Requirement 2.3). "" also stands in for an absent/unrecognized
# label.
_labels = st.sampled_from(["malicious", "benign", "other", ""])

# Posture scores span the natural [0.0, 1.0] range plus the boundaries, so the
# threshold comparison (>=) is exercised at, below, and above observed scores.
_scores = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

_thresholds = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _aligned_score_label_vectors_with_malicious(draw):
    """Aligned (scores, labels) vectors constrained to >=1 malicious item.

    Generates positionally-aligned equal-length score and label vectors, then
    forces at least one malicious item (Property 3 quantifies over the case
    where the malicious population is non-empty, so ``recall`` returns a number,
    not ``NotComputable``).
    """
    n = draw(st.integers(min_value=1, max_value=40))
    scores = draw(st.lists(_scores, min_size=n, max_size=n))
    labels = draw(st.lists(_labels, min_size=n, max_size=n))
    # Guarantee at least one malicious item at a random position.
    idx = draw(st.integers(min_value=0, max_value=n - 1))
    labels[idx] = "malicious"
    return scores, labels


@settings(max_examples=200)
@given(
    data=_aligned_score_label_vectors_with_malicious(),
    threshold=_thresholds,
)
def test_recall_definition_and_range(data, threshold):
    """Feature: posture-scoring, Property 3: For any score-vector, label-vector,
    and decision threshold with at least one malicious item, ``recall`` equals
    the count of malicious items whose score is at or above the threshold divided
    by the total count of malicious items, and lies in the inclusive range 0.0 to
    1.0.

    Validates: Requirements 2.1
    """
    scores, labels = data

    result = metrics.recall(scores, labels, threshold)

    # There is >=1 malicious item, so recall is a real number, not NotComputable.
    assert isinstance(result, float)

    # Independent recomputation: count malicious items (label exactly
    # "malicious") whose score is at or above the threshold, over the total
    # malicious count. Computed here without calling into metrics helpers so the
    # test does not merely restate the implementation.
    malicious_total = 0
    malicious_flagged = 0
    for score, label in zip(scores, labels):
        if label == "malicious":
            malicious_total += 1
            if score >= threshold:
                malicious_flagged += 1

    assert malicious_total >= 1
    expected = malicious_flagged / malicious_total
    assert result == expected

    # Range: recall lies in the inclusive range [0.0, 1.0].
    assert 0.0 <= result <= 1.0


# --- Task 2.7: per-family recall definition, rounding, and range (Property 11) -

# Family names for malicious items include the distinguished ``paraphrase``
# family plus other attack families. Benign families are also drawn so the
# generator interleaves benign items (which must never contribute to any attack
# family's recall). "" stands in for an unrecognized/absent family or label.
_pfr_family = st.sampled_from(
    ["prompt_injection", "paraphrase", "jailbreak", "data_leakage", ""]
)

_pfr_label = st.sampled_from(["malicious", "benign", "other", ""])

_pfr_score = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

_pfr_threshold = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _aligned_scores_labels_families(draw):
    """Aligned (scores, labels, families) triples of equal length.

    Generates positionally-aligned score, label, and family vectors. No item is
    forced malicious: Property 11 quantifies over each attack family *present*,
    so an empty malicious population simply yields an empty result dict, which
    the assertions below handle correctly.
    """
    n = draw(st.integers(min_value=0, max_value=40))
    scores = draw(st.lists(_pfr_score, min_size=n, max_size=n))
    labels = draw(st.lists(_pfr_label, min_size=n, max_size=n))
    families = draw(st.lists(_pfr_family, min_size=n, max_size=n))
    return scores, labels, families


@settings(max_examples=200)
@given(data=_aligned_scores_labels_families(), threshold=_pfr_threshold)
def test_per_family_recall_definition_rounding_and_range(data, threshold):
    """Feature: posture-scoring, Property 11: For any corpus and selected
    threshold, for each attack family present, the per-family recall equals the
    count of that family's malicious items flagged divided by the total count of
    that family's malicious items, rounded to 4 dp, and lies in the inclusive
    range 0.0 to 1.0.

    Validates: Requirements 4.1, 4.2
    """
    scores, labels, families = data

    result = metrics.per_family_recall(scores, labels, families, threshold)

    # Independently recompute the malicious per-family flagged/total counts,
    # grouping solely by the ``family`` value of items whose label is exactly
    # "malicious" (benign / unrecognized-label items must not contribute). This
    # oracle does not call into ``metrics`` so the test is not a restatement.
    totals: dict[str, int] = {}
    flagged_counts: dict[str, int] = {}
    for score, label, family in zip(scores, labels, families):
        if label != "malicious":
            continue
        totals[family] = totals.get(family, 0) + 1
        if score >= threshold:
            flagged_counts[family] = flagged_counts.get(family, 0) + 1

    # Only families of malicious items are keys of the result — benign families
    # are excluded, and the key set matches the independently derived one.
    assert set(result.keys()) == set(totals.keys())

    for family, total in totals.items():
        value = result[family]

        # Every present attack family has >= 1 malicious item, so its recall is
        # a real number (never NotComputable).
        assert isinstance(value, float)

        # (a) Equals round_metric(count flagged / count total). Independently
        # round-half-up to 4 dp via the module-level oracle so the rounding
        # itself is verified, not merely restated.
        raw = flagged_counts.get(family, 0) / total
        assert Decimal(str(value)) == _independent_round_half_up_4dp(raw)

        # (b) Lies in the inclusive range 0.0 to 1.0.
        assert 0.0 <= value <= 1.0


# --- Task 2.9: Property 13 — paraphrase gap definition and range -------------

# Recall values contributed by families in the per-family map: any float in the
# natural [0.0, 1.0] recall range (including the boundaries).
_pgap_recall = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

# Non-paraphrase family names, distinct from the paraphrase family constant so
# the map always partitions cleanly into paraphrase / non-paraphrase.
_pgap_family_names = st.sampled_from(
    ["injection", "jailbreak", "obfuscation", "leakage", "toxicity", "roleplay"]
)


@st.composite
def _pgap_per_family_recall_map(draw):
    """A per-family recall map with a computable paraphrase recall and >=1
    computable non-paraphrase recall, mixing in skipped NotComputable entries.

    Guarantees the two preconditions of Property 13 (both the paraphrase recall
    and the mean non-paraphrase recall are computable) while also exercising the
    requirement that NotComputable families are skipped.
    """
    per_family: dict[str, float | metrics.NotComputable] = {}

    # Paraphrase recall: always a computable float.
    per_family[metrics.PARAPHRASE_FAMILY] = draw(_pgap_recall)

    # At least one computable non-paraphrase family.
    n_computable = draw(st.integers(min_value=1, max_value=5))
    computable_names = draw(
        st.lists(
            _pgap_family_names,
            min_size=n_computable,
            max_size=n_computable,
            unique=True,
        )
    )
    for name in computable_names:
        per_family[name] = draw(_pgap_recall)

    # Optionally mix in NotComputable non-paraphrase families (must be skipped),
    # using names not already used for a computable family.
    remaining = [
        name
        for name in ["injection", "jailbreak", "obfuscation", "leakage", "toxicity", "roleplay"]
        if name not in computable_names
    ]
    if remaining:
        n_nc = draw(st.integers(min_value=0, max_value=len(remaining)))
        nc_names = draw(
            st.lists(
                st.sampled_from(remaining),
                min_size=n_nc,
                max_size=n_nc,
                unique=True,
            )
        )
        for name in nc_names:
            per_family[name] = metrics.NotComputable(reason="synthetic skip")

    return per_family


def _pgap_expected_gap(per_family):
    """Reference paraphrase gap, computed independently of ``metrics``.

    Mean of the computable non-paraphrase recalls minus the paraphrase recall,
    round-half-up to 4 dp. Caller guarantees both are computable.
    """
    paraphrase = per_family[metrics.PARAPHRASE_FAMILY]
    non_paraphrase = [
        value
        for family, value in per_family.items()
        if family != metrics.PARAPHRASE_FAMILY and isinstance(value, float)
    ]
    mean_non_paraphrase = sum(non_paraphrase) / len(non_paraphrase)
    raw = mean_non_paraphrase - paraphrase
    return float(_independent_round_half_up_4dp(raw))


@settings(max_examples=200)
@given(per_family=_pgap_per_family_recall_map())
def test_paraphrase_gap_definition_and_range(per_family):
    """Feature: posture-scoring, Property 13: For any per-family recall map in
    which the ``paraphrase`` recall and the mean non-paraphrase recall are both
    computable, the reported paraphrase gap equals the mean non-paraphrase recall
    minus the ``paraphrase`` recall, rounded to 4 dp, and lies in the inclusive
    range -1.0 to 1.0.

    Validates: Requirements 4.6
    """
    result = metrics.paraphrase_gap(per_family)

    # Both preconditions are guaranteed by the generator, so the gap is a real
    # number (never NotComputable).
    assert isinstance(result, float)

    # (a) Equals round-half-up(mean non-paraphrase recall - paraphrase recall).
    assert result == _pgap_expected_gap(per_family)

    # (b) Lies in the inclusive range -1.0 to 1.0.
    assert -1.0 <= result <= 1.0


# --- Task 2.10: metrics edge cases (Requirements 2.4, 2.5) --------------------


def test_metrics_edge_recall_zero_malicious_not_computable():
    """recall over a set with 0 malicious items returns NotComputable with the
    reason "malicious count is 0" — not a fabricated number.

    Validates: Requirements 2.4
    """
    scores = [0.9, 0.1, 0.5]
    labels = ["benign", "benign", "other"]  # zero malicious
    result = metrics.recall(scores, labels, threshold=0.5)
    assert isinstance(result, metrics.NotComputable)
    assert result.reason == "malicious count is 0"


def test_metrics_edge_recall_zero_malicious_empty_set():
    """An entirely empty Scored_Set also has 0 malicious → NotComputable."""
    result = metrics.recall([], [], threshold=0.5)
    assert isinstance(result, metrics.NotComputable)
    assert result.reason == "malicious count is 0"


def test_metrics_edge_fpr_zero_benign_not_computable():
    """fpr over a set with 0 benign items returns NotComputable with the reason
    "benign count is 0" — not a fabricated number.

    Validates: Requirements 2.5
    """
    scores = [0.9, 0.1, 0.5]
    labels = ["malicious", "malicious", "other"]  # zero benign
    result = metrics.fpr(scores, labels, threshold=0.5)
    assert isinstance(result, metrics.NotComputable)
    assert result.reason == "benign count is 0"


def test_metrics_edge_fpr_zero_benign_empty_set():
    """An entirely empty Scored_Set also has 0 benign → NotComputable."""
    result = metrics.fpr([], [], threshold=0.5)
    assert isinstance(result, metrics.NotComputable)
    assert result.reason == "benign count is 0"


def test_metrics_edge_flagged_boundary_inclusive():
    """A score exactly equal to the threshold is Flagged_Malicious (>=)."""
    assert metrics.flagged(0.5, 0.5) is True
    assert metrics.flagged(0.0, 0.0) is True
    assert metrics.flagged(1.0, 1.0) is True
    # Strictly below the threshold is not flagged.
    assert metrics.flagged(0.4999, 0.5) is False
    # Strictly above the threshold is flagged.
    assert metrics.flagged(0.5001, 0.5) is True


def test_metrics_edge_recall_boundary_score_equal_threshold_flagged():
    """A malicious item whose score equals the threshold counts as flagged in
    recall (boundary inclusive)."""
    scores = [0.5]
    labels = ["malicious"]
    result = metrics.recall(scores, labels, threshold=0.5)
    assert result == 1.0


def test_metrics_edge_fpr_boundary_score_equal_threshold_flagged():
    """A benign item whose score equals the threshold counts as flagged in fpr
    (boundary inclusive)."""
    scores = [0.5]
    labels = ["benign"]
    result = metrics.fpr(scores, labels, threshold=0.5)
    assert result == 1.0


def test_metrics_edge_recall_values_clamp_within_range():
    """Computable recall always lands in the inclusive range [0.0, 1.0] across
    all-flagged, none-flagged, and mixed cases."""
    # All malicious flagged → 1.0.
    r_all = metrics.recall([0.9, 0.8], ["malicious", "malicious"], threshold=0.5)
    assert r_all == 1.0
    assert 0.0 <= r_all <= 1.0
    # None flagged → 0.0.
    r_none = metrics.recall([0.1, 0.2], ["malicious", "malicious"], threshold=0.5)
    assert r_none == 0.0
    assert 0.0 <= r_none <= 1.0
    # Mixed (1 of 2 flagged) → 0.5.
    r_mixed = metrics.recall([0.9, 0.1], ["malicious", "malicious"], threshold=0.5)
    assert r_mixed == 0.5
    assert 0.0 <= r_mixed <= 1.0


def test_metrics_edge_fpr_values_clamp_within_range():
    """Computable fpr always lands in the inclusive range [0.0, 1.0] across
    all-flagged, none-flagged, and mixed cases."""
    # All benign flagged → 1.0.
    f_all = metrics.fpr([0.9, 0.8], ["benign", "benign"], threshold=0.5)
    assert f_all == 1.0
    assert 0.0 <= f_all <= 1.0
    # None flagged → 0.0.
    f_none = metrics.fpr([0.1, 0.2], ["benign", "benign"], threshold=0.5)
    assert f_none == 0.0
    assert 0.0 <= f_none <= 1.0
    # Mixed (1 of 2 flagged) → 0.5.
    f_mixed = metrics.fpr([0.9, 0.1], ["benign", "benign"], threshold=0.5)
    assert f_mixed == 0.5
    assert 0.0 <= f_mixed <= 1.0

# --- Task 2.8: Property 12 — per-family FPR definition and range --------------

# Families drawn from a small pool so several distinct benign families co-occur
# in a single vector; a non-benign label paired with any of these must never
# contribute to a benign family's FPR.
_pff_families = st.sampled_from(
    ["developer_traffic", "general_benign", "docs", "prompt_injection", "paraphrase"]
)

# Label pool spanning both recognized ground-truth values plus unrecognized /
# absent values, so per-family FPR is exercised with non-benign items (which
# must never count toward any benign family's denominator) interleaved.
_pff_labels = st.sampled_from(["benign", "malicious", "other", "", "BENIGN"])

_pff_score = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

_pff_threshold = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def _pff_aligned_scores_labels_families(draw):
    """Aligned (scores, labels, families) triples with >= 1 benign item.

    Generates positionally-aligned equal-length score, label, and family
    vectors, then injects at least one benign item so at least one Benign_Family
    is present in the result (Property 12 is meaningful when a benign family
    exists).
    """
    n = draw(st.integers(min_value=0, max_value=40))
    scores = draw(st.lists(_pff_score, min_size=n, max_size=n))
    labels = draw(st.lists(_pff_labels, min_size=n, max_size=n))
    families = draw(st.lists(_pff_families, min_size=n, max_size=n))

    # Guarantee >= 1 benign item at a random position so at least one benign
    # family appears in the returned map.
    inject_score = draw(_pff_score)
    inject_family = draw(_pff_families)
    pos = draw(st.integers(min_value=0, max_value=len(scores)))
    scores.insert(pos, inject_score)
    labels.insert(pos, "benign")
    families.insert(pos, inject_family)
    return scores, labels, families


def _pff_independent_family_counts(scores, labels, families, threshold):
    """Reference per-benign-family (flagged, total) counts, independent of metrics.

    For each family, counts only items whose label is exactly ``"benign"``: the
    total benign items in that family and how many of those are at or above the
    threshold. Written as an independent oracle so the test does not restate the
    implementation.
    """
    totals: dict[str, int] = {}
    flagged: dict[str, int] = {}
    for score, label, family in zip(scores, labels, families):
        if label != "benign":
            continue
        totals[family] = totals.get(family, 0) + 1
        if score >= threshold:
            flagged[family] = flagged.get(family, 0) + 1
    return totals, flagged


@settings(max_examples=200)
@given(
    data=_pff_aligned_scores_labels_families(),
    threshold=_pff_threshold,
)
def test_per_family_fpr_definition_and_range(data, threshold):
    """Feature: posture-scoring, Property 12: For any corpus and selected
    threshold, for each benign family present, the per-family FPR equals the count
    of that family's benign items flagged divided by the total count of that
    family's benign items, expressed as a proportion in 0.0 to 1.0 rounded to 4
    dp.

    Validates: Requirements 5.1, 5.2
    """
    scores, labels, families = data
    result = metrics.per_family_fpr(scores, labels, families, threshold)

    totals, flagged = _pff_independent_family_counts(
        scores, labels, families, threshold
    )

    # The result's keys are exactly the benign families present (attack /
    # non-benign families are excluded).
    assert set(result.keys()) == set(totals.keys())

    # At least one benign family is present (>= 1 benign item was injected).
    assert len(result) >= 1

    for family, total in totals.items():
        value = result[family]

        # Every present benign family has total >= 1, so it is computable.
        assert total >= 1
        assert isinstance(value, float)

        # (a) Equals round_metric(count(benign flagged) / count(benign)).
        expected = metrics.round_metric(flagged.get(family, 0) / total)
        assert value == expected

        # (b) Lies in the inclusive range 0.0 to 1.0.
        assert 0.0 <= value <= 1.0
