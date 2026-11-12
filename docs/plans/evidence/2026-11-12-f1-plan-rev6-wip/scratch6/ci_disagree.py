"""D' = cased code points where some single ci literal's verdict differs between re and regex (native fold)."""
import sys, re, regex, time
sys.path.insert(0, ".")
import translate_prototype as T
t0 = time.time()
D = set(); per = []
for cp in T._CASED:
    if 0xD800 <= cp <= 0xDFFF: continue
    items = [(T.C.LITERAL, cp)]
    src = T._set_source(items)
    want = T._ci_verdicts(src)
    native = T._build(*T._parts(items), icase=True)
    have = frozenset(regex.findall(native, T._CASED_STR))
    diff = want ^ have
    if diff:
        D |= {ord(c) for c in diff}; per.append((cp, len(diff)))
print("literals with a difference:", len(per), "max per literal:", max(n for _, n in per))
print("|D'| =", len(D), "body len", len(T._body(D)), "sum esc+1", sum(len(T._esc(c)) + 1 for c in D))
print(sorted(hex(c) for c in D))
print(f"{time.time()-t0:.0f}s")
