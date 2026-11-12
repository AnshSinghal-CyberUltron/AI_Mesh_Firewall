"""F1a-3 plan revision 3: Plan Reviewer B's head-of-line probe (#121 rev 2, review 5871411489,
`bench/hol_probe.py`), extended with the revision-3 read discipline (plan §3.6.5). Provenance: B's file,
unchanged except the `variant` and `c_event_us` arguments and the pacer below (marked "rev 3").

Variants: none = B's client unchanged (`iter_any`, no yield, no pacer);
          yield = bounded reads (`iter_chunked(16 KiB)`) and `await asyncio.sleep(0)` after each read;
          pace = yield + a per-stream token bucket, rate max(r_event, E_max / streams in flight),
                 E_max = util / c_event, burst max(8, 0.05 s x rate), 1 token per event of <= 1 KiB.

Original docstring (Reviewer B): head-of-line effect of ONE fast stream on a worker's other streams.

The plan's stream bound S_cpu = util / (r_event x c_event) (§3.6.2) assumes every stream emits at the
declared r_event (50 Hz). Nothing in the plan bounds an upstream's event rate per stream, and a custom origin
(an org admin's own server, or a small model on vLLM) can emit far faster. This probe measures what one or
four such streams do to 64 paced streams on the same event loop.

  python -I hol_probe.py server <port>
  python -I hol_probe.py client <port> <paced_streams> <flood_streams> <pipeline_us> <events>

Server: /paced -> `events` OpenAI-shape chunks (~330 B, one HTTP chunk each) at 20 ms +-30%;
        /flood -> the same chunks back-to-back (flow-controlled only by the client's reads) for as long
        as the paced streams run (`events` x 20 ms). Every event carries the server's CLOCK_MONOTONIC send
        time in its content delta ("T<seconds>"), which is system-wide on Linux, so client-side lateness = decode time - send time.
Client: the plan §3.13 aiohttp session (trust_env=False, auto_decompress=False, identity, bounded headers,
        no redirects) + the shipped SSEDecoder; `pipeline_us` adds a synchronous per-event busy loop that
        stands in for per-event work after decode (output controls, downstream write); 0 = dispatch scope.
Reports paced-stream lateness percentiles, flood events processed, and client CPU.
"""
from __future__ import annotations

import asyncio
import json
import random
import resource
import sys
import time

CHUNK_PAD = "x" * 180


def event(i: int) -> bytes:
    obj = {"id": "chatcmpl-hol", "object": "chat.completion.chunk", "created": 1762905600,
           "model": "gpt-4o-2024-08-06", "choices": [{"index": 0, "delta": {"content": f"T{time.monotonic():.6f}"},
           "logprobs": None, "finish_reason": None}], "pad": CHUNK_PAD, "i": i}
    data = b"data: " + json.dumps(obj, separators=(",", ":")).encode() + b"\n\n"
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


DONE = b"data: [DONE]\n\n"


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    rng = random.Random()
    try:
        while True:
            head = await reader.readuntil(b"\r\n\r\n")
            path = head.split(b" ", 2)[1].decode()
            n = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":", 1)[1])
            if n:
                await reader.readexactly(n)
            kind, events = path.strip("/").split("/")
            events = int(events)
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n")
            if kind == "paced":
                for i in range(events):
                    writer.write(event(i))
                    await writer.drain()
                    await asyncio.sleep(0.020 * rng.uniform(0.7, 1.3))
            else:
                end = time.monotonic() + events * 0.020
                i = 0
                while time.monotonic() < end:
                    for _ in range(16):
                        writer.write(event(i))
                        i += 1
                    await writer.drain()
            writer.write(f"{len(DONE):x}\r\n".encode() + DONE + b"\r\n0\r\n\r\n")
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()


async def server(port: int) -> None:
    srv = await asyncio.start_server(handle, "127.0.0.1", port, backlog=1024)
    print("ready", flush=True)
    async with srv:
        await srv.serve_forever()


_THROTTLED = [0.0]


class Pacer:  # rev 3: plan §3.6.5
    def __init__(self, rate_hz: float, burst: float) -> None:
        self.rate, self.burst, self.tokens, self.t = rate_hz, burst, burst, time.monotonic()
        self.waited = 0.0

    async def take(self, n: float = 1.0) -> None:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now
        self.tokens -= n
        if self.tokens < 0:
            d = -self.tokens / self.rate
            self.waited += d
            _THROTTLED[0] += d  # rev 3: total throttled time, counted as it accrues
            await asyncio.sleep(d)


