"""P09 (GW05 LGW05-4): benchmarked plan/snapshot.py runs _apply() concurrently from the periodic reconcile loop
and from the pub/sub listener (one reconcile per message).  _apply copies self._plans at its start, awaits one
GET per changed org, then assigns self._plans wholesale -> last writer wins.  Store is a deterministic fake whose
GET of the plan document can be slow (e.g. a Redis latency blip on one connection)."""
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
from rvproto.plan.fixtures import org_a
from rvproto.plan.snapshot import PlanSnapshot
from rvproto.runtime.metrics import Registry

class FakeRedis:
    def __init__(self):
        self.versions = {b"org-a": b"a-1"}; self.docs = {"rv:plan:org-a": json.dumps(org_a("a-1")).encode()}
        self.slow_next_get = 0.0
    async def hgetall(self, key):
        return dict(self.versions)
    async def get(self, key):
        doc = self.docs.get(key)          # value read at call time (as Redis would answer)...
        d, self.slow_next_get = self.slow_next_get, 0.0
        await asyncio.sleep(d)            # ...but the reply is delivered late
        return doc

async def main():
    r = FakeRedis(); ps = PlanSnapshot(r, Registry(0), reconcile_ms=1000, stale_ms=5000)
    await ps.load_all(); print("t0 serving", ps.get("org-a").version)
    # operator tightens the policy: a-2 is written and published
    r.docs["rv:plan:org-a"] = json.dumps(org_a("a-2")).encode(); r.versions[b"org-a"] = b"a-2"
    # periodic reconcile starts first; its GET reply is delayed 300 ms (store blip)
    r.slow_next_get = 0.3
    periodic = asyncio.create_task(ps.reconcile_once())
    await asyncio.sleep(0.01)
    # the operator pushes a-3 (another change) -> the listener's reconcile runs fast and applies a-3
    r.docs["rv:plan:org-a"] = json.dumps(org_a("a-3")).encode(); r.versions[b"org-a"] = b"a-3"
    await ps.reconcile_once(); print("t+10ms after push of a-3, serving", ps.get("org-a").version)
    await periodic
    print("t+300ms after the delayed periodic reconcile finishes, serving", ps.get("org-a").version,
          "| snapshot age", round(ps.age_s(), 3), "s (fresh, so readiness stays green)")
    await asyncio.sleep(0.05)
    await ps.reconcile_once(); print("next periodic reconcile (up to RV_PLAN_RECONCILE_MS later) serving", ps.get("org-a").version)
asyncio.run(main())
