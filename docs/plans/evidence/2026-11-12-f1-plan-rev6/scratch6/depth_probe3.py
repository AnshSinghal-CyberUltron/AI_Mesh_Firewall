import sys, re, regex, warnings, time
warnings.simplefilter("ignore")
sys.path.insert(0, "..")
import translate_prototype as T
sys.setrecursionlimit(1000)
def at_depth(n, f):
    if n <= 0:
        return f()
    return at_depth(n - 1, f)
def headroom(fn):
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
        elif c == "[": inset = True
        elif c == "(": d += 1; m = max(m, d)
        elif c == ")": d -= 1
        i += 1
    return m
D = int(sys.argv[1]) if __name__ == "__main__" else 0
leaf = r"(?i:[^\dı])"
cases = {
  "capture*+ (possessive, min 0)": "(" * D + "a" + ")*+" * D,
  "capture*+ ci leaf": "(" * (D-1) + leaf + ")*+" * (D-1),
  "(?:*+": "(?:" * D + "a" + ")*+" * D,
  "capture*": "(" * D + "a" + ")*" * D,
  "capture?": "(" * D + "a" + ")?" * D,
  "lookahead": "(?=" * D + "a" + ")" * D,
  "lookbehind": "(?<=" * D + "a" + ")" * D,
  "atomic": "(?>" * D + "a" + ")" * D,
  "noncapture plain": "(?:" * D + "a" + ")" * D,
  "\\b around capture*+": r"\b" + "(" * D + "a" + ")*+" * D + r"\b",
  "\\b inside each *+": "".join([r"(\b"] * D) + "a" + ")*+" * D,
  "cond": "(a)" + "(?(1)" * (D-1) + "a" + ")" * (D-1),
  "lookahead*": "(?:(?=" * (D//2) + "a" + ")a)*" * (D//2),
  "mixed": "".join(["(?=", "(", "(?>", "(?<!", "(?:"][i % 5] for i in range(D)) + "a" + "".join([")", ")*+", ")", ")", ")*"][i % 5] for i in reversed(range(D))),
}
for name, p in (cases.items() if __name__ == "__main__" else ()):
    try:
        re.compile(p)
    except Exception as e:
        print(f"{name:30} re refuses: {type(e).__name__} {e}"); continue
    t = T.translate(p, cpu_budget_s=None)
    t0 = time.thread_time(); regex.compile(t); tc = (time.thread_time() - t0) * 1e3
    print(f"{name:30} src {len(p):4} tdepth {tdepth(t):4} compile {tc:6.1f} ms | headroom: parse {headroom(lambda: re._parser.parse(p,0)):4} "
          f"analyze {headroom(lambda: T.analyze(p)):4} translate {headroom(lambda: T.translate(p, cpu_budget_s=None)):4} "
          f"stopper {headroom(lambda: T.stopper(p)):4} regex.compile {headroom(lambda: (regex.purge(), regex.compile(t))):4}", flush=True)
