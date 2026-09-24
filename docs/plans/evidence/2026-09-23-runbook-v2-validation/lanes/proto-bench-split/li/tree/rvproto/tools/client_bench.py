"""Pick the upstream HTTP client by measurement: CPU per request (process_time, the gateway's
binding cost) and latency, aiohttp vs httpx, same pooled-keepalive usage as dispatch/provider.py.
  python tools/client_bench.py http://<synthprov>:8080 out.json
Provider should run with -ttft 0 -itl 0 behaviour via headers (x-synth-ttft-ms/itl-ms = 0)."""

import asyncio
import json
import statistics
import sys
import time

import aiohttp
import httpx
import orjson
import uvloop

BODY = orjson.dumps({"model": "m", "max_tokens": 50, "messages": [{"role": "user", "content": "x" * 2000}]})
SSE_BODY = orjson.dumps({"model": "m", "max_tokens": 50, "stream": True,
                         "messages": [{"role": "user", "content": "x" * 2000}]})
HDR = {"content-type": "application/json", "x-synth-ttft-ms": "0", "x-synth-itl-ms": "0"}


async def run_aiohttp(url: str, n: int, conc: int, stream: bool) -> tuple[float, list[float]]:
    lat: list[float] = []
    conn = aiohttp.TCPConnector(limit=conc * 2)
    async with aiohttp.ClientSession(connector=conn, auto_decompress=False,
                                     skip_auto_headers=("User-Agent", "Accept-Encoding")) as s:
        async def one(i: int) -> None:
            t = time.perf_counter()
            async with s.post(url, data=SSE_BODY if stream else BODY, headers={**HDR, "x-request-id": f"a{i}"}) as r:
                if stream:
                    async for _ in r.content.iter_any():
                        pass
                else:
                    await r.read()
            lat.append(time.perf_counter() - t)
        await asyncio.gather(*(one(i) for i in range(conc)))  # warm pool
        lat.clear()
        c0 = time.process_time()
        for k in range(0, n, conc):
            await asyncio.gather(*(one(k + i) for i in range(conc)))
        return (time.process_time() - c0) / n, lat


async def run_httpx(url: str, n: int, conc: int, stream: bool) -> tuple[float, list[float]]:
    lat: list[float] = []
    limits = httpx.Limits(max_connections=conc * 2, max_keepalive_connections=conc * 2)
    async with httpx.AsyncClient(limits=limits, timeout=60) as s:
        async def one(i: int) -> None:
            t = time.perf_counter()
            h = {**HDR, "x-request-id": f"h{i}"}
            if stream:
                async with s.stream("POST", url, content=SSE_BODY, headers=h) as r:
                    async for _ in r.aiter_raw():
                        pass
            else:
                r = await s.post(url, content=BODY, headers=h)
                r.read()
            lat.append(time.perf_counter() - t)
        await asyncio.gather(*(one(i) for i in range(conc)))
        lat.clear()
        c0 = time.process_time()
        for k in range(0, n, conc):
            await asyncio.gather(*(one(k + i) for i in range(conc)))
        return (time.process_time() - c0) / n, lat


def main() -> None:
    url = sys.argv[1].rstrip("/") + "/v1/chat/completions"
    out = {}
    for lib, fn in (("aiohttp", run_aiohttp), ("httpx", run_httpx)):
        for stream in (False, True):
            for conc in (1, 32):
                n = 2000 if not stream else 600
                cpu, lat = uvloop.run(fn(url, n, conc, stream))
                key = f"{lib}/{'sse50' if stream else 'json'}/c{conc}"
                out[key] = {"cpu_us_per_req": round(cpu * 1e6, 1), "lat_p50_ms": round(statistics.median(lat) * 1e3, 3),
                            "n": n}
                print(key, out[key], flush=True)
    json.dump(out, open(sys.argv[2], "w"), indent=1)


if __name__ == "__main__":
    main()
