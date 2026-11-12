"""Revision-6 cost measurements (thread CPU, ms). Writes perf6.md.

  1. Reviewer B's revision-5 cases, revision 5 against revision 6.
  2. The worst accepted shape at each limit: translate, compile (total and the C call, which holds
     the GIL), match on 32 KiB (prose, one unbroken word, code) against `re`, and the retained bytes.
  3. Plans at the per-plan limits: total translate + compile CPU.
  4. The parse-only check (projector) on hostile plans.
"""
import importlib.util, os, random, re, sys, time, tracemalloc, warnings
sys.path.insert(0, ".")
warnings.simplefilter("ignore")
import regex, regex.regex as RR
import translate_prototype as T

spec = importlib.util.spec_from_file_location("tp_r5", "../r5_orig/translate_prototype.py")
R5 = importlib.util.module_from_spec(spec); sys.modules["tp_r5"] = R5; spec.loader.exec_module(R5)

_c = RR._regex.compile; LAST = {}
def _timed(*a, **k):
    t0 = time.thread_time(); r = _c(*a, **k); LAST["c"] = time.thread_time() - t0; return r
class _M:
    compile = staticmethod(_timed)
    def __getattr__(self, k): return getattr(RR._orig, k)
RR._orig = RR._regex; RR._regex = _M()

rnd = random.Random(3)
WORDS = "the model answer explains how quarterly revenue grew while costs fell in 2025 and 2026 across regions".split()
PROSE = " ".join(rnd.choice(WORDS) + rnd.choice(["", "", "", ",", "."]) for _ in range(9000))[:32768]
WORD = ("abcdefghij" * 4000)[:32768]
CODE = ("def f(x):\n    return x * 2  # 0x1F3A base64:QUJDRA==\n" * 800)[:32768]
OUT = []
def out(s=""):
    print(s, flush=True); OUT.append(s)

def compile_cost(text, reps=3):
    best = None
    for _ in range(reps):
        regex.purge(); t0 = time.thread_time(); rx = regex.compile(text); tot = time.thread_time() - t0
        cand = (tot, LAST["c"])
        best = cand if best is None or cand[0] < best[0] else best
    return rx, best

def footprint(text):
    regex.purge(); tracemalloc.start(); tracemalloc.reset_peak()
    a = tracemalloc.get_traced_memory()[0]
    rx = regex.compile(text)
    cur, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    return (cur - a), (peak - a), rx

def match_cost(rx, text, timeout=2.0):
    t0 = time.thread_time()
    try:
        for _ in rx.finditer(text, timeout=timeout, concurrent=True):
            pass
    except TimeoutError:
        return float("inf")
    return (time.thread_time() - t0) * 1e3

def re_match_cost(p, text):
    rx = re.compile(p); t0 = time.thread_time()
    for _ in rx.finditer(text):
        pass
    return (time.thread_time() - t0) * 1e3

def fmt(x):
    return ">2000" if x == float("inf") else f"{x:.1f}"

# 1. ------------------------------------------------------------------------------------------
out("## 1. Reviewer B's revision-5 cases (`limits_attack.py`), revision 5 → revision 6\n")
out("| Pattern | Rev. 5: passes limits? translated chars, compile ms, match 32 KiB ms | Rev. 6: verdict | Rev. 6 estimate (size / units) |")
out("|---|---|---|---|")
B_CASES = {
    "`\\s`×500": r"\s" * 500,
    "`\\b.`×330": r"\b." * 330,
    "`(?=\\w)`×166 + `a`": r"(?=\w)" * 166 + "a",
    "`(?<=\\w)`×142 + `a`": r"(?<=\w)" * 142 + "a",
    "400-deep groups": "(" * 400 + "a" + ")" * 400,
    "499-deep groups (projector poison)": "(" * 499 + "a" + ")" * 499,
    "330-deep `(?:`": "(?:" * 330 + "a" + ")" * 330,
    "`(a)+` nested 16 (new)": "(" * 16 + "a" + ")+" * 16,
    "`(?:a)+` nested 24 (new)": "(?:" * 24 + "a" + ")+" * 24,
    "`(a)+` nested 32 (new: OOM-killed the probe)": "(" * 32 + "a" + ")+" * 32,
}
for name, p in B_CASES.items():
    try:
        R5.check_limits(p)
        if "32" in name or "nested 24" in name:
            r5 = "passes; compile not run (exponential: 16 levels 0.2-0.3 s, 32 levels exhausted memory)"
        else:
            t = R5.translate(p, cpu_budget_s=None); rx, (tot, c) = compile_cost(t, 1)
            r5 = f"passes; {len(t):,} chars, compile {tot*1e3:.0f} ms, match {fmt(match_cost(rx, PROSE))} ms"
    except RecursionError:
        r5 = "**RecursionError** escapes"
    except Exception as e:
        r5 = f"{type(e).__name__}: {str(e)[:50]}"
    try:
        a = T.check_limits(p); r6 = f"accepted"
        est = f"{a.translated_size:,} / {a.compile_units:,}"
    except T.Refused as e:
        r6 = f"refused: {e}"
        try:
            a = T.analyze(p); est = f"{a.translated_size:,} / {a.compile_units:,}"
        except T.Refused:
            est = "—"
    out(f"| {name} | {r5} | {r6} | {est} |")

