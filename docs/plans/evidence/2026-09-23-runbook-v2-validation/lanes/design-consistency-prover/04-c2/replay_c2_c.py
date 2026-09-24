from collections import Counter
from gateway_v2.contracts.parity.c2 import generate_c2
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.v1_oracle import v1_disposition
recs = generate_c2(50_000, FrozenClock(epoch=1_704_067_200))
c = Counter((r.surface, r.mode, v1_disposition(r.prompt)) for r in recs)
print(f"{'surface':12s} {'mode':10s} {'allow':>6s} {'block':>6s}")
for s in ("chat", "completions", "responses", "embeddings", "mcp", "rag", "vector"):
    for m in ("stream", "nonstream"):
        a, b = c.get((s, m, "allow"), 0), c.get((s, m, "block"), 0)
        if a or b: print(f"{s:12s} {m:10s} {a:6d} {b:6d}")
