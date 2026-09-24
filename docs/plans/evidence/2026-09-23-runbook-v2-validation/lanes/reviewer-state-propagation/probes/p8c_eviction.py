"""P8c: the repo's PRODUCTION store policy (docker-compose.prod.yml: --maxmemory 768mb
--maxmemory-policy allkeys-lru; same at baseline 2a657fad) + rvproto's audit-in-the-store design
(one ~3.3 KB XADD per phase, MAXLEN 2,000,000 per org). Scaled down 16x: 48 MB instance.
The v2 refreshers and a v1 request loop keep touching what they touch in real life; everything
else (plan documents, budgets, the key hash while caches are warm, idle tenants' v1 keys) is idle.
  rvproto/.venv/bin/python p8c_eviction.py  (uses container sp-rsp-evict on :26382)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time

import redis

URL = "redis://127.0.0.1:26382/0"
REC = json.dumps({"request_id": "pin-5-e50e563b", "org_id": "org-a", "key_id": "key-a-1", "phase": "output",
                  "plan_version": "a-1", "disposition": "ALLOW", "pad": "x" * 3000})


def main() -> None:
    subprocess.run(["docker", "rm", "-f", "sp-rsp-evict"], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", "sp-rsp-evict", "-p", "127.0.0.1:26382:6379",
                    "redis:7.4-alpine", "redis-server", "--maxmemory", "48mb", "--maxmemory-policy", "allkeys-lru",
                    "--save", "", "--appendonly", "no"], check=True, capture_output=True)
    time.sleep(1.5)
    r = redis.Redis.from_url(URL)
    security = {}
    for org in ("org-a", "org-b", "org-q"):
        security[f"rv:plan:{org}"] = "string"
        r.set(f"rv:plan:{org}", json.dumps({"org_id": org, "version": f"{org}-1", "rules": ["..." * 50]}))
        r.hset("rv:plan_versions", org, f"{org}-1")
        security[f"rv:budget:{org}"] = "string"
        r.set(f"rv:budget:{org}", 10**9)
    r.hset("rv:keys", mapping={hashlib.sha256(f"k{i}".encode()).hexdigest(): "{}" for i in range(50)})
    r.set("rv:killswitch", "0")
    r.hset("rv:killswitch:org", "org-evil", "1")  # an ENGAGED org kill switch
    r.set("rv:auth_epoch", "7")
    for i in range(50):  # v1 tenants' keys; only tenant 0 is active right now
        r.set(f"auth:apikey:{i:064x}", json.dumps({"key_id": i, "user_id": 1, "project_id": 1}))
    r.set("kill_switch:acme:global", json.dumps({"is_active": True, "action": "disable"}))  # ENGAGED v1 KS
    r.set("firewall:config:acme", json.dumps({"x": 1}))
    for k in ["rv:plan_versions", "rv:keys", "rv:killswitch", "rv:killswitch:org", "rv:auth_epoch",
              "auth:apikey:" + f"{0:064x}", "auth:apikey:" + f"{1:064x}", "kill_switch:acme:global", "firewall:config:acme"]:
        security[k] = "tracked"
    stop = threading.Event()

    def v2_refreshers() -> None:  # rvproto KillSwitch.refresh_once every 0.5 s + plan reconcile every 1 s
        rr = redis.Redis.from_url(URL)
        n = 0
        while not stop.is_set():
            p = rr.pipeline(transaction=False)
            p.mget("rv:killswitch", "rv:auth_epoch")
            p.hgetall("rv:killswitch:org")
            p.hgetall("rv:killswitch:model")
            if n % 2 == 0:
                p.hgetall("rv:plan_versions")
            p.execute()
            n += 1
            time.sleep(0.5)

    def v1_requests() -> None:  # v1 reads the active tenant's key + kill switches per request
        rr = redis.Redis.from_url(URL)
        while not stop.is_set():
            rr.get("auth:apikey:" + f"{0:064x}")
            rr.get("kill_switch:tenant0:global")
            time.sleep(0.01)

    th = [threading.Thread(target=v2_refreshers, daemon=True), threading.Thread(target=v1_requests, daemon=True)]
    for t in th:
        t.start()
    w = redis.Redis.from_url(URL)
    written = 0
    evicted_at = {}
    t0 = time.time()
    while written < 40000:
        p = w.pipeline(transaction=False)
        for _ in range(200):
            p.xadd("rv:audit:org-a", {"r": REC}, maxlen=2_000_000, approximate=True)
        try:
            p.execute()
        except redis.ResponseError as exc:
            print(json.dumps({"write_error": str(exc)[:120], "written": written}))
            break
        written += 200
        for k in security:
            if k not in evicted_at and not r.exists(k):
                evicted_at[k] = {"after_audit_records": written, "at_s": round(time.time() - t0, 2)}
    stop.set()
    info = r.info("stats")
    mem = r.info("memory")
    survivors = [k for k in security if r.exists(k)]
    print(json.dumps({"policy": "allkeys-lru", "maxmemory_mb": 48, "audit_records_written": written,
                      "audit_stream_len": r.xlen("rv:audit:org-a"), "used_memory_mb": round(mem["used_memory"] / 2**20, 1),
                      "evicted_keys_total": info["evicted_keys"], "security_keys_EVICTED": evicted_at,
                      "security_keys_surviving": survivors}, indent=1))
    subprocess.run(["docker", "rm", "-f", "sp-rsp-evict"], capture_output=True)


if __name__ == "__main__":
    main()
