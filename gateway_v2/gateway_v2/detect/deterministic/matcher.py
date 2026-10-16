"""Single combined-alternation multi-pattern matcher (GW09, §10.9, R3).

This is the one matcher pass GW09 ships (design "matcher.py"). The shipped
ruleset (``detect/deterministic/patterns.py``) carries, per pattern, a cheap
``prefilter`` superset regex and a ``precise`` confirming regex. This module
compiles the prefilters ONCE at import into a SINGLE combined alternation --
``(?P<name1>pf1)|(?P<name2>pf2)|...`` -- so :func:`scan` runs exactly one
``re.finditer`` over the canonicalized text (R3.1/R3.2); there is no per-pattern
loop over the text. Each prefilter hit is CONFIRMED against that pattern's
``precise`` regex before a :class:`RawMatch` is emitted (R3.3): a
prefilter-only near-miss emits nothing.

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
(``precise.match(canonical_text, pos=match.start())``), not restricted to the
prefilter substring; this both confirms the hit (R3.3) and yields the precise
match's true extent. We then emit the PRECISE span offsets so the span the
redactor later targets is tight -- it covers exactly the sensitive value the
precise regex confirmed, not the wider prefilter window. A prefilter-only
near-miss (no precise match anchored there) emits nothing.

Layering: this module imports only ``gateway_v2.domain`` and the sibling
``gateway_v2.detect.deterministic.patterns`` (RULESET). It mirrors the
``detect/holdback.py`` house idiom: ``from __future__ import annotations``,
``re``, a frozen ``@dataclass(frozen=True, slots=True)``, a
``MappingProxyType`` immutable table, a tuple ``__all__``. The precompiled
combined regex and the precise-regex table are module-level CONSTANTS assigned
once at import (not mutable state): the table is wrapped in ``MappingProxyType``
so it is immutable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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


# One combined alternation over the prefilter supersets, compiled ONCE at import
# (not per call). A named group per pattern lets ``match.lastgroup`` identify
# which pattern fired. No global flags -- each prefilter carries its own scoped
# ``(?i:...)`` where needed, so the branches combine safely.
_COMBINED: Final[re.Pattern[str]] = re.compile(
    "|".join(f"(?P<{name}>{spec.prefilter})" for name, spec in RULESET.items())
)

# Per-pattern precise confirming regexes, precompiled ONCE at import into an
# immutable table so ``scan`` does no per-call compilation (R3.3). Wrapped in
# ``MappingProxyType`` so there is no module-level mutable state (R8.5).
_PRECISE: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {name: re.compile(spec.precise) for name, spec in RULESET.items()}
)


def scan(canonical_text: str) -> tuple[RawMatch, ...]:
    """Match all deterministic patterns in ONE pass over ``canonical_text``.

    A single :meth:`re.Pattern.finditer` over the combined alternation (R3.1/
    R3.2 -- no per-pattern loop over the text). For each prefilter hit the firing
    pattern is identified via ``match.lastgroup``; the hit is CONFIRMED by that
    pattern's precise regex anchored at the prefilter match START in the full
    canonical text (R3.3). Only on a confirm is a :class:`RawMatch` emitted,
    carrying the tight PRECISE span offsets. A prefilter-only near-miss (no
    precise match anchored there) emits nothing. Pure and deterministic.
    """
    matches: list[RawMatch] = []
    for match in _COMBINED.finditer(canonical_text):
        name = match.lastgroup
        if name is None:
            continue
        precise = _PRECISE[name]
        # Anchor the precise confirm at the prefilter match start in the FULL
        # text: the precise match may extend beyond the (possibly non-greedy)
        # prefilter span, so restricting to match.group() would drop valid hits.
        confirmed = precise.match(canonical_text, match.start())
        if confirmed is None:
            # Prefilter-only near-miss: the precise matcher did not confirm, so
            # no Finding is emitted (R3.3).
            continue
        matches.append(
            RawMatch(
                pattern_name=name,
                canonical_start=confirmed.start(),
                canonical_end=confirmed.end(),
            )
        )
    return tuple(matches)
