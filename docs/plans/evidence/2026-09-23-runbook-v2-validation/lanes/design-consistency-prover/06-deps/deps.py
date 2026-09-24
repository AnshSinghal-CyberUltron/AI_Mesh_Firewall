"""Parse rb.md task dependencies; compute closures; check §10.13/§10.12 claims and exit-artifact edges."""
import re, sys, json
from pathlib import Path
RB = Path(sys.argv[1]).read_text().splitlines()
L = lambda n: RB[n - 1]
def expand(tok):
    tok = tok.strip().rstrip(".")
    m = re.fullmatch(r"([A-Z]+)(\d+)\s*[–-]\s*([A-Z]+)(\d+)", tok)
    if m:
        p, a, _, b = m.groups(); w = len(m.group(2))
        return [f"{p}{i:0{w}d}" for i in range(int(a), int(b) + 1)]
    return [tok] if re.fullmatch(r"[A-Z]+\d+", tok) else []
def parse_list(s):
    out = []
    for t in re.split(r",\s*", s):
        out += expand(t)
    return out
# ---------- declared graph ----------
index, cards = {}, {}
for n in range(2296, 2321):
    m = re.match(r"\| (GW\d\d) \| .* \| (\w+) \| (.*) \|$", L(n))
    if m: index[m.group(1)] = parse_list(m.group(3)) if m.group(3) != "—" else []
cur = None
for n, line in enumerate(RB, 1):
    h = re.match(r"^## (GW\d\d) —", line) or re.match(r"^(T\d\d)  ", line) or re.match(r"^## (UI\d\d) - ", line)
    if h: cur = h.group(1); continue
    if cur and cur not in cards:
        m = re.match(r"^\| Depends on \| (.*) \|$", line) or re.match(r"^Dependencies: ([^.]*)\.", line)
        if m: cards[cur] = (parse_list(m.group(1)) if m.group(1) not in ("None",) else [], n)
print("index vs card mismatches:", [(k, index[k], cards[k][0]) for k in index if sorted(index[k]) != sorted(cards[k][0])] or "none")
G = {k: set(v) for k, (v, _) in cards.items()}
def closure(g, x, seen=None):
    seen = set() if seen is None else seen
    for d in g.get(x, ()):
        if d not in seen: seen.add(d); closure(g, d, seen)
    return seen
def cycles(g):
    return sorted({x for x in g if x in closure(g, x)})
print("declared-graph cycles:", cycles(G) or "none")
GW = [f"GW{i:02d}" for i in range(25)]
print("\nclosure(GW15) =", sorted(closure(G, "GW15")))
print("(a) GW15 missing from closure:", [x for x in ("GW02", "GW06", "GW08", "GW09", "GW10") if x not in closure(G, "GW15")])
lanes = {"lane1 GW01->GW02": "GW02", "lane2 GW03->GW04->GW06": "GW06", "lane3 GW05->GW07": "GW07", "lane4 GW08->GW09/GW10": ("GW09", "GW10")}
for name, tail in lanes.items():
    tails = tail if isinstance(tail, tuple) else (tail,)
    print(f"(b) {name}: converges at GW15? {all(t in closure(G, 'GW15') for t in tails)}")
print("(b) 'GW16,GW17,GW18 ... rejoin at GW19' encoded?", all(x in closure(G, "GW19") for x in ("GW16", "GW17", "GW18")),
      "| closure(GW19) =", sorted(closure(G, "GW19")))
print("    L2323 'GW01, GW02 and GW03 are independent of each other':",
      "GW02 depends on GW01" if "GW01" in G["GW02"] else "ok")
indep = [(a, b) for i, a in enumerate(GW) for b in GW[i+1:] if a not in closure(G, b) and b not in closure(G, a)]
named = {("GW01","GW02"),("GW01","GW03"),("GW02","GW03"),("GW01","GW04"),("GW02","GW04"),("GW03","GW04"),("GW09","GW10"),("GW16","GW17"),("GW16","GW18"),("GW17","GW18")}
print(f"    L2323 'Everything else is a strict chain': {len(indep)} independent GW pairs exist; not named in L2323: {len([p for p in indep if p not in named])}")
print("      e.g.", [p for p in indep if p not in named][:14])
# ---------- §10.12 mapping applied ----------
SUPER = {"T03": ["GW14"], "T05": ["GW05"], "T06": ["GW07"], "T07": ["GW09"], "T09": ["GW08", "GW10"], "T10": ["GW13"],
         "T11": ["GW12"], "T12": ["GW01"], "T13": ["GW11", "GW12"], "T14": ["GW06"], "T15": ["GW14"], "T17": ["GW19"],
         "T18": ["GW20"], "T19": ["GW20"], "T25": ["GW24"], "T26": ["GW24"], "T00": ["GW00"]}
for t, gws in SUPER.items():
    key = t + " "
    row = next(l for l in RB[3335:3364] if l.startswith("| " + key))
    assert all(g in row for g in gws), (t, row)
print("\n(c) closure(GW23) =", sorted(closure(G, "GW23")))
print("    GW23 depends (transitively) on T20/T21/T22/T23/UI12?", {x: x in closure(G, "GW23") for x in ("T20", "T21", "T22", "T23", "UI12")})
assert "Runs on the v2 candidate before GW23" in L(3360)
print("(d) UI12 declared deps:", cards["UI12"][0], "at L%d;" % cards["UI12"][1],
      "superseded per §10.12:", {t: SUPER[t] for t in cards["UI12"][0] if t in SUPER})
for ui in ("UI06", "UI07"):
    print(f"    {ui} declared deps {cards[ui][0]} (L{cards[ui][1]}) vs L3366 'UI06 and UI07 depend on [GW05, GW14]':",
          "GW05" in cards[ui][0] or "GW14" in cards[ui][0])
tdeps = {t: cards[t][0] for t in ("T20", "T21", "T22", "T23", "T24")}
print("    T20-T24 declared deps:", tdeps, "-> superseded refs:", {t: [d for d in v if d in SUPER] for t, v in tdeps.items()})
json.dump({"cards": {k: v[0] for k, v in cards.items()}, "index": index}, open(Path(sys.argv[2]) / "declared_graph.json", "w"), indent=1)
