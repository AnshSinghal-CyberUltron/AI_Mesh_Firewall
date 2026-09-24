"""Deterministic pattern catalogue (one ruleset, versioned by hash).

Patterns use the common subset of Python `re` and Hyperscan (no lookaround,
no backreferences) so both engines compile the identical ruleset.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PatternSpec:
    detector: str
    regex: str
    caseless: bool = False
    validator: str | None = None  # "luhn" | "entropy"


_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"

PATTERNS: tuple[PatternSpec, ...] = (
    PatternSpec("pii.email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}\b"),
    PatternSpec(
        "pii.phone",
        r"(?:\+1[ .-]?)?(?:\([2-9][0-9]{2}\)|\b[2-9][0-9]{2})[ .-]?[0-9]{3}[ .-]?[0-9]{4}\b",
    ),
    PatternSpec("pii.ssn", r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b"),
    PatternSpec("pii.card", r"\b(?:[0-9][ -]?){12,18}[0-9]\b", validator="luhn"),
    PatternSpec("pii.ipv4", rf"\b(?:{_OCTET}\.){{3}}{_OCTET}\b"),
    PatternSpec("secret.aws", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    PatternSpec(
        "secret.github",
        r"\b(?:(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b",
    ),
    PatternSpec("secret.slack", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    PatternSpec("secret.google", r"\bAIza[0-9A-Za-z_-]{35}"),
    PatternSpec("secret.stripe", r"\b(?:sk|rk)_live_[0-9A-Za-z]{24,}\b"),
    PatternSpec("secret.jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    PatternSpec(
        "secret.pem",
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----",
    ),
    PatternSpec(
        "secret.generic",
        r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret"
        r"|password|passwd)[\"']?[ \t]{0,4}[:=][ \t]{0,4}[\"']?[A-Za-z0-9_/+=.-]{16,}",
        caseless=True,
        validator="entropy",
    ),
    PatternSpec(
        "injection.heuristic",
        r"\b(?:ignore|disregard|forget)[ \t]+(?:all[ \t]+|any[ \t]+|the[ \t]+)?(?:previous|prior|above"
        r"|earlier)[ \t]+(?:instructions|rules|directions|guidelines)\b"
        r"|\b(?:reveal|print|show)[ \t]+(?:your|the)[ \t]+(?:system|hidden)[ \t]+prompt\b"
        r"|\byou[ \t]+are[ \t]+now[ \t]+(?:DAN|in[ \t]+developer[ \t]+mode)\b"
        r"|\b(?:system|developer)[ \t]+override\b",
        caseless=True,
    ),
)

PEM_HEADERS: tuple[str, ...] = tuple(
    f"-----BEGIN {kind}PRIVATE KEY{blk}-----"
    for kind in ("", "RSA ", "EC ", "DSA ", "OPENSSH ", "ENCRYPTED ", "PGP ")
    for blk in ("", " BLOCK")
)

RULESET_VERSION = hashlib.sha256(
    "\n".join(f"{p.detector}\t{p.regex}\t{p.caseless}\t{p.validator}" for p in PATTERNS).encode()
).hexdigest()[:12]
