"""P17: admission slots are one shared pool (no per-tenant share) and the fd-derived connection
budget ignores that every proxied request holds TWO sockets (client + upstream).

rvproto started with RLIMIT_NOFILE=256 (1 worker) -> connection_budget = floor((256-1)*0.75) = 191
= inflight cap = uvicorn limit_concurrency = provider pool. Tenant A opens up to 190 long-lived
streams (1 token/s); then tenant B sends 10 short requests. Records A's stream outcomes, B's
outcomes, and the worker's open fd count at the peak.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import sys
import time

import aiohttp

ST, OUT = sys.argv[1], sys.argv[2]
N_A = int(sys.argv[3]) if len(sys.argv) > 3 else 190


def worker_fds() -> int:
    launcher = int(open(f"{ST}/run/rvproto.pid").read().strip())
    pids = [int(k) for k in os.popen(f"pgrep -P {launcher}").read().split()]
    return sum(len(os.listdir(f"/proc/{p}/fd")) for p in pids)


async def a_stream(s: aiohttp.ClientSession, i: int, started: collections.Counter, stop: asyncio.Event) -> str:
    h = {"authorization": "Bearer sk-rv-org-a-0001", "x-synth-edge": "infinite", "x-synth-itl-ms": "1000",
         "x-synth-ttft-ms": "0"}
    try:
        async with s.post("http://127.0.0.1:8480/v1/chat/completions", headers=h,
                          json={"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": "go"}]}) as r:
            if r.status != 200:
                return f"{r.status}:{(await r.text())[:80]}"
            started["streaming"] += 1
            async for _ in r.content.iter_any():
                if stop.is_set():
                    return "200:streaming_until_stopped"
            return "200:ended"
    except Exception as e:  # noqa: BLE001
        return f"exc:{type(e).__name__}"


async def b_request(s: aiohttp.ClientSession) -> str:
    try:
        async with s.post("http://127.0.0.1:8480/v1/chat/completions",
                          headers={"authorization": "Bearer sk-rv-org-b-0001", "x-synth-tokens": "2"},
                          json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                          timeout=aiohttp.ClientTimeout(total=15)) as r:
            t = await r.text()
            return f"{r.status}:{t[:90]}"
    except Exception as e:  # noqa: BLE001
        return f"exc:{type(e).__name__}"


async def main() -> None:
    started: collections.Counter = collections.Counter()
    stop = asyncio.Event()
    conn = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=120)) as s:
        a_tasks = [asyncio.create_task(a_stream(s, i, started, stop)) for i in range(N_A)]
        await asyncio.sleep(8)
        peak_fds = worker_fds()
        b = await asyncio.gather(*[b_request(s) for _ in range(10)])
        stop.set()
        a = await asyncio.gather(*a_tasks)
    res = {"rlimit_nofile": 256, "a_streams_requested": N_A, "a_streaming_at_peak": started["streaming"],
           "worker_open_fds_at_peak": peak_fds, "a_outcomes": dict(collections.Counter(x[:60] for x in a)),
           "b_outcomes": dict(collections.Counter(x[:120] for x in b))}
    print(json.dumps(res, indent=1))
    json.dump(res, open(OUT, "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
