import sys, re, regex, warnings
warnings.simplefilter("ignore")
sys.path.insert(0, "..")
import translate_prototype as T

def at_depth(n, f):
    if n <= 0:
        return f()
    return at_depth(n - 1, f)

def headroom(fn):
    """largest number of extra frames under which fn() still succeeds"""
    lo, hi = 0, 1000
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            at_depth(mid, fn); lo = mid
        except RecursionError:
            hi = mid - 1
    return lo

def tdepth(s):
    d = m = 0; i = 0; inset = False
    while i < len(s):
        c = s[i]
        if c == "\\": i += 2; continue
        if inset:
            if c == "]": inset = False
        elif c == "[": inset = True; 
        elif c == "(": d += 1; m = max(m, d)
        elif c == ")": d -= 1
        i += 1
    return m

D = int(sys.argv[1]) if len(sys.argv) > 1 else 32
leaf = r"(?i:[^\dı])"
cases = {
  "capture+possessive": "(" * D + "a" + ")++" * D,
  "capture+possessive, ci leaf": "(" * (D-1) + leaf + ")++" * (D-1),
  "(?: possessive": "(?:" * D + "a" + ")++" * D,
  "capture greedy": "(" * D + "a" + ")+" * D,
  "lookahead": "(?=" * D + "a" + ")" * D,
  "lookbehind": "(?<=" * D + "a" + ")" * D,
  "atomic": "(?>" * D + "a" + ")" * D,
  "noncapture plain": "(?:" * D + "a" + ")" * D,
  "\\b around capture+possessive": r"\b" + "(" * D + "a" + ")++" * D + r"\b",
  "\\b inside each": "".join([r"(\b"] * D) + "a" + ")++" * D,
  "cond": "(a)" + "(?(1)" * (D-1) + "a" + ")" * (D-1),
  "mixed": "".join(["(?=", "(", "(?>", "(?<!", "(?:"][i % 5] for i in range(D)) + "a" + "".join([")", ")++", ")", ")", ")+"][i % 5] for i in reversed(range(D))),
}
for name, p in cases.items():
    try:
        re.compile(p)
    except Exception as e:
        print(f"{name:32} re refuses: {type(e).__name__} {e}"); continue
    t = T.translate(p, cpu_budget_s=None)
    print(f"{name:32} src len {len(p):4} transl depth {tdepth(t):4} | headroom: parse {headroom(lambda: re._parser.parse(p,0)):4} "
          f"analyze {headroom(lambda: T.analyze(p)):4} translate {headroom(lambda: T.translate(p, cpu_budget_s=None)):4} "
          f"stopper {headroom(lambda: T.stopper(p)):4} regex.compile {headroom(lambda: regex.compile(t)):4} re.compile {headroom(lambda: re.compile(p)):4}")
