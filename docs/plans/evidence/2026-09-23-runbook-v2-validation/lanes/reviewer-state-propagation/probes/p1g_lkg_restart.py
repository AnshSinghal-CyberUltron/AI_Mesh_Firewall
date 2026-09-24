"""P1g: 'last-known-good keeps serving' is process memory. A plan version that fails validation
arrives; running workers keep the previous plan; a restarted/scaled-out replica has no previous plan."""
import json, sys, time
from pc import log, sweep, r
KEY = "sk-rv-lat-00000-probe"
if sys.argv[1] == "publish_invalid":
    rr = r()
    doc = json.loads(rr.get("rv:plan:org-lat"))
    doc["version"] = "lat-2-invalid"
    doc["rules"][0]["detectors"] = ["secret.does_not_exist"]
    p = rr.pipeline(); p.set("rv:plan:org-lat", json.dumps(doc)); p.hset("rv:plan_versions", "org-lat", "lat-2-invalid")
    p.publish("rv:plan:updates", "org-lat"); p.execute()
    time.sleep(2.0)
    log("G1 invalid plan version published; RUNNING workers", sweep=sweep(KEY, 12))
else:
    log("G2 same store, after a replica RESTART", sweep=sweep(KEY, 12))
