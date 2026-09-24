from collections import defaultdict
from gateway_v2.contracts.parity.c2 import generate_c2
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.replay import replay
from gateway_v2.contracts.parity.v1_oracle import v1_disposition
clock = FrozenClock(epoch=1_704_067_200)
recs = generate_c2(50_000, clock)
cell_prompts = defaultdict(set)
for r in recs: cell_prompts[(r.surface, r.mode, r.tenant)].add(r.prompt)
print("distinct prompts per (surface,mode,tenant) cell:", sorted({len(v) for v in cell_prompts.values()}), "cells:", len(cell_prompts))
norm = lambda o: (o.disposition, o.transformations, o.provider_bytes.replace(o.client_id.encode(), b"<id>"), o.client_object, o.client_model, o.created)
outs = {}
for r in recs:
    outs.setdefault((r.surface, r.mode, r.prompt), norm(replay(r, clock, v1_disposition)))
print("distinct normalized replay outcomes over all 50k:", len({v for v in outs.values()}))
pairs = [(s, p) for (s, m, p) in outs if m == "stream" and (s, "nonstream", p) in outs]
same = sum(outs[(s, "stream", p)] == outs[(s, "nonstream", p)] for s, p in pairs)
print(f"stream vs nonstream, same surface+prompt: {same}/{len(pairs)} byte-identical outcomes (mode is ignored by replay)")
emb = {outs[k][3] for k in outs if k[0] == "embeddings"}
print("embeddings replay client_object:", emb, "(OpenAI embeddings object would be 'list')")
blocks = defaultdict(int)
for r in recs: blocks[r.surface] += v1_disposition(r.prompt) == "block"
print("v1-oracle BLOCK count per surface:", dict(blocks))
