"""P3d: org deletion on the LIVE stack. Delete the org's plan + its key WITHOUT an epoch bump (what a
control plane that only removes rows would do), then with an epoch bump."""
import hashlib, json, time
from pc import log, sweep, r
ORG, KEY = "org-del", "sk-rv-org-del-0001"
H = hashlib.sha256(KEY.encode()).hexdigest()
rr = r()
doc = {"org_id": ORG, "version": "del-1", "streaming_mode": "incremental", "rules": [
    {"rule_id": "D.secret.in", "category": "secret", "detectors": ["secret.*"], "mode": "enforce", "action": "block",
     "priority": 20, "scope": "input", "on_unavailable": {"kind": "fail_closed"}, "threshold": None}]}
p = rr.pipeline(); p.set("rv:plan:" + ORG, json.dumps(doc)); p.hset("rv:plan_versions", ORG, "del-1")
p.hset("rv:keys", H, json.dumps({"key_id": "k-del", "org_id": ORG, "rate_per_s": 1e6, "burst": 1e6, "epoch": 1}))
p.set("rv:budget:" + ORG, 10**9); p.publish("rv:plan:updates", ORG); p.execute()
time.sleep(1.5)
log("D0 org provisioned and warm", sweep=sweep(KEY, 16))
p = rr.pipeline(); p.hdel("rv:plan_versions", ORG); p.delete("rv:plan:" + ORG); p.hdel("rv:keys", H)
p.delete("rv:budget:" + ORG); p.publish("rv:plan:updates", ORG); p.execute()
time.sleep(2.0)
log("D1 org DELETED (plan, key, budget removed; no auth_epoch bump)", sweep=sweep(KEY, 16))
rr.incr("rv:auth_epoch"); time.sleep(1.0)
log("D2 after an auth_epoch bump", sweep=sweep(KEY, 16))
