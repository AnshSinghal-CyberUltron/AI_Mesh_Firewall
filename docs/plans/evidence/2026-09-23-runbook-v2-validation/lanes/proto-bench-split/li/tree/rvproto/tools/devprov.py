"""Minimal synthprov-compatible dev provider (used only until the harness synthprov exists).

  python tools/devprov.py --port 18080 --record /path/rec.jsonl --canary a,b
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time

import orjson
from aiohttp import web

WORDS = ("alpha bravo river stone cloud maple quiet green orbit paper lemon tiger north "
         "violet summer ocean silver candle forest harbor meadow copper window garden").split()
INJECT = {
    "email": [" contact", " alice.smith@example.com", " today"],
    "aws": [" key", " AKIAIOSFODNN7EXAMPLE", " end"],
    "split-aws": [" key", " AKIAIOSF", "ODNN7EXA", "MPLE", " end"],
}


def token(rid: str, j: int) -> str:
    h = hashlib.sha256(f"{rid}:{j}".encode()).digest()
    return " " + WORDS[h[0] % len(WORDS)]


def tokens_for(rid: str, n: int, inject: str) -> list[str]:
    toks = [token(rid, j) for j in range(n)]
    if inject in INJECT and n >= 4:
        toks[3:3] = INJECT[inject]
    return toks


class Prov:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.canaries = [c for c in a.canary.split(",") if c]
        self.rec = open(a.record, "a", buffering=1) if a.record else None  # noqa: SIM115
        self.stats = {"requests": 0}

    def record(self, **kw: object) -> None:
        self.stats["requests"] += 1
        if self.rec:
            self.rec.write(json.dumps(kw) + "\n")

    async def v1stub(self, req: web.Request, doc: dict[str, object], base: dict[str, object]) -> web.StreamResponse:
        """Byte-for-byte the upstream fixtures of the repo's conformance suite (_fake_*)."""
        head = {"id": "chatcmpl-sdk-001", "object": "chat.completion", "created": 1700000000,
                "model": "gpt-4o-mini", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        self.record(**base, status=200, tokens_out=3, recv_to_last_ns=0)
        if not doc.get("stream"):
            if doc.get("tools"):
                msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_sdk_001", "type": "function",
                       "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]}
                return web.json_response(dict(head, choices=[{"index": 0, "message": msg, "finish_reason": "tool_calls"}]))
            content = "{\"city\": \"Paris\", \"temp_c\": 21}" if doc.get("response_format") else "Hello from upstream."
            return web.json_response(dict(head, choices=[{"index": 0, "message": {"role": "assistant", "content": content},
                                                          "finish_reason": "stop"}]))
        resp = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await resp.prepare(req)
        for i, token in enumerate(["Hello", " streaming", " world."]):
            chunk = {"id": "chatcmpl-sdk-stream-001", "object": "chat.completion.chunk", "created": 1700000000,
                     "model": "gpt-4o-mini", "choices": [{"index": 0, "delta": {"content": token} if i > 0 else
                                                          {"role": "assistant", "content": token},
                                                          "finish_reason": None if i < 2 else "stop"}]}
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def chat(self, req: web.Request) -> web.StreamResponse:
        body = await req.read()
        t_recv = time.perf_counter_ns()
        rid = req.headers.get("x-request-id", "none")
        doc = orjson.loads(body)
        h = req.headers
        n = min(int(doc.get("max_tokens") or doc.get("max_completion_tokens") or self.a.tokens),
                int(h.get("x-synth-tokens", self.a.tokens)))
        ttft = float(h.get("x-synth-ttft-ms", self.a.ttft)) / 1000
        itl = float(h.get("x-synth-itl-ms", self.a.itl)) / 1000
        inject = h.get("x-synth-inject", "none")
        tool = h.get("x-synth-tool") == "1" and bool(doc.get("tools"))
        hits = [c for c in self.canaries if c.encode() in body]
        base = dict(request_id=rid, stream=bool(doc.get("stream")), body_len=len(body),
                    body_sha256=hashlib.sha256(body).hexdigest(), canary_hits=hits)
        if self.a.fixture == "v1stub":
            return await self.v1stub(req, doc, base)
        cid = "chatcmpl-" + hashlib.sha256(rid.encode()).hexdigest()[:24]
        toks = tokens_for(rid, n, inject)
        if not doc.get("stream"):
            await asyncio.sleep(ttft + itl * max(len(toks) - 1, 0))
            msg: dict[str, object] = {"role": "assistant", "content": "".join(toks)}
            fin = "stop"
            if tool:
                msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                       "function": {"name": "get_weather", "arguments": '{"city": "Paris", "unit": "celsius"}'}}]}
                fin = "tool_calls"
            out = {"id": cid, "object": "chat.completion", "created": 1, "model": doc.get("model"),
                   "choices": [{"index": 0, "message": msg, "finish_reason": fin}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": len(toks), "total_tokens": 10 + len(toks)}}
            self.record(**base, status=200, tokens_out=len(toks), recv_to_last_ns=time.perf_counter_ns() - t_recv)
            return web.json_response(out)
        resp = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await resp.prepare(req)

        async def chunk(delta: dict[str, object], fin: str | None = None) -> None:
            c = {"id": cid, "object": "chat.completion.chunk", "created": 1, "model": doc.get("model"),
                 "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
            await resp.write(b"data: " + orjson.dumps(c) + b"\n\n")

        start = time.monotonic()
        progress = [0]
        try:
            await self._emit(chunk, resp, doc, toks, tool, ttft, itl, start, cid, progress)
        except (ConnectionResetError, asyncio.CancelledError):
            self.record(**base, status=499, tokens_out=progress[0],
                        recv_to_last_ns=time.perf_counter_ns() - t_recv)
            raise
        self.record(**base, status=200, tokens_out=len(toks), recv_to_last_ns=time.perf_counter_ns() - t_recv)
        return resp

    async def _emit(self, chunk, resp, doc, toks, tool, ttft, itl, start, cid, progress):  # type: ignore[no-untyped-def]
        await asyncio.sleep(ttft)
        await chunk({"role": "assistant", "content": ""})
        if tool:
            args = '{"city": "Paris", "unit": "celsius", "days": 3}'
            frags = [args[i:i + 7] for i in range(0, len(args), 7)]
            await chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                         "function": {"name": "get_weather", "arguments": ""}}]})
            for k, f in enumerate(frags):
                await asyncio.sleep(max(0.0, start + ttft + itl * (k + 1) - time.monotonic()))
                await chunk({"tool_calls": [{"index": 0, "function": {"arguments": f}}]})
            await chunk({}, "tool_calls")
        else:
            for k, t in enumerate(toks):
                await asyncio.sleep(max(0.0, start + ttft + itl * k - time.monotonic()))
                await chunk({"content": t})
                progress[0] = k + 1
            await chunk({}, "stop")
        if (doc.get("stream_options") or {}).get("include_usage"):
            u = {"id": cid, "object": "chat.completion.chunk", "created": 1, "model": doc.get("model"),
                 "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": len(toks),
                                          "total_tokens": 10 + len(toks)}}
            await resp.write(b"data: " + orjson.dumps(u) + b"\n\n")
        await resp.write(b"data: [DONE]\n\n")

    async def models(self, req: web.Request) -> web.Response:
        return web.json_response({"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model"}]})

    async def stats_h(self, req: web.Request) -> web.Response:
        return web.json_response(self.stats)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--record", default="")
    ap.add_argument("--canary", default="")
    ap.add_argument("--tokens", type=int, default=20)
    ap.add_argument("--ttft", type=float, default=50)
    ap.add_argument("--itl", type=float, default=5)
    ap.add_argument("--fixture", default="synth", choices=["synth", "v1stub"])
    a = ap.parse_args()
    p = Prov(a)
    app = web.Application()
    app.router.add_post("/v1/chat/completions", p.chat)
    app.router.add_get("/v1/models", p.models)
    app.router.add_get("/_rv/stats", p.stats_h)
    web.run_app(app, host="127.0.0.1", port=a.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
