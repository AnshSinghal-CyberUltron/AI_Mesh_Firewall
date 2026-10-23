"""v2-enforcement-leaks task 21.2: the versioned-confusable property (design Property 8).

Property 8 (verbatim, design.md).
    For any change to the confusable table, ``detector_version`` SHALL change. For any
    character in the table, ``canonicalize`` SHALL fold it to its declared Latin letter
    with a 1:1 span record.

    **Validates: Requirements 2.8**

What this file owns, and what it deliberately does NOT re-assert
----------------------------------------------------------------
Three sibling tests already cover the table-local and fold-local halves; this file does
NOT duplicate them. It adds the one half none of them has: that the confusable digest is
actually WIRED INTO ``ruleset_version`` -- i.e. that a change to the table moves
``detector_version`` end to end.

* ``test_v2_leaks_confusables.py`` proves the digest is deterministic, that a single
  add/remove/re-target moves the DIGEST, table immutability and required coverage. It
  stops at the digest; it never touches ``ruleset_version``.
* ``test_v2_leaks_confusable_fold.py`` proves the per-entry 1:1 fold through
  ``canonicalize`` (span record ``((0,0,1),(1,1,2),(2,2,3))`` for ``x<ch>y``),
  exhaustively over every entry, plus ``CONFUSABLE_FOLD is CONFUSABLES``.
* ``test_deterministic_detectors_gw09.py::test_property7_*`` proves a mutated PATTERN
  moves ``ruleset_version`` -- but it mutates the precise regex, never the confusable
  record, so it does not prove the table is in the hash input.

So task 21.2 here proves:

1. The confusable table is in the ``ruleset_version`` hash input: the real version
   equals a locally reproduced hash model built from the REAL digest (faithfulness),
   and a model built with ANY other digest differs -- so substituting a mutated table's
   digest WOULD change ``detector_version`` (the Property-8 "any change -> version
   changes" half, proven without mutating the frozen module).
2. The end-to-end link: the three concrete detectors' ``detector_version`` all equal
   ``ruleset_version()``, and that value embeds the current ``CONFUSABLES_DIGEST``.

The 1:1-fold half of Property 8 is covered exhaustively by
``test_v2_leaks_confusable_fold.py`` and is NOT re-asserted here; instead a seeded
SAMPLE of table characters is folded so the Property-8 statement is self-contained in
this file, framed as seeded sampling rather than a copy of the exhaustive parametrize.

House idiom: a seeded ``random.Random``, at least 10,000 iterations, the seed in every
assertion message, no hypothesis. The hash model mirrors ``patterns.ruleset_version``
exactly (same pattern as ``test_deterministic_detectors_gw09.py::_hash_model``) and is
built over a copyable list so a mutated-copy digest changes the model while the frozen
``CONFUSABLES`` / ``RULESET`` are never touched.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence

from gateway_v2.detect.base import Detector
from gateway_v2.detect.confusables import (
    CONFUSABLE_SET_VERSION,
    CONFUSABLES,
    CONFUSABLES_DIGEST,
    confusable_digest,
)
from gateway_v2.detect.deterministic import (
    DEFAULT_BUDGET,
    RULESET,
    InjectionSignalDetector,
    PiiDetector,
    SecretDetector,
    canonicalize,
    ruleset_version,
)

_SEED = 0x21_2  # v2-enforcement-leaks task 21.2 (versioned confusable property)
_ITERS = 10_000


def _all_detectors() -> list[Detector]:
    """The three concrete deterministic detectors as ``Detector`` protocol objects."""
    return [PiiDetector(), SecretDetector(), InjectionSignalDetector()]


def _version_model(
    serialized_patterns: Sequence[tuple[str, str, str, str, str, str]],
    *,
    confusable_set_version: str,
    confusables_digest: str,
) -> str:
    """Reproduce ``patterns.ruleset_version`` over a COPYABLE model (no module mutation).

    Mirrors the production hash exactly: sha256 over each pattern's
    ``(name, category.value, kind.value, prefilter, precise, validator or "")`` joined
    by ``\\x1f`` and terminated by ``\\x1e``, patterns sorted by name, then the one
    ``("confusables", confusable_set_version, confusables_digest)`` record. Parameterising
    the confusable record lets a test substitute a different digest WITHOUT touching the
    frozen ``CONFUSABLES`` table or module, which is the Property-8 "table change ->
    version change" half.
    """
    hasher = hashlib.sha256()
    for record in sorted(serialized_patterns, key=lambda r: r[0]):
        hasher.update("\x1f".join(record).encode("utf-8"))
        hasher.update(b"\x1e")
    confusables = ("confusables", confusable_set_version, confusables_digest)
    hasher.update("\x1f".join(confusables).encode("utf-8"))
    hasher.update(b"\x1e")
    return hasher.hexdigest()


def _base_pattern_model() -> list[tuple[str, str, str, str, str, str]]:
    """The serialized real ``RULESET`` as a copyable list of records."""
    return [
        (
            spec.name,
            spec.category.value,
            spec.kind.value,
            spec.prefilter,
            spec.precise,
            spec.validator or "",
        )
        for spec in RULESET.values()
    ]


# --------------------------------------------------------------------------- #
# Preconditions: the local hash model is faithful to the shipped ruleset_version.
# --------------------------------------------------------------------------- #


def test_version_model_reproduces_the_real_ruleset_version() -> None:
    """The local model built from the REAL digest equals the shipped ``ruleset_version``.

    This is the faithfulness anchor: without it, a "model differs on a mutated digest"
    result would prove nothing about the real version.
    """
    modelled = _version_model(
        _base_pattern_model(),
        confusable_set_version=CONFUSABLE_SET_VERSION,
        confusables_digest=CONFUSABLES_DIGEST,
    )
    assert modelled == ruleset_version(), (
        f"seed={_SEED}: local version model does not reproduce ruleset_version() -- "
        "the model is not faithful to the shipped hash"
    )


def test_detector_version_is_ruleset_version_and_embeds_the_confusable_digest() -> None:
    """End-to-end Property-8 link (one example anchor for the seeded property).

    All three concrete detectors expose ``detector_version == ruleset_version()``, and
    that value embeds the current ``CONFUSABLES_DIGEST`` -- substituting any other digest
    into the model changes it, so the digest is genuinely in the hash input.
    """
    expected = ruleset_version()
    for det in _all_detectors():
        assert det.detector_version == expected, (
            f"seed={_SEED}: {type(det).__name__}.detector_version != ruleset_version()"
        )

    base = _base_pattern_model()
    with_real = _version_model(
        base,
        confusable_set_version=CONFUSABLE_SET_VERSION,
        confusables_digest=CONFUSABLES_DIGEST,
    )
    assert with_real == expected, f"seed={_SEED}: model with the real digest != detector_version"

    # A single different digest byte changes the whole version -> the digest is wired in.
    other_digest = ("0" if CONFUSABLES_DIGEST[0] != "0" else "1") + CONFUSABLES_DIGEST[1:]
    with_other = _version_model(
        base,
        confusable_set_version=CONFUSABLE_SET_VERSION,
        confusables_digest=other_digest,
    )
    assert with_other != expected, (
        f"seed={_SEED}: a different confusable digest did not change detector_version -- "
        "the table is not in the ruleset_version hash input"
    )


# --------------------------------------------------------------------------- #
# Property 8 (version half): any change to the confusable table changes detector_version.
# --------------------------------------------------------------------------- #


def test_property8_table_change_changes_detector_version() -> None:
    """**Validates: Requirements 2.8**

    For a seeded stream of mutated confusable tables (remove / add / re-target one
    random entry), the version ``ruleset_version`` WOULD produce with the mutated table's
    digest substituted differs from the real ``ruleset_version()``; and the real version
    always equals the model built from the REAL digest (faithfulness re-checked each
    iteration). The frozen ``CONFUSABLES`` table and ``RULESET`` are never mutated -- only
    a copied dict and a copied pattern-record list are.

    Coverage counters assert every mutation KIND (remove, re-target, add) was exercised,
    so a green result is not vacuous.
    """
    rng = random.Random(_SEED)
    real_version = ruleset_version()
    base_patterns = _base_pattern_model()
    keys = sorted(CONFUSABLES)
    ascii_targets = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    # The real version is reproduced by the model built from the real digest.
    assert (
        _version_model(
            base_patterns,
            confusable_set_version=CONFUSABLE_SET_VERSION,
            confusables_digest=CONFUSABLES_DIGEST,
        )
        == real_version
    ), f"seed={_SEED}: base model != ruleset_version() before mutation"

    removed = retargeted = added = 0

    for i in range(_ITERS):
        mutated = dict(CONFUSABLES)
        roll = rng.random()
        if roll < 1 / 3:
            kind = "remove"
            del mutated[rng.choice(keys)]
            removed += 1
        elif roll < 2 / 3:
            kind = "retarget"
            key = rng.choice(keys)
            others = [c for c in ascii_targets if c != mutated[key]]
            mutated[key] = rng.choice(others)
            retargeted += 1
        else:
            kind = "add"
            # A fresh Cyrillic/Greek-range code point with a combining mark so it is a
            # NEW key not already in the table, folded to a random ASCII target.
            new_key = chr(rng.randint(0x0370, 0x04FF)) + "\u0301"
            mutated[new_key] = rng.choice(ascii_targets)
            added += 1

        mutated_digest = confusable_digest(mutated)
        msg = f"seed={_SEED} iter={i} kind={kind} mutated_digest={mutated_digest[:12]}"

        # A changed table yields a changed digest (sanity on the mutation itself)...
        assert mutated_digest != CONFUSABLES_DIGEST, f"{msg}: mutation did not move the digest"

        # ...and the version the ruleset WOULD carry with that table differs from the
        # real version. This is the Property-8 "any table change -> detector_version
        # changes" half, proven without mutating the frozen module.
        modelled = _version_model(
            base_patterns,
            confusable_set_version=CONFUSABLE_SET_VERSION,
            confusables_digest=mutated_digest,
        )
        assert modelled != real_version, (
            f"{msg}: a confusable-table change did not change detector_version"
        )

        # Faithfulness holds every iteration: the frozen table's model is still the real
        # version (nothing leaked into the shared frozen state).
        assert ruleset_version() == real_version, f"{msg}: ruleset_version() drifted"

    coverage = f"seed={_SEED} removed={removed} retargeted={retargeted} added={added}"
    assert removed > 0, coverage
    assert retargeted > 0, coverage
    assert added > 0, coverage


# --------------------------------------------------------------------------- #
# Property 8 (fold half): a table character folds to its Latin letter with a 1:1 span
# record. The EXHAUSTIVE per-entry version lives in test_v2_leaks_confusable_fold.py and
# is NOT re-asserted; this seeded sample keeps the Property-8 statement self-contained.
# --------------------------------------------------------------------------- #


def test_property8_sampled_table_characters_fold_one_to_one() -> None:
    """**Validates: Requirements 2.8**

    Fold half of Property 8, over a SEEDED SAMPLE of table characters (not a copy of the
    exhaustive per-entry test in ``test_v2_leaks_confusable_fold.py``): embedding ``<ch>``
    between two ASCII letters canonicalises it to its declared Latin letter with a 1:1
    span map, so the character count and span records are unchanged.
    """
    rng = random.Random(_SEED ^ 0x0F_0F)
    items = sorted(CONFUSABLES.items())
    for i in range(_ITERS):
        ch, latin = rng.choice(items)
        text = f"x{ch}y"
        canonical, span_map = canonicalize(text, DEFAULT_BUDGET)
        msg = f"seed={_SEED ^ 0x0F0F} iter={i} ch=U+{ord(ch):04X} latin={latin}"
        assert canonical == f"x{latin}y", msg
        # 1:1 span record: each of the three characters maps to exactly its own index.
        assert span_map.records == ((0, 0, 1), (1, 1, 2), (2, 2, 3)), msg
