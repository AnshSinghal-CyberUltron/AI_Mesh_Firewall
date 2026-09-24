"""P9 (LGW06-5 / L14-4): inject store latency with the production target_p99_ms=20 contract
(request-path store socket_timeout = 20 ms). Deterministic-only org (no guard involvement).
Measures warm vs cold paths, background freshness, and store connection growth.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import urllib.request

import httpx

from pc import BASE, MODEL, log, r

CTL = "http://127.0.0.1:26390"
ORG = "org-lat"


def ctl(path: str) -> dict:
    return json.loads(urllib.request.urlopen(CTL + path, timeout=5).read())


def provision(n_keys: int) -> list[str]:
    rr = r()
    doc = {"org_id": ORG, "version": "lat-1", "streaming_mode": "incremental", "rules": [
        {"rule_id": "L.secret.in", "category": "secret", "detectors": ["secret.*"], "mode": "enforce",
         "action": "block", "priority": 20, "scope": "input", "on_unavailable": {"kind": "fail_closed"}, "threshold": None}]}
    p = rr.pipeline()
    p.set("rv:plan:" + ORG, json.dumps(doc))
    p.hset("rv:plan_versions", ORG, "lat-1")
    p.set("rv:budget:" + ORG, 10**12)
    keys = [f"sk-rv-lat-{i:05d}-probe" for i in range(n_keys)]
    for k in keys:
        p.hset("rv:keys", hashlib.sha256(k.encode()).hexdigest(),
               json.dumps({"key_id": k[-11:], "org_id": ORG, "rate_per_s": 1e6, "burst": 1e6, "epoch": 1}))
    p.publish("rv:plan:updates", ORG)
    p.execute()
    return keys


async def fire(keys: list[str]) -> dict:
    async def one(k: str) -> str:
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
            resp = await c.post("/v1/chat/completions", headers={"authorization": f"Bearer {k}"},
                                json={"model": MODEL, "max_tokens": 3, "messages": [{"role": "user", "content": "hi"}]})
        return "200" if resp.status_code == 200 else f"{resp.status_code} {resp.json()['error']['code']}"
    res = await asyncio.gather(*(one(k) for k in keys))
    out: dict = {}
    for x in res:
        out[x] = out.get(x, 0) + 1
    return out


def clients() -> int:
    return int(r().info("clients")["connected_clients"])


async def main() -> None:
    warm = provision(4)
    await asyncio.sleep(1.5)
    await fire(warm * 8)  # warm identity + lease on every worker
    await fire(warm * 8)
    base_clients = clients()
    cold = [f"sk-rv-lat-{i:05d}-probe" for i in range(100, 400)]
    rr = r()
    p = rr.pipeline()
    for k in cold:
        p.hset("rv:keys", hashlib.sha256(k.encode()).hexdigest(),
               json.dumps({"key_id": k[-11:], "org_id": ORG, "rate_per_s": 1e6, "burst": 1e6, "epoch": 1}))
    p.execute()
    log("T0 no injected latency", warm=await fire(warm * 8), store_clients=clients())
    ctl("/mode?m=delay&ms=100")  # +100 ms per chunk each direction => ~200 ms added RTT
    await asyncio.sleep(1.0)
    t0 = time.time()
    warm_res = await fire(warm * 8)
    cold_res = await fire(cold[:150])
    peak = clients()
    log("T1 +~200 ms store RTT (LGW06-5)", warm_keys=warm_res, cold_keys_150_concurrent=cold_res,
        store_clients_before=base_clients, store_clients_after_burst=peak, seconds=round(time.time() - t0, 2))
    await asyncio.sleep(6.0)
    rz = httpx.get(f"{BASE}/readyz", timeout=10).json()
    log("T2 background refreshers under the same latency (+6 s)", readyz_killswitch=rz["killswitch"],
        plans_fresh=rz["plans_fresh"], warm=await fire(warm * 8))
    ctl("/mode?m=pass")
    await asyncio.sleep(1.0)
    log("T3 latency removed", cold_retry=await fire(cold[:150]), store_clients=clients())


if __name__ == "__main__":
    asyncio.run(main())
