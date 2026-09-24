"""One canonicalization pass with a span map back to the original text.

Order: one bounded percent-decode -> NFKC (per base+combining group) -> strip
zero-width / bidi controls. Every canonical char i maps to [start[i], end[i]) in
the original string, so a finding on canonical text redacts original bytes.
Fast path: ASCII text without '%' is already canonical (identity map).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_ESCAPES = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_CONTROLS = frozenset(
    [chr(c) for c in range(0x200B, 0x2010)]
    + [chr(c) for c in range(0x202A, 0x202F)]
    + [chr(c) for c in range(0x2060, 0x2065)]
    + [chr(c) for c in range(0x2066, 0x206A)]
    + ["﻿", "­", "᠎"]
)
_CONTROL_RE = re.compile("[" + "".join(sorted(_CONTROLS)) + "]")


class DecodeBudgetExceeded(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Canonical:
    text: str
    orig: str
    starts: tuple[int, ...] | None  # None => identity
    ends: tuple[int, ...] | None

    def to_orig(self, start: int, end: int) -> tuple[int, int]:
        if self.starts is None or self.ends is None:
            return start, end
        if end <= start:
            pos = self.starts[start] if start < len(self.starts) else len(self.orig)
            return pos, pos
        return self.starts[start], self.ends[end - 1]


def canonicalize(s: str, decode_budget: int) -> Canonical:
    if s.isascii() and "%" not in s:
        return Canonical(s, s, None, None)
    chars, starts, ends = _percent_decode(s, decode_budget)
    stage1 = "".join(chars)
    if stage1 == s and (
        s.isascii()
        or (unicodedata.is_normalized("NFKC", s) and not _CONTROL_RE.search(s))
    ):
        return Canonical(s, s, None, None)
    out: list[str] = []
    o_start: list[int] = []
    o_end: list[int] = []
    i = 0
    n = len(chars)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(chars[j]):
            j += 1
        group = "".join(chars[i:j])
        norm = unicodedata.normalize("NFKC", group)
        for ch in norm:
            if ch in _CONTROLS:
                continue
            out.append(ch)
            o_start.append(starts[i])
            o_end.append(ends[j - 1])
        i = j
    return Canonical("".join(out), s, tuple(o_start), tuple(o_end))


def _percent_decode(s: str, budget: int) -> tuple[list[str], list[int], list[int]]:
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    pos = 0
    used = 0
    for m in _ESCAPES.finditer(s):
        for k in range(pos, m.start()):
            chars.append(s[k])
            starts.append(k)
            ends.append(k + 1)
        run = m.group(0)
        used += len(run) // 3
        if used > budget:
            raise DecodeBudgetExceeded(f"percent-decode budget {budget} exceeded")
        raw = bytes.fromhex(run.replace("%", ""))
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            decoded = None
        if decoded is None:
            for k in range(m.start(), m.end()):
                chars.append(s[k])
                starts.append(k)
                ends.append(k + 1)
        else:
            off = m.start()
            for ch in decoded:
                width = 3 * len(ch.encode("utf-8"))
                chars.append(ch)
                starts.append(off)
                ends.append(off + width)
                off += width
        pos = m.end()
    for k in range(pos, len(s)):
        chars.append(s[k])
        starts.append(k)
        ends.append(k + 1)
    return chars, starts, ends
