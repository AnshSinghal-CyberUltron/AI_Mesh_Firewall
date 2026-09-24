"""P06: long unbroken word-class runs in STREAMED output (benign base64 blob / long digit string).

rvproto's pattern-aware holdback keeps any trailing run of [A-Za-z0-9._%+@/=-] pending (it could
still become an email/key), and every upstream chunk re-scans the whole pending buffer (matcher)
and walks it back char by char (_word_run). Hypothesis: cost per stream is O(N^2) in the run
length, runs on the worker's event loop, and the client receives none of the run until it ends.

Measures per run: client time-to-first-blob-byte, total time, worker CPU seconds consumed
(/proc/<worker>/stat utime+stime), and the latency of small concurrent 'bystander' requests
(another tenant, org-b) served by the SAME worker while the blob streams.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
import uuid

import aiohttp

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8480"
PIDFILE = sys.argv[2]
OUT = sys.argv[3]
KEY_A, KEY_B = "sk-rv-org-a-0001", "sk-rv-org-b-0001"
TICK = os.sysconf("SC_CLK_TCK")


def worker_pids() -> list[int]:
    launcher = int(open(PIDFILE).read().strip())
    kids = os.popen(f"pgrep -P {launcher}").read().split()
    return [int(k) for k in kids]


def cpu_s(pid: int) -> float:
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / TICK


async def stream_blob(s: aiohttp.ClientSession, scen: str, n: int, step: int, itl_ms: float, base: str) -> dict:
    rid = f"p06-{scen}-{n}-{uuid.uuid4().hex[:6]}"
    h = {"authorization": f"Bearer {KEY_A}", "x-request-id": rid, "x-synth-edge": scen,
         "x-synth-run-chars": str(n), "x-synth-chunk-chars": str(step), "x-synth-itl-ms": str(itl_ms),
         "x-synth-ttft-ms": "0"}
    if scen == "words":  # control: ~n chars of short benign words (spaces end every run)
        h.pop("x-synth-edge")
        h["x-synth-tokens"] = str(max(n // 6, 1))
    body = {"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": "give me the blob"}]}
    t0 = time.perf_counter()
    first_blob = None
    got = 0
    buf = b""
    status = None
    err = None
    async with s.post(f"{base}/v1/chat/completions", json=body, headers=h) as r:
        status = r.status
        async for data in r.content.iter_any():
            buf += data
            while b"\n\n" in buf:
                ev, buf = buf.split(b"\n\n", 1)
                if not ev.startswith(b"data: ") or ev == b"data: [DONE]":
                    continue
                d = json.loads(ev[6:])
                if "error" in d:
                    err = d["error"]
                    continue
                for ch in d.get("choices") or []:
                    c = (ch.get("delta") or {}).get("content") or ""
                    blob = c.replace("Here it is: ", "").replace(" (end)", "")
                    if blob and first_blob is None:
                        first_blob = time.perf_counter() - t0
                    got += len(blob)
    return {"rid": rid, "status": status, "chars_expected": n, "chars_received": got, "error": err,
            "t_first_blob_s": None if first_blob is None else round(first_blob, 4),
            "t_total_s": round(time.perf_counter() - t0, 4)}


async def bystanders(s: aiohttp.ClientSession, stop: asyncio.Event, lat: list[float]) -> None:
    body = {"model": "gpt-4o-mini", "stream": False, "messages": [{"role": "user", "content": "hi"}]}
    h = {"authorization": f"Bearer {KEY_B}", "x-synth-ttft-ms": "0", "x-synth-tokens": "3"}
    while not stop.is_set():
        t = time.perf_counter()
        async with s.post(f"{BASE}/v1/chat/completions", json=body, headers=h) as r:
            await r.read()
        lat.append(time.perf_counter() - t)
        await asyncio.sleep(0.05)


async def one(scen: str, n: int, step: int, itl_ms: float, with_bystanders: bool, base: str = BASE) -> dict:
    pids = worker_pids()
    c0 = {p: cpu_s(p) for p in pids}
    lat: list[float] = []
    stop = asyncio.Event()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900)) as s:
        bt = asyncio.create_task(bystanders(s, stop, lat)) if with_bystanders else None
        res = await stream_blob(s, scen, n, step, itl_ms, base)
        stop.set()
        if bt:
            await bt
    c1 = {p: cpu_s(p) for p in pids}
    res.update(scenario=scen, run_chars=n, chunk_chars=step, itl_ms=itl_ms, via=base,
               worker_cpu_s=round(sum(c1[p] - c0[p] for p in pids), 3))
    if lat:
        lat.sort()
        res.update(bystander_n=len(lat), bystander_p50_ms=round(statistics.median(lat) * 1e3, 1),
                   bystander_p99_ms=round(lat[min(len(lat) - 1, int(0.99 * len(lat)))] * 1e3, 1),
                   bystander_max_ms=round(lat[-1] * 1e3, 1))
    return res


async def main() -> None:
    results = []
    plan = [("base64-long", 2048, 4, 0, True), ("base64-long", 4096, 4, 0, True), ("base64-long", 8192, 4, 0, True),
            ("base64-long", 16384, 4, 0, True), ("base64-long", 32768, 4, 0, True),
            ("digits-long", 16384, 4, 0, True),
            ("words", 32768, 0, 0, True)]
    for scen, n, step, itl, by in plan:
        r = await one(scen, n, step, itl, by)
        results.append(r)
        print(json.dumps(r), flush=True)
    # direct-to-provider control (no gateway): same 32k blob
    r = await one("base64-long", 32768, 4, 0, False, base="http://127.0.0.1:18481")
    r["note"] = "direct to edgeprov, no gateway (worker_cpu_s is the idle gateway)"
    results.append(r)
    print(json.dumps(r), flush=True)
    json.dump(results, open(OUT, "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
