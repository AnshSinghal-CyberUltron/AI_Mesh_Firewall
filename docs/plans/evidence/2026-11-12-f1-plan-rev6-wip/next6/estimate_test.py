"""Revision 6: the parse-only estimate bounds the real translation (Reviewer B, rev. 5, should-fix 4).

For fuzz-grammar patterns (plus case-insensitive shapes built around the code points where `re`
and `regex` fold differently), checks len(translate(p)) <= Analysis.translated_size and
compile_units(translate(p)) <= Analysis.compile_units. Also checks the bound's premise directly:
every case-insensitive literal (and its negation) that does not touch FOLD_DISAGREE gets no patch.

    python estimate_test.py <seed> <patterns>
"""
import random, re, statistics, sys, time, warnings
sys.path.insert(0, ".")
warnings.simplefilter("ignore")
import fuzz_gen as G
import translate_prototype as T

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
n = int(sys.argv[2]) if len(sys.argv) > 2 else 10000
R = random.Random(seed); G.R = R
F = sorted(T._FOLD_DISAGREE)
CI_LIT = [chr(c) for c in F] + list("iIkKsS") + ["\u212a", "\u017f", "\xb5", "\u03bc", "\u0390", "\u1fd3", "\ufb05", "\U0001e91c", "\U0001e93e", "\U00010400", "\U000104b2", "\U000118ae"]

def ci_shape():
    def item():
        r = R.random()
        if r < 0.4: return re.escape(R.choice(CI_LIT))
        if r < 0.6: return "[" + ("^" if R.random() < 0.4 else "") + "".join(re.escape(R.choice(CI_LIT)) for _ in range(R.randint(1, 3))) + "]"
        if r < 0.7: return "[" + ("^" if R.random() < 0.4 else "") + R.choice(["a-z", "A-Z", "Ā-Ȁ", "h-j", "H-J", "Ͱ-Ͽ", "ﬀ-ﬆ", "i-i", "\U00010400-\U00010430", "\U0001e900-\U0001e950"]) + R.choice(["", r"\d", r"\s", r"\W"]) + "]"
        if r < 0.8: return R.choice([r"\b", r"\B"])
        return G.atom(2)
    body = "".join(G.quant(item()) for _ in range(R.randint(1, 8)))
    return R.choice(["(?i)", "(?i)", "", "(?i:"]) + body + (")" if body and R.random() < 0 else "")

checked = refused = 0; bad = []; re_only_refused = []; ratios = []; uratios = []
t0 = time.perf_counter()
for i in range(n):
    p = ci_shape() if i % 2 else R.choice(["", "", "(?i)", "(?m)", "(?s)", "(?im)"]) + G.seq()
    if p.startswith("(?i:"): p += ")"
    try:
        re.compile(p)
    except Exception:
        try:  # the parse-only check refuses every pattern `re.compile` refuses
            T.check_limits(p); re_only_refused.append(p)
        except T.Refused:
            pass
        refused += 1; continue
    try:
        a = T.analyze(p)
        t = T.translate(p, cpu_budget_s=None)
    except (T.Refused, re.error):
        refused += 1; continue
    checked += 1
    u = T.compile_units(t)
    if len(t) > a.translated_size or u > a.compile_units:
        bad.append((p, len(t), a.translated_size, u, a.compile_units))
    ratios.append(a.translated_size / len(t)); uratios.append(a.compile_units / max(u, 1))

# The premise of the case-insensitive bound, for every cased code point as a literal and negated.
premise_bad = 0
for cp in T._CASED:
    for items in ([(T.C.LITERAL, cp)], [(T.C.NEGATE, None), (T.C.LITERAL, cp)]):
        neg, plain, cats = T._parts(items)
        real = T._ci_set(T._set_source(items), neg, plain, cats)
        native = T._build(neg, plain, cats, icase=True)
        if len(real) > len(native) + T._ci_patch_bound(items):
            premise_bad += 1
# ... and for random case-insensitive sets: BMP and astral literals and ranges, the disagreement
# points and categories, plain and negated. Every patched point must be one `_ci_patch_points` names.
import regex
BMP = [c for c in T._CASED if c <= 0xFFFF]; AST = [c for c in T._CASED if c > 0xFFFF]
sets_checked = sets_patched = 0
for _ in range(max(2000, n)):
    items = [(T.C.NEGATE, None)] if R.random() < 0.3 else []
    for _k in range(R.randint(1, 4)):
        r = R.random()
        if r < 0.3:
            items.append((T.C.LITERAL, R.choice(BMP)))
        elif r < 0.5:
            items.append((T.C.LITERAL, R.choice(AST)))
        elif r < 0.55:
            items.append((T.C.LITERAL, R.choice(F)))
        elif r < 0.85:
            a = R.choice(BMP if r < 0.7 else AST); items.append((T.C.RANGE, (a, a + R.randint(0, 60))))
        else:
            items.append((T.C.CATEGORY, R.choice([T.C.CATEGORY_DIGIT, T.C.CATEGORY_NOT_DIGIT, T.C.CATEGORY_WORD,
                                                   T.C.CATEGORY_NOT_WORD, T.C.CATEGORY_SPACE, T.C.CATEGORY_NOT_SPACE])))
    if not any(op is not T.C.NEGATE for op, _ in items):
        continue
    neg, plain, cats = T._parts(items)
    native = T._build(neg, plain, cats, icase=True)
    want = T._ci_verdicts(T._set_source(items))
    have = frozenset(regex.findall(T._PREFILTER_GUARD + native, T._CASED_STR))
    patch = {ord(c) for c in want ^ have}
    sets_checked += 1; sets_patched += bool(patch)
    real = T._ci_set(T._set_source(items), neg, plain, cats)
    if patch - T._ci_patch_points(items) or len(real) > len(native) + T._ci_patch_bound(items):
        premise_bad += 1
print(f"seed={seed} patterns={n} checked={checked} refused={refused} estimate-below-actual={len(bad)} "
      f"accepted-but-re.compile-refuses={len(re_only_refused)} sets={sets_checked} (patched {sets_patched}) premise-violations={premise_bad} size ratio est/actual: median {statistics.median(ratios):.2f} "
      f"p99 {sorted(ratios)[int(len(ratios)*0.99)]:.2f} max {max(ratios):.2f}; units ratio max {max(uratios):.2f} "
      f"in {time.perf_counter()-t0:.0f}s")
for b in bad[:10]:
    print("BELOW", b)
for p in re_only_refused[:10]:
    print("ACCEPTED-BUT-RE-REFUSES", repr(p))
sys.exit(1 if bad or premise_bad or re_only_refused else 0)
