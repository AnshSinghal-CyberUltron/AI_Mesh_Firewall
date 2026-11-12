import sys, time
sys.path.insert(0, "../scratch6"); sys.argv = ["x"]
exec(open("../scratch6/ccal2.py").read().split("src_shapes = {")[0])
cands = {}
for d in range(8, 14):
    cands[f"(a)+ nest x{d}"] = "(" * d + "a" + ")+" * d
    cands[f"(?:(a)+) nest x{d}"] = "(?:" * d + "(a)" + ")+" * d
    cands[f"(a)+? nest x{d}"] = "(" * d + "a" + ")+?" * d
    cands[f"(?i)(a)+ nest x{d}"] = "(?i)" + "(" * d + "a" + ")+" * d
    cands[f"\\b(a)+ nest x{d}"] = "(" * d + r"\ba" + ")+" * d
    cands[f"(a|b)+ nest x{d}"] = "(" * d + "a|b" + ")+" * d
    cands[f"(a)+(a)+ x{d}"] = "(" * d + "a" + ")+(a)" * d
best = {}
for name, p in cands.items():
    try:
        a = T.check_limits(p)
    except T.Refused:
        continue
    t = T.translate(p); cs = []
    for _ in range(3):
        regex.purge(); regex.compile(t); cs.append(last["c"])
    key = name.rsplit(" x", 1)[0]
    c = min(cs)
    if key not in best or best[key][1] < c:
        best[key] = (name, c, T.compile_units(t))
for k, (name, c, u) in best.items():
    print(f"{name:26} units {u:6}  C {c*1e3:6.2f} ms  {c*1e9/u:6.1f} ns/unit")
