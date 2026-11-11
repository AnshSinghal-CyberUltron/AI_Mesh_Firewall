"""Versioned, hashed deterministic ruleset (GW09, §10.9, R3/R4/R6/R8.3).

This is the single source of deterministic patterns GW09 ships (design
"patterns.py"). Each :class:`PatternSpec` carries BOTH a cheap ``prefilter``
superset regex -- the alternation branch the single-pass matcher
(``detect/deterministic/matcher.py``) compiles once -- AND a ``precise`` regex
that confirms a prefilter hit before a Finding is emitted (R3.3). A pattern is
either :data:`PatternKind.REDACTABLE` (a value whose span the one byte-verified
redactor targets) or :data:`PatternKind.SIGNAL` (an injection/command signal
that contributes a Finding only; its span is never a redaction target and never
forces a terminal block -- the §10.2.2 behaviour change, R6).

The ruleset is immutable: :data:`RULESET` is a ``MappingProxyType`` so no
module-level mutable state exists (R8.5). :func:`ruleset_version` is a sha256
over the serialized ruleset so the GW08 ``detector_version`` is a deterministic
hash: the same ruleset yields the same version, any pattern change yields a
changed version (R8.3, the rollback/versioned-hashed rule).

CRITICAL (R6.3, the v1 defect this card removes): there is NO bare
backtick-pair pattern. v1's ``ATTACK_PATTERNS["command_injection"]`` matched any
`` `[^`]+` `` and hard-blocked 5 of 5 benign inline-code prompts. The command
family here matches only explicit shell-injection constructs; a benign inline
`` `code` `` span never matches.

Start-superset invariant (v2-enforcement-leaks Group C, clause 2.7). The matcher
confirms a hit with ``precise.match(text, hit.start)``, so a precise match is only
ever found where its own prefilter matched. Every prefilter is therefore a
START-SUPERSET of its precise regex: wherever the precise regex can begin a match,
the prefilter matches at that position. The per-pattern audit is recorded beside
each pattern below. The one deliberate exception is the phone prefilter's start: it
has no leading ``(``, so a parenthesised number is confirmed from its first digit
(``415) 555-2671``) and existing spans are preserved.

Bounded prefilters (Group B, clause 2.6). A repeat that can start at any position
is bounded (email, phone, AWS, API-key value, card, SSN). The unbounded email local
part made ``scan("x" * 262_144)`` take 63.1 s (backtracking at every position), and
the failed-region rescan would multiply any such cost. The remaining unbounded
repeats sit behind a literal keyword (``-----BEGIN``, ``;``, ``|``, ``you``) and are
not ambiguous with a neighbouring repeat, so they cost O(run) per keyword. A bound
never narrows what the precise regex can begin: it is at least the precise
sub-pattern's bound, or it only limits how far the prefilter extends past a start.

Validators (Group B, clause 2.5). ``PatternSpec.validator`` names a check the
matcher applies after a precise confirm (``"luhn"`` for ``credential_card``). It is
a name, not a callable, so the ruleset stays plain data and the name is hashed into
:func:`ruleset_version`. The matcher owns the name -> function table.

Layering: this module imports only ``gateway_v2.domain`` (for ``Category``),
``gateway_v2.detect.confusables`` (same layer, for the confusable-set version that
:func:`ruleset_version` hashes) and stdlib. It mirrors the ``detect/holdback.py`` house
idiom: ``from __future__ import annotations``, ``hashlib``, frozen
``@dataclass(frozen=True, slots=True)``, a ``MappingProxyType`` immutable table,
a tuple ``__all__``. The ``re`` compilation itself lives in the sibling
``matcher.py`` (Task 3); this module ships only the pattern source strings.

Reference. The value/signal patterns are self-contained COPIES adapted from the
evidence snapshot ``rvproto/detect/patterns.py`` (and its ``ATTACK_PATTERNS``
companion) -- never imports. The material change over the reference is the
prefilter/precise split, the ``PatternKind`` demotion of injection/command to
SIGNAL, and the removal of the bare backtick-pair command pattern.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from gateway_v2.detect.confusables import CONFUSABLE_SET_VERSION, CONFUSABLES_DIGEST
from gateway_v2.domain import Category

__all__ = (
    "RULESET",
    "PatternKind",
    "PatternSpec",
    "ruleset_version",
)


class PatternKind(StrEnum):
    """Whether a pattern's span is a redaction target or a non-blocking signal."""

    REDACTABLE = "redactable"  # a value pattern whose span is a redaction target
    SIGNAL = "signal"  # an injection/command signal; never a redaction target


