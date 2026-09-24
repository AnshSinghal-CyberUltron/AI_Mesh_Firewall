"""Generate C2 with the real generator and measure its actual diversity."""
import hashlib, json, sys
from collections import Counter
from gateway_v2.contracts.parity.c2 import generate_c2
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.types import C2_TARGET, C2Record
import dataclasses

recs = generate_c2(C2_TARGET, FrozenClock(epoch=1_704_067_200))
fields = [f.name for f in dataclasses.fields(C2Record)]
def norm(r):  # everything that could vary per request EXCEPT the synthetic id and the frozen capture time
    return json.dumps({"surface": r.surface, "mode": r.mode, "tenant": r.tenant, "plan": r.plan_version,
                       "headers": r.headers, "prompt": r.prompt}, sort_keys=True)
h = Counter(hashlib.sha256(norm(r).encode()).hexdigest() for r in recs)
print("C2Record fields:", fields)
print("records:", len(recs))
print("distinct request_id:", len({r.request_id for r in recs}), "| distinct captured_at:", len({r.captured_at for r in recs}))
print("distinct prompts (bodies):", len({r.prompt for r in recs}))
print("distinct header tuples:", len({r.headers for r in recs}))
print("distinct normalized requests (sha256 of surface,mode,tenant,plan,headers,prompt):", len(h))
print("copies per distinct request: min", min(h.values()), "max", max(h.values()))
print("distinct (surface,prompt):", len({(r.surface, r.prompt) for r in recs}), "of", 7 * 8, "possible")
print("per-surface distinct prompts:", {s: len({r.prompt for r in recs if r.surface == s}) for s in sorted({r.surface for r in recs})})
print("per surface|mode counts:", dict(sorted(Counter(f"{r.surface}|{r.mode}" for r in recs).items())))
print("prompt frequency:", {p[:40]: c for p, c in Counter(r.prompt for r in recs).most_common()})
print("period of the whole corpus (first i where record i duplicates record 0 except id):",
      next(i for i in range(1, len(recs)) if norm(recs[i]) == norm(recs[0])))
