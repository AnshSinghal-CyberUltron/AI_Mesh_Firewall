"""P11: after a store failover that RESETS the old connections (clean failover, new endpoint healthy),
how many requests fail because a worker's request pool hands out a dead pooled connection?"""
import asyncio, hashlib, json, urllib.request
from pc import log, r
from p9_latency import fire
CTL = "http://127.0.0.1:26390"
def ctl(p): return json.loads(urllib.request.urlopen(CTL + p, timeout=5).read())
def keys(lo, hi):
    ks = [f"sk-rv-lat-{i:05d}-probe" for i in range(lo, hi)]
    p = r().pipeline()
    for k in ks:
        p.hset("rv:keys", hashlib.sha256(k.encode()).hexdigest(),
               json.dumps({"key_id": k[-11:], "org_id": "org-lat", "rate_per_s": 1e6, "burst": 1e6, "epoch": 1}))
    p.execute()
    return ks
async def main():
    a, b, c = keys(1000, 1150), keys(2000, 2150), keys(3000, 3150)
    log("D0 150 concurrent cold keys (grows each worker's request pool)", result=await fire(a), store_clients=int(r().info("clients")["connected_clients"]))
    ctl("/reset_existing")  # failover: every existing store connection is closed; the store itself is healthy
    await asyncio.sleep(1.0)
    log("D1 150 concurrent cold keys right after the failover", result=await fire(b))
    log("D2 150 more cold keys", result=await fire(c))
asyncio.run(main())
