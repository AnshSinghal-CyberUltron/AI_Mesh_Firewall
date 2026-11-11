"""F1a-3 dispatch client benchmark: CPU and memory a gateway worker spends per upstream request.

Prototype, not product code. Runs against `upstream.py` (a pre-rendered, keep-alive recording
upstream in ANOTHER process), so the numbers are the gateway side only. Every figure is the
client process's CPU time (`time.process_time`, all threads), never wall time, because the host
is shared. The upstream's own CPU is reported separately from /proc.

Variants (each a full request: send, read every byte, close the stream):

  current        the shipped dispatch path: `OpenAIUpstreamProvider` + `httpx_client_factory`
                 + the real `SSEDecoder` (gateway_v2 at this worktree)
  design         the F1a-3 design shape: one pooled client per origin with trust_env=False,
                 follow_redirects=False, `PinnedTransport` (resolve-then-pin, cached
                 resolution), per-request AES-256-GCM credential decrypt into the Authorization
                 header, the real `SSEDecoder`
  httpx_raw      httpx stream, bytes read, no SSE decode (isolates the client)
  aiohttp_raw    aiohttp stream, bytes read, no SSE decode (the alternative client)
  aiohttp_decode aiohttp + the real `SSEDecoder`
  *_json         the non-streaming (embeddings-like) shape: one JSON body read and parsed
  design_tls     `design` over TLS 1.3 with a pooled, reused connection (SNI + verification
                 against the name while dialling the pinned IP)
  tls_handshake  `design` over TLS with keep-alive off: one full handshake per request

Also: `getaddrinfo` (per-call cost of resolving a name on the loop, i.e. what an uncached
resolve-per-request would add) and pooled-connection memory (plain and TLS).

    python client_bench.py --host 172.17.0.1 --port 18950 --tls-port 18951 --ca ca.pem

The bearer is a random test value in F1A3_BENCH_KEY, shared with the upstream; never a real key.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import ssl
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[4] / "gateway_v2"))

import httpx  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from gateway_v2.dispatch.openai_upstream import (  # noqa: E402
    BODY_ENDPOINT_KEY,
    BODY_OPENAI_KEY,
    CHAT_ENDPOINT,
    OpenAIUpstreamProvider,
    httpx_client_factory,
)
from gateway_v2.dispatch.pinned_transport import PinnedTransport  # noqa: E402
from gateway_v2.dispatch.provider import UpstreamRequest  # noqa: E402
from gateway_v2.edge.wire.sse_decode import SSEDecoder  # noqa: E402

KEY = os.environ["F1A3_BENCH_KEY"]
Call = Callable[[int], Awaitable[int]]


def body(i: int, stream: bool) -> dict[str, object]:
    return {
        "model": "bench-model",
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": f"nonce-{i:016x} say pong " * 4},
        ],
        "max_tokens": 64,
        "stream": stream,
        **({"stream_options": {"include_usage": True}} if stream else {}),
    }


def rss_bytes() -> int:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def proc_cpu_s(pid: int) -> float:
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


async def drive(call: Call, n: int, concurrency: int, upstream_pid: int) -> dict[str, float]:
    counter = iter(range(n))
    errors = 0

    async def worker() -> None:
        nonlocal errors
        for i in counter:
            if await call(i) <= 0:
                errors += 1

    gc.collect()
    up0 = proc_cpu_s(upstream_pid) if upstream_pid else 0.0
    cpu0, wall0 = time.process_time(), time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    cpu1, wall1 = time.process_time(), time.perf_counter()
    up1 = proc_cpu_s(upstream_pid) if upstream_pid else 0.0
    return {
        "cpu_us_per_req": (cpu1 - cpu0) / n * 1e6,
        "wall_rps": n / (wall1 - wall0),
        "upstream_cpu_us_per_req": (up1 - up0) / n * 1e6,
        "errors": float(errors),
    }


async def measure(
    name: str, call: Call, args: argparse.Namespace, upstream_pid: int
) -> dict[str, object]:
    await drive(call, args.warmup, args.concurrency, upstream_pid)
    runs = [
        await drive(call, args.requests, args.concurrency, upstream_pid)
        for _ in range(args.repeats)
    ]
    cpu = [r["cpu_us_per_req"] for r in runs]
    return {
        "variant": name,
        "cpu_us_per_req_median": round(statistics.median(cpu), 1),
        "cpu_us_per_req_min": round(min(cpu), 1),
        "cpu_us_per_req_runs": [round(c, 1) for c in cpu],
        "wall_rps_median": round(statistics.median(r["wall_rps"] for r in runs), 0),
        "upstream_cpu_us_per_req_median": round(
            statistics.median(r["upstream_cpu_us_per_req"] for r in runs), 1
        ),
        "errors": sum(r["errors"] for r in runs),
    }


class CachedResolver:
    """Resolve-then-pin with a bounded TTL: the design's per-origin resolution cache."""

    def __init__(self, table: dict[str, str]) -> None:
        self.table = table
        self.cache: dict[tuple[str, int | None], tuple[float, tuple[str, ...]]] = {}

    async def __call__(self, host: str, port: int | None) -> tuple[str, ...]:
        now = time.monotonic()
        hit = self.cache.get((host, port))
        if hit is not None and hit[0] > now:
            return hit[1]
        addrs = (self.table.get(host, host),)
        self.cache[(host, port)] = (now + 30.0, addrs)
        return addrs


