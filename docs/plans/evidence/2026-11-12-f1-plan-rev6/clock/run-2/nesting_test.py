"""Revision 6: the nesting bomb (Reviewer B, revision 5, blocker 2).

1. `group_depth` (iterative) equals the depth `re._parser` actually recurses into, on random patterns
   built from the tokens that can fool a naive scan: sets ([]...], [^]...], [(], [\\]]), escapes,
   (?#...) comments, verbose mode (global and scoped) with # comments, named groups, (?P=name)
   backreferences, conditionals and flag groups. The oracle instruments re's own parser.
2. The bombs (499 and 400 deep) are refused by every entry point, never with RecursionError.
3. Every MAX_DEPTH-deep shape is accepted, translated and compiled by `regex` from inside a stack
   already 600 frames deep (the default recursion limit is 1000); MAX_DEPTH + 1 is refused.
4. Any exception (RecursionError, OverflowError, MemoryError, SystemError) becomes `Refused`, and a
   plan holding a bomb is checked without anything escaping.

    python nesting_test.py [patterns]
"""
import inspect, random, re, sys, time, warnings
sys.path.insert(0, ".")
warnings.simplefilter("ignore")
import regex
from re import _parser as P
import translate_prototype as T

failures = []
def check(cond, what):
    if not cond:
        failures.append(what); print("FAIL", what)

# --- 1. scanner against re's own recursion --------------------------------------------------
_orig_sub, _orig_parse = P._parse_sub, P._parse
_stack: list[str] = []
_max = [0]
def _sub(*a, **k):
    _stack.append("S"); _note()
    try: return _orig_sub(*a, **k)
    finally: _stack.pop()
def _par(*a, **k):
    _stack.append("C" if _stack and _stack[-1] in "PC" else "P"); _note()
    try: return _orig_parse(*a, **k)
    finally: _stack.pop()
def _note():
    d = _stack.count("S") - 1 + _stack.count("C")
    _max[0] = max(_max[0], d)
def oracle(p):
    _max[0] = 0; _stack.clear()
    P._parse_sub, P._parse = _sub, _par
    try:
        P.parse(p, 0); ok = True
    except Exception:
        ok = False
    finally:
        P._parse_sub, P._parse = _orig_sub, _orig_parse
    return _max[0], ok

R = random.Random(1)
TOK = ["a", "b", "\\(", "\\)", "\\[", "\\]", "\\\\", "\\#", "[]a]", "[^]a]", "[(]", "[)]", "[\\]]", "[[]", "[#]", "[a-]",
       "[]-a]", "(?#c(()", "(?#c[)", "(?#\\))", "#", "# c(([\n", " ", "\n", "|", "*", "+?", "{2}", "\\N{LATIN SMALL LETTER A}",
       "(?P=g1)", "(?:)", "\\1"]
OPEN = ["(", "(?:", "(?>", "(?=", "(?!", "(?<=", "(?<!", "(?P<g{}>", "(?i:", "(?x:", "(?-x:", "(?x-i:", "(?(1)"]
def gen(d=0):
    out = []
    for _ in range(R.randint(1, 6)):
        if d < 6 and R.random() < 0.35:
            o = R.choice(OPEN).format(R.randint(1, 9))
            out.append(o + gen(d + 1) + (")" if R.random() < 0.95 else ""))
        else:
            out.append(R.choice(TOK))
    return "".join(out)
n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
accepted = exact = low = 0
t0 = time.perf_counter()
for i in range(n):
    p = R.choice(["", "", "(?x)", "(?x)(?i)", "(?ix)"]) + "(?P<g1>a)" * (i % 2) + gen()
    want, ok = oracle(p)
    got = T.group_depth(p)
    if ok:
        accepted += 1
        exact += got == want
        if got != want and low < 10:
            print("DIFF", repr(p), "scan", got, "re", want)
        check(got == want, f"scan {got} != re {want}: {p!r}")
    else:  # re stopped with an error: the scan must not be below what re reached
        if got < want:
            low += 1; check(False, f"scan {got} < re {want} (rejected): {p!r}")
