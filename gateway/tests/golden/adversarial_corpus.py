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


# G95: Greek lowercase look-alikes. epsilon(e)/eta(n)/gamma(y) were the missing folds
# that let a Greek-homoglyph injection ("ignorε all prεvious instructions") bypass BOTH
# the Tier-0.5 injection scan and detect_pii/detect_secrets. NFKC leaves these as-is.
_GREEK_HOMO = {"a": "α", "e": "ε", "o": "ο", "i": "ι", "p": "ρ", "n": "η",
               "y": "γ", "v": "ν", "u": "υ", "x": "χ", "t": "τ", "k": "κ"}


def greek_homoglyph(s: str) -> str:
    """Substitute Latin letters with Greek confusables (epsilon/eta/gamma/… — NFKC-identity)."""
    return "".join(_GREEK_HOMO.get(c, c) for c in s.lower())


def base32(s: str) -> str:
    """G97: base32-encode (A-Z2-7). Prompt-laundering the base64/hex decode used to miss."""
    return base64.b32encode(s.encode()).decode()


def base85(s: str) -> str:
    """G98: RFC1924 base85-encode. Alphabet overlaps base64 so the base64 decode missed it."""
    return base64.b85encode(s.encode()).decode()


def ascii85(s: str, adobe: bool = False) -> str:
    """G100: Ascii85 (a85) encode; adobe=True adds the <~...~> frame."""
    return base64.a85encode(s.encode(), adobe=adobe).decode()


# G96: Mathematical Alphanumeric Symbols (U+1D400+) — "fancy" unicode a jailbreak is often
# pasted in (𝓲𝓰𝓷𝓸𝓻𝓮 / 𝕚𝕘𝕟𝕠𝕣𝕖 / 𝚒𝚐𝚗𝚘𝚛𝚎 …). NFKC compat-folds these to ASCII, so the firewall
# must still block. Lowercase bases; a few styles place letters as letterlike symbols OUTSIDE
# the block (holes) which we substitute so the string uses only ASSIGNED codepoints (an LLM
# reads those; the unassigned hole positions it would not).
_MATH_STYLE_BASE = {
    "bold": 0x1D41A, "italic": 0x1D44E, "bold_italic": 0x1D482, "script": 0x1D4B6,
    "bold_script": 0x1D4EA, "fraktur": 0x1D51E, "double_struck": 0x1D552,
    "bold_fraktur": 0x1D586, "sans": 0x1D5BA, "sans_bold": 0x1D5EE,
    "sans_italic": 0x1D622, "sans_bold_italic": 0x1D656, "monospace": 0x1D68A,
}
_MATH_STYLE_HOLES = {
    "italic": {"h": "ℎ"},
    "script": {"e": "ℯ", "g": "ℊ", "o": "ℴ"},
}


def math_styled(s: str, style: str) -> str:
    """Render ASCII letters in a Mathematical-Alphanumeric style (NFKC folds back to ASCII)."""
    base = _MATH_STYLE_BASE[style]
    holes = _MATH_STYLE_HOLES.get(style, {})
    out = []
    for c in s:
        cl = c.lower()
        if cl in holes:
            out.append(holes[cl])
        elif "a" <= cl <= "z":
            out.append(chr(base + ord(cl) - 97))
        else:
            out.append(c)
    return "".join(out)


_BIDI_CTRLS = ("‮", "⁧", "‏", "؜", "⁩", "‭", "⁦")
# RLO, RLI, RLM, ALM, PDI, LRO, LRI — all category Cf.


def bidi(s: str) -> str:
    """Interleave Unicode bidirectional/format controls (all category Cf) between characters.
    The firewall canonicaliser drops Cf before matching, so a value smuggled this way must
    still be detected and masked out of the egress bytes."""
    return "".join(c + _BIDI_CTRLS[i % len(_BIDI_CTRLS)] for i, c in enumerate(s))


def combining(s: str) -> str:
    """Append a combining mark (category Mn, U+0301) after each character. Mn is also dropped
    by the canonicaliser, so the underlying value must still be detected/redacted."""
    return "".join(c + "́" for c in s)


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
