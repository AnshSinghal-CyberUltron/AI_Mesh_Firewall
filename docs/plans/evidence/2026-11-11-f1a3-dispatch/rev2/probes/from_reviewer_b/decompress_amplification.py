# Provenance: written by PR #121 Plan Reviewer B (review 5869723316) on the review host (probes/) and
# copied unchanged below this header for the F1a-3 revision 2 evidence, with credit.
"""PR #121 B probe: decompression amplification from a hostile custom upstream.

A malicious org admin controls its own custom base URL (plan §2.11 "Malicious org admin", §4.1:
custom public hosts unrestricted by default). The upstream answers 200 text/event-stream with
Content-Encoding: gzip, ~200 KiB on the wire that inflates to ~200 MiB. The plan's only byte bound
is max_upstream_event_bytes() in the SSE decoder (§3.4), which runs AFTER the client library has
already inflated each read. This probe measures, per client, the largest single chunk the library
hands to the caller and the process peak RSS, with the gateway's decoder fed as in the plan.

RLIMIT_AS is capped so the probe cannot hurt the shared host.
"""
import asyncio
import json
import os
import resource
import sys

resource.setrlimit(resource.RLIMIT_AS, (3 * 2**30, 3 * 2**30))

PORT = int(sys.argv[1])
KEY = os.environ["B121_BENCH_KEY"]
URL = f"http://127.0.0.1:{PORT}/v1/chat/completions"
H = {"authorization": f"Bearer {KEY}", "content-type": "application/json"}
BODY = b'{"model":"m","messages":[{"role":"user","content":"hi"}],"stream":true}'


def rss_mib() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20


async def run_httpx() -> dict:
    import httpx
    biggest = total = 0
    async with httpx.AsyncClient(trust_env=False) as c:
        sent_ae = c.headers.get("accept-encoding")
        async with c.stream("POST", URL, content=BODY, headers=H) as r:
            async for chunk in r.aiter_bytes():
                biggest, total = max(biggest, len(chunk)), total + len(chunk)
    return {"client": f"httpx {httpx.__version__}", "default_accept_encoding": sent_ae,
            "wire_bytes": int(r.headers.get("content-length", 0)),
            "inflated_total_mib": round(total / 2**20, 1),
            "largest_single_chunk_mib": round(biggest / 2**20, 1)}


async def run_aiohttp() -> dict:
    import aiohttp
    biggest = total = 0
    async with aiohttp.ClientSession(trust_env=False) as s:
        async with s.post(URL, data=BODY, headers=H, allow_redirects=False) as r:
            ae = r.request_info.headers.get("Accept-Encoding")
            async for chunk in r.content.iter_any():
                biggest, total = max(biggest, len(chunk)), total + len(chunk)
            wire = int(r.headers.get("content-length", 0))
    return {"client": f"aiohttp {aiohttp.__version__}", "default_accept_encoding": ae,
            "wire_bytes": wire, "inflated_total_mib": round(total / 2**20, 1),
            "largest_single_chunk_mib": round(biggest / 2**20, 1)}


which = sys.argv[2]
r0 = rss_mib()
res = asyncio.run(run_httpx() if which == "httpx" else run_aiohttp())
res["rss_start_mib"] = round(r0, 1)
res["peak_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
print(json.dumps(res))
