# Provenance: written by PR #121 Plan Reviewer A (review 5869433944, scripts/paced_bench.py on the
# review host) and reused UNCHANGED below this line for the F1a-3 revision 2 reconciliation run.
"""Reviewer A check of the plan's Q5 evidence: httpx vs aiohttp client CPU per streamed request,
with the upstream writing all events at once (the plan's upstream.py) versus pacing them
(each SSE event a separate write, `gap_ms` apart, like a real token stream).

usage: paced_bench.py server <port> <gap_ms>      (run in its own process)
       paced_bench.py client <port> <variant> <n> <conc>
Prints JSON: client CPU us/request (process_time, all threads), body reads per request.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

EVENTS = [
    b'data: {"id":"c","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"tok%02d "}}]}\n\n'
    % i
    for i in range(20)
]
EVENTS.append(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
EVENTS.append(b'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":20,"total_tokens":29}}\n\n')
EVENTS.append(b"data: [DONE]\n\n")


def chunk(b: bytes) -> bytes:
    return b"%x\r\n" % len(b) + b + b"\r\n"


async def server(port: int, gap_ms: float) -> None:
    head = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n"
    allatonce = head + b"".join(chunk(e) for e in EVENTS) + b"0\r\n\r\n"

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                h = await reader.readuntil(b"\r\n\r\n")
                n = 0
                for line in h.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        n = int(line.split(b":")[1])
                if n:
                    await reader.readexactly(n)
                if gap_ms <= 0:
                    writer.write(allatonce)
                    await writer.drain()
                    continue
                writer.write(head)
                for e in EVENTS:
                    await asyncio.sleep(gap_ms / 1000)
                    writer.write(chunk(e))
                    await writer.drain()
                writer.write(b"0\r\n\r\n")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", port, backlog=1024)
    print("ready", flush=True)
    async with srv:
        await srv.serve_forever()


async def client(port: int, variant: str, n: int, conc: int) -> dict[str, object]:
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}], "stream": True}
    reads = 0
    if variant.startswith("httpx"):
        import httpx

        c = httpx.AsyncClient(
            trust_env=False,
            limits=httpx.Limits(max_connections=conc, max_keepalive_connections=conc),
            timeout=30.0,
        )

        async def one() -> None:
            nonlocal reads
            async with c.stream("POST", url, json=body) as r:
                async for _ in r.aiter_bytes():
                    reads += 1

        close = c.aclose
    else:
        import aiohttp

        s = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=conc), trust_env=False)

        async def one() -> None:
            nonlocal reads
            async with s.post(url, json=body) as r:
                async for _ in r.content.iter_any():
                    reads += 1

        close = s.close

    sem = asyncio.Semaphore(conc)

    async def guarded() -> None:
        async with sem:
            await one()

    await asyncio.gather(*(guarded() for _ in range(min(100, n))))  # warm-up
    reads = 0
    t0, w0 = time.process_time(), time.perf_counter()
    await asyncio.gather(*(guarded() for _ in range(n)))
    cpu, wall = time.process_time() - t0, time.perf_counter() - w0
    await close()
    return {"variant": variant, "n": n, "conc": conc, "cpu_us_per_req": round(cpu / n * 1e6, 1),
            "reads_per_req": round(reads / n, 2), "wall_s": round(wall, 2)}


if __name__ == "__main__":
    import uvloop

    uvloop.install()
    if sys.argv[1] == "server":
        asyncio.run(server(int(sys.argv[2]), float(sys.argv[3])))
    else:
        print(json.dumps(asyncio.run(client(int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), int(sys.argv[5])))))
