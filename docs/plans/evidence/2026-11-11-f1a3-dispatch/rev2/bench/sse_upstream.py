# Provenance: written by PR #121 Plan Reviewer B (review 5869723316, bench/sse_upstream.py on the
# review host) and reused UNCHANGED below this line for the F1a-3 revision 2 benchmark, with credit.
"""Mock OpenAI-compatible SSE upstream for the PR #121 Reviewer-B client-cost benchmark.

Runs in its OWN process so the client process's CPU is the gateway-side cost only.
HTTP/1.1 keep-alive, chunked transfer encoding, ONE HTTP chunk per SSE event (what OpenAI,
Mistral, DeepSeek etc. send). Unlike the author's upstream.py (which writes the whole
pre-rendered stream in a single transport.write), events can be PACED with an inter-event delay,
so each event arrives in its own TCP segment, as it does from a real model emitting tokens.

    python sse_upstream.py --port 19050 --events 200 --gap-ms 10 [--burst]

Event shape mirrors a 2025/2026 OpenAI chat.completion.chunk (id, object, created, model,
service_tier, system_fingerprint, choices[delta, logprobs, finish_reason], obfuscation pad),
about 250 bytes per event. The last three events are the finish chunk, the usage chunk and
[DONE]. Optional --gzip-bomb-kib sends a gzip Content-Encoding body for the decompression test.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import random
import sys

_rng = random.Random(121)


def _event(i: int, content: str | None, finish: str | None, usage: dict | None) -> bytes:
    delta: dict[str, object] = {}
    if i == 0:
        delta = {"role": "assistant", "content": "", "refusal": None}
    elif content is not None:
        delta = {"content": content}
    obj: dict[str, object] = {
        "id": "chatcmpl-CYbK3xQ2mZp9TtVwH0aLr7nE4sUoD",
        "object": "chat.completion.chunk",
        "created": 1762905600,
        "model": "gpt-4o-2024-08-06",
        "service_tier": "default",
        "system_fingerprint": "fp_cbf1785567",
        "choices": [] if usage else [
            {"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish}
        ],
        "usage": usage,
        "obfuscation": "".join(_rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJ0123456789")
                               for _ in range(_rng.randint(4, 14))),
    }
    return b"data: " + json.dumps(obj, separators=(",", ":")).encode() + b"\n\n"


def build_events(n: int) -> list[bytes]:
    words = ["the", " model", " streams", " one", " token", " per", " chunk", ",", " and",
             " real", " answers", " run", " to", " hundreds", " of", " tokens", "."]
    events = [_event(0, None, None, None)]
    for i in range(1, n - 2):
        events.append(_event(i, words[i % len(words)], None, None))
    events.append(_event(n, None, "stop", None))
    events.append(_event(n, None, None, {"prompt_tokens": 412, "completion_tokens": n,
                                          "total_tokens": 412 + n}))
    events.append(b"data: [DONE]\n\n")
    return events


def chunk(event: bytes) -> bytes:
    return f"{len(event):x}\r\n".encode() + event + b"\r\n"


HEAD = (b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream; charset=utf-8\r\n"
        b"transfer-encoding: chunked\r\ncache-control: no-cache\r\nx-request-id: req_bench\r\n\r\n")


class Stats:
    ok = 0
    bad_auth = 0


class Upstream(asyncio.Protocol):
    def __init__(self, args: argparse.Namespace, events: list[bytes], bearer: bytes,
                 bomb: bytes | None) -> None:
        self.args, self.events, self.bearer, self.bomb = args, events, bearer, bomb
        self.buf = bytearray()
        self.transport: asyncio.Transport | None = None
        self.loop = asyncio.get_running_loop()
        self.closed = False

    def connection_made(self, transport):  # type: ignore[no-untyped-def]
        self.transport = transport

    def connection_lost(self, exc):  # type: ignore[no-untyped-def]
        self.closed = True

    def data_received(self, data: bytes) -> None:
        self.buf += data
        while True:
            end = self.buf.find(b"\r\n\r\n")
            if end < 0:
                return
            head = bytes(self.buf[:end]).decode("latin-1")
            lines = head.split("\r\n")
            headers = {}
            for line in lines[1:]:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
            length = int(headers.get("content-length", "0"))
            if len(self.buf) < end + 4 + length:
                return
            del self.buf[: end + 4 + length]
            self.handle(lines[0], headers)

    def handle(self, request_line: str, headers: dict[str, str]) -> None:
        assert self.transport is not None
        if request_line.startswith("GET /stats"):
            body = json.dumps({"ok": Stats.ok, "bad_auth": Stats.bad_auth}).encode()
            self.transport.write(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                                 b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            return
        if headers.get("authorization", "").encode() != b"Bearer " + self.bearer:
            Stats.bad_auth += 1
            err = b'{"error":{"message":"bad key","type":"invalid_request_error"}}'
            self.transport.write(b"HTTP/1.1 401 Unauthorized\r\ncontent-type: application/json\r\n"
                                 b"content-length: " + str(len(err)).encode() + b"\r\n\r\n" + err)
            return
        Stats.ok += 1
        if self.bomb is not None:
            self.transport.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncontent-encoding: gzip\r\n"
                b"content-length: " + str(len(self.bomb)).encode() + b"\r\n\r\n" + self.bomb)
            return
        if self.args.burst:
            self.transport.write(HEAD + b"".join(chunk(e) for e in self.events) + b"0\r\n\r\n")
            return
        self.transport.write(HEAD)
        self._send(0)

    def _send(self, i: int) -> None:
        if self.closed or self.transport is None or self.transport.is_closing():
            return
        if i >= len(self.events):
            self.transport.write(b"0\r\n\r\n")
            return
        self.transport.write(chunk(self.events[i]))
        gap = self.args.gap_ms / 1000.0
        # +-30% jitter so streams on different connections do not tick in lock-step
        self.loop.call_later(gap * (0.7 + 0.6 * _rng.random()), self._send, i + 1)


async def serve(args: argparse.Namespace) -> None:
    bearer = os.environ[args.key_env].encode()
    events = build_events(args.events)
    bomb = None
    if args.gzip_bomb_kib:
        # gzip of zeros: ~1030x expansion. kib of compressed output -> ~1 MiB per KiB decompressed.
        raw = b"\0" * (args.gzip_bomb_kib * 1030 * 1024)
        bomb = gzip.compress(raw, compresslevel=9)
        del raw
    server = await asyncio.get_running_loop().create_server(
        lambda: Upstream(args, events, bearer, bomb), args.host, args.port, backlog=4096)
    sizes = [len(e) for e in events]
    print(json.dumps({"ready": True, "events": len(events), "event_bytes_mean":
                      round(sum(sizes) / len(sizes), 1), "stream_bytes": sum(sizes),
                      "bomb_compressed_bytes": len(bomb) if bomb else 0}), flush=True)
    async with server:
        await server.serve_forever()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=19050)
    p.add_argument("--events", type=int, default=200)
    p.add_argument("--gap-ms", type=float, default=10.0)
    p.add_argument("--burst", action="store_true")
    p.add_argument("--gzip-bomb-kib", type=int, default=0)
    p.add_argument("--key-env", default="B121_BENCH_KEY")
    args = p.parse_args()
    import uvloop
    uvloop.install()
    asyncio.run(serve(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
