"""P10: requests served during the freshness window of a store outage: x-rv-stages says audit:E
(record ENQUEUED) but the record is never persisted (writer's XADD fails; no retry)."""
import json, time, urllib.request, uuid
import httpx
from pc import BASE, MODEL, log, r, metrics_all
CTL = "http://127.0.0.1:26390"
KEY = "sk-rv-lat-00000-probe"
def ctl(p): return json.loads(urllib.request.urlopen(CTL + p, timeout=5).read())
def call(rid):
    with httpx.Client(base_url=BASE, timeout=30) as c:
        x = c.post("/v1/chat/completions", headers={"authorization": f"Bearer {KEY}", "x-request-id": rid},
                   json={"model": MODEL, "max_tokens": 3, "messages": [{"role": "user", "content": "hi"}]})
    return x.status_code, x.headers.get("x-rv-stages")
for _ in range(12): call("warm-" + uuid.uuid4().hex[:6])
time.sleep(1.2)
before = metrics_all()["count"].get("audit_write_failed", 0)
ctl("/backend?port=26999"); ctl("/reset_existing")
served = []
t0 = time.time()
for i in range(8):
    rid = f"outage-{i}-{uuid.uuid4().hex[:8]}"
    st, stages = call(rid)
    served.append({"rid": rid, "status": st, "x-rv-stages": stages, "at_s": round(time.time() - t0, 2)})
    time.sleep(0.3)
time.sleep(1.0)
ctl("/backend?port=26379")
time.sleep(3.0)
ids = {json.loads(f[b"r"])["request_id"] for _, f in r().xrange("rv:audit:org-lat")}
time.sleep(1.2)
after = metrics_all()["count"].get("audit_write_failed", 0)
log("audit header vs store", served_during_outage=served,
    persisted_records_for_them=sum(1 for s in served if s["rid"] in ids),
    served_200_with_audit_E=sum(1 for s in served if s["status"] == 200 and s["x-rv-stages"] and "audit:E" in s["x-rv-stages"]),
    audit_write_failed_delta=after - before)
