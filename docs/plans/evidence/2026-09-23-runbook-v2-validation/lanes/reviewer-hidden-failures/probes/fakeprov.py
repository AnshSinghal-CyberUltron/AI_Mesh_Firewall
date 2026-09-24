"""Minimal OpenAI-shaped provider for error-mapping probes: behaviour selected by header x-synth-mode.
400 -> invalid_request_error (context_length_exceeded); 429 -> rate_limit + retry-after: 7; ok -> tiny completion.
Counts calls per x-request-id into a JSONL file."""
import json, sys
from aiohttp import web
LOG = open(sys.argv[2], "a", buffering=1)
async def chat(req):
    body = await req.read(); mode = req.headers.get("x-synth-mode", "ok"); rid = req.headers.get("x-request-id")
    LOG.write(json.dumps({"rid": rid, "mode": mode, "len": len(body)}) + "\n")
    if mode == "400":
        return web.json_response({"error": {"message": "This model's maximum context length is 128000 tokens.", "type": "invalid_request_error",
                                            "param": "messages", "code": "context_length_exceeded"}}, status=400)
    if mode == "429":
        return web.json_response({"error": {"message": "Rate limit reached for gpt-4o-mini on tokens per min.", "type": "requests",
                                            "param": None, "code": "rate_limit_exceeded"}}, status=429, headers={"retry-after": "7"})
    return web.json_response({"id": "c", "object": "chat.completion", "created": 1, "model": "m", "choices": [{"index": 0,
        "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
app = web.Application(); app.router.add_post("/v1/chat/completions", chat)
web.run_app(app, host="127.0.0.1", port=int(sys.argv[1]), print=None)
