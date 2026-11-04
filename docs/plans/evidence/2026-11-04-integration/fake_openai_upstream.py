"""TEST HARNESS ONLY: a tiny OpenAI-compatible upstream for the integration live gates.

The REAL booted gateway-v2 (dev profile) dials this as AMF_DEV_PROVIDER_URL. The last user
message selects the script:
  * contains "TOOLCALL"  -> streamed (or non-stream) tool call get_weather({"city":"Paris"}),
                            arguments split across several chunks
  * contains "SLOWSTREAM" -> 40 text chunks, 150 ms apart (for client-side cancellation)
  * contains "UPSTREAM500" -> HTTP 500 from the provider (GW35 honest error action)
  * anything else        -> "Hello from the fake upstream." streamed or not
Every request is logged to stdout as one JSON line (path, stream, script, aborted?).
"""
import asyncio
import json

from aiohttp import web

CHUNK_ID = "chatcmpl-fake"


def chunk(delta, finish=None):
    body = {"id": CHUNK_ID, "object": "chat.completion.chunk", "created": 0, "model": "fake-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return ("data: " + json.dumps(body) + "\n\n").encode()


def last_user(body):
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


async def chat(request: web.Request) -> web.StreamResponse:
    body = await request.json()
    text = last_user(body)
    stream = bool(body.get("stream"))
    script = ("tool" if "TOOLCALL" in text else "slow" if "SLOWSTREAM" in text
              else "fail" if "UPSTREAM500" in text else "plain")
    log = {"path": request.path, "stream": stream, "script": script, "aborted": False,
           "max_tokens": body.get("max_tokens"), "max_completion_tokens": body.get("max_completion_tokens")}
    if script == "fail":
        print(json.dumps(log), flush=True)
        return web.json_response({"error": {"message": "upstream exploded", "type": "server_error"}}, status=500)
    if not stream:
        print(json.dumps(log), flush=True)
        if script == "tool":
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                   "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]}
            finish = "tool_calls"
        else:
            msg, finish = {"role": "assistant", "content": "Hello from the fake upstream."}, "stop"
        return web.json_response({"id": CHUNK_ID, "object": "chat.completion", "created": 0, "model": "fake-model",
                                  "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                                  "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}})
    resp = web.StreamResponse(headers={"content-type": "text/event-stream"})
    await resp.prepare(request)
    try:
        if script == "tool":
            await resp.write(chunk({"role": "assistant", "content": None, "tool_calls": [
                {"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": ""}}]}))
            for part in ["{\"ci", "ty\": \"Pa", "ris\"}"]:
                await resp.write(chunk({"tool_calls": [{"index": 0, "function": {"arguments": part}}]}))
            await resp.write(chunk({}, "tool_calls"))
        elif script == "slow":
            await resp.write(chunk({"role": "assistant", "content": ""}))
            for i in range(40):
                await asyncio.sleep(0.15)
                await resp.write(chunk({"content": f"word{i} "}))
            await resp.write(chunk({}, "stop"))
        else:
            await resp.write(chunk({"role": "assistant", "content": ""}))
            for part in ["Hello ", "from the ", "fake upstream."]:
                await resp.write(chunk({"content": part}))
            await resp.write(chunk({}, "stop"))
        await resp.write(b"data: [DONE]\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        log["aborted"] = True
        print(json.dumps(log), flush=True)
        raise
    print(json.dumps(log), flush=True)
    return resp


app = web.Application()
app.router.add_post("/v1/chat/completions", chat)
web.run_app(app, host="0.0.0.0", port=9600, print=None)
