"""Revision 7 (Plan Reviewer B, revision 6, should-fix 6): the per-worker program cache at 100k orgs.

    python cache7.py

Cold translate + `regex.compile` of an ordinary plan (Reviewer B's `cost/cold_compile.py` rule sets,
perf5's TEN_B and 64 rules of the same shapes), thread CPU, median of 5, cold caches; the bytes a
compiled rule retains (tracemalloc: `regex` allocates through PyMem, which it traces), against its
compile units (the translator's own caches are cleared before reading, so only the compiled program
counts), over the corpus and 2,000 accepted fuzz patterns; the revision-6 charge
(80 x units + 16 KiB) against the revision-7 one fitted here; and the resulting cache residency.
"""
import random, re, statistics, sys, time, tracemalloc, warnings
warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import regex
import translate_prototype as T
import fuzz_gen as G
from cost7 import TEN_B, sixty_four

def cold():
    for v in vars(T).values():
        if callable(getattr(v, "cache_clear", None)): v.cache_clear()
    re.purge(); regex.purge()

T.compile_rule("(?i)x")
for name, rules in (("10 ordinary rules", TEN_B), ("64 ordinary rules", sixty_four())):
    cpu = []
    for _ in range(5):
        cold(); c0 = time.thread_time(); [T.compile_rule(p) for p in rules]; cpu.append(time.thread_time() - c0)
    units = sum(T.check_limits(p).compile_units for p in rules)
    [T.compile_rule(p) for p in rules]
    cold(); tracemalloc.start(); progs = [T.compile_rule(p) for p in rules]; cold(); retained = tracemalloc.get_traced_memory()[0]; tracemalloc.stop()
    print(f"{name}: cold translate + compile median {statistics.median(cpu)*1e3:.1f} ms; compile units {units}; "
          f"retained {retained/1024:.0f} KiB; rev-6 charge {(80*units + 16384*len(rules))/1024:.0f} KiB")
G.R.seed(7)
pats = []
while len(pats) < 2000:
    p = G.seq()
    try:
        re.compile(p); a = T.check_limits(p); pats.append((p, a.compile_units))
    except Exception:
        pass
pats += [(p, T.check_limits(p).compile_units) for p in TEN_B + sixty_four()]
worst_ratio, worst_base, rows = 0.0, 0, []
for p, units in pats:
    T.compile_rule(p)  # warm `regex`'s global Unicode tables first: they are built once per process
    cold(); tracemalloc.start(); prog = T.compile_rule(p); cold(); b = tracemalloc.get_traced_memory()[0]; tracemalloc.stop()
    rows.append((b, units, prog.__sizeof__())); del prog
ratios = sorted(b / s for b, _u, s in rows)
print(f"{len(rows)} accepted rules: retained bytes median {statistics.median(b for b, _u, _s in rows):.0f}, "
      f"max {max(b for b, _u, _s in rows)}; retained / Pattern.__sizeof__(): median {statistics.median(ratios):.2f}, "
      f"max {ratios[-1]:.2f}")
charge = lambda size: 2 * size + 4096  # the revision-7 charge: measured at compile time
over = sum(1 for b, _u, s in rows if b > charge(s))
print(f"revision-7 charge 2 x Pattern.__sizeof__() + 4 KiB: rules retaining more than their charge: {over} of {len(rows)}; "
      f"charge / retained median {statistics.median(charge(s) / max(b, 1) for b, _u, s in rows):.2f}, "
      f"max {max(charge(s) / max(b, 1) for b, _u, s in rows):.2f}")
for name, rules in (("10 ordinary rules", TEN_B), ("64 ordinary rules", sixty_four())):
    c7 = sum(charge(T.compile_rule(p).__sizeof__()) for p in rules)
    print(f"{name}: revision-7 charge {c7/1024:.0f} KiB per plan; plans resident in a 64 MiB cache: {64*2**20 // c7}")
