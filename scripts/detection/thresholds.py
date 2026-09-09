"""Deterministic threshold selection for the posture scoring harness.

Candidate thresholds, the recall-maximising selection rule subject to
FPR <= target, tie-breaks, and the FPR floor. Pure functions over the train
split only, so no evaluation leakage occurs (Requirements 3, 7).

This module is intentionally self-contained: it computes recall and FPR
internally (via :func:`_recall` / :func:`_fpr`) rather than importing from
``metrics.py``, so it stays importable and usable standalone even before the
metrics helpers land. The flag/recall/FPR definitions here match the design's
``metrics.py`` contract exactly (``flagged`` = ``score >= threshold``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# The maximum benign false-positive rate defining the headline operating point,
# fixed at 1% (Requirement 3.1).
TARGET_FPR = 0.01

# A False_Positive_Rate is treated as at most the Target_FPR when it does not
# exceed the Target_FPR by more than this tolerance (Requirement 3.2).
FPR_TOLERANCE = 1e-6

# Recognized ground-truth label values, read only from the corpus label field.
_MALICIOUS_LABEL = "malicious"
_BENIGN_LABEL = "benign"


@dataclass(frozen=True)
class ThresholdChoice:
    """The outcome of the Threshold_Selection_Rule over a train score/label set.

    ``selected_threshold`` is the Decision_Threshold chosen to realise the
    headline operating point; ``recall`` and ``achieved_fpr`` are recomputed at
    that threshold; ``target_achievable`` is ``True`` when at least one candidate
    threshold satisfies ``fpr <= TARGET_FPR + FPR_TOLERANCE`` (and a FPR is
    computable), ``False`` otherwise (Requirements 3.2, 3.3, 3.4, 3.5).
    """

    selected_threshold: float
    recall: float
    achieved_fpr: float
    target_achievable: bool


@dataclass(frozen=True)
class FloorChoice:
    """The honest FPR-floor report when the Target_FPR is not achievable (R3.5).

    ``fpr_floor`` is the lowest False_Positive_Rate achievable over the candidate
    thresholds (the distinct train scores); ``floor_threshold`` is the
    Decision_Threshold that realises that floor and ``recall_at_floor`` is the
    Recall recomputed at that threshold. This is reported *instead of* a
    ``Recall_At_Target_FPR`` value whenever ``select_threshold`` returns
    ``target_achievable = False`` (Requirement 3.5).

    ``computable`` is ``True`` when a False_Positive_Rate could be computed at
    all (i.e. the score/label set contains at least one benign item and at least
    one candidate threshold). When there are no benign items — so no FPR (and
    hence no floor) can be computed — ``computable`` is ``False``, ``fpr_floor``
    is ``None``, and ``recall_at_floor`` is still reported at a deterministic
    fallback threshold so the caller has an honest, non-fabricated value to
    surface.
    """

    fpr_floor: float | None
    floor_threshold: float
    recall_at_floor: float
    computable: bool


def _flagged(score: float, threshold: float) -> bool:
    """A Posture_Score at or above the Decision_Threshold is Flagged_Malicious."""
    return score >= threshold


def _recall(
    scores: Sequence[float], labels: Sequence[str], threshold: float
) -> float | None:
    """Fraction of malicious items flagged at ``threshold``; ``None`` if no malicious.

    Returns a value in the inclusive range ``[0.0, 1.0]``. Returns ``None`` when
    the score/label set contains 0 malicious items (recall not computable).
    """
    total = 0
    hit = 0
    for score, label in zip(scores, labels):
        if label == _MALICIOUS_LABEL:
            total += 1
            if _flagged(score, threshold):
                hit += 1
    if total == 0:
        return None
    return hit / total


def _fpr(
    scores: Sequence[float], labels: Sequence[str], threshold: float
) -> float | None:
    """Fraction of benign items flagged at ``threshold``; ``None`` if no benign.

    Returns a value in the inclusive range ``[0.0, 1.0]``. Returns ``None`` when
    the score/label set contains 0 benign items (FPR not computable).
    """
    total = 0
    hit = 0
    for score, label in zip(scores, labels):
        if label == _BENIGN_LABEL:
            total += 1
            if _flagged(score, threshold):
                hit += 1
    if total == 0:
        return None
    return hit / total


def candidate_thresholds(train_scores: Sequence[float]) -> list[float]:
    """The candidate Decision_Thresholds for threshold selection (Requirement 3.6).

    The candidates are exactly the *distinct* posture scores observed over the
    train scores. Using each distinct observed score as a threshold represents
    every reachable operating point exactly once (``score >= threshold`` with
    ``threshold`` equal to an observed score is the coarsest cutoff that still
    flags that score) and evaluates no threshold not reachable from an observed
    score (Requirements 3.6, 3.7).

    Returned in ascending order for determinism. An empty ``train_scores`` yields
    an empty candidate list.
    """
    return sorted(set(train_scores))


def select_threshold(
    train_scores: Sequence[float], train_labels: Sequence[str]
) -> ThresholdChoice:
    """Select the Selected_Threshold per the Threshold_Selection_Rule.

    Over the candidate thresholds (distinct train scores), choose the threshold
    that maximises Recall subject to ``fpr <= TARGET_FPR + FPR_TOLERANCE``.
    Tie-break: among the recall-maximal feasible candidates pick the lowest FPR,
    and among those pick the highest threshold value. This makes the choice a
    deterministic pure function of the (train) score/label multiset — identical
    inputs yield the identical Selected_Threshold (Requirements 3.2, 3.3, 3.4).

    When no candidate satisfies the FPR constraint (or the FPR is not computable
    because there are no benign items), ``target_achievable`` is ``False`` and
    the returned threshold is the one realising the lowest achievable FPR (the
    FPR floor); the honest FPR-floor reporting is finalised in :func:`fpr_floor`
    (Task 3.4 / Requirement 3.5). The reported ``recall``/``achieved_fpr`` are
    recomputed at the returned threshold.
    """
    candidates = candidate_thresholds(train_scores)

    if not candidates:
        # No observed scores → no reachable operating point. Report a degenerate,
        # deterministic choice with the target unachievable.
        return ThresholdChoice(
            selected_threshold=0.0,
            recall=0.0,
            achieved_fpr=0.0,
            target_achievable=False,
        )

    # Precompute (threshold, recall, fpr) at every candidate. recall/fpr are
    # None only when the corresponding class has 0 items.
    evaluated: list[tuple[float, float, float | None]] = []
    for threshold in candidates:
        rec = _recall(train_scores, train_labels, threshold)
        fp = _fpr(train_scores, train_labels, threshold)
        evaluated.append((threshold, rec if rec is not None else 0.0, fp))

    # Feasible = FPR computable and within target + tolerance.
    feasible = [
        (threshold, rec, fp)
        for (threshold, rec, fp) in evaluated
        if fp is not None and fp <= TARGET_FPR + FPR_TOLERANCE
    ]

    if feasible:
        # Maximise recall; tie-break lowest FPR, then highest threshold.
        # sort key: (-recall, fpr, -threshold) so the first element is the pick.
        best = min(
            feasible,
            key=lambda item: (-item[1], item[2], -item[0]),
        )
        threshold, rec, fp = best
        return ThresholdChoice(
            selected_threshold=threshold,
            recall=rec,
            achieved_fpr=fp if fp is not None else 0.0,
            target_achievable=True,
        )

    # Target not achievable. Choose the threshold realising the lowest achievable
    # FPR (the floor). If FPR is not computable anywhere (no benign items), fall
    # back to the highest candidate threshold deterministically.
    computable = [
        (threshold, rec, fp)
        for (threshold, rec, fp) in evaluated
        if fp is not None
    ]
    if computable:
        # Lowest FPR; tie-break highest threshold (matches the selection rule's
        # secondary/tertiary ordering).
        floor = min(computable, key=lambda item: (item[2], -item[0]))
        threshold, rec, fp = floor
        return ThresholdChoice(
            selected_threshold=threshold,
            recall=rec,
            achieved_fpr=fp if fp is not None else 0.0,
            target_achievable=False,
        )

    # No benign items at all → FPR uncomputable everywhere. Deterministic fallback.
    threshold = candidates[-1]
    rec = _recall(train_scores, train_labels, threshold)
    return ThresholdChoice(
        selected_threshold=threshold,
        recall=rec if rec is not None else 0.0,
        achieved_fpr=0.0,
        target_achievable=False,
    )


def fpr_floor(
    train_scores: Sequence[float], train_labels: Sequence[str]
) -> FloorChoice:
    """Report the honest FPR floor over the candidate thresholds (Requirement 3.5).

    Returns the lowest achievable False_Positive_Rate across the candidate
    Decision_Thresholds (the distinct train scores) together with the
    Decision_Threshold realising that floor and the Recall recomputed at that
    threshold. This is what the caller surfaces *instead of* a
    ``Recall_At_Target_FPR`` whenever :func:`select_threshold` reports
    ``target_achievable = False`` — either because no candidate meets the target
    (the floor exceeds ``TARGET_FPR``) or because there are no benign items
    (no FPR, hence no floor, is computable).

    The floor is selected deterministically with the same ordering the
    Threshold_Selection_Rule uses for its fallback: lowest FPR first, then
    highest threshold among ties (Requirement 3.3). This makes ``fpr_floor`` a
    pure function of the (train) score/label multiset — identical inputs yield
    the identical ``FloorChoice``.

    Edge cases:

    - No candidate thresholds (empty ``train_scores``): there is no reachable
      operating point, so no FPR is computable. Returns a degenerate,
      deterministic ``FloorChoice`` with ``computable = False``, ``fpr_floor =
      None``, ``floor_threshold = 0.0``, and ``recall_at_floor = 0.0``.
    - No benign items (FPR uncomputable at every candidate): returns
      ``computable = False`` and ``fpr_floor = None``, with a deterministic
      fallback ``floor_threshold`` (the highest candidate) and the Recall there,
      so the report carries an honest recall value rather than a fabricated FPR.
    """
    candidates = candidate_thresholds(train_scores)

    if not candidates:
        return FloorChoice(
            fpr_floor=None,
            floor_threshold=0.0,
            recall_at_floor=0.0,
            computable=False,
        )

    # (threshold, recall, fpr) at every candidate; recall/fpr are None only when
    # the corresponding class has 0 items.
    evaluated: list[tuple[float, float, float | None]] = []
    for threshold in candidates:
        rec = _recall(train_scores, train_labels, threshold)
        fp = _fpr(train_scores, train_labels, threshold)
        evaluated.append((threshold, rec if rec is not None else 0.0, fp))

    computable = [
        (threshold, rec, fp)
        for (threshold, rec, fp) in evaluated
        if fp is not None
    ]

    if computable:
        # Lowest FPR; tie-break highest threshold (matches select_threshold's
        # floor-branch ordering, R3.3).
        threshold, rec, fp = min(computable, key=lambda item: (item[2], -item[0]))
        return FloorChoice(
            fpr_floor=fp,
            floor_threshold=threshold,
            recall_at_floor=rec,
            computable=True,
        )

    # No benign items anywhere → FPR uncomputable. Deterministic fallback to the
    # highest candidate threshold (matches select_threshold's no-benign branch);
    # report the recall there but no floor value.
    threshold = candidates[-1]
    rec = _recall(train_scores, train_labels, threshold)
    return FloorChoice(
        fpr_floor=None,
        floor_threshold=threshold,
        recall_at_floor=rec if rec is not None else 0.0,
        computable=False,
    )
