"""Template rendering and edit normalisation for REWRITE (GW33 C2, D-4, D-7). Pure.

Imports only `gateway_v2.domain`. `render_template` substitutes exactly two tokens by
`str.replace`; the compiler (`plan/rewrite_template.py`) refuses any other brace so the rendering is
total. `normalise_edits` makes the edit set pairwise non-overlapping: any overlap component whose
edits differ in kind or replacement collapses to ONE redact over the union (the security-preserving
outcome) and the code `edit_overlap_redacted` is reported.
"""
from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.domain import Category, Span, Transformation, posture

__all__ = ("normalise_edits", "render_template")


def render_template(template: str, category: Category, rule_id: str) -> str:
    """Substitute `{category}` and `{rule_id}` (and nothing else) into ``template``."""
    return template.replace("{category}", category.value).replace("{rule_id}", rule_id)


def _overlaps(end: int, nxt: Transformation, current: Transformation) -> bool:
    """Same predicate as the GW09 ``_mergeable``: share a byte, or identical spans."""
    return nxt.span == current.span or nxt.span.start < end


def normalise_edits(
    transformations: Sequence[Transformation],
    placeholder: str,
) -> tuple[tuple[Transformation, ...], tuple[str, ...]]:
    """Return ``(edits, codes)`` with ``edits`` pairwise non-overlapping (D-7).

    Components are overlap-connected runs after sorting by span. A component of one edit is kept; a
    component whose edits share ``kind`` and ``replacement`` becomes one edit of that kind over the
    union; any other component becomes one ``redact`` over the union with ``placeholder`` and adds
    ``edit_overlap_redacted``. Touching spans never merge. With no multi-edit component the input
    order is returned unchanged, so non-overlapping decisions are identical to the pre-change ones.
    """
    ordered = sorted(transformations, key=lambda t: (t.span.start, t.span.end))
    groups: list[list[Transformation]] = []
    group_end = 0
    for item in ordered:
        if groups and _overlaps(group_end, item, groups[-1][-1]):
            groups[-1].append(item)
            group_end = max(group_end, item.span.end)
        else:
            groups.append([item])
            group_end = item.span.end
    if all(len(group) == 1 for group in groups):
        return tuple(transformations), ()
    out: list[Transformation] = []
    overlap = False
    for group in groups:
        if len(group) == 1:
            out.append(group[0])
            continue
        span = Span(min(t.span.start for t in group), max(t.span.end for t in group))
        first = group[0]
        if all(t.kind == first.kind and t.replacement == first.replacement for t in group):
            out.append(Transformation(first.kind, span, first.replacement))
        else:
            overlap = True
            out.append(Transformation("redact", span, placeholder))
    return tuple(out), ((posture.EDIT_OVERLAP_REDACTED,) if overlap else ())