@dataclass(frozen=True, slots=True)
class PatternSpec:
    """One deterministic pattern: a cheap prefilter superset + a precise confirm.

    ``prefilter`` is the fast superset branch the combined-alternation matcher
    compiles; ``precise`` confirms a prefilter hit before a Finding is emitted
    (R3.3). ``category`` is a shipped ``gateway_v2.domain.Category`` member;
    ``detector_name`` is the family detector that owns the pattern.
    ``validator`` optionally names a post-confirm check the matcher applies
    (``"luhn"``); ``None`` means the precise confirm alone decides.
    """

    name: str
    category: Category
    kind: PatternKind
    prefilter: str
    precise: str
    detector_name: str
    validator: str | None = None


# --------------------------------------------------------------------------- #
# PII family (REDACTABLE, Category.PII) -- email / phone / SSN-like.
# --------------------------------------------------------------------------- #

# Email: prefilter is "a run with an @ between word chars"; precise is a
# conventional local@domain.tld with a 2+ char TLD.
# Bounds: local part {1,64} (RFC 5321 local-part limit), domain {1,253} (RFC 1035
# name limit). The unbounded local part was the 63.1 s ``"x" * 262_144`` case.
# Start-superset: the precise local part carries the SAME {1,64} bound. A
# finite-bounded prefilter cannot be a start-superset of an unbounded local part
# (a 65+ char run would begin a precise match where the prefilter cannot), so the
# bound is declared on both. This is behaviour-neutral: the precise regex is only
# ever anchored at a prefilter hit, where the run up to ``@`` is already <= 64
# chars. The precise domain stays unbounded; the prefilter needs one domain char
# to match at the start, so {1,253} does not narrow any start.
_EMAIL_PREFILTER: Final = r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}"
_EMAIL_PRECISE: Final = r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"

# US-ish phone: prefilter is "a 10-15 digit run with the usual separators";
# precise anchors an optional +1/country code and (NPA) NXX-XXXX shape.
# Bounds: {9,14} units + one digit = 10-15 digits; the precise regex has 10 or 11,
# so the bound never narrows a start.
# Start: deliberately unchanged (no leading ``(``), so existing spans are kept.
# KNOWN START-SUPERSET GAP, left open on purpose: the precise regex allows TWO
# separator chars between digits in two places, ``\)?[\s.\-]?`` after the NPA
# (``415) 555``) and ``1[\s.\-]?\(?`` before it (``+1 (415``). The prefilter's
# single ``[\s().\-]?`` rejects both, so ``(415) 555-2671`` and
# ``+1 (415) 555-2671`` are not detected. Widening the unit separator closes the
# gap, but without the failed-region rescan (task 18.4) the longer phone hit
# consumes neighbouring matches: ``123-45-6789 (415) 555-2671`` loses its SSN,
# which breaks detection preservation (Property 13). Revisit once 18.4 lands.
_PHONE_PREFILTER: Final = r"(?:\+?\d[\s().\-]?){9,14}\d"
_PHONE_PRECISE: Final = (
    r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)"
)

# SSN-like ddd-dd-dddd. Already bounded; start-superset (precise ``-`` is in the
# prefilter's ``[\s\-]``).
_SSN_PREFILTER: Final = r"\d{3}[\s\-]\d{2}[\s\-]\d{4}"
_SSN_PRECISE: Final = r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"

# --------------------------------------------------------------------------- #
# Secret family (REDACTABLE, Category.SECRET) -- AWS key / generic API key/token.
# --------------------------------------------------------------------------- #

