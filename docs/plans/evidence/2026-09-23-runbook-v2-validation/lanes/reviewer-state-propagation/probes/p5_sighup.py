"""P5a: GW03 says 'Change CPU quota and SIGHUP -> pools resize; no restart; no dropped in-flight
request'. rvproto implements no SIGHUP handler. Send SIGHUP to ONE worker while 4 long SSE streams
are in flight (spread over the workers), then watch the node.
  python p5_sighup.py <launcher_pgid>
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time

import httpx

from pc import BASE, KEY_B, MODEL, log


def workers(pgid: int) -> list[tuple[int, str]]:
    out = subprocess.run(["ps", "-o", "pid=,args=", "-g", str(pgid)], capture_output=True, text=True).stdout
    return [(int(l.split()[0]), l) for l in out.splitlines() if "--worker" in l]


async def stream(i: int, res: dict) -> None:
    body = {"model": MODEL, "stream": True, "max_tokens": 300,
            "messages": [{"role": "user", "content": f"long #{i}"}]}
    hdr = {"authorization": f"Bearer {KEY_B}", "x-synth-tokens": "300", "x-synth-itl-ms": "30"}
    n, done, err = 0, False, None
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
            async with c.stream("POST", "/v1/chat/completions", json=body, headers=hdr) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        n += 1
                        done = done or line.strip() == "data: [DONE]"
    except Exception as exc:  # noqa: BLE001
        err = type(exc).__name__
    res[i] = {"chunks": n, "completed": done, "error": err}


async def main(pgid: int) -> None:
    before = workers(pgid)
    res: dict = {}
    tasks = [asyncio.create_task(stream(i, res)) for i in range(4)]
    await asyncio.sleep(2.0)
    victim = before[0][0]
    os.kill(victim, signal.SIGHUP)
    t = time.time()
    log("SIGHUP sent to one worker", pid=victim, workers_before=len(before))
    await asyncio.gather(*tasks)
    await asyncio.sleep(2.0)
    after = workers(pgid)
    alive_launcher = subprocess.run(["ps", "-p", str(pgid)], capture_output=True).returncode == 0
    try:
        h = httpx.get(f"{BASE}/healthz", timeout=3).status_code
    except Exception as exc:  # noqa: BLE001
        h = type(exc).__name__
    log("after SIGHUP", in_flight_streams=res, workers_after=len(after), launcher_alive=alive_launcher,
        healthz=h, seconds=round(time.time() - t, 2))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
