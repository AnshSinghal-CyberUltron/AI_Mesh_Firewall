"""Canonicalisation must not change behaviour when it gets faster — task 1F.

`_canonicalize_with_map` is 67% of a guard pass (cProfile: 0.324 s of 0.482 s over 100
scans of 525 chars) and calls `unicodedata.category` twice per character in a pure-Python
loop. 1F adds an ASCII identity fast path.

This file freezes a VERBATIM COPY of the pre-1F implementation as
`_reference_canonicalize` and asserts the live one agrees with it byte-for-byte on
`(canonical_text, index_map)`. A snapshot of expected outputs would only cover the inputs
captured at snapshot time; a reference implementation covers every input anyone adds
later, including the adversarial Unicode classes below.

The classes exercised are exactly the ones the function exists to defeat, each named for
the guard that motivated it in the original source:

  G18   Unicode Tag block "ASCII smuggling" (U+E0020..U+E007E)
  G101  decorated single alphanumerics whose NFKC is multi-char
  G21   small-caps -> ASCII
        combining marks / zero-width / invisible Cf -> dropped
        fullwidth + circled compat folds, confusables, dashes, non-ASCII whitespace
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT / "gateway" / "ai_mesh_gateway"))

from patterns import (  # noqa: E402
    _CANON_MAX_LEN,
    _CONFUSABLE_MAP,
    _DASH_CHARS,
    _SMALLCAP_MAP,
    _canonicalize_with_map,
)


# --------------------------------------------------------------------------- #
# FROZEN reference: a verbatim copy of `_canonicalize_with_map` as it stood before
# task 1F. Do not "tidy" it — its value is that it is untouched.
# --------------------------------------------------------------------------- #
def _reference_canonicalize(text: str):
    """Return ``(canonical_text, index_map)`` using only 1->1 subs and 1->0 removals.

    ``index_map[k]`` is the offset in ``text`` of canonical char ``k`` so a canonical
    match span maps back to the exact original substring to mask.
    """
    if not text:
        return "", []
    src = text[:_CANON_MAX_LEN]
    out_chars: List[str] = []
    idx_map: List[int] = []
    for i, ch in enumerate(src):
        cp = ord(ch)
        # G18: Unicode Tag block "ASCII smuggling". TAG SPACE..TAG TILDE
        # (U+E0020..U+E007E) mirror printable ASCII 0x20..0x7E but are category Cf, so
        # the drop below would silently REMOVE them — hiding tag-encoded PII/secrets
        # from detection while the original tag bytes still egress (LLMs decode them).
        # DECODE the printable mirror back to ASCII here (BEFORE the Cf-drop). It is a
        # 1->1 position-preserving substitution, so index_map[k]=i still masks the
        # match back onto the original tag bytes. Tag controls (U+E0000/E0001/E007F)
        # are also Cf and fall through to the drop below.
        if 0xE0020 <= cp <= 0xE007E:
            out_chars.append(chr(cp - 0xE0000))
            idx_map.append(i)
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Mn", "Me"):                 # invisibles / combining marks -> drop
            continue
        if cat == "Cc" and ch not in "\t\n\r":         # control chars -> drop (keep whitespace)
            continue
        nc = unicodedata.normalize("NFKC", ch)
        if len(nc) == 1:
            ch2 = nc                                   # keep 1->1 compat folds (fullwidth/math/circled)
        else:
            # G101: a DECORATED single alphanumeric has a MULTI-char NFKC that the 1->1 guard
            # skipped — parenthesized letter ⒤ -> "(i)", parenthesized digit ⑵ -> "(2)", full-stop
            # digit ⒈ -> "1." — so it evaded detect_pii/detect_secrets (a parenthesized-digit SSN
            # went UNdetected; the scanner's richer deobfuscation caught the injection side, but the
            # PII/secret path relies on this canon). Fold to the lone alnum char (still 1->1, so the
            # index map still masks back onto the original char). Ligatures / fractions / "No."-type
            # symbols (>1 alnum: ﬁ->"fi", ½->"1⁄2", №->"No") are LEFT untouched. FP-safe: fires only
            # when the canonical form is a real PII/secret/injection pattern.
            _alnums = [c for c in nc if c.isalnum()]
            ch2 = _alnums[0] if len(_alnums) == 1 else ch
        if ch2 in _DASH_CHARS:
            ch2 = "-"
        elif unicodedata.category(ch2) == "Zs":
            ch2 = " "
        elif ch2 in _CONFUSABLE_MAP:
            ch2 = _CONFUSABLE_MAP[ch2]
        elif ch2 in _SMALLCAP_MAP:            # G21: small-caps -> ASCII (1->1)
            ch2 = _SMALLCAP_MAP[ch2]
        out_chars.append(ch2)
        idx_map.append(i)
    return "".join(out_chars), idx_map


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
ADVERSARIAL = [
    "".join(chr(0xE0000 + ord(c)) for c in "sk-live-ABCDEF"),   # G18 tag-block smuggling
    "\U000E0001" + "hidden" + "\U000E007F",                     # G18 tag controls
    "é̀mail@example.com",                            # combining marks (Mn)
    "sk​-‌live‍-key﻿",                      # zero-width / invisible Cf
    "ｆｕｌｌｗｉｄｔｈ　ｔｅｘｔ　１２３",                        # fullwidth (compat fold) + U+3000
    "⑵⒈⒤",                                                      # G101 decorated alphanumerics
    "ﬁ ½ №",                                                    # multi-alnum NFKC: must be LEFT alone
    "ᴀʙᴄᴅᴇꜰɢ",                                                  # G21 small-caps
    "аlex@ехample.com",                                         # Cyrillic confusables
    "4111‑1111–2222—3333",                       # assorted dashes
    "a b c d",                                   # non-ASCII whitespace (Zs)
    "line1\r\nline2\ttabbed",                                   # control chars that must be KEPT
    "".join(chr(c) for c in range(0x20)),                       # Cc controls that must be DROPPED
    "",                                                          # empty
    "x" * (_CANON_MAX_LEN + 500),                               # beyond the truncation cap
    "".join(chr(c) for c in range(32, 127)),                    # every printable ASCII
    "🙂👨‍👩‍👧‍👦 emoji + ZWJ",                                   # astral + ZWJ sequences
]


def _corpus() -> List[str]:
    out: List[str] = []
    d = ROOT / "tests" / "detection_corpus"
    for name in ("benign.jsonl", "malicious.jsonl"):
        for line in (d / name).read_text().splitlines():
            if line.strip():
                out.append(json.loads(line)["text"])
    return out


@pytest.mark.parametrize("text", ADVERSARIAL)
def test_adversarial_unicode_is_canonicalised_identically(text):
    assert _canonicalize_with_map(text) == _reference_canonicalize(text), (
        f"canonicalisation drifted from the frozen pre-1F reference for "
        f"{text[:60]!r} (categories: "
        f"{sorted({unicodedata.category(c) for c in text[:60]})})"
    )


def test_corpus_is_canonicalised_identically():
    bad = [t for t in _corpus()
           if _canonicalize_with_map(t) != _reference_canonicalize(t)]
    assert not bad, f"{len(bad)} corpus items canonicalise differently, e.g. {bad[:3]!r}"


def test_every_code_point_below_0x2000_is_canonicalised_identically():
    """Exhaustive over the range that carries the ASCII fast path and its neighbours —
    Latin, combining marks, punctuation, dashes and the Zs whitespace block."""
    bad = []
    for cp in range(0x2000):
        ch = chr(cp)
        if _canonicalize_with_map(ch) != _reference_canonicalize(ch):
            bad.append(f"U+{cp:04X} ({unicodedata.category(ch)})")
    assert not bad, f"{len(bad)} code points differ: {bad[:20]}"


def test_index_map_still_points_at_the_original_bytes():
    """The map's contract: index_map[k] is the offset in the ORIGINAL text of canonical
    char k, so a canonical match span masks back onto the right raw bytes."""
    for text in ADVERSARIAL + _corpus()[:50]:
        canon, idx = _canonicalize_with_map(text)
        assert len(canon) == len(idx), f"map length {len(idx)} != canon length {len(canon)}"
        src = text[:_CANON_MAX_LEN]
        assert all(0 <= i < len(src) for i in idx), "index map points outside the source"
        assert idx == sorted(idx), "index map is not monotonic"
