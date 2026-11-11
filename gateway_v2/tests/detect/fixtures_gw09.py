"""Labeled Fixture_Corpus for the GW09 deterministic detectors (R7).

The representative LOCAL corpus from which the per-family recall/FPR scorer in
``test_deterministic_detectors_gw09.py`` computes its OUTCOMES (R7.2/R7.5 -- the
scorer reads the real detectors over these fixtures; no pattern is tuned to make
an individual case pass). Three labeled families:

* :data:`BENIGN_DEV` -- the Developer_Traffic_Family (R7.1): benign inline-code /
  backtick prompts. These must yield NO ``REDACTABLE`` finding and NO terminal
  block. This is the family whose FPR is reported SEPARATELY (R7.3) and must be
  materially below v1's bare-backtick behaviour (v1 blocked 5/5 -> FPR 1.0).
* :data:`POSITIVE_PII_SECRET` -- positive PII / secret / credential fixtures, each
  labeled with the family it should hit (:data:`FAMILY_PII` / :data:`FAMILY_SECRET`
  / :data:`FAMILY_CREDENTIAL`).
* :data:`OBFUSCATED` -- encoded / obfuscated positives (percent, double-percent,
  zero-width-interleaved, fullwidth, "at/dot", homoglyph), each labeled with the
  family + the plaintext it decodes to, so a test can assert detection OR an
  explicit budget reject -- never a silent miss (Property 4 / LGW09-4).

CRITICAL corpus caveat (learned from the shipped ``canonicalize``). The single
canonicalization includes a `` at ``->``@`` and `` dot ``->``.`` email
de-obfuscation decoder. A benign sentence containing the standalone word
``at``/``dot`` surrounded by spaces is rewritten in canonical space. So:

* positive email fixtures use a LITERAL ``user@example.com`` (no `` at ``), and an
  OBFUSCATED ``"user at example dot com"`` fixture exercises the de-obfuscation;
* benign Developer_Traffic fixtures are kept CODE-ISH (backticks, inline snippets)
  and deliberately avoid an incidental standalone `` at ``/`` dot `` that would
  rewrite the text or trip a REDACTABLE pattern.

This module is PURE DATA: tuples of strings plus small frozen label records and
helpers. It imports only the shipped ``Category`` so a fixture's expected family
is a shipped domain member, never a bare string.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.domain.category import Category

__all__ = (
    "BENIGN_DEV",
    "FAMILY_CREDENTIAL",
    "FAMILY_PII",
    "FAMILY_SECRET",
    "OBFUSCATED",
    "POSITIVE_PII_SECRET",
    "ObfuscatedCase",
    "PositiveCase",
)

# --------------------------------------------------------------------------- #
# Family labels -- the shipped detector ``_family`` ids (patterns.py
# ``detector_name``). "secret" is the single detector family that owns BOTH the
# SECRET and CREDENTIAL categories; FAMILY_CREDENTIAL is the category label a
# fixture carries so the scorer can report credential outcomes distinctly.
# --------------------------------------------------------------------------- #

FAMILY_PII: str = "pii"
FAMILY_SECRET: str = "secret"
FAMILY_CREDENTIAL: str = "credential"


# --------------------------------------------------------------------------- #
# BENIGN_DEV -- the Developer_Traffic_Family (R7.1 / R7.3 / LGW09-1).
#
# Benign inline-code / backtick prompts. Each MUST produce zero REDACTABLE
# findings and zero terminal blocks (the v2 fix: a bare `` `code` `` span is not
# a command pattern anymore). Deliberately code-ish and free of a standalone
# `` at ``/`` dot `` that canonicalize would rewrite.
# --------------------------------------------------------------------------- #

BENIGN_DEV: tuple[str, ...] = (
    "fix this: `const x = 1`",
    "what does `arr.map(f)` do?",
    "run `pytest -q` for me",
    "explain the `rm` builtin",
    "is the regex `[a-z]+` right?",
    "why does `foo().bar()` throw?",
    "what's `git log --oneline`?",
)


# --------------------------------------------------------------------------- #
# POSITIVE_PII_SECRET -- labeled positive fixtures (R7.1).
#
# Each case carries the surrounding prompt text, the family it should hit, the
# shipped Category it should carry, and the planted plaintext value the span must
# cover (used by the span-soundness and recall checks). The planted value is a
# substring of ``text`` for every direct (non-obfuscated) positive.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PositiveCase:
    """A direct positive fixture: ``planted`` is a literal substring of ``text``."""

    text: str
    family: str
    category: Category
    planted: str


POSITIVE_PII_SECRET: tuple[PositiveCase, ...] = (
    PositiveCase(
        # No standalone " at " / " dot " -- those would trigger canonicalize's
        # email de-obfuscation and rewrite the literal address.
        text="contact user@example.com regarding the invoice",
        family=FAMILY_PII,
        category=Category.PII,
        planted="user@example.com",
    ),
    PositiveCase(
        text="my cell is 415-555-2671 call anytime",
        family=FAMILY_PII,
        category=Category.PII,
        planted="415-555-2671",
    ),
    PositiveCase(
        text="the ssn on file is 123-45-6789 verify it",
        family=FAMILY_PII,
        category=Category.PII,
        planted="123-45-6789",
    ),
    PositiveCase(
        text="rotate the key AKIAIOSFODNN7EXAMPLE in prod",
        family=FAMILY_SECRET,
        category=Category.SECRET,
        planted="AKIAIOSFODNN7EXAMPLE",
    ),
    PositiveCase(
        text="config has api_key=sk_live_abcdef0123456789 inline",
        family=FAMILY_SECRET,
        category=Category.SECRET,
        planted="api_key=sk_live_abcdef0123456789",
    ),
    PositiveCase(
        text="paste: -----BEGIN RSA PRIVATE KEY----- then the body",
        family=FAMILY_SECRET,
        category=Category.CREDENTIAL,
        planted="-----BEGIN RSA PRIVATE KEY-----",
    ),
    # Card number: carried as a labeled credential positive. The phone prefilter
    # is a digit-run superset that fires FIRST on a bare card, but its precise
    # confirm fails over the PAN; the failed-region rescan then recovers the
    # ``credential_card`` match and the Luhn validator confirms it (the PAN here
    # is Luhn-valid). So a standalone card IS emitted by ``scan`` as a CREDENTIAL
    # finding spanning the whole PAN -- a true OUTCOME of the matcher's rescan,
    # scored (not tuned) by the scorer (v2-enforcement-leaks Group B, 18.4).
    PositiveCase(
        text="charge the card 4111 1111 1111 1111 today",
        family=FAMILY_SECRET,
        category=Category.CREDENTIAL,
        planted="4111 1111 1111 1111",
    ),
)


# --------------------------------------------------------------------------- #
# OBFUSCATED -- encoded / obfuscated positives (R7.1 / Property 4 / LGW09-4).
#
# Each case carries the obfuscated prompt text, the family it should hit, and the
# plaintext the obfuscation decodes to under the declared Decoder_Set. The test
# asserts detection-within-budget OR an explicit budget reject -- never a silent
# miss. ``plaintext`` is what the planted value looks like AFTER canonicalization,
# used to assert the detected canonical value and span soundness.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ObfuscatedCase:
    """An obfuscated positive: ``plaintext`` is the decoded canonical value."""

    text: str
    family: str
    category: Category
    plaintext: str
    note: str


OBFUSCATED: tuple[ObfuscatedCase, ...] = (
    ObfuscatedCase(
        text="mail %75%73%65%72@example.com now",
        family=FAMILY_PII,
        category=Category.PII,
        plaintext="user@example.com",
        note="percent-encoded local part",
    ),
    ObfuscatedCase(
        text="mail %2575%2573%2565%2572@example.com now",
        family=FAMILY_PII,
        category=Category.PII,
        plaintext="user@example.com",
        note="double percent-encoded local part",
    ),
    ObfuscatedCase(
        text="mail us\u200ber@example.com now",
        family=FAMILY_PII,
        category=Category.PII,
        plaintext="user@example.com",
        note="zero-width space interleaved",
    ),
    ObfuscatedCase(
        text="mail \uff55\uff53\uff45\uff52@example.com now",
        family=FAMILY_PII,
        category=Category.PII,
        plaintext="user@example.com",
        note="fullwidth local part",
    ),
    ObfuscatedCase(
        text="reach user at example dot com for details",
        family=FAMILY_PII,
        category=Category.PII,
        plaintext="user@example.com",
        note="at/dot email obfuscation",
    ),
    ObfuscatedCase(
        text="mail \u0440user@example.com now",
        family=FAMILY_PII,
        category=Category.PII,
        plaintext="puser@example.com",
        note="cyrillic homoglyph prefix folds to ascii 'p'",
    ),
)
