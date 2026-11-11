"""Single combined-alternation multi-pattern matcher (GW09, §10.9, R3).

This is the one matcher pass GW09 ships (design "matcher.py"). The shipped
ruleset (``detect/deterministic/patterns.py``) carries, per pattern, a cheap
``prefilter`` superset regex and a ``precise`` confirming regex. This module
compiles the prefilters ONCE at import into a SINGLE combined alternation --
``(?P<name1>pf1)|(?P<name2>pf2)|...`` -- so the PRIMARY pass of :func:`scan`
runs exactly one ``re.finditer`` over the canonicalized text (R3.1/R3.2); there
is no per-pattern loop over the whole text. Each prefilter hit is CONFIRMED
against that pattern's ``precise`` regex before a :class:`RawMatch` is emitted
(R3.3): a prefilter-only near-miss emits nothing.

The combined regex is compiled with NO global flags -- each prefilter carries
its own scoped ``(?i:...)`` inline flag where case-insensitivity is wanted, so
combining the branches into one alternation is safe. The pattern names in
``RULESET`` are all ``[a-z_]+`` (valid Python identifiers), so they are used
directly as ``(?P<name>...)`` group names.

Engine choice (R3.4, R11.3): the Python ``re`` engine is the local default.
Hyperscan is a deferred performance optimization, out of GW09's local scope
(``auto`` fallback to ``re`` would lose Unicode word boundaries, so ``re`` is a
deliberate choice, not a fallback).

Confirm + span choice: a prefilter is a cheap superset, but its matched span is
NOT guaranteed to contain the whole precise match -- a non-greedy prefilter can
stop short of the precise match's true end (e.g. the override prefilter stops at
``instruction`` while the precise regex needs ``instructions``). So the precise
confirm is anchored at the prefilter match START in the FULL canonical text
(``precise.match(canonical_text, pos=match.start())``), NEVER with an ``endpos``
clamp, so a ``(?!\\d)`` / ``(?<!\\d)`` at a region edge is read against the real
neighbouring bytes. We then emit the PRECISE span offsets so the span the
redactor later targets is tight.

Validators and failed-region rescan (v2-enforcement-leaks Group B, Property 5,
Property 6). Two facts about the shipped single-pass force more than one confirm
per region:

* A precise confirm can succeed on the digits yet still be wrong -- a 13-19 digit
  run is only a card if it passes the Luhn checksum. ``PatternSpec.validator``
  names a post-confirm check (``"luhn"`` for ``credential_card``); the matcher
  owns the name -> function table :data:`_VALIDATORS`. A failed validator makes
  the confirm NOT a finding, exactly like a failed precise match.
* The primary ``finditer`` is leftmost-first over the combined alternation, so a
  pattern Q whose prefilter is a superset of pattern P's run (``pii_phone`` over a
  card-shaped digit run) consumes the whole region; when Q's confirm or validator
  then fails, P is never tried there. So on a failed confirm OR a failed validator
  over the region ``R = [hit.start, hit.end)`` we RESCAN ``R``: every OTHER pattern
  leftmost-first inside ``R``, and the failed pattern at positions AFTER
  ``hit.start``. A nested failure recurses with the failed set excluded.
* The prefilter is a SUPERSET, so even a SUCCESSFUL confirm can leave an
  over-consumption gap: Q's prefilter hit can end past Q's precise match end
  (``pii_phone`` prefilter ``(?:\\+?\\d[\\s().\\-]?){9,14}\\d`` matches
  ``4155552671 123-45`` while the precise phone is only ``4155552671``), yet
  ``finditer`` resumes at the whole PREFILTER hit end, so a confirmable match of
  another pattern in the trailing gap ``[precise_end, hit.end)`` (the SSN
  ``123-45-6789`` here) is never visited by the primary pass. So after a confirm
  whose ``hit.end > precise_end`` we RESCAN that gap offering EVERY pattern
  (including Q, which can legitimately match again later in the gap); the
  per-pattern cursor already guards Q's emitted span, so no duplicate is produced.

The rescan is bounded. Candidates are gathered per START position with the
once-compiled all-lookahead regex :data:`_ALL_AT`, restricted to the region (at
most the longest bounded prefilter), so overlapping candidates of different
patterns at one position are all surfaced in one leftmost-first pass -- no further
recursion into a failed candidate's own region is needed, because any match that
candidate would have hidden is already a candidate here. The single-exclusion
combined regexes precompiled into :data:`_EXCL` are a cheap existence gate that
skips the per-position walk when only the failed pattern could re-enter the
region. Per-pattern cursors (``_emitted_end``) stop a
value confirmed in both the primary pass and a rescan -- or in two overlapping
rescans -- from being emitted twice. Cost is O(n·k·B) with ``n`` the text length,
``k`` the pattern count and ``B`` the maximum bounded prefilter length; a typical
text with no failed confirms runs one primary ``finditer`` and costs the same as
before. Output is sorted by ``(start, RULESET order)``.

Layering: this module imports only ``gateway_v2.domain`` and the sibling
``gateway_v2.detect.deterministic.patterns`` (RULESET). It mirrors the
``detect/holdback.py`` house idiom: ``from __future__ import annotations``,
``re``, a frozen ``@dataclass(frozen=True, slots=True)``, ``MappingProxyType``
immutable tables, a tuple ``__all__``. The precompiled combined regex, the
precise-regex table, the single-exclusion table, the all-lookahead regex and the
validator table are module-level CONSTANTS assigned once at import (not mutable
state): each table is wrapped in ``MappingProxyType`` so it is immutable.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from gateway_v2.detect.deterministic.patterns import RULESET

__all__ = (
    "RawMatch",
    "scan",
)


@dataclass(frozen=True, slots=True)
class RawMatch:
    """One confirmed match in canonical space.

    ``pattern_name`` is the ``RULESET`` key; ``canonical_start`` /
    ``canonical_end`` are the PRECISE sub-span offsets into the canonical text.
    """

    pattern_name: str
    canonical_start: int
    canonical_end: int


# The ruleset's declaration order, used as the stable tie-break when two matches
# share a start offset (the output sort key, and the leftmost-first candidate
# order inside a rescan region).
_ORDER: Final[Mapping[str, int]] = MappingProxyType(
    {name: i for i, name in enumerate(RULESET)}
)


# One combined alternation over the prefilter supersets, compiled ONCE at import
# (not per call). A named group per pattern lets ``match.lastgroup`` identify
# which pattern fired. No global flags -- each prefilter carries its own scoped
# ``(?i:...)`` where needed, so the branches combine safely. The structure test
# ``_COMBINED.groups == len(RULESET)`` holds: this stays the primary pass.
_COMBINED: Final[re.Pattern[str]] = re.compile(
    "|".join(f"(?P<{name}>{spec.prefilter})" for name, spec in RULESET.items())
)

# Per-pattern precise confirming regexes, precompiled ONCE at import into an
# immutable table so ``scan`` does no per-call compilation (R3.3). Wrapped in
# ``MappingProxyType`` so there is no module-level mutable state (R8.5).
_PRECISE: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {name: re.compile(spec.precise) for name, spec in RULESET.items()}
)

# Per-pattern SINGLE-exclusion combined alternations: ``_EXCL[q]`` is the
# combined prefilter alternation of every pattern EXCEPT ``q``. On the common
# rescan (one failed pattern) it is a cheap EXISTENCE gate: a single precompiled
# ``search`` tells the candidate walk whether any OTHER pattern can start in the
# region at all, so a region that only the failed pattern could re-enter skips the
# per-position lookahead entirely. Compiled ONCE at import, wrapped in
# ``MappingProxyType``.
_EXCL: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {
        excluded: re.compile(
            "|".join(
                f"(?P<{name}>{spec.prefilter})"
                for name, spec in RULESET.items()
                if name != excluded
            )
        )
        for excluded in RULESET
    }
)

# The per-position all-lookahead regex: at any single position it reports, via
# zero-width lookaheads, EVERY pattern whose prefilter can start there. Compiled
# ONCE at import. The candidate walk reads ``match.groupdict()`` at each position
# inside a failed region to find which patterns can begin there; because it is
# anchored per-position, OVERLAPPING candidates of different patterns at one
# position (a card and an SSN on the same digit) are all surfaced and an arbitrary
# exclusion set is handled without recompiling -- a single consuming combined
# ``finditer`` would report only one per position. The region is at most the
# longest bounded prefilter, so this path is bounded.
_ALL_AT: Final[re.Pattern[str]] = re.compile(
    "".join(f"(?=(?P<{name}>{spec.prefilter}))?" for name, spec in RULESET.items())
)


def _luhn_ok(text: str, start: int, end: int) -> bool:
    """The Luhn checksum over the digits of ``text[start:end]`` (separators stripped).

    A card span carries single spaces or dashes between digit groups; those are
    not part of the checksum. An empty digit run (no digits in the span) is not a
    valid card. Pure.
    """
    digits = [ord(ch) - 48 for ch in text[start:end] if ch.isdigit()]
    if not digits:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


# The validator name -> check table. A :class:`PatternSpec` names a validator as a
# string (``"luhn"``), keeping the ruleset plain data and hashing the name into
# ``ruleset_version``; the matcher owns the name -> function mapping. Immutable
# (``MappingProxyType``) so there is no module-level mutable state (R8.5).
_VALIDATORS: Final[Mapping[str, Callable[[str, int, int], bool]]] = MappingProxyType(
    {"luhn": _luhn_ok}
)


def _confirm(text: str, name: str, pos: int) -> tuple[int, int] | None:
    """Confirm pattern ``name`` anchored at ``pos`` in the FULL text, validator applied.

    Returns the PRECISE ``(start, end)`` span on a confirm whose validator (if any)
    passes, else ``None``. The precise regex is matched on the full ``text`` with no
    ``endpos`` clamp, so a lookbehind/lookahead at a region edge is read against the
    real neighbouring bytes. A ``None`` result (failed precise match OR failed
    validator) is the design's failed-confirm region.
    """
    confirmed = _PRECISE[name].match(text, pos)
    if confirmed is None:
        return None
    span = (confirmed.start(), confirmed.end())
    validator = RULESET[name].validator
    if validator is not None and not _VALIDATORS[validator](text, span[0], span[1]):
        return None
    return span


def _emit_confirmed(
    text: str,
    name: str,
    prefilter_end: int,
    span: tuple[int, int],
    out: dict[str, int],
    matches: list[RawMatch],
) -> None:
    """Emit one confirmed ``name`` match and rescan its covered region.

    ``span`` is the PRECISE ``(start, end)``; ``prefilter_end`` is the end of the
    prefilter hit that produced it (the confirm is anchored at the prefilter hit
    START, so ``span[0]`` IS that start). A prefilter is a SUPERSET, so the primary
    ``finditer`` (and any outer rescan) resumes at the whole prefilter hit end and
    NOTHING inside the covered region ``[span[0], max(span[1], prefilter_end))`` is
    otherwise re-examined for a DIFFERENT pattern. A confirmable match of another
    pattern can begin there and extend out of the region -- a card or SSN after
    phone-shaped digits (the trailing gap ``[span[1], prefilter_end)``), or a
    card-shaped digit run that begins INSIDE this value's own bytes and runs past
    its end (a PAN seeded by the last digits of an api-key value). The leftmost-first
    primary pass would silently drop either. So after recording this match and
    advancing the per-pattern cursor we RESCAN the whole covered region offering
    EVERY pattern with no exclusion. ``name`` itself is re-offered but its ``out``
    cursor (now at ``span[1]``) forbids a duplicate or overlapping re-emission, and
    any OTHER pattern whose confirmable match begins in the region is surfaced with
    its true full extent (``_confirm`` runs on the full text). The region is at most
    the bounded prefilter hit length, so this stays O(n·k·B); each emitted value
    advances a cursor and occupies at least one byte, so the recursion terminates.
    Pure apart from appending to ``matches`` and advancing ``out``.
    """
    matches.append(
        RawMatch(pattern_name=name, canonical_start=span[0], canonical_end=span[1])
    )
    out[name] = span[1]
    region_end = max(span[1], prefilter_end)
    if region_end > span[0] + 1:
        # Only surface another-pattern match that EXTENDS BEYOND this value's precise
        # end (``min_end=span[1]``). A match that would lie wholly inside ``name``'s
        # span is legitimately COVERED by it (Property 6's containment arm) and is
        # left unemitted, so the output stays minimal and a benign value that happens
        # to contain a shorter other-pattern shape is not over-reported. A match that
        # runs out of the covered region (the trailing gap after phone-shaped digits,
        # or a PAN/email seeded by this value's own bytes but running past its end) is
        # NOT covered by ``name``, so it is emitted with its true full extent.
        _rescan(text, span[0], region_end, frozenset(), out, matches, min_end=span[1])


def _rescan(
    text: str,
    region_start: int,
    region_end: int,
    excluded: frozenset[str],
    out: dict[str, int],
    matches: list[RawMatch],
    *,
    min_end: int = -1,
) -> None:
    """Rescan ``text[region_start:region_end]`` for matches the primary pass hid.

    The primary ``finditer`` is leftmost-first over one combined alternation, so a
    pattern Q whose prefilter is a superset of a neighbouring run consumes the
    region and, when Q's confirm or validator fails, every OTHER pattern there is
    never tried. This rescan evaluates every pattern NOT in ``excluded`` whose
    prefilter STARTS inside the region, plus each excluded pattern only at
    positions AFTER ``region_start`` (where it has not already failed). Because the
    candidates are gathered PER START POSITION (not by a consuming combined
    ``finditer``), OVERLAPPING candidates of different patterns at one position --
    a card and an SSN that begin on the same digit -- are all surfaced, so one
    leftmost-first pass suffices and no further recursion into a failed candidate's
    own region is needed: any match that candidate would have hidden is already a
    candidate here. Confirms run on the FULL text, so a candidate whose start is in
    the region but whose value extends past ``region_end`` still confirms to its
    true extent. A confirmed candidate whose own prefilter over-consumed past its
    precise end has its trailing gap rescanned by :func:`_emit_confirmed`, exactly
    as the primary pass does: a confirmed phone recovered inside one rescan can
    itself shadow a card or SSN in its prefilter's over-consumption gap. The
    per-pattern ``out`` cursor forbids a duplicate or overlapping same-pattern
    emission across the primary pass and the rescans. ``min_end`` is the covered
    region's upper bound when this is the post-confirm rescan of a winner's own
    span (:func:`_emit_confirmed`): a candidate whose end does not exceed ``min_end``
    lies wholly inside the winner and is legitimately COVERED by it (Property 6's
    containment arm), so it is NOT emitted. A failed-region rescan passes the
    default ``min_end = -1``, emitting every confirmable match. Pure apart from
    appending to ``matches`` and advancing ``out``.
    """
    if region_start >= region_end:
        return
    for name, cand_start in _candidates(text, region_start, region_end, excluded):
        if cand_start < out.get(name, -1):
            # A same-pattern match already covered this start: the per-pattern
            # cursor forbids a duplicate or overlapping emission.
            continue
        span = _confirm(text, name, cand_start)
        if span is None or span[0] < out.get(name, -1):
            continue
        if span[1] <= min_end:
            # The candidate lies wholly inside the covering winner's span: leave it
            # covered (Property 6 containment arm), do not emit or advance a cursor.
            continue
        prefilter_hit = _PREFILTER[name].match(text, cand_start)
        prefilter_end = prefilter_hit.end() if prefilter_hit is not None else span[1]
        _emit_confirmed(text, name, prefilter_end, span, out, matches)


def _candidates(
    text: str,
    region_start: int,
    region_end: int,
    excluded: frozenset[str],
) -> list[tuple[str, int]]:
    """Prefilter START positions inside the region, leftmost-first then RULESET order.

    A region rescan must surface OVERLAPPING candidates of different patterns at
    the same position -- a card and an SSN can both start on the same digit -- so a
    single consuming combined ``finditer`` is not enough. Candidates are gathered
    per position with the once-compiled all-lookahead regex :data:`_ALL_AT`, which
    reports via zero-width lookaheads EVERY pattern whose prefilter can begin at
    that position, overlaps preserved. The single-exclusion precompiled alternation
    :data:`_EXCL` is a cheap existence gate: when exactly one pattern is excluded
    and no OTHER pattern's prefilter matches anywhere in the region, only that
    pattern re-tried after its start can be a candidate, so the per-position walk is
    skipped. A pattern in ``excluded`` is only a candidate at positions AFTER
    ``region_start`` (the most recently excluded pattern failed exactly there).
    Returns ``(name, start)`` sorted leftmost-first then by RULESET order. The
    region is at most the longest bounded prefilter, so the walk is bounded.
    """
    # Each entry is ``(start, RULESET order, name)`` so one sort key orders every
    # candidate leftmost-first then by declaration order.
    found: list[tuple[int, int, str]] = []
    only = next(iter(excluded)) if len(excluded) == 1 else None
    if only is not None and _EXCL[only].search(text, region_start, region_end) is None:
        # Fast path: no OTHER pattern can start in the region. Only the excluded
        # pattern, re-tried at positions AFTER the region start, can still be a
        # candidate. EVERY such start must be surfaced per position (a consuming
        # ``finditer`` would hide a start that an earlier overlapping prefilter hit
        # of the SAME pattern spans past -- e.g. a phone prefilter matching across a
        # digit run hides the real phone start a few bytes on), so a per-position
        # ``match`` is used, exactly like the general per-position walk below.
        found = [
            (pos, _ORDER[only], only)
            for pos in range(region_start + 1, region_end)
            if _PREFILTER[only].match(text, pos) is not None
        ]
        found.sort(key=lambda item: (item[0], item[1]))
        return [(name, start) for start, _, name in found]
    for pos in range(region_start, region_end):
        at = _ALL_AT.match(text, pos)
        if at is None:
            continue
        for name, group in at.groupdict().items():
            if group is None:
                continue
            if name in excluded and pos <= region_start:
                # An excluded pattern is only re-tried AFTER the region start.
                continue
            found.append((pos, _ORDER[name], name))
    found.sort(key=lambda item: (item[0], item[1]))
    return [(name, start) for start, _, name in found]


# Per-pattern prefilter regexes, precompiled ONCE at import. The rescan re-tries a
# single excluded pattern and probes a per-position candidate with these; the
# primary pass and the single-exclusion alternation do not need them, but the
# rescan does. Immutable (``MappingProxyType``).
_PREFILTER: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {name: re.compile(spec.prefilter) for name, spec in RULESET.items()}
)


def scan(canonical_text: str) -> tuple[RawMatch, ...]:
    """Match all deterministic patterns over ``canonical_text``.

    The PRIMARY pass is one :meth:`re.Pattern.finditer` over the combined
    alternation (R3.1/R3.2 -- no per-pattern loop over the whole text). For each
    prefilter hit the firing pattern is identified via ``match.lastgroup``; the hit
    is CONFIRMED by that pattern's precise regex anchored at the prefilter match
    START in the full canonical text, and the pattern's validator (if any) is then
    applied (R3.3). On a confirm whose validator passes a :class:`RawMatch`
    carrying the tight PRECISE span is emitted. On a failed confirm OR a failed
    validator the hit's ``[start, end)`` region is RESCANNED so a match of another
    pattern the leftmost-first primary pass consumed is not lost (Property 6), and
    a card-shaped run that fails Luhn emits nothing (Property 5). On a SUCCESSFUL
    confirm whose prefilter hit over-consumed (``match.end() > precise_end``) the
    trailing gap ``[precise_end, match.end())`` is RESCANNED offering every pattern,
    because ``finditer`` resumes past the whole prefilter hit and would otherwise
    drop a confirmable match of another pattern there (Property 6). Per-pattern
    cursors forbid a duplicate emission across the primary pass and the rescans.
    Output is sorted by ``(start, RULESET order)``. Pure and deterministic.
    """
    matches: list[RawMatch] = []
    emitted_end: dict[str, int] = {}
    # ``examined_to`` is the monotone frontier: positions ``[0, examined_to)`` have
    # been fully examined for EVERY pattern (emitted, or determined to hold no
    # confirmable match). It is NOT a per-pattern cursor -- a per-pattern cursor
    # would let a primary hit whose START sits behind an earlier same-pattern
    # emission be dropped whole, losing the OTHER-pattern matches its (superset)
    # region still covers. Because combined-alternation prefilter hits do not align
    # to value boundaries, a single hit can begin mid-value (confirm fails there)
    # and still cover a fresh value further on; the tail past the frontier must be
    # rescanned, never skipped.
    examined_to = 0
    for match in _COMBINED.finditer(canonical_text):
        name = match.lastgroup
        if name is None:
            continue
        hit_start, hit_end = match.start(), match.end()
        if hit_end <= examined_to:
            # The whole hit region is already examined by an earlier emission or
            # rescan: nothing new can be here.
            continue
        if hit_start < examined_to:
            # The hit starts behind the frontier but reaches past it: rescan only the
            # unexamined tail, offering every pattern (the frontier is not a position
            # where any particular pattern failed, so nothing is excluded there).
            _rescan(
                canonical_text, examined_to, hit_end, frozenset(), emitted_end, matches
            )
            examined_to = hit_end
            continue
        span = _confirm(canonical_text, name, hit_start)
        if span is None:
            # Failed confirm or failed validator: rescan the region for a match of
            # another pattern (or of this one past its start) the primary pass hid.
            _rescan(
                canonical_text,
                hit_start,
                hit_end,
                frozenset({name}),
                emitted_end,
                matches,
            )
            examined_to = hit_end
            continue
        # Emit the confirmed match and, if the prefilter hit over-consumed past the
        # precise end, rescan the trailing gap ``[span[1], hit_end)`` the primary
        # ``finditer`` skips when it resumes at ``hit_end`` (Property 6). A precise
        # match may also run PAST the prefilter hit (a non-greedy prefilter stopping
        # short, e.g. the override prefilter at ``instruction`` while the precise
        # needs ``instructions``), so the frontier advances to the larger of the two.
        _emit_confirmed(canonical_text, name, hit_end, span, emitted_end, matches)
        examined_to = max(hit_end, span[1])
    matches.sort(key=lambda m: (m.canonical_start, _ORDER[m.pattern_name]))
    return tuple(matches)
