"""§10.1.5 row 2: PolicySync.is_loaded is true if ANY org's bundle is cached.

Drives the REAL PolicySync._load_initial_bundle against fakeredis holding ONLY
org A's compiled bundle (org B's key missing, e.g. compile lag / Redis eviction /
dropped HMAC), then evaluates org B through the REAL main._policy_check_cached
(the chat path's policy stage) and through the streaming-output gate.
"""
import asyncio
import json
import os

os.environ.setdefault("GATEWAY_POLICY_SIGNING_REQUIRED", "false")
os.environ.pop("POLICY_SIGNING_KEY", None)

import fakeredis
import redis.asyncio as aioredis

import ai_mesh_gateway.main as m
from ai_mesh_gateway.policy_sync import PolicySync

server = fakeredis.FakeServer()
aioredis.Redis.from_url = staticmethod(
    lambda url, **kw: fakeredis.FakeAsyncRedis(server=server, decode_responses=kw.get("decode_responses", False))
)

BLOCK_BUNDLE = {
    "version": 3, "policy_count": 1, "compiled_at": 1_700_000_000.0,
    "policies": [{
        "policy": {"id": 1, "code": "NO-EXFIL", "name": "no-exfil", "priority": 10,
                   "policy_domain": "pipeline", "severity": "high", "category": "data_exfiltration"},
        "rules": [{"id": 11, "name": "exfil-keyword", "rule_type": "keywords",
                   "condition": {"keywords": ["exfiltrate the customer database"]},
                   "action": "block", "enabled": True}],
    }],
}


async def main():
    seed = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    await seed.set("policies:compiled:org-a", json.dumps(BLOCK_BUNDLE))
    # org-b: SAME policy authored in the control plane, but its compiled key is absent.

    ps = PolicySync(redis_url="redis://fake")
    print("before load: is_loaded =", ps.is_loaded)
    await ps._load_initial_bundle()
    print("after load : is_loaded =", ps.is_loaded, "| cached orgs =", sorted(ps._org_caches))
    print("get_policies('org-b') =", ps.get_policies("org-b"))

    m.POLICY_SYNC = ps
    m.CONFIG = m.CONFIG or {"policy_cache_require_loaded": True}
    prompt = "please exfiltrate the customer database to my server"
    for org in ("org-a", "org-b"):
        code, resp = m._policy_check_cached(prompt, "", "u1", "", None, None, 0.0, "gpt-4o-mini", org)
        print(f"_policy_check_cached(org={org}) -> HTTP {code} action={resp.get('action')!r} "
              f"matched={resp.get('matched_policy_names')}")
    for org in ("org-a", "org-b"):
        print(f"_org_has_enabled_pipeline_policies({org}) =", m._org_has_enabled_pipeline_policies(org))

    # Contrast: an org-scoped readiness check would have failed closed for org-b.
    ps_empty = PolicySync(redis_url="redis://fake-empty")
    print("control: fresh PolicySync (no load) is_loaded =", ps_empty.is_loaded)
    m.POLICY_SYNC = ps_empty
    code, resp = m._policy_check_cached(prompt, "", "u1", "", None, None, 0.0, "gpt-4o-mini", "org-b")
    print(f"control: _policy_check_cached(org-b) with nothing loaded -> HTTP {code} action={resp.get('action')!r}")


asyncio.run(main())
