"""Exhaustive check of the one-sided word boundaries (revision 5).

A one-sided form is emitted when the neighbouring item fixes one side of the boundary. Its
correctness depends on the OTHER side, which can be any code point. For each pattern and each
neighbour character k, the text interleaves every code point c with k ("c0 k c1 k c2 k ..."),
so every pair (c, k) and (k, c) is adjacent somewhere, and the match lists of `re` and the
translation are compared over that whole text. The text alone (every code point once, in order)
and the start/end edges are checked too.
"""
import re
import sys
import time

import regex

sys.path.insert(0, ".")
from translate_prototype import _BOUNDARY, _NON_BOUNDARY, translate  # noqa: E402

ALL = [chr(c) for c in range(sys.maxunicode + 1) if not 0xD800 <= c <= 0xDFFF]
PLAIN = "".join(ALL)

# (pattern, neighbour chars k that the item next to the boundary can match, expect one-sided?)
CASES = [
    (r"\b[A-Z]", "AZ", True), (r"[A-Z]\b", "AZ", True), (r"\B[A-Z]", "A", True), (r"[A-Z]\B", "A", True),
    (r"\b\d", "5١", True), (r"\d\b", "5١", True), (r"\B\d", "5", True), (r"\d\B", "5", True),
    (r"\b\s", " \x1c\xa0", True), (r"\s\b", " \x1f", True), (r"\B\s", " ", True), (r"\s\B", " ", True),
    (r"\b\W", "-", True), (r"\W\b", "-", True), (r"\b\w", "a_", True), (r"\w\b", "a_", True),
    (r"\b[A-Z]{2}\d{2}[A-Z0-9]{2,4}\b", "", True),
    (r"(?i)\bk", "kKK", True), (r"(?i)k\b", "kK", True), (r"(?i)\bss", "sSſ", True),
    (r"\b(?:ab|cd)", "ac", True), (r"(?:ab|cd)\b", "bd", True), (r"\ba+", "a", True), (r"a+\b", "a", True),
    (r"\b(?:a|-)", "a-", False), (r"(?i)\bι", "ιͅ", False), (r"\b.", "a", False), (r"\b[^a]", "b", False),
    (r"\b(?:a?)b", "b", False), (r"\b(?:a*)", "a", False),
]

t0 = time.perf_counter()
bad = 0
for flags in ("", "(?m)"):
    for pat, ks, one_sided in CASES:
        p = flags + pat
        tr = translate(p)
        two = _BOUNDARY in tr or _NON_BOUNDARY in tr
        if one_sided == two:
            print("FORM", repr(p), "expected one-sided" if one_sided else "expected two-sided", tr[:120])
            bad += 1
        a, b = re.compile(p), regex.compile(tr)
        texts = [PLAIN] + ["".join(c + k for c in ALL) for k in ks]
        if pat.startswith(r"\b[A-Z]{2}"):
            texts.append("".join(c + "AB12CD" + c for c in ALL))
        for s in texts:
            sa = [m.span() for m in a.finditer(s)]
            sb = [m.span() for m in b.finditer(s)]
            if sa != sb:
                bad += 1
                diff = sorted(set(sa) ^ set(sb))[:3]
                print("MISMATCH", repr(p), [(x, repr(s[max(0, x[0] - 2):x[1] + 2])) for x in diff])
        for c in ALL[:: 997] + ["", "a", " ", "ͅ"]:  # edges: start and end of the text
            for k in ks or "A":
                for s in (c, k, c + k, k + c):
                    if [m.span() for m in a.finditer(s)] != [m.span() for m in b.finditer(s)]:
                        bad += 1
                        print("EDGE", repr(p), repr(s))
print(f"one-sided boundaries: {len(CASES) * 2} patterns, {bad} mismatches in {time.perf_counter() - t0:.1f}s")
