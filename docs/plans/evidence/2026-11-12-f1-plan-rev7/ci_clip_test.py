"""Revision 7 (Plan Reviewer A, revision 6, blocker 1): the clipped case-insensitive verdict equals
`re`'s verdict on the unclipped item, on every cased code point.

    python ci_clip_test.py <seed> <random_sets>

Part 1, the properties the clipping relies on, over every code point (also in `gen_cased.py --check`):
  P1  a code point outside the cased table is its own lowercase (`_sre.unicode_tolower`);
  P2  the lowercase of a cased code point is cased;
  P3  `_sre.unicode_iscased` implies cased;
  P4  lowercase is idempotent;
  P6  every key and value of `re._casefix._EXTRA_CASES` is cased.
  It also lists the code points where a BMP-to-astral range could NOT be split at U+FFFF (the tail's
  RANGE_UNI_IGNORE upper-case test), which is why such ranges are kept whole.
Part 2, differential: `re.findall("(?i)" + unclipped, CASED)` == `re.findall("(?i)" + clipped, CASED)`:
  (a) every BMP-only range [a-b] over the boundary points of the cased table (each run's first and
      last code point, +-1, U+0000, U+FFFF), positive and negated;
  (b) random sets (literals, BMP ranges of every width, astral and BMP-to-astral ranges, categories,
      negation), 1 to 6 members;
  (c) Reviewer A's items (`ciwidth.py`, `ciplan.py`).
Prints the counts and the mismatches (expected: 0).
"""
import random
import re
import sys
import time
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import _sre  # noqa: E402
from re import _constants as C, _parser as P  # noqa: E402
from re._casefix import _EXTRA_CASES  # noqa: E402

import translate_prototype as T  # noqa: E402
from _cased_table import CASED_RANGES  # noqa: E402

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
n_random = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
parts = sys.argv[3] if len(sys.argv) > 3 else "2a,2b,2c"  # e.g. "2b": part 1 and the random sets only
rng = random.Random(seed)
t_start = time.process_time()

cased = set()
for a, b in CASED_RANGES:
    cased.update(range(a, b + 1))
lower, iscased = _sre.unicode_tolower, _sre.unicode_iscased
bad = dict.fromkeys(("P1", "P2", "P3", "P4", "P6"), 0)
for cp in range(sys.maxunicode + 1):
    lo = lower(cp)
    bad["P1"] += cp not in cased and lo != cp
    bad["P2"] += cp in cased and lo not in cased
    bad["P3"] += bool(iscased(cp)) and cp not in cased
    bad["P4"] += lower(lo) != lo
for k, vs in _EXTRA_CASES.items():
    bad["P6"] += k not in cased or any(v not in cased for v in vs)
print("part 1, properties over all", sys.maxunicode + 1, "code points, violations:", bad)
try:
    import ctypes
    up = ctypes.pythonapi._PyUnicode_ToUppercase
    up.restype, up.argtypes = ctypes.c_uint32, [ctypes.c_uint32]
    split_unsafe = []
    for cp in range(sys.maxunicode + 1):
        lo = lower(cp)
        u = up(lo)
        if lower(u) != lo and lo not in _EXTRA_CASES.get(lower(u), ()) and lo < u <= 0xFFFF:
            split_unsafe.append(f"U+{cp:04X}")
    print("  code points a split at U+FFFF would lose (kept-whole rule):", split_unsafe)
except Exception as e:  # noqa: BLE001
    print("  (split check skipped:", type(e).__name__, ")")

CASED_STR = T._CASED_STR


def verdict(src: str) -> frozenset:
    return frozenset(re.findall("(?i)" + src, CASED_STR))


def items_of(src: str):
    parsed = P.parse("(?i)" + src, 0)
    (op, av), = list(parsed)
    if op is C.IN:
        return av
    if op is C.LITERAL:
        return [(C.LITERAL, av)]
    if op is C.NOT_LITERAL:
        return [(C.NEGATE, None), (C.LITERAL, av)]
    raise ValueError(op)


checked = mism = 0
examples = []


def check(src: str) -> None:
    global checked, mism
    items = items_of(src)
    want = verdict(T._set_source(items))       # unclipped, as revision 6 asked `re`
    got = verdict(T.verdict_source(items))      # clipped (revision 7)
    checked += 1
    if want != got:
        mism += 1
        if len(examples) < 5:
            examples.append((src, sorted(want ^ got)[:5]))


edges = {0, 1, 0xFFFE, 0xFFFF}
for a, b in CASED_RANGES:
    for x in (a - 1, a, b, b + 1):
        if 0 <= x <= 0xFFFF and not 0xD800 <= x <= 0xDFFF:
            edges.add(x)
edges = sorted(edges)
pairs = [(x, y) for i, x in enumerate(edges) for y in edges[i:]]
rng.shuffle(pairs)
pairs = pairs[: max(1, n_random // 2)]
t0 = time.process_time()
for x, y in (pairs if "2a" in parts else []):
    body = f"{T._esc(x)}-{T._esc(y)}"
    check(f"[{body}]")
    check(f"[^{body}]")
print(f"part 2a: {checked} BMP ranges over {len(edges)} edge points (positive and negated): "
      f"{mism} mismatches, {time.process_time() - t0:.1f} s")


def rand_cp(kind: str) -> int:
    if kind == "bmp":
        while True:
            c = rng.randrange(0, 0x10000)
            if not 0xD800 <= c <= 0xDFFF:
                return c
    if kind == "edge":
        return rng.choice(edges)
    return rng.randrange(0x10000, 0x110000)


def member() -> str:
    r = rng.random()
    if r < 0.25:
        return T._esc(rand_cp(rng.choice(("bmp", "edge", "astral"))))
    if r < 0.35:
        return rng.choice((r"\w", r"\W", r"\d", r"\D", r"\s", r"\S"))
    kind = rng.random()
    if kind < 0.55:  # BMP-only range of any width
        a, b = sorted((rand_cp(rng.choice(("bmp", "edge"))), rand_cp(rng.choice(("bmp", "edge")))))
    elif kind < 0.8:  # astral-only
        a, b = sorted((rand_cp("astral"), rand_cp("astral")))
    else:  # BMP to astral (kept whole)
        a, b = rand_cp(rng.choice(("bmp", "edge"))), rand_cp("astral")
    return f"{T._esc(a)}-{T._esc(b)}"


before = checked, mism
t0 = time.process_time()
for _ in range(n_random if "2b" in parts else 0):
    body = "".join(member() for _ in range(rng.randint(1, 6)))
    check(("[^" if rng.random() < 0.3 else "[") + body + "]")
print(f"part 2b: {checked - before[0]} random sets: {mism - before[1]} mismatches, "
      f"{time.process_time() - t0:.1f} s")

before = checked, mism
for i in (range(64) if "2c" in parts else []):
    check(f"[{chr(0x41 + i % 26)}-￯{chr(0x10400 + i)}-\U0001e943]")
    for k in range(8):
        check(f"[{chr(0x41 + (i + k) % 26)}{chr(0x100 + i + 64 * k)}-￯\U00010400-\U0001e943]")
for hi in ((0x5A, 0x100, 0x1000, 0x4000, 0xFFEF) if "2c" in parts else ()):
    check(f"[A-{chr(hi)}]")
    check(f"[A-{chr(hi)}\U00010400-\U0001e943]")
print(f"part 2c: {checked - before[0]} items from Reviewer A's probes: {mism - before[1]} mismatches")
print(f"TOTAL: {checked} items, {mism} mismatches; examples {examples}; "
      f"{time.process_time() - t_start:.1f} s CPU; seed {seed}")
