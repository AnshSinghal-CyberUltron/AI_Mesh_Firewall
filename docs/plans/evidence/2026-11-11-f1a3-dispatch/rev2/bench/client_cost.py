"""PR #121 Reviewer-B: gateway-side CPU per streamed upstream request, httpx vs aiohttp.

Provenance: written by PR #121 Plan Reviewer B (review 5869723316) and reused for the F1a-3
revision 2 benchmark. Revision 2 adds ONE variant, `aiohttp_design`, and the `--design-host`
option; every other variant is Reviewer B's code, unchanged.

  aiohttp_design   the revision-2 transport as specified in plan section 3.6: hostname URL resolved
                   by a resolve-then-pin AbstractResolver (cached checked set), a socket_factory
                   dial guard (runs for every dial, IP literals included), trust_env=False,
                   auto_decompress=False with `Accept-Encoding: identity` and a refusal of any
                   other Content-Encoding before the body is read, bounded header/line sizes,
                   allow_redirects=False, a content-type check, then the shipped SSEDecoder.

Client process only (the upstream runs in another process). For each variant it drives
`--requests` full requests at `--concurrency` in flight (send, read every byte, decode, close) and
reports, per request:
  thread_us   main-thread CPU (time.thread_time) -- the event-loop thread, i.e. what one gateway
              worker's loop spends. Both clients are single-threaded here (IP-literal URL, no DNS).
  proc_us     whole-process CPU (time.process_time), to show no work hides in other threads.
  util        process CPU / wall: if this approaches 1.0 the client is saturated and per-event
              costs are understated (reads coalesce), so it is reported for every run.

Variants:
  httpx_raw        httpx 0.28 AsyncClient(trust_env=False) stream, aiter_bytes, no decode
  httpx_decode     + the shipped gateway_v2 SSEDecoder (PR worktree), per chunk
  current          the shipped OpenAIUpstreamProvider + httpx_client_factory + SSEDecoder
  aiohttp_raw      aiohttp ClientSession(trust_env=False) post, content.iter_any(), no decode
  aiohttp_decode   + the same SSEDecoder

The request body is the same pre-serialised ~1.4 KB JSON for both clients (bytes), with a fresh
per-request nonce, so JSON encoding cost is identical and outside the comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import statistics
import sys
import time

import httpx

from gateway_v2.dispatch.openai_upstream import (
    BODY_ENDPOINT_KEY,
    BODY_OPENAI_KEY,
    CHAT_ENDPOINT,
    OpenAIUpstreamProvider,
    httpx_client_factory,
)
from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.edge.wire.sse_decode import SSEDecoder

KEY = os.environ["B121_BENCH_KEY"]
SYSTEM = "You are a careful assistant for an enterprise help desk. " * 6
USER = "Summarise the attached ticket history and propose next steps. " * 12


def body_obj(i: int) -> dict:
    return {
        "model": "gpt-4o-2024-08-06",
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": f"nonce-{i:016x} " + USER}],
        "max_tokens": 512, "temperature": 0.2, "stream": True,
        "stream_options": {"include_usage": True},
    }


def body_bytes(i: int) -> bytes:
    return json.dumps(body_obj(i), separators=(",", ":")).encode()


HEADERS = {"authorization": f"Bearer {KEY}", "content-type": "application/json",
           "accept": "text/event-stream"}


async def drive(call, n: int, conc: int) -> dict:
    it = iter(range(n))
    bad = 0
    events = 0

    async def worker() -> None:
        nonlocal bad, events
        for i in it:
            r = await call(i)
            if r <= 0:
                bad += 1
            else:
                events += r

    gc.collect()
    t0, p0, w0 = time.thread_time(), time.process_time(), time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(conc)))
    t1, p1, w1 = time.thread_time(), time.process_time(), time.perf_counter()
    return {"thread_us": (t1 - t0) / n * 1e6, "proc_us": (p1 - p0) / n * 1e6,
            "wall_s": w1 - w0, "util": (p1 - p0) / (w1 - w0), "errors": bad,
            "events_per_req": events / max(n - bad, 1)}


async def measure(name: str, call, args) -> dict:
    await drive(call, args.warmup, args.concurrency)
    runs = [await drive(call, args.requests, args.concurrency) for _ in range(args.repeats)]
    out = {"variant": name,
           "thread_us_median": round(statistics.median(r["thread_us"] for r in runs), 1),
           "proc_us_median": round(statistics.median(r["proc_us"] for r in runs), 1),
           "thread_us_runs": [round(r["thread_us"], 1) for r in runs],
           "util_runs": [round(r["util"], 3) for r in runs],
           "wall_s_runs": [round(r["wall_s"], 2) for r in runs],
           "events_per_req": round(runs[0]["events_per_req"], 1),
           "errors": sum(r["errors"] for r in runs)}
    print(json.dumps(out), file=sys.stderr, flush=True)
    return out


async def main_async(args) -> list[dict]:
    base = f"http://{args.host}:{args.port}"
    url = f"{base}/v1/chat/completions"
    c = args.concurrency
    want = set(args.only.split(",")) if args.only else None
    res: list[dict] = []

    def on(name: str) -> bool:
        return want is None or name in want

    if on("httpx_raw") or on("httpx_decode"):
        client = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=60.0,
                                   limits=httpx.Limits(max_connections=c,
                                                       max_keepalive_connections=c))

        async def hx_raw(i: int) -> int:
            n = 0
            async with client.stream("POST", url, content=body_bytes(i), headers=HEADERS) as r:
                if r.status_code != 200:
                    return -1
                async for chunk in r.aiter_bytes():
                    n += 1
            return n

        async def hx_dec(i: int) -> int:
            d = SSEDecoder()
            n = 0
            async with client.stream("POST", url, content=body_bytes(i), headers=HEADERS) as r:
                if r.status_code != 200:
                    return -1
                async for chunk in r.aiter_bytes():
                    n += len(d.feed(chunk))
            return n if d.finished() and not d.malformed() else -1

        if on("httpx_raw"):
            res.append(await measure("httpx_raw", hx_raw, args))
        if on("httpx_decode"):
            res.append(await measure("httpx_decode", hx_dec, args))
        await client.aclose()

    if on("current"):
        prov = OpenAIUpstreamProvider(
            base_url=f"{base}/v1", decoder_factory=SSEDecoder,
            client_factory=httpx_client_factory(api_key=KEY, timeout_s=60.0, connections=c),
            connections=c)

        async def cur(i: int) -> int:
            req = UpstreamRequest(destination="bench", body={BODY_OPENAI_KEY: body_obj(i),
                                                             BODY_ENDPOINT_KEY: CHAT_ENDPOINT})
            n = 0
            async for ev in prov.open(req):
                if ev.gateway_error is not None:
                    return -1
                n += 1
            return n

        res.append(await measure("current", cur, args))
        await prov.aclose()

    if on("aiohttp_raw") or on("aiohttp_decode") or on("aiohttp_design"):
        import aiohttp

        sess = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=c, limit_per_host=c), trust_env=False,
            timeout=aiohttp.ClientTimeout(total=60.0))

        async def aio_raw(i: int) -> int:
            n = 0
            async with sess.post(url, data=body_bytes(i), headers=HEADERS,
                                 allow_redirects=False) as r:
                if r.status != 200:
                    return -1
                async for chunk in r.content.iter_any():
                    n += 1
            return n

        async def aio_dec(i: int) -> int:
            d = SSEDecoder()
            n = 0
            async with sess.post(url, data=body_bytes(i), headers=HEADERS,
                                 allow_redirects=False) as r:
                if r.status != 200:
                    return -1
                async for chunk in r.content.iter_any():
                    n += len(d.feed(chunk))
            return n if d.finished() and not d.malformed() else -1

        async def aio_dec_coalesce(i: int) -> int:
            # read whatever is buffered, then yield the loop for --coalesce-ms before the next
            # read, so several paced events arrive per wake-up (trades added per-token latency
            # for CPU). Not in the plan; measured to size the option.
            d = SSEDecoder()
            n = 0
            delay = args.coalesce_ms / 1000.0
            async with sess.post(url, data=body_bytes(i), headers=HEADERS,
                                 allow_redirects=False) as r:
                if r.status != 200:
                    return -1
                content = r.content
                while True:
                    chunk = await content.readany()
                    if not chunk:
                        break
                    n += len(d.feed(chunk))
                    await asyncio.sleep(delay)
            return n if d.finished() and not d.malformed() else -1

        if on("aiohttp_design"):
            import ipaddress
            import socket as _socket

            from aiohttp.abc import AbstractResolver

            checked = {ipaddress.ip_address(args.host)}

            class _DialRefused(OSError):
                pass

            def _dial_guard(addr_info):  # type: ignore[no-untyped-def]
                family, type_, proto, _c, sockaddr = addr_info
                if ipaddress.ip_address(sockaddr[0]) not in checked:
                    raise _DialRefused("refused")
                return _socket.socket(family=family, type=type_, proto=proto)

            class _PinnedResolver(AbstractResolver):
                async def resolve(self, host, port=0, family=_socket.AF_INET):  # type: ignore[no-untyped-def]
                    if host != args.design_host:
                        raise OSError("unknown origin")
                    return [{"hostname": host, "host": args.host, "port": port,
                             "family": _socket.AF_INET, "proto": 0,
                             "flags": _socket.AI_NUMERICHOST}]

                async def close(self) -> None:
                    return None

            dsess = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=c, limit_per_host=c, resolver=_PinnedResolver(),
                                               use_dns_cache=False, socket_factory=_dial_guard,
                                               keepalive_timeout=4.0),
                trust_env=False, auto_decompress=False, max_line_size=8190, max_field_size=8190,
                skip_auto_headers=("Accept-Encoding", "User-Agent"),
                timeout=aiohttp.ClientTimeout(total=60.0))
            durl = f"http://{args.design_host}:{args.port}/v1/chat/completions"
            dhdrs = dict(HEADERS, **{"accept-encoding": "identity"})

            async def aio_design(i: int) -> int:
                d = SSEDecoder()
                n = 0
                async with dsess.post(durl, data=body_bytes(i), headers=dhdrs,
                                      allow_redirects=False) as r:
                    if r.status != 200:
                        return -1
                    if r.headers.get("content-encoding", "identity").lower() != "identity":
                        return -1
                    if not r.headers.get("content-type", "").lower().startswith("text/event-stream"):
                        return -1
                    async for chunk in r.content.iter_any():
                        n += len(d.feed(chunk))
                return n if d.finished() and not d.malformed() else -1

            res.append(await measure("aiohttp_design", aio_design, args))
            await dsess.close()

        if on("aiohttp_raw"):
            res.append(await measure("aiohttp_raw", aio_raw, args))
        if on("aiohttp_decode"):
            res.append(await measure("aiohttp_decode", aio_dec, args))
        if on("aiohttp_decode_coalesce"):
            res.append(await measure(f"aiohttp_decode_coalesce{args.coalesce_ms:g}ms",
                                     aio_dec_coalesce, args))
        await sess.close()
    return res


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=19050)
    p.add_argument("--requests", type=int, default=400)
    p.add_argument("--warmup", type=int, default=64)
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--concurrency", type=int, default=64)
    p.add_argument("--only", default="")
    p.add_argument("--label", default="")
    p.add_argument("--coalesce-ms", type=float, default=50.0)
    p.add_argument("--design-host", default="upstream.bench.test")
    args = p.parse_args()
    import uvloop
    uvloop.install()
    t0 = time.perf_counter()
    res = asyncio.run(main_async(args))
    import aiohttp
    print(json.dumps({"label": args.label, "python": sys.version.split()[0],
                      "httpx": httpx.__version__, "aiohttp": aiohttp.__version__,
                      "concurrency": args.concurrency, "requests": args.requests,
                      "repeats": args.repeats, "maxrss_kib": __import__("resource").getrusage(__import__("resource").RUSAGE_SELF).ru_maxrss, "elapsed_s": round(time.perf_counter() - t0, 1),
                      "results": res}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