# 2. ------------------------------------------------------------------------------------------
def fill(unit, cap_size=T.MAX_TRANSLATED_SIZE, cap_units=T.MAX_COMPILE_UNITS, head="", tail="", max_len=T.MAX_LENGTH):
    """The longest head + unit*k + tail that the revision-6 limits accept."""
    lo, hi = 0, max_len
    best = None
    while lo <= hi:
        k = (lo + hi) // 2
        p = head + unit * k + tail
        ok = len(p) <= max_len
        if ok:
            try:
                T.check_limits(p)
            except T.Refused:
                ok = False
        if ok:
            best = p; lo = k + 1
        else:
            hi = k - 1
    return best

cased = [chr(c) for c in range(0x41, 0x24F) if chr(c).lower() != chr(c).upper()]
SHAPES = {
    "`\\s` repeated (set expansion)": fill(r"\s"),
    "`\\b.` repeated (two-sided boundary)": fill(r"\b."),
    "`\\b` repeated (two-sided, consecutive)": fill(r"\b", tail="a"),
    "`(?=\\w)` repeated + `a` (lookahead)": fill(r"(?=\w)", tail="a"),
    "`(?<=\\w)` repeated + `a` (lookbehind)": fill(r"(?<=\w)", tail="a"),
    "`[^\\W\\d]` repeated (negated set with categories)": fill(r"[^\W\d]"),
    "ci keyword list `(?i)\\b(?:…)\\b`": fill("|".join(rnd.choice(["invoice", "confidential", "internal", "bitcoin", "iban", "salary", "passport"]) for _ in range(1)) + "|", head="(?i)\\b(?:", tail="x)\\b"),
    "ci 64 distinct 2-char sets": "(?i)" + "".join("[" + re.escape(a) + re.escape(b) + "]" for a, b in (rnd.sample(cased, 2) for _ in range(64))),
    "ci astral literals in sets": fill("[\U0001e91c\U00010400a]", head="(?i)"),
    "`x{1000}` repeated (unrolled minimum)": fill("x{1000}"),
    "`\\w{3}\\s` × 250-style counted group": "(?:\\w{3}\\s){250}",
}
def _ok(p):
    try:
        T.check_limits(p); return True
    except T.Refused:
        return False
SHAPES["`(a)+` nested (deepest accepted)"] = next("(" * d + "a" + ")+" * d for d in range(16, 0, -1) if _ok("(" * d + "a" + ")+" * d))
SHAPES["`(?:a|b)+` nested (deepest accepted)"] = next("(?:a|" * d + "b" + ")+" * d for d in range(16, 0, -1) if _ok("(?:a|" * d + "b" + ")+" * d))
SHAPES["literal alternation, 1000 chars"] = "|".join(f"w{i:03d}" for i in range(200))[:999]
SHAPES["`(?i:a|…)*+` nested 16 (stack worst)"] = "(?i:a|" * 16 + "a" + ")*+" * 16
SHAPES["`\\b\\w+\\s+(?:token|secret)\\b` (A's rule)"] = r"\b\w+\s+(?:token|secret)\b"
SHAPES["IBAN shape"] = r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"

out("\n## 2. The worst accepted shape at each limit (revision 6)\n")
out(f"Limits: length ≤ {T.MAX_LENGTH}, depth ≤ {T.MAX_DEPTH}, translated size ≤ {T.MAX_TRANSLATED_SIZE:,}, "
    f"compile units ≤ {T.MAX_COMPILE_UNITS:,}, ci items ≤ {T.MAX_CI_ITEMS}, expanded repetition ≤ {T.MAX_EXPANDED_REPETITION}.\n")
out("| Shape | Src chars | Translated (estimate) | Units (estimate) | Translate | Compile total | of which C (GIL held) | Retained / peak KiB | Match 32 KiB: prose / word / code | `re` prose / word / code |")
out("|---|---|---|---|---|---|---|---|---|---|")
worst = {"translate": 0, "compile": 0, "c": 0, "retained": 0, "peak": 0}
for name, p in SHAPES.items():
    if not p:
        continue
    a = T.check_limits(p)
    T._ci_set.cache_clear(); T._ci_verdicts.cache_clear()
    t0 = time.thread_time(); t = T.translate(p, cpu_budget_s=None); tt = (time.thread_time() - t0) * 1e3
    rx, (tot, c) = compile_cost(t)
    ret, peak, _ = footprint(t)
    m = [match_cost(rx, s) for s in (PROSE, WORD, CODE)]
    mr = [re_match_cost(p, s) for s in (PROSE, WORD, CODE)]
    worst["translate"] = max(worst["translate"], tt); worst["compile"] = max(worst["compile"], tot * 1e3)
    worst["c"] = max(worst["c"], c * 1e3); worst["retained"] = max(worst["retained"], ret); worst["peak"] = max(worst["peak"], peak)
    out(f"| {name} | {len(p)} | {len(t):,} ({a.translated_size:,}) | {T.compile_units(t):,} ({a.compile_units:,}) | {tt:.1f} | {tot*1e3:.1f} | {c*1e3:.2f} | "
        f"{ret/1024:.0f} / {peak/1024:.0f} | {' / '.join(fmt(x) for x in m)} | {' / '.join(f'{x:.1f}' for x in mr)} |")