async def client(port: int, paced: int, flood: int, pipeline_us: int, events: int,
                 variant: str = "none", c_event_us: float = 31.9) -> dict:
    import aiohttp
    from gateway_v2.edge.wire.sse_decode import SSEDecoder

    sess = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=paced + flood + 8, use_dns_cache=False),
        trust_env=False, auto_decompress=False, max_line_size=8190, max_field_size=8190,
        skip_auto_headers=("Accept-Encoding", "User-Agent"), timeout=aiohttp.ClientTimeout(total=120))
    hdr = {"accept-encoding": "identity", "content-type": "application/json"}
    lateness: list[float] = []
    flood_events = [0]
    spin = pipeline_us / 1e6

    def work() -> None:
        if spin:
            end = time.perf_counter() + spin
            while time.perf_counter() < end:
                pass

    e_max = 0.75 / (c_event_us * 1e-6)  # rev 3: the worker's event budget, events/s
    rate = max(50.0, e_max / (paced + flood))
    burst = max(8.0, 0.05 * rate)

    async def one(kind: str) -> None:
        d = SSEDecoder()
        pacer = Pacer(rate, burst) if variant == "pace" else None
        async with sess.post(f"http://127.0.0.1:{port}/{kind}/{events}", data=b"{}", headers=hdr,
                             allow_redirects=False) as r:
            reads = r.content.iter_any() if variant == "none" else r.content.iter_chunked(16384)
            async for chunk in reads:
                for fr in d.feed(chunk):
                    if pacer is not None:
                        await pacer.take(1.0)
                    work()
                    if kind == "paced":
                        for ch, txt in fr.text_deltas:
                            if ch == "content" and txt.startswith("T"):
                                lateness.append(time.monotonic() - float(txt[1:]))
                    else:
                        flood_events[0] += 1
                if variant != "none":
                    await asyncio.sleep(0)  # rev 3: yield after each bounded read

    # warm the pool so connection setup is not measured
    await asyncio.gather(*(one("paced") for _ in range(4)))
    lateness.clear()
    _THROTTLED[0] = 0.0
    cpu0 = time.thread_time()
    w0 = time.monotonic()
    # rev 3: measure over the paced window. A paced flood stream drains the kernel's TCP backlog slowly by
    # design, so the flood readers are cancelled once every paced stream has finished.
    flood_tasks = [asyncio.ensure_future(one("flood")) for _ in range(flood)]
    await asyncio.gather(*(one("paced") for _ in range(paced)))
    wall = time.monotonic() - w0
    cpu = time.thread_time() - cpu0
    for t in flood_tasks:
        t.cancel()
    await asyncio.gather(*flood_tasks, return_exceptions=True)
    await sess.close()
    lateness.sort()

    def pct(p: float) -> float:
        return round(1000 * lateness[min(len(lateness) - 1, int(p * len(lateness)))], 2) if lateness else -1

    return {"paced": paced, "flood": flood, "pipeline_us": pipeline_us, "events": events,
            "paced_events_measured": len(lateness), "lateness_ms_p50": pct(0.50), "lateness_ms_p90": pct(0.90),
            "lateness_ms_p99": pct(0.99), "lateness_ms_max": round(1000 * lateness[-1], 2) if lateness else -1,
            "flood_events": flood_events[0], "flood_events_per_s": round(flood_events[0] / wall),
            "client_cpu_s": round(cpu, 2), "wall_s": round(wall, 2), "util": round(cpu / wall, 2),
            "maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "aiohttp": aiohttp.__version__,
            "variant": variant, "c_event_us": c_event_us, "pace_rate_hz": round(rate, 1), "pace_burst": round(burst, 1),
            "throttled_s_total": round(_THROTTLED[0], 2),
            "cpu_us_per_paced_event": round(1e6 * cpu / max(1, len(lateness) + flood_events[0]), 1)}


if __name__ == "__main__":
    if sys.argv[1] == "server":
        asyncio.run(server(int(sys.argv[2])))
    else:
        try:
            import uvloop
            uvloop.install()
        except ImportError:
            pass
        a = list(map(int, sys.argv[2:7]))
        variant = sys.argv[7] if len(sys.argv) > 7 else "none"
        c_ev = float(sys.argv[8]) if len(sys.argv) > 8 else 31.9
        print(json.dumps(asyncio.run(client(*a, variant=variant, c_event_us=c_ev))), flush=True)
