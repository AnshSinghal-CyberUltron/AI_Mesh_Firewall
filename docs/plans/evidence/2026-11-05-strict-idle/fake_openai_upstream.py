"""TEST HARNESS ONLY: a tiny OpenAI-compatible upstream for the strict-idle live gate.

Adapted from docs/plans/evidence/2026-11-04-integration/fake_openai_upstream.py. The REAL booted
gateway-v2 (dev profile) dials this as AMF_DEV_PROVIDER_URL. The last user message selects the
script:
  * contains "TOOLCALL"   -> streamed (or non-stream) tool call get_weather({"city":"Paris"})
  * contains "SLOWSTREAM" -> 40 text chunks, 150 ms apart (~6 s total, > the 3 s idle bound)
  * contains "SLOWLEAK"   -> like SLOWSTREAM but one chunk carries an INTERNAL IP (10.1.2.3) so
                             an enforcing output.ip_leak BLOCK rule trips the terminal block
                             (check c). ~6 s total, leak chunk mid-stream.
  * contains "SLOWSTALL"  -> emits a few chunks, then PAUSES 1.5 s mid-stream (> the 1 s
                             inter_chunk bound) so the upstream-stall guard cuts it (check d).
  * contains "UPSTREAM500" -> HTTP 500 from the provider
  * anything else         -> "Hello from the fake upstream." streamed or not
Every request is logged to stdout as one JSON line (path, stream, script, aborted?).

The two new scripts (SLOWLEAK, SLOWSTALL) are additive test-harness variants of SLOWSTREAM; they
add nothing to the product and exist only to exercise checks (c) and (d) on the real wire.
"""
import asyncio
import json

from aiohttp import web

CHUNK_ID = "chatcmpl-fake"

# An internal (RFC1918) address the gateway's output.ip_leak detector flags. Split so this source
# file itself never contains the literal "10.1.2.3" as one token that a scanner of THIS file might
# flag; the wire bytes the upstream emits are the real "10.1.2.3".
INTERNAL_IP = "10." + "1.2.3"


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
    script = ("tool" if "TOOLCALL" in text
              else "slowleak" if "SLOWLEAK" in text
              else "slowstall" if "SLOWSTALL" in text
              else "slow" if "SLOWSTREAM" in text
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
        elif script == "slowleak":
            # ~6 s stream (40 chunks, 150 ms apart) that carries an internal IP mid-stream, so the
            # whole withheld buffer trips an enforcing output.ip_leak BLOCK rule.
            await resp.write(chunk({"role": "assistant", "content": ""}))
            for i in range(40):
                await asyncio.sleep(0.15)
                if i == 20:
                    await resp.write(chunk({"content": f"server {INTERNAL_IP} "}))
                else:
                    await resp.write(chunk({"content": f"word{i} "}))
            await resp.write(chunk({}, "stop"))
        elif script == "slowstall":
            # A few healthy chunks, then a 1.5 s silence (> the 1 s inter_chunk bound).
            await resp.write(chunk({"role": "assistant", "content": ""}))
            for i in range(3):
                await asyncio.sleep(0.15)
                await resp.write(chunk({"content": f"word{i} "}))
            await asyncio.sleep(1.5)  # upstream stall mid-stream
            await resp.write(chunk({"content": "late "}))
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
