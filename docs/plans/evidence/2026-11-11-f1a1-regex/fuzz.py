import random, re, sys, time, warnings, regex
sys.path.insert(0, ".")
from translate_prototype import translate, Refused
warnings.simplefilter("ignore")
R = random.Random(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
ALPHA = list("abcxyzABCXYZ019_ -.,:\n") + ["\x1c", "\x1f", "\xa0", " ", "١", "１", "́", "ͅ", "\xdf", "\xb2", "σ", "Σ", "ı", "İ", "K", "k", "K", "ẞ", "é", "٠"]
ESC = [r"\d", r"\D", r"\w", r"\W", r"\s", r"\S"]
LIT = list("abcxyzAXZ019_ ") + ["é", "σ", "\xdf", "k", "ı"]

def atom(d):
    r = R.random()
    if r < 0.30: return re.escape(R.choice(LIT))
    if r < 0.45: return R.choice(ESC)
    if r < 0.52: return "."
    if r < 0.70:
        items = [R.choice(ESC + [re.escape(c) for c in LIT] + ["a-z", "0-9", "-"]) for _ in range(R.randint(1, 3))]
        return "[" + ("^" if R.random() < 0.3 else "") + "".join(items) + "]"
    if r < 0.85 and d < 3:
        kind = R.choice(["(", "(?:", "(?=", "(?!"])
        return kind + seq(d + 1) + ")"
    if r < 0.90: return R.choice([r"\b", r"\B", "^", "$", r"\A", r"\Z"])
    if r < 0.95: return R.choice(["{", "}", "a{e<=1}", "{,}", "x{2,}", "[[:alpha:]]"])
    return re.escape(R.choice(LIT))

def quant(a):
    r = R.random()
    if r < 0.6 or a in ("^", "$", r"\A", r"\Z", r"\b", r"\B"): return a
    q = R.choice(["*", "+", "?", "{2}", "{1,3}", "{,2}", "{2,}"])
    return a + q + ("?" if R.random() < 0.2 else "")

def seq(d=0):
    parts = [quant(atom(d)) for _ in range(R.randint(1, 4))]
    s = "".join(parts)
    if R.random() < 0.15 and d < 2: s += "|" + seq(d + 1)
    return s

def text():
    return "".join(R.choice(ALPHA) for _ in range(R.randint(0, 24)))

n = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
checked = mism = skipped = refused = 0
t0 = time.perf_counter()
for _ in range(n):
    p = R.choice(["", "", "(?i)", "(?m)", "(?s)", "(?im)"]) + seq()
    try:
        a = re.compile(p)
    except Exception:
        skipped += 1; continue
    try:
        b = regex.compile(translate(p))
    except Refused:
        refused += 1; continue
    except Exception as e:
        mism += 1; print("COMPILE-FAIL", repr(p), type(e).__name__, e); continue
    for _ in range(8):
        s = text()
        try:
            sa = [m.span() for m in a.finditer(s)]
            sb = [m.span() for m in b.finditer(s, timeout=1.0)]
        except Exception as e:
            print("ERR", repr(p), repr(s), e); continue
        checked += 1
        if sa != sb:
            mism += 1
            if mism <= 15: print("MISMATCH", repr(p), repr(s), sa, sb)
print(f"patterns={n} checked={checked} mismatches={mism} re-invalid-skipped={skipped} refused={refused} in {time.perf_counter()-t0:.1f}s")
