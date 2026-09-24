"""Recompute docs/plans/2026-09-02-hot-path-cost-matrix.md from its OWN line items and stated formulas.

No new prices, no estimates: every input below is copied from the cost matrix (line cited),
and every output is compared against the figure the matrix prints.
"""
import math

REQ_PER_RPS_MONTH = 86_400 * 30          # matrix line 5: 1,064 x 86,400 x 30
GIB = 1024 ** 3

def check(label, ours, theirs, tol=0.01):
    ok = abs(ours - theirs) <= tol * max(1.0, abs(theirs))
    print(f"  {'OK ' if ok else 'MISMATCH'} {label:62s} recomputed={ours:12,.2f}  matrix={theirs:12,.2f}")
    return ok

print("== 1. Line-item total (matrix §2, lines 34-62) ==")
lines = {  # $/mo exactly as printed
    1: 2736.76, 2: 0, 3: 68.00, 4: 18.25, 5: 1090.32, 6: 15.00, 7: 273.91, 8: 15.00, 9: 175.20,
    10: 0, 11: 0, 12: 0, 13: 0, 14: 73.00, 15: 10.00, 16: 53.00, 17: 15.00, 18: 18.00,
    19: 0, 20: 0, 21: 0,
}
total = sum(lines.values())
check("sum of 21 line items vs TOTAL (line 62)", total, 4561.44, tol=1e-6)
check("headroom 5000 - total (line 18)", 5000 - total, 438.56, tol=1e-6)
check("$/million requests (line 19)", total / (1064 * REQ_PER_RPS_MONTH / 1e6), 1.65, tol=0.01)
check("requests/month (line 5)", 1064 * REQ_PER_RPS_MONTH, 2_757_888_000, tol=0)

print("\n== 2. Egress (matrix §4.1, line 102) ==")
bytes_per_req = 2950 + 3294             # line 99: coalesce x16 + gzip, no trace, + prompt leg
gib = bytes_per_req * 1064 * REQ_PER_RPS_MONTH / GIB
check("GiB/month at 6,244 B/req", gib, 16037, tol=0.001)

def matrix_bands(g):
    """Bands AS WRITTEN in the matrix: 200 free · 824 @0.085 · 9,216 @0.065 · rest @0.045"""
    c = 0.0
    free = min(g, 200); g -= free
    b = min(g, 824); c += b * 0.085; g -= b
    b = min(g, 9216); c += b * 0.065; g -= b
    return c + max(g, 0) * 0.045

def assembler_bands(g):
    """Bands used by the 2026-08-29 assembler (asm_model.py/asm_bom.py eg()): 200 free,
    0.085 up to 10,240 GiB, 0.065 up to 153,600 GiB, then 0.045 — a DIFFERENT tier table."""
    c = 0.0
    free = min(g, 200); g -= free
    b = min(g, 10240 - 200); c += b * 0.085; g -= b
    b = min(g, 153600 - 10240); c += b * 0.065; g -= b
    return c + max(g, 0) * 0.045

eg = matrix_bands(16037)
check("banded egress (matrix bands)", eg, 929.95, tol=0.001)
check("LB outbound 16,037 x $0.010", 16037 * 0.010, 160.37, tol=0.001)
check("egress + LB (line 41)", eg + 16037 * 0.010, 1090.32, tol=0.001)
eg_asm = assembler_bands(16037)
print(f"  NOTE same 16,037 GiB through the ASSEMBLER's band table = ${eg_asm:,.2f} + LB ${160.37:,.2f} "
      f"= ${eg_asm + 160.37:,.2f} (matrix used ${1090.32:,.2f}); total would be ${total - 1090.32 + eg_asm + 160.37:,.2f}")

print("\n== 3. Audit / logging variants (lines 16, 22, 55) ==")
audit_gib = 1064 * REQ_PER_RPS_MONTH * 1024 / GIB
check("GCS audit @1 KiB/req, $0.02/GiB (line 16 = $53)", audit_gib * 0.02, 53.00, tol=0.01)
check("Cloud Logging @1 KiB, $0.50/GiB, 50 GiB free (=$1,290)", (audit_gib - 50) * 0.50, 1290, tol=0.01)
check("total with Cloud Logging instead of GCS (line 22)", total - 53 + 1290, 5798.44, tol=1e-6)
check("control plane on own nodes +$270 (line 21)", total + 270, 4831.44, tol=1e-6)

