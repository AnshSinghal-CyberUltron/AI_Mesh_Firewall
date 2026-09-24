"""P1d: NATURAL occurrence of the reconcile race (no fault injection): the real PlanSnapshot.run()
(pub/sub listener + 1 s periodic reconcile) while the control plane re-publishes plans for many
orgs (bulk change, e.g. a platform-integrity bump), each with its own notification.

After each bulk round the store is left QUIESCENT for 3.5 s (>3 reconcile periods); any org whose
served version != store version at that point is a PERSISTENT stale plan (the reconcile no longer
sees it as changed because the version index was advanced by the other task).
  rvproto/.venv/bin/python p1d_natural_race.py <n_orgs> <rounds> [missed_fraction]
missed_fraction: share of notifications that are dropped (lost pub/sub), default 0.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time

sys.path.insert(0, __import__("os").environ.get("RVPROTO_DIR", "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-state-propagation/rvproto-frozen1"))

import redis
import redis.asyncio as aioredis

from rvproto.domain.plan import ExecutionPlan
from rvproto.plan.fixtures import org_a
from rvproto.plan.snapshot import PlanSnapshot
from rvproto.runtime.metrics import Registry
from rvproto.runtime.store import K_PLAN_CHANNEL, K_PLAN_PREFIX, K_PLAN_VERSIONS

URL = "redis://127.0.0.1:26379/" + __import__("os").environ.get("DB", "3")


async def main() -> None:
    n, rounds = int(sys.argv[1]), int(sys.argv[2])
    missed = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    rnd = random.Random(int(__import__("os").environ.get("SEED", "7")))
    r = redis.Redis.from_url(URL)
    r.flushdb()
    orgs = [f"org-{i:04d}" for i in range(n)]
    p = r.pipeline()
    for o in orgs:
        p.set(K_PLAN_PREFIX + o, json.dumps(org_a("v0", o)))
        p.hset(K_PLAN_VERSIONS, o, "v0")
    p.execute()
    snap = PlanSnapshot(aioredis.Redis.from_url(URL), Registry(0), reconcile_ms=1000, stale_ms=5000)
    await snap.load_all()
    task = asyncio.create_task(snap.run())
    await asyncio.sleep(0.3)
    total_stale = 0
    for rd in range(1, rounds + 1):
        await asyncio.sleep(rnd.uniform(0, 1.0))  # random phase against the 1 s periodic reconcile
        order = orgs[:]
        rnd.shuffle(order)
        dropped = 0
        # the control plane's writer runs in its own thread, like a real separate process
        def writer() -> int:
            d = 0
            wr = redis.Redis.from_url(URL)
            # two overlapping change waves (e.g. a platform-integrity bump for every org, then
            # tenant edits landing while workers are still applying the first wave)
            seq = [(o, f"v{rd}a") for o in order] + [(o, f"v{rd}b") for o in rnd.sample(order, len(order))]
            for o, ver in seq:
                pp = wr.pipeline()
                pp.set(K_PLAN_PREFIX + o, json.dumps(org_a(ver, o)))
                pp.hset(K_PLAN_VERSIONS, o, ver)
                if rnd.random() >= missed:
                    pp.publish(K_PLAN_CHANNEL, o)
                else:
                    d += 1
                pp.execute()
            return d
        dropped = await asyncio.to_thread(writer)
        await asyncio.sleep(3.5)  # quiescent: >= 3 periodic reconciles
        store = {k.decode(): v.decode() for k, v in r.hgetall(K_PLAN_VERSIONS).items()}
        served = {o: (x.version if isinstance(x, ExecutionPlan) else type(x).__name__) for o in orgs
                  for x in [snap.get(o)]}
        stale = sorted(o for o in orgs if served[o] != store[o])
        idx_says_current = [o for o in stale if snap._versions.get(o) == store[o]]
        total_stale += len(stale)
        print(json.dumps({"round": rd, "orgs": n, "notifications_dropped": dropped,
                          "stale_after_3.5s_quiescence": len(stale),
                          "of_which_version_index_claims_current": len(idx_says_current),
                          "examples": [(o, served[o], store[o]) for o in stale[:3]],
                          "plan_reloads": snap.metrics.count.get("plan_reloads", 0),
                          "push_applied": snap.metrics.count.get("plan_push_applied", 0)}), flush=True)
    # final: give it 5 more seconds to prove the stale entries never heal
    await asyncio.sleep(5)
    store = {k.decode(): v.decode() for k, v in r.hgetall(K_PLAN_VERSIONS).items()}
    still = sorted(o for o in orgs if (lambda x: x.version if isinstance(x, ExecutionPlan) else None)(snap.get(o)) != store[o])
    print(json.dumps({"summary": {"rounds": rounds, "orgs": n, "missed_fraction": missed,
                                  "stale_org_rounds_total": total_stale,
                                  "stale_at_end_after_extra_5s": len(still), "examples": still[:5]}}), flush=True)
    task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
