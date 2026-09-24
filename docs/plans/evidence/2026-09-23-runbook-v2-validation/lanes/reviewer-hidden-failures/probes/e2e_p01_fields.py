"""E2E P01: secret in fields rvproto does not scan -> does the PROVIDER receive it? (org-a: secret.* -> BLOCK)
Evidence: HTTP status + x-rv-disposition from rvproto, synthprov's own canary_hits on the RAW body it received,
and the INPUT DecisionRecord rvproto wrote to its audit stream."""
import json, sys, time, uuid
import httpx, redis
EV, LOG = sys.argv[1], sys.argv[2]
can = {c["id"]: c["value"] for c in json.load(open(sys.argv[3]))["canaries"]}
KEY = can["secret.aws"]; MAIL = can["pii.email"]
H = {"authorization": "Bearer sk-rv-org-a-0001"}
def base(**kw):
    d = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello there"}], "max_tokens": 5, "stream": False}
    d.update(kw); return d
V = {
 "control user content": base(messages=[{"role": "user", "content": f"key {KEY}"}]),
 "legacy function_call.arguments": base(messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": None, "function_call": {"name": "f", "arguments": json.dumps({"k": KEY})}}, {"role": "function", "name": "f", "content": "ok"}]),
 "messages[].name": base(messages=[{"role": "user", "name": KEY, "content": "hi"}]),
 "tools[].function.description": base(tools=[{"type": "function", "function": {"name": "f", "description": f"use {KEY}", "parameters": {"type": "object", "properties": {}}}}]),
 "prediction.content": base(prediction={"type": "content", "content": f"answer {KEY}"}),
 "user": base(user=KEY),
 "assistant content part type=refusal": base(messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": [{"type": "refusal", "refusal": f"no {KEY}"}]}, {"role": "user", "content": "ok"}]),
 "PII control (REDACT) user content": base(messages=[{"role": "user", "content": f"mail {MAIL}"}]),
 "PII in tools description": base(tools=[{"type": "function", "function": {"name": "f", "description": f"mail {MAIL}", "parameters": {"type": "object", "properties": {}}}}]),
}
rids = {}
with httpx.Client(base_url="http://127.0.0.1:47400", timeout=30) as c:
    for name, doc in V.items():
        rid = "p01-" + uuid.uuid4().hex[:12]; rids[name] = rid
        r = c.post("/v1/chat/completions", json=doc, headers={**H, "x-request-id": rid})
        rids[name] = (rid, r.status_code, r.headers.get("x-rv-disposition"))
time.sleep(1.5)
prov = {}
for line in open(f"{LOG}/prov-records.jsonl"):
    rec = json.loads(line); prov[rec["rid"]] = rec
r = redis.Redis.from_url("redis://127.0.0.1:36379/0")
audit = {}
for _id, fields in r.xrange("rv:audit:org-a"):
    d = json.loads(fields[b"r"]);
    if d["phase"] == "input": audit[d["request_id"]] = d
print(f"{'field':<38} {'http':>4} {'x-rv-disposition':<16} {'audit(input)':<12} {'provider canary_hits (raw body)'}")
for name, (rid, st, disp) in rids.items():
    p = prov.get(rid); a = audit.get(rid, {})
    print(f"{name:<38} {st:>4} {str(disp):<16} {a.get('disposition','-'):<12} {p.get('canary_hits') if p else 'NO PROVIDER CALL'}")