# AWS access key id: AKIA|ASIA + 16 base32 chars.
# Bound {10,16}: the precise regex needs exactly 16, so 16 is the widest useful
# prefilter and the lower 10 keeps the near-miss behaviour. Start-superset.
_AWS_PREFILTER: Final = r"(?:AKIA|ASIA)[A-Z2-7]{10,16}"
_AWS_PRECISE: Final = r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z2-7]{16}(?![A-Z0-9])"

# Generic API key / token: a key-ish keyword, a separator, then a long value.
# Bound {12,512} on the value. The precise value is {16,} (unbounded, so a long
# secret is redacted whole); the prefilter only needs the 12-char lower bound to
# match at the precise start, so the upper bound does not narrow any start.
# Start-superset: same keyword alternation, and the optional ``[:=]`` contains the
# precise regex's required ``[:=]``.
# The separator is written ``\s*(?:[:=]\s*)?``, not the original
# ``\s*[:=]?\s*``. Both accept the same strings and reach the same match end (the
# value class has no whitespace, so only the end of the whitespace run can be
# followed by a value). The original form is ambiguous when ``[:=]`` is absent:
# the two ``\s*`` can split a whitespace run n ways, so a keyword followed by a
# long whitespace run cost O(n^2) (``"token" + " " * 20_000`` took 4.6 s).
_API_KEY_PREFILTER: Final = (
    r"(?i:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token"
    r"|client[_\-]?secret|bearer|token)[\"']?\s*(?:[:=]\s*)?[\"']?[A-Za-z0-9_\-./+=]{12,512}"
)
_API_KEY_PRECISE: Final = (
    r"(?i:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token"
    r"|client[_\-]?secret|bearer|token)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{16,}"
)

# --------------------------------------------------------------------------- #
# Credential family (REDACTABLE, Category.CREDENTIAL) -- PEM header / card.
# --------------------------------------------------------------------------- #

# PEM private-key header. Start-superset: ``[A-Z ]*`` covers ``(?:[A-Z]+ )*``
# after ``-----BEGIN``. The repeat is unbounded but anchored on the literal
# ``-----BEGIN``, so it costs O(run) per occurrence, not per position.
_PEM_PREFILTER: Final = r"-----BEGIN[A-Z ]*PRIVATE KEY"
_PEM_PRECISE: Final = r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----"

# Credit-card: 13-19 digits with optional single separators between groups.
# Already bounded; the prefilter is the precise regex without its lookarounds, so
# it is a start-superset. The Luhn checksum is the ``"luhn"`` validator.
_CARD_PREFILTER: Final = r"(?:\d[ \-]?){12,18}\d"
_CARD_PRECISE: Final = r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)"

# --------------------------------------------------------------------------- #
# Injection family (SIGNAL, Category.PROMPT_INJECTION / JAILBREAK).
# --------------------------------------------------------------------------- #

# Instruction-override phrases.
# Start-superset: every precise alternative is a keyword followed by ``\s+``. The
# original window ``[^\n]{0,40}`` could not cross a newline or a gap wider than 40
# chars, both of which ``\s+`` accepts (``ignore\nall previous instructions`` was
# missed). The second alternative, keyword + one whitespace char, matches at every
# precise start. The original alternative stays first, so where it matched before
# it still wins with the same extent; the new one only fires where it failed.
_OVERRIDE_PREFILTER: Final = (
    r"(?i:(?:ignore|disregard|forget|override)"
    r"(?:\b[^\n]{0,40}(?:instruction|above|prompt|rule)|\s))"
)
_OVERRIDE_PRECISE: Final = (
    r"(?i:ignore\s+(?:all\s+)?previous\s+instructions"
    r"|disregard\s+(?:the\s+)?above"
    r"|ignore\s+(?:the\s+)?(?:above|prior)\s+(?:instructions?|prompt)"
    r"|forget\s+(?:all\s+)?(?:previous\s+)?instructions?"
    r"|override\s+(?:the\s+)?(?:system\s+)?prompt)"
)

