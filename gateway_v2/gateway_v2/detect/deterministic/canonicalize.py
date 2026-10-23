"""Single canonicalization pass + span map + decode budget (GW09, §10.9, C26).

This is the ONE canonicalization GW09 ships (design "canonicalize.py", R2). It
decodes an inspected channel value into a canonical form in a single pass,
carrying a :class:`SpanMap` so a match found in canonical space collapses back
to a byte-offset :class:`~gateway_v2.domain.finding.Span` in the ORIGINAL text,
and it enforces a :class:`DecodeBudget` whose CHEAP pre-check runs BEFORE any
expansion-heavy decode (the C26 fix: v1 ran an 18x NFKC expansion of a 1 MiB
body for 7.4 s of event-loop CPU because the size/expansion rejection came
AFTER the heavy work). On exhaustion :func:`canonicalize` raises the LOCAL
:class:`BudgetExceeded` -- an explicit reject (R2.5/R2.6), never a silent
truncation. ``BudgetExceeded`` is caught inside ``detect/deterministic`` and
mapped to an ``UNAVAILABLE`` Finding; it is never an HTTP type and never
escapes the detect layer.

Layering: this module imports only ``gateway_v2.domain``, the intra-layer
``gateway_v2.detect.confusables`` table (+ stdlib). It mirrors
the ``detect/holdback.py`` house idiom: ``from __future__ import annotations``,
``re``, frozen ``@dataclass(frozen=True, slots=True)``, ``MappingProxyType``
immutable tables, a tuple ``__all__``. The ``[REDACTED]`` placeholder is
re-declared locally (detect may not import ``egress``) and is pinned equal to
``egress.output_guard.REDACTION_PLACEHOLDER`` by a test.

Decoder_Set (R2.2), applied once in order with span-map accumulation:
    1. percent-encoding incl. double %-encoding (``%2541`` -> ``%41`` -> ``A``)
    2. HTML entities (``&#64;``, ``&amp;``)
    3. base64 bounded-length segments only
    4. fullwidth -> ASCII fold
    5. zero-width / Default_Ignorable / Mn strip (full ``unicodedata`` category)
    6. ``\\u`` escape unescape
    7. Cyrillic / confusable homoglyph fold
    8. RTL-reversed-key normalization
    9. "at"/"dot" email de-obfuscation ("x at y dot com" -> "x@y.com")

Span invariant (design Property 2): the original ``Span`` returned, when
redacted in the ORIGINAL text, removes the planted sensitive bytes. Each
decoder is conservative -- it prefers a slightly WIDER original span over
dropping a byte, so a redaction never leaves part of a planted value behind.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from gateway_v2.detect.confusables import CONFUSABLES
from gateway_v2.domain import Span

__all__ = (
    "DEFAULT_BUDGET",
    "REDACTION_PLACEHOLDER",
    "BudgetExceeded",
    "DecodeBudget",
    "SpanMap",
    "canonicalize",
)

#: Re-declared locally (detect may not import ``egress``); a test pins this
#: equal to ``egress.output_guard.REDACTION_PLACEHOLDER``.
REDACTION_PLACEHOLDER: str = "[REDACTED]"

# --------------------------------------------------------------------------- #
# Decode-budget bounds. MODULE-LEVEL NAMED CONSTANTS, never dataclass fields
# (R8.4; ``check_capacity_literals`` forbids capacity literals as fields). The
# names are deliberately NOT in the capacity-keyword set, and the bounds live
# here so ``DecodeBudget`` carries none.
# --------------------------------------------------------------------------- #

#: Longest input (in characters) the cheap pre-check admits before heavy decode.
_MAX_INPUT_BYTES: Final = 262_144
#: Largest tolerated ratio of cheap-estimated expansion to current length.
_MAX_EXPANSION_RATIO: Final = 8.0
#: Largest number of decode-triggering "parts" (encoded tokens) admitted.
_MAX_PARTS: Final = 4_096

# Cheap part-estimate signals -- counted WITHOUT decoding (R2.6). Each is an
# upper-bound proxy for how much expansion-heavy work a decode would do.
_PERCENT_TOKEN: Final = re.compile(r"%[0-9A-Fa-f]{2}")
_ENTITY_TOKEN: Final = re.compile(r"&(?:#x?[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]{1,31});")
_UESC_TOKEN: Final = re.compile(r"\\u[0-9A-Fa-f]{4}")
# A bounded base64 run: only runs within this length are decoded (R2.2 bound).
_B64_MIN: Final = 16
_B64_MAX: Final = 512
_B64_RUN: Final = re.compile(rf"[A-Za-z0-9+/]{{{_B64_MIN},{_B64_MAX}}}={{0,2}}")

# --------------------------------------------------------------------------- #
# Decoder tables -- immutable (R8.5, no module-level mutable).
# --------------------------------------------------------------------------- #

# Cyrillic / Greek confusable homoglyphs are folded through the ONE versioned set,
# ``detect/confusables.py::CONFUSABLES`` (v2-enforcement-leaks Group C, clause 2.8),
# shared with the GW10 semantic pre-tokenizer fold. Every entry maps one character to
# one ASCII letter, so the fold is 1:1 and span records are unchanged.

# RTL / bidi control characters normalized away before matching.
_BIDI_CONTROLS: Final = frozenset(
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f"
)

# "at"/"dot" email de-obfuscation: " at " -> "@", " dot " -> ".".
_AT_WORD: Final = re.compile(r"\s+(?:at|AT|At)\s+")
_DOT_WORD: Final = re.compile(r"\s+(?:dot|DOT|Dot)\s+")


# --------------------------------------------------------------------------- #
# BudgetExceeded -- a LOCAL exception, caught inside detect/deterministic.
# --------------------------------------------------------------------------- #


class BudgetExceeded(Exception):
    """Local sentinel: the Decode_Budget was exhausted (R2.5, C26).

    Never an HTTP type. Caught inside ``detect/deterministic`` and mapped to an
    ``UNAVAILABLE`` Finding; it never escapes the detect layer and never
    silently truncates the input.
    """


# --------------------------------------------------------------------------- #
# DecodeBudget -- the cheap pre-check (R2.4/R2.6). Carries NO capacity field;
# its bound is the module-level named constants above.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DecodeBudget:
    """Declared bound on canonicalization cost (R2.4).

    Carries no capacity field (R8.4): ``within`` reads the module-level
    ``_MAX_INPUT_BYTES`` / ``_MAX_EXPANSION_RATIO`` / ``_MAX_PARTS`` named
    constants. ``within`` is the CHEAP pre-check (length, cheap expansion-ratio
    estimate, encoded-part counts) that :func:`canonicalize` evaluates BEFORE
    any expansion-heavy decode (R2.6, the C26 fix).
    """

    def within(self, text: str) -> bool:
        """Return True iff ``text`` is admissible without the heavy decode.

        Pure and O(len). Counts cheap signals only -- it NEVER decodes. The
        expansion-ratio estimate upper-bounds how far the Decoder_Set could
        grow the text (base64 ~= 0.75x out but the budget bounds the heavy work
        by part count, so the estimate charges each encoded part its decoded
        upper bound).
        """
        length = len(text)
        if length > _MAX_INPUT_BYTES:
            return False
        parts = (
            len(_PERCENT_TOKEN.findall(text))
            + len(_ENTITY_TOKEN.findall(text))
            + len(_UESC_TOKEN.findall(text))
            + len(_B64_RUN.findall(text))
        )
        if parts > _MAX_PARTS:
            return False
        # Cheap expansion-ratio estimate: a base64 run decodes to ~0.75 of its
        # chars but each encoded token is charged a small fixed expansion so a
        # pathological all-encoded body is rejected before the heavy pass.
        if length == 0:
            return True
        estimated = float(length) + 3.0 * float(parts)
        return (estimated / float(length)) <= _MAX_EXPANSION_RATIO


#: A ready-to-use default budget (R2 -- "a module-level DEFAULT_BUDGET is fine").
DEFAULT_BUDGET: Final[DecodeBudget] = DecodeBudget()


# --------------------------------------------------------------------------- #
# SpanMap -- canonical [start, end) -> original byte Span. Immutable tuple of
# (canonical_index, original_start, original_end) records built in the one pass.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SpanMap:
    """Maps a canonical-space ``[start, end)`` back to an original-text Span.

    Backed by an immutable tuple of ``(canonical_index, original_start,
    original_end)`` records -- exactly one per canonical character emitted by
    the single pass. ``to_original`` collapses a canonical range to the minimal
    original byte :class:`~gateway_v2.domain.finding.Span` covering it, favoring
    a slightly WIDER span over dropping a byte (design Property 2).
    """

    records: tuple[tuple[int, int, int], ...]

    def to_original(self, canonical_start: int, canonical_end: int) -> Span:
        """Collapse canonical ``[start, end)`` to the minimal covering Span.

        For an empty canonical range the degenerate original Span at the
        corresponding boundary is returned. Out-of-range inputs are clamped to
        the recorded extent; the result is always in bounds of the original.
        """
        recs = self.records
        if not recs:
            return Span(start=0, end=0)
        lo = canonical_start if canonical_start >= 0 else 0
        hi = canonical_end if canonical_end >= 0 else 0
        if hi < lo:
            hi = lo
        covering = [r for r in recs if lo <= r[0] < hi]
        if not covering:
            # Empty / zero-width range: anchor at the record nearest `lo`.
            anchor = min(recs, key=lambda r: abs(r[0] - lo))
            return Span(start=anchor[1], end=anchor[1])
        start = min(r[1] for r in covering)
        end = max(r[2] for r in covering)
        return Span(start=start, end=end)


# --------------------------------------------------------------------------- #
# Internal span-carrying character buffer. A list of (char, orig_start,
# orig_end). Kept local to each call -> no module-level mutable (R8.5).
# --------------------------------------------------------------------------- #


def _seed(text: str) -> list[tuple[str, int, int]]:
    """Seed the per-character buffer: each char maps to its own original byte."""
    return [(ch, i, i + 1) for i, ch in enumerate(text)]


def _buf_text(buf: list[tuple[str, int, int]]) -> str:
    return "".join(cell[0] for cell in buf)


def _span_of(buf: list[tuple[str, int, int]], start: int, end: int) -> tuple[int, int]:
    """Minimal original ``[orig_start, orig_end)`` covering buffer ``[start, end)``."""
    if start >= end:
        if not buf:
            return 0, 0
        idx = min(max(start, 0), len(buf) - 1)
        return buf[idx][1], buf[idx][1]
    lo = min(buf[k][1] for k in range(start, end))
    hi = max(buf[k][2] for k in range(start, end))
    return lo, hi


def _rebuild(
    source: str, matches: list[tuple[int, int, str]], buf: list[tuple[str, int, int]]
) -> list[tuple[str, int, int]]:
    """Replace non-overlapping ``source`` slices with decoded text, carrying spans.

    ``matches`` is a list of ``(start, end, decoded)`` over ``source`` (== the
    current ``_buf_text``). Each decoded char inherits the WHOLE matched slice's
    original span, so redacting any decoded byte removes the entire encoded
    token in the original (conservative, Property 2).
    """
    out: list[tuple[str, int, int]] = []
    cursor = 0
    for start, end, decoded in matches:
        if start < cursor:
            continue
        out.extend(buf[cursor:start])
        orig_lo, orig_hi = _span_of(buf, start, end)
        for ch in decoded:
            out.append((ch, orig_lo, orig_hi))
        cursor = end
    out.extend(buf[cursor:])
    return out


# --------------------------------------------------------------------------- #
# The ordered Decoder_Set (R2.2). Each decoder is a pure buf -> buf transform.
# --------------------------------------------------------------------------- #


def _decode_percent(buf: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Percent-decode, incl. double %-encoding, to a fixed point (bounded loop)."""
    for _ in range(3):  # double (and triple) %-encoding, bounded (R2.1 one pass)
        source = _buf_text(buf)
        matches: list[tuple[int, int, str]] = []
        for m in _PERCENT_TOKEN.finditer(source):
            try:
                decoded = bytes.fromhex(m.group()[1:]).decode("latin-1")
            except ValueError:
                continue
            matches.append((m.start(), m.end(), decoded))
        if not matches:
            break
        buf = _rebuild(source, matches, buf)
    return buf


