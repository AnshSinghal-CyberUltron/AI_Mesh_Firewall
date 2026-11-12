"""Revision 7 (Plan Reviewer A, revision 6, blocker 1): case-insensitive translation cost, before
and after the clipped verdict, the per-item budget checks and the `ci_units` limit.

    python ci_cost7.py <rev6 translator dir> <rev7 translator dir>

Every timing is thread CPU with cold caches (the translator's lru caches, `re.purge()`,
`regex.purge()`), median of 5 unless stated. One-time Unicode table builds are warmed first and not
counted (as in Reviewer A's `ciwidth.py`).
"""
import importlib
import re
import statistics
import sys
import time
import warnings

warnings.simplefilter("ignore")
import regex  # noqa: E402

OLD_DIR, NEW_DIR = sys.argv[1], sys.argv[2]


def load(path: str):
    for m in ("translate_prototype", "_cased_table"):
        sys.modules.pop(m, None)
    sys.path.insert(0, path)
    try:
        return importlib.import_module("translate_prototype")
    finally:
        sys.path.remove(path)


OLD, NEW = load(OLD_DIR), load(NEW_DIR)


def cold(T) -> None:
    for v in vars(T).values():
        if callable(getattr(v, "cache_clear", None)):
            v.cache_clear()
    re.purge()
    regex.purge()


def cpu(fn, reps: int = 5) -> tuple[float, str]:
    out, res = [], ""
    for _ in range(reps):
        cold(OLD)
        cold(NEW)
        t0 = time.thread_time()
        try:
            fn()
            res = "ok"
        except Exception as e:  # noqa: BLE001
            res = f"{type(e).__name__}: {str(e)[:70]}"
        out.append(time.thread_time() - t0)
    return statistics.median(out) * 1e3, res


for T in (OLD, NEW):  # warm the one-time Unicode tables of both engines
    T.translate("(?i)[\U00010400-\U0001e943]", cpu_budget_s=None)

print("== 1. one case-insensitive item, whole-item translation (ms, median of 5, cold) ==")
print(f"{'item':38} {'rev 6':>8} {'rev 7':>8} {'rev-7 ci_units':>15}")
for hi in (0x5A, 0x100, 0x1000, 0x4000, 0xFFEF):
    for astral in ("", "\U00010400-\U0001e943"):
        src = f"(?i)[A-{chr(hi)}{astral}]"
        o, _ = cpu(lambda: OLD.translate(src, cpu_budget_s=None))
        n, _ = cpu(lambda: NEW.translate(src, cpu_budget_s=None))
        units = NEW.analyze(src).ci_units
        label = f"[A-U+{hi:04X}{' + astral range' if astral else ''}]"
        print(f"{label:38} {o:8.2f} {n:8.2f} {units:15,}")
for src in ("(?i)[\\x01-\\U0001e943]", "(?i)[\\U00010400-\\U0001e943]"):
    o, _ = cpu(lambda: OLD.translate(src, cpu_budget_s=None))
    n, _ = cpu(lambda: NEW.translate(src, cpu_budget_s=None))
    print(f"{src[4:]:38} {o:8.2f} {n:8.2f} {NEW.analyze(src).ci_units:15,}")

print("\n== 2. Reviewer A's flat 64-set rule and 8-rule plan ==")
items = [f"[{chr(0x41 + i % 26)}-￯{chr(0x10400 + i)}-\U0001e943]" for i in range(64)]
FLAT = "(?i)" + "".join(items)


def rule(k: int) -> str:
    return "(?i)" + "".join(f"[{chr(0x41 + (i + k) % 26)}{chr(0x100 + i + 64 * k)}-￯\U00010400-\U0001e943]"
                            for i in range(64))


