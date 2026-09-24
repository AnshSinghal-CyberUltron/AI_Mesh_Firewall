"""OpenAI-shaped provider that honours logprobs=true (like api.openai.com): leaks a canary in content and
repeats every token in choices[].logprobs.content[].token/bytes.  JSON and SSE."""
import asyncio, json, sys
from aiohttp import web
CAN = {c["id"]: c["value"] for c in json.load(open(sys.argv[2]))["canaries"]}
TEXT = f"the key is {CAN['out.aws']} and mail {CAN['out.email']} ok"
TOKS = [(" " if i else "") + t for i, t in enumerate(TEXT.split(" "))]
def lp(t): return {"token": t, "logprob": -0.01, "bytes": list(t.encode()), "top_logprobs": []}
async def chat(req):
    d = json.loads(await req.read()); want = bool(d.get("logprobs"))
    base = {"id": "chatcmpl-lp", "created": 1, "model": "gpt-4o-mini"}
    if not d.get("stream"):
        return web.json_response({**base, "object": "chat.completion", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": TEXT, "refusal": None},
            "logprobs": {"content": [lp(t) for t in TOKS], "refusal": None} if want else None, "finish_reason": "stop"}]})
    resp = web.StreamResponse(headers={"content-type": "text/event-stream"}); await resp.prepare(req)
    async def ev(o): await resp.write(b"data: " + json.dumps(o).encode() + b"\n\n")
    await ev({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "logprobs": None, "finish_reason": None}]})
    for t in TOKS:
        await ev({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": t},
                  "logprobs": {"content": [lp(t)], "refusal": None} if want else None, "finish_reason": None}]}); await asyncio.sleep(0.005)
    await ev({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "logprobs": None, "finish_reason": "stop"}]})
    await resp.write(b"data: [DONE]\n\n"); return resp
app = web.Application(); app.router.add_post("/v1/chat/completions", chat)
web.run_app(app, host="127.0.0.1", port=int(sys.argv[1]), print=None)