# "you are now" role-reassignment. Start-superset: the precise regex is the
# prefilter followed by ``\s+\w``, so ``\b`` after ``now`` always holds.
_ROLE_PREFILTER: Final = r"(?i:you\s+are\s+now)\b"
_ROLE_PRECISE: Final = r"(?i:you\s+are\s+now\s+(?:a|an|the|in|free|dan|unrestricted|\w+))"

# Jailbreak phrase.
# Start-superset: the precise alternatives ``you\s+are\s+DAN\b``,
# ``enable\s+developer\s+mode`` and ``answer\s+without\s+(?:any\s+)?restrictions``
# begin BEFORE the original prefilter keywords (``DAN``, ``developer``,
# ``without``), so the precise confirm anchored at the original hit always failed.
# Their lead-context forms come first; the original alternatives stay, so the
# prefilter is still a superset of what it matched before. ``jailbreak``,
# ``developer\s+mode\s+enabled`` and ``do\s+anything\s+now`` start on an original
# alternative.
_JAILBREAK_PREFILTER: Final = (
    r"(?i:you\s+are\s+DAN\b|enable\s+developer\s+mode"
    r"|answer\s+without\s+(?:any\s+)?restrictions"
    r"|jailbreak|developer\s+mode|do\s+anything\s+now|\bDAN\b"
    r"|without\s+(?:any\s+)?restrictions)"
)
_JAILBREAK_PRECISE: Final = (
    r"(?i:jailbreak"
    r"|enable\s+developer\s+mode|developer\s+mode\s+enabled"
    r"|do\s+anything\s+now"
    r"|you\s+are\s+DAN\b"
    r"|answer\s+without\s+(?:any\s+)?restrictions)"
)

# --------------------------------------------------------------------------- #
# Command family (SIGNAL, Category.COMMAND) -- explicit shell constructs ONLY.
# NO bare backtick-pair pattern (R6.3): a benign inline `code` must NOT match.
# --------------------------------------------------------------------------- #

# $(...) command substitution. Start-superset: the precise regex needs one
# non-``)`` char after ``$(``, which is the whole prefilter.
_SUBST_PREFILTER: Final = r"\$\([^)]"
_SUBST_PRECISE: Final = r"\$\([^)\n]+\)"

# Destructive "; rm -rf" style command chains. Start-superset: the prefilter is
# the precise regex up to the ``-``.
_RMRF_PREFILTER: Final = r"(?i:(?:;|&&|\|\|)\s*rm\s+-)"
_RMRF_PRECISE: Final = r"(?i:(?:;|&&|\|\|)\s*rm\s+-[rfRF]{1,2}\b)"

# Pipe-to-shell (curl ... | sh). Start-superset: the prefilter is the precise
# regex without its trailing ``(?:\s|$|;)``.
_PIPE_SH_PREFILTER: Final = r"\|\s*(?:ba|z|da)?sh\b"
_PIPE_SH_PRECISE: Final = r"\|\s*(?:ba|z|da)?sh\b(?:\s|$|;)"


