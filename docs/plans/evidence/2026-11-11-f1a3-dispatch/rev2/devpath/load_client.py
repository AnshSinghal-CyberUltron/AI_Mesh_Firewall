"""F1a-3 rev 2: load generator for the develop dev-profile full-path measurement (devpath/run.sh).

Drives `--requests` streaming /v1/chat/completions requests at `--concurrency` in flight against a
running gateway_v2 (dev profile), reads every byte, and counts a request as qualified only when it
is HTTP 200, carries at least one content delta and ends with `data: [DONE]` with no error frame.
The client runs in its own process; its CPU is NOT part of the measurement (the gateway process's
CPU is read from /proc by run.sh).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

import aiohttp


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--requests", type=int, required=True)
    p.add_argument("--concurrency", type=int, required=True)
    p.add_argument("--model", default="gpt-4o-2024-08-06")
    a = p.parse_args()
    key = os.environ["DEVPATH_GATEWAY_KEY"]
    hdrs = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    stats = {"ok": 0, "bad_status": 0, "error_frame": 0, "no_done": 0, "statuses": {}}
    it = iter(range(a.requests))
    async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=120)) as s:
        async def worker() -> None:
            for i in it:
                body = {"model": a.model, "stream": True, "max_tokens": 512,
                        "messages": [{"role": "user", "content": f"nonce {i:08x}: summarise the ticket history and propose next steps."}]}
                async with s.post(a.url, json=body, headers=hdrs) as r:
                    stats["statuses"][str(r.status)] = stats["statuses"].get(str(r.status), 0) + 1
                    data = await r.read()
                if r.status != 200:
                    stats["bad_status"] += 1
                elif b'"error"' in data:
                    stats["error_frame"] += 1
                elif not data.rstrip().endswith(b"data: [DONE]") or b'"content"' not in data:
                    stats["no_done"] += 1
                else:
                    stats["ok"] += 1
        t0 = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(a.concurrency)))
        stats["wall_s"] = round(time.perf_counter() - t0, 2)
    print(json.dumps(stats))


if __name__ == "__main__":
    asyncio.run(main())
