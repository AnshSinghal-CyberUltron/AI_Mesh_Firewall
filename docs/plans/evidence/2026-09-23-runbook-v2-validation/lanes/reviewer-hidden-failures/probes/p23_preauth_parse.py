"""P23: work done BEFORE authentication on /v1/chat/completions (benchmarked edge/chat.py: read_body -> parse_chat ->
gate.enter -> admission).  Largest body the edge accepts is RV_MAX_BODY_BYTES = 1 MiB."""
import sys, time
import orjson
sys.path.insert(0, sys.argv[1])
from rvproto.edge.wire import parse_chat
msgs = [{"role": "user", "content": [{"type": "text", "text": "x"} for _ in range(8)]} for _ in range(2600)]
body = orjson.dumps({"model": "m", "messages": msgs})
assert len(body) <= 1 << 20, len(body)
xs = []
for _ in range(20):
    t0 = time.perf_counter(); c = parse_chat(body); xs.append(time.perf_counter() - t0)
print(f"body={len(body):,} B, segments={len(c.segments):,}; parse_chat (pre-auth, on the event loop) median={1e3*sorted(xs)[10]:.1f} ms")
