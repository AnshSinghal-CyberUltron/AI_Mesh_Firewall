import json, math
from collections import defaultdict
from pathlib import Path
from gateway_v2.contracts.parity.c3 import score_v1
from gateway_v2.contracts.parity.v1_oracle import v1_disposition
root = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/tests/detection_corpus")
print("score_v1() as shipped (all splits pooled):")
for s in score_v1(root): print(f"  {s.label:9s} {s.family:18s} n={s.n:3d} flagged={s.flagged:3d} rate={s.rate}")
def wilson(k, n, z=1.96):
    if n == 0: return (0, 0)
    p = k / n; d = 1 + z*z/n; c = (p + z*z/(2*n)) / d; h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (round(max(0, c-h), 3), round(min(1, c+h), 3))
agg = defaultdict(lambda: [0, 0])
for fn in ("malicious.jsonl", "benign.jsonl"):
    for l in (root / fn).read_text().splitlines():
        r = json.loads(l); k = (r["label"], r["family"], r["split"])
        agg[k][0] += 1; agg[k][1] += v1_disposition(r["text"]) == "block"
print("\nEVAL split only (held-out), v1 block rate with Wilson 95% CI:")
for (lab, fam, sp), (n, k) in sorted(agg.items()):
    if sp == "eval": print(f"  {lab:9s} {fam:18s} n={n:3d} blocked={k:3d} CI95={wilson(k, n)}")