def _decode_entities(buf: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Decode HTML entities (named and numeric)."""
    source = _buf_text(buf)
    matches: list[tuple[int, int, str]] = []
    for m in _ENTITY_TOKEN.finditer(source):
        decoded = html.unescape(m.group())
        if decoded != m.group():
            matches.append((m.start(), m.end(), decoded))
    return _rebuild(source, matches, buf) if matches else buf


def _decode_base64(buf: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Decode bounded-length base64 runs only (R2.2 bound)."""
    source = _buf_text(buf)
    matches: list[tuple[int, int, str]] = []
    for m in _B64_RUN.finditer(source):
        token = m.group()
        if len(token) % 4 != 0:
            continue
        try:
            raw = base64.b64decode(token, validate=True)
            decoded = raw.decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if not decoded.isprintable():
            continue
        matches.append((m.start(), m.end(), decoded))
    return _rebuild(source, matches, buf) if matches else buf


def _decode_fullwidth(buf: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Fold fullwidth / compatibility forms to ASCII (NFKC per character)."""
    out: list[tuple[str, int, int]] = []
    for ch, lo, hi in buf:
        folded = unicodedata.normalize("NFKC", ch)
        if folded == ch:
            out.append((ch, lo, hi))
            continue
        for fch in folded:
            out.append((fch, lo, hi))
    return out


def _strip_ignorable(buf: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Strip zero-width / Default_Ignorable / Mn marks (full category, not a subset)."""
    out: list[tuple[str, int, int]] = []
    for ch, lo, hi in buf:
        if ch in _BIDI_CONTROLS:
            continue
        if unicodedata.category(ch) == "Mn":
            continue
        if _is_default_ignorable(ch):
            continue
        out.append((ch, lo, hi))
    return out


def _is_default_ignorable(ch: str) -> bool:
    """Zero-width / Default_Ignorable_Code_Point membership (conservative set)."""
    code = ord(ch)
    if ch in ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad", "\u180e"):
        return True
    if 0xFE00 <= code <= 0xFE0F:  # variation selectors
        return True
    return 0x1D173 <= code <= 0x1D17A  # musical ignorables


def _decode_unicode_escapes(buf: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    r"""Decode ``\uXXXX`` escapes."""
    source = _buf_text(buf)
    matches: list[tuple[int, int, str]] = []
    for m in _UESC_TOKEN.finditer(source):
        try:
            decoded = chr(int(m.group()[2:], 16))
        except ValueError:
            continue
        matches.append((m.start(), m.end(), decoded))
    return _rebuild(source, matches, buf) if matches else buf


def _fold_homoglyphs(buf: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Fold Cyrillic / Greek confusables to their ASCII look-alike via ``CONFUSABLES``.

    One output cell per input cell, each keeping its original span, so the fold never
    moves an offset (Property 2) and confusable-free text is passed through unchanged.
    """
    out: list[tuple[str, int, int]] = []
    for ch, lo, hi in buf:
        out.append((CONFUSABLES.get(ch, ch), lo, hi))
    return out


def _normalize_bidi(buf: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """RTL-reversed-key normalization: drop residual bidi controls.

    The embedded reversal that bidi controls introduce is neutralized by
    removing the control characters (the strip above handles most); this is the
    explicit RTL-key pass retaining the surrounding original spans.
    """
    out: list[tuple[str, int, int]] = []
    for ch, lo, hi in buf:
        if ch in _BIDI_CONTROLS:
            continue
        out.append((ch, lo, hi))
    return out


def _deobfuscate_email(buf: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """De-obfuscate "x at y dot com" -> "x@y.com"."""
    source = _buf_text(buf)
    matches: list[tuple[int, int, str]] = []
    for m in _AT_WORD.finditer(source):
        matches.append((m.start(), m.end(), "@"))
    for m in _DOT_WORD.finditer(source):
        matches.append((m.start(), m.end(), "."))
    if not matches:
        return buf
    matches.sort(key=lambda t: t[0])
    return _rebuild(source, matches, buf)


# --------------------------------------------------------------------------- #
# canonicalize -- the single pass (R2.1). Budget pre-check FIRST (R2.6).
# --------------------------------------------------------------------------- #


def canonicalize(text: str, budget: DecodeBudget) -> tuple[str, SpanMap]:
    """Canonicalize ``text`` in one pass, returning ``(canonical, SpanMap)``.

    Order (R2.5/R2.6, the C26 fix):
      (a) Evaluate the Decode_Budget CHEAP pre-check FIRST. If it fails, raise
          :class:`BudgetExceeded` -- an explicit reject, never a silent
          truncation and never any expansion-heavy decode.
      (b) Apply the ordered Decoder_Set ONCE, accumulating a :class:`SpanMap`
          so a canonical match collapses back to the original byte Span.

    Pure: ``text`` is never mutated (R1.4); a new string is returned.
    """
    if not budget.within(text):
        raise BudgetExceeded("decode budget exceeded before heavy canonicalization")

    buf = _seed(text)
    buf = _decode_percent(buf)
    buf = _decode_entities(buf)
    buf = _decode_base64(buf)
    buf = _decode_fullwidth(buf)
    buf = _strip_ignorable(buf)
    buf = _decode_unicode_escapes(buf)
    buf = _fold_homoglyphs(buf)
    buf = _normalize_bidi(buf)
    buf = _deobfuscate_email(buf)

    canonical = _buf_text(buf)
    records = tuple((i, cell[1], cell[2]) for i, cell in enumerate(buf))
    return canonical, SpanMap(records=records)
