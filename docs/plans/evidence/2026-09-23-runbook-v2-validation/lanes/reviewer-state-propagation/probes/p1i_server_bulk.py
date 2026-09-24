"""P1i: the reconcile race on the LIVE 4-worker server (every worker is an independent replica of the
plan state). 2,000 tenants; one bulk re-publish (e.g. a platform-integrity bump) with one
notification per tenant; then per-worker served versions vs the store, after 15 s and 60 s.
"""

from __future__ import annotations

import json
import re
import time

import httpx
import redis

from pc import BASE, log

N = 2000
URL = "redis://127.0.0.1:26379/0"


def doc(org: str, ver: str) -> str:
    return json.dumps({"org_id": org, "version": ver, "streaming_mode": "incremental", "rules": [
        {"rule_id": "K.secret.in", "category": "secret", "detectors": ["secret.*"], "mode": "enforce",
         "action": "block", "priority": 20, "scope": "input", "on_unavailable": {"kind": "fail_closed"}, "threshold": None}]})


def served() -> dict[int, dict[str, str]]:
    time.sleep(1.2)  # per-worker dumps are 1 s apart
    out: dict[int, dict[str, str]] = {}
    for w in httpx.get(f"{BASE}/metrics/all", timeout=30).json()["per_worker"]:
        m = {}
        for k in w["gauge"]:
            g = re.match(r'plan_version_info\{org="(bulk-\d+)",version="([^"]+)"\}', k)
            if g:
                m[g.group(1)] = g.group(2)
        out[w["worker"]] = m
    return out


def main() -> None:
    r = redis.Redis.from_url(URL)
    orgs = [f"bulk-{i:05d}" for i in range(N)]
    p = r.pipeline()
    for o in orgs:
        p.set("rv:plan:" + o, doc(o, "k0"))
        p.hset("rv:plan_versions", o, "k0")
    p.publish("rv:plan:updates", "bulk-load")
    p.execute()
    t0 = time.time()
    while time.time() - t0 < 60:
        s = served()
        if all(len(v) == N and set(v.values()) == {"k0"} for v in s.values()):
            break
    log("B0 every worker serves k0 for all tenants", workers={w: len(v) for w, v in s.items()})
    for o in orgs:  # the control plane's bulk re-publish, one notification per tenant
        pp = r.pipeline()
        pp.set("rv:plan:" + o, doc(o, "k1"))
        pp.hset("rv:plan_versions", o, "k1")
        pp.publish("rv:plan:updates", o)
        pp.execute()
    t1 = time.time()
    for wait in (15, 60):
        time.sleep(max(0.0, t1 + wait - time.time()))
        s = served()
        stale = {w: sorted(o for o, v in m.items() if v != "k1") for w, m in s.items()}
        log(f"B1 +{wait}s after the bulk re-publish (store: k1 for all {N})",
            stale_per_worker={w: len(v) for w, v in stale.items()},
            examples={w: v[:3] for w, v in stale.items() if v})


if __name__ == "__main__":
    main()
