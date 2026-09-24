import random, math, statistics
random.seed(7)

# ---------- 1. M/D/1 sojourn-time simulation for the GPU stage ----------
def mds1(S_ms, rho, n=400000, warm=20000):
    lam = rho / S_ms           # arrivals per ms
    t = 0.0; free = 0.0; soj = []
    for i in range(n):
        t += random.expovariate(lam)
        start = max(t, free)
        free = start + S_ms
        if i >= warm: soj.append(free - t)
    soj.sort()
    return soj[len(soj)//2], soj[int(len(soj)*0.95)], soj[int(len(soj)*0.99)], soj[int(len(soj)*0.999)]

print("== M/D/1 GPU stage sojourn (service = 4.50 ms deterministic, Poisson arrivals) ==")
for rho in (0.2,0.3,0.4,0.5,0.6,0.7,0.8):
    p50,p95,p99,p999 = mds1(4.50, rho)
    print(f"  rho={rho:.1f}  RPS/L4={rho*1000/4.50/2:6.1f} (2 win/req)  p50={p50:6.2f}  p95={p95:6.2f}  p99={p99:6.2f}  p99.9={p999:6.2f} ms")

# ---------- 2. Cost model ----------
REQ_PER_RPS_MONTH = 30*86400          # 2,592,000 requests per RPS per 30-day month
GIB = 1024**3
def egress_cost(gib):                  # GCP Standard Tier, region-independent [V]
    free = min(gib, 200); gib -= free; c = 0.0
    b1 = min(gib, 10240-200); c += b1*0.085; gib -= b1
    b2 = min(gib, 153600-10240); c += b2*0.065; gib -= b2
    c += max(gib,0)*0.045
    return c
def lb_cost(gib):  return gib*0.010    # each way, conservative [V]

BYTES_EGRESS_INREGION = 1634           # 0.7*1770 (x64+gzip, no trace) + 0.3*1316 [D from M]
BYTES_EGRESS_BYOK_US  = 1634+3294      # + prompt leg to an out-of-region provider [D from M]
BYTES_LB              = 4700           # req in + resp out through the LB [D from M]

FLOOR_HA = 540.36    # Cloud SQL HA 4vCPU/16GiB/100GiB + Memorystore HA 2x13GB + GKE + 2 IPs + LB rule, 3-yr CUD, NO Cloud NAT [D from V]
FLOOR_LEAN = 421.0   # B4's lean floor (zonal SQL, Valkey HA, no GKE) [D from V]

def net_cost(rps, per_req_egress=BYTES_EGRESS_INREGION):
    g_e = rps*REQ_PER_RPS_MONTH*per_req_egress/GIB
    g_l = rps*REQ_PER_RPS_MONTH*BYTES_LB/GIB
    return egress_cost(g_e)+lb_cost(g_l), g_e

G2 = {"g2-standard-4":241.70349567, "g2-standard-8":291.89948703, "g2-standard-16":392.29146975}

print("\n== Posture A: 100% semantic coverage, 2x512-token windows/request, L4 batch-2 ==")
print("   L4 batch-2 service 4.50 ms -> 444 seq/s -> 222 req/s/L4 at 100%; utilisation cap 60% -> 133 RPS/L4")
for shape in ("g2-standard-4","g2-standard-8","g2-standard-16"):
    for n in (8,):
        rps = n*133
        comp = n*G2[shape]
        net,g = net_cost(rps)
        tot = comp+net+FLOOR_HA
        print(f"  {n} x {shape:16s} RPS={rps:5d} compute=${comp:8.2f} egress+LB=${net:7.2f} ({g:7.0f} GiB) floor=${FLOOR_HA:.2f} TOTAL=${tot:8.2f} headroom=${5000-tot:8.2f}")

print("\n== Posture A': L4 quota lifted -- how many g2-standard-4 fit $5,000? ==")
for n in range(10,20):
    rps=n*133; comp=n*G2["g2-standard-4"]; net,_=net_cost(rps); tot=comp+net+FLOOR_HA
    flag = "  <= FITS" if tot<=5000 else ""
    print(f"  n={n:2d} RPS={rps:5d} compute=${comp:8.2f} egress+LB=${net:7.2f} TOTAL=${tot:8.2f}{flag}")

print("\n== Same, but BYOK provider OUT of region (prompt leg billable) ==")
for n in (8,12,14):
    rps=n*133; comp=n*G2["g2-standard-4"]; net,_=net_cost(rps,BYTES_EGRESS_BYOK_US); tot=comp+net+FLOOR_HA
    print(f"  n={n:2d} RPS={rps:5d} compute=${comp:8.2f} egress+LB=${net:7.2f} TOTAL=${tot:8.2f} {'FITS' if tot<=5000 else 'OVER'}")

print("\n== Marginal $/RPS-month ==")
for rps in (500,1064,1862,4000,7812):
    n1,_=net_cost(rps); n2,_=net_cost(rps+1)
    print(f"  at {rps:5d} RPS: marginal egress+LB = ${ (n2-n1):.4f}/RPS-mo ; node share (g2-std-4/133) = ${G2['g2-standard-4']/133:.4f}")

# ---------- 3. CPU bottom-up ----------
cpu = {"framework+http+orjson parse [I]":0.15, "1 pure-ASGI middleware [D from M]":0.02,
       "non-classifier stages [M]":0.16, "Tier-1 input native+dict filter [D from M]":0.28,
       "tokenize 4096 ch [M this session]":0.8638, "Tier-1 output scan, 8 coalesced chunks [D]":0.30,
       "gzip-6 flush per answer [M]":0.155, "SSE frame build orjson [M]":0.24}
tot=sum(cpu.values())
print(f"\n== CPU per request, rewritten hot path, 4096-char prompt / 400-token answer ==")
for k,v in cpu.items(): print(f"   {v:6.3f} ms  {k}")
print(f"   {tot:6.3f} ms TOTAL -> {1000/tot:.0f} RPS/vCPU at 100% CPU, {0.6*1000/tot:.0f} at 60% cap  [D]")
print(f"   GPU-bound check: 8 x g2-standard-8 = 64 vCPU x {0.6*1000/tot:.0f} = {64*0.6*1000/tot:.0f} RPS of CPU capacity vs 1064 RPS of GPU capacity")
print(f"   => CPU would have to be {64*0.6*1000/tot/1064:.1f}x worse than this estimate before it binds instead of the GPU")

# ---------- 4. in-flight memory ----------
for T in (1,5,10,20):
    inflight = 133*T
    print(f"   T_hold={T:2d}s -> {inflight:5d} in-flight streams/node x 2 MB cap [M] = {inflight*2/1024:.2f} GB (g2-standard-8 has 32 GB)")
