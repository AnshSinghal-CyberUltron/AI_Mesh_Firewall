import json, hashlib
from collections import Counter, defaultdict
from gateway_v2.contracts.parity.c2 import generate_c2
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.replay import replay
from gateway_v2.contracts.parity.v1_oracle import v1_disposition, v1_tier1_categories

clock = FrozenClock(epoch=1_704_067_200)
recs = generate_c2(50_000, clock)
surf_prompts = defaultdict(set)
for r in recs: surf_prompts[r.surface].add(r.prompt[:32])
print("surface -> prompts it ever receives:")
for s, ps in sorted(surf_prompts.items()): print(f"  {s:12s}", sorted(ps))
outs = [replay(r, clock, v1_disposition) for r in recs]
print("replayed:", len(outs))
print("distinct dispositions:", Counter(o.disposition for o in outs))
print("distinct client_object per surface:", {s: sorted({o.client_object for o, r in zip(outs, recs) if r.surface == s}) for s in sorted(surf_prompts)})
shape = lambda o: hashlib.sha256(o.provider_bytes.replace(o.client_id.encode(), b"<id>")).hexdigest()[:12]
print("distinct provider_bytes (id-normalized):", len({shape(o) for o in outs}))
print("stream vs nonstream produce identical bytes for same prompt/surface:",
      all(shape(outs[i]) == shape(outs[j]) for i, j in [(0, 2)] ))
by_prompt = {}
for r in recs:
    by_prompt.setdefault(r.prompt, (v1_disposition(r.prompt), v1_tier1_categories(r.prompt)))
print("per-prompt v1-oracle disposition:")
for p, (d, cats) in by_prompt.items(): print(f"  {d:5s} {cats!s:40s} {p[:60]}")
