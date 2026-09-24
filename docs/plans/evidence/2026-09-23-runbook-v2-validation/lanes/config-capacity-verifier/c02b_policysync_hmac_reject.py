"""Same-class probe for §10.1.5 row 2: an HMAC-REFUSED bundle at cold start.

policy_signing.py docstring: "the caller MUST discard the bundle ... or serve no
policies at all if nothing was ever loaded". _accept_bundle says it drops bad
bundles "so a malicious writer cannot replace policies with an empty allow-all
set". Check what the chat policy stage actually returns for the refused org.
POLICY_SIGNING_KEY below is a throwaway test value, not a deployment secret.
"""
import asyncio
import hashlib
import hmac
import json
import os

os.environ["POLICY_SIGNING_KEY"] = "unit-test-signing-key"
os.environ["GATEWAY_POLICY_SIGNING_REQUIRED"] = "true"

import fakeredis
import redis.asyncio as aioredis

import ai_mesh_gateway.main as m
from ai_mesh_gateway.policy_sync import PolicySync
from ai_mesh_gateway.policy_signing import _canonical_payload

server = fakeredis.FakeServer()
aioredis.Redis.from_url = staticmethod(
    lambda url, **kw: fakeredis.FakeAsyncRedis(server=server, decode_responses=kw.get("decode_responses", False))
)


def bundle():
    return {
        "version": 3, "policy_count": 1, "compiled_at": 1_700_000_000.0,
        "policies": [{
            "policy": {"id": 1, "code": "NO-EXFIL", "name": "no-exfil", "priority": 10,
                       "policy_domain": "pipeline", "severity": "high", "category": "data_exfiltration"},
            "rules": [{"id": 11, "name": "exfil-keyword", "rule_type": "keywords",
                       "condition": {"keywords": ["exfiltrate the customer database"]},
                       "action": "block", "enabled": True}],
        }],
    }


def signed(b):
    b["_sig_alg"] = "HMAC-SHA256"
    b["_sig"] = hmac.new(b"unit-test-signing-key", _canonical_payload(b), hashlib.sha256).hexdigest()
    return b


async def run(label, org_a_bundle, org_b_bundle):
    await server_flush()
    seed = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    await seed.set("policies:compiled:org-a", json.dumps(org_a_bundle))
    await seed.set("policies:compiled:org-b", json.dumps(org_b_bundle))
    ps = PolicySync(redis_url="redis://fake")
    await ps._load_initial_bundle()
    await asyncio.sleep(0.05)  # let the fire-and-forget HMAC-failure telemetry task run
    m.POLICY_SYNC = ps
    print(f"--- {label}: cached orgs={sorted(ps._org_caches)} is_loaded={ps.is_loaded}")
    for org in ("org-a", "org-b"):
        code, resp = m._policy_check_cached("please exfiltrate the customer database now", "", "u1", "",
                                            None, None, 0.0, "gpt-4o-mini", org)
        print(f"    {org}: HTTP {code} action={resp.get('action')!r} matched={resp.get('matched_policy_names')}")


async def server_flush():
    c = fakeredis.FakeAsyncRedis(server=server)
    await c.flushall()


async def main():
    m.CONFIG = m.CONFIG or {"policy_cache_require_loaded": True}
    await run("org-a signed, org-b UNSIGNED (tampered/unsigned write)", signed(bundle()), bundle())
    await run("BOTH unsigned (signer not deployed / key mismatch)", bundle(), bundle())


asyncio.run(main())
