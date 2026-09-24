"""E2E P11 (GW06): org token budget vs per-worker lease chunks (benchmarked admit/quota.py, 2 workers).
Budget set to 30,000 tokens.  Each request costs len(body)//4 + max_tokens ~= 130 tokens, so the org can
afford ~230 requests.  Requests use fresh connections so SO_REUSEPORT spreads them over both workers."""
import collections, json, sys, time
import httpx, redis
r = redis.Redis.from_url("redis://127.0.0.1:36379/0")
BUDGET = 30000
r.set("rv:budget:org-q", BUDGET)
st = collections.Counter(); admitted_cost = 0
doc = {"model": "gpt-4o-mini", "max_tokens": 100, "messages": [{"role": "user", "content": "hello, please summarise the weather " * 2}]}
body_len = len(json.dumps(doc).encode())
cost = body_len // 4 + 100
for i in range(120):
    with httpx.Client(base_url="http://127.0.0.1:47400", timeout=30) as c:   # new connection each time
        resp = c.post("/v1/chat/completions", json=doc, headers={"authorization": "Bearer sk-rv-org-q-0001"})
    st[resp.status_code] += 1
    if resp.status_code == 200:
        admitted_cost += cost
remaining = int(r.get("rv:budget:org-q"))
m = httpx.get("http://127.0.0.1:47400/metrics/all").json()
held = {k: v for k, v in (m.get("gauge") or {}).items() if k.startswith("lease_held_tokens")}
print(f"org budget={BUDGET}  per-request cost={cost}  requests: {dict(st)}")
print(f"tokens actually admitted={admitted_cost}  budget left in Redis={remaining}  "
      f"-> {BUDGET - remaining - admitted_cost} tokens drawn into worker leases but unusable by the rejected worker(s)")
print("per-worker unspent lease (gauge, summed over workers in /metrics/all):", held)