out(f"\nWorst over these shapes: translate {worst['translate']:.1f} ms, compile {worst['compile']:.1f} ms, "
    f"C call {worst['c']:.2f} ms, retained {worst['retained']/1024:.0f} KiB, peak {worst['peak']/1024:.0f} KiB.")

# 3. ------------------------------------------------------------------------------------------
out("\n## 3. Plans at the per-plan limits (translate + compile, cold, rule by rule)\n")
def plan_cost(rules):
    res = T.check_plan(rules)
    acc = [p for p in rules if isinstance(res[p], T.Analysis)]
    T._ci_set.cache_clear(); T._ci_verdicts.cache_clear(); regex.purge()
    tc = cc = 0.0; size = units = 0; maxc = 0.0
    for p in acc:
        t0 = time.thread_time(); t = T.translate(p, cpu_budget_s=None); tc += time.thread_time() - t0
        t0 = time.thread_time(); regex.compile(t); d = time.thread_time() - t0; cc += d; maxc = max(maxc, LAST["c"])
        size += len(t); units += T.compile_units(t)
    return len(acc), len(rules) - len(acc), size, units, tc * 1e3, cc * 1e3, maxc * 1e3
plans = {
    "64 × the costliest per-character shape (`\\b.` at the size cap)": [SHAPES["`\\b.` repeated (two-sided boundary)"] + f"(?:x{i})" for i in range(64)],
    "64 × `\\s` at the size cap": [SHAPES["`\\s` repeated (set expansion)"][:-4] + f"(?:{i})" for i in range(64)],
    "64 × `(a)+` nested at the units cap": [SHAPES["`(a)+` nested (deepest accepted)"] + f"(?:z{i})" for i in range(64)],
    "64 × `x{1000}` at the units cap": [SHAPES["`x{1000}` repeated (unrolled minimum)"] + f"(?:z{i})" for i in range(64)],
    "64 × ci 64 distinct sets (512 per-plan ci items)": ["(?i)" + "".join("[" + re.escape(a) + re.escape(b) + "]" for a, b in (rnd.sample(cased, 2) for _ in range(64))) for _ in range(64)],
}
out("| Plan | Rules accepted / refused by the plan sums | Translated chars | Units | Translate ms | Compile ms | Largest C call ms |")
out("|---|---|---|---|---|---|---|")
for name, rules in plans.items():
    n_ok, n_bad, size, units, tc, cc, maxc = plan_cost(rules)
    out(f"| {name} | {n_ok} / {n_bad} | {size:,} | {units:,} | {tc:.0f} | {cc:.0f} | {maxc:.2f} |")

# 4. ------------------------------------------------------------------------------------------
out("\n## 4. The parse-only check (projector, control): `check_plan`, thread CPU\n")
hostile = {
    "64 rules: 1000-char `\\b`-dense ci patterns (each boundary asks `re` for a verdict)": ["(?i)" + "".join("\\b" + re.escape(rnd.choice(cased)) for _ in range(330)) for _ in range(64)],
    "64 rules: 990-char literal alternations": ["|".join(f"w{i:03d}" for i in range(198)) for _ in range(64)],
    "64 rules: `\\b.`×330 (refused)": [r"\b." * 330 for _ in range(64)],
    "64 rules: 499-deep bombs (refused)": ["(" * 499 + "a" + ")" * 499 for _ in range(64)],
    "64 rules: (?i) + 64 distinct 2-char sets": ["(?i)" + "".join("[" + re.escape(a) + re.escape(b) + "]" for a, b in (rnd.sample(cased, 2) for _ in range(64))) for _ in range(64)],
}
out("| Plan | check_plan ms (cold caches) | per rule worst ms |")
out("|---|---|---|")
for name, rules in hostile.items():
    T._ci_set.cache_clear(); T._ci_verdicts.cache_clear()
    t0 = time.thread_time(); T.check_plan(rules); tot = (time.thread_time() - t0) * 1e3
    wr = 0.0
    for p in rules[:8]:
        T._ci_verdicts.cache_clear(); t0 = time.thread_time()
        try:
            T.check_limits(p)
        except T.Refused:
            pass
        wr = max(wr, (time.thread_time() - t0) * 1e3)
    out(f"| {name} | {tot:.1f} | {wr:.1f} |")

out(f"\nHost: 2 shared CPUs, load average {os.getloadavg()[0]:.1f} at the end; thread CPU throughout.")
open("perf6.md", "w").write("\n".join(OUT) + "\n")
