"""P13: client cancellation at three points, and what the audit trail says afterwards.

 X1 cancel during INPUT guard work (16-window prompt, client gone after 0.3 s)
 X2 cancel after DISPATCH of a non-stream call (provider TTFT 3 s, client gone after 1 s)
 X3 cancel mid-SSE (client gone after ~5 content chunks)
For each: gateway counters before/after, provider-side record, and the audit records (rv:audit:org-a)
for the request id. T03 exit: 'All started requests remain in failure/deadline accounting.'
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid

import aiohttp
import redis

OUT, REC = sys.argv[1], sys.argv[2]
R = redis.Redis(port=16479)
BASE = "http://127.0.0.1:8480"
H = {"authorization": "Bearer sk-rv-org-a-0001"}
SENT = ("The regional team reviewed the quarterly plan and agreed that the warehouse schedule, "
        "the delivery routes and the training calendar should stay the same for the next season. ")


def counters() -> dict:
    import urllib.request
    d = json.load(urllib.request.urlopen(f"{BASE}/metrics.json"))
    keep = ("client_disconnects", "guard_windows", "guard_batches", "guard_cancelled_skipped", "provider_calls",
            "audit_enqueued", "audit_dropped")
    return {k: d["count"].get(k, 0) for k in keep}


async def cancel_after(rid: str, body: dict, hdr: dict, delay: float | None, chunks: int | None) -> None:
    async with aiohttp.ClientSession() as s:
        try:
            async with s.post(f"{BASE}/v1/chat/completions", json=body, headers={**H, "x-request-id": rid, **hdr},
                              timeout=aiohttp.ClientTimeout(total=delay) if delay else None) as r:
                n = 0
                async for _ in r.content.iter_any():
                    n += 1
                    if chunks and n >= chunks:
                        return  # leaving the context manager closes the connection mid-stream
        except asyncio.TimeoutError:
            return


async def case(label: str, body: dict, hdr: dict, delay: float | None = None, chunks: int | None = None) -> dict:
    rid = f"p13-{label}-{uuid.uuid4().hex[:6]}"
    c0 = counters()
    await cancel_after(rid, body, hdr, delay, chunks)
    await asyncio.sleep(6)  # let guard work / provider / audit writer finish
    c1 = counters()
    recs = [json.loads(v[b"r"]) for _, v in R.xrange("rv:audit:org-a", count=1_000_000)]
    mine = [{k: r[k] for k in ("phase", "disposition", "verification", "stages")} for r in recs if r["request_id"] == rid]
    prov = [json.loads(l) for l in open(REC) if rid in l]
    return {"case": label, "rid": rid, "counter_delta": {k: c1[k] - c0[k] for k in c0},
            "audit_records": mine, "provider_records": [{k: p.get(k) for k in ("event", "stream", "canary_hits")} for p in prov]}


async def main() -> None:
    out = [
        await case("X1_cancel_during_input_guard", {"model": "gpt-4o-mini", "messages": [
            {"role": "user", "content": SENT * 222}]}, {"x-synth-tokens": "2"}, delay=0.3),
        await case("X2_cancel_after_dispatch_json", {"model": "gpt-4o-mini", "messages": [
            {"role": "user", "content": "hello"}]}, {"x-synth-ttft-ms": "3000", "x-synth-tokens": "2"}, delay=1.0),
        await case("X3_cancel_mid_sse", {"model": "gpt-4o-mini", "stream": True, "messages": [
            {"role": "user", "content": "hello"}]}, {"x-synth-tokens": "400", "x-synth-itl-ms": "20"}, chunks=5),
    ]
    for o in out:
        print(json.dumps(o))
    json.dump(out, open(OUT, "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
