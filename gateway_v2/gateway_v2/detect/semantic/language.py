"""Language posture, confusable fold, and separate multilingual reporting (GW10).

This module is the pure language half of the semantic detector. It carries three
local, self-contained pieces, all exercised BEFORE the guard tokenizer runs:

1. A lightweight local **language-ID heuristic** -- a script / codepoint-range
   classifier stub (no model, no I/O). The real multilingual AUC is deferred to
   GW20 / T01 (R11.1); what is local and enforced here is the POSTURE.
2. The ``SUPPORTED_LANGUAGES`` frozenset and the frozen
   :class:`LanguagePosture`. An input whose identified language is outside the
   supported set yields a NON-CLEAN posture (``supported=False``) -- never a
   silent clean ``EXECUTED`` pass (R5.1, R5.2, Property 6). The detector maps a
   non-clean posture to an ``UNAVAILABLE`` / explicit unsupported-language
   finding; this module only decides the posture, it decides no disposition.
3. A self-contained confusable / homoglyph fold (:func:`fold_confusables`)
   applied BEFORE the tokenizer (R5.3). The fold table is a module-level
   :class:`~types.MappingProxyType` declared in THIS module -- it deliberately
   does NOT import the GW09 ``detect/deterministic/canonicalize.py`` internals
   (its homoglyph fold is one step of a nine-step byte-span decode pipeline; the
   semantic pre-tokenizer fold is a standalone string transform). The table is a
   small, self-contained copy of the common Cyrillic / Greek attack set in the
   same spirit as the GW09 fold.

:class:`MultilingualReport` is the separate per-language breakdown record the
caller / audit reads so multilingual behaviour is reported separately from the
aggregate English number (R5.5).

Layering: this module imports only the Python standard library. It never imports
``edge``, ``admit``, or ``plan`` (Layer_Contract, R8.7). It follows the house
idiom shared with ``detect/holdback.py`` and ``detect/deterministic/
canonicalize.py``: ``from __future__ import annotations``, frozen
``@dataclass(frozen=True, slots=True)`` value types, a ``MappingProxyType``
immutable table, a tuple ``__all__``, and no module-level mutable binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

__all__ = (
    "CONFUSABLE_FOLD",
    "SUPPORTED_LANGUAGES",
    "UNKNOWN_LANGUAGE",
    "LanguagePosture",
    "MultilingualReport",
    "fold_confusables",
    "identify_language",
    "posture_for",
)

#: Sentinel language tag for text the heuristic cannot classify (e.g. empty,
#: symbol-only, or digit-only input). It is deliberately NOT a member of
#: :data:`SUPPORTED_LANGUAGES`, so unknown text yields a non-clean posture.
UNKNOWN_LANGUAGE: Final = "und"

#: The languages the local semantic posture treats as supported. The real
#: multilingual recall / FPR for each is deferred (R11.1); this set is the
#: ENFORCED local decision -- a language outside it is never a silent clean
#: pass. BCP-47 primary subtags.
SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset(
    {
        "en",  # English -- the calibrated operating point
        "es",  # Spanish
        "fr",  # French
        "de",  # German
        "pt",  # Portuguese
        "it",  # Italian
        "nl",  # Dutch
        "ru",  # Russian (Cyrillic)
    }
)

# --------------------------------------------------------------------------- #
# Confusable / homoglyph fold table. A self-contained copy (NOT an import of the
# GW09 canonicalize internals) of the common Cyrillic / Greek look-alike attack
# set, folded to the ASCII character they imitate. Applied BEFORE the tokenizer
# (R5.3) so a homoglyph-obfuscated token reaches the guard as its ASCII form.
# ``MappingProxyType`` keeps the table immutable at module scope (the
# no-module-mutable gate rejects a bare dict/set literal; a MappingProxyType
# call is accepted, matching the canonicalize.py house idiom).
# --------------------------------------------------------------------------- #
CONFUSABLE_FOLD: Final = MappingProxyType(
    {
        # Cyrillic small letters -> ASCII look-alike.
        "\u0430": "a",  # CYRILLIC SMALL A
        "\u0435": "e",  # CYRILLIC SMALL IE
        "\u043e": "o",  # CYRILLIC SMALL O
        "\u0440": "p",  # CYRILLIC SMALL ER
        "\u0441": "c",  # CYRILLIC SMALL ES
        "\u0445": "x",  # CYRILLIC SMALL HA
        "\u0443": "y",  # CYRILLIC SMALL U
        "\u0456": "i",  # CYRILLIC SMALL BYELORUSSIAN-UKRAINIAN I
        "\u0458": "j",  # CYRILLIC SMALL JE
        "\u04bb": "h",  # CYRILLIC SMALL SHHA
        "\u0501": "d",  # CYRILLIC SMALL KOMI DE
        # Cyrillic capital letters -> ASCII look-alike.
        "\u0410": "A",  # CYRILLIC CAPITAL A
        "\u0415": "E",  # CYRILLIC CAPITAL IE
        "\u041e": "O",  # CYRILLIC CAPITAL O
        "\u0420": "P",  # CYRILLIC CAPITAL ER
        "\u0421": "C",  # CYRILLIC CAPITAL ES
        "\u0425": "X",  # CYRILLIC CAPITAL HA
        # Greek look-alikes -> ASCII.
        "\u0391": "A",  # GREEK CAPITAL ALPHA
        "\u0395": "E",  # GREEK CAPITAL EPSILON
        "\u039f": "O",  # GREEK CAPITAL OMICRON
        "\u03bf": "o",  # GREEK SMALL OMICRON
        "\u03b1": "a",  # GREEK SMALL ALPHA
    }
)


@dataclass(frozen=True, slots=True)
class LanguagePosture:
    """The explicit posture assigned to a request's identified language.

    ``supported`` is ``False`` for any language outside
    :data:`SUPPORTED_LANGUAGES` (including :data:`UNKNOWN_LANGUAGE`). An
    unsupported posture is NON-CLEAN: the detector surfaces it as an
    ``UNAVAILABLE`` / explicit unsupported-language finding rather than a clean
    ``EXECUTED`` pass (R5.2, Property 6). This type decides no disposition; it
    only records the language and whether it is supported.
    """

    language: str
    supported: bool

    @property
    def is_clean_eligible(self) -> bool:
        """Whether a clean ``EXECUTED`` semantic result is permissible.

        Returns ``True`` only when the language is supported. An unsupported
        language is never clean-eligible, which is the structural guarantee
        behind Property 6 (never a silent clean pass).
        """
        return self.supported


@dataclass(frozen=True, slots=True)
class MultilingualReport:
    """A single per-language breakdown record for separate reporting (R5.5).

    The caller / audit reads this record so multilingual behaviour is reported
    separately from the aggregate (English) number. It carries the detected
    language, its posture, and the count of segments observed in that language
    for the request. The real per-language recall / FPR numbers are deferred to
    GW20 / T01 (R11.1); this record carries only the local, enforced facts.
    """

    language: str
    posture: LanguagePosture
    segment_count: int


# --------------------------------------------------------------------------- #
# Codepoint-range script buckets for the heuristic classifier. Frozen tuples of
# (lo, hi) inclusive ranges, kept at module scope so the classifier allocates
# nothing per call. These are a STUB -- the real language-ID model is deferred
# (R11.1); the posture derived from the result is what is enforced.
# --------------------------------------------------------------------------- #
_CYRILLIC_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x0400, 0x04FF),
    (0x0500, 0x052F),
    (0x2DE0, 0x2DFF),
    (0xA640, 0xA69F),
)
_LATIN_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x0041, 0x005A),  # A-Z
    (0x0061, 0x007A),  # a-z
    (0x00C0, 0x024F),  # Latin-1 Supplement + Extended-A/B (accents)
)
_CJK_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7A3),  # Hangul syllables
)
_ARABIC_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
)
_DEVANAGARI_RANGES: Final[tuple[tuple[int, int], ...]] = ((0x0900, 0x097F),)


def _in_ranges(codepoint: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    """Return whether ``codepoint`` falls in any inclusive ``(lo, hi)`` range."""
    for lo, hi in ranges:
        if lo <= codepoint <= hi:
            return True
    return False


def fold_confusables(text: str) -> str:
    """Fold Cyrillic / Greek confusables to their ASCII look-alike.

    Applied BEFORE the tokenizer (R5.3). A pure, deterministic string transform:
    every character present in :data:`CONFUSABLE_FOLD` is replaced by its ASCII
    target; every other character is passed through unchanged. The input string
    is never mutated (strings are immutable; a new string is returned).
    """
    if not text:
        return text
    return "".join(CONFUSABLE_FOLD.get(ch, ch) for ch in text)


def identify_language(text: str) -> str:
    """Identify the dominant script and map it to a BCP-47 primary subtag.

    A lightweight local heuristic (a script / codepoint-range classifier stub,
    NOT a model -- the real language-ID is deferred, R11.1). It counts letters by
    script bucket and returns the primary subtag of the dominant script:

    * Latin-dominant      -> ``"en"`` (a Latin-script supported stand-in; the
      per-language split within Latin is a deferred model concern).
    * Cyrillic-dominant   -> ``"ru"``.
    * CJK / Arabic / Devanagari-dominant -> an unsupported tag
      (``"zh"`` / ``"ar"`` / ``"hi"``) so the posture is non-clean.
    * No classifiable letter (empty, symbols, digits) ->
      :data:`UNKNOWN_LANGUAGE`.

    This is a pure function: it reads only ``text`` and allocates no module
    state. The returned tag feeds :func:`posture_for`.
    """
    latin = cyrillic = cjk = arabic = devanagari = 0
    for ch in text:
        if not ch.isalpha():
            continue
        codepoint = ord(ch)
        if _in_ranges(codepoint, _LATIN_RANGES):
            latin += 1
        elif _in_ranges(codepoint, _CYRILLIC_RANGES):
            cyrillic += 1
        elif _in_ranges(codepoint, _CJK_RANGES):
            cjk += 1
        elif _in_ranges(codepoint, _ARABIC_RANGES):
            arabic += 1
        elif _in_ranges(codepoint, _DEVANAGARI_RANGES):
            devanagari += 1

    counts = (
        (latin, "en"),
        (cyrillic, "ru"),
        (cjk, "zh"),
        (arabic, "ar"),
        (devanagari, "hi"),
    )
    best_count, best_tag = max(counts, key=lambda pair: pair[0])
    if best_count == 0:
        return UNKNOWN_LANGUAGE
    return best_tag


def posture_for(language: str) -> LanguagePosture:
    """Build the :class:`LanguagePosture` for an identified ``language`` tag.

    ``supported`` is membership in :data:`SUPPORTED_LANGUAGES`; an unknown or
    out-of-set tag is non-clean (R5.2). Pure and deterministic.
    """
    return LanguagePosture(language=language, supported=language in SUPPORTED_LANGUAGES)
