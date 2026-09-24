"""Execute BASELINE (2a657fad) SecureStreamingResponse and count output-guard passes for 300
token-sized deltas, at the shipped STREAM_FLUSH_BYTES and at the other Pareto rows quoted in
secure_streaming.py:50-56. Verifies rb.md:2095/2243 '29 guard passes for 300 token deltas'."""
import asyncio, json, sys
import ai_mesh_gateway.secure_streaming as ss

def _sse(c):
    return "data: " + json.dumps({"id": "x", "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}]}) + "\n\n"

async def _inner(pieces):
    for p in pieces:
        yield _sse(p)
    yield "data: [DONE]\n\n"

class _Allow:
    action = "allow"; threat_type = "none"; matched_patterns = []; detail = ""
    compliance_tags = []; matched_values = {}; scan_degraded = False

class CountingGuard:
    def __init__(self): self.calls = 0
    async def inspect(self, text, **_kw):
        self.calls += 1
        return _Allow()

async def run(tokens):
    g = CountingGuard()
    s = ss.SecureStreamingResponse(_inner(tokens), None, redaction_enabled=True, output_guard=g)
    async for _ in s.__aiter__():
        pass
    return g.calls

shipped = ss.STREAM_FLUSH_BYTES
print("module:", ss.__file__)
print("shipped STREAM_FLUSH_BYTES =", shipped)
workloads = {
    "300 x 'tok{i} ' deltas": [f"tok{i} " for i in range(300)],
    "300 x 6-byte slices of prose": [("The quick brown fox jumps over the lazy dog. " * 60)[i*6:(i+1)*6] for i in range(300)],
}
for wname, toks in workloads.items():
    print(f"\n{wname}: {sum(len(t.encode()) for t in toks)} bytes")
    for fb in (64, 96, 160, 192, 384):
        ss.STREAM_FLUSH_BYTES = fb
        n = asyncio.run(run(toks))
        tag = "  <- shipped" if fb == shipped else ""
        print(f"   STREAM_FLUSH_BYTES={fb:3d}: guard passes = {n}{tag}")
    ss.STREAM_FLUSH_BYTES = shipped