PLAN = [rule(k) for k in range(8)]
for name, T in (("rev 6", OLD), ("rev 7", NEW)):
    ms, res = cpu(lambda: T.check_limits(FLAT), reps=3)
    print(f"{name}: check_limits(flat) {ms:.1f} ms -> {res}")
    ms, res = cpu(lambda: T.translate(FLAT, cpu_budget_s=None), reps=3)
    print(f"{name}: translate(flat), no CPU cap {ms:.1f} ms -> {res}")
    calls = {"n": 0}
    orig = T._check_budget

    def counting(orig=orig, calls=calls):
        calls["n"] += 1
        return orig()
    T._check_budget = counting
    cold(T)
    t0 = time.thread_time()
    try:
        T.translate(FLAT, cpu_budget_s=0.05)
        res = "TRANSLATED (the cap did not fire)"
    except T.Refused as e:
        res = f"Refused: {e}"
    dt = (time.thread_time() - t0) * 1e3
    T._check_budget = orig
    print(f"{name}: translate(flat) with the 50 ms cap: {res} after {dt:.1f} ms; budget checks {calls['n']}")
    cold(T)
    t0 = time.thread_time()
    verdicts = T.check_plan(PLAN)
    t_check = (time.thread_time() - t0) * 1e3
    acc = [p for p, v in verdicts.items() if isinstance(v, T.Analysis)]
    reasons = sorted({str(v)[:60] for v in verdicts.values() if not isinstance(v, T.Analysis)})
    cold(T)
    t0 = time.thread_time()
    for p in acc:
        try:
            T.compile_rule(p)
        except T.Refused:
            pass
    t_comp = (time.thread_time() - t0) * 1e3
    print(f"{name}: 8-rule plan: check_plan {t_check:.0f} ms, {len(acc)} accepted, refused reasons {reasons}; "
          f"gateway compile of the accepted rules {t_comp:.0f} ms")

print(f"\n== 3. worst accepted shapes at the rev-7 limits (ci_units {NEW.MAX_CI_UNITS:,} per rule, "
      f"{NEW.MAX_PLAN_CI_UNITS:,} per plan; 64 items per rule) ==")


def fill(make) -> str:
    """As many distinct items of one kind as the per-rule limits accept."""
    items: list[str] = []
    for i in range(64):
        cand = items + [make(i)]
        try:
            NEW.check_limits("(?i)" + "".join(cand))
        except NEW.Refused:
            break
        items = cand
    return "(?i)" + "".join(items)


SHAPES = {
    "literals (64)": fill(lambda i: chr(0x100 + i)),
    "2-character sets (rev-6 worst)": fill(lambda i: f"[{chr(0x61 + i % 26)}{chr(0x100 + i)}]"),
    "clipped full-BMP ranges": fill(lambda i: f"[{chr(0x2000 + i)}A-\uffef]"),
    "negated literals": fill(lambda i: f"[^{chr(0x100 + i)}]"),
    "negated category sets": fill(lambda i: f"[^\\s{chr(0x2000 + i)}]"),
    "BMP-to-astral ranges": fill(lambda i: f"[{chr(0x2000 + i)}\\x01-\\U0001e943]"),
    "sets of 32 narrow ranges": fill(lambda i: f"[{chr(0x2000 + i)}" + "".join(
        f"{NEW._esc(a)}-{NEW._esc(b)}" for a, b in NEW.CASED_RANGES[:64:2]) + "]"),
}
print(f"{'shape':34} {'items':>5} {'ci_units':>9} {'check':>7} {'translate':>10} {'compile':>8}")
worst = 0.0
for name, p in SHAPES.items():
    a = NEW.check_limits(p)
    c_ms, _ = cpu(lambda: NEW.check_limits(p))
    t_ms, _ = cpu(lambda: NEW.translate(p, cpu_budget_s=None))
    text = NEW.translate(p, cpu_budget_s=None)
    k_ms, _ = cpu(lambda: regex.compile(text))
    worst = max(worst, t_ms)
    print(f"{name:34} {a.ci_items:5} {a.ci_units:9,} {c_ms:7.1f} {t_ms:10.1f} {k_ms:8.1f}")
print(f"worst accepted shape: {worst:.1f} ms of translation CPU, against the {NEW.TRANSLATE_CPU_BUDGET_S * 1e3:.0f} ms cap")
for name in ("clipped full-BMP ranges", "negated category sets", "2-character sets (rev-6 worst)"):
    base = SHAPES[name]
    plan = [base.replace("(?i)", "(?i)" + chr(0x3000 + k), 1) for k in range(8)]
    verdicts = NEW.check_plan(plan)
    acc = [p for p, v in verdicts.items() if isinstance(v, NEW.Analysis)]
    cold(NEW)
    t0 = time.thread_time()
    for p in acc:
        NEW.compile_rule(p, cpu_budget_s=None)
    print(f"plan of 8 '{name}' rules: {len(acc)} accepted by the per-plan sums; gateway translate + compile "
          f"of those {(time.thread_time() - t0) * 1e3:.0f} ms (cold, one pass)")
