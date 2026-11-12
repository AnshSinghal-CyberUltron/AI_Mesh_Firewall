"""Search hostile-but-legal shapes for the slowest translation (revision 5, after Reviewer A's B1)."""
import random, re, sys, time
sys.path.insert(0, ".")
from translate_prototype import Refused, check_limits, translate, _ci_set, _ci_verdicts
R = random.Random(7)
cased = [chr(c) for c in range(0x41, 0x24F) if chr(c).lower() != chr(c).upper()]
def units(us, head="", tail=""):
    out = head
    for u in us:
        if len(out) + len(u) + len(tail) > 1000: break
        out += u
    return out + tail
shapes = {
  "166 wide ranges in repeats (A, B1)": units([f"[{chr(a)}-\uffef]+" for a in range(0x100, 0x800, 3)]),
  "\\b next to 166 wide ranges (A, B1)": units([f"\\b[{chr(a)}-\uffef]" for a in range(0x100, 0x800, 3)]),
  "one set of 300 ranges next to \\b": units([f"{chr(a)}-{chr(a+1)}" for a in range(0x100, 0x1000, 2)], "\\b[", "]"),
  "\\b before 1 alternation of 330 literals": units([chr(0x100+i) + "|" for i in range(600)], "\\b(?:", "z)"),
  "nested groups depth 100 behind \\b": "\\b" + "(" * 100 + "a" + ")" * 100 + "\\b",
  "333 boundaries around single chars": units(["\\ba"] * 400),
  "(?i) 64 distinct 2-char sets (ci cap)": "(?i)" + "".join("[" + re.escape(a) + re.escape(b) + "]" for a, b in (R.sample(cased, 2) for _ in range(64))),
  "(?i) 64 ci sets each next to \\b": "(?i)" + "".join("\\b[" + re.escape(a) + re.escape(b) + "]" for a, b in (R.sample(cased, 2) for _ in range(64))),
  "250 atomic-repeat candidates": units(["\\w+\\s"] * 300),
}
worst = 0
for name, p in shapes.items():
    try:
        a = check_limits(p)
    except Refused as e:
        print(f"{name:42} refused by limits: {e}"); continue
    _ci_set.cache_clear(); _ci_verdicts.cache_clear()
    t0 = time.thread_time(); translate(p, cpu_budget_s=None); t = (time.thread_time() - t0) * 1e3
    worst = max(worst, t)
    print(f"{name:42} {len(p):4} chars  translate {t:6.1f} ms CPU")
print(f"worst legal translation: {worst:.1f} ms CPU")
