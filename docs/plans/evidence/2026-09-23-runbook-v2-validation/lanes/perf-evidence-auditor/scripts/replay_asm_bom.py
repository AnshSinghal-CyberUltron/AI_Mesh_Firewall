REQ=30*86400; GIB=1024**3
def eg(g):
    f=min(g,200); g-=f; c=0.0
    b=min(g,10240-200); c+=b*0.085; g-=b
    b=min(g,153600-10240); c+=b*0.065; g-=b
    return c+max(g,0)*0.045
def net(rps, eb=1634, lbb=4700):
    ge=rps*REQ*eb/GIB; gl=rps*REQ*lbb/GIB
    return eg(ge)+gl*0.010, ge
FLOOR=540.36
COMP={"4 x g2-standard-24 (8 L4, 96 vCPU, 4 nodes/3 zones)":2736.76382712*1.0,
      "8 x g2-standard-4  (8 L4, 32 vCPU, 8 nodes/3 zones)":8*241.70349567,
      "8 x g2-standard-8  (8 L4, 64 vCPU, 8 nodes/3 zones)":8*291.89948703,
      "2 x g2-standard-48 (8 L4, 96 vCPU, 2 nodes/2 zones)":2*1368.38191356}
print("BOM check (compute values [V] Mumbai 3-yr resource CUD):")
for k,v in COMP.items(): print(f"  {k:52s} ${v:9.2f}/mo")
print()
def maxrps(comp, eb=1634, lbb=4700, lo=1, hi=60000):
    for _ in range(60):
        mid=(lo+hi)/2
        n,_=net(mid,eb,lbb)
        if comp+FLOOR+n<=5000: lo=mid
        else: hi=mid
    return lo
print("Max RPS the *budget* allows for each compute layout (egress-bound), in-region provider:")
for k,v in COMP.items():
    print(f"  {k:52s} -> {maxrps(v):8.0f} RPS  (egress+LB budget ${5000-v-FLOOR:8.2f})")
print("\nSame, BYOK provider OUT of region (+3,294 B/req):")
for k,v in COMP.items():
    print(f"  {k:52s} -> {maxrps(v,4928):8.0f} RPS")
print("\nCPU-only (no GPU) reference layouts:")
for k,v in {"6 x c4-standard-16 (96 x86 vCPU, AMX)":6*270.1294336,
            "3 x c4-standard-16 (48 vCPU)":3*270.1294336}.items():
    print(f"  {k:52s} ${v:8.2f}  -> {maxrps(v):8.0f} RPS budget ceiling")
print()
print("Cost at the recommended point (4 x g2-standard-24, 100% coverage, 1,064 RPS):")
n,g=net(1064)
print(f"  compute $2736.76 + floor ${FLOOR:.2f} + egress+LB ${n:.2f} ({g:.0f} GiB egress) = ${2736.76+FLOOR+n:.2f}  headroom ${5000-2736.76-FLOOR-n:.2f}")
print("Cost at 30% semantic coverage on the SAME 8 L4 (3,547 RPS of gateway traffic):")
n,g=net(3547); print(f"  compute $2736.76 + floor ${FLOOR:.2f} + egress+LB ${n:.2f} = ${2736.76+FLOOR+n:.2f}")
print("Egress-bound ceiling with that GPU tier bought:", f"{maxrps(2736.76):.0f} RPS (=> min coverage {1064/maxrps(2736.76)*100:.0f}%)")
