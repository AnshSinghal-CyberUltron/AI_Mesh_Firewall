"""P8: v1 and v2 (rvproto) on the SAME store during shadow/cutover/rollback (GW21/GW22 'both versions
on shared state'). Uses db 7 of the throwaway redis. The console/control plane writes v1 formats
(control/ai_mesh_control/core/signals.py + models.py); v2 reads its own keys.
  rvproto/.venv/bin/python p8_shared_state.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import time

sys.path.insert(0, __import__("os").environ.get("RVPROTO_DIR", "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-state-propagation/rvproto-frozen1"))

import redis
import redis.asyncio as aioredis

from rvproto.admit.identity import Identity
from rvproto.admit.killswitch import KillSwitch
from rvproto.runtime.metrics import Registry

URL = "redis://127.0.0.1:26379/7"
REPO = "/home/contact_cyberultron_com/AI_Mesh_Firewall"
EV = "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-state-propagation"
KEY = "sk-live-shared-state-probe-0001"
H = hashlib.sha256(KEY.encode()).hexdigest()


def v1(what: str) -> dict:
    out = subprocess.run([f"{REPO}/gateway/.venv/bin/python", f"{EV}/probes/p8_v1_side.py", KEY],
                         capture_output=True, text=True, cwd=REPO,
                         env={"PYTHONPATH": f"{REPO}/gateway:{REPO}/shared", "PATH": "/usr/bin:/bin"})
    line = [x for x in out.stdout.splitlines() if x.startswith("{")]
    return json.loads(line[-1]) if line else {"error": out.stderr[-400:]}


async def v2() -> dict:
    ident = Identity(aioredis.Redis.from_url(URL))
    ks = KillSwitch(aioredis.Redis.from_url(URL), Registry(0), refresh_ms=500, stale_ms=5000, on_epoch=ident.on_epoch)
    await ks.refresh_once()
    _, p = ident.cached(KEY)
    p = p or await ident.fetch(H)
    return {"side": "v2", "kill_switch": {"state": ks.state(), "org_killed": ks.org_killed("acme"),
                                           "model_killed": ks.model_killed("gpt-4o-mini")},
            "auth": "ok" if p is not None else "invalid"}


def log(step: str, **kw: object) -> None:
    print(json.dumps({"t": round(time.time(), 3), "step": step, **kw}, default=str), flush=True)


async def main() -> None:
    r = redis.Redis.from_url(URL)
    r.flushdb()
    # both versions provisioned for the same tenant/key (v2 keys as rvproto seeds them)
    r.set(f"auth:apikey:{H}", json.dumps({"key_id": "k1", "user_id": "u1", "project_id": "p1",
                                         "org_slug": "acme", "organization_id": "acme", "is_active": True}))
    r.hset("rv:keys", H, json.dumps({"key_id": "k1", "org_id": "acme", "rate_per_s": 1e6, "burst": 1e6, "epoch": 1}))
    r.set("rv:killswitch", "0")
    r.set("rv:auth_epoch", "1")
    log("S0 both versions provisioned", v1=v1("s0"), v2=await v2())
    # console engages the org-wide kill switch (control core/models.py build_redis_key/payload, signals.py SET)
    r.set("kill_switch:acme:global", json.dumps({"is_active": True, "action": "disable", "reason": "incident",
                                                "org_slug": "acme", "model_name": "__global__"}))
    log("S1 console ENGAGES org kill switch (v1 key format)", v1=v1("s1"), v2=await v2())
    r.delete("kill_switch:acme:global")
    # console revokes the key (control signals.py: DELETE auth:apikey:{hash}; no v2 epoch exists there)
    r.delete(f"auth:apikey:{H}")
    log("S2 console REVOKES the key (v1 path: DEL auth:apikey:{hash})", v1=v1("s2"), v2=await v2())
    # v2-era operator action during cutover, then ROLLBACK to v1 (GW22): v2-only keys are invisible to v1
    r.set(f"auth:apikey:{H}", json.dumps({"key_id": "k1", "user_id": "u1", "project_id": "p1",
                                         "org_slug": "acme", "organization_id": "acme", "is_active": True}))
    r.hset("rv:killswitch:org", "acme", "1")
    log("S3 v2 operator tooling engages org kill switch (rv:killswitch:org) -> after ROLLBACK v1 serves",
        v1=v1("s3"), v2=await v2())
    # pub/sub is not scoped by db number: a v2 subscriber on db 7 hears a publish made on db 0
    sub = redis.Redis.from_url(URL).pubsub()
    sub.subscribe("policy_updates")
    time.sleep(0.2)
    sub.get_message(timeout=1)
    redis.Redis.from_url("redis://127.0.0.1:26379/0").publish("policy_updates", json.dumps({"org_slug": "acme", "version": 9}))
    msg = sub.get_message(timeout=2)
    log("S4 channel namespace is instance-wide (db 0 publish reaches a db 7 subscriber)",
        received=None if msg is None else msg.get("data"))


if __name__ == "__main__":
    asyncio.run(main())
