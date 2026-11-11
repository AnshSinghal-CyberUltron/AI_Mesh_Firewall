"""v2-enforcement-leaks task 18.1: the versioned confusable set (``detect/confusables.py``).

Checks the table on its own: coverage (a superset of both shipped tables plus the
required Group C additions), per-entry correctness via ``unicodedata``, immutability,
and a deterministic digest that moves with any table change. Wiring the table into
canonicalize / language (18.2) and ``ruleset_version`` (18.3) is tested there.
"""

from __future__ import annotations

import random
import unicodedata

import pytest

from gateway_v2.detect import confusables
from gateway_v2.detect.confusables import (
    CONFUSABLE_SET_VERSION,
    CONFUSABLES,
    CONFUSABLES_DIGEST,
    confusable_digest,
)
from tests.detect import _gw09_original_oracle as oracle

#: The ORIGINAL 22-entry GW10 ``detect/semantic/language.py::CONFUSABLE_FOLD``, frozen
#: here because task 18.2 turned the shipped name into a re-export of ``CONFUSABLES``
#: (comparing the re-export against itself would prove nothing).
_ORIGINAL_GW10_FOLD: dict[str, str] = {
    "\u0430": "a",
    "\u0435": "e",
    "\u043e": "o",
    "\u0440": "p",
    "\u0441": "c",
    "\u0445": "x",
    "\u0443": "y",
    "\u0456": "i",
    "\u0458": "j",
    "\u04bb": "h",
    "\u0501": "d",
    "\u0410": "A",
    "\u0415": "E",
    "\u041e": "O",
    "\u0420": "P",
    "\u0421": "C",
    "\u0425": "X",
    "\u0391": "A",
    "\u0395": "E",
    "\u039f": "O",
    "\u03bf": "o",
    "\u03b1": "a",
}

#: Design Group C: the minimum additions over the shipped tables.
_REQUIRED_GREEK = "ΒΖΗΙΚΜΝΡΤΥΧινρυ"
_REQUIRED_CYRILLIC = "ВКМНТУЅІЈӀѕԛԝӏ"


def test_version_is_declared() -> None:
    assert CONFUSABLE_SET_VERSION == "amf-confusables-1"


def test_table_has_no_duplicate_entries() -> None:
    assert len(confusables._ENTRIES) == len(CONFUSABLES) == 55


@pytest.mark.parametrize(
    "shipped",
    [
        pytest.param(oracle.HOMOGLYPHS, id="gw09-_HOMOGLYPHS"),
        pytest.param(_ORIGINAL_GW10_FOLD, id="gw10"),
    ],
)
def test_superset_of_each_shipped_table(shipped: dict[str, str]) -> None:
    """Every shipped mapping is kept with the same target (the tables never disagree)."""
    for ch, latin in shipped.items():
        assert CONFUSABLES.get(ch) == latin, unicodedata.name(ch)


def test_required_group_c_additions_present() -> None:
    for ch in _REQUIRED_GREEK + _REQUIRED_CYRILLIC:
        assert ch in CONFUSABLES, unicodedata.name(ch)


def test_greek_capital_iota_targets_latin_capital_i() -> None:
    """The Group C exploration counterexample (``Ιgnore``) needs ``Ι`` -> ``I``."""
    assert CONFUSABLES["\u0399"] == "I"
    assert CONFUSABLES["\u041d"] == "H"  # CYRILLIC CAPITAL LETTER EN
    assert CONFUSABLES["\u03bd"] == "v"  # GREEK SMALL LETTER NU


@pytest.mark.parametrize(
    ("ch", "latin", "name"),
    [pytest.param(*entry, id=f"U+{ord(entry[0]):04X}") for entry in confusables._ENTRIES],
)
def test_entry_is_a_cyrillic_or_greek_letter_folding_to_one_ascii_letter(
    ch: str, latin: str, name: str
) -> None:
    """The code point is the letter its declared name says, and the target is one ASCII
    letter of the same case."""
    assert unicodedata.name(ch) == name
    assert name.startswith(("CYRILLIC ", "GREEK "))
    category = unicodedata.category(ch)
    assert category in {"Lu", "Ll"}
    assert len(latin) == 1 and latin.isascii() and latin.isalpha()
    assert latin.isupper() if category == "Lu" else latin.islower()
    assert CONFUSABLES[ch] == latin


def test_table_is_immutable() -> None:
    with pytest.raises(TypeError):
        CONFUSABLES["\u03ba"] = "k"  # type: ignore[index]


def test_digest_is_deterministic_and_computed_from_the_table() -> None:
    assert CONFUSABLES_DIGEST == confusable_digest(CONFUSABLES)
    assert CONFUSABLES_DIGEST == confusable_digest(dict(reversed(list(CONFUSABLES.items()))))
    assert len(CONFUSABLES_DIGEST) == 64
    int(CONFUSABLES_DIGEST, 16)


def test_any_single_table_change_moves_the_digest() -> None:
    """Seeded mutations: removing, adding or re-targeting one entry changes the digest."""
    rng = random.Random(0x18_01)
    keys = sorted(CONFUSABLES)
    for _ in range(500):
        mutated = dict(CONFUSABLES)
        roll = rng.random()
        if roll < 1 / 3:
            del mutated[rng.choice(keys)]
        elif roll < 2 / 3:
            key = rng.choice(keys)
            others = [c for c in "abcdefghijklmnopqrstuvwxyz" if c != mutated[key]]
            mutated[key] = rng.choice(others)
        else:
            mutated[chr(rng.randint(0x0370, 0x04FF)) + "\u0301"] = rng.choice("ABCxyz")
        assert confusable_digest(mutated) != CONFUSABLES_DIGEST
