"""P03c: audit record vs wire for the logprobs channel (org-a, non-stream + stream)."""
import json, sys, time, uuid, httpx, redis
R = redis.Redis(port=16479); out = []
for stream in (False, True):
    rid = f"p03c-{'s' if stream else 'j'}-{uuid.uuid4().hex[:6]}"
    r = httpx.post("http://127.0.0.1:8480/v1/chat/completions", json={"model": "gpt-4o-mini", "stream": stream, "logprobs": True,
                   "messages": [{"role": "user", "content": "hi"}]},
                   headers={"authorization": "Bearer sk-rv-org-a-0001", "x-request-id": rid, "x-synth-edge": "logprobs-secret"})
    time.sleep(0.5)
    recs = [json.loads(v[b"r"]) for _, v in R.xrange("rv:audit:org-a") if json.loads(v[b"r"])["request_id"] == rid]
    out.append({"rid": rid, "stream": stream, "wire_contains_raw_aws": "AKIAQYLPMN5HHHFPZAM2" in r.text,
                "wire_contains_raw_email": "alice.canary@example.com" in r.text,
                "audit": [{k: x[k] for k in ("phase", "disposition", "deciding_rules", "verification")} for x in recs]})
for o in out: print(json.dumps(o))
json.dump(out, open(sys.argv[1], "w"), indent=1)
