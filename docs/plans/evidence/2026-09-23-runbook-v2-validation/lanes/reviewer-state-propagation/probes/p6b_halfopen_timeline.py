"""P6b: fine-grained timeline of the half-open failover (probe every ~0.2 s, fresh connections)."""

from __future__ import annotations

import json
import time
import urllib.request

import httpx

from pc import BASE, KEY_B, MODEL, log

CTL = "http://127.0.0.1:26390"


def ctl(path: str) -> dict:
    return json.loads(urllib.request.urlopen(CTL + path, timeout=5).read())


def one() -> str:
    with httpx.Client(base_url=BASE, timeout=30) as c:
        r = c.post("/v1/chat/completions", headers={"authorization": f"Bearer {KEY_B}"},
                   json={"model": MODEL, "max_tokens": 3, "messages": [{"role": "user", "content": "hi"}]})
    return "200" if r.status_code == 200 else f"{r.status_code} {r.json()['error']['code']}"


def main() -> None:
    ctl("/mode?m=pass")
    for _ in range(4):
        one()
    ctl("/blackhole_existing")
    t0 = time.time()
    tl = []
    while time.time() - t0 < 20:
        tl.append((round(time.time() - t0, 2), one()))
        time.sleep(0.15)
    bad = [x for x in tl if x[1] != "200"]
    log("half-open failover timeline", first_non200=bad[0] if bad else None, last_non200=bad[-1] if bad else None,
        non200=len(bad), total=len(tl), codes=sorted({x[1] for x in bad}),
        outage_window_s=round(bad[-1][0] - bad[0][0], 2) if bad else 0, timeline=tl)
    ctl("/reset_existing")


if __name__ == "__main__":
    main()
