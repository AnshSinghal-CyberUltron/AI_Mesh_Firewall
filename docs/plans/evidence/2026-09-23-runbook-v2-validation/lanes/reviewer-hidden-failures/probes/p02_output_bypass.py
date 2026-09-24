"""P02: provider output fields that rvproto's output guard never inspects (org-a: output secret/PII -> REDACT).

Runs the benchmarked egress code (egress/jsonout.inspect_json and egress/stream.StreamPipeline) with the real
PlanInspector for org-a.  The 'model' leaks an email + AWS key in: content (control), logprobs tokens (client asked
logprobs=true), refusal, legacy function_call.arguments, audio.transcript, reasoning_content.
"""
import asyncio, sys
import orjson
sys.path.insert(0, sys.argv[1])
from rvproto.detect.matcher import Matcher
from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.edge.inspect import PlanInspector
from rvproto.egress.jsonout import inspect_json
from rvproto.egress.stream import StreamPipeline
from rvproto.plan.compiler import compile_plan
from rvproto.plan.fixtures import org_a
from rvproto.runtime.metrics import Registry

KEY, MAIL = "AKIAQYLPMN5HHHFPZAM2", "alice.canary@example.com"
ins = PlanInspector(Matcher(), DeterministicDetectors(Matcher()), compile_plan(org_a()))
secret_text = f"the key is {KEY} and mail {MAIL} ok"
toks = secret_text.split(" ")
logprobs = {"content": [{"token": (" " if i else "") + t, "logprob": -0.1, "bytes": list(((" " if i else "") + t).encode()),
                         "top_logprobs": [{"token": t, "logprob": -0.1, "bytes": list(t.encode())}]} for i, t in enumerate(toks)],
            "refusal": None}

def resp(message, lp=None):
    return orjson.dumps({"id": "x", "object": "chat.completion", "created": 1, "model": "m",
                         "choices": [{"index": 0, "message": message, "logprobs": lp, "finish_reason": "stop"}]})

cases = {
  "message.content (control)": resp({"role": "assistant", "content": secret_text}),
  "logprobs.content[].token/bytes (content redacted)": resp({"role": "assistant", "content": secret_text}, logprobs),
  "message.refusal": resp({"role": "assistant", "content": None, "refusal": secret_text}),
  "message.function_call.arguments (legacy)": resp({"role": "assistant", "content": None, "function_call": {"name": "f", "arguments": orjson.dumps({"q": secret_text}).decode()}}),
  "message.audio.transcript": resp({"role": "assistant", "content": None, "audio": {"id": "a", "data": "", "expires_at": 1, "transcript": secret_text}}),
  "message.reasoning_content (vLLM/DeepSeek-style)": resp({"role": "assistant", "content": "ok", "reasoning_content": secret_text}),
}
print("== non-stream (inspect_json) ==")
for name, raw in cases.items():
    jo = inspect_json(raw, ins)
    leak = [x for x in (KEY, MAIL) if x.encode() in jo.body]
    # bytes arrays in logprobs: reconstruct
    doc = orjson.loads(jo.body)
    lp = doc["choices"][0].get("logprobs")
    lp_text = "".join(bytes(c["bytes"]).decode() for c in lp["content"]) if lp else ""
    leak_lp = [x for x in (KEY, MAIL) if x in lp_text]
    print(f"{name:<50} disposition={jo.decision.disposition.value:<6} leaked_raw={leak or '-'} leaked_via_logprob_bytes={leak_lp or '-'}")

print("== SSE (StreamPipeline) ==")
async def run_stream(chunks):
    class R:  # minimal aiohttp.ClientResponse stand-in: .content.iter_any()
        class content:
            @staticmethod
            async def iter_any():
                for c in chunks:
                    yield c
    out = []
    async def send(b):
        out.append(b)
    pipe = StreamPipeline(ins, Registry(0), ceiling=10**9, inject_hold=None)
    st = await pipe.run(R(), send)
    return b"".join(out), st

def sse(delta, lp=None, finish=None):
    return b"data: " + orjson.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [{"index": 0, "delta": delta, "logprobs": lp, "finish_reason": finish}]}) + b"\n\n"

def lp1(t):
    return {"content": [{"token": t, "logprob": -0.1, "bytes": list(t.encode()), "top_logprobs": []}], "refusal": None}

streams = {
  "delta.content (control)": [sse({"role": "assistant", "content": ""})] + [sse({"content": (" " if i else "") + t}) for i, t in enumerate(toks)] + [sse({}, finish="stop"), b"data: [DONE]\n\n"],
  "delta.content + logprobs per chunk": [sse({"role": "assistant", "content": ""})] + [sse({"content": (" " if i else "") + t}, lp1((" " if i else "") + t)) for i, t in enumerate(toks)] + [sse({}, finish="stop"), b"data: [DONE]\n\n"],
  "delta.refusal": [sse({"role": "assistant", "content": None})] + [sse({"refusal": (" " if i else "") + t}) for i, t in enumerate(toks)] + [sse({}, finish="stop"), b"data: [DONE]\n\n"],
  "delta.function_call.arguments (legacy)": [sse({"role": "assistant", "content": None, "function_call": {"name": "f", "arguments": ""}})] + [sse({"function_call": {"arguments": (" " if i else "") + t}}) for i, t in enumerate(toks)] + [sse({}, finish="function_call"), b"data: [DONE]\n\n"],
  "delta.reasoning_content": [sse({"role": "assistant", "content": ""})] + [sse({"reasoning_content": (" " if i else "") + t}) for i, t in enumerate(toks)] + [sse({"content": "done"}), sse({}, finish="stop"), b"data: [DONE]\n\n"],
}
for name, chunks in streams.items():
    body, st = asyncio.run(run_stream(chunks))
    # reassemble everything the client can read
    txt = {"content": "", "logprob_tokens": "", "other": ""}
    for line in body.split(b"\n\n"):
        if not line.startswith(b"data: ") or line == b"data: [DONE]":
            continue
        o = orjson.loads(line[6:])
        for c in o.get("choices", []):
            d = c.get("delta") or {}
            txt["content"] += d.get("content") or ""
            for k in ("refusal", "reasoning_content"):
                txt["other"] += d.get(k) or ""
            txt["other"] += (d.get("function_call") or {}).get("arguments") or ""
            for e in ((c.get("logprobs") or {}).get("content") or []):
                txt["logprob_tokens"] += e["token"]
    leaks = {k: [x for x in (KEY, MAIL) if x in v] for k, v in txt.items()}
    print(f"{name:<45} stream_error={st.error} content_leak={leaks['content'] or '-'} logprob_leak={leaks['logprob_tokens'] or '-'} other_field_leak={leaks['other'] or '-'}")
