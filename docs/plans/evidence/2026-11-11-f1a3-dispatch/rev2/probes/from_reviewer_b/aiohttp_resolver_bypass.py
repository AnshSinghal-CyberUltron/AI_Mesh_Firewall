# Provenance: written by PR #121 Plan Reviewer B (review 5869723316) on the review host (probes/) and
# copied unchanged below this header for the F1a-3 revision 2 evidence, with credit.
"""PR #121 B probe: does an aiohttp custom AbstractResolver (the plan's §6.3 resolve-then-pin
mechanism for Q5) see IP-literal URLs?  Plan §5: "Gateway connect: resolve-then-pin, every new
connection".  The resolver below REFUSES EVERY name (raises OSError).  A tiny local server listens
on 127.0.0.1; if a request to an IP-literal URL still reaches it, the resolver was bypassed.
Also checks: allow_redirects default, trust_env default, auto_decompress default.
"""
import asyncio
import json
import socket

import aiohttp
from aiohttp.abc import AbstractResolver


class RefuseAll(AbstractResolver):
    calls: list[str] = []

    async def resolve(self, host, port=0, family=socket.AF_INET):
        RefuseAll.calls.append(host)
        raise OSError(f"refused by policy: {host}")

    async def close(self):
        return None


async def main() -> None:
    hits = 0

    async def handle(reader, writer):
        nonlocal hits
        await reader.readuntil(b"\r\n\r\n")
        hits += 1
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\nconnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    srv4 = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = srv4.sockets[0].getsockname()[1]
    srv6 = None
    try:
        srv6 = await asyncio.start_server(handle, "::1", port)
    except OSError:
        pass
    out = {"aiohttp": aiohttp.__version__, "rows": []}
    conn = aiohttp.TCPConnector(resolver=RefuseAll())
    async with aiohttp.ClientSession(connector=conn, trust_env=False) as s:
        for url in (f"http://localhost:{port}/v1/chat/completions",
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    f"http://[::1]:{port}/v1/chat/completions",
                    f"http://[::ffff:127.0.0.1]:{port}/v1/chat/completions"):
            before_calls, before_hits = len(RefuseAll.calls), hits
            try:
                async with s.post(url, data=b"{}", allow_redirects=False) as r:
                    status = r.status
            except Exception as e:  # noqa: BLE001
                status = type(e).__name__
            out["rows"].append({"url_host": url.split("/")[2].rsplit(":", 1)[0],
                                "resolver_called": len(RefuseAll.calls) > before_calls,
                                "reached_server": hits > before_hits, "result": status})
    import inspect
    sig = inspect.signature(aiohttp.ClientSession._request)
    out["defaults"] = {
        "ClientSession._request allow_redirects": sig.parameters["allow_redirects"].default,
        "ClientSession(trust_env) default": inspect.signature(aiohttp.ClientSession.__init__).parameters["trust_env"].default,
        "ClientSession(auto_decompress) default": inspect.signature(aiohttp.ClientSession.__init__).parameters["auto_decompress"].default,
        "TCPConnector(use_dns_cache, ttl_dns_cache)": (
            inspect.signature(aiohttp.TCPConnector.__init__).parameters["use_dns_cache"].default,
            inspect.signature(aiohttp.TCPConnector.__init__).parameters["ttl_dns_cache"].default),
    }
    print(json.dumps(out, indent=1))
    srv4.close()
    if srv6:
        srv6.close()


asyncio.run(main())
