import random, re
R = random.Random(1)
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
    if r < 0.76:  # an alternation of single characters (found the regex lone-negated-char merge bug)
        one = lambda: R.choice([re.escape(R.choice(LIT)), "[^" + re.escape(R.choice(LIT)) + "]", R.choice(ESC), "."])
        return "(?:" + "|".join(one() for _ in range(R.randint(2, 3))) + ")"
    if r < 0.85 and d < 3:
        kind = R.choice(["(", "(?:", "(?=", "(?!", "(?<=a", "(?>"])
        return kind + seq(d + 1) + ")"
    if r < 0.90: return R.choice([r"\b", r"\B", "^", "$", r"\A", r"\Z"])
    if r < 0.95: return R.choice(["{", "}", "a{e<=1}", "{,}", "x{2,}", "[[:alpha:]]"])
    return re.escape(R.choice(LIT))

def quant(a):
    r = R.random()
    if r < 0.6 or a in ("^", "$", r"\A", r"\Z", r"\b", r"\B"): return a
    q = R.choice(["*", "+", "?", "{2}", "{1,3}", "{,2}", "{2,}"])
    return a + q + R.choice(["", "", "", "?", "+"])

def seq(d=0):
    parts = [quant(atom(d)) for _ in range(R.randint(1, 4))]
    s = "".join(parts)
    if R.random() < 0.15 and d < 2: s += "|" + seq(d + 1)
    return s

def text():
    return "".join(R.choice(ALPHA) for _ in range(R.randint(0, 24)))

