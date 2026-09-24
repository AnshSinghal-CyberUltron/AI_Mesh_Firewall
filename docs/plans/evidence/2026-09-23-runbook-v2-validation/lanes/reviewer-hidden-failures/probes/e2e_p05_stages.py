"""E2E P05: is x-rv-stages evidence of execution?  Push a plan (org-s) with ONLY the semantic input rule:
no deterministic input detectors, no output detectors.  The harness qualifies a request only if every stage in
--profile-stages (canon,det,sem,resolve,dispatch,out,audit) reads :E.  Compare header vs the audit record."""
import hashlib, json, sys, time, uuid
import httpx, redis
r = redis.Redis.from_url("redis://127.0.0.1:36379/0")
plan = {"org_id": "org-s", "version": "s-1", "streaming_mode": "incremental", "rules": [
    {"rule_id": "S.inj.sem", "category": "injection", "detectors": ["injection.pg2"], "mode": "enforce",
     "action": "block", "priority": 10, "scope": "input", "on_unavailable": {"kind": "fail_closed"}, "threshold": 0.5}]}
r.hset("rv:keys", hashlib.sha256(b"sk-rv-org-s-0001").hexdigest(),
       json.dumps({"key_id": "key-s-1", "org_id": "org-s", "rate_per_s": 1e6, "burst": 1e6, "epoch": 1}))
r.set("rv:plan:org-s", json.dumps(plan)); r.hset("rv:plan_versions", "org-s", "s-1"); r.set("rv:budget:org-s", 10**12)
r.publish("rv:plan:updates", "org-s"); time.sleep(1.5)
can = {c["id"]: c["value"] for c in json.load(open(sys.argv[1]))["canaries"]}
out = []
with httpx.Client(base_url="http://127.0.0.1:47400", timeout=30) as c:
    for stream in (False, True):
        rid = "p05-" + uuid.uuid4().hex[:10]
        doc = {"model": "gpt-4o-mini", "stream": stream, "max_tokens": 5,
               "messages": [{"role": "user", "content": f"my key is {can['secret.aws']} and mail {can['pii.email']}"}]}
        hdr = {"authorization": "Bearer sk-rv-org-s-0001", "x-request-id": rid, "x-synth-inject": "email"}
        resp = c.post("/v1/chat/completions", json=doc, headers=hdr)
        out.append((stream, rid, resp.status_code, resp.headers.get("x-rv-stages"), resp.headers.get("x-rv-disposition"),
                    can["out.email"] in resp.text))
time.sleep(1)
aud = {}
for _id, f in r.xrange("rv:audit:org-s"):
    d = json.loads(f[b"r"]); aud.setdefault(d["request_id"], []).append(d)
for stream, rid, st, stages, disp, out_leak in out:
    print(f"stream={stream} http={st} disposition={disp} x-rv-stages={stages}")
    print(f"   provider-injected output email reached client: {out_leak}")
    for d in aud.get(rid, []):
        det_status = sorted({f['status'] for f in d['findings'] if f['detector'] != 'injection.pg2'})
        sem = [f['status'] for f in d['findings'] if f['detector'] == 'injection.pg2']
        print(f"   audit phase={d['phase']:<6} disposition={d['disposition']:<6} deterministic finding statuses={det_status} semantic={sem} stages={d['stages']}")
