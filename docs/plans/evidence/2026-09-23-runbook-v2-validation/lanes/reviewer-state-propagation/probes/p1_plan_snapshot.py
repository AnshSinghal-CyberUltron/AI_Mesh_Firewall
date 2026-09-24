"""P1: the REAL rvproto PlanSnapshot against a real Redis (db 2 of sp-rsp-redis :26379).

A  store flush while serving, then control plane re-publishes the SAME plan versions
B  version regress (older version pushed after newer) + same-version content change
C  deterministic concurrent-reconcile interleaving (fault proxy delays ONE plan GET)
Run: rvproto/.venv/bin/python p1_plan_snapshot.py <A|B|C>
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

sys.path.insert(0, __import__("os").environ.get("RVPROTO_DIR", "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-state-propagation/rvproto-frozen1"))

import redis
import redis.asyncio as aioredis

from rvproto.domain.plan import ExecutionPlan, PlanUnavailable, PlanUnknownTenant
from rvproto.plan.fixtures import org_a, org_b
from rvproto.plan.snapshot import PlanSnapshot
from rvproto.runtime.metrics import Registry
from rvproto.runtime.store import K_PLAN_CHANNEL, K_PLAN_PREFIX, K_PLAN_VERSIONS

DIRECT = "redis://127.0.0.1:26379/2"
PROXIED = "redis://127.0.0.1:26380/2"
CTL = "http://127.0.0.1:26390"


def ctl(path: str) -> dict:
    return json.loads(urllib.request.urlopen(CTL + path, timeout=5).read())


def show(x: object) -> str:
    if isinstance(x, ExecutionPlan):
        pii_out = next(r for r in x.rules if r.rule_id.endswith("pii.out"))
        return f"ExecutionPlan(version={x.version}, pii.out={pii_out.mode.value}/{pii_out.action.value})"
    if isinstance(x, PlanUnknownTenant):
        return "PLAN_UNKNOWN_TENANT"
    if isinstance(x, PlanUnavailable):
        return f"PLAN_UNAVAILABLE({x.reason})"
    return repr(x)


def publish(r: redis.Redis, doc: dict, notify: bool = True) -> None:
    p = r.pipeline()
    p.set(K_PLAN_PREFIX + doc["org_id"], json.dumps(doc))
    p.hset(K_PLAN_VERSIONS, doc["org_id"], doc["version"])
    if notify:
        p.publish(K_PLAN_CHANNEL, doc["org_id"])
    p.execute()


def with_pii_out(doc: dict, mode: str, action: str) -> dict:
    for rule in doc["rules"]:
        if rule["rule_id"].endswith("pii.out"):
            rule["mode"], rule["action"] = mode, action
    return doc


def log(step: str, **kw: object) -> None:
    print(json.dumps({"t": round(time.time(), 3), "step": step, **kw}), flush=True)


async def probe_a() -> None:
    r = redis.Redis.from_url(DIRECT)
    r.flushdb()
    for doc in (org_a(), org_b()):
        publish(r, doc)
    snap = PlanSnapshot(aioredis.Redis.from_url(DIRECT), Registry(0), reconcile_ms=1000, stale_ms=5000)
    await snap.load_all()
    runner = asyncio.create_task(snap.run())  # the real push listener + 1 s periodic reconcile
    await asyncio.sleep(1.2)
    log("A1 seeded + loaded", org_a=show(snap.get("org-a")), org_b=show(snap.get("org-b")))
    r.flushdb()  # store restart without persistence / failover to an empty replica
    await asyncio.sleep(2.5)
    log("A2 after store flush (2.5 s = 2 reconciles)", org_a=show(snap.get("org-a")),
        fresh=snap.fresh(), age_s=round(snap.age_s(), 3))
    for doc in (org_a(), org_b()):  # control plane re-publishes its compiled plans: SAME versions
        publish(r, doc)
    for i in range(1, 11):
        await asyncio.sleep(1.0)
        log(f"A3 +{i}s after re-publish of identical versions", org_a=show(snap.get("org-a")),
            org_b=show(snap.get("org-b")), fresh=snap.fresh(), age_s=round(snap.age_s(), 3),
            internal_versions_index=dict(snap._versions), internal_plans=sorted(snap._plans))
    runner.cancel()
    fresh = PlanSnapshot(aioredis.Redis.from_url(DIRECT), Registry(0), reconcile_ms=1000, stale_ms=5000)
    await fresh.load_all()
    log("A4 control: a NEW process (restart) loads the same store", org_a=show(fresh.get("org-a")))


async def probe_b() -> None:
    r = redis.Redis.from_url(DIRECT)
    r.flushdb()
    publish(r, org_a("a-1"))
    snap = PlanSnapshot(aioredis.Redis.from_url(DIRECT), Registry(0), reconcile_ms=1000, stale_ms=5000)
    await snap.load_all()
    runner = asyncio.create_task(snap.run())
    publish(r, with_pii_out(org_a("a-3"), "enforce", "redact"))
    await asyncio.sleep(1.5)
    log("B1 newer a-3 published", org_a=show(snap.get("org-a")))
    publish(r, with_pii_out(org_a("a-2"), "monitor", "redact"))  # OLDER version, weaker content
    await asyncio.sleep(1.5)
    log("B2 older a-2 published after a-3 (regress)", org_a=show(snap.get("org-a")))
    publish(r, with_pii_out(org_a("a-2"), "enforce", "block"))  # same version string, new content
    await asyncio.sleep(2.5)
    log("B3 same version a-2, content changed to pii.out=enforce/block", org_a=show(snap.get("org-a")),
        store_doc_pii_out=[x for x in json.loads(r.get(K_PLAN_PREFIX + "org-a"))["rules"]
                           if x["rule_id"].endswith("pii.out")][0]["action"])
    runner.cancel()


async def probe_c() -> None:
    """A (periodic) is fetching a changed org X; B (push) applies org W meanwhile; A then assigns
    its stale copy of the whole plan dict. W is left on the old plan while the version index
    says it is current -> no later reconcile refetches it."""
    r = redis.Redis.from_url(DIRECT)
    r.flushdb()
    ctl("/delay_reply_off")
    ctl("/mode?m=pass")
    publish(r, org_a("x-1", "org-x"))
    publish(r, with_pii_out(org_a("w-1", "org-w"), "monitor", "redact"))
    snap = PlanSnapshot(aioredis.Redis.from_url(PROXIED), Registry(0), reconcile_ms=1000, stale_ms=5000)
    await snap.load_all()
    log("C0 loaded", org_x=show(snap.get("org-x")), org_w=show(snap.get("org-w")))
    publish(r, org_a("x-2", "org-x"), notify=False)  # a change the periodic reconcile will pick up
    ctl("/delay_reply?match=GET&match2=rv:plan:org-x&ms=400&count=1")  # A's fetch of X is slow once
    task_a = asyncio.create_task(snap.reconcile_once())  # "periodic" reconcile A
    await asyncio.sleep(0.1)  # A has copied _plans and is awaiting GET rv:plan:org-x
    publish(r, with_pii_out(org_a("w-2", "org-w"), "enforce", "block"), notify=False)
    await snap.reconcile_once()  # "push" reconcile B for org W runs to completion first
    log("C1 after B (push) applied W", org_w=show(snap.get("org-w")))
    await task_a
    log("C2 after A (periodic) finished", org_x=show(snap.get("org-x")), org_w=show(snap.get("org-w")),
        version_index=dict(snap._versions))
    for i in range(1, 6):
        await snap.reconcile_once()  # every later periodic reconcile
    log("C3 after 5 more reconciles (quiescent store)", org_w=show(snap.get("org-w")),
        store_version_w=r.hget(K_PLAN_VERSIONS, "org-w").decode(), version_index=dict(snap._versions))
    print(json.dumps({"proxy": ctl("/stats")}))


if __name__ == "__main__":
    asyncio.run({"A": probe_a, "B": probe_b, "C": probe_c}[sys.argv[1]]())
