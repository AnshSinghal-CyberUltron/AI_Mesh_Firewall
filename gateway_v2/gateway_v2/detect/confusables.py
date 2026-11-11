"""The one versioned confusable set (v2-enforcement-leaks Group C, clause 2.8).

Pure module: it imports only the standard library, so the ``detect`` layer's import
contract (``detect`` imports only lower layers) holds trivially.

``CONFUSABLES`` is the declared table of Cyrillic and Greek letters folded to a Latin
look-alike during canonicalisation. It replaces two divergent copies:
``detect/deterministic/canonicalize.py::_HOMOGLYPHS`` (GW09, 20 entries) and
``detect/semantic/language.py::CONFUSABLE_FOLD`` (GW10, 22 entries). It is a superset
of both. The two copies never map one key to two targets; they differ only in
coverage (GW10 adds Greek ``Ε`` and ``α``).

Selection rule
--------------
Upper- and lower-case Cyrillic and Greek letters whose UTS #39 confusable prototype is
a single ASCII letter, folded 1:1 per character so canonicalisation span records are
unchanged. Reference: UTS #39 ``confusables.txt`` for Unicode 15.1.0 (the
"Unicode 15.1 confusables").

Verification status
-------------------
No pinned ``confusables.txt`` is vendored in this repository, and none is downloaded.
Each entry carries its Unicode character name, and the test suite checks that name with
``unicodedata.name`` (CPython 3.12 ships the Unicode 15.0.0 database; every entry is
pre-15.0 so the names are identical). It also checks that the entry is a Cyrillic or
Greek letter and that its target is one ASCII letter of the same case. That proves each
code point is the letter its comment names. It does not replace a line-by-line check
against the pinned file, which should be done when one is vendored.

Deliberate deviations from the raw UTS #39 skeleton
---------------------------------------------------
The skeleton algorithm folds BOTH sides of a comparison. Here only the input is folded,
and the patterns it is matched against are plain ASCII (mostly case-insensitive). So the
target is the case-matched Latin letter the character imitates:

* ``Ι`` (GREEK CAPITAL IOTA), ``І`` (CYRILLIC CAPITAL BYELORUSSIAN-UKRAINIAN I) and
  ``Ӏ`` (CYRILLIC LETTER PALOCHKA) fold to ``I``. UTS #39 skeletonises Latin ``I``
  itself to ``l``, so the raw prototype of these is ``l``; folding them to ``l`` would
  turn ``Ιgnore`` into ``lgnore`` and defeat the injection patterns.
* ``ӏ`` (CYRILLIC SMALL LETTER PALOCHKA) folds to ``l``, its UTS #39 prototype.
* ``υ`` (GREEK SMALL LETTER UPSILON) folds to ``u``. It is on the required Group C list.
  Its UTS #39 prototype should be re-checked against a pinned copy: it may be the
  non-ASCII ``ʋ`` rather than a plain ASCII letter.

Left out on purpose: ``κ`` (GREEK SMALL LETTER KAPPA), whose prototype is believed to
be the non-ASCII ``ĸ``; letters that imitate digits (``З`` → ``3``, ``б`` → ``6``);
and multi-character prototypes.

``CONFUSABLES_DIGEST`` is a sha256 over the sorted items, computed at import. The
digest feeds ``ruleset_version`` (task 18.3) so ``detector_version`` moves whenever the
table changes (Property 8). Bump ``CONFUSABLE_SET_VERSION`` with any table change.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

__all__ = (
    "CONFUSABLES",
    "CONFUSABLES_DIGEST",
    "CONFUSABLE_SET_VERSION",
    "confusable_digest",
)

#: The declared version of the confusable set. Bump it with any table change.
CONFUSABLE_SET_VERSION: Final = "amf-confusables-1"

# ``(character, Latin target, Unicode character name)``. The name is checked against
# ``unicodedata.name`` by the test suite, so a mistyped code point cannot hide behind
# a correct-looking comment. Ordered by code point.
_ENTRIES: Final[tuple[tuple[str, str, str], ...]] = (
    # Greek capital letters.
    ("\u0391", "A", "GREEK CAPITAL LETTER ALPHA"),
    ("\u0392", "B", "GREEK CAPITAL LETTER BETA"),
    ("\u0395", "E", "GREEK CAPITAL LETTER EPSILON"),
    ("\u0396", "Z", "GREEK CAPITAL LETTER ZETA"),
    ("\u0397", "H", "GREEK CAPITAL LETTER ETA"),
    ("\u0399", "I", "GREEK CAPITAL LETTER IOTA"),
    ("\u039a", "K", "GREEK CAPITAL LETTER KAPPA"),
    ("\u039c", "M", "GREEK CAPITAL LETTER MU"),
    ("\u039d", "N", "GREEK CAPITAL LETTER NU"),
    ("\u039f", "O", "GREEK CAPITAL LETTER OMICRON"),
    ("\u03a1", "P", "GREEK CAPITAL LETTER RHO"),
    ("\u03a4", "T", "GREEK CAPITAL LETTER TAU"),
    ("\u03a5", "Y", "GREEK CAPITAL LETTER UPSILON"),
    ("\u03a7", "X", "GREEK CAPITAL LETTER CHI"),
    # Greek small letters.
    ("\u03b1", "a", "GREEK SMALL LETTER ALPHA"),
    ("\u03b9", "i", "GREEK SMALL LETTER IOTA"),
    ("\u03bd", "v", "GREEK SMALL LETTER NU"),
    ("\u03bf", "o", "GREEK SMALL LETTER OMICRON"),
    ("\u03c1", "p", "GREEK SMALL LETTER RHO"),
    ("\u03c5", "u", "GREEK SMALL LETTER UPSILON"),
    # Cyrillic capital letters.
    ("\u0405", "S", "CYRILLIC CAPITAL LETTER DZE"),
    ("\u0406", "I", "CYRILLIC CAPITAL LETTER BYELORUSSIAN-UKRAINIAN I"),
    ("\u0408", "J", "CYRILLIC CAPITAL LETTER JE"),
    ("\u0410", "A", "CYRILLIC CAPITAL LETTER A"),
    ("\u0412", "B", "CYRILLIC CAPITAL LETTER VE"),
    ("\u0415", "E", "CYRILLIC CAPITAL LETTER IE"),
    ("\u041a", "K", "CYRILLIC CAPITAL LETTER KA"),
    ("\u041c", "M", "CYRILLIC CAPITAL LETTER EM"),
    ("\u041d", "H", "CYRILLIC CAPITAL LETTER EN"),
    ("\u041e", "O", "CYRILLIC CAPITAL LETTER O"),
    ("\u0420", "P", "CYRILLIC CAPITAL LETTER ER"),
    ("\u0421", "C", "CYRILLIC CAPITAL LETTER ES"),
    ("\u0422", "T", "CYRILLIC CAPITAL LETTER TE"),
    ("\u0423", "Y", "CYRILLIC CAPITAL LETTER U"),
    ("\u0425", "X", "CYRILLIC CAPITAL LETTER HA"),
    ("\u04ae", "Y", "CYRILLIC CAPITAL LETTER STRAIGHT U"),
    ("\u04c0", "I", "CYRILLIC LETTER PALOCHKA"),
    ("\u051a", "Q", "CYRILLIC CAPITAL LETTER QA"),
    ("\u051c", "W", "CYRILLIC CAPITAL LETTER WE"),
    # Cyrillic small letters.
    ("\u0430", "a", "CYRILLIC SMALL LETTER A"),
    ("\u0435", "e", "CYRILLIC SMALL LETTER IE"),
    ("\u043e", "o", "CYRILLIC SMALL LETTER O"),
    ("\u0440", "p", "CYRILLIC SMALL LETTER ER"),
    ("\u0441", "c", "CYRILLIC SMALL LETTER ES"),
    ("\u0443", "y", "CYRILLIC SMALL LETTER U"),
    ("\u0445", "x", "CYRILLIC SMALL LETTER HA"),
    ("\u0455", "s", "CYRILLIC SMALL LETTER DZE"),
    ("\u0456", "i", "CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I"),
    ("\u0458", "j", "CYRILLIC SMALL LETTER JE"),
    ("\u04af", "y", "CYRILLIC SMALL LETTER STRAIGHT U"),
    ("\u04bb", "h", "CYRILLIC SMALL LETTER SHHA"),
    ("\u04cf", "l", "CYRILLIC SMALL LETTER PALOCHKA"),
    ("\u0501", "d", "CYRILLIC SMALL LETTER KOMI DE"),
    ("\u051b", "q", "CYRILLIC SMALL LETTER QA"),
    ("\u051d", "w", "CYRILLIC SMALL LETTER WE"),
)

#: The confusable set: character -> the one ASCII letter it is folded to. Immutable.
CONFUSABLES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {ch: latin for ch, latin, _name in _ENTRIES}
)


def confusable_digest(table: Mapping[str, str]) -> str:
    """Return the sha256 hex of ``table``'s items sorted by key.

    Each item is serialised as ``<key>\\x1f<value>`` and terminated by ``\\x1e``, the
    same separators ``ruleset_version`` uses, so the digest is deterministic and any
    added, removed or re-targeted entry changes it.
    """
    hasher = hashlib.sha256()
    for key in sorted(table):
        hasher.update(f"{key}\x1f{table[key]}".encode())
        hasher.update(b"\x1e")
    return hasher.hexdigest()


#: sha256 over the sorted items of :data:`CONFUSABLES`, computed at import.
CONFUSABLES_DIGEST: Final = confusable_digest(CONFUSABLES)