print(f"[1] scanner vs re: {n} patterns, {accepted} accepted by re, exact on {exact}; "
      f"below re on rejected: {low} ({time.perf_counter()-t0:.0f}s)")

# --- 2. the bombs -----------------------------------------------------------------------------
for depth in (499, 400, 100, T.MAX_DEPTH + 1):
    p = "(" * depth + "a" + ")" * depth
    for name, fn in (("analyze", T.analyze), ("check_limits", T.check_limits), ("translate", T.translate),
                     ("stopper", T.stopper), ("compile_rule", T.compile_rule)):
        c0 = time.thread_time()
        try:
            fn(p); check(False, f"{depth}-deep accepted by {name}")
        except T.Refused as e:
            check("deeper than" in str(e), f"{depth}-deep {name}: {e}")
        except BaseException as e:
            check(False, f"{depth}-deep {name} raised {type(e).__name__}")
        check(time.thread_time() - c0 < 0.05, f"{depth}-deep {name} slow")
p = "(?#" + "(" * 600 + ")" + "a"  # a comment full of '(' is not nesting
check(T.group_depth(p) == 0 and T.check_limits(p).depth == 0, "comment counted as nesting")
p = "(?x)#" + "(" * 600 + "\na"   # nor is a verbose comment
check(T.group_depth(p) == 0, "verbose comment counted as nesting")
p = "(?#[)" + "(" * 499 + "a" + ")" * 499 + "]"  # a '[' in a comment must not hide the nesting
check(T.group_depth(p) == 499, "a '[' inside a comment hid the nesting")
p = "(?x)#[\n" + "(" * 499 + "a" + ")" * 499
check(T.group_depth(p) == 499, "a '[' inside a verbose comment hid the nesting")
print(f"[2] bombs refused by every entry point")

# --- 3. the limit, compiled from a deep stack ---------------------------------------------------
def frames():
    f, k = sys._getframe(), 0
    while f is not None:
        f, k = f.f_back, k + 1
    return k
def _down(k, fn):
    return fn() if k <= 0 else _down(k - 1, fn)
def at_frames(target, fn):
    """Run fn with the Python stack already `target` frames deep (absolute)."""
    return _down(target - frames() - 1, fn)
D = T.MAX_DEPTH
SHAPES = {
    "(?i:a|...)*+ (worst measured)": "(?i:a|" * D + "a" + ")*+" * D,
    "(?i:\\b|...)*+": "(?i:\\b|" * D + "a" + ")*+" * D,
    "(a|...)*+": "(a|" * D + "a" + ")*+" * D,
    "capture*+ with a ci negated leaf": "(" * (D - 1) + "(?i:[^\\dı])" + ")*+" * (D - 1),
    "(?>a|...)?+": "(?>a|" * D + "a" + ")?+" * D,
    "lookbehinds": "(?<=" * D + "a" + ")" * D,
    "conditionals": "(a)" + "(?(1)" * D + "a" + ")" * D,
    "mixed": "".join(["(?=", "(", "(?>", "(?!", "(?i:", "(?:a|"][i % 6] for i in range(D)) + "a"
             + "".join([")", ")*+", ")", ")", ")?", ")*"][i % 6] for i in reversed(range(D))),
}
for name, p in SHAPES.items():
    re.compile(p)  # every shape is a valid `re` pattern
    def work(p=p):
        check(frames() >= 600, "not deep")
        a = T.check_limits(p)
        t = T.translate(p)
        regex.purge()
        rx = regex.compile(t)
        T.compile_rule(p)
        return a, t
    try:
        a, t = at_frames(600, work)
        check(a.depth == D, f"{name}: depth {a.depth}")
    except BaseException as e:
        check(False, f"{name}: {type(e).__name__}: {e}")
