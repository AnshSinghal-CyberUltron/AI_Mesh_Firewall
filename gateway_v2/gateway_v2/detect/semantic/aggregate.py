"""The declared Aggregation_Rule for semantic detection (GW10) — pure.

The GW08 ``GuardBackend`` classifies the ``W`` windows of a request independently
and returns a per-window ``GuardResult[]``. GW10's declared **Aggregation_Rule**
folds that per-window sequence into the request-level semantic result(s)
(Requirement 2.3, design §"One classify call + aggregation", Property 9):

* **Max-score-per-category across windows.** For each semantic category the
  request-level score is the MAXIMUM ``GuardResult.score`` over all windows for
  that category. An injection present in any single window must surface at the
  request level; a mean/min would let one hostile window be diluted by benign
  ones — the opposite of the card's "a planted middle-window miss is a failure"
  stance.
* **Owning-window Span.** The emitted ``Span`` is the offset of the window that
  produced that maximum, mapped back through the ``WindowPlan.spans`` map built by
  :mod:`gateway_v2.detect.semantic.windowing` (``spans[window_index]``). On a tie
  the FIRST window reaching the maximum owns the span, so the fold is
  deterministic (Property 8).
* **Semantic categories only.** Emitted semantic categories are restricted to
  ``Category.PROMPT_INJECTION`` and ``Category.JAILBREAK`` (Requirement 2.5). A
  ``GuardResult`` whose category is outside that semantic set is NOT emitted. Each
  ``GuardResult.category`` string is validated with ``domain.category.parse_category``
  (which rejects non-members) before the semantic-set membership check, so an
  unknown spelling can never be mistaken for a semantic category.

Everything here is a PURE, deterministic function of its inputs: no clock, no I/O,
no mutation of the ``GuardResult`` sequence or the ``WindowPlan``. The shipped GW08
``GuardResult`` and the shipped ``domain`` types (``Category``, ``Span``,
``parse_category``) are used, never redefined.

Layering: imports only the standard library, ``gateway_v2.domain`` (``Category``,
``Span``, ``parse_category``), and the GW08 ``detect`` sibling
``detect.guard.backend`` (``GuardResult``). It never imports a higher layer
(``edge`` / ``admit`` / ``plan``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from gateway_v2.detect.guard.backend import GuardResult
from gateway_v2.domain import Category, Span, parse_category

__all__ = (
    "SEMANTIC_CATEGORIES",
    "AggregatedCategory",
    "aggregate_results",
)

#: The semantic categories a GW10 finding may carry (Requirement 2.5). A
#: ``GuardResult`` whose parsed category is outside this frozenset is not emitted.
SEMANTIC_CATEGORIES: frozenset[Category] = frozenset(
    {Category.PROMPT_INJECTION, Category.JAILBREAK},
)


@dataclass(frozen=True, slots=True)
class AggregatedCategory:
    """One request-level semantic result after the max-score-per-category fold.

    ``category`` — a semantic ``Category`` (``PROMPT_INJECTION`` or ``JAILBREAK``).
    ``score`` — the MAXIMUM window score for that category across all windows.
    ``window_index`` — the index of the window that produced that maximum (the
        first such window on a tie), used to map back to its span.
    ``span`` — the ``Span`` of the owning window in the original input, taken from
        ``WindowPlan.spans[window_index]``.
    """

    category: Category
    score: float
    window_index: int
    span: Span


def aggregate_results(
    results: Sequence[GuardResult],
    spans: Sequence[Span],
) -> tuple[AggregatedCategory, ...]:
    """Fold per-window ``GuardResult``s into request-level per-category results.

    The declared Aggregation_Rule (Property 9): for each semantic category present
    in ``results`` the request-level score is the maximum ``GuardResult.score`` over
    all windows for that category, and the emitted ``Span`` is the offset of the
    window that produced that maximum, mapped through ``spans[window_index]``.

    Each ``GuardResult.category`` string is validated with ``parse_category``; a
    result whose category is not a semantic category (``PROMPT_INJECTION`` /
    ``JAILBREAK``) is skipped and never emitted (Requirement 2.5). ``parse_category``
    raises on an unknown spelling, so a malformed backend category is surfaced
    rather than silently dropped.

    Pure and deterministic: it does not mutate ``results`` or ``spans``, and on a
    score tie the FIRST window reaching the maximum owns the span. The returned
    tuple is ordered by first appearance of each semantic category in ``results``,
    so the output is a stable function of the input order.

    ``spans`` is the ``WindowPlan.spans`` map; ``results[i].window_index`` must index
    into it. A ``window_index`` outside ``spans`` raises ``IndexError`` — a
    malformed backend/window pairing is a bug, never a silently dropped finding.
    """

    order: list[Category] = []
    best: dict[Category, AggregatedCategory] = {}

    for result in results:
        category = parse_category(result.category)
        if category not in SEMANTIC_CATEGORIES:
            continue

        span = spans[result.window_index]
        current = best.get(category)
        if current is None:
            order.append(category)
            best[category] = AggregatedCategory(
                category=category,
                score=result.score,
                window_index=result.window_index,
                span=span,
            )
        elif result.score > current.score:
            # Strict ">" keeps the FIRST window on a tie — deterministic.
            best[category] = AggregatedCategory(
                category=category,
                score=result.score,
                window_index=result.window_index,
                span=span,
            )

    return tuple(best[category] for category in order)
