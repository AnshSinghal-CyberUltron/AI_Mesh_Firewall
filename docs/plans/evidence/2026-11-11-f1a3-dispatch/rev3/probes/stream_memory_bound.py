"""F1a-3 plan revision 3: the per-stream memory bound (answers A6(c), B-S-2).

Two measurements, both on the design's transport settings (aiohttp 3.14.5, plan §3.13) and the shipped
`SSEDecoder` with the revision-3 event bound added as a wrapper (develop's decoder has no bound):

1. ``retained``: the Python heap a chat request keeps while its stream slot is held, through develop's
   real parser: raw body bytes + ``OpenAIRequestParser.parse`` (CanonicalRequest with its folded channels
   and upstream template) + one rendered upstream body (``json.dumps`` of the parsed object, the
   upstream_body.py render path's output size). Reported as a multiple of the raw body size, for body
   sizes up to ``max_request_bytes()`` at per_worker_rss = 400 MiB (1 MiB) and 1 GiB (2.56 MiB).

2. ``hostile``: process RSS per stream when every stream is driven to its bounds by a hostile upstream
   (64 streams at once, one process, uvloop), for three shapes:
     U  an unterminated event that grows to just under the event bound, then the upstream stalls;
     E  back-to-back complete events of just under the event bound, as fast as the client reads, with the
        revision-3 per-stream pacer charging ceil(bytes / 1 KiB) tokens per event;
     S  a reader that stops reading after the head (an empty pacer bucket, or a slow client) while the
        upstream writes as fast as TCP allows: aiohttp's StreamReader buffer stops at its high-water mark
        and pauses the transport.
   for read_bufsize 16 KiB and 64 KiB (aiohttp's high water is 2 x read_bufsize), reading with
   ``iter_chunked(16 KiB)``. RSS is sampled every 10 ms from /proc/self/statm; per-stream = (peak -
   baseline) / streams, the baseline taken with the same connections open and idle.

  python -I stream_memory_bound.py server <port>
  python -I stream_memory_bound.py retained                      (needs PYTHONPATH=<worktree>/gateway_v2)
  python -I stream_memory_bound.py hostile <port> <shape> <read_bufsize> <streams> <event_bound> [rcvbuf]

3. ``kernel`` (shape S only): the kernel TCP buffer memory the stalled streams pin, from the system-wide
   /proc/net/sockstat "TCP ... mem" page count (both ends on this host, so it includes the upstream's send
   buffers; other processes add noise), with the upstream socket's SO_RCVBUF left to autotuning (0) or set
   to 64 KiB in the connector's socket_factory, where the plan's dial guard already runs (§3.13).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time

PAGE = os.sysconf("SC_PAGE_SIZE")
KIB = 1024


def rss_bytes() -> int:
    with open("/proc/self/statm") as fh:
        return int(fh.read().split()[1]) * PAGE


# ----------------------------------------------------------------------------------------- server
def _chunk(data: bytes) -> bytes:
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


def _event(content_len: int) -> bytes:
    obj = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
           "choices": [{"index": 0, "delta": {"content": "x" * content_len}, "finish_reason": None}]}
    return b"data: " + json.dumps(obj, separators=(",", ":")).encode() + b"\n\n"


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            head = await reader.readuntil(b"\r\n\r\n")
            path = head.split(b" ", 2)[1].decode().strip("/").split("/")
            n = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":", 1)[1])
            if n:
                await reader.readexactly(n)
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n")
            kind = path[0]
            if kind == "ok":  # one small normal event, for the baseline
                writer.write(_chunk(_event(16)) + _chunk(b"data: [DONE]\n\n") + b"0\r\n\r\n")
                await writer.drain()
                continue
            bound, secs = int(path[1]), float(path[2])
            end = time.monotonic() + secs
            if kind == "U":  # unterminated event just under the bound, then stall
                body = b'data: {"choices":[{"index":0,"delta":{"content":"' + b"x" * (bound - 200)
                for i in range(0, len(body), 4 * KIB):
                    writer.write(_chunk(body[i:i + 4 * KIB]))
                    await writer.drain()
                while time.monotonic() < end:
                    await asyncio.sleep(0.05)
            elif kind == "E":  # complete events just under the bound, as fast as the reader allows
                ev = _chunk(_event(bound - 1024))
                while time.monotonic() < end:
                    writer.write(ev)
                    await writer.drain()
            elif kind == "S":  # small events as fast as TCP allows; the client does not read
                ev = _chunk(_event(300)) * 64
                while time.monotonic() < end:
                    writer.write(ev)
                    await writer.drain()
            writer.write(_chunk(b"data: [DONE]\n\n") + b"0\r\n\r\n")
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, asyncio.LimitOverrunError):
        pass
    finally:
        writer.close()


async def server(port: int) -> None:
    srv = await asyncio.start_server(handle, "127.0.0.1", port, backlog=1024)
    print("ready", flush=True)
    async with srv:
        await srv.serve_forever()


# ----------------------------------------------------------------------------------------- pacer
class Pacer:
    """Revision-3 per-stream read pacer (plan §3.6.5): a token bucket whose rate is the stream's share of
    the worker's event budget, never below the declared per-stream rate. Tokens per event =
    ceil(event bytes / 1 KiB). Awaiting it before the next read is what stops reading (TCP back-pressure)."""

    def __init__(self, rate_hz: float, burst: float) -> None:
        self.rate, self.burst, self.tokens, self.t = rate_hz, burst, burst, time.monotonic()

    async def take(self, n: float) -> None:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now
        self.tokens -= n
        if self.tokens < 0:
            await asyncio.sleep(-self.tokens / self.rate)


class Oversize(Exception):
    pass


def tcp_mem_pages() -> int:
    with open("/proc/net/sockstat") as fh:
        for line in fh:
            if line.startswith("TCP:"):
                parts = line.split()
                return int(parts[parts.index("mem") + 1])
    return 0


async def hostile(port: int, shape: str, read_bufsize: int, streams: int, bound: int, rcvbuf: int = 0) -> dict:
    import array
    import fcntl
    import socket
    import termios

    import aiohttp
    from gateway_v2.edge.wire.sse_decode import SSEDecoder

    def factory(addr_info):  # the plan's socket_factory seam (dial guard); here it only sizes SO_RCVBUF
        family, type_, proto, _, _ = addr_info
        sock = socket.socket(family=family, type=type_, proto=proto)
        sock.setblocking(False)
        if rcvbuf:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        return sock

    sess = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=streams + 4, use_dns_cache=False, socket_factory=factory),
        trust_env=False, auto_decompress=False, max_line_size=8190, max_field_size=8190,
        skip_auto_headers=("Accept-Encoding", "User-Agent"), read_bufsize=read_bufsize,
        timeout=aiohttp.ClientTimeout(total=60))
    hdr = {"accept-encoding": "identity", "content-type": "application/json"}
    url = f"http://127.0.0.1:{port}"

    async def ok() -> None:
        async with sess.post(f"{url}/ok", data=b"{}", headers=hdr, allow_redirects=False) as r:
            await r.read()

    await asyncio.gather(*(ok() for _ in range(streams)))  # open and pool the connections
    for _ in range(3):
        await asyncio.sleep(0.2)
    base = rss_bytes()
    tcp0 = tcp_mem_pages()
    tcp_peak = [tcp0]
    peak = [base]
    stop = asyncio.Event()

    async def sampler() -> None:
        while not stop.is_set():
            peak[0] = max(peak[0], rss_bytes())
            tcp_peak[0] = max(tcp_peak[0], tcp_mem_pages())
            await asyncio.sleep(0.01)

    outcome = {"oversize": 0, "events": 0, "done": 0}
    secs = 3.0

    async def one() -> None:
        d = SSEDecoder()
        pacer = Pacer(rate_hz=345.0, burst=17.0)  # the §3.6.5 rate for 65 streams at 31.9 us/event
        async with sess.post(f"{url}/{shape}/{bound}/{secs}", data=b"{}", headers=hdr,
                             allow_redirects=False) as r:
            if shape == "S":
                await asyncio.sleep(secs - 0.5)  # never read: aiohttp pauses the transport at high water
                sock = r.connection.transport.get_extra_info("socket") if r.connection else None
                if sock is not None:
                    buf = array.array("i", [0])
                    fcntl.ioctl(sock.fileno(), termios.FIONREAD, buf)  # unread bytes in OUR receive queue
                    outcome["kernel_rcvq_bytes"] = outcome.get("kernel_rcvq_bytes", 0) + buf[0]
                    outcome["so_rcvbuf_effective"] = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
                await asyncio.sleep(0.5)
                return
            try:
                async for chunk in r.content.iter_chunked(16 * KIB):
                    frames = d.feed(chunk)
                    if len(d._buffer) > bound:
                        raise Oversize
                    for _fr in frames:
                        outcome["events"] += 1
                        await pacer.take(math.ceil(bound / KIB) if shape == "E" else 1)
                    await asyncio.sleep(0)  # yield after each read chunk (§3.6.5)
                outcome["done"] += 1
            except Oversize:
                outcome["oversize"] += 1

    samp = asyncio.ensure_future(sampler())
    await asyncio.gather(*(one() for _ in range(streams)))
    stop.set()
    await samp
    await sess.close()
    per = (peak[0] - base) / streams
    return {"shape": shape, "read_bufsize": read_bufsize, "streams": streams, "event_bound": bound,
            "baseline_rss_mib": round(base / 2**20, 1), "peak_rss_mib": round(peak[0] / 2**20, 1),
            "per_stream_kib": round(per / KIB, 1), **outcome, "aiohttp": aiohttp.__version__,
            "so_rcvbuf_set": rcvbuf, "tcp_mem_peak_delta_kib_per_stream_both_ends":
                round((tcp_peak[0] - tcp0) * PAGE / streams / KIB, 1),
            "client_kernel_rcvq_kib_per_stream": round(outcome.get("kernel_rcvq_bytes", 0) / streams / KIB, 1)}


# ----------------------------------------------------------------------------------------- retained
def retained() -> list[dict]:
    import gc
    import tracemalloc

    from gateway_v2.domain.plan import Surface
    from gateway_v2.edge.wire.openai import OpenAIRequestParser
    from gateway_v2.runtime.kinds import HardwareSignals
    from gateway_v2.runtime.resources import from_signals

    out = []
    for rss_mib in (400, 1024):
        rss = rss_mib * 2**20
        sig = HardwareSignals(cpu_quota=16.0, memory_limit=rss * 12, fd_limit=65536,
                              cpu_source="t", mem_source="t", fd_source="t")
        c = from_signals(sig, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=rss)
        limit = c.max_request_bytes()
        for target in (64 * KIB, limit):
            for shape in ("short", "long"):
                # short: many 1 KiB messages (chat history); long: few 64 KiB messages (documents)
                size = 1024 if shape == "short" else min(65536, target // 8)
                msgs, total = [], 0
                while total < target - size - 512:
                    text = ("lorem ipsum dolor sit amet " * (size // 27 + 1))[:size]
                    msgs.append({"role": "user" if len(msgs) % 2 == 0 else "assistant", "content": text})
                    total += size + 40
                raw = json.dumps({"model": "m", "messages": msgs, "stream": True}).encode()
                gc.collect()
                tracemalloc.start()
                held_raw = bytes(bytearray(raw))  # a real copy: bytes(bytes) would alias
                req = OpenAIRequestParser().parse(held_raw, surface=Surface.CHAT, contract=c)
                parsed_only, _ = tracemalloc.get_traced_memory()
                rendered = json.dumps(json.loads(held_raw)).encode()  # one upstream render (transient parse in peak)
                cur, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                out.append({"per_worker_rss_mib": rss_mib, "max_request_bytes": limit, "shape": shape,
                            "raw_bytes": len(raw), "raw_plus_parsed_kib": round(parsed_only / KIB),
                            "plus_render_kib": round(cur / KIB), "peak_kib": round(peak / KIB),
                            "factor_held": round(cur / len(raw), 2), "factor_peak": round(peak / len(raw), 2)})
                del held_raw, req, rendered
    return out


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "server":
        asyncio.run(server(int(sys.argv[2])))
    elif mode == "retained":
        for row in retained():
            print(json.dumps(row), flush=True)
    else:
        import uvloop
        uvloop.install()
        port, shape, rb, n, bound = int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
        rcv = int(sys.argv[7]) if len(sys.argv) > 7 else 0
        print(json.dumps(asyncio.run(hostile(port, shape, rb, n, bound, rcv))), flush=True)
