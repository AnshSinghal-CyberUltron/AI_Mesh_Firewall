"""v2-enforcement-leaks task 18.2: GW09 and GW10 fold through the one confusable set.

* ``canonicalize`` folds every ``CONFUSABLES`` character to its declared Latin letter with
  a 1:1 span record (Property 8, fold half; clause 2.8).
* ``language.CONFUSABLE_FOLD`` IS ``CONFUSABLES`` (one table, not a copy), and
  ``fold_confusables`` agrees with ``canonicalize`` on every entry.
* Confusable-free text with no encoded content canonicalises to itself with an identity
  span map (clause 3.8), checked over seeded random text.
"""

from __future__ import annotations

import random

import pytest

from gateway_v2.detect.confusables import CONFUSABLES
from gateway_v2.detect.deterministic import DEFAULT_BUDGET, canonicalize
from gateway_v2.detect.semantic import CONFUSABLE_FOLD as EXPORTED_FOLD
from gateway_v2.detect.semantic.language import CONFUSABLE_FOLD, fold_confusables

_ITERATIONS = 2_000


def test_gw10_fold_table_is_the_one_confusable_set() -> None:
    assert CONFUSABLE_FOLD is CONFUSABLES
    assert EXPORTED_FOLD is CONFUSABLES


@pytest.mark.parametrize(
    ("ch", "latin"),
    [pytest.param(ch, latin, id=f"U+{ord(ch):04X}") for ch, latin in CONFUSABLES.items()],
)
def test_every_table_character_folds_one_to_one(ch: str, latin: str) -> None:
    """Property 8: a table character folds to its Latin letter with a 1:1 span record.

    **Validates: Requirements 2.8**
    """
    text = f"x{ch}y"
    canonical, span_map = canonicalize(text, DEFAULT_BUDGET)
    assert canonical == f"x{latin}y"
    assert span_map.records == ((0, 0, 1), (1, 1, 2), (2, 2, 3))
    assert fold_confusables(text) == canonical


# Confusable-free alphabet: no table character, and nothing a decoder acts on. ``a``,
# ``d``, ``o`` and ``t`` are left out so no " at " / " dot " word forms; words stay
# under the 16-character base64 run minimum; no ``%``, ``&``, ``\\``, ``+`` or ``/``.
_LETTERS = "bcefghijklmnpqrsuvwxyzBCEFGHJKLMNQRSUVWZ" + "бгджзлфцчшщ" + "βγδλπσφω" + "éüñç"
_SEPARATORS = (" ", ", ", ". ", "\n", "! ", "? ", "  ")


def _confusable_free_text(rng: random.Random) -> str:
    parts: list[str] = []
    for _ in range(rng.randint(0, 12)):
        word = "".join(rng.choice(_LETTERS) for _ in range(rng.randint(1, 15)))
        parts.append(word + rng.choice(_SEPARATORS))
    return "".join(parts)


def test_alphabet_holds_no_table_character() -> None:
    assert not set(_LETTERS) & set(CONFUSABLES)


def test_confusable_free_text_canonicalises_to_itself_with_identity_span_map() -> None:
    """Clause 3.8: no confusable or encoded content -> canonical == input, identity map.

    **Validates: Requirements 3.8**
    """
    rng = random.Random(0x18_02)
    for _ in range(_ITERATIONS):
        text = _confusable_free_text(rng)
        canonical, span_map = canonicalize(text, DEFAULT_BUDGET)
        assert canonical == text, repr(text)
        assert span_map.records == tuple((i, i, i + 1) for i in range(len(text))), repr(text)
        assert fold_confusables(text) == text, repr(text)
