"""P08: per-tenant fairness at the shared semantic guard (noisy neighbour).

Tenant A (org-a) sends benign LONG prompts (~7k tokens = 16 guard windows, inside rvproto's
max-window limit, i.e. a legitimate request). Tenant B (org-b) sends short prompts at 1 req/s.
Both plans select PG2. The guard is shared (one worker, CPU guard). Phases:
  1. B alone                      -> baseline
  2. A (1 in flight, closed loop) + B at 1 rps
  3. same as 2 but B uses the official OpenAI SDK with default retries (counts HTTP attempts)
Question: does admission give B any share, or is B starved/shed by A's legitimate traffic?
"""
from __future__ import annotations

import asyncio
import collections
import json
import sys
import time

import aiohttp
import httpx
import openai

BASE = "http://127.0.0.1:8480"
OUT = sys.argv[1]
SENT = ("The regional team reviewed the quarterly plan and agreed that the warehouse schedule, "
        "the delivery routes and the training calendar should stay the same for the next season. ")
LONG = SENT * 222  # 7,104 PG2 tokens -> 16 windows (max accepted 7,200)


async def tenant_a(s: aiohttp.ClientSession, stop: asyncio.Event, log: list) -> None:
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": LONG}]}
    h = {"authorization": "Bearer sk-rv-org-a-0001", "x-synth-ttft-ms": "0", "x-synth-tokens": "2"}
    while not stop.is_set():
        t = time.perf_counter()
        async with s.post(f"{BASE}/v1/chat/completions", json=body, headers=h) as r:
            txt = await r.text()
            code = (json.loads(txt).get("error") or {}).get("code") if r.status >= 400 else None
            log.append({"t": t, "status": r.status, "code": code, "lat": time.perf_counter() - t})


async def tenant_b(s: aiohttp.ClientSession, dur: float, log: list) -> None:
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is the capital of France?"}]}
    h = {"authorization": "Bearer sk-rv-org-b-0001", "x-synth-ttft-ms": "0", "x-synth-tokens": "2"}
    end = time.perf_counter() + dur
    tasks = []

    async def one() -> None:
        t = time.perf_counter()
        async with s.post(f"{BASE}/v1/chat/completions", json=body, headers=h) as r:
            txt = await r.text()
            code = (json.loads(txt).get("error") or {}).get("code") if r.status >= 400 else None
            log.append({"t": t, "status": r.status, "code": code, "lat": time.perf_counter() - t,
                        "retry_after_ms": r.headers.get("retry-after-ms"), "stages": r.headers.get("x-rv-stages")})

    while time.perf_counter() < end:
        tasks.append(asyncio.create_task(one()))
        await asyncio.sleep(1.0)
    await asyncio.gather(*tasks)


def summarize(name: str, log: list) -> dict:
    st = collections.Counter(f"{x['status']}:{x['code']}" for x in log)
    lat = sorted(x["lat"] for x in log if x["status"] == 200)
    stages = collections.Counter(x.get("stages") for x in log if x["status"] == 200)
    return {"who": name, "n": len(log), "outcomes": dict(st),
            "ok_p50_ms": round(lat[len(lat) // 2] * 1e3, 1) if lat else None,
            "ok_max_ms": round(lat[-1] * 1e3, 1) if lat else None, "ok_stages": dict(stages)}


async def phase(dur: float, with_a: bool) -> dict:
    a_log: list = []
    b_log: list = []
    stop = asyncio.Event()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
        at = asyncio.create_task(tenant_a(s, stop, a_log)) if with_a else None
        if at:
            await asyncio.sleep(1.0)
        await tenant_b(s, dur, b_log)
        stop.set()
        if at:
            await at
    out = {"B": summarize("B(org-b short)", b_log)}
    if with_a:
        out["A"] = summarize("A(org-a 16-window)", a_log)
    return out


def sdk_phase(n_calls: int) -> dict:
    """B via the OpenAI SDK (default max_retries=2) while A keeps the guard busy; count wire attempts."""
    attempts = collections.Counter()

    def hook(req: httpx.Request) -> None:
        attempts["http_attempts"] += 1

    cl = openai.OpenAI(base_url=f"{BASE}/v1", api_key="sk-rv-org-b-0001",
                       http_client=httpx.Client(event_hooks={"request": [hook]}, timeout=60),
                       default_headers={"x-synth-ttft-ms": "0", "x-synth-tokens": "2"})
    res = collections.Counter()
    t0 = time.perf_counter()
    for _ in range(n_calls):
        try:
            cl.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi there"}])
            res["ok"] += 1
        except openai.APIStatusError as e:
            res[f"err_{e.status_code}"] += 1
    return {"sdk_calls": n_calls, "results": dict(res), **attempts, "wall_s": round(time.perf_counter() - t0, 1)}


async def main() -> None:
    # window count of A's prompt as the gateway computes it (metric guard_windows_per_request)
    out = {"phase1_B_alone": await phase(15, False), "phase2_A_plus_B": await phase(30, True)}
    print(json.dumps(out, indent=1), flush=True)
    stop = asyncio.Event()
    a_log: list = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
        at = asyncio.create_task(tenant_a(s, stop, a_log))
        await asyncio.sleep(1.0)
        out["phase3_B_sdk_with_retries"] = await asyncio.to_thread(sdk_phase, 5)
        stop.set()
        await at
    out["phase3_A"] = summarize("A", a_log)
    print(json.dumps({k: out[k] for k in ("phase3_B_sdk_with_retries", "phase3_A")}, indent=1), flush=True)
    json.dump(out, open(OUT, "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
