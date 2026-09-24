"""E2E P18 (GW06): per-org request-rate limit (GCRA) is per worker process -> the org's configured rate is
multiplied by the number of workers (x replicas).  Key for org-q re-seeded with rate_per_s=2, burst=1.
40 requests are sent back-to-back over fresh connections (SO_REUSEPORT spreads them) in ~1 s."""
import collections, hashlib, json, time
import httpx, redis
r = redis.Redis.from_url("redis://127.0.0.1:36379/0")
r.hset("rv:keys", hashlib.sha256(b"sk-rv-org-q-0001").hexdigest(),
       json.dumps({"key_id": "key-q-1", "org_id": "org-q", "rate_per_s": 2.0, "burst": 1.0, "epoch": 1}))
r.set("rv:budget:org-q", 10**12)
r.incr("rv:auth_epoch"); time.sleep(1.2)          # epoch bump -> every worker drops its identity cache
st = collections.Counter(); t0 = time.time()
for i in range(40):
    with httpx.Client(base_url="http://127.0.0.1:47400", timeout=10) as c:
        st[c.post("/v1/chat/completions", headers={"authorization": "Bearer sk-rv-org-q-0001"},
                  json={"model": "gpt-4o-mini", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).status_code] += 1
el = time.time() - t0
print(f"configured org rate = 2 req/s (burst 1); workers = 4; sent 40 in {el:.2f}s -> admitted {st[200]} "
      f"(= {st[200]/el:.1f} req/s), 429 = {st[429]}; allowed by the org limit over that window ~ {2*el+1:.0f}")
