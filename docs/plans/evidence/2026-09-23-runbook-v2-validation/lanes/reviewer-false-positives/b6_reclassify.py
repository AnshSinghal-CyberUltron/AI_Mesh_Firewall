"""Re-classify the design-consistency-prover's 60 exit-artifact need-pairs under the most charitable
reading of the runbook's sequencing prose:
  (1) L2323 'Everything else is a strict chain' = numeric GW order, except the named parallel groups;
  (2) L3366 'T00->T02 and T04 run first and serve both tracks' (T02/T04 precede every GW card);
  (3) §10.12 L3360 'T23 ... Runs on the v2 candidate before GW23'; L3366 'T20–T24 run on the v2 candidate';
  (4) UI tasks 'proceed in parallel throughout' (unordered vs GW).
A need is SATISFIED if the producer is ordered before the consumer under (1)-(3); FORWARD if the producer is
ordered after the consumer (test needs later work); UNORDERED otherwise."""
import re, sys
rows = [l for l in open(sys.argv[1]) if re.match(r"^GW\d\d\s+L\d+\s+needs", l)]
pairs = []
for l in rows:
    m = re.match(r"^(GW\d\d)\s+L(\d+)\s+needs (\S+)\s+-> (\S+)", l)
    pairs.append((m.group(1), int(m.group(2)), m.group(3), m.group(4)))
def pos(t):
    if t in ("T00", "T02", "T04"): return -1          # (2) first
    if t.startswith("GW"): return int(t[2:])
    if t in ("T20", "T21", "T22", "T23"): return 22.5  # (3) before GW23, after GW22's candidate exists
    if t == "T24": return 23.5                         # after GW23
    return None                                        # UI*: unordered
PAR = [{1, 2, 3, 4}, {9, 10}, {16, 17, 18}]
def cls(c, p):
    pc, pp = pos(c), pos(p)
    if pp is None: return "UNORDERED"
    if pp < pc:
        if any(pc in g and pp in g for g in PAR if isinstance(pp, int)): return "UNORDERED(parallel group)"
        return "SATISFIED"
    return "FORWARD"
from collections import Counter, defaultdict
out = defaultdict(list)
for c, line, p, k in pairs:
    out[(k, cls(c, p))].append(f"{c}->{p}@L{line}")
print("rows:", len(pairs), "unique pairs:", len({(c, p) for c, _, p, _ in pairs}))
print("prover MISSING unique:", len({(c, p) for c, _, p, k in pairs if k == "MISSING"}),
      "CYCLE unique:", len({(c, p) for c, _, p, k in pairs if k == "CYCLE"}))
for key in sorted(out):
    print(key, len(out[key]), out[key])
