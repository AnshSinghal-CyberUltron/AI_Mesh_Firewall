"""Seed the shared store: API keys -> principals, org plans, budgets, kill-switch, epoch.

  python tools/seed.py redis://127.0.0.1:16379/0 [--quota-budget N] [--flush]

Test keys (not secrets): sk-rv-org-a-0001 / sk-rv-org-b-0001 / sk-rv-org-q-0001.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rvproto.plan.fixtures import tenants  # noqa: E402
from rvproto.runtime.store import (  # noqa: E402
    K_AUTH_EPOCH,
    K_BUDGET_PREFIX,
    K_KEYS,
    K_KILLSWITCH,
    K_PLAN_CHANNEL,
    K_PLAN_PREFIX,
    K_PLAN_VERSIONS,
)

KEYS = {
    "sk-rv-org-a-0001": {"key_id": "key-a-1", "org_id": "org-a"},
    "sk-rv-org-b-0001": {"key_id": "key-b-1", "org_id": "org-b"},
    "sk-rv-org-q-0001": {"key_id": "key-q-1", "org_id": "org-q"},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--budget", type=int, default=10**13)
    ap.add_argument("--quota-budget", type=int, default=2500)
    ap.add_argument("--rate", type=float, default=1e6, help="GCRA requests/s per org per worker")
    ap.add_argument("--burst", type=float, default=1e6)
    ap.add_argument("--flush", action="store_true")
    ap.add_argument("--extra-key", action="append", default=[], help="KEY:ORG (e.g. a test suite's key)")
    a = ap.parse_args()
    r = redis.Redis.from_url(a.url)
    if a.flush:
        for pattern in ("rv:*",):
            for k in r.scan_iter(pattern):
                r.delete(k)
    pipe = r.pipeline()
    keys = dict(KEYS)
    for i, kv in enumerate(a.extra_key):
        key, org = kv.rsplit(":", 1)
        keys[key] = {"key_id": f"key-extra-{i}", "org_id": org}
    for key, meta in keys.items():
        doc = dict(meta, rate_per_s=a.rate, burst=a.burst, epoch=1)
        pipe.hset(K_KEYS, hashlib.sha256(key.encode()).hexdigest(), json.dumps(doc))
    for org, plan in tenants().items():
        pipe.set(K_PLAN_PREFIX + org, json.dumps(plan))
        pipe.hset(K_PLAN_VERSIONS, org, plan["version"])
        budget = a.quota_budget if org == "org-q" else a.budget
        pipe.set(K_BUDGET_PREFIX + org, budget)
    pipe.set(K_KILLSWITCH, "0")
    pipe.setnx(K_AUTH_EPOCH, "1")
    pipe.execute()
    r.publish(K_PLAN_CHANNEL, "seed")
    print(json.dumps({"seeded_keys": [k if k in KEYS else "<extra>" for k in keys], "orgs": list(tenants()), "budget": a.budget,
                      "quota_budget": a.quota_budget}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
