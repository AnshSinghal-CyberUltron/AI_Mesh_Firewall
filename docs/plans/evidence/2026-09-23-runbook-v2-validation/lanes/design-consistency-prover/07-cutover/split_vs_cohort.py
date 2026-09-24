"""Does per-TENANT cohort routing avoid §10.7.4's objection ('the same tenant gets different security
dispositions depending on which gateway served the request')? Compare with per-request weights."""
import json, random
from pathlib import Path
from gateway_v2.contracts.parity.v1_oracle import v1_tier1_categories
def v1(t): return "block" if v1_tier1_categories(t) else "allow"
def v2(t):  # §10.2.2 row 2: command/injection patterns become signal on benign inline code
    cats = [c for c in v1_tier1_categories(t) if c != "command_injection"]
    return "block" if cats else "allow"
rows = [json.loads(l) for l in (Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/tests/detection_corpus") / "benign.jsonl").read_text().splitlines()]
prompts = [r["text"] for r in rows if r["family"] == "developer_traffic"]
print("developer_traffic prompts where v1 and v2 disagree:", sum(v1(p) != v2(p) for p in prompts), "of", len(prompts))
rng = random.Random(7); TENANTS, REQS = 200, 40
traffic = {t: [rng.choice(prompts) for _ in range(REQS)] for t in range(TENANTS)}
for w in (0.05, 0.25, 0.50):
    cohort = set(rng.sample(range(TENANTS), int(w * TENANTS)))
    mixed_req = mixed_ten = 0
    for t, reqs in traffic.items():
        seen_r, seen_c = {}, {}
        for p in reqs:
            gw_r = v2 if rng.random() < w else v1          # per-request weighted split
            gw_c = v2 if t in cohort else v1                 # per-tenant cohort
            seen_r.setdefault(p, set()).add(gw_r(p)); seen_c.setdefault(p, set()).add(gw_c(p))
        mixed_req += any(len(s) > 1 for s in seen_r.values())
        mixed_ten += any(len(s) > 1 for s in seen_c.values())
    print(f"w={w:.2f}: tenants that got BOTH block and allow for the IDENTICAL prompt -> per-request split {mixed_req}/{TENANTS}, per-tenant cohort {mixed_ten}/{TENANTS}")
