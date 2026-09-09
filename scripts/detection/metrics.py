"""Pure metric functions for the posture scoring harness.

Recall, false-positive-rate, per-family breakdowns, the paraphrase gap, and the
round-half-up rounding helper. Side-effect-free and fully property-testable
(Requirements 2, 4, 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

# Every reported metric is rounded to this many decimal places using
# round-half-up (Requirements 2.8, 4.2, 5.2).
_METRIC_DECIMAL_PLACES = 4
_METRIC_QUANTUM = Decimal("0.0001")


def round_metric(value: float) -> float:
    """Round ``value`` to 4 decimal places using round-half-up.

    Uses :class:`decimal.Decimal` with :data:`decimal.ROUND_HALF_UP` rather than
    Python's built-in :func:`round`, which applies banker's (round-half-to-even)
    rounding — so ``round_metric`` reproduces byte-identical values on re-runs
    and always rounds a ``.5`` case away from zero at the 4th decimal place
    (Requirements 2.8, 4.2, 5.2).

    The result carries at most 4 decimal places and the function is idempotent:
    ``round_metric(round_metric(v)) == round_metric(v)`` for any real ``v``.
    """
    # Convert via ``str`` so the exact decimal the caller wrote (e.g. "0.12345")
    # is quantized, not the nearest binary float approximation of it. This makes
    # the half-up decision deterministic at the 4th place.
    quantized = Decimal(str(value)).quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_UP)
    return float(quantized)


# Recognized ground-truth label values, consistent with
# ``scripts/detection/corpus.py`` (MALICIOUS_LABEL / BENIGN_LABEL). Any other
# value — including an absent label — is treated as neither malicious nor benign
# and does not contribute to the malicious or benign counts (Requirement 2.3).
MALICIOUS_LABEL = "malicious"
BENIGN_LABEL = "benign"


@dataclass(frozen=True)
class NotComputable:
    """Sentinel returned in place of a metric value when it cannot be computed.

    A metric is *not computable* when the denominator population is empty — for
    ``recall`` when the Scored_Set has 0 malicious items, and for ``fpr`` when it
    has 0 benign items (Requirements 2.4, 2.5). The harness returns this sentinel
    (carrying a non-empty ``reason``) rather than fabricating a number; the caller
    turns it into an error indication and never reports a metric value for it.
    """

    reason: str


def flagged(score: float, threshold: float) -> bool:
    """Return whether ``score`` is flagged malicious at ``threshold``.

    An item is Flagged_Malicious when its Posture_Score is at or above the
    Decision_Threshold: ``score >= threshold``. A score exactly equal to the
    threshold is flagged (boundary inclusive).
    """
    return score >= threshold


def recall(
    scores: Sequence[float],
    labels: Sequence[str],
    threshold: float,
) -> float | NotComputable:
    """Recall of a Posture over a Scored_Set at ``threshold`` (Requirement 2.1).

    Recall is the count of malicious items that are Flagged_Malicious divided by
    the total count of malicious items in the Scored_Set, a value in the inclusive
    range ``[0.0, 1.0]``. Classification is solely from the ``labels`` field values
    (``"malicious"``); no score is ever used to derive a label.

    When the Scored_Set contains 0 malicious items the metric is undefined, so a
    :class:`NotComputable` sentinel with reason ``"malicious count is 0"`` is
    returned rather than a number (Requirement 2.4).

    ``scores`` and ``labels`` are positionally aligned; the shorter length bounds
    the pairs considered.
    """
    total = 0
    flagged_count = 0
    for score, label in zip(scores, labels):
        if label == MALICIOUS_LABEL:
            total += 1
            if flagged(score, threshold):
                flagged_count += 1

    if total == 0:
        return NotComputable(reason="malicious count is 0")
    return flagged_count / total


def fpr(
    scores: Sequence[float],
    labels: Sequence[str],
    threshold: float,
) -> float | NotComputable:
    """False-positive-rate of a Posture over a Scored_Set at ``threshold`` (R2.2).

    FPR is the count of benign items that are Flagged_Malicious divided by the
    total count of benign items in the Scored_Set, a value in the inclusive range
    ``[0.0, 1.0]``. Classification is solely from the ``labels`` field values
    (``"benign"``); no score is ever used to derive a label.

    When the Scored_Set contains 0 benign items the metric is undefined, so a
    :class:`NotComputable` sentinel with reason ``"benign count is 0"`` is returned
    rather than a number (Requirement 2.5).

    ``scores`` and ``labels`` are positionally aligned; the shorter length bounds
    the pairs considered.
    """
    total = 0
    flagged_count = 0
    for score, label in zip(scores, labels):
        if label == BENIGN_LABEL:
            total += 1
            if flagged(score, threshold):
                flagged_count += 1

    if total == 0:
        return NotComputable(reason="benign count is 0")
    return flagged_count / total


# The distinguished Attack_Family whose recall gap against the other attack
# families is reported separately (Requirement 4.6). Kept as a module constant so
# ``paraphrase_gap`` and any caller reference the same family name.
PARAPHRASE_FAMILY = "paraphrase"


def per_family_recall(
    scores: Sequence[float],
    labels: Sequence[str],
    families: Sequence[str],
    threshold: float,
) -> dict[str, float | NotComputable]:
    """Per-family Recall over the malicious Corpus_Items at ``threshold`` (R4.1, 4.2).

    Groups the malicious items (``label == "malicious"``) by their ``families``
    value and, for each Attack_Family present, returns the count of that family's
    malicious items that are Flagged_Malicious divided by the total count of that
    family's malicious items, a value in the inclusive range ``[0.0, 1.0]`` rounded
    to 4 decimal places via :func:`round_metric` (Requirement 4.2).

    A family that has 0 malicious items in the Scored_Set maps to a
    :class:`NotComputable` sentinel rather than a number (Requirement 4.5 at the
    reporting boundary); such a family only appears here if it labels a non-malicious
    item, so in practice every returned malicious family is computable. Only families
    of malicious items are keys of the result — benign families are excluded.

    ``scores``, ``labels`` and ``families`` are positionally aligned; the shortest
    length bounds the triples considered. Pure: no side effects.
    """
    totals: dict[str, int] = {}
    flagged_counts: dict[str, int] = {}
    for score, label, family in zip(scores, labels, families):
        if label != MALICIOUS_LABEL:
            continue
        totals[family] = totals.get(family, 0) + 1
        if flagged(score, threshold):
            flagged_counts[family] = flagged_counts.get(family, 0) + 1

    result: dict[str, float | NotComputable] = {}
    for family, total in totals.items():
        if total == 0:
            result[family] = NotComputable(
                reason=f"malicious count is 0 for family {family!r}"
            )
        else:
            result[family] = round_metric(flagged_counts.get(family, 0) / total)
    return result


def per_family_fpr(
    scores: Sequence[float],
    labels: Sequence[str],
    families: Sequence[str],
    threshold: float,
) -> dict[str, float | NotComputable]:
    """Per-family False_Positive_Rate over the benign Corpus_Items (R5.1, 5.2).

    Groups the benign items (``label == "benign"``) by their ``families`` value and,
    for each Benign_Family present, returns the count of that family's benign items
    that are Flagged_Malicious divided by the total count of that family's benign
    items, expressed as a proportion in the inclusive range ``[0.0, 1.0]`` rounded to
    4 decimal places via :func:`round_metric` (Requirement 5.2).

    A family that has 0 benign items in the Scored_Set maps to a
    :class:`NotComputable` sentinel rather than a number (Requirement 5.5 at the
    reporting boundary). Only families of benign items are keys of the result —
    attack families are excluded.

    ``scores``, ``labels`` and ``families`` are positionally aligned; the shortest
    length bounds the triples considered. Pure: no side effects.
    """
    totals: dict[str, int] = {}
    flagged_counts: dict[str, int] = {}
    for score, label, family in zip(scores, labels, families):
        if label != BENIGN_LABEL:
            continue
        totals[family] = totals.get(family, 0) + 1
        if flagged(score, threshold):
            flagged_counts[family] = flagged_counts.get(family, 0) + 1

    result: dict[str, float | NotComputable] = {}
    for family, total in totals.items():
        if total == 0:
            result[family] = NotComputable(
                reason=f"benign count is 0 for family {family!r}"
            )
        else:
            result[family] = round_metric(flagged_counts.get(family, 0) / total)
    return result


def paraphrase_gap(
    per_family_recall: dict[str, float | NotComputable],
) -> float | NotComputable:
    """Recall gap between the non-paraphrase families and ``paraphrase`` (R4.6).

    Given a :func:`per_family_recall` map, computes the mean of the computable
    non-``paraphrase`` Attack_Family recalls minus the ``paraphrase`` recall,
    rounded to 4 decimal places via :func:`round_metric`, a value in the inclusive
    range ``[-1.0, 1.0]`` (each recall is in ``[0.0, 1.0]``, so their difference is
    in ``[-1.0, 1.0]``).

    The gap is only defined when BOTH the ``paraphrase`` recall and the mean
    non-paraphrase recall are computable (Requirement 4.6). Returns a
    :class:`NotComputable` sentinel — rather than a number — when:

    * the ``paraphrase`` family is absent or maps to :class:`NotComputable`; or
    * there are no computable non-paraphrase family recalls (so their mean is
      undefined).

    Only real ``float`` recall values contribute to the non-paraphrase mean; any
    :class:`NotComputable` family is skipped. Pure: no side effects.
    """
    paraphrase = per_family_recall.get(PARAPHRASE_FAMILY)
    if not isinstance(paraphrase, float):
        return NotComputable(
            reason=f"{PARAPHRASE_FAMILY!r} recall is not computable"
        )

    non_paraphrase = [
        value
        for family, value in per_family_recall.items()
        if family != PARAPHRASE_FAMILY and isinstance(value, float)
    ]
    if not non_paraphrase:
        return NotComputable(
            reason="mean non-paraphrase recall is not computable"
        )

    mean_non_paraphrase = sum(non_paraphrase) / len(non_paraphrase)
    return round_metric(mean_non_paraphrase - paraphrase)
