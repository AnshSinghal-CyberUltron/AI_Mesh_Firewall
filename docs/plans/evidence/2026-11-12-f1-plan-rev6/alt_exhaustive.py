"""Alternations of single-character items: every pair and triple from the translator's vocabulary,
plain, repeated, and inside a negative lookahead, with and without (?i), against `re`.

Found while building the revision-5 stopper: `regex` merges alternatives that are lone negated
characters into one negated set, so `(?:[^9]|[^c])` matched only characters outside BOTH."""
import itertools, re, sys, time, regex
sys.path.insert(0, ".")
from translate_prototype import translate
VOC = ["a", "b", "K", "ı", "[^b]", "[^d]", "[^K]", "[ab]", "[^ab]", r"\d", r"\D", r"\w", r"\W", r"\s", r"\S", ".", "[^\\d]", "[a\\d]", "[^a\\s]"]
TEXT = "".join(chr(c) for c in range(0x3100)) + "İıſK\U0001d7ce\U00010400"
t0 = time.perf_counter(); bad = checked = 0
for k in (2, 3):
    for combo in itertools.product(VOC, repeat=k):
        alt = "|".join(combo)
        for flags in ("", "(?i)", "(?s)"):
            for shape in ("(?:{})", "(?:{})+", "(?!(?:{}))(?s:.)", "x(?:{})"):
                p = flags + shape.format(alt)
                a = [m.span() for m in re.finditer(p, TEXT)]
                b = [m.span() for m in regex.finditer(translate(p), TEXT)]
                checked += 1
                if a != b:
                    bad += 1
                    if bad <= 10: print("MISMATCH", repr(p))
print(f"single-char alternations: {checked} patterns, {bad} mismatches in {time.perf_counter()-t0:.0f}s")
