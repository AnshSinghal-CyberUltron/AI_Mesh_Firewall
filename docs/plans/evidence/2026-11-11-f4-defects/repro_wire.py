"""F4 / pre-check / F6 repro on the in-process dev-profile app (origin/develop)."""
import asyncio, json, sys
sys.path.insert(0, ".")
from tests.edge import _gw35_harness as h

def show(label, res):
    body = res.json() if res.body[:1] == b"{" else res.body[:200]
    err = body.get("error") if isinstance(body, dict) else None
    trace = body.get("pipeline_trace") if isinstance(body, dict) else None
    stages = {s["name"]: (s["action"], s.get("deciding_rule")) for s in trace["stages"]} if trace else None
    print(f"{label}: status={res.status} x-request-id={res.headers.get('x-request-id')} "
          f"error={json.dumps(err)}")
    if stages:
        print(f"   stages={stages}")

# F4: non-stream upstream error envelope
for code in ("upstream_error", "upstream_rate_limited"):
    app, records, _ = h.build_app(h.ScriptedProvider(h.failed_events(code)))
    show(f"F4 non-stream {code}", asyncio.run(h.post(app, records)))
    app, records, _ = h.build_app(h.ScriptedProvider(h.failed_events(code)))
    res = asyncio.run(h.post(app, records, stream=True))
    print(f"F4 stream {code}: status={res.status} x-request-id={res.headers.get('x-request-id')} "
          f"body={res.body[:260]!r}")

# pre-check: oversized body
app, records, deps = h.build_app(h.ScriptedProvider(h.ok_events()))
limit = deps["contract"].max_request_bytes()
big = json.dumps({"model": "echo-1", "messages": [{"role": "user", "content": "a" * (limit + 10)}]}).encode()
show(f"pre-check oversized ({len(big)} > {limit} bytes)", asyncio.run(h.post(app, records, body=big)))
