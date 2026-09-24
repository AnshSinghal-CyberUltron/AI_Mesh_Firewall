"""P08: GW05 plan snapshot at tenant scale (benchmarked plan/snapshot.py against a real Redis 7.4, DB 1).
Every worker runs reconcile_once() every RV_PLAN_RECONCILE_MS (1 s) AND once per pub/sub message; each is a
full HGETALL of rv:plan_versions + a copy of the plan dict, executed on the worker's event loop.
Measures: startup load_all() time, steady reconcile (no change) event-loop time, and a push storm."""
import asyncio, json, sys, time
sys.path.insert(0, sys.argv[1])
import redis, redis.asyncio as aioredis
from rvproto.plan.fixtures import org_a
from rvproto.plan.snapshot import PlanSnapshot
from rvproto.runtime.metrics import Registry
URL = "redis://127.0.0.1:36379/1"

def seed(n):
    r = redis.Redis.from_url(URL); r.flushdb()
    pipe = r.pipeline(transaction=False)
    for i in range(n):
        org = f"org-{i:06d}"; doc = org_a(f"v1", org)
        pipe.set("rv:plan:" + org, json.dumps(doc)); pipe.hset("rv:plan_versions", org, "v1")
        if i % 5000 == 4999: pipe.execute()
    pipe.execute()

async def measure(n):
    r = aioredis.Redis.from_url(URL)
    ps = PlanSnapshot(r, Registry(0), reconcile_ms=1000, stale_ms=5000)
    t0 = time.perf_counter(); await ps.load_all(); t_load = time.perf_counter() - t0
    # steady reconcile: nothing changed; time the whole call and the synchronous (loop-blocking) part
    samples = []
    for _ in range(10):
        t0 = time.perf_counter(); await ps.reconcile_once(); samples.append(time.perf_counter() - t0)
    # loop-block: run a 1 ms ticker concurrently and record its worst lateness during 5 reconciles
    lat = []
    stop = False
    async def ticker():
        while not stop:
            t = time.perf_counter(); await asyncio.sleep(0.001); lat.append(time.perf_counter() - t - 0.001)
    tk = asyncio.create_task(ticker())
    for _ in range(5):
        await ps.reconcile_once()
    stop = True; await tk
    # push storm: 200 plan updates published -> _listen runs one full reconcile per message (serial)
    await r.aclose()
    return t_load, sorted(samples)[len(samples)//2], max(lat)

for n in (1000, 10000, 100000):
    seed(n)
    t_load, t_rec, lag = asyncio.run(measure(n))
    print(f"orgs={n:>6}: startup load_all={t_load:7.2f}s  steady reconcile (no change) median={1e3*t_rec:7.1f} ms  "
          f"worst event-loop lag during reconcile={1e3*lag:6.1f} ms  -> per worker per second (x18 workers on a unit)")
redis.Redis.from_url(URL).flushdb()