over = "(" * (D + 1) + "a" + ")" * (D + 1)
try:
    T.check_limits(over); check(False, "MAX_DEPTH + 1 accepted")
except T.Refused:
    pass
# Random MAX_DEPTH-deep patterns: every one is either refused by another limit or compiles from 600 deep.
OPEN3 = ["(", "(?:", "(?>", "(?i:", "(?-i:", "(?s:", "(?=", "(?!", "(?P<g{}>", "(?i:a|", "(a|", "(?:a|", "(?>a|", "(?i:\\b|"]
QS = ["", "*", "*+", "*?", "?+", "{0,3}+", "{0,2}", "+", "{1,2}"]
compiled = refused = 0
for k in range(300):
    opens = [R.choice(OPEN3) for _ in range(D)]
    p = ("(?i)" if R.random() < 0.3 else "") + "".join(o.format(i) for i, o in enumerate(opens)) + R.choice(["a", "\\b", "[^\\W\\d]", "(?i:[^\\dı])"])
    p += "".join(")" + ("" if o in ("(?=", "(?!") else R.choice(QS)) for o in reversed(opens))
    def work(p=p):
        try:
            re.compile(p)
        except re.error:
            return False
        try:
            T.compile_rule(p); return True
        except T.Refused as e:
            if "Recursion" in str(e):
                raise
            return False
    try:
        if at_frames(600, work): compiled += 1
        else: refused += 1
    except BaseException as e:
        check(False, f"random {D}-deep: {type(e).__name__} {p!r}")
print(f"[3] {len(SHAPES)} worst shapes at depth {D} compiled from 600 frames deep; random {D}-deep: "
      f"{compiled} compiled from 600 deep, {refused} refused by another limit, 0 recursion errors expected")

# --- 4. every failure is a refusal ---------------------------------------------------------------
for p, why in (("a{4294967296}", "OverflowError from re's parser"), ("(?<=a+)b", "re.error"),
               ("(?L)a", "re.error/ValueError"), ("a\\", "bad escape")):
    for fn in (T.analyze, T.check_limits, T.translate, T.stopper, T.compile_rule):
        try:
            fn(p); check(False, f"{why}: accepted by {fn.__name__}")
        except T.Refused:
            pass
        except BaseException as e:
            check(False, f"{why}: {fn.__name__} raised {type(e).__name__}")
for exc in (RecursionError, MemoryError, SystemError, OverflowError):
    real = regex.compile
    def boom(*a, **k): raise exc("injected")
    regex.compile = boom
    try:
        T.compile_rule("abc"); check(False, f"{exc.__name__} from regex.compile escaped as success")
    except T.Refused:
        pass
    except BaseException as e:
        check(False, f"{exc.__name__} from regex.compile escaped as {type(e).__name__}")
    finally:
        regex.compile = real
def deep_parse():  # RecursionError inside re's parser, from a stack with almost no room left
    try:
        T.analyze("(" * D + "a" + ")" * D); return "ok"
    except T.Refused:
        return "refused"
r = at_frames(sys.getrecursionlimit() - 20, deep_parse)
check(r == "refused", f"near-limit stack: {r}")
plan = [r"\bpassword\b", "(" * 499 + "a" + ")" * 499, r"(?i)\bconfidential\b", "(" * 16 + "a" + ")+" * 16]
res = T.check_plan(plan)
check(isinstance(res[plan[0]], T.Analysis) and isinstance(res[plan[2]], T.Analysis), "plan: good rules refused")
check(isinstance(res[plan[1]], T.Refused) and isinstance(res[plan[3]], T.Refused), "plan: bombs accepted")
print(f"[4] exceptions become Refused; plan with bombs: {[type(v).__name__ for v in res.values()]}")
print("FAILURES:", len(failures))
sys.exit(1 if failures else 0)
