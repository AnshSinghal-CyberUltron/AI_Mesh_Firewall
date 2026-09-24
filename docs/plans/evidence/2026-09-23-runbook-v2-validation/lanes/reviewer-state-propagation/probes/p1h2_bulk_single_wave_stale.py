"""P1h: cost of one bulk plan change (every org re-published with a notification each) for ONE worker's
real PlanSnapshot: store commands issued and time to converge, as a function of org count."""
import asyncio, json, sys, time
sys.path.insert(0, __import__("os").environ.get("RVPROTO_DIR", "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-state-propagation/rvproto-frozen1"))
import redis, redis.asyncio as aioredis
from rvproto.plan.fixtures import org_a
from rvproto.plan.snapshot import PlanSnapshot
from rvproto.runtime.metrics import Registry
from rvproto.domain.plan import ExecutionPlan
URL = "redis://127.0.0.1:26379/8"
def stats(r):
    s = r.info("commandstats")
    return {k: v["calls"] for k, v in s.items() if k in ("cmdstat_hgetall", "cmdstat_get")}, {k: v["usec"] for k, v in s.items() if k in ("cmdstat_hgetall", "cmdstat_get")}
async def run(n):
    r = redis.Redis.from_url(URL); r.flushdb()
    orgs = [f"org-{i:05d}" for i in range(n)]
    p = r.pipeline()
    for o in orgs:
        p.set("rv:plan:" + o, json.dumps(org_a("v0", o))); p.hset("rv:plan_versions", o, "v0")
    p.execute()
    snap = PlanSnapshot(aioredis.Redis.from_url(URL), Registry(0), reconcile_ms=1000, stale_ms=5000)
    await snap.load_all()
    task = asyncio.create_task(snap.run()); await asyncio.sleep(0.5)
    r.config_resetstat()
    def writer():
        w = redis.Redis.from_url(URL)
        for o in orgs:
            pp = w.pipeline(); pp.set("rv:plan:" + o, json.dumps(org_a("v1", o))); pp.hset("rv:plan_versions", o, "v1"); pp.publish("rv:plan:updates", o); pp.execute()
    t0 = time.time()
    await asyncio.to_thread(writer)
    t_written = time.time() - t0
    while any(not (isinstance(snap.get(o), ExecutionPlan) and snap.get(o).version == "v1") for o in orgs):
        await asyncio.sleep(0.05)
        if time.time() - t0 > float(__import__("os").environ.get("CAP", "60")): break
    t_conv = time.time() - t0
    await asyncio.sleep(0.5)
    calls, usec = stats(r)
    print(json.dumps({"orgs": n, "writer_s": round(t_written, 2), "converged_s": round(t_conv, 2),
                      "store_calls_one_worker": calls, "store_cpu_ms_one_worker": {k: round(v/1000, 1) for k, v in usec.items()},
                      "hgetall_calls_per_notification": round(calls.get("cmdstat_hgetall", 0) / n, 2)}), flush=True)
    stale = [o for o in orgs if not (isinstance(snap.get(o), ExecutionPlan) and snap.get(o).version == "v1")]
    print(json.dumps({"orgs": n, "stale_after_cap": len(stale), "examples": [(o, getattr(snap.get(o), "version", None), snap._versions.get(o)) for o in stale[:5]],
                      "store_says": "v1 for every org"}), flush=True)
    task.cancel()
async def main():
    for n in [int(x) for x in __import__("os").environ.get("NS", "2000").split(",")]:
        await run(n)
asyncio.run(main())
