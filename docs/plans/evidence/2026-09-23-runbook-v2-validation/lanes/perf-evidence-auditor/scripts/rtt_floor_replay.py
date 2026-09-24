"""Replay of the wf_1ccbbae1 B1 agent's RTT 'physics floor' (src_rtt_floor_cmd.txt), unchanged formula."""
import math
def gc(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2); dl = math.radians(lo2 - lo1)
    return 6371.0 * math.acos(min(1, math.sin(p1)*math.sin(p2) + math.cos(p1)*math.cos(p2)*math.cos(dl)))
mumbai = (19.0760, 72.8777)
for name, pt in (("Delhi asia-south2 (CROSS-REGION)", (28.6139, 77.2090)),
                 ("Singapore asia-southeast1", (1.3521, 103.8198))):
    d = gc(mumbai, pt); fib = d * 1.4; ow = fib * 1000 / 2.0e8 * 1000
    print(f"{name:34s} great-circle={d:6.0f} km fibre(x1.4)={fib:6.0f} km one-way={ow:5.2f} ms RTT floor={2*ow:5.2f} ms")
print("Note: the formula takes a distance between two REGIONS; it has no term for an off-box GPU in the SAME region/zone.")
