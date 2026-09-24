"""P2 kill switch on the LIVE 4-worker rvproto (:8493, redis via fault proxy :26380).

  engaged <global|org|model>  flip ON while long SSE streams are in flight
  outage                      store outage (connections reset, backend closed) while streams are in
                              flight; KS goes stale past the 5 s ceiling; then restore
Each in-flight stream: devprov 300 tokens x 30 ms ITL (~9 s). New-request sweeps run concurrently.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

import httpx
import redis

from pc import BASE, KEY_B, MODEL, log

CTL = "http://127.0.0.1:26390"


def ctl(path: str) -> dict:
    return json.loads(urllib.request.urlopen(CTL + path, timeout=5).read())


async def stream(i: int, t_flip: list[float], out: dict) -> None:
    body = {"model": MODEL, "stream": True, "max_tokens": 300,
            "messages": [{"role": "user", "content": f"long answer please #{i}"}]}
    hdr = {"authorization": f"Bearer {KEY_B}", "x-synth-tokens": "300", "x-synth-itl-ms": "30",
           "x-request-id": f"ks-stream-{i}-{int(time.time())}"}
    chunks: list[float] = []
    status = None
    done = False
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        async with c.stream("POST", "/v1/chat/completions", json=body, headers=hdr) as resp:
            status = resp.status_code
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    chunks.append(time.time())
                    if line.strip() == "data: [DONE]":
                        done = True
    tf = t_flip[0] if t_flip else float("inf")
    out[i] = {"status": status, "chunks": len(chunks), "completed_with_DONE": done,
              "chunks_after_event": sum(1 for t in chunks if t > tf),
              "chunks_after_event_plus_1s": sum(1 for t in chunks if t > tf + 1.0),
              "last_chunk_after_event_s": round(max(chunks) - tf, 3) if chunks and tf != float("inf") else None}


async def new_requests(t_flip: list[float], dur: float, out: list) -> None:
    t0 = time.time()
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as _:
        pass
    while time.time() - t0 < dur:
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
            resp = await c.post("/v1/chat/completions", headers={"authorization": f"Bearer {KEY_B}"},
                                json={"model": MODEL, "max_tokens": 3, "messages": [{"role": "user", "content": "hi"}]})
        code = resp.status_code if resp.status_code == 200 else f"{resp.status_code} {resp.json()['error']['code']}"
        rel = round(time.time() - t_flip[0], 3) if t_flip else None
        out.append((rel, code))
        await asyncio.sleep(0.1)


async def engaged(scope: str) -> None:
    rr = redis.Redis.from_url("redis://127.0.0.1:26379/0")
    t_flip: list[float] = []
    out: dict = {}
    sweeps: list = []
    tasks = [asyncio.create_task(stream(i, t_flip, out)) for i in range(4)]
    await asyncio.sleep(2.0)
    if scope == "global":
        rr.set("rv:killswitch", "1")
    elif scope == "org":
        rr.hset("rv:killswitch:org", "org-b", "1")
    else:
        rr.hset("rv:killswitch:model", MODEL, "1")
    t_flip.append(time.time())
    log(f"KS {scope} ENGAGED", at=t_flip[0])
    nr = asyncio.create_task(new_requests(t_flip, 3.0, sweeps))
    await asyncio.gather(*tasks, nr)
    first_503 = next((s for s in sweeps if s[1] != 200), None)
    last_200 = max((s[0] for s in sweeps if s[1] == 200), default=None)
    log(f"KS {scope} result", in_flight_streams=out, new_requests_first_rejection=first_503,
        new_requests_last_200_after_flip_s=last_200, new_request_samples=sweeps[:6] + sweeps[-2:])
    rr.set("rv:killswitch", "0")
    rr.hdel("rv:killswitch:org", "org-b")
    rr.hdel("rv:killswitch:model", MODEL)


async def outage() -> None:
    t_ev: list[float] = []
    out: dict = {}
    sweeps: list = []
    ctl("/mode?m=pass")
    tasks = [asyncio.create_task(stream(i, t_ev, out)) for i in range(4)]
    await asyncio.sleep(2.0)
    ctl("/backend?port=26999")  # nothing listens there: new connections refused
    ctl("/reset_existing")  # every open store connection dropped
    t_ev.append(time.time())
    log("STORE OUTAGE begins (all store connections reset, reconnects refused)")
    nr = asyncio.create_task(new_requests(t_ev, 9.0, sweeps))
    await asyncio.gather(*tasks, nr)
    ctl("/backend?port=26379")
    t_restore = time.time()
    rec = []
    while time.time() - t_restore < 15:
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
            resp = await c.post("/v1/chat/completions", headers={"authorization": f"Bearer {KEY_B}"},
                                json={"model": MODEL, "max_tokens": 3, "messages": [{"role": "user", "content": "hi"}]})
        rec.append((round(time.time() - t_restore, 3), resp.status_code))
        if len(rec) >= 8 and all(x[1] == 200 for x in rec[-8:]):
            break
        await asyncio.sleep(0.1)
    first_rej = next((s for s in sweeps if s[1] != 200), None)
    kinds = sorted({s[1] for s in sweeps if s[1] != 200}, key=str)
    log("outage result", in_flight_streams=out, new_requests_first_rejection=first_rej,
        rejection_codes=kinds, samples=sweeps[::6],
        recovery_after_restore=rec[:3] + rec[-3:])


if __name__ == "__main__":
    if sys.argv[1] == "engaged":
        asyncio.run(engaged(sys.argv[2]))
    else:
        asyncio.run(outage())
