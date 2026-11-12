import sys, re, regex, warnings, time
warnings.simplefilter("ignore")
sys.path.insert(0, "..")
import translate_prototype as T
from depth_probe3 import headroom, tdepth
D = int(sys.argv[1])
leaf = r"(?i:[^\dı])"
cases = {
  "(?i: *+": "(?i:" * D + "a" + ")*+" * D,
  "(?s: *+ ci leaf": "(?s:" * (D-1) + leaf + ")*+" * (D-1),
  "(?-i: *+": "(?i)" + "(?-i:" * D + "a" + ")*+" * D,
  "(?i: *?": "(?i:" * D + "a" + ")*?" * D,
  "(?i: {0,2}+": "(?i:" * D + "a" + "){0,2}+" * D,
  "(?i:a| *+": "(?i:a|" * D + "a" + ")*+" * D,
  "(?P<n> *+": "".join(f"(?P<g{i}>" for i in range(D)) + "a" + ")*+" * D,
  "(?<=(?i: *+": "(?<=(?i:" * (D//2) + "a" + "))" * (D//2),
}
for name, p in cases.items():
    try:
        re.compile(p)
    except Exception as e:
        print(f"{name:30} re refuses: {type(e).__name__} {e}"); continue
    t = T.translate(p, cpu_budget_s=None)
    print(f"{name:30} src {len(p):4} tdepth {tdepth(t):4} | headroom translate {headroom(lambda: T.translate(p, cpu_budget_s=None)):4} "
          f"regex.compile {headroom(lambda: (regex.purge(), regex.compile(t))):4}", flush=True)
