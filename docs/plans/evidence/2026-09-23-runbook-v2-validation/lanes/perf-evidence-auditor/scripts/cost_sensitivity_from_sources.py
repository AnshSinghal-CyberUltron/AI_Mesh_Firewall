"""Only arithmetic on figures stated in the source documents (no new prices)."""
total = 4561.44                     # cost matrix line 62
g2 = 2736.76                        # cost matrix line 37 (4 x g2-standard-24, 3-yr RESOURCE CUD)
flex = 3467.00                      # ASSEMBLY §5.6: Flexible CUD fallback '+26.7% ... to ~$3,467'
print(f"G2 via Flexible CUD (ASSEMBLY §5.6 fallback): total = {total:.2f} - {g2:.2f} + {flex:.2f} = {total-g2+flex:,.2f}  (>5,000? {total-g2+flex>5000})")
print(f"  check 2,736.76 x 1.267 = {g2*1.267:,.2f}")
print(f"Egress via assembler band table (cost_matrix_recompute.py §2): total = {4861.70:,.2f} (headroom {5000-4861.70:.2f})")
print(f"Both together: {total - g2 + flex - 1090.32 + 1390.58:,.2f}")
print()
print("Cost-matrix CPU line items vs MEASURED v1 components (not substitutable 1:1; shown for provenance):")
rows = [
 ("Tier-1 output scan, 8 coalesced chunks", 0.300, "[D]", 23.5, "output-guard accum, 300-token stream, 1E table (2026-09-09-1E...md:12)"),
 ("non-classifier stages", 0.160, "[M] target-design microbench", 3.5, "sum of measured v1 stage WALL p50s routing1.7+rl0.8+auth0.4+ks0.2+policy0.2+model_input0.2 (2026-09-09-e2e-baseline.md:25-33)"),
 ("whole request CPU (scan-only, no output)", None, "", 13.25, "/proc CPU, 4096-char, conc 1 (2026-09-09-cpu-per-request...md:10)"),
 ("whole request CPU (full 9-stage, 5-token answer)", 2.169, "[D] total", 28.0, "p99-under-20ms...md:38"),
]
for name, m, tag, v1, src in rows:
    ms = f"{m:.3f} ms {tag}" if m is not None else "-"
    print(f"  {name:48s} matrix: {ms:32s} measured v1: {v1:6.2f} ms  ({src})")
