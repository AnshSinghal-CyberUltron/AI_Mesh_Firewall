"""Real backend kill on a unit: requests in flight while the guard backend is killed (triton_grpc:
`docker kill rv-triton`; local_gpu owner topology: tools/kill_owners.py, the launcher restarts
the owners) must come back UNAVAILABLE (sem:U) and route through each tenant's posture; never
clean. GW19 overload sheds (503 code=overloaded) are retried after retry-after-ms, like the SDK.

  python tools/triton_kill_check.py --base http://10.160.0.46:8400 --kill-cmd "<ssh ... docker kill>"
      --revive-cmd "<ssh ... docker start>" --out out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
import uuid

import httpx

KEYS = {"a": "sk-rv-org-a-0001", "b": "sk-rv-org-b-0001"}


async def one(c: httpx.AsyncClient, org: str, i: int, tag: str) -> dict[str, object]:
    rid = f"tk-{tag}-{org}-{i}-{uuid.uuid4().hex[:8]}"
    t0 = time.monotonic()
    sheds = 0
    for _ in range(40):
        r = await c.post("/v1/chat/completions", headers={"authorization": f"Bearer {KEYS[org]}", "x-request-id": rid,
                                                           "x-synth-tokens": "20"},
                         json={"model": "rv-synth-1", "max_tokens": 20, "stream": i % 2 == 0,
                               "messages": [{"role": "user", "content": f"benign question number {i}"}]})
        if r.status_code != 503 or r.json().get("error", {}).get("code") != "overloaded":
            break
        sheds += 1
        await asyncio.sleep(float(r.headers.get("retry-after-ms", "100")) / 1000.0)
    return {"rid": rid, "org": org, "t": round(time.monotonic() - t0, 3), "status": r.status_code,
            "disp": r.headers.get("x-rv-disposition"), "stages": r.headers.get("x-rv-stages"), "sheds": sheds}


async def wave(base: str, tag: str, n: int, spacing: float) -> list[dict[str, object]]:
    async with httpx.AsyncClient(base_url=base, timeout=60) as c:
        tasks = []
        for i in range(n):
            for org in ("a", "b"):
                tasks.append(asyncio.create_task(one(c, org, i, tag)))
            await asyncio.sleep(spacing)
        return list(await asyncio.gather(*tasks))


def classify(rows: list[dict[str, object]]) -> dict[str, object]:
    out = {"n": len(rows), "sem_E": 0, "sem_U": 0, "shed_retries": sum(int(x.get("sheds", 0)) for x in rows),
           "bad": []}
    for x in rows:
        st = str(x["stages"])
        if "sem:E" in st:
            out["sem_E"] += 1  # type: ignore[operator]
            ok = x["disp"] == "ALLOW" and x["status"] == 200
        elif "sem:U" in st:
            out["sem_U"] += 1  # type: ignore[operator]
            ok = (x["status"], x["disp"]) == ((403, "BLOCK") if x["org"] == "a" else (200, "ALLOW"))
        else:
            ok = False
        if not ok:
            out["bad"].append(x)  # type: ignore[union-attr]
    return out


async def main_async(a: argparse.Namespace) -> dict[str, object]:
    before = await wave(a.base, "before", 10, a.spacing)
    load = asyncio.create_task(wave(a.base, "during", a.during, a.spacing))
    await asyncio.sleep(a.kill_after)
    t_kill = time.monotonic()
    subprocess.run(a.kill_cmd, shell=True, check=True, capture_output=True)
    during = await load
    after = await wave(a.base, "after", 10, a.spacing)
    subprocess.run(a.revive_cmd, shell=True, check=True, capture_output=True)
    t_rev = time.monotonic()
    recovered_s = None
    for _ in range(240):
        probe = await wave(a.base, "probe", 1, 0.0)
        if all("sem:E" in str(x["stages"]) for x in probe):
            recovered_s = round(time.monotonic() - t_rev, 1)
            break
        await asyncio.sleep(1.0)
    final = await wave(a.base, "recovered", 10, a.spacing)
    return {"kill_at_s": round(t_kill, 3), "before": classify(before), "during": classify(during),
            "after_kill": classify(after), "recovered_after_s": recovered_s, "recovered": classify(final)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--kill-cmd", required=True)
    ap.add_argument("--revive-cmd", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spacing", type=float, default=0.02, help="seconds between request pairs")
    ap.add_argument("--during", type=int, default=60, help="request pairs in the wave the kill lands in")
    ap.add_argument("--kill-after", type=float, default=0.4, help="seconds into that wave")
    a = ap.parse_args()
    res = asyncio.run(main_async(a))
    json.dump(res, open(a.out, "w"), indent=1)
    summary = {k: ({kk: vv for kk, vv in v.items() if kk != "bad"} | {"bad": len(v["bad"])}
                   if isinstance(v, dict) else v) for k, v in res.items()}
    print(json.dumps(summary))
    ok = all(not v["bad"] for v in res.values() if isinstance(v, dict))
    ok = ok and res["after_kill"]["sem_U"] == res["after_kill"]["n"] and res["recovered_after_s"] is not None
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
