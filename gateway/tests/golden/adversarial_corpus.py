"""Adversarial corpus for the chat-pipeline stress program (R2).

Hammers the FROZEN deterministic firewall (scanner.py / patterns.py) in-process.
Every entry is derived from docs/stress/ATTACK_LANDSCAPE.md (gap register G1-G14)
and was confirmed by running the real code (see test_adversarial_attacks.py).

SECURITY: only SYNTHETIC secrets appear here (never the runtime OpenRouter key).
The obfuscation transforms below are the payload-shaping primitives; the corpus
records the DESIRED security property, and the test marks currently-broken cases
xfail(strict) so R4 fixes flip them to XPASS and force un-xfailing.
"""
from __future__ import annotations

import base64
import unicodedata

# ---------------------------------------------------------------- synthetic secrets
SSN = "123-45-6789"
EMAIL = "john.doe@example.com"
CARD = "4111 1111 1111 1111"                                  # test Visa
OAI_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"          # fake classic OpenAI shape (NOT sk-or-)

# ---------------------------------------------------------------- obfuscation transforms
ZWSP = "​"
_FW = {chr(c): chr(c - 0x20 + 0xFF00) for c in range(0x21, 0x7F)}   # ASCII -> fullwidth
_HOMO = {"a": "а", "e": "е", "o": "о", "p": "р",
         "c": "с", "x": "х", "i": "і"}               # Latin -> Cyrillic look-alikes


def zero_width(s: str) -> str:
    """Insert a zero-width space between every character."""
    return ZWSP.join(list(s))


def nbsp_hyphen(s: str) -> str:
    """Replace ASCII hyphen with U+2011 non-breaking hyphen (NFKC does NOT fold it to '-')."""
    return s.replace("-", "‑")


def fullwidth(s: str) -> str:
    """Map ASCII printables to their fullwidth (U+FF01-FF5E) forms."""
    return "".join(_FW.get(c, c) for c in s)


def homoglyph(s: str) -> str:
    """Substitute Latin letters with Cyrillic confusables (NFKC does NOT fold these)."""
    return "".join(_HOMO.get(c, c) for c in s.lower())


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ---------------------------------------------------------------- test-only leak probe
# A THOROUGH canonicalizer used ONLY by the tests to decide "did sensitive data survive
# redaction in any recoverable form?" It is deliberately independent of (and stronger than)
# the product's current normalization — that asymmetry is exactly gap G1.
_DASHES = "‐‑‒–—―−﹘﹣－"
_HOMO_BACK = {v: k for k, v in _HOMO.items()}


def canon_probe(text: str) -> str:
    out = unicodedata.normalize("NFKC", text)
    out = "".join(c for c in out if unicodedata.category(c) != "Cf")      # strip zero-width / bidi / tags
    out = "".join("-" if c in _DASHES else c for c in out)                # fold unicode dashes -> '-'
    out = "".join(" " if unicodedata.category(c) == "Zs" else c for c in out)  # fold unicode spaces
    out = "".join(_HOMO_BACK.get(c, c) for c in out)                      # fold confusables back
    return out