def sealed_key() -> tuple[AESGCM, bytes, bytes, bytes]:
    dek = AESGCM(AESGCM.generate_key(bit_length=256))
    nonce, aad = os.urandom(12), b"\x00\x00\x00\x0bamf.cred.v1" + b"x" * 132
    return dek, nonce, dek.encrypt(nonce, KEY.encode(), aad), aad


def design_client(
    origin_host: str, port: int, connections: int, *, tls_ca: str | None, keepalive: bool
) -> httpx.AsyncClient:
    verify: ssl.SSLContext | bool = False
    if tls_ca:
        verify = ssl.create_default_context(cafile=tls_ca)
    inner = httpx.AsyncHTTPTransport(
        verify=verify,
        limits=httpx.Limits(
            max_connections=connections,
            max_keepalive_connections=connections if keepalive else 0,
        ),
        trust_env=False,
    )
    resolver = CachedResolver({"bench.upstream.test": ARGS.host})
    transport = PinnedTransport(resolver, frozenset({f"{origin_host}:{port}"}), inner)
    return httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(30.0, pool=5.0),
    )


async def main_async(args: argparse.Namespace) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    base = f"http://{args.host}:{args.port}"
    pid, tls_pid = args.upstream_pid, args.tls_upstream_pid
    c = args.concurrency
    only = set(args.only.split(",")) if args.only else None

    def want(name: str) -> bool:
        return only is None or name in only

    if want("current"):
        provider = OpenAIUpstreamProvider(
            base_url=f"{base}/v1",
            decoder_factory=SSEDecoder,
            client_factory=httpx_client_factory(api_key=KEY, timeout_s=30.0, connections=c),
            connections=c,
        )

        async def current(i: int) -> int:
            req = UpstreamRequest(
                destination="bench",
                body={BODY_OPENAI_KEY: body(i, True), BODY_ENDPOINT_KEY: CHAT_ENDPOINT},
            )
            n = 0
            async for event in provider.open(req):
                if event.gateway_error is not None:
                    return -1
                n += 1
            return n

        results.append(await measure("current", current, args, pid))
        await provider.aclose()

    dek, nonce, ct, aad = sealed_key()

    async def design_call(client: httpx.AsyncClient, url: str, i: int, stream: bool) -> int:
        bearer = b"Bearer " + dek.decrypt(nonce, ct, aad)
        headers = {"authorization": bearer.decode("ascii")}
        if not stream:
            resp = await client.post(url, json=body(i, False), headers=headers)
            return 1 if resp.status_code == 200 and json.loads(resp.content)["choices"] else -1
        decoder = SSEDecoder()
        n = 0
        async with client.stream("POST", url, json=body(i, True), headers=headers) as resp:
            if resp.status_code != 200:
                return -1
            async for raw in resp.aiter_bytes():
                n += len(decoder.feed(raw))
        return n if decoder.finished() and not decoder.malformed() else -1

    if want("design") or want("design_json"):
        client = design_client(args.host, args.port, c, tls_ca=None, keepalive=True)
        url = f"{base}/v1/chat/completions"
        if want("design"):
            results.append(
                await measure("design", lambda i: design_call(client, url, i, True), args, pid)
            )
        if want("design_json"):
            results.append(
                await measure(
                    "design_json", lambda i: design_call(client, url, i, False), args, pid
                )
            )
        await client.aclose()

    if want("httpx_raw") or want("httpx_json"):
        raw_client = httpx.AsyncClient(
            trust_env=False,
            limits=httpx.Limits(max_connections=c, max_keepalive_connections=c),
            headers={"authorization": f"Bearer {KEY}"},
        )
        url = f"{base}/v1/chat/completions"

        async def httpx_raw(i: int) -> int:
            async with raw_client.stream("POST", url, json=body(i, True)) as resp:
                total = 0
                async for raw in resp.aiter_bytes():
                    total += len(raw)
            return total

        async def httpx_json(i: int) -> int:
            resp = await raw_client.post(url, json=body(i, False))
            return 1 if json.loads(resp.content)["choices"] else -1

        if want("httpx_raw"):
            results.append(await measure("httpx_raw", httpx_raw, args, pid))
        if want("httpx_json"):
            results.append(await measure("httpx_json", httpx_json, args, pid))
        await raw_client.aclose()

    if want("aiohttp_raw") or want("aiohttp_decode") or want("aiohttp_json"):
        import aiohttp

        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=c),
            headers={"authorization": f"Bearer {KEY}"},
            trust_env=False,
        )
        url = f"{base}/v1/chat/completions"

        async def aio_raw(i: int) -> int:
            async with session.post(url, json=body(i, True)) as resp:
                total = 0
                async for raw in resp.content.iter_any():
                    total += len(raw)
            return total

        async def aio_decode(i: int) -> int:
            decoder = SSEDecoder()
            n = 0
            async with session.post(url, json=body(i, True)) as resp:
                async for raw in resp.content.iter_any():
                    n += len(decoder.feed(raw))
            return n if decoder.finished() else -1

        async def aio_json(i: int) -> int:
            async with session.post(url, json=body(i, False)) as resp:
                data = json.loads(await resp.read())
            return 1 if data["choices"] else -1

        if want("aiohttp_raw"):
            results.append(await measure("aiohttp_raw", aio_raw, args, pid))
        if want("aiohttp_decode"):
            results.append(await measure("aiohttp_decode", aio_decode, args, pid))
        if want("aiohttp_json"):
            results.append(await measure("aiohttp_json", aio_json, args, pid))
        await session.close()

    if args.tls_port and (want("design_tls") or want("tls_handshake")):
        url = f"https://bench.upstream.test:{args.tls_port}/v1/chat/completions"
        if want("design_tls"):
            client = design_client(
                "bench.upstream.test", args.tls_port, c, tls_ca=args.ca, keepalive=True
            )
            results.append(
                await measure("design_tls", lambda i: design_call(client, url, i, True), args,
                              tls_pid)
            )
            await client.aclose()
        if want("tls_handshake"):
            client = design_client(
                "bench.upstream.test", args.tls_port, c, tls_ca=args.ca, keepalive=False
            )
            saved = args.requests
            args.requests = max(saved // 4, 500)
            results.append(
                await measure("tls_handshake", lambda i: design_call(client, url, i, True), args,
                              tls_pid)
            )
            args.requests = saved
            await client.aclose()

    if want("getaddrinfo"):
        loop = asyncio.get_running_loop()
        gc.collect()
        cpu0 = time.process_time()
        n = 2000
        for _ in range(n):
            await loop.getaddrinfo("localhost", 443)
        results.append(
            {"variant": "getaddrinfo", "cpu_us_per_call": round((time.process_time() - cpu0)
                                                                / n * 1e6, 1)}
        )

    if want("memory"):
        for label, host, port, tls in (
            ("plain", args.host, args.port, None),
            ("tls", "bench.upstream.test", args.tls_port, args.ca),
        ):
            if tls and not args.tls_port:
                continue
            conns = args.memory_connections
            gc.collect()
            before = rss_bytes()
            client = design_client(host, port, conns, tls_ca=tls, keepalive=True)
            scheme = "https" if tls else "http"
            url = f"{scheme}://{host}:{port}/v1/chat/completions"
            await asyncio.gather(*(design_call(client, url, i, True) for i in range(conns)))
            gc.collect()
            pool = getattr(getattr(client._transport, "_inner", None), "_pool", None)  # noqa: SLF001
            open_conns = len(pool.connections) if pool is not None else -1
            after = rss_bytes()
            results.append(
                {
                    "variant": f"memory_{label}",
                    "pooled_connections": open_conns,
                    "rss_bytes_per_connection": round((after - before) / max(open_conns, 1), 0),
                }
            )
            await client.aclose()
    return results


ARGS = argparse.Namespace()


def main() -> int:
    global ARGS
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="172.17.0.1")
    parser.add_argument("--port", type=int, default=18950)
    parser.add_argument("--tls-port", type=int, default=0)
    parser.add_argument("--ca", default="")
    parser.add_argument("--upstream-pid", type=int, default=0)
    parser.add_argument("--tls-upstream-pid", type=int, default=0)
    parser.add_argument("--requests", type=int, default=6000)
    parser.add_argument("--warmup", type=int, default=300)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--memory-connections", type=int, default=256)
    parser.add_argument("--only", default="")
    ARGS = parser.parse_args()
    import uvloop

    uvloop.install()
    results = asyncio.run(main_async(ARGS))
    meta = {
        "python": sys.version.split()[0],
        "httpx": httpx.__version__,
        "concurrency": ARGS.concurrency,
        "requests_per_run": ARGS.requests,
        "repeats": ARGS.repeats,
        "unit": "client process CPU microseconds per request (process_time, all threads)",
    }
    print(json.dumps({"meta": meta, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