print("\n== 4. CPU ladder (matrix §5, lines 120-145) ==")
cpu_items = [0.150, 0.020, 0.160, 0.280, 0.864, 0.300, 0.155, 0.240]
cpu = sum(cpu_items)
check("sum of CPU items (line 132 = 2.169 ms)", cpu, 2.169, tol=0.001)
check("theoretical RPS/vCPU = 1000/2.169 (line 138 = 461)", 1000 / cpu, 461, tol=0.002)
check("practical = 0.6 x theoretical (line 139 = 277)", 0.6 * 1000 / cpu, 277, tol=0.002)
check("96 vCPU x 277 (line 26 = 26,592)", 96 * 277, 26592, tol=0)
check("CPU/GPU headroom 26,592 / 1,064 ('~25x')", 26592 / 1064, 25, tol=0.01)
check("used in posture A 1,064/96 (line 142 = 11.1)", 1064 / 96, 11.1, tol=0.01)
check("conservative floor binds: 96 x 10.25 x 0.6 (= ~590)", 96 * 10.25 * 0.6, 590, tol=0.01)
check("'50x' worse: 277 / 5.4", 277 / 5.4, 50, tol=0.05)
check("'1,600x' worse: 277 / 0.17", 277 / 0.17, 1600, tol=0.05)
print("  --- substitute MEASURED v1 CPU costs (docs/perf/evidence 2026-09-09) into the matrix's OWN formula 96 x RPS/vCPU x 0.6:")
for label, ms in (("scan-only 13.25 ms CPU/req (cpu-per-request...md:10)", 13.25),
                  ("scan-only 11.11 ms CPU/req (p99-under-20ms...md:37)", 11.11),
                  ("full 9-stage ~28 ms CPU/req (p99-under-20ms...md:38)", 28.0),
                  ("full path 27.3 ms CPU/req (post-fix-profile.md:17)", 27.3)):
    rps_vcpu = 1000 / ms
    print(f"     {label:55s} -> {rps_vcpu:6.1f} RPS/vCPU -> 96 vCPU x 0.6 = {96*rps_vcpu*0.6:7.0f} RPS "
          f"(matrix: 26,592; ratio {26592/(96*rps_vcpu*0.6):5.1f}x)")

print("\n== 5. Per-L4 throughput (matrix line 143) ==")
print(f"  stated derivation '490 windows/s / 2 windows/request, 60% cap' = {490/2*0.6:.1f} RPS/L4 -> x8 = {490/2*0.6*8:.0f} RPS")
print(f"  assembler's actual derivation (asm_model.py): 0.6 x 1000/4.50 ms = {0.6*1000/4.50:.1f} RPS/L4 -> x8 = {8*133} (133 rounded)")
check("1,064 / 8 = 133 (line 143)", 1064 / 8, 133, tol=0)
check("'490 windows/s ÷ 2 × 0.6' reproduces 133?", 490 / 2 * 0.6, 133, tol=0.01)

print("\n== 6. §8 cost model: Cost(RPS) = 613.36 + ceil(RPS/266)*701.19 + banded egress(RPS*15.07) + LB + GCS ==")
fixed_items = [18.25, 15.00, 273.91, 15.00, 175.20, 73.00, 10.00, 15.00, 18.00]  # lines 4,6,7,8,9,14,15,17,18
check("fixed = ALB+IPs+SQL+backups+Valkey+GKE+AR+Monitoring+snapshots", sum(fixed_items), 613.36, tol=1e-6)
check("per-node 701.19 = (2736.76 + 68)/4", (2736.76 + 68) / 4, 701.19, tol=1e-4)
gib_per_rps = 16037 / 1064
check("15.07 GiB per RPS-month", gib_per_rps, 15.07, tol=0.001)

def cost(rps, nodes=None, bands=matrix_bands):
    n = math.ceil(rps / 266) if nodes is None else nodes
    g = rps * gib_per_rps
    audit = rps * REQ_PER_RPS_MONTH * 1024 / GIB * 0.02
    return 613.36 + n * 701.19 + bands(g) + g * 0.010 + audit, n

for rps, printed in ((1064, 4561), (1330, 5401), (2000, 8188), (5320, 19519), (10000, 36251)):
    c, n = cost(rps)
    check(f"§8 table row {rps:,} RPS ({n} nodes)", c, printed, tol=0.005)
c, n = cost(1862, nodes=7)
check("A' 14 L4 / 7 nodes / 1,862 RPS (line 24 = $7,228.70)", c, 7228.70, tol=0.005)

print("  §8.1 budget -> max RPS (100% scanned): solve max RPS with cost <= budget")
def max_rps(budget, coverage=1.0, bands=matrix_bands):
    best = 0
    for rps in range(1, 40000):
        n = math.ceil(coverage * rps / 266)
        c, _ = cost(rps, nodes=n, bands=bands)
        if c <= budget: best = rps
    return best
for budget, printed in ((5000, 1064), (7500, 1862), (10000, 2466), (25000, 6770), (50000, 13832), (100000, 28196)):
    ours = max_rps(budget)
    check(f"§8.1 ${budget:,} -> max RPS", ours, printed, tol=0.01)
print("  §8.2 risk-gated coverage on $5,000")
for cov, printed in ((1.0, 1064), (0.5, 1596), (0.26, 2361), (0.10, 3160)):
    ours = max_rps(5000, coverage=cov)
    check(f"§8.2 coverage {cov:.0%} -> max RPS", ours, printed, tol=0.01)
print("  §5.4 ladder rows under the matrix's OWN egress model (prompt leg included):")
print(f"     posture B '≥26% -> 4,151 RPS' vs §8.2's own 26% answer: {max_rps(5000, coverage=0.26):,} RPS")
print(f"     posture C 'deterministic only 7,200-9,400' vs zero-GPU-node max under matrix egress: {max_rps(5000, coverage=0.0):,} RPS (with $0 compute)")

print("\n== 7. 'Upside: tenant provider in-region ~$3,970' (line 20) ==")
for label, b in (("response-only coalesce x16 + gzip (2,950 B)", 2950), ("response-only x64 (1,770 B)", 1770),
                 ("assembler blend eb=1634 B", 1634)):
    g = b * 1064 * REQ_PER_RPS_MONTH / GIB
    alt = total - 1090.32 + matrix_bands(g) + g * 0.010
    print(f"     {label:45s}: {g:8.0f} GiB -> total ${alt:,.2f}  (matrix says ~$3,970)")