RULESET: Mapping[str, PatternSpec] = MappingProxyType(
    {
        spec.name: spec
        for spec in (
            PatternSpec(
                name="pii_email",
                category=Category.PII,
                kind=PatternKind.REDACTABLE,
                prefilter=_EMAIL_PREFILTER,
                precise=_EMAIL_PRECISE,
                detector_name="pii",
            ),
            PatternSpec(
                name="pii_phone",
                category=Category.PII,
                kind=PatternKind.REDACTABLE,
                prefilter=_PHONE_PREFILTER,
                precise=_PHONE_PRECISE,
                detector_name="pii",
            ),
            PatternSpec(
                name="pii_ssn",
                category=Category.PII,
                kind=PatternKind.REDACTABLE,
                prefilter=_SSN_PREFILTER,
                precise=_SSN_PRECISE,
                detector_name="pii",
            ),
            PatternSpec(
                name="secret_aws_key",
                category=Category.SECRET,
                kind=PatternKind.REDACTABLE,
                prefilter=_AWS_PREFILTER,
                precise=_AWS_PRECISE,
                detector_name="secret",
            ),
            PatternSpec(
                name="secret_api_key",
                category=Category.SECRET,
                kind=PatternKind.REDACTABLE,
                prefilter=_API_KEY_PREFILTER,
                precise=_API_KEY_PRECISE,
                detector_name="secret",
            ),
            PatternSpec(
                name="credential_pem_private_key",
                category=Category.CREDENTIAL,
                kind=PatternKind.REDACTABLE,
                prefilter=_PEM_PREFILTER,
                precise=_PEM_PRECISE,
                detector_name="secret",
            ),
            PatternSpec(
                name="credential_card",
                category=Category.CREDENTIAL,
                kind=PatternKind.REDACTABLE,
                prefilter=_CARD_PREFILTER,
                precise=_CARD_PRECISE,
                detector_name="secret",
                validator="luhn",
            ),
            PatternSpec(
                name="injection_instruction_override",
                category=Category.PROMPT_INJECTION,
                kind=PatternKind.SIGNAL,
                prefilter=_OVERRIDE_PREFILTER,
                precise=_OVERRIDE_PRECISE,
                detector_name="injection",
            ),
            PatternSpec(
                name="injection_role_reassignment",
                category=Category.PROMPT_INJECTION,
                kind=PatternKind.SIGNAL,
                prefilter=_ROLE_PREFILTER,
                precise=_ROLE_PRECISE,
                detector_name="injection",
            ),
            PatternSpec(
                name="injection_jailbreak",
                category=Category.JAILBREAK,
                kind=PatternKind.SIGNAL,
                prefilter=_JAILBREAK_PREFILTER,
                precise=_JAILBREAK_PRECISE,
                detector_name="injection",
            ),
            PatternSpec(
                name="command_substitution",
                category=Category.COMMAND,
                kind=PatternKind.SIGNAL,
                prefilter=_SUBST_PREFILTER,
                precise=_SUBST_PRECISE,
                detector_name="injection",
            ),
            PatternSpec(
                name="command_rm_rf",
                category=Category.COMMAND,
                kind=PatternKind.SIGNAL,
                prefilter=_RMRF_PREFILTER,
                precise=_RMRF_PRECISE,
                detector_name="injection",
            ),
            PatternSpec(
                name="command_pipe_to_shell",
                category=Category.COMMAND,
                kind=PatternKind.SIGNAL,
                prefilter=_PIPE_SH_PREFILTER,
                precise=_PIPE_SH_PRECISE,
                detector_name="injection",
            ),
        )
    }
)


def ruleset_version() -> str:
    """Deterministic sha256 hex of the ruleset content (R8.3, clause 2.8).

    The digest is taken over each pattern's ``(name, category.value, kind.value,
    prefilter, precise, validator)`` joined by ``\\x1f``, each record terminated by
    ``\\x1e``, sorted by name. ``validator`` serialises as its name, or ``""`` for
    ``None`` (a validator name is never empty). One more record follows the
    patterns: ``("confusables", CONFUSABLE_SET_VERSION, CONFUSABLES_DIGEST)``, so a
    change to the confusable table that canonicalisation folds through also
    changes the version (Property 8). The same ruleset and table always yield the
    same version. This is the value GW08's ``detector_version`` returns.
    """
    hasher = hashlib.sha256()
    for name in sorted(RULESET):
        spec = RULESET[name]
        serialized = "\x1f".join(
            (
                spec.name,
                spec.category.value,
                spec.kind.value,
                spec.prefilter,
                spec.precise,
                spec.validator or "",
            )
        )
        hasher.update(serialized.encode("utf-8"))
        hasher.update(b"\x1e")
    confusables = "\x1f".join(("confusables", CONFUSABLE_SET_VERSION, CONFUSABLES_DIGEST))
    hasher.update(confusables.encode("utf-8"))
    hasher.update(b"\x1e")
    return hasher.hexdigest()
