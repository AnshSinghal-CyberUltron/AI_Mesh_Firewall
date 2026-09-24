"""Corrected dependency table + machine verification against every exit-artifact need."""
import sys, re, json, runpy
from pathlib import Path
EVD = Path(sys.argv[2])
CORR = {  # card: deps   (comments = why an edge was added)
 "GW00": [], "GW01": ["GW00", "T02"], "GW02": ["GW00", "GW01", "T02", "T04"], "GW03": ["GW00"], "GW04": ["GW00"],
 "GW05": ["GW04"], "GW06": ["GW03", "GW04", "GW05"], "GW07": ["GW04", "GW05"], "GW08": ["GW03", "GW04", "GW07"],
 "GW09": ["GW02", "GW08"], "GW10": ["GW02", "GW08"], "GW11": ["GW02", "GW03", "GW07", "GW08"],
 "GW12": ["GW01", "GW03", "GW08", "GW11"], "GW13": ["GW07", "GW09", "GW10", "GW12"],
 "GW14": ["GW02", "GW04", "GW06", "GW08", "GW12", "GW13"],
 "GW15": ["GW01", "GW02", "GW06", "GW09", "GW10", "GW11", "GW13", "GW14"],
 "GW16": ["GW15"], "GW17": ["GW15"], "GW18": ["GW15"], "GW19": ["GW06", "GW08", "GW12", "GW15"],
 "GW20": ["GW14", "GW15", "GW16", "GW17", "GW18", "GW19"], "GW21": ["GW02", "GW20"], "GW22": ["GW21", "T21"],
 "GW23": ["GW22", "T20", "T21", "T22", "T23"], "GW24": ["GW23", "T24"],
 # T/UI nodes restated in GW terms per §10.12 (superseded T-refs replaced by their absorbing GW card)
 "T00": [], "T01": ["T00"], "T02": ["T00", "T01"], "T04": ["T01", "T02"],
 "T20": ["GW20"], "T21": ["GW19", "GW20", "T20"], "T22": ["GW01", "GW05", "GW07", "GW13", "T20", "UI12"],
 "T23": ["T04", "GW01", "T20", "T21", "T22"], "T24": ["T23", "GW23"],
 "UI11": [], "UI12": ["UI11", "GW20", "T20", "T21"], "UI07": ["GW05", "GW07", "GW13"],
}
# Tests whose producers sit DOWNSTREAM of their card cannot be satisfied by any edge (cycle): relocate them.
RELOCATE = {("GW03", 2459): "GW19", ("GW04", 2494): "GW15", ("GW04", 2496): "GW15", ("GW05", 2532): "GW20",
            ("GW05", 2536): "UI07", ("GW07", 2608): "GW15", ("GW07", 2611): "GW15", ("GW08", 2648): "GW15",
            ("GW08", 2653): "GW19", ("GW12", 2807): "GW15", ("GW12", 2808): "GW13", ("GW19", 3070): "GW20",
            ("GW09", 2687): "GW15", ("GW13", 2845): "GW20", ("GW11", 2764): "GW15"}
def clo(g, x, s=None):
    s = set() if s is None else s
    for d in g.get(x, ()):
        if d not in s: s.add(d); clo(g, d, s)
    return s
cyc = [x for x in CORR if x in clo(CORR, x)]
print("corrected graph cycles:", cyc or "none")
src = Path(EVD / "exit_artifacts.py").read_text()
NEEDS = eval(src[src.index("NEEDS = [") + 8: src.index("]\n# GW23 row")] + "]")
bad = []
for consumer, line, quote, producers in NEEDS:
    where = RELOCATE.get((consumer, line), consumer)
    need = set(producers) | ({consumer} if where != consumer else set())
    missing = [p for p in need if p not in clo(CORR, where) and p != where]
    status = "OK" if not missing else f"UNSATISFIED {missing}"
    if missing: bad.append((consumer, line, missing))
    tag = f"(relocated {consumer}->{where})" if where != consumer else ""
    print(f"  {consumer} L{line} needs {producers} -> {status} {tag}")
print("unsatisfied:", bad or "none")
print("lanes converge at GW15:", all(x in clo(CORR, "GW15") for x in ("GW02", "GW06", "GW07", "GW09", "GW10")))
print("GW16-18 rejoin at GW20 (not GW19):", all(x in clo(CORR, "GW20") for x in ("GW16", "GW17", "GW18")),
      "| at GW19:", all(x in clo(CORR, "GW19") for x in ("GW16", "GW17", "GW18")))
print("GW23 gated on T20/T21/T22/T23/UI12:", {x: x in clo(CORR, "GW23") for x in ("T20", "T21", "T22", "T23", "UI12")})
def longest(g, x, memo={}):
    if x not in memo: memo[x] = 1 + max((longest(g, d, memo) for d in g.get(x, ()) if d.startswith("GW")), default=0)
    return memo[x]
decl = {k: v for k, v in json.load(open(EVD / "declared_graph.json"))["cards"].items()}
print("longest GW chain to GW24: declared", longest(decl, "GW24", {}), "| corrected", longest(CORR, "GW24", {}))
json.dump(CORR, open(EVD / "corrected_graph.json", "w"), indent=1)
