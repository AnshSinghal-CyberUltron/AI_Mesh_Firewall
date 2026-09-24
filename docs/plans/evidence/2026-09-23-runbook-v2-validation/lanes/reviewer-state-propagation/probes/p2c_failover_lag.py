"""P2c: store failover with ordinary async-replication lag (the newest acknowledged writes are not on
the replica that gets promoted). The operator engages the GLOBAL kill switch and revokes key B
(HDEL + INCR auth_epoch in one MULTI) on the primary; both are acknowledged and take effect; then
the primary dies and the replica (which missed those writes) is promoted. Workers reconnect.
primary sp-rsp-redis :26379, replica sp-rsp-redis-b :26381, workers reach the store via proxy :26380.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import urllib.request

import redis

from pc import KEY_A, KEY_B, log, readyz, sweep

CTL = "http://127.0.0.1:26390"
PRIMARY = redis.Redis.from_url("redis://127.0.0.1:26379/0")
REPLICA = redis.Redis.from_url("redis://127.0.0.1:26381/0")
HB = hashlib.sha256(KEY_B.encode()).hexdigest()


def ctl(path: str) -> dict:
    return json.loads(urllib.request.urlopen(CTL + path, timeout=5).read())


def state(rr: redis.Redis) -> dict:
    return {"killswitch": (rr.get("rv:killswitch") or b"").decode(), "auth_epoch": (rr.get("rv:auth_epoch") or b"").decode(),
            "key_b_present": bool(rr.hexists("rv:keys", HB))}


def main() -> None:
    ctl("/mode?m=pass")
    ctl("/backend?port=26379")
    log("L0 healthy; replica in sync", primary=state(PRIMARY), replica=state(REPLICA),
        org_a=sweep(KEY_A, 8), org_b=sweep(KEY_B, 8))
    REPLICA.execute_command("REPLICAOF", "NO", "ONE")  # the replica stops receiving (lag): later writes miss it
    PRIMARY.set("rv:killswitch", "1")
    p = PRIMARY.pipeline(transaction=True)
    p.hdel("rv:keys", HB)
    p.incr("rv:auth_epoch")
    p.execute()
    time.sleep(1.5)
    log("L1 operator ENGAGED global kill switch + REVOKED key B on the primary (acknowledged)",
        primary=state(PRIMARY), replica=state(REPLICA), org_a=sweep(KEY_A, 8), org_b=sweep(KEY_B, 8))
    ctl("/backend?port=26381")  # failover: the promoted replica now serves the endpoint
    ctl("/reset_existing")  # the old primary is gone
    subprocess.run(["docker", "pause", "sp-rsp-redis"], check=True)  # primary dead
    for i in range(1, 5):
        time.sleep(2)
        rz = readyz()
        log(f"L2 +{2 * i}s after failover to the lagging replica", store=state(REPLICA),
            org_a=sweep(KEY_A, 8), org_b_revoked_key=sweep(KEY_B, 8),
            readyz={"status": rz["status"], "killswitch": rz["killswitch"], "plans_fresh": rz["plans_fresh"]})
    subprocess.run(["docker", "unpause", "sp-rsp-redis"], check=True)
    ctl("/backend?port=26379")
    ctl("/reset_existing")
    PRIMARY.set("rv:killswitch", "0")
    PRIMARY.hset("rv:keys", HB, json.dumps({"key_id": "key-b-1", "org_id": "org-b", "rate_per_s": 1e6,
                                             "burst": 1e6, "epoch": 1}))
    PRIMARY.incr("rv:auth_epoch")
    log("L3 restored the original primary for later probes", primary=state(PRIMARY))


if __name__ == "__main__":
    main()
