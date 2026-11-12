"""Reviewer A, revision 5, NIT 1, and revision 6: every spelling of a one-code-point negation, in
alternations (pairs and triples), through non-capturing and capturing groups, with astral members
and one-code-point ranges, plain, repeated, in a negative lookahead, in a lookbehind and after a
literal, under no flag, (?i) and (?s), against `re`.

`regex` merges alternatives that are negated sets of ONE code point into one negated set (an
intersection of the complements): (?:[^9]|[^c]) matches neither 9 nor c. Revision 5 doubled a lone
negated escape, but `[^a-a]` was emitted as is, and (?:[^a-a]|[^b-b]) under-matched (12,548 matches
against re's 12,550 here). Also checks that no translated pattern, here or in 3,000 fuzz patterns,
contains a negated set covering one code point."""
import itertools, random, re, sys, time
sys.path.insert(0, ".")
import regex
import fuzz_gen as G
import translate_prototype as T

VOC = ["[^9]", "[^9-9]", "(?:[^9])", "(?:(?:[^9]))", "([^9])", "[^c]", "[^c-c]", "(?:[^c-c])", "[^\U0001d7ce]",
       "[^\U0001d7ce-\U0001d7ce]", "[^99]", "[^9-99]", "[^k]", "[^K]", "[^ı]", "x"]
SHAPES = ["(?:{})", "(?:{})+", "(?!(?:{}))(?s:.)", "x(?:{})", "(?<=(?:{}))."]
TEXT = "".join(chr(c) for c in range(0x3100)) + "İıſKkK\U0001d7ce\U00010400"
# One negated set covering one code point, in the translator's escape vocabulary.
LONE = re.compile(r"\[\^(\\(?:x[0-9a-f]{2}|u[0-9a-f]{4}|U[0-9a-f]{8}))(?:-\1)?\]")

t0 = time.perf_counter(); bad = checked = lone = raw_bug = 0
for k in (2, 3):
    for combo in itertools.product(VOC, repeat=k):
        if k == 3 and len(set(combo)) < 2:
            continue
        alt = "|".join(combo)
        for flags in ("", "(?i)", "(?s)"):
            for shape in SHAPES:
                p = flags + shape.format(alt)
                try:
                    rp = re.compile(p)
                except re.error:
                    continue
                t = T.translate(p)
                if LONE.search(t):
                    lone += 1
                a = [m.span() for m in rp.finditer(TEXT)]
                b = [m.span() for m in regex.finditer(t, TEXT)]
                checked += 1
                if a != b:
                    bad += 1
                    if bad <= 10: print("MISMATCH", repr(p), "->", t)
# The raw bug is still there in `regex` (so the workaround is still needed).
for raw in (r"(?:[^\x61-\x61]|[^\x62-\x62])", r"(?:(?:[^\x39])|(?:[^\x63]))", r"(?:[^\U0001d7ce]|[^\x61])"):
    raw_bug += len(regex.findall(raw, TEXT)) != len(TEXT)
R = random.Random(9); G.R = R
for _ in range(3000):
    p = R.choice(["", "(?i)", "(?s)"]) + G.seq()
    try:
        re.compile(p); t = T.translate(p)
    except Exception:
        continue
    if LONE.search(t):
        lone += 1
        if lone <= 5: print("LONE", repr(p), t)
print(f"one-code-point negations: {checked} patterns, {bad} mismatches, {lone} lone negated sets emitted; "
      f"raw `regex` still merges: {raw_bug}/3 ({time.perf_counter()-t0:.0f}s)")
sys.exit(1 if bad or lone else 0)
