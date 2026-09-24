"""GW05 hook: publish a new plan version for one org (pub/sub push + periodic reconcile pick it up).

  python tools/plan_update.py <redis_url> <org> <new_version> [--rule RULE_ID --mode M --action A]
Prints the publish time (unix s) so convergence can be measured against per-worker
plan_version_info / x-rv-plan-version.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rvproto.plan.compiler import compile_plan  # noqa: E402
from rvproto.runtime.store import K_PLAN_CHANNEL, K_PLAN_PREFIX, K_PLAN_VERSIONS  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("url")
ap.add_argument("org")
ap.add_argument("version")
ap.add_argument("--rule")
ap.add_argument("--mode")
ap.add_argument("--action")
a = ap.parse_args()
r = redis.Redis.from_url(a.url)
doc = json.loads(r.get(K_PLAN_PREFIX + a.org))
doc["version"] = a.version
for rule in doc["rules"]:
    if rule["rule_id"] == a.rule:
        if a.mode:
            rule["mode"] = a.mode
        if a.action:
            rule["action"] = a.action
compile_plan(doc)  # validate before publishing (control-plane rejects invalid plans at save time)
pipe = r.pipeline()
pipe.set(K_PLAN_PREFIX + a.org, json.dumps(doc))
pipe.hset(K_PLAN_VERSIONS, a.org, a.version)
pipe.publish(K_PLAN_CHANNEL, a.org)
pipe.execute()
print(json.dumps({"org": a.org, "version": a.version, "published_at": time.time()}))
