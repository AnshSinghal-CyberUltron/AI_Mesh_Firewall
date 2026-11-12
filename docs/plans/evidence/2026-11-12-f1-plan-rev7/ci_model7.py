"""Revision 7: calibrate and validate the case-insensitive cost model (`_ci_units`) of the translator.

    python ci_model7.py <seed> <fuzz_rules>

Part 1 measures each feature of the model on its own (per item, thread CPU, cold caches, median of
5 rules of 16 distinct items): a literal item; a set's base; each literal, range and category member;
a heavy item (negated, with a category, or covering more than 64 cased code points); each cased code
point a clipped range covers; each BMP code point of a BMP-to-astral range.
Part 2 validates: random rules of 1-64 distinct case-insensitive items of every kind; for each, the
real translation CPU against the model's units (1 unit = 1 microsecond on this host). Reports the
largest real/model ratio (it must stay below 1 for the model to be a bound here) and the worst
accepted rule's translation CPU against the 50 ms translation cap.
"""
import random
import re
import statistics
import sys
import time
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import regex  # noqa: E402

import translate_prototype as T  # noqa: E402

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
n_fuzz = int(sys.argv[2]) if len(sys.argv) > 2 else 300
rng = random.Random(seed)


def cold() -> None:
    for v in vars(T).values():
        if callable(getattr(v, "cache_clear", None)):
            v.cache_clear()
    re.purge()
    regex.purge()


T.translate("(?i)[\U00010400-\U0001e943]", cpu_budget_s=None)  # one-time Unicode tables


def cost(pattern: str, reps: int = 5) -> float:
    out = []
    for _ in range(reps):
        cold()
        t0 = time.thread_time()
        T.translate(pattern, cpu_budget_s=None)
        out.append(time.thread_time() - t0)
    return statistics.median(out) * 1e6


def per_item(items: list[str]) -> float:
    return cost("(?i)" + "".join(items)) / len(items)


def tag(i: int) -> str:
    return chr(0x2000 + i)  # a distinct non-cased member per item, so the items stay distinct


N = 16
print("== part 1: per-item cost of each feature (microseconds, cold, median of 5) ==")
rows = [
    ("literal item", [chr(0x100 + i) for i in range(N)]),
    ("set, 2 literal members", [f"[{tag(i)}a]" for i in range(N)]),
    ("set, 32 literal members", [f"[{tag(i)}" + "".join(chr(0x61 + j % 26) + chr(0x3b1 + j % 24) for j in range(16)) + "]" for i in range(N)]),
    ("set, 8 narrow ranges", [f"[{tag(i)}" + "".join(f"{chr(0x61 + 3 * j)}-{chr(0x62 + 3 * j)}" for j in range(8)) + "]" for i in range(N)]),
    ("set, 32 narrow ranges", [f"[{tag(i)}" + "".join(f"{T._esc(a)}-{T._esc(b)}" for a, b in T.CASED_RANGES[:64:2]) + "]" for i in range(N)]),
    ("set with 1 category (heavy)", [f"[\\w{tag(i)}]" for i in range(N)]),
    ("negated, 2 members (heavy)", [f"[^\\s{tag(i)}]" for i in range(N)]),
    ("negated literal (heavy)", [f"[^{chr(0x100 + i)}]" for i in range(N)]),
    ("clipped wide [A-U+FFEF] (heavy)", [f"[{tag(i)}A-￯]" for i in range(N)]),
    ("astral-only wide range", [f"[{tag(i)}\\U00010000-\\U0010ffff]" for i in range(N)]),
    ("BMP-to-astral [U+0001-astral]", [f"[{tag(i)}\\x01-\\U0001e943]" for i in range(3)]),
]
print(f"{'item':36} {'real':>8} {'units':>8} {'real/units':>10}")
for name, items in rows:
    p = "(?i)" + "".join(items)
    units = sum(T._ci_units(T._ci_items_of(it)) for it in items)
    real = cost(p)
    print(f"{name:36} {real / len(items):8.0f} {units / len(items):8.0f} {real / units:10.2f}")


def rand_member() -> str:
    r = rng.random()
    if r < 0.35:
        return T._esc(rng.choice((rng.randrange(0x41, 0x24F), rng.randrange(0x370, 0x52F), rng.randrange(0x10400, 0x1e943))))
    if r < 0.45:
        return rng.choice((r"\w", r"\W", r"\d", r"\D", r"\s", r"\S"))
    k = rng.random()
    if k < 0.5:
        a = rng.randrange(0x41, 0xFFF0)
        b = min(0xFFEF, a + rng.choice((2, 30, 300, 3000, 65000)))
    elif k < 0.8:
        a = rng.randrange(0x10000, 0x10FFFF)
        b = min(0x10FFFF, a + rng.choice((2, 300, 65000, 1000000)))
    else:
        a, b = rng.randrange(0x20, 0xFFF0), rng.randrange(0x10000, 0x10FFFF)
    return f"{T._esc(a)}-{T._esc(b)}"


def rand_item(i: int) -> str:
    if rng.random() < 0.3:
        return chr(rng.choice((0x41 + i % 26, 0x100 + i, 0x3b1 + i % 24)))
    neg = "^" if rng.random() < 0.25 else ""
    return f"[{neg}{tag(i)}" + "".join(rand_member() for _ in range(rng.choice((1, 1, 2, 3, 4, 8, 16)))) + "]"


print(f"\n== part 2: {n_fuzz} random rules, seed {seed} ==")
ratios, worst = [], (0.0, "")
refused = 0
for _ in range(n_fuzz):
    # Add random items while the rule stays within its limits, up to a random target share of the
    # unit cap, so the accepted rules spread up to the cap.
    target = rng.uniform(0.3, 1.0) * T.MAX_CI_UNITS
    items: list[str] = []
    for i in range(64):
        cand = items + [rand_item(i)]
        try:
            a = T.check_limits("(?i)" + "".join(cand))
        except T.Refused:
            continue
        items = cand
        if a.ci_units >= target:
            break
    p = "(?i)" + "".join(items) if items else "(?i)a"
    try:
        a = T.check_limits(p)
    except T.Refused:
        refused += 1
        continue
    real = cost(p, reps=3)
    if a.ci_units:
        ratios.append((real / a.ci_units, p[:60]))
    if real > worst[0]:
        worst = (real, f"{a.ci_items} items, {a.ci_units} units")
ratios.sort()
print(f"accepted {len(ratios)}, refused {refused}; real/model ratio: median "
      f"{statistics.median(r for r, _ in ratios):.2f}, p99 {ratios[int(0.99 * (len(ratios) - 1))][0]:.2f}, "
      f"max {ratios[-1][0]:.2f} ({ratios[-1][1]!r})")
print(f"worst accepted rule: {worst[0] / 1e3:.1f} ms of translation CPU ({worst[1]}); cap "
      f"{T.TRANSLATE_CPU_BUDGET_S * 1e3:.0f} ms; per-rule unit cap {T.MAX_CI_UNITS}")
