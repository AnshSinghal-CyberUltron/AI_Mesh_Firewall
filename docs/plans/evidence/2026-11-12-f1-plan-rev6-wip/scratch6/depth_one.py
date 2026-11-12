import sys, re, regex, warnings, time, resource
warnings.simplefilter("ignore")
sys.path.insert(0, "..")
import translate_prototype as T
D = int(sys.argv[1]); which = sys.argv[2]
leaf = r"(?i:[^\dı])"
cases = {
  "cappos": "(" * D + "a" + ")++" * D,
  "noncappos": "(?:" * D + "a" + ")++" * D,
  "capgreedy": "(" * D + "a" + ")+" * D,
}
p = cases[which]
for step in ("re.compile", "translate", "regex.compile"):
    t0 = time.thread_time()
    if step == "re.compile": re.compile(p)
    elif step == "translate": t = T.translate(p, cpu_budget_s=None)
    else: regex.compile(t)
    print(step, f"{(time.thread_time()-t0)*1e3:.1f} ms maxrss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024} MiB", flush=True)
