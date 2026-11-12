"""F1a-3 rev 2: client-side RSS per in-flight upstream stream (aiohttp design transport + SSEDecoder,
and httpx + SSEDecoder), for the memory term of stream_slots() (plan 3.6.2) and section 6.7.

Starts N concurrent slow streams against a local paced upstream (Reviewer B's sse_upstream.py, run
separately), waits until every stream has decoded >= 3 events, then reads VmRSS. Per-stream bytes =
(RSS at N - RSS at N0) / (N - N0). Plain HTTP on loopback (TLS adds per-connection state: revision 1
measured httpx pooled TLS 36,416 B against plain 22,240 B).
Usage: python stream_memory.py <port> <client: aiohttp|httpx> <n0> <n>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from gateway_v2.edge.wire.sse_decode import SSEDecoder

KEY = os.environ["B121_BENCH_KEY"]
HDRS = {"authorization": f"Bearer {KEY}", "content-type": "application/json",
        "accept": "text/event-stream", "accept-encoding": "identity"}
BODY = json.dumps({"model": "m", "stream": True, "messages": [{"role": "user", "content": "x" * 1200}]}).encode()


def rss() -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return -1


async def run(port: int, client: str, n0: int, n: int) -> dict:
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    progress: list[int] = []
    release = asyncio.Event()

    if client == "aiohttp":
        import aiohttp
        sess = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=n + 8), trust_env=False,
                                     auto_decompress=False, skip_auto_headers=("Accept-Encoding",))

        async def one(i: int) -> None:
            d = SSEDecoder()
            async with sess.post(url, data=BODY, headers=HDRS, allow_redirects=False) as r:
                async for chunk in r.content.iter_any():
                    progress[i] += len(d.feed(chunk))
                    if release.is_set():
                        break
        close = sess.close
    else:
        import httpx
        cl = httpx.AsyncClient(trust_env=False, limits=httpx.Limits(max_connections=n + 8,
                               max_keepalive_connections=n + 8), timeout=120)

        async def one(i: int) -> None:
            d = SSEDecoder()
            async with cl.stream("POST", url, content=BODY, headers=HDRS) as r:
                async for chunk in r.aiter_bytes():
                    progress[i] += len(d.feed(chunk))
                    if release.is_set():
                        break
        close = cl.aclose

    out = {"client": client}
    for label, count in (("n0", n0), ("n", n)):
        release.clear()
        progress[:] = [0] * count
        tasks = [asyncio.create_task(one(i)) for i in range(count)]
        while min(progress, default=3) < 3:
            await asyncio.sleep(0.2)
        await asyncio.sleep(1.0)
        out[f"rss_at_{label}_{count}"] = rss()
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    out["bytes_per_stream"] = round((out[f"rss_at_n_{n}"] - out[f"rss_at_n0_{n0}"]) / (n - n0))
    await close()
    return out


if __name__ == "__main__":
    import uvloop
    uvloop.install()
    print(json.dumps(asyncio.run(run(int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])))))
