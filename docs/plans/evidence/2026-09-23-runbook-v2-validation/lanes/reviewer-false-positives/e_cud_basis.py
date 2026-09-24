"""Re-price the cost matrix on the basis the SOURCE declares ('3-year CUD', cost-matrix L4), keeping the
pricing-verifier's corrected Mumbai rates for every non-commit line. Inputs = pricing-verifier's own
cost_matrix_recompute.json (catalog-derived), no new prices introduced."""
import json, sys
d = json.load(open(sys.argv[1]))
L = {x["line"]: x for x in d["lines"]}
od = sum(x["on_demand_mumbai_usd"] for x in d["lines"])
doc = sum(x["doc_usd"] for x in d["lines"])
print(f"doc total {doc:.2f} | on-demand Mumbai total {od:.2f} (ledger says 8952.24)")
# Lines whose doc value embeds a commitment: 1 (exact 3-yr CUD SKUs), 7 (0.48 x compute), 9 (0.60 x rate).
cud3 = od - (L[1]["on_demand_mumbai_usd"] - L[1]["doc_usd"]) - (L[7]["on_demand_mumbai_usd"] - L[7]["doc_usd"]) \
          - (L[9]["on_demand_mumbai_usd"] - L[9]["doc_usd"])
print(f"3-yr-CUD basis (lines 1/7/9 at doc's own commit prices) + Mumbai-corrected lines 3/4/5/15/16: {cud3:.2f}"
      f"  -> vs $5,000 cap: {cud3-5000:+.2f}")
g2_1yr = 3831.47  # pricing-verifier line-1 note: '(1-yr CUD would be $3831.47.)'
one = od - (L[1]["on_demand_mumbai_usd"] - g2_1yr)
print(f"G2 1-yr CUD only, rest on-demand: {one:.2f}")
# Which corrections drive the residual over-cap on the CUD basis?
for n in (3, 4, 5, 15, 16):
    print(f"  line {n:2d} {L[n]['service'][:45]:45s} doc {L[n]['doc_usd']:9.2f} -> Mumbai {L[n]['on_demand_mumbai_usd']:9.2f} "
          f"({L[n]['on_demand_mumbai_usd']-L[n]['doc_usd']:+.2f})")
