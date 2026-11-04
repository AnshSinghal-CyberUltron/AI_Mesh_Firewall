"""Byte verification of `kind="rewrite"` edits (GW33 C5, requirement 5.7).

`apply_decision` calls `verify_rewrites` only when the released slice holds at least one rewrite
edit, so a decision with none follows the exact pre-change code path. Fails closed by raising
`RewriteVerificationFailure`; the stream and strict pipelines map it to `rewrite_unverified`.

Checks, for the in-slice edits sorted by start:
* coverage and overlap (second line of defence: `apply_decision` raises first, D-7 makes overlap
  unreachable from the resolver);
* every rewrite replacement sits in `out` exactly at its expected offset (the span start shifted by
  the summed length change of the earlier edits);
* survivor check (L-2 narrowed): the *retained segments* are the maximal pieces of `out` between
  replacement texts. For a rewrite whose original length is at least `survivor_min_chars`, the
  original bytes must not occur inside a single retained segment. A coincidental concatenation
  across an edit boundary spans a replacement, so it can never match, and shorter originals are
  exempt (the exact coverage check already holds them).
Imports `domain` and `runtime.resources_output` only.
"""
from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.domain import Decision
from gateway_v2.egress.output_errors import RewriteVerificationFailure
from gateway_v2.runtime.resources_output import DEFAULT_REWRITE_SURVIVOR_MIN_CHARS

__all__ = ("RewriteCheck", "has_rewrite_edit", "verify_rewrites")

_REWRITE = "rewrite"


@dataclass(frozen=True, slots=True)
class RewriteCheck:
    """Result carrier for tests and metrics. No content."""

    verified: bool
    edits: int


def has_rewrite_edit(decision: Decision, base_offset: int, length: int) -> bool:
    """Whether any rewrite transformation touches ``[base_offset, base_offset + length)``."""
    for item in decision.transformations:
        if item.kind != _REWRITE:
            continue
        if item.span.end - base_offset > 0 and item.span.start - base_offset < length:
            return True
    return False


def verify_rewrites(
    released: str,
    out: str,
    decision: Decision,
    base_offset: int,
    *,
    survivor_min_chars: int = DEFAULT_REWRITE_SURVIVOR_MIN_CHARS,
) -> RewriteCheck:
    """Verify ``out`` is ``released`` with every in-slice edit applied; raise on any miss."""
    length = len(released)
    edits: list[tuple[int, int, str, str]] = []
    for item in decision.transformations:
        start = item.span.start - base_offset
        end = item.span.end - base_offset
        if end <= 0 or start >= length:
            continue
        if not 0 <= start <= end <= length:
            raise RewriteVerificationFailure("rewrite span is not covered by the released slice")
        edits.append((start, end, item.replacement, item.kind))
    edits.sort(key=lambda item: (item[0], item[1]))
    shift = 0
    cursor = 0
    cursor_out = 0
    rewrites: list[tuple[int, int]] = []
    segments: list[str] = []
    for start, end, replacement, kind in edits:
        if start < cursor:
            raise RewriteVerificationFailure("overlapping edits in released slice")
        expected = start + shift
        if out[expected : expected + len(replacement)] != replacement:
            raise RewriteVerificationFailure("replacement is not at its expected offset")
        segments.append(out[cursor_out:expected])
        if kind == _REWRITE:
            rewrites.append((start, end))
        shift += len(replacement) - (end - start)
        cursor = end
        cursor_out = expected + len(replacement)
    segments.append(out[cursor_out:])
    if "".join(segments) != _retained(released, edits):
        raise RewriteVerificationFailure("retained bytes differ from the released slice")
    for start, end in rewrites:
        if end - start < survivor_min_chars:
            continue
        original = released[start:end]
        if any(original in segment for segment in segments):
            raise RewriteVerificationFailure("original bytes survive a rewrite")
    return RewriteCheck(verified=True, edits=len(rewrites))


def _retained(released: str, edits: list[tuple[int, int, str, str]]) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end, _replacement, _kind in edits:
        pieces.append(released[cursor:start])
        cursor = end
    pieces.append(released[cursor:])
    return "".join(pieces)
