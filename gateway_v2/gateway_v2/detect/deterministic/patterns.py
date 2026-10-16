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

Layering: this module imports only ``gateway_v2.domain`` (for ``Category``) and
stdlib. It mirrors the ``detect/holdback.py`` house idiom: ``from __future__
import annotations``, ``hashlib``, frozen
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
    """

    name: str
    category: Category
    kind: PatternKind
    prefilter: str
    precise: str
    detector_name: str


# --------------------------------------------------------------------------- #
# PII family (REDACTABLE, Category.PII) -- email / phone / SSN-like.
# --------------------------------------------------------------------------- #

# Email: prefilter is "a run with an @ between word chars"; precise is a
# conventional local@domain.tld with a 2+ char TLD.
_EMAIL_PREFILTER: Final = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+"
_EMAIL_PRECISE: Final = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"

# US-ish phone: prefilter is "a 10+ digit run with the usual separators";
# precise anchors an optional +1/country code and (NPA) NXX-XXXX shape.
_PHONE_PREFILTER: Final = r"(?:\+?\d[\s().\-]?){9,}\d"
_PHONE_PRECISE: Final = (
    r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)"
)

# SSN-like ddd-dd-dddd.
_SSN_PREFILTER: Final = r"\d{3}[\s\-]\d{2}[\s\-]\d{4}"
_SSN_PRECISE: Final = r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"

# --------------------------------------------------------------------------- #
# Secret family (REDACTABLE, Category.SECRET) -- AWS key / generic API key/token.
# --------------------------------------------------------------------------- #

# AWS access key id: AKIA|ASIA + 16 base32 chars.
_AWS_PREFILTER: Final = r"(?:AKIA|ASIA)[A-Z2-7]{10,}"
_AWS_PRECISE: Final = r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z2-7]{16}(?![A-Z0-9])"

# Generic API key / token: a key-ish keyword, a separator, then a long value.
_API_KEY_PREFILTER: Final = (
    r"(?i:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token"
    r"|client[_\-]?secret|bearer|token)[\"']?\s*[:=]?\s*[\"']?[A-Za-z0-9_\-./+=]{12,}"
)
_API_KEY_PRECISE: Final = (
    r"(?i:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token"
    r"|client[_\-]?secret|bearer|token)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{16,}"
)

# --------------------------------------------------------------------------- #
# Credential family (REDACTABLE, Category.CREDENTIAL) -- PEM header / card.
# --------------------------------------------------------------------------- #

# PEM private-key header.
_PEM_PREFILTER: Final = r"-----BEGIN[A-Z ]*PRIVATE KEY"
_PEM_PRECISE: Final = r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----"

# Credit-card: 13-19 digits with optional single separators between groups.
_CARD_PREFILTER: Final = r"(?:\d[ \-]?){12,18}\d"
_CARD_PRECISE: Final = r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)"

# --------------------------------------------------------------------------- #
# Injection family (SIGNAL, Category.PROMPT_INJECTION / JAILBREAK).
# --------------------------------------------------------------------------- #

# Instruction-override phrases.
_OVERRIDE_PREFILTER: Final = (
    r"(?i:(?:ignore|disregard|forget|override)\b[^\n]{0,40}"
    r"(?:instruction|above|prompt|rule))"
)
_OVERRIDE_PRECISE: Final = (
    r"(?i:ignore\s+(?:all\s+)?previous\s+instructions"
    r"|disregard\s+(?:the\s+)?above"
    r"|ignore\s+(?:the\s+)?(?:above|prior)\s+(?:instructions?|prompt)"
    r"|forget\s+(?:all\s+)?(?:previous\s+)?instructions?"
    r"|override\s+(?:the\s+)?(?:system\s+)?prompt)"
)

# "you are now" role-reassignment.
_ROLE_PREFILTER: Final = r"(?i:you\s+are\s+now)\b"
_ROLE_PRECISE: Final = r"(?i:you\s+are\s+now\s+(?:a|an|the|in|free|dan|unrestricted|\w+))"

# Jailbreak phrase.
_JAILBREAK_PREFILTER: Final = (
    r"(?i:jailbreak|developer\s+mode|do\s+anything\s+now|\bDAN\b"
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

# $(...) command substitution.
_SUBST_PREFILTER: Final = r"\$\([^)]"
_SUBST_PRECISE: Final = r"\$\([^)\n]+\)"

# Destructive "; rm -rf" style command chains.
_RMRF_PREFILTER: Final = r"(?i:(?:;|&&|\|\|)\s*rm\s+-)"
_RMRF_PRECISE: Final = r"(?i:(?:;|&&|\|\|)\s*rm\s+-[rfRF]{1,2}\b)"

# Pipe-to-shell (curl ... | sh).
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
    """Deterministic sha256 hex of the ruleset content (R8.3).

    The digest is taken over each pattern's ``(name, category.value, kind.value,
    prefilter, precise)`` serialized and sorted by name, so the same ruleset
    always yields the same version and any pattern change yields a changed
    version. This is the value GW08's ``detector_version`` returns.
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
            )
        )
        hasher.update(serialized.encode("utf-8"))
        hasher.update(b"\x1e")
    return hasher.hexdigest()
