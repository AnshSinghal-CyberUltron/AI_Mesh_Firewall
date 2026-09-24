"""P04: pattern-aware holdback on realistic outputs (benchmarked egress/stream.py + org-a PlanInspector).
For each sample, tokens (o200k) arrive one per upstream chunk.  We record, per content char, the number of
upstream chunks it waited before being released (x ITL = added release lag), the per-chunk synchronous
processing time (event-loop block), and total CPU for the stream."""
import asyncio, json, sys, time
import orjson
sys.path.insert(0, sys.argv[1])
from rvproto.detect.matcher import Matcher
from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.edge.inspect import PlanInspector
from rvproto.egress.stream import StreamPipeline
from rvproto.plan.compiler import compile_plan
from rvproto.plan.fixtures import org_a
from rvproto.runtime.metrics import Registry
from gateway_v2.runtime.resources import load_contract

tokens = json.load(open(sys.argv[2]))
ins = PlanInspector(Matcher(), DeterministicDetectors(Matcher()), compile_plan(org_a()))
ITL_MS = 20

def frame(delta, finish=None):
    return b"data: " + orjson.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + b"\n\n"

async def run(toks):
    chunks = [frame({"role": "assistant", "content": ""})] + [frame({"content": t}) for t in toks] + [frame({}, "stop"), b"data: [DONE]\n\n"]
    released = []        # cumulative released chars after each upstream chunk
    per_chunk = []
    state = {"cum": 0}
    class R:
        class content:
            @staticmethod
            async def iter_any():
                for c in chunks:
                    t0 = time.perf_counter_ns()
                    yield c
                    # control returns here after the pipeline processed + emitted this chunk
                    per_chunk.append(time.perf_counter_ns() - t0)
                    released.append(state["cum"])
    async def send(b):
        for line in b.split(b"\n\n"):
            if line.startswith(b"data: {"):
                o = orjson.loads(line[6:])
                for ch in o.get("choices", []):
                    state["cum"] += len((ch.get("delta") or {}).get("content") or "")
    pipe = StreamPipeline(ins, Registry(0), ceiling=10**12, inject_hold=None)
    t0 = time.process_time()
    st = await pipe.run(R(), send)
    cpu = time.process_time() - t0
    # arrival chunk of each char: token j is chunk j+1 (chunk 0 = role)
    arrive = []
    for j, t in enumerate(toks):
        arrive += [j + 1] * len(t)
    rel_at = []
    k = 0
    for ci, cum in enumerate(released):
        while k < min(cum, len(arrive)):
            rel_at.append(ci); k += 1
    waits = [r - a for r, a in zip(rel_at, arrive)]
    return st, cpu, max(waits) if waits else 0, sum(1 for w in waits if w >= 1) / max(1, len(waits)), max(per_chunk), per_chunk

print(f"{'sample':<28} {'chunks':>6} {'max hold (chunks)':>17} {'= lag @ITL20':>12} {'chars held>=1 chunk':>19} {'max per-chunk block':>19} {'stream CPU':>10}")
for name, toks in tokens.items():
    st, cpu, mx, frac, blk, _ = asyncio.run(run(toks))
    print(f"{name:<28} {len(toks):>6} {mx:>17} {mx*ITL_MS:>10} ms {100*frac:>18.1f}% {blk/1e6:>16.2f} ms {cpu:>9.2f}s  err={st.error}")
c, _ = load_contract()
print(f"\nholdback ceiling per stream (contract.stream_buffer_bytes(active_streams)) on this host: 1 stream={c.stream_buffer_bytes(1):,} "
      f"| 1000 streams={c.stream_buffer_bytes(1000):,} (compared against len(pending) in CHARACTERS)")
